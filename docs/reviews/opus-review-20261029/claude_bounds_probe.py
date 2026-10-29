"""Independently reproduce Claude CLI's US$ bound candidate, using no network.

Only synthetic proposals and the committed SEC snapshot are read. No held-out
data, credentials, environment files, providers or SEC requests are used.
Writes only the adjacent claude_bounds_results.json.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import socket
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def deny_network(*args, **kwargs):
    raise RuntimeError("Network use is forbidden in this offline review probe")


socket.create_connection = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network

from receipts import extract, verify  # noqa: E402
from receipts.store import Store  # noqa: E402


def run_case(store, pair_id, metric, operator, number, currency, quote_mode):
    value_text = f"{currency}{number} billion"
    metric_text = metric.replace("_", " ")
    source = f"NVIDIA Q2 FY2027 {metric_text} was {operator} {value_text}."
    quote = source if quote_mode == "full_source" else value_text
    proposal = extract.ModelClaim(
        metric=metric, metric_text=metric_text, value_text=value_text,
        value=float(number), unit="USD", scale="billions", period_text="Q2 FY2027",
        period_type="quarter", comparison="none", bases=["GAAP"],
        forward_looking=False, entity="NVIDIA", segment=None, span_text=quote,
    )
    warnings = []
    claims, ranges = extract.build_claims([(0, source, [proposal])], warnings)
    assert len(claims) == 1, (pair_id, currency, warnings)
    claim = claims[0]
    hedge = verify.hedge_of(claim)
    verdict = verify.verify(claim, store)
    source_start = source.index(quote)
    value_start = source.index(value_text)
    recovered_start, recovered_end = extract._source_bound_span(
        source, source_start, source_start + len(quote), value_start, value_text,
    )
    fact_value = 96_221_000_000 if metric == "revenue" else 59_688_000_000
    threshold = float(number) * 1_000_000_000
    source_predicate_true = fact_value > threshold if operator == "more than" else fact_value < threshold
    assert not source_predicate_true, (pair_id, fact_value, threshold)
    return {
        "case_id": f"{pair_id}_{'us_dollar' if currency == 'US$' else 'dollar_control'}",
        "pair_id": pair_id,
        "currency_spelling": currency,
        "quote_mode": quote_mode,
        "source_text": source,
        "model_proposal": proposal.model_dump(mode="json"),
        "actual_claim": claim.model_dump(mode="json"),
        "located_value_ranges": ranges,
        "source_bound_span": {
            "input_start": source_start, "input_end": source_start + len(quote),
            "output_start": recovered_start, "output_end": recovered_end,
            "output_text": source[recovered_start:recovered_end],
            "extended_left": recovered_start < source_start,
        },
        "hedge_of": asdict(hedge) if hedge else None,
        "warnings": warnings,
        "actual_verdict": verdict.model_dump(mode="json"),
        "evidence_periods": [
            {"metric": fact.metric, "value": fact.value, "unit": fact.unit,
             "fiscal_year": fact.fiscal_year, "fiscal_period": fact.fiscal_period,
             "period_start": fact.period_start.isoformat() if fact.period_start else None,
             "period_end": fact.period_end.isoformat(), "accession": fact.accession}
            for fact in verdict.evidence
        ],
        "expectation": {
            "status": "wrong", "reason": "value_mismatch",
            "source_predicate_true": source_predicate_true,
            "operator": operator, "strict": True,
            "filed_value_usd": fact_value, "threshold_usd": threshold,
            "explanation": "A strict source bound must be enforced without the equality rounding tolerance.",
        },
        "false_verified": verdict.status == "verified",
        "bound_recognized": hedge is not None,
        "status_correct": verdict.status == "wrong",
        "impact": ("false_verified" if verdict.status == "verified" else
                   "bound_ignored_but_amount_mismatch_still_wrong" if hedge is None else
                   "bound_correctly_enforced"),
    }


def main():
    snapshot = ROOT / "data/snapshot"
    store = Store(snapshot / "facts.sqlite", snapshot / "pr_facts")
    scenarios = [
        ("full_exact_lower_bound", "revenue", "more than", "96.221", "full_source"),
        ("full_far_lower_bound", "revenue", "more than", "97", "full_source"),
        ("full_rounded_upper_bound", "revenue", "less than", "96.2", "full_source"),
        ("full_rounded_lower_bound", "net_income", "more than", "59.7", "full_source"),
        ("number_only_exact_lower_bound", "revenue", "more than", "96.221", "number_only"),
        ("number_only_rounded_lower_bound", "net_income", "more than", "59.7", "number_only"),
    ]
    try:
        cases = [run_case(store, pair_id, metric, operator, number, currency, quote_mode)
                 for pair_id, metric, operator, number, quote_mode in scenarios
                 for currency in ("US$", "$")]
    finally:
        store.close()
    paths = [Path(__file__).resolve(), ROOT / "receipts/extract.py", ROOT / "receipts/verify.py",
             ROOT / "receipts/periods.py", ROOT / "receipts/store.py", ROOT / "receipts/types.py",
             snapshot / "facts.sqlite", *sorted((snapshot / "pr_facts/NVDA").glob("*.jsonl"))]
    result = {
        "review_date": "2026-10-29",
        "candidate_origin": "Claude CLI independent review; separately reproduced by the correctness reviewer",
        "scope": "Synthetic proposal boundary probes; no held-out inputs and no product changes.",
        "provider_calls": 0, "sec_calls": 0, "cost_usd": 0,
        "live_model_frequency_measured": False,
        "finding": {
            "severity": "P1",
            "confirmed": True,
            "title": "US$ currency spelling prevents recognition and source recovery of strict numeric bounds",
            "locations": ["receipts/verify.py:706", "receipts/verify.py:714", "receipts/extract.py:1060"],
            "mechanism": "hedge_of leaves US before the dollar sign; its final currency/sign trim does not remove US. The same source prefix prevents _source_bound_span from finding an adjacent operator. Equality matching then replaces the strict source bound.",
            "qualification": "The suggested > US$97B revenue case stays wrong because the equality mismatch exceeds tolerance. Exact-boundary and rounded-threshold cases demonstrate actual false Verified, not merely a weaker explanation.",
            "suggested_fix": "Normalize supported full currency tokens consistently for source anchoring and hedge recognition; preserve strict/inclusive bounds separately from value equality tolerance.",
        },
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in paths},
        "summary": {
            "cases": len(cases), "paired_scenarios": len(scenarios),
            "false_verified_us_dollar_cases": sum(case["false_verified"] for case in cases if case["currency_spelling"] == "US$"),
            "us_dollar_cases_wrong_despite_ignored_bound": sum(case["impact"] == "bound_ignored_but_amount_mismatch_still_wrong" for case in cases),
            "dollar_control_cases": sum(case["currency_spelling"] == "$" for case in cases),
            "all_dollar_controls_correct": all(case["status_correct"] and case["bound_recognized"] for case in cases if case["currency_spelling"] == "$"),
        },
        "cases": cases,
    }
    output = Path(__file__).with_name("claude_bounds_results.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(ROOT)), **result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
