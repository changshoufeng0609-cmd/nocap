"""Release-text evidence: a figure NoCap's structured facts can't check, confirmed by the sentence of the company's own
filed 8-K earnings release that states it.

Most "?" in a real earnings summary are figures the structured facts don't hold (Microsoft Cloud revenue, comparable
sales, constant-currency growth, a store count), yet the company's earnings release (EX-99.x of an item-2.02 8-K)
states them in words. scripts/build_release_text.py indexes those sentences, one JSON line per prose sentence or bullet
item with digits: data/snapshot/release_text/<TICKER>.jsonl.gz with {"ticker", "cik", "accession", "exhibit", "url",
"accepted_at", "fiscal_year", "fiscal_period", "period_start", "period_end", "seq", "text", "lead"}, where
fiscal_period is the release's reporting quarter and lead the nearest lead-in sentence ending in ":" of a bullet item.

The model proposes, code decides, and a wrong ✓ is worse than "?". This is only a fallback: receipts.verify.verify asks
it about a figure its structured check left "?" for lack of evidence (reason no_evidence), never about one that is
ambiguous, forward-looking, already ✓, ✗ or ◷, or whose company or quarter doesn't resolve. A match makes the figure ✓
(reason release_statement) with the quoted sentence as its receipt; no match leaves the "?" exactly as it was. Text
never makes a figure ✗: a sentence can show what a figure is, but a number prose doesn't state proves nothing.

A release sentence states the claim's figure only when all of these hold (release_statement, _agree):
- the same quarter: the release reports the claim's resolved fiscal quarter and was accepted by as_of, and the sentence
  and its lead name no other period (another quarter, fiscal or calendar year, half, six or nine months, full year,
  "ended" another date) and no forward-looking or "respectively" wording. A year named without a quarter word, even
  the release's own fiscal year ("For fiscal 2026, free cash flow was $893 million"), and a section heading of the
  release's body that names one ("Fiscal 2026 Financial Stewardship"), make a figure the year's, never the quarter's;
- the same number, written the same way: "11.2%" is "11.2 percent", but "$93.9 billion" is not "$93.87 billion", and
  per-share amounts, millions, billions and counts stay apart. A prior-period comparison base ("from $84.4 billion last
  year", "compared to $2.610 billion") is never the current figure;
- the same measure: the claim's measure words (its metric_text and the clause before its number, without stopwords,
  hedges, direction and period words) all appear around the sentence's number, and every word that scopes the
  sentence's number (its clause's subject, a geography after it: "U.S.", "International Operated Markets", "Microsoft
  Cloud", "647 in the United States") appears in the claim. A bullet item takes its lead's measure ("U.S. increased
  0.8%" under "Global comparable sales increased 1.3%:") only when it names no measure of its own: the index's lead is
  not nesting-aware, so "Systemwide sales increased 5%" never borrows the lead's "comparable sales". The other current
  figures the claim's clause gives for the measure are in the sentence too ("Operating margin of 11.1%, up 60 bps" is
  not "operating margin decreased 60 bps to 9.8%"), and the release states the measure at no other number: a heading
  the indexed sentence doesn't show can make it a segment's or a year to date's ("Recurring revenues ..., or 8%");
- the same qualifiers, both ways: constant currency, adjusted or non-GAAP (including "excluding ..."), organic,
  comparable, systemwide, trailing twelve months, each bound to its own number: in "increased 21% (up 22% in constant
  currency)" only the 22% is a constant-currency figure;
- the same direction and kind (a change, or a level) when both sides state one, and the same comparison (year over
  year, or sequential) when both state one for a change;
- compatible bounds: "more than 20%" is "over 20%" and "nearly 220 million" is "nearly 220 million"; an "approximately"
  claim may match a bare figure, but a bare "20%" never matches "over 20%".
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

from receipts import periods
from receipts import verify as _v
from receipts.types import SCALE_FACTOR, Claim, EvidenceStore, Fact, Verdict

log = logging.getLogger(__name__)

REASON = "release_statement"
SOURCE = "release_text"
ENV_DIR = "RECEIPTS_RELEASE_TEXT_DIR"  # an index directory to read instead of the store's own (evaluation runs)
_TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,9}\Z")
_CACHE_FILES = 64


# ---------------------------------------------------------------------------
# The index: read lazily, one file per ticker, cached until the file changes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Unit:
    """One indexed sentence or bullet item of an earnings release."""

    ticker: str
    cik: int
    accession: str
    exhibit: str
    url: str
    accepted_at: datetime
    fiscal_year: int
    fiscal_period: str
    period_start: Optional[date]
    period_end: Optional[date]
    seq: int
    text: str
    lead: str


class Statement(NamedTuple):
    """The unit that states the claim's figure, the sentence of it quoted as the receipt, and the figure's
    qualifiers there."""

    unit: Unit
    quote: str
    qualifiers: frozenset[str]


_LOCK = threading.Lock()
_CACHE: dict[Path, tuple[tuple[int, int], tuple[Unit, ...]]] = {}


def index_dir(store: Any) -> Optional[Path]:
    """Where the store's release-text index lives: the store's own release_text_dir when it sets one, else
    RECEIPTS_RELEASE_TEXT_DIR, else <data dir>/release_text or <data dir>/snapshot/release_text beside the store's
    facts.sqlite. None for a store without a data directory (a test double): no release text, nothing changes."""
    explicit = getattr(store, "release_text_dir", None)
    if explicit:
        return Path(explicit)
    env = os.environ.get(ENV_DIR, "").strip()
    if env:
        return Path(env)
    db = getattr(store, "db_path", None)
    if db is None:
        return None
    base = Path(db).parent
    for candidate in (base / "release_text", base / "snapshot" / "release_text"):
        if candidate.is_dir():
            return candidate
    return None


def _date(x: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(x)[:10]) if x else None
    except ValueError:
        return None


def _unit(row: Any) -> Optional[Unit]:
    try:
        fp = str(row["fiscal_period"]).upper()
        url = str(row["url"])
        accepted = _v.to_utc(row["accepted_at"])
        if fp not in periods.QUARTERS or not url.startswith("https://www.sec.gov/") or accepted is None:
            return None
        return Unit(ticker=str(row["ticker"]).upper(), cik=int(row["cik"]), accession=str(row["accession"]),
                    exhibit=str(row.get("exhibit") or ""), url=url, accepted_at=accepted,
                    fiscal_year=int(row["fiscal_year"]), fiscal_period=fp, period_start=_date(row.get("period_start")),
                    period_end=_date(row.get("period_end")), seq=int(row.get("seq") or 0),
                    text=" ".join(str(row["text"]).split()), lead=" ".join(str(row.get("lead") or "").split()))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _load(path: Path) -> tuple[Unit, ...]:
    """Every valid line of one index file; a bad or half-written line is skipped, never fatal."""
    out: list[Unit] = []
    try:
        opener: Any = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    unit = _unit(json.loads(line))
                except ValueError:
                    continue
                if unit is not None:
                    out.append(unit)
    except (OSError, EOFError, ValueError) as e:  # a damaged file reads as no release text
        log.warning("release text %s unreadable: %s", path.name, type(e).__name__)
        return ()
    return tuple(out)


def units(store: Any, ticker: str) -> tuple[Unit, ...]:
    """The indexed units of one company, () when there is no index or no file for it."""
    folder = index_dir(store)
    ticker = (ticker or "").strip().upper()
    if folder is None or not _TICKER_RE.match(ticker):
        return ()
    for path in (folder / f"{ticker}.jsonl.gz", folder / f"{ticker}.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        stamp = (st.st_mtime_ns, st.st_size)
        with _LOCK:
            hit = _CACHE.get(path)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        loaded = _load(path)
        with _LOCK:
            _CACHE[path] = (stamp, loaded)
            while len(_CACHE) > _CACHE_FILES:
                _CACHE.pop(next(iter(_CACHE)))
        return loaded
    return ()


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _stems(words: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in words.split())


_STOP = _stems(
    "a an the of to in on at by for from with without and or nor as than that this these those its it their our his "
    "her we us they them you your i which who whom whose what when where while whereas but also both each all any such "
    "into onto out off per via within across among between including include included includes "
    "be is are was were been being am has have had having do does did done can could will would shall should may might "
    "must there here then thus so very more most less least much many few several said says "
    "reported report reports reporting posted posts recorded delivered deliver delivers delivering achieved achieve "
    "achieves achieving generated generate generates totaled totalled totaling totalling reached reach reaches came come "
    "comes stood stand saw see sees showed show shows made make makes hit hits remained remain remains continued "
    "continue continues continuing currently current now today operates operate operated returned return returns "
    "company companies corporation corp inc incorporated co ltd plc llc firm group business businesses result results "
    "performance financial financials number numbers figure figures amount amounts level levels overall global "
    "worldwide consolidated combined aggregate entire total company-wide companywide network location locations "
    "footprint key strong solid robust healthy record notable impressive significant positive negative positively "
    "negatively favorably unfavorably favorable unfavorable impacted impact impacts affected affect driven drive drove "
    "reflecting reflected reflects reflect primarily mainly largely partly partially offset offsets related relating "
    "associated due thanks helped help supported support led boosted boost fueled fuelled resulting resulted "
    "representing represented represents accounting accounted approximately approx about around roughly nearly almost "
    "over above below under exceeding exceeded exceed exceeds just some close circa estimated only "
    "slightly modestly moderately marginally significantly substantially considerably sharply strongly steadily "
    "again further still meanwhile however additionally moreover likewise "
    "million millions billion billions thousand thousands trillion trillions percent percentage point points dollar "
    "dollars usd bps")
_DIRECTION = _stems(
    "increase increased increases increasing grew grow grows growing growth rose rise rises rising risen up gain gains "
    "gained gaining jump jumped jumps surge surged surges climb climbed higher expanded expand expands expanding "
    "expansion accelerate accelerated accelerating acceleration improve improved improves improving improvement "
    "decrease decreased decreases decreasing decline declined declines declining fell fall falls falling fallen down "
    "drop dropped drops lower shrank shrink shrinks shrinking shrunk contract contracted contraction narrowed "
    "decelerate decelerated decelerating slowed slowing change changed changes")
_PERIOD = _stems(
    "quarter quarters quarterly year years yearly annual annually fiscal fy q1 q2 q3 q4 month months monthly week weeks "
    "weekly period periods first second third fourth last prior previous same ago earlier ended ending end "
    "year-over-year yoy y/y sequential sequentially quarter-over-quarter qoq q/q h1 h2 half ytd corresponding "
    "quarter-end year-end day days twelve trailing")
_QUALIFIER_WORDS = _stems(
    "constant currency currencies cc fx neutral adjusted adjusting adjust non-gaap gaap organic organically comparable "
    "comp comps same-store systemwide system-wide ttm ltm reported basis excluding exclude excludes excluded normalized "
    "core underlying pro forma pro-forma like-for-like")
_GENERIC = _STOP | _DIRECTION | _PERIOD | _QUALIFIER_WORDS
_GEOGRAPHY = _stems(
    "united_states united_kingdom america americas north south latin central canada canadian mexico puerto rico "
    "international domestic europe european emea apac asia asian pacific china chinese greater japan japanese korea "
    "taiwan australia india brazil germany france spain italy iceland zealand sweden africa middle east region")
# The nouns a figure measures. A bullet item that names none of them ("U.S. increased 0.8%") takes its lead's measure.
_MEASURE_NOUNS = _stems(
    "revenue revenues sale sales income earnings profit profits loss losses margin margins eps per_share expense expenses "
    "cost costs cash flow flows ebitda ebit dividend dividends repurchase repurchases buyback buybacks share shares "
    "member members membership user users subscriber subscribers customer customers account accounts warehouse "
    "warehouses store stores restaurant restaurants unit units shipment shipments delivery deliveries order orders "
    "booking bookings backlog obligation obligations rpo arr deposit deposits loan loans asset assets liability "
    "liabilities debt equity capital capex expenditure expenditures investment investments spending traffic "
    "transaction transactions visit visits guest guests volume volumes price prices rate rates yield ratio tax "
    "taxes charge charges benefit benefits impairment gain headcount employee employees cardholder cardholders "
    "enrollment gmv payment payments premium premiums reserve reserves inventory contract contracts fee fees refund "
    "refunds")
_FORWARD = _stems(
    "expect expects expected expecting anticipate anticipates anticipated anticipating guidance outlook forecast "
    "forecasts forecasted project projects projected projecting projection projections target targets targeted intend "
    "intends plan plans planned planning aim aims believe believes estimate estimates sees will would should could "
    "might")

# Phrases read as one word before splitting (case-insensitive unless the pattern says otherwise).
# "eps" is earnings per share; "per_share" any per-share amount: FFO, a dividend or a charge per share is not EPS.
_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:net\s+)?(?:earnings|income)\s+per\s+(diluted|basic|common)?\s*share\b|\bEPS\b", re.I),
     r" \1 eps "),
    (re.compile(r"\bper\s+(diluted|basic|common)\s+share\b", re.I), r" \1 per_share "),
    (re.compile(r"\bper\s+share\b", re.I), " per_share "),
    (re.compile(r"\bnon[\s-]?recurring\b|\bone[\s-]time\b|\bone[\s-]off\b", re.I), " one_time "),
    (re.compile(r"\bU\.\s?S\.(?:\s?A\.)?|\bU\.S\b|\bUSA\b|\bUS\b(?!\$)|\bUnited\s+States\b"), " united_states "),
    (re.compile(r"\bU\.\s?K\.|\bUK\b|\bUnited\s+Kingdom\b"), " united_kingdom "),
    (re.compile(r"\be[\s-]?commerce\b", re.I), " ecommerce "),
    (re.compile(r"\bsame[\s-]+stores?\b|\blike[\s-]+for[\s-]+like\b", re.I), " comparable "),
    (re.compile(r"\bsystem[\s-]+wide\b", re.I), " systemwide "),
)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'’&-]*")


def _words(text: str) -> list[str]:
    """Normalized words: lowercase, possessives and plurals folded, the phrases above as one word."""
    for rx, repl in _PHRASES:
        text = rx.sub(repl, text)
    out = []
    for m in _WORD_RE.finditer(text):
        w = m.group().lower().replace("’", "'").strip("-'&")
        if re.fullmatch(r"[a-z][a-z'&-]{2,}\d{1,2}", w):
            w = w.rstrip("0123456789")  # a footnote mark: "Net income2", "free cash flow3"
        for tail in ("'s", "'"):
            if w.endswith(tail):
                w = w[: -len(tail)]
        if w:
            out.append(_stem(w))
    return out


def _content(text: str, company: frozenset[str]) -> set[str]:
    """The words of `text` that say what a figure measures: no stopwords, hedges, directions, periods, qualifiers or
    company names."""
    return {w for w in _words(text) if w not in _GENERIC and w not in company and not w[:1].isdigit()}


def _company_words(store: Any, cik: int, ticker: str, claim: Claim) -> frozenset[str]:
    """The words of the company's own names (the claim's entity, its ticker, its SEC name), which never scope a
    figure. A measure noun or a geography in a name ("Booking", "International") stays a scope word."""
    names = [claim.entity or "", ticker]
    company = getattr(store, "company", None)
    if callable(company):
        try:
            names.append(str((company(cik) or {}).get("name") or ""))
        except Exception:  # a hint only
            pass
    words: set[str] = set()
    for name in names:
        words.update(_words(re.sub(r"/[A-Z]+/?", " ", name)))
    words.update(w[:-1] for w in list(words) if w.endswith("s") and len(w) > 3)  # "MCDONALDS CORP" is McDonald's
    return frozenset(words - _MEASURE_NOUNS - _GEOGRAPHY)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
_SCALES = {"trillion": "trillion", "tn": "trillion", "t": "trillion", "billion": "billion", "bn": "billion",
           "bln": "billion", "b": "billion", "million": "million", "mn": "million", "mln": "million",
           "mm": "million", "m": "million", "thousand": "thousand", "k": "thousand"}
_NUMBER_RE = re.compile(
    r"(?<![\w.,/:$\-−])(?P<lp>\()?(?P<sign>[-−])?(?P<cur>US\$|\$)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d,]*\d)(?!\.\d)(?P<rp>\))?"
    r"(?P<suf>\s?%|\s?(?:percent|per\s+cent)\b|\s(?:trillion|billion|million|thousand)s?\b"
    r"|\s?(?:tn|bn|bln|mn|mln|mm)\b|\s?(?-i:[BMKT])\b|\s(?:basis\s+points?|bps|percentage\s+points?|points?|pts?)\b)?",
    re.I)
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
          r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTH_BEFORE_RE = re.compile(rf"\b{_MONTH}\.?\s*$", re.I)  # "September 30", never "decreased 5%"
_REFERENCE_BEFORE_RE = re.compile(r"\b(?:exhibits?|items?|forms?|sections?|rules?|pages?|notes?|footnotes?|no\.|"
                                  r"nos\.|schedules?|tables?|chapters?)\s*$", re.I)
_BLANK_RES = (
    re.compile(r"\(?\b\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b|\b\d{3}/\d{3}-\d{4}\b|\+\d[\d\s()\-]{7,}\d"),  # phone numbers
    re.compile(r"\b\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?", re.I),  # times
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO dates
)


@dataclass(frozen=True)
class _Num:
    start: int      # where the figure starts: "(", a sign, the currency or the first digit
    num: int        # where its digits start
    end: int
    digits: str     # as written, without thousands separators
    cls: str        # "pct", "ps" (per share), "usd:<scale>", "n:<scale>" (a count), "bps", "pts"
    neg: bool


def _digits(num: str) -> str:
    d = num.replace(",", "")
    return "0" + d if d.startswith(".") else d


def _blank(text: str) -> str:
    """Phone numbers, times and ISO dates as spaces, offsets kept: they are never figures."""
    for rx in _BLANK_RES:
        text = rx.sub(lambda m: " " * len(m.group()), text)
    return text


def _numbers(text: str) -> list[_Num]:
    """The figures written in `text`, with their offsets. Years, dates, hyphenated words ("90-day", "1-point"),
    references ("Exhibit 99.2") and digits inside words are not figures."""
    scan = _blank(text)
    out = []
    for m in _NUMBER_RE.finditer(scan):
        if not m.group("num"):
            continue
        num_start, num_end = m.span("num")
        suffix = (m.group("suf") or "").strip()
        if not suffix and re.match(r"-?[A-Za-z]", scan[num_end:num_end + 2]):
            continue  # "90-day", "16-week", "3Q", "10x"
        if re.match(r"[-–]\d", scan[m.end():m.end() + 2]):
            continue  # a range or an identifier: "2025-2026"
        before = scan[:m.start()]
        if _MONTH_BEFORE_RE.search(before) or _REFERENCE_BEFORE_RE.search(before):
            continue  # "September 30", "Exhibit 99.2"
        digits = _digits(m.group("num"))
        if not m.group("cur") and not suffix and re.fullmatch(r"(?:19|20)\d{2}", digits):
            continue  # a year
        low = suffix.lower()
        after = scan[m.end():]
        if low == "%" or low.startswith(("percent", "per cent")):
            cls = "pct"
        elif "basis" in low or low == "bps":
            cls = "bps"
        elif "point" in low or low in ("pt", "pts"):
            cls = "pts"
        else:
            scale = _SCALES.get(low[:-1] if low.endswith("s") and len(low) > 3 else low, "") if low else ""
            if m.group("cur"):
                if scale:
                    cls = f"usd:{scale}"
                elif re.match(r"\s*per\s+(?:diluted\s+|basic\s+|common\s+)?share\b", after, re.I):
                    cls = "ps"
                else:
                    cls = "usd:units"
            else:
                cls = f"n:{scale or 'units'}"
        # Accounting negatives close right after the digits: "(3)%", "$(0.14)". "increased $401 million (13.7 percent)"
        # is a parenthetical restatement, not a negative figure.
        dollar_paren = bool(re.search(r"(?:US\$|\$)\s?\($", scan[:num_start])) and bool(m.group("rp"))
        neg = bool(m.group("sign")) or bool(m.group("lp") and m.group("rp")) or dollar_paren
        if dollar_paren and not cls.startswith(("usd", "ps")):
            cls = "ps" if re.match(r"\s*per\s+(?:diluted\s+|basic\s+|common\s+)?share\b", after, re.I) else "usd:units"
        if m.group("lp") and m.group("rp"):
            start = m.start("lp")
        elif m.group("sign"):
            start = m.start("sign")
        elif m.group("cur"):
            start = m.start("cur")
        else:
            start = num_start
        out.append(_Num(start, num_start, m.end(), digits, cls, neg))
    return out


def _claim_number(claim: Claim) -> Optional[tuple[str, str, bool, int]]:
    """(digits, class, negative, offset of the figure within value_text) of the claim's value_text, as written."""
    vt = claim.value_text or ""
    m = re.search(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+", vt)
    if m is None:
        return None
    digits = _digits(m.group())
    scale_word = re.match(r"\s*(trillion|billion|million|thousand|tn|bn|bln|mn|mln|mm)s?\b|\s*([BMKT])\b",
                          vt[m.end():], re.I)
    scale = ""
    if scale_word:
        scale = _SCALES.get((scale_word.group(1) or scale_word.group(2)).lower(), "")
    if not scale:
        scale = {"thousands": "thousand", "millions": "million", "billions": "billion"}.get(claim.scale, "units")
    if claim.unit == "%":
        cls = "pct"
    elif claim.unit == "USD/share":
        cls = "ps"
    elif claim.unit == "USD":
        cls = f"usd:{scale}"
    elif re.search(r"basis\s+points?|\bbps\b", vt, re.I):
        cls = "bps"
    elif re.search(r"\bpoints?\b|\bpts?\b|\bpp\b", vt, re.I):
        cls = "pts"
    else:
        cls = f"n:{scale}"
    prefix = vt[:m.start()]
    neg = bool(re.search(r"[-−(]\s*(?:US\$|\$)?\s*$", prefix))
    figure_at = len(re.sub(r"[\s$(\-−]*(?:US\$|\$)?[\s(\-−]*$", "", prefix))
    return digits, cls, neg, figure_at


# ---------------------------------------------------------------------------
# A figure in its sentence: clause, measure, qualifiers, direction, kind, bound, comparison, base
# ---------------------------------------------------------------------------
_BREAK_RE = re.compile(
    r"[,;:—–()]|\.(?=\s|$)"
    r"|\b(?:while|whereas|but|with|including|versus|vs\.?|against|compared|from|by|as)\b"
    r"|,?\s+and\s+(?=(?:was|were|is|are|has|had|have|increas\w*|decreas\w*|grew|rose|fell|declin\w*|reached|"
    r"totaled|totalled|delivered|generated|posted|recorded|expanded|improved|up|down)\b)", re.I)
_UP_RE = re.compile(r"\b(?:increas\w*|grew|grow\w*|rose|ris(?:e|es|en|ing)|up|gain\w*|jump\w*|surg\w*|climb\w*|"
                    r"higher|expand\w*|expansion|accelerat\w*|improv\w*)\b", re.I)
_DOWN_RE = re.compile(r"\b(?:decreas\w*|declin\w*|fell|fall\w*|down|drop\w*|lower|shr[ai]nk\w*|shrunk|contract\w*|"
                      r"narrow\w*|decelerat\w*|slow\w*)\b", re.I)
_CHANGE_NOUN_RE = re.compile(r"\b(?:growth|increase|decrease|decline|rise|gain|drop|fall|improvement|expansion|"
                             r"contraction|jump)\b(?:\s+[\w'’-]+){0,3}\s*$", re.I)
_CHANGE_AFTER_RE = re.compile(r"\s*(?:\)\s*)?(?:(?!(?:and|or|but|while|to|from|in|of|for|at|as|compared|versus)\b)"
                              r"[A-Za-z][\w'’&*-]*\s+){0,5}?(?P<w>growth|increase|rise|gain|jump|improvement|expansion|"
                              r"decline|decrease|drop|fall|contraction|higher|lower|more|less|fewer|greater)\b", re.I)
_LEVEL_MARKER_RE = re.compile(r"\b(?:was|were|is|are|be|of|to|at|reached|totaled|totalled|totaling|totalling|stood|"
                              r"with|operates|operated|had|has|have)(?:\s+(?:a|an|the|new|record|all-time|total|"
                              r"quarterly))*\s*$", re.I)
_HEDGE_TAIL_RE = re.compile(r"(?:\b(?:approximately|approx\.?|about|around|roughly|nearly|almost|over|more\s+than|"
                            r"less\s+than|just|some|close\s+to|at\s+least|at\s+most|up\s+to|under|above|below)\s*|~\s*)+$",
                            re.I)
_QUALIFIER_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cc", re.compile(r"constant[\s-]+currenc(?:y|ies)|currency[\s-]+neutral|\bex[\s-]?fx\b|\bfx[\s-]+neutral\b"
                      r"|\b(?:fx|f/x|currency)[\s-]+adjusted\b"
                      r"|\bexclud\w*\s+(?:the\s+)?(?:impacts?\s+(?:of|from)\s+)?(?:changes\s+in\s+)?(?:[\w-]+\s+){0,4}?"
                      r"(?:foreign[\s-]+(?:currency|exchange)|currency|fx)\b"
                      r"|\badjust\w*\s+for\s+(?:the\s+)?(?:impacts?\s+of\s+)?(?:[\w-]+\s+){0,5}?(?:foreign[\s-]+exchange|"
                      r"currency|fx)\b", re.I)),
    ("adjusted", re.compile(r"\bnon[\s-]?gaap\b|\badjusted\b|\bexclud(?:ing|es?|ed)\b|\bnormali[sz]ed\b|\bcore\b"
                            r"|\bunderlying\b|\bpro[\s-]?forma\b|\badjusting\b|\bbefore\s+(?:the\s+)?(?:[\w-]+\s+){0,3}?"
                            r"(?:charges?|items?|costs?|expenses?|impairments?|gains?|losses?|adjustments?|amortization"
                            r"|restructuring|special)\b|\bnet\s+of\b", re.I)),
    ("organic", re.compile(r"\borganic(?:ally)?\b", re.I)),
    ("comparable", re.compile(r"\bcomparable\b(?!\s+(?:fiscal\s+)?(?:period|quarter|year|prior))|\bcomps?\b"
                              r"|\bsame[\s-]+stores?\b|\blike[\s-]+for[\s-]+like\b", re.I)),
    ("systemwide", re.compile(r"\bsystem[\s-]?wide\b", re.I)),
    ("ttm", re.compile(r"\btrailing[\s-]+(?:twelve|12)[\s-]+months?\b|\b(?:last|past)[\s-]+(?:twelve|12)[\s-]+months?\b"
                       r"|\bttm\b|\bltm\b", re.I)),
    # An explicit GAAP or as-reported label ("non-GAAP" is already read above as adjusted). It never has to match a
    # figure that says nothing, but it can't stand beside an adjusted one: "Core Earnings (GAAP) of $0.72".
    ("gaap", re.compile(r"(?<![\w-])gaap\b|\bas[\s-]+reported\b", re.I)),
)
_BASIS_PHRASE_RE = re.compile(r"\b(?:on|in)\s+an?\s+(?P<b>[\w\s/-]{1,40}?)\s+basis\b", re.I)
_LEADING_EXCLUDING_RE = re.compile(r"^\W*(?:when\s+)?(?:excluding|exclusive\s+of|before)\b[^,]*,", re.I)
_YOY_RE = re.compile(r"year[\s-]+over[\s-]+year|\byoy\b|\by/y\b|\b(?:a\s+)?year\s+(?:ago|earlier)\b|\bprior[\s-]+year\b"
                     r"|\blast\s+(?:fiscal\s+)?year\b|\bprevious\s+year\b|\bcorresponding\s+period\b"
                     r"|\bsame\s+(?:period|quarter)\s+(?:of\s+)?(?:last|the\s+prior|a|the\s+previous)\s+(?:fiscal\s+)?year\b",
                     re.I)
