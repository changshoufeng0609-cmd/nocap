"""J1 regressions for month quarters, coverage and snapshot-only checkouts."""
import json
from pathlib import Path
import shutil

import pytest

from receipts import api, periods, store
from receipts.check import check_detailed
from receipts.cli import main
from receipts.verify import verify
from test_verify import claim, STORE

ROOT = Path(__file__).resolve().parents[1]


def test_data_directory_shared_by_api_and_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(api, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("RECEIPTS_DATA_DIR", raising=False)
    assert store.default_data_dir() == api.default_data_dir({}) == tmp_path / "data/snapshot"
    (tmp_path / "data").mkdir()
    (tmp_path / "data/facts.sqlite").touch()
    assert store.default_data_dir() == api.default_data_dir({}) == tmp_path / "data"
    monkeypatch.setenv("RECEIPTS_DATA_DIR", "evidence")
    assert store.default_data_dir() == api.default_data_dir({"RECEIPTS_DATA_DIR": "evidence"}) == tmp_path / "evidence"
    with pytest.raises(FileNotFoundError):
        store.Store()  # explicit missing data must never silently use unrelated evidence


def test_snapshot_only_cli_uses_read_only_model_cache(tmp_path, monkeypatch, capsys):
    shutil.copytree(ROOT / "data/snapshot", tmp_path / "data/snapshot")
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.delenv("RECEIPTS_DATA_DIR", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "unused-offline-test-key")
    from receipts import extract
    monkeypatch.setattr(extract, "make_client", lambda **kw: pytest.fail("snapshot example made a model call"))
    assert main(["check", str(ROOT / "examples/nvda_q2_fy2027.txt"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["extraction"]["cost_usd"] == 0
    assert result["extraction"]["calls"] == result["extraction"]["cached_calls"]
    assert sum(r["verdict"]["status"] == "wrong" for r in result["results"]) == 2
    # Direct detailed API with a genuinely empty write cache also reaches the bundled responses.
    s = store.Store()
    try:
        res = check_detailed((ROOT / "examples/nvda_q2_fy2027.txt").read_text(), store=s,
                             cache_dir=tmp_path / "empty-cache")
        assert res.extraction.cost_usd == 0 and res.extraction.cached_calls == len(res.extraction.calls)
    finally:
        s.close()


@pytest.mark.parametrize("phrase,year,fp", [
    ("June quarter 2026", 2026, "Q3"),
    ("June-quarter", 2026, "Q3"),
    ("September quarter 2025", 2025, "Q4"),
    ("quarter ended in September 2025", 2025, "Q4"),
])
def test_apple_month_quarters(phrase, year, fp):
    with_store = store.Store(db_path=ROOT / "data/snapshot/facts.sqlite")
    try:
        rp = periods.resolve_period(phrase, with_store, 320193, as_of="2026-07-30T20:15:00Z")
        assert (rp.fiscal_year, rp.fiscal_period) == (year, fp)
    finally:
        with_store.close()


def test_month_context_and_week_calendar():
    from datetime import date
    cal = [periods.CalEntry(2023, "Q3", date(2023, 4, 2), date(2023, 7, 1)),
           periods.CalEntry(2024, "Q3", date(2024, 3, 31), date(2024, 6, 29))]
    for phrase, context in [("June quarter 2023", None), ("the quarter", "Apple June quarter 2023")]:
        rp = periods.resolve_in_calendar(phrase, cal, context=context)
        assert rp.period_end == date(2023, 7, 1)
    assert periods.find_period_phrase("Apple's June-quarter 2026 revenue rose.") == "June-quarter 2026"
    assert periods.resolve_in_calendar("the quarter", cal, context="Apple June quarter", as_of="2024-07-01").fiscal_year == 2024
    assert periods.resolve_in_calendar("September quarter 2023", cal) is None


def test_foreign_fiscal_context_not_reused():
    v = verify(claim("$96.2 billion", 96.2, period_text="the quarter"), STORE,
               context="Microsoft Q2 FY2026")
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")


def test_real_apple_month_period_and_lookahead():
    s = store.Store(db_path=ROOT / "data/snapshot/facts.sqlite")
    try:
        c = claim("$109.4 billion", 109.4, entity="Apple", period_text="June quarter")
        c = c.model_copy(update={"span_text": "June-quarter revenue of $109.4 billion"})
        assert verify(c, s, context="Amazon second quarter 2026").status == "verified"
        assert verify(c, s, context="Amazon second quarter 2026", as_of="2026-07-30T20:15:00Z").reason == "look_ahead"
        v = verify(claim("$10 billion", 10, entity="IBM"), s)
        assert "NoCap covers 11 companies today:" in v.explanation
        assert v.status == "unverifiable" and "IBM" in v.explanation
    finally:
        s.close()
