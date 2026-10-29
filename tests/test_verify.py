"""Verifier rules against an in-memory FakeStore.

Values, accessions and acceptance times are real (SEC companyfacts and submissions, cached by the
Day-1 EDGAR spike) unless a comment says "hand-made". NVIDIA's fiscal year ends in late January,
Microsoft's on June 30 and Meta's on December 31.
"""
import json
import re
import sys
from datetime import date, datetime, timezone

import pytest

from receipts import check as check_mod
from receipts import cli
from receipts import extract as extract_mod
from receipts.types import Claim, Fact
from receipts.verify import tolerance, verify

NVDA, MSFT, META, LOSSCO = 1045810, 789019, 1326801, 1
EDGAR = "https://www.sec.gov/Archives/edgar/data"


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# filings: (cik, ticker, form, accession, accepted_at, document)
NVDA_10Q_Q2FY27 = (NVDA, "NVDA", "10-Q", "0001045810-26-000075", "2026-08-26T20:36:00Z", "nvda-20260726.htm")
NVDA_8K_Q2FY27 = (NVDA, "NVDA", "8-K", "0001045810-26-000073", "2026-08-26T20:21:19Z", "q2fy27pr.htm")
NVDA_10Q_Q1FY27 = (NVDA, "NVDA", "10-Q", "0001045810-26-000052", "2026-05-20T20:35:52Z", "nvda-20260426.htm")
NVDA_8K_Q1FY27 = (NVDA, "NVDA", "8-K", "0001045810-26-000051", "2026-05-20T20:21:19Z", "q1fy27pr.htm")
NVDA_10K_FY26 = (NVDA, "NVDA", "10-K", "0001045810-26-000021", "2026-02-25T21:42:19Z", "nvda-20260125.htm")
NVDA_10Q_Q3FY26 = (NVDA, "NVDA", "10-Q", "0001045810-25-000230", "2025-11-19T21:36:17Z", "nvda-20251026.htm")
NVDA_10Q_Q2FY26 = (NVDA, "NVDA", "10-Q", "0001045810-25-000209", "2025-08-27T20:52:07Z", "nvda-20250727.htm")
NVDA_10Q_Q1FY26 = (NVDA, "NVDA", "10-Q", "0001045810-25-000116", "2025-05-28T20:32:57Z", "nvda-20250427.htm")
MSFT_10Q_Q3FY26 = (MSFT, "MSFT", "10-Q", "0001193125-26-191507", "2026-04-29T20:06:24Z", "msft-20260331.htm")
MSFT_10Q_Q2FY26 = (MSFT, "MSFT", "10-Q", "0001193125-26-027207", "2026-01-28T21:07:34Z", "msft-20251231.htm")
META_10Q_Q3 = (META, "META", "10-Q", "0001628280-26-066773", "2026-10-22T00:00:08Z", "meta-20260930.htm")
META_8K_Q3 = (META, "META", "8-K", "0001628280-26-066700", "2026-10-21T20:03:35Z", "meta-09302026xexhibit991.htm")
META_10Q_Q2 = (META, "META", "10-Q", "0001628280-26-050705", "2026-07-29T22:58:51Z", "meta-20260630.htm")
META_10Q_Q1 = (META, "META", "10-Q", "0001628280-26-028526", "2026-04-29T23:54:11Z", "meta-20260331.htm")

PERIODS = {  # (cik, fiscal_year, fiscal_period) -> (start, end)
    (NVDA, 2026, "Q1"): (date(2025, 1, 27), date(2025, 4, 27)),
    (NVDA, 2026, "Q2"): (date(2025, 4, 28), date(2025, 7, 27)),
    (NVDA, 2026, "Q3"): (date(2025, 7, 28), date(2025, 10, 26)),
    (NVDA, 2026, "Q4"): (date(2025, 10, 27), date(2026, 1, 25)),
    (NVDA, 2026, "FY"): (date(2025, 1, 27), date(2026, 1, 25)),
    (NVDA, 2027, "Q1"): (date(2026, 1, 26), date(2026, 4, 26)),
    (NVDA, 2027, "Q2"): (date(2026, 4, 27), date(2026, 7, 26)),
    (MSFT, 2026, "Q1"): (date(2025, 7, 1), date(2025, 9, 30)),
    (MSFT, 2026, "Q2"): (date(2025, 10, 1), date(2025, 12, 31)),
    (MSFT, 2026, "Q3"): (date(2026, 1, 1), date(2026, 3, 31)),
    (META, 2026, "Q1"): (date(2026, 1, 1), date(2026, 3, 31)),
    (META, 2026, "Q2"): (date(2026, 4, 1), date(2026, 6, 30)),
    (META, 2026, "Q3"): (date(2026, 7, 1), date(2026, 9, 30)),
    (LOSSCO, 2026, "Q1"): (date(2026, 1, 1), date(2026, 3, 31)),
}


def fact(filing, metric, value, fy, fp, *, unit="USD", basis="GAAP", source=None, segment=None, derived_from=()):
    cik, ticker, form, accession, accepted, doc = filing
    start, end = PERIODS[(cik, fy, fp)]
    source = source or ("press_release" if form == "8-K" else "xbrl")
    folder = accession.replace("-", "")
    return Fact(cik=cik, ticker=ticker, metric=metric, basis=basis, segment=segment, value=value, unit=unit,
                period_start=start, period_end=end, fiscal_year=fy, fiscal_period=fp, source=source,
                derived_from=list(derived_from), form=form, accession=accession, accepted_at=utc(accepted),
                url=f"{EDGAR}/{cik}/{folder}/{doc}")


