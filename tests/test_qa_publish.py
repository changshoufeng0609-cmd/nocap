"""Offline publication integrity: no provider calls or hidden denominator changes."""
import json
from pathlib import Path

import pytest

from eval import qa_core as core, qa_publish as publisher


@pytest.fixture
def public_run(tmp_path, monkeypatch):
    # Copy only the fixed public manifest; these response rows are synthetic fixtures.
    manifest = json.loads(core.MANIFEST.read_text())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(core.MANIFEST.read_text())
    monkeypatch.setattr(publisher, "ROOT", tmp_path)
    monkeypatch.setattr(core, "MANIFEST", manifest_path)
    folder = tmp_path / "eval/qa_runs/fixture"
    folder.mkdir(parents=True)
    raw = {"manifest_sha256": core.digest(manifest_path.read_bytes()), "target": "spark", "planned": 50,
           "status": "complete", "records": [{"question_id": manifest["items"][0]["question"]["id"],
                                                  "raw_outcome": "abstain", "gate": {"accepted": False, "reason": "abstain"}}]}
    source = folder / "spark.json"
    source.write_text(json.dumps(raw))
    return tmp_path, source, raw


def test_missing_models_are_pending_not_perfect_or_zero_cost(public_run):
    root, _, _ = public_run
    result = publisher.publish("fixture", output=root / "output.json")
    assert result["status"] == "in_progress"
    assert len(result["cases"]) == 50
    assert [run["calls"] for run in result["runs"]] == [1, 0, 0, 0]
    assert result["runs"][1]["sensitivity_score"] is None
    assert result["runs"][1]["token_api_cost_usd"] is None
    assert result["cases"][0]["responses"][1]["primary_accepted"] is None
    assert result["cases"][0]["responses"][0]["primary_accepted"] is False


@pytest.mark.parametrize("bad_id", ["../fixture", "a/b", "", "a" * 81])
def test_bad_run_id_rejected_before_read_or_write(bad_id):
    with pytest.raises(ValueError, match="run ID"):
        publisher.publish(bad_id)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "manifest"])
def test_publication_rejects_records_that_could_hide_or_duplicate_denominators(public_run, mutation):
    root, source, raw = public_run
    if mutation == "duplicate":
        raw["records"] *= 2
    elif mutation == "unknown":
        raw["records"][0]["question_id"] = "not-in-manifest"
    else:
        raw["manifest_sha256"] = "0" * 64
    source.write_text(json.dumps(raw))
    with pytest.raises(AssertionError):
        publisher.publish("fixture", output=root / "output.json")
    assert not (root / "output.json").exists()


def test_sensitivity_must_bind_to_the_exact_original_record_bytes(public_run):
    root, _, raw = public_run
    path = root / "sensitivity.json"
    path.write_text(json.dumps({"manifest_sha256": raw["manifest_sha256"], "new_model_calls": 0,
                               "runs": [], "source_records": {"spark": {"sha256": "0" * 64}}}))
    with pytest.raises(AssertionError):
        publisher.publish("fixture", sensitivity_path=path, output=root / "output.json")
    assert not (root / "output.json").exists()


def test_unknown_cost_reservation_stays_separate_from_known_usage(public_run):
    root, source, raw = public_run
    path = root / "sensitivity.json"
    path.write_text(json.dumps({"manifest_sha256": raw["manifest_sha256"], "new_model_calls": 0, "runs": [],
                               "source_records": {"spark": {"sha256": core.digest(source.read_bytes())}},
                               "ledger": {"attempts": [
                                   {"target": "spark", "status": "known_usage", "charged": 1000},
                                   {"target": "spark", "status": "unknown_usage_retained", "charged": 8000},
                               ]}}))
    result = publisher.publish("fixture", sensitivity_path=path, output=root / "output.json")
    assert result["runs"][0]["charged_or_reserved_usd"] == 0.009
    assert result["runs"][0]["unknown_reserved_usd"] == 0.008
