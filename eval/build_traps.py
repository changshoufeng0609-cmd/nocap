#!/usr/bin/env python
"""Build the Receipts trap-draft corpus (trap_v1): AI-written earnings articles with planted, subtle errors.

Muse Wonder proposes, code decides. Wonder writes each article from a fact sheet and lists the errors it says it
planted; deterministic code then re-reads every figure in the article against the evidence store and writes
every gold label itself. A planted error that turns out to be true is dropped, an accidental error that is
genuinely false becomes an extra gold error, and a draft code cannot fully account for is rejected with its
reason logged (eval/corpus/trap_v1_rejects.jsonl).

Stages:
  1. Sheets (offline, deterministic). For each of the 10 companies, its 2 most recent quarters in
     data/facts.sqlite. A sheet lists the quarter's figures (revenue, YoY/QoQ growth, net income, EPS, margins,
     operating income, segments), prior-period comparisons, 1-2 peers' latest figures, a peer's not-yet-public
     figures (look-ahead bait) and the release's revenue outlook. Every item carries a fact id, and every
     CURRENT/PRIOR/PEER item uses only evidence public at the draft's dateline (`as_of`, release + 2 hours).
  2. Generate. One `muse-wonder-1.0` Responses call per job (Standard mode, reasoning.effort low, strict JSON
     schema): a 150-250-word newsroom article plus the planted errors (verbatim quote, value text, type, fact
     id). Responses are cached by request hash in data/trap_cache/wonder/; every call's usage and cost go to
     data/trap_cache/ledger.jsonl, and --budget caps the ledger total across runs.
  3. Validate (code decides). Code finds every financial figure by regex and reads its metric, company,
     segment, period, comparison and basis from the surrounding words, then matches it to the sheet:
       * a planted error must quote the article verbatim; its value must contradict the store's fact by more
         than twice the written rounding (half a unit of the last digit) for every filed version; code
         re-derives its type in the verifier's order (look_ahead, period, scale, basis, entity, else value);
       * every other figure must match a fact its words agree with, within rounding. One that matches no
         evidence at all is an accidental error and joins the gold; one in the rounding margin, one code
         cannot read, or one whose value belongs to a different fact than its words say, rejects the draft.
  4. Write eval/corpus/trap_v1.jsonl (accepted drafts; dev/test 70/30 by sheet with a fixed seed) and
     eval/corpus/trap_v1_rejects.jsonl.

Usage:
  .venv/bin/python eval/build_traps.py --sheets         # print the fact sheets and the job plan (no API)
  .venv/bin/python eval/build_traps.py --validate-dev   # verify frozen dev gold using its original cache (free)
  .venv/bin/python eval/build_traps.py --dry-run        # same dev-only check when a frozen corpus exists
  .venv/bin/python eval/build_traps.py --budget 0.90    # generate what is missing, validate, write the corpus
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import random
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS_DIR = ROOT / "eval" / "corpus"
TRAPS_PATH = CORPUS_DIR / "trap_v1.jsonl"
REJECTS_PATH = CORPUS_DIR / "trap_v1_rejects.jsonl"
CACHE_DIR = ROOT / "data" / "trap_cache"
WONDER_CACHE = CACHE_DIR / "wonder"
LEDGER_PATH = CACHE_DIR / "ledger.jsonl"
EDGAR_CACHE = ROOT / "data" / "cache" / "edgar"
PR_DIR = ROOT / "data" / "pr_facts"
DOTENV_PATH = ROOT / ".env"

VERSION = "trap_v1"
SEED = 20261029
DEV_FRACTION = 0.70
TICKERS = ("NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "JPM", "BAC", "AMD", "NFLX")
QUARTERS_PER_COMPANY = 2
AS_OF_DELAY = timedelta(hours=2)   # the dateline: two hours after the earnings release
MIN_WORDS, MAX_WORDS = 140, 265    # the brief asks for 150-250; a few words of slack before rejecting
MIN_ERRORS = 2                     # confirmed (planted + accidental) errors an accepted draft needs

BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-wonder-1.0"          # Standard mode only: never a -contributor ID
EFFORT = "low"
MAX_OUTPUT_TOKENS = 12000          # reasoning and JSON share it
TIMEOUT_S = 240.0
MAX_ATTEMPTS = 4
RETRYABLE_STATUS = frozenset({429, 500, 502, 503})  # 504 is not retried: the same request would time out again
PRICE_PER_M = {"input": 4.00, "cached_input": 0.20, "output": 20.00}  # muse-wonder-1.0 Standard, 2026-10-27
DEFAULT_BUDGET_USD = 0.90          # generation cap across runs (the eval run is budgeted separately)
EST_CALL_USD = 0.06                # reserved per call by the budget guard until real spend is known
SCHEMA_NAME = "trap_draft"
PROMPT_VERSION = "trap-prompt-v2.2"

ERROR_TYPES = ("period", "scale", "basis", "entity", "value", "look_ahead")
EXPECTED_REASON = {"period": "period_mismatch", "scale": "scale_mismatch", "basis": "basis_mismatch",
                   "entity": "entity_mismatch", "value": "value_mismatch", "look_ahead": "look_ahead"}

COMPANY_NAME = {"NVDA": "NVIDIA", "AAPL": "Apple", "MSFT": "Microsoft", "META": "Meta", "AMZN": "Amazon",
                "GOOGL": "Alphabet", "JPM": "JPMorgan Chase", "BAC": "Bank of America", "AMD": "AMD",
                "NFLX": "Netflix"}
# How drafts write each company. Case-sensitive, matched on word boundaries, possessives allowed.
COMPANY_ALIASES = {
    "NVDA": ("NVIDIA", "Nvidia", "NVDA"),
    "AAPL": ("Apple", "AAPL"),
    "MSFT": ("Microsoft", "MSFT"),
    "META": ("Meta Platforms", "Meta", "Facebook", "META"),
    "AMZN": ("Amazon.com", "Amazon", "AMZN"),
    "GOOGL": ("Alphabet", "Google", "GOOGL", "GOOG"),
    "JPM": ("JPMorgan Chase", "JPMorganChase", "JPMorgan", "J.P. Morgan", "JPM"),
    "BAC": ("Bank of America", "BofA", "BAC"),
    "AMD": ("Advanced Micro Devices", "AMD"),
    "NFLX": ("Netflix", "NFLX"),
}
BANKS = frozenset({"JPM", "BAC"})
CALENDAR_YEAR = frozenset({"META", "AMZN", "GOOGL", "JPM", "BAC", "NFLX", "AMD"})
PEERS = {"NVDA": ("AMD",), "AMD": ("NVDA",), "AAPL": ("MSFT", "GOOGL"), "MSFT": ("GOOGL", "AMZN"),
         "GOOGL": ("MSFT", "META"), "META": ("GOOGL", "AMZN"), "AMZN": ("MSFT", "GOOGL"), "JPM": ("BAC",),
         "BAC": ("JPM",), "NFLX": ("GOOGL",)}
# Segments a story leads with: (store segment name, how a draft may write it). The first also gets YoY growth.
SEGMENTS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "NVDA": (("Data Center", ("Data Center", "data center", "data-center")),),
    "AMD": (("Data Center", ("Data Center", "data center", "data-center")), ("Client", ("Client", "client"))),
    "AAPL": (("iPhone", ("iPhone",)), ("Services", ("Services", "services"))),
    "MSFT": (("Intelligent Cloud", ("Intelligent Cloud",)),
             ("Productivity and Business Processes", ("Productivity and Business Processes",))),
    "META": (("Family of Apps", ("Family of Apps",)), ("Reality Labs", ("Reality Labs",))),
    "AMZN": (("AWS", ("AWS", "Amazon Web Services")),
             ("Advertising services", ("advertising services", "Advertising services", "advertising",
                                       "Advertising"))),
    "GOOGL": (("Google Cloud", ("Google Cloud",)), ("YouTube ads", ("YouTube ads", "YouTube advertising",
                                                                    "YouTube ad"))),
    "JPM": (("CIB", ("Commercial & Investment Bank", "Commercial and Investment Bank", "CIB")),
            ("CCB", ("Consumer & Community Banking", "Consumer and Community Banking", "CCB"))),
    "BAC": (("Consumer Banking", ("Consumer Banking",)), ("Global Markets", ("Global Markets",))),
    "NFLX": (("UCAN", ("UCAN", "U.S. and Canada", "US and Canada")), ("EMEA", ("EMEA",))),
}
PEER_SEGMENT = {"AMD": "Data Center", "NVDA": "Data Center", "MSFT": "Intelligent Cloud", "GOOGL": "Google Cloud",
                "AMZN": "AWS", "META": "Family of Apps", "JPM": "CIB", "BAC": "Consumer Banking",
                "AAPL": "Services", "NFLX": "UCAN"}
PEER_SEGMENT_FOR = {("NFLX", "GOOGL"): "YouTube ads", ("META", "GOOGL"): "YouTube ads",
                    ("META", "AMZN"): "Advertising services"}
OPERATING_MARGIN = frozenset({"META", "GOOGL", "NFLX", "MSFT", "AMZN"})
GROSS_MARGIN = frozenset({"NVDA", "AMD", "AAPL", "MSFT"})
LEVEL_METRICS = ("revenue", "net_income", "eps_diluted", "operating_income", "operating_expenses",
                 "net_interest_income", "segment_revenue")

MONTH = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
         "November", "December")
QWORD = {"Q1": "first", "Q2": "second", "Q3": "third", "Q4": "fourth"}
SCALE_FACTOR = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9, "trillions": 1e12}


# ============================================================ small helpers

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_frac(*parts: Any) -> float:
    return int(sha256_text("|".join([str(SEED), *map(str, parts)]))[:12], 16) / float(16 ** 12)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def date_label(d: date) -> str:
    return f"{MONTH[d.month - 1]} {d.day}, {d.year}"


def fiscal_label(ticker: str, fy: int, fp: str) -> str:
    """'second quarter of fiscal 2027' (fiscal-year companies) or 'second quarter of 2026' (calendar years)."""
    return f"{QWORD.get(fp, fp)} quarter of {'' if ticker in CALENDAR_YEAR else 'fiscal '}{fy}"


def context_line(ticker: str, fy: int, fp: str, end: date) -> str:
    """The document-context line check() receives, in eval/build_corpus.py's format."""
    return (f"{COMPANY_NAME[ticker]} ({ticker}), {QWORD.get(fp, fp)} quarter of fiscal {fy}, "
            f"the quarter ended {date_label(end)}")


def item_hash(text: str, as_of: Optional[str], context: Optional[str]) -> str:
    """Same recipe as eval/build_corpus.py, so eval/eval.py's result cache keys work unchanged."""
    return sha256_text(json.dumps({"text": text, "as_of": as_of, "context": context}, sort_keys=True))


_BC = None


def build_corpus_module():
    """eval/build_corpus.py, for its number finder and HTML/sentence splitters (shared and already tested)."""
    global _BC
    if _BC is None:
        import importlib.util
        name = "receipts_eval_build_corpus_for_traps"
        spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / "build_corpus.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _BC = mod
    return _BC


# ============================================================ display formatting

def fmt_usd(v: float) -> str:
    """$96.2 billion, $2.30 billion, $784 million: the precision a newsroom would print."""
    sign, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e10:
        return f"{sign}${a / 1e9:,.1f} billion"
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f} billion"
    return f"{sign}${a / 1e6:,.0f} million"


def fmt_eps(v: float) -> str:
    return f"-${abs(v):.2f}" if v < 0 else f"${v:.2f}"


def fmt_pct(v: float, decimals: int) -> str:
    return f"{v:.{decimals}f}%"


# ============================================================ fact sheets

@dataclass
class SheetFact:
    """One fact-sheet item. value is in full units: USD (not billions), USD/share, or percent as a number."""

    id: str
    section: str                 # current | prior | peer | later | guidance
    ticker: str
    metric: str                  # revenue, revenue_growth, net_income, net_income_growth, eps_diluted,
    #                              operating_income, gross_margin, operating_margin, operating_expenses,
    #                              net_interest_income, segment_revenue, segment_revenue_growth, guidance
    unit: str                    # USD | USD/share | % | text
    value: Optional[float]
    display: str
    basis: str = "GAAP"
    segment: Optional[str] = None
    comparison: Optional[str] = None    # YoY | QoQ for growth facts
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    period_label: str = ""              # 'second quarter of fiscal 2027 (quarter ended July 26, 2026)'
    first_public: Optional[str] = None  # earliest acceptance time of its evidence (UTC)
    alternatives: list[float] = field(default_factory=list)  # other filed versions (restatements, recasts)
    evidence: list[dict] = field(default_factory=list)       # accession, form, source, accepted_at, value
    derived: Optional[str] = None       # formula for derived facts
    note: str = ""
    numbers: list[dict] = field(default_factory=list)        # guidance only: the figures in its sentence

    def values(self) -> list[float]:
        return [v for v in [self.value, *self.alternatives] if v is not None]

    def family(self) -> str:
        return metric_family(self.metric)

    def is_growth(self) -> bool:
        return self.metric.endswith("_growth")


def metric_family(metric: str) -> str:
    """revenue_growth -> revenue, segment_revenue_growth -> revenue, eps_diluted -> eps, ..."""
    m = metric[:-len("_growth")] if metric.endswith("_growth") else metric
    return {"segment_revenue": "revenue", "eps_diluted": "eps", "eps_basic": "eps"}.get(m, m)


@dataclass
class Sheet:
    sheet_id: str
    ticker: str
    company: str
    fiscal_year: int
    fiscal_period: str
    period_start: str
    period_end: str
    release_accession: str
    release_at: str
    as_of: str
    context: str
    facts: list[SheetFact]

    def by_id(self) -> dict[str, SheetFact]:
        return {f.id: f for f in self.facts}

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Sheet":
        d = dict(d)
        d["facts"] = [SheetFact(**f) for f in d["facts"]]
        return cls(**d)


def _quarters(store, cik: int) -> list[tuple[int, str, date, date]]:
    return [c for c in store.fiscal_calendar(cik) if c[1] != "FY"]


def _quarter_facts(store, cik: int, metric: str, start: date, end: date, *, basis: str = "GAAP",
                   segment: Optional[str] = None, as_of: Optional[datetime] = None) -> list:
    """Every filed version of one quarterly fact visible at as_of (XBRL, derived and press release)."""
    out = [f for f in store.facts(cik, metric, basis=basis, segment=segment, period_end=end, as_of=as_of)
           if f.period_start == start]
    return sorted(out, key=lambda f: to_utc(f.accepted_at))


def _distinct(values: Iterable[float], rel: float = 1e-9) -> list[float]:
    out: list[float] = []
    for v in values:
        if not any(abs(v - u) <= rel * max(1.0, abs(u)) for u in out):
            out.append(v)
    return out


def _evidence(facts: list) -> list[dict]:
    return [{"accession": f.accession, "form": f.form, "source": f.source, "accepted_at": iso_z(f.accepted_at),
             "value": f.value, "url": f.url} for f in facts]


def release_of(store, cik: int, start: date, end: date) -> Optional[tuple[str, datetime]]:
    """The earnings release that first published a quarter: the earliest press-release revenue fact's 8-K."""
    facts = _quarter_facts(store, cik, "revenue", start, end)
    prs = [f for f in facts if f.source == "press_release"]
    if prs:
        return prs[0].accession, to_utc(prs[0].accepted_at)
    return (facts[0].accession, to_utc(facts[0].accepted_at)) if facts else None


