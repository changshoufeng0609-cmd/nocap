"""The spot500 run report reproduces each run's own counts and never invents precision.

Offline: it reads the committed run records and frozen corpora only.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from eval import spot500
from eval import spot500_report as report

ROOT = Path(__file__).resolve().parents[1]
RUNS = {"v1": "live-20261101.json", "v2": "v2-live-20261102.json", "v3": "v3-live-20261102.json"}


def summary(name):
    run, items = report.load_run(ROOT / "eval/spot500_runs" / RUNS[name])
    return report.summarize(items, run["records"])


@pytest.mark.parametrize("name, caught, marked, flagged, verified", [
    ("v1", (27, 35), 0, (0, 72), (54, 72)),
    ("v2", (27, 35), 1, (4, 75), (48, 75)),
    ("v3", (29, 32), 0, (1, 74), (58, 74)),
])
def test_report_matches_the_published_spot_checks(name, caught, marked, flagged, verified):
    rates = summary(name)["rates"]
    assert (rates["caught"]["k"], rates["caught"]["n"]) == caught
    assert rates["target_verified"]["k"] == marked
    assert (rates["true_wrong"]["k"], rates["true_wrong"]["n"]) == flagged
    assert (rates["true_verified"]["k"], rates["true_verified"]["n"]) == verified


def test_combined_verdict_precision_of_the_three_spot_checks():
    rates = [summary(name)["rates"] for name in RUNS]
    assert sum(r["verified_precision"]["k"] for r in rates) == 160
    assert sum(r["verified_precision"]["n"] for r in rates) == 161
    assert sum(r["wrong_precision"]["k"] for r in rates) == 72
    assert sum(r["wrong_precision"]["n"] for r in rates) == 77


def test_intervals_are_deterministic_and_never_collapse_at_zero():
    first, second = summary("v3"), summary("v3")
    assert first == second
    low, high = first["rates"]["target_verified"]["ci95"]
    assert low == 0.0 and high > 0.05
    for row in first["rates"].values():
        assert row["ci95"][0] <= row["rate"] <= row["ci95"][1]


def test_wilson_bounds():
    assert report.wilson(0, 35) == (0.0, pytest.approx(0.0989, abs=1e-4))
    low, high = report.wilson(29, 32)
    assert low == pytest.approx(0.7578, abs=1e-4) and high == pytest.approx(0.9676, abs=1e-4)
    assert report.wilson(10, 10)[1] == 1.0


def test_a_record_whose_summary_was_edited_is_refused(tmp_path):
    run = json.loads((ROOT / "eval/spot500_runs" / RUNS["v3"]).read_text())
    edited = deepcopy(run)
    edited["summary"]["total"]["caught_errors"] += 1
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="does not reproduce"):
        report.load_run(path)


def test_a_record_from_another_corpus_is_refused(tmp_path):
    run = json.loads((ROOT / "eval/spot500_runs" / RUNS["v3"]).read_text())
    run["corpus_sha256"] = spot500.digest(b"another corpus")
    path = tmp_path / "other.json"
    path.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="frozen corpus"):
        report.load_run(path)


def test_slices_partition_the_items():
    run, items = report.load_run(ROOT / "eval/spot500_runs" / RUNS["v3"])
    bank = [item for item in items if "bank" in item["features"]]
    rest = [item for item in items if "bank" not in item["features"]]
    whole = report.summarize(items, run["records"], bootstrap=False)["counts"]
    parts = [report.summarize(part, run["records"], bootstrap=False)["counts"] for part in (bank, rest)]
    assert all(whole[key] == sum(part.get(key, 0) for part in parts) for key in whole)
