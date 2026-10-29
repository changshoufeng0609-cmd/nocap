"""Export public QA records for the static benchmark page. Offline; no provider calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import qa_core as core

LABELS = {"spark": ("Muse Spark 1.3", "Meta Model API"), "wonder": ("Muse Wonder 1.0", "Meta Model API"),
          "spark-open-baseten": ("Muse Spark Open", "Baseten"), "glimmer": ("Muse Glimmer", "Modal · self-hosted H100")}


def publish(run_id: str, *, sensitivity_path: Path | None = None, output: Path | None = None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid run ID")
    manifest = json.loads(core.MANIFEST.read_text())
    manifest_hash = core.digest(core.MANIFEST.read_bytes())
    sensitivity = json.loads(sensitivity_path.read_text()) if sensitivity_path else None
    if sensitivity:
        assert sensitivity["manifest_sha256"] == manifest_hash and sensitivity["new_model_calls"] == 0
    sensitivity_by_target = {run["target"]: run for run in (sensitivity or {}).get("runs", [])}
    runs, cases = [], []
    source_records = {}
    answers_by_target = {}
    for target in core.TARGETS:
        source = ROOT / "eval/qa_runs" / run_id / f"{target}.json"
        raw = json.loads(source.read_text()) if source.exists() else None
        label, hosting = LABELS[target]
        extra = sensitivity_by_target.get(target)
        attempts = [row for row in (sensitivity or {}).get("ledger", {}).get("attempts", []) if row["target"] == target]
        if raw:
            assert raw["manifest_sha256"] == manifest_hash and raw["target"] == target and raw["planned"] == 50
            ids = [record["question_id"] for record in raw["records"]]
            assert len(set(ids)) == len(ids)
            assert set(ids) <= {row["question"]["id"] for row in manifest["items"]}
            if sensitivity:
                assert sensitivity["source_records"][target]["sha256"] == core.digest(source.read_bytes())
            source_records[target] = {"path": str(source.relative_to(ROOT)), "sha256": core.digest(source.read_bytes())}
        answers_by_target[target] = {row["question_id"]: row for row in (raw or {}).get("records", [])}
        runs.append({"target": target, "label": label, "hosting": hosting,
                     "status": raw["status"] if raw else "not_run", "planned": 50,
                     "calls": len((raw or {}).get("records", [])), "primary_score": (raw or {}).get("score"),
                     "sensitivity_score": (extra or {}).get("sensitivity_score"),
                     "started_at": (raw or {}).get("started_at"), "finished_at": (raw or {}).get("finished_at"),
                     "token_api_cost_usd": (raw or {}).get("token_api_cost_usd"),
                     "charged_or_reserved_usd": sum(row["charged"] for row in attempts) / 1e6 if sensitivity and target != "glimmer" else None,
                     "unknown_reserved_usd": sum(row["charged"] for row in attempts if row["status"] != "known_usage") / 1e6 if sensitivity and target != "glimmer" else None,
                     "unknown_usage_calls": (raw or {}).get("unknown_usage_calls"),
                     "latency_median_s": (raw or {}).get("latency_median_s"), "wall_s": (raw or {}).get("wall_s"),
                     "max_output_tokens": (raw or {}).get("max_output_tokens"),
                     "reasoning_effort": (raw or {}).get("reasoning_effort"),
                     "concurrency": (raw or {}).get("concurrency"), "gpu_usage_separate": target == "glimmer"})
    for row in manifest["items"]:
        question = row["question"]
        sources = list({(fact["url"], fact["accession"]): {key: fact[key] for key in ("url", "accession", "form", "accepted_at", "source")}
                        for fact in row["gold"]["sources"]}.values())
        responses = []
        for target in core.TARGETS:
            record = answers_by_target[target].get(question["id"])
            extra = next((r for r in sensitivity_by_target.get(target, {}).get("records", []) if r["question_id"] == question["id"]), None)
            raw = (record or {}).get("raw_answer") or {}
            responses.append({"target": target, "label": LABELS[target][0], "raw_outcome": (record or {}).get("raw_outcome", "not_run"),
                              "value_text": raw.get("value_text"), "entity": raw.get("entity"), "unit": raw.get("unit"),
                              "period": raw.get("fiscal_period"), "fiscal_year": raw.get("fiscal_year"),
                              "reason": raw.get("reason"), "primary_accepted": (record or {}).get("gate", {}).get("accepted"),
                              "primary_reason": (record or {}).get("gate", {}).get("reason"),
                              "sensitivity_outcome": (extra or {}).get("outcome"),
                              "sensitivity_accepted": (extra or {}).get("gate", {}).get("accepted"),
                              "sensitivity_reason": (extra or {}).get("gate", {}).get("reason"),
                              "normalizations": (extra or {}).get("normalization_changes", {}),
                              "model_output_text": (record or {}).get("output_text"),
                              "output_sha256": (record or {}).get("output_sha256")})
        cases.append({"question": question, "reference_value": core.gold_answer(core.Question.model_validate(question), row["gold"]).value_text,
                      "source_value_full_units": row["gold"]["value_decimal"], "sources": sources, "responses": responses})
    payload = {"title": "Closed-book financial Q&A", "run_id": run_id, "manifest_sha256": manifest_hash,
               "seed": manifest["seed"], "cutoff": manifest["cutoff"], "question_count": 50,
               "ticker_counts": manifest["ticker_counts"], "metric_counts": manifest["metric_counts"],
               "period_counts": manifest["period_counts"], "runs": runs, "cases": cases,
               "sensitivity_policy": (sensitivity or {}).get("normalization_policy"),
               "sensitivity_sha256": core.digest(sensitivity_path.read_bytes()) if sensitivity_path else None,
               "gpu_cost_context": {"observed_shared_total_usd": 1.25997788,
                                    "observed_successful_session_usd": 0.82043920,
                                    "scope": "Three shared Modal validation attempts; includes warmup, Private browser validation and Glimmer QA. Not a QA-only allocation; provider metering may lag.",
                                    "source_path": "docs/private-http-live-validation-2026-10-29.json"},
               "source_records": source_records, "methods_path": "eval/qa_methods.md",
               "status": "complete" if sensitivity and len(runs) == 4 and all(run["status"] == "complete" for run in runs) else "in_progress"}
    destination = output or ROOT / "web/app/benchmarks/results.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sensitivity", type=Path)
    args = parser.parse_args()
    result = publish(args.run_id, sensitivity_path=args.sensitivity)
    print(json.dumps({"status": result["status"], "questions": len(result["cases"]), "new_model_calls": 0}))
