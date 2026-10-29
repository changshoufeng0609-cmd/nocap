"""Offline correctness boundary probes; no provider calls or held-out inputs.

Run from any directory with the project's Python environment. Writes only the
adjacent correctness_results.json. Synthetic proposals test accepted pipeline
inputs, not the frequency with which any live model emits those proposals.
"""
from __future__ import annotations

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


BASE = dict(
    metric="revenue", metric_text="revenue", value_text="$96.221 billion",
    value=96.221, unit="USD", scale="billions", period_text="Q2 FY2027",
    period_type="quarter", comparison="none", bases=["GAAP"],
    forward_looking=False, entity="NVIDIA", segment=None,
)


def run_case(store, case_id, finding, source, *, expectation, control=False,
             quote=None, **changes):
    fields = {**BASE, **changes, "span_text": source if quote is None else quote}
    proposal = extract.ModelClaim(**fields)
    warnings = []
    claims, ranges = extract.build_claims([(0, source, [proposal])], warnings)
    assert len(claims) == 1, (case_id, warnings)
    claim = claims[0]
    verdict = verify.verify(claim, store)
    allowed_statuses = expectation["allowed_statuses"]
    return {
        "case_id": case_id,
        "finding": finding,
        "classification": ("control" if control else "known_residual_limitation"
                           if finding == "F4" else "new_reproduced_boundary_finding"),
        "source_text": source,
        "model_proposal": proposal.model_dump(mode="json"),
        "actual_claim": claim.model_dump(mode="json"),
        "located_value_ranges": ranges,
        "warnings": warnings,
        "actual_verdict": verdict.model_dump(mode="json"),
        "evidence_periods": [
            {"metric": fact.metric, "value": fact.value, "unit": fact.unit,
             "basis": fact.basis, "segment": fact.segment,
             "fiscal_year": fact.fiscal_year, "fiscal_period": fact.fiscal_period,
             "period_start": fact.period_start.isoformat() if fact.period_start else None,
             "period_end": fact.period_end.isoformat(), "accession": fact.accession}
            for fact in verdict.evidence
        ],
        "expectation": expectation,
        "meets_expectation": verdict.status in allowed_statuses,
        "issue_reproduced": not control and verdict.status == "verified"
                            and "verified" not in allowed_statuses,
    }


