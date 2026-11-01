"""Balance sheets retain each registrant's original filing and first-public timestamp."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import date

import pytest

from receipts import balance_sheet as BS, ingest
from receipts.store import Store, connect_rw, replace_company
from receipts.types import Claim
from receipts.verify import verify
from scripts.build_snapshot import compact_facts

OLD, NEW = 9001, 9002
K, Q1, Q2 = "0000009001-26-000045", "0000009001-26-000067", "0000009001-26-000093"
FILINGS = (
    (K, "10-K", "2026-02-18", "2025-12-31", OLD),
    (Q1, "10-Q", "2026-05-04", "2026-03-31", OLD),
    (Q2, "10-Q", "2026-08-03", "2026-06-30", NEW),
)


def setup(tmp_path, monkeypatch):
    monkeypatch.setitem(ingest.PREDECESSOR_CIKS, NEW, (OLD,))
    path = tmp_path / "facts.sqlite"
    con = connect_rw(path)
    company = {"cik": NEW, "ticker": "NEW", "name": "New Holdings", "aliases": {"new holdings"},
               "fiscal_year_end": "1231"}
    filings = [{"accession": a, "form": form, "filed": filed, "accepted_at": f"{filed}T20:00:00Z",
                "report_date": end, "items": "", "primary_doc": "doc.htm",
                "url": f"https://www.sec.gov/Archives/edgar/data/{filer}/{a.replace('-', '')}/doc.htm"}
               for a, form, filed, end, filer in FILINGS]
    calendar = [(2025, "FY", date(2025, 1, 1), date(2025, 12, 31), K),
                (2026, "Q1", date(2026, 1, 1), date(2026, 3, 31), Q1),
                (2026, "Q2", date(2026, 4, 1), date(2026, 6, 30), Q2)]
    replace_company(con, company, filings, calendar, [])
    con.commit()
    con.close()
    archive = tmp_path / "archive"
    archive.mkdir()
    # Mirrors the archived XOM issue: the current registrant carries the year-end comparative in August;
    # the original 10-K and the March quarter are present only under the predecessor.
    rows = {OLD: [("2025-12-31", 10_681e6, K), ("2026-03-31", 8_435e6, Q1),
                  ("2026-06-30", 10_588e6, Q2)],
            NEW: [("2025-12-31", 10_681e6, Q2), ("2026-06-30", 10_588e6, Q2)]}
    index = {}
    for cik, values in rows.items():
        entries = [{"end": end, "val": v, "accn": accession} for end, v, accession in values]
        raw = json.dumps({"facts": {"us-gaap": {"CashAndCashEquivalentsAtCarryingValue": {
            "units": {"USD": entries}}}}}).encode()
        (archive / f"CIK{cik:010d}.json.gz").write_bytes(gzip.compress(raw))
        index[str(cik)] = {"sha256": hashlib.sha256(raw).hexdigest()}
    (archive / "index.json").write_text(json.dumps(index))
    return path, archive, index


@pytest.mark.parametrize("compact", [False, True])
def test_predecessor_balance_sheets_keep_original_receipts_and_public_times(tmp_path, monkeypatch, compact):
    path, archive, index = setup(tmp_path, monkeypatch)
    con = sqlite3.connect(path)
    if compact:
        compact_facts(con)
    summary = BS.add_to_db(con, archive)
    first = con.execute("SELECT cik, metric, value, period_end, accession, accepted_at, url FROM facts "
                        "ORDER BY period_end").fetchall()
    assert [r[4] for r in first] == [K, Q1, Q2]
    assert [r[0] for r in first] == [NEW, NEW, NEW]
    assert f"/data/{OLD}/" in first[0][-1] and f"/data/{OLD}/" in first[1][-1]
    assert summary["audit"]["NEW"]["companyfacts_sha256"] == index[str(NEW)]["sha256"]
    assert summary["audit"]["NEW"]["predecessor_companyfacts_sha256"] == {str(OLD): index[str(OLD)]["sha256"]}
    BS.add_to_db(con, archive)
    assert con.execute("SELECT cik, metric, value, period_end, accession, accepted_at, url FROM facts "
                       "ORDER BY period_end").fetchall() == first
    con.close()
    store = Store(path, tmp_path / "pr_facts")
    try:
        for value, period, cutoff, accession in (
            (10.7, "end of 2025", "2026-03-01T00:00:00Z", K),
            (8.4, "first quarter of 2026", "2026-05-05T00:00:00Z", Q1),
        ):
            span = f"New Holdings had ${value} billion in cash and cash equivalents."
            claim = Claim(id="cash", metric="other", metric_text="cash and cash equivalents",
                          value_text=f"${value} billion", value=value, unit="USD", scale="billions",
                          period_text=period, period_type="instant", comparison="none", basis="GAAP",
                          forward_looking=False, entity="New Holdings", segment=None,
                          span_text=span, span_start=0, span_end=len(span))
            verdict = verify(claim, store, context=f"New Holdings, {period}", as_of=cutoff)
            assert (verdict.status, verdict.reason) == ("verified", "match")
            assert {f.accession for f in verdict.evidence} == {accession}
    finally:
        store.close()


@pytest.mark.parametrize("broken", ["missing", "hash"])
def test_predecessor_archive_is_required_and_hash_checked_before_replacement(tmp_path, monkeypatch, broken):
    path, archive, _ = setup(tmp_path, monkeypatch)
    old = archive / f"CIK{OLD:010d}.json.gz"
    if broken == "missing":
        old.unlink()
    else:
        old.write_bytes(gzip.compress(b'{"facts":{}}'))
    con = sqlite3.connect(path)
    with pytest.raises(SystemExit, match="balance_sheet:.*CIK0000009001"):
        BS.add_to_db(con, archive)
    assert con.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    con.close()
