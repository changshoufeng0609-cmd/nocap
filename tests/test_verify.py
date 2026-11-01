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
from receipts import verify as verify_mod
from receipts.splits import split_events
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
    # ... and the comparatives the same filings report: the 10-Q's year-ago quarter, the release's prior-quarter and
    # year-ago columns (a change is computed within one filing: verify._Check.growth)
    fact(NVDA_10Q_Q2FY27, "revenue", 46_743e6, 2026, "Q2"),
    fact(NVDA_10Q_Q2FY27, "eps_diluted", 1.08, 2026, "Q2", unit="USD/share"),
    fact(NVDA_8K_Q2FY27, "revenue", 81_615e6, 2027, "Q1"),
    fact(NVDA_8K_Q2FY27, "revenue", 46_743e6, 2026, "Q2"),
    fact(NVDA_8K_Q2FY27, "eps_diluted", 2.39, 2027, "Q1", unit="USD/share"),
    fact(NVDA_8K_Q2FY27, "segment_revenue", 75.4e9, 2027, "Q1", segment="Data Center"),  # hand-made, as Q1's own
    fact(NVDA_8K_Q2FY27, "segment_revenue", 41.0e9, 2026, "Q2", segment="Data Center"),  # hand-made, as Q2 FY26's
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
    # Without the context, only a company the same draft names may own the figure (the status was already "wrong";
    # attribution only picks the reason).
    named = [(MSFT, "MSFT", "Microsoft")]
    v = verify(c, STORE, named=named)
    assert (v.status, v.reason) == ("wrong", "entity_mismatch") and "Microsoft" in v.explanation
    # "$83 billion" (+-0.6%) is too coarse to identify an owner even among named companies: a plain value mismatch.
    coarse = claim("$83 billion", 83, entity="Meta Platforms", period_text="quarter ended March 31, 2026")
    assert verify(coarse, STORE, named=named).reason == "value_mismatch"


META_ON_MSFT = claim("$82.9 billion", 82.9, entity="Meta Platforms", period_text="quarter ended March 31, 2026")


def test_a_peer_named_in_the_draft_is_still_attributed():
    named = verify_mod.named_in_draft("Meta and Microsoft both reported their March quarters.", STORE)
    assert {t for _, t, _ in named} == {"META", "MSFT"}
    v = verify(META_ON_MSFT, STORE, named=named)
    assert (v.status, v.reason) == ("wrong", "entity_mismatch") and "Microsoft" in v.explanation


def test_an_unnamed_company_with_an_identical_value_is_not_attributed():
    """$82.9 billion is exactly Microsoft's revenue, but a draft that never names Microsoft can't be blaming it:
    across hundreds of companies an equal figure is mostly a coincidence. The figure is still wrong."""
    for named in (None, [], verify_mod.named_in_draft("Meta reported its March quarter.", STORE)):
        v = verify(META_ON_MSFT, STORE, named=named)
        assert (v.status, v.reason) == ("wrong", "value_mismatch") and "Microsoft" not in v.explanation


def test_attribution_still_requires_a_unique_match():
    twin = fact((LOSSCO, "LOSS", "10-Q", "0000000001-26-000010", "2026-04-30T20:00:00Z", "loss-20260331.htm"),
                "revenue", 82_886e6, 2026, "Q1")  # another named company with exactly Microsoft's figure
    named = [(MSFT, "MSFT", "Microsoft"), (LOSSCO, "LOSS", "Loss Co")]
    v = verify(META_ON_MSFT, FakeStore(FACTS + [twin]), named=named)
    assert (v.status, v.reason) == ("wrong", "value_mismatch")
    assert "Microsoft" not in v.explanation and "Loss Co" not in v.explanation


def test_a_gross_margin_equal_to_another_companys_never_names_it():
    """Gross margins cluster: even among the companies one draft names, an equal value is chance, so a wrong one stays
    a plain value mismatch instead of blaming another company. A precise dollar figure still names its owner (above)."""
    margins = [fact(META_10Q_Q1, "gross_margin", 81.2, 2026, "Q1", unit="%"),
               fact(MSFT_10Q_Q3FY26, "gross_margin", 68.7, 2026, "Q3", unit="%")]
    c = pct("68.7%", 68.7, "gross_margin", entity="Meta Platforms", period_text="quarter ended March 31, 2026")
    v = verify(c, FakeStore(FACTS + margins), named=[(MSFT, "MSFT", "Microsoft")])
    assert (v.status, v.reason) == ("wrong", "value_mismatch") and "Microsoft" not in v.explanation


def test_a_company_the_context_only_mentions_needs_a_precise_figure():
    """Once the context line stops setting the fiscal period (it names only another company), that company is one more
    mention: "$83 billion" (+-0.6%) is too coarse to be Microsoft's $82.886 billion, "$82.9 billion" is not."""
    coarse = claim("$83 billion", 83, entity="Meta Platforms", period_text="Q1 2026")
    v = verify(coarse, STORE, context="Microsoft Q3 FY2026")
    assert (v.status, v.reason) == ("wrong", "value_mismatch") and "Microsoft" not in v.explanation
    precise = claim("$82.9 billion", 82.9, entity="Meta Platforms", period_text="Q1 2026")
    v = verify(precise, STORE, context="Microsoft Q3 FY2026")
    assert (v.status, v.reason) == ("wrong", "entity_mismatch") and "Microsoft" in v.explanation


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
    """Wording that is always true: the name may be outside coverage or a covered company under another name."""
    v = verify(claim("$96.2 billion", 96.2, entity="Acme Robotics"), STORE, context="NVIDIA Q2 FY2027")
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation.startswith('NoCap couldn\'t match "Acme Robotics" to a company it covers')
    assert "it covers 3 companies today: NVIDIA, Microsoft and Meta" in v.explanation
    assert "isn't one of them" not in v.explanation


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
    assert v.explanation == ("NoCap's evidence for NVIDIA ends with Q2 FY2027, so it can't check Q3 FY2027 yet; "
                             "$96.2 billion matches its revenue for Q2 FY2027 (quarter ended Jul 26, 2026).")
    assert v.evidence and all(f.fiscal_period == "Q2" and f.fiscal_year == 2027 for f in v.evidence)