FACTS = [
    # NVIDIA Q2 FY2027: 10-Q XBRL, and the earnings press release 15 minutes earlier
    fact(NVDA_10Q_Q2FY27, "revenue", 96_221e6, 2027, "Q2"),
    fact(NVDA_10Q_Q2FY27, "gross_profit", 72_142e6, 2027, "Q2"),
    fact(NVDA_10Q_Q2FY27, "cost_of_revenue", 24_079e6, 2027, "Q2"),
    fact(NVDA_10Q_Q2FY27, "operating_expenses", 8_408e6, 2027, "Q2"),
    fact(NVDA_10Q_Q2FY27, "net_income", 59_688e6, 2027, "Q2"),
    fact(NVDA_10Q_Q2FY27, "eps_diluted", 2.46, 2027, "Q2", unit="USD/share"),
    fact(NVDA_10Q_Q2FY27, "eps_basic", 2.47, 2027, "Q2", unit="USD/share"),
    fact(NVDA_8K_Q2FY27, "revenue", 96_221e6, 2027, "Q2"),
    fact(NVDA_8K_Q2FY27, "eps_diluted", 2.46, 2027, "Q2", unit="USD/share"),
    fact(NVDA_8K_Q2FY27, "eps_diluted", 2.22, 2027, "Q2", unit="USD/share", basis="non-GAAP"),
    fact(NVDA_8K_Q2FY27, "gross_margin", 75.0, 2027, "Q2", unit="%"),
    fact(NVDA_8K_Q2FY27, "gross_margin", 75.0, 2027, "Q2", unit="%", basis="non-GAAP"),
    fact(NVDA_8K_Q2FY27, "net_income", 53_954e6, 2027, "Q2", basis="non-GAAP"),
    fact(NVDA_8K_Q2FY27, "segment_revenue", 89.0e9, 2027, "Q2", segment="Data Center"),  # as printed: "$89.0 billion"
    fact(NVDA_8K_Q2FY27, "segment_revenue", 7.2e9, 2027, "Q2", segment="Edge Computing"),  # as printed
    # NVIDIA Q1 FY2027 and fiscal 2026
    fact(NVDA_10Q_Q1FY27, "revenue", 81_615e6, 2027, "Q1"),
    fact(NVDA_10Q_Q1FY27, "gross_profit", 61_157e6, 2027, "Q1"),
    fact(NVDA_10Q_Q1FY27, "eps_diluted", 2.39, 2027, "Q1", unit="USD/share"),
    fact(NVDA_8K_Q1FY27, "eps_diluted", 1.87, 2027, "Q1", unit="USD/share", basis="non-GAAP"),
    fact(NVDA_8K_Q1FY27, "segment_revenue", 75.4e9, 2027, "Q1", segment="Data Center"),  # hand-made
    fact(NVDA_10Q_Q2FY26, "revenue", 46_743e6, 2026, "Q2"),
    fact(NVDA_10Q_Q2FY26, "gross_profit", 33_853e6, 2026, "Q2"),
    fact(NVDA_10Q_Q2FY26, "eps_diluted", 1.08, 2026, "Q2", unit="USD/share"),
    fact(NVDA_10Q_Q2FY26, "segment_revenue", 41.0e9, 2026, "Q2", segment="Data Center"),  # hand-made
    fact(NVDA_10Q_Q1FY26, "revenue", 44_062e6, 2026, "Q1"),
    fact(NVDA_10Q_Q3FY26, "revenue", 57_006e6, 2026, "Q3"),
    fact(NVDA_10K_FY26, "revenue", 215_938e6, 2026, "FY"),
    fact(NVDA_10K_FY26, "revenue", (215_938 - 44_062 - 46_743 - 57_006) * 1e6, 2026, "Q4", source="derived",
         derived_from=["0001045810-26-000021", "0001045810-25-000230"]),  # Q4 = FY - 9M
    # Microsoft fiscal Q3 and Q2 2026
    fact(MSFT_10Q_Q3FY26, "revenue", 82_886e6, 2026, "Q3"),
    fact(MSFT_10Q_Q3FY26, "net_income", 31_778e6, 2026, "Q3"),
    fact(MSFT_10Q_Q3FY26, "eps_diluted", 4.27, 2026, "Q3", unit="USD/share"),
    fact(MSFT_10Q_Q2FY26, "revenue", 81_273e6, 2026, "Q2"),
    # Meta 2026
    fact(META_10Q_Q3, "revenue", 64_601e6, 2026, "Q3"),
    fact(META_10Q_Q3, "cost_of_revenue", 13_369e6, 2026, "Q3"),
    fact(META_10Q_Q3, "net_income", 9_860e6, 2026, "Q3"),
    fact(META_10Q_Q3, "eps_diluted", 3.83, 2026, "Q3", unit="USD/share"),
    fact(META_8K_Q3, "revenue", 64_601e6, 2026, "Q3"),
    fact(META_10Q_Q2, "revenue", 60_801e6, 2026, "Q2"),
    fact(META_10Q_Q2, "net_income", 15_848e6, 2026, "Q2"),
    fact(META_10Q_Q1, "revenue", 56_311e6, 2026, "Q1"),
]

ALIASES = {"nvidia": (NVDA, "NVDA"), "nvda": (NVDA, "NVDA"), "nvidia corporation": (NVDA, "NVDA"),
           "microsoft": (MSFT, "MSFT"), "msft": (MSFT, "MSFT"), "meta": (META, "META"),
           "meta platforms": (META, "META")}


class FakeStore:
    """The EvidenceStore protocol over a list of Facts, with receipts.store.Store's query semantics:
    segment=None is consolidated only, "*" is any segment, any other name matches case-insensitively."""

    def __init__(self, facts=FACTS, aliases=ALIASES, periods=PERIODS):
        self._facts, self._aliases = list(facts), dict(aliases)
        self._cal = {}
        for (cik, fy, fp), (start, end) in periods.items():
            self._cal.setdefault(cik, []).append((fy, fp, start, end))

    def resolve_entity(self, name):
        return self._aliases.get(name.strip().casefold())

    def facts(self, cik, metric, *, basis=None, segment=None, period_end=None, as_of=None):
        out = [f for f in self._facts if f.cik == cik and f.metric == metric]
        if basis is not None:
            out = [f for f in out if f.basis == basis]
        if segment is None:
            out = [f for f in out if f.segment is None]
        elif segment != "*":
            out = [f for f in out if f.segment and f.segment.casefold() == segment.strip().casefold()]
        if period_end is not None:
            out = [f for f in out if f.period_end == period_end]
        if as_of is not None:
            out = [f for f in out if f.accepted_at <= as_of]
        return out

    def fiscal_calendar(self, cik):
        return list(self._cal.get(cik, []))

    def first_public(self, cik, metric, period_end, basis=None):
        return min((f.accepted_at for f in self.facts(cik, metric, basis=basis, segment="*", period_end=period_end)),
                   default=None)


class FilingsStore(FakeStore):
    """Adds receipts.store.Store.filings(), which the look-ahead rule uses to date the Item 2.02 earnings 8-K."""

    def __init__(self, facts, filings, **kw):
        super().__init__(facts, **kw)
        self._filings = filings

    def filings(self, cik, forms=None):
        return [r for r in self._filings if r["cik"] == cik and (forms is None or r["form"] in forms)]


STORE = FakeStore()
Q2FY27 = "second quarter of fiscal 2027"


def claim(value_text, value, *, metric="revenue", unit="USD", scale="billions", period_text=Q2FY27, entity="NVIDIA",
          basis=None, comparison="none", period_type="quarter", forward=False, segment=None, span=None,
          metric_text=None, cid="c1"):
    span = span or f"{metric_text or metric.replace('_', ' ')} of {value_text}"
    return Claim(id=cid, metric=metric, metric_text=metric_text or metric.replace("_", " "), value_text=value_text,
                 value=value, unit=unit, scale=scale, period_text=period_text, period_type=period_type,
                 comparison=comparison, basis=basis, forward_looking=forward, entity=entity, segment=segment,
                 span_text=span, span_start=0, span_end=len(span))


