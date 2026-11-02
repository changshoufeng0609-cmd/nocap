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
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, Optional

from receipts.fiscal_labels import is_long
from receipts.types import Basis, Fact, Metric

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "facts.sqlite"
PR_DIR = ROOT / "data" / "pr_facts"
SCHEMA_VERSION = "1"
ANY_SEGMENT = "*"
PR_CACHE_FILES = 64  # parsed releases retained per Store; older files reload on demand


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
    "net_income": MetricSpec((
        "NetIncomeLoss",
        "ProfitLoss",  # AVGO tags only consolidated net income; ingest uses it only where the filing reports no
                       # noncontrolling interest for that period, so it equals NetIncomeLoss (see receipts.ingest)
    ), "USD", True),
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
    "TSLA": ["Tesla", "Tesla, Inc.", "Tesla Motors", "Tesla Motors, Inc."],
    "AVGO": ["Broadcom", "Broadcom Inc."],
    "ORCL": ["Oracle", "Oracle Corporation"],
    "CRM": ["Salesforce", "Salesforce, Inc."],
    "ADBE": ["Adobe", "Adobe Inc.", "Adobe Systems"],
    "INTC": ["Intel", "Intel Corporation"],
    "QCOM": ["Qualcomm", "Qualcomm Incorporated"],
    # SEC titles the registrant since its 2026 holding-company reorganization "ExxonMobil Holdings Corp" (CIK 2115436,
    # stitched to CIK 34088 by receipts.ingest.PREDECESSOR_CIKS); people write Exxon Mobil, ExxonMobil or Exxon
    "XOM": ["Exxon Mobil", "ExxonMobil", "Exxon Mobil Corporation", "Exxon"],
    "CSCO": ["Cisco", "Cisco Systems", "Cisco Systems, Inc."],
    "IBM": ["IBM", "International Business Machines", "International Business Machines Corporation"],
    "PLTR": ["Palantir", "Palantir Technologies", "Palantir Technologies Inc."],
    "UBER": ["Uber", "Uber Technologies", "Uber Technologies, Inc."],
    "WMT": ["Walmart", "Walmart Inc.", "Wal-Mart", "Wal-Mart Stores"],
    "COST": ["Costco", "Costco Wholesale", "Costco Wholesale Corporation"],
    "DIS": ["Disney", "Walt Disney", "The Walt Disney Company"],
    "NKE": ["Nike", "Nike, Inc."],
    "MCD": ["McDonald's", "McDonalds", "McDonald's Corporation"],
    "KO": ["Coca-Cola", "The Coca-Cola Company", "Coca Cola"],  # not "Coke": COKE is Coca-Cola Consolidated
    "LLY": ["Eli Lilly", "Lilly", "Eli Lilly and Company"],
    "UNH": ["UnitedHealth", "UnitedHealth Group", "UnitedHealth Group Incorporated", "United Health"],
    "JNJ": ["Johnson & Johnson", "J&J", "Johnson and Johnson"],
    "GS": ["Goldman Sachs", "Goldman", "The Goldman Sachs Group", "Goldman Sachs Group", "GS"],
    "GM": ["General Motors", "GM"],
    "MS": ["Morgan Stanley", "MS"],
    "HPE": ["Hewlett Packard Enterprise", "HP Enterprise"],  # the longer name must win before the short "HP"
    "WFC": ["Wells Fargo", "Wells Fargo & Company"],
}

_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
             "llc", "de", "and", "the"}


