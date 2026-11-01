"""A filed percentage keeps its own printed precision in explanations."""
import pytest

from receipts.verify import verify
from test_verify import FakeStore, NVDA_8K_Q2FY27, fact, pct


def rate(value, *, decimals=0, basis="GAAP", fy=2027):
    return fact(NVDA_8K_Q2FY27, "gross_margin", value, fy, "Q2", unit="%", basis=basis).model_copy(
        update={"decimals": decimals, "locator": f"Gross margin | {value:.{decimals}f}%"})


@pytest.mark.parametrize("basis,status,reason", [("GAAP", "wrong", "basis_mismatch"),
                                                  (None, "unverifiable", "ambiguous")])
@pytest.mark.parametrize("decimals,printed", [(0, "52%"), (1, "52.0%"), (2, "52.00%")])
def test_basis_explanation_preserves_the_filed_rate_precision(basis, status, reason, decimals, printed):
    store = FakeStore([rate(52, decimals=decimals), rate(54, basis="non-GAAP")])
    verdict = verify(pct("54%", 54, "gross_margin", basis=basis), store)
    assert (verdict.status, verdict.reason) == (status, reason)
    assert verdict.explanation.endswith(f"the GAAP figure is {printed}.")
    assert verdict.expected_value == (52 if basis else None)


def test_bounded_basis_ambiguity_keeps_both_stated_rate_precisions():
    store = FakeStore([rate(52), rate(54, basis="non-GAAP")])
    verdict = verify(pct("53%", 53, "gross_margin", span="gross margin of more than 53%"), store)
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")
    assert "was 54%, which is more than 53%" in verdict.explanation
    assert verdict.explanation.endswith("the GAAP figure is 52%.")


def test_look_ahead_filed_rate_keeps_printed_precision():
    store = FakeStore([rate(52)])
    verdict = verify(pct("60%", 60, "gross_margin"), store, as_of="2026-08-26T19:00:00Z")
    assert (verdict.status, verdict.reason) == ("wrong", "look_ahead")
    assert verdict.explanation.endswith("the filed figure is 52%.")


def test_period_mismatch_filed_rate_keeps_printed_precision():
    store = FakeStore([rate(52), rate(54, fy=2026)])
    verdict = verify(pct("54.0%", 54, "gross_margin"), store)
    assert (verdict.status, verdict.reason) == ("wrong", "period_mismatch")
    assert verdict.explanation.endswith("it was 52%.")


def test_value_mismatch_displays_the_filed_rate_at_printed_precision():
    verdict = verify(pct("60%", 60, "gross_margin"), FakeStore([rate(52)]))
    assert (verdict.status, verdict.reason) == ("wrong", "value_mismatch")
    assert "gross margin was 52% for" in verdict.explanation


def test_rounded_rate_ambiguity_retains_printed_trailing_zeroes():
    verdict = verify(pct("52.000%", 52, "gross_margin"), FakeStore([rate(52, decimals=2)]))
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")
    assert "rounded gross margin of 52.00%" in verdict.explanation


def test_computed_margin_is_not_formatted_as_its_money_operand():
    figures = [fact(NVDA_8K_Q2FY27, "gross_profit", 52.123e6, 2027, "Q2"),
               fact(NVDA_8K_Q2FY27, "revenue", 100e6, 2027, "Q2")]
    verdict = verify(pct("60%", 60, "gross_margin"), FakeStore(figures))
    assert (verdict.status, verdict.reason) == ("wrong", "value_mismatch")
    assert "gross margin was 52.1% for" in verdict.explanation