class SheetBuilder:
    """Fact sheets from receipts.store.Store. CURRENT, PRIOR and PEER items use only evidence public at as_of;
    LATER items are a peer's figures first published after as_of."""

    def __init__(self, store) -> None:
        self.store = store
        self.cik = {t: store.resolve_entity(t)[0] for t in TICKERS}
        self._rel: dict[tuple, Optional[tuple[str, datetime]]] = {}

    def quarters(self, ticker: str) -> list[tuple[int, str, date, date]]:
        return _quarters(self.store, self.cik[ticker])

    def release(self, ticker: str, q: tuple) -> Optional[tuple[str, datetime]]:
        key = (ticker, q[2], q[3])
        if key not in self._rel:
            self._rel[key] = release_of(self.store, self.cik[ticker], q[2], q[3])
        return self._rel[key]

    def shift(self, ticker: str, q: tuple, quarters: int) -> Optional[tuple]:
        cal = self.quarters(ticker)
        idx = next((i for i, c in enumerate(cal) if c[2] == q[2] and c[3] == q[3]), None)
        if idx is None or not 0 <= idx + quarters < len(cal):
            return None
        return cal[idx + quarters]

    def year_ago(self, ticker: str, q: tuple) -> Optional[tuple]:
        return next((c for c in self.quarters(ticker) if c[0] == q[0] - 1 and c[1] == q[1]), None)

    def latest_quarters(self, ticker: str, n: int = QUARTERS_PER_COMPANY) -> list[tuple]:
        """The n most recent quarters that have a GAAP revenue fact (any source)."""
        return [q for q in self.quarters(ticker) if self.release(ticker, q) is not None][-n:]

    def peer_quarter(self, peer: str, as_of: datetime, *, public: bool, near: date) -> Optional[tuple]:
        """The peer's latest quarter public at as_of, or (public=False) its quarter ending within 45 days of
        `near` that was first published after as_of."""
        best = None
        for q in self.quarters(peer):
            rel = self.release(peer, q)
            if rel is None:
                continue
            if public and rel[1] <= as_of:
                best = q
            if not public and rel[1] > as_of and abs((q[3] - near).days) <= 45:
                return q
        return best

    # ------------------------------------------------------------ fact helpers (ids are assigned by build())
    def level(self, section: str, ticker: str, metric: str, q: tuple, as_of: datetime, *, basis: str = "GAAP",
              segment: Optional[str] = None, later: bool = False, note: str = "") -> Optional[SheetFact]:
        fy, fp, start, end = q
        facts = _quarter_facts(self.store, self.cik[ticker], metric, start, end, basis=basis, segment=segment,
                               as_of=None if later else as_of)
        if not facts or (later and to_utc(facts[0].accepted_at) <= as_of):
            return None
        vals = _distinct(f.value for f in facts)
        latest = facts[-1].value
        eps = metric.startswith("eps")
        return SheetFact(
            id="", section=section, ticker=ticker, metric=metric, unit="USD/share" if eps else "USD", value=latest,
            display=fmt_eps(latest) if eps else fmt_usd(latest), basis=basis, segment=segment, fiscal_year=fy,
            fiscal_period=fp, period_start=start.isoformat(), period_end=end.isoformat(),
            period_label=f"{fiscal_label(ticker, fy, fp)} (quarter ended {date_label(end)})",
            first_public=iso_z(min(to_utc(f.accepted_at) for f in facts)),
            alternatives=[v for v in vals if abs(v - latest) > 1e-9 * max(1.0, abs(latest))],
            evidence=_evidence(facts), note=note)

    def growth(self, ticker: str, metric: str, q: tuple, base_q: Optional[tuple], comparison: str,
               as_of: datetime, *, segment: Optional[str] = None) -> Optional[SheetFact]:
        if base_q is None:
            return None
        cik = self.cik[ticker]
        cur = _quarter_facts(self.store, cik, metric, q[2], q[3], segment=segment, as_of=as_of)
        base = _quarter_facts(self.store, cik, metric, base_q[2], base_q[3], segment=segment, as_of=as_of)
        if not cur or not base or not base[-1].value or base[-1].value < 0 or cur[-1].value < 0:
            return None
        combos = _distinct(100.0 * (c / b - 1.0) for c in _distinct(f.value for f in cur)
                           for b in _distinct(f.value for f in base) if b > 0)
        g = 100.0 * (cur[-1].value / base[-1].value - 1.0)
        if max(combos) - min(combos) > 1.0:
            return None  # recast segments: the growth rate itself is ambiguous
        name = "segment_revenue_growth" if segment else f"{metric}_growth"
        what = f"{segment} revenue" if segment else metric.replace("_", " ")
        return SheetFact(
            id="", section="current", ticker=ticker, metric=name, unit="%", value=g,
            display=fmt_pct(g, 0 if abs(g) >= 10 else 1), segment=segment, comparison=comparison,
            fiscal_year=q[0], fiscal_period=q[1], period_start=q[2].isoformat(), period_end=q[3].isoformat(),
            period_label=f"{fiscal_label(ticker, q[0], q[1])} vs {fiscal_label(ticker, base_q[0], base_q[1])}",
            first_public=iso_z(max(to_utc(cur[0].accepted_at), to_utc(base[0].accepted_at))),
            alternatives=[c for c in combos if abs(c - g) > 1e-9], evidence=_evidence(cur[-1:] + base[-1:]),
            derived=f"{what} for the quarter ended {q[3]} / the quarter ended {base_q[3]} - 1")

    def ratio(self, ticker: str, name: str, q: tuple, as_of: datetime, *, basis: str = "GAAP") -> Optional[SheetFact]:
        """gross_margin (gross profit, or revenue minus cost of revenue, over revenue) or operating_margin."""
        cik = self.cik[ticker]
        rev = _quarter_facts(self.store, cik, "revenue", q[2], q[3], as_of=as_of)
        if basis == "non-GAAP":
            rev = _quarter_facts(self.store, cik, "revenue", q[2], q[3], basis="non-GAAP", as_of=as_of) or rev
        if not rev:
            return None
        if name == "operating_margin":
            num, formula = _quarter_facts(self.store, cik, "operating_income", q[2], q[3], basis=basis,
                                          as_of=as_of), "operating income / revenue"
            pct_of = lambda r, n: 100.0 * n / r  # noqa: E731
        else:
            num = _quarter_facts(self.store, cik, "gross_profit", q[2], q[3], basis=basis, as_of=as_of)
            formula, pct_of = "gross profit / revenue", (lambda r, n: 100.0 * n / r)
            if not num:
                num = _quarter_facts(self.store, cik, "cost_of_revenue", q[2], q[3], basis=basis, as_of=as_of)
                formula, pct_of = "(revenue - cost of revenue) / revenue", (lambda r, n: 100.0 * (r - n) / r)
        if not num:
            return None
        pct = pct_of(rev[-1].value, num[-1].value)
        combos = _distinct(pct_of(r, n) for r in _distinct(f.value for f in rev) for n in _distinct(f.value for f in num))
        return SheetFact(
            id="", section="current", ticker=ticker, metric=name, unit="%", value=pct, display=fmt_pct(pct, 1),
            basis=basis, fiscal_year=q[0], fiscal_period=q[1], period_start=q[2].isoformat(),
            period_end=q[3].isoformat(),
            period_label=f"{fiscal_label(ticker, q[0], q[1])} (quarter ended {date_label(q[3])})",
            first_public=iso_z(max(to_utc(rev[0].accepted_at), to_utc(num[0].accepted_at))),
            alternatives=[c for c in combos if abs(c - pct) > 1e-9], evidence=_evidence(rev[-1:] + num[-1:]),
            derived=formula)

    # ------------------------------------------------------------ the sheet
    def build(self, ticker: str, q: tuple, *, as_of_delay: timedelta = AS_OF_DELAY) -> Optional[Sheet]:
        fy, fp, start, end = q
        rel = self.release(ticker, q)
        if rel is None:
            return None
        accession, released = rel
        as_of = released + as_of_delay
        facts: list[SheetFact] = []

        def add(f: Optional[SheetFact]) -> Optional[SheetFact]:
            if f is not None:
                f.id = f"F{len(facts) + 1:02d}"
                facts.append(f)
            return f

        def differs(a: Optional[SheetFact], b: Optional[SheetFact], rel_gap: float, abs_gap: float = 0.0) -> bool:
            return a is not None and b is not None and abs(a.value - b.value) > max(rel_gap * abs(a.value), abs_gap)

        prior_q, yago_q = self.shift(ticker, q, -1), self.year_ago(ticker, q)
        bank = ticker in BANKS
        rev = add(self.level("current", ticker, "revenue", q, as_of, note="total net revenue" if bank else ""))
        if rev is None:
            return None
        ng_rev = self.level("current", ticker, "revenue", q, as_of, basis="non-GAAP",
                            note="managed / fully taxable-equivalent basis" if bank else "")
        if differs(rev, ng_rev, 0.003):
            add(ng_rev)
        add(self.growth(ticker, "revenue", q, yago_q, "YoY", as_of))
        add(self.growth(ticker, "revenue", q, prior_q, "QoQ", as_of))
        add(self.level("current", ticker, "net_income", q, as_of))
        add(self.growth(ticker, "net_income", q, yago_q, "YoY", as_of))
        eps = add(self.level("current", ticker, "eps_diluted", q, as_of))
        ng_eps = self.level("current", ticker, "eps_diluted", q, as_of, basis="non-GAAP")
        if differs(eps, ng_eps, 0.0, 0.015):
            add(ng_eps)
        if not bank:
            oi = add(self.level("current", ticker, "operating_income", q, as_of))
            ng_oi = self.level("current", ticker, "operating_income", q, as_of, basis="non-GAAP")
            if differs(oi, ng_oi, 0.01):
                add(ng_oi)
            if ticker in GROSS_MARGIN:
                gm = add(self.ratio(ticker, "gross_margin", q, as_of))
                ng = self.ratio(ticker, "gross_margin", q, as_of, basis="non-GAAP")
                if differs(gm, ng, 0.0, 0.3):
                    add(ng)
            if ticker in OPERATING_MARGIN:
                add(self.ratio(ticker, "operating_margin", q, as_of))
        else:
            add(self.level("current", ticker, "operating_expenses", q, as_of, note="total noninterest expense"))
            nii = add(self.level("current", ticker, "net_interest_income", q, as_of))
            ng_nii = self.level("current", ticker, "net_interest_income", q, as_of, basis="non-GAAP",
                                note="fully taxable-equivalent basis")
            if differs(nii, ng_nii, 0.003):
                add(ng_nii)
        segs: list[str] = []
        for i, (seg, _aliases) in enumerate(SEGMENTS.get(ticker, ())):
            f = add(self.level("current", ticker, "segment_revenue", q, as_of, segment=seg,
                               note="segment net revenue" if bank else ""))
            if f is not None:
                segs.append(seg)
                if i == 0:
                    add(self.growth(ticker, "segment_revenue", q, yago_q, "YoY", as_of, segment=seg))
        for pq, tag in ((prior_q, "previous quarter"), (yago_q, "year-ago quarter")):
            if pq is None:
                continue
            add(self.level("prior", ticker, "revenue", pq, as_of, note=tag))
            if tag == "previous quarter":
                add(self.level("prior", ticker, "net_income", pq, as_of, note=tag))
            add(self.level("prior", ticker, "eps_diluted", pq, as_of, note=tag))
            if segs:
                add(self.level("prior", ticker, "segment_revenue", pq, as_of, segment=segs[0], note=tag))
        for peer in PEERS.get(ticker, ()):
            seg = PEER_SEGMENT_FOR.get((ticker, peer), PEER_SEGMENT.get(peer))
            pq = self.peer_quarter(peer, as_of, public=True, near=end)
            if pq is not None:
                add(self.level("peer", peer, "revenue", pq, as_of))
                add(self.level("peer", peer, "eps_diluted", pq, as_of))
                if seg:
                    add(self.level("peer", peer, "segment_revenue", pq, as_of, segment=seg))
            lq = self.peer_quarter(peer, as_of, public=False, near=end)
            if lq is not None:
                lrel = self.release(peer, lq)
                note = f"first published {iso_z(lrel[1])}, after the dateline" if lrel else ""
                if seg:
                    add(self.level("later", peer, "segment_revenue", lq, as_of, segment=seg, later=True, note=note))
                add(self.level("later", peer, "revenue", lq, as_of, later=True, note=note))
        add(guidance_fact(ticker, accession, q))
        return Sheet(sheet_id=f"{ticker}-FY{fy}{fp}", ticker=ticker, company=COMPANY_NAME[ticker],
                     fiscal_year=fy, fiscal_period=fp, period_start=start.isoformat(), period_end=end.isoformat(),
                     release_accession=accession, release_at=iso_z(released), as_of=iso_z(as_of),
                     context=context_line(ticker, fy, fp, end), facts=facts)


# ============================================================ guidance (from the cached EX-99.1)

_GUIDE_CUE = re.compile(r"\b(?:expects?|expected|outlook|guidance|forecast|anticipates?|projects?)\b", re.I)
_GUIDE_SUBJECT = re.compile(r"\b(?:revenue|net sales)\b", re.I)
_GUIDE_PAST = re.compile(r"\b(?:was|were|grew|increased|decreased|in-line|consistent with our)\b", re.I)


def _release_url(ticker: str, accession: str) -> Optional[str]:
    with contextlib.suppress(OSError, ValueError, KeyError):
        return json.loads((PR_DIR / ticker / f"{accession}.report.json").read_text(encoding="utf-8"))["url"]
    return None


def guidance_sentence(ticker: str, accession: str) -> Optional[str]:
    """The release's revenue outlook sentence, read offline from data/cache/edgar (None if absent)."""
    url = _release_url(ticker, accession)
    path = EDGAR_CACHE / f"{sha256_text(url)}.bin" if url else None
    if path is None or not path.exists():
        return None
    try:
        bc = build_corpus_module()
        blocks = bc.html_blocks(path.read_bytes())
    except Exception:  # noqa: BLE001 - guidance is optional
        return None
    for b in blocks:
        for _off, s in bc.split_sentences(b):
            if (len(s) < 260 and _GUIDE_CUE.search(s) and _GUIDE_SUBJECT.search(s) and re.search(r"\$\s?\d", s)
                    and not _GUIDE_PAST.search(s) and "F/X" not in s):
                return s
    return None


def guidance_fact(ticker: str, accession: str, q: tuple) -> Optional[SheetFact]:
    s = guidance_sentence(ticker, accession)
    if not s:
        return None
    nums = [{"text": n.text, "kind": n.kind, "value": n.value, "scale": n.scale, "decimals": n.decimals,
             "is_range": n.is_range} for n in build_corpus_module().find_numbers(s)]
    if not nums:
        return None
    return SheetFact(id="", section="guidance", ticker=ticker, metric="guidance", unit="text", value=None,
                     display=s, fiscal_year=q[0], fiscal_period=q[1], period_label="the release's outlook",
                     note="forward-looking: quote it faithfully or leave it out; never alter it", numbers=nums,
                     evidence=[{"accession": accession, "form": "8-K", "source": "press_release"}])


# ============================================================ evidence index (for diagnosing a wrong figure)

@dataclass(frozen=True)
class Ev:
    """One known value: a sheet fact or any other filed (or derived) quarterly figure in the store."""

    ticker: str
    metric: str             # as SheetFact.metric
    segment: Optional[str]
    basis: str
    comparison: Optional[str]
    period_end: str
    value: float
    unit: str
    first_public: Optional[str]
    fact_id: Optional[str] = None


def sheet_evidence(sheet: Sheet) -> list[Ev]:
    out = []
    for f in sheet.facts:
        if f.value is None:
            continue
        for v in f.values():
            out.append(Ev(f.ticker, f.metric, f.segment, f.basis, f.comparison, f.period_end or "", v, f.unit,
                          f.first_public, f.id))
    return out


