"""Closed-book financial QA: fixed public questions, independent scoring and a gold-blind gate.

This module never constructs a model client. Gold is used only during pre-registration
and scoring. gate_answer accepts a Question, an answer and the normal evidence store;
it cannot access the manifest's gold records and never repairs a model's answer.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import random
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from receipts import extract, verify
from receipts.store import Store
from receipts.types import Claim, Fact

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/snapshot"
MANIFEST = ROOT / "eval/qa_manifest.json"
SEED = 20261029
TARGETS = ("spark", "wonder", "spark-open-baseten", "glimmer")
METRICS = {
    "revenue": "revenue", "net_income": "net income", "eps_diluted": "diluted earnings per share",
    "operating_income": "operating income", "operating_cash_flow": "cash flow from operations",
    "gross_profit": "gross profit", "research_and_development": "research and development expense",
    "net_interest_income": "net interest income",
}
NAMES = {"AAPL": "Apple", "AMD": "Advanced Micro Devices", "AMZN": "Amazon", "BAC": "Bank of America",
         "GOOGL": "Alphabet", "JPM": "JPMorgan Chase", "META": "Meta Platforms", "MSFT": "Microsoft",
         "NFLX": "Netflix", "NVDA": "NVIDIA", "TSLA": "Tesla"}
UNIT_PATTERN = re.compile(r"^\s*(?P<sign>[-−])?\$\s*(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
                          r"\s*(?P<scale>thousand|million|billion|trillion)?\s*(?P<share>per (?:diluted )?share)?\s*$", re.I)
SCALES = {None: Decimal(1), "thousand": Decimal(1000), "million": Decimal(1000000),
          "billion": Decimal(1000000000), "trillion": Decimal(1000000000000)}


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str
    text: str
    ticker: str
    entity: str
    metric: str
    fiscal_year: int
    fiscal_period: str
    basis: Literal["GAAP"]
    unit: Literal["USD", "USD/share"]
    as_of: str
    maximum_quantum: str


class RawAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["answer", "abstain"]
    entity: str | None
    metric: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    basis: str | None
    unit: str | None
    value_text: str | None
    reason: str

    @model_validator(mode="after")
    def shape(self):
        names = ("entity", "metric", "fiscal_year", "fiscal_period", "basis", "unit", "value_text")
        if self.status == "abstain" and any(getattr(self, name) is not None for name in names):
            raise ValueError("An abstention must not hide a numeric answer")
        if self.status == "answer" and any(getattr(self, name) is None for name in names):
            raise ValueError("A numeric answer must identify its financial statement")
        if len(self.reason) > 600 or any(isinstance(getattr(self, name), str) and len(getattr(self, name)) > 180 for name in names):
            raise ValueError("Answer fields exceed the bounded contract")
        return self


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_hash(data: Any) -> str:
    return digest(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def save_new(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def evidence_hashes(directory: Path = SNAPSHOT) -> dict[str, str]:
    paths = [directory / "facts.sqlite", directory / "manifest.json", *sorted((directory / "pr_facts").glob("**/*.jsonl"))]
    return {str(path.relative_to(directory)): digest(path.read_bytes()) for path in paths}


def source_hashes() -> dict[str, str]:
    paths = [ROOT / "eval/qa_core.py", ROOT / "eval/qa_benchmark.py"]
    paths += [ROOT / "receipts" / name for name in ("extract.py", "verify.py", "store.py", "periods.py", "types.py", "numeric.py")]
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths if path.exists()}


def parse_answer(text: str) -> RawAnswer:
    # Exact enclosing JSON fences are allowed, but surrounding prose is not
    # silently removed: it could contain additional contradictory statements.
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*)\n```", text, flags=re.I)
    if match:
        text = match.group(1).strip()
    if len(text.encode()) > 16000:
        raise ValueError("Oversized response")
    return RawAnswer.model_validate_json(text)


def number(answer: RawAnswer) -> tuple[Decimal, Decimal, Decimal]:
    """Full-unit number, written precision, and magnitude; no evidence or gold access."""
    match = UNIT_PATTERN.fullmatch(answer.value_text or "")
    if not match:
        raise ValueError("Use one explicitly denominated dollar amount, not a range or prose")
    if answer.unit not in ("USD", "USD/share") or bool(match["share"]) != (answer.unit == "USD/share"):
        raise ValueError("Value expression and declared unit disagree")
    magnitude = SCALES[(match["scale"] or "").lower() or None]
    if answer.unit == "USD/share" and magnitude != 1:
        raise ValueError("Scaled per-share answers are unsupported")
    digits = match["number"].replace(",", "")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    value = Decimal(digits) * magnitude * (-1 if match["sign"] else 1)
    quantum = Decimal(10) ** -decimals * magnitude
    if not value.is_finite() or not math.isfinite(float(value)):
        raise ValueError("Non-finite answer")
    return value, quantum, magnitude


def binding_error(question: Question, answer: RawAnswer, store: Any) -> str | None:
    resolved = store.resolve_entity(answer.entity or "")
    if resolved is None or resolved[1] != question.ticker:
        return "entity_mismatch_or_unknown"
    # Store normalization drops parentheticals for ordinary draft aliases. A raw
    # answer must not hide an unbound company or restriction in those parentheses.
    clean = lambda value: re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()
    names = {question.entity, question.ticker}
    for row in store._rows("SELECT ticker, name, aliases FROM companies"):
        if row["ticker"] == question.ticker:
            names.update([row["name"], *json.loads(row["aliases"])])
    if clean(answer.entity or "") not in {clean(name) for name in names}:
        return "entity_mismatch_or_unknown"
    for name in ("metric", "fiscal_year", "fiscal_period", "basis", "unit"):
        if getattr(answer, name) != getattr(question, name):
            return f"{name}_mismatch"
    try:
        _, quantum, _ = number(answer)
    except ValueError:
        return "invalid_numeric_expression"
    if quantum > Decimal(question.maximum_quantum):
        return "insufficient_numeric_precision"
    return None


def gate_answer(question: Question, answer: RawAnswer, store: Any) -> dict[str, Any]:
    """Gold-blind publication gate: accept the supplied number or withhold it."""
    if answer.status == "abstain":
        return {"accepted": False, "reason": "model_abstained", "verdict": None}
    if problem := binding_error(question, answer, store):
        return {"accepted": False, "reason": problem, "verdict": None}
    value, _, magnitude = number(answer)
    scale = {Decimal(1): "units", Decimal(1000): "thousands", Decimal(1000000): "millions", Decimal(1000000000): "billions"}.get(magnitude)
    raw_value = value / magnitude
    if scale is None:  # The shared Claim contract stops at billions.
        scale, raw_value = "billions", value / Decimal(1000000000)
    period = f"{answer.fiscal_period} FY{answer.fiscal_year}"
    sentence = f"{answer.entity} {answer.basis} {METRICS[answer.metric]} for {period} was {answer.value_text}."
    claim = Claim(id="qa1", entity=answer.entity, metric=answer.metric, metric_text=METRICS[answer.metric],
                  value_text=answer.value_text, value=float(raw_value), unit=answer.unit, scale=scale,
                  period_text=period, period_type="annual" if answer.fiscal_period == "FY" else "quarter",
                  comparison="none", basis=answer.basis, forward_looking=False,
                  span_text=sentence, span_start=0, span_end=len(sentence))
    verdict = verify.verify(claim, store, as_of=question.as_of, context=f"{question.entity}, {period}")
    return {"accepted": verdict.status == "verified", "reason": verdict.reason,
            "claim": claim.model_dump(mode="json"), "verdict": verdict.model_dump(mode="json")}


def score_answer(question: Question, answer: RawAnswer, gold: dict, store: Any) -> str:
    """Independent numeric gold scoring, not the verifier's status as a label."""
    if answer.status == "abstain":
        return "abstain"
    if binding_error(question, answer, store):
        return "wrong"
    value, _, _ = number(answer)
    quantum = Decimal(question.maximum_quantum)
    expected = Decimal(gold["value_decimal"])
    # Compare the specified reporting precision, independently of verify.py.
    return "correct" if (value / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP) == (
        expected / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP) else "wrong"