def fold(text: str) -> str:
    """NFKC, then accents dropped (NFKD without its combining marks): "Estée" -> "Estee", "ＮＶＩＤＩＡ" -> "NVIDIA".
    Without the second step an accented letter would split a name ("Estée" -> "est e"). ASCII text is unchanged."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalize_entity(name: str) -> str:
    """'JPMorgan Chase & Co.' -> 'jpmorgan chase'; "Nvidia's" -> 'nvidia'; 'Apple Inc. (AAPL)' -> 'apple';
    'Estée Lauder' -> 'estee lauder'."""
    s = fold(name).lower().replace("’", "'")
    s = re.sub(r"\([^)]*\)", " ", s)
    # A closing possessive belongs to the sentence; inside a name the s belongs
    # to the name (Lowe's Companies, Domino's Pizza, McDonald's Corporation).
    s = re.sub(r"'s(?=\W*$)", "", s)
    s = s.replace("&", " and ").replace(".", "").replace("'", "")
    tokens = re.sub(r"[^0-9a-z]+", " ", s).split()
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return initials_key(" ".join(tokens))


def initials_key(key: str) -> str:
    """Adjacent single-letter initials share a key: 'd r horton' == 'dr horton'.
    Whole words are never joined, reordered or prefix-matched.
    """
    return re.sub(r"\b[a-z](?:\s+[a-z])+(?!\w)", lambda m: "".join(m[0].split()), key)


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


# "KEYCORP /NEW/", "QUALCOMM INC/DE", "WELLS FARGO & COMPANY/MN", "US BANCORP \DE\"
_SEC_NAME_TAG = re.compile(r"\s*[/\\][A-Za-z]{2,3}[/\\]?\s*$")

# Common names from the S&P 500 constituents' "Security" column (Wikipedia list, read 2026-10-30) that the SEC names
# miss: "Marsh McLennan" for MARSH & MCLENNAN COMPANIES, "Verizon" for VERIZON COMMUNICATIONS. Each normalizes to the
# Security name's key: at least two words, or one distinctive word that is not an English word (plural and possessive
# forms included), a common first name or surname, a US place name or a market index. "Moody's", "Lowe's" and "DuPont"
# are lookup keys but sit in WORD_NAMES, so running text never counts them; "Estée Lauder" is the Security name without
# "Companies (The)". Every allow and block decision, with its reason, is in
# eval/evidence_expansion/sp500-aliases-20261031.json; tests/test_entity_scale.py fails if any key names two of the 499
# companies. Like ALIASES, these are added when the alias index is built, so a stored database needs no re-ingest; the
# first one is the company's display name.
SECURITY_ALIASES: dict[str, tuple[str, ...]] = {
    "AOS": ("A. O. Smith",), "APD": ("Air Products",), "MO": ("Altria",), "BNY": ("BNY Mellon",),
    "BG": ("Bunge Global",), "CHRW": ("C.H. Robinson",), "COF": ("Capital One",), "CF": ("CF Industries",),
    "CRL": ("Charles River Laboratories",), "SCHW": ("Charles Schwab",), "CI": ("Cigna",), "COIN": ("Coinbase",),
    "CRWD": ("CrowdStrike",), "DECK": ("Deckers Brands",), "DLR": ("Digital Realty",), "DHI": ("D. R. Horton",),
    "DD": ("DuPont",), "EME": ("Emcor",), "EL": ("Estée Lauder",), "EXPD": ("Expeditors International",),
    "FDS": ("FactSet",), "FDXF": ("FedEx Freight",), "BEN": ("Franklin Resources",), "GE": ("GE Aerospace",),
    "GEHC": ("GE HealthCare",), "GNRC": ("Generac",), "HLT": ("Hilton Worldwide",), "HON": ("Honeywell Technologies",),
    "IBKR": ("Interactive Brokers",), "IQV": ("IQVIA",), "JBHT": ("J.B. Hunt",), "JCI": ("Johnson Controls",),
    "LHX": ("L3Harris",), "LH": ("Labcorp",), "LDOS": ("Leidos",), "LOW": ("Lowe's",), "LITE": ("Lumentum",),
    "LYB": ("LyondellBasell",), "MRSH": ("Marsh McLennan",), "MTD": ("Mettler Toledo",), "MGM": ("MGM Resorts",),
    "MCO": ("Moody's",), "NXPI": ("NXP Semiconductors",), "ORLY": ("O'Reilly Automotive",),
    "PKG": ("Packaging Corporation of America",), "PYPL": ("PayPal",), "PNC": ("PNC Financial Services",),
    "RCL": ("Royal Caribbean Group", "Royal Caribbean"), "SLB": ("Schlumberger",), "STX": ("Seagate Technology",),
    "SJM": ("J.M. Smucker",), "SMCI": ("Supermicro",), "TROW": ("T. Rowe Price",), "TTWO": ("Take-Two Interactive",),
    "VZ": ("Verizon",), "VRTX": ("Vertex Pharmaceuticals",), "VRT": ("Vertiv",), "WRB": ("W. R. Berkley",),
    "GWW": ("W. W. Grainger",), "WAB": ("Wabtec",), "ZBH": ("Zimmer Biomet",),
    # Short names: the Security name without generic trailing words (Laboratories, Scientific, Motor(s), Company,
    # Companies, Corporation, Corp, Incorporated, Inc, Co, Group, Holdings, Holding, plc, Ltd, US), when that leaves two
    # words or more that no other company uses
    "ACGL": ("Arch Capital",), "CFG": ("Citizens Financial",), "NCLH": ("Norwegian Cruise Line",),
    "SPG": ("Simon Property",), "TMUS": ("T-Mobile",), "TMO": ("Thermo Fisher",), "UAL": ("United Airlines",),
    "WEC": ("WEC Energy",),
    # Common names the S&P 500 QA (2026-10-31, G4) found missing, which neither rule above yields: two words that no
    # other company uses (Truist and Lockheed, one word each, are lookup-only SHORT_NAMES)
    "MLM": ("Martin Marietta",),
}

# Lookup-only names: a claim's company (or the company an Ask lookup proposes) resolves by them, but running text never
# counts them (they are in WORD_NAMES): one-word short names ("Ford" for Ford Motor Company, "Abbott" for Abbott
# Laboratories), the earlier one-word blocks Ball, Casey's, Cognizant, Domino's and Flex, short names that are also
# ordinary phrases or places ("Charles River", "principal financial officer"), and the one-word common names the S&P 500
# QA found missing ("Truist" for Truist Financial, "Lockheed" for Lockheed Martin). Dow stays out (the Dow index), so
# does "Boston" (Boston Scientific: a city). Decisions and reasons: eval/evidence_expansion/sp500-aliases-20261031.json.
SHORT_NAMES: dict[str, tuple[str, ...]] = {
    "ABT": ("Abbott",), "BKNG": ("Booking",), "COO": ("Cooper",), "CSGP": ("CoStar",), "EG": ("Everest",),
    "EXPE": ("Expedia",), "F": ("Ford",), "IDXX": ("Idexx",), "OMC": ("Omnicom",), "TDG": ("TransDigm",),
    "TRV": ("Travelers",), "WMB": ("Williams",),
    "BALL": ("Ball",), "CASY": ("Casey's", "Caseys"), "CTSH": ("Cognizant", "Cognizant Technology"),
    "DPZ": ("Domino's", "Dominos"), "FLEX": ("Flex",),
    "CRL": ("Charles River",), "PFG": ("Principal Financial",), "PEG": ("Public Service Enterprise",),
    "TFC": ("Truist",), "LMT": ("Lockheed",),
    # Fourth round: the names people use for the 499 companies (COMMON_NAMES), when they are English words,
    # surnames, first names, places or phrases, or shorter than six letters
    "ADM": ("Archer Daniels",), "AJG": ("Gallagher",), "ALGN": ("Align",), "AMD": ("Advanced Micro",),
    "ANET": ("Arista",), "APO": ("Apollo", "Apollo Global"), "ARE": ("Alexandria",), "ATO": ("Atmos",),
    "AVY": ("Avery",), "AWK": ("American Water",), "AXP": ("Amex",), "BAX": ("Baxter",), "BE": ("Bloom",),
    "BG": ("Bunge",), "BRK-B": ("Berkshire",), "C": ("Citi",), "CAH": ("Cardinal",), "CARR": ("Carrier",),
    "CDNS": ("Cadence", "Cadence Design"), "CFG": ("Citizens",), "CHTR": ("Charter",), "CL": ("Colgate",),
    "CPT": ("Camden",), "D": ("Dominion",), "DAL": ("Delta",), "DECK": ("Deckers",), "DGX": ("Quest",),
    "DHI": ("Horton",), "DRI": ("Darden",), "DUK": ("Duke",), "DVN": ("Devon",), "ED": ("ConEd", "Con Ed"),
    "EMR": ("Emerson",), "ESS": ("Essex",), "EW": ("Edwards",), "EXR": ("Extra Space",), "FANG": ("Diamondback",),
    "FERG": ("Ferguson",), "FITB": ("Fifth Third",), "FIX": ("Comfort Systems",), "FRT": ("Federal Realty",),
    "GILD": ("Gilead",), "HIG": ("Hartford Insurance",), "HLT": ("Hilton",), "HRL": ("Hormel",),
    "HST": ("Host Hotels",), "IFF": ("International Flavors",), "ISRG": ("Intuitive",), "ITW": ("Illinois Tool",),
    "J": ("Jacobs",), "JKHY": ("Jack Henry",), "KDP": ("Keurig",), "KIM": ("Kimco",), "LII": ("Lennox",),
    "LOW": ("Lowes",), "LRCX": ("Lam",), "LUV": ("Southwest",), "LVS": ("Sands",), "LYV": ("Live Nation",),
    "MAA": ("Mid-America Apartment",), "MAR": ("Marriott",), "MCHP": ("Microchip",), "MNST": ("Monster",),
    "MPC": ("Marathon",), "MPWR": ("Monolithic Power",), "MSI": ("Motorola",), "MU": ("Micron",),
    "NCLH": ("Norwegian Cruise",), "NOC": ("Northrop",), "NSC": ("Norfolk",), "NXPI": ("NXP",),
    "ORLY": ("O'Reilly", "O Reilly"), "OXY": ("Occidental",), "P": ("Pure Storage",), "PANW": ("Palo Alto",),
    "PEP": ("Pepsi",),  # five-letter brand name: lookup-only, never a running-text company mention
    "PH": ("Parker",), "PM": ("Philip Morris",), "PNW": ("Pinnacle West",), "PRU": ("Prudential",), "PWR": ("Quanta",),
    "Q": ("Qnity",), "REG": ("Regency",), "RF": ("Regions",), "RJF": ("Raymond James",), "ROK": ("Rockwell",),
    "ROP": ("Roper",), "ROST": ("Ross",), "SCHW": ("Schwab",), "SMCI": ("Super Micro",), "SPG": ("Simon",),
    "SW": ("Smurfit",), "SYF": ("Synchrony",), "TRGP": ("Targa",), "TROW": ("T. Rowe",), "TSN": ("Tyson",),
    "TT": ("Trane",), "TTWO": ("Take-Two",), "TYL": ("Tyler",), "UHS": ("Universal Health",),
    "UPS": ("United Parcel",), "VEEV": ("Veeva",), "VMC": ("Vulcan",), "VRTX": ("Vertex",), "WRB": ("Berkley",),
    "WST": ("West Pharmaceutical",), "WTW": ("Willis",), "XEL": ("Xcel",), "ZBH": ("Zimmer",), "ZBRA": ("Zebra",),
}


# Fourth round (2026-10-31): the names people use for each of the 499 companies, derived the same way for all of them:
# the Security and SEC names without "The", Inc., Corp., Co., Company, Companies, Holdings, Group, Ltd., plc, "& Co.",
# N.V. and S.A., and their first one or two words where that is how the company is known ("Valero", "Huntington
# Ingalls", "Bristol Myers"), plus a few such names that are not first words ("Con Edison", "P&G"). Distinctive ones are
# ordinary aliases here; English words, surnames, first names, places and phrases are lookup-only SHORT_NAMES; a name
# that starts another company's name ("Huntington": HBAN and HII), or is generic, is blocked. Never display names: an
# explanation keeps "Procter & Gamble". Every decision and reason: eval/evidence_expansion/sp500-aliases-20261031.json.
COMMON_NAMES: dict[str, tuple[str, ...]] = {
    "A": ("Agilent",), "AKAM": ("Akamai",), "AMP": ("Ameriprise",), "BMY": ("Bristol Myers",),
    "BR": ("Broadridge", "Broadridge Financial"), "CBOE": ("Cboe Global",), "CMG": ("Chipotle",),
    "CNP": ("CenterPoint",), "DOC": ("Healthpeak",), "ED": ("Con Edison",), "ELV": ("Elevance",),
    "ES": ("Eversource",), "EXPD": ("Expeditors",), "FDS": ("FactSet Research",), "HII": ("Huntington Ingalls",),
    "HOOD": ("Robinhood",), "HWM": ("Howmet",), "KEYS": ("Keysight",), "LNT": ("Alliant",), "LULU": ("Lululemon",),
    "LYB": ("LyondellBasell Industries",), "MDLZ": ("Mondelez",), "MRSH": ("Marsh & McLennan",), "MRVL": ("Marvell",),
    "MTB": ("M&T",), "NEE": ("NextEra",), "PG": ("P&G",), "PNC": ("PNC Financial",), "REGN": ("Regeneron",),
    "STX": ("Seagate",), "SWKS": ("Skyworks",), "TDY": ("Teledyne",), "VLO": ("Valero",), "VMRK": ("Vivmark",),
    "VRSK": ("Verisk",),
}


# Issuer self-names confirmed in archived earnings releases (2026-11-02).
# These are exact, bare-name lookups only: notably SBA in a bank's "SBA loans"
# sentence must never count as SBA Communications. Provenance and sweep:
# data/eval_runs/codex/names/{before.json,extraction_review.json,sweep.py}.
RELEASE_SHORT_NAMES: dict[str, tuple[str, ...]] = {
    "BDX": ("BD",), "COO": ("CooperCompanies",), "FCX": ("Freeport",),
    "GWW": ("Grainger",), "MPWR": ("MPS",), "MRSH": ("Marsh",),
    "ODFL": ("Old Dominion",), "ON": ("onsemi",), "SBAC": ("SBA",), "WM": ("WM",),
}
RELEASE_NAME_KEYS = frozenset(normalize_entity(n) for names in RELEASE_SHORT_NAMES.values() for n in names)


def known_names(ticker: str) -> list[str]:
    """Curated, constituent and release-attested names, including bare-only aliases, as written."""
    t = (ticker or "").upper()
    return [*ALIASES.get(t, []), *SECURITY_ALIASES.get(t, ()), *COMMON_NAMES.get(t, ()),
            *SHORT_NAMES.get(t, ()), *RELEASE_SHORT_NAMES.get(t, ())]


_SUFFIX_CASE = {"INC": "Inc", "CORP": "Corp", "CO": "Co", "LTD": "Ltd", "PLC": "plc"}
_SMALL_WORDS = frozenset({"and", "of", "the", "for", "de"})
_SHORT_WORDS = frozenset({"oil", "gas", "air", "new", "one", "two", "sun", "sea", "bay", "top"})


def _title_word(word: str, first: bool) -> str:
    """One word of an SEC name written in capitals: 'MCLENNAN' -> 'Mclennan', "O'REILLY" -> "O'Reilly", "MCDONALD'S" ->
    "Mcdonald's", 'INC.' -> 'Inc.', 'OF' -> 'of'. Likely acronyms keep their capitals: words of three letters or fewer
    ('TJX', 'AES') and short words without a vowel ('CVS'), and anything with '&' ('AT&T', 'M&T')."""
    core = word.strip(",.")
    if not re.search(r"[A-Z]{2}", core) or re.search(r"[a-z]", core):
        return word
    if core in _SUFFIX_CASE:
        return word.replace(core, _SUFFIX_CASE[core])
    if core.lower() in _SMALL_WORDS:
        return word.lower() if not first else word[:1] + word[1:].lower()
    if "&" in core or (len(core) <= 3 and core.lower() not in _SHORT_WORDS) or (
            len(core) <= 4 and not re.search(r"[AEIOUY]", core)):
        return word
    parts = re.split(r"([-'/])", word.lower())
    return "".join(p if p in "-'/" or (i and parts[i - 1] == "'" and p in ("s", "s.", "s,")) else p[:1].upper() + p[1:]
                   for i, p in enumerate(parts))


def display_name(ticker: str, sec_name: str) -> str:
    """How explanations name a company: its first ALIASES or SECURITY_ALIASES name ("NVIDIA", "Marsh McLennan"), else
    its SEC name without a registrant tag and with words in capitals title-cased: "KEYCORP /NEW/" -> "Keycorp",
    "FLEX LTD." -> "Flex Ltd.", "EXPAND ENERGY Corp" -> "Expand Energy Corp". Never a lookup-only short name: an
    explanation says "Booking Holdings", not "Booking"."""
    t = (ticker or "").upper()
    names = [*ALIASES.get(t, []), *SECURITY_ALIASES.get(t, ())]
    if names:
        return names[0]
    name = " ".join(_SEC_NAME_TAG.sub("", sec_name or "").split()) or (ticker or "")
    return " ".join(_title_word(w, i == 0) for i, w in enumerate(name.split()))


def company_aliases(ticker: str, *names: str) -> set[str]:
    """Alias keys stored at ingest: the ticker, ALIASES, SECURITY_ALIASES and each name, also without a trailing SEC
    registrant tag."""
    forms = [n for name in names if name for n in dict.fromkeys((name, _SEC_NAME_TAG.sub("", name)))]
    # SEC inverted names put the surname before separated initials, e.g.
    # HUNT J B TRANSPORT SERVICES INC. Preserve the complete remaining name.
    forms += [re.sub(r"^([A-Z][A-Z'-]+)\s+((?:[A-Z]\.?\s+){2,})",
                     lambda m: m[2] + m[1] + " ", n) for n in forms]
    keys = {normalize_entity(a) for a in [ticker, *known_names(ticker), *forms] if a}
    return {k for k in keys if k}


# ----------------------------------------------------------------------------- entity safety
# A ticker that is an everyday English word, or has two letters or fewer, never names a company by itself: "T", "A",
# "IT", "ON", "ALL", "NOW", "COST" and "KEY" are words in a draft long before they are AT&T, Agilent, Gartner, onsemi,
# Allstate, ServiceNow, Costco or KeyCorp. Each company still resolves by its name ("AT&T", "Allstate"). Running text
# already counts a bare ticker only as that word in capitals ("NVDA", not "nvda"); this list is for tickers that are
# also written in capitals, or as a name, with another meaning: "APP", "DOC", "USB", "CMS", "PSA", "Ben", "Lin".
WORD_TICKERS = frozenset({
    "all", "amp", "are", "ball", "carr", "cat", "coin", "coo", "cop", "cost", "dash", "deck", "dow", "echo",
    "fang", "fast", "fix", "flex", "fox", "gen", "gild", "has", "hood", "hum", "ice", "key", "keys", "lite", "low",
    "mar", "mas", "met", "now", "peg", "pep", "pool", "reg", "tap", "tech", "wat", "well",
    # S&P 500, approved 2026-10-31 (eval/evidence_expansion/sp500-aliases-20261031.json)
    "amt", "app", "ben", "cms", "cnc", "doc", "emr", "erie", "ess", "gis", "hal", "hon", "iex", "kim", "len", "lin",
    "luv", "mlm", "mmm", "msi", "nee", "pru", "rok", "tel", "uri", "usb",
    "bro", "day", "peak", "psa", "ste",  # "tech bro", "D-DAY", "PEAK season", "Groupe PSA", "STE 200"; HOLX is left
                                         # out: not a word, and nothing else in capitals
    "dal", "exe", "mos",  # also in capitals: the DAL airport code, an .EXE file, "12 MOS", a MOS transistor
})
# Single words that are company names but also ordinary prose. A free-text scan (a draft's own text, an Ask question)
# never treats them as naming the company, also not in capitals; a claim whose extracted company is "Target" or "FICO"
# still resolves. Written with a legal suffix ("Chevron Corp.", "Merck & Co.") they count again.
WORD_NAMES = WORD_TICKERS | frozenset({
    "ball", "block", "carnival", "coherent", "dover", "dow", "flex", "fox", "mosaic", "news", "southern", "tapestry",
    "target", "visa", "workday",
    # S&P 500, approved 2026-10-31: ordinary words, surnames, places and indexes
    "albemarle", "chevron", "corning", "dupont", "eaton", "hershey", "loews", "lowe", "mccormick", "merck", "moody",
    "nasdaq", "progressive", "rollins", "stryker", "waters",
    # and tickers that are also something else in capitals: "start-ups", "FICO score", "MSCI index", "RTX 5090",
    # "AES encryption", "APA style", "PTC" (a production tax credit), "TKO", "OXY", "MGM" studios
    "aes", "apa", "fico", "lulu", "mgm", "msci", "otis", "oxy", "ptc", "rtx", "tko", "ups", "wynn", "yum",
}) | frozenset(normalize_entity(n) for table in (SHORT_NAMES, RELEASE_SHORT_NAMES)
              for names in table.values() for n in names)  # lookup-only, also phrases


def is_word_ticker(ticker: str) -> bool:
    key = normalize_entity(ticker or "")
    return len(re.sub(r"[^0-9a-z]", "", key)) <= 2 or key in WORD_TICKERS


def name_keys(ticker: str, name: str) -> set[str]:
    """Keys a company's names produce (ALIASES, SECURITY_ALIASES and its SEC name, with and without a registrant tag),
    as opposed to its bare ticker."""
    names = [*known_names(ticker), name, _SEC_NAME_TAG.sub("", name or "")]
    return {k for k in (normalize_entity(a) for a in names if a) if k}


def lookup_keys(ticker: str, name: str, aliases: Iterable[str]) -> set[str]:
    """The stored alias keys an entity lookup may use: all but a bare word ticker. That holds even when the company's
    own name normalizes to the same word ("Dow Inc." -> "dow"): the string can't tell the ticker from the name. The
    exceptions are explicit ALIASES ("GS", "GM", "MS") and lookup-only SHORT_NAMES ("Ball", "Flex"): a claim whose
    company is written that way resolves, while running text still never counts a two-letter or word ticker."""
    keys = set(aliases)
    if is_word_ticker(ticker):
        bare = normalize_entity(ticker)
        explicit = (*ALIASES.get((ticker or "").upper(), ()), *SHORT_NAMES.get((ticker or "").upper(), ()),
                    *RELEASE_SHORT_NAMES.get((ticker or "").upper(), ()))
        if bare not in {normalize_entity(a) for a in explicit}:
            keys.discard(bare)
    return keys


def ticker_only_key(ticker: str, name: str, aliases: Iterable[str]) -> Optional[str]:
    """The bare ticker's key when no name or alias produces it too ("nvda", but not "meta" or "ibm"): running text
    counts it only as the ticker written in capitals."""
    key = normalize_entity(ticker or "")
    return key if key and key in set(aliases) and key not in name_keys(ticker, name) else None


def scan_keys(ticker: str, name: str, aliases: Iterable[str]) -> set[str]:
    """Name keys a free-text scan matches in any case: lookup_keys without single words of two letters or fewer,
    ordinary words and lookup-only names (WORD_NAMES) and the bare ticker (matched only in capitals, see
    EntityIndex.tickers)."""
    bare = ticker_only_key(ticker, name, aliases)
    return {k for k in lookup_keys(ticker, name, aliases)
            if k != bare and k not in WORD_NAMES and (" " in k or len(k) > 2)}


_LEGAL_SUFFIXES = frozenset({"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
                             "llc", "nv", "sa", "ag", "se"})


def has_legal_suffix(name: str) -> bool:
    """'Flex Ltd.', 'Ball Corporation', 'Dow Inc.': a company name written out, not a bare word."""
    words = _tokens(name)
    return len(words) > 1 and words[-1] in _LEGAL_SUFFIXES


# A legal suffix says a name is written as a company's legal name, so it must be the covered company's own (code review
# H1, 2026-10-31): "Prudential plc" is the UK insurer, not Prudential Financial, Inc., and "Citizens, Inc." (NYSE: CIA)
# is not Citizens Financial Group. A lookup-only name (WORD_NAMES) counts with a suffix only as the company's own legal
# name with a suffix that fits it ("Ball Corp.", "Merck & Co.", "The Southern Company", "Eaton Corp." for Eaton Corp
# plc); any other name only with a suffix that fits the company ("Nvidia Corp.", never "Nvidia plc"). A suffix fits when
# it is of the same kind as the company's own, or starts it. The kinds: the US forms (Inc., Corp., Co., Company, LLC)
# are one; plc, Ltd., N.V., S.A., AG and SE are one each. A legal name without a suffix ("Abbott Laboratories",
# "KeyCorp") is of the US kind.
_FORM_KIND = {"inc": "us", "incorporated": "us", "corp": "us", "corporation": "us", "co": "us", "company": "us",
              "llc": "us", "plc": "plc", "ltd": "ltd", "limited": "ltd", "nv": "nv", "sa": "sa", "ag": "ag", "se": "se"}
_FORM_SPELLING = {"incorporated": "inc", "corporation": "corp", "company": "co", "limited": "ltd"}
_CONNECTORS = frozenset({"and", "the", "de"})  # normalize_entity drops them at the end of a name, as in "& Co."
LegalName = tuple[tuple[str, ...], tuple[str, ...]]


def legal_form(name: str) -> LegalName:
    """(name words, legal-form words) of a name as written: "Merck & Co., Inc." -> (("merck",), ("co", "inc")), "The
    Southern Company" -> (("southern",), ("co",)), "MOODYS CORP /DE/" and "Moody's Corp." -> (("moodys",), ("corp",)),
    "Nvidia" -> (("nvidia",), ()). A registrant tag, a parenthetical, a leading "the" and a closing possessive are
    dropped, and an apostrophe inside a word, but not its "s": SEC names write "MOODYS" and "LOWES"."""
    s = fold(_SEC_NAME_TAG.sub("", name or "")).lower().replace("’", "'")
    s = re.sub(r"'s?$", "", re.sub(r"\([^)]*\)", " ", s).strip())  # "Ball Corp.'s"
    words = re.sub(r"[^0-9a-z]+", " ", s.replace("&", " and ").replace(".", "").replace("'", "")).split()
    while words and words[0] == "the":
        words.pop(0)
    while len(words) > 1 and words[-1] in _CONNECTORS:  # "Nvidia plc and": no name ends in a connector
        words.pop()
    forms: list[str] = []
    while len(words) > 1 and (words[-1] in _FORM_KIND or (words[-1] == "and" and forms)):  # "& Co."
        word = words.pop()
        if word != "and":
            forms.insert(0, _FORM_SPELLING.get(word, word))
    return tuple(initials_key(" ".join(words)).split()), tuple(forms)


def _form_kind(forms: tuple[str, ...]) -> str:
    return _FORM_KIND[forms[-1]] if forms else "us"


def legal_base_key(name: str) -> str:
    """normalize_entity without any closing legal suffix, also those it keeps: "NXP Semiconductors N.V." -> "nxp
    semiconductors", "Zoetis S.A." -> "zoetis"."""
    words = normalize_entity(name).split()
    while len(words) > 1 and (words[-1] in _LEGAL_SUFFIXES or words[-1] in _SUFFIXES):
        words.pop()
    return " ".join(words)


