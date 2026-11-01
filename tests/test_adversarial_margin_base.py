"""Comparison endpoints and bank denominator bases must not become false mismatches."""
import pytest

from receipts.verify import verify
from test_verify import FakeStore, NVDA, NVDA_8K_Q2FY27, fact
from test_verify_margins import margin_claim, ratio_facts


@pytest.mark.parametrize("label,metric", [
    ("net margin", "net_income"),
    ("operating margin", "operating_income"),
    ("gross margin", "gross_profit"),
])
@pytest.mark.parametrize("suffix", ["", " a year earlier"])
def test_from_margin_endpoint_uses_the_comparison_period(label, metric, suffix):
    c = margin_claim(label, value_text="24.1%", value=24.1,
                     span=f"{label} expanded to 25.3% from 24.1%{suffix}")
    v = verify(c, FakeStore(ratio_facts(metric)))
    assert v.status == "verified", v.explanation
    assert "Q2 FY2026" in v.explanation
    assert {f.fiscal_year for f in v.evidence} == {2026}


def test_unlabelled_margin_base_matching_two_periods_remains_ambiguous():
    c = margin_claim(value_text="24.1%", value=24.1,
                     span="net margin expanded to 25.3% from 24.1%")
    v = verify(c, FakeStore(ratio_facts(qoq=.241)))
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "comparison period" in v.explanation


def test_margin_base_matching_only_current_period_does_not_verify():
    c = margin_claim(value_text="25.3%", value=25.3,
                     span="net margin expanded to 28.0% from 25.3%")
    v = verify(c, FakeStore(ratio_facts()))
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


def test_margin_current_endpoint_does_not_shift_to_comparison_period():
    c = margin_claim(value_text="25.3%", value=25.3,
                     span="net margin expanded to 25.3% from 24.1%")
    v = verify(c, FakeStore(ratio_facts()))
    assert v.status == "verified", v.explanation
    assert {f.fiscal_year for f in v.evidence} == {2027}


def test_margin_base_with_already_shifted_period_is_not_shifted_twice():
    c = margin_claim(value_text="24.1%", value=24.1, period_text="a year earlier",
                     span="net margin expanded to 25.3% from 24.1% a year earlier")
    v = verify(c, FakeStore(ratio_facts()), context="NVIDIA Q2 FY2027")
    assert v.status == "verified", v.explanation
    assert {f.fiscal_year for f in v.evidence} == {2026}


def test_labelled_margin_base_does_not_fall_back_to_current_period():
    c = margin_claim(value_text="25.3%", value=25.3,
                     span="net margin expanded to 28.0% from 25.3% a year earlier")
    v = verify(c, FakeStore(ratio_facts()))
    assert v.status != "verified", v.explanation


def bank_store(*, different_filing=False, future=False):
    # Ratios independently verified in JPM's archived Q2 2026 release:
    # $21,155M / $57,347M reported revenue = 36.889%; / $58,022M managed = 36.460%.
    figures = [fact(NVDA_8K_Q2FY27, "net_income", 21155e6, 2027, "Q2"),
               fact(NVDA_8K_Q2FY27, "revenue", 57347e6, 2027, "Q2"),
               fact(NVDA_8K_Q2FY27, "revenue", 58022e6, 2027, "Q2", basis="non-GAAP")]
    figures[-1] = figures[-1].model_copy(update={"locator": "Net revenue - managed | 58,022"})
    if different_filing:
        figures[-1] = figures[-1].model_copy(update={"accession": "other-release"})
    if future:
        from datetime import datetime, timezone
        figures[-1] = figures[-1].model_copy(update={"accepted_at": datetime(2027, 1, 1, tzinfo=timezone.utc)})
    return FakeStore(figures, aliases={"jpmorgan": (NVDA, "JPM")})


def bank_claim(value_text="36.5%", value=36.5, **kw):
    return margin_claim(value_text=value_text, value=value, entity="JPMorgan", basis=None, **kw)


def test_bank_net_margin_matching_managed_revenue_is_basis_ambiguous():
    v = verify(bank_claim(), bank_store())
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "managed" in v.explanation.lower()
    assert "revenue" in v.explanation.lower()
    assert {f.basis for f in v.evidence} == {"GAAP", "non-GAAP"}


def test_bank_net_margin_matching_gaap_still_verifies():
    v = verify(bank_claim("36.9%", 36.9), bank_store())
    assert v.status == "verified", v.explanation


@pytest.mark.parametrize("different_filing,future", [(True, False), (False, True)])
def test_bank_managed_margin_does_not_borrow_other_filing_or_future_revenue(different_filing, future):
    v = verify(bank_claim(), bank_store(different_filing=different_filing, future=future),
               as_of="2026-09-01T00:00:00Z")
    assert v.status == "wrong", v.explanation
    assert all(f.basis == "GAAP" for f in v.evidence)
