"""scripts/archive_companyfacts.py: archive and restore round-trip the exact bytes (SYNTHETIC body)."""
import hashlib
import json

import scripts.archive_companyfacts as ac


def test_archive_then_restore_reproduces_the_body_and_its_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ac, "ARCHIVE", tmp_path / "archive")
    body = json.dumps({"facts": {"us-gaap": {}}, "cik": 904}).encode()
    body_path, meta_path, gz_path = ac.paths(904)
    body_path.parent.mkdir(parents=True)
    body_path.write_bytes(body)
    meta_path.write_text(json.dumps({"url": ac.url_for(904), "fetched_at": "2026-10-31T00:00:00+00:00",
                                     "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}))
    assert ac.archive([904], delete_raw=True) == 0 and not body_path.exists() and gz_path.exists()
    meta_path.write_text(json.dumps({"sha256": "0" * 64}))  # a later fetch left a sidecar for other bytes
    assert ac.restore([904]) == 0
    assert body_path.read_bytes() == body
    assert json.loads(meta_path.read_text())["sha256"] == hashlib.sha256(body).hexdigest()
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_a_failing_cik_leaves_the_earlier_ones_indexed_and_their_raw_bodies_in_place(tmp_path, monkeypatch):
    """Review L3: the index used to be saved only at the end while raw bodies went one by one, so a later CIK's
    SystemExit left earlier archives unindexed with their raw bodies deleted, and restore refused them."""
    import pytest
    monkeypatch.setattr(ac, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ac, "ARCHIVE", tmp_path / "archive")
    body = b'{"facts": {}}'
    body_path, meta_path, gz_path = ac.paths(1)
    body_path.parent.mkdir(parents=True)
    body_path.write_bytes(body)
    meta_path.write_text(json.dumps({"sha256": hashlib.sha256(body).hexdigest(), "fetched_at": "x"}))
    with pytest.raises(SystemExit):
        ac.archive([1, 2], delete_raw=True)  # CIK 2 has no cached body
    assert "1" in ac.load_index() and gz_path.exists() and body_path.exists()
    assert ac.restore([1]) == 0 and ac.archive([1], delete_raw=True) == 0 and not body_path.exists()


def test_an_exhibit_that_fails_its_hash_leaves_the_earlier_ones_indexed_and_raw(tmp_path, monkeypatch):
    import sqlite3
    import pytest
    import scripts.archive_exhibits as ax
    monkeypatch.setattr(ax, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ax, "ARCHIVE", tmp_path / "archive")
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE filings (cik INTEGER, accession TEXT, form TEXT)")
    con.execute("INSERT INTO filings VALUES (1, '0000000001-26-000001', '8-K')")
    con.commit()
    con.close()
    monkeypatch.setattr(ax.eight_k_folders, "__defaults__", (db,))
    ax.CACHE.mkdir()
    for i, (body, good) in enumerate(((b"<html>a</html>", True), (b"<html>b</html>", False))):
        url = f"https://www.sec.gov/Archives/edgar/data/1/000000000126000001/ex99-{i}.htm"
        name = hashlib.sha256(url.encode()).hexdigest()
        (ax.CACHE / f"{name}.bin").write_bytes(body)
        (ax.CACHE / f"{name}.json").write_text(json.dumps({"url": url, "sha256": hashlib.sha256(
            body if good else b"other").hexdigest()}))
    with pytest.raises(SystemExit):
        ax.archive(delete_raw=True)
    assert len(ax.load_index()) == 1 and len(list(ax.CACHE.glob("*.bin"))) == 2