def legal_fit(legal: Mapping[Company, tuple[LegalName, ...]], written: str, key: str,
              hits: Iterable[Company]) -> set[Company]:
    """The companies of `hits` (those `key`, the normalized `written`, names) that `written`, a name with a legal
    suffix, can be: for a lookup-only key (WORD_NAMES) one of the company's own legal names with a suffix that fits it,
    for any other key any name with a suffix that fits the company. `legal`: EntityIndex.legal."""
    words, forms = legal_form(written)
    own_name_only = key in WORD_NAMES
    return {c for c in hits
            if any((not own_name_only or own_words == words)
                   and (forms == own_forms[:len(forms)] or _form_kind(forms) == _form_kind(own_forms))
                   for own_words, own_forms in legal.get(c, ()))}


_TOKEN_RE = re.compile(r"[0-9A-Za-z.']+|&")


def scan_tokens(text: str) -> tuple[str, list[tuple[str, int, int]]]:
    """(fold(text), its words as (word, start, end)), case kept: "'s" dropped, "&" read as "and", dots and apostrophes
    removed ("J.P." -> "JP", "O'Reilly" -> "OReilly"). Lowercased, the words are _tokens(text)."""
    s = fold(text).replace("’", "'")
    out = []
    for m in _TOKEN_RE.finditer(s):
        word = "and" if m.group() == "&" else re.sub(r"'[sS](?![0-9A-Za-z])", "", m.group()).replace(".", "").replace("'", "")
        if word:
            out.append((word, m.start(), m.end()))
    return s, out