class StoreIndex:
    """Every quarterly level fact of the 10 companies (all bases, consolidated and segment), plus derived YoY/QoQ
    growth and margins, so code can tell which period, basis or company a wrong figure really belongs to."""

    def __init__(self, store, tickers: Iterable[str] = TICKERS) -> None:
        self.items: list[Ev] = []
        for t in tickers:
            cik = store.resolve_entity(t)[0]
            cal = _quarters(store, cik)
            by_q: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
            for metric in LEVEL_METRICS + ("gross_profit", "cost_of_revenue"):
                store_metric = metric
                for f in store.facts(cik, store_metric, segment="*"):
                    if f.period_start is None or not 80 <= (f.period_end - f.period_start).days <= 100:
                        continue
                    m = "segment_revenue" if metric == "revenue" and f.segment else metric
                    if metric == "segment_revenue" and not f.segment:
                        continue
                    key = (m, f.segment, f.basis)
                    by_q[(f.period_start, f.period_end)][key].append(f)
                    if m in LEVEL_METRICS:
                        self.items.append(Ev(t, m, f.segment, f.basis, None, f.period_end.isoformat(), f.value,
                                             "USD/share" if m.startswith("eps") else "USD",
                                             iso_z(f.accepted_at)))
            for i, (fy, fp, start, end) in enumerate(cal):
                cur = by_q.get((start, end), {})
                prev = by_q.get((cal[i - 1][2], cal[i - 1][3]), {}) if i else {}
                yq = next((c for c in cal if c[0] == fy - 1 and c[1] == fp), None)
                yago = by_q.get((yq[2], yq[3]), {}) if yq else {}
                for (m, seg, basis), fs in cur.items():
                    if m not in ("revenue", "net_income", "segment_revenue", "operating_income"):
                        continue
                    for comp, base in (("YoY", yago), ("QoQ", prev)):
                        for b in base.get((m, seg, basis), []):
                            for c in fs:
                                if b.value and b.value > 0 and c.value >= 0:
                                    self.items.append(Ev(t, f"{m}_growth", seg, basis, comp, end.isoformat(),
                                                         100.0 * (c.value / b.value - 1.0), "%",
                                                         iso_z(max(to_utc(c.accepted_at), to_utc(b.accepted_at)))))
                for basis in ("GAAP", "non-GAAP"):
                    revs = cur.get(("revenue", None, basis)) or cur.get(("revenue", None, "GAAP"), [])
                    for r in revs:
                        if not r.value:
                            continue
                        for g in cur.get(("gross_profit", None, basis), []):
                            self.items.append(Ev(t, "gross_margin", None, basis, None, end.isoformat(),
                                                 100.0 * g.value / r.value, "%",
                                                 iso_z(max(to_utc(g.accepted_at), to_utc(r.accepted_at)))))
                        for c in cur.get(("cost_of_revenue", None, basis), []):
                            self.items.append(Ev(t, "gross_margin", None, basis, None, end.isoformat(),
                                                 100.0 * (r.value - c.value) / r.value, "%",
                                                 iso_z(max(to_utc(c.accepted_at), to_utc(r.accepted_at)))))
                        for o in cur.get(("operating_income", None, basis), []):
                            self.items.append(Ev(t, "operating_margin", None, basis, None, end.isoformat(),
                                                 100.0 * o.value / r.value, "%",
                                                 iso_z(max(to_utc(o.accepted_at), to_utc(r.accepted_at)))))


# ============================================================ reading the draft (code reads every figure)

@dataclass
class Figure:
    """One financial figure in the draft, as code reads it."""

    idx: int
    start: int                 # global offsets of the figure text, e.g. "$96.2 billion"
    end: int
    text: str
    kind: str                  # money | pct | pp | bps
    value: float               # as written, unsigned, scale not applied
    sign: int
    decimals: int
    scale: str
    is_range: bool
    sent: int                  # sentence index
    per_share: bool = False
    growth: bool = False
    comparison: Optional[str] = None   # YoY | QoQ, from the words
    basis: Optional[str] = None        # GAAP | non-GAAP | both
    family: Optional[str] = None       # revenue | net_income | eps | operating_income | gross_margin |
    #                                    operating_margin | operating_expenses | net_interest_income | other
    segment: Optional[str] = None      # canonical segment the words name
    entity: Optional[str] = None       # ticker
    entity_explicit: bool = False
    role: str = "current"              # current | prior | forward
    prior: Optional[str] = None        # year_ago | prior_q (for role prior)
    label_end: Optional[str] = None    # period end named by an explicit label next to the figure (ISO date)
    hedge: Optional[str] = None
    comparison_conflict: bool = False  # the sentence also has an unclaimed cue for the other comparison

    def claimed(self) -> float:
        """Full units: USD, USD/share or percent, signed by the draft's own words."""
        v = self.value * (SCALE_FACTOR[self.scale] if self.kind == "money" else 1.0)
        return self.sign * v

    def tolerance(self) -> float:
        step = 10.0 ** -self.decimals
        return 0.5 * step * (SCALE_FACTOR[self.scale] if self.kind == "money" else 1.0)


@dataclass
class Sentence:
    start: int
    end: int
    text: str
    para: int


# Comparison cues for a growth rate. The earliest cue after the figure (up to the next percentage) wins, so
# "up 18% from the previous quarter and 106% from a year earlier" reads 18% as QoQ and 106% as YoY.
_YOY = re.compile(r"year[- ]over[- ]year|year[- ]on[- ]year|\ba year (?:earlier|ago|before)\b|\byear[- ]ago\b|"
                  r"\byear-earlier\b|\b(?:same|prior[- ]year) (?:quarter|period)\b|\blast year\b|\bYoY\b|\bY/Y\b",
                  re.I)
_QOQ = re.compile(r"quarter[- ]over[- ]quarter|quarter[- ]on[- ]quarter|\bsequential(?:ly)?\b|"
                  r"\b(?:the )?(?:previous|prior|preceding|last) quarter\b|\bQoQ\b|\bQ/Q\b", re.I)
_YEAR_AGO_CUE = re.compile(r"\ba year (?:earlier|ago|before)\b|\byear[- ]ago\b|\byear-earlier\b|\blast year\b|"
                           r"\bprior[- ]year\b|\bsame (?:quarter|period) (?:a year ago|last year)\b", re.I)
_PRIOR_Q_CUE = re.compile(r"\b(?:previous|prior|preceding|last) quarter\b|\bsequential", re.I)
_PRIOR_BEFORE = re.compile(r"(?:\bfrom|\bcompared (?:with|to)|\bversus|\bvs\.?|\bup from|\bdown from|\bthan|"
                           r"\bagainst)\s*(?:the\s+)?$", re.I)
_GROWTH_BEFORE = re.compile(
    r"(?:\bup|\bdown|\bincreas(?:e|ed|ing)|\bdecreas(?:e|ed|ing)|\bgrew|\bgrow(?:th|ing)?|\brose|\brising|\bfell|"
    r"\bdeclin(?:e|ed|ing)|\bjump(?:ed)?|\bclimb(?:ed)?|\bsurg(?:e|ed)|\bgain(?:ed)?|\badvanc(?:e|ed)|"
    r"\bslipp?(?:ed)?|\bdropp?(?:ed)?|\bslid|\bexpand(?:ed)?|\bshr[ai]nk|\bshrunk|\bsoar(?:ed)?|\bplung(?:e|ed)|"
    r"\bsank|\bfalling|\bby)\b[^$%\d]{0,25}$", re.I)
_GROWTH_AFTER = re.compile(r"^\s*(?:growth|increase|decrease|jump|gain|rise|climb|decline|drop|surge|higher|lower)\b",
                           re.I)
_DOWN_BEFORE = re.compile(r"(?:\bdown|\bdecreas\w*|\bfell|\bdeclin\w*|\bdropp?\w*|\bslipp?\w*|\bslid|\bshr[ai]nk\w*|"
                          r"\bshrunk|\bplung\w*|\bsank|\bfalling|\blower)\b[^$%\d]{0,25}$", re.I)
_HEDGE_BEFORE = re.compile(r"\b(?:approximately|approx\.|about|around|roughly|nearly|almost|over|more than|"
                           r"less than|at least|up to|just over|just under|in excess of|some|close to|upwards of|"
                           r"north of|south of|below|above|topping|exceeding)\s*$", re.I)
_PER_SHARE_AFTER = re.compile(r"^\s*(?:a|per|for each)\s+(?:diluted\s+|basic\s+)?(?:common\s+)?share\b", re.I)
_FL_CUE = re.compile(r"\b(?:expects?|expected|expecting|forecasts?|forecasted|outlook|guidance|guided|projects?|"
                     r"projected|anticipates?|anticipated|sees)\b", re.I)
_NONGAAP_CUE = re.compile(r"\bnon-?GAAP\b|\badjusted\b|\bmanaged\b|\bFTE\b|fully[- ]taxable[- ]equivalent", re.I)
_GAAP_CUE = re.compile(r"(?<![-\w])GAAP\b")
_BOTH_BASIS = re.compile(r"\bGAAP and non-GAAP\b|\bboth GAAP and non-GAAP\b|\bon both a GAAP and", re.I)
_PRONOUN_START = re.compile(r"^(?:Its|It|The company(?:'s|’s)?|The (?:chipmaker|bank|lender|retailer|streamer|"
                            r"group|firm|rival|search giant|tech giant|iPhone maker|software maker|cloud giant|"
                            r"social media giant|e-commerce giant)(?:'s|’s)?|That company)\b")
_FIGURE_ANAPHOR = re.compile(r"^(?:That|This|The figure|That figure|The total|That total|The result)\b")
_CLAUSE_BREAK = re.compile(r";|\s[—–]\s|,\s+(?:while|whereas|but|although|though|even as)\s|"
                           r"\s(?:while|whereas|although)\s")

# Metric keywords (news style). Overlaps resolve to the longest match.
_METRIC_KEYWORDS: list[tuple[str, str]] = [
    ("eps", r"\b(?:diluted |basic )?(?:earnings|EPS|net income|profit) per (?:diluted |basic )?(?:common )?share\b|"
            r"\bper (?:diluted |basic )?share\b|\ba (?:diluted )?share\b|\bEPS\b"),
    ("net_interest_income", r"\bnet interest income\b|\bNII\b"),
    ("gross_margin", r"\bgross margins?\b"),
    ("operating_margin", r"\boperating margins?\b"),
    ("operating_income", r"\boperating (?:income|profit)\b|\bincome from operations\b"),
    ("net_income", r"\bnet (?:income|earnings|profit)\b|\bprofits?\b|\bearned\b|\bbottom line\b"),
    ("operating_expenses", r"\b(?:total )?(?:operating expenses|costs and expenses|noninterest expenses?|expenses)\b"),
    ("revenue", r"\b(?:total |net |quarterly )?revenues?\b|\bnet sales\b|\bsales\b|\btop line\b"),
    ("other", r"\bfree cash flow\b|\bcash flow\b|\bcapital expenditures\b|\bcapex\b|\btax rate\b|\bbuybacks?\b|"
              r"\brepurchases?\b|\bdividends?\b|\bmarket (?:value|cap\w*)\b|\bshares\b|\bbacklog\b|\bdeposits\b|"
              r"\bloans\b|\bcharges?\b|\bsubscribers\b|\bmembers(?:hips)?\b|\busers\b"),
]
_METRIC_RX = [(m, re.compile(rx)) for m, rx in _METRIC_KEYWORDS]
_METRIC_RX_I = [(m, re.compile(rx, re.I)) for m, rx in _METRIC_KEYWORDS if m not in ("eps",)]
_METRIC_RX_I.insert(0, ("eps", re.compile(_METRIC_KEYWORDS[0][1].replace(r"\bEPS\b", r"\bEPS\b"), re.I)))
_MONTH_RX = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_LABEL_ENDED = re.compile(r"\bquarter ended " + _MONTH_RX + r"\s+(\d{1,2}),?\s+(\d{4})", re.I)
_LABEL_ENDED_NOYEAR = re.compile(r"\bquarter ended (" + _MONTH_RX + r")\s+(\d{1,2})\b(?!,?\s+\d{4})", re.I)
_LABEL_MONTH_Q = re.compile(r"\b(" + _MONTH_RX + r") quarter\b")
_LABEL_ORD = re.compile(r"\b(first|second|third|fourth)[- ]quarter(?: of)?(?: fiscal(?: year)?)? ?(\d{4})?\b|"
                        r"\bfiscal (\d{4}) (first|second|third|fourth)[- ]quarter\b|"
                        r"\bfiscal (first|second|third|fourth)[- ]quarter\b|\bQ([1-4])(?: (?:FY)?\s?'?(\d{2,4}))?\b",
                        re.I)


def segment_aliases() -> list[tuple[str, str, str]]:
    """(alias, ticker, store segment) for every segment a sheet can carry, longest alias first."""
    out: list[tuple[str, str, str]] = []
    for t, segs in SEGMENTS.items():
        for seg, aliases in segs:
            out += [(a, t, seg) for a in aliases]
    for t, seg in PEER_SEGMENT.items():
        if not any(o[1] == t and o[2] == seg for o in out):
            out.append((seg, t, seg))
    for (_s, t), seg in PEER_SEGMENT_FOR.items():
        if not any(o[1] == t and o[2] == seg for o in out):
            out.append((seg, t, seg))
    return sorted(set(out), key=lambda x: -len(x[0]))


_SEG_ALIASES = segment_aliases()


