"""No network/model calls. PostgreSQL tests verify fail-closed behavior, not a live server."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from receipts import spend


DAY = "2026-10-28"


def local(tmp_path, cap=1.0, **kwargs):
    return spend.LocalLedger(cap, tmp_path / "spend.sqlite3", today=lambda: DAY, **kwargs)


def _process_reserve(path, output, start):
    ledger = spend.LocalLedger(1, path, today=lambda: DAY)
    start.wait(10)
    token = ledger.reserve(0.6)
    output.put(token is not None)


def _crash_after_reserve(path):
    import os
    ledger = spend.LocalLedger(1, path, today=lambda: DAY)
    ledger.reserve(0.75)
    os._exit(0)


def test_pending_is_durable_and_blocks_after_restart(tmp_path):
    first = local(tmp_path)
    token = first.reserve(0.7)
    assert token is not None
    restarted = local(tmp_path)
    assert restarted.spent_today() == pytest.approx(0.7)
    assert restarted.reserve(0.4) is None
    restarted.settle(token, 0.2)
    assert first.spent_today() == pytest.approx(0.2)


def test_explicit_memory_mode_and_pending_diagnostics():
    day = [DAY]
    ledger = spend.LocalLedger(1, None, today=lambda: day[0])
    first = ledger.reserve(0.7)
    assert ledger.pending_count() == 1
    day[0] = "2026-10-29"
    second = ledger.reserve(0.4)
    assert ledger.pending_count() == 2  # Includes the prior day's pending work.
    ledger.settle(first, 0.1)
    assert ledger.pending_count() == 1
    ledger.settle(second, 0.2)
    assert ledger.pending_count() == 0
    assert ledger.spent_today() == pytest.approx(0.2)


def test_sub_micro_cap_is_rounded_down():
    ledger = spend.LocalLedger(0.0000009, None)
    assert ledger.reserve(0.0000001) is None


def test_actual_process_exit_keeps_precharge(tmp_path):
    ledger = local(tmp_path)
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_reserve, args=(str(ledger.path),))
    process.start()
    process.join(15)
    assert process.exitcode == 0
    assert local(tmp_path).spent_today() == pytest.approx(0.75)
    assert local(tmp_path).reserve(0.3) is None


def test_two_processes_cannot_both_take_last_allowance(tmp_path):
    ledger = local(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    output, start = ctx.Queue(), ctx.Event()
    workers = [ctx.Process(target=_process_reserve, args=(str(ledger.path), output, start)) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    results = [output.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert sorted(results) == [False, True]
    assert ledger.spent_today() == pytest.approx(0.6)


def test_many_independent_clients_cannot_overspend(tmp_path):
    local(tmp_path)
    def attempt(_):
        return local(tmp_path).reserve(0.1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(attempt, range(40)))
    assert sum(token is not None for token in tokens) == 10
    assert local(tmp_path).spent_today() == 1


@pytest.mark.parametrize("actual,uncertain,charged", [
    (0.1, False, 0.1), (0, False, 0), (0.1, True, 0.4), (0, True, 0.4),
    (0.8, False, 0.8), (0.8, True, 0.8), (1.2, False, 1.2),
])
def test_settlement_is_conservative_and_idempotent(tmp_path, actual, uncertain, charged):
    ledger = local(tmp_path)
    token = ledger.reserve(0.4)
    ledger.settle(token, actual, uncertain=uncertain)
    ledger.settle(token, actual, uncertain=uncertain)
    assert local(tmp_path).spent_today() == pytest.approx(charged)
    if charged > 1:
        assert ledger.reserve(0.01) is None


def test_concurrent_duplicate_settlements_only_refund_once(tmp_path):
    ledger = local(tmp_path)
    token = ledger.reserve(0.9)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: local(tmp_path).settle(token, 0.1), range(20)))
    assert ledger.spent_today() == pytest.approx(0.1)


def test_different_repeated_settlement_is_rejected(tmp_path):
    ledger = local(tmp_path)
    token = ledger.reserve(0.4)
    ledger.settle(token, 0, uncertain=True)
    with pytest.raises(spend.BudgetUnavailable, match="temporarily disabled"):
        ledger.settle(token, 0, uncertain=False)
    assert ledger.spent_today() == pytest.approx(0.4)


def test_midnight_settles_only_original_admission_day(tmp_path):
    day = [DAY]
    ledger = spend.LocalLedger(1, tmp_path / "spend.sqlite3", today=lambda: day[0])
    old = ledger.reserve(0.8)
    day[0] = "2026-10-29"
    assert ledger.spent_today() == 0
    new = ledger.reserve(0.7)
    ledger.settle(old, 0.2)
    assert ledger.spent_today() == pytest.approx(0.7)
    ledger.settle(new, 0.1)
    day[0] = DAY
    assert ledger.spent_today() == pytest.approx(0.2)
    ledger.settle(old, 0.2)
    assert ledger.spent_today() == pytest.approx(0.2)


def test_unknown_old_day_does_not_refund_new_day(tmp_path):
    day = [DAY]
    ledger = spend.LocalLedger(1, tmp_path / "spend.sqlite3", today=lambda: day[0])
    old = ledger.reserve(1)
    day[0] = "2026-10-29"
    ledger.settle(old, 0, uncertain=True)
    assert ledger.spent_today() == 0
    assert ledger.reserve(1) is not None
    day[0] = DAY
    assert ledger.spent_today() == 1
    assert ledger.reserve(0.01) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -0.01, True, False, "0.1", None, 10**20])
def test_invalid_amounts_fail_closed(tmp_path, bad):
    with pytest.raises(spend.BudgetUnavailable):
        local(tmp_path, cap=bad)
    ledger = local(tmp_path)
    token = ledger.reserve(0.4)
    with pytest.raises(spend.BudgetUnavailable):
        ledger.reserve(bad)
    with pytest.raises(spend.BudgetUnavailable):
        ledger.settle(token, bad)
    assert ledger.spent_today() == pytest.approx(0.4)


def test_zero_cap_and_rounding_cannot_enable_paid_work(tmp_path):
    ledger = local(tmp_path, cap=0)
    assert ledger.reserve(0.00000001) is None
    with pytest.raises(spend.BudgetUnavailable):
        ledger.reserve(0)
    second = spend.LocalLedger(0.000001, tmp_path / "second.sqlite3", today=lambda: DAY)
    assert second.reserve(0.00000001) is not None
    assert second.spent_today() == 0.000001
    assert second.reserve(0.00000001) is None


def test_cap_mismatch_fails_instead_of_silently_resetting(tmp_path):
    first = local(tmp_path, cap=1)
    token = first.reserve(0.4)
    second = local(tmp_path, cap=2)
    for action in (lambda: second.reserve(0.1), second.spent_today, lambda: second.settle(token, 0)):
        with pytest.raises(spend.BudgetUnavailable):
            action()
    assert first.spent_today() == pytest.approx(0.4)


def test_foreign_or_forged_tokens_cannot_refund(tmp_path):
    ledger = local(tmp_path)
    token = ledger.reserve(0.4)
    for forged in (None, replace(token, operation_id="0" * 32), replace(token, namespace="other"),
                   replace(token, day="2026-10-29"), replace(token, estimate_micros=1),
                   replace(token, cap_micros=True)):
        with pytest.raises(spend.BudgetUnavailable):
            ledger.settle(forged, 0)
    with pytest.raises(spend.BudgetUnavailable):
        ledger.settle(token, 0, uncertain=1)
    assert ledger.spent_today() == pytest.approx(0.4)


@pytest.mark.parametrize("contents", [b"", b"not sqlite", b"SQLite format 3\x00broken"])
def test_corrupt_or_empty_existing_store_is_not_reset(tmp_path, contents):
    path = tmp_path / "spend.sqlite3"
    path.write_bytes(contents)
    with pytest.raises(spend.BudgetUnavailable):
        local(tmp_path)
    assert path.read_bytes() == contents


def test_missing_schema_and_unwritable_location_fail_closed(tmp_path):
    path = tmp_path / "spend.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value INTEGER)")
    with pytest.raises(spend.BudgetUnavailable):
        local(tmp_path)
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(spend.BudgetUnavailable):
        spend.LocalLedger(1, blocker / "ledger.sqlite3")


def test_missing_or_corrupt_usage_store_after_construction_fails(tmp_path):
    ledger = local(tmp_path)
    ledger.path.write_bytes(b"damaged ledger")
    with pytest.raises(spend.BudgetUnavailable):
        ledger.reserve(0.1)
    with pytest.raises(spend.BudgetUnavailable):
        ledger.spent_today()


def test_legacy_import_is_atomic_and_only_once(tmp_path):
    legacy = tmp_path / "_spend.json"
    legacy.write_text(json.dumps({"day": DAY, "spent_usd": 0.6}))
    ledger = local(tmp_path, legacy_path=legacy)
    assert ledger.spent_today() == pytest.approx(0.6)
    assert ledger.reserve(0.5) is None
    legacy.write_text("damaged after successful migration")
    assert local(tmp_path, legacy_path=legacy).spent_today() == pytest.approx(0.6)


@pytest.mark.parametrize("payload", ["broken", "{}", '[1]', '{"day":"bad","spent_usd":1}',
                                       '{"day":"2026-10-28","spent_usd":NaN}',
                                       '{"day":"2026-10-28","spent_usd":true}',
                                       '{"day":"2026-10-28","spent_usd":-1}'])
def test_invalid_legacy_never_imports_zero_or_marks_done(tmp_path, payload):
    legacy = tmp_path / "_spend.json"
    legacy.write_text(payload)
    with pytest.raises(spend.BudgetUnavailable):
        local(tmp_path, legacy_path=legacy)
    with sqlite3.connect(tmp_path / "spend.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM nocap_spend_meta").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM nocap_spend_days").fetchone() == (0,)
    legacy.write_text(json.dumps({"day": DAY, "spent_usd": 0.7}))
    assert local(tmp_path, legacy_path=legacy).spent_today() == pytest.approx(0.7)


def test_legacy_old_day_preserved_and_above_cap_not_clamped(tmp_path):
    legacy = tmp_path / "_spend.json"
    legacy.write_text(json.dumps({"day": "2026-10-27", "spent_usd": 1.5}))
    ledger = local(tmp_path, legacy_path=legacy)
    assert ledger.spent_today() == 0
    prior = spend.LocalLedger(1, ledger.path, today=lambda: "2026-10-27")
    assert prior.spent_today() == 1.5
    assert prior.reserve(0.1) is None


def test_postgres_constructor_does_not_import_connect_or_create_schema(monkeypatch):
    def forbidden(_):
        raise AssertionError("driver must be lazy")
    monkeypatch.setattr(spend.importlib, "import_module", forbidden)
    ledger = spend.PostgresLedger(1, "postgresql://user:private-secret@example.invalid/nocap")
    for action in (lambda: ledger.reserve(0.1), ledger.spent_today):
        with pytest.raises(spend.BudgetUnavailable) as error:
            action()
        assert "private-secret" not in str(error.value)
        assert str(error.value) == spend.SAFE_ERROR


@pytest.mark.parametrize("url", ["", None, "https://user:secret@example.invalid/db", "postgresql:///db",
                                 "postgresql://user:secret@example.invalid:bad/db"])
def test_postgres_bad_config_is_safe(url):
    with pytest.raises(spend.BudgetUnavailable) as error:
        spend.PostgresLedger(1, url)
    assert "secret" not in str(error.value)


def test_postgres_connection_failure_never_uses_local_fallback(monkeypatch, tmp_path):
    def broken(*args, **kwargs):
        raise RuntimeError("password=private-secret connection refused")
    monkeypatch.setattr(spend.importlib, "import_module", lambda _: SimpleNamespace(connect=broken))
    ledger = spend.PostgresLedger(1, "postgresql://user:private-secret@example.invalid/db")
    for action in (lambda: ledger.reserve(0.1), ledger.spent_today):
        with pytest.raises(spend.BudgetUnavailable) as error:
            action()
        assert str(error.value) == spend.SAFE_ERROR
    assert list(tmp_path.iterdir()) == []


def test_init_script_defaults_to_reviewable_sql_without_driver_or_network():
    result = subprocess.run([sys.executable, "scripts/init_spend_db.py"], capture_output=True, text=True, check=True)
    assert result.stdout == spend.POSTGRES_SCHEMA
    assert "CREATE TABLE" in result.stdout
    assert result.stderr == ""