def eps(value_text, value, basis=None, **kw):
    return claim(value_text, value, metric="eps_diluted", unit="USD/share", scale="units", basis=basis, **kw)


def pct(value_text, value, metric, **kw):
    return claim(value_text, value, metric=metric, unit="%", scale="units", **kw)


def accessions(v):
    return [f.accession for f in v.evidence]


# --- verified, and the tolerance the draft's own precision sets ---------------------
def test_verified_with_receipt():
    v = verify(claim("$96.2 billion", 96.2), STORE)
    assert (v.status, v.reason, v.expected_value) == ("verified", "match", None)
    assert "0001045810-26-000075" in accessions(v) and "0001045810-26-000073" in accessions(v)
    assert v.explanation == "Matches NVIDIA's 10-Q: revenue was $96.221 billion for Q2 FY2027 (quarter ended " \
                            "Jul 26, 2026)."


@pytest.mark.parametrize("value_text,value,scale,unit,expected", [
    ("$96.2 billion", 96.2, "billions", "USD", 0.05e9),
    ("$96,221 million", 96221, "millions", "USD", 0.5e6),
    ("18%", 18, "units", "%", 0.5),
    ("$2.46", 2.46, "units", "USD/share", 0.005),
    ("75.0%", 75.0, "units", "%", 0.05),
])
def test_tolerance_comes_from_written_precision(value_text, value, scale, unit, expected):
    c = claim(value_text, value, scale=scale, unit=unit, metric="revenue" if unit == "USD" else "gross_margin")
    assert tolerance(c) == pytest.approx(expected)


def test_more_written_digits_is_stricter():
    assert verify(claim("$96.25 billion", 96.25), STORE).status == "wrong"     # +-0.005B
    assert verify(claim("$96 billion", 96), STORE).status == "verified"        # +-0.5B


def test_half_unit_boundary_is_inclusive():
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "revenue", 96.25e9, 2027, "Q2")])  # hand-made value
    assert verify(claim("$96.2 billion", 96.2), store).status == "verified"
    assert verify(claim("$96.3 billion", 96.3), store).status == "verified"
    assert verify(claim("$96.4 billion", 96.4), store).status == "wrong"


def test_eps_rounding():
    assert verify(eps("$2.46", 2.46), STORE).status == "verified"
    v = verify(eps("$2.47", 2.47), STORE)  # basic EPS is 2.47; the claim is diluted
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", 2.46)


# --- diagnosing a wrong value ---------------------------------------------------------
def test_value_mismatch_reports_expected_in_claim_units():
    v = verify(claim("$97.4 billion", 97.4), STORE)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", 96.221)
    assert "not $97.4 billion" in v.explanation


def test_scale_mismatch():
    v = verify(claim("$96.2 million", 96.2, scale="millions"), STORE)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "scale_mismatch", 96221.0)
    assert "factor of 1,000" in v.explanation


def test_period_mismatch_names_the_other_quarter():
    v = verify(claim("$81.6 billion", 81.6), STORE)  # Q1 FY2027's revenue under a Q2 label
    assert (v.status, v.reason, v.expected_value) == ("wrong", "period_mismatch", 96.221)
    assert "Q1 FY2027" in v.explanation
    assert "0001045810-26-000052" in accessions(v)


def test_period_swap_on_a_june_fiscal_year():
    v = verify(claim("$82.9 billion", 82.9, entity="Microsoft", period_text="quarter ended December 31, 2025"), STORE)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "period_mismatch", 81.273)
    assert "Q3 FY2026" in v.explanation


def test_basis_mismatch_both_directions():
    v = verify(eps("$2.22", 2.22, basis="GAAP"), STORE)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "basis_mismatch", 2.46)
    v = verify(eps("$2.46", 2.46, basis="non-GAAP"), STORE)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "basis_mismatch", 2.22)


def test_entity_mismatch_against_the_company_the_context_names():
    c = claim("$82.9 billion", 82.9, entity="Meta Platforms", period_text="quarter ended March 31, 2026")
    v = verify(c, STORE, context="Microsoft, quarter ended March 31, 2026")
    assert (v.status, v.reason, v.expected_value) == ("wrong", "entity_mismatch", 56.311)
    assert "Microsoft" in v.explanation and "0001193125-26-191507" in accessions(v)
    # Without the context, a precise figure that is exactly one other known company's number still names it
    # (the status was already "wrong"; the peer search only picks the reason).
    v = verify(c, STORE)
    assert (v.status, v.reason) == ("wrong", "entity_mismatch") and "Microsoft" in v.explanation
    # "$83 billion" (+-0.6%) is too coarse to identify an owner: a plain value mismatch.
    coarse = claim("$83 billion", 83, entity="Meta Platforms", period_text="quarter ended March 31, 2026")
    assert verify(coarse, STORE).reason == "value_mismatch"


# --- GAAP versus non-GAAP --------------------------------------------------------------
def test_non_gaap_verifies_only_against_press_release_facts():
    v = verify(eps("$2.22", 2.22, basis="non-GAAP"), STORE)
    assert v.status == "verified"
    assert {f.source for f in v.evidence} == {"press_release"}


