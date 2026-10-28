#!/usr/bin/env python3
"""Validate an initialized, explicitly approved PostgreSQL ledger without model calls.

Default: print the plan without reading credentials or connecting. --run writes
synthetic records only under fresh random validation namespaces. Audit rows are
retained; the application namespace and schema are never changed by this script.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from receipts.spend import BudgetUnavailable, PostgresLedger, Reservation  # noqa: E402


ENV_NAME = "RECEIPTS_SPEND_DATABASE_URL"
CAP_USD = .10
CRASH_EXIT = 23
CHECKS = (
    "process_last_allowance_race", "concurrent_duplicate_settlement",
    "row_lock_release", "row_lock_timeout_rollback", "unknown_usage_retention",
    "injected_utc_rollover", "committed_reservation_process_crash",
    "connection_failure_closed",
)


class ValidationFailure(Exception):
    """Only a script-owned, credential-free reason code crosses this boundary."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, "Invalid arguments. Use --help; connection strings belong only in the environment.\n")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValidationFailure(code)


def database_url() -> str:
    value = os.environ.get(ENV_NAME, "").strip()
    if not value:
        raise ValidationFailure("database_url_missing")
    return value


def connection_url(*, application_name: str, missing_database: str | None = None) -> str:
    parts = urlsplit(database_url())
    replaced = {"application_name"} | ({"dbname"} if missing_database else set())
    # libpq URI queries are not HTML forms: '+' is literal, not a space. Preserve
    # every untouched encoded value byte-for-byte, including credentials/options.
    query = [segment for segment in parts.query.split("&") if segment and
             unquote(segment.split("=", 1)[0]) not in replaced]
    query.append("application_name=" + quote(application_name, safe=""))
    return urlunsplit((parts.scheme, parts.netloc,
                       "/" + quote(missing_database, safe="") if missing_database else parts.path,
                       "&".join(query), parts.fragment))


def ledger(namespace: str, day: str) -> PostgresLedger:
    return PostgresLedger(CAP_USD, connection_url(application_name=namespace),
                          namespace=namespace, today=lambda: day)