def main():
    snapshot = ROOT / "data/snapshot"
    store = Store(snapshot / "facts.sqlite", snapshot / "pr_facts")
    rows = []
    try:
        rows.append(run_case(
            store, "F1_scale_suffix_omitted", "F1",
            "NVIDIA Q2 FY2027 revenue was $96.221 million.",
            value_text="$96.221",
            expectation={"allowed_statuses": ["wrong", "unverifiable"],
                         "reason": "The source says millions; the filed total is 96.221 billion. A 1,000x scale error must not verify."},
        ))
        rows.append(run_case(
            store, "F1_negative_sign_omitted", "F1",
            "NVIDIA Q2 FY2027 revenue was -$96.221 billion.",
            expectation={"allowed_statuses": ["wrong", "unverifiable"],
                         "reason": "The source is negative; the evidence is positive. The sign outside value_text must remain authoritative."},
        ))
        rows.append(run_case(
            store, "F1_currency_symbol_omitted", "F1",
            "NVIDIA Q2 FY2027 revenue was €96.221 billion.",
            value_text="96.221 billion",
            expectation={"allowed_statuses": ["unverifiable"],
                         "reason": "EUR wording cannot be verified using a USD fact without an explicit supported conversion."},
        ))
        rows.append(run_case(
            store, "F2_comparison_period_leaks_to_current_value", "F2",
            "Revenue was $46.743 billion, up from $40 billion a year ago.",
            value_text="$46.743 billion", value=46.743,
            expectation={"allowed_statuses": ["wrong", "unverifiable"],
                         "reason": "The first amount is assigned Q2 FY2027; the later 'a year ago' qualifies the second amount, not the first."},
        ))
        rows.append(run_case(
            store, "F2_without_unrelated_comparison_control", "F2",
            "Revenue was $46.743 billion.",
            value_text="$46.743 billion", value=46.743, control=True,
            expectation={"allowed_statuses": ["wrong"],
                         "reason": "The same current-period amount is correctly rejected when the unrelated comparison is removed."},
        ))
        growth = dict(metric="revenue_growth", value_text="106%", value=106,
                      unit="%", scale="units", comparison="YoY")
        rows.append(run_case(
            store, "F3_growth_qualifier_in_metric_and_source", "F3",
            "NVIDIA revenue growth excluding China was 106% year over year.",
            metric_text="revenue growth excluding China", **growth,
            expectation={"allowed_statuses": ["unverifiable"],
                         "reason": "Consolidated revenue growth does not support revenue growth excluding China."},
        ))
        rows.append(run_case(
            store, "F3_growth_qualifier_in_source", "F3",
            "NVIDIA revenue excluding China grew 106% year over year.",
            metric_text="revenue growth", **growth,
            expectation={"allowed_statuses": ["unverifiable"],
                         "reason": "The complete retained source qualifier must constrain the proposed growth metric."},
        ))
        qualified_income = "NVIDIA Q2 FY2027 net income excluding stock compensation was $59.688 billion."
        income = dict(metric="net_income", metric_text="net income",
                      value_text="$59.688 billion", value=59.688)
        rows.append(run_case(
            store, "F4_number_only_quote_loses_qualifier", "F4", qualified_income,
            quote="$59.688 billion", **income,
            expectation={"allowed_statuses": ["unverifiable"],
                         "reason": "The source adjustment was omitted from the model description and quote; a consolidated fact cannot support that adjusted claim."},
        ))
        rows.append(run_case(
            store, "F4_full_quote_preserves_qualifier_control", "F4", qualified_income,
            control=True, **income,
            expectation={"allowed_statuses": ["unverifiable"],
                         "reason": "The J2 local-source guard correctly rejects the same source when the complete quote is retained."},
        ))
    finally:
        store.close()

    provenance_paths = [
        Path(__file__).resolve(), ROOT / "receipts/extract.py", ROOT / "receipts/verify.py",
        ROOT / "receipts/periods.py", ROOT / "receipts/store.py", ROOT / "receipts/types.py",
        snapshot / "facts.sqlite", *sorted((snapshot / "pr_facts/NVDA").glob("*.jsonl")),
    ]
    result = {
        "review_date": "2026-10-29",
        "scope": "Offline synthetic proposal boundary probes using the committed SEC snapshot; no held-out input, no outcome-driven tuning.",
        "provider_calls": 0, "sec_calls": 0, "cost_usd": 0,
        "live_model_frequency_measured": False,
        "findings": {
            "F1": {"severity": "P1", "location": "receipts/extract.py:1114",
                   "title": "Numeric normalization trusts a cropped value_text instead of the complete source token"},
            "F2": {"severity": "P1", "location": "receipts/verify.py:1382",
                   "title": "Comparison-period wording attached to a different amount can verify the current-period amount"},
            "F3": {"severity": "P1", "location": "receipts/verify.py:582",
                   "title": "Revenue-growth fast paths omit unsupported-qualification checks"},
            "F4": {"severity": "P1", "location": "receipts/extract.py:1100",
                   "title": "Number-only quotes can omit source metric qualifications",
                   "classification": "known_residual_limitation",
                   "not_a_new_discovery": True,
                   "prior_document": "docs/j2-review-2026-10-29.md",
                   "prior_document_scope": "J2 explicitly limits closure to its specific reviewed failures and does not claim arbitrary model extraction cannot mislabel or omit a claim.",
                   "implementation_disclosure": "receipts/verify.py:_local_level_description states that number-only spans cannot recover omitted semantics and still rely on metric_text."},
        },
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in provenance_paths},
        "summary": {"cases": len(rows), "issue_cases_reproduced": sum(row["issue_reproduced"] for row in rows),
                    "control_cases": sum(row["classification"] == "control" for row in rows),
                    "controls_met_expectation": all(row["meets_expectation"] for row in rows if row["classification"] == "control")},
        "cases": rows,
    }
    output = Path(__file__).with_name("correctness_results.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(ROOT)), **result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