_QOQ_RE = re.compile(r"quarter[\s-]+over[\s-]+quarter|\bqoq\b|\bq/q\b|\bsequential(?:ly)?\b"
                     r"|\b(?:previous|prior|preceding|last)\s+quarter\b", re.I)
_PRIOR_AFTER_RE = re.compile(r"\b(?:last|prior|previous|a)\s+(?:fiscal\s+)?year\b|\byear[\s-]+(?:ago|earlier)\b"
                             r"|\bprior[\s-]+year\b|\b(?:prior|previous|last|preceding)\s+quarter\b"
                             r"|\bin\s+(?:the\s+)?(?:prior|previous|comparable|year-ago)\s+(?:year|period|quarter)\b",
                             re.I)
_PRIOR_SUBJECT_RE = re.compile(r"\b(?:last|prior|previous)[\s-]+(?:fiscal\s+)?year(?:'s|’s)?\b|\ba\s+year\s+ago\b"
                               r"|\byear[\s-]+ago\b|\bprior[\s-]+year\b|\b(?:last|prior|previous)\s+quarter(?:'s|’s)?\b",
                               re.I)
_PRIOR_INTRO_RE = re.compile(r"(?:from|compared\s+(?:to|with)|versus|vs\.?|than|over|since|against)\s+(?:the\s+)?$",
                             re.I)
