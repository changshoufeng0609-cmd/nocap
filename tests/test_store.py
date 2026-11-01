"""Unit tests for the evidence layer: receipts.edgar, receipts.store, receipts.ingest.

Run: .venv/bin/python -m pytest -q tests/test_store.py

Most tests build a synthetic company through the real pipeline (build_company -> replace_company ->
Store), so no network is needed. The tests at the bottom read data/facts.sqlite and the EDGAR cache
when present and skip otherwise.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from receipts.edgar import Edgar, EdgarError, _norm_exhibit, _RateLimiter, parse_accepted
from receipts.ingest import build_company
from receipts.store import (ALIASES, DB_PATH, Store, company_aliases, connect_rw, display_name, normalize_entity,
                            replace_company)
from receipts.types import EvidenceStore, Fact

UTC = timezone.utc
CIK, TICKER = 999, "TEST"

# accession key -> (accession, form, filed, acceptanceDateTime, reportDate, DEI fy, DEI fp)
FILINGS = {
    "A1": ("0000000999-25-000001", "10-Q", "2025-04-30", "2025-04-30T20:00:00.000Z", "2025-03-31", 2025, "Q1"),
    "A2": ("0000000999-25-000002", "10-Q", "2025-07-30", "2025-07-30T20:00:00.000Z", "2025-06-30", 2025, "Q2"),
    "A3": ("0000000999-25-000003", "10-Q", "2025-10-30", "2025-10-30T20:00:00.000Z", "2025-09-30", 2025, "Q3"),
    "AK": ("0000000999-26-000001", "10-K", "2026-02-20", "2026-02-20T21:00:00.000Z", "2025-12-31", 2025, "FY"),
    "AP": ("0000000999-26-000002", "DEF 14A", "2026-03-20", "2026-03-20T12:00:00.000Z", None, 2025, "FY"),
    "B1": ("0000000999-26-000003", "10-Q", "2026-04-30", "2026-04-30T20:00:00.000Z", "2026-03-31", 2026, "Q1"),
    "E1": ("0000000999-25-000010", "8-K", "2025-10-29", "2025-10-29T20:05:00.000Z", "2025-10-29", None, None),
}
Q1, Q2, Q3 = ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"), ("2025-07-01", "2025-09-30")
H1, M9, FY = ("2025-01-01", "2025-06-30"), ("2025-01-01", "2025-09-30"), ("2025-01-01", "2025-12-31")
Q1_26, TTM_26 = ("2026-01-01", "2026-03-31"), ("2025-04-01", "2026-03-31")


def acc(key: str) -> str:
    return FILINGS[key][0]


def accepted(key: str) -> datetime:
    return parse_accepted(FILINGS[key][3])


def submission_rows(filings: dict) -> list[dict]:
    return [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": acc_at,
             "reportDate": report or "", "primaryDocument": "doc.htm", "items": "2.02,9.01" if form == "8-K" else ""}
            for a, form, filed, acc_at, report, _, _ in filings.values()]


def companyfacts(filings: dict, *entries) -> dict:
    """entries: (concept, period, value, filing key[, unit]); period is (start, end) or an instant date."""
    usgaap: dict = {}
    for concept, period, value, key, *unit in entries:
        a, form, filed, _, _, fy, fp = filings[key]
        start, end = period if isinstance(period, tuple) else (None, period)
        e = {"end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed}
        if start:
            e["start"] = start
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault(unit[0] if unit else "USD", []).append(e)
    return {"facts": {"us-gaap": usgaap}}


CF = companyfacts(
    FILINGS,
    # revenue: 3-month values, YTD and FY; Q1 2025 is restated (100 -> 101) in the 2026 Q1 10-Q
    ("Revenues", Q1, 100e6, "A1"), ("Revenues", Q2, 110e6, "A2"), ("Revenues", H1, 210e6, "A2"),
    ("Revenues", Q3, 120e6, "A3"), ("Revenues", M9, 330e6, "A3"), ("Revenues", FY, 460e6, "AK"),
    ("Revenues", Q1, 101e6, "B1"), ("Revenues", Q1_26, 140e6, "B1"),
    # net income, plus a pay-versus-performance figure in a DEF 14A that must never be used
    ("NetIncomeLoss", Q1, 10e6, "A1"), ("NetIncomeLoss", Q2, 11e6, "A2"), ("NetIncomeLoss", Q3, 12e6, "A3"),
    ("NetIncomeLoss", M9, 33e6, "A3"), ("NetIncomeLoss", FY, 46e6, "AK"), ("NetIncomeLoss", FY, 999e6, "AP"),
    # operating income has no 9M YTD: Q4 falls back to FY - Q1 - Q2 - Q3
    ("OperatingIncomeLoss", Q1, 20e6, "A1"), ("OperatingIncomeLoss", Q2, 21e6, "A2"),
    ("OperatingIncomeLoss", Q3, 22e6, "A3"), ("OperatingIncomeLoss", FY, 90e6, "AK"),
    # cash flow is year-to-date only in 10-Qs, plus a trailing-twelve-month value
    ("NetCashProvidedByUsedInOperatingActivities", Q1, 50e6, "A1"),
    ("NetCashProvidedByUsedInOperatingActivities", H1, 120e6, "A2"),
    ("NetCashProvidedByUsedInOperatingActivities", M9, 200e6, "A3"),
    ("NetCashProvidedByUsedInOperatingActivities", FY, 300e6, "AK"),
    ("NetCashProvidedByUsedInOperatingActivities", TTM_26, 310e6, "B1"),
    # EPS is not additive: never derived
    ("EarningsPerShareDiluted", Q1, 1.00, "A1", "USD/shares"), ("EarningsPerShareDiluted", Q2, 1.10, "A2", "USD/shares"),
    ("EarningsPerShareDiluted", Q3, 1.20, "A3", "USD/shares"), ("EarningsPerShareDiluted", M9, 3.30, "A3", "USD/shares"),
    ("EarningsPerShareDiluted", FY, 4.60, "AK", "USD/shares"),
    # AMD-style: both concepts in one filing with different meanings; OperatingExpenses must win
    ("CostsAndExpenses", Q3, 300e6, "A3"), ("OperatingExpenses", Q3, 70e6, "A3"),
    # an instant (balance) metric
    ("StockRepurchaseProgramRemainingAuthorizedRepurchaseAmount1", "2025-12-31", 5e9, "AK"),
)


def build_store(tmp_path: Path, filings: dict = FILINGS, cf: dict = CF, *, cik: int = CIK, ticker: str = TICKER,
                fye: str = "1231"):
    build = build_company(cik=cik, ticker=ticker, names=["Test Corp"], fiscal_year_end=fye,
                          filing_rows=submission_rows(filings), companyfacts=cf, facts_start=date(2020, 1, 1),
                          today=date(2026, 10, 27))
    conn = connect_rw(tmp_path / "facts.sqlite")
    replace_company(conn, build.company, build.filings, build.periods, build.facts)
    conn.close()
    return Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr"), build


@pytest.fixture
def store(tmp_path):
    s, _ = build_store(tmp_path)
    yield s
    s.close()


def only(facts: list[Fact], **kw) -> list[Fact]:
    return [f for f in facts if all(getattr(f, k) == v for k, v in kw.items())]


# --------------------------------------------------------------------------- derived quarters
@pytest.mark.parametrize("method", ["resolve_entity", "facts", "fiscal_calendar", "first_public"])
def test_store_matches_protocol_signatures(method):
    import inspect
    want = inspect.signature(getattr(EvidenceStore, method)).parameters
    have = inspect.signature(getattr(Store, method)).parameters
    assert [(p.name, p.kind, p.default) for p in want.values()] == \
           [(p.name, p.kind, p.default) for p in have.values()]


def test_derived_q4_is_fy_minus_nine_months(store):
    q4 = only(store.facts(CIK, "revenue", period_end=date(2025, 12, 31)), fiscal_period="Q4")
    assert len(q4) == 1
    f = q4[0]
    assert f.source == "derived" and f.value == 130e6           # 460 - 330
    assert f.derived_from == [acc("AK"), acc("A3")]
    assert f.accepted_at == accepted("AK")                     # latest input
    assert (f.form, f.accession) == ("10-K", acc("AK"))
    assert (f.period_start, f.period_end, f.fiscal_year) == (date(2025, 10, 1), date(2025, 12, 31), 2025)
    assert f.concept == "us-gaap:Revenues" and "9M YTD" in f.locator


def test_derived_q4_falls_back_to_quarter_sum(store):
    (f,) = only(store.facts(CIK, "operating_income", period_end=date(2025, 12, 31)), fiscal_period="Q4")
    assert f.value == 27e6                                     # 90 - 20 - 21 - 22
    assert f.derived_from == [acc("AK"), acc("A1"), acc("A2"), acc("A3")]


def test_eps_is_never_derived(store):
    facts = store.facts(CIK, "eps_diluted", period_end=date(2025, 12, 31))
    assert [f.fiscal_period for f in facts] == ["FY"]
    assert all(f.source == "xbrl" for f in store.facts(CIK, "eps_diluted"))


def test_ytd_differencing_for_cash_flow(store):
    ocf = {f.fiscal_period: f for f in store.facts(CIK, "operating_cash_flow") if f.fiscal_year == 2025}
    assert ocf["Q1"].source == "xbrl" and ocf["Q1"].value == 50e6
    assert (ocf["Q2"].value, ocf["Q2"].derived_from) == (70e6, [acc("A2"), acc("A1")])   # H1 - Q1
    assert (ocf["Q3"].value, ocf["Q3"].derived_from) == (80e6, [acc("A3"), acc("A2")])   # 9M - H1
    assert (ocf["Q4"].value, ocf["Q4"].derived_from) == (100e6, [acc("AK"), acc("A3")])  # FY - 9M
    assert ocf["Q3"].accepted_at == accepted("A3")
    assert ocf["Q2"].period_start == date(2025, 4, 1) and ocf["Q2"].period_end == date(2025, 6, 30)


def restated_q4_values(tmp_path, fy_v2: float, m9_v2: float) -> list[float]:
    """FY2025 is restated in the FY2026 10-K (CK); the Q3 2026 10-Q (C3) carries a 9M 2025 comparative."""
    filings = {k: FILINGS[k] for k in ("A3", "AK")}
    filings["C3"] = ("0000000999-26-000030", "10-Q", "2026-10-30", "2026-10-30T20:00:00.000Z", "2026-09-30", 2026, "Q3")
    filings["CK"] = ("0000000999-27-000001", "10-K", "2027-02-20", "2027-02-20T21:00:00.000Z", "2026-12-31", 2026, "FY")
    cf = companyfacts(filings, ("Revenues", Q3, 120e6, "A3"), ("Revenues", M9, 330e6, "A3"),
                      ("Revenues", FY, 460e6, "AK"), ("Revenues", M9, m9_v2, "C3"), ("Revenues", FY, fy_v2, "CK"))
    s, _ = build_store(tmp_path, filings, cf)
    return [f.value for f in only(s.facts(CIK, "revenue", period_end=date(2025, 12, 31)), fiscal_period="Q4")]


def test_restated_total_with_stale_components_is_not_derived(tmp_path):
    # BAC case: FY restated in a later 10-K, 9M never restated -> "500 - 330" would invent a Q4
    assert restated_q4_values(tmp_path, fy_v2=500e6, m9_v2=330e6) == [130e6]


def test_consistent_restatement_gives_a_new_derived_version(tmp_path):
    # NVDA split case: FY and 9M both restated (x10) -> a second, split-adjusted Q4 version
    assert restated_q4_values(tmp_path, fy_v2=4600e6, m9_v2=3300e6) == [130e6, 1300e6]


def test_no_derivation_when_a_quarter_was_filed(store):
    q2 = store.facts(CIK, "revenue", period_end=date(2025, 6, 30))
    assert [f.source for f in q2] == ["xbrl"] and q2[0].value == 110e6


def test_ytd_and_ttm_are_stored_but_not_served(store, tmp_path):
    ocf_end_q1_26 = store.facts(CIK, "operating_cash_flow", period_end=date(2026, 3, 31))
    assert ocf_end_q1_26 == []                                 # only a TTM value ends there
    nine_month = store.facts(CIK, "revenue", period_end=date(2025, 9, 30))
    assert [f.value for f in nine_month] == [120e6]           # the 9M value (330) is not served
    import sqlite3
    kinds = {r[0] for r in sqlite3.connect(tmp_path / "facts.sqlite").execute("SELECT period_kind FROM facts")}
    assert {"ytd", "ttm"} <= kinds


# --------------------------------------------------------------------------- DEF 14A exclusion
def test_def14a_facts_are_excluded(store):
    fy = only(store.facts(CIK, "net_income", period_end=date(2025, 12, 31)), fiscal_period="FY")
    assert [(f.value, f.form) for f in fy] == [(46e6, "10-K")]
    (q4,) = only(store.facts(CIK, "net_income", period_end=date(2025, 12, 31)), fiscal_period="Q4")
    assert q4.value == 13e6 and acc("AP") not in q4.derived_from  # 46 - 33, not 999 - 33
    assert all(f.form != "DEF 14A" for f in store.facts(CIK, "net_income"))


def test_def14a_is_counted_as_excluded(tmp_path):
    _, build = build_store(tmp_path)
    assert build.stats["excluded_forms"].get("DEF 14A") == 1


# --------------------------------------------------------------------------- versions and as_of
def test_every_version_is_kept_and_sorted(store):
    versions = store.facts(CIK, "revenue", period_end=date(2025, 3, 31))
    assert [(f.value, f.accession) for f in versions] == [(100e6, acc("A1")), (101e6, acc("B1"))]
    assert versions[0].accepted_at < versions[1].accepted_at


def test_as_of_filters_by_acceptance_time(store):
    end = date(2025, 3, 31)
    between = datetime(2025, 12, 1, tzinfo=UTC)
    assert [f.value for f in store.facts(CIK, "revenue", period_end=end, as_of=between)] == [100e6]
    assert store.facts(CIK, "revenue", period_end=end, as_of=accepted("A1") - timedelta(seconds=1)) == []
    assert [f.value for f in store.facts(CIK, "revenue", period_end=end, as_of=accepted("A1"))] == [100e6]
    naive = datetime(2025, 4, 30, 20, 0, 0)                   # naive means UTC: exactly the acceptance time
    assert len(store.facts(CIK, "revenue", period_end=end, as_of=naive)) == 1
    eastern = datetime(2025, 4, 30, 15, 59, 59, tzinfo=timezone(timedelta(hours=-4)))  # 19:59:59 UTC
    assert store.facts(CIK, "revenue", period_end=end, as_of=eastern) == []


def test_derived_fact_appears_when_its_last_input_is_public(store):
    end = date(2025, 12, 31)
    before = store.facts(CIK, "revenue", period_end=end, as_of=accepted("AK") - timedelta(minutes=1))
    after = store.facts(CIK, "revenue", period_end=end, as_of=accepted("AK"))
    assert before == [] and {f.fiscal_period for f in after} == {"Q4", "FY"}


def test_first_public_is_earliest_acceptance(store):
    assert store.first_public(CIK, "revenue", date(2025, 3, 31)) == accepted("A1")
    assert store.first_public(CIK, "revenue", date(2025, 12, 31)) == accepted("AK")
    assert store.first_public(CIK, "gross_profit", date(2025, 3, 31)) is None


# --------------------------------------------------------------------------- press-release facts
def pr_fact(**kw) -> Fact:
    base = dict(cik=CIK, ticker=TICKER, metric="revenue", basis="GAAP", value=120e6, unit="USD",
                period_start=date(2025, 7, 1), period_end=date(2025, 9, 30), fiscal_year=2025, fiscal_period="Q3",
                source="press_release", form="8-K", accession=acc("E1"), accepted_at=accepted("E1"),
                url="https://www.sec.gov/Archives/edgar/data/999/x/ex991.htm", locator="Total revenue", decimals=-6)
    base.update(kw)
    return Fact(**base)


def write_pr(tmp_path: Path, facts: list[Fact], extra_lines: list[str] = ()) -> None:
    folder = tmp_path / "pr" / TICKER
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f.model_dump_json() for f in facts] + list(extra_lines)
    (folder / f"{acc('E1')}.jsonl").write_text("\n".join(lines) + "\n")


def test_press_release_facts_are_loaded_at_query_time(store, tmp_path):
    end = date(2025, 9, 30)
    assert store.first_public(CIK, "revenue", end) == accepted("A3")
    write_pr(tmp_path, [pr_fact(), pr_fact(basis="non-GAAP", value=125e6)], ['{"not": "a fact"}', "{truncated"])
    facts = store.facts(CIK, "revenue", period_end=end)
    assert [(f.source, f.basis) for f in facts] == [("press_release", "GAAP"), ("press_release", "non-GAAP"),
                                                    ("xbrl", "GAAP")]
    assert [f.value for f in store.facts(CIK, "revenue", basis="non-GAAP")] == [125e6]
    assert store.first_public(CIK, "revenue", end) == accepted("E1")      # the 8-K beat the 10-Q
    assert store.first_public(CIK, "revenue", end, basis="non-GAAP") == accepted("E1")
    assert store.facts(CIK, "revenue", period_end=end, as_of=accepted("E1") - timedelta(seconds=1)) == []


def test_segment_filter(store, tmp_path):
    write_pr(tmp_path, [pr_fact(metric="segment_revenue", segment="Data Center", value=90e6)])
    end = date(2025, 9, 30)
    assert store.facts(CIK, "segment_revenue", period_end=end) == []          # None = consolidated only
    assert len(store.facts(CIK, "segment_revenue", segment="data center ", period_end=end)) == 1
    assert len(store.facts(CIK, "segment_revenue", segment="*", period_end=end)) == 1


# --------------------------------------------------------------------------- concept selection
def test_first_concept_wins_within_a_filing(store):
    (opex,) = store.facts(CIK, "operating_expenses", period_end=date(2025, 9, 30))
    assert (opex.concept, opex.value) == ("us-gaap:OperatingExpenses", 70e6)


def test_concept_switch_keeps_every_version(tmp_path):
    filings = {k: FILINGS[k] for k in ("A3", "AK", "B1")}
    cf = companyfacts(filings,
                      ("RevenueFromContractWithCustomerExcludingAssessedTax", Q3, 120e6, "A3"),
                      ("Revenues", Q3, 120e6, "B1"))                      # later filing switched concept
    s, _ = build_store(tmp_path, filings, cf)
    versions = s.facts(CIK, "revenue", period_end=date(2025, 9, 30))
    assert [(f.concept, f.accession) for f in versions] == [
        ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", acc("A3")), ("us-gaap:Revenues", acc("B1"))]
    assert s.first_public(CIK, "revenue", date(2025, 9, 30)) == accepted("A3")


def test_instant_metric(store):
    (f,) = store.facts(CIK, "repurchase_authorization_remaining")
    assert f.period_start is None and f.value == 5e9 and (f.fiscal_year, f.fiscal_period) == (2025, "FY")


def test_rebuild_is_idempotent(tmp_path):
    s1, b1 = build_store(tmp_path)
    snapshot = (s1.facts(CIK, "revenue"), s1.facts(CIK, "operating_cash_flow"), s1.fiscal_calendar(CIK))
    s1.close()
    s2, b2 = build_store(tmp_path)
    assert (s2.facts(CIK, "revenue"), s2.facts(CIK, "operating_cash_flow"), s2.fiscal_calendar(CIK)) == snapshot
    assert len(b1.facts) == len(b2.facts)
    s2.close()


# --------------------------------------------------------------------------- fiscal calendars
NVDA_FILINGS = {
    "Q3": ("0001045810-25-000230", "10-Q", "2025-11-19", "2025-11-19T21:36:17.000Z", "2025-10-26", 2026, "Q3"),
    "K": ("0001045810-26-000021", "10-K", "2026-02-25", "2026-02-25T21:42:19.000Z", "2026-01-25", 2026, "FY"),
    "Q1": ("0001045810-26-000052", "10-Q", "2026-05-20", "2026-05-20T20:35:52.000Z", "2026-04-26", 2027, "Q1"),
    "Q2": ("0001045810-26-000075", "10-Q", "2026-08-26", "2026-08-26T20:36:00.000Z", "2026-07-26", 2027, "Q2"),
}
NVDA_CF = companyfacts(
    NVDA_FILINGS,
    ("Revenues", ("2025-07-28", "2025-10-26"), 57_006e6, "Q3"), ("Revenues", ("2025-01-27", "2025-10-26"), 147_811e6, "Q3"),
    ("Revenues", ("2025-01-27", "2026-01-25"), 215_938e6, "K"),
    ("Revenues", ("2026-01-26", "2026-04-26"), 80_000e6, "Q1"),
    ("Revenues", ("2026-04-27", "2026-07-26"), 96_221e6, "Q2"), ("Revenues", ("2026-01-26", "2026-07-26"), 176_221e6, "Q2"),
)


def test_nvda_fiscal_calendar_non_calendar_year(tmp_path):
    s, build = build_store(tmp_path, NVDA_FILINGS, NVDA_CF, cik=1045810, ticker="NVDA", fye="0131")
    cal = s.fiscal_calendar(1045810)
    assert (2027, "Q2", date(2026, 4, 27), date(2026, 7, 26)) in cal
    assert (2027, "Q1", date(2026, 1, 26), date(2026, 4, 26)) in cal
    assert (2026, "FY", date(2025, 1, 27), date(2026, 1, 25)) in cal
    assert (2026, "Q4", date(2025, 10, 27), date(2026, 1, 25)) in cal
    assert build.warnings == []
    (q2,) = s.facts(1045810, "revenue", period_end=date(2026, 7, 26))
    assert (q2.fiscal_year, q2.fiscal_period, q2.value) == (2027, "Q2", 96_221e6)
    (q4,) = only(s.facts(1045810, "revenue", period_end=date(2026, 1, 25)), fiscal_period="Q4")
    assert q4.value == 68_127e6 and q4.fiscal_year == 2026


# --------------------------------------------------------------------------- entities
@pytest.fixture
def ten_companies(tmp_path):
    sec_names = {"NVDA": "NVIDIA CORP", "AAPL": "Apple Inc.", "MSFT": "MICROSOFT CORP", "META": "Meta Platforms, Inc.",
                 "AMZN": "AMAZON COM INC", "GOOGL": "Alphabet Inc.", "JPM": "JPMORGAN CHASE & CO",
                 "BAC": "BANK OF AMERICA CORP /DE/", "AMD": "ADVANCED MICRO DEVICES INC", "NFLX": "NETFLIX INC"}
    conn = connect_rw(tmp_path / "facts.sqlite")
    for i, (ticker, name) in enumerate(sec_names.items()):
        replace_company(conn, {"cik": 1000 + i, "ticker": ticker, "name": name,
                               "aliases": company_aliases(ticker, name), "fiscal_year_end": "1231"}, [], [], [])
    conn.close()
    s = Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr")
    yield s
    s.close()


@pytest.mark.parametrize("name, ticker", [
    ("NVIDIA", "NVDA"), ("Nvidia", "NVDA"), ("Nvidia's", "NVDA"), ("NVIDIA Corporation", "NVDA"), ("nvda", "NVDA"),
    ("Alphabet", "GOOGL"), ("Google", "GOOGL"), ("Alphabet Inc.", "GOOGL"), ("GOOG", "GOOGL"),
    ("Meta", "META"), ("Meta Platforms", "META"), ("Facebook", "META"), ("Meta Platforms, Inc.", "META"),
    ("JPMorgan", "JPM"), ("JPMorgan Chase", "JPM"), ("JPMorgan Chase & Co.", "JPM"), ("J.P. Morgan", "JPM"),
    ("Bank of America", "BAC"), ("Bank of America Corp.", "BAC"), ("BofA", "BAC"),
    ("AMD", "AMD"), ("Advanced Micro Devices", "AMD"),
    ("Netflix", "NFLX"), ("Amazon", "AMZN"), ("Amazon.com", "AMZN"), ("Apple", "AAPL"), ("Apple Inc", "AAPL"),
    ("Microsoft", "MSFT"), ("Apple Inc. (AAPL)", "AAPL"), ("JPMorgan’s", "JPM"),
])
def test_entity_aliases(ten_companies, name, ticker):
    hit = ten_companies.resolve_entity(name)
    assert hit is not None and hit[1] == ticker


# Segments and subsidiaries stay unresolved: resolving them to the parent would compare a segment
# number with the parent's total and produce a false "wrong".
@pytest.mark.parametrize("name", ["Tesla", "AWS", "Amazon Web Services", "Google Cloud", "Microsoft Azure",
                                  "America", "", "   ", "Chase"])
def test_unknown_entities_resolve_to_none(ten_companies, name):
    assert ten_companies.resolve_entity(name) is None


SEC_NAMES = {
    "NVDA": "NVIDIA CORP", "AAPL": "Apple Inc.", "MSFT": "MICROSOFT CORP", "META": "Meta Platforms, Inc.",
    "AMZN": "AMAZON COM INC", "GOOGL": "Alphabet Inc.", "JPM": "JPMORGAN CHASE & CO", "BAC": "BANK OF AMERICA CORP /DE/",
    "AMD": "ADVANCED MICRO DEVICES INC", "NFLX": "NETFLIX INC", "TSLA": "Tesla, Inc.", "AVGO": "Broadcom Inc.",
    "ORCL": "ORACLE CORP", "CRM": "Salesforce, Inc.", "ADBE": "ADOBE INC.", "INTC": "INTEL CORP",
    "QCOM": "QUALCOMM INC/DE", "CSCO": "CISCO SYSTEMS, INC.", "IBM": "INTERNATIONAL BUSINESS MACHINES CORP",
    "PLTR": "Palantir Technologies Inc.", "UBER": "Uber Technologies, Inc", "WMT": "Walmart Inc.",
    "COST": "COSTCO WHOLESALE CORP /NEW", "DIS": "Walt Disney Co", "NKE": "NIKE, Inc.", "MCD": "MCDONALDS CORP",
    "KO": "COCA COLA CO", "LLY": "ELI LILLY & Co", "UNH": "UNITEDHEALTH GROUP INC", "JNJ": "JOHNSON & JOHNSON",
    "GS": "GOLDMAN SACHS GROUP INC", "WFC": "WELLS FARGO & COMPANY/MN", "GM": "General Motors Co",
    "MS": "Morgan Stanley",
    "HPE": "Hewlett Packard Enterprise Co",
    "XOM": "ExxonMobil Holdings Corp",  # the registrant's SEC title since its 2026 holding-company reorganization
}


def test_alias_table_covers_every_company():
    assert set(ALIASES) == set(SEC_NAMES)
    assert normalize_entity("JPMorgan Chase & Co.") == "jpmorgan chase"
    assert normalize_entity("BANK OF AMERICA CORP /DE/") == "bank of america"


def test_no_alias_belongs_to_two_companies():
    owners: dict[str, set[str]] = {}
    for ticker in ALIASES:
        for key in company_aliases(ticker, SEC_NAMES[ticker]):
            owners.setdefault(key, set()).add(ticker)
    assert {k: v for k, v in owners.items() if len(v) > 1} == {}


@pytest.fixture
def all_companies(tmp_path):
    conn = connect_rw(tmp_path / "facts.sqlite")
    for i, (ticker, name) in enumerate(SEC_NAMES.items()):
        replace_company(conn, {"cik": 2000 + i, "ticker": ticker, "name": name,
                               "aliases": company_aliases(ticker, name), "fiscal_year_end": "1231"}, [], [], [])
    conn.close()
    s = Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr")
    yield s
    s.close()


@pytest.mark.parametrize("name, ticker", [
    ("Walmart", "WMT"), ("Wal-Mart", "WMT"), ("Coca-Cola", "KO"), ("The Coca-Cola Company", "KO"),
    ("J&J", "JNJ"), ("Johnson & Johnson", "JNJ"), ("Lilly", "LLY"), ("Eli Lilly", "LLY"), ("Eli Lilly and Co.", "LLY"),
    ("UnitedHealth", "UNH"), ("UnitedHealth Group", "UNH"), ("Goldman", "GS"), ("Goldman Sachs", "GS"),
    ("Wells Fargo", "WFC"), ("Salesforce", "CRM"), ("Broadcom", "AVGO"), ("Oracle", "ORCL"), ("Adobe", "ADBE"),
    ("Intel", "INTC"), ("Qualcomm", "QCOM"), ("Cisco", "CSCO"), ("IBM", "IBM"), ("International Business Machines", "IBM"),
    ("Palantir", "PLTR"), ("Uber", "UBER"), ("Costco", "COST"), ("Disney", "DIS"), ("The Walt Disney Company", "DIS"),
    ("Nike", "NKE"), ("McDonald's", "MCD"), ("McDonalds", "MCD"),
    # the original companies still resolve once the table grows
    ("Meta", "META"), ("Alphabet", "GOOGL"), ("Google", "GOOGL"), ("NVIDIA", "NVDA"), ("JPMorgan", "JPM"),
    ("Bank of America", "BAC"), ("Tesla", "TSLA"), ("Apple", "AAPL"), ("Microsoft", "MSFT"), ("Amazon", "AMZN"),
    # the names people use for Exxon Mobil, whose SEC title is now "ExxonMobil Holdings Corp"
    ("Exxon", "XOM"), ("ExxonMobil", "XOM"), ("Exxon Mobil", "XOM"), ("Exxon Mobil Corporation", "XOM"),
])
def test_expanded_entity_aliases(all_companies, name, ticker):
    hit = all_companies.resolve_entity(name)
    assert hit is not None and hit[1] == ticker


# Companies left out of this table, a bottler that shares a nickname, and segments stay unresolved.
@pytest.mark.parametrize("name", ["Visa", "Mastercard", "Coke", "AWS", "Google Cloud", "America", "Chase", "Azure"])
def test_expanded_unknown_entities_resolve_to_none(all_companies, name):
    assert all_companies.resolve_entity(name) is None


def test_profit_loss_stands_in_for_net_income_only_without_a_noncontrolling_interest(tmp_path):
    filings = {k: FILINGS[k] for k in ("A1", "A2", "A3")}
    cf = companyfacts(filings,
                      ("ProfitLoss", Q1, 10e6, "A1"),                                         # no NCI: net income
                      ("ProfitLoss", Q2, 11e6, "A2"),
                      ("NetIncomeLossAttributableToNoncontrollingInterest", Q2, 1e6, "A2"),  # NCI: not net income
                      ("NetIncomeLoss", Q3, 12e6, "A3"), ("ProfitLoss", Q3, 12.5e6, "A3"))    # both: NetIncomeLoss
    s, build = build_store(tmp_path, filings, cf)
    got = {f.period_end: (f.concept, f.value) for f in s.facts(CIK, "net_income")}
    # Q2 tags an NCI and Q3 shows one (ProfitLoss above NetIncomeLoss), so Q1's ProfitLoss may include one too: "?"
    # (review fix 15); Q2's own filing tags both sides, so its net income is ProfitLoss less that NCI, derived
    assert got == {date(2025, 6, 30): ("us-gaap:NetIncomeLoss", 10e6), date(2025, 9, 30): ("us-gaap:NetIncomeLoss", 12e6)}
    assert build.stats["consolidated_net_income_skipped"] == 2 and build.stats["attributable_net_income_derived"] == 1
    q2 = next(f for f in s.facts(CIK, "net_income") if f.period_end == date(2025, 6, 30))
    assert q2.source == "derived" and q2.derived_from == [q2.accession] and "ProfitLoss 11,000,000" in q2.locator
    s.close()
    (tmp_path / "none").mkdir()
    s, build = build_store(tmp_path / "none", filings, companyfacts(filings, ("ProfitLoss", Q1, 10e6, "A1"),
                                                                    ("ProfitLoss", Q2, 11e6, "A2")))
    got = {f.period_end: (f.concept, f.value) for f in s.facts(CIK, "net_income")}
    assert got == {date(2025, 3, 31): ("us-gaap:ProfitLoss", 10e6), date(2025, 6, 30): ("us-gaap:ProfitLoss", 11e6)}
    s.close()


def test_a_contract_revenue_slice_never_stands_for_revenue(tmp_path):
    filings = {k: FILINGS[k] for k in ("A1", "A2", "A3", "AK")}
    rfc = "RevenueFromContractWithCustomerExcludingAssessedTax"
    cf = companyfacts(filings,
                      (rfc, Q1, 150e6, "A1"), ("NoninterestExpense", Q1, 1.2e9, "A1"),  # a bank's fee income,
                      ("InterestIncomeExpenseNet", Q1, 1.4e9, "A1"),                   # beside its interest income
                      (rfc, Q2, 500e6, "A2"),                                          # no cost reported: kept
                      (rfc, Q3, 900e6, "A3"), ("OperatingExpenses", Q3, 1.2e9, "A3"),   # at least half: kept
                      ("CostOfRevenue", Q3, 600e6, "A3"),
                      (rfc, FY, 13e6, "AK"), ("CostOfGoodsAndServicesSold", FY, 567e6, "AK"))  # a REIT's 10-K
    s, build = build_store(tmp_path, filings, cf)
    got = {f.period_end: f.value for f in s.facts(CIK, "revenue")}
    assert got == {date(2025, 6, 30): 500e6, date(2025, 9, 30): 900e6}
    assert build.stats["component_revenue_skipped"] == 2
    s.close()


# --------------------------------------------------------------------------- EDGAR client (offline)
def test_parse_accepted_is_utc():
    assert parse_accepted("2026-08-26T20:36:00.000Z") == datetime(2026, 8, 26, 20, 36, tzinfo=UTC)


def test_exhibit_type_normalization():
    assert _norm_exhibit("ex-99.01") == "EX-99.1" and _norm_exhibit("EX-99") == "EX-99"


def test_offline_cache_miss_raises(tmp_path):
    with pytest.raises(EdgarError):
        Edgar(offline=True, cache_dir=tmp_path).get("https://www.sec.gov/not-cached")


def test_rate_limiter_spaces_requests():
    limiter, t0 = _RateLimiter(0.05), time.monotonic()
    for _ in range(3):
        limiter.wait()
    assert time.monotonic() - t0 >= 0.1


def test_exhibit_url_from_cached_index():
    ed = Edgar(offline=True)
    try:
        url = ed.exhibit_url(1045810, "0001045810-26-000073", "EX-99.1")
    except EdgarError:
        pytest.skip("NVDA Q2 FY2027 8-K index not in the local cache")
    assert url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm"


# --------------------------------------------------------------------------- real database (if built)
@pytest.fixture(scope="module")
def real():
    if not DB_PATH.exists():
        pytest.skip("data/facts.sqlite not built; run python -m receipts.ingest")
    s = Store()
    if s.resolve_entity("NVIDIA") is None:
        pytest.skip("NVDA not ingested")
    yield s
    s.close()


def test_real_nvda_spot_check(real):
    cik, ticker = real.resolve_entity("NVIDIA")
    assert (cik, ticker) == (1045810, "NVDA")
    end = date(2026, 7, 26)
    (f,) = [f for f in real.facts(cik, "revenue", period_end=end) if f.source == "xbrl"]
    assert (f.value, f.form, f.fiscal_year, f.fiscal_period) == (96_221_000_000, "10-Q", 2027, "Q2")
    assert f.accepted_at == datetime(2026, 8, 26, 20, 36, tzinfo=UTC)
    first = real.first_public(cik, "revenue", end)
    assert first is not None and first <= f.accepted_at


def test_real_fiscal_calendars(real):
    nvda = real.fiscal_calendar(1045810)
    assert (2027, "Q2", date(2026, 4, 27), date(2026, 7, 26)) in nvda
    assert (2026, "Q3", date(2026, 3, 29), date(2026, 6, 27)) in real.fiscal_calendar(320193)    # AAPL
    assert (2026, "FY", date(2025, 7, 1), date(2026, 6, 30)) in real.fiscal_calendar(789019)     # MSFT


# --------------------------------------------------------------------------- entity safety at S&P 500 scale
WORD_TICKER_COMPANIES = {  # ticker -> SEC-style name: each ticker is an everyday word or two letters or fewer
    "T": "AT&T INC.", "F": "FORD MOTOR CO", "A": "AGILENT TECHNOLOGIES, INC.", "C": "CITIGROUP INC", "IT": "GARTNER INC",
    "ON": "ON SEMICONDUCTOR CORP", "NOW": "ServiceNow, Inc.", "LOW": "LOWE'S COMPANIES INC", "ALL": "ALLSTATE CORP",
    "KEY": "KEYCORP /NEW/", "MO": "ALTRIA GROUP, INC.", "CAT": "CATERPILLAR INC", "HAS": "HASBRO, INC.",
    "BALL": "BALL Corp", "CARR": "Carrier Global Corp", "COST": "COSTCO WHOLESALE CORP /NEW", "KO": "COCA COLA CO",
    "GS": "GOLDMAN SACHS GROUP INC",
    # S&P 500 tickers that are words, names or abbreviations in capitals (approved 2026-10-31)
    "APP": "AppLovin Corp", "USB": "US BANCORP \\DE\\", "LIN": "LINDE PLC", "BRO": "BROWN & BROWN, INC.",
    "PSA": "Public Storage", "DOC": "Healthpeak Properties, Inc.", "DOW": "DOW INC.",
}
# Tickers that resolve when a claim's company is written that way, but that running text never counts, also not in
# capitals: "FICO score", "MSCI index", "RTX 5090", "start-ups", "AES encryption", "APA style", a "PTC" tax credit.
WORD_NAME_TICKERS = {"UPS": "UNITED PARCEL SERVICE INC", "FICO": "FAIR ISAAC CORP", "MSCI": "MSCI Inc.",
                     "RTX": "RTX Corp", "AES": "AES CORP", "APA": "APA Corp", "PTC": "PTC INC."}
OTHER_COMPANIES = {"NVDA": "NVIDIA CORP", "MSFT": "MICROSOFT CORP", "CVX": "CHEVRON CORP", "MRK": "MERCK & CO., INC.",
                   "FLEX": "FLEX LTD.", "TGT": "TARGET CORP", "V": "VISA INC.", "MCO": "MOODYS CORP /DE/"}


@pytest.fixture
def word_tickers(tmp_path):
    conn = connect_rw(tmp_path / "facts.sqlite")
    companies = {**WORD_TICKER_COMPANIES, **WORD_NAME_TICKERS, **OTHER_COMPANIES, "AAPL": "Apple Inc."}
    for i, (ticker, name) in enumerate(companies.items()):
        replace_company(conn, {"cik": 3000 + i, "ticker": ticker, "name": name,
                               "aliases": company_aliases(ticker, name), "fiscal_year_end": "1231"}, [], [], [])
    conn.close()
    s = Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr")
    yield s
    s.close()


# BALL is a lookup-only short name; GS is an explicit lookup alias (review L9).
@pytest.mark.parametrize("word", [*(t for t in WORD_TICKER_COMPANIES if t not in {"BALL", "GS"}), "t", "it", "now", "all", "cost",
                                  "Dow", "Key", "Low"])
def test_a_word_ticker_never_resolves_by_itself(word_tickers, word):
    assert word_tickers.resolve_entity(word) is None


@pytest.mark.parametrize("name, ticker", [
    ("AT&T", "T"), ("Ford Motor", "F"), ("Agilent Technologies", "A"), ("Citigroup", "C"), ("Gartner", "IT"),
    ("ON Semiconductor", "ON"), ("ServiceNow", "NOW"), ("Lowe's Companies", "LOW"), ("Allstate", "ALL"),
    ("KeyCorp", "KEY"), ("Altria Group", "MO"), ("Caterpillar", "CAT"), ("Hasbro", "HAS"), ("Carrier Global", "CARR"),
    ("Costco", "COST"), ("Coca-Cola", "KO"), ("Goldman Sachs", "GS"), ("Apple", "AAPL"), ("AAPL", "AAPL"),
    ("AppLovin", "APP"), ("US Bancorp", "USB"), ("Linde", "LIN"), ("Brown & Brown", "BRO"), ("Public Storage", "PSA"),
    ("Healthpeak Properties", "DOC"), ("Altria", "MO"),
    # WORD_NAMES keeps these out of running text only: a claim whose company is written "FICO" still resolves
    ("UPS", "UPS"), ("FICO", "FICO"), ("MSCI", "MSCI"), ("RTX", "RTX"), ("AES", "AES"), ("APA", "APA"), ("PTC", "PTC"),
    ("Chevron", "CVX"), ("Merck", "MRK"), ("Moody's", "MCO"), ("Moodys", "MCO"),
    ("Ford", "F"), ("Ball", "BALL"), ("Flex", "FLEX"), ("Dow Inc.", "DOW"),  # lookup-only short names
])
def test_word_ticker_companies_still_resolve_by_name(word_tickers, name, ticker):
    hit = word_tickers.resolve_entity(name)
    assert hit is not None and hit[1] == ticker


def test_a_drafts_ordinary_words_name_no_company(word_tickers):
    text = ("Apple said it is on track: all of its cost of revenue fell, and now the key low-cost lines are a fast "
            "path. C-suite leaders at the ball game had a T-shirt. Start-ups pitched the app over USB-C, analyst Lin "
            "said, and a tech bro checked his FICO score, the MSCI index, an RTX 5090, Groupe PSA, AES encryption, APA "
            "style and a PTC on wind power, with ups and downs. Chevron signs, a merck, a moody target and visa rules.")
    assert [t for _, t, _ in word_tickers.names_in_text(text)] == ["AAPL"]
    named = word_tickers.names_in_text("Allstate and ServiceNow beat Costco, AT&T and Goldman Sachs.")
    assert [t for _, t, _ in named] == ["ALL", "NOW", "COST", "T", "GS"]


def test_every_lookup_key_in_the_snapshot_names_exactly_one_company():
    s = Store(DB_PATH.parent / "snapshot" / "facts.sqlite")
    try:
        lookup = s._index()[0]
        assert {k: sorted(t for _, t in v) for k, v in lookup.items() if len(v) > 1} == {}
    finally:
        s.close()


# --------------------------------------------------------------------------- DEI label reuse (receipts.fiscal_labels)
def test_a_dei_label_reused_a_year_later_is_relabelled_by_the_companys_convention(tmp_path):
    filings = {"K1": ("0000000999-25-000100", "10-K", "2025-06-20", "2025-06-20T20:00:00.000Z", "2025-05-31", 2025, "FY"),
               "Q1A": ("0000000999-25-000101", "10-Q", "2024-09-10", "2024-09-10T20:00:00.000Z", "2024-08-31", 2025, "Q1"),
               "Q1B": ("0000000999-26-000101", "10-Q", "2025-09-10", "2025-09-10T20:00:00.000Z", "2025-08-31", 2025, "Q1")}
    cf = companyfacts(filings, ("Revenues", ("2024-06-01", "2025-05-31"), 430e6, "K1"),
                      ("Revenues", ("2024-06-01", "2024-08-31"), 100e6, "Q1A"),
                      ("Revenues", ("2025-06-01", "2025-08-31"), 120e6, "Q1B"),
                      ("Revenues", ("2024-06-01", "2024-08-31"), 100e6, "Q1B"))
    s, build = build_store(tmp_path, filings, cf, fye="0531")
    cal = {(fy, fp): (start, end) for fy, fp, start, end in s.fiscal_calendar(CIK)}
    assert cal[(2025, "Q1")] == (date(2024, 6, 1), date(2024, 8, 31))
    assert cal[(2026, "Q1")] == (date(2025, 6, 1), date(2025, 8, 31))  # the 10-Q said Q1 FY2025 again
    (latest,) = s.facts(CIK, "revenue", period_end=date(2025, 8, 31))
    assert (latest.fiscal_year, latest.fiscal_period, latest.value) == (2026, "Q1", 120e6)
    assert any("that period is Q1 FY2026: relabelled" in w for w in build.warnings)
    s.close()


def test_a_dei_label_two_periods_claim_is_dropped(tmp_path):
    filings = {"Q1A": ("0000000999-25-000101", "10-Q", "2024-09-10", "2024-09-10T20:00:00.000Z", "2024-08-31", 2025, "Q1"),
               "Q1B": ("0000000999-25-000102", "10-Q", "2024-11-10", "2024-11-10T20:00:00.000Z", "2024-10-31", 2025, "Q1")}
    cf = companyfacts(filings, ("Revenues", ("2024-06-01", "2024-08-31"), 100e6, "Q1A"),
                      ("Revenues", ("2024-08-01", "2024-10-31"), 110e6, "Q1B"))
    s, build = build_store(tmp_path, filings, cf, fye="0531")
    assert s.fiscal_calendar(CIK) == []  # no 10-K to settle it: neither period is labelled, so both answer "?"
    assert any("claimed for different periods" in w and "label dropped" in w for w in build.warnings)
    assert all(f.fiscal_period is None for f in s.facts(CIK, "revenue"))
    s.close()


def test_a_word_name_resolves_only_when_written_with_its_legal_suffix(word_tickers):
    """Dow Inc.'s own name is the word "dow" (also the Dow index): "Dow" alone never resolves, "Dow Inc." does. Ball's
    is lookup-only now (SHORT_NAMES): "Ball" resolves as a claim's company, and running text still never counts it."""
    assert word_tickers.resolve_entity("Dow") is None and word_tickers.resolve_entity("DOW") is None
    for written in ("Dow Inc.", "Dow Inc", "DOW INC."):
        hit = word_tickers.resolve_entity(written)
        assert hit is not None and hit[1] == "DOW"
    assert word_tickers.word_name("Dow")[1] == "DOW" and word_tickers.word_name("Costco") is None
    assert word_tickers.resolve_entity("Ball")[1] == "BALL" and word_tickers.names_in_text("Ball and Flex rose") == []


def test_a_bare_word_name_is_explained_not_called_uncovered(word_tickers):
    from receipts.types import Claim
    from receipts.verify import verify
    span = "Dow's revenue for the third quarter of 2026 was $3.2 billion."
    c = Claim(id="c1", metric="revenue", metric_text="revenue", value_text="$3.2 billion", value=3.2, unit="USD",
              scale="billions", period_text="the third quarter of 2026", period_type="quarter", comparison="none",
              basis=None, forward_looking=False, entity="Dow", segment=None, span_text=span, span_start=0,
              span_end=len(span))
    v = verify(c, word_tickers)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "everyday word" in v.explanation


def test_exxon_mobil_is_named_as_people_write_it():
    # explanations say "Exxon Mobil", not the holding company's SEC title
    assert display_name("XOM", "ExxonMobil Holdings Corp") == "Exxon Mobil"
