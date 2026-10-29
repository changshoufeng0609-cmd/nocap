"""Offline follow-up for Claude report findings 1, 3 and 4.

Synthetic proposals exercise deterministic boundaries; no live model frequency
or held-out performance is measured. Writes only adjacent follow-up results.
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


def run_case(store, case_id, finding, source, expectation, *, quote=None, control=False, **changes):
    fields = dict(metric="revenue", metric_text="revenue", value_text="$96.2 billion",
                  value=96.2, unit="USD", scale="billions", period_text="Q2 FY2027",
                  period_type="quarter", comparison="none", bases=["GAAP"],
                  forward_looking=False, entity="NVIDIA", segment=None,
                  span_text=source if quote is None else quote)
    fields.update(changes)
    proposal = extract.ModelClaim(**fields)
    warnings = []
    claims, ranges = extract.build_claims([(0, source, [proposal])], warnings)
    assert len(claims) == 1, (case_id, warnings)
    claim = claims[0]
    verdict = verify.verify(claim, store)
    hedge = verify.hedge_of(claim)
    meets = verdict.status in expectation["allowed_statuses"]
    return {
        "case_id": case_id, "claude_finding": finding, "control": control,
        "source_text": source, "model_proposal": proposal.model_dump(mode="json"),
        "actual_claim": claim.model_dump(mode="json"), "located_value_ranges": ranges,
        "warnings": warnings, "hedge_of": asdict(hedge) if hedge else None,
        "actual_verdict": verdict.model_dump(mode="json"),
        "evidence_periods": [
            {"metric": f.metric, "value": f.value, "unit": f.unit,
             "fiscal_year": f.fiscal_year, "fiscal_period": f.fiscal_period,
             "period_start": f.period_start.isoformat() if f.period_start else None,
             "period_end": f.period_end.isoformat(), "accession": f.accession}
            for f in verdict.evidence
        ],
        "expectation": expectation, "meets_expectation": meets,
        "issue_reproduced": not control and not meets,
    }


def main():
    snapshot = ROOT / "data/snapshot"
    store = Store(snapshot / "facts.sqlite", snapshot / "pr_facts")
    cases = []
    try:
        yoy_wire = "NVIDIA's revenue rose 106% from a year earlier to $46.7 billion in the second quarter of fiscal 2027."
        qoq_wire = "NVIDIA's revenue rose 18% from the previous quarter to $81.6 billion in the second quarter of fiscal 2027."
        bad_current_period = {
            "allowed_statuses": ["wrong", "unverifiable"],
            "preferred_reason": "period_mismatch",
            "explanation": "The endpoint amount belongs to the current quarter; the comparison phrase qualifies the preceding growth percentage, not that amount.",
        }
        for quote_mode, quote in [("full_source", None), ("minimal_metric_value", "revenue rose 106% from a year earlier to $46.7 billion")]:
            cases.append(run_case(store, "C1_wire_yoy_" + quote_mode, 1, yoy_wire,
                                  bad_current_period, quote=quote,
                                  value_text="$46.7 billion", value=46.7))
        cases.append(run_case(store, "C1_wire_qoq_full_source", 1, qoq_wire,
                              bad_current_period, value_text="$81.6 billion", value=81.6))
        cases.append(run_case(store, "C1_number_only_quote_control", 1, yoy_wire,
                              bad_current_period, quote="$46.7 billion", control=True,
                              value_text="$46.7 billion", value=46.7))

        growth_fields = dict(metric="revenue_growth", metric_text="revenue growth", unit="%", scale="units")
        for word, value, correct_comparison in [("year over year", 18, "YoY"), ("sequentially", 106, "QoQ")]:
            source = f"Revenue rose {value}% {word}."
            expectation = {
                "allowed_statuses": ["wrong", "unverifiable"],
                "preferred_reason": "period_mismatch",
                "explanation": "The explicit source comparison must constrain the result; matching the other comparison is insufficient.",
            }
            for proposed_comparison in ["none", correct_comparison]:
                cases.append(run_case(store, f"C3_{correct_comparison}_proposed_{proposed_comparison}", 3,
                                      source, expectation,
                                      comparison=proposed_comparison,
                                      value=value, value_text=f"{value}%", **growth_fields,
                                      control=proposed_comparison != "none"))

        up_to = "NVIDIA Q2 FY2027 revenue was up to $96.2 billion from $46.7 billion a year earlier."
        rose_to = "NVIDIA Q2 FY2027 revenue rose to $96.2 billion from $46.7 billion a year earlier."
        growth_endpoint_expectation = {
            "allowed_statuses": ["verified", "approximate", "unverifiable"],
            "explanation": "In the growth-to reading, 96.2B correctly rounds 96.221B. The phrase 'up to' can also denote an upper bound; ambiguity may yield ?, but should not force a wrong verdict. This is a linguistic expectation, not an independent human label.",
        }
        for source_label, source in [("up_to", up_to), ("rose_to", rose_to)]:
            for quote_mode, quote in [("full_source", None), ("number_only", "$96.2 billion")]:
                cases.append(run_case(store, f"C4_{source_label}_{quote_mode}", 4, source,
                                      growth_endpoint_expectation, quote=quote,
                                      control=source_label == "rose_to"))
    finally:
        store.close()

    paths = [Path(__file__).resolve(), ROOT / "receipts/extract.py", ROOT / "receipts/verify.py",
             ROOT / "receipts/periods.py", ROOT / "receipts/store.py", ROOT / "receipts/types.py",
             snapshot / "facts.sqlite", *sorted((snapshot / "pr_facts/NVDA").glob("*.jsonl"))]
    result = {
        "review_date": "2026-10-29", "source_report": "docs/reviews/opus-review-20261029/claude-report.md",
        "scope": "Offline cross-validation of Claude findings 1, 3 and 4; synthetic model proposals using the committed snapshot, with no held-out inputs or product changes.",
        "provider_calls": 0, "sec_calls": 0, "cost_usd": 0,
        "model_premises": {
            "1": "A level claim has comparison=none and retains the comparison phrase between the metric and amount in its quote. Full-source and minimal metric-to-value quotes are tested; a number-only quote is a control.",
            "3": "The model incorrectly proposes comparison=none despite explicit source YoY/QoQ wording. This proposed-field error is hypothetical, not measured from live outputs. Controls use the correct typed comparison.",
            "4": "The model proposes a current-period revenue level. 'Up to ... from ...' has a growth-endpoint reading; an unverifiable result is accepted because an upper-bound reading may also be possible. Both full and number-only quotes are tested.",
        },
        "reviewer_severity": {"1": "P1", "3": "P1", "4": "P2"},
        "no_p0_escalation": True,
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in paths},
        "summary": {
            "cases": len(cases),
            "issues_reproduced": sum(c["issue_reproduced"] for c in cases),
            "control_cases": sum(c["control"] for c in cases),
            "all_controls_meet_expectation": all(c["meets_expectation"] for c in cases if c["control"]),
        },
        "cases": cases,
    }
    output = Path(__file__).with_name("claude_followup_results.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(ROOT)), **result["summary"]}, ensure_ascii=False))
    for case in cases:
        print(case["case_id"], case["actual_verdict"]["status"], case["actual_verdict"]["reason"],
              "meets_expectation=" + str(case["meets_expectation"]))


if __name__ == "__main__":
    main()