_BASE_GAP_RE = re.compile(r"(?:\bfrom|\bcompared\s+(?:to|with)|\bversus|\bvs\.?|\bagainst|\bthan|\brelative\s+to)$",
                          re.I)
_CONNECTOR_RE = re.compile(r"\bcompared\s+(?:to|with)\b|\bversus\b|\bvs\.?(?=\s)|\bagainst\b|\brelative\s+to\b", re.I)


def _breaks(text: str) -> list[tuple[int, int]]:
    """Clause boundaries as (start, end): punctuation, parentheses, connectors, and "and" before a new verb. Neither a
    decimal point nor the comma of "2,998" breaks a clause, nor "by" right before a figure ("increased by 5%")."""
    out = []
    for m in _BREAK_RE.finditer(text):
        s, e = m.span()
        if m.group() == "," and 0 < s < len(text) - 1 and text[s - 1].isdigit() and text[s + 1].isdigit():
            continue
        if m.group().lower() == "by" and re.match(r"\s*(?:US\$|\$)?\s?\d", text[e:]):
            continue
        out.append((s, e))
    return out


def _parens(text: str) -> list[tuple[int, int]]:
    """Every parenthesis as (offset of "(", offset after ")" or the end)."""
    out, opens = [], []
    for i, ch in enumerate(text):
        if ch == "(":
            opens.append(i)
        elif ch == ")" and opens:
            out.append((opens.pop(), i + 1))
    out.extend((o, len(text)) for o in opens)
    return sorted(out)


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for s, e in spans:
        for i in range(max(0, s), min(len(chars), e)):
            chars[i] = " "
    return "".join(chars)


@dataclass
class _Figure:
    """A number in its sentence, read the same way on both sides."""

    measure: set[str]           # what it measures: its clause's subject, walked back past clauses that name nothing
    zone: set[str]              # the words around it: from that subject to the next figure (plus an inherited lead)
    scope: set[str]             # the words that narrow it: its measure, plus a geography after it in its clause
    qualifiers: frozenset[str]
    direction: Optional[str]    # "up" | "down"
    kind: Optional[str]         # "change" | "level"
    bound: str                  # "" | "approx" | "over" | "atleast" | "under" | "atmost" | "nearly"
    comparison: frozenset[str]  # {"YoY"} | {"QoQ"} | {}
    base: bool                  # a prior-period comparison base ("from $84.4 billion last year")
    forward: bool               # guidance wording in its sentence or lead
    measure_start: int
    inherited: bool = False     # its measure is its bullet's lead's