def aggregate(records: list[dict], planned: int = 50) -> dict:
    counts = Counter(row["raw_outcome"] for row in records)
    accepted = [row for row in records if row.get("gate", {}).get("accepted") is True]
    correct_accepted = sum(row["raw_outcome"] == "correct" for row in accepted)
    answered = counts["correct"] + counts["wrong"]
    return {"planned": planned, "completed_records": len(records), "not_run": planned - len(records),
            "raw_correct": counts["correct"], "raw_wrong": counts["wrong"], "raw_abstain": counts["abstain"],
            "format_errors": counts["format_error"], "api_errors": counts["api_error"],
            "raw_accuracy_all": counts["correct"] / planned,
            "raw_answer_precision": counts["correct"] / answered if answered else None,
            "raw_answer_coverage": answered / planned,
            "verified_accepted": len(accepted), "verified_correct": correct_accepted,
            "verified_precision": correct_accepted / len(accepted) if accepted else None,
            "verified_coverage": len(accepted) / planned,
            "unsafe_passed": len(accepted) - correct_accepted}


def gold_answer(question: Question, gold: dict) -> RawAnswer:
    value = Decimal(gold["value_decimal"])
    if question.unit == "USD/share":
        expression = f"${value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)} per share"
    else:
        expression = f"${(value / Decimal(1000000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)} million"
    if expression.startswith("$-"):
        expression = "-$" + expression[2:]
    return RawAnswer(status="answer", entity=question.entity, metric=question.metric,
                     fiscal_year=question.fiscal_year, fiscal_period=question.fiscal_period, basis=question.basis,
                     unit=question.unit, value_text=expression, reason="Offline gold fixture; never a model result")


