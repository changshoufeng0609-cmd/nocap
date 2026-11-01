"""Canonical extraction labels must not erase unsupported metric qualifiers.

These synthetic proposals use the real bundled SEC snapshot. They test the
deterministic boundary, not how often a live model produces these proposals.
"""
from pathlib import Path

import pytest

from receipts.store import Store
from receipts.types import Claim
from receipts.verify import verify


@pytest.fixture(scope="module")
def snapshot():
    root = Path(__file__).resolve().parents[1] / "data/snapshot"
    store = Store(root / "facts.sqlite", root / "pr_facts")
    try:
        yield store
    finally:
        store.close()


def proposal(metric_text="net income", *, metric="net_income", value_text="$59.688 billion",
             value=59.688, unit="USD", scale="billions", span=None, **changes):
    span = span or f"NVIDIA {metric_text} was {value_text}."
    fields = dict(id="c1", metric=metric, metric_text=metric_text, value_text=value_text,
                  value=value, unit=unit, scale=scale, period_text="Q2 FY2027", period_type="quarter",
                  comparison="none", basis="GAAP", forward_looking=False, entity="NVIDIA", segment=None,
                  span_text=span, span_start=0, span_end=len(span))
    fields.update(changes)
    return Claim(**fields)


@pytest.mark.parametrize("description", [
    "net income excluding stock compensation",
    "European net income",
    "european net income",
    "net income from discontinued operations",
    "net income attributable to common stockholders",
    "net income before restructuring charges",
    "net income for European operations",
    "net income excluding Data Center",
    "net income plus operating income",
    "core profitability",  # unknown wording cannot borrow a canonical label
    "net income 除股酬外",
])
def test_qualified_net_income_cannot_match_the_consolidated_snapshot(snapshot, description):
    # This is the real filed total, so the old canonical fast path verified it.
    assert verify(proposal(), snapshot).status == "verified"
    verdict = verify(proposal(description), snapshot)
    assert verdict.status == "unverifiable"
    assert verdict.expected_value is None


@pytest.mark.parametrize("description", [
    "net income", "Net earnings", "GAAP net income", "total consolidated net income",
    "NVIDIA's quarterly net income", "second-quarter net income", "Q2 FY2027 net income",
])
def test_supported_net_income_wording_keeps_its_real_receipt(snapshot, description):
    verdict = verify(proposal(description), snapshot)
    assert verdict.status == "verified"
    assert any(f.value == 59_688_000_000 and f.metric == "net_income" for f in verdict.evidence)


def test_consolidated_name_uses_net_income_where_no_separate_consolidated_figure_is_stored(snapshot):
    # NVIDIA reports no noncontrolling interests, so no separate consolidated figure is stored: its net income is its
    # consolidated net income, and the true amount keeps its ✓ (a regression the owner caught on Nov 1).
    verdict = verify(proposal("total consolidated net income"), snapshot)
    assert (verdict.status, verdict.reason) == ("verified", "match"), verdict.explanation
    assert "no separate consolidated net income" in verdict.explanation


@pytest.mark.parametrize("metric,description,value_text,value,unit,scale", [
    ("revenue", "revenue excluding China", "$96.221 billion", 96.221, "USD", "billions"),
    ("operating_income", "operating income from discontinued operations", "$63.734 billion", 63.734, "USD", "billions"),
    ("eps_diluted", "diluted EPS excluding stock compensation", "$2.46", 2.46, "USD/share", "units"),
    ("eps_diluted", "European diluted EPS", "$2.46", 2.46, "USD/share", "units"),
    ("gross_margin", "gross margin excluding restructuring costs", "75.0%", 75, "%", "units"),
])
def test_other_canonical_metrics_do_not_drop_their_qualifiers(snapshot, metric, description, value_text, value, unit, scale):
    claim = proposal(description, metric=metric, value_text=value_text, value=value, unit=unit, scale=scale)
    assert verify(claim, snapshot).status == "unverifiable"


@pytest.mark.parametrize("span", [
    "European revenue was $96.221 billion, while net income was $59.688 billion.",
    "Net income was $59.688 billion, and revenue excluding China was $96.221 billion.",
    "Net income was $59.688 billion; operating income from discontinued operations was $63.734 billion.",
])
def test_another_claims_qualifier_does_not_poison_this_metric(snapshot, span):
    assert verify(proposal(span=span), snapshot).status == "verified"


