"""The /benchmarks accuracy panel's numbers (web/app/benchmarks/accuracy.json) are what scripts/build_accuracy_panel.py
makes of the committed spot500 run records through eval/spot500_report.py, never numbers typed by hand.

Offline: it reads the committed run records, frozen corpora, manifests and methods document only.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_accuracy_panel.py"
METHODS = ROOT / "eval/spot500_methods.md"


def generator():
    spec = importlib.util.spec_from_file_location("build_accuracy_panel", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built():
    gen = generator()
    text = gen.render(gen.build())
    return gen, text, json.loads(text)


def counts(rates: dict, key: str) -> tuple[int, int]:
    return rates[key]["k"], rates[key]["n"]


def github_anchor(heading: str) -> str:
    """GitHub's heading anchor for an ASCII heading: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 _-]", "", heading.strip().lower()).replace(" ", "-")


def test_the_committed_file_is_the_generators_output(built):
    gen, text, _ = built
    assert gen.OUTPUT == ROOT / "web/app/benchmarks/accuracy.json"
    assert gen.OUTPUT.read_text(encoding="utf-8") == text


def test_the_current_verifier_reproduces_the_published_full2_results(built):
    current = built[2]["current"]
    assert (current["set"], current["commit"], current["run_date"]) == ("full2", "8c2fd78", "2026-11-02")
    assert (current["items"], current["companies"]) == (1000, 400)
    rates = current["rates"]
    assert counts(rates, "caught") == (306, 360)
    assert counts(rates, "target_verified") == (0, 360)
    assert counts(rates, "true_wrong") == (17, 1298)
    assert counts(rates, "true_verified") == (736, 1298)
    assert counts(rates, "verified_precision") == (736, 738)
    assert counts(rates, "wrong_precision") == (257, 275)
    # The panel shows the report's own rounding, as in the methods document's table.
    shown = {key: (rates[key]["pct"], rates[key]["ci95_pct"]) for key in rates}
    assert shown["caught"] == ("85.0%", ["79.9%", "89.5%"])
    assert shown["target_verified"] == ("0.0%", ["0.0%", "1.1%"])
    assert shown["true_wrong"] == ("1.3%", ["0.7%", "2.1%"])
    assert shown["true_verified"] == ("56.7%", ["52.5%", "60.6%"])
    assert shown["verified_precision"] == ("99.7%", ["99.0%", "100.0%"])
    assert shown["wrong_precision"] == ("93.5%", ["89.8%", "96.6%"])
    assert (current["model"], current["model_calls"], current["cost_usd"]) == ("muse-spark-1.3", 940, 3.79727435)
    assert current["cost_is_estimate"] is False


def test_the_companies_never_used_in_development(built):
    fresh = built[2]["current"]["without_company_in_development"]
    assert fresh["slice"] == "without company_in_development"
    assert (fresh["items"], fresh["companies"]) == (768, 312)
    assert counts(fresh["rates"], "caught") == (235, 274)
    assert counts(fresh["rates"], "target_verified") == (0, 274)
    assert counts(fresh["rates"], "true_wrong") == (14, 1018)
    assert (fresh["rates"]["caught"]["pct"], fresh["rates"]["true_wrong"]["pct"]) == ("85.8%", "1.4%")


def test_the_previous_version_reproduces_the_first_comprehensive_test(built):
    previous = built[2]["previous"]
    assert (previous["set"], previous["commit"], previous["run_date"]) == ("full", "8d69d3c", "2026-11-02")
    assert (previous["items"], previous["companies"]) == (1000, 391)
    rates = previous["rates"]
    assert counts(rates, "caught") == (310, 376)
    assert counts(rates, "target_verified") == (2, 376)
    assert counts(rates, "true_wrong") == (14, 1271)
    assert counts(rates, "verified_precision") == (741, 744)
    assert "without_company_in_development" not in previous


def test_every_rate_is_its_own_count_inside_its_interval(built):
    data = built[2]
    for measured in (data["current"], data["current"]["without_company_in_development"], data["previous"]):
        assert set(measured["rates"]) == {"caught", "target_verified", "true_wrong", "true_verified",
                                          "verified_precision", "wrong_precision"}
        for row in measured["rates"].values():
            assert 0 <= row["k"] <= row["n"] and row["rate"] == row["k"] / row["n"]
            low, high = row["ci95"]
            assert 0 <= low <= row["rate"] <= high <= 1


def test_the_methods_links_name_existing_headings(built):
    data = built[2]
    assert data["github"] == "https://github.com/changshoufeng0609-cmd/nocap/blob/main/"
    assert data["report"] == "eval/spot500_report.py" and (ROOT / data["report"]).is_file()
    anchors = [github_anchor(line.lstrip("#")) for line in METHODS.read_text(encoding="utf-8").splitlines()
               if line.startswith("#")]
    expected = {"current": "results-of-the-one-live-run-november-2-2026-after-round-8",
                "previous": "results-of-the-one-live-run-november-2-2026-all-companies"}
    for name, anchor in expected.items():
        assert data[name]["methods"] == f"eval/spot500_methods.md#{anchor}"
        assert anchors.count(anchor) == 1, anchor  # a repeated heading would get GitHub's "-1" suffix
        assert (ROOT / data[name]["record"]).is_file()


def test_check_mode_passes_on_the_committed_file_and_fails_on_a_stale_one(tmp_path):
    current = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, cwd=ROOT,
                             timeout=300)
    assert current.returncode == 0, current.stdout + current.stderr
    stale = tmp_path / "accuracy.json"
    stale.write_text((ROOT / "web/app/benchmarks/accuracy.json").read_text(encoding="utf-8").replace(
        '"k": 306', '"k": 305', 1), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--check", "--output", str(stale)], capture_output=True,
                            text=True, cwd=ROOT, timeout=300)
    assert result.returncode == 1 and "differs" in result.stdout