def _tokens(text: str) -> list[str]:
    """normalize_entity's tokenization, without dropping suffixes or parentheses: for scanning running text."""
    return [word.lower() for word, _, _ in scan_tokens(text)[1]]


# A period ends a sentence unless it closes an abbreviation, an initial or a dotted name ("Inc.", "T. Rowe", "J.P.",
# "U.S."): the same rule as receipts.verify.sentence_breaks.
_NOT_SENTENCE_END = frozenset("inc corp co ltd plc llc no vs approx est jan feb mar apr jun jul aug sep sept oct nov dec "
                              "st jr sr mr mrs ms dr bros cos".split())


def sentence_joins(s: str, tokens: list[tuple[str, int, int]]) -> list[bool]:
    """joins[i]: whether words i and i+1 of scan_tokens may be read as one name, i.e. no sentence ends between them, so
    "... return on capital. One analyst said" never reads as Capital One, nor does 'stressed "return on capital." One'
    (a period before a closing quote or bracket ends the sentence too: code review L6)."""
    out = []
    for (_, a0, a1), (_, b0, _) in zip(tokens, tokens[1:]):
        between = s[a0:b0]
        cut = bool(re.search(r"[!?;]|\n\s*\n", s[a1:b0]))  # a paragraph break too; one newline may just wrap a name
        for m in re.finditer(r"\.[\"'”’)\]]*(?=\s)", between):
            # Only the token's tail is needed; an uncapped dotted token makes this search quadratic (review L15).
            tail = between[max(0, m.start() - 32):m.start()]
            word = re.search(r"[A-Za-z][A-Za-z.]*$", tail)
            if not (word and ("." in word.group() or word.group().casefold() in _NOT_SENTENCE_END
                              or (len(word.group()) == 1 and not tail[max(0, word.start() - 1):word.start()].isdigit()))):
                cut = True
        out.append(not cut)
    return out