def test_non_gaap_never_matches_xbrl():
    c = claim("$8.4 billion", 8.4, metric="operating_expenses", basis="non-GAAP")  # XBRL GAAP opex is $8.408B
    v = verify(c, STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "press-release" in v.explanation


def test_unstated_basis_that_matches_only_non_gaap_is_ambiguous_not_wrong():
    v = verify(eps("$2.22", 2.22), STORE)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")
    assert "non-GAAP" in v.explanation and "$2.46" in v.explanation


def test_gaap_claims_also_use_press_release_gaap_cells():
    store = FakeStore([f for f in FACTS if f.accession != "0001045810-26-000075"])  # before the 10-Q exists
    v = verify(claim("$96.2 billion", 96.2, basis="GAAP"), store)
    assert v.status == "verified" and accessions(v) == ["0001045810-26-000073"]
    assert "8-K press release" in v.explanation


# --- point in time ----------------------------------------------------------------------
def test_look_ahead_before_the_earnings_release():
    v = verify(claim("$96.2 billion", 96.2), STORE, as_of="2026-08-26T20:00:00Z")
    assert (v.status, v.reason) == ("wrong", "look_ahead")
    assert "2026-08-26 20:21:19 UTC" in v.explanation and "8-K press release" in v.explanation
    assert v.as_of == utc("2026-08-26T20:00:00Z") and v.expected_value is None


def test_as_of_between_the_8k_and_the_10q_uses_the_press_release():
    v = verify(claim("$96.2 billion", 96.2), STORE, as_of=utc("2026-08-26T20:25:00Z"))
    assert v.status == "verified" and accessions(v) == ["0001045810-26-000073"]


def test_as_of_after_both_filings():
    v = verify(claim("$96.2 billion", 96.2), STORE, as_of=datetime(2026, 9, 1))  # naive means UTC
    assert v.status == "verified" and len(v.evidence) == 2


def test_look_ahead_with_a_wrong_value_still_gives_the_filed_figure():
    v = verify(claim("$90.0 billion", 90.0), STORE, as_of="2026-08-01")
    assert (v.status, v.reason, v.expected_value) == ("wrong", "look_ahead", 96.221)


# --- unverifiable ---------------------------------------------------------------------------
def test_forward_looking_guidance():
    v = verify(claim("$108.0 billion", 108.0, forward=True, period_text="third quarter of fiscal 2027"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "forward_looking")
    v = verify(pct("2%", 2, "guidance_tolerance"), STORE)
    assert v.reason == "forward_looking"


def test_unknown_entity_explains_coverage():
    v = verify(claim("$96.2 billion", 96.2, entity="Acme Robotics"), STORE, context="NVIDIA Q2 FY2027")
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "NoCap covers" in v.explanation and "Acme Robotics" in v.explanation
    assert "isn't one of them" in v.explanation


def test_generic_subject_uses_the_context_company():
    v = verify(claim("$96.2 billion", 96.2, entity="the company", period_text="the quarter"), STORE,
               context="NVIDIA Q2 FY2027")
    assert v.status == "verified"


def test_no_facts_is_no_evidence():
    v = verify(claim("$26.0 billion", 26.0, metric="capital_returned"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")


def test_unreported_quarter_is_no_evidence_with_a_hint():
    v = verify(claim("$96.2 billion", 96.2, period_text="third quarter of fiscal 2027"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "Q2 FY2027" in v.explanation  # the number does match the previous quarter


def test_ambiguous_period_never_yields_wrong():
    v = verify(claim("$50.0 billion", 50.0, period_text="the quarter"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")
    v = verify(claim("$82.9 billion", 82.9, entity="Microsoft", period_text="third quarter of 2026"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")  # calendar phrasing on a June fiscal year


def test_unit_that_does_not_fit_the_metric():
    v = verify(claim("96.2%", 96.2, unit="%", scale="units"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")


def test_untracked_metric():
    assert verify(claim("$500 billion", 500, metric="other"), STORE).reason == "no_evidence"


# --- derived metrics -------------------------------------------------------------------------
def test_gross_margin_derived_from_gross_profit_and_revenue():
    v = verify(pct("74.9%", 74.9, "gross_margin", period_text="first quarter of fiscal 2027"), STORE)
    assert v.status == "verified"  # 61,157 / 81,615 = 74.93%; no press-release cell for Q1 in this store
    assert {f.metric for f in v.evidence} == {"gross_profit", "revenue"}


def test_gross_margin_precision():
    assert verify(pct("75.0%", 75.0, "gross_margin"), STORE).status == "verified"
    v = verify(pct("75.1%", 75.1, "gross_margin"), STORE)  # 72,142 / 96,221 = 74.98%
    assert (v.status, v.reason) == ("wrong", "value_mismatch")
    assert v.expected_value == pytest.approx(74.976, abs=1e-3)


def test_non_gaap_margin_is_cited_from_the_press_release():
    v = verify(pct("75.0%", 75.0, "gross_margin", basis="non-GAAP"), STORE)
    assert v.status == "verified" and "8-K press release" in v.explanation
    # derived from the release's non-GAAP gross profit ($72,188M, real) over 10-Q revenue: still the release
    store = FakeStore([f for f in FACTS if f.metric != "gross_margin"]
                      + [fact(NVDA_8K_Q2FY27, "gross_profit", 72_188e6, 2027, "Q2", basis="non-GAAP")])
    v = verify(pct("75.0%", 75.0, "gross_margin", basis="non-GAAP"), store)
    assert v.status == "verified" and "8-K press release" in v.explanation and "10-Q" not in v.explanation


def test_gross_margin_from_cost_of_revenue_when_no_gross_profit_line():
    v = verify(pct("79.3%", 79.3, "gross_margin", entity="Meta", period_text="third quarter of 2026"), STORE)
    assert v.status == "verified"  # (64,601 - 13,369) / 64,601 = 79.31%


def test_revenue_growth_qoq_and_yoy():
    assert verify(pct("18%", 18, "revenue_growth", comparison="QoQ"), STORE).status == "verified"   # 17.90%
    assert verify(pct("106%", 106, "revenue_growth", comparison="YoY"), STORE).status == "verified"  # 105.85%
    assert verify(pct("18%", 18, "revenue_growth", comparison="sequential"), STORE).status == "verified"


def test_revenue_growth_with_swapped_comparison_is_a_period_mismatch():
    v = verify(pct("106%", 106, "revenue_growth", comparison="QoQ"), STORE)
    assert (v.status, v.reason) == ("wrong", "period_mismatch")
    assert v.expected_value == pytest.approx(17.896, abs=1e-3)
    assert "year over year" in v.explanation


def test_revenue_growth_without_a_stated_base():
    assert verify(pct("18%", 18, "revenue_growth"), STORE).status == "verified"
    v = verify(pct("50%", 50, "revenue_growth"), STORE)  # wrong under both readings
    assert (v.status, v.reason) == ("wrong", "value_mismatch")


def test_revenue_decline_wording_sets_the_sign():
    v = verify(pct("18%", 18, "revenue_growth", comparison="QoQ", span="revenue fell 18%"), STORE)
    assert v.status == "wrong"


def test_segment_revenue_and_growth_from_segment_facts():
    v = verify(claim("$89.0 billion", 89.0, metric="segment_revenue", segment="data center"), STORE)
    assert v.status == "verified" and v.evidence[0].segment == "Data Center"
    v = verify(claim("$89.0 billion", 89.0, metric="segment_revenue", segment="Data Center segment"), STORE)
    assert v.status == "verified"  # the store's exact name match fails; the "*" fallback normalizes names
    yoy = pct("117%", 117, "revenue_growth", segment="Data Center", comparison="YoY")
    assert verify(yoy, STORE).status == "verified"  # 89.0 / 41.0 - 1 = 117.1%
    v = verify(claim("$89.0 billion", 89.0, metric="segment_revenue"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")  # which segment?


def test_q4_and_full_year_share_an_end_date_but_not_a_value():
    v = verify(claim("$68.1 billion", 68.1, period_text="fourth quarter of fiscal 2026"), STORE)
    assert v.status == "verified" and v.evidence[0].source == "derived"
    v = verify(claim("$215.9 billion", 215.9, period_text="fiscal 2026", period_type="annual"), STORE)
    assert v.status == "verified" and v.evidence[0].form == "10-K"


def test_meta_calendar_quarter():
    v = verify(claim("$64.6 billion", 64.6, entity="Meta Platforms", period_text="third quarter of 2026"), STORE)
    assert v.status == "verified" and "Q3 2026" in v.explanation


# --- wording guards -----------------------------------------------------------------------------
def test_year_ago_figure_in_the_same_sentence_is_not_a_false_alarm():
    c = claim("$46.7 billion", 46.7, span="up from $46.7 billion a year ago")
    v = verify(c, STORE)
    assert v.status == "verified" and "Q2 FY2026" in v.explanation


def test_hedged_numbers():
    v = verify(claim("$97 billion", 97, span="revenue of approximately $97 billion"), STORE)
    assert (v.status, v.reason) == ("approximate", "approximate")
    assert verify(claim("$90 billion", 90, span="revenue of more than $90 billion"), STORE).status == "verified"
    assert verify(claim("$100 billion", 100, span="revenue of nearly $100 billion"), STORE).status == "wrong"


def test_net_loss_wording():
    store = FakeStore([fact((LOSSCO, "LOSS", "10-Q", "0000000001-26-000001", "2026-05-01T20:00:00Z", "x.htm"),
                            "net_income", -1.2e9, 2026, "Q1")], aliases={"lossco": (LOSSCO, "LOSS")})
    c = claim("$1.2 billion", 1.2, metric="net_income", entity="LossCo", period_text="Q1 2026",
              span="a net loss of $1.2 billion")  # SYNTHETIC company
    assert verify(c, store).status == "verified"


def test_explanations_are_one_plain_sentence():
    claims = [claim("$96.2 billion", 96.2), claim("$96.2 million", 96.2, scale="millions"), claim("$81.6 billion", 81.6),
              eps("$2.22", 2.22, basis="GAAP"), eps("$2.22", 2.22), claim("$26.0 billion", 26.0, metric="capital_returned"),
              claim("$1 billion", 1, entity="Acme"), pct("106%", 106, "revenue_growth", comparison="QoQ")]
    for c in claims:
        for v in (verify(c, STORE), verify(c, STORE, as_of="2026-08-01")):
            assert v.explanation.endswith(".") and not re.search(r"[.!?]\s+[A-Z]", v.explanation), v.explanation


# --- receipts/store.py semantics: segments, restatements, banks, earnings 8-Ks -----------------
def test_segment_wording_is_never_checked_against_the_consolidated_total():
    edge = claim("$7.2 billion", 7.2, metric_text="Edge Computing revenue", span="Edge Computing revenue was $7.2 billion")
    v = verify(edge, STORE)  # metric "revenue" and no segment, but the words name one
    assert v.status == "verified" and v.evidence[0].segment == "Edge Computing"
    gaming = claim("$30.0 billion", 30.0, metric_text="Gaming revenue", span="Gaming revenue was $30.0 billion")
    assert verify(gaming, STORE).status == "unverifiable"  # no Gaming facts: "?", never a ✗ against $96.2B total
    own_name = claim("$96.2 billion", 96.2, metric_text="NVIDIA Q2 revenue", span="NVIDIA Q2 revenue was $96.2 billion")
    assert verify(own_name, STORE).status == "verified"
    assert verify(claim("$96.2 billion", 96.2, metric_text="total GAAP revenue"), STORE).status == "verified"


BAC, JPM = 70858, 19617
PERIODS[(BAC, 2025, "Q2")] = (date(2025, 4, 1), date(2025, 6, 30))
PERIODS[(JPM, 2026, "Q3")] = (date(2026, 7, 1), date(2026, 9, 30))
BAC_10Q = (BAC, "BAC", "10-Q", "0000070858-25-000101", "2025-07-30T20:00:00Z", "bac-10q.htm")  # hand-made ids
BAC_10K = (BAC, "BAC", "10-K", "0000070858-26-000201", "2026-02-24T21:00:00Z", "bac-10k.htm")  # hand-made ids
BANKS = {"bank of america": (BAC, "BAC"), "jpmorgan": (JPM, "JPM")}


def test_restated_figures_any_version_visible_at_as_of_verifies():
    # BAC restated its 2025 Q2 revenue from $26.46B to $27.44B in a 2026 filing (values from the store owner)
    store = FakeStore([fact(BAC_10Q, "revenue", 26.46e9, 2025, "Q2"), fact(BAC_10K, "revenue", 27.44e9, 2025, "Q2")],
                      aliases=BANKS)
    q2 = dict(entity="Bank of America", period_text="second quarter of 2025", basis="GAAP")
    assert verify(claim("$26.5 billion", 26.5, **q2), store).status == "verified"   # the original version
    assert verify(claim("$27.4 billion", 27.4, **q2), store).status == "verified"   # the restated version
    v = verify(claim("$25.0 billion", 25.0, **q2), store)
    assert (v.reason, v.expected_value) == ("value_mismatch", 27.44)               # corrected with the latest
    v = verify(claim("$27.4 billion", 27.4, **q2), store, as_of="2025-12-31")      # restatement not out yet
    assert (v.status, v.expected_value) == ("wrong", 26.46)


def test_bank_headline_revenue_without_a_basis_is_never_wrong_on_gaap_alone():
    filing = (JPM, "JPM", "10-Q", "0000019617-26-000301", "2026-11-04T21:00:00Z", "jpm-10q.htm")  # hand-made
    release = (JPM, "JPM", "8-K", "0000019617-26-000280", "2026-10-14T11:00:00Z", "jpm-ex991.htm")  # hand-made
    gaap = fact(filing, "revenue", 45.31e9, 2026, "Q3")
    managed = fact(release, "revenue", 46.01e9, 2026, "Q3", basis="non-GAAP")
    q3 = dict(entity="JPMorgan", period_text="third quarter of 2026")
    v = verify(claim("$46.0 billion", 46.0, **q3), FakeStore([gaap], aliases=BANKS))
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")    # may be the managed (FTE) figure
    both = FakeStore([gaap, managed], aliases=BANKS)
    assert verify(claim("$46.0 billion", 46.0, **q3), both).reason == "ambiguous"  # matches managed, unlabeled
    assert verify(claim("$50.0 billion", 50.0, **q3), both).reason == "value_mismatch"  # neither reading fits
    managed_words = claim("$46.0 billion", 46.0, metric_text="managed revenue", span="managed revenue of $46.0 billion",
                          **q3)
    assert verify(managed_words, both).status == "verified"


def test_non_gaap_wording_without_a_basis_uses_press_release_facts_only():
    adjusted = eps("$2.22", 2.22, metric_text="adjusted EPS", span="adjusted EPS of $2.22")
    v = verify(adjusted, STORE)
    assert v.status == "verified" and {f.source for f in v.evidence} == {"press_release"}
    v = verify(eps("$2.46", 2.46, metric_text="adjusted EPS", span="adjusted EPS of $2.46"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")  # that is the GAAP figure, but unlabeled


MSFT_8K_Q3FY26 = {"accession": "0001193125-26-191457", "cik": MSFT, "form": "8-K", "items": "2.02,9.01",
                  "accepted_at": "2026-04-29T20:03:31Z"}  # real: the earnings release, 3 minutes before the 10-Q


def _msft_store(*extra_filings):
    facts = [f for f in FACTS if f.cik == MSFT]  # XBRL only: no press-release facts for this quarter
    return FilingsStore(facts, [MSFT_8K_Q3FY26, *extra_filings])


MSFT_Q3 = dict(entity="Microsoft", period_text="quarter ended March 31, 2026")


def test_look_ahead_is_dated_by_the_item_202_earnings_release():
    store = _msft_store({"accession": "0001193125-26-190001", "cik": MSFT, "form": "8-K", "items": "8.01",
                         "accepted_at": "2026-04-10T12:00:00Z"})  # hand-made non-earnings 8-K: ignored
    v = verify(claim("$82.9 billion", 82.9, **MSFT_Q3), store, as_of="2026-04-29T20:00:00Z")
    assert (v.status, v.reason) == ("wrong", "look_ahead")
    assert "0001193125-26-191457" in v.explanation and "2026-04-29 20:03:31 UTC" in v.explanation


def test_between_the_earnings_8k_and_the_10q_without_a_release_fact_is_unverifiable():
    v = verify(claim("$82.9 billion", 82.9, **MSFT_Q3), _msft_store(), as_of="2026-04-29T20:05:00Z")
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "earnings release" in v.explanation


def test_undatable_earnings_release_is_unverifiable_not_look_ahead():
    store = FakeStore([f for f in FACTS if f.cik == MSFT])  # no filings table, no press-release facts
    v = verify(claim("$82.9 billion", 82.9, **MSFT_Q3), store, as_of="2026-04-29T20:00:00Z")
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")


def test_non_gaap_without_release_facts_hints_at_the_gaap_match():
    store = FakeStore([f for f in FACTS if f.source != "press_release"])  # 10-Q only
    v = verify(pct("75.0%", 75.0, "gross_margin", basis="non-GAAP"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")  # GAAP and non-GAAP margins often coincide
    assert "matches its GAAP gross margin" in v.explanation


def test_display_name_drops_a_parenthesized_ticker():
    v = verify(claim("$96.2 billion", 96.2, entity="NVIDIA (NVDA)"),
               FakeStore(aliases={**ALIASES, "nvidia (nvda)": (NVDA, "NVDA")}))
    assert v.explanation.startswith("Matches NVIDIA's 10-Q")
    v = verify(claim("$64.6 billion", 64.6, entity="Meta Platforms", period_text="third quarter of 2026"), STORE)
    assert v.explanation.startswith("Matches Meta Platforms' 10-Q")


def test_q4_eps_has_no_derived_value():
    v = verify(eps("$1.50", 1.50, period_text="fourth quarter of fiscal 2026"), STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")


# --- the public API and the CLI ---------------------------------------------------------------
DRAFT = "NVIDIA reported revenue of $96.2 billion for the second quarter of fiscal 2027."


def _fake_extraction(monkeypatch, claims):
    def fake(text, context=None, **kw):
        return extract_mod.ExtractResult(list(claims), context, ["no claim covers the number '7' at offset 0"])
    monkeypatch.setattr(extract_mod, "extract_detailed", fake)


def test_check_pairs_claims_with_verdicts(monkeypatch):
    c = claim("$96.2 billion", 96.2, span="revenue of $96.2 billion")
    c = c.model_copy(update={"span_start": DRAFT.index("revenue of"), "span_end": DRAFT.index(" for the")})
    _fake_extraction(monkeypatch, [c])
    out = check_mod.check(DRAFT, store=STORE, as_of="2026-08-26T20:25:00Z")
    assert [(cl.id, v.status) for cl, v in out] == [("c1", "verified")]
    res = check_mod.check_detailed(DRAFT, store=STORE)
    assert res.context == "NVIDIA, second quarter of fiscal 2027" and res.warnings


def test_check_without_an_evidence_store_says_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "receipts.store", None)  # makes `import receipts.store` fail
    with pytest.raises(RuntimeError, match="receipts.store"):
        check_mod.check(DRAFT)


def test_cli_prints_markers_explanations_and_receipts(monkeypatch, tmp_path, capsys):
    c = claim("$96.2 billion", 96.2, span="revenue of $96.2 billion")
    c = c.model_copy(update={"span_start": DRAFT.index("revenue of"), "span_end": DRAFT.index(" for the")})
    _fake_extraction(monkeypatch, [c])
    draft = tmp_path / "draft.txt"
    draft.write_text(DRAFT, encoding="utf-8")
    assert cli.main(["check", str(draft)], store=STORE) == 0
    out = capsys.readouterr().out
    assert "$96.2 billion [✓c1]" in out
    assert "receipt: 10-Q 0001045810-26-000075 · accepted 2026-08-26 20:36:00 UTC · https://www.sec.gov/" in out
    assert cli.main(["check", str(draft), "--json", "--as-of", "2026-08-26T20:00:00Z"], store=STORE) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["verdict"]["reason"] == "look_ahead"


# --- regressions from the Day 2 dev eval (10 of 11 false alarms were constant-currency growth) ---------------
@pytest.mark.parametrize("span", [
    "up 17% in constant currency",
    "revenue grew 106% year over year (+14% on a FX-neutral basis)",
    "11% on a F/X neutral basis",
    "Revenue grew 106% year over year (+14% on a foreign exchange (F/X",
    "revenues increased 106%, or 19% in constant currency",
])
def test_constant_currency_growth_is_never_wrong_against_reported_growth(span):
    v = verify(pct("14%", 14, "revenue_growth", comparison="YoY", span=span), STORE)
    assert v.status != "wrong", (span, v.reason, v.explanation)


def test_negative_wording_signs_the_claimed_value():
    store = FakeStore([fact(NVDA_8K_Q2FY27, "free_cash_flow", -2.12e9, 2027, "Q2", basis="non-GAAP",
                            source="press_release")])
    c = claim("$2.12 billion", 2.12, metric="free_cash_flow", span="free cash flow was negative $2.12 billion")
    assert verify(c, store).status == "verified"
    c = claim("$2.12 billion", 2.12, metric="free_cash_flow", span="free cash flow was $2.12 billion")
    assert verify(c, store).status != "verified"


# --- Day 3: percent changes of any base metric, currency-neutral wording, readings, precision guards ---------
def test_percent_change_of_eps_carries_the_rounding_of_its_inputs():
    # 2.46 / 1.08 - 1 = 127.8%; per-share inputs rounded to the cent move it by up to ~1.5 pp
    c = claim("128%", 128, metric="other", unit="%", scale="units", metric_text="Diluted EPS growth",
              comparison="YoY", span="diluted EPS of $2.46, up 128% year over year")
    v = verify(c, STORE)
    assert v.status == "verified" and "diluted EPS grew 127.8% year over year" in v.explanation
    wrong = c.model_copy(update={"value_text": "135%", "value": 135, "span_text": "diluted EPS up 135% year over year"})
    v = verify(wrong, STORE)
    assert (v.status, v.reason) == ("wrong", "value_mismatch")
    swapped = c.model_copy(update={"comparison": "QoQ", "span_text": "diluted EPS up 128% quarter over quarter"})
    assert verify(swapped, STORE).reason == "period_mismatch"  # 2.46 / 2.39 is +2.9% QoQ; 128% is the YoY change


def test_percent_on_a_level_metric_with_change_wording_is_its_change():
    # Meta net income: $9.86B in Q3 2026 vs $15.848B in Q2 = -37.8% QoQ
    c = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms",
              period_text="third quarter of 2026", comparison="QoQ", metric_text="net income",
              span="net income fell 38% from the previous quarter")
    assert verify(c, STORE).status == "verified"  # "fell" signs the change, "loss" rules don't apply to a change
    rose = c.model_copy(update={"span_text": "net income rose 38% from the previous quarter"})
    assert verify(rose, STORE).status == "wrong"
    level = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms",
                  period_text="third quarter of 2026", metric_text="net income", span="net income of 38%")
    assert (verify(level, STORE).status, verify(level, STORE).reason) == ("unverifiable", "no_evidence")


def test_more_wording_than_the_metric_is_never_the_company_total():
    c = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms",
              period_text="third quarter of 2026", comparison="QoQ",
              metric_text="net income from Reality Labs", span="net income from Reality Labs fell 38%")
    assert verify(c, STORE).status == "unverifiable"
    other = claim("38%", 38, metric="other", unit="%", scale="units", entity="Meta Platforms",
                  period_text="third quarter of 2026", comparison="QoQ",
                  metric_text="net income available to common stockholders", span="... fell 38%")
    assert verify(other, STORE).status == "unverifiable"


@pytest.mark.parametrize("span,value_text,status", [
    ("Revenue was $96.2 billion and increased 106% (up 104% in constant currency)", "$96.2 billion", "verified"),
    ("Revenue was $96.2 billion and increased 106% (up 104% in constant currency)", "106%", "verified"),
    ("Revenue was $96.2 billion and increased 106% (up 104% in constant currency)", "104%", "unverifiable"),
    ("revenues increased 106%, or 104% in constant currency, to $96.2 billion", "$96.2 billion", "verified"),
    ("revenues increased 106%, or 104% in constant currency, to $96.2 billion", "104%", "unverifiable"),
])
def test_constant_currency_wording_qualifies_only_the_number_next_to_it(span, value_text, status):
    if value_text.startswith("$"):
        c = claim(value_text, 96.2, span=span)
    else:
        c = pct(value_text, float(value_text.rstrip("%")), "revenue_growth", comparison="YoY", span=span)
    assert verify(c, STORE).status == status, verify(c, STORE).explanation


def test_operating_margin_is_operating_income_over_revenue():
    store = FakeStore(FACTS + [fact(META_10Q_Q3, "operating_income", 25_840e6, 2026, "Q3")])  # hand-made value
    c = claim("40%", 40, metric="other", unit="%", scale="units", entity="Meta Platforms", metric_text="operating margin",
              period_text="third quarter of 2026", span="operating margin was 40%")  # 25,840 / 64,601 = 40.0%
    assert verify(c, store).status == "verified"
    assert verify(c.model_copy(update={"value_text": "45%", "value": 45}), store).status == "wrong"
    cloud = c.model_copy(update={"metric_text": "Cloud operating margin"})
    assert verify(cloud, store).status == "unverifiable"  # a segment's margin is never the company's


def test_unqualified_eps_can_be_basic_or_diluted():
    c = eps("$2.47", 2.47, metric_text="EPS", span="EPS of $2.47")  # basic is 2.47, diluted 2.46
    v = verify(c, STORE)
    assert v.status == "verified" and "basic EPS" in v.explanation
    assert verify(eps("$2.47", 2.47, metric_text="diluted EPS", span="diluted EPS of $2.47"), STORE).status == "wrong"
    untyped = claim("$2.46", 2.46, metric="other", unit="USD/share", scale="units", metric_text="EPS",
                    span="EPS of $2.46")
    assert verify(untyped, STORE).status == "verified"
    assert verify(untyped.model_copy(update={"value_text": "$2.60", "value": 2.60}), STORE).status == "wrong"


def test_a_coarse_change_never_names_another_period_by_coincidence():
    # NVIDIA revenue: Q2 FY27 is +17.9% QoQ and Q1 FY27 was +19.8% QoQ. "20%" for Q2 is wrong; that it sits near
    # Q1's change is coincidence at this precision, so the reason is a plain value mismatch.
    v = verify(pct("20%", 20, "revenue_growth", comparison="QoQ"), STORE)
    assert (v.status, v.reason) == ("wrong", "value_mismatch")
    # A precise figure still names the other period (test_period_mismatch_names_the_other_quarter).


# --- J1: bounds override numeric rounding, and splits override stale per-share versions -----------
@pytest.mark.parametrize("words,inclusive", [
    ("more than", False), ("over", False), ("exceeding", False), ("above", False),
    ("at least", True), ("upwards of", True),
])
@pytest.mark.parametrize("actual", [4.459e9, 4.5e9, 4.501e9, 6e9])
def test_lower_bounds_have_no_rounding_allowance(words, inclusive, actual):
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "operating_income", actual, 2027, "Q2")])
    c = claim("$4.5 billion", 4.5, metric="operating_income", basis="GAAP",
              span=f"operating income was {words} $4.5 billion")
    v = verify(c, store)
    meets = actual >= 4.5e9 if inclusive else actual > 4.5e9
    assert v.status == ("verified" if meets else "wrong")
    if not meets:
        assert v.reason == "value_mismatch" and v.expected_value == pytest.approx(actual / 1e9)


@pytest.mark.parametrize("words,inclusive", [
    ("less than", False), ("under", False), ("below", False), ("at most", True),
    ("no more than", True), ("not more than", True),
])
@pytest.mark.parametrize("actual", [3e9, 4.499e9, 4.5e9, 4.541e9])
def test_upper_bounds_have_no_rounding_allowance(words, inclusive, actual):
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "operating_income", actual, 2027, "Q2")])
    c = claim("$4.5 billion", 4.5, metric="operating_income", basis="GAAP",
              span=f"operating income was {words} $4.5 billion")
    meets = actual <= 4.5e9 if inclusive else actual < 4.5e9
    assert verify(c, store).status == ("verified" if meets else "wrong")


@pytest.mark.parametrize("value_text", ["$4.5 billion", "4.5 billion", "more than $4.5 billion"])
def test_bound_is_found_when_value_text_includes_or_omits_the_currency_or_hedge(value_text):
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "operating_income", 4.459e9, 2027, "Q2")])
    c = claim(value_text, 4.5, metric="operating_income", basis="GAAP",
              span="operating income was more than $4.5 billion")
    assert verify(c, store).status == "wrong"


@pytest.mark.parametrize("words", ["nearly", "almost"])
@pytest.mark.parametrize("actual,status", [(4.459e9, "verified"), (4.5e9, "wrong"),
                                          (4.501e9, "wrong"), (4.3e9, "wrong")])
def test_nearly_is_close_and_strictly_below_the_written_number(words, actual, status):
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "operating_income", actual, 2027, "Q2")])
    c = claim("$4.5 billion", 4.5, metric="operating_income", basis="GAAP",
              span=f"operating income was {words} $4.5 billion")
    assert verify(c, store).status == status


@pytest.mark.parametrize("words", ["about", "approximately", "roughly", "around"])
@pytest.mark.parametrize("actual,status", [(4.42e9, "approximate"), (4.58e9, "approximate"),
                                          (4.3e9, "wrong"), (4.7e9, "wrong")])
def test_approximation_words_allow_a_limited_band_on_both_sides(words, actual, status):
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "operating_income", actual, 2027, "Q2")])
    c = claim("$4.5 billion", 4.5, metric="operating_income", basis="GAAP",
              span=f"operating income was {words} $4.5 billion")
    assert verify(c, store).status == status