def _qualifiers(text: str) -> set[str]:
    if re.fullmatch(r"\s*(?:as[\s-]+)?reported\s*", text, re.I):
        return {"gaap"}  # "on a reported basis"
    found, rest = set(), text
    for name, rx in _QUALIFIER_RES:
        if rx.search(rest):
            found.add(name)
            rest = rx.sub(lambda m: " " * len(m.group()), rest)  # "excluding foreign exchange" is cc, not adjusted
    return found


def _bound(before: str) -> str:
    text = _v.strip_bound_currency_prefix(" ".join(before.lower().split())[-80:])
    for kind, inclusive, rx in _v._HEDGES:
        if rx.search(text):
            return {("lower", False): "over", ("lower", True): "atleast", ("upper", False): "under",
                    ("upper", True): "atmost", ("nearly", True): "nearly"}.get((kind, inclusive), "approx")
    return ""


def _direction(text: str) -> Optional[str]:
    """The direction of the last direction word in text."""
    last: Optional[tuple[int, str]] = None
    for rx, name in ((_UP_RE, "up"), (_DOWN_RE, "down")):
        for m in rx.finditer(text):
            if last is None or m.start() > last[0]:
                last = (m.start(), name)
    return last[1] if last else None


# Where the words right after a figure stop describing it: "23% in constant currency" is the figure's own qualifier,
# "5% on organic sales growth of 4%" names a cause
_AFTER_STOP_RE = re.compile(r"[,;:()]|\b(?:on|driven|due|reflecting|led|from|primarily|mainly|largely|including|with|as|"
                            r"compared|versus|vs|to|and|or|while|but|which|that|despite|after|before|offset)\b", re.I)
# A clause after a figure's comma that only qualifies it: "was $96.2 billion, excluding acquisitions"
_TRAILING_QUALIFIER_RE = re.compile(
    r"\s*,\s*(?P<phrase>(?:excluding|exclusive\s+of|before|net\s+of|adjusted\s+for|in\s+constant\s+currenc|at\s+constant\s+"
    r"currenc|on\s+an?\s+[\w\s/-]{1,40}?\s+basis|ex[\s-]|including\s+the\s+impact|after\s+adjusting)[^,;:()]*)", re.I)
_FOOTNOTE_RE = re.compile(r"(?<=[A-Za-z]{3})\d{1,2}(?=[\s,.;:)”\"]|$)")
# What may separate a figure from the one it restates: links, basis and comparison wording, the earlier figure's unit
_RESTATEMENT_NOISE_RE = re.compile(
    r"\b(?:on|in)\s+an?\s+[\w\s/-]{1,40}?\s+basis\b|\bas[\s-]+reported\b|\bin\s+constant[\s-]+currenc(?:y|ies)\b"
    r"|\bconstant[\s-]+currenc(?:y|ies)\b|\borganic(?:ally)?\b|\breported\b|year[\s-]+over[\s-]+year|\byoy\b|\by/y\b"
    r"|\bsequential(?:ly)?\b|\bper\s+(?:diluted\s+|basic\s+|common\s+)?share\b|\bor\b|\band\b|[,;()\-–—]"
    r"|\b(?:growth|increase|decrease|decline|gain|rise|drop)\b|\b(?:in|for)\s+the\s+(?:quarter|period)\b", re.I)
_LOCAL_BREAK_RE = re.compile(r"[,;:—–]|\b(?:and|while|whereas|but|with|including)\b", re.I)


def _local(gap: str) -> str:
    """The words of a gap that belong to the figure after it: those after its last clause break."""
    cuts = [m.end() for m in _LOCAL_BREAK_RE.finditer(gap)]
    return gap[cuts[-1]:] if cuts else gap


def _family(cls: str) -> str:
    return "money" if cls == "ps" or cls.startswith("usd") else "count" if cls.startswith("n:") else (
        "points" if cls in ("bps", "pts") else cls)


def _change(text: str, numbers: list[_Num], a: int, b: int, measure_start: int, neg: bool, cls: str = "",
            depth: int = 0) -> tuple[Optional[str], Optional[str]]:
    """(direction, kind) of the figure at text[a:b]: "up"/"down" and "change"/"level", None where its words don't say.
    A figure that only restates the one before it ("$401 million (13.7 percent)", "$18 million, or 5%", "18 percent
    year over year as reported; 16 percent on a constant currency basis") takes that figure's; a level takes the
    direction of its clause ("decreased 18% to $6.2 billion")."""
    previous = [n for n in numbers if n.end <= a]
    prev = previous[-1] if previous else None
    raw = text[prev.end:a] if prev else text[measure_start:a]
    if prev is not None and depth < 3 and not re.search(r"[A-Za-z]", _RESTATEMENT_NOISE_RE.sub(" ", raw)):
        # It restates the nearest earlier figure of its own kind ("11% ... to $1.344 billion on a reported basis and
        # 11% ... organic"), else the one right before it, though a percent never restates a dollar level
        same = [n for n in previous if cls and _family(n.cls) == _family(cls)]
        target = same[-1] if same else prev
        found = _change(text, numbers, target.start, target.end, measure_start, target.neg, target.cls, depth + 1)
        if target is prev and not same and _family(cls) == "pct" and found[1] == "level":
            return None, None
        return found
    local = _local(raw)
    plain = _HEDGE_TAIL_RE.sub("", _v.strip_bound_currency_prefix(local).rstrip(" ,"))
    direction = _direction(local)
    after = _CHANGE_AFTER_RE.match(text, b)
    if _CHANGE_NOUN_RE.search(plain):
        kind: Optional[str] = "change"  # "an increase of 6%", "with growth accelerating to 30%"
    elif _LEVEL_MARKER_RE.search(plain):
        kind = "level"  # "expanded its warehouse network to 939", "increased 11.2 percent, to $93.9 billion"
    elif direction is not None:
        kind = "change"  # "increased 19%", "(up 22% in constant currency)"
    elif after:
        kind = "change"  # "1.5% growth", "8% lower volumes", "1.6% Same Store Core Revenues growth"
        direction = "down" if re.match(r"decl|decr|drop|fall|contr|lower|less|fewer", after.group("w"), re.I) else "up"
    else:
        kind = None
    if kind == "level" and direction is None:
        direction = _direction(text[measure_start:a])  # "HIV product sales increased 18% to $6.2 billion"
    if direction is None and kind != "level":
        # "expanding core operating margin 220 basis points, core gross margin 120 basis points": one verb for a list
        direction = _direction(text[:a])
    if neg and direction is None:
        direction = "down"
    return direction, kind


_AFTER_FIGURE_RE = re.compile(r"[\s)]*(?:(?:year[\s-]+over[\s-]+year|yoy|y/y|sequentially|organically|as\s+reported)"
                              r"[\s,)]*)*$", re.I)


def _basis_phrase_qualifiers(text: str, numbers: list[_Num], a: int, b: int) -> set[str]:
    """Qualifiers of the basis phrases ("on a non-GAAP basis") that cover the figure at text[a:b]. A phrase right after a
    figure covers the figures back to the previous phrase ("and was $36.4 billion and increased 18% (up 19% ...) on a
    non-GAAP basis"), except one restated with "or" ("8% ..., or approximately 7% on a constant currency basis"); one
    that opens its clause covers the figures after it, to the next phrase ("..., and on a non-GAAP basis was $4.13");
    one inside a subject covers the next figure ("revenue on a pro forma basis for the quarter was $96.2 billion")."""
    phrases = list(_BASIS_PHRASE_RE.finditer(text))
    parens = _parens(text)
    q: set[str] = set()
    for i, m in enumerate(phrases):
        before = text[:m.start()]
        figure_before = [n for n in numbers if n.end <= m.start()]
        gap_from = figure_before[-1].end if figure_before else 0
        for o, c in parens:  # "18% (up 19% in constant currency) on a non-GAAP basis": from the parenthesis' end
            if o < gap_from < c <= m.start():
                gap_from = c
        if figure_before and _AFTER_FIGURE_RE.fullmatch(before[gap_from:]):
            lower = phrases[i - 1].end() if i > 0 else 0
            if lower <= a and b <= m.start() and not re.search(r"\bor\b", text[b:m.start()], re.I):
                q |= _qualifiers(m.group("b"))
        elif not before.strip() or before.rstrip()[-1] in ",;:(" or re.search(r"\b(?:and|while|but|whereas)\s*$",
                                                                               before, re.I):
            nxt = phrases[i + 1].start() if i + 1 < len(phrases) else len(text)
            if m.end() <= a < nxt:
                q |= _qualifiers(m.group("b"))
        else:
            after = [n for n in numbers if n.start >= m.end()]
            if after and a in (after[0].start, after[0].num):
                q |= _qualifiers(m.group("b"))
    return q