# An all-caps passage ("TECH BRO CULTURE", "THE APP STORE") says nothing about tickers: there a ticker-shaped word is just
# a word in capitals. A run is the word next to another all-caps word of three or more letters, separated only by spaces
# or dashes. GAAP, EPS and NON (as in NON-GAAP) are written in capitals anyway, so they don't start a run: "NVDA GAAP".
_CAPS_WORD = re.compile(r"[A-Z]{3,}")
_RUN_GAP = re.compile(r"[ \t\u00a0\-\u2010-\u2015]+")
_ALWAYS_CAPITALS = frozenset({"GAAP", "EPS", "NON"})


def in_caps_run(s: str, tokens: list[tuple[str, int, int]], i: int) -> bool:
    for j in (i - 1, i + 1):
        if 0 <= j < len(tokens):
            gap = s[tokens[min(i, j)][2]:tokens[max(i, j)][1]]
            word = tokens[j][0]
            if _RUN_GAP.fullmatch(gap) and _CAPS_WORD.fullmatch(word) and word not in _ALWAYS_CAPITALS:
                return True
    return False


Company = tuple[int, str]  # (cik, ticker)


class EntityIndex(NamedTuple):
    """Keys of every covered company, built once per Store (the companies table is read-only)."""
    lookup: dict[str, set[Company]]       # every key resolve_entity may use
    scan: dict[str, set[Company]]         # name keys running text matches in any case
    rows: list[tuple]                     # (cik, ticker, name, aliases) by ticker
    words: dict[str, set[Company]]        # bare word tickers that are also the company's own name ("dow" for Dow Inc.)
    tickers: dict[str, set[Company]]      # "NVDA": a bare ticker running text matches only as that all-caps word
    suffixed: dict[str, set[Company]]     # one-word WORD_NAMES names that count before a legal suffix ("Chevron Corp.")
    word_names: dict[str, set[Company]]   # WORD_NAMES names of 3+ letters other than the bare ticker ("Visa", "Charles
                                          # River"): an Ask question counts them only for the company the lookup proposed
    own_tickers: dict[str, set[Company]]  # "UPS", "FICO", "MSCI": a ticker that is also the company's WORD_NAMES name;
                                          # an Ask question counts it in capitals, only for the proposed company
    legal: dict[Company, tuple[LegalName, ...]]  # legal_form of the SEC name and of each known name with a legal suffix
                                                 # ("The Coca-Cola Company"): what a name with a suffix must fit