class ObservedLedger(PostgresLedger):
    """Observe the backend PID inside the adapter's actual transaction, including poolers."""

    def __init__(self, *args: Any, channel: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.channel = channel
        self.failure_sqlstate: str | None = None

    @contextmanager
    def _transaction(self):
        try:
            with super()._transaction() as connection:
                pid = int(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
                self.channel.send({"event": "session", "backend_pid": pid})
                yield connection
        except Exception as error:
            # Record only the standardized five-character code before reserve()
            # replaces the driver error with its safe BudgetUnavailable exception.
            state = getattr(error, "sqlstate", None)
            self.failure_sqlstate = state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else None
            raise


def worker(action: str, namespace: str, day: str, barrier: Any, channel: Any,
           token_data: dict[str, Any] | None = None) -> None:
    """Spawn target: never send a DSN, provider data, or exception text through IPC."""
    logging.disable(logging.CRITICAL)
    try:
        client = ObservedLedger(CAP_USD, connection_url(application_name=namespace + "_worker"),
                                namespace=namespace, today=lambda: day, channel=channel)
        channel.send({"event": "ready", "process_pid": os.getpid()})
        barrier.wait(30)
        if action == "settle":
            client.settle(Reservation(**token_data), .015)
            result = {"event": "result", "settled": True}
        elif action == "lock_timeout":
            try:
                token = client.reserve(.02)
            except BudgetUnavailable:
                result = {"event": "result", "unavailable": True, "sqlstate": client.failure_sqlstate}
            else:
                result = {"event": "result", "unavailable": False, "accepted": token is not None}
        else:
            token = client.reserve(.02 if action == "lock" else .06)
            result = {"event": "result", "accepted": token is not None,
                      "token": asdict(token) if token is not None else None}
        channel.send(result)  # Pipe.send is synchronous; no Queue feeder is lost at os._exit.
        if action == "crash":
            os._exit(CRASH_EXIT)
    except BaseException:
        try:
            channel.send({"event": "failure", "reason": "worker_operation_failed"})
        except BaseException:
            pass
        raise SystemExit(2) from None
    finally:
        channel.close()


def message(channel: Any, timeout: float = 30) -> dict[str, Any]:
    if not channel.poll(timeout):
        raise ValidationFailure("worker_timeout")
    try:
        item = channel.recv()
    except (EOFError, OSError):
        raise ValidationFailure("worker_channel_closed") from None
    require(isinstance(item, dict) and item.get("event") != "failure", "worker_operation_failed")
    return item


class Workers:
    def __init__(self, action: str, namespace: str, day: str, count: int = 1,
                 token: Reservation | None = None):
        self.context = multiprocessing.get_context("spawn")
        self.barrier = self.context.Barrier(count + 1)
        self.processes, self.channels = [], []
        self.process_ids: set[int] = set()
        self.backend_ids: set[int] = set()
        self.action, self.namespace, self.day, self.count, self.token = action, namespace, day, count, token

    def __enter__(self):
        try:
            for _ in range(self.count):
                receive, send = self.context.Pipe(duplex=False)
                process = self.context.Process(target=worker, args=(
                    self.action, self.namespace, self.day, self.barrier, send,
                    asdict(self.token) if self.token is not None else None))
                self.channels.append(receive)
                self.processes.append(process)
                try:
                    process.start()
                finally:
                    send.close()
            for channel in self.channels:
                ready = message(channel)
                require(ready.get("event") == "ready", "worker_not_ready")
                self.process_ids.add(ready["process_pid"])
            require(len(self.process_ids) == self.count, "workers_not_independent")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def start(self) -> None:
        self.barrier.wait(30)

    def session(self, index: int = 0) -> int:
        item = message(self.channels[index])
        require(item.get("event") == "session", "missing_transaction_session")
        self.backend_ids.add(item["backend_pid"])
        return item["backend_pid"]

    def result(self, index: int = 0) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while True:
            item = message(self.channels[index], max(0, deadline - time.monotonic()))
            if item.get("event") == "session":
                self.backend_ids.add(item["backend_pid"])
                continue
            require(item.get("event") == "result", "invalid_worker_result")
            return item

    def joined(self, expected_exit: int = 0) -> None:
        for process in self.processes:
            process.join(5)
            require(process.exitcode == expected_exit, "unexpected_worker_exit")

    def __exit__(self, *args: Any) -> None:
        # Terminate only this validation run's own temporary processes on failure.
        for process in self.processes:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
        for channel in self.channels:
            channel.close()


def audit(namespace: str, day: str) -> dict[str, Any]:
    """Fresh transaction: exact integer conservation, not a float-only spend check."""
    with ledger(namespace, day)._transaction() as connection:
        days = connection.execute(
            "SELECT day, charged_micros FROM nocap_spend_days WHERE namespace=%s", (namespace,)).fetchall()
        rows = connection.execute(
            "SELECT day, estimate_micros, actual_micros, uncertain, state "
            "FROM nocap_spend_reservations WHERE namespace=%s", (namespace,)).fetchall()
    charged, totals, pending = dict(days), {}, 0
    for admission_day, estimate, actual, uncertain, state in rows:
        require(type(estimate) is int and estimate > 0, "invalid_stored_estimate")
        if state == "pending":
            require(actual is None and uncertain is None, "invalid_pending_row")
            final = estimate
            pending += 1
        else:
            require(state == "settled" and type(actual) is int and actual >= 0 and uncertain in (0, 1),
                    "invalid_settled_row")
            final = max(estimate, actual) if uncertain else actual
        totals[admission_day] = totals.get(admission_day, 0) + final
    for admission_day in charged.keys() | totals.keys():
        require(type(charged.get(admission_day)) is int and
                charged[admission_day] == totals.get(admission_day, 0), "ledger_conservation_failed")
    return {"charged_micros_by_day": charged, "reservation_rows": len(rows),
            "pending_rows": pending, "settled_rows": len(rows) - pending, "integer_conservation": True}


def race(namespace: str, day: str, count: int) -> dict[str, Any]:
    original = ledger(namespace, day)
    seed = original.reserve(.04)
    require(seed is not None, "seed_reservation_rejected")
    original.settle(seed, .04)
    with Workers("reserve", namespace, day, count) as workers:
        workers.start()
        results = [workers.result(i) for i in range(count)]
        workers.joined()
        require(sum(r["accepted"] for r in results) == 1, "last_allowance_admitted_multiple_or_none")
        details = {"independent_processes": len(workers.process_ids), "accepted": 1, "denied": count - 1}
    state = audit(namespace, day)
    require(state["charged_micros_by_day"][day] == 100_000 and state["pending_rows"] == 1,
            "last_allowance_balance_wrong")
    return {**details, **state}


def duplicate(namespace: str, day: str, _: int) -> dict[str, Any]:
    original = ledger(namespace, day)
    token = original.reserve(.09)
    require(token is not None, "seed_reservation_rejected")
    with Workers("settle", namespace, day, 2, token) as workers:
        workers.start()
        require(all(workers.result(i).get("settled") for i in range(2)), "duplicate_settlement_failed")
        workers.joined()
    state = audit(namespace, day)
    require(state["charged_micros_by_day"][day] == 15_000 and state["pending_rows"] == 0,
            "duplicate_settlement_changed_balance")
    return {"independent_processes": 2, **state}


def lock_case(namespace: str, day: str, *, expire: bool) -> dict[str, Any]:
    holder = ledger(namespace, day)
    seed = holder.reserve(.01)
    require(seed is not None, "seed_reservation_rejected")
    holder.settle(seed, .01)
    before = audit(namespace, day)
    with Workers("lock_timeout" if expire else "lock", namespace, day) as workers:
        with holder._transaction() as connection:
            holder_pid = int(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
            connection.execute("SELECT day FROM nocap_spend_days WHERE namespace=%s AND day=%s FOR UPDATE",
                               (namespace, day)).fetchone()
            workers.start()
            waiter_pid = workers.session()
            require(holder_pid != waiter_pid, "lock_clients_share_backend_session")
            observed = False
            until = time.monotonic() + 3
            while time.monotonic() < until:
                connection.execute("SELECT pg_stat_clear_snapshot()")
                row = connection.execute("SELECT wait_event_type, pg_blocking_pids(pid) "
                                         "FROM pg_stat_activity WHERE pid=%s", (waiter_pid,)).fetchone()
                if row and row[0] == "Lock" and holder_pid in row[1]:
                    observed = True
                    break
                time.sleep(.05)
            require(observed, "target_row_lock_not_observed")
            if expire:
                outcome = workers.result()
                require(outcome.get("unavailable") is True, "lock_timeout_did_not_fail_closed")
                require(outcome.get("sqlstate") == "55P03", "blocked_request_failed_without_lock_timeout")
        if not expire:
            outcome = workers.result()
            require(outcome.get("accepted") is True, "lock_release_did_not_resume_reservation")
        workers.joined()
    after = audit(namespace, day)
    if expire:
        require(after == before, "lock_timeout_transaction_changed_ledger")
    else:
        require(after["charged_micros_by_day"][day] == 30_000 and after["pending_rows"] == 1,
                "lock_release_balance_wrong")
    return {"distinct_pinned_sessions": 2, "target_blocker_observed": True,
            "adapter_lock_timeout_exercised": expire,
            "lock_timeout_sqlstate": "55P03" if expire else None, **after}


def unknown(namespace: str, day: str, _: int) -> dict[str, Any]:
    client = ledger(namespace, day)
    token = client.reserve(.07)
    require(token is not None, "seed_reservation_rejected")
    client.settle(token, .01, uncertain=True)
    ledger(namespace, day).settle(token, .01, uncertain=True)
    require(ledger(namespace, day).reserve(.04) is None, "unknown_usage_refunded_reservation")
    state = audit(namespace, day)
    require(state["charged_micros_by_day"][day] == 70_000 and state["pending_rows"] == 0,
            "unknown_usage_balance_wrong")
    return state


def rollover(namespace: str, day: str, _: int) -> dict[str, Any]:
    tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    old = ledger(namespace, day).reserve(.08)
    require(old is not None, "seed_reservation_rejected")
    next_day = ledger(namespace, tomorrow)
    require(next_day.spent_today() == 0, "prior_day_consumed_new_allowance")
    new = next_day.reserve(.02)
    require(new is not None, "new_day_reservation_rejected")
    next_day.settle(old, .03)
    state = audit(namespace, tomorrow)
    require(state["charged_micros_by_day"] == {day: 30_000, tomorrow: 20_000},
            "settlement_changed_wrong_admission_day")
    return {"clock_mode": "injected_dates_not_wall_clock_midnight", **state}


def crash(namespace: str, day: str, _: int) -> dict[str, Any]:
    with Workers("crash", namespace, day) as workers:
        workers.start()
        require(workers.result().get("accepted") is True, "crash_reservation_not_committed")
        workers.joined(CRASH_EXIT)
    restarted = ledger(namespace, day)
    require(restarted.reserve(.05) is None, "crash_reservation_was_refunded")
    state = audit(namespace, day)
    require(state["charged_micros_by_day"][day] == 60_000 and state["pending_rows"] == 1,
            "crash_reservation_not_retained")
    return {"crash_point": "process_exit_after_committed_reservation", **state}


def connection_failure(namespace: str, day: str, _: int) -> dict[str, Any]:
    # Same approved host and credentials, but a random nonexistent database. No
    # database is created; dbname query overrides are removed by connection_url().
    missing = "nocap_missing_" + uuid4().hex
    broken = PostgresLedger(CAP_USD, connection_url(application_name=namespace, missing_database=missing),
                            namespace=namespace, today=lambda: day)
    try:
        broken.reserve(.01)
    except BudgetUnavailable:
        pass
    else:
        raise ValidationFailure("connection_failure_returned_allowance")
    state = audit(namespace, day)
    require(not state["reservation_rows"] and not state["charged_micros_by_day"],
            "failed_connection_changed_valid_database")
    return {"fault": "nonexistent_database_on_approved_host", "adapter_rejected": True,
            "api_fallback_tested": False, **state}


RUNNERS = (race, duplicate, lambda n, d, _: lock_case(n, d, expire=False),
           lambda n, d, _: lock_case(n, d, expire=True), unknown, rollover, crash, connection_failure)


def main() -> int:
    parser = SafeParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="write isolated validation rows to the approved database")
    parser.add_argument("--workers", type=int, choices=range(2, 9), default=4, help="independent race processes (2–8)")
    parser.add_argument("--output", type=Path, help="write the credential-free JSON report to this local path")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"status": "not_run", "checks": CHECKS, "model_calls": 0,
                          "requires": "approved existing PostgreSQL schema and RECEIPTS_SPEND_DATABASE_URL"}, indent=2))
        return 0
    logging.disable(logging.CRITICAL)
    report: dict[str, Any] = {"status": "failed", "started_utc": datetime.now(timezone.utc).isoformat(),
        "namespace_prefix": "nocap_validation_" + uuid4().hex, "checks": [], "model_calls": 0,
        "amounts": "synthetic ledger amounts, not provider charges", "audit_rows_retained": True,
        "limitations": ["No model or API dispatch is exercised.",
                        "No mid-commit network-loss or database-failover fault is injected."]}
    exit_code = 1
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        database_url()
        day = datetime.now(timezone.utc).date().isoformat()
        # Verify real connectivity, TLS and the initialized schema without writes.
        audit(report["namespace_prefix"] + "_preflight", day)
        for name, run in zip(CHECKS, RUNNERS):
            namespace = report["namespace_prefix"] + "_" + name
            # Names remain within the adapter's 100-character namespace limit.
            namespace = namespace[:100]
            check: dict[str, Any] = {"name": name, "namespace": namespace}
            started = time.monotonic()
            try:
                check.update(status="passed", **run(namespace, day, args.workers))
            except ValidationFailure as error:
                check.update(status="failed", reason=str(error))
            except Exception as error:
                check.update(status="failed", reason="database_or_validation_failure", error_type=type(error).__name__)
            check["elapsed_s"] = round(time.monotonic() - started, 3)
            report["checks"].append(check)
            print(json.dumps({"check": name, "status": check["status"]}), file=sys.stderr, flush=True)
        if all(c["status"] == "passed" for c in report["checks"]):
            report["status"], exit_code = "passed", 0
    except ValidationFailure as error:
        report.update(status="blocked", reason=str(error))
        exit_code = 2
    except KeyboardInterrupt:
        report.update(status="interrupted", reason="operator_interrupted")
        exit_code = 130
    except Exception as error:
        report.update(status="blocked", reason="database_configuration_schema_or_access_unavailable",
                      error_type=type(error).__name__)
        exit_code = 2
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        except OSError:
            print("Could not write the local validation report.", file=sys.stderr)
            exit_code = 2
    print(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
