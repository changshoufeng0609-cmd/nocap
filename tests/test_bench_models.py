"""Benchmark guards and scoring, with no model endpoint or held-out corpus access."""
import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from receipts import extract as ex


spec = importlib.util.spec_from_file_location("receipts_bench_under_test",
                                             Path(__file__).resolve().parents[1] / "eval" / "bench_models.py")
bench = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bench
spec.loader.exec_module(bench)


def forbidden(*args, **kwargs):
    raise AssertionError("this operation must not run")


@pytest.fixture
def setup_run(monkeypatch, tmp_path):
    text_path = tmp_path / "gold.txt"
    text_path.write_text("Revenue was $1 billion.")
    monkeypatch.setattr(bench, "GOLD_TEXT", text_path)
    monkeypatch.setattr(bench, "make_target", lambda name: ex.BACKENDS[bench.TARGETS[name][0]])
    monkeypatch.setattr(bench, "seed_from_shared_cache", lambda *args: 0)
    monkeypatch.setattr(ex, "passages_to_call", lambda *args, **kwargs: 1)
    monkeypatch.setattr(ex, "make_client", forbidden)
    monkeypatch.setattr(ex, "extract_detailed", forbidden)
    monkeypatch.setattr(bench, "warm_up", forbidden)
    ev = SimpleNamespace(MUTATORS=(), _as_of=lambda it: None,
                         score=lambda *args: {"per_mutator": {}, "originals": {}, "controls": {}, "rows": []})
    return dict(store=None, ev=ev, dev_items=[{"text": "A dev passage", "context": "", "item_id": "dev-only"}],
                gold=[{"value": 1, "scale": "billions", "canonical": {"revenue"}, "basis": None, "period": []}],
                cache_dir=tmp_path / "cache", warmup_timeout_s=1)


@pytest.mark.parametrize("name", ["spark", "glimmer"])
@pytest.mark.parametrize("offline,budget,allow", [(True, 1.0, True), (False, 0.0, True)])
def test_offline_and_zero_budget_never_create_clients_or_warm_up(setup_run, name, offline, budget, allow):
    r = bench.run_target(name, budget=bench.Budget(budget), offline=offline, allow_self_hosted=allow, **setup_run)
    assert r["gold"]["skipped"]
    assert r["dev"]["ran"] == 0 and r["spent_usd"] == 0
    assert r["dev"]["skip_reasons"]["dev-only"]


def test_self_hosted_zero_token_estimate_still_requires_explicit_gpu_permission(setup_run):
    r = bench.run_target("glimmer", budget=bench.Budget(1), offline=False, **setup_run)
    assert "--allow-self-hosted" in r["skipped"]


def test_budget_is_checked_before_client_creation(setup_run):
    r = bench.run_target("spark", budget=bench.Budget(0.001), offline=False, **setup_run)
    assert "budget:" in r["skipped"] and r["dev"]["ran"] == 0


@pytest.mark.parametrize("name", ["spark", "glimmer"])
def test_cached_work_needs_no_key_endpoint_or_gpu_permission(setup_run, monkeypatch, name):
    monkeypatch.setattr(ex, "passages_to_call", lambda *args, **kwargs: 0)
    seen = []

    def extract(text, context, **kwargs):
        seen.append(kwargs["client"])
        return ex.ExtractResult([], context)

    monkeypatch.setattr(ex, "extract_detailed", extract)
    r = bench.run_target(name, budget=bench.Budget(0), offline=True, **setup_run)
    assert seen == [bench.CACHE_ONLY_CLIENT, bench.CACHE_ONLY_CLIENT]
    assert r["dev"]["ran"] == 1 and r["spent_usd"] == 0


def test_cache_miss_during_offline_extraction_cannot_fall_back_to_a_real_client(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "make_client", forbidden)
    with pytest.raises(ex.ExtractionError, match="cache-only benchmark"):
        ex.extract_detailed("Revenue was $1 billion.", client=bench.CACHE_ONLY_CLIENT,
                            cache_dir=tmp_path, backend=ex.BACKENDS["meta"])


def test_explicit_gpu_permission_warms_up_only_when_live_work_is_started(setup_run, monkeypatch):
    client = object()
    calls = []
    monkeypatch.setattr(ex, "make_client", lambda **kw: calls.append("client") or client)
    monkeypatch.setattr(bench, "warm_up", lambda *args: calls.append("warmup") or 0.1)

    def extract(text, context, **kwargs):
        assert kwargs["client"] is client
        calls.append("extract")
        return ex.ExtractResult([], context)

    monkeypatch.setattr(ex, "extract_detailed", extract)
    r = bench.run_target("glimmer", budget=bench.Budget(1), offline=False, allow_self_hosted=True, **setup_run)
    assert calls == ["client", "warmup", "extract", "extract"]
    assert "outside the token budget" in r["notes"][0]


def test_fresh_runs_do_not_seed_shared_response_cache(setup_run, monkeypatch):
    monkeypatch.setattr(bench, "seed_from_shared_cache", forbidden)
    bench.run_target("spark", budget=bench.Budget(0), offline=True, reuse_shared_cache=False, **setup_run)


@pytest.mark.parametrize("budget", [-1, math.inf, -math.inf, math.nan])
def test_invalid_budgets_are_rejected_before_work(budget):
    with pytest.raises(ValueError, match="finite, nonnegative"):
        bench.Budget(budget)


@pytest.mark.parametrize("period", ["Q3 FY2027", "second quarter of fiscal 2028"])
def test_generic_period_aliases_cannot_hide_an_explicit_contradiction(period):
    assert not bench._text_match(period, ["second quarter of fiscal 2027", "the quarter"])
    assert bench._text_match("Q2 FY2027", ["second quarter of fiscal 2027", "the quarter"])


def test_signed_numbers_are_not_treated_as_the_same_gold_value():
    gold = [{"value": 1, "scale": "billions", "canonical": {"revenue"}, "basis": None, "period": []}]
    entries = [{"value": -1, "scale": "billions", "metric": "revenue", "basis": None, "period_text": ""}]
    assert bench.match_gold(entries, gold) == (set(), set())


def test_one_to_one_matching_does_not_depend_on_broad_alias_order():
    assert bench._assign([(0, 0), (0, 1), (1, 0)]) == [(0, 1), (1, 0)]


def test_recorded_latency_is_marked_in_each_row():
    result = {"label": "Example", "self_hosted": False, "gold": {
        "raw": {"wrong": 0, "claims": 1, "by_reason": {}, "verbatim_spans": 1},
        "raw_label_errors": 0, "strict_matched": 1, "shipped_claims": 1, "unflagged_errors": 0,
        "wall_s": 2.3, "wall_from": "recorded call latencies (max; not end-to-end)",
        "cost_per_check_usd": 0.001}}
    assert "2.3 s (recorded max)" in bench.row(result)
    result["gold"]["wall_from"] = "this run"
    assert "2.3 s (live)" in bench.row(result)
