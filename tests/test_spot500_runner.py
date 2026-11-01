"""Frozen spot-check scoring and admission tests; no real extraction or provider calls."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from eval import spot500
from receipts import extract as ex


def item(name, *, role="true", mutator="none", status="verified", reason="match"):
    text = f"{name} revenue was $2 billion."
    start = text.index("$2")
    value = "$2 billion"
    return {"item_id": name, "item_hash": spot500.digest(name.encode()), "text": text,
            "context": "Example Q2 FY2026", "as_of": None, "mutator": mutator,
            "features": ["revenue", "bank_revenue"], "gold_claims": [
                {"value_text": value, "span": [start, start + len(value)], "basis": "GAAP",
                 "metric": "revenue", "role": role, "expected_status": status,
                 "expected_reason": reason}]}


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    rows = [item("original"), item("mutant", role="target", mutator="value", status="wrong",
                                  reason="value_mismatch"),
            item("guidance", role="guidance", mutator="forward_looking_control",
                 status="unverifiable", reason="forward_looking")]
    corpus = tmp_path / "spot500_v1.jsonl"
    corpus.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = tmp_path / "spot500_manifest.json"
    manifest.write_text(json.dumps({"corpus_sha256": spot500.file_digest(corpus), "items": [
        {"item_id": row["item_id"], "sha256": spot500.json_digest(row)} for row in rows]}))
    monkeypatch.setattr(spot500, "CORPUS", corpus)
    monkeypatch.setattr(spot500, "MANIFEST", manifest)
    return rows


def test_oracle_runs_offline_retains_all_records_and_refuses_second_run(frozen, tmp_path, monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail("Oracle attempted provider configuration or dotenv access")
    monkeypatch.setattr(ex, "make_client", prohibited)
    monkeypatch.setattr(ex, "settings_env", prohibited)
    monkeypatch.setattr(ex, "_load_api_key", prohibited)
    _, rows = spot500.load_items()
    output = tmp_path / "oracle.json"
    result = spot500.run(rows, output, stub=True, evidence_dir=tmp_path / "absent", budget=.95)
    total = result["summary"]["total"]
    assert total["status_and_reason_correct"] == total["gold_claims"] == 3
    assert total["caught_errors"] == total["expected_reasons"] == 1
    assert total["false_alarm_original_items"] == total["verified_guidance"] == 0
    assert result["cost_usd"] == result["metering"]["calls"] == 0
    assert result["estimated_cost_usd"] == len(rows) * spot500.harness.DEFAULT_EST_COST_PER_ITEM
    assert result["inputs_unchanged"]
    retained = [json.loads(line) for line in output.with_suffix(".run").joinpath("records.jsonl").read_text().splitlines()]
    assert [row["item_id"] for row in retained] == [row["item_id"] for row in rows]
    assert set(result["records"]) == {row["item_id"] for row in rows}
    assert json.loads(output.read_text())["status"] == "completed"
    for feature in ("revenue", "bank_revenue"):
        assert result["summary"]["by_feature"][feature] == total
    with pytest.raises(FileExistsError):
        spot500.run(rows, output, stub=True, evidence_dir=tmp_path, budget=.95)


def test_interrupted_run_directory_cannot_be_reused(frozen, tmp_path):
    output = tmp_path / "interrupted.json"
    output.with_suffix(".run").mkdir()
    with pytest.raises(FileExistsError):
        spot500.run(frozen, output, stub=True, evidence_dir=tmp_path, budget=.95)
    assert not output.exists()


@pytest.mark.parametrize("budget", [float("nan"), 0, -1, 1, 2, .001])
def test_invalid_or_insufficient_budget_fails_before_output(frozen, tmp_path, budget):
    output = tmp_path / "budget.json"
    with pytest.raises(ValueError):
        spot500.run(frozen, output, stub=True, evidence_dir=tmp_path, budget=budget)
    assert not output.exists() and not output.with_suffix(".run").exists()


def test_live_requires_exported_key_without_dotenv_or_client(frozen, tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(ex, "make_client", lambda **kwargs: pytest.fail("Client created"))
    monkeypatch.setattr(ex, "_load_api_key", lambda *args: pytest.fail("Dotenv read"))
    with pytest.raises(ValueError, match="Export MODEL_API_KEY"):
        spot500.run(frozen, tmp_path / "live.json", stub=False, evidence_dir=tmp_path, budget=.95)


def test_live_wiring_with_synthetic_backend_retains_errors_and_usage(frozen, tmp_path, monkeypatch):
    """Exercise the live branch with a fake client/store/result, never a provider SDK dispatch."""
    from receipts import check as checker
    from receipts import store as stores

    closed = []
    monkeypatch.setenv("MODEL_API_KEY", "SYNTHETIC_TEST_KEY")
    monkeypatch.setattr(ex, "_load_api_key", lambda *args: pytest.fail("Dotenv read"))
    monkeypatch.setattr(ex, "settings_env", lambda: pytest.fail("Dotenv settings read"))
    monkeypatch.setattr(spot500, "evidence_hashes", lambda path: {"facts.sqlite": "synthetic"})
    monkeypatch.setattr(spot500.harness, "install_sdk_hooks", lambda: True)
    monkeypatch.setattr(stores, "Store", lambda db, pr: SimpleNamespace(close=lambda: closed.append("store")))

    def fake_client(**kwargs):
        assert kwargs["api_key"] == "SYNTHETIC_TEST_KEY"
        assert kwargs["backend"] == ex.make_backend("meta")
        return SimpleNamespace(close=lambda: closed.append("client"))
    monkeypatch.setattr(ex, "make_client", fake_client)
    rows = {row["text"]: row for row in frozen}

    def fake_check(text, *, context, backend, cache_dir, extra_cache_dirs, concurrency, **kwargs):
        assert extra_cache_dirs == () and concurrency == 1
        assert cache_dir.parent == tmp_path / "synthetic.run"
        request = ex.build_request(text, context, backend=backend)
        ex.RequestUsage(backend).call(lambda: SimpleNamespace(usage={"input_tokens": 100, "output_tokens": 50}), request)
        if rows[text]["item_id"] == "original":
            raise RuntimeError("SECRET_PROVIDER_PAYLOAD")
        return SimpleNamespace(results=spot500.oracle_pairs(rows[text]),
                               extraction=SimpleNamespace(calls=[]), warnings=[], coverage={})
    monkeypatch.setattr(checker, "check_detailed", fake_check)
    output = tmp_path / "synthetic.json"
    result = spot500.run(frozen, output, stub=False, evidence_dir=tmp_path, budget=.95)
    assert closed == ["store", "client"]
    assert result["summary"]["total"]["errors"] == 1
    assert len(result["records"]) == result["metering"]["calls"] == 3
    assert result["cost_usd"] == pytest.approx(3 * .0003375)
    assert not result["cost_is_estimate"]
    assert "SECRET_PROVIDER_PAYLOAD" not in output.read_text()
    assert "SYNTHETIC_TEST_KEY" not in output.read_text()
    assert len(output.with_suffix(".run").joinpath("records.jsonl").read_text().splitlines()) == 3


def test_load_rejects_changed_corpus_or_item_order(frozen):
    original = spot500.CORPUS.read_bytes()
    spot500.CORPUS.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="corpus hash"):
        spot500.load_items()
    spot500.CORPUS.write_bytes(original)
    manifest = json.loads(spot500.MANIFEST.read_text())
    manifest["items"].reverse()
    spot500.MANIFEST.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="order or hashes"):
        spot500.load_items()


def test_missing_results_stay_in_denominators(frozen):
    total = spot500.score(frozen, {})["total"]
    assert total["items"] == total["missing_records"] == total["missing_claims"] == 3
    assert total["target_claims"] == total["true_claims"] == total["guidance_claims"] == 1
    assert total["caught_errors"] == total["expected_reasons"] == 0


def test_equal_value_at_other_occurrence_cannot_match():
    row = item("repeated")
    pair = spot500.oracle_pairs(row)[0]
    pair[0].update(span_start=0, span_end=1)
    total = spot500.score([row], {row["item_id"]: {"pairs": [pair]}})["total"]
    assert total["missing_claims"] == total["extra_claims"] == 1
    assert total["status_correct"] == 0


def test_score_exposes_false_alarm_abstention_wrong_reason_and_guidance(frozen):
    records = {row["item_id"]: {"pairs": spot500.oracle_pairs(row)} for row in frozen}
    records["original"]["pairs"][0][1].update(status="unverifiable", reason="no_evidence")
    extra = deepcopy(records["original"]["pairs"][0])
    extra[0].update(value_text="$9 billion")
    extra[1].update(status="wrong", reason="value_mismatch")
    records["original"]["pairs"].append(extra)
    records["mutant"]["pairs"][0][1].update(reason="period_mismatch")
    records["guidance"]["pairs"][0][1].update(status="verified", reason="match")
    result = spot500.score(frozen, records)
    total = result["total"]
    assert total["false_alarm_original_items"] == total["original_true_unverifiable"] == 1
    assert total["unverifiable_true_claims"] == total["extra_wrong"] == 1
    assert total["caught_errors"] == 1 and total["expected_reasons"] == 0
    assert total["verified_guidance"] == total["verified_guidance_items"] == 1
    assert result["by_mutator"]["value"]["expected_reasons"] == 0


def test_contradictory_duplicate_is_not_counted_as_caught(frozen):
    row = frozen[1]
    pairs = spot500.oracle_pairs(row)
    other = deepcopy(pairs[0])
    other[1].update(status="verified", reason="match")
    pairs.append(other)
    total = spot500.score([row], {row["item_id"]: {"pairs": pairs}})["total"]
    assert total["caught_errors"] == total["expected_reasons"] == 0
    assert total["target_verified"] == total["conflicting_matches"] == total["extra_claims"] == 1


def test_guidance_verified_with_wrong_basis_still_counts(frozen):
    row = frozen[2]
    pairs = spot500.oracle_pairs(row)
    pairs[0][0]["basis"] = "non-GAAP"
    pairs[0][1].update(status="verified", reason="match")
    total = spot500.score([row], {row["item_id"]: {"pairs": pairs}})["total"]
    assert total["verified_guidance"] == total["missing_claims"] == 1


def setup_guard(tmp_path, *, cap=.95):
    backend = ex.make_backend("meta")
    request = ex.build_request("Example revenue was $2 billion.", "Example Q2 FY2026", backend=backend)
    guard = spot500.spark_guard(tmp_path / "attempts.json", backend, {ex.request_key(request, backend)}, cap)
    return backend, request, guard


def test_guard_records_usage_and_allows_only_one_attempt_per_request(tmp_path):
    backend, request, guard = setup_guard(tmp_path)
    original = ex.RequestUsage.call
    with guard.installed():
        ex.RequestUsage(backend).call(lambda: SimpleNamespace(usage={"input_tokens": 100, "output_tokens": 50}), request)
        with pytest.raises(ex.ModelCallRejected):
            ex.RequestUsage(backend).call(lambda: pytest.fail("Repeated dispatch"), request)
    assert ex.RequestUsage.call is original
    assert guard.summary()["calls"] == 1 and guard.summary()["estimated_calls"] == 0
    assert guard.summary()["charge_usd"] == pytest.approx(.0003375)
    assert json.loads(guard.path.read_text()) == guard.summary()


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": -1, "output_tokens": 3}])
def test_guard_retains_unknown_usage_reservation(tmp_path, usage):
    backend, request, guard = setup_guard(tmp_path)
    with guard.installed():
        ex.RequestUsage(backend).call(lambda: SimpleNamespace(usage=usage), request)
    assert guard.summary()["estimated_calls"] == 1
    assert guard.summary()["charge_usd"] == ex.estimate_request_cost(request, backend)


def test_guard_failure_retains_reservation_without_provider_payload(tmp_path):
    backend, request, guard = setup_guard(tmp_path)
    def fail():
        raise RuntimeError("SECRET_PROVIDER_PAYLOAD")
    with guard.installed(), pytest.raises(RuntimeError):
        ex.RequestUsage(backend).call(fail, request)
    assert guard.summary()["estimated_calls"] == 1
    assert "SECRET" not in guard.path.read_text()


@pytest.mark.parametrize("change", [{"model": "muse-spark-1.3-contributor"}, {"max_output_tokens": 8001},
                                   {"input": []}])
def test_changed_request_is_rejected_before_dispatch(tmp_path, change):
    backend, request, guard = setup_guard(tmp_path)
    with guard.installed(), pytest.raises(ex.ModelCallRejected):
        ex.RequestUsage(backend).call(lambda: pytest.fail("Unapproved dispatch"), {**request, **change})
    assert guard.summary()["calls"] == 0


def test_guard_budget_rejects_before_dispatch_and_ledger_cannot_be_reused(tmp_path):
    backend, request, guard = setup_guard(tmp_path, cap=.01)
    with guard.installed(), pytest.raises(ex.ModelCallRejected):
        ex.RequestUsage(backend).call(lambda: pytest.fail("Over-budget dispatch"), request)
    assert guard.summary()["calls"] == 0
    with pytest.raises(FileExistsError):
        setup_guard(tmp_path)


def test_failed_ledger_persistence_prevents_dispatch(tmp_path, monkeypatch):
    backend, request, guard = setup_guard(tmp_path)
    monkeypatch.setattr(guard, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        guard.call(ex.RequestUsage(backend), lambda: pytest.fail("Unlogged dispatch"), request)


def test_look_ahead_keeps_current_api_status_and_reason():
    row = item("cutoff", role="target", mutator="look_ahead", status="wrong", reason="look_ahead")
    result = spot500.score([row], {row["item_id"]: {"pairs": spot500.oracle_pairs(row)}})
    assert result["total"]["caught_errors"] == result["total"]["expected_reasons"] == 1
