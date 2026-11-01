"""Available-to-common tags are ambiguity evidence, never a replacement net-income observation."""
from datetime import date

import pytest

from receipts.types import Claim, Fact
from receipts.verify import verify
from test_verify import FakeStore, claim, utc

CIK = 797468
START, END = date(2026, 4, 1), date(2026, 6, 30)
ACC = "0001628280-26-053388"


def income():
    return Fact(cik=CIK, ticker="OXY", metric="net_income", concept="us-gaap:NetIncomeLoss",
                basis="GAAP", value=2977e6, unit="USD", period_start=START, period_end=END,
                fiscal_year=2026, fiscal_period="Q2", source="derived", derived_from=[ACC],
                form="10-Q", accession=ACC, accepted_at=utc("2026-08-05T20:18:40Z"),
                url=f"https://www.sec.gov/Archives/edgar/data/{CIK}/{ACC.replace('-', '')}/oxy.htm",
                decimals=-6, locator=f"NetIncomeLoss = ProfitLoss 2,996,000,000 - "
                f"NetIncomeLossAttributableToNoncontrollingInterest 19,000,000 (10-Q {ACC})")


def common(**updates):
    # model_copy also lets the pre-fix Fact schema run these behavioral regressions.
    return income().model_copy(update=dict(metric="net_income_to_common", value=2787e6,
        concept="us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic", source="xbrl",
        derived_from=[], locator=None) | updates)


def store(facts):
    return FakeStore(facts, aliases={"occidental": (CIK, "OXY")},
                     periods={(CIK, 2026, "Q2"): (START, END)})


def check(value, facts, **kwargs):
    c = claim(f"${value} billion", float(value), metric="net_income", metric_text="net income",
              entity="Occidental", period_text="Q2 2026",
              span=f"Occidental's net income was ${value} billion")
    return verify(c, store(facts), **kwargs)


def test_oxy_to_common_headline_abstains_with_both_figures_and_receipts():
    v = check("2.79", [income(), common()])
    assert (v.status, v.reason, v.expected_value) == ("unverifiable", "ambiguous", None)
    assert "common stockholders" in v.explanation
    assert "$2.787 billion" in v.explanation and "$2.977 billion" in v.explanation
    assert {f.metric for f in v.evidence} == {"net_income", "net_income_to_common"}


def test_oxy_served_net_income_still_verifies():
    assert check("2.977", [income(), common()]).status == "verified"


@pytest.mark.parametrize("value", ["2.79", "2.977", "7.123"])
def test_to_common_cannot_verify_or_refute_on_its_own(value):
    assert check(value, [common()]).status == "unverifiable"


@pytest.mark.parametrize("updates", [
    {"accession": "different"}, {"basis": "non-GAAP"}, {"segment": "Subsidiary"},
    {"period_start": date(2026, 1, 1)}, {"source": "derived"},
    {"concept": "us-gaap:ProfitLoss"}, {"unit": "shares"},
    {"accepted_at": utc("2026-09-01T00:00:00Z")},
])
def test_alternative_requires_same_filing_period_and_public_direct_gaap_dollars(updates):
    v = check("2.79", [income(), common(**updates)], as_of="2026-08-06T00:00:00Z")
    assert v.status == "wrong"
    assert all(f.metric == "net_income" for f in v.evidence)


def test_evidence_metric_is_not_a_claim_metric():
    from pydantic import ValidationError
    c = claim("$2.79 billion", 2.79)
    with pytest.raises(ValidationError):
        Claim.model_validate(c.model_dump() | {"metric": "net_income_to_common"})
