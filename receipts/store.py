"""SQLite evidence store (implements receipts.types.EvidenceStore).

data/facts.sqlite holds companies, filings, XBRL facts, derived quarters and fiscal calendars. It is
built by `python -m receipts.ingest`. Press-release facts are not in SQLite: they are read at query
time from data/pr_facts/<TICKER>/<accession>.jsonl (one types.Fact JSON per line, written by the
press-release agent), so their output shows up without re-ingesting.

Query semantics (the verifier relies on these):
  * facts() serves quarterly, annual and instant facts. YTD (H1/9M) and trailing-twelve-month rows are
    stored for provenance but never returned, so a 9-month value can't be mistaken for a quarter.
  * Every version is returned (original filing, later comparatives, restatements, derived values),
    sorted by accepted_at. Each one carries its own accession and accepted_at.
  * as_of keeps only facts accepted at or before that instant. A naive datetime is treated as UTC.
  * basis=None means any basis. segment=None means consolidated facts only (segment IS NULL);
    segment="*" means any segment; any other value is matched case-insensitively.
  * A period_end that is a fiscal year end matches both the Q4 fact and the FY fact. Tell them apart
    with fiscal_period ("Q4" vs "FY") or period_start.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

from receipts.types import Basis, Fact, Metric

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "facts.sqlite"
PR_DIR = ROOT / "data" / "pr_facts"
SCHEMA_VERSION = "1"
ANY_SEGMENT = "*"


def default_data_dir(env: Optional[Mapping[str, str]] = None, *, root: Optional[Path] = None) -> Path:
    """Shared CLI/API evidence location; explicit overrides never silently use other data."""
    env = os.environ if env is None else env
    root = ROOT if root is None else root
    if value := (env.get("RECEIPTS_DATA_DIR") or "").strip():
        path = Path(value).expanduser()
        return path if path.is_absolute() else root / path
    data = root / "data"
    return data if (data / "facts.sqlite").exists() else data / "snapshot"


# --------------------------------------------------------------------------- metric mapping
@dataclass(frozen=True)
class MetricSpec:
    concepts: tuple[str, ...]  # us-gaap local names; per filing and period, the first one present wins
    xbrl_unit: str             # companyfacts unit key: "USD" or "USD/shares"
    additive: bool             # Q4 = FY - 9M and Q(n) = YTD(n) - YTD(n-1) are valid (never for EPS)
    instant: bool = False      # a balance at a date (no period_start)


# Canonical metric -> us-gaap concepts. This is the one table to extend.
# Order matters where a filing tags several concepts with different meanings: AMD tags both
# OperatingExpenses (opex) and CostsAndExpenses (total costs incl. cost of sales), so opex wins.
METRIC_CONCEPTS: dict[str, MetricSpec] = {
    "revenue": MetricSpec((
        "Revenues",                                              # NVDA, NFLX, BAC, GOOGL (since Q2 2025), JPM FY
        "RevenueFromContractWithCustomerExcludingAssessedTax",   # AAPL, MSFT, META, AMZN, AMD, GOOGL (before)
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",                          # JPM quarters: "Total net revenue"
    ), "USD", True),
    "cost_of_revenue": MetricSpec((
        "CostOfRevenue",                  # NVDA, META, GOOGL, NFLX
        "CostOfGoodsAndServicesSold",     # AAPL, MSFT, AMZN, AMD
    ), "USD", True),
    "gross_profit": MetricSpec(("GrossProfit",), "USD", True),
    "research_and_development": MetricSpec((
        "ResearchAndDevelopmentExpense",  # NFLX "Technology and development" uses this concept too
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ), "USD", True),
    "operating_expenses": MetricSpec((
        "OperatingExpenses",              # "Total operating expenses" (NVDA, AAPL, MSFT, AMD)
        "CostsAndExpenses",               # "Total costs and expenses" (META, GOOGL, AMZN)
        "NoninterestExpense",             # banks: "Total noninterest expense" (JPM, BAC)
    ), "USD", True),
    "operating_income": MetricSpec(("OperatingIncomeLoss",), "USD", True),
    "net_income": MetricSpec(("NetIncomeLoss",), "USD", True),
    "eps_basic": MetricSpec(("EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"), "USD/shares", False),
    "eps_diluted": MetricSpec(("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"), "USD/shares", False),
    "operating_cash_flow": MetricSpec((
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ), "USD", True),
    "net_interest_income": MetricSpec(("InterestIncomeExpenseNet",), "USD", True),
    "dividend_per_share": MetricSpec((
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",  # BAC tags its annual figure only as cash paid
    ), "USD/shares", True),
    "repurchase_authorization_remaining": MetricSpec(
        ("StockRepurchaseProgramRemainingAuthorizedRepurchaseAmount1",), "USD", False, instant=True),
}

FACT_UNIT = {"USD": "USD", "USD/shares": "USD/share"}  # companyfacts unit -> types.Unit


# --------------------------------------------------------------------------- entities
# Ticker -> aliases as written in drafts. SEC names and tickers are added at ingest. Subsidiary and
# product names (AWS, YouTube) are deliberately absent: "AWS revenue" resolved to Amazon would be
# checked against total revenue and wrongly flagged, while an unresolved entity is only "?".
ALIASES: dict[str, list[str]] = {
    "NVDA": ["NVIDIA", "Nvidia Corporation"],
    "AAPL": ["Apple", "Apple Inc."],
    "MSFT": ["Microsoft", "Microsoft Corporation"],
    "META": ["Meta", "Meta Platforms", "Meta Platforms, Inc.", "Facebook", "FB"],
    "AMZN": ["Amazon", "Amazon.com", "Amazon.com, Inc."],
    "GOOGL": ["Alphabet", "Alphabet Inc.", "Google", "GOOG"],
    "JPM": ["JPMorgan", "JPMorgan Chase", "JPMorgan Chase & Co.", "JP Morgan", "J.P. Morgan",
            "JP Morgan Chase", "J.P. Morgan Chase", "JPMC"],
    "BAC": ["Bank of America", "Bank of America Corporation", "BofA", "BoA", "BankAmerica"],
    "AMD": ["AMD", "Advanced Micro Devices", "Advanced Micro Devices, Inc."],
    "NFLX": ["Netflix", "Netflix, Inc."],
}

_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
             "llc", "de", "and", "the"}


def normalize_entity(name: str) -> str:
    """'JPMorgan Chase & Co.' -> 'jpmorgan chase'; "Nvidia's" -> 'nvidia'; 'Apple Inc. (AAPL)' -> 'apple'."""
    s = unicodedata.normalize("NFKC", name).lower().replace("’", "'")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"'s\b", "", s)
    s = s.replace("&", " and ").replace(".", "").replace("'", "")
    tokens = re.sub(r"[^0-9a-z]+", " ", s).split()
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# --------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    cik             INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    aliases         TEXT NOT NULL,   -- JSON list of normalize_entity() keys
    fiscal_year_end TEXT             -- MMDD from Submissions: NVDA 0131, AAPL 0926, MSFT 0630
);
CREATE TABLE IF NOT EXISTS filings (
    accession   TEXT PRIMARY KEY,
    cik         INTEGER NOT NULL,
    form        TEXT NOT NULL,
    filed       TEXT NOT NULL,       -- YYYY-MM-DD
    accepted_at TEXT NOT NULL,       -- UTC, YYYY-MM-DDTHH:MM:SSZ
    report_date TEXT,
    items       TEXT,
    primary_doc TEXT,
    url         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_filings_cik ON filings (cik, form, accepted_at);
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY,
    cik           INTEGER NOT NULL,
    ticker        TEXT NOT NULL,
    metric        TEXT NOT NULL,
    concept       TEXT,
    basis         TEXT NOT NULL,
    segment       TEXT,
    value         REAL NOT NULL,     -- full units (USD, USD/share)
    unit          TEXT NOT NULL,
    period_start  TEXT,
    period_end    TEXT NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period TEXT,
    source        TEXT NOT NULL CHECK (source IN ('xbrl', 'derived')),
    derived_from  TEXT NOT NULL,     -- JSON list of input accessions
    form          TEXT NOT NULL,
    accession     TEXT NOT NULL,
    accepted_at   TEXT NOT NULL,     -- UTC, YYYY-MM-DDTHH:MM:SSZ
    url           TEXT NOT NULL,
    locator       TEXT,
    decimals      INTEGER,
    period_kind   TEXT NOT NULL CHECK (period_kind IN ('quarter', 'annual', 'instant', 'ytd', 'ttm'))
);
CREATE INDEX IF NOT EXISTS ix_facts_lookup ON facts (cik, metric, period_end);
CREATE TABLE IF NOT EXISTS fiscal_periods (
    cik           INTEGER NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,     -- Q1..Q4 or FY
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    source        TEXT NOT NULL,     -- accession whose DEI labels the period; Q4 is Q3 end + 1 .. FY end
    PRIMARY KEY (cik, fiscal_year, fiscal_period)
);
"""
SERVED_KINDS = ("quarter", "annual", "instant")
_FACT_COLUMNS = ("cik", "ticker", "metric", "concept", "basis", "segment", "value", "unit", "period_start",
                 "period_end", "fiscal_year", "fiscal_period", "source", "derived_from", "form", "accession",
                 "accepted_at", "url", "locator", "decimals", "period_kind")