@pytest.mark.parametrize("span", [
    "NVIDIA net income excluding stock compensation was $59.688 billion.",
    "NVIDIA European net income was $59.688 billion.",
    "NVIDIA net income from discontinued operations was $59.688 billion.",
    "NVIDIA net income was $59.688 billion excluding stock compensation.",
    "NVIDIA net income was $59.688 billion from discontinued operations.",
    "Revenue was $96.221 billion and European net income was $59.688 billion.",
    "Net income excluding stock compensation was $59.688 billion, and revenue was $96.221 billion.",
    "NVIDIA European Q2 FY2027 net income was $59.688 billion.",
    "NVIDIA net income excluding stock compensation in Q2 FY2027 was $59.688 billion.",
    "NVIDIA net income was $59.688 billion for European operations in Q2 FY2027.",
])
def test_value_bound_source_qualifier_survives_a_simplified_metric_description(snapshot, span):
    verdict = verify(proposal("net income", span=span), snapshot)
    assert verdict.status == "unverifiable" and verdict.expected_value is None


def test_each_metric_in_one_sentence_keeps_its_own_modifiers(snapshot):
    span = "European net income was $59.688 billion while revenue was $96.221 billion."
    assert verify(proposal(span=span), snapshot).status == "unverifiable"
    revenue = proposal("revenue", metric="revenue", value_text="$96.221 billion", value=96.221, span=span)
    assert verify(revenue, snapshot).status == "verified"


@pytest.mark.parametrize("description,value", [("diluted EPS", 2.46), ("EPS", 2.47),
                                              ("GAAP earnings per diluted share", 2.46),
                                              ("per diluted share", 2.46), ("earned per diluted share", 2.46)])
def test_supported_per_share_wording_and_basic_alternative_remain(snapshot, description, value):
    claim = proposal(description, metric="eps_diluted", value_text=f"${value}", value=value,
                     unit="USD/share", scale="units")
    assert verify(claim, snapshot).status == "verified"


def test_unqualified_eps_with_basic_enum_preserves_diluted_alternative(snapshot):
    claim = proposal("EPS", metric="eps_basic", value_text="$2.46", value=2.46,
                     unit="USD/share", scale="units")
    assert verify(claim, snapshot).status == "verified"


def test_supported_segment_still_requires_its_own_filing_fact(snapshot):
    claim = proposal("Data Center revenue", metric="revenue", value_text="$89.0 billion", value=89)
    verdict = verify(claim, snapshot)
    assert verdict.status == "verified"
    assert all(f.segment == "Data Center" for f in verdict.evidence)


@pytest.mark.parametrize("description,span", [
    ("reported quarterly sales", "NVIDIA reported quarterly sales of $96.221 billion."),
    ("revenue", "Revenue for the quarter ended July 26, 2026 was $96.221 billion."),
    ("revenue for the quarter ended July 26, 2026", "Revenue for the quarter ended July 26, 2026 was $96.221 billion."),
])
def test_known_period_scaffolding_and_reported_sales_are_supported(snapshot, description, span):
    claim = proposal(description, metric="revenue", value_text="$96.221 billion", value=96.221, span=span)
    assert verify(claim, snapshot).status == "verified"


def test_metric_guard_does_not_change_precision_or_diagnose_a_bad_unit(snapshot):
    assert verify(proposal(value_text="$59.7 billion", value=59.7), snapshot).status == "verified"
    wrong = verify(proposal(value_text="$59.8 billion", value=59.8), snapshot)
    assert wrong.status == "wrong" and wrong.reason == "value_mismatch"
    assert verify(proposal(value_text="59.688 shares", unit="shares"), snapshot).status == "unverifiable"


def test_supported_percent_comparison_still_uses_the_growth_path(snapshot):
    claim = proposal("revenue growth", metric="revenue_growth", value_text="106%", value=106,
                     unit="%", scale="units", comparison="YoY", span="Revenue grew 106% year over year.")
    assert verify(claim, snapshot).status == "verified"


@pytest.mark.parametrize("span", [
    "NVIDIA is reporting net income of $59.688 billion.",
    "NVIDIA has now reported net income of $59.688 billion.",
    "NVIDIA is generating net income of $59.688 billion.",
    "NVIDIA is producing net income of $59.688 billion.",
    "with net income of $59.688 billion",
    "where net income was $59.688 billion",
    "Net income also improved from $59.688 billion in the previous quarter.",
    "Net income compared with $59.688 billion a year earlier.",
    "Net income was significantly higher, totaling $59.688 billion.",
])
def test_reporting_and_comparison_syntax_does_not_change_the_level_metric(snapshot, span):
    # The claim's typed period determines the receipt; these cases isolate the
    # phrase guard, not the extractor's interpretation of relative dates.
    assert verify(proposal(span=span), snapshot).status == "verified"