def test_bound_on_computed_growth_is_unknown_when_input_rounding_crosses_it():
    # Diluted EPS 2.46 / 2.39 gives ~2.93%; the cents' rounding overlaps a 3% boundary.
    c = pct("3%", 3, "eps_diluted", metric_text="diluted EPS growth", comparison="QoQ", basis="GAAP",
            span="diluted EPS grew more than 3%")
    v = verify(c, STORE)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")
    assert "rounding" in v.explanation


@pytest.mark.parametrize("actual,status", [(-4.6e9, "verified"), (-4.459e9, "wrong"), (4.6e9, "wrong")])
def test_a_loss_bound_checks_the_loss_magnitude(actual, status):
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "net_income", actual, 2027, "Q2")])
    c = claim("$4.5 billion", 4.5, metric="net_income", basis="GAAP",
              span="net loss was more than $4.5 billion")
    assert verify(c, store).status == status


def test_bound_that_only_fits_an_unstated_non_gaap_basis_is_ambiguous():
    store = FakeStore([fact(NVDA_10Q_Q2FY27, "operating_income", 4.459e9, 2027, "Q2"),
                       fact(NVDA_8K_Q2FY27, "operating_income", 4.6e9, 2027, "Q2", basis="non-GAAP")])
    c = claim("$4.5 billion", 4.5, metric="operating_income", span="operating income was over $4.5 billion")
    assert (verify(c, store).status, verify(c, store).reason) == ("unverifiable", "ambiguous")