def iso_utc(dt: datetime) -> str:
    """Aware or naive (taken as UTC) datetime -> 'YYYY-MM-DDTHH:MM:SSZ', truncated to whole seconds."""
    return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def connect_rw(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Writable connection with the current schema (rebuilt from scratch on a version change)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() if has_meta else None
    if version is None or version[0] != SCHEMA_VERSION:
        with conn:
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    with conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    return conn


def fact_row(fact: Fact, period_kind: str) -> tuple:
    d = fact.model_dump()
    d["period_start"] = fact.period_start.isoformat() if fact.period_start else None
    d["period_end"] = fact.period_end.isoformat()
    d["accepted_at"] = iso_utc(fact.accepted_at)
    d["derived_from"] = json.dumps(fact.derived_from)
    d["period_kind"] = period_kind
    return tuple(d[c] for c in _FACT_COLUMNS)


def replace_company(conn: sqlite3.Connection, company: dict, filings: Iterable[dict],
                    periods: Iterable[tuple[int, str, date, date, str]],
                    facts: Iterable[tuple[Fact, str]]) -> None:
    """Atomically replace everything stored for one company (this is what makes ingest idempotent)."""
    cik = int(company["cik"])
    with conn:
        for table in ("facts", "filings", "fiscal_periods"):
            conn.execute(f"DELETE FROM {table} WHERE cik = ?", (cik,))
        conn.execute("DELETE FROM companies WHERE cik = ? OR ticker = ?", (cik, company["ticker"]))
        conn.execute("INSERT INTO companies VALUES (?, ?, ?, ?, ?)",
                     (cik, company["ticker"], company["name"], json.dumps(sorted(company["aliases"])),
                      company.get("fiscal_year_end")))
        conn.executemany("INSERT OR REPLACE INTO filings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            (f["accession"], cik, f["form"], f["filed"], f["accepted_at"], f["report_date"], f["items"],
             f["primary_doc"], f["url"]) for f in filings])
        conn.executemany("INSERT INTO fiscal_periods VALUES (?, ?, ?, ?, ?, ?)", [
            (cik, fy, fp, start.isoformat(), end.isoformat(), src) for fy, fp, start, end, src in periods])
        conn.executemany(f"INSERT INTO facts ({', '.join(_FACT_COLUMNS)}) VALUES "
                         f"({', '.join('?' * len(_FACT_COLUMNS))})", [fact_row(f, k) for f, k in facts])