def prepare_manifest(request_builder) -> dict:
    store = Store(SNAPSHOT / "facts.sqlite", SNAPSHOT / "pr_facts")
    cutoff = json.loads((SNAPSHOT / "manifest.json").read_text())["built_at"]
    rng, candidates = random.Random(SEED), {}
    try:
        for ticker in sorted(NAMES):
            cik, _ = store.resolve_entity(ticker)
            by_key = {}
            for metric in METRICS:
                for fact in store.facts(cik, metric, basis="GAAP", as_of=datetime.fromisoformat(cutoff.replace("Z", "+00:00"))):
                    if fact.fiscal_period not in {"Q1", "Q2", "Q3", "Q4"} or fact.fiscal_year is None:
                        continue
                    if fact.segment is not None or fact.unit not in {"USD", "USD/share"}:
                        continue
                    if not fact.period_start or not 70 <= (fact.period_end - fact.period_start).days <= 110:
                        continue
                    key = (metric, fact.fiscal_year, fact.fiscal_period, fact.period_end.isoformat(), fact.unit)
                    by_key.setdefault(key, []).append(fact)
            pool = []
            for key, facts in sorted(by_key.items()):
                # Only unambiguous versions enter the pre-registered question set.
                values = {str(Decimal(str(f.value)).normalize()) for f in facts}
                if len(values) != 1 or not all(math.isfinite(f.value) for f in facts):
                    continue
                metric, year, period, period_end, unit = key
                quantum = "0.01" if unit == "USD/share" else "1000000"
                precision = "USD per share to the nearest cent" if unit == "USD/share" else "USD millions to the nearest million"
                question = Question(id=f"qa-{ticker}-{year}{period}-{metric}", ticker=ticker, entity=NAMES[ticker],
                                    metric=metric, fiscal_year=year, fiscal_period=period, basis="GAAP", unit=unit,
                                    maximum_quantum=quantum, as_of=cutoff,
                                    text=f"What was {NAMES[ticker]}'s GAAP {METRICS[metric]} for {period} FY{year} (quarter ended {period_end}), using only information public by {cutoff}? Give {precision}.")
                gold = {"value_decimal": str(Decimal(str(facts[0].value))), "unit": unit, "period_end": period_end,
                        "sources": [f.model_dump(mode="json") for f in facts], "provisional": True}
                if gate_answer(question, gold_answer(question, gold), store)["accepted"]:
                    pool.append({"question": question.model_dump(), "gold": gold})
            candidates[ticker] = pool

        selected = []
        # Four different metrics and four fiscal periods per company when available;
        # the remaining six companies receive one additional distinct combination.
        extra = set(rng.sample(sorted(NAMES), 6))
        for ticker in sorted(NAMES):
            pool = candidates[ticker].copy()
            rng.shuffle(pool)
            chosen, metrics, periods = [], set(), set()
            for row in pool:
                q = row["question"]
                period_key = (q["fiscal_year"], q["fiscal_period"])
                if q["metric"] not in metrics and period_key not in periods:
                    chosen.append(row); metrics.add(q["metric"]); periods.add(period_key)
                if len(chosen) == 4:
                    break
            if len(chosen) != 4:
                raise ValueError(f"Insufficient diverse eligible facts for {ticker}")
            if ticker in extra:
                chosen.append(next(row for row in pool if row not in chosen and row["question"]["metric"] not in metrics))
            selected.extend(chosen)
        selected.sort(key=lambda row: row["question"]["id"])
        assert len(selected) == 50 and len({row["question"]["id"] for row in selected}) == 50
        for row in selected:
            question = Question.model_validate(row["question"])
            row["question_sha256"] = json_hash(row["question"])
            row["gold_sha256"] = json_hash(row["gold"])
            row["request_sha256"] = {target: json_hash(request_builder(question, target)) for target in TARGETS}
        return {"version": 1, "scope": "Closed-book financial QA, provisional public-snapshot dev questions; not held-out or retrieval-augmented QA",
                "seed": SEED, "question_count": 50, "cutoff": cutoff,
                "selection": "Sorted public snapshot candidates; all visible versions agree; gold fixture must pass existing verifier; Random(seed), four distinct metrics and periods per company, one extra metric for six seeded companies; fixed before any model answer",
                "gold_policy": "All eligible visible versions must have exactly the same full-unit value; provisional labels await human review",
                "raw_prompt_policy": "Question and generic output schema only; no source texts, source URLs, gold values, search, retrieval or tools",
                "gate_policy": "Gold-blind question binding then existing deterministic verifier; accepts or withholds the original number, never corrects it",
                "scoring_policy": "Independent Decimal comparison at requested nearest-million/cent precision; abstention, malformed and failed calls separate; accepted=0 yields null precision",
                "ticker_counts": dict(Counter(row["question"]["ticker"] for row in selected)),
                "metric_counts": dict(Counter(row["question"]["metric"] for row in selected)),
                "period_counts": dict(Counter(f'{row["question"]["fiscal_period"]} FY{row["question"]["fiscal_year"]}' for row in selected)),
                "snapshot_hashes": evidence_hashes(), "source_hashes": source_hashes(), "items": selected}
    finally:
        store.close()