def build_entity_index(rows: Iterable[tuple]) -> EntityIndex:
    """rows: (cik, ticker, name, aliases), aliases a JSON list of stored keys (or None). ALIASES, SECURITY_ALIASES,
    COMMON_NAMES, SHORT_NAMES and RELEASE_SHORT_NAMES are added here, so a stored database picks up new aliases
    without a re-ingest."""
    idx = EntityIndex({}, {}, [], {}, {}, {}, {}, {}, {})
    for cik, ticker, name, aliases in rows:
        stored = json.loads(aliases) if isinstance(aliases, str) else list(aliases or ())
        idx.rows.append((cik, ticker, name, aliases))
        c = (cik, ticker)
        sec = legal_form(name or "")
        idx.legal[c] = tuple(dict.fromkeys([sec, *(f for f in map(legal_form, known_names(ticker)) if f[1])]))
        # Re-key stored initials and recompute the SEC name too: snapshots may
        # predate these normalization rules and have nonempty legacy aliases.
        keys = {initials_key(a) for a in stored} | company_aliases(ticker, name)
        keys.discard("")
        bare = ticker_only_key(ticker, name, keys)
        tkey = normalize_entity(ticker)
        for k in lookup_keys(ticker, name, keys):
            idx.lookup.setdefault(k, set()).add(c)
            if k in RELEASE_NAME_KEYS:
                continue  # strictly bare lookup, including when Ask proposes this company
            single = " " not in k
            if k in WORD_NAMES:  # never in running text; a one-word name counts before a legal suffix
                if single:
                    idx.suffixed.setdefault(k, set()).add(c)
                if len(k) > 2 and k != tkey:
                    idx.word_names.setdefault(k, set()).add(c)
                elif k == tkey and not is_word_ticker(ticker) and ticker.isalpha():
                    idx.own_tickers.setdefault(ticker.upper(), set()).add(c)
            elif k == bare:
                if ticker.isalpha():
                    idx.tickers.setdefault(ticker.upper(), set()).add(c)
            elif k in {"3m", "hp"}:  # short company names, in capitals: not "3m" metres or "hp" horsepower
                idx.tickers.setdefault(k.upper(), set()).add(c)
            elif not single or len(k) > 2:
                idx.scan.setdefault(k, set()).add(c)
        if is_word_ticker(ticker) and tkey in name_keys(ticker, name):
            idx.words.setdefault(tkey, set()).add(c)
            idx.suffixed.setdefault(tkey, set()).add(c)
    return idx