def split_version_store(old_value=5.87, new_value=0.59):
    # Synthetic split history using the existing NVIDIA calendar and actual filing timestamps.
    old = fact(NVDA_10Q_Q2FY26, "eps_diluted", old_value, 2026, "Q2", unit="USD/share")
    new = fact(NVDA_10Q_Q2FY27, "eps_diluted", new_value, 2026, "Q2", unit="USD/share")
    return FakeStore([old, new])


@pytest.mark.parametrize("value,status", [(5.87, "wrong"), (0.59, "verified")])
def test_only_the_latest_split_basis_verifies(value, status):
    c = eps(f"${value:.2f}", value, period_text="Q2 FY2026", basis="GAAP")
    v = verify(c, split_version_store())
    assert v.status == status
    if status == "wrong":
        assert v.expected_value == 0.59 and "10-for-1 stock split" in v.explanation


def test_split_adjustment_becomes_authoritative_at_its_acceptance_timestamp():
    c = eps("$5.87", 5.87, period_text="Q2 FY2026", basis="GAAP")
    store = split_version_store()
    assert verify(c, store, as_of="2026-08-26T20:35:59Z").status == "verified"
    after = verify(c, store, as_of="2026-08-26T20:36:00Z")
    assert after.status == "wrong" and after.expected_value == 0.59
    assert "2026-08-26 20:36:00 UTC" in after.explanation
    assert "today" not in after.explanation


