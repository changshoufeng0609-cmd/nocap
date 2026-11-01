"""A bank's revenue as net interest income plus noninterest income (receipts.ingest.BANK_REVENUE), served once its own
releases show that sum is the total revenue they report (scripts/confirm_revenue.py), where the filing tags no broader
revenue. Regions' "Total revenue" for Q2 2025 is $1,905M: net interest income plus noninterest income."""
import gzip
import json
import sqlite3
from datetime import date, datetime, timezone

from receipts.ingest import BANK_REVENUE, build_company
from receipts.types import Fact

FILINGS = {  # key -> (accession, form, filed, accepted, report date, DEI fy, DEI fp)
    "Q1": ("0000000904-25-000001", "10-Q", "2025-05-01", "2025-05-01T20:00:00.000Z", "2025-03-31", 2025, "Q1"),
    "Q2": ("0000000904-25-000002", "10-Q", "2025-08-01", "2025-08-01T20:00:00.000Z", "2025-06-30", 2025, "Q2"),
}
PERIOD = {"Q1": ("2025-01-01", "2025-03-31"), "Q2": ("2025-04-01", "2025-06-30")}
# Q1 tags only the two parts; Q2 also tags Revenues, which stays the served figure
ENTRIES = (("InterestIncomeExpenseNet", "Q1", 1240e6), ("NoninterestIncome", "Q1", 544e6),
           ("InterestIncomeExpenseNet", "Q2", 1260e6), ("NoninterestIncome", "Q2", 645e6), ("Revenues", "Q2", 1905e6))


def companyfacts() -> dict:
    usgaap: dict = {}
    for concept, key, value in ENTRIES:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        start, end = PERIOD[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    return {"facts": {"us-gaap": usgaap}}


def revenue(bank: bool):
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    b = build_company(cik=904, ticker="EXBK", names=["Example Bancorp"], fiscal_year_end="1231", filing_rows=rows,
                      companyfacts=companyfacts(), facts_start=date(2024, 1, 1), today=date(2026, 10, 1),
                      sic="6022", bank_revenue=bank)
    return {(f.period_end.isoformat(), f.concept.removeprefix("us-gaap:"), f.source, f.value, f.locator)
            for f, kind in b.facts if f.metric == "revenue" and kind == "quarter"}


def test_a_decided_bank_serves_its_two_parts_summed_where_no_broader_revenue_is_tagged():
    assert revenue(False) == {("2025-06-30", "Revenues", "xbrl", 1905e6, None)}
    assert revenue(True) == {
        ("2025-03-31", BANK_REVENUE, "derived", 1784e6, f"{BANK_REVENUE} = InterestIncomeExpenseNet 1,240,000,000 + "
                                                          f"NoninterestIncome 544,000,000 (10-Q {FILINGS['Q1'][0]})"),
        ("2025-06-30", "Revenues", "xbrl", 1905e6, None)}


def _bank(tmp_path, totals):
    (tmp_path / "audit").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audit" / "EXBK.json").write_text(json.dumps({"cik": 904, "revenue": []}))
    (tmp_path / "cf").mkdir(exist_ok=True)
    (tmp_path / "cf" / "CIK0000000904.json.gz").write_bytes(gzip.compress(json.dumps(companyfacts()).encode()))
    folder = tmp_path / "pr" / "EXBK"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "r.jsonl").write_text("".join(Fact(
        cik=904, ticker="EXBK", metric="revenue", basis="GAAP", value=v, unit="USD",
        period_start=date.fromisoformat(PERIOD[k][0]), period_end=date.fromisoformat(PERIOD[k][1]),
        source="press_release", form="8-K", accession="0000000904-25-000009",
        accepted_at=datetime(2025, 7, 18, tzinfo=timezone.utc), url="https://example.invalid/ex99.htm",
        locator=f"Total revenue | {v / 1e6:,.0f}", decimals=-6).model_dump_json() + "\n" for k, v in totals))
    return tmp_path / "audit", tmp_path / "pr", tmp_path / "cf"


def test_the_bank_decision_needs_two_matching_release_totals_and_none_that_differ(tmp_path):
    from scripts.confirm_revenue import bank_total
    d = bank_total("EXBK", *_bank(tmp_path, [("Q1", 1784e6), ("Q2", 1905e6)]))
    assert d["decided"] and [p["period"] for p in d["periods"]] == ["2025-01-01..2025-03-31",
                                                                   "2025-04-01..2025-06-30"]
    assert not bank_total("EXBK", *_bank(tmp_path / "one", [("Q1", 1784e6)]))["decided"]
    d = bank_total("EXBK", *_bank(tmp_path / "fte", [("Q1", 1784e6), ("Q2", 1921e6)]))  # a taxable-equivalent total
    assert not d["decided"] and d["reason"].startswith("a release total differs")


def test_main_records_the_bank_decision_and_revokes_it_when_a_release_total_differs(tmp_path):
    from scripts.confirm_revenue import BANK_KEY, main
    audit_dir, pr_dir, cf_dir = _bank(tmp_path, [("Q1", 1784e6), ("Q2", 1905e6)])
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("facts_start", "2023-09-14"), ("quarters", "8")])
    con.commit()
    con.close()
    path = tmp_path / "confirmations.json"
    kw = dict(audit_dir=audit_dir, pr_dir=pr_dir, confirmations_path=path, db=db, cf_dir=cf_dir)
    assert main(["EXBK"], **kw) == 0
    assert json.loads(path.read_text())["EXBK"][BANK_KEY]["decided_on"] == "2026-10-31"
    _bank(tmp_path, [("Q1", 1784e6), ("Q2", 1921e6)])
    assert main(["EXBK"], **kw) == 0 and "EXBK" not in json.loads(path.read_text())
