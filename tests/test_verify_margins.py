"""Company margins and absolute margin changes, using synthetic filing figures.

These tests exercise deterministic interpretations of cached-schema claims; no
model extraction or external evidence is needed.
"""
import pytest

from receipts.verify import verify
from test_verify import (
    FakeStore,
    NVDA_10Q_Q1FY27,
    NVDA_10Q_Q2FY26,
    NVDA_10Q_Q2FY27,
    NVDA_8K_Q2FY27,
    claim,
    fact,
)


def margin_claim(label="net margin", value_text="25.3%", value=25.3, *, span=None, **changes):
    options = dict(metric="gross_margin" if label == "gross margin" else "other",
                   metric_text=label, unit="%" if "%" in value_text else "other", scale="units",
                   basis="GAAP", span=span or f"{label} was {value_text}")
    options.update(changes)
    return claim(value_text, value, **options)


def ratio_facts(metric="net_income", current=.253, previous=.241, *, filing=NVDA_10Q_Q2FY27,
                previous_filing=None, basis="GAAP", qoq=None):
    """One filing's current and year-ago columns; exact dollars keep precision tests meaningful."""
    previous_filing = previous_filing or filing
    facts = [fact(filing, metric, current * 100e6, 2027, "Q2", basis=basis),
             fact(filing, "revenue", 100e6, 2027, "Q2"),
             fact(previous_filing, metric, previous * 80e6, 2026, "Q2", basis=basis),
             fact(previous_filing, "revenue", 80e6, 2026, "Q2")]
    if qoq is not None:
        facts += [fact(filing, metric, qoq * 90e6, 2027, "Q1", basis=basis),
                  fact(filing, "revenue", 90e6, 2027, "Q1")]
    return facts


def change(label="net margin", value_text="120 basis points", value=120, *, direction="expanded",
           comparison="YoY", source_comparison="year over year", **changes):
    return margin_claim(label, value_text, value, comparison=comparison,
                        span=f"{label} {direction} {value_text} {source_comparison}".strip(), **changes)


@pytest.mark.parametrize("label", ["net margin", "net profit margin", "consolidated net margin"])
def test_net_margin_uses_net_income_and_revenue_with_both_inputs_in_receipt_and_explanation(label):
    verdict = verify(margin_claim(label), FakeStore(ratio_facts()))
    assert verdict.status == "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} == {"net_income", "revenue"}
    assert {f.value for f in verdict.evidence} == {25.3e6, 100e6}
    assert "25.3" in verdict.explanation and "100" in verdict.explanation
    assert "net income" in verdict.explanation.lower() and "revenue" in verdict.explanation.lower()


def test_wrong_net_margin_is_reported_in_percent():
    verdict = verify(margin_claim(value_text="28.0%", value=28), FakeStore(ratio_facts()))
    assert (verdict.status, verdict.reason) == ("wrong", "value_mismatch")
    assert verdict.expected_value == pytest.approx(25.3)
    assert "25.3" in verdict.explanation and "100" in verdict.explanation


@pytest.mark.parametrize("label,span,segment", [
    ("Cloud net margin", None, None),
    ("net margin", None, "Cloud"),
    ("net margin", "Cloud net margin was 25.3%", None),
    ("net margin from continuing operations", None, None),
    ("net margin", "net margin excluding restructuring charges was 25.3%", None),
])
def test_net_margin_does_not_borrow_company_totals_for_qualified_or_segment_claims(label, span, segment):
    verdict = verify(margin_claim(label, span=span, segment=segment), FakeStore(ratio_facts()))
    assert verdict.status == "unverifiable", verdict.explanation
    assert verdict.expected_value is None


