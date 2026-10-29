"""Numeric overflow and corrupt evidence must never turn into a false verified verdict."""
import math

import pytest

from receipts.verify import verify, within
from test_verify import FACTS, NVDA_10Q_Q1FY27, NVDA_10Q_Q2FY27, FakeStore, claim, fact


@pytest.mark.parametrize("a,b,tolerance", [
    (math.inf, 96.221, 0.05), (96.221, math.inf, 0.05), (96.221, 1.0, math.inf),
    (math.nan, 96.221, 0.05), (96.221, math.nan, 0.05), (96.221, 96.221, math.nan),
    (1.7e308, -1.7e308, 1.7e308),
])
def test_nonfinite_comparisons_and_intermediate_overflow_never_match(a, b, tolerance):
    assert within(a, b, tolerance) is False


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, 1e300])
def test_invalid_claim_or_full_unit_overflow_is_unverifiable(value):
    verdict = verify(claim("a numeric value", value), FakeStore())
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")
    assert verdict.expected_value is None and not verdict.evidence


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_nonfinite_filing_value_cannot_verify_exact_or_bounded_claim(value):
    corrupt = fact(NVDA_10Q_Q2FY27, "revenue", value, 2027, "Q2")
    for span in ("revenue was $96.2 billion", "revenue was more than $96.2 billion"):
        verdict = verify(claim("$96.2 billion", 96.2, span=span), FakeStore([corrupt]))
        assert verdict.status == "unverifiable" and verdict.expected_value is None and not verdict.evidence


def test_finite_filing_remains_usable_when_another_version_is_nonfinite():
    corrupt = fact(NVDA_10Q_Q2FY27, "revenue", math.inf, 2027, "Q2")
    verdict = verify(claim("$96.2 billion", 96.2), FakeStore([corrupt, *FACTS]))
    assert verdict.status == "verified" and all(math.isfinite(f.value) for f in verdict.evidence)


def test_finite_growth_inputs_that_overflow_leave_no_valid_comparison():
    current = fact(NVDA_10Q_Q2FY27, "revenue", 1e300, 2027, "Q2")
    previous = fact(NVDA_10Q_Q1FY27, "revenue", 1e-300, 2027, "Q1")
    verdict = verify(claim("18%", 18, metric="revenue_growth", unit="%", scale="units", comparison="QoQ"),
                     FakeStore([current, previous]))
    assert verdict.status == "unverifiable" and verdict.expected_value is None and not verdict.evidence


def test_overflowing_filing_precision_does_not_create_infinite_tolerance():
    corrupt = fact(NVDA_10Q_Q2FY27, "revenue", 96_221e6, 2027, "Q2").model_copy(update={"decimals": -1000})
    verdict = verify(claim("$96.2 billion", 96.2), FakeStore([corrupt]))
    assert verdict.status == "unverifiable" and not verdict.evidence


def test_normal_finite_rounding_tolerance_is_unchanged():
    assert within(96.2, 96.221, 0.05)
    assert not within(96.2, 96.251, 0.05)
    assert verify(claim("$96.2 billion", 96.2), FakeStore()).status == "verified"
