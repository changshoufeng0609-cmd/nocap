"""The snapshot's compact facts layout (scripts/build_snapshot.compact_facts) reads back exactly what it stored."""
import sqlite3
from datetime import date, datetime, timezone

import pytest

from receipts import api
from receipts.ingest import build_company
from receipts.store import METRIC_CONCEPTS, Store, connect_rw, replace_company
from scripts.build_snapshot import FACTS_COMPACT, FACTS_JOINED, copy_sqlite, sqlite_digest

Q1, Q2, Q3 = ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"), ("2025-07-01", "2025-09-30")
H1, M9, FY = ("2025-01-01", "2025-06-30"), ("2025-01-01", "2025-09-30"), ("2025-01-01", "2025-12-31")


def company(cik: int, ticker: str, scale: float):
    """A calendar-year company with three 10-Qs and a 10-K: quarter, year-to-date, annual, instant and derived rows."""
    filings = {k: (f"{cik:010d}-{yy}-{n:06d}", form, filed, f"{filed}T20:00:00.000Z", report, 2025, fp)
               for k, yy, n, form, filed, report, fp in (
                   ("A1", 25, 1, "10-Q", "2025-04-30", "2025-03-31", "Q1"),
                   ("A2", 25, 2, "10-Q", "2025-07-30", "2025-06-30", "Q2"),
                   ("A3", 25, 3, "10-Q", "2025-10-30", "2025-09-30", "Q3"),
                   ("AK", 26, 1, "10-K", "2026-02-20", "2025-12-31", "FY"))}
    usgaap: dict = {}
    for concept, period, value, key, unit in (
            ("Revenues", Q1, 100e6, "A1", "USD"), ("Revenues", Q2, 110e6, "A2", "USD"),
            ("Revenues", H1, 210e6, "A2", "USD"), ("Revenues", Q3, 120e6, "A3", "USD"),
            ("Revenues", M9, 330e6, "A3", "USD"), ("Revenues", FY, 460e6, "AK", "USD"),
            ("NetIncomeLoss", Q1, 10e6, "A1", "USD"), ("NetIncomeLoss", Q2, 11e6, "A2", "USD"),
            ("NetIncomeLoss", Q3, 12e6, "A3", "USD"), ("NetIncomeLoss", FY, 46e6, "AK", "USD"),
            ("EarningsPerShareDiluted", Q1, 1.00, "A1", "USD/shares"),
            ("EarningsPerShareDiluted", Q2, 1.10, "A2", "USD/shares"),
            ("StockRepurchaseProgramRemainingAuthorizedRepurchaseAmount1", FY[1], 5e9, "AK", "USD")):
        a, form, filed, _, _, fy, fp = filings[key]
        start, end = period if isinstance(period, tuple) else (None, period)
        e = {"end": end, "val": value * (scale if unit == "USD" else 1), "accn": a, "fy": fy, "fp": fp, "form": form,
             "filed": filed} | ({"start": start} if start else {})
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(e)
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in filings.values()]
    return build_company(cik=cik, ticker=ticker, names=[f"{ticker} Corp"], fiscal_year_end="1231", filing_rows=rows,
                         companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2020, 1, 1),
                         today=date(2026, 10, 27))


@pytest.fixture
def source(tmp_path):
    db = tmp_path / "data" / "facts.sqlite"
    conn = connect_rw(db)
    for cik, ticker, scale in ((901, "AAA", 1.0), (902, "BBB", 2.5)):
        b = company(cik, ticker, scale)
        replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    return db


def snapshot_of(source, tmp_path):
    snap = tmp_path / "snapshot"
    snap.mkdir(exist_ok=True)
    copy_sqlite(source, snap / "facts.sqlite")
    return snap


def query(db, sql, params=()):
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_compaction_round_trip(source, tmp_path):
    db = snapshot_of(source, tmp_path) / "facts.sqlite"
    objects = dict(query(db, "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')"))
    assert objects["facts"] == "view" and objects[FACTS_COMPACT] == "table"
    assert not {r[1] for r in query(db, f"PRAGMA table_info({FACTS_COMPACT})")} & set(FACTS_JOINED)
    columns = [(r[1], r[2]) for r in query(source, "PRAGMA table_info(facts)")]
    assert [(r[1], r[2]) for r in query(db, "PRAGMA table_info(facts)")] == columns  # same names, order and types
    kinds = {r[0] for r in query(source, "SELECT DISTINCT period_kind FROM facts")}
    assert {"quarter", "annual", "ytd", "instant"} <= kinds  # rows Store never serves are kept as well
    assert {r[0] for r in query(source, "SELECT DISTINCT source FROM facts")} == {"xbrl", "derived"}
    assert query(db, "SELECT * FROM facts ORDER BY id") == query(source, "SELECT * FROM facts ORDER BY id")
    assert sqlite_digest(db) == sqlite_digest(source)  # the manifest's digest and counts do not see the layout
    plan = " ".join(r[3] for r in query(db, "EXPLAIN QUERY PLAN SELECT * FROM facts WHERE cik = ? AND metric = ?",
                                        (901, "revenue")))
    assert "USING INDEX ix_facts_lookup" in plan


def test_store_and_the_api_count_read_the_view(source, tmp_path):
    snap = snapshot_of(source, tmp_path)
    before, after = Store(source, tmp_path / "pr_facts"), Store(snap / "facts.sqlite", tmp_path / "pr_facts")
    try:
        assert after.companies_list() == before.companies_list()
        for cik in (901, 902):
            for metric in METRIC_CONCEPTS:
                for kw in ({}, {"as_of": datetime(2025, 8, 1, tzinfo=timezone.utc)},
                           {"period_end": date(2025, 12, 31)}):
                    got, want = after.facts(cik, metric, **kw), before.facts(cik, metric, **kw)
                    assert [f.model_dump() for f in got] == [f.model_dump() for f in want]
        assert len(after.facts(902, "revenue")) == 5 and after.facts(902, "revenue")[0].url.endswith("/doc.htm")
    finally:
        before.close()
        after.close()
    assert api.data_info(snap)["facts"] == query(source, "SELECT COUNT(*) FROM facts")[0][0]


@pytest.mark.parametrize("change", [
    "UPDATE facts SET url = 'https://www.sec.gov/Archives/edgar/data/901/other.htm' WHERE id = (SELECT MIN(id) FROM facts)",
    "UPDATE facts SET ticker = 'ZZZ' WHERE id = (SELECT MAX(id) FROM facts)",
    "UPDATE facts SET accepted_at = '2026-01-01T00:00:00Z' WHERE id = (SELECT MIN(id) FROM facts WHERE source = 'derived')",
    "DELETE FROM filings WHERE accession = (SELECT accession FROM facts ORDER BY id LIMIT 1)",
])
def test_the_build_fails_when_the_view_would_not_reproduce_a_row(source, tmp_path, change):
    con = sqlite3.connect(source)
    with con:
        assert con.execute(change).rowcount == 1
    con.close()
    with pytest.raises(SystemExit, match="compact facts view does not reproduce"):
        snapshot_of(source, tmp_path)
