"""Synthetic trap-scoring regressions; no corpus or model access."""
import importlib.util
import sys
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("trap_scoring_under_test",
                                             Path(__file__).resolve().parents[1] / "eval" / "eval.py")
ev = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ev
spec.loader.exec_module(ev)


def item(*, error=False, guidance=False):
    figure = {"span": [8, 13], "value_text": "$1.37"}
    return {"item_id": "synthetic", "ticker": "X", "text": "EPS was $1.37.",
            "planted": [{**figure, "type": "basis", "expected_reason": "basis_mismatch"}] if error else [],
            "accidental": [],
            "true_claims": [] if error else [{**figure, "fact_id": "synthetic", "forward_looking": guidance}],
            "sentences": [[0, 14]]}


def result(*statuses):
    claim = {"value_text": "$1.37", "span_text": "EPS was $1.37.", "span_start": 0, "span_end": 14}
    return {"synthetic": {"pairs": [[claim, {"status": status, "reason": "basis_mismatch" if status == "wrong"
                                           else "match"}] for status in statuses], "error": None}}


def report(summary, results):
    return ev.render_traps(summary, split="dev", verifier="synthetic", n_items=1,
                           stats=ev.RunStats(), results=results, cost_note="$0; no model calls")


def test_a_caught_error_with_a_verified_duplicate_reports_the_conflict():
    results = result("wrong", "verified")
    summary = ev.score_traps([item(error=True)], results)
    counts = summary["per_type"]["basis"]
    assert counts["n"] == counts["caught"] == counts["reason_ok"] == counts["conflicting_verified"] == 1
    assert counts.get("target_verified", 0) == 0  # that remains the uncaught-verified bucket
    assert summary["rows"][0]["all_caught"]  # existing any-wrong catch definition is unchanged
    assert summary["rows"][0]["errors"][0]["conflicting_verified"] is True
    assert "conflicting verdicts (✗+✓): 1 errors" in report(summary, results)


@pytest.mark.parametrize("statuses", [("wrong",), ("verified",), ("wrong", "unverifiable")])
def test_a_conflict_requires_both_wrong_and_verified(statuses):
    summary = ev.score_traps([item(error=True)], result(*statuses))
    assert summary["per_type"]["basis"].get("conflicting_verified", 0) == 0
    assert summary["rows"][0]["errors"][0]["conflicting_verified"] is False


@pytest.mark.parametrize("guidance", [False, True])
@pytest.mark.parametrize("statuses", [("approximate",), ("verified", "approximate")])
def test_approximate_true_figures_are_not_reported_as_verified(guidance, statuses):
    results = result(*statuses)
    summary = ev.score_traps([item(guidance=guidance)], results)
    key = "fl" if guidance else "true"
    assert summary["true_claims"][f"{key}_approximate"] == 1
    assert summary["true_claims"].get(f"{key}_verified", 0) == 0
    line = next(line for line in report(summary, results).splitlines()
                if line.startswith("guidance figures:" if guidance else "true figures (not errors):"))
    assert "✓ 0" in line and "≈ 1" in line


def test_an_approximation_does_not_hide_a_false_alarm_on_the_same_true_figure():
    summary = ev.score_traps([item()], result("wrong", "approximate"))
    assert summary["true_claims"]["true_wrong"] == 1
    assert summary["true_claims"].get("true_approximate", 0) == 0
