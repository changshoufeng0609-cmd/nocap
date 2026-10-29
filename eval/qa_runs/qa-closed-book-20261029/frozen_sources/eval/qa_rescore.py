"""Offline sensitivity analysis for the frozen closed-book QA responses.

The primary exact-contract records are never edited. This post-run analysis accepts
only unambiguous metadata aliases, without reading a question or gold when normalizing.
Numbers, value_text, entities, metrics and stated years are never repaired.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import qa_core as core, qa_benchmark as runner
from receipts.store import Store

NORMALIZATION_POLICY = "post-run-metadata-aliases-v1"


def normalize_answer(answer: core.RawAnswer) -> tuple[core.RawAnswer, dict]:
    """No Question/gold parameter or lookup: this cannot fill missing answer knowledge."""
    if answer.status == "abstain":
        return answer, {}
    changes = {}
    unit = re.sub(r"\s+", " ", (answer.unit or "").replace("_", " ").strip().casefold())
    expression = core.UNIT_PATTERN.fullmatch(answer.value_text or "")
    scaled = re.fullmatch(r"usd\s+(thousands?|millions?|billions?)", unit)
    if scaled:
        magnitude = scaled[1].removesuffix("s")
        if expression is None or (expression["scale"] or "").casefold() != magnitude or expression["share"]:
            raise ValueError("Declared monetary magnitude and explicit value_text disagree")
        changes["unit"] = "USD"
    elif unit in {"usd per share", "$ per share", "usd/share"}:
        if expression is None or not expression["share"] or expression["scale"]:
            raise ValueError("Per-share value_text must explicitly say per share and be unscaled")
        changes["unit"] = "USD/share"
    elif unit == "usd":
        changes["unit"] = "USD"

    period = (answer.fiscal_period or "").strip().upper()
    if re.fullmatch(r"Q[1-4]|FY", period):
        changes["fiscal_period"] = period
    else:
        suffix = re.fullmatch(r"(Q[1-4])\s+(?:FY\s*)?((?:19|20)\d{2})", period)
        prefix = re.fullmatch(r"FY\s*((?:19|20)\d{2})\s+(Q[1-4])", period)
        if suffix or prefix:
            quarter, year = (suffix[1], int(suffix[2])) if suffix else (prefix[2], int(prefix[1]))
            if year != answer.fiscal_year:
                raise ValueError("Embedded period year contradicts the response fiscal_year")
            changes["fiscal_period"] = quarter
    changes = {key: value for key, value in changes.items() if getattr(answer, key) != value}
    normalized = answer.model_copy(update=changes)
    assert normalized.value_text == answer.value_text and normalized.fiscal_year == answer.fiscal_year
    return normalized, changes


def binding_category(question: core.Question, answer: core.RawAnswer, reason: str, store: Any) -> str:
    """Separate an actual wrong question binding from metadata that cannot be interpreted."""
    if reason == "entity_mismatch_or_unknown":
        resolved = store.resolve_entity(answer.entity or "")
        return "question_mismatch" if resolved and resolved[1] != question.ticker else "unassessable"
    vocabularies = {"metric_mismatch": set(core.METRICS), "fiscal_period_mismatch": {"Q1", "Q2", "Q3", "Q4", "FY"},
                    "basis_mismatch": {"GAAP", "non-GAAP"}, "unit_mismatch": {"USD", "USD/share"}}
    if reason == "fiscal_year_mismatch":
        return "question_mismatch"
    for name, allowed in vocabularies.items():
        if reason == name:
            field = name.removesuffix("_mismatch")
            return "question_mismatch" if getattr(answer, field) in allowed else "unassessable"
    return "unassessable"


def assess(row: dict, record: dict, store: Any) -> dict:
    question = core.Question.model_validate(row["question"])
    if record.get("error_type") is not None:
        return {"question_id": question.id, "outcome": "api_error", "gate": {"accepted": False, "reason": "api_error"}}
    try:
        if not record.get("response_complete"):
            raise ValueError("Incomplete response")
        answer = core.parse_answer(record["output_text"])
    except Exception:
        return {"question_id": question.id, "outcome": "format_error", "gate": {"accepted": False, "reason": "format_error"}}
    try:
        normalized, changes = normalize_answer(answer)
    except ValueError as error:
        return {"question_id": question.id, "outcome": "unassessable", "normalization_rejection": str(error),
                "raw_answer": answer.model_dump(), "normalization_changes": {},
                "gate": {"accepted": False, "reason": "ambiguous_or_conflicting_metadata"}}
    gate = core.gate_answer(question, normalized, store)
    if normalized.status == "abstain":
        outcome = "abstain"
    elif problem := core.binding_error(question, normalized, store):
        outcome = binding_category(question, normalized, problem, store)
    else:
        outcome = "correct" if core.score_answer(question, normalized, row["gold"], store) == "correct" else "numeric_wrong"
    return {"question_id": question.id, "raw_answer": answer.model_dump(), "normalized_answer": normalized.model_dump(),
            "normalization_changes": changes, "outcome": outcome, "gate": gate}


def summarize(records: list[dict], planned=50) -> dict:
    counts = Counter(record["outcome"] for record in records)
    accepted = [row for row in records if row["gate"]["accepted"]]
    correct_accepted = sum(row["outcome"] == "correct" for row in accepted)
    wrong = counts["numeric_wrong"] + counts["question_mismatch"]
    answered = counts["correct"] + wrong
    return {"planned": planned, "records": len(records), "not_run": planned - len(records),
            "correct": counts["correct"], "wrong": wrong, "numeric_wrong": counts["numeric_wrong"],
            "question_mismatch": counts["question_mismatch"], "abstain": counts["abstain"],
            "unassessable": counts["unassessable"], "format_error": counts["format_error"], "api_error": counts["api_error"],
            "raw_accuracy_all": counts["correct"] / planned, "raw_answer_precision": counts["correct"] / answered if answered else None,
            "raw_answer_coverage": answered / planned, "verified_accepted": len(accepted),
            "verified_correct": correct_accepted, "verified_precision": correct_accepted / len(accepted) if accepted else None,
            "verified_coverage": len(accepted) / planned, "unsafe_passed": len(accepted) - correct_accepted,
            "normalized_records": sum(bool(row.get("normalization_changes")) for row in records)}


def usage_audit(run: dict, ledger_rows: list[dict]) -> dict:
    """Independent Decimal arithmetic over retained usage, with reasoning included only once."""
    target = run["target"]
    prices = run.get("token_api_pricing_per_million") or dict(runner.backend(target).prices)
    ledger = {row["question"]: row for row in ledger_rows if row["target"] == target}
    total = 0
    unknown = 0
    for record in run["records"]:
        assert record["question_id"] in ledger
        entry = ledger[record["question_id"]]
        assert entry["request_hash"] == record["request_sha256"]
        if record.get("usage") is None:
            unknown += 1
            assert entry["charged"] >= entry["reservation"]
            continue
        usage = record["usage"]
        assert 0 <= usage["cached_tokens"] <= usage["input_tokens"]
        amount = (Decimal(usage["input_tokens"] - usage["cached_tokens"]) * Decimal(str(prices["input"])) +
                  Decimal(usage["cached_tokens"]) * Decimal(str(prices["cached_input"])) +
                  Decimal(usage["output_tokens"]) * Decimal(str(prices["output"])))
        micro = int(amount.to_integral_value(rounding=ROUND_CEILING))
        assert micro == entry["charged"]
        if target != "glimmer":
            assert Decimal(str(record["token_api_cost_usd"])) == Decimal(micro) / Decimal(1000000)
        total += micro
    return {"usage_records": len(run["records"]) - unknown, "unknown_usage": unknown,
            "token_api_cost_usd": total / 1e6 if target != "glimmer" else None,
            "gpu_separate": target == "glimmer", "ledger_matches": True}


def run_rescore(run_id: str, output: Path) -> dict:
    manifest = runner.load_manifest()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid run ID")
    folder = ROOT / "data/eval_runs" / run_id
    by_id = {row["question"]["id"]: row for row in manifest["items"]}
    manifest_hash = core.digest(core.MANIFEST.read_bytes())
    assert core.evidence_hashes(folder / "evidence") == manifest["snapshot_hashes"]
    ledger = runner.AdmissionLedger(folder / "admission.sqlite3", manifest_hash).report()
    result = {"scope": "Post-run offline sensitivity analysis; the exact-contract primary records remain unchanged",
              "normalization_policy": NORMALIZATION_POLICY, "manifest_sha256": manifest_hash,
              "normalizer_sha256": core.digest(Path(__file__).read_bytes()), "new_model_calls": 0,
              "source_records": {}, "runs": [], "ledger": ledger,
              "limitations": ["This metadata normalization was specified after observing format mismatches, not pre-registered.",
                              "Closed-book questions provide no filings, retrieval or tools; do not infer gains for retrieval-augmented QA.",
                              "Only explicit monetary magnitude and consistent embedded fiscal-year aliases are normalized; values are never edited.",
                              "Unknown, conflicting or unsupported metadata remains unassessable; it is not automatically a financial error.",
                              "All 50 planned questions remain in coverage and overall-accuracy denominators; accepted=0 gives null precision."]}
    store = Store(folder / "evidence/facts.sqlite", folder / "evidence/pr_facts")
    try:
        for target in core.TARGETS:
            path = ROOT / "eval/qa_runs" / run_id / f"{target}.json"
            run = json.loads(path.read_text())  # Do not silently omit a missing target.
            assert run["manifest_sha256"] == manifest_hash and run["planned"] == 50 and run["target"] == target
            result["source_records"][target] = {"path": str(path.relative_to(ROOT)), "sha256": core.digest(path.read_bytes())}
            records = []
            assert len({r["question_id"] for r in run["records"]}) == len(run["records"])
            for record in run["records"]:
                row = by_id[record["question_id"]]
                assert record["request_sha256"] == row["request_sha256"][target]
                if record.get("output_text") is not None:
                    assert core.digest(record["output_text"].encode()) == record["output_sha256"]
                records.append(assess(row, record, store))
            result["runs"].append({"target": target, "primary_score": run["score"], "sensitivity_score": summarize(records),
                                   "records": records, "usage_audit": usage_audit(run, ledger["attempts"])})
    finally:
        store.close()
    core.save_new(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--operator-reviewed", required=True, action="store_true")
    args = parser.parse_args()
    result = run_rescore(args.run_id, args.output)
    print(json.dumps({"new_model_calls": 0, "runs": [{"target": row["target"], "score": row["sensitivity_score"],
                                                     "usage": row["usage_audit"]} for row in result["runs"]]}, indent=2))