def _figure(text: str, a: int, b: int, company: frozenset[str], *, numbers: list[_Num], count: bool,
            lead: str = "", neg: bool = False, cls: str = "") -> _Figure:
    """Read the figure at text[a:b] (a at its currency sign or first digit, b after its unit)."""
    text = _FOOTNOTE_RE.sub(lambda m: " " * len(m.group()), text)  # "organic1 basis", "Net income2": offsets kept
    parens = _parens(text)
    inside = [p for p in parens if p[0] < a < p[1]]
    own = inside[-1] if inside else None  # the innermost parenthesis around the figure
    closed = [p for p in parens if p[1] <= a and not (own and own[0] <= p[0])]  # parentheses of other figures
    breaks = _breaks(text)
    # The figure's measure: the nearest clause before it that names something, skipping parentheses it isn't in
    cuts = sorted({0, *[e for _, e in breaks if e <= a]}, reverse=True)
    measure: set[str] = set()
    measure_start = a
    for c in cuts:
        stop = min([s for s, _ in breaks if s >= c] + [a])
        stop = min(stop, a)
        if stop <= c or any(p[0] < c and stop <= p[1] for p in closed):
            continue
        words = _content(_NUMBER_RE.sub(" ", text[c:stop]), company)
        if words:
            measure, measure_start = words, c
            break
    following = [n for n in numbers if n.start >= b]
    next_start = following[0].start if following else len(text)
    clause_end = min([s for s, _ in breaks if s >= b] + [len(text)])
    after_clause = text[b:min(clause_end, next_start)]
    count_noun: set[str] = set()
    if count:  # "939 warehouses", "70 loyalty markets", "84.1 million paid members"
        noun = re.match(r"\s*((?:[A-Za-z_][\w'’&-]*\s*){1,4})", after_clause)
        if noun:
            head = re.split(r"\b(?:in|on|at|of|for|from|to|as|per|by|and|or|was|were|is|are|that|which|who|"
                            r"increased|decreased|grew|rose|fell|up|down)\b", noun.group(1), maxsplit=1, flags=re.I)[0]
            count_noun = _content(head, company)
    previous = [n for n in numbers if n.end <= a]
    gap_start = max(previous[-1].end if previous else 0, own[0] + 1 if own else 0)
    gap = text[gap_start:a]
    # A bullet item that names no measure of its own takes its lead's ("U.S. increased 0.8%")
    lead_words: set[str] = set()
    lead_q: set[str] = set()
    inherited = False
    if lead and not ((measure | count_noun) & _MEASURE_NOUNS):
        lead_nums = _numbers(lead)
        head = lead[:lead_nums[0].start] if lead_nums else lead
        head = re.split(r"\b(?:was|were|is|are|increas\w*|decreas\w*|grew|rose|fell|declin\w*)\b", head,
                        maxsplit=1, flags=re.I)[0]
        lead_measure = _content(head, company)
        if lead_measure & _MEASURE_NOUNS:
            inherited = True
            measure = measure | lead_measure
            lead_words = _content(lead, company)
            lead_q = _qualifiers(head)
    # Qualifiers bound to this figure: its own clause, its parenthesis or one right after it, the words right after it
    # or a qualifying clause after its comma, a basis phrase that covers it, a leading qualifying clause, its lead
    basis_spans = [m.span() for m in _BASIS_PHRASE_RE.finditer(text)]
    own_words = _blank_spans(text[:a], [*closed, *basis_spans])[measure_start:]  # basis phrases: _basis_phrase_qualifiers
    right_after = _AFTER_STOP_RE.split(text[b:min(clause_end, next_start)], maxsplit=1)[0]
    q: set[str] = set(lead_q) | _qualifiers(own_words) | _qualifiers(right_after)
    if own:
        q |= _qualifiers(_blank_spans(text, basis_spans)[own[0]:own[1]])
    tail = text[b:next_start]
    after_paren = re.match(r"\s*\(([^()]*)\)", tail)
    if after_paren:  # "$96.2 billion (excluding acquisitions)"
        q |= _qualifiers(after_paren.group(1))
        tail = tail[after_paren.end():]
    trailing = _TRAILING_QUALIFIER_RE.match(tail)
    if trailing:  # "was $96.2 billion, excluding acquisitions."
        q |= _qualifiers(trailing.group("phrase")) or {"adjusted"}
    leading_clause = re.match(r"\W*([^,;:()]{1,100}),", text)
    if leading_clause and leading_clause.end() <= a and not _numbers(leading_clause.group(1)):
        q |= _qualifiers(_blank_spans(leading_clause.group(1), []))  # "In constant currency, revenue was ..."
    q |= _basis_phrase_qualifiers(text, numbers, a, b)
    leading_excluding = _LEADING_EXCLUDING_RE.match(text)
    if leading_excluding and leading_excluding.end() <= a:
        q |= _qualifiers(leading_excluding.group()) or {"adjusted"}
    # Direction and kind
    plain_gap = _HEDGE_TAIL_RE.sub("", _v.strip_bound_currency_prefix(_local(gap)).rstrip(" ,"))
    own_cls = next((n.cls for n in numbers if n.start <= a < n.end or n.num == a), "")
    direction, kind = _change(text, numbers, a, b, measure_start, neg, cls or own_cls)
    # Comparison: the figure's own words first, else its sentence's (and lead's) when they name only one kind
    own_cmp = gap + " " + text[b:next_start]
    comparison = {name for name, rx in (("YoY", _YOY_RE), ("QoQ", _QOQ_RE)) if rx.search(own_cmp)}
    if not comparison:
        whole = {name for name, rx in (("YoY", _YOY_RE), ("QoQ", _QOQ_RE)) if rx.search(f"{text} {lead}")}
        comparison = whole if len(whole) == 1 else set()
    # A prior-period comparison base is not the current figure
    base = bool(_BASE_GAP_RE.search(plain_gap))
    if not base and kind != "change":
        if _CONNECTOR_RE.search(text[:a]) and not re.search(r"\bto$", plain_gap, re.I):
            base = True  # "..., compared to $2.610 billion, $5.87 per diluted share, last year"
        elif _PRIOR_AFTER_RE.search(text[b:next_start].split(";")[0]):
            base = True  # "$5.87 per diluted share, last year"
    for subject in _PRIOR_SUBJECT_RE.finditer(text[:a]):  # "Last year's results included ...", "prior year charges"
        if not _PRIOR_INTRO_RE.search(text[:subject.start()]):
            base = True
    geography = {w for w in _words(after_clause) if w in _GEOGRAPHY}
    zone_text = text[measure_start:next_start]
    zone = _content(zone_text, company) | count_noun | lead_words
    # Guidance wording anywhere in the sentence or its lead ("... the financial results and business outlook, ...")
    forward = any(w in _FORWARD for w in _words(f"{text} {lead}") if w not in company)
    return _Figure(measure=measure | count_noun, zone=zone, scope=measure | count_noun | geography,
                   qualifiers=frozenset(q), direction=direction, kind=kind, bound=_bound(text[gap_start:a]),
                   comparison=frozenset(comparison), base=base, forward=forward, measure_start=measure_start,
                   inherited=inherited)


# ---------------------------------------------------------------------------
# Sentences a release states its quarter's figures in
# ---------------------------------------------------------------------------
_SENTENCE_RE = re.compile(r"(?<=[.!?])[\"”’*]*\s+(?=[\"“‘(]?[A-Z*])")
_OTHER_SPAN_RE = re.compile(
    r"\b(?:six|nine|6|9)[\s-]+months?\b|(?<!trailing\s)(?<!last\s)(?<!past\s)\b(?:twelve|12)[\s-]+months?\b"
    r"|\b(?:first|second)\s+half\b|\bhalf[\s-]+year\b|\byear[\s-]+to[\s-]+date\b|\bytd\b"
    r"|\bfull[\s-]+(?:fiscal[\s-]+)?year\b|\bfiscal\s+year\b(?!\s+\d)|\bannual(?:ly)?\b|\b5[23][\s-]+weeks?\b"
    r"|\b(?:24|28|36|40)[\s-]+weeks?\b|\bfor\s+the\s+year\b|\byear\s+ended\b|\brespectively\b", re.I)
_QUARTER_WORD_RE = re.compile(r"\b(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:fiscal[\s-]+)?quarter\b"
                              r"|(?<![\w-])(?:f?q(?P<q>[1-4])|(?P<nq>[1-4])q)(?![\w-])", re.I)
_FISCAL_YEAR_RE = re.compile(r"\bfiscal\s+(?:year\s+)?(?P<y>\d{4})\b|(?<![A-Za-z])FY\s*'?(?P<fy>\d{4}|\d{2})(?!\d)",
                             re.I)
_YEAR_RE = re.compile(r"(?<![\d$,.])(?P<y>(?:19|20)\d{2})(?![\d,]|\s?%)")
_ENDED_RE = re.compile(rf"\b(?:ended|ending)\s+(?P<date>{_MONTH}\.?\s+\d{{1,2}},?\s+\d{{4}})", re.I)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
# A comparison base names the period a change is measured from, not another period the figures are for: "results for
# the quarter ended September 30, 2026, as compared to the corresponding period of last fiscal year:"
_COMPARISON_BASE_RE = re.compile(r"\b(?:as\s+)?compared\s+(?:to|with)\b[^,;:]*|\b(?:from|versus|vs\.?|over|against)\s+"
                                 r"(?:the\s+)?(?:same|corresponding|comparable|prior|previous|last|year[\s-]ago)\b[^,;:]*",
                                 re.I)
_NOT_AN_END = frozenset("inc corp co ltd vs approx no jan feb mar apr jun jul aug sep sept oct nov dec".split())


_NAMED_BASE_RE = re.compile(
    r"\b(?:compared\s+(?:to|with)|versus|vs\.?|from|over|against|relative\s+to)\s+(?:the\s+)?(?:same\s+)?"
    r"(?:(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:fiscal[\s-]+)?quarter|q(?P<q>[1-4]))\b"
    r"(?:\s+(?:of\s+)?(?:(?P<fiscal>fiscal\s+(?:year\s+)?|fy\s*)?(?P<y>\d{4})|(?P<last>last|the\s+prior|the\s+previous|a"
    r"\s+year\s+ago)))?", re.I)


def named_base_comparisons(text: str, fiscal_year: int, fiscal_period: str, end_year: Optional[int]) -> set[str]:
    """The comparisons that bases naming a quarter imply, for figures of fiscal_year/fiscal_period: "compared to first
    quarter of 2025" in a Q1 2026 release is year over year, "the fourth quarter of 2025" sequential, any other quarter
    "other"."""
    out = set()
    q = int(fiscal_period[1])
    for m in _NAMED_BASE_RE.finditer(text):
        n = _ORDINALS.get((m.group("ord") or "").lower()) or int(m.group("q"))
        if m.group("y"):
            y = int(m.group("y"))
            years = {fiscal_year} if m.group("fiscal") else {fiscal_year} | ({end_year} if end_year else set())
            if n == q and y in {x - 1 for x in years}:
                out.add("YoY")
            elif (n == q - 1 and y in years) or (q == 1 and n == 4 and y in {x - 1 for x in years}):
                out.add("QoQ")
            else:
                out.add("other")
        elif m.group("last"):
            out.add("YoY" if n == q else "other")
        else:
            out.add("QoQ" if n == q - 1 or (q == 1 and n == 4) else "YoY" if n == q else "other")
    return out


def _sentences(text: str) -> list[tuple[int, int]]:
    """Sentence offsets within a unit's text (a bullet item may hold several). "U.S." and "Inc." end none."""
    out, start = [], 0
    for m in _SENTENCE_RE.finditer(text):
        word = re.search(r"([A-Za-z][A-Za-z.]*)\.[\"”’*]*$", text[start:m.start()])
        if word and (len(word.group(1)) <= 2 or "." in word.group(1) or word.group(1).lower() in _NOT_AN_END):
            continue
        out.append((start, m.start()))
        start = m.end()
    out.append((start, len(text)))
    return [(s, e) for s, e in out if text[s:e].strip()]


