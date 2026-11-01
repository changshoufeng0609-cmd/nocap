"""Source overlay integrity; all data is public benchmark/snapshot material."""
import json
from pathlib import Path

import pytest

from eval import qa_reproduce as replay


@pytest.mark.parametrize("name", ["../.env", "/tmp/file", "eval/../../secret"])
def test_artifact_paths_cannot_escape_scratch(name):
    with pytest.raises(ValueError):
        replay.checked_relative(name)


@pytest.mark.parametrize("run_id", ["../run", "a/b", "", "a" * 81])
def test_invalid_run_id_rejected_before_reading(run_id, tmp_path):
    with pytest.raises(ValueError, match="run ID"):
        replay.prepare_scratch(run_id, tmp_path, root=tmp_path)


def test_frozen_overlay_and_original_manifest_have_fixed_hashes():
    folder = replay.ROOT / "eval/qa_runs/qa-closed-book-20261029/frozen_sources"
    data = (folder / "bundle.json").read_bytes()
    assert replay.digest(data) == replay.BUNDLE_SHA256
    index = json.loads(data)
    manifest = json.loads((replay.ROOT / "eval/qa_manifest.json").read_text())
    assert index["original_source_files"] == len(manifest["source_hashes"])
    assert index["manifest_sha256"] == replay.digest((replay.ROOT / "eval/qa_manifest.json").read_bytes())
    for name, expected in index["files"].items():
        assert replay.digest((folder / replay.checked_relative(name)).read_bytes()) == expected
        if name in manifest["source_hashes"]:
            assert expected == manifest["source_hashes"][name]
    assert index["files"]["data/snapshot/manifest.json"] == manifest["snapshot_hashes"]["manifest.json"]


def _evidence(root, *, live=None, archived=None):
    records = root / "eval/qa_runs/fixture"
    if live is not None:
        (root / "data/snapshot").mkdir(parents=True)
        (root / "data/snapshot/facts.sqlite").write_bytes(live)
    if archived is not None:
        (records / "evidence").mkdir(parents=True)
        (records / "evidence/facts.sqlite").write_bytes(archived)
    return records


def test_matching_live_snapshot_is_used_and_the_archive_ignored(tmp_path):
    records = _evidence(tmp_path, live=b"october 29", archived=b"anything else")
    pin = replay.digest(b"october 29")
    assert replay.pinned_snapshot_bytes("facts.sqlite", pin, root=tmp_path, records=records) == (b"october 29", "live")


@pytest.mark.parametrize("live", [b"topped up", None])
def test_changed_or_missing_live_snapshot_reads_the_same_named_archive(tmp_path, live):
    records = _evidence(tmp_path, live=live, archived=b"october 29")
    pin = replay.digest(b"october 29")
    assert replay.pinned_snapshot_bytes("facts.sqlite", pin, root=tmp_path, records=records) == (b"october 29", "archive")


@pytest.mark.parametrize("archived", [None, b"tampered"])
def test_changed_live_snapshot_without_a_matching_archive_fails(tmp_path, archived):
    records = _evidence(tmp_path, live=b"topped up", archived=archived)
    with pytest.raises(ValueError, match="snapshot bytes differ: facts.sqlite"):
        replay.pinned_snapshot_bytes("facts.sqlite", replay.digest(b"october 29"), root=tmp_path, records=records)


def test_archived_evidence_is_the_pinned_october_29_bytes():
    manifest = json.loads((replay.ROOT / "eval/qa_manifest.json").read_text())
    archive = replay.ROOT / "eval/qa_runs/qa-closed-book-20261029/evidence"
    assert (archive / "facts.sqlite").is_file()  # the top-up changed it, so the frozen benchmark depends on this copy
    files = [p for p in archive.rglob("*") if p.is_file()]
    for path in files:
        assert replay.digest(path.read_bytes()) == manifest["snapshot_hashes"][path.relative_to(archive).as_posix()]


def test_every_pinned_snapshot_file_resolves_live_or_archived(tmp_path):
    hashes = replay.prepare_scratch("qa-closed-book-20261029", tmp_path)
    manifest = json.loads((replay.ROOT / "eval/qa_manifest.json").read_text())
    for name, expected in manifest["snapshot_hashes"].items():
        assert replay.digest((tmp_path / "data/snapshot" / name).read_bytes()) == expected
    assert set(hashes["archived_evidence"]) <= set(manifest["snapshot_hashes"]) - {"manifest.json"}


def test_changed_bundle_rejected_before_import(tmp_path):
    folder = tmp_path / "eval/qa_runs/fixture/frozen_sources"
    folder.mkdir(parents=True)
    (folder / "bundle.json").write_text('{}\n')
    with pytest.raises(ValueError, match="bundle index differs"):
        replay.prepare_scratch("fixture", tmp_path / "scratch", root=tmp_path)
