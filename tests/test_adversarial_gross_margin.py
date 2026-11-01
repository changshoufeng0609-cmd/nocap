"""Gross margins retain the filing's reported rate and its own sales base."""
import pytest

from receipts.verify import verify
from test_verify import FakeStore, NVDA_10Q_Q2FY27, NVDA_8K_Q2FY27, fact
from test_verify_margins import change, margin_claim, ratio_facts


def cell(metric, value, *, fy=2027, filing=NVDA_8K_Q2FY27, **updates):
    unit = "%" if metric == "gross_margin" else "USD"
    return fact(filing, metric, value, fy, "Q2", unit=unit).model_copy(update=updates)


@pytest.mark.parametrize("numerator", ["gross_profit", "cost_of_revenue"])
def test_gross_margin_never_combines_two_filings(numerator):
    figures = [cell(numerator, 25e6 if numerator == "gross_profit" else 75e6),
               cell("revenue", 100e6, filing=NVDA_10Q_Q2FY27)]
    verdict = verify(margin_claim("gross margin", "25.0%", 25), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


def test_gross_margin_does_not_join_different_durations_of_one_filing():
    figures = [cell("gross_profit", 25e6), cell("revenue", 100e6)]
    figures[1] = figures[1].model_copy(update={"period_start": figures[1].period_start.replace(day=28)})
    verdict = verify(margin_claim("gross margin", "25.0%", 25), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


def test_stated_gross_margin_level_wins_over_recomputed_ratio():
    figures = ratio_facts("gross_profit", current=.253)
    figures += [cell("gross_margin", 25.4, locator="Gross margin | 25.4%")]
    verdict = verify(margin_claim("gross margin", "25.30%", 25.30), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(25.4)
    assert {f.metric for f in verdict.evidence} == {"gross_margin"}


def test_stated_gross_margin_rates_win_for_point_changes():
    figures = ratio_facts("gross_profit", current=.253, previous=.241)
    figures += [cell("gross_margin", 25.4, decimals=2), cell("gross_margin", 24.4, fy=2026, decimals=2)]
    verdict = verify(change("gross margin", "120 bps", 120), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(100)
    assert {f.metric for f in verdict.evidence} == {"gross_margin"}


def retail_figures(*, sales=False, gross=True, merchandise=False):
    figures = [cell("revenue", 102e6, locator="Total revenues | 102"),
               cell("cost_of_revenue", 75e6, locator="Merchandise costs | 75" if merchandise else "Cost of sales | 75")]
    if gross:
        figures.append(cell("gross_profit", 25e6, locator="Gross profit | 25"))
    if sales:
        figures.append(cell("revenue", 100e6, concept="us-gaap:SalesRevenueNet", locator="Net sales | 100"))
    return figures


@pytest.mark.parametrize("gross,merchandise", [(True, False), (False, True)])
def test_retail_gross_margin_without_its_sales_base_is_unknown(gross, merchandise):
    verdict = verify(margin_claim("gross margin", "25.0%", 25),
                     FakeStore(retail_figures(gross=gross, merchandise=merchandise)))
    assert verdict.status == "unverifiable", verdict.explanation
    assert "net sales" in verdict.explanation.lower()


@pytest.mark.parametrize("gross,merchandise", [(True, False), (False, True)])
def test_retail_gross_margin_uses_same_filing_tagged_net_sales(gross, merchandise):
    verdict = verify(margin_claim("gross margin", "25.0%", 25),
                     FakeStore(retail_figures(sales=True, gross=gross, merchandise=merchandise)))
    assert verdict.status == "verified", verdict.explanation
    denominators = [f for f in verdict.evidence if f.metric == "revenue"]
    assert denominators and all(f.value == 100e6 for f in denominators)


def test_retail_gross_margin_does_not_borrow_sales_from_another_filing():
    figures = retail_figures()
    figures.append(cell("revenue", 100e6, filing=NVDA_10Q_Q2FY27, concept="us-gaap:SalesRevenueNet"))
    verdict = verify(margin_claim("gross margin", "25.0%", 25), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


def test_retail_gross_margin_rejects_conflicting_net_sales_cells():
    figures = retail_figures(sales=True)
    figures.append(cell("revenue", 101e6, concept="us-gaap:SalesRevenueNet", locator="Net sales | 101"))
    verdict = verify(margin_claim("gross margin", "25.0%", 25), FakeStore(figures))
    assert verdict.status == "unverifiable", verdict.explanation


@pytest.mark.parametrize("value_text,value", [("55.4%", 55.4), ("55.00%", 55.0)])
def test_coarse_stated_margin_does_not_confirm_or_refute_a_precise_claim(value_text, value):
    # AMD's release prints 55%, which neither establishes nor contradicts 55.4%.
    figures = [cell("gross_margin", 55, decimals=0, locator="Gross margin | 55%")]
    verdict = verify(margin_claim("gross margin", value_text, value), FakeStore(figures))
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous"), verdict.explanation
    assert "rounded" in verdict.explanation.lower()
    assert "55%" in verdict.explanation


def test_precise_stated_margin_still_verifies_beside_a_coarser_rate():
    figures = [cell("gross_margin", 55, decimals=0),
               cell("gross_margin", 55.4, filing=NVDA_10Q_Q2FY27, decimals=1)]
    verdict = verify(margin_claim("gross margin", "55.4%", 55.4), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation


def test_coarse_stated_margin_can_still_refute_a_disjoint_claim():
    figures = [cell("gross_margin", 55, decimals=0)]
    verdict = verify(margin_claim("gross margin", "56.0%", 56), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation


@pytest.mark.parametrize("text,value,status", [("150 bps", 150, "unverifiable"), ("100 bps", 100, "verified"),
                                              ("1.5 percentage points", 1.5, "unverifiable")])
def test_margin_change_checks_subtraction_of_coarse_printed_rates(text, value, status):
    # 55% and 54% printed: their subtraction (100 bps) checks out; any other change within their rounding
    # (0-200 bps) may be true, so it is unknown rather than wrong
    figures = [cell("gross_margin", 55, decimals=0), cell("gross_margin", 54, fy=2026, decimals=0)]
    verdict = verify(change("gross margin", text, value), FakeStore(figures))
    assert verdict.status == status, verdict.explanation
    if status == "verified":
        assert "from 54% to 55%" in verdict.explanation
    else:
        assert verdict.reason == "ambiguous" and "rounded" in verdict.explanation.lower()


def test_precise_stated_rates_can_confirm_a_margin_change():
    figures = [cell("gross_margin", 55, decimals=3), cell("gross_margin", 54, fy=2026, decimals=3)]
    verdict = verify(change("gross margin", "100 bps", 100), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation


def test_coarse_stated_rates_can_refute_a_disjoint_margin_change():
    figures = [cell("gross_margin", 55, decimals=0), cell("gross_margin", 54, fy=2026, decimals=0)]
    verdict = verify(change("gross margin", "250 bps", 250), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation


def test_coarse_stated_change_with_unspecified_comparison_is_also_ambiguous():
    figures = [cell("gross_margin", 55, decimals=0), cell("gross_margin", 54, fy=2026, decimals=0)]
    proposal = change("gross margin", "150 bps", 150, comparison="none", source_comparison="")
    verdict = verify(proposal, FakeStore(figures))
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous"), verdict.explanation
    assert "rounded" in verdict.explanation.lower()


def test_a_calculation_that_rounds_to_the_coarse_stated_rate_checks_a_finer_claim():
    # AMD's release prints "Gross margin 55%" and its own gross profit and revenue give 55.45%: 55.4% verifies
    # against that calculation, as it did before stated rates took precedence.
    figures = ratio_facts("gross_profit", current=.5545, filing=NVDA_8K_Q2FY27)
    figures += [cell("gross_margin", 55, decimals=0, locator="Gross margin | 55%")]
    verdict = verify(margin_claim("gross margin", "55.4%", 55.4), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} >= {"gross_profit", "revenue"}


def test_a_calculation_that_misses_the_stated_rate_is_left_out():
    # 54.2% from the dollars doesn't round to the stated 55%, so the stated rate alone decides
    figures = ratio_facts("gross_profit", current=.542, filing=NVDA_8K_Q2FY27)
    figures += [cell("gross_margin", 55, decimals=0, locator="Gross margin | 55%")]
    verdict = verify(margin_claim("gross margin", "54.2%", 54.2), FakeStore(figures))
    assert verdict.status != "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} == {"gross_margin"}