# Round 10a: a year named without a quarter word is the year's, not the release's quarter ("For fiscal 2026, free cash
# flow was $893 million", "in 2026"), in any quarter's release. Dates ("ended April 30, 2026", "January 2025") are no
# year of their own; "fourth quarter of fiscal 2026", "Q4 2026" and a bare quarter sentence keep naming the quarter, and a
# comparison base naming the prior year is read as before (_COMPARISON_BASE_RE).
_DATE_WORDS_RE = re.compile(rf"\b{_MONTH}\.?\s+(?:\d{{1,2}},?\s+)?\d{{4}}\b", re.I)
_ANY_QUARTER_RE = re.compile(r"\bquarter(?:s|ly)?\b|\bthree[\s-]+months?\b|\b1[2-7][\s-]+weeks?\b"
                             r"|(?<![\w-])(?:f?q[1-4]|[1-4]q)(?![\w-])", re.I)
_YEAR_WORD_RE = re.compile(r"(?<![\d$,.])(?:19|20)\d{2}(?!\d|,\d|\s?%)")  # "In 2026, ..." too, never "2,026"


def _names_a_year_only(text: str) -> bool:
    """Whether text names a fiscal or calendar year and no quarter (above): its figures are the year's."""
    undated = _DATE_WORDS_RE.sub(" ", _COMPARISON_BASE_RE.sub(" ", text))
    return (bool(_FISCAL_YEAR_RE.search(undated) or _YEAR_WORD_RE.search(undated))
            and not _ANY_QUARTER_RE.search(undated))


def _names_only_its_quarter(text: str, unit: Unit) -> bool:
    """Whether a sentence (or lead) of the release names no period but the release's own quarter (a comparison base
    aside)."""
    return not _names_a_year_only(text) and _names_no_other_period(text, unit)  # round 10a: a year is not the quarter


def _names_no_other_period(text: str, unit: Unit) -> bool:
    """_names_only_its_quarter as it read before round 10a, which took the release's fiscal year for its quarter: what
    release_statement still reads as another figure of the release, which can conflict with a match."""
    text = _COMPARISON_BASE_RE.sub(" ", text)
    if _OTHER_SPAN_RE.search(text):
        return False
    q = int(unit.fiscal_period[1])
    for m in _QUARTER_WORD_RE.finditer(text):
        n = _ORDINALS.get((m.group("ord") or "").lower()) or int(m.group("q") or m.group("nq"))
        if n != q:
            return False
    for m in _FISCAL_YEAR_RE.finditer(text):
        y = m.group("y") or m.group("fy")
        if (int(y) if len(y) == 4 else 2000 + int(y)) != unit.fiscal_year:
            return False
    allowed = {unit.fiscal_year} | {d.year for d in (unit.period_start, unit.period_end) if d is not None}
    if any(int(m.group("y")) not in allowed for m in _YEAR_RE.finditer(text)):
        return False
    for m in _ENDED_RE.finditer(text):
        ended = periods.parse(m.group("date")).end
        if ended is None or unit.period_end is None or abs((ended - unit.period_end).days) > 7:
            return False
    return True


# ---------------------------------------------------------------------------
# The match
# ---------------------------------------------------------------------------
class _ClaimFigure(NamedTuple):
    figure: _Figure
    words: set[str]   # every content word of the claim around its figure: what a sentence's scope must stay within
    digits: str
    cls: str
    companions: frozenset[str] = frozenset()  # digits of the other current figures of the same measure in its clause


def claim_figure(claim: Claim, line: Optional[_v.DraftLine], company: frozenset[str]) -> Optional[_ClaimFigure]:
    """The claim's figure as its own draft sentence reads it (its span without the draft); None when it can't be read."""
    number = _claim_number(claim)
    if number is None:
        return None
    digits, cls, neg, figure_at = number
    if line is not None:
        text, a, b = line.text, line.start, line.end
    else:
        pos = _v._value_pos(claim)
        if pos is None:
            return None
        text, a, b = claim.span_text, pos, pos + len(claim.value_text)
    a += figure_at  # "nearly 220 million": the hedge is read before the figure, as in a release
    nums = _numbers(text)
    fig = _figure(text, a, b, company, numbers=nums, count=cls.startswith("n:"), neg=neg, cls=cls)
    fig.measure |= _content(claim.metric_text or "", company) | _content(claim.segment or "", company)
    q = set(fig.qualifiers) | _qualifiers(claim.metric_text or "")
    if claim.basis == "non-GAAP":
        q.add("adjusted")
    fig.qualifiers = frozenset(q)
    if not fig.comparison and fig.kind == "change" and claim.comparison in ("YoY", "QoQ", "sequential"):
        fig.comparison = frozenset({"QoQ" if claim.comparison in ("QoQ", "sequential") else "YoY"})
    fig.forward = fig.forward or any(w in _FORWARD for w in _words(claim.metric_text or "") if w not in company)
    clause_end = min([s for s, _ in _breaks(text) if s >= b] + [len(text)])
    words = fig.measure | fig.zone | _content(text[fig.measure_start:clause_end], company)
    companions = set()
    if not fig.base:  # a comparison base's companions are another period's figures
        for n in nums:
            if n.start < a <= n.end or a <= n.start < b:
                continue
            other = _figure(text, n.start, n.end, company, numbers=nums, count=n.cls.startswith("n:"), neg=n.neg)
            if other.measure_start == fig.measure_start and not other.base:
                companions.add(n.digits)
    return _ClaimFigure(fig, words, digits, cls, frozenset(companions))


def _class_fits(claim_cls: str, sent_cls: str, claim_words: set[str], zone: set[str]) -> bool:
    if claim_cls == sent_cls:
        return True
    if claim_cls == "ps" and sent_cls == "usd:units":
        return bool({"eps", "per_share"} & zone)  # "Diluted earnings per share was $4.92"
    if claim_cls == "usd:units" and sent_cls == "ps":
        return bool({"eps", "per_share"} & claim_words)  # "EPS included a one-time benefit of $0.15"
    return False


def _agree(c: _Figure, s: _Figure, claim_words: set[str]) -> bool:
    """Whether the claim's figure and the sentence's figure state the same thing (see the module doc)."""
    if s.base or s.forward or c.forward or not c.measure or not s.measure:
        return False
    # The claim says no more about the figure than the sentence does; a claim's "EPS" may be the sentence's per-share
    # amount ("a non-recurring benefit of $0.15 per diluted share"), never the other way round
    zone = s.zone | ({"eps"} if "per_share" in s.zone else set())
    if not c.measure <= zone:
        return False
    if not s.scope <= claim_words:
        return False  # the sentence's figure is narrower: a segment, a geography, a sub-measure
    if c.qualifiers - {"gaap"} != s.qualifiers - {"gaap"}:
        return False
    if {"gaap", "adjusted"} <= (c.qualifiers | s.qualifiers):
        return False  # an explicit GAAP label against an adjusted figure, on either side
    if c.direction and s.direction and c.direction != s.direction:
        return False
    if c.kind and s.kind and c.kind != s.kind:
        return False
    if "change" in (c.kind, s.kind) and not _comparisons_fit(c.comparison, s.comparison):
        return False
    return c.bound == s.bound or (c.bound == "approx" and s.bound == "")


def _comparisons_fit(claim: frozenset[str], sentence: frozenset[str]) -> bool:
    """A change compares with the year-ago quarter unless it says otherwise: a sequential change on either side needs
    one on the other, a stated comparison can't meet an unclear one, and a base two quarters back fits nothing."""
    if "other" in claim or "other" in sentence:
        return False
    if ("QoQ" in claim) != ("QoQ" in sentence):
        return False
    return not (claim and len(sentence) > 1)


def _quote(sentence: str, num: _Num, start: int, lead: str = "") -> str:
    """The sentence as the receipt quotes it: whole up to 320 characters, else the part around the figure. A bullet item
    that takes its lead's measure is quoted after its lead ("Global comparable sales increased 1.3%: U.S. increased
    0.8%")."""
    text = " ".join(sentence.split())
    if len(text) > 320:
        lo = max(0, min(start, num.start - 160))
        hi = min(len(sentence), num.end + 140)
        text = ("…" if lo > 0 else "") + " ".join(sentence[lo:hi].split()) + ("…" if hi < len(sentence) else "")
    return f"{lead} {text}" if lead else text


def release_statement(claim: Claim, store: EvidenceStore, entity: tuple[int, str],
                      period: Optional[periods.ResolvedPeriod], as_of: Optional[datetime],
                      line: Optional[_v.DraftLine], *, skip_names: bool = False) -> Optional[Statement]:
    """The release sentence that states the claim's figure for its resolved quarter, or None (see the module doc).
    entity is the claim's resolved (cik, ticker), period its resolved fiscal quarter, line its draft sentence.
    skip_names: read a sentence's figures past parentheses without digits, such as a drug's generic name ("Trodelvy®
    (sacituzumab govitecan-hziy) sales increased 42% to $506 million"): only table_conflict, whose outcome is "?", reads
    sentences so (round 10a)."""
    if period is None or period.fiscal_period not in periods.QUARTERS:
        return None
    cik, ticker = int(entity[0]), str(entity[1])
    company = _company_words(store, cik, ticker, claim)
    read = claim_figure(claim, line, company)
    if read is None or read.figure.forward or not read.figure.measure:
        return None
    if read.figure.kind == "change":
        text = line.text if line is not None else claim.span_text
        read.figure.comparison = read.figure.comparison | frozenset(named_base_comparisons(
            text, period.fiscal_year, period.fiscal_period, period.period_end.year if period.period_end else None))
    releases: dict[str, list[_Stated]] = {}
    chosen = [unit for unit in units(store, ticker)
              if unit.cik == cik and (unit.fiscal_year, unit.fiscal_period) == (period.fiscal_year, period.fiscal_period)
              and (as_of is None or unit.accepted_at <= as_of)
              and not (period.period_end is not None and unit.period_end is not None
                       and abs((unit.period_end - period.period_end).days) > 7)]
    sections: dict[tuple[str, str], set[int]] = {}
    for unit in chosen:
        sections.setdefault((unit.accession, unit.exhibit), set())
    for key in sections:
        sections[key] = _year_sections([u for u in chosen if (u.accession, u.exhibit) == key])
    for unit in chosen:
        if unit.lead and not _names_no_other_period(unit.lead, unit):
            continue
        year_section = unit.seq in sections[(unit.accession, unit.exhibit)] or (
            bool(unit.lead) and _names_a_year_only(unit.lead))  # a bullet under "Fiscal 2026 highlights:"
        releases.setdefault(unit.accession, []).extend(_stated(unit, read, company, skip_names=skip_names,
                                                               year_section=year_section))
    hits: list[tuple[tuple[Any, ...], Statement]] = []
    for stated in releases.values():
        matches = [x for x in stated if not x.year and x.num.digits == read.digits
                   and _agree(read.figure, x.figure, read.words) and _companions_stated(read, x.sentence)]
        if not matches:
            continue
        m = min(matches, key=lambda x: (x.unit.exhibit, x.unit.seq, x.s0))
        if any(x.num.digits != read.digits and _same_statement(m.figure, x.figure) for x in stated):
            continue  # the release states this measure at another number too: a segment or a year to date its heading
            # names, which the indexed sentence doesn't show ("Recurring revenues increased $60 million, or 8%")
        quote = _quote(m.sentence, m.num, m.figure.measure_start, m.unit.lead if m.figure.inherited else "")
        hits.append(((m.unit.accepted_at, m.unit.exhibit, m.unit.seq, m.s0), Statement(m.unit, quote,
                                                                                     m.figure.qualifiers)))
    return min(hits, key=lambda h: h[0])[1] if hits else None


