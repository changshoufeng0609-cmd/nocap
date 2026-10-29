#!/usr/bin/env python
"""Build the Receipts seeded-error corpus v0: eval/corpus/v0_{sources,dev,test}.jsonl.

Two stages. Labels come from code only, never from an LLM, so every gold label is exact.

Stage 1, sources (needs data/cache/edgar; makes at most --max-requests live SEC calls at 2 req/s):
  1. For each of the 10 companies, pick the 3 most recent quarters that already have a filed 10-Q
     (so XBRL ground truth exists), and the Item 2.02 8-K that first published that quarter.
  2. Fetch the EX-99.1 press release (spikes/a_edgar.py: polite client, shared content cache).
  3. Split it into sentences and tag every number: unit, scale, written precision, period role,
     metric, segment qualifier, comparison (YoY/QoQ), and stated basis.
  4. Screen each sentence against the company's XBRL companyfacts. A sentence enters the pool only if
     it states 1-3 financial numbers about the reporting quarter, no number contradicts XBRL, and every
     number is either XBRL-consistent or self-describing (it names its segment, basis, or currency
     adjustment). This drops sentences that only make sense under a section heading, for example a
     segment bullet that just says "Revenue was $89.0 billion".
  5. Keep about 10 guidance sentences as forward-looking controls.
  Output: eval/corpus/v0_sources.jsonl, with rows of kind "company" (XBRL series used for screening),
  "document" (one press release), and "sentence" (text, offsets, number tags).

Stage 2, items: a pure function of v0_sources.jsonl, so the tests can rebuild it offline.
  6 deterministic mutators (value, period, scale, basis, look_ahead, entity). Each mutant has exactly one
  changed claim and a gold label. Then a group-aware 70/30 dev/test split, stratified by company and
  mutator with a fixed seed. An original and all its mutants always land in the same split.
  Output: eval/corpus/v0_dev.jsonl and eval/corpus/v0_test.jsonl.

Usage:
  .venv/bin/python eval/build_corpus.py               # both stages
  .venv/bin/python eval/build_corpus.py --items-only  # stage 2 only, offline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "eval" / "corpus"
SOURCES_PATH = CORPUS_DIR / "v0_sources.jsonl"
DEV_PATH = CORPUS_DIR / "v0_dev.jsonl"
TEST_PATH = CORPUS_DIR / "v0_test.jsonl"
EDGAR_CACHE = ROOT / "data" / "cache" / "edgar"

VERSION = "v0"
SEED = 20261028
TICKERS = ("NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "JPM", "BAC", "AMD", "NFLX")
QUARTERS_PER_COMPANY = 3
FILINGS_CUTOFF = date(2025, 6, 1)    # submissions window used to find the 10-Qs and 8-Ks
SERIES_FROM = date(2024, 6, 1)       # XBRL quarters kept for screening (covers year-ago of every corpus quarter)
DEFAULT_MAX_REQUESTS = 100           # hard cap on live SEC calls per build (the task allows 120)
POOL_PER_DOC = 7                     # at most this many pool sentences per press release
FL_CONTROLS = 10
TARGET_PER_MUTATOR = 28
MAX_MUTANTS_PER_ORIGINAL = 2
DEV_FRACTION = 0.70
MUTATORS = ("value", "period", "scale", "basis", "look_ahead", "entity")
EXPECTED_REASON = {"value": "value_mismatch", "period": "period_mismatch", "scale": "scale_mismatch",
                   "basis": "basis_mismatch", "look_ahead": "look_ahead", "entity": "entity_mismatch"}
# Basis appendix (Day 3, append-only): 4-5-number sentences that state both bases, from the same 30 releases.
# Their sentence rows carry pool "basis_extra", so the v0 pool, mutants and split never see them; their items
# carry batch "basis5" and are split on their own (assign_split is per batch), so every v0 item keeps its id
# and its dev/test assignment.
BASIS_EXTRA_POOL = "basis_extra"
BASIS_EXTRA_BATCH = "basis5"
BASIS_EXTRA_NUMBERS = (4, 5)
# "Net income, on a GAAP basis, was $27.7 billion and increased 12%, and on a non-GAAP basis was $30.8 billion ..."
BASIS_PAIR_RE = re.compile(r"\bon a (GAAP) basis\b.*?\band on a (non-GAAP) basis\b")
# "On a non-GAAP(*) basis, gross margin was 54%, operating income was $2.2 billion, ..."
BASIS_ALL_RE = re.compile(r"^On a (non-GAAP)(?:\(\*\))? basis, ")


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str                 # display name for contexts and entity swaps
    aliases: tuple[str, ...]  # how press releases write the name; longest first
    exchange: str
    sector: str               # "tech" or "bank"; banks have no gross margin or operating income


COMPANIES: dict[str, Company] = {c.ticker: c for c in (
    Company("NVDA", "NVIDIA", ("NVIDIA Corporation", "NVIDIA"), "NASDAQ", "tech"),
    Company("AAPL", "Apple", ("Apple Inc.", "Apple"), "NASDAQ", "tech"),
    Company("MSFT", "Microsoft", ("Microsoft Corporation", "Microsoft"), "NASDAQ", "tech"),
    Company("META", "Meta", ("Meta Platforms, Inc.", "Meta Platforms", "Meta"), "NASDAQ", "tech"),
    Company("AMZN", "Amazon", ("Amazon.com, Inc.", "Amazon.com", "Amazon"), "NASDAQ", "tech"),
    Company("GOOGL", "Alphabet", ("Alphabet Inc.", "Alphabet"), "NASDAQ", "tech"),
    Company("JPM", "JPMorgan Chase", ("JPMorgan Chase & Co.", "JPMorganChase", "JPMorgan Chase", "JPMorgan"),
            "NYSE", "bank"),
    Company("BAC", "Bank of America", ("Bank of America Corporation", "Bank of America"), "NYSE", "bank"),
    Company("AMD", "AMD", ("Advanced Micro Devices, Inc.", "Advanced Micro Devices", "AMD"), "NASDAQ", "tech"),
    Company("NFLX", "Netflix", ("Netflix, Inc.", "Netflix"), "NASDAQ", "tech"),
)}

# Sentences excluded after a manual read of the pool, with the reason. Matched by sentence prefix.
MANUAL_EXCLUDE: dict[str, str] = {}


# ============================================================ small helpers

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def rng_for(*parts: Any) -> random.Random:
    """A deterministic RNG keyed by the seed and the given parts (stable across Python runs)."""
    key = "|".join([str(SEED), *map(str, parts)])
    return random.Random(int(sha256_text(key)[:16], 16))


def stable_frac(*parts: Any) -> float:
    return int(sha256_text("|".join([str(SEED), *map(str, parts)]))[:12], 16) / float(16 ** 12)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ============================================================ text extraction

BLOCK_TAGS = ("p", "div", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6")
_WS = re.compile(r"\s+")


def normalize_ws(s: str) -> str:
    for a, b in (("\xa0", " "), (" ", " "), (" ", " "), (" ", " "), (" ", " "),
                 ("​", ""), ("﻿", ""), ("­", "")):
        s = s.replace(a, b)
    return _WS.sub(" ", s).strip()


def html_blocks(html: bytes) -> list[str]:
    """Leaf text blocks of an EDGAR HTML document, in document order, whitespace-normalized."""
    from bs4 import BeautifulSoup  # stage 1 only

    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "head", "title"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with(" ")
    blocks = []
    for el in soup.find_all(BLOCK_TAGS):
        if el.find(list(BLOCK_TAGS) + ["table", "tr"]):
            continue
        t = normalize_ws(el.get_text(""))
        if t:
            blocks.append(t)
    return blocks


ABBREVIATIONS = {
    "inc", "corp", "co", "ltd", "llc", "plc", "u.s", "u.k", "calif", "mr", "ms", "mrs", "dr", "no", "vs", "approx",
    "st", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "e.g", "i.e", "etc",
    "n.a", "a.m", "p.m", "l.p", "est", "dept", "fig", "nos", "jr", "sr",
}
_BOUNDARY = re.compile(r"[.!?][”\"’)\]]*\s+(?=[“\"‘(\[]?[A-Z0-9•◦$])")
_BULLET_SPLIT = re.compile(r"\s*[•◦▪●]\s*")
_LEADING_JUNK = re.compile(r"^(?:[•◦▪●■\-–—]\s*|n\s(?=[A-Z$#]))+")


def split_sentences(block: str) -> list[tuple[int, str]]:
    """(offset in block, sentence) pairs. Splits on sentence punctuation and on bullet glyphs."""
    out: list[tuple[int, str]] = []
    pieces: list[tuple[int, str]] = []
    pos = 0
    for m in _BULLET_SPLIT.finditer(block):
        if m.start() > pos:
            pieces.append((pos, block[pos:m.start()]))
        pos = m.end()
    pieces.append((pos, block[pos:]))
    for base, piece in pieces:
        start = 0
        for m in _BOUNDARY.finditer(piece):
            end = m.start() + 1
            word = re.search(r"([A-Za-z.]+)$", piece[start:m.start()])
            w = word.group(1).lower().rstrip(".") if word else ""
            if w in ABBREVIATIONS or re.fullmatch(r"[a-z]", w or "-") or re.fullmatch(r"(?:[a-z]\.)+[a-z]", w or "-"):
                continue
            seg = piece[start:end]
            out.append((base + start, seg))
            start = m.end()
        out.append((base + start, piece[start:]))
    cleaned = []
    for off, s in out:
        lead = len(s) - len(s.lstrip())
        s2 = s.strip()
        m = _LEADING_JUNK.match(s2)
        if m:
            lead += m.end()
            s2 = s2[m.end():]
        s2 = s2.strip()
        if s2:
            cleaned.append((off + lead, s2))
    return cleaned


# ============================================================ numbers

NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MONEY_RE = re.compile(
    r"(?P<neg>negative\s+)?(?:(?<=[\s(])(?P<sign>[-−]))?(?P<cur>\$)\s?(?P<num>" + NUM + r")"
    r"(?:\s?(?:-|–)\s?\$?(?P<num2>" + NUM + r"))?"
    r"(?:\s?(?P<scale>trillion|billion|million|thousand)s?(?![A-Za-z])|(?P<abbr>bn|mn|[TBMK])(?![A-Za-z]))?")
PCT_RE = re.compile(
    r"(?:(?<=[\s(])(?P<sign>[+−-]))?(?<![\w.,$])(?P<num>" + NUM + r")"
    r"(?:\s?(?:-|–)\s?(?P<num2>" + NUM + r"))?"
    r"\s?(?P<unit>%|percent(?:age points?)?\b|per cent\b|basis points?\b|bps\b)")
SCALE_OF = {"trillion": "trillions", "billion": "billions", "million": "millions", "thousand": "thousands",
            "T": "trillions", "B": "billions", "bn": "billions", "M": "millions", "mn": "millions", "K": "thousands"}
SCALE_FACTOR = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9, "trillions": 1e12}

MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(MONTHS + r"\.?\s+\d{1,2},?\s+\d{4}|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
YEAR_RE = re.compile(r"(?<![\d$.,])(?:19|20)\d{2}(?![\d%])")
QLABEL_RE = re.compile(r"\b(?:Q[1-4](?:\s?[’']\d{2})?|[1-4]Q\d{2}|H[12](?:\s?[’']\d{2})?|[1-4]H\d{2}|FY\s?\d{2,4})\b")
ORDINAL_RE = re.compile(r"\b\d+(?:st|nd|rd|th)\b")
GLUED_RE = re.compile(r"(?<=[A-Za-z)®™*])\d{1,3}\b")   # footnote markers and product names: revenue2, (FCF)9, MI308
FOOTNOTE_RE = re.compile(r"\(\d{1,2}\)|\*+")             # "(1)" footnote references
DIGIT_RE = re.compile(r"\d")


@dataclass
class Num:
    start: int                # span of the whole mention in the sentence, e.g. "$96.2 billion"
    end: int
    text: str
    dstart: int               # span of the digits a value mutator rewrites, e.g. "96.2"
    dend: int
    kind: str                 # money | pct | pp | bps
    value: float              # as written, scale not applied
    decimals: int
    scale: str                # units | thousands | millions | billions | trillions
    scale_start: int = -1     # span of the written scale word ("billion", "B"), -1 if none
    scale_end: int = -1
    thousands_sep: bool = False
    sign: int = 1
    is_range: bool = False
    approx: Optional[str] = None     # approximately | over | nearly | ...
    role: str = "current"            # current | prior | forward
    metric: Optional[str] = None
    qualifier: Optional[str] = None  # segment or sub-metric name, e.g. "Data Center"
    growth: bool = False
    comparison: Optional[str] = None  # YoY | QoQ | None (not stated)
    basis: Optional[str] = None       # GAAP | non-GAAP | None
    fx_adjusted: bool = False
    screen: str = "na"                # ok | mismatch | na
    screen_detail: Optional[str] = None

    def scaled(self) -> float:
        return self.sign * self.value * SCALE_FACTOR[self.scale]


def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def find_numbers(s: str) -> list[Num]:
    nums: list[Num] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for m in MONEY_RE.finditer(s):
        a = m.start("neg") if m.group("neg") else (m.start("sign") if m.group("sign") else m.start("cur"))
        scale_word = m.group("scale") or m.group("abbr")
        scale = SCALE_OF.get(scale_word, "units") if scale_word else "units"
        sgn = -1 if (m.group("neg") or m.group("sign")) else 1
        n = Num(start=a, end=m.end(), text=s[a:m.end()], dstart=m.start("num"), dend=m.end("num"), kind="money",
                value=float(m.group("num").replace(",", "")), decimals=_decimals(m.group("num")), scale=scale,
                thousands_sep="," in m.group("num"), sign=sgn, is_range=bool(m.group("num2")))
        if scale_word:
            g = "scale" if m.group("scale") else "abbr"
            n.scale_start, n.scale_end = m.start(g), m.end(g)
        nums.append(n)
        taken.append((a, m.end()))
    for m in PCT_RE.finditer(s):
        if not free(m.start(), m.end()):
            continue
        unit = m.group("unit").lower()
        kind = "bps" if unit.startswith(("basis", "bps")) else "pp" if "point" in unit else "pct"
        a = m.start("sign") if m.group("sign") else m.start("num")
        sgn = -1 if m.group("sign") in ("-", "−") else 1
        nums.append(Num(start=a, end=m.end(), text=s[a:m.end()], dstart=m.start("num"), dend=m.end("num"),
                        kind=kind, value=float(m.group("num").replace(",", "")), decimals=_decimals(m.group("num")),
                        scale="units", thousands_sep="," in m.group("num"), sign=sgn,
                        is_range=bool(m.group("num2"))))
        taken.append((m.start(), m.end()))
    nums.sort(key=lambda n: n.start)
    return nums


def other_numeric(s: str, nums: list[Num]) -> bool:
    """True if the sentence has digits that are not money, percentages, dates, years, quarter labels,
    ordinals, or footnote markers, for example a count like "325 million paid memberships"."""
    chars = list(s)
    spans = [(n.start, n.end) for n in nums]
    for rx in (DATE_RE, YEAR_RE, QLABEL_RE, ORDINAL_RE, GLUED_RE, FOOTNOTE_RE):
        spans += [(m.start(), m.end()) for m in rx.finditer(s)]
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return bool(DIGIT_RE.search("".join(chars)))


# ============================================================ tagging

FL_BENIGN = re.compile(
    r"(?:consistent with|in[- ]line with|ahead of|above|below|versus|vs\.?|than|exceeded|beat|outperformed) "
    r"(?:our |the )?(?:prior |previous )?(?:forecast|guidance|expectations|outlook)s?"
    r"|higher[- ]than[- ](?:forecasted|planned|projected|expected)|than (?:forecasted|planned|expected)", re.I)
FL_RE = re.compile(
    r"\b(?:expect(?:s|ed|ing|ations?)?|anticipat(?:e|es|ed|ing)|forecast(?:s|ed|ing)?|outlook|guidance|"
    r"project(?:s|ed|ing|ions?)?|will|would|could|should|plan(?:s|ned)?|intend(?:s|ed)?|target(?:s|ing)?|"
    r"estimate(?:s|d)?|believe(?:s|d)?|on track|going forward|next quarter|remainder of|pipeline)\b", re.I)
FL_LOWER = re.compile(r"\b(?:may|might)\b")
NON_QUARTER_RE = re.compile(
    r"trailing (?:twelve|12) months|year[- ]to[- ]date|\bYTD\b|first (?:six|nine) months|first half|"
    r"\bfull[- ]year\b|\bannual(?:ized)?\b|run[- ]rate|for the year|nine months|"
    r"\bfor (?:fiscal (?:year )?)?(?:19|20)\d{2}\b", re.I)
COUNTERFACTUAL_RE = re.compile(r"would have|had it not|excluding this charge|absent the", re.I)
FL_CONTROL_CUE = re.compile(r"\b(?:expects?|expected|anticipates?|projects?|forecasts?|guidance|outlook)\b",
                            re.I)
PAST_ACTUAL_RE = re.compile(r"\b(?:recognized|recorded|incurred|reported|was|were|grew|increased|decreased)\b", re.I)

YOY_PHRASES = [
    (r"from a year ago", "from the previous quarter"),
    (r"from the year[- ]ago quarter", "from the previous quarter"),
    (r"year over year", "quarter over quarter"),
    (r"year-over-year", "quarter-over-quarter"),
    (r"\bYoY\b", "QoQ"),
    (r"\bY/Y\b", "Q/Q"),
    (r"from last year", "from last quarter"),
    (r"compared (?:with|to) (?:the )?(?:prior|previous)[- ]year(?: quarter| period)?", "compared with the prior quarter"),
]
QOQ_PHRASES = [
    (r"from the (?:previous|prior) quarter", "from a year ago"),
    (r"\bsequentially\b", "year over year"),
    (r"quarter over quarter", "year over year"),
    (r"quarter-over-quarter", "year-over-year"),
    (r"\bQoQ\b", "YoY"),
    (r"\bQ/Q\b", "Y/Y"),
    (r"from Q[1-4]\b", "from a year ago"),
    (r"from last quarter", "from last year"),
]
_YOY_ANY = re.compile("|".join(p for p, _ in YOY_PHRASES), re.I)
_QOQ_ANY = re.compile("|".join(p for p, _ in QOQ_PHRASES), re.I)

GROWTH_BEFORE = re.compile(
    r"(?:\bup|\bdown|increased?|decreased?|\bgrew|growth of|growing|\brose|\bfell|declined?|increase of|decrease of|"
    r"gain of|improved|expanded by|accelerating to|\bby)\s*(?:by\s+)?(?:approximately |about |nearly |over |more than |a record )?\(?[+−-]?$", re.I)
GROWTH_AFTER = re.compile(r"^\)?\s*(?:growth|increase|decrease|higher|lower|year[- ]over[- ]year|YoY)\b", re.I)
DOWN_BEFORE = re.compile(r"(?:\bdown|decreased?|\bfell|declined?|decrease of|lower)\s*(?:by\s+)?(?:approximately |about )?\(?$", re.I)
APPROX_BEFORE = re.compile(
    r"(?:approximately|approx\.|about|around|roughly|nearly|almost|over|more than|less than|at least|up to|"
    r"just over|just under|in excess of|crossed|surpassed|exceeded|topped|passed|eclipsed|reached|~)\s*"
    r"(?:a record\s+)?$", re.I)
FX_AFTER = re.compile(r"^[^.;]{0,40}?(?:constant currency|F/X neutral|FX-neutral|F/X|foreign exchange|"
                      r"excluding (?:changes in )?foreign exchange)", re.I)
PRIOR_BEFORE = re.compile(r"(?:compared (?:with|to)|versus|vs\.?|up from|down from|from)\s*$", re.I)
PRIOR_AFTER = re.compile(
    r"^[^;]{0,25}?\b(?:in (?:the )?(?:prior|previous|year[- ]ago)\b|a year ago|last year|"
    r"in Q[1-4]\s?[’']\d{2}|in (?:the )?(?:first|second|third|fourth) quarter(?: of)? (?:fiscal (?:year )?)?\d{4}|"
    r"in [1-4]Q\d{2}|in the prior year period|in the year ago quarter)", re.I)

METRIC_KEYWORDS: list[tuple[str, str]] = [
    ("eps_diluted", r"(?:diluted )?(?:earnings|net income) per (?:diluted )?(?:common )?share|\bdiluted EPS\b|"
                    r"\bEPS\b|per diluted share"),
    ("net_interest_income", r"net interest income|\bNII\b"),
    ("operating_cash_flow", r"operating cash flow|cash flow from operating activities|"
                            r"cash (?:generated|provided) (?:from|by) operating activities|cash flow from operations"),
    ("free_cash_flow", r"free cash flow|\bFCF\b"),
    ("gross_margin", r"gross margins?"),
    ("operating_margin", r"operating margins?"),
    ("operating_income", r"operating income|income from operations"),
    ("net_income", r"net income(?! per)|net earnings|net profit"),
    ("costs_and_expenses", r"(?:total )?costs and expenses"),
    ("noninterest_expense", r"noninterest expense|\bexpense\b"),
    ("opex", r"operating expenses"),
    ("capital_returned", r"\breturn(?:ed|ing)?\b[^.;]{0,60}?\bto (?:shareholders|stockholders)"),
    ("noninterest_revenue", r"noninterest revenue|noninterest income"),
    ("revenue", r"revenues?|net sales|\bsales\b"),
    ("tax_rate", r"tax rate"),
    ("other", r"dividends?|capital expenditures|headcount|deposits|loans|assets under management|\bAUM\b|"
              r"client balances|book value|ROTCE|\bROE\b|CET1|charge-offs|provision for credit losses|"
              r"ad impressions|price per ad|daily active|memberships|view hours|run rate|backlog|"
              r"remaining performance obligation|wallet share|market share|overhead ratio|efficiency ratio|"
              r"credit costs|cash, cash equivalents|long-term debt|fees|investment assets|marketable securities|"
              r"share repurchase authorization|tax benefit|charge|gain|loss"),
]
_METRIC_RX = [(m, re.compile(rx, re.I)) for m, rx in METRIC_KEYWORDS]
VOCAB_METRICS = {"revenue", "segment_revenue", "eps_diluted", "net_interest_income", "operating_cash_flow",
                 "free_cash_flow", "gross_margin", "operating_margin", "operating_income", "net_income",
                 "costs_and_expenses", "noninterest_expense", "opex", "capital_returned", "tax_rate",
                 "noninterest_revenue", "impact"}
QUALIFIABLE = {"revenue", "operating_income", "net_income", "gross_margin", "operating_margin", "noninterest_expense",
               "costs_and_expenses", "opex", "eps_diluted", "net_interest_income", "noninterest_revenue"}
GENERIC_WORDS = {
    "the", "total", "net", "company", "company’s", "company's", "consolidated", "quarterly", "record", "gaap",
    "non-gaap", "revenue", "revenues", "sales", "first", "second", "third", "fourth", "first-quarter",
    "second-quarter", "third-quarter", "fourth-quarter", "fiscal", "diluted", "basic", "operating", "income",
    "gross", "our", "its", "this", "firm", "in", "for", "during", "both", "eps", "cash", "flow", "free", "earnings",
    "overall", "reported", "year", "quarter", "sequential", "costs", "expenses", "and", "a", "an", "q1", "q2", "q3",
    "q4", "on", "all-time", "strong", "quarter’s", "quarter's", "revenue –", "–", "—", "-",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december",
}
for _c in COMPANIES.values():
    for _a in _c.aliases:
        GENERIC_WORDS.update(w.lower().strip(",.") for w in _a.split())
    GENERIC_WORDS.add(_c.ticker.lower())
GENERIC_WORDS -= {"america"}  # "North America segment" is a segment, not Bank of America
QUALIFIER_LOWER = {"segment", "business", "subscription", "advertising", "ad", "ads", "services", "service",
                   "product", "products", "cloud", "hardware", "software", "platform", "licensing", "markets",
                   "trading", "banking", "lending", "card", "commercial", "consumer", "international", "domestic",
                   "regional", "gaming", "client", "embedded", "automotive", "networking", "compute", "devices",
                   "search", "subscriptions"}
STOP_LEFT = re.compile(r"[,;:(–—]|\b(?:was|were|is|are|of|as|with|to|by|that|which|while|and|or|from|at)\s*$", re.I)


PRONOUNS = {"we", "our", "us", "its", "their", "it", "they", "i"}
_WORD_TOKEN = re.compile(r"[A-Za-z][\w&’'.-]*")


def _qualifier(s: str, ks: int, ke: int) -> Optional[str]:
    """Segment or sub-metric words directly in front of a metric keyword ('Data Center revenue',
    'North America segment sales') or right after it ('Revenue in Intelligent Cloud'). Generic words
    ('Total', 'Second-quarter', company names) are skipped; any other lowercase word ends the phrase."""
    picked: list[str] = []
    left = s[max(0, ks - 70):ks]
    for w in reversed(left.split()):
        if re.search(r"[,;:()–—“”\"]", w) or not _WORD_TOKEN.fullmatch(w.strip("’'")):
            break
        wl = w.lower().strip("’'.")
        if wl in GENERIC_WORDS or QLABEL_RE.fullmatch(w.strip("’'")):
            continue
        if wl in PRONOUNS:
            break
        if w[:1].isupper() or wl in QUALIFIER_LOWER or wl in {"and", "&"} and picked:
            picked.append(w)
            continue
        break
    while picked and picked[-1].lower() in {"and", "&"}:
        picked.pop()
    right = re.match(r"\s+(?:in|from) (?:the )?((?:[A-Z][\w&’'.-]*(?:\s+(?:and|&)\s+|\s+)?)+)", s[ke:ke + 80])
    if right:
        phrase = right.group(1).strip()
        if phrase.split()[0].lower() not in GENERIC_WORDS and not QLABEL_RE.match(phrase):
            picked.insert(0, phrase)
    words = [p for p in reversed(picked) if p.lower() not in GENERIC_WORDS]
    return " ".join(words).strip() or None


def _inherit_from(nums: list[Num], i: int) -> Optional[Num]:
    """The earlier number whose metric a keyword-less number inherits: the nearest one of a compatible kind
    (money in the same scale family, or any percent-like value), else the nearest one with a metric."""
    n = nums[i]
    per_share = n.kind == "money" and n.scale == "units"
    for m in reversed(nums[:i]):
        if not m.metric:
            continue
        if n.kind == "money" and m.kind == "money" and (m.scale == "units") == per_share:
            return m
        if n.kind in ("pct", "pp", "bps") and m.kind in ("pct", "pp", "bps"):
            return m
    return next((m for m in reversed(nums[:i]) if m.metric), None)


def is_forward_looking(s: str) -> bool:
    t = FL_BENIGN.sub(" ", s)
    return bool(FL_RE.search(t) or FL_LOWER.search(t))


def _prior_after(after: str, cur: Optional[tuple[int, str]]) -> bool:
    """True if the text after a number names a prior period ('in Q3'25', 'in second quarter 2025', 'a year
    ago'). A label of the document's own quarter ('in Q1'26' in a Q1 2026 release) is not prior."""
    m = PRIOR_AFTER.search(after)
    if not m:
        return False
    lab = re.search(r"Q([1-4])\s?[’']?(\d{2})\b|\b([1-4])Q(\d{2})\b|\b(first|second|third|fourth) quarter(?: of)? "
                    r"(?:fiscal (?:year )?)?(\d{4})", m.group(0), re.I)
    if lab and cur is not None:
        q = int(lab.group(1) or lab.group(3) or ORD[lab.group(5).lower()])
        yy = lab.group(2) or lab.group(4) or lab.group(6)
        year = int(yy) + (2000 if len(yy) == 2 else 0)
        if (year, f"Q{q}") == (cur[0], cur[1]):
            return False
    return True


IMPACT_BEFORE = re.compile(r"(?:impact|benefit|charge|headwind|tailwind|effect) of\s*(?:approximately\s+|about\s+)?$",
                           re.I)


def tag_numbers(s: str, nums: list[Num], forward: bool, cur: Optional[tuple[int, str]] = None) -> None:
    """Fill role, metric, qualifier, growth, comparison, basis, approx, fx flags in place. `cur` is the
    document's (fiscal_year, fiscal_period), used to tell its own quarter label from a prior one."""
    kw_hits = []
    for metric, rx in _METRIC_RX:
        for m in rx.finditer(s):
            if any(not (m.end() <= a or m.start() >= b) for a, b, _ in kw_hits):
                continue
            kw_hits.append((m.start(), m.end(), metric))
    kw_hits.sort()
    resp = re.search(r"\bGAAP and non-GAAP\b(?:[^.;]|\.(?=\d))*?\brespectively\b", s, re.I)
    both = re.search(r"\bGAAP and non-GAAP\b(?:[^.;]|\.(?=\d))*?\bboth\b", s, re.I)
    prev_metric: Optional[tuple[str, Optional[str]]] = None
    for i, n in enumerate(nums):
        prev_end = nums[i - 1].end if i else 0
        next_start = nums[i + 1].start if i + 1 < len(nums) else len(s)
        before, after = s[prev_end:n.start], s[n.end:next_start]
        if forward:
            n.role = "forward"
        ap = APPROX_BEFORE.search(s[max(0, n.start - 25):n.start])
        if ap:
            n.approx = ap.group(0).strip().lower()
        if n.kind in ("pct", "pp", "bps"):
            n.growth = bool(GROWTH_BEFORE.search(s[max(0, n.start - 40):n.start]) or n.text[:1] in "+−-"
                            or GROWTH_AFTER.search(after))
            if n.kind == "pct" and n.growth and DOWN_BEFORE.search(s[max(0, n.start - 30):n.start]):
                n.sign = -1
        # A pct right after a growth figure ("increased 24%, or 23% in constant currency", "grew 12% year over
        # year (11% on a F/X neutral basis)") is the same growth restated.
        if n.kind == "pct" and not n.growth and i and nums[i - 1].growth and \
                re.search(r"(?:,\s*or|\()\s*(?:up\s+|down\s+)?$", before, re.I):
            n.growth = True
            if n.sign == 1:
                n.sign = nums[i - 1].sign
        if FX_AFTER.search(after) or re.search(r"(?:constant currency|F/X neutral|FX-neutral)\s*$", before[-40:]):
            n.fx_adjusted = True
        if n.role != "forward" and not n.growth and (PRIOR_BEFORE.search(before[-20:]) or _prior_after(after, cur)):
            n.role = "prior"
        # metric: a keyword spanning the number, else the nearest keyword to its left within its own segment,
        # else one to its right before the next number ("$28 billion in operating cash flow"), else inherited
        # from the nearest earlier number of a compatible kind ("Net sales increased 20% to $200.6 billion",
        # "compared with $18.2 billion, or $1.68 per diluted share").
        hit = None
        for ks, ke, metric in kw_hits:
            if ks < n.start < ke or ks < n.end <= ke:
                hit = (ks, ke, metric)
        if hit is None:
            for ks, ke, metric in kw_hits:
                if ke <= n.start and ks >= prev_end:
                    hit = (ks, ke, metric)
        if hit is None and not n.growth:
            for ks, ke, metric in kw_hits:
                if n.end <= ks < next_start and ks - n.end <= 40:
                    hit = (ks, ke, metric)
                    break
        if IMPACT_BEFORE.search(s[max(0, n.start - 45):n.start]):
            n.metric, n.qualifier = "impact", None
            continue
        if hit is not None:
            metric = hit[2]
            qual = _qualifier(s, hit[0], hit[1]) if metric in QUALIFIABLE else None
            if metric == "revenue" and qual:
                metric = "segment_revenue"
            n.metric, n.qualifier = metric, qual
        else:
            src = _inherit_from(nums, i)
            if src is not None:
                n.metric, n.qualifier = src.metric, src.qualifier
            elif n.growth:
                for ks, ke, metric in kw_hits:  # "increased 24% ... revenues": keyword further right
                    if ks >= n.end:
                        qual = _qualifier(s, ks, ke) if metric in QUALIFIABLE else None
                        n.metric, n.qualifier = ("segment_revenue" if metric == "revenue" and qual else metric), qual
                        break
        if n.metric and n.metric != "other":
            prev_metric = (n.metric, n.qualifier)
        if n.growth:
            window = after[:60]
            if _YOY_ANY.search(window):
                n.comparison = "YoY"
            elif _QOQ_ANY.search(window):
                n.comparison = "QoQ"
        # basis
        ctx = s[max(0, n.start - 80):n.start]
        if resp and resp.start() < n.start < resp.end():
            idx = [m for m in nums if resp.start() < m.start < resp.end()]
            n.basis = "GAAP" if idx and idx[0] is n else "non-GAAP"
        elif both and both.start() < n.start <= both.end() + 12:
            n.basis = "both"
        elif re.search(r"non-GAAP", ctx, re.I) or re.search(r"^[^.;]{0,30}?(?:excluding significant items|"
                                                             r"on a non-GAAP basis)", after, re.I):
            n.basis = "non-GAAP"
        elif re.search(r"(?<!non-)\bGAAP\b", ctx) or re.search(r"^[^.;]{0,25}?on a GAAP basis", after):
            n.basis = "GAAP"
        elif re.search(r"\b(?:adjusted|managed)\b", ctx[-40:], re.I):
            n.basis = "non-GAAP"


# ============================================================ XBRL series and screening

CONCEPTS: dict[str, tuple[list[str], str, str]] = {
    # metric: (us-gaap concepts, unit, how to merge concepts per period: "max" or "first")
    "revenue": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenuesNetOfInterestExpense",
                 "SalesRevenueNet"], "USD", "max"),
    "net_income": (["NetIncomeLoss"], "USD", "first"),
    "eps_diluted": (["EarningsPerShareDiluted"], "USD/shares", "first"),
    "operating_income": (["OperatingIncomeLoss"], "USD", "first"),
    "gross_profit": (["GrossProfit"], "USD", "first"),
    "cost_of_revenue": (["CostOfRevenue", "CostOfGoodsAndServicesSold"], "USD", "first"),
    "net_interest_income": (["InterestIncomeExpenseNet"], "USD", "first"),
    "noninterest_revenue": (["NoninterestIncome"], "USD", "first"),
    "costs_and_expenses": (["CostsAndExpenses"], "USD", "first"),
    "noninterest_expense": (["NoninterestExpense"], "USD", "first"),
    "opex": (["OperatingExpenses"], "USD", "first"),
    "operating_cash_flow": (["NetCashProvidedByUsedInOperatingActivities"], "USD", "first"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment"], "USD", "first"),
    "repurchases": (["PaymentsForRepurchaseOfCommonStock"], "USD", "first"),
    "dividends_paid": (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"], "USD", "first"),
}
NON_ADDITIVE = {"eps_diluted"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def xbrl_series(facts: dict) -> dict[str, dict[str, float]]:
    """metric -> {quarter end (ISO): value} from companyfacts, 10-Q/10-K only, latest filed value per period.
    Quarters missing as 3-month facts are derived from year-to-date differences (not for EPS)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out: dict[str, dict[str, float]] = {}
    for metric, (concepts, unit, merge) in CONCEPTS.items():
        merged: dict[str, float] = {}
        for concept in concepts:
            rows = gaap.get(concept, {}).get("units", {}).get(unit, [])
            dur: dict[tuple[str, str], tuple[str, float]] = {}
            for r in rows:
                if r.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A") or "start" not in r:
                    continue
                key = (r["start"], r["end"])
                if key not in dur or r.get("filed", "") > dur[key][0]:
                    dur[key] = (r.get("filed", ""), float(r["val"]))
            q: dict[str, float] = {}
            for (s, e), (_, v) in dur.items():
                if 80 <= (_d(e) - _d(s)).days <= 100:
                    q[e] = v
            if metric not in NON_ADDITIVE:
                for (s, e), (_, v) in dur.items():
                    if (_d(e) - _d(s)).days <= 100 or e in q:
                        continue
                    for (s2, e2), (_, v2) in dur.items():
                        if s2 == s and 80 <= (_d(e) - _d(e2)).days <= 100:
                            q[e] = v - v2
                            break
            for e, v in q.items():
                if _d(e) < SERIES_FROM:
                    continue
                if e not in merged or (merge == "max" and v > merged[e]):
                    merged[e] = v
        if merged:
            out[metric] = dict(sorted(merged.items()))
    for e, rev in out.get("revenue", {}).items():
        gp = out.get("gross_profit", {}).get(e)
        if gp is None and e in out.get("cost_of_revenue", {}):
            gp = rev - out["cost_of_revenue"][e]
        if gp is not None and rev:
            out.setdefault("gross_margin", {})[e] = 100.0 * gp / rev
        if e in out.get("operating_income", {}) and rev:
            out.setdefault("operating_margin", {})[e] = 100.0 * out["operating_income"][e] / rev
    for e, rep in out.get("repurchases", {}).items():
        if e in out.get("dividends_paid", {}):
            out.setdefault("capital_returned", {})[e] = rep + out["dividends_paid"][e]
    return out


def series_at(series: dict[str, float], end: date, *, days: int = 0, tol: int = 12) -> Optional[float]:
    """Value of the quarter ending about `end + days`, or None."""
    want = end + timedelta(days=days)
    best = None
    for e, v in series.items():
        gap = abs((_d(e) - want).days)
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, v)
    return None if best is None else best[1]


def growth_at(series: dict[str, float], end: date, comparison: str) -> Optional[float]:
    cur = series_at(series, end)
    base = series_at(series, end, days=-364 if comparison == "YoY" else -91)
    if cur is None or base is None or base == 0:
        return None
    return 100.0 * (cur - base) / abs(base)


SCREEN_METRICS = {"revenue", "net_income", "eps_diluted", "operating_income", "gross_margin", "operating_margin",
                  "net_interest_income", "costs_and_expenses", "noninterest_expense", "opex", "operating_cash_flow",
                  "capital_returned", "noninterest_revenue"}


def expected_for(n: Num, series: dict[str, dict[str, float]], end: date, *, growth_comparison: Optional[str] = None,
                 shift_days: int = 0) -> Optional[float]:
    """The XBRL value a number should equal, in the number's own unit (USD, %, or percentage points)."""
    if n.metric not in SCREEN_METRICS or n.qualifier or n.basis == "non-GAAP" or n.fx_adjusted or n.is_range:
        return None
    s = series.get(n.metric)
    if not s:
        return None
    at = end + timedelta(days=shift_days)
    if n.role == "prior":
        at = at - timedelta(days=364)
    if n.kind == "money":
        return series_at(s, at)
    if n.kind == "pct" and n.growth:
        return growth_at(s, at, growth_comparison or n.comparison or "YoY")
    if n.kind == "pct" and n.metric in ("gross_margin", "operating_margin"):
        return series_at(s, at)
    if n.kind == "pp" and n.growth and n.metric in ("gross_margin", "operating_margin"):
        cur, base = series_at(s, at), series_at(s, at, days=-364)
        return None if cur is None or base is None else cur - base
    return None


def within_tolerance(n: Num, claimed: float, actual: float) -> bool:
    """claimed and actual in the number's unit (USD scaled, or %). Tolerance follows written precision."""
    step = 10 ** (-n.decimals)
    if n.kind == "money":
        unit = SCALE_FACTOR[n.scale]
        if n.approx in ("over", "more than", "in excess of", "just over"):
            return actual >= claimed and actual <= claimed * 1.10
        if n.approx in ("nearly", "almost", "just under"):
            return claimed * 0.93 <= actual <= claimed
        if n.approx:
            return abs(actual - claimed) <= max(0.5 * step * unit, 0.02 * abs(claimed))
        return abs(actual - claimed) <= 0.5 * step * unit * 1.0001 + 1e-9
    slack = 0.15 if n.growth or n.kind == "pp" else 0.05
    if n.approx:
        slack += 1.0
    return abs(actual - claimed) <= 0.5 * step + slack


def screen_number(n: Num, series: dict, end: date) -> None:
    exp = expected_for(n, series, end)
    if exp is None:
        n.screen, n.screen_detail = "na", None
        return
    claimed = n.scaled() if n.kind == "money" else n.sign * n.value
    ok = within_tolerance(n, claimed, exp)
    n.screen = "ok" if ok else "mismatch"
    n.screen_detail = f"xbrl={exp:.6g}"


def self_describing(n: Num) -> bool:
    """A number whose meaning does not depend on a section heading: a named segment or sub-metric, an explicit
    non-GAAP or currency-neutral basis, a prior-period value with its period, or a capital-return statement."""
    return bool(n.qualifier or n.basis == "non-GAAP" or n.fx_adjusted or n.role == "prior"
                or n.metric in ("capital_returned", "free_cash_flow", "tax_rate", "impact"))


TARGET_METRICS = {"revenue", "segment_revenue", "net_income", "eps_diluted", "gross_margin", "operating_income",
                  "costs_and_expenses", "noninterest_expense", "opex", "net_interest_income", "operating_cash_flow",
                  "free_cash_flow", "capital_returned", "noninterest_revenue"}


def targetable(n: dict) -> bool:
    """A number a mutator may change: a current-period claim that is verifiable in principle."""
    return (n["role"] == "current" and not n["approx"] and not n["fx_adjusted"] and not n["is_range"]
            and n["kind"] in ("money", "pct") and n["metric"] in TARGET_METRICS
            and (n["kind"] == "money" or n["growth"] or n["metric"] == "gross_margin")
            and (n["screen"] == "ok" or n["qualifier"] or n["basis"] == "non-GAAP"
                 or n["metric"] in ("capital_returned", "free_cash_flow")))


# ============================================================ stage 1: sources

def edgar_client(max_requests: int):
    """spikes/a_edgar.py's client (2 req/s, shared cache) with a hard cap on live requests."""
    sys.path.insert(0, str(ROOT / "spikes"))
    import a_edgar  # noqa: E402
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"')

    def is_cached(url: str) -> bool:
        key = hashlib.sha256(url.encode()).hexdigest()
        return (a_edgar.CACHE / f"{key}.bin").exists() and (a_edgar.CACHE / f"{key}.json").exists()

    class BudgetedEdgar(a_edgar.Edgar):
        def get(self, url: str) -> bytes:
            if not is_cached(url):
                if not ua or "@" not in ua:
                    raise SystemExit(f"not cached and SEC_USER_AGENT is not set in .env: {url}")
                if self.live_requests >= max_requests:
                    raise SystemExit(f"live SEC request cap ({max_requests}) reached before {url}")
            return super().get(url)

    return BudgetedEdgar(ua or "Receipts eval build (cache only)"), a_edgar


def fiscal_label(facts: dict, accession: str) -> tuple[Optional[int], Optional[str]]:
    for ns in facts.get("facts", {}).values():
        for node in ns.values():
            for rows in node.get("units", {}).values():
                for r in rows:
                    if r.get("accn") == accession and r.get("fy") and r.get("fp"):
                        return int(r["fy"]), r["fp"]
    return None, None


def discover_documents(ed, a_edgar, ticker: str) -> tuple[int, str, dict, list[dict]]:
    cik, title = a_edgar.resolve_cik(ed, ticker)
    facts = ed.json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    filings = a_edgar.load_filings(ed, cik, FILINGS_CUTOFF)
    tenqs = sorted((f for f in filings if f["form"] == "10-Q"), key=lambda f: f["reportDate"],
                   reverse=True)[:QUARTERS_PER_COMPANY]
    docs = []
    for q in tenqs:
        eightks = [f for f in filings if f["form"] == "8-K" and "2.02" in (f["items"] or "")
                   and q["reportDate"] < f["filingDate"] <= q["filingDate"]]
        if not eightks:
            print(f"  {ticker} {q['reportDate']}: no Item 2.02 8-K before the 10-Q; skipped")
            continue
        k = min(eightks, key=lambda f: f["acceptanceDateTime"])
        url = a_edgar.ex99_url(ed, cik, k["accessionNumber"])
        if not url:
            print(f"  {ticker} {q['reportDate']}: no EX-99.1 in {k['accessionNumber']}; skipped")
            continue
        fy, fp = fiscal_label(facts, q["accessionNumber"])
        docs.append({
            "ticker": ticker, "cik": cik, "company_title": title, "fy": fy, "fp": fp,
            "period_end": q["reportDate"], "accession": k["accessionNumber"],
            "accepted_at": iso_z(parse_ts(k["acceptanceDateTime"])), "filing_date": k["filingDate"],
            "url": url, "form": "8-K", "exhibit": "EX-99.1",
            "tenq_accession": q["accessionNumber"], "tenq_accepted_at": iso_z(parse_ts(q["acceptanceDateTime"])),
        })
    return cik, title, facts, docs


def doc_id_of(d: dict) -> str:
    return f"{d['ticker']}-FY{d['fy']}{d['fp']}"


def quarter_word(fp: str) -> str:
    return {"Q1": "first", "Q2": "second", "Q3": "third", "Q4": "fourth"}.get(fp, fp)


def context_line(ticker: str, fy: int, fp: str, period_end: str) -> str:
    """The document-context line check() receives, in the style receipts.extract expects ("NVIDIA Q2 FY2027"):
    company plus fiscal period, with the period-end date so receipts.periods can resolve it unambiguously."""
    c = COMPANIES[ticker]
    end = _d(period_end)
    return (f"{c.name} ({ticker}), {quarter_word(fp)} quarter of fiscal {fy}, "
            f"the quarter ended {end.strftime('%B')} {end.day}, {end.year}")


def analyze_sentence(text: str, series: dict, end: date, cur: Optional[tuple[int, str]] = None) -> dict:
    nums = find_numbers(text)
    forward = is_forward_looking(text)
    tag_numbers(text, nums, forward, cur)
    for n in nums:
        if n.role != "forward":
            screen_number(n, series, end)
    fin = [n for n in nums if n.kind in ("money", "pct", "pp", "bps")]
    reasons = []
    if not 1 <= len(fin) <= 3:
        reasons.append(f"{len(fin)} financial numbers")
    if other_numeric(text, nums):
        reasons.append("non-financial numbers")
    if NON_QUARTER_RE.search(text):
        reasons.append("not about the quarter")
    if COUNTERFACTUAL_RE.search(text):
        reasons.append("counterfactual")
    if not 25 <= len(text) <= 420:
        reasons.append("length")
    if text.isupper():
        reasons.append("all caps")
    if re.match(r"\(\d{1,2}\)|[a-z]\.(?=[A-Z])", text):
        reasons.append("footnote")
    for n in fin:
        if n.metric not in VOCAB_METRICS:
            reasons.append(f"metric {n.metric} for {n.text}")
        elif n.screen == "mismatch":
            reasons.append(f"XBRL mismatch for {n.text} ({n.screen_detail})")
        elif n.role != "forward" and n.screen != "ok" and not self_describing(n):
            reasons.append(f"context-dependent {n.text}")
    kind = "forward_looking" if forward else "verifiable"
    if forward:
        reasons = [r for r in reasons if not r.startswith(("context-dependent", "XBRL", "metric"))]
        cue = FL_CONTROL_CUE.search(text)
        if any(n.metric is None or n.metric == "other" for n in fin):
            reasons.append("untyped forward-looking numbers")
        if not cue or not fin or cue.start() > fin[0].start:
            reasons.append("no guidance verb before the numbers")
        if PAST_ACTUAL_RE.search(text) or re.search(r"\bcompare[sd]? (?:with|to)\b|\bvs\.?|\bversus\b", text):
            reasons.append("mixes guidance with actuals")
    if not forward and any(n.role == "forward" for n in fin):
        reasons.append("forward numbers")
    return {"numbers": nums, "kind": kind, "reasons": reasons}


def score_sentence(nums: list[dict]) -> float:
    s = 0.0
    for n in nums:
        if targetable(n):
            s += 2.0
        if n["screen"] == "ok":
            s += 1.5
        if n["metric"] in ("revenue", "net_income", "eps_diluted", "operating_income", "gross_margin"):
            s += 1.0
        if n["fx_adjusted"] or n["approx"]:
            s -= 0.75
    return s


def build_sources(max_requests: int) -> list[dict]:
    ed, a_edgar = edgar_client(max_requests)
    rows: list[dict] = []
    all_docs: list[dict] = []
    for ticker in TICKERS:
        cik, title, facts, docs = discover_documents(ed, a_edgar, ticker)
        series = xbrl_series(facts)
        c = COMPANIES[ticker]
        rows.append({"kind": "company", "ticker": ticker, "cik": cik, "name": c.name, "sec_title": title,
                     "exchange": c.exchange, "sector": c.sector,
                     "series": {m: {e: round(v, 6) for e, v in s.items()} for m, s in series.items()
                                if m in SCREEN_METRICS | {"eps_diluted", "gross_profit"}}})
        for d in docs:
            html = ed.get(d["url"])
            blocks = html_blocks(html)
            d["doc_id"] = doc_id_of(d)
            d["series"] = series
            d["blocks"] = blocks
            all_docs.append(d)
        print(f"{ticker}: {len(docs)} press releases ({', '.join(doc_id_of(d) for d in docs)})")
    print(f"SEC live requests this build: {ed.live_requests} (cap {max_requests}); cache hits: {ed.cache_hits}")

    sentence_rows: list[dict] = []
    for d in all_docs:
        end = _d(d["period_end"])
        doc_text = "\n".join(d["blocks"])
        offsets, pos = [], 0
        for b in d["blocks"]:
            offsets.append(pos)
            pos += len(b) + 1
        doc_numbers = sorted({number_key(n) for b in d["blocks"] for n in find_numbers(b)})
        rows.append({
            "kind": "document", "doc_id": d["doc_id"], "ticker": d["ticker"], "cik": d["cik"], "fy": d["fy"],
            "fp": d["fp"], "period_end": d["period_end"], "accession": d["accession"],
            "accepted_at": d["accepted_at"], "filing_date": d["filing_date"], "url": d["url"], "form": d["form"],
            "exhibit": d["exhibit"], "tenq_accession": d["tenq_accession"],
            "tenq_accepted_at": d["tenq_accepted_at"], "text_sha256": sha256_text(doc_text),
            "n_blocks": len(d["blocks"]), "numbers": doc_numbers,
            "context": context_line(d["ticker"], d["fy"], d["fp"], d["period_end"]),
        })
        seen: set[str] = set()
        for bi, block in enumerate(d["blocks"]):
            for off, sent in split_sentences(block):
                if sent in seen or not re.search(r"\d", sent):
                    continue
                seen.add(sent)
                a = analyze_sentence(sent, d["series"], end, (d["fy"], d["fp"]))
                excluded = next((why for pre, why in MANUAL_EXCLUDE.items() if sent.startswith(pre)), None)
                if excluded:
                    a["reasons"].append(f"manual: {excluded}")
                start = offsets[bi] + off
                assert doc_text[start:start + len(sent)] == sent
                sentence_rows.append({
                    "kind": "sentence", "doc_id": d["doc_id"], "ticker": d["ticker"], "text": sent,
                    "char_start": start, "char_end": start + len(sent), "block_index": bi,
                    "accession": d["accession"], "accepted_at": d["accepted_at"], "url": d["url"],
                    "pool": a["kind"], "eligible": not a["reasons"], "reasons": a["reasons"],
                    "numbers": [asdict(n) for n in a["numbers"]],
                })
    ANALYZED[:] = sentence_rows
    chosen = select_pool(sentence_rows)
    for i, r in enumerate(sorted(chosen, key=lambda r: (TICKERS.index(r["ticker"]), r["doc_id"], r["char_start"]))):
        r.pop("eligible")
        r.pop("reasons")
        r["sentence_id"] = f"{r['doc_id']}-s{r['char_start']:06d}"
        rows.append(r)
    return rows


def _cached_html(url: str) -> bytes:
    path = EDGAR_CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
    if not path.exists():
        raise SystemExit(f"not in data/cache/edgar (run the full build first): {url}")
    return path.read_bytes()


def _basis_spans(text: str) -> Optional[tuple[str, list[tuple[int, int, str]]]]:
    """(pattern, [(start, end, basis)]) for a sentence that states both bases in clauses: the basis of every
    character range, or None when the sentence is neither pattern."""
    m = BASIS_PAIR_RE.search(text)
    if m:
        return "gaap_and_non_gaap", [(m.end(1), m.start(2), "GAAP"), (m.end(2), len(text), "non-GAAP")]
    m = BASIS_ALL_RE.match(text)
    if m:
        return "non_gaap_all", [(m.end(1), len(text), "non-GAAP")]
    return None


def analyze_basis_sentence(text: str, series: dict, end: date, cur: Optional[tuple[int, str]]) -> Optional[dict]:
    """Stage 1b screen for the basis appendix: the v0 rules except the number count (4-5 here), with each number's
    basis read from its clause ("on a GAAP basis was A ..., and on a non-GAAP basis was B ...") instead of the
    clause-local tagger, then re-screened against XBRL. None when the sentence doesn't qualify."""
    spans = _basis_spans(text)
    if spans is None:
        return None
    pattern, ranges = spans
    a = analyze_sentence(text, series, end, cur)
    nums = [n for n in a["numbers"] if n.kind in ("money", "pct", "pp", "bps")]
    if not BASIS_EXTRA_NUMBERS[0] <= len(nums) <= BASIS_EXTRA_NUMBERS[1] or a["kind"] != "verifiable":
        return None
    for n in nums:
        basis = next((b for s, e, b in ranges if s <= n.start < e), None)
        if basis is None:
            return None  # a figure before the first basis phrase: its basis is unknown
        n.basis = basis
        screen_number(n, series, end)
    reasons = [r for r in a["reasons"] if not r.endswith("financial numbers") and not r.startswith(("XBRL", "context"))]
    for n in nums:
        if n.metric not in VOCAB_METRICS:
            reasons.append(f"metric {n.metric}")
        elif n.screen == "mismatch":
            reasons.append(f"XBRL mismatch for {n.text} ({n.screen_detail})")
        elif n.screen != "ok" and not self_describing(n):
            reasons.append(f"context-dependent {n.text}")
    if reasons:
        return None
    return {"numbers": a["numbers"], "basis_pattern": pattern}


def build_basis_extra_rows(source_rows: list[dict]) -> list[dict]:
    """Stage 1b, offline: basis-appendix sentence rows from the v0 documents' cached press releases, with the same
    sentence splitting and offsets as stage 1."""
    company_rows = {r["ticker"]: r for r in source_rows if r["kind"] == "company"}
    out: list[dict] = []
    for d in (r for r in source_rows if r["kind"] == "document"):
        blocks = html_blocks(_cached_html(d["url"]))
        doc_text = "\n".join(blocks)
        if sha256_text(doc_text) != d["text_sha256"]:
            raise SystemExit(f"{d['doc_id']}: cached press release differs from the one v0 was built from")
        pos, seen = 0, set()
        for bi, block in enumerate(blocks):
            for off, sent in split_sentences(block):
                if sent in seen or not re.search(r"\d", sent):
                    continue
                seen.add(sent)
                a = analyze_basis_sentence(sent, company_rows[d["ticker"]]["series"], _d(d["period_end"]),
                                           (d["fy"], d["fp"]))
                if a is None:
                    continue
                start = pos + off
                assert doc_text[start:start + len(sent)] == sent
                out.append({
                    "kind": "sentence", "doc_id": d["doc_id"], "ticker": d["ticker"], "text": sent,
                    "char_start": start, "char_end": start + len(sent), "block_index": bi,
                    "accession": d["accession"], "accepted_at": d["accepted_at"], "url": d["url"],
                    "pool": BASIS_EXTRA_POOL, "basis_pattern": a["basis_pattern"],
                    "numbers": [asdict(n) for n in a["numbers"]],
                    "sentence_id": f"{d['doc_id']}-s{start:06d}",
                })
            pos += len(block) + 1
    return sorted(out, key=lambda r: (TICKERS.index(r["ticker"]), r["doc_id"], r["char_start"]))


CANDIDATE_COUNTS: Counter = Counter()  # originals each mutator could apply to, from the last build_items()
ANALYZED: list[dict] = []  # every analyzed sentence of the last stage-1 run (for --dump-rejects)


def number_key(n: Num) -> str:
    return f"{n.kind}:{n.value:g}:{n.scale}"


def select_pool(sentences: list[dict]) -> list[dict]:
    """Up to POOL_PER_DOC verifiable sentences per press release (best score first) plus FL_CONTROLS
    forward-looking controls spread across companies."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for r in sentences:
        if r["eligible"] and r["pool"] == "verifiable":
            by_doc[r["doc_id"]].append(r)
    chosen = []
    for doc_id, rs in by_doc.items():
        rs.sort(key=lambda r: (-score_sentence(r["numbers"]), stable_frac("pool", r["text"])))
        chosen.extend(rs[:POOL_PER_DOC])
    fl = [r for r in sentences if r["eligible"] and r["pool"] == "forward_looking"]
    fl.sort(key=lambda r: (stable_frac("fl", r["text"])))
    per_ticker: Counter = Counter()
    picked: list[dict] = []
    for round_ in range(3):
        for r in fl:
            if len(picked) >= FL_CONTROLS:
                break
            if r in picked or per_ticker[r["ticker"]] != round_:
                continue
            picked.append(r)
            per_ticker[r["ticker"]] += 1
    return chosen + picked


# ============================================================ stage 2: mutators

@dataclass
class Mutation:
    mutator: str
    text: str
    context: str
    as_of: Optional[str]
    target_value_text: str
    target_span: tuple[int, int]
    original_value_text: str
    expected_value: Optional[float]      # correct value in the claim's own unit and scale, when known
    affected_value_texts: list[str]      # every claim the edit makes wrong (equal to [target] when only one)
    edit: Optional[dict]                 # {"start","end","before","after"} on the original text; None if text unchanged
    screen: str                          # how we know the mutant is false: "construction" | "xbrl" | "assumed"
    detail: dict = field(default_factory=dict)


def _replace(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


def _fmt_digits(value: float, decimals: int, thousands: bool) -> str:
    return f"{value:,.{decimals}f}" if thousands else f"{value:.{decimals}f}"


def _targets(sentence: dict) -> list[int]:
    return [i for i, n in enumerate(sentence["numbers"]) if targetable(n)]


def _pick(rng: random.Random, idxs: list[int], nums: list[dict], prefer_money: bool = True) -> int:
    if prefer_money:
        money = [i for i in idxs if nums[i]["kind"] == "money"]
        if money and rng.random() < 0.7:
            return rng.choice(money)
    return rng.choice(idxs)


def mutate_value(sentence: dict, doc: dict, company_rows: dict) -> Optional[Mutation]:
    nums = sentence["numbers"]
    idxs = _targets(sentence)
    if not idxs:
        return None
    rng = rng_for(sentence["sentence_id"], "value")
    i = _pick(rng, idxs, nums)
    n = nums[i]
    text = sentence["text"]
    step = 10 ** (-n["decimals"])
    doc_numbers = set(doc["numbers"])
    rel = rng.uniform(0.04, 0.12)
    direction = rng.choice((-1, 1))
    for attempt in range(8):
        delta = max(abs(n["value"]) * rel, 2 * step)
        new = round(n["value"] + direction * delta, n["decimals"])
        if new <= 0:
            direction, attempt = 1, attempt
            continue
        change = abs(new - n["value"])
        key = f"{n['kind']}:{new:g}:{n['scale']}"
        if change + 1e-12 >= 2 * step and change + 1e-12 >= 0.03 * abs(n["value"]) and key not in doc_numbers \
                and all(abs(m["value"] - new) > 1e-9 for m in nums):
            break
        direction = -direction if attempt % 2 == 0 else direction
        rel *= 1.35
    else:
        return None
    digits = _fmt_digits(new, n["decimals"], n["thousands_sep"])
    a, b = n["dstart"], n["dend"]
    new_text = _replace(text, a, b, digits)
    mention_new = text[n["start"]:a] + digits + text[b:n["end"]]
    t0 = n["start"]
    return Mutation(
        mutator="value", text=new_text, context=doc["context"], as_of=None, target_value_text=mention_new,
        target_span=(t0, t0 + len(mention_new)), original_value_text=n["text"],
        expected_value=n["sign"] * n["value"], affected_value_texts=[mention_new],
        edit={"start": a, "end": b, "before": text[a:b], "after": digits}, screen="construction",
        detail={"relative_change": round((new - n["value"]) / n["value"], 4), "metric": n["metric"]})


SCALE_SWAP = {"billion": "million", "million": "billion", "thousand": "million", "trillion": "billion",
              "B": "M", "M": "B", "bn": "mn", "mn": "bn", "K": "M"}


def mutate_scale(sentence: dict, doc: dict, company_rows: dict) -> Optional[Mutation]:
    nums = sentence["numbers"]
    idxs = [i for i in _targets(sentence) if nums[i]["kind"] == "money" and nums[i]["scale_start"] >= 0]
    if not idxs:
        return None
    rng = rng_for(sentence["sentence_id"], "scale")
    i = rng.choice(idxs)
    n = nums[i]
    text = sentence["text"]
    a, b = n["scale_start"], n["scale_end"]
    word = text[a:b]
    new_word = SCALE_SWAP.get(word) or SCALE_SWAP.get(word.lower())
    if not new_word:
        return None
    new_text = _replace(text, a, b, new_word)
    mention_new = text[n["start"]:a] + new_word + text[b:n["end"]]
    return Mutation(
        mutator="scale", text=new_text, context=doc["context"], as_of=None, target_value_text=mention_new,
        target_span=(n["start"], n["start"] + len(mention_new)), original_value_text=n["text"],
        expected_value=n["sign"] * n["value"], affected_value_texts=[mention_new],
        edit={"start": a, "end": b, "before": word, "after": new_word}, screen="construction",
        detail={"from_scale": word, "to_scale": new_word, "metric": n["metric"]})


QUARTER_WORD_RE = re.compile(r"\b(?P<w>[Ff]irst|[Ss]econd|[Tt]hird|[Ff]ourth)(?P<sep>[- ])(?P<q>[Qq]uarter)\b")
QLABEL_SWAP_RE = re.compile(r"\bQ(?P<n>[1-4])\b")
BANK_Q_RE = re.compile(r"\b(?P<n>[1-4])Q(?P<yy>\d{2})\b")
MONTH_Q_RE = re.compile(r"\b(?P<m>March|June|September|December) quarter\b")
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4}
ORD_WORD = {v: k for k, v in ORD.items()}
APPLE_MONTH = {"December": 1, "March": 2, "June": 3, "September": 4}
APPLE_MONTH_WORD = {v: k for k, v in APPLE_MONTH.items()}


def _quarter_refs(text: str) -> list[tuple[int, int, int, str]]:
    """(start, end, quarter number, kind) for every reference to a quarter by name."""
    refs = []
    for m in QUARTER_WORD_RE.finditer(text):
        refs.append((m.start(), m.end(), ORD[m.group("w").lower()], "word"))
    for m in QLABEL_SWAP_RE.finditer(text):
        refs.append((m.start(), m.end(), int(m.group("n")), "q"))
    for m in BANK_Q_RE.finditer(text):
        refs.append((m.start(), m.end(), int(m.group("n")), "bank"))
    for m in MONTH_Q_RE.finditer(text):
        refs.append((m.start(), m.end(), APPLE_MONTH[m.group("m")], "month"))
    return refs


def _swap_quarter(text: str, ref: tuple[int, int, int, str], new_q: int) -> str:
    a, b, q, kind = ref
    old = text[a:b]
    if kind == "word":
        m = QUARTER_WORD_RE.fullmatch(old)
        w = ORD_WORD[new_q]
        w = w.capitalize() if m.group("w")[0].isupper() else w
        return w + m.group("sep") + m.group("q")
    if kind == "q":
        return f"Q{new_q}"
    if kind == "bank":
        return f"{new_q}Q{old[2:]}"
    return f"{APPLE_MONTH_WORD[new_q]} quarter"


def mutate_period(sentence: dict, doc: dict, company_rows: dict) -> Optional[Mutation]:
    text = sentence["text"]
    nums = sentence["numbers"]
    series = company_rows[sentence["ticker"]]["series"]
    end = _d(doc["period_end"])
    rng = rng_for(sentence["sentence_id"], "period")
    has_date = bool(DATE_RE.search(text))
    options: list[Mutation] = []
    # (a) swap the comparison of exactly one growth claim: YoY <-> QoQ
    growth = [i for i, n in enumerate(nums) if n["growth"] and n["kind"] == "pct" and n["role"] == "current"]
    yoy = [(m, rep) for p, rep in YOY_PHRASES for m in re.finditer(p, text)]
    qoq = [(m, rep) for p, rep in QOQ_PHRASES for m in re.finditer(p, text)]
    if len(growth) == 1 and len(yoy) + len(qoq) == 1 and targetable(nums[growth[0]]):
        n = nums[growth[0]]
        (m, rep), was = (yoy[0], "YoY") if yoy else (qoq[0], "QoQ")
        if m.start() > n["start"]:
            other = "QoQ" if was == "YoY" else "YoY"
            alt = expected_for(_as_num(n), series, end, growth_comparison=other)
            screen = "xbrl" if alt is not None else "assumed"
            claimed = n["sign"] * n["value"]
            if alt is None or not within_tolerance(_as_num(n), claimed, alt):
                new_text = _replace(text, m.start(), m.end(), rep)
                options.append(Mutation(
                    mutator="period", text=new_text, context=doc["context"], as_of=None,
                    target_value_text=n["text"], target_span=(n["start"], n["end"]), original_value_text=n["text"],
                    expected_value=None if alt is None else round(alt, 2), affected_value_texts=[n["text"]],
                    edit={"start": m.start(), "end": m.end(), "before": m.group(0), "after": rep}, screen=screen,
                    detail={"kind": "comparison", "from": was, "to": other, "metric": n["metric"]}))
    # (b) rename the quarter of a one-number sentence: the number now claims another quarter
    refs = _quarter_refs(text)
    fin = [i for i, n in enumerate(nums) if n["kind"] in ("money", "pct", "pp", "bps")]
    cur_q = int(doc["fp"][1]) if doc["fp"] and doc["fp"].startswith("Q") else None
    if len(refs) == 1 and len(fin) == 1 and not has_date and cur_q and refs[0][2] == cur_q and targetable(nums[fin[0]]):
        n = nums[fin[0]]
        new_q = cur_q - 1 if cur_q > 1 else 2
        shift = -91 if new_q < cur_q else 91
        alt = expected_for(_as_num(n), series, end, shift_days=shift)
        claimed = n["scaled"] if n["kind"] == "money" else n["sign"] * n["value"]
        if alt is not None or n["kind"] == "money":
            if alt is None or not within_tolerance(_as_num(n), claimed, alt):
                new_word = _swap_quarter(text, refs[0], new_q)
                a, b = refs[0][0], refs[0][1]
                new_text = _replace(text, a, b, new_word)
                delta = len(new_word) - (b - a)
                ts = (n["start"] + (delta if n["start"] > a else 0), n["end"] + (delta if n["start"] > a else 0))
                options.append(Mutation(
                    mutator="period", text=new_text, context=doc["context"], as_of=None,
                    target_value_text=n["text"], target_span=ts, original_value_text=n["text"],
                    expected_value=None if alt is None else round(alt / (SCALE_FACTOR[n["scale"]]
                                                                         if n["kind"] == "money" else 1), 4),
                    affected_value_texts=[n["text"]],
                    edit={"start": a, "end": b, "before": text[a:b], "after": new_word},
                    screen="xbrl" if alt is not None else "assumed",
                    detail={"kind": "quarter", "from": f"Q{cur_q}", "to": f"Q{new_q}", "metric": n["metric"]}))
    if not options:
        return None
    return options[0] if len(options) == 1 else rng.choice(options)


def _as_num(n: dict) -> Num:
    keys = Num.__dataclass_fields__.keys()
    return Num(**{k: n[k] for k in keys})


def mutate_basis(sentence: dict, doc: dict, company_rows: dict, variant: int = 0) -> Optional[Mutation]:
    """Remove one basis word from 'GAAP and non-GAAP <metric> were A and B, respectively' so that exactly one
    of the two claims carries the wrong basis. variant 0 removes ' and non-GAAP' (B becomes a GAAP claim);
    variant 1 removes 'GAAP and ' (A becomes a non-GAAP claim)."""
    text = sentence["text"]
    m = re.search(r"\bGAAP and non-GAAP\b", text)
    if not m or not re.search(r"\brespectively\b", text[m.end():]):
        return None
    nums = [n for n in sentence["numbers"] if n["start"] > m.end() and n["kind"] in ("money", "pct")]
    if len(nums) != 2 or nums[0]["kind"] != nums[1]["kind"]:
        return None
    a_, b_ = nums
    step = 10 ** (-max(a_["decimals"], b_["decimals"]))
    if abs(a_["value"] - b_["value"]) < 2 * step - 1e-12:
        return None  # the two bases are too close to call the mutant clearly wrong
    if variant == 0:
        cut = (m.start() + len("GAAP"), m.end())         # "GAAP[ and non-GAAP]"
        target, expected = b_, a_
    else:
        cut = (m.start(), m.start() + len("GAAP and "))   # "[GAAP and ]non-GAAP"
        target, expected = a_, b_
    a, b, after = cut[0], cut[1], ""
    if cut[0] == 0:  # sentence-initial "GAAP and non-GAAP": the edit also capitalizes "non-GAAP"
        b, after = cut[1] + 1, text[cut[1]].upper()
    removed = text[a:b]
    new_text = _replace(text, a, b, after)
    shift = len(after) - (b - a)
    ts = (target["start"] + shift, target["end"] + shift)
    return Mutation(
        mutator="basis", text=new_text, context=doc["context"], as_of=None, target_value_text=target["text"],
        target_span=ts, original_value_text=target["text"], expected_value=expected["sign"] * expected["value"],
        affected_value_texts=[target["text"]],
        edit={"start": a, "end": b, "before": removed, "after": after}, screen="construction",
        detail={"stated_basis": "GAAP" if variant == 0 else "non-GAAP",
                "true_basis_of_number": "non-GAAP" if variant == 0 else "GAAP", "metric": target["metric"],
                "target_basis": "GAAP" if variant == 0 else "non-GAAP"})


def _clearly_apart(a: float, b: float, decimals: int) -> bool:
    return abs(a - b) >= 2 * 10 ** (-decimals) - 1e-12


def mutate_basis_extra(sentence: dict, doc: dict, company_rows: dict, variant: int = 0) -> Optional[Mutation]:
    """Basis mutator for 4-5-number sentences (the appendix): one basis word trades places with the other, so every
    figure in that clause now claims the other basis. The target is one figure whose other-basis value differs by
    at least twice its written precision; affected_value_texts lists every figure the swap made wrong.

    gaap_and_non_gaap ("on a GAAP basis was A and increased X%, and on a non-GAAP basis was B and increased Y%"):
      variant 0 turns "non-GAAP" into "GAAP": B (and Y) now claim GAAP; the true GAAP figure is A, by construction.
      variant 1 turns "GAAP" into "non-GAAP": A (and X) now claim non-GAAP; the true figure is B.
    non_gaap_all ("On a non-GAAP basis, gross margin was 54%, operating income was $2.2 billion, ..."), variant 0
      only: "non-GAAP" becomes "GAAP"; the GAAP values come from XBRL (screen "xbrl")."""
    text, nums = sentence["text"], [n for n in sentence["numbers"] if n["kind"] in ("money", "pct")]
    pattern = sentence.get("basis_pattern")
    if pattern == "gaap_and_non_gaap":
        m = BASIS_PAIR_RE.search(text)
        if not m:
            return None
        clause = {"GAAP": [n for n in nums if m.end(1) <= n["start"] < m.start(2)],
                  "non-GAAP": [n for n in nums if n["start"] >= m.end(2)]}
        levels = {b: [n for n in ns if n["kind"] == "money"] for b, ns in clause.items()}
        if any(len(v) != 1 for v in levels.values()):
            return None
        a_, b_ = levels["GAAP"][0], levels["non-GAAP"][0]
        if a_["scale"] != b_["scale"] or not _clearly_apart(a_["value"], b_["value"], max(a_["decimals"], b_["decimals"])):
            return None
        growth = {b: [n for n in ns if n["kind"] == "pct" and n["growth"] and not n["fx_adjusted"]]
                  for b, ns in clause.items()}
        if variant == 0:
            tok, target, expected, mine, other = (m.start(2), m.end(2)), b_, a_, "non-GAAP", "GAAP"
        elif variant == 1:
            tok, target, expected, mine, other = (m.start(1), m.end(1)), a_, b_, "GAAP", "non-GAAP"
        else:
            return None
        affected = [target["text"]]
        if len(growth[mine]) == 1 and len(growth[other]) == 1 and _clearly_apart(
                growth[mine][0]["value"], growth[other][0]["value"], growth[mine][0]["decimals"]):
            affected.append(growth[mine][0]["text"])  # its growth rate now claims the other basis too
        expected_value, screen = expected["sign"] * expected["value"], "construction"
    elif pattern == "non_gaap_all" and variant == 0:
        m = BASIS_ALL_RE.match(text)
        if not m:
            return None
        tok, mine, other = (m.start(1), m.end(1)), "non-GAAP", "GAAP"
        series = company_rows[sentence["ticker"]]["series"]
        end = _d(doc["period_end"])
        wrong: list[tuple[int, float]] = []
        for i, n in enumerate(nums):
            s = series.get(n["metric"] or "")
            gaap = series_at(s, end) if s else None
            if gaap is None:
                continue
            gaap_units = gaap / SCALE_FACTOR[n["scale"]] if n["kind"] == "money" else gaap
            if _clearly_apart(n["sign"] * n["value"], gaap_units, n["decimals"]):
                wrong.append((i, gaap_units))
        if not wrong:
            return None
        rng = rng_for(sentence["sentence_id"], "basis_extra", variant)
        i = _pick(rng, [w[0] for w in wrong], nums)
        target = nums[i]
        expected_value = round(dict(wrong)[i], 4)
        affected = [nums[j]["text"] for j, _ in wrong]
        screen = "xbrl"
    else:
        return None
    new_text = _replace(text, tok[0], tok[1], other)
    shift = len(other) - (tok[1] - tok[0])
    ts = (target["start"] + (shift if target["start"] > tok[0] else 0),
          target["end"] + (shift if target["start"] > tok[0] else 0))
    return Mutation(
        mutator="basis", text=new_text, context=doc["context"], as_of=None, target_value_text=target["text"],
        target_span=ts, original_value_text=target["text"], expected_value=expected_value,
        affected_value_texts=affected, edit={"start": tok[0], "end": tok[1], "before": mine, "after": other},
        screen=screen, detail={"stated_basis": other, "true_basis_of_number": mine, "metric": target["metric"],
                               "target_basis": other, "pattern": pattern})


def mutate_look_ahead(sentence: dict, doc: dict, company_rows: dict) -> Optional[Mutation]:
    nums = sentence["numbers"]
    idxs = _targets(sentence)
    if not idxs or "dividend" in sentence["text"].lower():
        return None
    rng = rng_for(sentence["sentence_id"], "look_ahead")
    i = _pick(rng, idxs, nums)
    n = nums[i]
    as_of = iso_z(parse_ts(doc["accepted_at"]) - timedelta(hours=1))
    current = [m["text"] for m in nums if m["role"] == "current" and m["kind"] in ("money", "pct", "pp", "bps")]
    return Mutation(
        mutator="look_ahead", text=sentence["text"], context=doc["context"], as_of=as_of,
        target_value_text=n["text"], target_span=(n["start"], n["end"]), original_value_text=n["text"],
        expected_value=None, affected_value_texts=current, edit=None, screen="construction",
        detail={"accepted_at": doc["accepted_at"], "metric": n["metric"]})


ENTITY_METRICS = {"revenue", "net_income", "eps_diluted", "operating_income", "capital_returned",
                  "net_interest_income", "gross_margin"}
TECH_ONLY = {"operating_income", "gross_margin"}


def _entity_mentions(text: str, company: Company) -> list[tuple[int, int]]:
    """Spans of company-name mentions (with an adjacent '(NASDAQ: TICK)'), skipping product or segment uses
    such as 'Microsoft Cloud' or 'Amazon Web Services'."""
    spans = []
    for alias in company.aliases:
        for m in re.finditer(r"(?<![\w&])" + re.escape(alias) + r"(?![\w&])", text):
            if any(not (m.end() <= a or m.start() >= b) for a, b in spans):
                continue
            nxt = text[m.end():m.end() + 30]
            if re.match(r"\s+(?!Inc\b|Corporation\b|Corp\b)[A-Z0-9]", nxt):
                continue
            end = m.end()
            tick = re.match(r"\s?\((?:NASDAQ|NYSE|Nasdaq):\s?[A-Z.]+\)", nxt)
            if tick:
                end += tick.end()
            spans.append((m.start(), end))
    return sorted(spans)


def _entity_replacement_text(old: str, new: Company) -> str:
    m = re.search(r"\((NASDAQ|NYSE|Nasdaq):(\s?)([A-Z.]+)\)", old)
    name = new.name
    if m:
        return f"{name} ({new.exchange}:{m.group(2)}{new.ticker})"
    return name


def mutate_entity(sentence: dict, doc: dict, company_rows: dict, docs_by_label: dict) -> Optional[Mutation]:
    text = sentence["text"]
    nums = sentence["numbers"]
    company = COMPANIES[sentence["ticker"]]
    mentions = _entity_mentions(text, company)
    fin = [n for n in nums if n["kind"] in ("money", "pct", "pp", "bps")]
    if len(mentions) > 1:
        return None
    for other in COMPANIES.values():  # the sentence must not already name another company
        if other.ticker != company.ticker and _entity_mentions(text, other):
            return None
    if not mentions and (DATE_RE.search(text) or any(n["qualifier"] for n in fin)):
        return None  # a context swap needs every number to be company-level and no explicit date
    cands = [i for i in _targets(sentence) if nums[i]["metric"] in ENTITY_METRICS and not nums[i]["qualifier"]
             and nums[i]["basis"] in (None, "GAAP")]
    if not cands:
        return None
    rng = rng_for(sentence["sentence_id"], "entity")
    i = _pick(rng, cands, nums)
    n = nums[i]
    label = (doc["fy"], doc["fp"])
    replacements = []
    for t in TICKERS:
        if t == company.ticker or (label not in docs_by_label.get(t, {})):
            continue
        other_doc = docs_by_label[t][label]
        if abs((_d(other_doc["period_end"]) - _d(doc["period_end"])).days) > 7 and (DATE_RE.search(text)):
            continue
        if n["metric"] in TECH_ONLY and COMPANIES[t].sector != "tech":
            continue
        if n["metric"] == "net_interest_income" and COMPANIES[t].sector != "bank":
            continue
        series = company_rows[t]["series"]
        alt = expected_for(_as_num(n), series, _d(other_doc["period_end"]))
        claimed = n["scaled"] if n["kind"] == "money" else n["sign"] * n["value"]
        if alt is None and n["kind"] != "money":
            continue  # a percentage could coincide with the other company's; only keep checked ones
        if alt is not None and within_tolerance(_as_num(n), claimed, alt):
            continue
        replacements.append((t, other_doc, alt))
    if not replacements:
        return None
    t, other_doc, alt = rng.choice(replacements)
    new_company = COMPANIES[t]
    if mentions:
        a, b = mentions[0]
        old = text[a:b]
        new_name = _entity_replacement_text(old, new_company)
        new_text = _replace(text, a, b, new_name)
        shift = len(new_name) - (b - a)
        ts = (n["start"] + (shift if n["start"] > a else 0), n["end"] + (shift if n["start"] > a else 0))
        edit = {"start": a, "end": b, "before": old, "after": new_name}
        location = "text"
    else:
        new_text, ts, edit, location = text, (n["start"], n["end"]), None, "context"
    affected = [m["text"] for m in fin if m["role"] == "current"]
    return Mutation(
        mutator="entity", text=new_text, context=other_doc["context"], as_of=None, target_value_text=n["text"],
        target_span=ts, original_value_text=n["text"],
        expected_value=None if alt is None else round(alt / (SCALE_FACTOR[n["scale"]] if n["kind"] == "money"
                                                             else 1), 4),
        affected_value_texts=affected, edit=edit, screen="xbrl" if alt is not None else "assumed",
        detail={"location": location, "from_ticker": company.ticker, "to_ticker": t, "metric": n["metric"]})


# ============================================================ stage 2: items and split

def _hydrate(sentence: dict) -> dict:
    """Add derived fields the mutators use (scaled values)."""
    for n in sentence["numbers"]:
        n["scaled"] = n["sign"] * n["value"] * SCALE_FACTOR[n["scale"]]
    return sentence


def item_hash(text: str, as_of: Optional[str], context: Optional[str]) -> str:
    return sha256_text(json.dumps({"text": text, "as_of": as_of, "context": context}, sort_keys=True))


def build_items(source_rows: list[dict]) -> list[dict]:
    """All corpus items (originals, controls, mutants) with gold labels and split, sorted by item_id."""
    company_rows = {r["ticker"]: r for r in source_rows if r["kind"] == "company"}
    docs = {r["doc_id"]: r for r in source_rows if r["kind"] == "document"}
    sentences = [_hydrate(json.loads(json.dumps(r))) for r in source_rows
                 if r["kind"] == "sentence" and r.get("pool") != BASIS_EXTRA_POOL]
    docs_by_label: dict[str, dict] = defaultdict(dict)
    for d in docs.values():
        docs_by_label[d["ticker"]][(d["fy"], d["fp"])] = d

    originals: list[dict] = []
    for s in sentences:
        d = docs[s["doc_id"]]
        is_fl = s["pool"] == "forward_looking"
        originals.append(_item(
            item_id=f"{VERSION}-{s['sentence_id']}", s=s, d=d, text=s["text"], context=d["context"], as_of=None,
            mutator="none" if not is_fl else "forward_looking_control", original_item_id=None,
            expected_status="unverifiable" if is_fl else "verified",
            expected_reason="forward_looking" if is_fl else "match",
            extra={"forward_looking_value_texts": [n["text"] for n in s["numbers"]
                                                   if n["kind"] in ("money", "pct", "pp", "bps")]} if is_fl else {}))

    # candidate mutations per sentence and mutator (deterministic)
    pool = [s for s in sentences if s["pool"] == "verifiable"]
    cands: dict[str, dict[str, list[Mutation]]] = {}
    for s in pool:
        d = docs[s["doc_id"]]
        c: dict[str, list[Mutation]] = {}
        for name, fn in (("value", mutate_value), ("scale", mutate_scale), ("period", mutate_period),
                         ("look_ahead", mutate_look_ahead)):
            m = fn(s, d, company_rows)
            if m:
                c[name] = [m]
        bs = [m for m in (mutate_basis(s, d, company_rows, v) for v in (0, 1)) if m]
        if bs:
            c["basis"] = bs
        e = mutate_entity(s, d, company_rows, docs_by_label)
        if e:
            c["entity"] = [e]
        cands[s["sentence_id"]] = c

    # Assignment, round-robin over mutators (scarcest first). Each pick goes to the company with the fewest
    # mutants of that kind so far, then to the least-used original. At most MAX_MUTANTS_PER_ORIGINAL mutants
    # per original, and one per (original, mutator) except the two basis variants.
    by_id = {s["sentence_id"]: s for s in pool}
    avail = {mut: [(sid, mm) for sid, c in sorted(cands.items()) for mm in c.get(mut, [])] for mut in MUTATORS}
    CANDIDATE_COUNTS.clear()
    CANDIDATE_COUNTS.update({mut: len({sid for sid, _ in v}) for mut, v in avail.items()})
    order = sorted(MUTATORS, key=lambda m: (len(avail[m]), m))
    load: Counter = Counter()
    per_company: Counter = Counter()
    have: set[tuple[str, str]] = set()
    taken: Counter = Counter()
    assigned: list[tuple[dict, Mutation]] = []
    progress = True
    while progress:
        progress = False
        for mut in order:
            if taken[mut] >= TARGET_PER_MUTATOR:
                continue
            ok = [(sid, mm) for sid, mm in avail[mut] if load[sid] < MAX_MUTANTS_PER_ORIGINAL
                  and (mut == "basis" or (sid, mut) not in have)]
            if not ok:
                continue
            sid, mm = min(ok, key=lambda x: (per_company[(by_id[x[0]]["ticker"], mut)], load[x[0]],
                                             stable_frac("assign", mut, x[0], x[1].target_value_text)))
            avail[mut].remove((sid, mm))
            assigned.append((by_id[sid], mm))
            load[sid] += 1
            per_company[(by_id[sid]["ticker"], mut)] += 1
            have.add((sid, mut))
            taken[mut] += 1
            progress = True

    mutants: list[dict] = []
    counts: Counter = Counter()
    for s, mm in sorted(assigned, key=lambda x: (x[0]["sentence_id"], x[1].mutator, x[1].target_value_text)):
        d = docs[s["doc_id"]]
        counts[(s["sentence_id"], mm.mutator)] += 1
        suffix = mm.mutator if counts[(s["sentence_id"], mm.mutator)] == 1 else \
            f"{mm.mutator}{counts[(s['sentence_id'], mm.mutator)]}"
        mutants.append(_item(
            item_id=f"{VERSION}-{s['sentence_id']}-{suffix}", s=s, d=d, text=mm.text, context=mm.context,
            as_of=mm.as_of, mutator=mm.mutator, original_item_id=f"{VERSION}-{s['sentence_id']}",
            expected_status="wrong", expected_reason=EXPECTED_REASON[mm.mutator],
            extra={"target_value_text": mm.target_value_text, "target_span": list(mm.target_span),
                   "original_value_text": mm.original_value_text, "expected_value": mm.expected_value,
                   "affected_value_texts": mm.affected_value_texts, "edit": mm.edit, "mutant_screen": mm.screen,
                   "mutation": mm.detail}))
    items = originals + mutants
    items += build_basis_extra_items(source_rows, docs, company_rows)
    assign_split(items)
    return sorted(items, key=lambda r: r["item_id"])


def build_basis_extra_items(source_rows: list[dict], docs: dict, company_rows: dict) -> list[dict]:
    """The basis appendix (batch basis5): each basis_extra sentence as an unmodified original plus its basis
    mutants (two variants for the GAAP/non-GAAP pair pattern, one for the all-non-GAAP pattern)."""
    out: list[dict] = []
    for s in (_hydrate(json.loads(json.dumps(r))) for r in source_rows
              if r["kind"] == "sentence" and r.get("pool") == BASIS_EXTRA_POOL):
        d = docs[s["doc_id"]]
        muts = [m for m in (mutate_basis_extra(s, d, company_rows, v) for v in (0, 1)) if m]
        if not muts:
            continue
        oid = f"{VERSION}-{s['sentence_id']}"
        out.append(_item(item_id=oid, s=s, d=d, text=s["text"], context=d["context"], as_of=None, mutator="none",
                         original_item_id=None, expected_status="verified", expected_reason="match",
                         extra={"batch": BASIS_EXTRA_BATCH}))
        for k, mm in enumerate(muts, 1):
            out.append(_item(
                item_id=f"{oid}-basis" + (str(k) if k > 1 else ""), s=s, d=d, text=mm.text, context=mm.context,
                as_of=mm.as_of, mutator="basis", original_item_id=oid, expected_status="wrong",
                expected_reason=EXPECTED_REASON["basis"],
                extra={"batch": BASIS_EXTRA_BATCH, "target_value_text": mm.target_value_text,
                       "target_span": list(mm.target_span), "original_value_text": mm.original_value_text,
                       "expected_value": mm.expected_value, "affected_value_texts": mm.affected_value_texts,
                       "edit": mm.edit, "mutant_screen": mm.screen, "mutation": mm.detail}))
    return out


def _item(*, item_id: str, s: dict, d: dict, text: str, context: str, as_of: Optional[str], mutator: str,
          original_item_id: Optional[str], expected_status: str, expected_reason: str, extra: dict) -> dict:
    base = {
        "item_id": item_id, "corpus_version": VERSION, "ticker": s["ticker"], "text": text, "as_of": as_of,
        "context": context, "original_item_id": original_item_id, "mutator": mutator,
        "target_value_text": None, "target_span": None, "original_value_text": None, "expected_value": None,
        "affected_value_texts": [], "expected_status": expected_status, "expected_reason": expected_reason,
        "edit": None, "mutant_screen": None, "mutation": None,
        "group_id": original_item_id or item_id, "source_sentence_id": s["sentence_id"], "doc_id": d["doc_id"],
        "fiscal_year": d["fy"], "fiscal_period": d["fp"], "period_end": d["period_end"],
        "accession": d["accession"], "accepted_at": d["accepted_at"], "url": d["url"],
        "char_start": s["char_start"], "char_end": s["char_end"],
    }
    base.update(extra)
    base["item_hash"] = item_hash(text, as_of, context)
    return base


def assign_split(items: list[dict]) -> None:
    """Group-aware stratified split, run separately per batch (v0 items carry no batch field; appendix items
    do), so an appended batch never moves a v0 item between dev and test."""
    batches: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        batches[it.get("batch", VERSION)].append(it)
    for batch in sorted(batches):
        _assign_split_batch(batches[batch])


def _assign_split_batch(items: list[dict]) -> None:
    """Group-aware stratified split. A group is an original plus its mutants. Strata are (ticker, mutator of
    the group's rarest mutant, or the original's own kind). Inside a stratum, groups are ordered by a seeded
    hash; a running fractional count across strata (sorted) decides how many go to test, so the overall test
    share is 30% and each stratum gets 30% +/- 1 group."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[it["group_id"]].append(it)
    global_count = Counter(it["mutator"] for it in items)
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for gid, its in groups.items():
        muts = sorted({it["mutator"] for it in its if it["original_item_id"]}, key=lambda m: (global_count[m], m))
        own = next(it["mutator"] for it in its if not it["original_item_id"])
        strata[(its[0]["ticker"], muts[0] if muts else own)].append(gid)
    acc, assigned_test = 0.0, 0
    split_of: dict[str, str] = {}
    for key in sorted(strata):
        gids = sorted(strata[key], key=lambda g: stable_frac("split", g))
        acc += (1 - DEV_FRACTION) * len(gids)
        n_test = int(round(acc)) - assigned_test
        n_test = max(0, min(n_test, len(gids)))
        for j, g in enumerate(gids):
            split_of[g] = "test" if j < n_test else "dev"
        assigned_test += n_test
    for it in items:
        it["split"] = split_of[it["group_id"]]


# ============================================================ reporting and main

def summarize(items: list[dict]) -> str:
    lines = []
    by_mut = Counter(it["mutator"] for it in items)
    by_split = Counter(it["split"] for it in items)
    lines.append(f"items: {len(items)}  (dev {by_split['dev']}, test {by_split['test']})")
    lines.append("by mutator: " + ", ".join(f"{m} {by_mut[m]}" for m in
                                            ("none", "forward_looking_control", *MUTATORS)))
    tick = Counter(it["ticker"] for it in items)
    lines.append("by company: " + ", ".join(f"{t} {tick[t]}" for t in TICKERS))
    grid = defaultdict(Counter)
    for it in items:
        grid[it["ticker"]][it["mutator"]] += 1
    cols = ("none", "forward_looking_control", *MUTATORS)
    lines.append("\n" + "ticker".ljust(7) + "".join(c[:10].rjust(11) for c in cols))
    for t in TICKERS:
        lines.append(t.ljust(7) + "".join(str(grid[t][c]).rjust(11) for c in cols))
    lines.append("\noriginals each mutator applies to: " + ", ".join(f"{m} {CANDIDATE_COUNTS[m]}" for m in MUTATORS))
    scr = Counter((it["mutator"], it["mutant_screen"]) for it in items if it["mutant_screen"])
    lines.append("\nmutant falsity evidence: " + ", ".join(f"{m}/{s} {n}" for (m, s), n in sorted(scr.items())))
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items-only", action="store_true", help="skip stage 1; rebuild items from v0_sources.jsonl")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help=f"cap on live SEC requests (default {DEFAULT_MAX_REQUESTS})")
    ap.add_argument("--dump-rejects", type=Path, help="write every analyzed sentence with its rejection reasons")
    ap.add_argument("--add-basis-extra", action="store_true",
                    help="offline: append the basis appendix's sentence rows (4-5 numbers) to v0_sources.jsonl, "
                         "leaving every existing row unchanged, then rebuild the items")
    args = ap.parse_args(argv)

    if args.add_basis_extra:
        rows = read_jsonl(SOURCES_PATH)
        kept = [r for r in rows if r.get("pool") != BASIS_EXTRA_POOL]
        extra = build_basis_extra_rows(kept)
        write_jsonl(SOURCES_PATH, kept + extra)
        print(f"appended {len(extra)} basis-appendix sentence rows to {SOURCES_PATH.relative_to(ROOT)}")
    elif not args.items_only:
        rows = build_sources(args.max_requests)
        rows += build_basis_extra_rows(rows)
        write_jsonl(SOURCES_PATH, rows)
        if args.dump_rejects:
            write_jsonl(args.dump_rejects, ({"doc_id": r["doc_id"], "text": r["text"], "pool": r["pool"],
                                             "eligible": r.get("eligible"), "reasons": r.get("reasons"),
                                             "numbers": [(n["text"], n["metric"], n["qualifier"], n["role"],
                                                          n["screen"], n["screen_detail"], n["basis"])
                                                         for n in r["numbers"]]} for r in ANALYZED))
        n_s = sum(r["kind"] == "sentence" for r in rows)
        print(f"wrote {SOURCES_PATH.relative_to(ROOT)} ({n_s} sentences)")
    rows = read_jsonl(SOURCES_PATH)
    items = build_items(rows)
    write_jsonl(DEV_PATH, (it for it in items if it["split"] == "dev"))
    write_jsonl(TEST_PATH, (it for it in items if it["split"] == "test"))
    print(f"wrote {DEV_PATH.relative_to(ROOT)} and {TEST_PATH.relative_to(ROOT)}")
    print(summarize(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