def test_pre_split_comparison_cannot_get_a_match_via_the_previous_year_fallback():
    c = eps("$5.87", 5.87, basis="GAAP", span="up from $5.87 a year ago")
    store = split_version_store()
    store._facts.append(fact(NVDA_10Q_Q2FY27, "eps_diluted", 0.87, 2027, "Q2", unit="USD/share"))
    assert verify(c, store).status == "wrong"


def test_reverse_split_uses_the_latest_basis_too():
    v = verify(eps("$0.59", 0.59, period_text="Q2 FY2026", basis="GAAP"), split_version_store(0.59, 5.87))
    assert v.status == "wrong" and v.expected_value == 5.87
    assert "1-for-10 reverse stock split" in v.explanation


@pytest.mark.parametrize("value", [0.89, 0.90])
def test_a_genuine_per_share_restatement_keeps_both_public_versions(value):
    store = split_version_store(0.89, 0.90)
    assert verify(eps(f"${value:.2f}", value, period_text="Q2 FY2026", basis="GAAP"), store).status == "verified"


def test_rounding_that_cannot_identify_a_split_ratio_does_not_discard_versions():
    # 0.03 vs 0.01, each rounded to cents, could be 2:1, 3:1, ...; no ratio is established.
    store = split_version_store(0.03, 0.01)
    assert verify(eps("$0.03", 0.03, period_text="Q2 FY2026", basis="GAAP"), store).status == "verified"


def test_a_later_restatement_does_not_revive_a_superseded_split_version():
    store = split_version_store()
    restated = store._facts[-1].model_copy(update={"value": 0.60, "accepted_at": utc("2026-09-01T20:00:00Z"),
                                                  "accession": "0001045810-26-000080"})
    store._facts.append(restated)
    c = eps("$5.87", 5.87, period_text="Q2 FY2026", basis="GAAP")
    v = verify(c, store)
    assert v.status == "wrong" and v.expected_value == 0.60
    assert "10-for-1 stock split" in v.explanation
    for value in (0.59, 0.60):
        assert verify(eps(f"${value:.2f}", value, period_text="Q2 FY2026", basis="GAAP"), store).status == "verified"