def _company_mentions(s: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for t, aliases in COMPANY_ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            for m in re.finditer(r"(?<![\w.])" + re.escape(a) + r"(?:'s|’s)?(?![\w])", s):
                if any(not (m.end() <= x or m.start() >= y) for x, y, _ in found):
                    continue
                found.append((m.start(), m.end(), t))
    return sorted(found)


def _segment_mentions(s: str) -> list[tuple[int, int, str, str]]:
    """(start, end, ticker, segment) where the words use a segment as the subject of a revenue figure:
    'Data Center revenue', 'revenue from AWS', 'Google Cloud grew', 'the Intelligent Cloud segment posted'."""
    out: list[tuple[int, int, str, str]] = []
    for alias, t, seg in _SEG_ALIASES:
        a = re.escape(alias)
        pats = (rf"(?<![\w-]){a}(?:'s|’s)?\s+(?:segment\s+|business\s+|unit\s+|division\s+)?"
                rf"(?:net\s+)?(?:revenues?|sales)\b",
                rf"\b(?:revenues?|sales)\s+(?:from|at|in|for)\s+(?:the\s+|its\s+)?{a}(?![\w-])",
                rf"(?<![\w-]){a}(?:'s|’s)?(?:\s+(?:segment|business|unit|division))?(?:,)?\s+(?:grew|rose|climbed|"
                rf"jumped|increased|surged|gained|advanced|posted|generated|brought in|contributed|reported|"
                rf"delivered|reached|totaled|was|came in|added)\b",
                rf"(?<![\w-]){a}(?:'s|’s)?(?:\s+(?:segment|business|unit|division))?,?\s+where\s+(?:its\s+)?"
                rf"(?:revenues?|sales)\b",
                rf"(?<![\w-]){a}(?:'s|’s)?[^.;$%\d]{{0,45}}?\b(?:producing|generating|posting|delivering|bringing in|"
                rf"reporting|with)\s+(?:revenues?|sales)\b")
        for p in pats:
            for m in re.finditer(p, s):
                if any(not (m.end() <= x or m.start() >= y) for x, y, _, _ in out):
                    continue
                out.append((m.start(), m.end(), t, seg))
    return sorted(out)


def _metric_hits(s: str) -> list[tuple[int, int, str]]:
    hits: list[tuple[int, int, str]] = []
    for metric, rx in _METRIC_RX_I:
        for m in rx.finditer(s):
            hits.append((m.start(), m.end(), metric))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out: list[tuple[int, int, str]] = []
    for h in sorted(hits, key=lambda h: -(h[1] - h[0])):
        if all(h[1] <= o[0] or h[0] >= o[1] for o in out):
            out.append(h)
    return sorted(out)


def _labels(text: str, cal: Optional[list[tuple[int, str, date, date]]], default_year: int) -> list[tuple[int, str]]:
    """(position, period end) for every explicit period label in text: 'quarter ended June 30, 2026', 'June
    quarter', 'second quarter of 2026', 'fiscal third quarter', 'Q2'. Calendar labels resolve on the company's
    own fiscal calendar when it is known."""
    out: list[tuple[int, str]] = []
    month_no = {m.lower(): i + 1 for i, m in enumerate(MONTH)}
    for m in _LABEL_ENDED.finditer(text):
        with contextlib.suppress(ValueError, KeyError):
            out.append((m.start(), date(int(m.group(2)), month_no[m.group(0).split()[2].lower()],
                                        int(m.group(1))).isoformat()))
    for m in _LABEL_ENDED_NOYEAR.finditer(text):
        ends = [q[3] for q in cal or [] if q[3].month == month_no[m.group(1).lower()]]
        if ends:
            out.append((m.start(), min(ends, key=lambda d: abs(d.year - default_year)).isoformat()))
    for m in _LABEL_MONTH_Q.finditer(text):
        mo = month_no[m.group(1).lower()]
        ends = [q[3] for q in cal or [] if q[3].month == mo or ((q[3].month % 12) + 1 == mo and q[3].day >= 25)]
        if ends:
            out.append((m.start(), min(ends, key=lambda d: abs(d.year - default_year)).isoformat()))
    for m in _LABEL_ORD.finditer(text):
        word = (m.group(1) or m.group(4) or m.group(5) or "").lower()
        qn = {"first": 1, "second": 2, "third": 3, "fourth": 4}.get(word) or (int(m.group(6)) if m.group(6) else None)
        yy = m.group(2) or m.group(3) or m.group(7)
        fy = (int(yy) + (2000 if len(yy) == 2 else 0)) if yy else None
        cands = [q for q in cal or [] if qn and q[1] == f"Q{qn}" and (fy is None or q[0] == fy)]
        if cands:
            out.append((m.start(), min(cands, key=lambda q: abs(q[0] - default_year))[3].isoformat()))
    return sorted(out)


def _label_end(text: str, ticker: str, cal: Optional[list[tuple[int, str, date, date]]],
               default_year: int, *, last: bool = False) -> Optional[str]:
    """The period end named by the first label in text (or the last one, for text before a figure)."""
    found = _labels(text, cal, default_year)
    return (found[-1] if last else found[0])[1] if found else None


def split_paragraphs(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for m in [*re.finditer(r"\n[ \t]*\n\s*", text), None]:
        chunk = text[pos:m.start() if m else len(text)]
        if chunk.strip():
            out.append((pos + len(chunk) - len(chunk.lstrip()), chunk.strip()))
        if m:
            pos = m.end()
    return out


_POSSESSIVE_STOP = re.compile(r"(?<=[A-Za-z]['’]s\.)\s+(?=[A-Z$])")


def sentences_of(text: str) -> list[Sentence]:
    """Sentences with offsets into text, from eval/build_corpus.py's splitter. It reads the "s." of a sentence
    ending in a possessive ("the market leader's.") as an initial, so those are split here as well."""
    bc = build_corpus_module()
    out: list[Sentence] = []
    for p, (pstart, ptext) in enumerate(split_paragraphs(text)):
        for off, s in bc.split_sentences(ptext.replace("\n", " ")):
            pos = 0
            for m in [*_POSSESSIVE_STOP.finditer(s), None]:
                piece = s[pos:m.start() if m else len(s)]
                if piece.strip():
                    out.append(Sentence(pstart + off + pos, pstart + off + pos + len(piece), piece, p))
                if m:
                    pos = m.end()
    return out


@dataclass
class Reading:
    sentences: list[Sentence]
    figures: list[Figure]
    other_numeric: list[str]   # sentences with digits that are not financial figures, dates, years or labels
    mentions: dict[int, list[tuple[int, int, str]]]


def _latest_comparison(window: str) -> Optional[str]:
    y = [m.start() for m in _YOY.finditer(window)]
    q = [m.start() for m in _QOQ.finditer(window)]
    if not y and not q:
        return None
    return "YoY" if (y and (not q or max(y) > max(q))) else "QoQ"


def _earliest_comparison(window: str) -> Optional[str]:
    y, q = _YOY.search(window), _QOQ.search(window)
    if y and (not q or y.start() <= q.start()):
        return "YoY"
    return "QoQ" if q else None


def read_draft(text: str, subject: str, *, calendars: Optional[dict[str, list]] = None,
               default_year: int = 2026) -> Reading:
    """Code's reading of every financial figure: metric family, segment, company, period role, comparison, basis.

    Words are read inside the figure's clause. A figure takes its metric (and segment) from a keyword between the
    previous figure and itself; with none, from a keyword right after it ("$2.46 per diluted share"); else from the
    previous figure in the clause ("up 106% ... to $96.2 billion"). Its company is the nearest company named before
    it in the sentence, else a pronoun's antecedent ("Its ..."), else the company that owns the segment it names,
    else the story's subject."""
    bc = build_corpus_module()
    sents = sentences_of(text)
    figures: list[Figure] = []
    other: list[str] = []
    mentions: dict[int, list[tuple[int, int, str]]] = {}
    prev_entities: list[str] = []
    prev_figs: list[Figure] = []         # the previous sentence's figures, for "That compared with ..."
    for si, st in enumerate(sents):
        s = st.text
        nums = bc.find_numbers(s)
        if bc.other_numeric(s, nums):
            other.append(s)
        comp = _company_mentions(s)
        mentions[si] = comp
        segs = _segment_mentions(s)
        hits = _metric_hits(s)
        bc_benign = bc.FL_BENIGN
        breaks = [m.start() for m in _CLAUSE_BREAK.finditer(s)]
        carry = prev_entities[-1] if (not comp and prev_entities and _PRONOUN_START.match(s)) else None
        anaphor_pool = prev_figs if (prev_figs and _FIGURE_ANAPHOR.match(s)
                                     and sents[prev_figs[-1].sent].para == st.para) else []
        local: list[Figure] = []
        for i, n in enumerate(nums):
            f = Figure(idx=len(figures) + len(local), start=st.start + n.start, end=st.start + n.end, text=n.text,
                       kind=n.kind, value=n.value, sign=n.sign, decimals=n.decimals, scale=n.scale,
                       is_range=n.is_range, sent=si)
            prev_end = nums[i - 1].end if i else 0
            next_start = nums[i + 1].start if i + 1 < len(nums) else len(s)
            clause_start = max([b for b in breaks if b < n.start], default=0)
            prev_local = local[-1] if local and nums[i - 1].end > clause_start else None
            own_from = max(prev_end, clause_start)
            before = s[own_from:n.start]
            after = s[n.end:next_start]
            hedge = _HEDGE_BEFORE.search(s[max(0, n.start - 30):n.start])
            f.hedge = hedge.group(0).strip().lower() if hedge else None
            if n.kind == "money" and n.scale == "units" and (_PER_SHARE_AFTER.search(after)
                                                             or re.search(r"\bEPS\b|per share", before, re.I)):
                f.per_share = True
            own_hits = [h for h in hits if own_from <= h[0] and h[1] <= n.start]
            if n.kind in ("pct", "pp", "bps"):
                f.growth = bool(_GROWTH_BEFORE.search(s[max(own_from, n.start - 45):n.start])
                                or _GROWTH_AFTER.search(after) or n.text[:1] in "+")
                if f.growth and _DOWN_BEFORE.search(s[max(own_from, n.start - 45):n.start]):
                    f.sign = -1
                if not f.growth and prev_local is not None and prev_local.growth and not own_hits and \
                        re.search(r"(?:,|\band\b|\bor\b|\()\s*$", before):
                    f.growth, f.sign = True, prev_local.sign   # "up 106% from a year earlier and 18% ..."
                if f.growth:
                    nxt_pct = next((m.start for m in nums[i + 1:] if m.kind in ("pct", "pp", "bps")), len(s))
                    f.comparison = _earliest_comparison(s[n.end:min(nxt_pct, n.end + 90)])
                    if f.comparison is None:
                        f.comparison = _latest_comparison(before)
            # basis: the nearest cue in the figure's own phrase; "GAAP and non-GAAP ... both" covers both
            clause_text = s[clause_start:n.start]
            if _BOTH_BASIS.search(clause_text) and re.search(r"\bboth\b", s[clause_start:n.end + 15], re.I):
                f.basis = "both"
            else:
                cues = [(m.end(), "non-GAAP") for m in _NONGAAP_CUE.finditer(before)]
                cues += [(m.end(), "GAAP") for m in _GAAP_CUE.finditer(before)]
                if cues:
                    f.basis = max(cues)[1]
                elif prev_local is not None and not own_hits:
                    f.basis = prev_local.basis
                if re.search(r"^[^.;]{0,30}?on a non-GAAP basis", after, re.I):
                    f.basis = "non-GAAP"
                elif re.search(r"^[^.;]{0,30}?on a GAAP basis", after, re.I):
                    f.basis = "GAAP"
            # role: forward (an outlook cue before the figure in its clause, or "is expected" right after it),
            # prior (a comparison base), or current
            fl_before = bc_benign.sub(" ", s[clause_start:n.start])
            if _FL_CUE.search(fl_before) or re.match(r"^\s*(?:is|are)?\s*(?:expected|forecast|projected|guided)\b",
                                                     after, re.I):
                f.role = "forward"
            elif not f.growth and _PRIOR_BEFORE.search(s[max(0, n.start - 22):n.start]):
                tail = after[:60]
                if _YEAR_AGO_CUE.search(tail):
                    f.role, f.prior = "prior", "year_ago"
                elif _PRIOR_Q_CUE.search(tail):
                    f.role, f.prior = "prior", "prior_q"
                elif re.search(r"^\s*(?:in|for|during)\s+(?:the|its)\s", tail):
                    f.role, f.prior = "prior", "label"
            elif not f.growth and re.match(r"^\s*(?:a year (?:earlier|ago|before)|in the year[- ]ago quarter|"
                                           r"last year)", after, re.I):
                f.role, f.prior = "prior", "year_ago"
            elif not f.growth and re.match(r"^\s*(?:in the (?:previous|prior|preceding) quarter|last quarter)",
                                           after, re.I):
                f.role, f.prior = "prior", "prior_q"
            # metric family and segment
            own_segs = [g for g in segs if own_from <= g[0] and g[1] <= n.start + 1]
            clause_end = min([b for b in breaks if b > n.end], default=len(s))
            nxt = [h for h in hits if n.end <= h[0] < min(next_start, clause_end, n.end + 30)]
            nxt_of = nxt if nxt and re.match(r"^\s*(?:in|of)\s+(?:[\w&-]+\s+){0,2}$", s[n.end:nxt[0][0]]) else []
            seg_ticker = None
            inherited: Optional[Figure] = None
            anaphor = next((g for g in reversed(anaphor_pool) if g.kind == n.kind and
                            (g.scale == "units") == (n.scale == "units") and not g.growth), None)
            if f.per_share or (nxt and nxt[0][2] == "eps" and n.kind == "money" and n.scale == "units"):
                f.family, f.per_share = "eps", True
            elif own_hits or own_segs:
                f.family = own_hits[-1][2] if own_hits else "revenue"
                if own_segs and (not own_hits or own_segs[-1][1] >= own_hits[-1][0]):
                    f.segment, seg_ticker = own_segs[-1][3], own_segs[-1][2]
                    if f.family not in ("revenue",) and own_hits and own_hits[-1][0] > own_segs[-1][1]:
                        f.segment, seg_ticker = None, None   # "Data Center revenue ... and net income ..."
            elif prev_local is not None:
                f.family, f.segment, inherited = prev_local.family, prev_local.segment, prev_local
            elif nxt_of and not f.growth:
                f.family = nxt_of[0][2]                                  # "$28.0 billion in operating income"
                nseg = [g for g in segs if n.end <= g[0] < next_start]
                if nseg:
                    f.segment, seg_ticker = nseg[0][3], nseg[0][2]
            elif anaphor is not None and not local:
                f.family, f.segment, inherited = anaphor.family, anaphor.segment, anaphor  # "That compared with $X"
            else:
                earlier = [h for h in hits if clause_start <= h[1] <= n.start]
                f.family = earlier[-1][2] if earlier else None
            # company
            pre = [c for c in comp if c[1] <= n.start]
            owners = {t for a, t, sg in _SEG_ALIASES if sg == f.segment} if f.segment else set()
            if pre:
                f.entity, f.entity_explicit = pre[-1][2], True
            elif carry:
                f.entity, f.entity_explicit = carry, True
            elif inherited is not None:
                f.entity, f.entity_explicit = inherited.entity, inherited.entity_explicit
            elif f.segment and len(owners) == 1:
                f.entity, f.entity_explicit = next(iter(owners)), True
            elif f.segment and subject not in owners and seg_ticker:
                f.entity, f.entity_explicit = seg_ticker, True
            else:
                f.entity = subject
            # an explicit period label nearest the figure: right after it, else the closest one before it
            cal = (calendars or {}).get(f.entity)
            f.label_end = (_label_end(s[n.end:min(len(s), next_start)], f.entity, cal, default_year)
                           or _label_end(s[own_from:n.start], f.entity, cal, default_year, last=True))
            local.append(f)
        # a growth rate in a sentence that also carries an unclaimed cue for the other comparison ("revenue growth
        # was 8.0% year over year, ... evidence of sequential momentum") is ambiguous, whatever code reads it as
        present = {c for c, rx in (("YoY", _YOY), ("QoQ", _QOQ)) if rx.search(s)}
        present |= {"YoY"} if _YEAR_AGO_CUE.search(s) else set()
        present |= {"QoQ"} if _PRIOR_Q_CUE.search(s) else set()
        claimed = {g.comparison for g in local if g.growth} | {"YoY" for g in local if g.prior == "year_ago"} | \
                  {"QoQ" for g in local if g.prior == "prior_q"}
        for g in local:
            if g.growth and g.comparison and ({"YoY", "QoQ"} - {g.comparison}) & (present - claimed):
                g.comparison_conflict = True
        figures.extend(local)
        if local:
            prev_figs = local
        if comp:
            prev_entities = [c[2] for c in comp]
    return Reading(sents, figures, other, mentions)


# ============================================================ matching figures to facts

def _unit_ok(f: Figure, unit: str) -> bool:
    if unit == "USD":
        return f.kind == "money" and not f.per_share and f.scale != "units"
    if unit == "USD/share":
        return f.kind == "money" and (f.per_share or f.scale == "units")
    if unit == "%":
        return f.kind == "pct"
    return False


def value_match(f: Figure, value: float, unit: str) -> bool:
    if not _unit_ok(f, unit):
        return False
    return abs(f.claimed() - value) <= f.tolerance() * 1.0001 + 1e-9 * max(1.0, abs(value))


def distance(f: Figure, values: Iterable[float]) -> float:
    """Distance to the nearest value, in units of the figure's rounding tolerance."""
    tol = f.tolerance() or 1e-12
    return min((abs(f.claimed() - v) / tol for v in values), default=float("inf"))


def same_period(a: Optional[str], b: Optional[str], days: int = 7) -> bool:
    """Two period ends name the same quarter: fiscal calendars end a few days off the month end (Apple's June 27
    quarter is 'the June quarter'), as in receipts.periods.nearest."""
    if not a or not b:
        return False
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def consistent(f: Figure, fact: SheetFact, sheet: Sheet, *, strict_metric: bool = True) -> list[str]:
    """Why the figure's words disagree with a fact (empty when they agree). strict_metric=False lets a figure
    whose metric words code could not find (family None) fit any metric: used only to confirm a true value."""
    why: list[str] = []
    if fact.section == "guidance":
        return ["guidance"] if f.role != "forward" else []
    if f.role == "forward":
        why.append("forward-looking sentence")
    if not _unit_ok(f, fact.unit):
        why.append("unit")
    if f.entity != fact.ticker:
        why.append(f"company {f.entity} vs {fact.ticker}")
    fam = fact.family()
    if f.family is not None and f.family != fam:
        why.append(f"metric {f.family} vs {fam}")
    if f.family is None and strict_metric:
        why.append("no metric words")
    if (f.segment or None) != (fact.segment or None):
        why.append(f"segment {f.segment} vs {fact.segment}")
    if fact.is_growth() != f.growth:
        why.append("growth vs level")
    if fact.is_growth():
        if f.comparison is None:
            why.append("growth without a comparison")
        elif f.comparison != fact.comparison:
            why.append(f"comparison {f.comparison} vs {fact.comparison}")
    if f.basis in ("GAAP", "non-GAAP") and f.basis != fact.basis:
        why.append(f"basis {f.basis} vs {fact.basis}")
    # period: CURRENT facts need a figure with no prior-period cue; PRIOR facts need the matching cue; PEER and
    # LATER facts need an explicit label naming their quarter
    if fact.section == "current":
        if f.role == "prior":
            why.append("prior-period wording")
        if f.label_end and not same_period(f.label_end, fact.period_end):
            why.append(f"label {f.label_end} vs {fact.period_end}")
    elif fact.section == "prior":
        tag = "year_ago" if "year-ago" in fact.note else "prior_q"
        if f.role != "prior":
            why.append("no prior-period wording")
        elif f.prior in ("year_ago", "prior_q") and f.prior != tag:
            why.append(f"prior {f.prior} vs {tag}")
        elif f.prior == "label" and not same_period(f.label_end, fact.period_end):
            why.append("prior label")
    elif fact.section in ("peer", "later"):
        if not same_period(f.label_end, fact.period_end):
            why.append(f"period label {f.label_end} vs {fact.period_end}")
    return why


def store_fact(f: Figure, sheet: Sheet, evidence: list[Ev], calendars: Optional[dict[str, list]]) -> Optional[SheetFact]:
    """A fact the sheet does not list but the store has, for the period the figure's words name: e.g. "net income
    rose 17% from the previous quarter" when the sheet only lists the YoY rate. Code computes it (StoreIndex)."""
    if f.family is None or f.role == "forward":
        return None
    cal = (calendars or {}).get(f.entity) or []
    target: Optional[str] = f.label_end
    if target is None and f.entity == sheet.ticker:
        cur = next((i for i, q in enumerate(cal) if q[3].isoformat() == sheet.period_end), None)
        if f.role == "current":
            target = sheet.period_end
        elif cur is not None and f.prior == "prior_q" and cur > 0:
            target = cal[cur - 1][3].isoformat()
        elif cur is not None and f.prior == "year_ago":
            ya = next((q for q in cal if q[0] == cal[cur][0] - 1 and q[1] == cal[cur][1]), None)
            target = ya[3].isoformat() if ya else None
    if target is None:
        return None
    hits = [e for e in evidence if e.fact_id is None and e.ticker == f.entity and metric_family(e.metric) == f.family
            and (e.segment or None) == (f.segment or None) and e.metric.endswith("_growth") == f.growth
            and (not f.growth or e.comparison == f.comparison)
            and (f.basis not in ("GAAP", "non-GAAP") or e.basis == f.basis) and same_period(e.period_end, target)
            and _unit_ok(f, e.unit)]
    # A version published later must not inherit an older version's availability. When no version is public,
    # retain only the first publication so the caller can label the figure as look-ahead.
    hits = [e for e in hits if e.first_public is not None]
    visible = [e for e in hits if e.first_public <= sheet.as_of]
    if visible:
        hits = visible
    elif hits:
        first = min(e.first_public for e in hits)
        hits = [e for e in hits if e.first_public == first]
    if not hits or (f.basis not in ("GAAP", "non-GAAP") and len({e.basis for e in hits}) > 1):
        hits = [e for e in hits if e.basis == "GAAP"] or hits
    if not hits:
        return None
    e0 = min(hits, key=lambda e: e.first_public or "")
    vals = _distinct(e.value for e in hits)
    seg = f" {e0.segment}" if e0.segment else ""
    return SheetFact(id=f"S:{e0.ticker}:{e0.metric}{':' + e0.segment if e0.segment else ''}:{e0.basis}"
                        f"{':' + e0.comparison if e0.comparison else ''}:{e0.period_end}",
                     section="store", ticker=e0.ticker, metric=e0.metric, unit=e0.unit, value=vals[0],
                     display=_display(vals[0], e0.unit), basis=e0.basis, segment=e0.segment, comparison=e0.comparison,
                     period_end=e0.period_end, period_label=f"{COMPANY_NAME[e0.ticker]}{seg} quarter ended "
                     f"{date_label(date.fromisoformat(e0.period_end))}",
                     first_public=min((e.first_public for e in hits if e.first_public), default=None),
                     alternatives=vals[1:], note="from the store (not on the sheet)")


def _display(v: float, unit: str) -> str:
    return fmt_usd(v) if unit == "USD" else fmt_eps(v) if unit == "USD/share" else fmt_pct(v, 1)


def guidance_match(f: Figure, sheet: Sheet) -> Optional[SheetFact]:
    for g in sheet.facts:
        if g.section != "guidance":
            continue
        for n in g.numbers:
            if n["kind"] == f.kind and abs(n["value"] - f.value) < 1e-9 and n["scale"] == f.scale:
                return g
    return None


# ============================================================ diagnosis (code decides the error type)

def diagnose(f: Figure, claimed: SheetFact, sheet: Sheet, evidence: list[Ev],
             companies: Optional[set[str]] = None) -> tuple[str, Optional[Ev], list[str]]:
    """The error type of a figure that contradicts `claimed`, in the verifier's order: look_ahead, period,
    scale, basis, entity, else value. Returns (type, the evidence the figure really matches, all types seen).

    A period swap must come from a nearby quarter (within 13 months) and a wrong-company figure from a company
    on the sheet or named in the story, so a drifted value that happens to equal some unrelated filing (Amazon's
    derived gross margin, Meta's 2023 operating income) stays a value error."""
    as_of = sheet.as_of
    on_sheet = {x.ticker for x in sheet.facts} | set(companies or ())

    def near(e: Ev, days: int) -> bool:
        return bool(claimed.period_end) and abs((date.fromisoformat(e.period_end)
                                                 - date.fromisoformat(claimed.period_end)).days) <= days
    seen: list[tuple[str, Optional[Ev]]] = []
    if claimed.first_public and claimed.first_public > as_of:
        seen.append(("look_ahead", None))
    same = lambda e: (e.ticker == claimed.ticker and metric_family(e.metric) == claimed.family()  # noqa: E731
                      and (e.segment or None) == (claimed.segment or None) and e.metric.endswith("_growth")
                      == claimed.is_growth())
    evidence = [e for e in evidence if e.first_public and e.first_public <= as_of]
    for e in evidence:  # period: the same metric, company and basis in another period or comparison
        if same(e) and e.basis == claimed.basis and value_match(f, e.value, e.unit) and near(e, 400) and \
                (e.period_end != claimed.period_end or e.comparison != claimed.comparison):
            seen.append(("period", e))
            break
    for k, s in SCALE_FACTOR.items():  # scale: the digits are right, the scale word is not
        if f.kind == "money" and s != SCALE_FACTOR[f.scale] and claimed.unit == "USD":
            alt = Figure(**{**asdict(f), "scale": k})
            if any(value_match(alt, v, "USD") for v in claimed.values()):
                seen.append(("scale", None))
                break
    for e in evidence:  # basis: the other basis, same company, metric and period
        if same(e) and e.basis != claimed.basis and e.period_end == claimed.period_end and \
                e.comparison == claimed.comparison and value_match(f, e.value, e.unit):
            seen.append(("basis", e))
            break
    for e in evidence:  # entity: another company's same metric in a nearby quarter
        if e.ticker != claimed.ticker and e.ticker in on_sheet and metric_family(e.metric) == claimed.family() and \
                e.metric.endswith("_growth") == claimed.is_growth() and value_match(f, e.value, e.unit) and \
                near(e, 100):
            seen.append(("entity", e))
            break
    if not seen:
        seen.append(("value", None))
    order = {t: i for i, t in enumerate(("look_ahead", "period", "scale", "basis", "entity", "value"))}
    seen.sort(key=lambda x: order[x[0]])
    return seen[0][0], seen[0][1], [t for t, _ in seen]


def matches_any_evidence(f: Figure, evidence: list[Ev], ticker: Optional[str] = None) -> list[Ev]:
    return [e for e in evidence if (ticker is None or e.ticker == ticker) and value_match(f, e.value, e.unit)]


# ============================================================ validation (code decides)

_LOOSE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", " ": " "})
_NUMBER_WORDS = re.compile(r"\b(?:doubled|tripled|quadrupled|halved|twice|thrice|two-thirds|one-third|one-half|"
                           r"three-quarters|nine-tenths|tenfold|(?:two|three|four|five|six|seven|eight|nine|ten)-fold|"
                           r"\d+-fold|\d+x)\b", re.I)


def _loose(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).translate(_LOOSE)).strip()


def locate_quote(article: str, quote: str) -> Optional[tuple[int, int, bool]]:
    """(start, end, repaired) of a quote in the article: verbatim, else after quote/dash/space normalization."""
    if quote and quote in article:
        i = article.index(quote)
        return i, i + len(quote), False
    lq = _loose(quote)
    if not lq:
        return None
    # map loose positions back: try every sentence-sized window that loosens to the quote
    la = _loose(article)
    if lq not in la:
        return None
    for i in range(len(article)):
        if _loose(article[i:i + len(quote) + 8]).startswith(lq):
            for j in range(i + max(1, len(lq) - 8), min(len(article), i + len(quote) + 12) + 1):
                if _loose(article[i:j]) == lq:
                    return i, j, True
    return None


@dataclass
class Validation:
    accepted: bool
    reasons: list[str]                  # why it was rejected (empty when accepted)
    log: list[str]                      # repairs, relabels, demotions
    planted: list[dict] = field(default_factory=list)
    accidental: list[dict] = field(default_factory=list)
    true_claims: list[dict] = field(default_factory=list)
    text: str = ""
    sentences: list[list[int]] = field(default_factory=list)
    words: int = 0
    fixes: dict[str, int] = field(default_factory=dict)   # reason -> sentence index whose removal cures it
    sentence_texts: list[str] = field(default_factory=list)


def _fact_brief(fact: SheetFact) -> dict:
    return {"fact_id": fact.id, "ticker": fact.ticker, "metric": fact.metric, "segment": fact.segment,
            "basis": fact.basis, "comparison": fact.comparison, "period_end": fact.period_end,
            "period_label": fact.period_label, "section": fact.section, "value": fact.value,
            "display": fact.display, "alternatives": fact.alternatives, "first_public": fact.first_public,
            "evidence": [{k: e.get(k) for k in ("accession", "form", "source", "accepted_at")} for e in fact.evidence]}


def _ev_brief(e: Optional[Ev]) -> Optional[dict]:
    return None if e is None else {"ticker": e.ticker, "metric": e.metric, "segment": e.segment, "basis": e.basis,
                                   "comparison": e.comparison, "period_end": e.period_end, "value": e.value,
                                   "first_public": e.first_public, "fact_id": e.fact_id}


def validate_draft(sheet: Sheet, output: dict, *, evidence: Optional[list[Ev]] = None,
                   calendars: Optional[dict[str, list]] = None, min_errors: int = MIN_ERRORS) -> Validation:
    """Code decides. `output` is Wonder's JSON (headline, article, planted_errors). `evidence` adds the store's
    other periods, bases and companies (StoreIndex.items) to the sheet's own facts for diagnosis.

    Repair: when every problem sits in a planted error's own sentence (it contradicts a true figure elsewhere in
    the story, carries a comparison cue that gives it away, or is an implausible scale slip), code deletes those
    sentences and validates the shorter story again. Nothing else is ever rewritten."""
    first = _validate_once(sheet, output, evidence=evidence, calendars=calendars, min_errors=min_errors)
    if first.accepted or not first.reasons or not all(r in first.fixes for r in first.reasons):
        return first
    drop = sorted({first.fixes[r] for r in first.reasons})
    repaired, note = drop_sentences(output, first, drop)
    if repaired is None:
        first.reasons.append(f"repair impossible: {note}")
        return first
    second = _validate_once(sheet, repaired, evidence=evidence, calendars=calendars, min_errors=min_errors)
    second.log = [f"repair: {r}" for r in first.reasons] + [f"repair: {note}"] + first.log + second.log
    return second


_ANAPHOR_START = re.compile(r"^(?:That|This|These|Those|It|Its|The figure|The total|The result|The gain|The increase|"
                            r"The decline|Both)\b")


def drop_sentences(output: dict, v: Validation, drop: list[int]) -> tuple[Optional[dict], str]:
    """Wonder's output with the given sentences (indexes into v.sentences) removed from the article, and the
    planted errors that lived in them removed from its list. None when a later sentence refers back to one."""
    text = v.text
    spans = [v.sentences[i] for i in drop]
    for i in drop:
        nxt = i + 1
        if nxt < len(v.sentences) and "\n\n" not in text[v.sentences[i][1]:v.sentences[nxt][0]] and \
                _ANAPHOR_START.match(v.sentence_texts[nxt]) and nxt not in drop:
            return None, f"the sentence after {v.sentence_texts[i][:60]!r} refers back to it"
    out = text
    for a, b in sorted(spans, reverse=True):
        tail = re.match(r"[ \t]*", out[b:]).end()
        out = out[:a] + out[b + tail:]
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    headline = (output.get("headline") or "").strip()
    if headline and not re.search(r"\d", headline) and out.startswith(headline):
        article = out[len(headline):].strip()
    else:
        headline, article = "", out
    gone = [v.sentence_texts[i] for i in drop]
    kept = [pe for pe in (output.get("planted_errors") or [])
            if not any((pe.get("quote") or "").strip() and (pe.get("quote") or "").strip() in g for g in gone)
            and (pe.get("value_text") or "") in article]
    return ({"headline": headline, "article": article, "planted_errors": kept},
            f"dropped {len(drop)} sentence(s): " + " | ".join(g[:70] for g in gone))


def _validate_once(sheet: Sheet, output: dict, *, evidence: Optional[list[Ev]] = None,
                   calendars: Optional[dict[str, list]] = None, min_errors: int = MIN_ERRORS) -> Validation:
    reasons: list[str] = []
    log: list[str] = []
    facts = sheet.by_id()
    ev_all = sheet_evidence(sheet) + list(evidence or [])
    headline = (output.get("headline") or "").strip()
    article = (output.get("article") or "").strip()
    article = re.sub(r"\n{3,}", "\n\n", article.replace("\r\n", "\n"))
    if headline and re.search(r"\d", headline):
        log.append(f"headline dropped: it states figures ({headline!r})")
        headline = ""
    text = f"{headline}\n\n{article}" if headline else article
    words = len(re.findall(r"[A-Za-z0-9$%][\w.,'’%$-]*", article))
    val = Validation(False, reasons, log, text=text, words=words)
    if not MIN_WORDS <= words <= MAX_WORDS:
        reasons.append(f"length: {words} words (want 150-250)")
    if len(split_paragraphs(article)) < 2:
        reasons.append("structure: fewer than 2 paragraphs")
    reading = read_draft(text, sheet.ticker, calendars=calendars, default_year=sheet.fiscal_year)
    val.sentences = [[s.start, s.end] for s in reading.sentences]
    val.sentence_texts = [s.text for s in reading.sentences]
    for s in reading.other_numeric:
        reasons.append(f"non-financial number: {s[:120]!r}")
    for m in _NUMBER_WORDS.finditer(text):
        reasons.append(f"figure in words: {m.group(0)!r}")
    if re.search(r"\b\d+(?:\.\d+)?\s?cents\b", text):
        reasons.append("figure in cents")

    # ---- planted errors: locate each one in the text (code computes offsets)
    planted_by_fig: dict[int, dict] = {}
    for k, pe in enumerate(output.get("planted_errors") or []):
        quote, vt = (pe.get("quote") or "").strip(), (pe.get("value_text") or "").strip()
        loc = locate_quote(text, quote)
        if loc is None and vt and text.count(vt) == 1:
            i = text.index(vt)
            sent = next((s for s in reading.sentences if s.start <= i < s.end), None)
            if sent is not None:
                loc = (sent.start, sent.end, True)
                log.append(f"planted #{k + 1}: quote not in the text; anchored on its value {vt!r}")
        if loc is None:
            reasons.append(f"planted #{k + 1}: quote not found verbatim: {quote[:100]!r}")
            continue
        qs, qe, repaired = loc
        if repaired:
            log.append(f"planted #{k + 1}: quote repaired to the verbatim text {text[qs:qe][:80]!r}")
        figs = [f for f in reading.figures if qs <= f.start and f.end <= qe]
        hit = [f for f in figs if f.text == vt] or [f for f in figs if _loose(f.text) == _loose(vt)]
        if not hit and figs:
            vnums = build_corpus_module().find_numbers(vt)
            if vnums:
                hit = [f for f in figs if abs(f.value - vnums[0].value) < 1e-9 and f.kind == vnums[0].kind]
                if hit:
                    log.append(f"planted #{k + 1}: value_text {vt!r} read as {hit[0].text!r}")
        if not hit and vt and text.count(vt) == 1:   # the quote is another sentence: anchor on the unique value
            hit = [f for f in reading.figures if f.start == text.index(vt) and f.text == vt]
            if hit:
                log.append(f"planted #{k + 1}: value {vt!r} is not in its quote; anchored on the value's sentence")
        if not hit:
            reasons.append(f"planted #{k + 1}: value_text {vt!r} is not a figure inside its quote")
            continue
        fig = hit[0]
        if fig.idx in planted_by_fig:
            reasons.append(f"planted #{k + 1}: same figure as another planted error")
            continue
        planted_by_fig[fig.idx] = {"k": k + 1, "raw": pe, "quote_span": [qs, qe]}

    # ---- every figure: true, planted error, accidental error, or a reason to reject
    named = {t for ms in reading.mentions.values() for _a, _b, t in ms}

    def is_true(fact: SheetFact) -> bool:
        return any(value_match(f, v, fact.unit) for v in fact.values()) and \
            not (fact.first_public and fact.first_public > sheet.as_of)

    for f in reading.figures:
        sent = reading.sentences[f.sent]
        pe = planted_by_fig.get(f.idx)
        where = f"{f.text!r} in {sent.text[:90]!r}"
        if f.hedge and f.role != "forward":
            reasons.append(f"hedged figure {where} ({f.hedge})")
            continue
        if f.comparison_conflict:
            r = f"ambiguous comparison: {where} has both year-over-year and sequential wording"
            reasons.append(r)
            if pe is not None:
                val.fixes[r] = f.sent
            continue
        if f.role == "forward":
            g = guidance_match(f, sheet)
            if g is None:
                reasons.append(f"forward-looking figure not in the release's outlook: {where}")
            elif pe:
                reasons.append(f"planted #{pe['k']}: an error in guidance cannot be verified: {where}")
            else:
                val.true_claims.append({"value_text": f.text, "span": [f.start, f.end], "fact_id": g.id,
                                        "forward_looking": True, "sentence": f.sent})
            continue
        cands = [fact for fact in sheet.facts if fact.section != "guidance" and _unit_ok(f, fact.unit)]
        ok = [fact for fact in cands if not consistent(f, fact, sheet)]
        true_ok = [fact for fact in ok if is_true(fact)]
        how = ""
        if not true_ok and f.family is None:   # metric words not found: the value and every other word must fit
            true_ok = [fact for fact in cands if not consistent(f, fact, sheet, strict_metric=False) and is_true(fact)]
            true_ok = true_ok if len(true_ok) == 1 else []
            how = "metric from the value (no metric words)"
        if not true_ok and f.segment is None:  # "Google Cloud remained the standout, producing revenue of $20.0B"
            named_segments = {sg for a, t, sg in _SEG_ALIASES if t == f.entity and re.search(
                rf"(?<![\w-]){re.escape(a)}(?![\w-])", sent.text[:max(0, f.start - sent.start)])}
            for fact in cands:
                if fact.segment in named_segments and is_true(fact):
                    alt = Figure(**{**asdict(f), "segment": fact.segment})
                    if not consistent(alt, fact, sheet):
                        true_ok, how = [fact], f"segment {fact.segment} named earlier in the sentence"
                        break
        store = None
        if not ok and not true_ok:
            store = store_fact(f, sheet, ev_all, calendars)
            if store is not None and not consistent(f, store, sheet) and is_true(store):
                true_ok, how = [store], "a store fact the sheet does not list"
        if pe is None:
            if true_ok:
                val.true_claims.append({"value_text": f.text, "span": [f.start, f.end], "fact_id": true_ok[0].id,
                                        "forward_looking": False, "sentence": f.sent})
                if how:
                    log.append(f"true figure {where}: {true_ok[0].id} via {how}")
                continue
            # not planted and not true: an accidental error, when code can say what it claims and that it is false
            claimed = ok[0] if len(ok) == 1 else (store if not ok else None)
            if claimed is None:
                elsewhere = [fact.id for fact in cands if any(value_match(f, v, fact.unit) for v in fact.values())]
                reasons.append(f"unmappable figure {where}: {len(ok)} facts fit its words"
                               f"{', value matches ' + ','.join(elsewhere) if elsewhere else ''}")
                continue
            d = distance(f, claimed.values() + unstated_basis_values(f, claimed, ev_all, sheet.as_of))
            if d <= 2.0 and not (claimed.first_public and claimed.first_public > sheet.as_of):
                reasons.append(f"borderline figure {where}: {d:.2f}x rounding from {claimed.id} {claimed.display}")
                continue
            etype, matched, all_types = diagnose(f, claimed, sheet, ev_all, named)
            other = [e for e in matches_any_evidence(f, ev_all) if e.fact_id != claimed.id
                     and e.first_public and e.first_public <= sheet.as_of]
            if etype == "value" and other:
                reasons.append(f"ambiguous figure {where}: reads as {claimed.id} but its value is also filed for "
                               f"{other[0].ticker} {other[0].metric} {other[0].period_end}")
                continue
            if etype != "value" and not _differing_attribute_explicit(f, claimed, matched):
                reasons.append(f"ambiguous figure {where}: reads as {claimed.id}; its value belongs to "
                               f"{_ev_brief(matched)} and the words that tell them apart are implicit")
                continue
            log.append(f"accidental error: {where} reads as {claimed.id} ({claimed.display}); code says {etype}")
            val.accidental.append(_error_record(f, claimed, etype, matched, all_types, sheet, text, sent,
                                                wonder=None, explanation="not flagged by Wonder; found by code"))
            continue
        # planted: what do the words claim? Code's reading decides; Wonder's fact id is a cross-check
        raw = pe["raw"]
        wtype = raw.get("error_type")
        fid, sid = raw.get("fact_id"), raw.get("source_fact_id")
        wfact, wsrc = facts.get(fid), facts.get(sid)
        claimed: Optional[SheetFact] = None
        if wfact is not None and wfact in ok:
            claimed = wfact
        elif len(ok) == 1 or (not ok and store is not None and not consistent(f, store, sheet)):
            claimed = ok[0] if ok else store
            agree = wfact is None or _key(wfact) == _key(claimed)
            names_source = wfact is not None and (wfact is wsrc or any(value_match(f, v, wfact.unit)
                                                                      for v in wfact.values()))
            if not agree and not names_source:
                reasons.append(f"planted #{pe['k']}: Wonder says it corrupts {fid} ({wfact.display}) but the words "
                               f"read as {claimed.id} ({claimed.display}): {where}")
                continue
            if not agree:
                log.append(f"planted #{pe['k']}: Wonder's fact_id {fid} is the figure's source; the words claim "
                           f"{claimed.id} ({claimed.display})")
            elif claimed is not wfact:
                log.append(f"planted #{pe['k']}: fact {fid} -> {claimed.id} (same metric, period and company)")
        elif wfact is not None and not ok:
            why = consistent(f, wfact, sheet)
            reasons.append(f"planted #{pe['k']}: the words do not state {fid} ({'; '.join(why)}): {where}")
            continue
        else:
            reasons.append(f"planted #{pe['k']}: {len(ok)} facts fit the words and fact_id {fid!r} is not one: {where}")
            continue
        future = bool(claimed.first_public and claimed.first_public > sheet.as_of)
        other_basis = unstated_basis_values(f, claimed, ev_all, sheet.as_of)
        d = distance(f, claimed.values() + other_basis)
        if other_basis and not future and distance(f, other_basis) <= 1.0:
            log.append(f"planted #{pe['k']} matches {claimed.id} on the other basis and the words state none: "
                       f"demoted to a true claim")
        if not future and d <= 1.0:
            log.append(f"planted #{pe['k']} is true ({f.text} = {claimed.id} {claimed.display}): demoted to a true "
                       f"claim")
            val.true_claims.append({"value_text": f.text, "span": [f.start, f.end], "fact_id": claimed.id,
                                    "forward_looking": False, "sentence": f.sent, "demoted_plant": True})
            continue
        if not future and d <= 2.0:
            reasons.append(f"planted #{pe['k']}: borderline, {d:.2f}x rounding from {claimed.display}: {where}")
            continue
        etype, matched, all_types = diagnose(f, claimed, sheet, ev_all, named)
        revenue = next((x.value for x in sheet.facts if x.section == "current" and x.metric == "revenue"
                        and x.basis == "GAAP"), None)
        if etype == "scale" and revenue and abs(f.claimed()) > 1.5 * abs(revenue):
            r = f"implausible scale slip {where}: larger than {sheet.company}'s whole revenue"
            reasons.append(r)
            val.fixes[r] = f.sent
            continue
        if wtype != etype:
            log.append(f"planted #{pe['k']}: Wonder says {wtype}, code says {etype} ({f.text} vs {claimed.display})")
        val.planted.append(_error_record(f, claimed, etype, matched, all_types, sheet, text, sent, wonder=raw,
                                         explanation=raw.get("explanation") or ""))
    # a story that states two different values for the same claim contradicts itself: no trap, just noise
    stated: dict[tuple, list[tuple[str, float]]] = defaultdict(list)
    by_id = {**facts}
    for rec in val.true_claims + val.planted + val.accidental:
        fact = by_id.get(rec["fact_id"])
        if fact is None and rec.get("claimed"):
            continue
        if fact is not None and fact.section != "guidance":
            stated[_key(fact) + (fact.basis,)].append((rec["value_text"], rec.get("claimed_value")))
    planted_sent = {tuple(e["span"]): e["sentence_span"] for e in val.planted}
    for key, vals in stated.items():
        if len({v for v, _ in vals}) > 1:
            r = (f"self-contradiction: {key[0]} {key[1]} {key[2] or ''} {key[3]} {key[4] or ''} stated as "
                 f"{' and '.join(sorted({v for v, _ in vals}))}")
            reasons.append(r)
            mine = [e for e in val.planted if e["fact_id"] in by_id and _key(by_id[e["fact_id"]]) + (
                by_id[e["fact_id"]].basis,) == key]
            if len(mine) == 1:
                ss = mine[0]["sentence_span"]
                val.fixes[r] = next(i for i, sp in enumerate(val.sentences) if sp == ss)
    n_err = len(val.planted) + len(val.accidental)
    if not reasons and n_err < min_errors:
        reasons.append(f"only {n_err} confirmed error(s); want at least {min_errors}")
    val.accepted = not reasons
    return val


def unstated_basis_values(f: Figure, claimed: SheetFact, evidence: list[Ev], as_of: str) -> list[float]:
    """With no basis written, a figure is true if it matches the claim on any basis (verify.py: never "wrong" on
    GAAP alone). These are the other bases' filed values for the same company, metric, period and comparison."""
    if f.basis in ("GAAP", "non-GAAP"):
        return []
    return [e.value for e in evidence if e.ticker == claimed.ticker and e.metric == claimed.metric
            and (e.segment or None) == (claimed.segment or None) and e.comparison == claimed.comparison
            and e.basis != claimed.basis and same_period(e.period_end, claimed.period_end, 3)
            and e.first_public and e.first_public <= as_of]


def _key(fact: SheetFact) -> tuple:
    return (fact.ticker, fact.family(), fact.segment, fact.period_end, fact.comparison, fact.is_growth())


def _differing_attribute_explicit(f: Figure, claimed: SheetFact, matched: Optional[Ev]) -> bool:
    """An unflagged figure whose value belongs to another fact is a genuine error only if the words that separate
    the two facts are on the page: the company named, the comparison stated, the basis written, or (for another
    quarter of the same metric) no period cue at all, so the story's own quarter is meant."""
    if matched is None:
        return True
    if matched.ticker != claimed.ticker:
        return f.entity_explicit
    if matched.comparison != claimed.comparison:
        return f.comparison is not None
    if matched.basis != claimed.basis:
        return f.basis in ("GAAP", "non-GAAP")
    if matched.period_end != claimed.period_end:
        return f.role == "current" and f.label_end is None
    return True


def _error_record(f: Figure, claimed: SheetFact, etype: str, matched: Optional[Ev], all_types: list[str],
                  sheet: Sheet, text: str, sent: Sentence, *, wonder: Optional[dict], explanation: str) -> dict:
    return {
        "type": etype, "wonder_type": (wonder or {}).get("error_type"), "types_seen": all_types,
        "value_text": f.text, "span": [f.start, f.end], "sentence_span": [sent.start, sent.end],
        "quote": sent.text, "claimed_value": f.claimed(), "fact_id": claimed.id,
        "wonder_fact_id": (wonder or {}).get("fact_id"), "wonder_source_fact_id": (wonder or {}).get("source_fact_id"),
        "claimed": _fact_brief(claimed), "true_value": claimed.value, "true_display": claimed.display,
        "matched_evidence": _ev_brief(matched), "expected_status": "wrong", "expected_reason": EXPECTED_REASON[etype],
        "relative_error": (None if not claimed.value else round(f.claimed() / claimed.value - 1.0, 6)),
        "explanation": explanation,
    }


# ============================================================ the prompt (Muse Wonder proposes)

SYSTEM_PROMPT = """You are a business reporter on a wire-service earnings desk, and you also build test material \
for a fact-checking tool. Each story you write hides a few subtle factual errors that the tool must catch; \
everything else in it must be exactly right.

Write like Reuters or Bloomberg: a news lede, then the figures that matter woven into sentences with context \
(what drove demand, how the business is shifting, what investors watch, the competition). Vary sentence structure \
and never list figures one after another. No bullet points, no executive quotes, no dateline line.

You get one FACT SHEET per story. Each line has a fact id, the metric, the period and the exact value to print. \
Sections: CURRENT (the subject's results for the quarter), PRIOR (the subject's previous-quarter and year-ago \
figures), PEER (a rival's latest results, already public at the dateline), LATER (a rival's results that were NOT \
public at the dateline; use them only for a look_ahead error), GUIDANCE (the release's outlook sentence).

Each story: 160-210 words in 3 or 4 paragraphs separated by one blank line, plus a headline with no figures. Use 8 \
to 12 figures.

Every figure that is not a planted error:
- comes from the sheet and is printed exactly as the sheet shows it;
- if it is a growth rate, names its comparison ("year over year", "from a year earlier", "from the previous \
quarter", "sequentially");
- if its metric is marked BOTH BASES, has "GAAP" or "non-GAAP" in front of it; for other metrics, do not mention \
the basis;
- if it is a PRIOR figure, states its period right after it ("from $81.6 billion in the previous quarter", \
"compared with $46.7 billion a year earlier");
- if it is a PEER figure, names the rival before it and the rival's quarter after it, as the sheet writes it ("in \
the quarter ended June 27, 2026");
- if it is guidance, may be paraphrased with "expects" or "forecast" but keeps its figures; never put an error in \
guidance.
Print no other number of any kind: no share prices, market values, share or unit counts, users, headcount, product \
model numbers, other dates, fractions or multiples ("doubled"), totals, differences or ratios you work out \
yourself, and no hedges ("about", "nearly", "more than").

Planted errors: exactly the requested ones, one per requested type, each in its own sentence, each changing one \
figure and nothing else, each reading like an honest slip:
- period: a quarter-over-quarter growth rate called year over year (or the reverse), or a PRIOR previous-quarter \
figure stated as this quarter's;
- scale: the right digits with the wrong scale word (million for billion, or the reverse);
- basis: a BOTH BASES metric's non-GAAP figure labeled GAAP, or its GAAP figure labeled non-GAAP;
- entity: a PEER figure attributed to the subject, or the subject's figure attributed to the rival \
(similar-sized metric);
- value: the right metric, company and period with the value drifted 2-8% (percentages by 3-8 points), printed at \
the sheet's precision;
- look_ahead: a LATER figure stated as known, naming the rival and the quarter as the sheet writes it.
Never print the correct value of a fact you corrupt anywhere in the story, and never give one figure two \
different values. Put no words near an error that give it away (a rate presented as year over year must not sit \
next to sequential or previous-quarter wording, or the reverse). A scale error must stay plausible for the \
company: print "million" where the sheet says "billion".

When you get two sheets, make the two stories clearly different in structure, lede and wording.

Return JSON: stories, one per sheet in the order given, each with sheet_id, headline, article (paragraphs \
separated by \\n\\n) and planted_errors. Each planted error: quote (the whole sentence containing it, copied \
verbatim), value_text (the wrong figure exactly as printed), error_type, fact_id (the sheet fact the sentence \
claims to state), source_fact_id (the fact whose value you used instead, or "")."""

_ERROR_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["quote", "value_text", "error_type", "fact_id", "source_fact_id"],
    "properties": {"quote": {"type": "string"}, "value_text": {"type": "string"},
                   "error_type": {"type": "string", "enum": list(ERROR_TYPES)},
                   "fact_id": {"type": "string"}, "source_fact_id": {"type": "string"}}}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["stories"],
    "properties": {"stories": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["sheet_id", "headline", "article", "planted_errors"],
        "properties": {"sheet_id": {"type": "string"}, "headline": {"type": "string"}, "article": {"type": "string"},
                       "planted_errors": {"type": "array", "items": _ERROR_ITEM}}}}},
}