def company_aliases(ticker: str, *names: str) -> set[str]:
    keys = {normalize_entity(a) for a in [ticker, *ALIASES.get(ticker.upper(), []), *names] if a}
    return {k for k in keys if k}


# --------------------------------------------------------------------------- the store
def _duration_ok(f: Fact) -> bool:
    """Press-release facts: keep instants, quarters and years (not YTD columns), like the SQLite side."""
    if f.period_start is None:
        return True
    days = (f.period_end - f.period_start).days
    return 80 <= days <= 100 or 350 <= days <= 380


def _segment_ok(fact_segment: Optional[str], wanted: Optional[str]) -> bool:
    if wanted == ANY_SEGMENT:
        return True
    if wanted is None:
        return fact_segment is None
    return fact_segment is not None and fact_segment.strip().casefold() == wanted.strip().casefold()


_SOURCE_RANK = {"press_release": 0, "xbrl": 1, "derived": 2}


def _sort_key(f: Fact):
    return (to_utc(f.accepted_at), f.period_end, f.period_start or date.min,
            _SOURCE_RANK.get(f.source, 3), f.accession, f.concept or "")


class Store:
    """EvidenceStore backed by data/facts.sqlite (read-only) plus data/pr_facts JSONL files."""

    def __init__(self, db_path: Path | str | None = None, pr_dir: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else default_data_dir() / "facts.sqlite"
        self.pr_dir = Path(pr_dir) if pr_dir is not None else self.db_path.parent / "pr_facts"
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"{self.db_path} does not exist. Build it with: python -m receipts.ingest "
                "--tickers NVDA,AAPL,MSFT,META,AMZN,GOOGL,JPM,BAC,AMD,NFLX --quarters 8")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._pr_cache: dict[Path, tuple[tuple[int, int], list[Fact]]] = {}

    def close(self) -> None:
        self._conn.close()

    def _rows(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ------------------------------------------------------------ EvidenceStore
    def resolve_entity(self, name: str) -> Optional[tuple[int, str]]:
        """Exact match on normalized ticker, SEC name or alias. No prefix or fuzzy matching on purpose:
        "Amazon Web Services" or "Google Cloud" must stay unresolved ("?"), not become the parent whose
        total revenue would then contradict a segment figure."""
        key = normalize_entity(name or "")
        if not key:
            return None
        hits = {(r["cik"], r["ticker"]) for r in self._rows("SELECT cik, ticker, aliases FROM companies")
                if key in json.loads(r["aliases"])}
        return hits.pop() if len(hits) == 1 else None  # none, or two companies share it: unresolved

    def facts(self, cik: int, metric: Metric, *, basis: Optional[Basis] = None,
              segment: Optional[str] = None, period_end: Optional[date] = None,
              as_of: Optional[datetime] = None) -> list[Fact]:
        if isinstance(period_end, datetime):
            period_end = period_end.date()
        sql = [f"SELECT * FROM facts WHERE cik = ? AND metric = ? AND period_kind IN {SERVED_KINDS}"]
        params: list = [int(cik), metric]
        if basis is not None:
            sql.append("AND basis = ?")
            params.append(basis)
        if segment is None:
            sql.append("AND segment IS NULL")
        elif segment != ANY_SEGMENT:
            sql.append("AND segment = ? COLLATE NOCASE")
            params.append(segment.strip())
        if period_end is not None:
            sql.append("AND period_end = ?")
            params.append(period_end.isoformat())
        cutoff = to_utc(as_of) if as_of is not None else None
        if cutoff is not None:
            sql.append("AND accepted_at <= ?")
            params.append(iso_utc(cutoff))
        out = [_row_to_fact(r) for r in self._rows(" ".join(sql), params)]
        for f in self._press_release_facts(cik):
            if (f.metric == metric and (basis is None or f.basis == basis) and _segment_ok(f.segment, segment)
                    and (period_end is None or f.period_end == period_end)
                    and (cutoff is None or to_utc(f.accepted_at) <= cutoff) and _duration_ok(f)):
                out.append(f)
        out.sort(key=_sort_key)
        return out

    def fiscal_calendar(self, cik: int) -> list[tuple[int, str, date, date]]:
        rows = self._rows(
            "SELECT fiscal_year, fiscal_period, period_start, period_end FROM fiscal_periods WHERE cik = ? "
            "ORDER BY period_end, CASE fiscal_period WHEN 'FY' THEN 1 ELSE 0 END", (int(cik),))
        return [(r[0], r[1], date.fromisoformat(r[2]), date.fromisoformat(r[3])) for r in rows]

    def first_public(self, cik: int, metric: Metric, period_end: date,
                     basis: Optional[Basis] = None) -> Optional[datetime]:
        """Earliest accepted_at over every matching fact: XBRL, derived and press release, any segment."""
        if isinstance(period_end, datetime):
            period_end = period_end.date()
        found = self.facts(cik, metric, basis=basis, segment=ANY_SEGMENT, period_end=period_end)
        return min((to_utc(f.accepted_at) for f in found), default=None)

    # ------------------------------------------------------------ extras (not in the protocol)
    def company(self, cik: int) -> Optional[dict]:
        rows = self._rows("SELECT cik, ticker, name, fiscal_year_end FROM companies WHERE cik = ?", (int(cik),))
        return dict(rows[0]) if rows else None

    def filings(self, cik: int, forms: Optional[Iterable[str]] = None) -> list[dict]:
        """Stored filings (10-K, 10-Q, 8-K and amendments), oldest first."""
        rows = [dict(r) for r in self._rows(
            "SELECT * FROM filings WHERE cik = ? ORDER BY accepted_at", (int(cik),))]
        wanted = set(forms) if forms else None
        return [r for r in rows if wanted is None or r["form"] in wanted]

    def _press_release_facts(self, cik: int) -> list[Fact]:
        info = self.company(cik)
        if not info:
            return []
        folder = self.pr_dir / info["ticker"].upper()
        if not folder.is_dir():
            return []
        out: list[Fact] = []
        with self._lock:
            for path in sorted(folder.glob("*.jsonl")):
                try:
                    st = path.stat()
                except OSError:
                    continue
                stamp = (st.st_mtime_ns, st.st_size)
                cached = self._pr_cache.get(path)
                if cached is None or cached[0] != stamp:
                    cached = (stamp, _load_jsonl(path))
                    self._pr_cache[path] = cached
                out.extend(f for f in cached[1] if f.cik == int(cik) or f.ticker.upper() == info["ticker"])
        return out


def _load_jsonl(path: Path) -> list[Fact]:
    """Parse one press-release JSONL file. Bad or half-written lines are skipped, never fatal."""
    facts = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return facts
    for line in lines:
        if not line.strip():
            continue
        try:
            f = Fact.model_validate_json(line)
        except ValueError:
            continue
        # aware UTC like the SQLite facts, so callers can compare accepted_at with as_of safely
        facts.append(f.model_copy(update={"accepted_at": to_utc(f.accepted_at)}))
    return facts


def _row_to_fact(r: sqlite3.Row) -> Fact:
    return Fact(
        cik=r["cik"], ticker=r["ticker"], metric=r["metric"], concept=r["concept"], basis=r["basis"],
        segment=r["segment"], value=r["value"], unit=r["unit"], period_start=_d(r["period_start"]),
        period_end=date.fromisoformat(r["period_end"]), fiscal_year=r["fiscal_year"],
        fiscal_period=r["fiscal_period"], source=r["source"], derived_from=json.loads(r["derived_from"]),
        form=r["form"], accession=r["accession"], accepted_at=_parse_utc(r["accepted_at"]), url=r["url"],
        locator=r["locator"], decimals=r["decimals"])
