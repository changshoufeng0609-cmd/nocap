"""Printed endpoint arithmetic and direction diagnostics from the prerelease judge."""
from datetime import date

import pytest

from receipts.verify import verify
from test_adversarial_gross_margin import cell
from test_verify import FakeStore, NVDA, PERIODS, NVDA_8K_Q2FY27, NVDA_10K_FY26, NVDA_10Q_Q2FY27, claim, fact, utc
from test_verify_margins import change, margin_claim, ratio_facts


@pytest.mark.parametrize("label,metric", [("gross margin", "gross_profit"),
                                         ("operating margin", "operating_income"),
                                         ("net margin", "net_income")])
@pytest.mark.parametrize("text,value", [("360 basis points", 360), ("3.6 percentage points", 3.6),
                                       ("360.0 bps", 360), ("3.60 points", 3.6)])
def test_printed_endpoint_subtraction_matches_in_either_unit(label, metric, text, value):
    figures = ratio_facts(metric, current=.50060, previous=.46494)
    verdict = verify(change(label, text, value), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    if value == 360 or text == "3.60 points":
        assert "46.5%" in verdict.explanation and "50.1%" in verdict.explanation
        assert "rounded" in verdict.explanation
        assert "filed figures" in verdict.explanation
        assert ("356.6" if value == 360 else "3.566") in verdict.explanation


@pytest.mark.parametrize("text,value", [("355 bps", 355), ("3.55 points", 3.55),
                                       ("370 bps", 370), ("3.7 points", 3.7)])
def test_neither_exact_nor_printed_is_wrong_with_exact_correction(text, value):
    verdict = verify(change("gross margin", text, value),
                     FakeStore(ratio_facts("gross_profit", current=.50060, previous=.46494)))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(356.6 if "bps" in text else 3.566)


@pytest.mark.parametrize("decimals,current,previous,text,value", [
    (0, 55, 54, "100 bps", 100),
    (2, 50.06, 46.49, "357 bps", 357),
    (2, 50.06, 46.49, "3.57 points", 3.57),
])
def test_stored_margin_uses_its_own_decimal_places(decimals, current, previous, text, value):
    figures = [cell("gross_margin", current, decimals=decimals),
               cell("gross_margin", previous, fy=2026, decimals=decimals)]
    verdict = verify(change("gross margin", text, value), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation


def test_stored_precision_does_not_fall_back_to_one_decimal():
    figures = ratio_facts("gross_profit", current=.50060, previous=.46494, filing=NVDA_8K_Q2FY27)
    figures += [cell("gross_margin", 50.06, decimals=2),
                cell("gross_margin", 46.49, fy=2026, decimals=2)]
    assert verify(change("gross margin", "357 bps", 357), FakeStore(figures)).status == "verified"
    verdict = verify(change("gross margin", "360 bps", 360), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(356.6)


def test_rounding_does_not_loosen_margin_level():
    verdict = verify(margin_claim("gross margin", "50.10%", 50.1),
                     FakeStore(ratio_facts("gross_profit", current=.50060)))
    assert verdict.status == "wrong", verdict.explanation


def test_public_stated_precision_also_governs_other_filings_same_period():
    figures = ratio_facts("gross_profit", current=.50060, previous=.46494)
    figures += [cell("gross_margin", 50.06, decimals=2),
                cell("gross_margin", 46.49, fy=2026, decimals=2)]
    verdict = verify(change("gross margin", "360 bps", 360), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert verdict.expected_value == pytest.approx(356.6)
    assert verify(change("gross margin", "357 bps", 357), FakeStore(figures)).status == "verified"


def test_printed_match_cites_stored_rates_that_supply_precision():
    figures = ratio_facts("gross_profit", current=.50060, previous=.464944)
    figures += [cell("gross_margin", 50.06, decimals=2),
                cell("gross_margin", 46.49, fy=2026, decimals=2)]
    # The exact delta is 356.56 bps, while the printed endpoints give 357.0.
    verdict = verify(change("gross margin", "357.0 bps", 357), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} == {"gross_profit", "revenue", "gross_margin"}


def test_future_stated_precision_does_not_constrain_public_margin_change():
    figures = ratio_facts("gross_profit", current=.50060, previous=.46494)
    rates = [cell("gross_margin", 50.06, decimals=2),
             cell("gross_margin", 46.49, fy=2026, decimals=2)]
    figures += [f.model_copy(update={"accepted_at": utc("2026-08-28T00:00:00Z")}) for f in rates]
    verdict = verify(change("gross margin", "360 bps", 360), FakeStore(figures),
                     as_of="2026-08-27T00:00:00Z")
    assert verdict.status == "verified", verdict.explanation
    assert all(f.accession == NVDA_10Q_Q2FY27[3] for f in verdict.evidence)


@pytest.mark.parametrize("current_only", [True, False])
def test_complete_stated_pair_survives_one_missing_dollar_endpoint(current_only):
    figures = ratio_facts("gross_profit", current=.50060, previous=.46494, filing=NVDA_8K_Q2FY27)
    figures = figures[:2] if current_only else figures[2:]
    figures += [cell("gross_margin", 50.1, decimals=1),
                cell("gross_margin", 46.5, fy=2026, decimals=1)]
    verdict = verify(change("gross margin", "360 bps", 360), FakeStore(figures))
    assert verdict.status == "verified", verdict.explanation
    assert {f.metric for f in verdict.evidence} == {"gross_margin"}


@pytest.mark.parametrize("text,value", [("360 bps", 360), ("3.6 points", 3.6)])
def test_printed_endpoint_match_keeps_contraction_direction(text, value):
    figures = ratio_facts(current=.46494, previous=.50060)
    assert verify(change(value_text=text, value=value, direction="contracted"),
                  FakeStore(figures)).status == "verified"
    verdict = verify(change(value_text=text, value=value), FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert "the draft says it expanded" in verdict.explanation
    assert "not " + text not in verdict.explanation


@pytest.mark.parametrize("text,value,current,previous", [("255 bps", 255, .27552, .25),
                                                        ("360 bps", 360, .50060, .46494)])
def test_wrong_margin_direction_names_direction_not_number(text, value, current, previous):
    verdict = verify(change("gross margin", text, value, direction="contracted"),
                     FakeStore(ratio_facts("gross_profit", current=current, previous=previous)))
    assert verdict.status == "wrong", verdict.explanation
    assert "expanded" in verdict.explanation and "the draft says it contracted" in verdict.explanation
    assert "not " + text not in verdict.explanation
    assert verdict.expected_value > 0


@pytest.mark.parametrize("current,previous,value,direction,actual", [
    (96_221e6, 46_743e6, 105.85, "fell", "grew"),
    (40e6, 100e6, 60, "grew", "fell"),
])
def test_wrong_growth_direction_names_direction_not_number(current, previous, value, direction, actual):
    figures = [fact(NVDA_8K_Q2FY27, "revenue", current, 2027, "Q2"),
               fact(NVDA_8K_Q2FY27, "revenue", previous, 2026, "Q2")]
    text = f"{value}%"
    proposal = claim(text, value, metric="revenue_growth", metric_text="revenue", unit="%", scale="units",
                     comparison="YoY", span=f"Revenue {direction} {text} from a year ago")
    verdict = verify(proposal, FakeStore(figures))
    assert verdict.status == "wrong", verdict.explanation
    assert actual in verdict.explanation and f"the draft says it {direction}" in verdict.explanation
    assert "not " + text not in verdict.explanation


def test_annual_growth_also_explains_wrong_direction():
    current = fact(NVDA_10K_FY26, "revenue", 200e6, 2026, "FY")
    previous = current.model_copy(update={"value": 100e6, "fiscal_year": 2025,
                                         "period_start": date(2024, 1, 29), "period_end": date(2025, 1, 26)})
    proposal = claim("100%", 100, metric="revenue_growth", metric_text="revenue", unit="%", scale="units",
                     comparison="YoY", period_text="fiscal 2026", period_type="annual",
                     span="Revenue fell 100% year over year in fiscal 2026")
    calendar = {**PERIODS, (NVDA, 2025, "FY"): (previous.period_start, previous.period_end)}
    verdict = verify(proposal, FakeStore([current, previous], periods=calendar))
    assert verdict.status == "wrong", verdict.explanation
    assert "grew" in verdict.explanation and "the draft says it fell" in verdict.explanation
    assert "not 100%" not in verdict.explanation