ANGLES = {
    "wire": "a straight results story: the headline numbers first, then the businesses behind them, then the outlook",
    "rival": "an analysis story that sets the quarter against the rival's latest results",
    "markets": "a markets-desk story about what investors watched: growth, profitability, the strongest business "
               "and what comes next",
}
_METRIC_WORDS = {"revenue": "revenue", "revenue_growth": "revenue growth", "net_income": "net income",
                 "net_income_growth": "net income growth", "eps_diluted": "diluted EPS",
                 "operating_income": "operating income", "gross_margin": "gross margin",
                 "operating_margin": "operating margin", "operating_expenses": "total expenses",
                 "net_interest_income": "net interest income", "segment_revenue": "revenue",
                 "segment_revenue_growth": "revenue growth"}


def both_bases(sheet: Sheet) -> set[tuple[str, Optional[str]]]:
    seen: dict[tuple[str, Optional[str]], set[str]] = defaultdict(set)
    for f in sheet.facts:
        if f.section == "current":
            seen[(f.metric, f.segment)].add(f.basis)
    return {k for k, v in seen.items() if len(v) > 1}


def render_sheet(sheet: Sheet) -> str:
    """The sheet as Wonder sees it: grouped by section, one fact per line, values exactly as they may be printed."""
    two = both_bases(sheet)
    heads = {"current": f"CURRENT ({fiscal_label(sheet.ticker, sheet.fiscal_year, sheet.fiscal_period)}, quarter "
                        f"ended {date_label(date.fromisoformat(sheet.period_end))})",
             "prior": "PRIOR", "peer": "PEER (public at the dateline)",
             "later": "LATER (NOT public at the dateline)", "guidance": "GUIDANCE"}
    lines: list[str] = []
    for sec in ("current", "prior", "peer", "later", "guidance"):
        rows = [f for f in sheet.facts if f.section == sec]
        if not rows:
            continue
        lines.append(heads[sec])
        for f in rows:
            if sec == "guidance":
                lines.append(f'{f.id} "{f.display}" (the release\'s outlook)')
                continue
            what = _METRIC_WORDS.get(f.metric, f.metric.replace("_", " "))
            if f.segment:
                what = f"{f.segment} {what}"
            if f.comparison:
                what += ", year over year" if f.comparison == "YoY" else ", from the previous quarter"
            if (f.metric, f.segment) in two and sec == "current":
                what += f" [BOTH BASES] {f.basis}"
            elif (f.metric, f.segment) in two and sec == "prior":
                what += f" ({f.basis})"
            if f.note and sec == "current" and "basis" not in f.note:
                what += f" ({f.note})"
            if sec == "prior":
                what += f", {f.note} ({fiscal_label(f.ticker, f.fiscal_year, f.fiscal_period)})"
            if sec in ("peer", "later"):
                what = f"{COMPANY_NAME[f.ticker]} {what}, quarter ended {date_label(date.fromisoformat(f.period_end))}"
            tail = f" (released {f.first_public[:10]})" if sec == "later" else ""
            lines.append(f"{f.id} {what}: {f.display}{tail}")
    return "\n".join(lines)


