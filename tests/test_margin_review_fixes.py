"""Source-local direction and written precision regressions from the November 1 review."""
import pytest

from receipts.verify import decimals_of, verify
from test_verify import FakeStore
from test_verify_margins import change, margin_claim, ratio_facts


@pytest.mark.parametrize("prefix", ["", "Revenue rose $1 million; "])
def test_margin_fall_keeps_direction_before_starting_percentage(prefix):
    proposal = margin_claim(value_text="183.05 bps", value=183.05, comparison="YoY",
                            span=f"{prefix}net margin fell from 39.4412% by 183.05 bps year over year")
    verdict = verify(proposal, FakeStore(ratio_facts(current=.376107, previous=.394412)))
    assert verdict.status == "verified", verdict.explanation
    assert "fell" in verdict.explanation


def test_margin_fall_keeps_direction_with_rounded_start_and_change():
    proposal = margin_claim(value_text="183 bps", value=183, comparison="YoY",
                            span="net margin fell from 39.4% by 183 bps year over year")
    verdict = verify(proposal, FakeStore(ratio_facts(current=.376107, previous=.394412)))
    assert verdict.status == "verified", verdict.explanation
    assert "fell" in verdict.explanation


@pytest.mark.parametrize("prefix", [
    "Revenue fell $1 million; ",
    "Revenue fell 10%, and ",
    "Operating margin fell from 27.0% by 200 bps, while ",
])
def test_margin_starting_value_never_borrows_another_claims_direction(prefix):
    proposal = margin_claim(value_text="120 bps", value=120, comparison="YoY",
                            span=f"{prefix}net margin rose from 24.1% by 120 bps year over year")
    verdict = verify(proposal, FakeStore(ratio_facts()))
    assert verdict.status == "verified", verdict.explanation
    assert "grew" in verdict.explanation


@pytest.mark.parametrize("text,value,decimals", [(".4 percentage points", .4, 1),
                                                (".40 percentage points", .4, 2),
                                                ("−.40 bps", -.4, 2),
                                                ("1,234.50 bps", 1234.5, 2)])
def test_leading_decimal_precision_matches_margin_number_parser(text, value, decimals):
    assert decimals_of(text, value) == decimals


@pytest.mark.parametrize("value_text", [".4 percentage points", ".40 percentage points"])
def test_flat_margin_cannot_verify_a_leading_decimal_increase(value_text):
    verdict = verify(change(value_text=value_text, value=.4, direction="rose"),
                     FakeStore(ratio_facts(current=.25, previous=.25)))
    assert (verdict.status, verdict.reason) == ("wrong", "value_mismatch"), verdict.explanation
    assert verdict.expected_value == pytest.approx(0)


@pytest.mark.parametrize("text,value", [("120bps", 120), ("120 bps", 120), ("120bp", 120),
                                        ("1.2pp", 1.2), ("1.2 pts", 1.2),
                                        ("1.2 percentage points", 1.2)])
def test_margin_unit_spellings_match_frontend(text, value):
    # The model may retain percent as the enum unit. Source text is authoritative.
    verdict = verify(change(value_text=text, value=value, unit="%"), FakeStore(ratio_facts()))
    assert verdict.status == "verified", verdict.explanation


@pytest.mark.parametrize("text,value,expected", [("130bps", 130, 120), ("130 bps", 130, 120),
                                                ("130bp", 130, 120), ("1.3pp", 1.3, 1.2),
                                                ("1.3 pts", 1.3, 1.2),
                                                ("1.3 percentage points", 1.3, 1.2)])
def test_margin_unit_spellings_correct_in_source_units(text, value, expected):
    verdict = verify(change(value_text=text, value=value, unit="%", scale="millions"),
                     FakeStore(ratio_facts()))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(expected)