@pytest.mark.parametrize("entity, period, kind, latest, requested", [
    ("Meta", "Q4 2026", "quarter", "Q3 2026", "Q4 2026"),
    ("Meta", "Q1 2027", "quarter", "Q3 2026", "Q1 2027"),
    ("Microsoft", "Q4 FY2026", "quarter", "Q3 FY2026", "Q4 FY2026"),
    ("NVIDIA", "FY2027", "annual", "Q2 FY2027", "fiscal 2027"),
])
def test_later_period_names_the_company_evidence_limit(entity, period, kind, latest, requested):
    v = verify(claim("$500 billion", 500, entity=entity, period_text=period, period_type=kind), STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == f"NoCap's evidence for {entity} ends with {latest}, so it can't check {requested} yet."
    assert not v.evidence and v.expected_value is None


def test_coverage_limit_is_held_evidence_not_the_as_of_cutoff():
    v = verify(claim("$500 billion", 500, period_text="Q3 FY2027"), STORE, as_of="2026-03-01T00:00:00Z")
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == "NoCap's evidence for NVIDIA ends with Q2 FY2027, so it can't check Q3 FY2027 yet."


def test_full_year_and_fourth_quarter_have_the_same_coverage_endpoint():
    cal = {k: v for k, v in PERIODS.items() if k[0] == NVDA and k[1] == 2026}
    store = FakeStore([f for f in FACTS if f.cik == NVDA and f.fiscal_year == 2026], periods=cal)
    v = verify(claim("$500 billion", 500, period_text="Q1 FY2027"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == "NoCap's evidence for NVIDIA ends with Q4 FY2026, so it can't check Q1 FY2027 yet."
    # A missing Q4 label at the same endpoint as FY is a gap, not newer evidence.
    cal = {(NVDA, 2026, "FY"): PERIODS[(NVDA, 2026, "FY")]}
    store = FakeStore([f for f in FACTS if f.cik == NVDA and f.fiscal_period == "FY"], periods=cal)
    v = verify(claim("$500 billion", 500, period_text="Q4 FY2026"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == "NoCap couldn't match Q4 FY2026 to a fiscal period labelled in NVIDIA's filings."


@pytest.mark.parametrize("metric, basis, segment", [
    ("revenue", "GAAP", None), ("net_income", "non-GAAP", None), ("segment_revenue", "GAAP", "Other"),
])
def test_newer_facts_without_fiscal_labels_do_not_claim_coverage_ends_earlier(metric, basis, segment):
    newer = FACTS[0].model_copy(update={"period_end": date(2026, 10, 25), "fiscal_year": None,
                                      "fiscal_period": None, "metric": metric, "basis": basis, "segment": segment})
    v = verify(claim("$500 billion", 500, period_text="Q3 FY2027"), FakeStore([*FACTS, newer]))
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == "NoCap couldn't match Q3 FY2027 to a fiscal period labelled in NVIDIA's filings."


@pytest.mark.parametrize("cal", [PERIODS, {}])
def test_calendar_without_held_facts_does_not_claim_an_evidence_limit(cal):
    v = verify(claim("$500 billion", 500, period_text="Q3 FY2027"), FakeStore([], periods=cal))
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == "NoCap couldn't match Q3 FY2027 to a fiscal period labelled in NVIDIA's filings."


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
    v = verify(pct("75.1%", 75.1, "gross_margin"), STORE)
    assert (v.status, v.reason) == ("wrong", "value_mismatch")
    # The release states 75.0%; its reported rate takes precedence over a
    # recomputation from the 10-Q's rounded gross profit and revenue.
    assert v.expected_value == pytest.approx(75.0)
    assert {f.metric for f in v.evidence} == {"gross_margin"}


def test_non_gaap_margin_is_cited_from_the_press_release():
    v = verify(pct("75.0%", 75.0, "gross_margin", basis="non-GAAP"), STORE)
    assert v.status == "verified" and "8-K press release" in v.explanation
    # Derived from the release's non-GAAP gross profit and that release's own revenue.
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
    # Meta net income: $9.86B in Q3 2026 vs $15.848B in Q2 = -37.8% QoQ, from a release that reports both quarters
    # (hand-made: Meta's releases and 10-Qs report each quarter with its year-ago quarter only)
    release = FakeStore([fact(META_8K_Q3, "net_income", 9_860e6, 2026, "Q3"),
                         fact(META_8K_Q3, "net_income", 15_848e6, 2026, "Q2")])
    c = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms",
              period_text="third quarter of 2026", comparison="QoQ", metric_text="net income",
              span="net income fell 38% from the previous quarter")
    assert verify(c, release).status == "verified"  # "fell" signs the change, "loss" rules don't apply to a change
    rose = c.model_copy(update={"span_text": "net income rose 38% from the previous quarter"})
    assert verify(rose, release).status == "wrong"
    level = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms",
                  period_text="third quarter of 2026", metric_text="net income", span="net income of 38%")
    assert (verify(level, release).status, verify(level, release).reason) == ("unverifiable", "no_evidence")
    # From the two 10-Qs alone, each reporting one of the quarters, there is no change to check
    v = verify(c, STORE)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation.startswith("No single filing NoCap holds reports Meta Platforms' net income for both Q3 2026")


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


# --- S&P 500 scale: context lines, banks, unresolved names, unlabelled and long periods ------------------------
SO, AAPL_CIK, GS_CIK = 92122, 320193, 886982


@pytest.mark.parametrize("context, tickers", [
    ("Southern Copper Q3 2026", []),             # "Copper" is part of the name: never Southern Co.
    ("Goldman Sachs BDC Q3 2026", []),           # nor this subsidiary Goldman Sachs
    ("NVIDIA earnings Q2 FY2027", ["NVDA"]),
    ("NVIDIA quarterly results, Q2 FY2027", ["NVDA"]),
    ("Microsoft Earnings Call Transcript Q3 FY2026", ["MSFT"]),
    ("Apple 10-Q, second quarter of fiscal 2026", ["AAPL"]),
    ("Apple (AAPL), first quarter of fiscal 2026, the quarter ended December 27, 2025", ["AAPL"]),
    ("Q3 2026 (Apple)", ["AAPL"]),
])
def test_a_shorter_context_prefix_drops_only_lowercase_or_document_words(context, tickers):
    store = FakeStore(aliases={**ALIASES, "southern": (SO, "SO"), "apple": (AAPL_CIK, "AAPL"),
                               "goldman sachs": (GS_CIK, "GS")})
    assert [t for _, t, _ in verify_mod.context_entities(context, store)] == tickers


def test_a_generic_subject_under_a_lookalike_context_is_unresolved_not_wrong():
    store = FakeStore(aliases={**ALIASES, "southern": (SO, "SO")})
    v = verify(claim("$3.1 billion", 3.1, entity="the company", period_text="Q3 2026"), store,
               context="Southern Copper Q3 2026")
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "Southern" not in v.explanation


KEYCORP = 91576
BANK_PERIODS = {(cik, 2026, fp): span for cik in (KEYCORP, GS_CIK) for fp, span in (
    ("Q1", (date(2026, 1, 1), date(2026, 3, 31))), ("Q2", (date(2026, 4, 1), date(2026, 6, 30))))}


def bank_fact(cik, ticker, fp, value):  # hand-made figures
    start, end = BANK_PERIODS[(cik, 2026, fp)]
    accession = f"{cik:010d}-26-00000{fp[1]}"
    return Fact(cik=cik, ticker=ticker, metric="revenue", basis="GAAP", value=value, unit="USD", period_start=start,
                period_end=end, fiscal_year=2026, fiscal_period=fp, source="xbrl", derived_from=[], form="10-Q",
                accession=accession, accepted_at=utc(f"2026-0{3 * int(fp[1]) + 1}-28T20:00:00Z"),
                url=f"{EDGAR}/{cik}/{accession.replace('-', '')}/doc.htm")


BANK_STORE = FakeStore([bank_fact(KEYCORP, "KEY", "Q1", 1_922e6), bank_fact(KEYCORP, "KEY", "Q2", 1_890e6),
                   bank_fact(GS_CIK, "GS", "Q1", 15_060e6), bank_fact(GS_CIK, "GS", "Q2", 14_580e6)],
                  aliases={"keycorp": (KEYCORP, "KEY"), "goldman sachs": (GS_CIK, "GS")}, periods=BANK_PERIODS)


@pytest.mark.parametrize("entity, value_text, value, scale, expected", [
    ("KeyCorp", "$1.95 billion", 1.95, "billions", ("unverifiable", "ambiguous")),   # a regional bank now
    ("KeyCorp", "$1.92 billion", 1.92, "billions", ("unverifiable", "ambiguous")),   # = Q1's GAAP: may be managed
    ("KeyCorp", "$1.89 million", 1.89, "millions", ("wrong", "scale_mismatch")),     # no basis explains x1000
    ("Goldman Sachs", "$14.9 billion", 14.9, "billions", ("wrong", "value_mismatch")),  # headlines GAAP revenue
    ("Goldman Sachs", "$15.06 billion", 15.06, "billions", ("wrong", "period_mismatch")),
])
def test_bank_headline_revenue_with_no_basis_stated(entity, value_text, value, scale, expected):
    v = verify(claim(value_text, value, entity=entity, scale=scale, period_text="Q2 2026"), BANK_STORE)
    assert (v.status, v.reason) == expected
    if expected[0] == "unverifiable":
        assert "managed (fully taxable-equivalent)" in v.explanation
    stated = verify(claim(value_text, value, entity=entity, scale=scale, period_text="Q2 2026", basis="GAAP"), BANK_STORE)
    assert stated.status == "wrong"  # a stated basis is checked as written


@pytest.fixture
def covered(tmp_path):
    from receipts.store import Store, company_aliases, connect_rw, replace_company
    names = {"MPC": "Marathon Petroleum Corp", "PSA": "Public Storage", "LIN": "LINDE PLC", "APP": "AppLovin Corp",
             "AMZN": "AMAZON COM INC", "GS": "GOLDMAN SACHS GROUP INC", "MSFT": "MICROSOFT CORP",
             "META": "Meta Platforms, Inc."}
    conn = connect_rw(tmp_path / "facts.sqlite")
    for i, (ticker, name) in enumerate(names.items()):
        replace_company(conn, {"cik": 7000 + i, "ticker": ticker, "name": name, "aliases": company_aliases(ticker, name),
                               "fiscal_year_end": "1231"}, [], [], [])
    conn.close()
    s = Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr")
    yield s
    s.close()


@pytest.mark.parametrize("entity, reason, says, never", [
    ("Public", "no_evidence", "(did you mean Public Storage?)", "covers 8 companies"),  # "Marathon" is MPC's now
    ("Groupe PSA", "no_evidence", "it covers 8 companies today", "contains the name of"),
    ("Lin TV", "no_evidence", "it covers 8 companies today", "contains the name of"),
    ("App Store", "no_evidence", "it covers 8 companies today", "contains the name of"),
    ("Acme Robotics", "no_evidence", "it covers 8 companies today", "isn't one of them"),
    ("Amazon Web Services", "ambiguous", "contains the name of Amazon, which NoCap covers", "is part of"),
    ("Microsoft and Meta", "ambiguous", "names more than one company (Microsoft and Meta)", "couldn't match"),
])
def test_an_unresolved_name_is_explained_in_words_that_are_always_true(covered, entity, reason, says, never):
    v = verify(claim("$10 billion", 10, entity=entity, period_text="Q2 2026"), covered)
    assert (v.status, v.reason) == ("unverifiable", reason)
    assert says in v.explanation and never not in v.explanation
    if reason == "no_evidence":
        assert v.explanation.startswith(f'NoCap couldn\'t match "{entity}" to a company it covers')
    assert v.explanation.endswith(".") and not re.search(r"[.!?]\s+[A-Z]", v.explanation)  # one sentence


def test_a_period_without_a_fiscal_label_is_explained_in_words_that_are_always_true():
    """A past quarter whose fiscal label ingest dropped (impossible or clashing) is not a future one: "No filing
    covers it yet" would be false. Microsoft's calendar here has a later quarter after the missing label."""
    cal = {**PERIODS, (MSFT, 2027, "Q1"): (date(2026, 7, 1), date(2026, 9, 30))}
    v = verify(claim("$85 billion", 85, entity="Microsoft", period_text="Q4 FY2026"), FakeStore(periods=cal))
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == "NoCap couldn't match Q4 FY2026 to a fiscal period labelled in Microsoft's filings."


COSTCO = 909832
COSTCO_PERIODS = {(COSTCO, 2025, "Q3"): (date(2025, 2, 17), date(2025, 5, 11)),
                  (COSTCO, 2025, "Q4"): (date(2025, 5, 12), date(2025, 8, 31)),   # 16 weeks: 112 days
                  (COSTCO, 2025, "FY"): (date(2024, 9, 2), date(2025, 8, 31))}


def costco_fact(fp, value, *, start=None, source="derived"):  # values from Costco's FY2025 10-K
    s, e = COSTCO_PERIODS[(COSTCO, 2025, fp)]
    return Fact(cik=COSTCO, ticker="COST", metric="revenue", basis="GAAP", value=value, unit="USD",
                period_start=start or s, period_end=e, fiscal_year=2025, fiscal_period=fp, source=source,
                derived_from=["0000909832-25-000097"], form="10-K", accession="0000909832-25-000097",
                accepted_at=utc("2025-10-08T20:00:00Z"), url=f"{EDGAR}/{COSTCO}/000090983225000097/cost-20250831.htm")


def test_a_sixteen_week_fourth_quarter_the_calendar_labels_q4_is_a_quarter():
    store = FakeStore([costco_fact("Q4", 86_156e6), costco_fact("Q3", 63_205e6)], aliases={"costco": (COSTCO, "COST")},
                      periods=COSTCO_PERIODS)
    v = verify(claim("$86.16 billion", 86.16, entity="Costco", period_text="Q4 FY2025"), store)
    assert (v.status, v.reason) == ("verified", "match") and "(quarter ended Aug 31, 2025)" in v.explanation
    # The same 112-day span under another label is no quarter NoCap reads: "?", never a match
    long_q3 = FakeStore([costco_fact("Q3", 63_205e6, start=date(2025, 1, 20))], aliases={"costco": (COSTCO, "COST")},
                        periods=COSTCO_PERIODS)
    v = verify(claim("$63.2 billion", 63.2, entity="Costco", period_text="Q3 FY2025"), long_q3)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")


def test_the_fallback_scan_follows_the_same_case_and_word_rules():
    """A store without names_in_text gets an n-gram scan under the same rules as receipts.store.Store."""
    def named(text, store=STORE):
        return [t for _, t, _ in verify_mod.named_in_draft(text, store)]
    assert named("Shares of NVDA rose") == ["NVDA"] and named("Nvidia and meta rose") == ["NVDA", "META"]
    assert named("shares of nvda rose") == [] and named("WHY NVDA AND MSFT ROSE") == []
    visa = FakeStore(aliases={"visa": (1403161, "V"), "visa inc": (1403161, "V")})
    assert named("visa rules and the Visa card", visa) == [] and named("Visa Inc. said", visa) == ["V"]
    more = FakeStore(aliases={"capital one": (927628, "COF"), "charles river": (816761, "CRL")})
    assert named("A return on capital. One analyst said", more) == [] and named("Capital One said", more) == ["COF"]
    assert named("along the Charles River", more) == []  # a lookup-only phrase (store.SHORT_NAMES)


USB, LHX = 36104, 202058
NAME_PERIODS = {(USB, 2026, "Q2"): (date(2026, 4, 1), date(2026, 6, 30)),
                (LHX, 2026, "Q3"): (date(2026, 7, 4), date(2026, 10, 2))}


def name_fact(cik, ticker, accession, metric, value, fp):  # values from the 10-Qs (S&P 500 QA); acceptance hand-made
    start, end = NAME_PERIODS[(cik, 2026, fp)]
    return Fact(cik=cik, ticker=ticker, metric=metric, basis="GAAP", value=value, unit="USD", period_start=start,
                period_end=end, fiscal_year=2026, fiscal_period=fp, source="xbrl", form="10-Q", accession=accession,
                accepted_at=utc("2026-10-30T20:00:00Z"), url=f"{EDGAR}/{cik}/{accession.replace('-', '')}/10q.htm")


NAME_STORE = FakeStore([name_fact(USB, "USB", "0000036104-26-000044", "net_income", 2_177e6, "Q2"),
                        name_fact(LHX, "LHX", "0000202058-26-000087", "revenue", 6_060e6, "Q3")],
                       aliases={"us bancorp": (USB, "USB"), "u.s. bancorp": (USB, "USB"), "l3harris": (LHX, "LHX")},
                       periods=NAME_PERIODS)


@pytest.mark.parametrize("entity, span, value_text, value, metric", [
    ("U.S. Bancorp", "U.S. Bancorp reported net income of $2,177 million in the second quarter of 2026.",
     "$2,177 million", 2177, "net_income"),
    ("U.S. Bancorp", "US Bancorp reported net income of $2,177 million in the second quarter of 2026.",
     "$2,177 million", 2177, "net_income"),
    ("U.S. Bancorp", "U.S. Bancorp net income was $2,177 million in the second quarter of 2026.",
     "$2,177 million", 2177, "net_income"),
    ("L3Harris", "L3Harris reported revenue of $6,060 million in the third quarter of 2026.",
     "$6,060 million", 6060, "revenue"),
    ("L3Harris", "Revenue at L3Harris was $6,060 million in the third quarter of 2026.", "$6,060 million", 6060,
     "revenue"),
])
def test_a_company_name_with_dots_or_a_digit_inside_does_not_qualify_its_figure(entity, span, value_text, value, metric):
    """S&P 500 QA, G3: "U.S." was a leftover word (company words kept the dots that the leftover check strips), and the
    "3" of "L3Harris" counted as a figure that ended the wording, leaving "Harris"."""
    period = "second quarter of 2026" if metric == "net_income" else "third quarter of 2026"
    v = verify(claim(value_text, value, metric=metric, scale="millions", entity=entity, span=span, period_text=period),
               NAME_STORE)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


def test_us_wording_still_qualifies_another_companys_figure():
    """Only the claim's own company name stops counting: "U.S." in Meta's sentence still names a region."""
    for span in ("Meta's U.S. revenue was $64.601 billion in the third quarter of 2026.",
                 "Meta reported U.S. revenue of $64.601 billion in the third quarter of 2026."):
        v = verify(claim("$64.601 billion", 64.601, entity="Meta", period_text="third quarter of 2026", span=span), STORE)
        assert v.status == "unverifiable" and "U.S." in v.explanation, span


# --- a stock split the company's later filings show (S&P 500 QA, T4) --------------------------------------------
APH = 820313
APH_PERIODS = {(APH, 2024, "FY"): (date(2024, 1, 1), date(2024, 12, 31)),
               (APH, 2025, "Q2"): (date(2025, 4, 1), date(2025, 6, 30)),
               (APH, 2025, "Q3"): (date(2025, 7, 1), date(2025, 9, 30)),
               (APH, 2025, "FY"): (date(2025, 1, 1), date(2025, 12, 31)),
               (APH, 2026, "Q2"): (date(2026, 4, 1), date(2026, 6, 30)),
               (APH, 2026, "Q3"): (date(2026, 7, 1), date(2026, 9, 30))}
APH_10K_FY24 = ("10-K", "0001558370-25-000714", "2025-02-07T21:43:42Z")
APH_10Q_Q2_25 = ("10-Q", "0001558370-25-009610", "2025-07-25T20:06:08Z")
APH_10Q_Q3_25 = ("10-Q", "0001104659-25-102142", "2025-10-24T20:17:12Z")
APH_10K_FY25 = ("10-K", "0001104659-26-013549", "2026-02-11T22:28:12Z")
APH_10Q_Q2_26 = ("10-Q", "0001104659-26-089194", "2026-07-31T20:01:55Z")
APH_10Q_Q3_26 = ("10-Q", "0001104659-26-120646", "2026-10-30T20:01:49Z")  # the first filing after the 2-for-1 split


def aph_eps(filing, value, fy, fp, *, decimals=None, source="xbrl"):  # values from APH's filings (snapshot acac293)
    form, accession, accepted = filing
    start, end = APH_PERIODS[(APH, fy, fp)]
    return Fact(cik=APH, ticker="APH", metric="eps_diluted", basis="GAAP", value=value, unit="USD/share",
                period_start=start, period_end=end, fiscal_year=fy, fiscal_period=fp, source=source, form=form,
                accession=accession, accepted_at=utc(accepted), decimals=decimals,
                url=f"{EDGAR}/{APH}/{accession.replace('-', '')}/aph.htm")


APH_EPS = [aph_eps(APH_10K_FY24, 1.92, 2024, "FY"), aph_eps(APH_10Q_Q2_25, 0.86, 2025, "Q2"),
           aph_eps(APH_10Q_Q3_25, 0.97, 2025, "Q3"),
           aph_eps(APH_10K_FY25, 1.92, 2024, "FY"), aph_eps(APH_10K_FY25, 3.34, 2025, "FY"),
           aph_eps(APH_10Q_Q2_26, 0.86, 2025, "Q2"), aph_eps(APH_10Q_Q2_26, 1.37, 2026, "Q2"),
           aph_eps(APH_10Q_Q3_26, 0.49, 2025, "Q3"), aph_eps(APH_10Q_Q3_26, 0.79, 2026, "Q3")]


def aph_store(facts=APH_EPS, ticker="APH"):
    """ticker "APHX" stands for a company whose XBRL reports no split (receipts.splits has none for it)."""
    return FakeStore([f.model_copy(update={"ticker": ticker}) for f in facts], aliases={"amphenol": (APH, ticker)},
                     periods=APH_PERIODS)


# A second period restated by the same ratio (Q2 2025: $0.86, then $0.43, hand-made): without a split its XBRL reports,
# the ratio must show in two periods
APH_EPS_TWO = APH_EPS + [aph_eps(APH_10Q_Q3_26, 0.43, 2025, "Q2")]


def test_company_splits_dates_a_split_from_the_companys_own_versions():
    """Q3 2025's $0.97 became $0.49: a 2-for-1 split, shown first by the Q3 2026 10-Q. The FY2025 10-K and the Q2
    2026 10-Q repeat old-basis figures unchanged (FY2024's $1.92, Q2 2025's $0.86), so they are pre-split filings."""
    (split,) = verify_mod.company_splits(APH_EPS_TWO)
    assert (split.ratio, split.forward) == (2, True)
    assert split.old_basis_until == utc(APH_10Q_Q2_26[2]) and split.new_basis_from == utc(APH_10Q_Q3_26[2])
    assert [f.value for f in split.evidence] == [0.86, 0.43]  # the pair with the latest old version (Jul 2026)
    # One restated period alone is a restatement, not a split (Block's Q3 2023 EPS, -$0.05 then -$0.15)
    assert verify_mod.company_splits(APH_EPS) == []
    # ... unless the company's XBRL reports the split (2026-09-02): then the 10-Q of Jul 31, 2026 is the last on the old
    # basis even without the repeats
    reported = [e for e in split_events("APH") if e.starts.year == 2026]
    (split,) = verify_mod.company_splits(APH_EPS, reported)
    assert (split.ratio, split.forward, split.new_basis_from) == (2, True, utc(APH_10Q_Q3_26[2]))
    assert split.old_basis_until == utc(APH_10Q_Q2_26[2]) and [f.value for f in split.evidence] == [0.97, 0.49]
    # Two versions that differ only by rounding are no split (MCHP's -$0.01 in its 10-K and -$0.01 in a release)
    tiny = [aph_eps(APH_10K_FY25, -0.01, 2025, "FY", decimals=3),
            aph_eps(APH_10Q_Q2_26, -0.01, 2025, "FY", source="press_release")]
    assert verify_mod.company_splits(tiny) == []


@pytest.mark.parametrize("value_text, value, period, period_type, status, receipt", [
    ("$1.67", 1.67, "full year 2025", "annual", "unverifiable", APH_10K_FY25),  # $3.34 / 2: no filing has it yet
    ("$0.69", 0.69, "second quarter of 2026", "quarter", "unverifiable", APH_10Q_Q2_26),  # $1.37 / 2, rounded
    ("$3.34", 3.34, "full year 2025", "annual", "verified", APH_10K_FY25),       # as filed
    ("$1.50", 1.50, "full year 2025", "annual", "wrong", APH_10K_FY25),
    ("$0.49", 0.49, "third quarter of 2025", "quarter", "verified", APH_10Q_Q3_26),  # restated by the Q3 2026 10-Q
])
def test_a_split_adjusted_figure_for_a_period_no_filing_restated_yet_is_never_wrong(value_text, value, period,
                                                                                   period_type, status, receipt):
    v = verify(eps(value_text, value, entity="Amphenol", period_text=period, period_type=period_type,
                   span=f"Diluted EPS was {value_text} for {period}."), aph_store())
    assert v.status == status, v.explanation
    if status == "unverifiable":
        assert v.reason == "no_evidence" and "adjusted for the 2-for-1 stock split" in v.explanation
        # the period's own receipt only, never the split's two versions of Q3 2025 (code review H3)
        assert set(accessions(v)) == {receipt[1]}


def test_no_split_reading_before_the_split_shows_or_for_a_filing_not_known_to_be_pre_split():
    c = eps("$1.67", 1.67, entity="Amphenol", period_text="full year 2025", period_type="annual",
            span="Diluted EPS was $1.67 for full year 2025.")
    assert verify(c, aph_store(), as_of="2026-09-01T00:00:00Z").status == "wrong"  # no restated version public yet
    # For a company whose XBRL reports no split, only the figures date it. A later filing that repeats an old-basis
    # figure dates every earlier one: the Q2 2026 10-Q alone will do
    no_10k_repeat = [f for f in APH_EPS_TWO if not (f.accession == APH_10K_FY25[1] and f.fiscal_year == 2024)]
    assert verify(c, aph_store(no_10k_repeat, "APHX")).status == "unverifiable"
    # With no repeat at all, the FY2025 10-K (filed after the old $0.97) may already be on the new basis
    no_repeat = [f for f in no_10k_repeat if not (f.accession == APH_10Q_Q2_26[1] and f.fiscal_year == 2025)]
    assert verify(c, aph_store(no_repeat, "APHX")).status == "wrong"
    # APH's own XBRL dates the split (2026-09-02): every filing before it is on the old basis
    assert verify(c, aph_store(no_repeat)).status == "unverifiable"


# --- a change is computed within one filing (S&P 500 QA, T3) --------------------------------------------------
MLM = 916076
MLM_PERIODS = {(MLM, 2025, "Q2"): (date(2025, 4, 1), date(2025, 6, 30)),
               (MLM, 2026, "Q2"): (date(2026, 4, 1), date(2026, 6, 30))}


def mlm_revenue(accession, form, accepted, value, fy):  # values from MLM's filings (snapshot acac293)
    start, end = MLM_PERIODS[(MLM, fy, "Q2")]
    return Fact(cik=MLM, ticker="MLM", metric="revenue", basis="GAAP", value=value, unit="USD", period_start=start,
                period_end=end, fiscal_year=fy, fiscal_period="Q2", source="xbrl", form=form, accession=accession,
                accepted_at=utc(accepted), url=f"{EDGAR}/{MLM}/{accession.replace('-', '')}/mlm.htm")


# Martin Marietta sold a business: Q2 2025 revenue was $1,811M as first filed, $1,609M as recast for continuing
# operations in the FY2025 10-K and in the Q2 2026 10-Q, which reports Q2 2026 at $1,947M
MLM_STORE = FakeStore([
    mlm_revenue("0000950170-25-105060", "10-Q", "2025-08-07T20:18:28Z", 1_811e6, 2025),
    mlm_revenue("0001193125-26-059193", "10-K", "2026-02-19T20:16:57Z", 1_609e6, 2025),
    mlm_revenue("0001193125-26-326001", "10-Q", "2026-07-30T19:35:50Z", 1_609e6, 2025),
    mlm_revenue("0001193125-26-326001", "10-Q", "2026-07-30T19:35:50Z", 1_947e6, 2026),
], aliases={"martin marietta": (MLM, "MLM")}, periods=MLM_PERIODS)


@pytest.mark.parametrize("value_text, value, status", [("21%", 21, "verified"), ("7.5%", 7.5, "wrong")])
def test_a_change_against_a_recast_base_uses_the_current_filings_own_comparative(value_text, value, status):
    """$1,947M / $1,609M is +21.0%; +7.5% (against the $1,811M first filed) mixes two bases and is wrong."""
    v = verify(pct(value_text, value, "revenue_growth", comparison="YoY", entity="Martin Marietta",
                   period_text="second quarter of 2026", span=f"Revenue rose {value_text} year over year"), MLM_STORE)
    assert v.status == status, v.explanation
    assert "revenue grew 21.0" in v.explanation and set(accessions(v)) == {"0001193125-26-326001"}


def test_a_change_needs_both_figures_from_one_filing():
    # Without the Q2 2026 10-Q's comparative, Q2 2025 is only in other filings, one of them on another basis: "?"
    alone = FakeStore([f for f in MLM_STORE._facts if not (f.accession == "0001193125-26-326001" and f.fiscal_year == 2025)],
                      aliases={"martin marietta": (MLM, "MLM")}, periods=MLM_PERIODS)
    v = verify(pct("21%", 21, "revenue_growth", comparison="YoY", entity="Martin Marietta",
                   period_text="second quarter of 2026", span="Revenue rose 21% year over year"), alone)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation.startswith("No single filing NoCap holds reports Martin Marietta's revenue for both Q2 2026")
    # A derived fourth quarter pairs with the third quarter of the 10-Q it is derived from (68,127 / 57,006 = +19.5%)
    v = verify(pct("19.5%", 19.5, "revenue_growth", comparison="QoQ", period_text="fourth quarter of fiscal 2026"), STORE)
    assert v.status == "verified" and "10-K (derived)" in v.explanation


META_10Q_Q3_25 = (META, "META", "10-Q", "0001628280-25-047240", "2025-10-30T20:00:00Z", "meta-20250930.htm")  # time hand-made
META_SEQ_PERIODS = {**PERIODS, (META, 2025, "Q3"): (date(2025, 7, 1), date(2025, 9, 30))}


@pytest.mark.parametrize("comparative, status", [(2_709e6, "verified"), (3_100e6, "unverifiable")])
def test_a_sequential_change_from_two_10qs_counts_only_when_the_later_one_restated_nothing(comparative, status):
    """A 10-Q never reports the previous quarter, so a QoQ change comes from two filings. It counts when the later
    10-Q's year-ago figure is the value first filed: Meta's Q3 2026 10-Q repeats Q3 2025's net income of $2,709M, so
    no recast took effect since, and $9,860M against Q2's $15,848M (filed in between) is -37.8%. Had it reported Q3 2025
    otherwise (a hand-made $3,100M), the two quarters may be on different bases: "?"."""
    def q3_2025(filing, value):
        q3 = fact(filing, "net_income", value, 2026, "Q3")  # then moved a year back
        start, end = META_SEQ_PERIODS[(META, 2025, "Q3")]
        return q3.model_copy(update={"period_start": start, "period_end": end, "fiscal_year": 2025})
    store = FakeStore([fact(META_10Q_Q3, "net_income", 9_860e6, 2026, "Q3"),
                       fact(META_10Q_Q2, "net_income", 15_848e6, 2026, "Q2"),
                       q3_2025(META_10Q_Q3_25, 2_709e6), q3_2025(META_10Q_Q3, comparative)], periods=META_SEQ_PERIODS)
    c = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms",
              period_text="third quarter of 2026", comparison="QoQ", metric_text="net income",
              span="net income fell 38% from the previous quarter")
    v = verify(c, store)
    assert v.status == status, v.explanation
    if status == "verified":
        assert "fell 37.8% quarter over quarter" in v.explanation
        assert set(accessions(v)) == {META_10Q_Q3[3], META_10Q_Q2[3]}
    else:
        assert v.explanation.startswith("No single filing NoCap holds reports Meta Platforms' net income for both")


# --- splits the company's XBRL reports (S&P 500 QA round 3, T4) -------------------------------------------------
CVNA, CRWD = 1690820, 1535527
SPLIT_PERIODS = {(CVNA, 2025, "Q3"): (date(2025, 7, 1), date(2025, 9, 30)),
                 (CVNA, 2025, "FY"): (date(2025, 1, 1), date(2025, 12, 31)),
                 (CVNA, 2026, "Q1"): (date(2026, 1, 1), date(2026, 3, 31)),
                 (CVNA, 2026, "Q2"): (date(2026, 4, 1), date(2026, 6, 30)),
                 (CRWD, 2026, "Q2"): (date(2025, 5, 1), date(2025, 7, 31)),
                 (CRWD, 2026, "Q4"): (date(2025, 11, 1), date(2026, 1, 31)),
                 (CRWD, 2026, "FY"): (date(2025, 2, 1), date(2026, 1, 31))}


def split_fact(cik, ticker, filing, value, fy, fp, *, basis="GAAP"):  # values from the snapshot (1485dee)
    form, accession, accepted = filing
    start, end = SPLIT_PERIODS[(cik, fy, fp)]
    return Fact(cik=cik, ticker=ticker, metric="eps_diluted", basis=basis, value=value, unit="USD/share",
                period_start=start, period_end=end, fiscal_year=fy, fiscal_period=fp,
                source="press_release" if form == "8-K" else "xbrl", form=form, accession=accession,
                accepted_at=utc(accepted), url=f"{EDGAR}/{cik}/{accession.replace('-', '')}/doc.htm")


# Carvana's 5-for-1 (May 2026): its EPS is dimensional in XBRL, so releases carry it, and no period is held on both bases
CVNA_EPS = [split_fact(CVNA, "CVNA", ("8-K", "0001690820-26-000008", "2026-02-18T21:00:00Z"), 8.45, 2025, "FY"),
            split_fact(CVNA, "CVNA", ("10-K", "0001690820-26-000009", "2026-02-18T22:00:00Z"), 8.45, 2025, "FY"),
            split_fact(CVNA, "CVNA", ("8-K", "0001690820-26-000034", "2026-04-29T20:00:00Z"), 1.69, 2026, "Q1"),
            split_fact(CVNA, "CVNA", ("8-K", "0001690820-26-000054", "2026-07-29T20:00:00Z"), 0.42, 2026, "Q2"),
            split_fact(CVNA, "CVNA", ("8-K", "0001690820-26-000066", "2026-10-28T20:00:00Z"), 0.21, 2025, "Q3")]
# CrowdStrike's 4-for-1 (June 2026): -$0.31 restated as -$0.07 is no exact quarter
CRWD_EPS = [split_fact(CRWD, "CRWD", ("10-Q", "0001535527-25-000025", "2025-08-28T20:00:00Z"), -0.31, 2026, "Q2"),
            split_fact(CRWD, "CRWD", ("8-K", "0001535527-26-000007", "2026-03-03T21:00:00Z"), 1.12, 2026, "Q4",
                       basis="non-GAAP"),
            split_fact(CRWD, "CRWD", ("8-K", "0001535527-26-000029", "2026-08-26T20:00:00Z"), -0.07, 2026, "Q2"),
            split_fact(CRWD, "CRWD", ("8-K", "0001535527-26-000029", "2026-08-26T20:00:00Z"), 0.23, 2026, "Q2",
                       basis="non-GAAP"),
            split_fact(CRWD, "CRWD", ("10-Q", "0001535527-26-000031", "2026-08-27T20:00:00Z"), -0.07, 2026, "Q2")]


def split_store(facts, ticker):
    cik = facts[0].cik
    return FakeStore([f.model_copy(update={"ticker": ticker}) for f in facts],
                     aliases={"carvana": (cik, ticker), "crowdstrike": (cik, ticker)}, periods=SPLIT_PERIODS)


@pytest.mark.parametrize("value_text, value, period, period_type, status, says", [
    ("$1.03", 1.03, "third quarter of 2025", "quarter", "unverifiable", "$1.03 is close to $0.21 x 5 = $1.05"),
    ("$0.21", 0.21, "third quarter of 2025", "quarter", "verified", "Matches"),
    ("$1.69", 1.69, "full year 2025", "annual", "unverifiable", "adjusted for the 5-for-1 stock split"),
    ("$8.45", 8.45, "full year 2025", "annual", "verified", "Matches"),
])
def test_a_split_the_company_reports_reads_figures_no_period_holds_on_both_bases(value_text, value, period,
                                                                                period_type, status, says):
    """Carvana: Q3 2025 is held only as restated ($0.21), so the as-first-reported $1.03 is near 5 x $0.21 but can't be
    confirmed: "?" with the split named, never a claim about a pre-split figure NoCap never saw (code review H3), and
    only that period's receipt; FY2025's $8.45 / 5 is "?" too."""
    v = verify(eps(value_text, value, entity="Carvana", period_text=period, period_type=period_type,
                   span=f"Diluted EPS was {value_text} for {period}."), split_store(CVNA_EPS, "CVNA"))
    assert v.status == status and says in v.explanation, v.explanation
    if value_text == "$1.03":
        assert v.reason == "no_evidence" and "5-for-1 stock split" in v.explanation
        assert set(accessions(v)) == {"0001690820-26-000066"}  # Q3 2025's own receipt, the release of Oct 28, 2026
    # without a split its XBRL reports, nothing shows one: as filed, nothing else
    v = verify(eps(value_text, value, entity="Carvana", period_text=period, period_type=period_type,
                   span=f"Diluted EPS was {value_text} for {period}."), split_store(CVNA_EPS, "CVNX"))
    assert v.status == ("verified" if status == "verified" else "wrong")


def test_a_reported_split_is_not_visible_before_its_first_filing():
    """Before the first filing that reports it (Jul 29, 2026), FY2025's $1.69 is checked against the $8.45 as filed."""
    c = eps("$1.69", 1.69, entity="Carvana", period_text="full year 2025", period_type="annual",
            span="Diluted EPS was $1.69 for full year 2025.")
    assert verify(c, split_store(CVNA_EPS, "CVNA"), as_of="2026-07-01T00:00:00Z").status == "wrong"


@pytest.mark.parametrize("value_text, value, basis, period, status, says", [
    ("-$0.31", -0.31, "GAAP", "second quarter of fiscal 2026", "wrong", "as reported before a 4-for-1 stock split"),
    ("-$0.07", -0.07, "GAAP", "second quarter of fiscal 2026", "verified", "Matches"),
    # no pre-split non-GAAP figure is held: "?" with the split named (code review H3)
    ("$0.93", 0.93, "non-GAAP", "second quarter of fiscal 2026", "unverifiable", "is close to $0.23 x 4 = $0.92"),
    # the pre-split GAAP figure is held (-$0.31): a figure near 4 x -$0.07 that is not it is plainly wrong
    ("-$0.28", -0.28, "GAAP", "second quarter of fiscal 2026", "wrong", "was -$0.07 for Q2 FY2026"),
    ("$0.28", 0.28, "non-GAAP", "fourth quarter of fiscal 2026", "unverifiable", "adjusted for the 4-for-1 stock split"),
    ("$1.12", 1.12, "non-GAAP", "fourth quarter of fiscal 2026", "verified", "Matches"),
])
def test_a_restatement_recomputed_on_the_new_share_count_still_shows_the_split(value_text, value, basis, period,
                                                                                status, says):
    """CrowdStrike: -$0.31 / 4 is -$0.0775, but the restated figure is -$0.07; with the split its XBRL reports, the two
    are one figure on two share bases."""
    v = verify(eps(value_text, value, basis=basis, entity="CrowdStrike", period_text=period,
                   span=f"{basis} diluted EPS was {value_text} for the {period}."), split_store(CRWD_EPS, "CRWD"))
    assert v.status == status and says in v.explanation, v.explanation


def test_one_restated_period_or_switching_dividends_are_no_split():
    """Block's Q3 2023 EPS, -$0.05 then -$0.15 (one period), and Bunge's quarterly dividend, $0.68 and $2.72 by turns,
    show no split; two periods restated by one ratio do (AMCR's 1-for-5 shows in four)."""
    q = lambda v, acc, at, metric="eps_diluted": aph_eps(("10-Q", acc, at), v, 2025, "Q3").model_copy(  # noqa: E731
        update={"metric": metric})
    assert verify_mod.company_splits([q(-0.05, "a", "2025-10-24T20:00:00Z"), q(-0.15, "b", "2026-10-30T20:00:00Z")]) == []
    dividends = [q(2.72, "a", "2025-10-24T20:00:00Z", "dividend_per_share"),
                 q(0.68, "b", "2026-02-11T20:00:00Z", "dividend_per_share"),
                 q(2.72, "c", "2026-10-30T20:00:00Z", "dividend_per_share")]
    assert verify_mod.company_splits(dividends) == []


def test_every_split_the_table_lists_is_confirmed_by_the_served_figures():
    """receipts.splits holds only splits the served per-share figures confirm (company_splits finds each one), and its
    decision record lists exactly those."""
    import json
    from pathlib import Path
    from receipts.splits import SPLIT_EVENTS
    from receipts.store import Store
    root = Path(__file__).resolve().parents[1]
    record = json.loads((root / "eval/evidence_expansion/sp500-splits-20261031.json").read_text())
    confirmed = sorted((d["ticker"], d["n"], d["direction"] == "forward", d["d_old"]) for d in record["decisions"]
                       if d["decision"] == "split")
    assert confirmed == sorted((t, e.ratio, e.forward, str(e.starts)) for t, es in SPLIT_EVENTS.items() for e in es)
    s = Store(root / "data/snapshot/facts.sqlite", root / "data/snapshot/pr_facts")
    try:
        for ticker, events in SPLIT_EVENTS.items():
            cik = s.by_ticker(ticker)[0]
            facts = [f for m in sorted(verify_mod.PER_SHARE_METRICS) for f in s.facts(cik, m)]
            found = {(x.ratio, x.forward) for x in verify_mod.company_splits(facts, events)}
            assert {(e.ratio, e.forward) for e in events} <= found, ticker
    finally:
        s.close()
