"""Deterministic verification: one Claim plus the EvidenceStore gives one Verdict. No model is called here.

Rules (docs/adr-001-architecture.md, .claude/reference/project.md):
  * Forward-looking claims (guidance) are unverifiable / forward_looking.
  * The entity maps to a CIK through store.resolve_entity; failing that, unverifiable / ambiguous.
  * The period resolves on the company's own fiscal calendar (receipts.periods); failing that, ambiguous.
  * Non-GAAP claims verify only against press-release facts. GAAP or unstated claims verify against XBRL,
    derived and press-release GAAP facts. With no basis stated, non-GAAP wording ("adjusted", "managed",
    "FTE") also means press-release facts only, and a match on the other basis is ambiguous, never wrong.
    Bank headline revenue and net interest income with no basis stated are never wrong on GAAP alone.
  * Constant-currency / FX-neutral figures are always unverifiable: filings report actual exchange rates. That
    wording qualifies only the number it stands next to ("increased 24%, or 23% in constant currency, to $119.8
    billion" makes only the 23% currency-neutral).
  * Tolerance is the precision written in value_text: "$96.2 billion" allows +-$0.05B, "18%" +-0.5 pp,
    "$2.46" +-$0.005, "75.0%" +-0.05 pp. Hedges widen it: "approximately" gives "≈", "more than" is a bound.
    A value derived from rounded inputs also carries their rounding (a change of $1.08 -> $2.46 is +-1.5 pp).
  * Derived metrics: gross_margin = gross_profit / revenue (or (revenue - cost of revenue) / revenue) for the
    same period and basis; operating margin = operating income / revenue; a percent change of any base metric
    (revenue, operating income, net income, EPS, costs and expenses, ...) QoQ against the previous fiscal quarter
    and YoY against the same quarter of the prior fiscal year; segment revenue from segment facts only. A segment
    the wording names ("Data Center revenue", "Google Cloud operating income") is never checked against the
    consolidated total, and wording that says more than a metric ("operating income from Reality Labs") is "?".
  * Readings (interpret): an untyped figure ("other") is checked only when its own description names one base
    metric; unqualified "EPS" may be basic or diluted, and either match verifies it.
  * Restatements: the store returns every version; a claim matching any version visible at as_of is
    verified, and a wrong claim is corrected with the latest visible version.
  * A wrong value is diagnosed in this order: period_mismatch (the other comparison base of a change, or the same
    metric in a period a writer plausibly confuses: same fiscal year, adjacent quarter, a year off), scale_mismatch
    (a x1000 shift), basis_mismatch (the other basis), entity_mismatch (a company the context names, or exactly
    one other known company), else value_mismatch. Naming another period or company needs a precise figure
    (half a unit <= 0.5% of it): "$181.5 billion" identifies its owner, "16%" doesn't. expected_value is in the
    claim's own unit and scale.
  * With as_of, evidence accepted after as_of does not count. If none of it was public then, the claim is
    wrong / look_ahead, citing the acceptance time, only when as_of also precedes the period's Item 2.02
    earnings 8-K (or a press-release fact dates the release); otherwise it is unverifiable, because the
    release may already have been out with no press-release fact on file.
  * No facts: unverifiable / no_evidence. Ambiguity never produces "wrong".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from receipts import periods
from receipts.periods import CalEntry, ResolvedPeriod
from receipts.types import SCALE_FACTOR, Claim, EvidenceStore, Fact, Verdict

log = logging.getLogger(__name__)

USD_METRICS = frozenset({
    "revenue", "segment_revenue", "operating_expenses", "operating_income", "net_income", "capital_returned",
    "repurchase_authorization_remaining", "gross_profit", "cost_of_revenue", "research_and_development",
    "operating_cash_flow", "free_cash_flow", "net_interest_income"})
PER_SHARE_METRICS = frozenset({"eps_diluted", "eps_basic", "dividend_per_share"})
PERCENT_METRICS = frozenset({"revenue_growth", "gross_margin", "tax_rate"})
INSTANT_METRICS = frozenset({"repurchase_authorization_remaining"})
NON_GAAP_BY_NATURE = frozenset({"free_cash_flow"})
# Bank headline revenue and net interest income are often managed / fully taxable-equivalent (non-GAAP):
# with no basis stated, a mismatch against GAAP alone is "?", never "✗".
BANK_TICKERS = frozenset({"JPM", "BAC", "C", "WFC", "GS", "MS", "USB", "PNC", "TFC", "COF", "SCHW", "BK", "STT"})
BASIS_SENSITIVE_AT_BANKS = frozenset({"revenue", "net_interest_income"})
SCALE_SHIFTS = ((1e3, "1,000"), (1e-3, "1,000"), (1e6, "a million"), (1e-6, "a million"))
SOURCE_RANK = {"xbrl": 0, "derived": 1, "press_release": 2}
EARNINGS_RELEASE_WINDOW_DAYS = 100  # an Item 2.02 8-K this many days after a period end reports that period

METRIC_LABELS = {
    "revenue": "revenue", "segment_revenue": "revenue", "revenue_growth": "revenue growth",
    "gross_margin": "gross margin", "operating_expenses": "operating expenses",
    "operating_income": "operating income", "net_income": "net income", "eps_diluted": "diluted EPS",
    "eps_basic": "basic EPS", "capital_returned": "capital returned to shareholders",
    "repurchase_authorization_remaining": "remaining buyback authorization",
    "dividend_per_share": "dividend per share", "tax_rate": "effective tax rate",
    "guidance_tolerance": "guidance range", "other": "figure", "gross_profit": "gross profit",
    "cost_of_revenue": "cost of revenue", "research_and_development": "R&D expense",
    "operating_cash_flow": "operating cash flow", "free_cash_flow": "free cash flow",
    "net_interest_income": "net interest income", "operating_margin": "operating margin",
}
GENERIC_ENTITIES = frozenset({"", "the company", "company", "we", "our", "us", "it", "the firm", "the group",
                              "management", "the business", "n/a", "none", "unknown"})

# Hedge and bound words right before a number decide how it is compared (hedge_of). Longer phrases are listed first,
# so "no more than" is an upper bound, not "more than".
_HEDGES: tuple[tuple[str, bool, re.Pattern[str]], ...] = tuple((kind, inclusive, re.compile(rx + r"\s*$")) for kind, inclusive, rx in (
    ("upper", True, r"\b(?:no\s+more\s+than|not\s+more\s+than|at\s+most|up\s+to|a\s+maximum\s+of)"),
    ("lower", True, r"\b(?:no\s+less\s+than|not\s+less\s+than|at\s+least|upwards?\s+of|a\s+minimum\s+of)"),
    ("nearly", True, r"\b(?:nearly|almost|just\s+(?:under|below|shy\s+of)|a\s+little\s+under)"),
    ("lower", False, r"\b(?:more\s+than|greater\s+than|higher\s+than|over|above|exceed(?:ing|ed|s)?"
                     r"|in\s+excess\s+of|north\s+of|surpass(?:ing|ed|es)?|topp(?:ing|ed)|just\s+over)"),
    ("upper", False, r"\b(?:less\s+than|fewer\s+than|lower\s+than|under|below|south\s+of)"),
    ("approx", True, r"(?:\b(?:approximately|approx\.?|about|around|roughly|close\s+to|some|circa|an\s+estimated)|~)"),
))
_DOWN_RE = re.compile(r"\b(?:down|declin\w*|decreas\w*|fell|fall\w*|dropp?\w*|lower|shr[ai]nk\w*|shrunk"
                      r"|contract\w*)\b")
_LOSS_RE = re.compile(r"\bloss(?:es)?\b")
_YEAR_AGO_RE = re.compile(r"\ba\s+year\s+(?:ago|earlier|before)\b|\byear[\s-]+(?:ago|earlier)\b|\bprior[\s-]+year\b"
                          r"|\blast\s+year\b|\bprevious\s+year\b")
_PRIOR_Q_RE = re.compile(r"\b(?:previous|prior|preceding|last)\s+quarter\b|\bsequential(?:ly)?\b")
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# Words that put a whole statement on a non-GAAP basis ("On a non-GAAP basis, gross margin was 56%, ...").
_NON_GAAP_WORDS_RE = re.compile(r"\b(?:non[\s-]?gaap|adjusted|managed|fte|fully[\s-]taxable[\s-]equivalent|underlying"
                                r"|organic|pro[\s-]forma)\b", re.I)
# Constant-currency / FX-neutral wording qualifies the one number it stands next to: "increased 24%, or 23% in
# constant currency, to $119.8 billion" restates the 23% only. Such a figure can't be derived from reported results
# (Day 2 eval: 10 of 11 false alarms compared it with reported growth), so it is always "?".
_CURRENCY_NEUTRAL_RE = re.compile(r"constant[\s-]currenc(?:y|ies)|currency[\s-]neutral|\bex[\s-]?fx\b"
                                  r"|\b(?:fx|f/x)[\s-]?neutral|\bon\s+an?\s+(?:fx|f/x|foreign[\s-]exchange)\b"
                                  r"|excluding\s+(?:the\s+)?(?:impact\s+of\s+)?foreign[\s-]exchange", re.I)
_FIGURE_RE = re.compile(r"[+−-]?\$?\s?\(?\d[\d,]*(?:\.\d+)?\)?\s?(?:%|percent\b|percentage\s+points?\b|basis\s+points?\b"
                        r"|(?:trillion|billion|million|thousand)\b|[BMK]\b)?", re.I)
# A percent change: the wording says the figure moved, or names its comparison base.
_CHANGE_RE = re.compile(r"\b(?:increas\w*|decreas\w*|grew|grow\w*|rose|rise|rising|up|down|fell|fall\w*|declin\w*"
                        r"|dropp?\w*|jump\w*|surg\w*|climb\w*|higher|lower|expand\w*|shr[ai]nk\w*|shrunk|contract\w*"
                        r"|change)\b|year[\s-]+over[\s-]+year|quarter[\s-]+over[\s-]+quarter|sequential\w*|\byoy\b"
                        r"|\bqoq\b|\by/y\b|\bq/q\b|\bfrom\s+(?:a\s+year\s+ago|the\s+(?:previous|prior)\s+quarter)", re.I)
_MARGIN_RE = re.compile(r"\bmargins?\b|\bratio\b|\brate\b|\byield\b|\breturn\s+on\b|\bper\s+cent\s+of\b", re.I)
_TO_COMMON_RE = re.compile(r"\b(?:available|applicable|attributable)\s+to\s+(?:common|class)", re.I)
# The base metric a percent change (or an untyped figure) names, most specific wording first. Wording outside this
# list stays untracked ("?"): "net income available to common stockholders" is not NetIncomeLoss.
_BASE_METRIC_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((m, re.compile(rx, re.I)) for m, rx in (
    ("eps_basic", r"\bbasic\s+(?:earnings|net\s+income|EPS)\b(?:\s+per\s+(?:common\s+)?share)?"
                  r"|\b(?:earnings|net\s+income)\s+per\s+(?:common\s+)?basic\s+share\b"),
    ("eps_diluted", r"\b(?:diluted\s+)?(?:earnings|net\s+income)\s+per\s+(?:common\s+)?(?:diluted\s+)?share\b"
                    r"|\b(?:diluted\s+)?EPS\b"),
    ("free_cash_flow", r"\bfree\s+cash\s+flows?\b|\bFCF\b"),
    ("operating_cash_flow", r"\boperating\s+cash\s+flows?\b|\bcash\s+flows?\s+from\s+operati\w+"
                            r"|\bcash\s+provided\s+by\s+operating\s+activities\b"),
    ("net_interest_income", r"\bnet\s+interest\s+income\b|\bNII\b"),
    ("operating_income", r"\boperating\s+(?:income|profit)\b|\bincome\s+from\s+operations\b"),
    ("gross_profit", r"\bgross\s+profit\b"),
    ("cost_of_revenue", r"\bcosts?\s+of\s+(?:revenues?|sales)\b"),
    ("research_and_development", r"\bresearch\s+and\s+development\b|\bR&D\b"),
    ("operating_expenses", r"\b(?:total\s+)?costs\s+and\s+expenses\b|\b(?:total\s+)?operating\s+expenses\b"
                           r"|\b(?:total\s+)?non-?interest\s+expenses?\b"),
    ("net_income", r"\bnet\s+(?:income|earnings|profit)\b"),
))
# Level metrics whose percent change Receipts derives from two periods' facts (cur / prev - 1).
GROWTH_BASES = frozenset({"revenue", "segment_revenue", "operating_income", "net_income", "eps_diluted", "eps_basic",
                          "operating_expenses", "gross_profit", "cost_of_revenue", "research_and_development",
                          "operating_cash_flow", "free_cash_flow", "net_interest_income"})
# Another company's figure is named as the owner of a wrong value only when the draft's number is this precise
# (relative half-unit): "$181.5 billion" identifies Amazon; "16%" could be anyone's.
PEER_MATCH_MAX_REL_TOL = 0.005
_NEGATIVE_RE = re.compile(r"\b(?:negative|minus)\b")
# Words before "revenue" that don't name a segment: totals, bases, periods, and possessives.
_GENERIC_QUALIFIERS = frozenset(
    "total net gross reported record quarterly annual consolidated overall company group companywide company-wide "
    "fiscal full-year full year quarter quarters first second third fourth first-quarter second-quarter "
    "third-quarter fourth-quarter q1 q2 q3 q4 its the our their his her a an of for in and gaap non-gaap "
    "managed adjusted headline top-line topline revenue revenues sales fy ttm trailing twelve-month "
    "year-over-year sequential latest current inc corp corporation co ltd plc llc".split())
_REVENUE_SUBJECT_RE = re.compile(r"((?:[A-Z][\w&.\-]*\s+){1,4})(?:[Ss]egment\s+)?[Rr]evenues?\b")
_CONTEXT_NOISE_RE = re.compile(  # period words in a context line; what is left is the company name
    r"\b(?:f?q[1-4](?:'\d{2})?|[1-4]q\d{0,4}|fy\s*'?\d{2,4}|fiscal|years?|quarters?|full|ended|ending|end|of|for|"
    r"the|first|second|third|fourth|three|twelve|months|period|as|\d{1,4}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
    r"apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    r"|[,.;:()]|(?:'|’)s\b", re.I)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def to_utc(x: Any) -> Optional[datetime]:
    """datetime, date or ISO string as an aware UTC datetime. Naive values are taken as UTC."""
    if x is None:
        return None
    if isinstance(x, str):
        s = x.strip()
        x = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
    if isinstance(x, date) and not isinstance(x, datetime):
        x = datetime(x.year, x.month, x.day)
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)


def possessive(name: str) -> str:
    """The possessive form: NVIDIA's, but Meta Platforms'."""
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def fmt_ts(x: datetime) -> str:
    return f"{to_utc(x):%Y-%m-%d %H:%M:%S} UTC"


def decimals_of(value_text: str, value: float) -> int:
    """Decimal places of the number written in value_text ('$96.2 billion' -> 1, '18%' -> 0)."""
    nums = _NUM_RE.findall(value_text or "")
    if nums:
        pick = min(nums, key=lambda n: abs(float(n.replace(",", "")) - abs(value)))
        return len(pick.split(".", 1)[1]) if "." in pick else 0
    r = repr(float(value))
    return 0 if r.endswith(".0") or "e" in r else len(r.split(".", 1)[1])


def tolerance(claim: Claim) -> float:
    """Half a unit of the last written digit, in full units: '$96.2 billion' -> 0.05e9, '18%' -> 0.5."""
    step = 10.0 ** -decimals_of(claim.value_text, claim.value)
    if claim.unit in ("USD", "shares"):
        step *= SCALE_FACTOR.get(claim.scale, 1.0)
    return 0.5 * step


def claimed_value(claim: Claim, *, change: Optional[bool] = None) -> float:
    """The claim's value in full units (USD, not billions), signed by the draft's own words. change: the figure is
    a percent change (default: metric revenue_growth); then decline wording signs it and loss wording doesn't."""
    v = float(claim.value)
    if claim.unit in ("USD", "shares"):
        v *= SCALE_FACTOR.get(claim.scale, 1.0)
    change = claim.metric == "revenue_growth" if change is None else change
    words = f"{claim.span_text} {claim.metric_text}".lower()
    if v > 0 and _NEGATIVE_RE.search(words):
        v = -v  # "free cash flow was negative $2.12 billion"
    elif v > 0 and change and _DOWN_RE.search(words):
        v = -v  # "revenue fell 5%"
    elif v > 0 and not change and claim.metric in ("net_income", "operating_income") and _LOSS_RE.search(words):
        v = -v  # "a net loss of $1.2 billion"
    return v