@dataclass
class Job:
    job_id: str
    sheet_id: str
    variant: str              # a | b
    angle: str
    error_types: list[str]

    def to_json(self) -> dict:
        return asdict(self)


def story_prompt(sheet: Sheet, job: Job) -> str:
    asof = parse_ts(sheet.as_of)
    return (f"SHEET {sheet.sheet_id}: {sheet.company} ({sheet.ticker}), "
            f"{fiscal_label(sheet.ticker, sheet.fiscal_year, sheet.fiscal_period)}\n"
            f"DATELINE: {asof.strftime('%Y-%m-%d %H:%M')} UTC, two hours after the earnings release\n"
            f"ANGLE: {ANGLES[job.angle]}\n"
            f"PLANT {len(job.error_types)} ERRORS: {', '.join(job.error_types)}\n"
            f"{render_sheet(sheet)}\n")


def build_request(pairs: list[tuple[Sheet, Job]]) -> dict:
    """One Responses call that writes one story per (sheet, job). The stable system prompt goes first so prompt
    caching can reuse it across calls."""
    user = "\n".join(story_prompt(sh, job) for sh, job in pairs)
    return {
        "model": MODEL, "reasoning": {"effort": EFFORT}, "store": False, "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME, "schema": RESPONSE_SCHEMA, "strict": True}},
    }