class _Stated(NamedTuple):
    unit: Unit
    s0: int
    sentence: str
    num: _Num
    figure: _Figure
    year: bool = False  # round 10a: the year's figure (_year_figure): it may conflict with a match, never be one


# Round 10a: a release's section heading that names a year and no quarter ("Fiscal 2026 Financial Stewardship") makes
# the sentences under it the year's, as long as the index shows every unit between them: a heading without digits is
# not indexed, so after a gap another section ("Fourth Quarter Results") may have begun. Only the body's headings count:
# the title block before the dateline ("Raises Full-Year 2026 Adjusted EBITDA Guidance", "updates 2026 outlook", above
# the quarter's headline figures) heads no section, nor does a title ("Brown-Forman Reports Fiscal 2026 Results").
_TITLE_VERB_RE = re.compile(r"\b(?:reports?|reported|announces?|announced|delivers?|posts?|releases?)\b", re.I)
_SENTENCE_END_RE = re.compile(r"[.!?][\"”’)]*\s*$")


def _section_heading(text: str) -> bool:
    words = _WORD_RE.findall(text)
    return (0 < len(words) <= 8 and not re.search(r"[.!?;,]\s*$", text) and not _numbers(text)
            and not _TITLE_VERB_RE.search(text))


def _year_sections(release: list[Unit]) -> set[int]:
    """The seqs of one release's units that sit under a year-only section heading (above)."""
    scoped: set[int] = set()
    year, previous, body = False, None, False
    for unit in sorted(release, key=lambda u: u.seq):
        if previous is not None and unit.seq != previous + 1:
            year = False  # an unindexed unit may be another heading
        if _section_heading(unit.text):
            year = body and _names_a_year_only(unit.text)
        elif year:
            scoped.add(unit.seq)
        if not body and _SENTENCE_END_RE.search(unit.text) and len(_WORD_RE.findall(unit.text)) >= 6:
            body = True  # the dateline's paragraph: the units before it are the release's title block
        previous = unit.seq
    return scoped


def _year_figure(sentence: str, num: _Num, nums: list[_Num]) -> bool:
    """Whether a figure of a sentence that names its quarter is still the year's: the words right after it, or those
    from the previous figure (or the sentence's start) to it, name a year and no quarter ("Fourth-quarter revenue was
    $5.0 billion and fiscal 2026 revenue was $19.8 billion")."""
    breaks = [b for b, _ in _breaks(sentence) if b >= num.end]
    after = [n.start for n in nums if n.start >= num.end]
    tail = sentence[num.end:min(breaks + after + [len(sentence)])]
    before = [n.end for n in nums if n.end <= num.start]
    head = sentence[max(before, default=0):num.start]
    return _names_a_year_only(tail) or _names_a_year_only(head)


_NAME_PAREN_RE = re.compile(r"\([^()\d]*\)")  # a parenthesis without digits: a generic name, a ticker, a mark


def _stated(unit: Unit, read: "_ClaimFigure", company: frozenset[str], *, skip_names: bool = False,
            year_section: bool = False) -> list[_Stated]:
    """Every figure of one unit that is written like the claim's (same unit class), read in its sentence; sentences
    that name another period, forward-looking ones and flattened table rows give none. skip_names: parentheses without
    digits are read as spaces (release_statement). year_section: the unit is under a year-only heading
    (_year_sections), so a figure of a sentence that names no quarter is the year's. A year's figure is kept, marked, as
    a conflict for a match (release_statement), as before round 10a, but is never a match."""
    out = []
    for s0, s1 in _sentences(unit.text):
        sentence = written = unit.text[s0:s1]
        if skip_names:  # offsets kept, so the receipt quotes the sentence as written
            sentence = _NAME_PAREN_RE.sub(lambda m: " " * len(m.group()), sentence)
        if not _names_no_other_period(sentence, unit):
            continue  # another period: neither a match nor a conflict
        year_only = not _names_only_its_quarter(sentence, unit) or (year_section and not _ANY_QUARTER_RE.search(sentence))
        nums = _numbers(sentence)
        if len(nums) >= 6 and len(nums) * 2 > len(_words(sentence)):
            continue  # a flattened table row, not a sentence: "Total Revenues $ 4.4 $ 5.9 $ 8.0 ..."
        lead = unit.lead if s0 == 0 else ""
        bases: Optional[frozenset[str]] = None
        for num in nums:
            if num.cls != read.cls and not _class_fits(read.cls, num.cls, read.words, {"per_share"}):
                continue
            figure = _figure(sentence, num.start, num.end, company, numbers=nums, count=num.cls.startswith("n:"),
                             lead=lead, neg=num.neg)
            if not _class_fits(read.cls, num.cls, read.words, figure.zone):
                continue
            if figure.kind == "change":
                if bases is None:
                    bases = frozenset(named_base_comparisons(f"{sentence} {lead}", unit.fiscal_year, unit.fiscal_period,
                                                             unit.period_end.year if unit.period_end else None))
                figure.comparison = figure.comparison | bases
            out.append(_Stated(unit, s0, written, num, figure, year_only or _year_figure(sentence, num, nums)))
    return out


def _same_statement(a: _Figure, b: _Figure) -> bool:
    """Whether two figures of a release state the same measure the same way (so different numbers conflict)."""
    if b.base or b.forward or a.scope != b.scope or a.qualifiers - {"gaap"} != b.qualifiers - {"gaap"}:
        return False
    if a.kind and b.kind and a.kind != b.kind:
        return False
    return not ("change" in (a.kind, b.kind) and not _comparisons_fit(a.comparison, b.comparison))


def _companions_stated(read: "_ClaimFigure", sentence: str) -> bool:
    """The other current figures the claim's clause states for the same measure ("Operating margin of 11.1%, up 60
    bps") must be in the release sentence too: a release can state the same change for two margins."""
    digits = {n.digits for n in _numbers(sentence)}
    return all(d in digits for d in read.companions)


# ---------------------------------------------------------------------------
# The hook: receipts.verify.verify passes every verdict through fallback()
# ---------------------------------------------------------------------------
_TTM_PERIOD_RE = re.compile(r"\b(?:(?:over|for|in|during)\s+)?(?:the\s+)?(?:trailing|last|past)[\s-]+(?:twelve|12)[\s-]+"
                            r"months?\b|\bttm\b|\bltm\b", re.I)


def claim_period(claim: Claim, store: EvidenceStore, cik: int, context: Optional[str], as_of: Optional[datetime],
                 change: bool) -> Optional[periods.ResolvedPeriod]:
    """The claim's fiscal period as the structured check resolves it (receipts.verify._Check), except that "trailing
    twelve months" is a qualifier here, not a period: its quarter is the one the twelve months end with."""
    ctx = context
    named = _v.context_entities(context, store)
    if named and all(c != cik for c, _, _ in named):
        if periods.parse(claim.period_text).month is None or periods.parse(context).fiscal:
            ctx = None  # another company's context steers no period of this one
    words = _v._comparable_period_text(claim, ctx) or claim.period_text or ""
    words = _TTM_PERIOD_RE.sub(" ", words).strip()
    period_type = None if claim.period_type == "trailing_twelve_months" else claim.period_type
    return periods.resolve_in_calendar(words, periods.calendar(store, cik), context=ctx, period_type=period_type,
                                       comparison="YoY" if change else claim.comparison, as_of=as_of)


def _fact(claim: Claim, st: Statement, digits: str) -> Fact:
    """The receipt: the quoted sentence of the filed release, with its EDGAR URL, accession and acceptance time."""
    u = st.unit
    factor = SCALE_FACTOR.get(claim.scale, 1.0) if claim.unit in ("USD", "shares", "other") else 1.0
    value = float(digits) * factor
    written = len(digits.split(".", 1)[1]) if "." in digits else 0
    return Fact(cik=u.cik, ticker=u.ticker, metric=claim.metric, concept=None,
                basis="non-GAAP" if st.qualifiers else (claim.basis or "GAAP"), segment=claim.segment, value=value,
                unit=claim.unit, period_start=u.period_start, period_end=u.period_end or u.accepted_at.date(),
                fiscal_year=u.fiscal_year, fiscal_period=u.fiscal_period, source=SOURCE, derived_from=[], form="8-K",
                accession=u.accession, accepted_at=u.accepted_at, url=u.url, locator=st.quote,
                decimals=written - round(math.log10(factor)))


def fallback(verdict: Verdict, claim: Claim, store: EvidenceStore, *, as_of: Any = None, context: Optional[str] = None,
             entity: Optional[tuple[int, str]] = None, draft: Optional[str] = None) -> Verdict:
    """verdict, or ✓ (release_statement) when it is a "?" for lack of evidence and the company's earnings release for
    the claim's quarter states the same figure. Anything else, and any failure here, returns verdict unchanged."""
    if verdict.status != "unverifiable" or verdict.reason != "no_evidence":
        return verdict
    if claim.forward_looking or claim.metric == "guidance_tolerance" or not math.isfinite(claim.value):
        return verdict
    try:
        as_of_dt = _v.to_utc(as_of)
        claim = claim.model_copy(update={"span_text": _v._without_markdown(claim.span_text),
                                         "metric_text": _v._without_markdown(claim.metric_text)})
        draft = _v._without_markdown(draft) if draft else draft
        own = entity or _v.resolve_claim_entity(claim, store, context)
        if own is None:
            return verdict
        cik, ticker = int(own[0]), str(own[1])
        if not units(store, ticker):
            return verdict
        line = _v.draft_line(claim, draft)
        read = claim_figure(claim, line, _company_words(store, cik, ticker, claim))
        if read is None:
            return verdict
        change = read.figure.kind == "change" or (claim.metric == "revenue_growth" and read.figure.kind != "level")
        period = claim_period(claim, store, cik, context, as_of_dt, change)
        st = release_statement(claim, store, (cik, ticker), period, as_of_dt, line)
        if st is None or period is None:
            return verdict
        name = " ".join(re.sub(r"\([^)]*\)", " ", claim.entity or "").split())
        name = name if name.casefold() not in _v.GENERIC_ENTITIES else ticker
        label = periods.label(periods.ResolvedPeriod(period.fiscal_year, period.fiscal_period, None),
                              periods.calendar(store, cik))
        when = f"{st.unit.accepted_at:%b} {st.unit.accepted_at.day}, {st.unit.accepted_at.year}"
        owner = name if name.endswith(("'s", "’s")) else _v.possessive(name)  # McDonald's, not McDonald's'
        explanation = f"Matches {owner} 8-K earnings release for {label} (accepted {when}): “{st.quote}”"
        return Verdict(claim_id=verdict.claim_id, status="verified", reason=REASON, explanation=explanation,
                       evidence=[_fact(claim, st, read.digits)], as_of=as_of_dt)
    except Exception:  # release text is a bonus: a failure here never changes the structured verdict
        log.exception("release-text fallback failed for claim %s", claim.id)
        return verdict


