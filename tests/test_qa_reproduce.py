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


def test_changed_bundle_rejected_before_import(tmp_path):
    folder = tmp_path / "eval/qa_runs/fixture/frozen_sources"
    folder.mkdir(parents=True)
    (folder / "bundle.json").write_text('{}\n')
    with pytest.raises(ValueError, match="bundle index differs"):
        replay.prepare_scratch("fixture", tmp_path / "scratch", root=tmp_path)