def request_hash(req: dict) -> str:
    return sha256_text(json.dumps({"v": PROMPT_VERSION, **req}, sort_keys=True, ensure_ascii=False))


def stories_of(output: dict, pairs: list[tuple[Sheet, Job]]) -> dict[str, dict]:
    """Wonder's stories keyed by job id: by sheet id, else by position."""
    stories = output.get("stories") if isinstance(output, dict) else None
    if not isinstance(stories, list):
        return {}
    out: dict[str, dict] = {}
    for i, (sh, job) in enumerate(pairs):
        st = next((x for x in stories if isinstance(x, dict) and x.get("sheet_id") == sh.sheet_id), None)
        if st is None and i < len(stories) and isinstance(stories[i], dict):
            st = stories[i]
        if st is not None:
            out[job.job_id] = st
    return out


# ============================================================ Model API: client, retries, cache, ledger

class BudgetExceeded(RuntimeError):
    pass


def ledger_total(path: Path = LEDGER_PATH) -> float:
    return round(sum(float(r.get("cost_usd") or 0.0) for r in read_jsonl(path)), 6)


def cost_usd(usage: dict) -> float:
    inp, cached, out = usage.get("input_tokens", 0), usage.get("cached_tokens", 0), usage.get("output_tokens", 0)
    cached = min(cached, inp)
    return ((inp - cached) * PRICE_PER_M["input"] + cached * PRICE_PER_M["cached_input"]
            + out * PRICE_PER_M["output"]) / 1e6


def _usage(resp: Any) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    det_in = getattr(u, "input_tokens_details", None)
    det_out = getattr(u, "output_tokens_details", None)
    return {"input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "cached_tokens": int(getattr(det_in, "cached_tokens", 0) or 0) if det_in else 0,
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "reasoning_tokens": int(getattr(det_out, "reasoning_tokens", 0) or 0) if det_out else 0}


def make_client():
    """OpenAI SDK client for Meta Model API. The key goes to the SDK only and is never printed or logged."""
    import openai
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv
        load_dotenv(DOTENV_PATH, override=False)
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if not key:
        raise SystemExit("MODEL_API_KEY is not set (add it to .env or export it). Nothing was sent.")
    return openai.OpenAI(api_key=key, base_url=BASE_URL, max_retries=0, timeout=TIMEOUT_S)


def call_with_retry(fn: Callable[[], Any], *, sleep: Callable[[float], None] = time.sleep) -> Any:
    """Retry 429/500/502/503 with exponential backoff and jitter, honoring Retry-After; never 504 or 4xx."""
    import openai
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn()
        except openai.APIStatusError as e:
            status = e.status_code
            if status not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                raise
            ra = None
            with contextlib.suppress(Exception):
                ra = float(e.response.headers.get("retry-after"))
            delay = min(60.0, ra if ra is not None else 2.0 ** attempt) + random.uniform(0, 1.0)
            print(f"  Model API {status}; retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.1f}s", flush=True)
            sleep(delay)
        except (openai.APIConnectionError, openai.APITimeoutError):
            if attempt == MAX_ATTEMPTS:
                raise
            sleep(2.0 ** attempt + random.uniform(0, 1.0))
    raise RuntimeError("unreachable")


def cache_get(key: str) -> Optional[dict]:
    p = WONDER_CACHE / f"{key}.json"
    with contextlib.suppress(OSError, ValueError):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(rec, dict) and isinstance(rec.get("output_text"), str):
            return rec
    return None


def cache_find(key: str, pairs: list[tuple["Sheet", "Job"]]) -> Optional[dict]:
    """The cached response for this exact request, else the newest one generated for the very same jobs (same
    sheets, angles and error types) under an earlier prompt revision; its record keeps its own hash and version."""
    hit = cache_get(key)
    if hit is not None or not WONDER_CACHE.is_dir():
        return hit
    want = [j.to_json() for _, j in pairs]
    best = None
    for p in WONDER_CACHE.glob("*.json"):
        with contextlib.suppress(OSError, ValueError):
            rec = json.loads(p.read_text(encoding="utf-8"))
            if rec.get("jobs") == want and isinstance(rec.get("output_text"), str):
                if best is None or rec.get("created_utc", "") > best.get("created_utc", ""):
                    best = rec
    return None if best is None else {**best, "legacy_prompt": True}


def cache_put(key: str, rec: dict) -> None:
    WONDER_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = WONDER_CACHE / f".{key}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(WONDER_CACHE / f"{key}.json")


def generate(pairs: list[tuple[Sheet, Job]], *, client=None, budget: float = DEFAULT_BUDGET_USD,
             allow_live: bool = True) -> Optional[dict]:
    """Wonder's output for one call (cached by request hash), or None when uncached and live calls are off.
    Before a live call, the budget guard reserves 1.5x the most expensive call so far against --budget."""
    req = build_request(pairs)
    key = request_hash(req)
    hit = cache_find(key, pairs)
    if hit is not None:
        return {**hit, "cached": True}
    if not allow_live:
        return None
    spent = ledger_total()
    per_call = [float(r["cost_usd"]) for r in read_jsonl(LEDGER_PATH) if r.get("cost_usd")]
    est = max(EST_CALL_USD * len(pairs), 1.5 * max(per_call)) if per_call else EST_CALL_USD * len(pairs)
    if spent + est > budget + 1e-9:
        raise BudgetExceeded(f"ledger ${spent:.4f} + estimate ${est:.4f} would pass the ${budget:.2f} budget")
    client = client or make_client()
    t0 = time.monotonic()
    resp = call_with_retry(lambda: client.responses.create(**req))
    usage = _usage(resp)
    cost = cost_usd(usage)
    rec = {"request_sha256": key, "jobs": [j.to_json() for _, j in pairs], "sheet_ids": [sh.sheet_id for sh, _ in pairs],
           "model": MODEL, "served_model": getattr(resp, "model", None), "effort": EFFORT,
           "prompt_version": PROMPT_VERSION, "created_utc": iso_z(datetime.now(timezone.utc)),
           "response_id": getattr(resp, "id", None), "status": getattr(resp, "status", None), "usage": usage,
           "cost_usd": round(cost, 6), "latency_s": round(time.monotonic() - t0, 2),
           "output_text": resp.output_text or ""}
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: rec[k] for k in ("created_utc", "request_sha256", "sheet_ids", "model", "usage",
                                                 "cost_usd", "latency_s", "status")}) + "\n")
    try:
        json.loads(rec["output_text"])
        cache_put(key, rec)
    except ValueError:
        rec["invalid_json"] = True
    return {**rec, "cached": False}


# ============================================================ jobs and split

def feasible_types(sh: Sheet) -> list[str]:
    out = ["period", "scale", "value"]
    if both_bases(sh):
        out.append("basis")
    if any(f.section == "peer" for f in sh.facts):
        out.append("entity")
    if any(f.section == "later" for f in sh.facts):
        out.append("look_ahead")
    return out


# Second ('b') stories, written after the first round was validated. They go to the two sheets whose first story
# code rejected (JPM Q1, META Q2), then to sheets that support the rarest error types (period, look_ahead, basis).
EXTRA_JOBS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("JPM-FY2026Q1", ("period", "basis", "look_ahead"), "markets"),
    ("META-FY2026Q2", ("period", "entity", "look_ahead"), "wire"),
    ("NVDA-FY2027Q2", ("period", "basis", "entity"), "markets"),
    ("MSFT-FY2026Q4", ("period", "scale", "look_ahead"), "markets"),
    ("AMD-FY2026Q1", ("period", "scale", "entity"), "markets"),
    ("GOOGL-FY2026Q2", ("period", "value", "look_ahead"), "wire"),
)