def within(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-9 * max(1.0, abs(a), abs(b))


def to_claim_units(value: float, claim: Claim) -> float:
    """A full-unit value in the claim's own unit and scale: 9.86e9 for a '$9.86 million' claim is 9860.0."""
    if claim.unit in ("USD", "shares"):
        value /= SCALE_FACTOR.get(claim.scale, 1.0)
    return float(f"{value:.10g}")


def fmt_money(v: float) -> str:
    sign, a = ("-" if v < 0 else ""), abs(v)
    for factor, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if a >= factor:
            x = f"{a / factor:,.3f}".rstrip("0").rstrip(".")
            return f"{sign}${x} {word}"
    return f"{sign}${a:,.2f}"


def fmt_value(v: float, metric: str, claim: Claim) -> str:
    if metric in PERCENT_METRICS or claim.unit == "%":
        d = max(1, decimals_of(claim.value_text, claim.value) + 1)
        return f"{v:.{d}f}%"
    if metric in PER_SHARE_METRICS or claim.unit == "USD/share":
        return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"
    if claim.unit == "shares":
        return f"{v:,.0f} shares"
    return fmt_money(v)


def _seg_norm(s: Optional[str]) -> str:
    s = (s or "").casefold().replace("&", " and ")
    s = re.sub(r"\b(?:segment|segments|revenue|revenues|business|market|platform|the)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def implied_segment(claim: Claim) -> Optional[str]:
    """A segment the wording names although the claim has none: 'Data Center revenue' -> 'Data Center'.

    A segment figure must never be checked against the consolidated total (a false ✗), so a revenue claim
    whose metric_text or quote puts a non-generic qualifier before "revenue" is treated as a segment claim.
    The company's own name, periods and bases ("NVIDIA Q2 revenue", "total GAAP revenue") don't count."""
    company = {w for w in re.sub(r"(?:'s|’s)\b", "", claim.entity or "").casefold().split() if w}
    candidates = []
    m = re.match(r"^\s*(.*?)\s*\brevenues?\b", claim.metric_text or "", re.I)
    if m:
        candidates.append(m.group(1))
    i = claim.span_text.find(claim.value_text) if claim.value_text else -1
    for m in _REVENUE_SUBJECT_RE.finditer(claim.span_text[:i] if i > 0 else claim.span_text):
        candidates.append(m.group(1))
    for raw in candidates:
        words = [w for w in re.sub(r"(?:'s|’s)\b", "", raw).replace(",", " ").split()]
        keep = [w for w in words if w.casefold().strip(".") not in _GENERIC_QUALIFIERS
                and w.casefold() not in company and not re.fullmatch(r"(?:fy|q[1-4])?'?\d{2,4}", w.casefold())]
        if keep and len(keep) <= 4:
            return " ".join(keep)
    return None


def _company_words(claim: Claim) -> set[str]:
    return {w for w in re.sub(r"(?:'s|’s)\b|\([^)]*\)", " ", claim.entity or "").casefold().split() if w}


def _value_pos(claim: Claim) -> Optional[int]:
    """Where value_text sits in span_text, or None when it isn't there or appears twice (then nothing local is known)."""
    vt, span = claim.value_text or "", claim.span_text or ""
    i = span.find(vt) if vt else -1
    if i < 0 or span.find(vt, i + 1) >= 0:
        return None
    return i


def currency_neutral(claim: Claim) -> bool:
    """True when constant-currency / FX-neutral wording qualifies this claim's own number: it is in metric_text, or
    in the span next to this number rather than another one ("up 18% (up 17% in constant currency)" qualifies
    the 17% only). When the number can't be located in the span, any such wording counts (the old, safe rule)."""
    if _CURRENCY_NEUTRAL_RE.search(claim.metric_text or ""):
        return True
    span = claim.span_text or ""
    words = list(_CURRENCY_NEUTRAL_RE.finditer(span))
    if not words:
        return False
    pos = _value_pos(claim)
    if pos is None:
        return True
    figures = [(m.start(), m.end()) for m in _FIGURE_RE.finditer(span) if re.search(r"\d", m.group(0))]
    mine = next(((a, b) for a, b in figures if a <= pos < b), None)
    if mine is None:
        return True
    for w in words:
        before = [b for a, b in figures if b <= w.start()]
        after = [a for a, b in figures if a >= w.end()]
        gap_before = w.start() - max(before) if before else None
        gap_after = min(after) - w.end() if after else None
        if gap_before is not None and (gap_after is None or gap_before <= gap_after):
            owner = max((f for f in figures if f[1] <= w.start()), key=lambda f: f[1])
        elif gap_after is not None:
            owner = min((f for f in figures if f[0] >= w.end()), key=lambda f: f[0])
        else:
            continue
        if owner == mine:
            return True
    return False


def relative_tolerance(claim: Claim) -> float:
    v = abs(claimed_value(claim))
    return tolerance(claim) / v if v else float("inf")


def _metric_phrases(text: str) -> list[tuple[str, int, int]]:
    """(base metric, start, end) for every base-metric phrase in text, most specific wording winning overlaps."""
    found: list[tuple[str, int, int]] = []
    for metric, rx in _BASE_METRIC_WORDS:
        for m in rx.finditer(text or ""):
            if any(not (m.end() <= a or m.start() >= b) for _, a, b in found):
                continue
            found.append((metric, m.start(), m.end()))
    return sorted(found, key=lambda x: x[1])


def _qualifier_before(text: str, start: int, company: set[str]) -> Optional[str]:
    """Capitalized words right before a metric phrase that name a segment, region or product line: 'Google Cloud
    operating income' -> 'Google Cloud'. Totals, bases, periods and the company's own name don't count."""
    m = re.search(r"((?:[A-Z][\w&.\-]*\s+){1,4})(?:[Ss]egment\s+)?$", text[:start])
    if not m:
        return None
    words = [w for w in m.group(1).replace(",", " ").split()]
    keep = [w for w in words if w.casefold().strip(".") not in _GENERIC_QUALIFIERS | {"diluted", "basic", "consolidated"}
            and w.casefold() not in company and not re.fullmatch(r"(?:fy|q[1-4])?'?\d{2,4}", w.casefold())]
    return " ".join(keep) if keep and len(keep) <= 4 else None


@dataclass(frozen=True)
class Reading:
    """What a claim states, as code reads it: a level of `metric`, or a percent change of `growth_base`."""

    metric: str                      # checked metric; "revenue_growth" for revenue changes, else the level metric
    growth_base: Optional[str]       # base metric of a percent change ("revenue", "eps_diluted", ...), or None
    segment: Optional[str]           # segment the wording names, if any
    alternatives: tuple[str, ...] = ()  # other metrics the same wording may mean: unqualified "EPS" is basic or diluted
    derived: Optional[str] = None    # "operating_margin" (operating income / revenue)


def _eps_alternatives(metric: str, claim: Claim) -> tuple[str, ...]:
    """'EPS' or 'earnings per share' with neither 'diluted' nor 'basic' in its wording can be read either way."""
    if metric not in ("eps_diluted", "eps_basic"):
        return ()
    if re.search(r"\b(?:diluted|basic)\b", f"{claim.metric_text} {claim.span_text}", re.I):
        return ()
    return ("eps_basic",) if metric == "eps_diluted" else ("eps_diluted",)


_FRAME_WORDS = frozenset(
    "increase increases increased increasing decrease decreases decreased decreasing grew grow grows growing growth "
    "rose rise rises rising up down fell fall falls falling decline declines declined declining jumped jump climbed "
    "higher lower expanded expansion change changed year over quarter yoy qoq y/y q/q sequential sequentially from "
    "ago prior previous last compared versus vs vs. by to at was were is are has have had a an the of for in on its basis "
    "their our record reported posted delivered".split())


def _leftover_words(text: str, phrase: tuple[int, int], company: set[str], qualifier: Optional[str]) -> list[str]:
    """Words of a metric description outside its metric phrase that are not framing (change and comparison words,
    totals, bases, periods, the company, the qualifier taken as segment). Any left means the wording says more than
    the phrase ('operating income from Reality Labs'), so the figure isn't read as the company-level metric."""
    rest = f"{text[:phrase[0]]} {text[phrase[1]:]}"
    qual = {w.casefold() for w in (qualifier or "").split()}
    out = []
    for w in re.findall(r"[A-Za-z][\w&'/.-]*", rest):
        k = w.casefold().strip(".")
        if (k in _FRAME_WORDS or k in _GENERIC_QUALIFIERS or k in company or k in qual
                or k in {"diluted", "basic", "consolidated", "gaap", "non-gaap", "segment", "percent", "percentage",
                         "points", "fiscal", "quarterly", "annual"}
                or re.fullmatch(r"(?:fy|q[1-4])?'?\d{0,4}", k)):
            continue
        out.append(w)
    return out


def _is_change(claim: Claim) -> bool:
    """The claim is a percent change: its comparison is stated, or change wording sits in metric_text or right
    before the number (after any earlier figure in the span)."""
    if claim.comparison in ("YoY", "QoQ", "sequential"):
        return True
    if _CHANGE_RE.search(claim.metric_text or ""):
        return True
    pos = _value_pos(claim)
    span = claim.span_text or ""
    if pos is None:
        return False
    before = span[:pos]
    figures = [m for m in _FIGURE_RE.finditer(before) if re.search(r"\d", m.group(0))]
    window = before[figures[-1].end():] if figures else before
    return bool(_CHANGE_RE.search(window[-60:]))


def interpret(claim: Claim) -> Optional[Reading]:
    """How the verifier reads a claim, or None when it names nothing Receipts tracks (then "?", never "✗").

    * revenue_growth: a percent change of revenue (or of a segment's revenue).
    * A percent figure on a level metric with change wording ("operating income increased 11%"): its change.
    * metric "other": the base metric its own metric_text names. A percent change may also take its subject from
      the span ("Net income was $14.4 billion, up 12%") when at most one figure sits between the two. A level
      figure must be named by metric_text itself, so "a favorable impact of $0.11" is never read as EPS.
    * metric_text "operating margin": operating income / revenue for the company (no segment wording)."""
    m, unit = claim.metric, claim.unit
    company = _company_words(claim)
    seg = (claim.segment or "").strip() or None
    if m == "revenue_growth":
        return Reading("revenue_growth", "revenue", seg)
    if m in GROWTH_BASES and unit == "%":
        if _MARGIN_RE.search(claim.metric_text or "") or not _is_change(claim):
            return Reading(m, None, seg)  # a level metric written in percent: the unit check says "?"
        if _TO_COMMON_RE.search(claim.metric_text or ""):
            return None
        if m in ("revenue", "segment_revenue"):
            return Reading("revenue_growth", "revenue", seg)
        hit = _metric_phrases(claim.metric_text)
        if hit and seg is None:
            seg = _qualifier_before(claim.metric_text, hit[0][1], company)
        if hit and seg is None and _leftover_words(claim.metric_text, hit[0][1:], company, None):
            return Reading(m, None, seg)  # more wording than the metric: never the company total ("?")
        return Reading(m, m, seg)
    if m != "other":
        return Reading(m, None, seg, _eps_alternatives(m, claim))
    mt = claim.metric_text or ""
    if _TO_COMMON_RE.search(mt):
        return None
    if unit == "%" and re.fullmatch(r"\s*(?:(?:total|consolidated|gaap|non-gaap|adjusted)\s+)*operating\s+margins?\s*",
                                    mt, re.I):
        return Reading("operating_margin", None, None, derived="operating_margin")
    if _MARGIN_RE.search(mt):
        return None
    phrases = _metric_phrases(mt)
    if phrases:
        base, start, end = phrases[0]
        seg = seg or _qualifier_before(mt, start, company)
        if len(phrases) > 1 or _leftover_words(mt, (start, end), company, seg):
            return None  # the description says more than one metric phrase: leave it untracked ("?")
    elif unit == "%" and _is_change(claim):
        pos = _value_pos(claim)
        if pos is None:
            return None
        before = claim.span_text[:pos]
        cands = _metric_phrases(before)
        if not cands:
            return None
        base, start, end = cands[-1]
        between = [x for x in _FIGURE_RE.finditer(before[end:]) if re.search(r"\d", x.group(0))]
        if len(between) > 1 or _TO_COMMON_RE.search(before[start:]) or _MARGIN_RE.search(before[start:]):
            return None
        seg = seg or _qualifier_before(before, start, company)
    else:
        return None
    if unit == "%":
        return Reading(base, base, seg) if _is_change(claim) else None
    if base in PER_SHARE_METRICS and unit in ("USD/share", "USD"):
        return Reading(base, None, seg, _eps_alternatives(base, claim))
    if base in USD_METRICS and unit == "USD":
        return Reading(base, None, seg)
    return None


def _fact_kind(f: Fact) -> str:
    if f.period_start is not None:
        days = (f.period_end - f.period_start).days + 1
        return "quarter" if 80 <= days <= 100 else "annual" if 350 <= days <= 380 else "other"
    fp = (f.fiscal_period or "").upper()
    return "quarter" if fp in periods.QUARTERS else "annual" if fp == "FY" else "instant"


def _source_rank(f: Fact) -> int:
    return SOURCE_RANK.get(f.source, 3)


def form_phrase(f: Fact) -> str:
    if f.source == "press_release":
        return "8-K press release"
    return f"{f.form} (derived)" if f.source == "derived" else f.form


def _dedupe_facts(facts: Iterable[Fact]) -> list[Fact]:
    out: dict[tuple[str, str, Optional[str], float, date], Fact] = {}
    for f in facts:
        out.setdefault((f.accession, f.metric, f.segment, f.value, f.period_end), f)
    return sorted(out.values(), key=lambda f: (to_utc(f.accepted_at), _source_rank(f)))


@dataclass(frozen=True)
class Obs:
    """One observed value for a (company, metric, period, basis): a fact, or a value derived from facts."""

    value: float
    facts: tuple[Fact, ...]
    err: float = 0.0  # rounding carried in from the inputs: a change computed from $2.02 and $1.57 is +-0.7 pp

    @property
    def public_at(self) -> datetime:  # a derived value is public once its last input is
        return max(to_utc(f.accepted_at) for f in self.facts)

    @property
    def primary(self) -> Fact:
        """The fact the value is about: the fact itself, a margin's gross profit, a growth rate's current
        quarter. A non-GAAP margin is cited from its press release, never from the 10-Q revenue beneath it."""
        return self.facts[0]


def _half_unit(f: Fact) -> float:
    """Half the last reported digit of a fact, in its own units (XBRL decimals, or the press-release cell's)."""
    if f.decimals is not None:
        return 0.5 * 10.0 ** -f.decimals
    return {"USD/share": 0.005, "%": 0.05}.get(f.unit, 0.5)


@dataclass(frozen=True)
class Hedge:
    """How the draft qualifies its number. lower / upper: a bound on the filed value (strict, or inclusive for
    "at least" / "at most"); nearly: slightly below the number; approx: either side, within the approximation band."""

    kind: str        # "lower" | "upper" | "nearly" | "approx"
    inclusive: bool

    @property
    def words(self) -> str:
        return {("lower", False): "more than", ("lower", True): "at least", ("upper", False): "less than",
                ("upper", True): "at most", ("nearly", True): "just under"}.get((self.kind, self.inclusive),
                                                                               "approximately")


def hedge_of(claim: Claim) -> Optional[Hedge]:
    """The hedge or bound word right before the claim's number (or leading its value_text: 'over $28 billion')."""
    span, vt = claim.span_text or "", claim.value_text or ""
    i = span.find(vt) if vt else -1
    lead = re.match(r"[^\d$€£¥+\-−(]*", vt).group(0)
    text = " ".join(f"{span[:i] if i >= 0 else ''} {lead}".lower().split())[-40:]
    for kind, inclusive, rx in _HEDGES:
        if rx.search(text):
            return Hedge(kind, inclusive)
    return None


SPLIT_RATIOS = (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50, 100)


def split_ratio(a: Fact, b: Fact) -> Optional[int]:
    """n when two versions of one per-share figure differ by an n-for-1 (or 1-for-n) stock split, allowing each value
    its own rounding ($5.87 vs $0.59 is Netflix's 10-for-1). None when no ratio fits, or several do (cents can't
    tell 2-for-1 from 3-for-1), so a genuine restatement ($0.89 vs $0.90) is never taken for a split."""
    va, vb = abs(a.value), abs(b.value)
    if not va or not vb or (a.value > 0) != (b.value > 0):
        return None
    (big, fb), (small, fs) = sorted(((va, a), (vb, b)), key=lambda x: -x[0])
    fits = [n for n in SPLIT_RATIOS if abs(big - n * small) <= n * _half_unit(fs) + _half_unit(fb) + 1e-12]
    return fits[0] if len(fits) == 1 else None


def _best_obs(obs: Iterable[Obs]) -> Obs:
    """The receipt to cite for a match: a filing over a press release, the original version first."""
    return min(obs, key=lambda o: (_source_rank(o.primary), o.public_at))


def _current_obs(obs: Iterable[Obs]) -> Obs:
    """The figure to correct a wrong claim with: a filing over a press release, the latest version visible
    (the store keeps restated versions; a claim matching any of them was already accepted as verified)."""
    return min(obs, key=lambda o: (_source_rank(o.primary), -o.public_at.timestamp()))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
def _name_variants(name: str) -> list[str]:
    out = [name.strip()]
    s = re.sub(r"(?:'s|’s)$", "", out[0]).strip()
    out.append(s)
    out.append(re.sub(r",?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.)$",
                      "", s).strip())
    return [v for v in dict.fromkeys(out) if v]


def _resolve(store: EvidenceStore, name: str) -> Optional[tuple[int, str]]:
    for cand in _name_variants(name):
        r = store.resolve_entity(cand)
        if r:
            return int(r[0]), str(r[1])
    return None


def context_entities(context: Optional[str], store: EvidenceStore) -> list[tuple[int, str, str]]:
    """Companies a context line names, as (cik, ticker, name as written): 'NVIDIA Q2 FY2027' -> NVIDIA."""
    if not context:
        return []
    out: dict[int, tuple[int, str, str]] = {}
    for chunk in re.split(r"[;,/|]|\s+(?:and|vs\.?|versus)\s+", context):
        name = " ".join(_CONTEXT_NOISE_RE.sub(" ", chunk).split())
        if not re.search(r"[A-Za-z]", name):
            continue
        hit = _resolve(store, name)
        words = name.split()
        for k in range(len(words) - 1, 0, -1):  # "NVIDIA Corp" or a leftover word: try shorter prefixes
            if hit:
                break
            hit = _resolve(store, " ".join(words[:k]))
        if hit and hit[0] not in out:
            out[hit[0]] = (hit[0], hit[1], name)
    return list(out.values())


def resolve_claim_entity(claim: Claim, store: EvidenceStore, context: Optional[str] = None) -> Optional[tuple[int, str]]:
    """(cik, ticker) for the claim's company. A generic subject ('the company') falls back to a context that
    names exactly one company; a named company the store doesn't know is never swapped for the context's."""
    name = (claim.entity or "").strip()
    if name.casefold() not in GENERIC_ENTITIES:
        return _resolve(store, name)
    ents = context_entities(context, store)
    return ents[0][:2] if len(ents) == 1 else None


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
def verify(claim: Claim, store: EvidenceStore, *, as_of: Any = None, context: Optional[str] = None) -> Verdict:
    """The verdict for one claim. as_of: datetime or ISO string; evidence accepted later is look-ahead.
    context: the document-context line (company and fiscal period) used for extraction."""
    as_of_dt = to_utc(as_of)

    def plain(status: str, reason: str, explanation: str) -> Verdict:
        return Verdict(claim_id=claim.id, status=status, reason=reason, explanation=explanation, as_of=as_of_dt)

    if claim.forward_looking or claim.metric == "guidance_tolerance":
        return plain("unverifiable", "forward_looking",
                     "This is guidance about a future period, so no filing can confirm it yet.")
    reading = interpret(claim)
    if reading is None:
        what = f" ({claim.metric_text})" if claim.metric_text else ""
        return plain("unverifiable", "no_evidence",
                     f"Receipts doesn't track this kind of figure{what} yet, so there is no filing fact to check.")
    if currency_neutral(claim):
        return plain("unverifiable", "no_evidence",
                     f"{claim.value_text} is a constant-currency (FX-neutral) figure; filings report results at actual "
                     f"exchange rates, so Receipts can't check it.")
    entity = resolve_claim_entity(claim, store, context)
    if entity is None:
        return unresolved_entity(claim, store, as_of_dt)
    return _Check(claim, store, entity, as_of_dt, context, reading).run()


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def unresolved_entity(claim: Claim, store: EvidenceStore, as_of: Optional[datetime] = None) -> Verdict:
    """'?' for a company the store doesn't resolve, saying why: a generic subject with no company in the context, a
    name that mixes or abbreviates covered companies, or a company outside Receipts' coverage (named plainly, with
    the list of covered companies, instead of suggesting it isn't in EDGAR)."""
    who = " ".join((claim.entity or "").split())

    def plain(reason: str, explanation: str) -> Verdict:
        return Verdict(claim_id=claim.id, status="unverifiable", reason=reason, explanation=explanation, as_of=as_of)

    if who.casefold() in GENERIC_ENTITIES:
        return plain("ambiguous", "The draft doesn't say which company this figure belongs to.")
    covered = known_companies(store)
    if not covered:
        return plain("ambiguous", f"Couldn't match \"{who}\" to a company in the SEC filings database.")
    try:
        from receipts.store import ALIASES
    except Exception:
        ALIASES = {}
    mentioned = [name for _cik, ticker, name in covered
                 if any(re.search(rf"(?<![\w&]){re.escape(a)}(?![\w&])", who, re.I) for a in [ticker, *ALIASES.get(ticker, [])])]
    if len(mentioned) > 1:
        return plain("ambiguous", f"\"{who}\" names more than one company ({_join(mentioned)}), so Receipts can't "
                                  f"tell whose figure this is.")
    if mentioned:  # "Amazon Web Services": a unit of a covered company, never checked against its company-wide total
        return plain("ambiguous", f"\"{who}\" is part of {mentioned[0]}, not the whole company, so Receipts doesn't "
                                  f"check it against {possessive(mentioned[0])} company-wide filings.")
    return plain("no_evidence", f"Receipts covers {len(covered)} companies today: {_join([n for _, _, n in covered])}; "
                                f"\"{who}\" isn't one of them, so there is no filing data to check it against.")


class _Check:
    def __init__(self, claim: Claim, store: EvidenceStore, entity: tuple[int, str], as_of: Optional[datetime],
                 context: Optional[str], reading: Optional[Reading] = None):
        self.c, self.store, self.as_of, self.context = claim, store, as_of, context
        self.cik, self.ticker = entity
        name = " ".join(re.sub(r"\([^)]*\)", " ", claim.entity).split())  # "Amazon (AMZN)" -> "Amazon"
        self.company = name if name.casefold() not in GENERIC_ENTITIES else self.ticker
        self.co_s = possessive(self.company)
        self.cal = periods.calendar(store, self.cik)
        r = reading or interpret(claim) or Reading(claim.metric, None, None)
        self.growth_base = r.growth_base      # a percent change of this metric, or None for a level figure
        self.derived = r.derived
        self.alternatives = r.alternatives
        self.segment = r.segment
        if self.segment is None and r.metric in ("revenue", "revenue_growth"):
            self.segment = implied_segment(claim)  # never check a segment figure against the consolidated total
        self.metric = "segment_revenue" if r.metric == "revenue" and self.segment else r.metric
        # The basis the draft states. With none stated, non-GAAP wording ("adjusted", "managed", "FTE") or a
        # non-GAAP-only measure still means press-release evidence only, and a GAAP mismatch is never "✗".
        self.basis_stated = claim.basis is not None
        non_gaap_words = _NON_GAAP_WORDS_RE.search(f"{claim.metric_text} {claim.span_text}")
        self.basis = claim.basis or ("non-GAAP" if self.metric in NON_GAAP_BY_NATURE or non_gaap_words else None)
        self.claimed = claimed_value(claim, change=self.growth_base is not None)
        self.tol = tolerance(claim)
        self._facts: dict[tuple[Any, ...], list[Fact]] = {}
        self.hedge = hedge_of(claim)
        self.superseded: list[tuple[Obs, Obs, int]] = []  # (pre-split version, the version that replaced it, ratio)

    # -- verdicts ------------------------------------------------------------
    def verdict(self, status: str, reason: str, explanation: str, *, evidence: Iterable[Fact] = (),
                expected: Optional[float] = None) -> Verdict:
        return Verdict(claim_id=self.c.id, status=status, reason=reason, explanation=explanation,
                       expected_value=None if expected is None else to_claim_units(expected, self.c),
                       evidence=_dedupe_facts(evidence), as_of=self.as_of)

    def wrong(self, reason: str, actual: Obs, explanation: str, extra: Iterable[Fact] = ()) -> Verdict:
        return self.verdict("wrong", reason, explanation, evidence=[*actual.facts, *extra], expected=actual.value)

    # -- labels ----------------------------------------------------------------
    def plabel(self, rp: ResolvedPeriod, cal: Optional[list[CalEntry]] = None) -> str:
        return periods.label(rp, self.cal if cal is None else cal)

    def subject(self) -> str:
        """What a percent change is of: 'revenue', 'Data Center revenue', 'diluted EPS', 'Google Cloud operating income'."""
        what = "revenue" if self.metric == "revenue_growth" else METRIC_LABELS.get(self.growth_base or "", "figure")
        if self.metric != "revenue_growth" and self.c.basis:
            what = f"{self.c.basis} {what}"
        return f"{self.segment} {what}" if self.segment else what

    def mlabel(self, basis_word: bool = True) -> str:
        if self.growth_base and self.metric != "revenue_growth":
            return f"{self.subject() if basis_word else METRIC_LABELS.get(self.growth_base, 'figure')} growth"
        base = METRIC_LABELS.get(self.metric, self.metric)
        if self.segment and self.metric in ("segment_revenue", "revenue_growth"):
            base = f"{self.segment} {base}"
        elif self.segment and self.metric not in ("revenue", "segment_revenue"):
            base = f"{self.segment} {base}"
        if basis_word and self.c.basis and self.metric not in ("revenue", "segment_revenue", "revenue_growth"):
            base = f"{self.c.basis} {base}"
        return base

    def fmt(self, v: float) -> str:
        return fmt_value(v, "revenue_growth" if self.growth_base else self.metric, self.c)

    def statement(self, o: Obs, rp: ResolvedPeriod, comparison: Optional[str] = None,
                  cal: Optional[list[CalEntry]] = None) -> str:
        if self.growth_base:
            verb = "grew" if o.value >= 0 else "fell"
            return f"{self.subject()} {verb} {self.fmt(abs(o.value))} {_cmp_words(comparison or self.c.comparison)} " \
                   f"in {self.plabel(rp, cal)}"
        return f"{self.mlabel()} was {self.fmt(o.value)} for {self.plabel(rp, cal)}"

    # -- evidence --------------------------------------------------------------
    def public(self, o: Obs) -> bool:
        return self.as_of is None or o.public_at <= self.as_of

    def _store_facts(self, cik: int, metric: str, period_end: date, segment: Optional[str]) -> list[Fact]:
        """Store query (cached per check). segment=None is consolidated only; a named segment falls back to
        every segment ("*") so 'data center' still finds 'Data Center segment' through _seg_norm."""
        key = (cik, metric, period_end, segment)
        if key not in self._facts:
            facts = list(self.store.facts(cik, metric, segment=segment, period_end=period_end) or [])
            if segment and not facts:
                facts = list(self.store.facts(cik, metric, segment="*", period_end=period_end) or [])
            self._facts[key] = facts
        return self._facts[key]

    def level(self, cik: int, metric: str, rp: Optional[ResolvedPeriod], basis: Optional[str],
              segment: Optional[str] = None) -> list[Obs]:
        if rp is None or rp.period_end is None:
            return []
        want = "instant" if metric in INSTANT_METRICS else "annual" if rp.fiscal_period == "FY" else "quarter"
        out = []
        for f in self._store_facts(cik, metric, rp.period_end, segment):
            if f.cik != cik or f.metric != metric or f.period_end != rp.period_end:
                continue
            if basis == "non-GAAP":
                if f.basis != "non-GAAP" or f.source != "press_release":
                    continue  # non-GAAP figures come only from press releases
            elif f.basis != "GAAP":
                continue
            if segment is None:
                if f.segment:
                    continue
            elif _seg_norm(f.segment) != _seg_norm(segment):
                continue
            if want == "instant":
                if f.period_start is not None:
                    continue
            elif _fact_kind(f) != want:
                continue
            out.append(Obs(float(f.value), (f,)))
        if metric in PER_SHARE_METRICS and len(out) > 1:
            out = self._split_consistent(out)
        return out

    def _split_consistent(self, obs: list[Obs]) -> list[Obs]:
        """Per-share versions of one figure that differ by a stock-split ratio: only those on the share basis of the
        latest version visible at as_of count (with no as_of, the latest version). A split restates every earlier
        per-share figure, so the pre-split number no longer describes the shares. Versions that differ for other
        reasons (BAC's 2026 restatement) all still count."""
        visible = [o for o in obs if self.public(o)]
        if len(visible) < 2:
            return obs
        latest = max(visible, key=lambda o: (o.public_at, -_source_rank(o.primary)))
        keep = []
        for o in obs:
            n = split_ratio(o.primary, latest.primary) if self.public(o) else None
            if n:
                self.superseded.append((o, latest, n))
            else:
                keep.append(o)
        return keep

    def growth(self, cik: int, cal: list[CalEntry], rp: ResolvedPeriod, comparison: str, basis: Optional[str],
               segment: Optional[str]) -> list[Obs]:
        """Percent change of the base metric against the previous fiscal quarter (QoQ) or the same quarter a year
        earlier (YoY). The inputs' own rounding is carried as err: per-share figures move a change by up to ~1 pp."""
        base = (periods.shift(rp, cal, quarters=-1) if comparison in ("QoQ", "sequential")
                else periods.shift(rp, cal, years=-1))
        metric = self.growth_base or "revenue"
        if metric == "revenue" and segment:
            metric = "segment_revenue"
        cur, prev = self.level(cik, metric, rp, basis, segment), self.level(cik, metric, base, basis, segment)
        out = []
        for c in cur:
            for p in prev:
                if p.value <= 0:
                    continue  # a change from zero or from a loss has no meaningful percent
                ratio = c.value / p.value
                rel = (_half_unit(c.primary) / abs(c.value) if c.value else 0.0) + _half_unit(p.primary) / p.value
                out.append(Obs((ratio - 1.0) * 100.0, c.facts + p.facts, abs(ratio) * rel * 100.0))
        return out

    def operating_margin(self, cik: int, rp: ResolvedPeriod, basis: Optional[str]) -> list[Obs]:
        """Operating income / revenue for the company. A non-GAAP margin uses the release's non-GAAP operating
        income over revenue (revenue has one value either way); it is cited from the press release."""
        revenue = self.level(cik, "revenue", rp, None)
        income = self.level(cik, "operating_income", rp, basis)
        return [Obs(i.value / r.value * 100.0, i.facts + r.facts) for i in income for r in revenue if r.value]

    def margin(self, cik: int, rp: ResolvedPeriod, basis: Optional[str]) -> list[Obs]:
        out = self.level(cik, "gross_margin", rp, basis)  # a press-release "Gross margin 75.0%" cell
        revenue = self.level(cik, "revenue", rp, None)
        gross = self.level(cik, "gross_profit", rp, basis)
        out += [Obs(g.value / r.value * 100.0, g.facts + r.facts) for g in gross for r in revenue if r.value]
        if not gross and basis != "non-GAAP":  # Meta reports no gross profit line
            cost = self.level(cik, "cost_of_revenue", rp, None)
            out += [Obs((r.value - k.value) / r.value * 100.0, r.facts + k.facts) for r in revenue for k in cost
                    if r.value]
        return out

    def observe(self, rp: Optional[ResolvedPeriod], *, cik: Optional[int] = None,
                cal: Optional[list[CalEntry]] = None, basis: Any = "same", comparison: Optional[str] = None) -> list[Obs]:
        cik = self.cik if cik is None else cik
        cal = self.cal if cal is None else cal
        basis = self.basis if basis == "same" else basis
        if rp is None or rp.period_end is None:
            return []
        if self.growth_base:
            return self.growth(cik, cal, rp, comparison or self.c.comparison, basis, self.segment)
        if self.metric == "gross_margin":
            return self.margin(cik, rp, basis)
        if self.derived == "operating_margin":
            return self.operating_margin(cik, rp, basis)
        return self.level(cik, self.metric, rp, basis, self.segment)

    def matches(self, obs: Iterable[Obs], claimed: Optional[float] = None, tol: Optional[float] = None) -> list[Obs]:
        claimed = self.claimed if claimed is None else claimed
        tol = self.tol if tol is None else tol
        return [o for o in obs if within(claimed, o.value, tol + o.err)]

    def alternative_matches(self, rp: ResolvedPeriod) -> tuple[Optional[str], list[Obs]]:
        """A match under another reading of the same wording ('EPS' read as basic rather than diluted)."""
        for metric in self.alternatives:
            hits = [o for o in self.accepts(self.level(self.cik, metric, rp, self.basis, self.segment)) if self.public(o)]
            if hits:
                return metric, hits
        return None, []

    # -- bounds: "more than", "at most", "nearly" are authoritative ---------------------------------
    @property
    def bounded(self) -> bool:
        return self.hedge is not None and self.hedge.kind in ("lower", "upper", "nearly")

    def bound_test(self, o: Obs) -> Optional[bool]:
        """Whether the filed value meets the draft's bound: True, False, or None when the value's own rounding
        (a change computed from rounded inputs) straddles it. No tolerance is added: "more than $4.5 billion" is
        false for $4.459 billion. A decline or a loss bounds the magnitude ("fell more than 30%" is below -30%).
        "nearly X" means slightly below X: within the approximation band under it, and never above it."""
        h = self.hedge
        assert h is not None
        sign = -1.0 if self.claimed < 0 else 1.0
        x, lo, hi = sign * self.claimed, sign * o.value - o.err, sign * o.value + o.err
        if h.kind == "nearly":
            band = max(2 * self.tol, 0.01 * abs(x))
            if hi < x - band or lo > x:
                return False
            return True if (lo >= x - band and hi <= x) else None

        def meets(v: float) -> bool:
            if h.kind == "lower":
                return v >= x if h.inclusive else v > x
            return v <= x if h.inclusive else v < x

        a, b = meets(lo), meets(hi)
        return a if a == b else None

    def accepts(self, obs: Iterable[Obs]) -> list[Obs]:
        """Observations the claim is right about: its bound when it states one, else equality within tolerance."""
        if self.bounded:
            return [o for o in obs if self.bound_test(o) is True]
        return self.matches(obs)

    def _bounded(self, rp: ResolvedPeriod, pub: list[Obs]) -> Verdict:
        """A bounded claim ("more than $4.5 billion", "nearly 20%") is judged by its bound alone, never by plain
        equality within tolerance. Unmet on the stated basis but met on the other one the draft never named, or a bank
        headline that may be the managed figure: "?", as for any ambiguity."""
        words = self.hedge.words if self.hedge else ""
        tests = [(o, self.bound_test(o)) for o in pub]
        ok = [o for o, t in tests if t is True]
        if ok:
            return self._verified(rp, ok, note=f", which is {words} {self.c.value_text}")
        actual = _current_obs(pub)
        if any(t is None for _, t in tests):
            return self.verdict("unverifiable", "ambiguous",
                                f"{self.co_s} {form_phrase(actual.primary)} reports that {self.statement(actual, rp)}, "
                                f"which is too close to {self.c.value_text} to tell, given the rounding of the figures "
                                f"it is computed from.", evidence=actual.facts)
        if not self.basis_stated:
            other = "GAAP" if self.basis == "non-GAAP" else "non-GAAP"
            alt = [o for o in self.observe(rp, basis=other) if self.public(o) and self.bound_test(o) is True]
            if alt:
                best = _best_obs(alt)
                return self.verdict("unverifiable", "ambiguous",
                                    f"{self.co_s} {other} {self.mlabel(False)} for {self.plabel(rp)} was "
                                    f"{self.fmt(best.value)}, which is {words} {self.c.value_text}, but the draft doesn't "
                                    f"say {other}, and the {self.basis or 'GAAP'} figure is {self.fmt(actual.value)}.",
                                    evidence=[*best.facts, *actual.facts])
        bank = self._bank_headline(rp, actual)
        if bank is not None:
            return bank
        return self.wrong("value_mismatch", actual,
                          f"{self.co_s} {form_phrase(actual.primary)} reports that {self.statement(actual, rp)}, "
                          f"which is not {words} {self.c.value_text}.")

    # -- the rules -------------------------------------------------------------
    def run(self) -> Verdict:
        c = self.c
        if not self._unit_fits():
            return self.verdict("unverifiable", "no_evidence",
                                f"Receipts checks {self.mlabel(False)} in {_unit_words(self.metric)}, and this figure "
                                f"is written in {_unit_words_of(c.unit)}.")
        if self.metric == "segment_revenue" and not self.segment:
            return self.verdict("unverifiable", "ambiguous", "The draft doesn't say which segment this revenue is for.")
        level_claim = self.growth_base is None
        rp = periods.resolve_in_calendar(c.period_text, self.cal, context=self.context, period_type=c.period_type,
                                         comparison=c.comparison if level_claim else "YoY", as_of=self.as_of)
        if rp is None:
            said = c.period_text.strip() or "no period"
            return self.verdict("unverifiable", "ambiguous",
                                f"Couldn't tell which {self.company} fiscal period \"{said}\" refers to.")
        if self.growth_base and (c.comparison == "none" or rp.fiscal_period == "FY"):
            return self._growth_unstated(rp)
        obs_all = self.observe(rp)
        if not obs_all:
            return self._no_evidence(rp)
        pub = [o for o in obs_all if self.public(o)]
        if not pub:
            return self._look_ahead(rp, obs_all)
        if self.bounded:
            return self._bounded(rp, pub)
        hits = self.matches(pub)
        if hits:
            return self._verified(rp, hits)
        for check in (self._approximate, self._comparison_base, self._alternative_reading):
            v = check(rp, pub)
            if v is not None:
                return v
        actual = _current_obs(pub)
        for diagnose in (self._diag_split, self._diag_period, self._diag_scale, self._diag_basis, self._diag_entity,
                         self._bank_headline):
            v = diagnose(rp, actual)
            if v is not None:
                return v
        return self.wrong("value_mismatch", actual,
                          f"{self.co_s} {form_phrase(actual.primary)} reports that "
                          f"{self.statement(actual, rp)}, not {c.value_text}.")

    def _unit_fits(self) -> bool:
        u = self.c.unit
        if self.growth_base or self.derived == "operating_margin":
            return u == "%"
        if self.metric in USD_METRICS:
            return u == "USD"
        if self.metric in PER_SHARE_METRICS:
            return u in ("USD/share", "USD")
        if self.metric in PERCENT_METRICS:
            return u == "%"
        return True

    def _verified(self, rp: ResolvedPeriod, hits: list[Obs], *, note: str = "") -> Verdict:
        best = _best_obs(hits)
        return self.verdict("verified", "match",
                            f"Matches {self.co_s} {form_phrase(best.primary)}: {self.statement(best, rp)}{note}.",
                            evidence=[f for o in hits for f in o.facts])

    def _no_evidence(self, rp: ResolvedPeriod) -> Verdict:
        if rp.period_end is None:
            text = f"No {self.company} filing covers {self.plabel(rp)} yet"
        elif self.basis == "non-GAAP":
            text = (f"No press-release fact for {self.co_s} non-GAAP {self.mlabel(False)} for {self.plabel(rp)} "
                    f"(non-GAAP figures come only from earnings releases)")
        else:
            text = f"No filing fact for {self.co_s} {self.mlabel()} for {self.plabel(rp)}"
        hint = None if self.bounded else self._other_period_match(rp)  # equality hints don't fit a bound
        if hint is not None:
            orp, o = hint
            text += f"; {self.c.value_text} matches its {self.mlabel()} for {self.plabel(orp)}"
            return self.verdict("unverifiable", "no_evidence", text + ".", evidence=o.facts)
        if self.basis == "non-GAAP" and rp.period_end is not None and not self.bounded:  # a hint only
            gaap = [o for o in self.matches(self.observe(rp, basis="GAAP")) if self.public(o)]
            if gaap:
                text += f"; {self.c.value_text} matches its GAAP {self.mlabel(False)}"
                return self.verdict("unverifiable", "no_evidence", text + ".", evidence=_best_obs(gaap).facts)
        return self.verdict("unverifiable", "no_evidence", text + ".")

    def _alternative_reading(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """Unqualified 'EPS' that matches basic rather than diluted EPS: right under that reading, so verified."""
        metric, hits = self.alternative_matches(rp)
        if not hits:
            return None
        best = _best_obs(hits)
        label = METRIC_LABELS.get(metric or "", "figure")
        return self.verdict("verified", "match",
                            f"Matches {self.co_s} {form_phrase(best.primary)}: {label} was {self.fmt(best.value)} for "
                            f"{self.plabel(rp)} (the draft doesn't say basic or diluted).",
                            evidence=[f for o in hits for f in o.facts])

    def earnings_release(self, rp: ResolvedPeriod) -> Optional[dict[str, Any]]:
        """The earliest Item 2.02 8-K accepted within EARNINGS_RELEASE_WINDOW_DAYS after the period end, from
        the store's filings table (receipts.store.Store.filings; not part of the EvidenceStore protocol)."""
        filings = getattr(self.store, "filings", None)
        if filings is None or rp.period_end is None:
            return None
        try:
            rows = filings(self.cik, forms=["8-K", "8-K/A"])
        except Exception:  # an optional extra: never let it break a verdict
            log.debug("store.filings failed", exc_info=True)
            return None
        best = None
        for r in rows or []:
            if "2.02" not in str(r.get("items") or ""):
                continue
            at = to_utc(r["accepted_at"])
            if 0 < (at.date() - rp.period_end).days <= EARNINGS_RELEASE_WINDOW_DAYS and (best is None or at < best[0]):
                best = (at, r)
        return best[1] if best else None

    def _look_ahead(self, rp: ResolvedPeriod, obs_all: list[Obs]) -> Verdict:
        """Nothing for this figure was public at as_of. It is look-ahead only if the figure's first public moment
        (its earliest fact, or the earnings 8-K for the period if that came first) is after as_of. Without a
        press-release fact the earliest fact is the 10-Q, which banks file weeks after the 8-K: if the 8-K was
        already out, or it can't be dated, the claim is unverifiable rather than wrong."""
        assert self.as_of is not None
        first = min(obs_all, key=lambda o: o.public_at)
        binding = max(first.facts, key=lambda f: to_utc(f.accepted_at))
        release = self.earnings_release(rp)
        release_at = to_utc(release["accepted_at"]) if release else None
        has_release_fact = any(f.source == "press_release" for o in obs_all for f in o.facts)
        value_ok = bool(self.accepts(obs_all))
        filed = _current_obs(obs_all)
        what = f"{self.co_s} {self.mlabel()} for {self.plabel(rp)}"
        if release_at is not None and release_at <= first.public_at:
            if self.as_of < release_at:
                text = (f"{what} wasn't public until its 8-K earnings release ({release['accession']}) was accepted "
                        f"at {fmt_ts(release_at)}, after the as-of time {fmt_ts(self.as_of)}")
            else:
                return self.verdict(
                    "unverifiable", "no_evidence",
                    f"{self.co_s} earnings release for {self.plabel(rp)} (8-K accepted {fmt_ts(release_at)}) was "
                    f"out by the as-of time {fmt_ts(self.as_of)}, but Receipts has no press-release figure for its "
                    f"{self.mlabel()}, and its {form_phrase(binding)} came later, at {fmt_ts(binding.accepted_at)}.")
        elif has_release_fact or release_at is not None:
            text = (f"{what} wasn't public until its {form_phrase(binding)} was accepted at "
                    f"{fmt_ts(binding.accepted_at)}, after the as-of time {fmt_ts(self.as_of)}")
        else:
            return self.verdict(
                "unverifiable", "no_evidence",
                f"The first filing with {what} that Receipts holds is its {form_phrase(binding)}, accepted at "
                f"{fmt_ts(binding.accepted_at)}, after the as-of time {fmt_ts(self.as_of)}, and the earnings release "
                f"that may have come earlier can't be dated.")
        if not value_ok:
            text += f", and the filed figure is {self.fmt(filed.value)}"
        return self.verdict("wrong", "look_ahead", text + ".", evidence=first.facts,
                            expected=None if value_ok else filed.value)

    def _growth_unstated(self, rp: ResolvedPeriod) -> Verdict:
        """A percent change whose base the draft doesn't name: right if either reading matches, wrong only if
        both readings have public evidence and neither matches, otherwise ambiguous."""
        readings = [("YoY", self.observe(rp, comparison="YoY"))]
        if rp.fiscal_period != "FY":
            readings.append(("QoQ", self.observe(rp, comparison="QoQ")))
        if not any(obs for _, obs in readings):
            return self._no_evidence(rp)
        public = [(cmp, [o for o in obs if self.public(o)]) for cmp, obs in readings]
        if not any(obs for _, obs in public):
            return self._look_ahead(rp, [o for _, obs in readings for o in obs])
        if self.bounded and any(self.bound_test(o) is None for _, obs in public for o in obs) and not any(
                self.accepts(obs) for _, obs in public):
            return self.verdict("unverifiable", "ambiguous",
                                f"{self.co_s} {self.mlabel()} for {self.plabel(rp)} is too close to {self.c.value_text} "
                                f"to tell, given the rounding of the figures it is computed from.",
                                evidence=[f for _, obs in public for o in obs for f in o.facts])
        for cmp, obs in public:
            hits = self.accepts(obs)
            if hits:
                best = _best_obs(hits)
                return self.verdict("verified", "match",
                                    f"Matches {self.co_s} {form_phrase(best.primary)}: "
                                    f"{self.statement(best, rp, cmp)}.", evidence=[f for o in hits for f in o.facts])
        parts = [f"{self.fmt(_current_obs(obs).value)} {_cmp_words(cmp)}" for cmp, obs in public if obs]
        if all(obs for _, obs in public):
            actual = _current_obs(public[0][1])
            tail = (f"neither of which is {self.hedge.words} {self.c.value_text}" if self.bounded and len(parts) > 1
                    else f"which is not {self.hedge.words} {self.c.value_text}" if self.bounded
                    else f"not {self.c.value_text}")
            return self.wrong("value_mismatch", actual,
                              f"{self.co_s} {self.mlabel()} for {self.plabel(rp)} was {' and '.join(parts)}, {tail}.",
                              extra=[f for _, obs in public for o in obs for f in o.facts])
        return self.verdict("unverifiable", "ambiguous",
                            f"The draft doesn't say what the {self.c.value_text} change is measured against, and "
                            f"{self.co_s} {self.mlabel()} for {self.plabel(rp)} was {' and '.join(parts)}.",
                            evidence=[f for _, obs in public for o in obs for f in o.facts])

    def _approximate(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """'approximately $97 billion': right within the approximation band, max(2 x the written half-unit, 1%),
        on either side, and shown as approximate (≈); an exact match was already verified."""
        if self.hedge is None or self.hedge.kind != "approx":
            return None
        band = max(2 * self.tol, 0.01 * abs(self.claimed))
        near = self.matches(pub, tol=band)
        if not near:
            return None
        best = _best_obs(near)
        return self.verdict("approximate", "approximate",
                            f"Close enough for an approximation: {self.co_s} {form_phrase(best.primary)} "
                            f"reports that {self.statement(best, rp)}.", evidence=best.facts)

    def _diag_split(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """The per-share figure as first reported, before a stock split restated it (Netflix's $5.87, now $0.59)."""
        for old, new, n in self.superseded:
            if old.primary.period_end != rp.period_end or not self.matches([old]):
                continue
            split = (f"a {n}-for-1 stock split" if abs(old.value) > abs(new.value)
                     else f"a 1-for-{n} reverse stock split")
            return self.wrong("value_mismatch", actual,
                              f"{self.c.value_text} is {self.co_s} {self.mlabel()} for {self.plabel(rp)} as reported "
                              f"before {split}; on today's share count it is {self.fmt(actual.value)}.",
                              extra=old.facts)
        return None

    def _comparison_base(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """'up from $46.7 billion a year ago': a level figure for the comparison period the sentence names."""
        if self.c.comparison != "none" or self.growth_base:
            return None
        words = self.c.span_text.lower()
        shifted = []
        if _YEAR_AGO_RE.search(words):
            shifted.append(periods.shift(rp, self.cal, years=-1))
        if _PRIOR_Q_RE.search(words):
            shifted.append(periods.shift(rp, self.cal, quarters=-1))
        for srp in shifted:
            hits = [o for o in self.accepts(self.observe(srp)) if self.public(o)]
            if hits and srp is not None:
                return self._verified(srp, hits, note=", the comparison period the sentence cites")
        return None

    def _other_period_match(self, rp: ResolvedPeriod) -> Optional[tuple[ResolvedPeriod, Obs]]:
        """The same figure in a period a writer plausibly confuses with rp: another quarter of the same fiscal year,
        the adjacent quarter, or the same quarter (or year) one fiscal year off. A match two years away is more
        likely coincidence than a mix-up (Day 2: BAC's Q3 2025 expenses 'matched' Q3 2023), so it isn't sought."""
        want_fy = rp.fiscal_period == "FY"

        def index(fy: int, fp: str) -> int:
            return fy * 4 + (int(fp[1]) - 1 if fp in periods.QUARTERS else 3)

        here = index(rp.fiscal_year, rp.fiscal_period)
        for e in sorted(self.cal, key=lambda e: e.end, reverse=True):
            if (e.fiscal_period == "FY") != want_fy or (e.fiscal_year, e.fiscal_period) == rp[:2]:
                continue
            gap = abs(index(e.fiscal_year, e.fiscal_period) - here)
            plausible = (gap == 4 if want_fy else (e.fiscal_year == rp.fiscal_year or gap in (1, 4)))
            if not plausible:
                continue
            orp = ResolvedPeriod(e.fiscal_year, e.fiscal_period, e.end)
            hits = [o for o in self.matches(self.observe(orp)) if self.public(o)]
            if hits:
                return orp, _best_obs(hits)
        return None

    # -- diagnosis of a wrong value -----------------------------------------
    def _diag_period(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        c = self.c
        if self.growth_base:
            other = "YoY" if c.comparison in ("QoQ", "sequential") else "QoQ"
            hits = [o for o in self.matches(self.observe(rp, comparison=other)) if self.public(o)]
            if hits:
                return self.wrong("period_mismatch", actual,
                                  f"{c.value_text} is {self.co_s} {_cmp_words(other)} {self.mlabel()}; "
                                  f"{_cmp_words(c.comparison)} it was {self.fmt(actual.value)} in {self.plabel(rp)}.",
                                  extra=_best_obs(hits).facts)
        if relative_tolerance(c) > PEER_MATCH_MAX_REL_TOL:
            return None  # "3%" matches some quarter's change by chance; only a precise figure names its period
        hint = self._other_period_match(rp)
        if hint is None:
            return None
        orp, o = hint
        return self.wrong("period_mismatch", actual,
                          f"{c.value_text} is {self.co_s} {self.mlabel()} for {self.plabel(orp)}; for "
                          f"{self.plabel(rp)} it was {self.fmt(actual.value)}.", extra=o.facts)

    def _diag_scale(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        if self.c.unit not in ("USD", "shares"):
            return None
        pub = [o for o in self.observe(rp) if self.public(o)]
        for factor, words in SCALE_SHIFTS:
            hits = self.matches(pub, claimed=self.claimed * factor, tol=self.tol * factor)
            if hits:
                best = _best_obs(hits)
                return self.wrong("scale_mismatch", best,
                                  f"{self.co_s} {form_phrase(best.primary)} reports that "
                                  f"{self.statement(best, rp)}, so {self.c.value_text} is off by a factor of "
                                  f"{words}.")
        return None

    def _diag_basis(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        other = "GAAP" if self.basis == "non-GAAP" else "non-GAAP"
        hits = [o for o in self.matches(self.observe(rp, basis=other)) if self.public(o)]
        if not hits:
            return None
        best = _best_obs(hits)
        label = self.mlabel(False)
        mine = self.basis or "GAAP"
        if not self.basis_stated:  # the draft never named a basis: reading it one way is a guess, not an error
            return self.verdict("unverifiable", "ambiguous",
                                f"{self.c.value_text} matches {self.co_s} {other} {label} for {self.plabel(rp)}, "
                                f"but the draft doesn't say {other}, and the {mine} figure is "
                                f"{self.fmt(actual.value)}.", evidence=[*best.facts, *actual.facts])
        return self.wrong("basis_mismatch", actual,
                          f"{self.c.value_text} is {self.co_s} {other} {label} for {self.plabel(rp)}; the "
                          f"{mine} figure is {self.fmt(actual.value)}.", extra=best.facts)

    def _bank_headline(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """Bank revenue or net interest income with no basis stated may be the managed (FTE) figure, which only
        the earnings release carries: a GAAP mismatch is "?" until a press-release figure can rule that out."""
        measure = "revenue" if self.metric == "revenue_growth" else (self.growth_base or self.metric)
        if self.basis_stated or self.ticker not in BANK_TICKERS or measure not in BASIS_SENSITIVE_AT_BANKS:
            return None
        if any(self.public(o) for o in self.observe(rp, basis="non-GAAP")):
            return None  # both readings are on file and neither matched: a real mismatch
        return self.verdict("unverifiable", "ambiguous",
                            f"{self.co_s} {form_phrase(actual.primary)} reports that {self.statement(actual, rp)}, "
                            f"but bank headline {self.mlabel(False)} is often on a managed (fully taxable-equivalent) "
                            f"basis that only the earnings release can confirm.", evidence=actual.facts)

    def _diag_entity(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        c = self.c
        named = context_entities(self.context, self.store)
        for ocik, _oticker, oname in named:
            if ocik == self.cik:
                continue
            ocal = periods.calendar(self.store, ocik)
            orp = periods.resolve_in_calendar(c.period_text, ocal, context=self.context, period_type=c.period_type,
                                              comparison=c.comparison, as_of=self.as_of)
            if orp is None or orp.period_end is None:
                orp = periods.nearest(ocal, rp)
            if orp is None:
                continue
            hits = [o for o in self.matches(self.observe(orp, cik=ocik, cal=ocal)) if self.public(o)]
            if hits:
                return self.wrong("entity_mismatch", actual,
                                  f"{c.value_text} is {possessive(oname)} {self.mlabel()} for {self.plabel(orp, ocal)}; "
                                  f"{self.co_s} was {self.fmt(actual.value)}.", extra=_best_obs(hits).facts)
        return self._peer_entity(rp, actual, skip={self.cik, *(n[0] for n in named)})

    def _peer_entity(self, rp: ResolvedPeriod, actual: Obs, skip: set[int]) -> Optional[Verdict]:
        """A wrong figure that is exactly another known company's same metric for the matching quarter (the one
        ending within a week, or the one with the same fiscal label). Only precise figures qualify, and only when
        exactly one company matches: the status is already "wrong", this only names whose number it is."""
        if relative_tolerance(self.c) > PEER_MATCH_MAX_REL_TOL:
            return None
        found = []
        for ocik, _oticker, oname in known_companies(self.store):
            if ocik in skip:
                continue
            ocal = periods.calendar(self.store, ocik)
            same_label = periods.lookup(ocal, rp.fiscal_year, rp.fiscal_period)
            cands = [periods.nearest(ocal, rp),
                     ResolvedPeriod(same_label.fiscal_year, same_label.fiscal_period, same_label.end) if same_label else None]
            for orp in dict.fromkeys(x for x in cands if x is not None and x.period_end is not None):
                hits = [o for o in self.matches(self.observe(orp, cik=ocik, cal=ocal)) if self.public(o)]
                if hits:
                    found.append((oname, orp, ocal, hits))
                    break
        if len(found) != 1:
            return None
        oname, orp, ocal, hits = found[0]
        return self.wrong("entity_mismatch", actual,
                          f"{self.c.value_text} is {possessive(oname)} {self.mlabel()} for {self.plabel(orp, ocal)}; "
                          f"{self.co_s} was {self.fmt(actual.value)}.", extra=_best_obs(hits).facts)


def known_companies(store: EvidenceStore) -> list[tuple[int, str, str]]:
    """(cik, ticker, display name) for every company in Receipts' universe that the store resolves. The universe is
    receipts.store.ALIASES (the ten project companies); a store that doesn't know a ticker just skips it."""
    try:
        from receipts.store import ALIASES
    except Exception:  # an evidence layer without the alias table: no peer search
        return []
    out = []
    for ticker, names in ALIASES.items():
        try:
            hit = store.resolve_entity(ticker)
        except Exception:
            hit = None
        if hit:
            out.append((int(hit[0]), str(hit[1]), names[0] if names else ticker))
    return out


def _cmp_words(comparison: Optional[str]) -> str:
    return {"YoY": "year over year", "QoQ": "quarter over quarter", "sequential": "quarter over quarter"}.get(
        comparison or "", "")


def _unit_words(metric: str) -> str:
    return "percent" if metric in PERCENT_METRICS else "dollars per share" if metric in PER_SHARE_METRICS else "dollars"


def _unit_words_of(unit: str) -> str:
    return {"USD": "dollars", "%": "percent", "USD/share": "dollars per share", "shares": "shares"}.get(unit, unit)
