"""Qualified figures are never confirmed by evidence reported on another basis (eval/qualifier_robustness.py).

The grid and the single cases use typed claims over the committed snapshot; no model, network or SEC call.
"""
import sys
from pathlib import Path

import pytest

from receipts.store import Store
from receipts.verify import basis_conflict, distinct_measure, verify

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import qualifier_robustness as grid  # noqa: E402

NON_GAAP_PHRASES = ("on an adjusted basis", "on a non-GAAP basis", "on a managed basis")


@pytest.fixture(scope="module")
def snapshot():
    store = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    try:
        yield store
    finally:
        store.close()


def test_grid_never_confirms_a_qualified_figure_with_gaap_evidence(snapshot):
    result = grid.run(snapshot)
    assert result["controls"]["outcomes"] == {"verified": 16}
    assert result["variants"] == 8 * 16 * 4 * 2 * 2
    assert "verified on GAAP evidence" not in result["outcomes"]
    # Only a stated non-GAAP basis may verify, against the company's own non-GAAP row, and only when the model
    # did not call the figure GAAP.
    for row in result["verified_rows"]:
        assert row["evidence_basis"] == ["non-GAAP"] and row["model_basis"] is None
        assert any(phrase.lower() in row["span"].lower() for phrase in NON_GAAP_PHRASES)


def claim(span, value_text="$9.86 billion", value=9.86, basis="GAAP", metric="net_income", metric_text="net income"):
    entry = (("Meta", metric_text, metric, value_text, value, "USD", "billions", "the third quarter of 2026"))
    return grid.claim(span, entry, metric_text, basis)


@pytest.mark.parametrize("span", [
    "On an adjusted basis, Meta's net income for the third quarter of 2026 was $9.86 billion.",
    "Meta's net income for the third quarter of 2026 was $9.86 billion, on an adjusted basis.",
    "Meta's net income for the third quarter of 2026 was $9.86 billion (non-GAAP).",
    "On a non-GAAP basis, Meta's revenue was $64.6 billion and its net income was $9.86 billion.",
])
def test_basis_phrase_bound_to_a_gaap_labelled_figure_leaves_it_unverifiable(snapshot, span):
    verdict = verify(claim(span), snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")
    assert basis_conflict(claim(span)) in verdict.explanation


@pytest.mark.parametrize("span,value_text,value,metric,metric_text", [
    # Another figure's modifiers stay with that figure.
    ("Adjusted diluted EPS was $2.62, and Meta's net income for the third quarter of 2026 was $9.86 billion.",
     "$9.86 billion", 9.86, "net_income", "net income"),
    # A sentence stating both bases supports the model's GAAP reading.
    ("Meta's GAAP and non-GAAP net income for the third quarter of 2026 were both $9.86 billion.",
     "$9.86 billion", 9.86, "net_income", "net income"),
    ("Meta's GAAP net income for the third quarter of 2026 was $9.86 billion, or more on an adjusted basis.",
     "$9.86 billion", 9.86, "net_income", "net income"),
])
def test_basis_wording_of_another_figure_or_both_bases_keeps_the_gaap_figure_verifiable(
        snapshot, span, value_text, value, metric, metric_text):
    c = claim(span, value_text, value, metric=metric, metric_text=metric_text)
    assert basis_conflict(c) is None and distinct_measure(c) is None
    assert verify(c, snapshot).status == "verified"


@pytest.mark.parametrize("span,measure", [
    ("Meta's net income for the third quarter of 2026 was $9.86 billion, on an organic basis.", "organic"),
    ("On a pro forma basis, Meta's net income for the third quarter of 2026 was $9.86 billion.", "pro forma"),
    ("Meta's net income for the third quarter of 2026 was $9.86 billion (on an underlying basis).",
     "on an underlying basis"),
])
def test_company_defined_measures_are_never_checked_against_reported_figures(snapshot, span, measure):
    for basis in (None, "GAAP"):
        verdict = verify(claim(span, basis=basis), snapshot)
        assert (verdict.status, verdict.reason) == ("unverifiable", "no_evidence")
        assert f"“{measure}”" in verdict.explanation