def plan_calls(sheets: list[Sheet], *, extra: int = 4) -> list[list[Job]]:
    """One 3-error story ('a') per sheet, written two per call (a company's two quarters), then the first `extra`
    EXTRA_JOBS ('b'), two per call. First-round types are assigned greedily so the six types stay balanced among
    the types each sheet supports. The plan is deterministic."""
    counts: Counter = Counter()

    def pick(sh: Sheet, k: int, salt: str) -> list[str]:
        cand = sorted(feasible_types(sh), key=lambda t: (counts[t], stable_frac(sh.sheet_id, salt, t)))[:k]
        counts.update(cand)
        return sorted(cand, key=ERROR_TYPES.index)

    def angle(sh: Sheet, types: list[str], salt: str) -> str:
        if "entity" in types or "look_ahead" in types:
            return "rival"
        return ("wire", "markets")[int(stable_frac(sh.sheet_id, salt, "angle") * 2)]

    ordered = sorted(sheets, key=lambda s: stable_frac("order", s.sheet_id))
    a_jobs: dict[str, Job] = {}
    for sh in ordered:
        types = pick(sh, 3, "a")
        a_jobs[sh.sheet_id] = Job(f"{sh.sheet_id}-a", sh.sheet_id, "a", angle(sh, types, "a"), types)
    calls: list[list[Job]] = []
    for t in TICKERS:
        mine = [a_jobs[s.sheet_id] for s in sheets if s.ticker == t]
        if mine:
            calls.append(mine)
    have = {s.sheet_id: s for s in sheets}
    b_jobs = [Job(f"{sid}-b", sid, "b", ang, list(types)) for sid, types, ang in EXTRA_JOBS[:extra]
              if sid in have and set(types) <= set(feasible_types(have[sid]))]
    calls += [b_jobs[i:i + 2] for i in range(0, len(b_jobs), 2)]
    return calls


def assign_split(items: list[dict]) -> None:
    """70/30 dev/test by sheet: both drafts of a sheet share a split. Companies are visited in a seeded order and
    each turn takes that company's next sheet (seeded order), so the test split spans many companies; sheets go
    to test while it holds at most 30% of the drafts. Deterministic and independent of input order."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[it["group_id"]].append(it)
    queues: dict[str, list[str]] = defaultdict(list)
    for gid in sorted(groups, key=lambda g: (stable_frac("split", g), g)):
        queues[groups[gid][0]["ticker"]].append(gid)
    tick_order = sorted(queues, key=lambda t: (stable_frac("ticker", t), t))
    rounds: list[str] = []
    while any(queues[t] for t in tick_order):
        for t in tick_order:
            if queues[t]:
                rounds.append(queues[t].pop(0))
    want_test = round((1 - DEV_FRACTION) * len(items))
    test_n = 0
    split_of: dict[str, str] = {}
    for g in rounds:
        if test_n + len(groups[g]) <= want_test:
            split_of[g] = "test"
            test_n += len(groups[g])
        else:
            split_of[g] = "dev"
    for it in items:
        it["split"] = split_of[it["group_id"]]


# ============================================================ corpus items

def make_item(sheet: Sheet, job: Job, gen: dict, v: Validation) -> dict:
    item_id = f"trap-{job.job_id}"
    return {
        "item_id": item_id, "corpus_version": VERSION, "ticker": sheet.ticker, "sheet_id": sheet.sheet_id,
        "job_id": job.job_id, "group_id": sheet.sheet_id, "text": v.text, "as_of": sheet.as_of,
        "context": sheet.context, "fiscal_year": sheet.fiscal_year, "fiscal_period": sheet.fiscal_period,
        "period_end": sheet.period_end, "release_accession": sheet.release_accession,
        "release_at": sheet.release_at, "requested_types": job.error_types, "angle": job.angle,
        "planted": v.planted, "accidental": v.accidental, "true_claims": v.true_claims,
        "sentences": v.sentences, "words": v.words, "validation_log": v.log,
        "generation": {"model": gen.get("model"), "served_model": gen.get("served_model"), "effort": gen.get("effort"),
                       "prompt_version": gen.get("prompt_version"), "request_sha256": gen.get("request_sha256"),
                       "usage": gen.get("usage"), "cost_usd": gen.get("cost_usd")},
        "item_hash": item_hash(v.text, sheet.as_of, sheet.context),
    }


def summarize(items: list[dict], rejects: list[dict]) -> str:
    L = [f"accepted {len(items)}, rejected {len(rejects)}"]
    sp = Counter(it.get("split", "-") for it in items)
    L.append(f"split: dev {sp['dev']}, test {sp['test']}")
    pt = Counter(e["type"] for it in items for e in it["planted"])
    at = Counter(e["type"] for it in items for e in it["accidental"])
    L.append("planted errors by type (code's label): " + ", ".join(f"{t} {pt[t]}" for t in ERROR_TYPES))
    L.append("accidental errors by type: " + (", ".join(f"{t} {n}" for t, n in at.items()) or "none"))
    L.append(f"true claims: {sum(len(it['true_claims']) for it in items)}")
    rel = sum(1 for it in items for e in it["planted"] if e["wonder_type"] != e["type"])
    dem = sum(1 for it in items for c in it["true_claims"] if c.get("demoted_plant"))
    L.append(f"planted errors relabeled by code: {rel}; planted 'errors' that were true (demoted): {dem}")
    rc = Counter(r.split(":")[0] for rj in rejects for r in rj["reasons"])
    if rc:
        L.append("reject reasons: " + ", ".join(f"{k} {n}" for k, n in rc.most_common()))
    return "\n".join(L)


# ============================================================ main

def build_sheets(store) -> list[Sheet]:
    sb = SheetBuilder(store)
    out = []
    for t in TICKERS:
        for q in sb.latest_quarters(t):
            sh = sb.build(t, q)
            if sh is not None:
                out.append(sh)
    return out


def calendars_of(store) -> dict[str, list]:
    return {t: _quarters(store, store.resolve_entity(t)[0]) for t in TICKERS}


def corpus_fingerprints(path: Path) -> dict:
    """Hash raw lines, including their original whitespace. Held-out rows are never sent to a validator."""
    raw = path.read_bytes()
    lines = {"dev": [], "test": []}
    for line in raw.splitlines(keepends=True):
        if line.strip():
            split = json.loads(line).get("split")
            if split not in lines:
                raise ValueError("every frozen corpus row must have a dev or test split")
            lines[split].append(line)
    return {"corpus_sha256": hashlib.sha256(raw).hexdigest(),
            "dev_sha256": hashlib.sha256(b"".join(lines["dev"])).hexdigest(),
            "held_out_sha256": hashlib.sha256(b"".join(lines["test"])).hexdigest(),
            "dev_rows": len(lines["dev"]), "held_out_rows": len(lines["test"])}


def validate_committed_dev(store, path: Optional[Path] = None) -> dict:
    """Rebuild only dev labels from their pinned Wonder responses and current evidence, without any writes.

    Do not use cache_find: a later response for the same jobs is not the original generation. Do not select the
    latest quarters: a frozen draft retains its own quarter. A missing cache is a failure, never a live call.
    """
    path = path or TRAPS_PATH
    before = corpus_fingerprints(path)
    items = [it for it in read_jsonl(path) if it["split"] == "dev"]
    builder = SheetBuilder(store)
    evidence, calendars = StoreIndex(store).items, calendars_of(store)
    errors = []
    verified = 0
    gold_errors: Counter = Counter()
    true_figures = guidance_figures = 0
    for it in items:
        key = it.get("generation", {}).get("request_sha256", "")
        gen = cache_get(key) if re.fullmatch(r"[0-9a-f]{64}", key) else None
        problem = None
        if gen is None:
            problem = "original Wonder response missing from local cache; no model call made"
        elif gen.get("request_sha256") != key or gen.get("model") != MODEL:
            problem = "cached generation does not match the pinned request and Standard-mode Wonder model"
        if problem:
            errors.append({"item_id": it["item_id"], "reason": problem})
            continue
        jobs = [j for j in gen.get("jobs", []) if j.get("job_id") == it["job_id"]
                and j.get("sheet_id") == it["sheet_id"]]
        try:
            output = json.loads(gen["output_text"])
            stories = [s for s in output.get("stories", []) if isinstance(s, dict)
                       and s.get("sheet_id") == it["sheet_id"]]
        except (ValueError, AttributeError):
            stories = []
        if len(jobs) != 1 or len(stories) != 1:
            errors.append({"item_id": it["item_id"], "reason": "original cached job/story is missing or ambiguous"})
            continue
        q = next((q for q in builder.quarters(it["ticker"])
                  if q[0] == it["fiscal_year"] and q[1] == it["fiscal_period"]
                  and q[3].isoformat() == it["period_end"]), None)
        sheet = builder.build(it["ticker"], q) if q is not None else None
        if sheet is None:
            errors.append({"item_id": it["item_id"], "reason": "original quarter's evidence is unavailable"})
            continue
        val = validate_draft(sheet, stories[0], evidence=evidence, calendars=calendars)
        if not val.accepted:
            errors.append({"item_id": it["item_id"], "reason": "gold validation rejected", "details": val.reasons})
            continue
        rebuilt = make_item(sheet, Job(**jobs[0]), gen, val)
        changed = [k for k, value in rebuilt.items() if value != it.get(k)]
        if changed:
            errors.append({"item_id": it["item_id"], "reason": "frozen dev row differs", "fields": changed})
        else:
            verified += 1
            gold_errors.update(e["type"] for e in val.planted + val.accidental)
            true_figures += sum(not c["forward_looking"] for c in val.true_claims)
            guidance_figures += sum(c["forward_looking"] for c in val.true_claims)
    after = corpus_fingerprints(path)
    unchanged = before == after
    if not items:
        errors.append({"reason": "no dev rows in the frozen corpus"})
    if not unchanged:
        errors.append({"reason": "corpus changed during validation"})
    return {"mode": "validate-dev", "validated_dev_rows": verified, "dev_rows": len(items),
            "held_out_rows_validated": 0, "corpus_unchanged": unchanged, "before": before, "after": after,
            "gold": {"errors_by_type": dict(sorted(gold_errors.items())), "errors": sum(gold_errors.values()),
                     "true_figures": true_figures, "guidance_figures": guidance_figures},
            "model_calls": 0, "cost_usd": 0.0, "errors": errors, "ok": not errors}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheets", action="store_true", help="print the fact sheets and the call plan, then stop")
    ap.add_argument("--dry-run", action="store_true",
                    help="no model calls; an existing frozen corpus gets dev-only validation, with no writes")
    ap.add_argument("--validate-dev", action="store_true",
                    help="validate only frozen dev rows against their original cached Wonder responses; never write")
    ap.add_argument("--json-out", type=Path, help="optional validation summary, for --validate-dev/--dry-run")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD,
                    help="USD cap on the generation ledger total (every Wonder call ever made by this script)")
    ap.add_argument("--extra", type=int, default=4, help="how many EXTRA_JOBS second stories to write")
    ap.add_argument("--only", help="only calls containing these job ids or sheet ids (comma-separated)")
    ap.add_argument("--max-calls", type=int, default=None, help="at most this many live calls in this run")
    args = ap.parse_args(argv)

    frozen = TRAPS_PATH.exists() and any(it.get("split") == "test" for it in read_jsonl(TRAPS_PATH))
    validate_only = args.validate_dev or (args.dry_run and frozen)
    if validate_only and (args.sheets or args.only or args.max_calls is not None):
        ap.error("dev validation cannot be combined with --sheets, --only or --max-calls")
    if args.json_out and not validate_only:
        ap.error("--json-out requires --validate-dev, or --dry-run with a frozen corpus")
    if args.json_out and args.json_out.resolve() in {TRAPS_PATH.resolve(), REJECTS_PATH.resolve()}:
        ap.error("validation output cannot overwrite corpus files")
    if frozen and not validate_only and not args.sheets:
        raise SystemExit("trap_v1 has a frozen held-out split; use --validate-dev (cache-only, no corpus writes). "
                         "Build new drafts as a separate corpus version.")
    from receipts.store import Store
    store = Store()
    if validate_only:
        try:
            report = validate_committed_dev(store)
        finally:
            store.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if report["ok"] else 1
    sheets = build_sheets(store)
    by_sheet = {s.sheet_id: s for s in sheets}
    calls = plan_calls(sheets, extra=args.extra)
    if args.only:
        want = set(args.only.split(","))
        calls = [c for c in calls if any(j.job_id in want or j.sheet_id in want for j in c)]
    if args.sheets:
        for sh in sheets:
            print(f"=== {sh.sheet_id}  as_of {sh.as_of}  release {sh.release_accession} at {sh.release_at}")
            print(render_sheet(sh))
            print()
        counts = Counter(t for c in calls for j in c for t in j.error_types)
        for c in calls:
            print(" + ".join(f"{j.job_id} [{j.angle}: {','.join(j.error_types)}]" for j in c))
        print("planned error types:", dict(counts))
        return 0
    index = StoreIndex(store)
    cals = calendars_of(store)
    client = None
    items, rejects = [], []
    live = 0
    complete = True
    for call in calls:
        pairs = [(by_sheet[j.sheet_id], j) for j in call]
        allow = not args.dry_run and (args.max_calls is None or live < args.max_calls)
        try:
            if allow and client is None and cache_find(request_hash(build_request(pairs)), pairs) is None:
                client = make_client()
            gen = generate(pairs, client=client, budget=args.budget, allow_live=allow)
        except BudgetExceeded as e:
            print(f"budget stop before {[j.job_id for j in call]}: {e}")
            complete = False
            break
        if gen is None:
            print(f"{[j.job_id for j in call]}: not generated (no cache, live calls off)")
            complete = False
            continue
        if not gen.get("cached"):
            live += 1
            print(f"{[j.job_id for j in call]}: generated ${gen['cost_usd']:.4f} {gen['usage']} {gen['latency_s']}s; "
                  f"ledger ${ledger_total():.4f}", flush=True)
        try:
            stories = stories_of(json.loads(gen["output_text"]), pairs)
        except ValueError:
            stories = {}
        for sh, job in pairs:
            out = stories.get(job.job_id)
            if out is None:
                rejects.append({"job_id": job.job_id, "sheet_id": sh.sheet_id, "reasons": ["no story in Wonder's output"],
                                "request_sha256": gen.get("request_sha256")})
                print(f"{job.job_id}: REJECT no story")
                continue
            v = validate_draft(sh, out, evidence=index.items, calendars=cals)
            if v.accepted:
                items.append(make_item(sh, job, gen, v))
                print(f"{job.job_id}: ACCEPT {len(v.planted)} planted + {len(v.accidental)} accidental, "
                      f"{len(v.true_claims)} true, {v.words} words")
            else:
                rejects.append({"job_id": job.job_id, "sheet_id": sh.sheet_id, "reasons": v.reasons, "log": v.log,
                                "text": v.text, "planted_errors": out.get("planted_errors"),
                                "requested_types": job.error_types, "request_sha256": gen.get("request_sha256")})
                print(f"{job.job_id}: REJECT {v.reasons[:4]}")
            for line in v.log:
                print(f"    log: {line}")
    if not complete:
        print("incomplete generation: corpus files not rewritten")
        return 1
    if args.only or args.max_calls is not None:
        print("partial run (--only/--max-calls): corpus files not rewritten")
        if items or rejects:
            print(summarize(items, rejects))
        return 0
    assign_split(items)
    items.sort(key=lambda it: it["item_id"])
    write_jsonl(TRAPS_PATH, items)
    write_jsonl(REJECTS_PATH, sorted(rejects, key=lambda r: r["job_id"]))
    print(f"wrote {TRAPS_PATH.relative_to(ROOT)} ({len(items)}) and {REJECTS_PATH.relative_to(ROOT)} ({len(rejects)})")
    print(summarize(items, rejects))
    print(f"generation ledger total: ${ledger_total():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