@pytest.mark.parametrize("description,metric,value,unit,scale,basis", [
    ("Cash flow from operating activities", "operating_cash_flow", 24.077, "USD", "billions", "GAAP"),
    ("Net cash generated from operating activities", "operating_cash_flow", 24.077, "USD", "billions", "GAAP"),
    ("free cash flow (FCF)", "free_cash_flow", 21.341, "USD", "billions", "non-GAAP"),
    ("top line", "revenue", 96.221, "USD", "billions", "GAAP"),
])
def test_equivalent_financial_labels_keep_their_snapshot_receipt(snapshot, description, metric, value, unit, scale, basis):
    claim = proposal(description, metric=metric, value_text=f"${value} billion", value=value,
                     unit=unit, scale=scale, basis=basis)
    assert verify(claim, snapshot).status == "verified"


def test_bank_total_expenses_uses_its_own_operating_expense_fact(snapshot):
    claim = proposal("total expenses", metric="operating_expenses", value_text="$18.531 billion", value=18.531,
                     entity="Bank of America", period_text="Q1 FY2026", span="Total expenses were $18.531 billion.")
    verdict = verify(claim, snapshot)
    assert verdict.status == "verified"
    assert all(f.metric == "operating_expenses" for f in verdict.evidence)


@pytest.mark.parametrize("description,segment,entity,period,value_text,value,scale", [
    ("Gaming business revenue", "Gaming", "AMD", "Q1 FY2026", "$720 million", 720, "millions"),
    ("Sales and trading revenue", "Sales and trading", "Bank of America", "Q2 FY2026", "$7.098 billion", 7.098, "billions"),
])
def test_segment_name_and_business_label_are_not_extra_metric_phrases(
        snapshot, description, segment, entity, period, value_text, value, scale):
    claim = proposal(description, metric="segment_revenue", value_text=value_text, value=value, scale=scale,
                     segment=segment, entity=entity, period_text=period, span=f"{description} was {value_text}.")
    verdict = verify(claim, snapshot)
    assert verdict.status == "verified"
    assert all(f.segment == segment for f in verdict.evidence)
    unsupported = claim.model_copy(update={"metric_text": description + " excluding stock compensation"})
    assert verify(unsupported, snapshot).status == "unverifiable"


@pytest.mark.parametrize("metric,description,span,value_text,value,unit,scale", [
    ("eps_diluted", "earnings per diluted share", "GAAP and non-GAAP earnings per diluted share were both $2.46", "$2.46", 2.46, "USD/share", "units"),
    ("gross_margin", "gross margins", "GAAP and non-GAAP gross margins were both 75.0%", "75.0%", 75, "%", "units"),
])
def test_both_is_predicate_framing_not_a_metric_qualifier(snapshot, metric, description, span, value_text, value, unit, scale):
    claim = proposal(description, metric=metric, span=span, value_text=value_text, value=value, unit=unit, scale=scale)
    assert verify(claim, snapshot).status == "verified"


@pytest.mark.parametrize("span", [
    "NVIDIA is reporting European net income of $59.688 billion.",
    "NVIDIA has now reported net income excluding stock compensation of $59.688 billion.",
    "Net income with revenue was $59.688 billion.",
    "Net income significantly excluding stock compensation was $59.688 billion.",
    "Net income were both attributable to common stockholders at $59.688 billion.",
])
def test_sentence_framing_never_discards_a_financial_modifier(snapshot, span):
    assert verify(proposal(span=span), snapshot).status == "unverifiable"


@pytest.mark.parametrize("description", [
    "Cash flow from operating activities excluding stock compensation",
    "free cash flow (FCF) from discontinued operations",
    "European top line",
])
def test_added_financial_aliases_still_require_all_qualifiers(snapshot, description):
    metric = "operating_cash_flow" if description.startswith("Cash") else "free_cash_flow" if description.startswith("free") else "revenue"
    assert verify(proposal(description, metric=metric), snapshot).status == "unverifiable"


@pytest.mark.parametrize("metric,description,span,value_text,value", [
    ("research_and_development", "Research and development", "Research and development alone came to $20.1 billion", "$20.1 billion", 20.1),
    ("operating_cash_flow", "Cash flow from operations", "Cash flow from operations held up at $32.8 billion", "$32.8 billion", 32.8),
])
def test_meta_saved_example_predicates_preserve_their_filing_receipts(snapshot, metric, description, span, value_text, value):
    claim = proposal(description, metric=metric, span=span, value_text=value_text, value=value,
                     entity="Meta Platforms", period_text="third quarter of 2026", basis=None)
    verdict = verify(claim, snapshot)
    assert verdict.status == "verified"
    assert verdict.evidence and all(f.metric == metric for f in verdict.evidence)


@pytest.mark.parametrize("span", [
    "NVIDIA net income alone excluding stock compensation came to $59.688 billion.",
    "NVIDIA European net income alone came to $59.688 billion.",
    "NVIDIA net income from discontinued operations held up at $59.688 billion.",
])
def test_focusing_and_stability_predicates_cannot_hide_unsupported_qualifiers(snapshot, span):
    assert verify(proposal(span=span), snapshot).status == "unverifiable"
