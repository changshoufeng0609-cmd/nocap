"""Durable, conservative spend admission, not a provider-billing guarantee.

Reservations are charged before work starts. Unknown usage retains at least that
charge; a crash never refunds it. Each token belongs to its UTC admission day.
SQLite coordinates processes sharing one file, not separate Vercel instances.
PostgreSQL requires an explicitly initialized shared database; it never falls back.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, ROUND_CEILING, ROUND_FLOOR
import importlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Callable, Iterator, Any
from urllib.parse import urlsplit
from uuid import uuid4


SAFE_ERROR = "Spend tracking is unavailable. Paid operations are temporarily disabled."
MAX_MICROS = 2**53 - 1


class BudgetUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(SAFE_ERROR)


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _day(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise BudgetUnavailable()
    try:
        date.fromisoformat(value)
    except ValueError:
        raise BudgetUnavailable() from None
    return value


def _micros(value: object, *, positive: bool = False, rounding: str = ROUND_CEILING) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise BudgetUnavailable()
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount < 0 or (positive and amount == 0)
                or amount > Decimal(MAX_MICROS) / 1_000_000):
            raise BudgetUnavailable()
        result = int((amount * 1_000_000).to_integral_value(rounding=rounding))
    except (DecimalException, ValueError, OverflowError):
        raise BudgetUnavailable() from None
    if result > MAX_MICROS:
        raise BudgetUnavailable()
    return result


def _stored_int(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MICROS:
        raise BudgetUnavailable()
    return value


@dataclass(frozen=True)
class Reservation:
    operation_id: str
    namespace: str
    day: str
    estimate_micros: int
    cap_micros: int


# The same constraints work in SQLite and PostgreSQL. PostgreSQL initialization is
# deliberately separate from construction and runtime operations.
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS nocap_spend_days (
        namespace TEXT NOT NULL,
        day TEXT NOT NULL,
        cap_micros BIGINT NOT NULL CHECK (cap_micros >= 0),
        charged_micros BIGINT NOT NULL CHECK (charged_micros >= 0),
        PRIMARY KEY (namespace, day)
    )""",
    """CREATE TABLE IF NOT EXISTS nocap_spend_reservations (
        namespace TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        day TEXT NOT NULL,
        estimate_micros BIGINT NOT NULL CHECK (estimate_micros > 0),
        actual_micros BIGINT,
        uncertain INTEGER CHECK (uncertain IN (0, 1)),
        state TEXT NOT NULL CHECK (state IN ('pending', 'settled')),
        PRIMARY KEY (namespace, operation_id),
        FOREIGN KEY (namespace, day) REFERENCES nocap_spend_days(namespace, day),
        CHECK ((state = 'pending' AND actual_micros IS NULL AND uncertain IS NULL)
            OR (state = 'settled' AND actual_micros IS NOT NULL AND actual_micros >= 0 AND uncertain IS NOT NULL))
    )""",
    """CREATE TABLE IF NOT EXISTS nocap_spend_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
)
POSTGRES_SCHEMA = ";\n\n".join(SCHEMA_STATEMENTS) + ";\n"


class _Ledger:
    postgres = False

    def __init__(self, cap_usd: float, *, namespace: str = "nocap",
                 today: Callable[[], str] = utc_day):
        self._cap = _micros(cap_usd, rounding=ROUND_FLOOR)
        self.cap_usd = self._cap / 1_000_000
        if not isinstance(namespace, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", namespace):
            raise BudgetUnavailable()
        self.namespace, self._today = namespace, today

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        raise NotImplementedError
        yield  # pragma: no cover

    def _execute(self, connection: Any, sql: str, args: tuple = ()) -> Any:
        return connection.execute(sql.replace("?", "%s") if self.postgres else sql, args)

    def _budget(self, connection: Any, day: str, *, lock: bool = True) -> tuple[int, int] | None:
        row = self._execute(connection,
            "SELECT cap_micros, charged_micros FROM nocap_spend_days WHERE namespace=? AND day=?"
            + (" FOR UPDATE" if self.postgres and lock else ""), (self.namespace, day)).fetchone()
        if row is None:
            return None
        cap, charged = (_stored_int(row[0]), _stored_int(row[1]))
        if cap != self._cap:
            raise BudgetUnavailable()
        return cap, charged

    def _ensure_day(self, connection: Any, day: str) -> tuple[int, int]:
        self._execute(connection,
            "INSERT INTO nocap_spend_days(namespace, day, cap_micros, charged_micros) "
            "VALUES (?, ?, ?, 0) ON CONFLICT(namespace, day) DO NOTHING",
            (self.namespace, day, self._cap))
        row = self._budget(connection, day)
        if row is None:
            raise BudgetUnavailable()
        return row

    def reserve(self, estimate_usd: float) -> Reservation | None:
        """Durably debit before dispatch. None means the daily allowance is insufficient."""
        estimate, day = _micros(estimate_usd, positive=True), _day(self._today())
        token = Reservation(uuid4().hex, self.namespace, day, estimate, self._cap)
        try:
            with self._transaction() as connection:
                cap, charged = self._ensure_day(connection, day)
                if estimate > cap - charged:
                    return None
                self._execute(connection,
                    "UPDATE nocap_spend_days SET charged_micros=? WHERE namespace=? AND day=?",
                    (charged + estimate, self.namespace, day))
                self._execute(connection,
                    "INSERT INTO nocap_spend_reservations(namespace, operation_id, day, estimate_micros, state) "
                    "VALUES (?, ?, ?, ?, 'pending')", (self.namespace, token.operation_id, day, estimate))
            return token
        except Exception:
            # The commit may have succeeded even if its acknowledgement was lost.
            # Never retry with a new token or authorize work on an ambiguous result.
            raise BudgetUnavailable() from None

    def settle(self, token: Reservation, actual_usd: float, *, uncertain: bool = False) -> None:
        """Refund only complete known usage; duplicate identical settlements are no-ops."""
        actual = _micros(actual_usd)
        if (not isinstance(token, Reservation) or type(uncertain) is not bool
                or token.namespace != self.namespace or token.cap_micros != self._cap
                or type(token.cap_micros) is not int or not isinstance(token.operation_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", token.operation_id)):
            raise BudgetUnavailable()
        _day(token.day)
        _stored_int(token.estimate_micros)
        if token.estimate_micros == 0:
            raise BudgetUnavailable()
        try:
            with self._transaction() as connection:
                budget = self._budget(connection, token.day)
                if budget is None:
                    raise BudgetUnavailable()
                row = self._execute(connection,
                    "SELECT day, estimate_micros, actual_micros, uncertain, state "
                    "FROM nocap_spend_reservations WHERE namespace=? AND operation_id=?",
                    (self.namespace, token.operation_id)).fetchone()
                if row is None or row[0] != token.day or _stored_int(row[1]) != token.estimate_micros:
                    raise BudgetUnavailable()
                if row[4] == "settled":
                    if _stored_int(row[2]) != actual or type(row[3]) is not int or row[3] != int(uncertain):
                        raise BudgetUnavailable()
                    return
                if row[4] != "pending" or row[2] is not None or row[3] is not None:
                    raise BudgetUnavailable()
                final = max(actual, token.estimate_micros) if uncertain else actual
                charged = budget[1] - token.estimate_micros + final
                _stored_int(charged)
                self._execute(connection,
                    "UPDATE nocap_spend_days SET charged_micros=? WHERE namespace=? AND day=?",
                    (charged, self.namespace, token.day))
                self._execute(connection,
                    "UPDATE nocap_spend_reservations SET actual_micros=?, uncertain=?, state='settled' "
                    "WHERE namespace=? AND operation_id=?",
                    (actual, int(uncertain), self.namespace, token.operation_id))
        except Exception:
            raise BudgetUnavailable() from None

    def spent_today(self) -> float:
        """Admission charge includes pending and conservatively settled reservations."""
        try:
            day = _day(self._today())
            with self._transaction() as connection:
                budget = self._budget(connection, day, lock=False)
                return (budget[1] if budget else 0) / 1_000_000
        except Exception:
            raise BudgetUnavailable() from None

    def pending_count(self) -> int:
        """Pending reservations for this namespace, across all admission days."""
        try:
            with self._transaction() as connection:
                row = self._execute(connection,
                    "SELECT COUNT(*) FROM nocap_spend_reservations WHERE namespace=? AND state='pending'",
                    (self.namespace,)).fetchone()
                return _stored_int(row[0])
        except Exception:
            raise BudgetUnavailable() from None


class LocalLedger(_Ledger):
    """SQLite transactions coordinate local processes that share the same durable file."""

    def __init__(self, cap_usd: float, path: Path | str | None, *, namespace: str = "nocap",
                 today: Callable[[], str] = utc_day, legacy_path: Path | str | None = None):
        super().__init__(cap_usd, namespace=namespace, today=today)
        try:
            self.path = Path(path) if path is not None else None
            self._memory: sqlite3.Connection | None = None
            self._memory_lock = threading.RLock()
            if str(path) == ":memory:":
                raise BudgetUnavailable()
            if self.path is None:
                # Explicit test/local-only mode: no restart or multi-process guarantee.
                if legacy_path is not None:
                    raise BudgetUnavailable()
                self._memory = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
                is_new = True
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                is_new = not self.path.exists()
            with self._transaction() as connection:
                if is_new:
                    for statement in SCHEMA_STATEMENTS:
                        connection.execute(statement)
                # Existing empty, malformed or incompatible files must not reset spend.
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise BudgetUnavailable()
                connection.execute("SELECT namespace, day, cap_micros, charged_micros FROM nocap_spend_days LIMIT 0")
                connection.execute("SELECT namespace, operation_id, day, estimate_micros, actual_micros, uncertain, "
                                   "state FROM nocap_spend_reservations LIMIT 0")
                connection.execute("SELECT key, value FROM nocap_spend_meta LIMIT 0")
            if legacy_path is not None:
                with self._transaction() as connection:
                    self._import_legacy(connection, Path(legacy_path))
        except Exception:
            raise BudgetUnavailable() from None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._memory_lock:
            connection = self._memory or sqlite3.connect(self.path, timeout=5, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                if self._memory is None:
                    connection.close()

    def _import_legacy(self, connection: sqlite3.Connection, path: Path) -> None:
        marker = f"legacy_imported:{self.namespace}"
        if connection.execute("SELECT value FROM nocap_spend_meta WHERE key=?", (marker,)).fetchone():
            return
        status = "absent"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise BudgetUnavailable()
            day, amount = _day(record.get("day")), _micros(record.get("spent_usd"))
            _, charged = self._ensure_day(connection, day)
            total = _stored_int(charged + amount)
            connection.execute("UPDATE nocap_spend_days SET charged_micros=? WHERE namespace=? AND day=?",
                               (total, self.namespace, day))
            status = "imported"
        connection.execute("INSERT INTO nocap_spend_meta(key, value) VALUES (?, ?)", (marker, status))


class PostgresLedger(_Ledger):
    """Optional shared ledger. Schema provisioning is a separate, approved operation."""

    postgres = True

    def __init__(self, cap_usd: float, database_url: str, *, namespace: str = "nocap",
                 today: Callable[[], str] = utc_day):
        super().__init__(cap_usd, namespace=namespace, today=today)
        try:
            parsed = urlsplit(database_url)
            if (not isinstance(database_url, str) or parsed.scheme not in ("postgres", "postgresql")
                    or not parsed.hostname or parsed.fragment):
                raise ValueError
            # Validate malformed ports without exposing a URL in an exception.
            parsed.port
            self._database_url = database_url
        except Exception:
            raise BudgetUnavailable() from None

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        # Lazy import keeps cached examples usable when the optional driver is absent.
        driver = importlib.import_module("psycopg")
        with driver.connect(self._database_url, connect_timeout=5, autocommit=True,
                            sslmode="require") as connection:
            with connection.transaction():
                # Bound lock waits and SQL execution; failure retains prior debits.
                connection.execute("SET LOCAL lock_timeout = '5s'")
                connection.execute("SET LOCAL statement_timeout = '10s'")
                yield connection