def find_mentions(s: str, tokens: list[tuple[str, int, int]], index: EntityIndex, *, max_words: int = 6,
                  proposed: Optional[int] = None, joins: Optional[list[bool]] = None) -> list[tuple[int, int, set[Company]]]:
    """Every company mention in running text, as (first word, end word, companies), longest phrase first at each word.
    A name key matches in any case; a bare ticker only as that word in capitals outside an all-caps run, never when it
    is in WORD_TICKERS or WORD_NAMES; a WORD_NAMES name only as the company's own legal name ("Chevron Corp."). A name
    with a legal suffix counts only where the suffix fits the company (legal_fit); one that fits none ("Prudential plc",
    "Nvidia plc") is another company's legal name, a mention of no covered company (an empty set), and no shorter
    phrase inside it is tried. With `proposed` (an Ask lookup's company), that company also counts by its own WORD_NAMES
    name ("Visa", "Ford", "Charles River") and by a ticker that is its WORD_NAMES name, written in capitals ("UPS",
    "FICO"), and never any other company that way. A phrase two companies share stays a mention of both: callers decide
    (a draft records neither, an Ask question refuses). No mention starts inside an earlier one: "Norfolk Southern
    Corp." is Norfolk Southern, never also Southern Co. by its "Southern Corp." joins[i] says whether words i and i+1
    may be read as one name (default: always)."""
    out = []
    covered = 0  # words before this one belong to the last mention found
    for i in range(len(tokens)):
        if i < covered:
            continue
        for n in range(min(max_words, len(tokens) - i), 0, -1):
            if joins is not None and not all(joins[i:i + n - 1]):
                continue
            words = [w.lower() for w, _, _ in tokens[i:i + n]]
            if n > 1 and words[-1] in _CONNECTORS:
                continue  # no name ends in "and" ("Nvidia plc and" would normalize to Nvidia past its suffix)
            phrase = " ".join(words)
            hits = index.scan.get(phrase)
            if not hits:
                key = normalize_entity(s[tokens[i][1]:tokens[i + n - 1][2]])
                hits = index.scan.get(key)
                if n > 1 and words[-1] in _LEGAL_SUFFIXES:  # written as a legal name
                    if not hits:
                        key = legal_base_key(phrase)  # "Zoetis N.V." -> "zoetis"
                        hits = index.scan.get(key) or (index.suffixed.get(key) if " " not in key else None)
                        # suffixed: "Merck & Co.", "Flex Ltd.", the WORD_NAMES name written out
                    if hits:
                        hits = legal_fit(index.legal, s[tokens[i][1]:tokens[i + n - 1][2]], key, hits)
                        if not hits:  # another company's legal name: "Prudential plc" is not Prudential Financial
                            out.append((i, i + n, set()))
                            covered = i + n
                            break
            if not hits and n == 1:
                caps = index.tickers.get(tokens[i][0])
                hits = caps if caps and not in_caps_run(s, tokens, i) else None
                if not hits and proposed is not None:
                    own = index.own_tickers.get(tokens[i][0])
                    if own and not in_caps_run(s, tokens, i):
                        hits = {c for c in own if c[0] == proposed} or None
            if not hits and proposed is not None:
                hits = {c for c in index.word_names.get(phrase, ()) if c[0] == proposed} or None
            if hits:
                out.append((i, i + n, set(hits)))
                covered = i + n
                break
    return out


