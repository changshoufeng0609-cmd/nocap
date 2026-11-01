"""Balance-sheet figures (receipts.balance_sheet): built from archived companyfacts, read from a claim's own words, and
checked at a balance-sheet date. Synthetic companies go through the real path: a gzip archive, add_to_db on an evidence
database, receipts.store.Store and receipts.verify.verify. No network, no model."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from receipts import api, balance_sheet as BS
from receipts import verify as V
from receipts.store import Store, connect_rw, replace_company
from receipts.types import Claim
from scripts.build_snapshot import compact_facts, copy_sqlite

UTC = timezone.utc
TEST, FALL = 999, 998
# key -> (accession, form, filed, accepted, report date, items)
FILINGS = {
    TEST: {
        "K24": ("0000000999-25-000001", "10-K", "2025-02-20", "2025-02-20T21:00:00Z", "2024-12-31", ""),
        "Q1": ("0000000999-25-000002", "10-Q", "2025-04-30", "2025-04-30T20:00:00Z", "2025-03-31", ""),
        "Q2": ("0000000999-25-000003", "10-Q", "2025-07-30", "2025-07-30T20:00:00Z", "2025-06-30", ""),
        "Q3": ("0000000999-25-000004", "10-Q", "2025-10-30", "2025-10-30T20:00:00Z", "2025-09-30", ""),
        "K25": ("0000000999-26-000001", "10-K", "2026-02-20", "2026-02-20T21:00:00Z", "2025-12-31", ""),
        "E3": ("0000000999-25-000010", "8-K", "2025-10-29", "2025-10-29T20:05:00Z", "2025-10-29", "2.02,9.01"),
        "X8": ("0000000999-25-000011", "8-K", "2025-11-15", "2025-11-15T12:00:00Z", "2025-11-15", "8.01"),
    },
    FALL: {
        "K24": ("0000000998-25-000001", "10-K", "2025-02-25", "2025-02-25T21:00:00Z", "2024-12-31", ""),
        "Q3": ("0000000998-25-000004", "10-Q", "2025-10-31", "2025-10-31T20:00:00Z", "2025-09-30", ""),
    },
}
PERIODS = {
    TEST: [(2024, "Q4", "2024-10-01", "2024-12-31"), (2024, "FY", "2024-01-01", "2024-12-31"),
           (2025, "Q1", "2025-01-01", "2025-03-31"), (2025, "Q2", "2025-04-01", "2025-06-30"),
           (2025, "Q3", "2025-07-01", "2025-09-30"), (2025, "Q4", "2025-10-01", "2025-12-31"),
           (2025, "FY", "2025-01-01", "2025-12-31")],
    FALL: [(2024, "FY", "2024-01-01", "2024-12-31"), (2025, "Q3", "2025-07-01", "2025-09-30")],
}
B = 1e9
# (concept, date, value, filing key[, namespace, unit])
TEST_FACTS = [
    ("CashAndCashEquivalentsAtCarryingValue", "2023-12-31", 8.8 * B, "K24"),      # before facts_start
    ("CashAndCashEquivalentsAtCarryingValue", "2024-12-31", 10.123 * B, "K24"),
    ("CashAndCashEquivalentsAtCarryingValue", "2024-12-31", 10.1 * B, "Q1"),      # the same figure, written coarsely
    ("CashAndCashEquivalentsAtCarryingValue", "2024-12-31", 10.123 * B, "Q2"),    # a comparative repeating it
    ("CashAndCashEquivalentsAtCarryingValue", "2024-12-31", 10.5 * B, "K25"),     # a restatement
    ("CashAndCashEquivalentsAtCarryingValue", "2025-03-31", 11.0 * B, "Q1"),
    ("CashAndCashEquivalentsAtCarryingValue", "2025-06-30", 12.0 * B, "Q2"),
    ("CashAndCashEquivalentsAtCarryingValue", "2025-08-15", 99.0 * B, "Q3"),      # not a balance-sheet date
    ("CashAndCashEquivalentsAtCarryingValue", "2025-09-30", 13.0 * B, "Q3"),
    ("CashAndCashEquivalentsAtCarryingValue", "2025-09-30", 13.7 * B, "X8"),      # an 8-K's recast: no 10-Q or 10-K
    ("CashAndCashEquivalentsAtCarryingValue", "2025-12-31", 14.25 * B, "K25"),
    ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-09-30", 13.4 * B, "Q3"),  # never used here
    ("Assets", "2024-12-31", 100.0 * B, "K24"), ("Assets", "2025-03-31", 101.0 * B, "Q1"),
    ("Assets", "2025-06-30", 102.0 * B, "Q2"), ("Assets", "2025-09-30", 103.456 * B, "Q3"),
    ("Assets", "2025-12-31", 104.0 * B, "K25"),
    ("LiabilitiesAndStockholdersEquity", "2025-06-30", 102.9 * B, "Q2"),          # contradicts its total assets
    ("LiabilitiesAndStockholdersEquity", "2025-09-30", 103.456 * B, "Q3"),
    ("Liabilities", "2024-12-31", -5.0 * B, "K24"),                                # negative: a tagging slip
    ("Liabilities", "2025-09-30", 60.0 * B, "Q3"),
    ("StockholdersEquity", "2025-09-30", 43.456 * B, "Q3"),
    ("LongTermDebt", "2025-12-31", 20.5 * B, "K25"),
    ("LongTermDebtNoncurrent", "2025-12-31", 18.0 * B, "K25"),
    ("LongTermDebtNoncurrent", "2025-09-30", 17.0 * B, "Q3"),                     # two values for one date
    ("LongTermDebtNoncurrent", "2025-09-30", 17.5 * B, "Q3"),
    ("DebtLongtermAndShorttermCombinedAmount", "2025-12-31", 22.0 * B, "K25"),
    ("CommonStockSharesOutstanding", "2025-09-30", 1_234_567_000, "Q3", "us-gaap", "shares"),
    ("CommonStockSharesOutstanding", "2025-12-31", 1_230_000_000, "K25", "us-gaap", "shares"),
    ("EntityCommonStockSharesOutstanding", "2025-10-24", 1_233_000_000, "Q3", "dei", "shares"),
    ("EntityCommonStockSharesOutstanding", "2026-02-13", 1_229_500_000, "K25", "dei", "shares"),
]
FALL_FACTS = [
    ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2025-09-30", 5.5 * B, "Q3"),
    ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2024-12-31", 4.0 * B, "K24"),
    ("Assets", "2025-09-30", 30.0 * B, "Q3"),
    ("StockholdersEquity", "2025-09-30", -2.0 * B, "Q3"),
]


def companyfacts(cik: int, rows) -> dict:
    tagged: dict = {}
    for concept, end, value, key, *rest in rows:
        ns, unit = rest if rest else ("us-gaap", "USD")
        accn, form, filed, *_ = FILINGS[cik][key]
        tagged.setdefault(ns, {}).setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(
            {"end": end, "val": value, "accn": accn, "form": form, "filed": filed})
    return {"cik": cik, "facts": tagged}


def write_db(path) -> None:
    conn = connect_rw(path)
    for cik, ticker, name in ((TEST, "TEST", "Testco Inc."), (FALL, "FALL", "Fallco Inc.")):
        filings = [{"accession": a, "form": form, "filed": filed, "accepted_at": at, "report_date": report,
                    "items": items, "primary_doc": "doc.htm", "url": f"https://www.sec.gov/Archives/{a}.htm"}
                   for a, form, filed, at, report, items in FILINGS[cik].values()]
        periods_ = [(fy, fp, date.fromisoformat(s), date.fromisoformat(e), "src") for fy, fp, s, e in PERIODS[cik]]
        company = {"cik": cik, "ticker": ticker, "name": name, "aliases": {ticker.lower(), name.split()[0].lower()},
                   "fiscal_year_end": "1231"}
        replace_company(conn, company, filings, periods_, [])
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('facts_start', '2024-06-30')")
    conn.close()


def write_archive(folder, *, corrupt: bool = False) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    index = {}
    for cik, rows in ((TEST, TEST_FACTS), (FALL, FALL_FACTS)):
        raw = json.dumps(companyfacts(cik, rows)).encode()
        (folder / f"CIK{cik:010d}.json.gz").write_bytes(gzip.compress(raw))
        index[str(cik)] = {"sha256": hashlib.sha256(raw + (b" " if corrupt else b"")).hexdigest()}
    (folder / "index.json").write_text(json.dumps(index))


@pytest.fixture
def built(tmp_path):
    db = tmp_path / "data" / "facts.sqlite"
    write_db(db)
    write_archive(tmp_path / "archive")
    con = sqlite3.connect(db)
    summary = BS.add_to_db(con, tmp_path / "archive")
    con.close()
    return db, summary


@pytest.fixture
def store(built):
    s = Store(built[0], built[0].parent / "pr_facts")
    yield s
    s.close()


def claim(span: str, value_text: str, value: float, *, metric_text: str, period_text: str = "third quarter of 2025",
          unit: str = "USD", scale: str = "billions", entity: str = "Testco", metric: str = "other",
          basis=None) -> Claim:
    return Claim(id="c1", metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                 scale=scale, period_text=period_text, period_type="instant", comparison="none", basis=basis,
                 forward_looking=False, entity=entity, segment=None, span_text=span, span_start=0, span_end=len(span))


def cash(value_text: str, value: float, period_text: str = "third quarter of 2025", **kw) -> Claim:
    return claim(f"Testco ended the period with {value_text} in cash and cash equivalents", value_text, value,
                 metric_text="cash and cash equivalents", period_text=period_text, **kw)


def check(c: Claim, store, **kw):
    return V.verify(c, store, context=kw.pop("context", "Testco, third quarter of 2025"), **kw)


# --------------------------------------------------------------------------- build
def test_build_stores_each_value_once_at_balance_sheet_dates(store):
    facts = sorted(store.facts(TEST, "cash_and_equivalents"), key=lambda f: (f.period_end, f.accepted_at))
    assert [(f.period_end.isoformat(), f.value, f.form) for f in facts] == [
        ("2024-12-31", 10.123 * B, "10-K"), ("2024-12-31", 10.5 * B, "10-K"),  # original and restatement only
        ("2025-03-31", 11.0 * B, "10-Q"), ("2025-06-30", 12.0 * B, "10-Q"), ("2025-09-30", 13.0 * B, "10-Q"),
        ("2025-12-31", 14.25 * B, "10-K")]
    assert all(f.period_start is None and f.concept == "us-gaap:CashAndCashEquivalentsAtCarryingValue"
               and f.source == "xbrl" and f.basis == "GAAP" and f.decimals is not None for f in facts)
    # a year end takes its FY label; quarters take theirs
    labels = {f.period_end.isoformat(): (f.fiscal_year, f.fiscal_period) for f in facts}
    assert labels["2025-12-31"] == (2025, "FY") and labels["2025-09-30"] == (2025, "Q3")


def test_build_leaves_out_what_does_not_hold_up(built, store):
    _, summary = built
    why = sorted(d["why"].split(":")[0][:30] for d in summary["audit"]["TEST"]["dropped"])
    assert len(why) == 3, why  # two values for one date, a negative liability, assets its own total contradicts
    assert store.facts(TEST, "total_assets", period_end=date(2025, 6, 30)) == []
    assert [f.value for f in store.facts(TEST, "total_liabilities")] == [60.0 * B]
    assert store.facts(TEST, "long_term_debt_noncurrent", period_end=date(2025, 9, 30)) == []
    # cash, cash equivalents and restricted cash only stands in at a company that tags no cash and cash equivalents
    assert {f.concept for f in store.facts(TEST, "cash_and_equivalents")} == {BS.CCE.name}
    assert {f.concept for f in store.facts(FALL, "cash_and_equivalents")} == {BS.CCE_RESTRICTED.name}
    assert summary["audit"]["FALL"]["fallback"] and not summary["audit"]["TEST"]["fallback"]


def test_cover_page_share_count_keeps_its_own_date(store):
    shares = store.facts(TEST, "shares_outstanding")
    cover = [f for f in shares if f.concept == BS.SHARES_COVER.name]
    assert [(f.period_end.isoformat(), f.value, f.fiscal_period) for f in cover] == [
        ("2025-10-24", 1_233_000_000, None), ("2026-02-13", 1_229_500_000, None)]
    assert all(f.unit == "shares" for f in shares)


def test_add_to_db_is_idempotent_and_writes_a_compacted_snapshot(built, tmp_path):
    db, first = built
    con = sqlite3.connect(db)
    rows = con.execute("SELECT * FROM facts ORDER BY id").fetchall()
    again = BS.add_to_db(con, tmp_path / "archive")
    assert again["replaced"] == first["facts"] and con.execute("SELECT * FROM facts ORDER BY id").fetchall() == rows
    compact_facts(con)  # the snapshot layout: facts_compact behind a view named facts
    con.commit()
    BS.add_to_db(con, tmp_path / "archive")
    assert con.execute("SELECT * FROM facts ORDER BY id").fetchall() == rows
    meta = con.execute("SELECT value FROM meta WHERE key = 'balance_sheet'").fetchone()[0]
    assert json.loads(meta)["facts"] == len(rows)
    con.close()


def test_add_to_db_refuses_an_archive_its_index_does_not_describe(tmp_path):
    db = tmp_path / "facts.sqlite"
    write_db(db)
    write_archive(tmp_path / "archive", corrupt=True)
    con = sqlite3.connect(db)
    with pytest.raises(SystemExit, match="sha256"):
        BS.add_to_db(con, tmp_path / "archive")
    con.close()


def test_snapshot_build_adds_the_facts_and_health_counts_them(tmp_path):
    db = tmp_path / "data" / "facts.sqlite"
    write_db(db)
    write_archive(tmp_path / "archive")
    snap = tmp_path / "snapshot"
    snap.mkdir()
    summary = copy_sqlite(db, snap / "facts.sqlite", balance_sheet_archive=tmp_path / "archive")
    assert summary["facts"] > 0 and api.data_info(snap)["facts"] == summary["facts"]
    s = Store(snap / "facts.sqlite", snap / "pr_facts")
    assert [f.value for f in s.facts(TEST, "total_assets", period_end=date(2025, 9, 30))] == [103.456 * B]
    s.close()
    assert copy_sqlite(db, tmp_path / "plain.sqlite") is None  # without an archive, nothing is added


# --------------------------------------------------------------------------- wording
@pytest.mark.parametrize("span,metric_text,key", [
    ("Testco ended the quarter with $13.0 billion in cash and cash equivalents", "cash and cash equivalents",
     "cash_and_equivalents"),
    ("cash and equivalents of $13.0 billion", "cash and equivalents", "cash_and_equivalents"),
    ("Cash & cash equivalents were $13.0 billion", "Cash & cash equivalents", "cash_and_equivalents"),
    ("Testco had $13.0 billion in cash", "cash", "cash"),
    ("a cash pile of $13.0 billion", "cash pile", "cash"),
    ("Total assets stood at $13.0 billion", "total assets", "total_assets"),
    ("total liabilities of $13.0 billion", "total liabilities", "total_liabilities"),
    ("Shareholders' equity was $13.0 billion", "shareholders' equity", "stockholders_equity"),
    ("total stockholders’ equity of $13.0 billion", "total stockholders' equity", "stockholders_equity"),
    ("long-term debt of $13.0 billion", "long-term debt", "long_term_debt"),
    ("long term debt rose to $13.0 billion", "long term debt", "long_term_debt"),
    ("total debt was $13.0 billion", "total debt", "total_debt"),
    ("Testco had $13.0 billion of debt", "debt", "total_debt"),
    ("Testco had 13.0 billion shares outstanding", "shares outstanding", "shares_outstanding"),
    ("13.0 billion shares of common stock outstanding", "shares of common stock outstanding", "shares_outstanding"),
    ("Total assets were $13.0 billion at the end of the third quarter", "total assets", "total_assets"),
])
def test_reads_balance_sheet_wording(span, metric_text, key):
    w = BS.read_wording(claim(span, "13.0 billion" if "shares" in span else "$13.0 billion", 13.0,
                              metric_text=metric_text, unit="shares" if "shares" in span else "USD"))
    assert w is not None and w.reading.key == key


@pytest.mark.parametrize("span,metric_text", [
    ("Free cash flow was $13.0 billion", "free cash flow"),
    ("cash flow from operations was $13.0 billion", "cash flow from operations"),
    ("Testco returned $13.0 billion in cash to shareholders", "cash returned to shareholders"),
    ("Testco generated $13.0 billion in cash", "cash"),
    ("net cash of $13.0 billion", "net cash"),
    ("net debt of $13.0 billion", "net debt"),
    ("cash and marketable securities of $13.0 billion", "cash and marketable securities"),
    ("Cash, cash equivalents and marketable securities totaled $13.0 billion",
     "cash, cash equivalents and marketable securities"),
    ("cash and cash equivalents and short-term investments of $13.0 billion",
     "cash and cash equivalents and short-term investments"),
    ("restricted cash and cash equivalents of $13.0 billion", "restricted cash and cash equivalents"),
    ("total current assets of $13.0 billion", "total current assets"),
    ("total assets under management of $13.0 billion", "total assets under management"),
    ("average total assets of $13.0 billion", "average total assets"),
    ("the current portion of long-term debt was $13.0 billion", "current portion of long-term debt"),
    ("total liabilities and shareholders' equity of $13.0 billion", "total liabilities and shareholders' equity"),
    ("Cash and cash equivalents increased $13.0 billion", "cash and cash equivalents"),
    ("an increase in cash of $13.0 billion", "increase in cash"),
    ("Total assets rose to $14.0 billion from $13.0 billion", "total assets"),
    ("cash per share of $13.0", "cash per share"),
    ("debt repayments of $13.0 billion", "debt repayments"),
    ("Cash and cash equivalents held overseas were $13.0 billion", "cash and cash equivalents held overseas"),
    ("Cash and cash equivalents were $13.0 billion, including restricted cash", "cash and cash equivalents"),
    ("Cash and cash equivalents were $13.0 billion at September 30, 2025, most of it held abroad",
     "cash and cash equivalents"),
    ("Cash and cash equivalents were $13.0 billion (excluding restricted cash)", "cash and cash equivalents"),
    ("Google parent Alphabet's cash and cash equivalents of $13.0 billion", "cash and cash equivalents"),
])
def test_other_wording_is_not_a_balance(span, metric_text):
    assert BS.read_wording(claim(span, "$13.0 billion" if "13.0 billion" in span else "$13.0", 13.0,
                                 metric_text=metric_text)) is None


@pytest.mark.parametrize("span,metric_text", [
    ("diluted shares outstanding of 13.0 billion", "diluted shares outstanding"),
    ("weighted-average shares outstanding of 13.0 billion", "weighted-average shares outstanding"),
])
def test_share_counts_that_are_averages_are_not_balances(span, metric_text):
    assert BS.read_wording(claim(span, "13.0 billion", 13.0, metric_text=metric_text, unit="shares")) is None


# --------------------------------------------------------------------------- dates
CAL = [V.periods.CalEntry(fy, fp, date.fromisoformat(s), date.fromisoformat(e)) for fy, fp, s, e in PERIODS[TEST]]


@pytest.mark.parametrize("period_text,context,expected", [
    ("as of September 30, 2025", None, date(2025, 9, 30)),
    ("December 31, 2025", None, date(2025, 12, 31)),            # a fourth quarter and its year end together
    ("third quarter of 2025", None, date(2025, 9, 30)),
    ("end of the quarter", "Testco, third quarter of 2025", date(2025, 9, 30)),
    ("", "Testco, third quarter of 2025", date(2025, 9, 30)),
    ("year-end", "Testco, fourth quarter of 2025", date(2025, 12, 31)),
    ("year-end", "Testco, third quarter of 2025", None),       # last year's end or this year's?
    ("end of the prior fiscal year", "Testco, third quarter of 2025", date(2024, 12, 31)),
    ("year-end 2024", None, date(2024, 12, 31)),
    ("a year ago", "Testco, fourth quarter of 2025", date(2024, 12, 31)),
    ("the prior year", "Testco, fourth quarter of 2025", None),  # its end, or the same quarter a year earlier?
    ("beginning of the year", "Testco, third quarter of 2025", None),
    ("first half of 2025", None, None),
    ("last quarter", "Testco, third quarter of 2025", None),
    ("September 30, 2025 and December 31, 2024", None, None),
])
def test_balance_sheet_date(period_text, context, expected):
    bd = BS.balance_date(claim("x $1 billion", "$1 billion", 1.0, metric_text="total assets", period_text=period_text),
                         CAL, context=context, as_of=None)
    assert (bd.date if bd else None) == expected


def test_year_end_with_a_bare_year_is_unclear_off_a_calendar_fiscal_year():
    cal = [V.periods.CalEntry(2025, "Q4", date(2025, 6, 29), date(2025, 9, 27)),
           V.periods.CalEntry(2025, "FY", date(2024, 9, 29), date(2025, 9, 27))]
    c = claim("x $1 billion", "$1 billion", 1.0, metric_text="total assets", period_text="year-end 2025")
    assert BS.balance_date(c, cal, context=None, as_of=None) is None
    c = c.model_copy(update={"period_text": "fiscal 2025 year-end"})
    assert BS.balance_date(c, cal, context=None, as_of=None).date == date(2025, 9, 27)


# --------------------------------------------------------------------------- verdicts
def test_true_cash_verifies_with_the_balance_sheet_date(store):
    v = check(cash("$13.0 billion", 13.0), store)
    assert (v.status, v.reason) == ("verified", "match")
    assert v.explanation == "Matches Testco's 10-Q: cash and cash equivalents were $13 billion at September 30, 2025."
    assert [f.accession for f in v.evidence] == [FILINGS[TEST]["Q3"][0]]


def test_wrong_cash_is_wrong_with_the_filed_figure(store):
    v = check(cash("$15.5 billion", 15.5), store)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", 13.0)
    assert v.explanation == ("Testco's 10-Q reports that cash and cash equivalents were $13 billion at September 30, "
                             "2025, not $15.5 billion.")


def test_another_dates_balance_is_a_period_mismatch(store):
    v = check(cash("$12.0 billion", 12.0), store)
    assert (v.status, v.reason) == ("wrong", "period_mismatch")
    assert v.explanation == ("$12.0 billion is Testco's cash and cash equivalents at June 30, 2025; at September 30, "
                             "2025 it was $13 billion.")


def test_scale_slip_and_bounds_and_approximations(store):
    v = check(cash("$13.0 million", 13.0, scale="millions"), store)
    assert (v.status, v.reason) == ("wrong", "scale_mismatch")
    v = check(claim("cash and cash equivalents were more than $12.5 billion", "$12.5 billion", 12.5,
                    metric_text="cash and cash equivalents"), store)
    assert v.status == "verified" and v.explanation.endswith(", which is more than $12.5 billion.")
    v = check(claim("cash and cash equivalents were more than $13.5 billion", "$13.5 billion", 13.5,
                    metric_text="cash and cash equivalents"), store)
    assert (v.status, v.reason) == ("wrong", "value_mismatch")
    assert "which is not more than $13.5 billion" in v.explanation
    v = check(claim("cash and cash equivalents of about $13.1 billion", "$13.1 billion", 13.1,
                    metric_text="cash and cash equivalents"), store)
    assert (v.status, v.reason) == ("approximate", "approximate")


def test_restated_balance_matches_either_version_and_is_corrected_with_the_latest(store):
    for text, value in (("$10.12 billion", 10.12), ("$10.5 billion", 10.5)):
        assert check(cash(text, value, "December 31, 2024"), store).status == "verified"
    v = check(cash("$11.9 billion", 11.9, "December 31, 2024"), store)
    assert (v.status, v.expected_value) == ("wrong", 10.5)


def test_total_assets_liabilities_and_equity(store):
    v = check(claim("Total assets were $103.46 billion", "$103.46 billion", 103.46, metric_text="total assets"), store)
    assert v.explanation == "Matches Testco's 10-Q: total assets were $103.456 billion at September 30, 2025."
    v = check(claim("total liabilities of $66.0 billion", "$66.0 billion", 66.0, metric_text="total liabilities"),
              store)
    assert (v.status, v.expected_value) == ("wrong", 60.0)
    v = check(claim("stockholders' equity was $43.46 billion", "$43.46 billion", 43.46,
                    metric_text="stockholders' equity"), store)
    assert v.explanation == "Matches Testco's 10-Q: stockholders' equity was $43.456 billion at September 30, 2025."
    v = check(claim("Total assets were $102.0 billion", "$102.0 billion", 102.0, metric_text="total assets",
                    period_text="second quarter of 2025"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")  # the filing's own totals disagreed: not stored


def test_a_bare_cash_figure_is_never_wrong(store):
    v = check(claim("Testco had $13.0 billion in cash", "$13.0 billion", 13.0, metric_text="cash"), store)
    assert v.status == "verified"
    v = check(claim("Testco had $20.0 billion in cash", "$20.0 billion", 20.0, metric_text="cash"), store)
    assert (v.status, v.reason, v.expected_value) == ("unverifiable", "no_evidence", None)
    assert "can also mean cash with marketable securities" in v.explanation and v.evidence


def test_debt_verifies_against_what_the_company_tags_and_is_never_wrong(store):
    year_end = "December 31, 2025"
    for text, value, measure in (("$18.0 billion", 18.0, "excluding"), ("$20.5 billion", 20.5, "including")):
        v = check(claim(f"long-term debt of {text}", text, value, metric_text="long-term debt", period_text=year_end),
                  store)
        assert v.status == "verified" and f"long-term debt {measure} its current portion" in v.explanation
    v = check(claim("long-term debt of $25.0 billion", "$25.0 billion", 25.0, metric_text="long-term debt",
                    period_text=year_end), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation.startswith("Testco's 10-K reports long-term debt excluding its current portion of $18 billion "
                                    "and long-term debt including its current portion of $20.5 billion at December 31")
    v = check(claim("total debt was $22.0 billion", "$22.0 billion", 22.0, metric_text="total debt",
                    period_text=year_end), store)
    assert v.explanation == ("Matches Testco's 10-K: total short- and long-term debt was $22 billion at December 31, "
                             "2025.")
    v = check(claim("total debt was $22.0 billion", "$22.0 billion", 22.0, metric_text="total debt"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "doesn't add up debt components" in v.explanation


def test_share_counts_name_their_date(store):
    def shares(span, text, value, period_text="third quarter of 2025", scale="billions"):
        return check(claim(span, text, value, metric_text="shares outstanding", unit="shares", scale=scale,
                           period_text=period_text), store)
    v = shares("Testco had 1.23 billion shares outstanding", "1.23 billion", 1.23)
    assert v.explanation == ("Matches Testco's 10-Q: 1,234,567,000 shares of common stock were outstanding at "
                             "September 30, 2025.")
    v = shares("Testco had 1.233 billion shares outstanding", "1.233 billion", 1.233)
    assert v.explanation == ("Matches Testco's 10-Q: its cover page reports 1,233,000,000 shares of common stock "
                             "outstanding as of October 24, 2025.")
    v = shares("1,233,000,000 shares outstanding", "1,233,000,000", 1_233_000_000, "as of October 24, 2025", "units")
    assert v.status == "verified"
    v = shares("Testco had 1.5 billion shares outstanding", "1.5 billion", 1.5)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "changes from day to day" in v.explanation
    # a claim that names the balance-sheet date gets the count at that date only, never the later cover-page count
    v = shares("Testco had 1.233 billion shares outstanding", "1.233 billion", 1.233, "end of the third quarter of 2025")
    assert v.status == "unverifiable" and "cover page" not in v.explanation
    v = shares("Testco ended the quarter with 1.233 billion shares outstanding", "1.233 billion", 1.233)
    assert v.status == "unverifiable" and "cover page" not in v.explanation


def test_a_negative_figure_of_an_item_that_never_is_stays_unread(store):
    v = check(cash("($13.0 billion)", -13.0), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "doesn't track" in v.explanation


def test_as_of_before_the_release_is_not_public_yet_and_after_it_unverifiable(store):
    c = cash("$13.0 billion", 13.0)
    v = check(c, store, as_of="2025-10-29T19:00:00Z")
    assert (v.status, v.reason) == ("wrong", "look_ahead")
    assert "wasn't public until its 8-K earnings release (0000000999-25-000010)" in v.explanation
    v = check(c, store, as_of="2025-10-30T00:00:00Z")  # the release was out; the 10-Q came later
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert check(c, store, as_of="2025-11-01T00:00:00Z").status == "verified"


def test_a_date_that_is_no_balance_sheet_is_unverifiable(store):
    v = check(cash("$13.0 billion", 13.0, "as of October 15, 2025"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert v.explanation == ("No Testco balance sheet NoCap holds is dated October 15, 2025, so it can't check "
                             "$13.0 billion at that date.")
    v = check(cash("$13.0 billion", 13.0, "year-end"), store)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")


def test_the_sentences_own_period_words_decide_the_date(store):
    # "a year ago" in the quote, period filled in from the context: the year-ago balance is the one checked
    c = claim("cash and cash equivalents were $10.5 billion a year ago", "$10.5 billion", 10.5,
              metric_text="cash and cash equivalents", period_text="fourth quarter of 2025")
    assert check(c, store, context="Testco, fourth quarter of 2025").status == "unverifiable"  # named Q4: they disagree
    c = c.model_copy(update={"period_text": ""})
    v = check(c, store, context="Testco, fourth quarter of 2025")
    assert v.status == "verified" and "at December 31, 2024" in v.explanation
    c = claim("cash and cash equivalents at the end of the prior fiscal year were $10.5 billion", "$10.5 billion", 10.5,
              metric_text="cash and cash equivalents", period_text="end of the prior fiscal year")
    assert check(c, store).status == "verified"


def test_respectively_never_makes_a_figure_wrong(store):
    c = claim("Cash and cash equivalents were $13.5 billion and $10.5 billion at September 30, 2025 and December 31, "
              "2024, respectively", "$13.5 billion", 13.5, metric_text="cash and cash equivalents",
              period_text="September 30, 2025")
    assert (check(c, store).status, check(c, store).reason) == ("unverifiable", "no_evidence")


def test_cash_tagged_only_with_restricted_cash_is_unverifiable(store):
    ctx = "Fallco, third quarter of 2025"
    c = claim("Fallco ended the quarter with $5.5 billion in cash and cash equivalents", "$5.5 billion", 5.5,
              metric_text="cash and cash equivalents", entity="Fallco")
    v = check(c, store, context=ctx)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "includes restricted cash" in v.explanation
    v = check(c.model_copy(update={"value": 6.0, "value_text": "$6.0 billion",
                                   "span_text": c.span_text.replace("$5.5", "$6.0")}), store, context=ctx)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "includes restricted cash" in v.explanation


def test_negative_equity(store):
    ctx = "Fallco, third quarter of 2025"
    v = check(claim("Fallco's shareholders' equity was negative $2.0 billion", "$2.0 billion", 2.0,
                    metric_text="shareholders' equity", entity="Fallco"), store, context=ctx)
    assert v.status == "verified"
    v = check(claim("Fallco's shareholders' equity was $2.0 billion", "$2.0 billion", 2.0,
                    metric_text="shareholders' equity", entity="Fallco"), store, context=ctx)
    assert (v.status, v.expected_value) == ("wrong", -2.0)


def test_figures_that_are_not_balances_keep_their_old_verdicts(store):
    v = check(claim("a favorable impact from tariff refunds of $0.11", "$0.11", 0.11, unit="USD/share", scale="units",
                    metric_text="favorable impact from tariff refunds", period_text="third quarter of 2025"), store)
    assert v.explanation == ("NoCap doesn't track this kind of figure (favorable impact from tariff refunds) yet, so "
                             "there is no filing fact to check.")
    for metric in ("free_cash_flow", "other"):
        c = claim("Free cash flow was $13.0 billion", "$13.0 billion", 13.0, metric_text="free cash flow",
                  metric=metric)
        assert BS.check(c, store, context="Testco, third quarter of 2025") is None
        assert "cash and cash equivalents" not in check(c, store).explanation


def test_an_unknown_company_is_unresolved(store):
    v = check(claim("Nobody Corp ended the period with $13.0 billion in cash and cash equivalents", "$13.0 billion",
                    13.0, metric_text="cash and cash equivalents", entity="Nobody Corp"), store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "couldn't match" in v.explanation

# --------------------------------------------------------------------------- review regressions (2026-11-01)
@pytest.mark.parametrize("value_text,value,prefix", [
    ("$5.5 billion", 5.5, ""),
    ("$5.51 billion", 5.51, "about "),
    ("$6.0 billion", 6.0, ""),
])
def test_restricted_inclusive_cash_does_not_establish_cash_alone(store, value_text, value, prefix):
    c = claim(f"Fallco had {prefix}{value_text} in cash and cash equivalents", value_text, value,
              metric_text="cash and cash equivalents", entity="Fallco")
    v = check(c, store, context="Fallco, third quarter of 2025")
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "restricted cash" in v.explanation and "alone" in v.explanation
    assert v.expected_value is None


@pytest.mark.parametrize("bound,expected", [
    ("less than", "wrong"), ("more than", "verified"),
    ("at most", "wrong"), ("at least", "verified"),
])
def test_negative_equity_bounds_use_signed_values(store, bound, expected):
    c = claim(f"Fallco's shareholders' equity was {bound} -$3 billion", "-$3 billion", -3,
              metric_text="shareholders' equity", entity="Fallco")
    v = check(c, store, context="Fallco, third quarter of 2025")
    assert v.status == expected
    assert (v.expected_value == -2) if expected == "wrong" else v.expected_value is None


def test_positive_number_deficit_wording_keeps_its_existing_unverifiable_result(store):
    c = claim("Fallco had a shareholders' deficit of more than $2 billion", "$2 billion", 2,
              metric_text="shareholders' deficit", entity="Fallco")
    assert check(c, store, context="Fallco, third quarter of 2025").status == "unverifiable"


@pytest.mark.parametrize("wording", ["at the end of the calendar year", "at calendar year-end", "calendar year-end 2025"])
@pytest.mark.parametrize("december_balance", [True, False])
def test_calendar_year_end_does_not_use_fiscal_year_end(wording, december_balance):
    cal = [V.periods.CalEntry(2025, "Q4", date(2025, 6, 29), date(2025, 9, 27)),
           V.periods.CalEntry(2025, "FY", date(2024, 9, 29), date(2025, 9, 27))]
    if december_balance:
        cal.append(V.periods.CalEntry(2026, "Q1", date(2025, 9, 28), date(2025, 12, 27)))
    c = claim("Total assets were $359.241 billion", "$359.241 billion", 359.241,
              metric_text="total assets", period_text=wording, entity="Apple")
    bd = BS.balance_date(c, cal, context="Apple fiscal Q4 2025", as_of=None)
    assert bd is not None
    assert bd.date == (date(2025, 12, 27) if december_balance else date(2025, 12, 31))


def test_calendar_year_end_gets_year_from_own_claim_before_context():
    c = claim("Total assets were $1 billion", "$1 billion", 1, metric_text="total assets",
              period_text="calendar year-end 2024")
    bd = BS.balance_date(c, CAL, context="Testco fiscal Q4 2025", as_of=None)
    assert bd.date == date(2024, 12, 31)


@pytest.mark.parametrize("tags,expected", [
    ([("RestrictedCashAndCashEquivalents", 0)], "verified"),
    ([("RestrictedCashAndCashEquivalents", 1e6)], "unverifiable"),
    ([("RestrictedCashAndCashEquivalentsAtCarryingValue", 0)], "unverifiable"),
    ([("RestrictedCashAndCashEquivalentsAtCarryingValue", 0),
      ("RestrictedCashAndCashEquivalentsNoncurrent", 0)], "verified"),
    ([("RestrictedCashAndCashEquivalents", 0),
      ("RestrictedCashAndCashEquivalentsNoncurrent", 1e6)], "unverifiable"),
    ([("RestrictedCashAndCashEquivalents", 0), ("RestrictedCashAndCashEquivalents", 1e6),
      ("RestrictedCashAndCashEquivalentsAtCarryingValue", 0),
      ("RestrictedCashAndCashEquivalentsNoncurrent", 0)], "unverifiable"),
])
def test_inclusive_cash_needs_complete_uncontradicted_zero_tags(store, monkeypatch, tags, expected):
    rows = FALL_FACTS + [(name, "2025-09-30", value, "Q3") for name, value in tags]
    filing_rows = {a: {"form": form, "accepted_at": at, "url": f"https://www.sec.gov/{a}", "report_date": report}
                   for a, form, filed, at, report, items in FILINGS[FALL].values()}
    facts, _audit = BS.company_facts(cik=FALL, ticker="FALL", companyfacts=companyfacts(FALL, rows),
                                     filings=filing_rows, calendar=PERIODS[FALL])
    original = store.facts
    monkeypatch.setattr(store, "facts", lambda cik, metric, **kw:
                        [f for f in facts if f.metric == metric] if cik == FALL else original(cik, metric, **kw))
    c = claim("Fallco had $5.5 billion in cash and cash equivalents", "$5.5 billion", 5.5,
              metric_text="cash and cash equivalents", entity="Fallco")
    v = check(c, store, context="Fallco, third quarter of 2025")
    assert v.status == expected
    if expected == "verified":
        assert "tags all restricted cash and cash equivalents as zero" in v.explanation
        assert {f.concept for f in v.evidence} == {BS.CCE_RESTRICTED.name} | {f"us-gaap:{n}" for n, _ in tags}
        assert all(f.accession == FILINGS[FALL]["Q3"][0] for f in v.evidence)
    else:
        assert "can't check cash and cash equivalents alone" in v.explanation
    # Zero support rows must not be mistaken for a zero cash balance (including bare "cash" wording).
    zero = claim("Fallco had $0.0 billion in cash", "$0.0 billion", 0,
                 metric_text="cash", entity="Fallco")
    assert check(zero, store, context="Fallco, third quarter of 2025").status == "unverifiable"


def test_restricted_cash_zero_in_later_filing_cannot_support_earlier_total(store, monkeypatch):
    original = store.facts
    total = original(FALL, "cash_and_equivalents")[-1]
    zero = total.model_copy(update={"concept": BS.RESTRICTED_TOTAL.name, "value": 0,
                                   "accession": "later", "accepted_at": datetime(2026, 1, 1, tzinfo=UTC)})
    monkeypatch.setattr(store, "facts", lambda cik, metric, **kw:
                        original(cik, metric, **kw) + ([zero] if cik == FALL and metric == "cash_and_equivalents" else []))
    c = claim("Fallco had $5.5 billion in cash and cash equivalents", "$5.5 billion", 5.5,
              metric_text="cash and cash equivalents", entity="Fallco")
    for as_of in (None, datetime(2025, 12, 1, tzinfo=UTC)):
        assert check(c, store, context="Fallco, third quarter of 2025", as_of=as_of).status == "unverifiable"


@pytest.mark.parametrize("context_year", [2025, 2026])
def test_calendar_words_use_claim_year_and_override_fiscal_quarter(store, context_year):
    c = claim("Testco's total assets were $104.0 billion at calendar year-end", "$104.0 billion", 104,
              metric_text="total assets", period_text="fiscal Q3 2025")
    v = check(c, store, context=f"Testco fiscal Q4 {context_year}")
    assert v.status == "verified"
    assert {f.period_end for f in v.evidence} == {date(2025, 12, 31)}


def test_calendar_words_conflicting_with_explicit_balance_date_stay_ambiguous(store):
    c = claim("Testco's total assets were $104.0 billion at calendar year-end", "$104.0 billion", 104,
              metric_text="total assets", period_text="September 30, 2025")
    v = check(c, store, context="Testco fiscal Q4 2025")
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")


def test_calendar_year_end_without_filed_balance_is_unverifiable(store, monkeypatch):
    original = store.facts
    monkeypatch.setattr(store, "facts", lambda cik, metric, **kw:
                        [f for f in original(cik, metric, **kw) if f.period_end != date(2025, 12, 31)])
    c = claim("Testco's total assets were $103.456 billion at calendar year-end", "$103.456 billion", 103.456,
              metric_text="total assets", period_text="fiscal Q3 2025")
    v = check(c, store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "December 31, 2025" in v.explanation


def test_calendar_year_end_does_not_use_unpublished_balance(store):
    c = claim("Testco's total assets were $104.0 billion at calendar year-end", "$104.0 billion", 104,
              metric_text="total assets", period_text="fiscal Q3 2025")
    v = check(c, store, as_of=datetime(2026, 1, 1, tzinfo=UTC))
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "February 20, 2026" in v.explanation or "2026-02-20" in v.explanation


def test_calendar_year_end_prefers_exact_filed_date_to_nearby_calendar_entry():
    cal = [V.periods.CalEntry(2026, "Q1", date(2025, 9, 28), date(2025, 12, 27))]
    c = claim("Total assets were $1 billion", "$1 billion", 1, metric_text="total assets",
              period_text="calendar year-end 2025")
    bd = BS.balance_date(c, cal, context=None, as_of=None, balance_dates=[date(2025, 12, 31)])
    assert bd.date == date(2025, 12, 31)


@pytest.mark.parametrize("earlier_zero,later_value", [(True, 4.5), (False, 4.0)])
def test_zero_support_is_retained_for_each_restatement_and_cutoff(store, monkeypatch, earlier_zero, later_value):
    rows = FALL_FACTS + [
        ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "2024-12-31", later_value * B, "Q3"),
        ("RestrictedCashAndCashEquivalents", "2024-12-31", 0, "Q3"),
    ]
    if earlier_zero:
        rows.append(("RestrictedCashAndCashEquivalents", "2024-12-31", 0, "K24"))
    filing_rows = {a: {"form": form, "accepted_at": at, "url": f"https://www.sec.gov/{a}", "report_date": report}
                   for a, form, filed, at, report, items in FILINGS[FALL].values()}
    facts, _ = BS.company_facts(cik=FALL, ticker="FALL", companyfacts=companyfacts(FALL, rows),
                               filings=filing_rows, calendar=PERIODS[FALL])
    support = [f for f in facts if f.concept == BS.RESTRICTED_TOTAL.name]
    assert {f.accession for f in support} == ({FILINGS[FALL]["K24"][0]} if earlier_zero else set()) | {FILINGS[FALL]["Q3"][0]}
    original = store.facts
    monkeypatch.setattr(store, "facts", lambda cik, metric, **kw:
                        [f for f in facts if f.metric == metric] if cik == FALL else original(cik, metric, **kw))
    value_text = f"${later_value:.2f} billion"
    c = claim(f"Fallco had {value_text} in cash and cash equivalents", value_text, later_value,
              metric_text="cash and cash equivalents", entity="Fallco", period_text="December 31, 2024")
    assert check(c, store, context=None).status == "verified"
    old = check(c, store, context=None, as_of=datetime(2025, 3, 1, tzinfo=UTC))
    assert (old.status, old.expected_value) == (("wrong", 4) if earlier_zero else ("unverifiable", None))
    assert {f.accession for f in old.evidence} == {FILINGS[FALL]["K24"][0]}


def test_prior_calendar_year_with_an_explicit_year_is_not_shifted_twice():
    c = claim("Total assets were $1 billion", "$1 billion", 1, metric_text="total assets",
              period_text="at the end of the previous calendar year 2025")
    bd = BS.balance_date(c, CAL, context="Testco fiscal Q1 2026", as_of=None)
    assert bd.date == date(2025, 12, 31)
