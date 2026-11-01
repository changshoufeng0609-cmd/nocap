"""Packaged filing quotes must reproduce source bytes and the store's acceptance time."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.build_snapshot import copy_filing_texts
from test_grounding import TEXT, filing_fixture


def sources(tmp_path):
    src = tmp_path / "source"
    row = filing_fixture(src)
    body = f"<html><body>{TEXT}</body></html>".encode()
    row["source_sha256"] = hashlib.sha256(body).hexdigest()
    (src / "index.json").write_text(json.dumps({"version": 1, "filings": [row]}))
    store = SimpleNamespace(by_ticker=lambda ticker: (1045810, "NVDA"),
                            filings=lambda cik, forms: [dict(row)])
    edgar = SimpleNamespace(get=lambda url: body)
    return src, row, store, edgar


def test_snapshot_copies_only_source_bound_filings(tmp_path):
    src, row, store, edgar = sources(tmp_path)
    dst = tmp_path / "snapshot"
    digest, count = copy_filing_texts(src, dst, store, edgar)
    assert len(digest) == 64 and count == 1
    assert (dst / f"{row['sha256']}.txt").read_text() == TEXT
    assert json.loads((dst / "index.json").read_text())["filings"][0]["accepted_at"] == row["accepted_at"]


@pytest.mark.parametrize("field,value", [
    ("accepted_at", "2026-08-26T19:00:00Z"),
    ("url", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/other.htm"),
    ("source_sha256", "0" * 64),
])
def test_snapshot_rejects_changed_provenance(tmp_path, field, value):
    src, row, store, edgar = sources(tmp_path)
    changed = dict(row, **{field: value})
    (src / "index.json").write_text(json.dumps({"version": 1, "filings": [changed]}))
    with pytest.raises(SystemExit):
        copy_filing_texts(src, tmp_path / "snapshot", store, edgar)


def test_snapshot_rejects_rewritten_text_even_with_updated_hash(tmp_path):
    src, row, store, edgar = sources(tmp_path)
    fabricated = TEXT.replace("depends", "never depends")
    changed = dict(row, sha256=hashlib.sha256(fabricated.encode()).hexdigest(), characters=len(fabricated))
    (src / f"{changed['sha256']}.txt").write_text(fabricated)
    (src / "index.json").write_text(json.dumps({"version": 1, "filings": [changed]}))
    with pytest.raises(SystemExit, match="original cached SEC"):
        copy_filing_texts(src, tmp_path / "snapshot", store, edgar)


def word_ticker_filing(tmp_path):
    """A packaged COST 10-Q, its cached SEC bytes and a real Store that lists the filing: COST is a word ticker, which
    resolve_entity never matches by itself."""
    from receipts.store import Store, company_aliases, connect_rw, replace_company
    src = tmp_path / "source"
    row = filing_fixture(src)
    body = f"<html><body>{TEXT}</body></html>".encode()
    row.update(id="COST-0000909832-26-000011", ticker="COST", cik=909832, accession="0000909832-26-000011",
               url="https://www.sec.gov/Archives/edgar/data/909832/000090983226000011/cost-20260510.htm",
               source_sha256=hashlib.sha256(body).hexdigest())
    (src / "index.json").write_text(json.dumps({"version": 1, "filings": [row]}))
    conn = connect_rw(tmp_path / "facts.sqlite")
    replace_company(conn, {"cik": 909832, "ticker": "COST", "name": "COSTCO WHOLESALE CORP /NEW",
                           "fiscal_year_end": "0831", "aliases": company_aliases("COST", "COSTCO WHOLESALE CORP /NEW")},
                    [{"accession": row["accession"], "form": "10-Q", "filed": "2026-07-31", "report_date": "2026-06-27",
                      "accepted_at": row["accepted_at"], "items": "", "primary_doc": "cost-20260510.htm",
                      "url": row["url"]}], [], [])
    conn.close()
    return src, row, Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr"), SimpleNamespace(get=lambda url: body)


def test_snapshot_binds_a_word_ticker_filing_to_its_company(tmp_path):
    src, row, store, edgar = word_ticker_filing(tmp_path)
    try:
        assert store.resolve_entity("COST") is None
        digest, count = copy_filing_texts(src, tmp_path / "snapshot", store, edgar)
        assert count == 1 and len(digest) == 64
        assert (tmp_path / "snapshot" / f"{row['sha256']}.txt").read_text() == TEXT
    finally:
        store.close()


def test_empty_filing_collection_is_valid_without_network(tmp_path):
    never = lambda *args: pytest.fail("No source lookup is needed")
    assert copy_filing_texts(tmp_path / "missing", tmp_path / "out", None,
                             SimpleNamespace(get=never)) == ("", 0)