# ---------------------------------------------------------------------------
# Round 10a: a release table cell against the release's own words
# ---------------------------------------------------------------------------
def table_conflict(verdict: Verdict, claim: Claim, store: EvidenceStore, *, as_of: Any = None,
                   context: Optional[str] = None, entity: Optional[tuple[int, str]] = None,
                   draft: Optional[str] = None) -> Verdict:
    """verdict, or "?" (ambiguous, never ✓) when it is a "✗" against a cell of a release table that NoCap mapped to the
    claim's measure, and the company's own earnings release for the claim's quarter states the claim's figure for the
    same measure in words (release_statement, the matcher fallback uses). The table's cell can be another column than
    the sentence's figure: Gilead's "Trodelvy | 336" is its U.S. sales, while the release says "Trodelvy ... sales
    increased 42% to $506 million". NoCap never marks wrong a figure the company's own release states. A filed 10-Q or
    10-K figure is not a mapped cell, and keeps its "✗"; any failure here returns verdict unchanged."""
    if verdict.status != "wrong" or verdict.reason == "look_ahead" or not verdict.evidence:
        return verdict
    cell = verdict.evidence[0]
    if cell.source != "press_release" or claim.forward_looking or not math.isfinite(claim.value):
        return verdict
    try:
        as_of_dt = _v.to_utc(as_of)
        claim = claim.model_copy(update={"span_text": _v._without_markdown(claim.span_text),
                                         "metric_text": _v._without_markdown(claim.metric_text)})
        draft = _v._without_markdown(draft) if draft else draft
        own = entity or _v.resolve_claim_entity(claim, store, context)
        if own is None:
            return verdict
        cik, ticker = int(own[0]), str(own[1])
        if cik != cell.cik or not units(store, ticker):
            return verdict
        line = _v.draft_line(claim, draft)
        read = claim_figure(claim, line, _company_words(store, cik, ticker, claim))
        if read is None:
            return verdict
        change = read.figure.kind == "change" or (claim.metric == "revenue_growth" and read.figure.kind != "level")
        period = claim_period(claim, store, cik, context, as_of_dt, change)
        st = release_statement(claim, store, (cik, ticker), period, as_of_dt, line, skip_names=True)
        if st is None or period is None:
            return verdict
        cal = periods.calendar(store, cik)
        label = periods.label(periods.ResolvedPeriod(period.fiscal_year, period.fiscal_period, None), cal)
        row = (cell.locator or "").split("|", 1)[0].strip() or _v.METRIC_LABELS.get(cell.metric, cell.metric)
        when = f"{st.unit.accepted_at:%b} {st.unit.accepted_at.day}, {st.unit.accepted_at.year}"
        name = " ".join(re.sub(r"\([^)]*\)", " ", claim.entity or "").split())
        name = name if name.casefold() not in _v.GENERIC_ENTITIES else ticker
        owner = name if name.endswith(("'s", "’s")) else _v.possessive(name)
        explanation = (f"{owner} 8-K earnings release table gives “{row}” as {_v.fmt_value(cell.value, cell.metric, claim)}"
                       f" for {label}, but its earnings release (accepted {when}) states: “{st.quote}” The cell may be "
                       f"another column of the table (a region or a product line, for example), so NoCap can't tell "
                       f"which figure is right and doesn't mark {claim.value_text} wrong.")
        return Verdict(claim_id=verdict.claim_id, status="unverifiable", reason="ambiguous", explanation=explanation,
                       evidence=[cell, _fact(claim, st, read.digits)], as_of=verdict.as_of)
    except Exception:  # a safety net: a failure here never changes the structured verdict
        log.exception("release-text table check failed for claim %s", claim.id)
        return verdict


# ---------------------------------------------------------------------------
# Round 10a: net income as the company's own release states it
# ---------------------------------------------------------------------------
_NET_INCOME_MEASURES = (frozenset({"net", "income"}), frozenset({"net", "earning"}), frozenset({"net", "profit"}))
# A sentence with any non-GAAP wording states no GAAP figure here, whatever its basis phrase looks like ("On a
# non-GAAP(*) basis, ... net income was $2 billion": the asterisk keeps _BASIS_PHRASE_RE from reading it)
_NOT_GAAP_SENTENCE_RE = re.compile(r"non[\s‐‑-]?gaap|\badjust(?:ed|ing|ments?)\b|\bexclud(?:ing|es?|ed)\b|\bexclusive\s+of\b"
                                   r"|\bcore\b|\bunderlying\b|\bpro[\s-]?forma\b|\bcomparable\b|\borganic\b"
                                   r"|\boperating\s+(?:basis|earnings|net\s+income)\b|\bconstant[\s-]+currenc", re.I)
_FIGURE_TAIL_RE = re.compile(r"[^,;:()]*")  # the words right after a figure, to its clause's end
_YEAR_WORDS_RE = re.compile(r"\bfull[\s-]+(?:fiscal[\s-]+)?year\b|\bfiscal\s+year\b|\bfor\s+the\s+year\b"
                            r"|\b(?:twelve|12)[\s-]+months\b|\byear\s+ended\b", re.I)


def _stated_periods(words: str, unit: Unit, cal: list[periods.CalEntry]) -> set[tuple[int, str]]:
    """The fiscal periods (fiscal_year, "Q1".."Q4" or "FY") a figure of `unit` can be for, read from the words that
    date it in its own clause: the release's quarter, its fiscal year (a fourth quarter's release reports both), or a
    period the words name outright. Words naming nothing leave the release's quarter, and in a fourth quarter's release
    also its year: a summary sentence may give either."""
    quarter = (unit.fiscal_year, unit.fiscal_period)
    year = (unit.fiscal_year, "FY")
    p = periods.parse(words)
    if p.kind in ("ytd", "conflict") or p.shift is not None:
        return set()
    if p.year is not None or p.end is not None:
        rp = periods.resolve_in_calendar(words, cal)
        return {(rp.fiscal_year, rp.fiscal_period)} if rp is not None else set()
    if p.kind == "quarter":
        return {quarter} if p.quarter in (None, int(unit.fiscal_period[1])) else set()
    if p.kind == "annual" or _YEAR_WORDS_RE.search(words):
        return {year} if unit.fiscal_period == "Q4" else set()
    return {quarter, year} if unit.fiscal_period == "Q4" else {quarter}


def net_income_statement(store: EvidenceStore, cik: int, ticker: str, digits: str, cls: str,
                         wanted: set[tuple[int, str]], *, as_of: Optional[datetime] = None
                         ) -> Optional[tuple[Unit, str, tuple[int, str]]]:
    """(unit, quote, period) of a sentence of the company's own earnings releases that states its GAAP net income at
    the figure written `digits` (number class `cls`, as _numbers reads it) for one of the `wanted` fiscal periods, or
    None. "Net income" here is the release's unqualified line ("GAAP net income for fiscal 2026 was $8.52 billion,
    compared with $7.83 billion in fiscal 2025"): no adjusted, attributable, per-share or other measure. A current
    figure is dated by its clause (_stated_periods); a comparison base only by the period the words right after it
    name ("compared with $1.45 billion for the fourth quarter of fiscal 2025") or by "a year ago" from its sentence's
    current figure. Used only to keep a figure from "✗" (receipts.verify._Check._release_net_income)."""
    cal = periods.calendar(store, cik)
    company = frozenset(_words(ticker)) | frozenset(
        w for w in _words(re.sub(r"/[A-Z]+/?", " ", str((getattr(store, "company", lambda _: {})(cik) or {})
                                                        .get("name") or ""))) if w not in _MEASURE_NOUNS)
    for unit in units(store, ticker):
        if unit.cik != cik or (as_of is not None and unit.accepted_at > as_of):
            continue
        for s0, s1 in _sentences(unit.text):
            sentence = unit.text[s0:s1]
            lead = unit.lead if s0 == 0 else ""
            if digits not in sentence.replace(",", "") or _NOT_GAAP_SENTENCE_RE.search(f"{sentence} {lead}"):
                continue
            nums = _numbers(sentence)
            read = []
            for num in nums:
                fig = _figure(sentence, num.start, num.end, company, numbers=nums, count=False, lead=lead, neg=num.neg)
                if (num.cls == cls and not fig.forward and not num.neg and not fig.qualifiers - {"gaap"}
                        and fig.measure in _NET_INCOME_MEASURES and fig.kind != "change"):
                    read.append((num, fig))
            current: set[tuple[int, str]] = set()
            for num, fig in read:
                if not fig.base:
                    current |= _stated_periods(sentence[fig.measure_start:num.end] + " " +
                                               _FIGURE_TAIL_RE.match(sentence, num.end).group(), unit, cal)
            for num, fig in read:
                if num.digits != digits:
                    continue
                if fig.base:
                    tail = _FIGURE_TAIL_RE.match(sentence, num.end).group()
                    named = periods.parse(tail)
                    if named.year is not None or named.end is not None:
                        rp = periods.resolve_in_calendar(tail, cal)
                        found = {(rp.fiscal_year, rp.fiscal_period)} if rp is not None else set()
                    elif named.shift == "year":
                        found = {(fy - 1, fp) for fy, fp in current}
                    else:
                        found = set()
                else:
                    found = _stated_periods(sentence[fig.measure_start:num.end] + " " +
                                            _FIGURE_TAIL_RE.match(sentence, num.end).group(), unit, cal)
                for period in sorted(found & wanted):
                    return unit, _quote(sentence, num, fig.measure_start, lead if fig.inherited else ""), period
    return None