# --------------------------------------------------------------------------- the store
def _duration_ok(f: Fact) -> bool:
    """Press-release facts: keep instants, quarters and years (not YTD columns), like the SQLite side, plus a 16- or
    17-week quarter that carries its fiscal label (Costco's Q4, Kroger's Q1), as receipts.press_release.is_served does."""
    if f.period_start is None:
        return True
    days = (f.period_end - f.period_start).days
    return 80 <= days <= 100 or 350 <= days <= 380 or (
        f.fiscal_period in ("Q1", "Q2", "Q3", "Q4") and is_long(f.period_start, f.period_end))


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
        self._pr_cache: OrderedDict[Path, tuple[tuple[int, int], list[Fact]]] = OrderedDict()
        self._alias_index: Optional[EntityIndex] = None

    def close(self) -> None:
        self._conn.close()

    def _rows(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # ------------------------------------------------------------ EvidenceStore
    def resolve_entity(self, name: str) -> Optional[tuple[int, str]]:
        """Exact match on normalized ticker, SEC name or alias. No prefix or fuzzy matching on purpose:
        "Amazon Web Services" or "Google Cloud" must stay unresolved ("?"), not become the parent whose
        total revenue would then contradict a segment figure. A name written with a legal suffix resolves only where
        the suffix fits the company (legal_fit: "Prudential plc" is not Prudential Financial, Inc.), and a lookup-only
        name (WORD_NAMES) only bare, never after "the"."""
        key = normalize_entity(name or "")
        if not key:
            return None
        index = self._index()
        hits = index.lookup.get(key, set())
        if legal_form(name)[1]:  # written as a legal name: only a suffix that fits the company counts (legal_fit)
            if not hits:  # "LyondellBasell N.V."; "Dow Inc." names the company even though "Dow" alone never does
                key = legal_base_key(name)
                hits = index.lookup.get(key) or index.words.get(key, set())
            hits = legal_fit(index.legal, name, key, hits)  # "Prudential plc" is not Prudential Financial, Inc.
        elif key in WORD_NAMES and _tokens(name)[:1] == ["the"]:
            return None  # a lookup-only name only bare: "the carrier" is a carrier, not Carrier Global
        return next(iter(hits)) if len(hits) == 1 else None  # none, or two companies share it: unresolved

    def word_name(self, name: str) -> Optional[tuple[int, str, str]]:
        """(cik, ticker, display name) when `name` is, by itself, a covered company's everyday-word name ("Dow"); "Dow
        plc" is a legal name, not the word."""
        if legal_form(name or "")[1]:
            return None
        hits = self._index()[3].get(normalize_entity(name or ""), set())
        if len(hits) != 1:
            return None
        cik, ticker = next(iter(hits))
        return next((c for c in self.companies_list() if c[0] == cik), (cik, ticker, ticker))

    def by_ticker(self, ticker: str) -> Optional[tuple[int, str]]:
        """(cik, ticker) of the company stored under exactly this ticker, or None. For code that already holds a ticker
        from the store itself (an Ask fact, a packaged filing): there "COST" or "T" is a ticker, whereas a draft's
        words go through resolve_entity, where a bare word ticker may not name a company."""
        rows = self._rows("SELECT cik, ticker FROM companies WHERE ticker = ?", ((ticker or "").strip().upper(),))
        return (int(rows[0]["cik"]), str(rows[0]["ticker"])) if rows else None

    def suggestion(self, name: str) -> Optional[tuple[int, str, str]]:
        """(cik, ticker, display name) of the one covered company `name` most likely meant: the company whose bare
        ticker it is ("GS"), else the only one whose SEC name begins with it as whole words ("Marathon" -> MARATHON
        PETROLEUM). For an explanation's "did you mean" only, never a lookup: "Marathon" must not become MPC."""
        key = normalize_entity(name or "")
        if not key:
            return None
        rows = self._index()[2]
        hits = [r for r in rows if normalize_entity(r[1]) == key]
        if not hits:
            hits = [r for r in rows if f"{normalize_entity(_SEC_NAME_TAG.sub('', r[2]))} ".startswith(f"{key} ")]
        if len(hits) != 1:
            return None
        return next(c for c in self.companies_list() if c[0] == hits[0][0])

    def _index(self) -> EntityIndex:
        """The entity keys of every company (EntityIndex), built once: the companies table is read-only. [0] is the
        lookup index, [1] the free-text scan index, [2] the company rows, [3] the word names ("dow" for Dow Inc.)."""
        with self._lock:
            if self._alias_index is None:
                self._alias_index = build_entity_index(
                    tuple(r) for r in self._rows("SELECT cik, ticker, name, aliases FROM companies ORDER BY ticker"))
            return self._alias_index

    def entity_index(self) -> EntityIndex:
        return self._index()

    def names_in_text(self, text: str, max_words: int = 6) -> list[tuple[int, str, str]]:
        """Companies a piece of running text names, as (cik, ticker, display name), in order of first mention
        (find_mentions): a name in any case, a bare ticker only in capitals outside an all-caps run, never a word ticker
        or an ordinary word ("target", "now") unless a legal suffix follows it. A phrase two companies share names
        neither, and no shorter phrase inside it is tried instead. No name runs across the end of a sentence."""
        display = {cik: name for cik, _, name in self.companies_list()}
        s, tokens = scan_tokens(text)
        found: dict[int, tuple[int, str, str]] = {}
        for _, _, hits in find_mentions(s, tokens, self._index(), max_words=max_words,
                                        joins=sentence_joins(s, tokens)):
            if len(hits) == 1:
                cik, ticker = next(iter(hits))
                found.setdefault(cik, (cik, ticker, display.get(cik, ticker)))
        return list(found.values())

    def companies_list(self) -> list[tuple[int, str, str]]:
        """(cik, ticker, display name) of every company (display_name)."""
        return [(cik, ticker, display_name(ticker, name)) for cik, ticker, name, _ in self._index()[2]]

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
                self._pr_cache.move_to_end(path)
                while len(self._pr_cache) > PR_CACHE_FILES:
                    self._pr_cache.popitem(last=False)
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
