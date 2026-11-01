"""scripts/relabel_release_facts.py: release labels follow the stored calendar, byte for byte (SYNTHETIC data)."""
from datetime import date, datetime, timezone

from receipts.ingest import build_company
from receipts.store import connect_rw, replace_company
from receipts.types import Fact
from scripts.relabel_release_facts import relabel

FILINGS = {  # key -> (accession, form, filed, accepted, report date, DEI fy, DEI fp)
    "K": ("0000000903-25-000001", "10-K", "2025-02-20", "2025-02-20T20:00:00.000Z", "2024-12-31", 2024, "FY"),
    "Q1": ("0000000903-25-000002", "10-Q", "2025-04-30", "2025-04-30T20:00:00.000Z", "2025-03-31", 2025, "Q1"),
    "Q2": ("0000000903-25-000003", "10-Q", "2025-07-30", "2025-07-30T20:00:00.000Z", "2025-06-30", 2025, "Q1"),  # mis-tag
}


def store(tmp_path):
    usgaap: dict = {}
    for (start, end), value, key in ((("2024-01-01", "2024-12-31"), 400e6, "K"), (("2025-01-01", "2025-03-31"), 101e6, "Q1"),
                                     (("2025-04-01", "2025-06-30"), 102e6, "Q2")):
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault("Revenues", {"units": {"USD": []}})["units"]["USD"].append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    b = build_company(cik=903, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                      companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2024, 1, 1), today=date(2026, 1, 1))
    db = tmp_path / "facts.sqlite"
    conn = connect_rw(db)
    replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    return db, b


def release(start, end, fy, fp, value):
    return Fact(cik=903, ticker="EXMP", metric="revenue", basis="GAAP", value=value, unit="USD",
                period_start=date.fromisoformat(start), period_end=date.fromisoformat(end), fiscal_year=fy,
                fiscal_period=fp, source="press_release", form="8-K", accession="0000000903-25-000009",
                accepted_at=datetime(2025, 10, 28, 20, tzinfo=timezone.utc), url="https://example.invalid/ex99.htm",
                locator="Revenue | 1")


def test_labels_follow_the_store_and_a_rejected_label_is_cleared(tmp_path):
    db, b = store(tmp_path)
    # The Q2 10-Q is tagged Q1: out of place, and it knocks out the Q1 label it shares with the real Q1.
    assert any("claimed for different periods" in w for w in b.warnings)
    lines = [release("2024-01-01", "2024-12-31", 2023, "FY", 400e6),   # stored as FY2024: relabelled
             release("2025-04-01", "2025-06-30", 2025, "Q2", 102e6),   # covered by a 10-Q, unlabelled: cleared
             release("2025-07-01", "2025-09-30", 2025, "Q3", 103e6)]   # no 10-Q yet: kept as inferred
    folder = tmp_path / "pr" / "EXMP"
    folder.mkdir(parents=True)
    path = folder / "0000000903-25-000009.jsonl"
    path.write_text("".join(f.model_dump_json() + "\n" for f in lines))
    before = path.read_text().splitlines()
    result = relabel(tmp_path / "pr", db, write=True)
    after = [Fact.model_validate_json(line) for line in path.read_text().splitlines()]
    assert [(f.fiscal_year, f.fiscal_period) for f in after] == [(2024, "FY"), (None, None), (2025, "Q3")]
    assert [c["new"] for c in result["changes"]] == [[2024, "FY"], [None, None]]
    assert path.read_text().splitlines()[2] == before[2]  # untouched lines stay byte for byte
