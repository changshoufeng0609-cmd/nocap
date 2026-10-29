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
    store = SimpleNamespace(resolve_entity=lambda ticker: (1045810, "NVDA"),
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


def test_empty_filing_collection_is_valid_without_network(tmp_path):
    never = lambda *args: pytest.fail("No source lookup is needed")
    assert copy_filing_texts(tmp_path / "missing", tmp_path / "out", None,
                             SimpleNamespace(get=never)) == ("", 0)