def test_non_gaap_net_margin_requires_non_gaap_income_and_cites_release_inputs():
    figures = ratio_facts(filing=NVDA_8K_Q2FY27, basis="non-GAAP")
    verdict = verify(margin_claim("adjusted net margin", basis=None), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    income = next(f for f in verdict.evidence if f.metric == "net_income")
    assert income.basis == "non-GAAP" and income.source == "press_release"
    assert {f.accession for f in verdict.evidence} == {NVDA_8K_Q2FY27[3]}
    assert verify(margin_claim(basis="non-GAAP"), FakeStore(ratio_facts())).status == "unverifiable"


@pytest.mark.parametrize("income,words", [(0, "0.0%"), (-.05, "-5.0%")])
def test_zero_and_negative_net_income_are_valid_margin_inputs(income, words):
    verdict = verify(margin_claim(value_text=words, value=income * 100),
                     FakeStore(ratio_facts(current=income)))
    assert verdict.status == "verified", verdict.explanation


@pytest.mark.parametrize("label,metric", [
    ("gross margin", "gross_profit"),
    ("operating margin", "operating_income"),
    ("net margin", "net_income"),
    ("net profit margin", "net_income"),
])
@pytest.mark.parametrize("value_text,value", [("120 basis points", 120), ("120 bps", 120),
                                               ("1.2 percentage points", 1.2), ("1.2 pts", 1.2)])
def test_margin_changes_use_same_filing_ratios_in_claim_units(label, metric, value_text, value):
    verdict = verify(change(label, value_text, value), FakeStore(ratio_facts(metric)))
    assert verdict.status == "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} == {metric, "revenue"}
    assert len(verdict.evidence) == 4
    assert {f.accession for f in verdict.evidence} == {NVDA_10Q_Q2FY27[3]}
    assert "year" in verdict.explanation.lower()
    assert "25.3" in verdict.explanation and "24.1" in verdict.explanation


@pytest.mark.parametrize("unit,scale", [("other", "units"), ("%", "units"), ("USD", "millions")])
def test_margin_change_unit_is_read_from_value_text_not_model_unit(unit, scale):
    verdict = verify(change(unit=unit, scale=scale), FakeStore(ratio_facts()))
    assert verdict.status == "verified", verdict.explanation


def test_wrong_margin_change_expected_value_is_in_basis_points():
    verdict = verify(change(value_text="130 bps", value=130), FakeStore(ratio_facts()))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(120)
    assert "basis point" in verdict.explanation.lower() or "bps" in verdict.explanation.lower()


def test_percentage_point_precision_uses_written_decimal_places():
    figures = ratio_facts("operating_income", current=.25344, previous=.232)
    rounded = change("operating margin", "2.1 percentage points", 2.1, direction="rose")
    # 2.10 also matches the printed endpoints (25.3 - 23.2); use a distinct
    # finer number to check that claim precision still constrains exact matches.
    precise = change("operating margin", "2.12 percentage points", 2.12, direction="rose")
    assert verify(rounded, FakeStore(figures)).status == "verified"
    verdict = verify(precise, FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(2.144)


def test_basis_point_precision_uses_written_decimal_places():
    figures = ratio_facts(current=.253044, previous=.241)
    assert verify(change(value_text="120 bps", value=120), FakeStore(figures)).status == "verified"
    # 120.0 matches printed endpoints. 120.2 matches neither calculation.
    verdict = verify(change(value_text="120.2 bps", value=120.2), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(120.44)


def test_margin_decline_is_signed_by_source_words():
    verdict = verify(change(value_text="50 bps", value=50, direction="fell"),
                     FakeStore(ratio_facts(previous=.258)))
    assert verdict.status == "verified", verdict.explanation
    assert "fell" in verdict.explanation.lower() or "decreased" in verdict.explanation.lower()
    wrong = verify(change(value_text="60 bps", value=60, direction="fell"),
                   FakeStore(ratio_facts(previous=.258)))
    assert wrong.status == "wrong" and wrong.expected_value == pytest.approx(-50)


def test_margin_change_can_cross_zero():
    verdict = verify(change(value_text="200 bps", value=200),
                     FakeStore(ratio_facts(current=.01, previous=-.01)))
    assert verdict.status == "verified", verdict.explanation


def test_source_yoy_comparison_overrides_model_qoq():
    figures = ratio_facts(qoq=.243)
    verdict = verify(change(comparison="QoQ"), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert "year" in verdict.explanation.lower()


def test_qoq_margin_change_uses_release_prior_quarter_column():
    figures = ratio_facts(filing=NVDA_8K_Q2FY27, qoq=.243)
    verdict = verify(change(value_text="100 bps", value=100, comparison="QoQ",
                            source_comparison="quarter over quarter"), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.fiscal_period for f in verdict.evidence} == {"Q1", "Q2"}
    assert {f.fiscal_year for f in verdict.evidence} == {2027}


@pytest.mark.parametrize("value,has_qoq,status", [(120, False, "verified"), (100, True, "verified"),
                                                 (99, False, "unverifiable"), (99, True, "wrong")])
def test_unstated_margin_comparison_follows_growth_rule(value, has_qoq, status):
    figures = ratio_facts(qoq=.243 if has_qoq else None)
    verdict = verify(change(value_text=f"{value} bps", value=value, comparison="none", source_comparison=""),
                     FakeStore(figures))
    assert verdict.status == status, verdict.explanation
    if status == "unverifiable":
        assert verdict.reason == "ambiguous"


def test_gross_margin_change_can_use_printed_margin_cells():
    # Printed 25.300% / 24.100% rates support a change to the nearest basis point.
    figures = [fact(NVDA_8K_Q2FY27, "gross_margin", 25.3, 2027, "Q2", unit="%"),
               fact(NVDA_8K_Q2FY27, "gross_margin", 24.1, 2026, "Q2", unit="%")]
    figures = [f.model_copy(update={"decimals": 3}) for f in figures]
    verdict = verify(change("gross margin"), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert len(verdict.evidence) == 2


def test_gross_margin_change_can_use_cost_of_revenue():
    figures = ratio_facts("cost_of_revenue", current=.747, previous=.759)
    verdict = verify(change("gross margin"), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} == {"cost_of_revenue", "revenue"}
    assert len(verdict.evidence) == 4


def test_margin_change_never_pairs_separate_filings():
    figures = ratio_facts(previous_filing=NVDA_10Q_Q2FY26)
    verdict = verify(change(), FakeStore(figures))
    assert (verdict.status, verdict.reason) == ("unverifiable", "no_evidence")
    assert "filing" in verdict.explanation.lower()
    assert "same" in verdict.explanation.lower() or "single" in verdict.explanation.lower()


def test_margin_change_never_mixes_revenue_and_income_filings():
    figures = ratio_facts()
    figures[1] = fact(NVDA_8K_Q2FY27, "revenue", 100e6, 2027, "Q2")
    verdict = verify(change(), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation
    assert "filing" in verdict.explanation.lower()


def test_qoq_margin_change_has_no_cross_filing_unrestated_fallback():
    figures = ratio_facts(qoq=.243)
    figures[-2] = fact(NVDA_10Q_Q1FY27, "net_income", .243 * 90e6, 2027, "Q1")
    figures[-1] = fact(NVDA_10Q_Q1FY27, "revenue", 90e6, 2027, "Q1")
    # The year-ago comparison is unchanged in the older filing, but task (b)
    # still requires the two margin periods in one filing.
    figures += [fact(NVDA_10Q_Q1FY27, "net_income", .241 * 80e6, 2026, "Q2"),
                fact(NVDA_10Q_Q1FY27, "revenue", 80e6, 2026, "Q2")]
    verdict = verify(change(value_text="100 bps", value=100, comparison="QoQ",
                            source_comparison="quarter over quarter"), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


@pytest.mark.parametrize("label", ["net margin", "operating margin", "gross margin"])
def test_margin_change_rejects_segment_wording(label):
    figures = ratio_facts({"net margin": "net_income", "operating margin": "operating_income",
                          "gross margin": "gross_profit"}[label])
    verdict = verify(change(f"Cloud {label}"), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


@pytest.mark.parametrize("zero_current,missing_prior", [(True, False), (False, True)])
def test_margin_change_needs_nonzero_denominators_and_both_periods(zero_current, missing_prior):
    figures = ratio_facts()
    if zero_current:
        figures[1] = figures[1].model_copy(update={"value": 0})
    if missing_prior:
        figures = figures[:2]
    verdict = verify(change(), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation
    assert verdict.expected_value is None


def test_non_gaap_margin_change_uses_both_adjusted_income_columns():
    figures = ratio_facts(filing=NVDA_8K_Q2FY27, basis="non-GAAP")
    verdict = verify(change(basis="non-GAAP"), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.basis for f in verdict.evidence if f.metric == "net_income"} == {"non-GAAP"}
    figures[2] = figures[2].model_copy(update={"basis": "GAAP"})
    assert verify(change(basis="non-GAAP"), FakeStore(figures)).status == "unverifiable"


def test_margin_change_does_not_use_future_pair_at_as_of():
    verdict = verify(change(), FakeStore(ratio_facts()), as_of="2026-08-26T20:30:00Z")
    assert verdict.status != "verified"
    assert verdict.reason in {"look_ahead", "no_evidence"}
    assert all(f.accepted_at.isoformat().startswith("2026-08-26T20:36") for f in verdict.evidence)


@pytest.mark.parametrize("span,previous", [
    ("Revenue fell $1 million; net margin rose 120 basis points year over year", .241),
    ("Revenue rose $1 million; net margin fell 120 basis points year over year", .265),
])
def test_margin_change_sign_belongs_to_its_own_value(span, previous):
    proposal = margin_claim(value_text="120 basis points", value=120, span=span, comparison="YoY")
    verdict = verify(proposal, FakeStore(ratio_facts(previous=previous)))
    assert verdict.status == "verified", verdict.explanation


@pytest.mark.parametrize("span", [
    "Cloud net margin expanded 120 basis points year over year",
    "net margin excluding stock compensation expanded 120 basis points year over year",
    "net margin expanded 120 basis points year over year from continuing operations",
])
def test_margin_change_uses_qualifiers_in_source_even_if_metric_text_omits_them(span):
    proposal = margin_claim(value_text="120 basis points", value=120, span=span, comparison="YoY")
    verdict = verify(proposal, FakeStore(ratio_facts()))
    assert verdict.status == "unverifiable", verdict.explanation


def test_margin_change_number_comes_from_source_not_model_percent_conversion():
    verdict = verify(change(value_text="120 bps", value=1.2, unit="%"), FakeStore(ratio_facts()))
    assert verdict.status == "verified", verdict.explanation


def test_non_gaap_margin_change_cannot_mix_adjusted_and_reported_denominators():
    figures = ratio_facts(filing=NVDA_8K_Q2FY27, basis="non-GAAP")
    # The release supplies adjusted revenue only for the current column. Its
    # net margin is 23%; the year-ago 24.1% used reported revenue, so the -110
    # bps difference is not a like-for-like adjusted-margin change.
    figures += [fact(NVDA_8K_Q2FY27, "revenue", 110e6, 2027, "Q2", basis="non-GAAP")]
    verdict = verify(change(value_text="110 bps", value=110, direction="fell", basis="non-GAAP"),
                     FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


def test_non_gaap_margin_change_uses_adjusted_denominators_when_both_are_filed():
    figures = ratio_facts(filing=NVDA_8K_Q2FY27, basis="non-GAAP")
    figures += [fact(NVDA_8K_Q2FY27, "revenue", 110e6, 2027, "Q2", basis="non-GAAP"),
                fact(NVDA_8K_Q2FY27, "revenue", 88e6, 2026, "Q2", basis="non-GAAP")]
    # 25.3/110 - 19.28/88 = 1.090909... percentage points.
    verdict = verify(change(value_text="109.1 bps", value=109.1, basis="non-GAAP"), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.basis for f in verdict.evidence if f.metric == "revenue"} == {"non-GAAP"}


def test_margin_change_rejects_q4_inputs_derived_across_filings():
    from datetime import date
    from test_verify import NVDA, NVDA_10K_FY26, NVDA_10Q_Q3FY26, PERIODS

    calendar = dict(PERIODS)
    calendar[(NVDA, 2025, "Q4")] = date(2024, 10, 28), date(2025, 1, 26)
    figures = []
    for metric, current, previous in [("net_income", 25.3e6, 19.28e6), ("revenue", 100e6, 80e6)]:
        current_fact = fact(NVDA_10K_FY26, metric, current, 2026, "Q4", source="derived",
                            derived_from=[NVDA_10Q_Q3FY26[3]])
        figures += [current_fact,
                    current_fact.model_copy(update={"value": previous, "fiscal_year": 2025,
                                                    "period_start": date(2024, 10, 28),
                                                    "period_end": date(2025, 1, 26)})]
    verdict = verify(change(period_text="Q4 FY2026"), FakeStore(figures, periods=calendar))
    assert verdict.status == "unverifiable", verdict.explanation
    assert "filing" in verdict.explanation.lower()


@pytest.mark.parametrize("span", [
    "net margin rose 25.3% year over year",
    "net margin expanded by 25.3%",
])
def test_relative_percent_margin_change_is_not_a_level_margin(span):
    verdict = verify(margin_claim(span=span), FakeStore(ratio_facts()))
    assert verdict.status == "unverifiable", verdict.explanation


@pytest.mark.parametrize("span", ["net margin rose to 25.3%", "net margin expanded to 25.3% year over year"])
def test_percentage_endpoint_remains_a_level_margin(span):
    verdict = verify(margin_claim(span=span), FakeStore(ratio_facts()))
    assert verdict.status == "verified", verdict.explanation


@pytest.mark.parametrize("value_text,value", [("100-120 bps", 110), ("100–120 bps", 110),
                                               ("1.0-1.2 percentage points", 1.1)])
def test_margin_change_range_does_not_become_its_last_endpoint(value_text, value):
    verdict = verify(change(value_text=value_text, value=value, direction="fell"),
                     FakeStore(ratio_facts(previous=.264)))
    assert verdict.status == "unverifiable", verdict.explanation
