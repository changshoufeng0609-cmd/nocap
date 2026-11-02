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
    same period and basis; operating/net margin = operating/net income / revenue. Margin changes in basis points
    or percentage points require both periods' inputs from one filing. A percent change of any base metric
    (revenue, operating income, net income, EPS, costs and expenses, ...) QoQ against the previous fiscal quarter
    and YoY against the same quarter of the prior fiscal year, with both figures from one filing (the 10-Q's
    comparative, the release's prior column, or for a derived quarter the filings it comes from), since another
    filing's version may be on another basis; when no filing reports both (a 10-Q never reports the previous quarter),
    from two filings only if the later one restated nothing (its year-ago figure is the one first filed), else "?";
    segment revenue from segment facts only. A segment
    the wording names ("Data Center revenue", "Google Cloud operating income") is never checked against the
    consolidated total, and wording that says more than a metric ("operating income from Reality Labs") is "?".
  * Readings (interpret): an untyped figure ("other") is checked only when its own description names one base
    metric; unqualified "EPS" may be basic or diluted, and either match verifies it. A bare "per share" figure is EPS
    only when attached to a net-income amount in its sentence ("net income of $362 million, or $1.28 per share");
    "net income attributable to <the company itself>" is plain net income; "for the same quarter last year" after a
    level amount labels its period as "a year ago" does; a bank's noninterest income is read only as a percent change,
    from the NoninterestIncome term of the bank revenue the store serves.
  * Restatements: the store returns every version; a claim matching any version visible at as_of is
    verified, and a wrong claim is corrected with the latest visible version.
  * Stock splits (company_splits: the splits a company's XBRL reports, receipts.splits, and versions of one per-share
    figure that differ by a split ratio in two periods or more): only versions on the latest share basis count. A
    per-share figure on the pre-split basis is wrong with the split explained, also for a period held only as restated;
    one that matches a period's pre-split filing adjusted for the split, where no filing restated that period yet, is
    unverifiable / no_evidence, never wrong. With as_of, a claim matching an actual later split-adjusted filing is
    wrong / look_ahead, citing that version's first acceptance and the figure filed by as_of.
  * A wrong value is diagnosed in this order: period_mismatch (the other comparison base of a change, or the same
    metric in a period a writer plausibly confuses: same fiscal year, adjacent quarter, a year off), scale_mismatch
    (a x1000 shift), basis_mismatch (the other basis), entity_mismatch (a company the context line names, or exactly
    one other company the same draft names; never a company the draft doesn't name, and never a draft-named peer's
    percentage, since margins and tax rates cluster), else value_mismatch. Naming another period or company needs a
    precise figure (half a unit <= 0.5% of it): "$181.5 billion" identifies its owner, "16%" doesn't; only a context
    line that still sets the fiscal period may name its company for a coarser figure. expected_value is in the claim's
    own unit and scale.
  * With as_of, evidence accepted after as_of does not count. If none of it was public then, the claim is
    wrong / look_ahead, citing the acceptance time, only when as_of also precedes the period's Item 2.02
    earnings 8-K (or a press-release fact dates the release); otherwise it is unverifiable, because the
    release may already have been out with no press-release fact on file.
  * No facts: unverifiable / no_evidence. Ambiguity never produces "wrong".
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Iterable, NamedTuple, Optional, Sequence, get_args

from receipts import periods
from receipts.periods import CalEntry, ResolvedPeriod
from receipts.splits import split_events
from receipts.types import SCALE_FACTOR, Claim, EvidenceStore, Fact, Metric, Verdict

log = logging.getLogger(__name__)

USD_METRICS = frozenset({
    "revenue", "segment_revenue", "operating_expenses", "operating_income", "net_income", "capital_returned",
    "repurchase_authorization_remaining", "gross_profit", "cost_of_revenue", "research_and_development",
    "operating_cash_flow", "free_cash_flow", "net_interest_income"})
PER_SHARE_METRICS = frozenset({"eps_diluted", "eps_basic", "dividend_per_share"})
PERCENT_METRICS = frozenset({"revenue_growth", "gross_margin", "tax_rate"})
INSTANT_METRICS = frozenset({"repurchase_authorization_remaining"})
NON_GAAP_BY_NATURE = frozenset({"free_cash_flow"})
# Bank headline revenue and net interest income are often managed / fully taxable-equivalent (non-GAAP): with no basis
# stated, a mismatch against GAAP alone is "?", never "✗". The banks are the S&P 500's GICS Diversified Banks and
# Regional Banks (Wikipedia constituents, read 2026-10-30), plus the one custody bank whose releases headline FTE
# figures. The other custody banks, brokers and card issuers headline GAAP net revenue, so a mismatch there stays "✗":
# BNY (formerly BK), State Street, Schwab, Goldman Sachs (its 31 stored release revenue figures are all GAAP), Morgan
# Stanley, American Express, Capital One, Synchrony. Static on purpose: ingest needs no bank flag.
BANK_TICKERS = (
    "BAC", "C", "JPM", "PNC", "TFC", "USB", "WFC",  # Diversified Banks
    "CFG", "FITB", "HBAN", "KEY", "MTB", "RF",      # Regional Banks
    "NTRS",                                         # Northern Trust: net interest income and revenue on an FTE basis
)
BASIS_SENSITIVE_AT_BANKS = frozenset({"revenue", "net_interest_income"})
# Companies whose own earnings releases report net sales below a larger total revenue in the same table, from an
# offline survey of the archived EX-99.1 exhibits (round 8): Costco (net sales plus membership fees), Walmart (plus
# membership and other income), Dollar Tree (plus other revenue), Incyte (product net sales plus royalties and
# milestones), Snap-on (plus financial services revenue) and Viatris (plus other revenues). The revenue NoCap holds for
# them is that total, so a figure the draft calls (net) sales is checked only against a net-sales fact, and with none
# held it is "?" (_Check.sales_only). Companies whose top line is net sales (Apple, Amazon, Home Depot, Target) are
# checked as before. Static on purpose, as BANK_TICKERS: ingest needs no flag.
NET_SALES_BELOW_TOTAL_REVENUE = frozenset({"COST", "DLTR", "INCY", "SNA", "VTRS", "WMT"})
_SALES_NOUN_RE = re.compile(r"\bsales\b", re.I)
_REVENUE_NOUN_RE = re.compile(r"\brevenues?\b", re.I)
_NET_SALES_ROW_RE = re.compile(r"\s*(?:total\s+)?(?:net\s+)?sales\s*(?:\(\w{1,3}\)\s*|\d{1,2}\s*)*[.:]*\s*", re.I)
# The bank revenue receipts.ingest serves as net interest income plus noninterest income, as its locator writes it.
_BANK_REVENUE_FORMULA_RE = re.compile(r"RevenuesNetOfInterestExpense = InterestIncomeExpenseNet "
                                      r"(?P<nii>-?\d[\d,]*(?:\.\d+)?) \+ NoninterestIncome "
                                      r"(?P<other>-?\d[\d,]*(?:\.\d+)?) \((?P<form>\S+) (?P<accession>\S+)\)")
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
    "net_interest_income": "net interest income", "operating_margin": "operating margin", "net_margin": "net margin",
    "noninterest_income": "noninterest income",
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
                      r"|contract\w*|narrow\w*)\b")
_LOSS_RE = re.compile(r"\bloss(?:es)?\b")
_YEAR_AGO_RE = re.compile(r"\ba\s+year\s+(?:ago|earlier|before)\b|\byear[\s-]+(?:ago|earlier)\b|\bprior[\s-]+year\b"
                          r"|\blast\s+year\b|\bprevious\s+year\b")
_PRIOR_Q_RE = re.compile(r"\b(?:previous|prior|preceding|last)\s+quarter\b|\bsequential(?:ly)?\b")
# After a period label, wording that makes the label the subject of the next amount.
_INTRODUCES_AMOUNT_RE = re.compile(r"[\s)]*(?::|=|(?:\s*\w+){0,2}\s+(?:was|were|is|are|stood|totaled|totalled"
                                   r"|reached|came\s+in)\b)")
# A named quarter of an earlier year is a period, not a year-over-year comparison:
# "from the fourth quarter of last year" can be the previous quarter.
_NAMED_QUARTER_RE = re.compile(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th|q[1-4])[\s-]+(?:fiscal[\s-]+)?"
                               r"quarter\s+of\s+(?:the\s+)?(?:last|prior|previous|preceding)\s+(?:fiscal\s+)?year\b"
                               r"|\bq[1-4]\s+(?:of\s+)?(?:last|prior|previous)\s+year\b", re.I)
_GROWTH_COMPARISONS = (
    ("YoY", re.compile(r"\byear[\s-]+over[\s-]+year\b|\byoy\b|\by/y\b|" + _YEAR_AGO_RE.pattern, re.I)),
    ("QoQ", re.compile(r"\bquarter[\s-]+over[\s-]+quarter\b|\bqoq\b|\bq/q\b|" + _PRIOR_Q_RE.pattern, re.I)),
)
_NUM_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)")
# Words that put a whole statement on a non-GAAP basis ("On a non-GAAP basis, gross margin was 56%, ...").
_NON_GAAP_WORDS_RE = re.compile(r"\b(?:non[\s-]?gaap|adjusted|managed|fte|(?:fully[\s-])?taxable[\s-]equivalent|underlying"
                                r"|organic|pro[\s-]forma)\b", re.I)
# Constant-currency / FX-neutral wording qualifies the one number it stands next to: "increased 24%, or 23% in
# constant currency, to $119.8 billion" restates the 23% only. Such a figure can't be derived from reported results
# (Day 2 eval: 10 of 11 false alarms compared it with reported growth), so it is always "?".
_CURRENCY_NEUTRAL_RE = re.compile(r"constant[\s-]currenc(?:y|ies)|currency[\s-]neutral|\bex[\s-]?fx\b"
                                  r"|\b(?:fx|f/x)[\s-]?neutral|\bon\s+an?\s+(?:fx|f/x|foreign[\s-]exchange)\b"
                                  r"|excluding\s+(?:the\s+)?(?:impact\s+of\s+)?foreign[\s-]exchange", re.I)
# A digit inside a word is not a figure: the "3" of "L3Harris" (or of "Q3") never ends the wording before a number.
_FIGURE_RE = re.compile(r"(?<![A-Za-z])[+−-]?(?:US\$|USD\s*|\$)?\s?\(?\d[\d,]*(?:\.\d+)?\)?\s?(?:%|percent\b"
                        r"|percentage\s+points?\b|basis\s+points?\b|(?:trillion|billion|million|thousand)\b|[BMK]\b)?",
                        re.I)
# A percent change: the wording says the figure moved, or names its comparison base.
_CHANGE_RE = re.compile(r"\b(?:increas\w*|decreas\w*|grew|grow\w*|rose|rise|rising|up|down|fell|fall\w*|declin\w*"
                        r"|dropp?\w*|jump\w*|surg\w*|climb\w*|higher|lower|expand\w*|shr[ai]nk\w*|shrunk|contract\w*"
                        r"|narrow\w*|widen\w*|improv\w*|change)\b|year[\s-]+over[\s-]+year|quarter[\s-]+over[\s-]+quarter|sequential\w*|\byoy\b"
                        r"|\bqoq\b|\by/y\b|\bq/q\b|\bfrom\s+(?:a\s+year\s+ago|the\s+(?:previous|prior)\s+quarter)", re.I)
_MARGIN_RE = re.compile(r"\bmargins?\b|\bratio\b|\brate\b|\byield\b|\breturn\s+on\b|\bper\s+cent\s+of\b", re.I)
_SHARE_OF_RE = re.compile(r"(?:%|\bpercent)\s+of\b|\bshare\s+of\b|\bproportion\b", re.I)
_TO_COMMON_RE = re.compile(r"\b(?:available|applicable|attributable)\s+to\s+(?:common|class)", re.I)
_COMMON_LINE_RE = re.compile(r"\b(?:available|applicable|attributable)\s+(?:to|for)\s+common\s+"
                             r"(?:stockholders|shareholders)\b", re.I)
_INCLUDING_NCI_RE = re.compile(r"\bincluding\s+(?:non[ -]?controlling|minority)\s+interests?\b", re.I)
_CONSOLIDATED_INCOME_RE = re.compile(r"\bconsolidated\s+(?=net\s+(?:income|earnings|profit|loss)\b)", re.I)
# EPS is computed on income available to common, so that wording inside an EPS phrase names EPS itself: "net income
# attributable to common stockholders per diluted share" is diluted EPS. Outside one, it stays a net-income line.
_TO_COMMON_HOLDERS = (r"(?:\s+(?:available|attributable|applicable)\s+(?:to|for)\s+common\s+"
                      r"(?:stockholders|shareholders))?")
_INCOME_LINES = {
    "net_income_to_common": ("us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic",
                             "net income available to common stockholders"),
    "net_income_including_nci": ("us-gaap:ProfitLoss", "consolidated net income including noncontrolling interests"),
}
# The base metric a percent change (or an untyped figure) names, most specific wording first. Wording outside this
# list stays untracked ("?"): "net income available to common stockholders" is not NetIncomeLoss.
_BASE_METRIC_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((m, re.compile(rx, re.I)) for m, rx in (
    ("eps_basic", r"\bbasic\s+(?:earnings|net\s+income|EPS)\b(?:\s+per\s+(?:common\s+)?share)?"
                  rf"|\b(?:earnings|net\s+income){_TO_COMMON_HOLDERS}\s+per\s+(?:common\s+)?basic\s+share\b"),
    ("eps_diluted", rf"\b(?:diluted\s+)?(?:earnings|net\s+income){_TO_COMMON_HOLDERS}\s+per\s+(?:common\s+)?"
                    r"(?:diluted\s+)?share\b|\b(?:diluted\s+)?EPS\b"),
    ("free_cash_flow", r"\bfree\s+cash\s+flows?\b|\bFCF\b"),
    ("operating_cash_flow", r"\boperating\s+cash\s+flows?\b|\bcash\s+flows?\s+from\s+operati\w+"
                            r"|\bcash\s+provided\s+by\s+operating\s+activities\b"),
    ("net_interest_income", r"\bnet\s+interest\s+income\b|\bNII\b"),
    # A bank's noninterest income: only its percent change is read, and only where the store holds the line
    # (_Check._noninterest_income); its level is not a tracked base metric (not in USD_METRICS).
    ("noninterest_income", r"\b(?:total\s+)?non[\s-]?interest\s+income\b"),
    ("operating_income", r"\boperating\s+(?:income|profit)\b|\bincome\s+from\s+operations\b"),
    ("gross_profit", r"\bgross\s+profit\b"),
    ("cost_of_revenue", r"\bcosts?\s+of\s+(?:revenues?|sales)\b"),
    ("research_and_development", r"\bresearch\s+and\s+development\b|\bR&D\b"),
    ("operating_expenses", r"\b(?:total\s+)?costs\s+and\s+expenses\b|\b(?:total\s+)?operating\s+expenses\b"
                           r"|\b(?:total\s+)?non-?interest\s+expenses?\b"),
    ("net_income", r"\bnet\s+(?:income|earnings|profit)\b"),
))
# Level metrics whose percent change NoCap derives from two periods' facts (cur / prev - 1).
GROWTH_BASES = frozenset({"revenue", "segment_revenue", "operating_income", "net_income", "eps_diluted", "eps_basic",
                          "operating_expenses", "gross_profit", "cost_of_revenue", "research_and_development",
                          "operating_cash_flow", "free_cash_flow", "net_interest_income"})
# Another company's figure is named as the owner of a wrong value only when the draft's number is this precise
# (relative half-unit): "$181.5 billion" identifies Amazon; "16%" could be anyone's.
PEER_MATCH_MAX_REL_TOL = 0.005
LIST_COVERED_UP_TO = 40  # above this, the uncovered-company message gives the count, not every name
_NEGATIVE_RE = re.compile(r"\b(?:negative|minus)\b")
# Words before "revenue" that don't name a segment: totals, bases, periods, and possessives.
_GENERIC_QUALIFIERS = frozenset(
    "total net gross reported record quarterly annual consolidated overall company group companywide company-wide "
    "fiscal full-year full year quarter quarters first second third fourth first-quarter second-quarter "
    "third-quarter fourth-quarter q1 q2 q3 q4 its the our their his her a an of for in and gaap non-gaap "
    "managed adjusted te fte taxable-equivalent fully-taxable-equivalent "
    "headline top-line topline revenue revenues sales fy ttm trailing twelve-month "
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
        # Extraction normalizes trillions to billions, but the written digit
        # still has trillion precision. Only a magnitude attached to a number
        # owns that number's rounding; an unrelated letter T is not a scale.
        magnitude = re.search(rf"{_NUM_RE.pattern}\s*\)?\s*"
                              r"(?P<word>trillions?|tn|T|billions?|bln|bn|B|millions?|mln|mn|MM|M|thousands?|K)\b",
                              claim.value_text, re.I)
        word = magnitude.group("word").lower() if magnitude else ""
        factor = (1e12 if word in {"trillion", "trillions", "tn", "t"} else
                  1e9 if word in {"billion", "billions", "bln", "bn", "b"} else
                  1e6 if word in {"million", "millions", "mln", "mn", "mm", "m"} else
                  1e3 if word in {"thousand", "thousands", "k"} else SCALE_FACTOR.get(claim.scale, 1.0))
        step *= factor
    return 0.5 * step


def claimed_value(claim: Claim, *, change: Optional[bool] = None, loss_scope: bool = True) -> float:
    """The claim's value in full units (USD, not billions), signed by the draft's own words. change: the figure is
    a percent change (default: metric revenue_growth); then decline wording signs it and loss wording doesn't.
    loss_scope=False: any loss word in the span signs an income figure, as before its own clause was read
    (_income_subject); verify uses that reading only to keep a figure it fits from becoming "✗"."""
    v = float(claim.value)
    if claim.unit in ("USD", "shares"):
        v *= SCALE_FACTOR.get(claim.scale, 1.0)
    change = claim.metric == "revenue_growth" if change is None else change
    words = f"{claim.span_text} {claim.metric_text}".lower()
    if v > 0 and _NEGATIVE_RE.search(words):
        v = -v  # "free cash flow was negative $2.12 billion"
    elif v > 0 and change and _DOWN_RE.search(f"{_change_clause(claim)} {claim.metric_text}".lower()):
        v = -v  # "revenue fell 5%", but not "rose 5% versus a decline of 1%"
    elif (v > 0 and not change and claim.metric in ("net_income", "operating_income") and _LOSS_RE.search(words)
          and not (loss_scope and _income_subject(claim))):
        v = -v  # "a net loss of $1.2 billion", but not "net income of $2.6 billion versus a net loss of $0.5 billion"
    elif (v > 0 and not change and claim.metric in ("eps_diluted", "eps_basic")
          and (bound := _income_per_share(claim)) is not None and bound.loss):
        v = -v  # "a net loss of $50 million, or $0.25 per share"
    return v


# A change's own clause, for its direction words. Backward it runs to a sentence break or clause connector, and across an
# earlier figure only where the words between merely link two statements of the same change ("fell $1.3 billion, or
# 5.3%", "from $26.5 billion to $25.2 billion, or 5.3%"). Forward it runs to the first comma, connector, clause boundary
# or figure (a cause or concession: "despite lower traffic"), past the commas that enclose such a restatement
# ("$1.3 billion, or 5.3%, lower"; "5.3%, or $1.3 billion, lower"). A fronted subordinate clause ("Despite lower costs,
# revenue rose 5.3%") is not the figure's clause. "Revenue rose 5.3% versus a comparable sales decline of 1%" is a rise.
_CLAUSE_CONNECTOR_RE = re.compile(r"\b(?:versus|vs\.?|compared|while|whereas|but|and)\b", re.I)
_FIGURE_LINK_RE = re.compile(r"[\s,()]*(?:(?:or|to|from|by|of|at|an?)[\s,()]+)*", re.I)
_RESTATED_AFTER_RE = re.compile(rf"\s*,?\s*(?:or\s+|\(\s*)(?:{_FIGURE_RE.pattern})\s*\)?\s*,?", re.I)
_AFTER_CLAUSE_BREAK_RE = re.compile(r"[,;!?]|\.(?!\d)|\b(?:versus|vs\.?|compared|while|whereas|but|and|despite|although"
                                    r"|though|as|amid|on|with|driven|due|reflecting|led|thanks|helped|boosted|fueled"
                                    r"|fuelled|supported|including|primarily|mainly|largely|partly)\b", re.I)
_FRONTED_CLAUSE_RE = re.compile(r"\s*(?:despite|although|though|while|whereas|as|amid|unlike|with|after"
                                r"|even\s+(?:as|though))\b[^,;]*,", re.I)


def _change_clause(claim: Claim) -> str:
    """The words of this figure's own clause; the whole span when the figure can't be located in it."""
    pos, span = _value_pos(claim), claim.span_text or ""
    if pos is None:
        return span
    before = span[:pos]
    start = max(sentence_breaks(before), default=0)
    if fronted := _FRONTED_CLAUSE_RE.match(before, start):
        start = fronted.end()
    start = max([start, *(m.end() for m in _CLAUSE_CONNECTOR_RE.finditer(before, start))])
    figures = [m for m in _FIGURE_RE.finditer(_without_period_phrases(before))
               if re.search(r"\d", m.group(0)) and m.start() >= start]
    end = pos
    for figure in reversed(figures):
        if not _FIGURE_LINK_RE.fullmatch(before[figure.end():end]):
            start = figure.end()  # another statement's figure: this clause starts after it
            break
        end = figure.start()
    after = span[pos + len(claim.value_text):]
    if restated := _RESTATED_AFTER_RE.match(after):
        after = after[restated.end():]  # "5.3%, or $1.3 billion, lower"
    elif re.search(r"(?:,\s*or|\bor|\()\s*$", before):
        after = re.sub(r"^\s*[,)]", "", after)  # "$1.3 billion, or 5.3%, lower"
    after = _AFTER_CLAUSE_BREAK_RE.split(after, maxsplit=1)[0]
    if figure := next((m for m in _FIGURE_RE.finditer(_without_period_phrases(after)) if re.search(r"\d", m.group(0))),
                      None):
        after = after[:figure.start()]
    return f"{before[start:]} {after}"


# An income figure's own subject. A loss word elsewhere in the span belongs to another figure: in "Net income was $2.6
# billion, compared with a net loss of $0.5 billion a year earlier" only the $0.5 billion is a loss. A figure is read as
# income only when its own clause names income and no loss. That clause runs back to a sentence break or clause
# connector, past a fronted clause ("After a loss a year ago, net income was ...") and an aside on what the figure
# includes ("Net income, which included a $0.3 billion loss on a sale, was ..."); a clause with no subject of its own
# ("compared with $1.9 billion") takes its sentence's. Forward, to a clause break or "from"/"against", it must name no
# loss, and no loss noun may follow the figure ("a $2.6 billion loss", "$2.6 billion, a loss"); nor may the claim's own
# metric words name one. Otherwise a loss word anywhere in the span signs the figure, as it always did.
_INCOME_NOUN_RE = re.compile(r"\b(?:net\s+(?:income|earnings|profits?)|operating\s+(?:income|profits?)"
                             r"|income\s+from\s+operations|earnings|profits?)\b(?!\s+(?:per|before|tax))", re.I)
_SUBJECT_ASIDE_RE = re.compile(r",\s*(?:(?:which|that)\s+(?:included?|includes|reflect(?:ed|s)?|exclud(?:ed|es|e))"
                               r"|including|excluding|reflecting)\b[^;]*?,(?=\s*(?:was|were|is|are|came|totall?ed"
                               r"|amounted|stood|reached)\b)", re.I)
_LOSS_NOUN_AFTER_RE = re.compile(r"[\s,(]*(?:(?:an?|the|net|operating|pre-?tax|quarterly|gaap|adjusted|non-?gaap|in|of"
                                 r"|total)\s+)*loss(?:es)?\b", re.I)
_INCOME_CLAUSE_BREAK_RE = re.compile(rf"{_AFTER_CLAUSE_BREAK_RE.pattern}|\b(?:from|against)\b", re.I)


def _income_subject(claim: Claim) -> bool:
    """Whether an income figure's own clause reads it as income, whatever loss words the rest of its span has."""
    if _LOSS_RE.search((claim.metric_text or "").lower()):
        return False
    pos, span = _value_pos(claim), claim.span_text or ""
    if pos is None:
        return False
    before = span[:pos]
    start = max(sentence_breaks(before), default=0)
    if fronted := _FRONTED_CLAUSE_RE.match(before, start):
        start = fronted.end()
    head = _SUBJECT_ASIDE_RE.sub(lambda m: " " * len(m.group()), before[start:])
    clause = head[max([0, *(m.end() for m in _CLAUSE_CONNECTOR_RE.finditer(head))]):]
    own = clause if _LOSS_RE.search(clause.lower()) or _INCOME_NOUN_RE.search(clause) else head
    if _LOSS_RE.search(own.lower()) or not _INCOME_NOUN_RE.search(own):
        return False
    after = span[pos + len(claim.value_text):]
    if _LOSS_NOUN_AFTER_RE.match(after):
        return False
    after = _INCOME_CLAUSE_BREAK_RE.split(after, maxsplit=1)[0]
    if figure := next((m for m in _FIGURE_RE.finditer(_without_period_phrases(after)) if re.search(r"\d", m.group(0))),
                      None):
        after = after[:figure.start()]
    return not _LOSS_RE.search(after.lower())


def within(a: float, b: float, tol: float) -> bool:
    # IEEE infinity must not satisfy an infinite relative allowance (inf <= inf).
    # Preserve the existing finite-number tolerance, but fail closed on overflow.
    if not all(math.isfinite(v) for v in (a, b, tol)):
        return False
    difference = abs(a - b)
    allowance = tol + 1e-9 * max(1.0, abs(a), abs(b))
    return math.isfinite(difference) and math.isfinite(allowance) and difference <= allowance


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


def _visible_money_correction(claim: Claim, verdict: Verdict) -> Verdict:
    """Never round a monetary correction back to the very amount being rejected."""
    if (verdict.status != "wrong" or verdict.expected_value is None or claim.unit != "USD"
            or _MARGIN_CHANGE_UNIT.search(claim.value_text) or ", not " not in verdict.explanation):
        return verdict
    actual = verdict.expected_value * SCALE_FACTOR.get(claim.scale, 1.0)
    if not math.isfinite(actual) or actual == claimed_value(claim):
        return verdict
    short = fmt_money(actual)
    if short != fmt_money(claimed_value(claim)):
        return verdict
    # Full dollars make even a highly precise trillion claim reviewable. Replace
    # only the correction before "not", preserving the draft's quoted amount.
    amount = f"{abs(actual):,.6f}".rstrip("0").rstrip(".")
    precise = f"{'-' if actual < 0 else ''}${amount}"
    before, after = verdict.explanation.split(", not ", 1)
    return verdict.model_copy(update={"explanation": before.replace(short, precise) + ", not " + after})


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
        if phrase := periods.find_period_phrase(raw):
            raw = raw.replace(phrase, "", 1)
        raw = re.sub(r"\b(?:fully[\s-])?taxable[\s-]equivalent\b", "taxable-equivalent", raw, flags=re.I)
        words = [w for w in re.sub(r"(?:'s|’s)\b", "", raw).replace(",", " ").split()]
        keep = [w for w in words if w.casefold().strip(".") not in _GENERIC_QUALIFIERS
                and w.casefold() not in company and not re.fullmatch(r"(?:fy|q[1-4])?'?\d{2,4}", w.casefold())]
        if keep and len(keep) <= 4:
            return " ".join(keep)
    return None


def _company_words(claim: Claim) -> set[str]:
    """The words of the claim's company name, also without their dots: the qualifier rules compare "U.S." both as
    written and stripped ("u.s"), and a draft may spell U.S. Bancorp "US Bancorp"."""
    words = re.sub(r"(?:'s|’s)\b|\([^)]*\)", " ", claim.entity or "").casefold().split()
    return {form for w in words for form in (w, w.strip("."), w.replace(".", "")) if form}


def _value_pos(claim: Claim) -> Optional[int]:
    """Where value_text sits in span_text, or None when it isn't there or appears twice (then nothing local is known)."""
    vt, span = claim.value_text or "", claim.span_text or ""
    i = span.find(vt) if vt else -1
    if i < 0 or span.find(vt, i + 1) >= 0:
        return None
    return i


def _value_comparison_context(claim: Claim) -> Optional[tuple[str, str]]:
    """Comparison wording attached to this unique value, excluding other amounts.

    A suffix between two figures belongs to the first unless a new clause
    introduces the second. In particular, "106% from a year ago to $46.7B"
    cannot lend "a year ago" to the endpoint amount.
    """
    pos = _value_pos(claim)
    if pos is None:
        return None
    span = claim.span_text
    figures = list(_FIGURE_RE.finditer(_without_period_phrases(span)))
    previous = [hit for hit in figures if hit.end() <= pos]
    end = pos + len(claim.value_text)
    following = [hit for hit in figures if hit.start() >= end]
    before = span[previous[-1].end() if previous else 0:pos]
    after = span[end:following[0].start() if following else len(span)]
    boundary = r"[;!?]|\.(?!\d)|\b(?:and|while|whereas|but|or)\b"
    pieces = re.split(boundary, before, flags=re.I)
    # Without a clause boundary, text after the preceding amount describes
    # that amount, not this one. Prefix comparisons remain valid when this is
    # the clause's first figure ("year-over-year revenue growth of 106%").
    before = pieces[-1] if not previous or len(pieces) > 1 else ""
    after = re.split(boundary, after, maxsplit=1, flags=re.I)[0]
    return before, after


def _source_growth_comparisons(claim: Claim) -> set[str]:
    local = _value_comparison_context(claim)
    if local is None:
        return set()
    # Wording after a comma describes something else: "rose 18% from the first quarter, a slower pace
    # than last year" compares the pace, not this figure.
    before, after = local[0], local[1].split(",", 1)[0]
    parts = [_NAMED_QUARTER_RE.sub(lambda hit: " " * len(hit.group()), part) for part in (before, after)]
    return {comparison for comparison, pattern in _GROWTH_COMPARISONS
            if any(pattern.search(part) for part in parts)}


def _ambiguous_growth_endpoint(claim: Claim) -> bool:
    """"Up to X from Y" can describe growth to X or a maximum of X."""
    pos = _value_pos(claim)
    if pos is None:
        return False
    number = re.search(r"\d", claim.value_text)
    lead = claim.value_text[:number.start()] if number else ""
    before = strip_bound_currency_prefix(claim.span_text[:pos] + lead)
    if not re.search(r"\bup\s+to$", before, re.I):
        return False
    after = claim.span_text[pos + len(claim.value_text):]
    clause = re.split(r"[;!?]|\.(?!\d)", after, maxsplit=1)[0]
    return bool(re.match(r"\s*,?\s*(?:from|compared\s+(?:with|to)|versus|vs\.?)(?!\w)", clause, re.I)
                and _FIGURE_RE.search(clause))


# "FX-adjusted" (also "currency-adjusted", "adjusted for foreign exchange", "foreign exchange neutral") is
# constant-currency wording too, but it mostly stands before its noun ("FX-adjusted revenue rose 10%"), so it
# qualifies only a number whose own wording holds it (_own_wording), that it directly follows ("$10.0 billion,
# FX-adjusted"), or whose sentence opens with it ("On an FX-adjusted basis, ..."): never the nearest number of another
# clause ("Revenue rose 9%; FX-adjusted revenue rose 10%" leaves the 9% checked).
_FX_ADJUSTED = (r"(?:\b(?:fx|f/x|currency|foreign[\s-]+exchange)[\s-]+adjusted\b|\badjusted\s+for\s+(?:the\s+)?"
                r"(?:impact\s+of\s+)?(?:foreign[\s-]+)?(?:currenc(?:y|ies)|exchange(?:[\s-]+rates?)?|fx|f/x)\b"
                r"|\bforeign[\s-]+exchange[\s-]+neutral\b)")
_FX_ADJUSTED_RE = re.compile(_FX_ADJUSTED, re.I)
_FX_ADJUSTED_AFTER_RE = re.compile(rf"[\s,(]*(?:(?:on|in)\s+an?\s+)?{_FX_ADJUSTED}", re.I)
_FX_ADJUSTED_LEADING_RE = re.compile(rf"\W*(?:on|in)\s+an?\s+{_FX_ADJUSTED}\s+basis\b", re.I)


def _fx_adjusted(claim: Claim) -> bool:
    """FX-adjusted wording that qualifies this claim's own number (see above)."""
    span = claim.span_text or ""
    if _FX_ADJUSTED_RE.search(claim.metric_text or ""):
        return True
    if not _FX_ADJUSTED_RE.search(span):
        return False
    pos = _value_pos(claim)
    if pos is None:
        return True  # the number can't be located: any such wording counts, as for constant currency
    before = span[:pos]
    return bool(_FX_ADJUSTED_RE.search(_own_wording(claim))
                or _FX_ADJUSTED_AFTER_RE.match(span, pos + len(claim.value_text))
                or _FX_ADJUSTED_LEADING_RE.match(before, max(sentence_breaks(before), default=0)))


def currency_neutral(claim: Claim) -> bool:
    """True when constant-currency / FX-neutral wording qualifies this claim's own number: it is in metric_text, or
    in the span next to this number rather than another one ("up 18% (up 17% in constant currency)" qualifies
    the 17% only). When the number can't be located in the span, any such wording counts (the old, safe rule).
    FX-adjusted wording counts where _fx_adjusted binds it."""
    if _CURRENCY_NEUTRAL_RE.search(claim.metric_text or "") or _fx_adjusted(claim):
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


_BASIS_WORDS = r"(?:non[\s-]?gaap|adjusted|managed|fte|te|(?:fully[\s-])?taxable[\s-]equivalent|underlying|organic|pro[\s-]forma)"
# Wording that states a figure's basis ("on an adjusted basis", "(non-GAAP)"), as opposed to an adjective such as
# "non-GAAP" in "GAAP and non-GAAP gross margins were both 75.0%".
_BASIS_PHRASE_RE = re.compile(rf"\b(?:on|in)\s+an?\s+{_BASIS_WORDS}\s+basis\b|\(\s*{_BASIS_WORDS}\s*\)", re.I)
# Such a phrase right after a number belongs to it: "$9.86 billion, on an adjusted basis", "$2.62 (non-GAAP)".
_BASIS_TAIL_RE = re.compile(rf"\s*,?\s*(?:(?:on|in)\s+an?\s+{_BASIS_WORDS}\s+basis\b"
                            rf"|\(\s*(?:(?:on|in)\s+an?\s+)?{_BASIS_WORDS}(?:\s+basis)?\s*\))", re.I)
# A sentence that opens with a basis phrase puts every figure in it on that basis ("On an adjusted basis, ...").
_LEADING_BASIS_RE = re.compile(rf"\W*(?:on|in)\s+an?\s+{_BASIS_WORDS}\s+basis\b", re.I)
_GAAP_WORD_RE = re.compile(r"(?<![\w-])(?<!non )gaap\b", re.I)
# Company-defined measures that filings don't report under that name: checking one against a reported (GAAP or
# non-GAAP) figure could wrongly confirm or wrongly reject it.
_DISTINCT_MEASURE_RE = re.compile(r"\borganic(?:ally)?\b|\bpro[\s-]forma\b|\b(?:on|in)\s+an?\s+underlying\s+basis\b", re.I)


def _own_wording(claim: Claim) -> str:
    """The wording that describes this claim's own number: metric_text, a basis phrase opening its sentence, the
    text from the previous figure or sentence break to the number (period phrases are not figures), and basis
    wording right after it. Another figure's modifiers ("Adjusted EPS was $2.62, and revenue was ...") stay out."""
    own = claim.metric_text or ""
    pos = _value_pos(claim)
    if pos is None:
        return own
    before = claim.span_text[:pos]
    start = max(sentence_breaks(before), default=0)
    figures = [m.end() for m in _FIGURE_RE.finditer(_without_period_phrases(before)) if re.search(r"\d", m.group(0))]
    leading = _LEADING_BASIS_RE.match(before[start:])
    tail = _BASIS_TAIL_RE.match(claim.span_text[pos + len(claim.value_text):])
    return " ".join([own, leading.group(0) if leading else "", before[max([start] + figures):],
                     tail.group(0) if tail else ""])


def basis_conflict(claim: Claim) -> Optional[str]:
    """A non-GAAP basis phrase bound to this claim's own number although the claim was read as GAAP ("On an
    adjusted basis, net income was $9.86 billion"), or None. With the basis unclear, it is never checked as GAAP."""
    if claim.basis != "GAAP":
        return None
    own = _own_wording(claim)
    hit = _BASIS_PHRASE_RE.search(own)
    if not hit or _GAAP_WORD_RE.search(_NON_GAAP_WORDS_RE.sub(" ", own)):
        return None
    return hit.group(0).strip("() ")


def _non_gaap_measure_qualifiers(text: str) -> tuple[str, ...]:
    """Specific non-GAAP meanings; the generic basis name alone does not name a separate measure."""
    qualifiers = set()
    for hit in re.finditer(r"\b(?:adjusted|managed|fte|te|(?:fully[\s-])?taxable[\s-]equivalent)\b", text, re.I):
        word = hit.group(0).casefold()
        qualifiers.add(word if word in ("adjusted", "managed") else "taxable-equivalent")
    return tuple(sorted(qualifiers))


def _claim_measure_qualifiers(claim: Claim) -> tuple[str, ...]:
    # A company name such as TE Connectivity is not a revenue qualifier.
    own = re.sub(rf"(?<!\w){re.escape(claim.entity)}(?!\w)", "", _own_wording(claim), flags=re.I)
    return _non_gaap_measure_qualifiers(own)


def distinct_measure(claim: Claim) -> Optional[str]:
    """Organic, pro forma or "underlying basis" wording bound to this claim's own number, or None."""
    hit = _DISTINCT_MEASURE_RE.search(_own_wording(claim))
    return hit.group(0) if hit else None


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
    raw = re.sub(r"\b(?:fully[\s-])?taxable[\s-]equivalent\b", "taxable-equivalent", m.group(1), flags=re.I)
    words = [w for w in raw.replace(",", " ").split()]
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
    derived: Optional[str] = None    # company margin, computed from income / revenue
    absolute: bool = False           # the change of growth_base in dollars or per share ("up $0.32"), not a percent


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
    "higher lower expanded expansion contracted narrowed widened improved change changed year over quarter yoy qoq y/y q/q sequential sequentially from "
    "ago earlier before prior previous last compared versus vs vs. by to at was were is are has have had a an the of for in on its basis "
    "their our record reported posted delivered that which period periods".split())


# An acronym in parentheses that defines the EPS phrase right before it is that phrase, not a second metric: 'GAAP
# earnings per share ("EPS")', in any quote style or none. ("free cash flow (FCF)" is read by its alias.)
_DEFINED_EPS_RE = re.compile(r"(\b(?:(?P<word>diluted|basic)\s+)?(?:earnings|net\s+income)\s+per\s+(?:common\s+)?"
                             r"(?:(?:diluted|basic)\s+)?share)"
                             r"(\s*\(\s*[\"'“”‘’]?\s*(?:(?P=word)\s+)?EPS\s*[\"'“”‘’]?\s*\))", re.I)


def _without_defined_acronyms(text: str) -> str:
    """Blank such a defining acronym, keeping offsets."""
    return _DEFINED_EPS_RE.sub(lambda m: m.group(1) + " " * len(m.group(3)), text)


# A comparison period named right after an amount ("compared to $33.6 billion for the same quarter last year", "in the
# prior-year period", "the year-ago quarter") says which period the amount is for; it does not qualify the metric.
# "same" must name a quarter or period of an earlier year: same-store, comparable or identical sales stay measures of
# their own, and "the same quarter" with no year stays unread.
_SAME_PERIOD_RE = re.compile(
    r"same\s+(?:fiscal\s+)?(?:quarter|period)\s+(?:(?:of\s+)?(?:last|the\s+prior|the\s+previous)\s+(?:fiscal\s+)?year"
    r"|a\s+year\s+(?:ago|earlier|before)|(?:of\s+)?(?:the\s+)?(?:prior|previous)[\s-]+year)\b"
    r"|(?:prior|previous)[\s-]+(?:fiscal[\s-]+)?year\s+(?:quarter|period)\b|year[\s-]+ago\s+(?:quarter|period)\b", re.I)
# As the label of a level amount (_comparison_period_after reads it), or as the base of a change.
_LEVEL_PERIOD_LABEL_RE = re.compile(rf"[\s,()]*(?:(?:in|for|during|over)\s+)?(?:the\s+)?(?:{_SAME_PERIOD_RE.pattern})",
                                    re.I)
# "over the same quarter last year", "over the prior-year period": only a same-period label, never "over a year ago"
_OVER_PERIOD_LABEL_RE = re.compile(rf"[\s,()]*over\s+(?:the\s+)?(?P<label>{_SAME_PERIOD_RE.pattern})", re.I)
_CHANGE_PERIOD_LABEL_RE = re.compile(r"[\s,()]*(?:(?:from|over|than|in|for|during|since|versus|vs\.?"
                                     rf"|compared\s+(?:with|to))\s+)?(?:the\s+)?(?:{_SAME_PERIOD_RE.pattern})", re.I)
# Same-store, comparable (store) or identical sales and "comps" are measures of their own, never revenue: also where a
# change's own words need not name its base ("Comps rose 5% from the same quarter last year").
_DISTINCT_SALES_RE = re.compile(r"\bsame[\s-]+stores?\b|\bcomparable(?:[\s-]+stores?)?[\s-]+sales\b"
                                r"|\bcomp(?:[\s-]+stores?)?[\s-]+sales\b|\bcomps\b"
                                r"|\bidentical(?:[\s-]+stores?)?[\s-]+sales\b", re.I)


# Where an amount's own words end: another clause or a cause ("driven by", "on", "as", ", including"). Words beyond
# such a break describe something else: "Total revenue was $26.5 billion, driven by comparable sales growth of 2.1%".
_CLAUSE_BREAK_RE = re.compile(r"[,;:()—–―]|\.(?!\d)|[!?]|\b(?:and|or|but|while|whereas|with|driven|due|on|as|including"
                              r"|reflecting|led|thanks|helped|boosted|fueled|fuelled|supported|amid|despite|primarily"
                              r"|mainly|largely|partly)\b", re.I)
# The number's immediate description: "a 5% increase in comparable sales", "5% comparable sales growth", "up 5% in
# same-store sales".
_COMPARISON_WORDS = r"(?:year[\s-]+over[\s-]+year|yoy|y/y|quarter[\s-]+over[\s-]+quarter|q/q|sequential)"
_DISTINCT_AFTER_RE = re.compile(rf"\s*(?:{_COMPARISON_WORDS}\s+)?(?:(?:increase|growth|gain|rise|jump|decline|decrease"
                                r"|drop|change|improvement)s?\s+)?(?:(?:in|of)\s+)?(?:(?:the|its|our|their)\s+)?"
                                rf"(?:{_DISTINCT_SALES_RE.pattern})"
                                # "2.1% on a comparable-store basis": the basis names the measure (constant-currency and
                                # organic wording are distinct measures of their own already); so does a plain "on a
                                # comparable basis", the company's own comparable measure, right after the number
                                rf"|\s*(?:{_COMPARISON_WORDS}[\s,]+)?[\s,(]*on\s+an?\s+(?:comparable|same|identical)"
                                r"(?:[\s-]+stores?(?:[\s-]+sales)?|[\s-]+sales)\s+basis\b"
                                rf"|\s*(?:{_COMPARISON_WORDS}[\s,]+)?[\s,(]*on\s+an?\s+comparable\s+basis\b", re.I)


def _distinct_sales(claim: Claim) -> bool:
    """Such a measure as this amount's own: named in metric_text, as the subject that leads straight into the number
    ("Comps rose 5%", "comparable sales growth of 2.1%"), or as the number's immediate description ("a 5% increase in
    comparable sales"). Only the amount's own words count, so a comps clause after a comma, a connector or another
    figure never unreads a revenue figure; an amount that can't be located has only its metric_text."""
    if _DISTINCT_SALES_RE.search(claim.metric_text or ""):
        return True
    pos, span = _value_pos(claim), claim.span_text or ""
    if pos is None:
        return False
    before = span[:pos]
    starts = [*sentence_breaks(before), *(m.end() for m in _CLAUSE_BREAK_RE.finditer(before)),
              *(m.end() for m in _FIGURE_RE.finditer(_without_period_phrases(before)) if re.search(r"\d", m.group(0)))]
    region = before[max(starts, default=0):]
    leads = list(_DISTINCT_SALES_RE.finditer(region))
    if leads and not _leftover_words(_without_period_phrases(region[leads[-1].end():]), (0, 0),
                                     _company_words(claim), None):
        return True  # the measure is the subject the number belongs to
    return bool(_DISTINCT_AFTER_RE.match(span, pos + len(claim.value_text)))


# Words that never change what a figure measures (round 9). ChatGPT-style drafts that trial users paste use them freely:
# "revenue increased approximately 4% year over year", "Operating income also grew 3%", "gross margin declined slightly
# to 11.02%". A hedge word right before its number is still read as a hedge (hedge_of); bounds and comparisons ("more
# than", "nearly", "almost", "at least", "below", "above", "exceeding") are not here and stay qualifiers, and so does
# "representing", which introduces shares ("representing 85% of total revenue") as well as growth.
_NEUTRAL_WORDS = frozenset(
    # hedges, away from the number they hedge
    "approximately approx about roughly around some "
    # discourse adverbs
    "also again further still meanwhile however additionally moreover likewise "
    # degree adverbs on a change
    "slightly modestly moderately marginally significantly substantially considerably sharply strongly steadily "
    # evaluative adjectives
    "all-time strong solid robust healthy "
    # reporting and linking verbs, a subject pronoun, a period preposition, a restating link, totals of the world. Not
    # "both": "revenue and EPS both up more than 20%" gives two figures one loose number ("were both" is framing).
    "achieves achieved achieving delivers delivering reflects reflected compares comparison announced remained remains "
    "flat unchanged essentially we during or worldwide global".split())


def _leftover_words(text: str, phrase: tuple[int, int], company: set[str], qualifier: Optional[str]) -> list[str]:
    """Words of a metric description outside its metric phrase that are not framing (change and comparison words,
    totals, bases, periods, the company, the qualifier taken as segment). Any left means the wording says more than
    the phrase ('operating income from Reality Labs'), so the figure isn't read as the company-level metric."""
    rest = f"{text[:phrase[0]]} {text[phrase[1]:]}"
    rest = re.sub(r"\b(?:fully[\s-])?taxable[\s-]equivalent\b", "taxable-equivalent", rest, flags=re.I)
    qual = {w.casefold() for w in (qualifier or "").split()}
    out = []
    for w in re.findall(r"[A-Za-z][\w&'/.-]*", rest):
        k = w.casefold().strip(".")
        if k.endswith("s'") and k[:-1] in company:
            k = k[:-1]  # Interactive Brokers' is the company possessive, not a scope qualifier.
        if (k in _FRAME_WORDS or k in _GENERIC_QUALIFIERS or k in _NEUTRAL_WORDS or k in company or k in qual
                or k in {"diluted", "basic", "consolidated", "gaap", "non-gaap", "segment", "percent", "percentage",
                         "points", "fiscal", "quarterly", "annual"}
                or re.fullmatch(r"(?:fy|q[1-4])?'?\d{0,4}", k)):
            continue
        out.append(w)
    return out


_LEVEL_METRIC_ALIASES = {
    "revenue": r"(?:net\s+)?(?:revenues?|sales)|top[ -]?line",
    "segment_revenue": r"(?:(?:segment|business)\s+)?(?:revenues?|sales)",
    "free_cash_flow": r"free\s+cash\s+flows?\s*\(FCF\)",
    "operating_cash_flow": r"(?:net\s+)?cash\s+(?:flows?\s+from|(?:generated|provided)\s+(?:from|by))"
                           r"\s+operating\s+activities",
    "operating_expenses": r"total\s+expenses",
    "gross_margin": r"gross\s+(?:profit\s+)?margins?",
    "net_margin": r"net\s+(?:profit\s+|income\s+)?margins?",  # "net income margin" is net margin
    "operating_margin": r"operating\s+margins?",
    "eps_diluted": r"(?:diluted\s+(?:EPS|earnings)|(?:earnings|earned)?\s*per\s+(?:common\s+)?diluted\s+share)",
    "eps_basic": rf"(?:basic\s+(?:EPS|earnings)|EPS|(?:earnings|net\s+income){_TO_COMMON_HOLDERS}"
                 r"\s+per\s+(?:common\s+)?share|(?:earnings|earned)?\s*per\s+(?:common\s+)?basic\s+share)",
    "net_income": r"net\s+loss(?:es)?",
    "operating_income": r"operating\s+loss(?:es)?",
    "research_and_development": r"(?:research\s+(?:and|&)\s+development|R&D)(?:\s+(?:expenses?|costs?))?",
    "capital_returned": r"(?:capital|cash)\s+returned(?:\s+to\s+(?:shareholders|stockholders))?",
    "repurchase_authorization_remaining": r"remaining\s+(?:share\s+)?(?:buyback|repurchase)\s+authorization"
                                          r"|(?:share\s+)?repurchase\s+authorization\s+remaining",
    "dividend_per_share": r"dividends?\s+per\s+(?:common\s+)?share",
    "tax_rate": r"(?:effective\s+)?tax\s+rate",
}


def _level_metric_phrases(metric: str, text: str, segment: Optional[str] = None) -> list[tuple[int, int]]:
    """Locate the canonical metric's own wording, keeping the longest supported aliases.

    The enum is a proposal, not permission to discard words such as 'excluding
    stock compensation' or 'from discontinued operations'. Unknown descriptions
    cannot borrow the company-wide metric merely because the model selected it.
    """
    patterns = [rx for base, rx in _BASE_METRIC_WORDS if base == metric]
    patterns.append(re.compile(rf"\b{re.escape(metric.replace('_', ' '))}\b", re.I))
    if metric in _LEVEL_METRIC_ALIASES:
        patterns.append(re.compile(rf"(?<!\w)(?:{_LEVEL_METRIC_ALIASES[metric]})(?!\w)", re.I))
    # A declared segment name can itself contain a metric word: "Sales and
    # trading revenue" is one revenue metric, not both sales and revenue. Only
    # complete occurrences of that segment are masked; its own fact is still
    # required downstream, and any additional qualifiers remain visible.
    segment_spans = ([hit.span() for hit in re.finditer(rf"\b{re.escape(segment)}\b", text, re.I)]
                     if segment else [])
    matches = [hit.span() for pattern in patterns for hit in pattern.finditer(text)
               if not any(a <= hit.start() and hit.end() <= b for a, b in segment_spans)]
    chosen: list[tuple[int, int]] = []
    for hit in sorted(set(matches), key=lambda hit: hit[1] - hit[0], reverse=True):
        if not any(hit[0] < b and hit[1] > a for a, b in chosen):
            chosen.append(hit)
    return sorted(chosen)


# Where a clause's subject ends: its verb, or "of"/"at"/"to" leading into a figure.
_SUBJECT_END_RE = re.compile(r"\b(?:was|were|is|are|has|have|had|rose|fell|grew|increas\w*|decreas\w*|declin\w*|expand\w*"
                             r"|contract\w*|improv\w*|reach\w*|total\w*|came|stood|jump\w*|climb\w*|dropp?\w*|narrow\w*"
                             r"|widen\w*|of|at|to)\b|[:=]", re.I)


def _clause_subject(claim: Claim) -> Optional[str]:
    """The subject of this figure's clause when other figures stand between the metric it names and the number, so
    _local_level_description reads nothing: "Adjusted operating margin in the region" of "Adjusted operating margin in
    the region decreased 40 basis points to 13.9%" (Allegion). Its words up to the clause's verb qualify every figure
    the clause states, as they qualify the first; words after the verb that belong to another figure ("rose 24%, or 23%
    FX-neutral, to $119.8B") don't. None when the clause names the metric nowhere before the number."""
    pos = _value_pos(claim)
    if pos is None:
        return None
    before = claim.span_text[:pos]
    phrases = _level_metric_phrases(claim.metric, before, claim.segment)
    if not phrases:
        return None
    start, end = phrases[-1]
    # Only one clause may run from the metric to the number ("in the region decreased 40 basis points to"): an aside,
    # a comma or a connector there means the metric is another figure's ("Despite net income of $4.43 billion a year
    # ago, Merck posted a loss of $1.34 billion").
    between = re.sub(rf"(?<![A-Za-z]){_FIGURE_RE.pattern}", " ", _without_period_phrases(before[end:]), flags=re.I)
    if re.search(r"[,;:()—–]|\b(?:and|but|or|while|whereas|which|that|despite|although|though|after|before)\b",
                 between, re.I):
        return None
    boundaries = sentence_breaks(before[:start])
    boundaries += [hit.end() for hit in re.finditer(r",\s*(?:and|but|while|whereas|as)\b", before[:start])]
    boundaries += [hit.end() for hit in _FIGURE_RE.finditer(_without_period_phrases(before[:start]))
                   if re.search(r"\d", hit.group(0))]
    verb = _SUBJECT_END_RE.search(before, end)
    if verb is None:
        return None
    subject = before[max(boundaries, default=0):verb.start()]
    if any(re.search(r"\d", hit.group(0)) for hit in _FIGURE_RE.finditer(_without_period_phrases(subject))):
        return None
    return subject


def _local_level_description(claim: Claim, *, growth: bool = False) -> Optional[str]:
    """Recover modifiers attached to the metric nearest this claim's own number.

    Stop the left context after a prior figure or sentence, and require no other
    figure between the named metric and this value. This avoids borrowing a
    different claim's modifiers in multi-number sentences. Number-only spans
    cannot recover omitted semantics and still rely on metric_text.
    """
    pos = _value_pos(claim)
    if pos is None:
        return None
    before = claim.span_text[:pos]
    phrases = _level_metric_phrases(claim.metric, before, claim.segment)
    if not phrases:
        return None
    start, end = phrases[-1]
    intervening = list(_FIGURE_RE.finditer(_without_period_phrases(before[end:])))
    if len(intervening) > (1 if growth else 0):
        return None  # e.g. revenue rose 24%, or 23% FX-neutral, to $119.8B
    boundaries = sentence_breaks(before[:start])
    boundaries += [hit.end() for hit in re.finditer(r",\s*(?:and|but|while|whereas|as)\b", before[:start])]
    boundaries += [hit.end() for hit in _FIGURE_RE.finditer(_without_period_phrases(before[:start]))]
    left = max(boundaries, default=0)
    description = before[left:]
    if growth:
        # "Revenue excluding China was $96B, up 106%" carries the same
        # qualified subject through its level amount. Blank just that amount,
        # not digits inside a label such as "Q1".
        description = re.sub(rf"(?<![A-Za-z]){_FIGURE_RE.pattern}", " ", _without_period_phrases(description),
                             flags=re.I)
    # Bounds and signs constrain the number rather than changing the metric.
    description = strip_bound_currency_prefix(description)
    description = re.sub(r"\b(?:negative|minus)\s*$", "", description, flags=re.I)
    for _kind, _inclusive, pattern in _HEDGES:
        if hit := re.search(pattern.pattern, description, re.I):
            description = description[:hit.start()]
            break
    if tail := _trailing_qualifier(claim):
        description += " " + tail
    return description


def _trailing_qualifier(claim: Claim) -> str:
    """Wording directly after this claim's own number that qualifies it, or "".

    A directly trailing qualifier belongs to this number; a new clause does
    not ("$59.7B, and European revenue was ..."). Period wording is harmless.
    """
    pos = _value_pos(claim)
    if pos is None:
        return ""
    tail = claim.span_text[pos + len(claim.value_text):]
    comparison = "|".join(f"(?:{pattern.pattern})" for _, pattern in _GROWTH_COMPARISONS)
    income_tail = r"|including" if claim.metric == "net_income" else ""
    if not re.match(rf"^[\s,()]*(?:(?:{comparison})[\s,()]*)?\b(?:excluding|before|after|from|for|in|attributable"
                    rf"|available|applicable{income_tail})\b", tail, re.I) and not _OVER_PERIOD_LABEL_RE.match(tail):
        return ""
    conjunction = r"and"
    if claim.metric == "net_income":
        conjunction += (r"(?!\s+(?:excluding|including|before|after|from|for|in|attributable|available|applicable)\b)")
    tail = re.split(rf"[;.!?]|\b(?:{conjunction}|while|whereas|but|with)\b", tail, maxsplit=1, flags=re.I)[0]
    if _FIGURE_RE.search(_without_period_phrases(tail)):
        return ""
    # "for the same quarter last year" names the period: a change's base (its YoY wording decides the comparison), or a
    # level amount's period when _comparison_period_after reads the same label, so the year-ago quarter is checked
    # (_comparison_base, only for comparison "none"). Otherwise it stays a qualifier, never a check of another period.
    change = claim.unit == "%" and _is_change(claim)
    label = (_CHANGE_PERIOD_LABEL_RE if change else _LEVEL_PERIOD_LABEL_RE).match(tail)
    if label and (change or (claim.comparison == "none" and _comparison_period_after(claim)[0])):
        rest = tail[label.end():]
        return " " * label.end() + rest if rest.strip(" ,()") else ""
    return tail


def _per_share_wording(claim: Claim) -> bool:
    """An EPS claim whose net-income words sit inside its own EPS phrase ("net income attributable to common
    stockholders per diluted share"): they name the numerator of EPS, not a net-income line."""
    if claim.metric not in ("eps_diluted", "eps_basic"):
        return False
    text = claim.metric_text or ""
    eps = _level_metric_phrases(claim.metric, text)
    return bool(eps) and all(any(a <= start and end <= b for a, b in eps)
                             for start, end in _level_metric_phrases("net_income", text))


def _named_income_line(claim: Claim) -> Optional[str]:
    """Use the amount's own source wording; a model-added label cannot name a different filed line."""
    if claim.metric != "net_income" and not _level_metric_phrases("net_income", claim.metric_text):
        return None
    if _per_share_wording(claim):
        return None
    income = claim.model_copy(update={"metric": "net_income"})
    words = _local_level_description(income, growth=claim.unit == "%") or _trailing_qualifier(income)
    if not words and claim.span_text.strip(" .;\n\t") == claim.value_text.strip(" .;\n\t"):
        words = claim.metric_text  # a number-only extraction has no recoverable source label
    common = bool(_COMMON_LINE_RE.search(words))
    including = bool(_INCLUDING_NCI_RE.search(words) or _CONSOLIDATED_INCOME_RE.search(words))
    if common and including:
        return "ambiguous"
    return "net_income_to_common" if common else "net_income_including_nci" if including else None


def _income_reading_claim(claim: Claim) -> Claim:
    """Remove only the supported line name for the ordinary scope guard; keep every other qualifier."""
    def strip(text: str) -> str:
        for pattern in (_COMMON_LINE_RE, _INCLUDING_NCI_RE, _CONSOLIDATED_INCOME_RE):
            # Keep an additional same-amount qualifier visible after removing its preceding line name.
            text = re.sub(rf"(?:{pattern.pattern})(?:\s+and\b)?", lambda m: " " * len(m.group()), text,
                          flags=re.I)
        return text
    return claim.model_copy(update={"metric": "net_income", "metric_text": strip(claim.metric_text),
                                    "span_text": strip(claim.span_text)})


# "Net income attributable to <the company itself>" is the parent line, us-gaap:NetIncomeLoss: plain net income. The
# company is "the Company", its own name words, or (in verify(), with the store) any name or ticker of the claim's own
# company. Every other attribution (common stockholders, noncontrolling interests, another company) stays as it is.
_PARENT_ATTRIBUTION_RE = re.compile(r"\b(?:net\s+(?:income|earnings|profit|loss(?:es)?)|earnings)"
                                    r"(\s+attributable\s+to\s+)", re.I)
_NAME_WORD_RE = re.compile(r"[A-Za-z0-9][\w&.'’-]*")


def _parent_name_end(text: str, start: int, company: set[str],
                     is_company: Optional[Callable[[str], bool]]) -> Optional[int]:
    """Where the claim's own company, named at text[start:], ends: the longest run of up to six words that is "the
    Company", the company's name words (with a leading "the" or a closing legal suffix), or a name is_company
    accepts."""
    words: list[re.Match[str]] = []
    for m in _NAME_WORD_RE.finditer(text, start):
        if len(words) == 6 or text[(words[-1].end() if words else start):m.start()].strip():
            break
        words.append(m)
    for k in range(len(words), 0, -1):
        name = re.sub(r"(?:'s|’s)$", "", text[words[0].start():words[k - 1].end()])
        keys = [re.sub(r"(?:'s|’s)$", "", w.group()).casefold().strip(".") for w in words[:k]]
        if keys[:1] == ["the"]:
            keys = keys[1:]
        if len(keys) > 1 and keys[-1].replace(".", "") in _LEGAL_FORM_WORDS:
            keys = keys[:-1]
        if keys == ["company"] or (keys and all(w in company for w in keys)) or (is_company and is_company(name)):
            return words[k - 1].end()
    return None


def _without_parent_attribution(claim: Claim, is_company: Optional[Callable[[str], bool]] = None) -> Claim:
    """The claim with "attributable to <its own company>" after a net-income phrase removed (blanked in span_text, whose
    offsets must hold), or the claim itself when it names no such attribution."""
    company = _company_words(claim)

    def strip(text: str) -> str:
        for m in reversed(list(_PARENT_ATTRIBUTION_RE.finditer(text))):
            end = _parent_name_end(text, m.end(1), company, is_company)
            if end is not None:
                text = text[:m.start(1)] + " " * (end - m.start(1)) + text[end:]
        return text

    span, metric_text = strip(claim.span_text or ""), strip(claim.metric_text or "")
    if (span, metric_text) == (claim.span_text or "", claim.metric_text or ""):
        return claim
    return claim.model_copy(update={"span_text": span, "metric_text": " ".join(metric_text.split())})


# A bare per-share amount bound to the net-income amount right before it in one sentence: "GAAP net income was $362
# million, or $1.28 per share", "earnings of $362 million ($1.28 per share)", "..., or $1.28 a share".
_BARE_PER_SHARE = r"(?:per|a)\s+(?:common\s+)?share\b"
_INCOME_FIGURE_RE = re.compile(r"\bnet\s+(?:income|earnings|profit|loss(?:es)?)\b|\bearnings\b", re.I)
_EPS_BASIS_WORDS_RE = re.compile(r"\b(?:non[\s-]?gaap|adjusted)\b", re.I)
# The newswire verb form "<Company> earned $5.3 billion, or $2.65 a share" ("lost": a net loss); only an article or
# basis word may stand between the verb and the amount. Its subject may name the company itself, "the company", "it"
# or "we".
_EARNED_RE = re.compile(r"\b(?P<verb>earned|lost)(?:\s+(?:an?|adjusted|non[\s-]?gaap|gaap))*\s*$", re.I)
_SELF_WORDS = frozenset({"it", "we", "said"})


class _IncomeShare(NamedTuple):
    start: int   # where the net-income wording begins in span_text
    loss: bool   # a net loss: the draft's per-share figure is negative


def _income_per_share(claim: Claim) -> Optional[_IncomeShare]:
    """The net-income amount a bare per-share figure ("per share", "a share") is attached to, or None. Only that binding
    reads such a figure as EPS, with the usual basic/diluted alternatives: dividends, FFO, book value, cash flow,
    distributable earnings, per-ADS figures and per-share impacts are never attached this way. The amount's own words
    must be net income (or a net loss, or earnings) and nothing more, and their basis must agree with the claim's
    ("adjusted net income" gives non-GAAP EPS; an adjusted amount read as GAAP is left unread)."""
    if (claim.metric not in ("eps_diluted", "eps_basic") or claim.unit not in ("USD/share", "USD")
            or claim.scale != "units" or (claim.segment or "").strip()
            or not re.fullmatch(rf"\s*{_BARE_PER_SHARE}\s*", claim.metric_text or "", re.I)):
        return None
    claim = _without_parent_attribution(claim)
    pos = _value_pos(claim)
    if pos is None:
        return None
    span, company = claim.span_text, _company_words(claim)
    words = re.match(rf"\s*{_BARE_PER_SHARE}", span[pos + len(claim.value_text):], re.I)
    if words is None:
        return None
    # The per-share figure's own trailing qualification ("per share from continuing operations") leaves it unread, and
    # so does a comparison period after it ("or $2.65 per share, a year ago"): the period check reads none there.
    own = claim.model_copy(update={"value_text": span[pos:pos + len(claim.value_text) + words.end()]})
    tail = _without_period_phrases(re.sub(r"(?:'s|’s)\b", "", _trailing_qualifier(own)))
    if _leftover_words(tail, (0, 0), company, None):
        return None
    rest = re.split(r"[;.!?]|\b(?:and|while|whereas|but|with)\b", span[pos + len(own.value_text):], maxsplit=1,
                    flags=re.I)[0]
    if figure := next((m for m in _FIGURE_RE.finditer(_without_period_phrases(rest)) if re.search(r"\d", m.group(0))),
                      None):
        rest = rest[:figure.start()]
    if _SAME_PERIOD_RE.search(rest) or any(pattern.search(rest) for _, pattern in _GROWTH_COMPARISONS):
        return None
    before = span[:pos]
    figures = [m for m in _FIGURE_RE.finditer(_without_period_phrases(before)) if re.search(r"\d", m.group(0))]
    if not figures:
        return None
    amount = figures[-1]
    link = before[amount.end():]
    if not claim.value_text.lstrip().startswith(("$", "US$", "USD")):
        link = re.sub(r"(?:US\s*\$|\$)\s*$", "", link)
    if (not re.fullmatch(r"\s*(?:,\s*or|or|\()\s*", link, re.I) or "%" in amount.group()
            or not re.search(r"\$|USD", amount.group())):
        return None
    head = before[:amount.start()]
    left = max([figures[-2].end() if len(figures) > 1 else 0, *sentence_breaks(head),
                *(m.end() for m in re.finditer(r",\s*(?:and|but|while|whereas|as)\b", head, re.I))])
    blank = lambda m: " " * len(m.group())  # noqa: E731 - offsets must hold
    subject = _without_period_phrases(re.sub(r"(?:'s|’s)\b", blank, head[left:]))
    hits = list(_INCOME_FIGURE_RE.finditer(subject))
    verb = None if hits else _EARNED_RE.search(subject)
    if ((len(hits) != 1 and verb is None)
            or _INCLUDING_NCI_RE.search(subject) or _CONSOLIDATED_INCOME_RE.search(subject)
            or _NON_GAAP_WORDS_RE.search(_EPS_BASIS_WORDS_RE.sub(" ", subject))  # managed, FTE, pro forma, organic...
            or re.search(r"\bsegments?\b|\btrailing\b|\bttm\b|\bltm\b|\btwelve[\s-]+months?\b", subject, re.I)
            or any(ch.isalpha() and not ch.isascii() for ch in subject)):
        return None
    if verb is not None:
        # "<Company> earned $5.3 billion, or $2.65 a share" (newswire): the company itself must be the subject, and
        # the amount has no metric noun of its own (the link right after it already rules out "earned $X in revenue")
        who = subject[:verb.start()]
        words = {w.casefold().strip(".") for w in re.findall(r"[A-Za-z][\w&'’.-]*", who)}
        if _leftover_words(who, (0, 0), company | _SELF_WORDS, None) or not words & (company | {"company", "it", "we"}):
            return None
        loss = verb["verb"].casefold() == "lost"
    else:
        phrase = hits[0].span()
        subject = _COMMON_LINE_RE.sub(blank, subject)  # EPS is computed on income available to common
        if _leftover_words(_level_sentence_framing(subject, phrase), phrase, company, None):
            return None
        loss = bool(re.search(r"\bloss(?:es)?\b", hits[0].group(), re.I))
    non_gaap = bool(_EPS_BASIS_WORDS_RE.search(subject))
    gaap = bool(_GAAP_WORD_RE.search(_EPS_BASIS_WORDS_RE.sub(" ", subject)))
    if (gaap and non_gaap) or (non_gaap and claim.basis == "GAAP") or (gaap and claim.basis == "non-GAAP"):
        return None
    return _IncomeShare(left + re.match(r"[\s,;:]*", subject).end(), loss)


def income_share_start(claim: Claim) -> Optional[int]:
    """Where, in claim.span_text, the net-income wording a bare per-share figure is bound to begins (_income_per_share),
    or None. Shared with extraction's source-context recovery."""
    bound = _income_per_share(claim)
    return None if bound is None else bound.start


_NOT_SENTENCE_END = frozenset("inc corp co ltd plc llc no vs approx est jan feb mar apr jun jul aug sep sept oct "
                              "nov dec".split())


def sentence_breaks(text: str) -> list[int]:
    """Offsets just after sentence or clause punctuation. "U.S.", "Inc." and initials are not breaks;
    "$5.2B." is. Shared with extraction's source-context recovery."""
    out = [m.end() for m in re.finditer(r"[;!?\n]", text)]
    for m in re.finditer(r"\.(?=\s|$)", text):
        word = re.search(r"(\S?)([A-Za-z][A-Za-z.]*)$", text[:m.start()])
        if word and ("." in word.group(2) or word.group(2).casefold() in _NOT_SENTENCE_END
                     or (len(word.group(2)) == 1 and not word.group(1).isdigit())):
            continue
        out.append(m.end())
    return sorted(out)


def _without_period_phrases(text: str) -> str:
    """Ignore recognized period scaffolding without shifting source offsets."""
    while phrase := periods.find_period_phrase(text):
        text = text.replace(phrase, " " * len(phrase), 1)
    return text


def _level_sentence_framing(text: str, phrase: tuple[int, int]) -> str:
    """Blank reporting syntax, preserving offsets and all metric modifiers.

    Position matters: "with revenue" can introduce a clause; "net income with
    revenue" is a composite metric and must not gain a blanket 'with' exemption.
    Likewise 'both' belongs to 'were both', not an arbitrary metric definition.
    """
    blank = lambda hit: " " * len(hit.group(0))
    prefix, suffix = text[:phrase[0]], text[phrase[1]:]
    prefix = re.sub(r"^[\s,]*(?:with|where|while|whereas|but)\b", blank, prefix, flags=re.I)
    prefix = re.sub(r"\b(?:now\s+)?(?:report(?:s|ed|ing)?|generat(?:es|ed|ing)|produc(?:es|ed|ing)|post(?:s|ed|ing))\b",
                    blank, prefix, flags=re.I)
    # Focusing on this one metric ("R&D alone came to ...") does not
    # redefine it; a retained region/exclusion still goes through the guard.
    suffix = re.sub(r"^\s+alone\b", blank, suffix, flags=re.I)
    suffix = re.sub(r"\bheld\s+(?:up|steady)\s+at\b", blank, suffix, flags=re.I)
    suffix = re.sub(r"\bcompared\s+with\b|\b(?:were|are)\s+both\b", blank, suffix, flags=re.I)
    suffix = re.sub(r"\b(?:significantly|substantially|slightly)\s+(?:higher|lower)\b", blank, suffix, flags=re.I)
    suffix = re.sub(r"\b(?:also|reached|totaled|totalled|totaling|totalling|amounted|stood|came|ended|"
                    r"generated|recorded|earned|improved)\b", blank, suffix, flags=re.I)
    return prefix + text[phrase[0]:phrase[1]] + suffix


def _level_reading(claim: Claim, company: set[str], segment: Optional[str], *, growth: bool = False,
                   why: Optional[list[str]] = None) -> Optional[Reading]:
    """Bind a canonical level metric to its description, just as for 'other'.

    Inspect metric_text and its value-bound phrase, not the entire sentence:
    another claim may carry a different region, basis, or adjustment. A prefix
    naming a segment is retained in the Reading, never compared with the total.
    why, when given, receives the qualifying words that left the metric unread.
    """
    descriptions = [claim.metric_text or ""]
    local = _local_level_description(claim, growth=growth)
    if local:
        descriptions.append(local)
    elif subject := _clause_subject(claim):
        descriptions.append(subject)  # the clause's subject qualifies this figure across the figures between them
    for index, text in enumerate(descriptions):
        text = re.sub(r"(?:'s|’s)\b", "", text)
        text = _without_defined_acronyms(_without_period_phrases(text))
        phrases = _level_metric_phrases(claim.metric, text, segment)
        if growth and index == 0 and not phrases:
            continue  # a change's own wording ("increase", "segment grew") need not name its base again
        # To-common wording outside the phrase is another line; inside an EPS phrase it names EPS (_TO_COMMON_HOLDERS)
        if len(phrases) != 1 or _TO_COMMON_RE.search(f"{text[:phrases[0][0]]} {text[phrases[0][1]:]}"):
            return None
        phrase = phrases[0]
        segment = segment or _qualifier_before(text, phrase[0], company)
        checked_text = _level_sentence_framing(text, phrase) if index else text
        leftover = _leftover_words(checked_text, phrase, company, segment)
        rest = text[:phrase[0]] + text[phrase[1]:]
        if leftover or any(char.isalpha() and not char.isascii() for char in rest):
            if why is not None:
                why.extend(leftover)
            return None
    # A number-first quote still owns a qualification right after its number ("grew 32% excluding
    # acquisitions"); the metric's own wording there ("$28 billion in operating cash flow") is not one.
    if not local and (tail := _trailing_qualifier(claim)):
        tail = _without_period_phrases(re.sub(r"(?:'s|’s)\b", "", tail))
        phrases = _level_metric_phrases(claim.metric, tail, segment)
        leftover = _leftover_words(tail, phrases[0] if phrases else (0, 0), company, segment)
        if len(phrases) > 1 or leftover:
            if why is not None and len(phrases) <= 1:
                why.extend(leftover)
            return None
    # Same-store, comparable or identical sales, or "comps", are never company revenue. Named as a segment ("Same-store
    # revenues", a store-sales segment the release reports), they are checked against that segment's own facts.
    if (claim.metric in ("revenue", "segment_revenue") and not segment and implied_segment(claim) is None
            and _distinct_sales(claim)):
        return None
    return Reading(claim.metric, None, segment, _eps_alternatives(claim.metric, claim))


def _growth_reading(claim: Claim, base: str, company: set[str], segment: Optional[str],
                    why: Optional[list[str]] = None) -> Optional[Reading]:
    """A change inherits its base metric's scope, including source-local exclusions."""
    level = claim.model_copy(update={"metric": base})
    checked = _level_reading(level, company, segment, growth=True, why=why)
    if checked is None:
        return None
    metric = "revenue_growth" if base in ("revenue", "segment_revenue") else base
    return Reading(metric, "revenue" if metric == "revenue_growth" else base, checked.segment,
                   checked.alternatives)


# A dollar or per-share amount that its own words make the change of a level, not the level: "up $0.32", "a decrease of
# $192 million", "was up $32 million", "increased by $36 million", "an increase in revenue of $1.2 billion", "a $192
# million decrease", "$0.24 lower than prior year". It is the change across a filing's two periods, never the level
# (Day 13 full set: Charter, Norfolk Southern and Truist changes were compared with the level). Words that lead to a
# level stay levels: "rose to $26.5 billion", "up from $25.2 billion"; so do "made up $35 billion" and the figure a
# restated percent change ends at ("up 5%, or $1.2 billion" is the change; "up 5% to $26.5 billion" the level).
_ABS_CHANGE_WORDS = (r"up|down|increas(?:e|ed|es|ing)|decreas(?:e|ed|es|ing)|ris(?:e|es|en|ing)|rose|gr[eo]w(?:s|n|ing)?"
                     r"|growth|fell|fall(?:s|en|ing)?|declin(?:e|ed|es|ing)|dropp?(?:ed|s|ing)?"
                     r"|improv(?:ed|es|ing|ement)|jump(?:ed|s)?|climb(?:ed|s)?|surg(?:e|ed|es)|reduc(?:ed|es|tion)"
                     r"|change[ds]?|(?:higher|lower)\s+by")
_ABS_CHANGE_BEFORE_RE = re.compile(
    rf"\b(?:{_ABS_CHANGE_WORDS})(?:\s+(?:of|by))?\s*$"
    r"|\b(?:increase|decrease|decline|rise|drop|reduction|improvement|growth|change)s?\s+in\s+(?:[\w&'’.-]+\s+){1,6}?"
    r"of\s*$", re.I)
_ABS_CHANGE_AFTER_RE = re.compile(
    r"\s*(?:per\s+(?:diluted\s+|basic\s+)?share\s+)?(?:(?:year[\s-]+over[\s-]+year|yoy|y/y|quarter[\s-]+over[\s-]+quarter"
    r"|sequential)\s+)?(?:increase|decrease|rise|decline|drop|reduction|improvement|jump)s?\b"
    r"|\s*(?:higher|lower|more|less|greater|smaller)(?:\s+than\b|\s+(?:from|compared|versus|vs\.?)\b|\s*[,.;]|\s*$)",
    re.I)
_NOT_A_CHANGE_RE = re.compile(r"\b(?:made|make|makes|making|picked|ramped|built|set|signed|wrapped|summed|add(?:ed|s)?"
                              r"|paid|broken|broke|took|booked)\s+(?:up|down)\s*$", re.I)
# An earlier figure this one restates ("up 5%, or $1.2 billion", "rose 5% (or $1.2 billion)") or is listed with ("a
# charge of $22 million, $22 million and $0.49 per ton, respectively"): the words before the first figure lead into
# every one of them.
_RESTATEMENT_LINK_RE = re.compile(r"(?:\(\s*or|,\s*or|\bor|\()\s*$", re.I)
_LIST_LINK_RE = re.compile(r"(?:,\s*(?:(?:and|or)\b)?|\s+(?:and|or)\b|\s*&)\s*$", re.I)


def _leading_words(text: str, pos: int, value_text: str) -> str:
    """The words that lead into the figure at pos in text: currency and a hedge stripped ("increased approximately
    $36 million"), and walked back over the earlier figures it restates or is listed with (above)."""
    number = re.search(r"\d", value_text)
    before = strip_bound_currency_prefix(text[:pos] + (value_text[:number.start()] if number else ""))
    for _ in range(8):
        for _kind, _inclusive, pattern in _HEDGES:
            if hit := re.search(pattern.pattern, before, re.I):
                before = before[:hit.start()]
                break
        link = _RESTATEMENT_LINK_RE.search(before) or _LIST_LINK_RE.search(before)
        head = before[:link.start()].rstrip() if link else ""
        last = [m for m in _FIGURE_RE.finditer(head) if re.search(r"\d", m.group(0)) and m.end() == len(head)]
        if not last:
            break
        before = strip_bound_currency_prefix(head[:last[-1].start()])
    return before.rstrip()


class DraftLine(NamedTuple):
    """The draft sentence that holds a claim's number (a semicolon doesn't end it), and the number's place in it."""

    text: str
    start: int
    end: int


def draft_line(claim: Claim, draft: Optional[str]) -> Optional[DraftLine]:
    """The claim's DraftLine when the draft is given and the claim's span sits at its offsets in it, else None."""
    if not draft or not 0 <= claim.span_start <= claim.span_end <= len(draft):
        return None
    if draft[claim.span_start:claim.span_end] != claim.span_text or (pos := _value_pos(claim)) is None:
        return None
    a = claim.span_start + pos
    b = a + len(claim.value_text)
    breaks = [x for x in sentence_breaks(draft) if draft[x - 1] != ";"]
    start = max([x for x in breaks if x <= a], default=0)
    end = min([x for x in breaks if x >= b], default=len(draft))
    return DraftLine(draft[start:end], a - start, b - start)


def _needs_its_sentence(claim: Claim, reading: Reading) -> bool:
    """Whether the quote alone can't show what the figure is of: neither the words before its number nor its
    metric_text name the base ("an increase of 3%", described as "increase"). A quote that names the base is read as it
    stands: the words a sentence puts before it are mostly a dateline, a cause or a comparison ("Driven by robust
    client engagement, ...", "Compared with the prior year, ..."), not the figure's own qualifiers."""
    base = reading.derived or reading.growth_base or reading.metric
    base = "revenue" if base == "revenue_growth" else base
    pos = _value_pos(claim)
    if pos is None or reading.segment:  # a quote that names its segment ("Fixed Income Markets up 21%") describes it
        return False
    return not (_level_metric_phrases(base, _without_period_phrases(claim.span_text[:pos]), reading.segment)
                or _level_metric_phrases(base, claim.metric_text or "", reading.segment))


def _sentence_subject_words(claim: Claim, reading: Reading, line: DraftLine) -> list[str]:
    """The qualifying words of the base metric its draft line names last before the figure (_needs_its_sentence): a
    segment, a region, a same-store measure or other words beyond framing, as _level_reading reads them. [] when the
    line names the base plainly ("Target revenue was $26.5 billion, an increase of 5.3%") or not at all."""
    base = reading.derived or reading.growth_base or reading.metric
    base = "revenue" if base == "revenue_growth" else base
    text = _without_period_phrases(re.sub(r"(?:'s|’s)\b", "  ", line.text[:line.start]))
    phrases = _level_metric_phrases(base, text)
    if not phrases:
        return []
    start, end = phrases[-1]
    boundaries = sentence_breaks(text[:start]) + [hit.end() for hit in _FIGURE_RE.finditer(text[:start])
                                                  if re.search(r"\d", hit.group(0))]
    left = max(boundaries, default=0)
    verb = _SUBJECT_END_RE.search(text, end)
    subject = text[left:verb.start() if verb else end]
    phrase = (start - left, end - left)
    company = _company_words(claim)
    segment = _qualifier_before(subject, phrase[0], company)
    known = reading.segment  # a segment the reading already checks the figure against is no qualifier
    named = " ".join(filter(None, (segment, known)))
    words = _leftover_words(_level_sentence_framing(subject, phrase), phrase, company, named or None)
    if segment and _seg_norm(segment) != _seg_norm(known):
        words = [segment, *words]
    if (distinct := _DISTINCT_SALES_RE.search(subject)) and not any(distinct.group(0) in w for w in words):
        words.append(distinct.group(0))
    return words


def _lead_source(claim: Claim, line: Optional[DraftLine]) -> Optional[tuple[str, int]]:
    """The text and offset to read a figure's leading words from: its span, or its draft line when the span holds
    nothing before the number but a list separator (", $22 million"): a list item takes the list's head from there."""
    pos = _value_pos(claim)
    if pos is None:
        return None
    if line is not None and not re.sub(r"(?:\band\b|\bor\b|[\s,;&$])", "", claim.span_text[:pos], flags=re.I):
        return line.text, line.start
    return claim.span_text, pos


def absolute_change(claim: Claim) -> bool:
    """A dollar or per-share figure its own words make a change (see above), not a level."""
    if claim.unit not in ("USD", "USD/share") or _MARGIN_CHANGE_UNIT.search(claim.value_text or ""):
        return False
    pos = _value_pos(claim)
    if pos is None:
        return False
    span = claim.span_text
    before = _leading_words(span, pos, claim.value_text)
    if _ABS_CHANGE_BEFORE_RE.search(before) and not _NOT_A_CHANGE_RE.search(before):
        return True
    after = span[pos + len(claim.value_text):]
    if restated := _RESTATED_AFTER_RE.match(after):
        after = after[restated.end():]  # "$1.3 billion, or 5.3%, lower"
    return bool(_ABS_CHANGE_AFTER_RE.match(after))


# A figure the draft describes as an item within a reported figure, not the figure: "gross profit ... included a charge
# of $22 million" (Martin Marietta, compared with aggregates gross profit), "an impact of $0.11 per share", "a $300
# million impairment", "including $1.2 billion from acquisitions". NoCap holds no filing fact for such items, so the
# figure is "?". A loss or cost noun is not one: "a net loss of $50 million" and "operating costs of $5 billion" are
# levels.
_ITEM_NOUNS = r"(?:charges?|impacts?|benefits?|gains?|headwinds?|tailwinds?|impairments?|write-?downs?|write-?offs?)"
_ITEM_MODIFIERS = (r"(?:pre-?tax|after-?tax|non-?cash|one-?time|favou?rable|unfavou?rable|negative|positive|net|total"
                   r"|tax|special|discrete|restructuring|acquisition-related|related|estimated|cumulative|unrealized"
                   r"|realized|mark-to-market|currency|foreign\s+exchange|fx|tariff)")
_ITEM_BEFORE_RE = re.compile(rf"(?P<words>(?:\b(?:an?|the)\s+)?(?:\b{_ITEM_MODIFIERS}\s+){{0,3}}\b{_ITEM_NOUNS})"
                             r"\s+(?:of|totall?ing|amounting\s+to)\s*$"
                             r"|\b(?P<verb>includ(?:ed|es|ing)|reflect(?:ed|s|ing)|exclud(?:ed|es|ing))\s*$", re.I)
# After the number only words that describe the item may come before its noun ("$22 million restructuring charge",
# "$0.11 per share tax benefit"), never a clause ("$26.5 billion despite currency headwinds").
_ITEM_GAP_WORD = (r"(?!(?:despite|driven|due|as|amid|with|from|on|by|and|or|but|while|which|that|after|before|including"
                  r"|excluding|reflecting|led|thanks|helped|primarily|mainly|largely|partly|compared|versus|vs|than|to"
                  r"|for|at|was|were|is|are|its|their|our|of)\b)[a-z][\w-]*")
_ITEM_AFTER_RE = re.compile(rf"\s*(?:per\s+(?:diluted\s+|basic\s+)?share\s+)?(?:in\s+)?(?:{_ITEM_GAP_WORD}\s+){{0,3}}?"
                            rf"{_ITEM_NOUNS}\b", re.I)


def item_wording(claim: Claim, line: Optional[DraftLine] = None) -> Optional[str]:
    """The words that make this figure an item within a reported figure (above), or None. A list item reads them from
    the list's head in its draft line ("a charge of $22 million, $22 million and $0.49 per ton")."""
    source = _lead_source(claim, line)
    if source is None:
        return None
    text, pos = source
    if hit := _ITEM_BEFORE_RE.search(_leading_words(text, pos, claim.value_text)):
        return " ".join((hit["words"] or hit["verb"]).split())
    after = text[pos + len(claim.value_text):]
    if hit := _ITEM_AFTER_RE.match(after):
        return " ".join(hit.group(0).split())
    return None


def _absolute_change_reading(claim: Claim, company: set[str], segment: Optional[str],
                             why: Optional[list[str]]) -> Optional[Reading]:
    """The change in dollars or per share of the base metric the claim names (absolute_change), or None when that is no
    base metric NoCap computes changes of. A change of income or EPS described as a loss ("net loss decreased $50
    million") is never read: a smaller loss is a rise in income, and the draft's sign can't be told."""
    m = claim.metric
    if m == "other":
        mt = _without_defined_acronyms(claim.metric_text or "")
        phrases = _metric_phrases(mt)
        if len(phrases) != 1 or _TO_COMMON_RE.search(mt) or _MARGIN_RE.search(mt):
            return None
        base, start, end = phrases[0]
        segment = segment or _qualifier_before(mt, start, company)
        if leftover := _leftover_words(mt, (start, end), company, segment):
            if why is not None:
                why.extend(leftover)
            return None
    else:
        base = "revenue" if m == "revenue_growth" else m
    if base not in GROWTH_BASES:
        return None
    if base in ("net_income", "operating_income", "eps_diluted", "eps_basic") and _LOSS_RE.search(
            f"{claim.metric_text} {_change_clause(claim)}".lower()):
        return None
    # Unlike a percent change ("up 5%"), a dollar change is read only where its words name the base, as a level is:
    # "cash from operations increased $3.0 billion" names no tracked base phrase.
    typed = claim.model_copy(update={"metric": base})
    local = _local_level_description(typed, growth=True)
    if not any(_level_metric_phrases(base, _without_period_phrases(text), segment)
               for text in (claim.metric_text or "", local or "")):
        return None
    checked = _growth_reading(claim, base, company, segment, why)
    if checked is None:
        return None
    level = "revenue" if base == "segment_revenue" else base
    return Reading(level, checked.growth_base, checked.segment, checked.alternatives, absolute=True)


def _is_change(claim: Claim) -> bool:
    """The claim is a percent change: its comparison is stated, or change wording sits in metric_text or right
    before the number (after any earlier figure in the span)."""
    if claim.comparison in ("YoY", "QoQ", "sequential"):
        return True
    if _CHANGE_RE.search(claim.metric_text or ""):
        return True
    if _source_growth_comparisons(claim):
        return True
    pos = _value_pos(claim)
    span = claim.span_text or ""
    if pos is None:
        return False
    before = span[:pos]
    figures = [m for m in _FIGURE_RE.finditer(before) if re.search(r"\d", m.group(0))]
    window = before[figures[-1].end():] if figures else before
    return bool(_CHANGE_RE.search(window[-60:]))


_MARGIN_METRICS = ("gross_margin", "operating_margin", "net_margin")
_MARGIN_UNIT_WORDS = r"basis\s+points?|bps?|percentage[\s-]+points?|pct\s+points?|points?|ppts?|pp|pts"
_MARGIN_CHANGE_UNIT = re.compile(rf"(?<![A-Za-z_])(?:{_MARGIN_UNIT_WORDS})(?!\w)", re.I)
_MARGIN_CHANGE_VALUE = re.compile(
    rf"(?<![\w.])(?P<number>[+−-]?{_NUM_RE.pattern})\s*"
    rf"(?P<unit>{_MARGIN_UNIT_WORDS})(?!\w)", re.I)


def _margin_change_value(claim: Claim) -> Optional[tuple[str, float]]:
    """The draft's own unit and amount, never a model's percent conversion or scale."""
    hits = list(_MARGIN_CHANGE_VALUE.finditer(claim.value_text))
    if len(hits) != 1 or len(_NUM_RE.findall(claim.value_text)) != 1:
        return None
    hit = hits[0]
    number = hit.group("number").replace(",", "").replace("−", "-")
    unit = "basis points" if hit.group("unit").lower().startswith(("basis", "bp")) else "percentage points"
    return unit, float(number)


def _margin_value_prefix(claim: Claim) -> str:
    """Own clause wording, retaining the direction in 'margin fell from X% by Y bps'."""
    pos = _value_pos(claim)
    if pos is None:
        return claim.metric_text
    before = claim.span_text[:pos]
    boundaries = sentence_breaks(before)
    figures = list(_FIGURE_RE.finditer(_without_period_phrases(before)))
    if figures:
        previous = figures[-1]
        phrases = [(start, end) for metric in _MARGIN_METRICS
                   if _level_metric_phrases(metric, claim.metric_text)
                   for start, end in _level_metric_phrases(metric, before[:previous.start()])]
        if phrases:
            start, end = max(phrases)
            lead = before[end:previous.start()]
            # A starting percentage belongs to this margin's change only when
            # 'from ... by' joins it, with no intervening claim or sentence.
            if (re.fullmatch(rf"\s*[+−-]?{_NUM_RE.pattern}\s*(?:%|percent)\s*", previous.group(), re.I)
                    and re.fullmatch(r"\s*by\s*", before[previous.end():], re.I)
                    and re.search(r"\bfrom\s*$", lead, re.I) and _CHANGE_RE.search(lead)
                    and not sentence_breaks(lead) and not _metric_phrases(lead)
                    and not _MARGIN_RE.search(lead)
                    and not re.search(r"\b(?:and|but|while|whereas|with|or)\b", lead, re.I)):
                figures.pop()
                boundaries.append(start)
    boundaries += [m.end() for m in figures]
    return before[max(boundaries, default=0):]


def _margin_claimed_value(claim: Claim) -> float:
    value = _margin_change_value(claim)
    assert value is not None
    words = f"{_margin_value_prefix(claim)} {claim.metric_text}".lower()
    return -abs(value[1]) if _DOWN_RE.search(words) or _NEGATIVE_RE.search(words) else value[1]


def _margin_reading(claim: Claim, company: set[str], why: Optional[list[str]]) -> Optional[Reading]:
    """Company-only margins, including descriptions proposed under a different enum metric."""
    names = [m for m in _MARGIN_METRICS if _level_metric_phrases(m, claim.metric_text)]
    if len(names) != 1 or claim.segment:
        return None
    metric = names[0]
    change = _margin_change_value(claim)
    if _MARGIN_CHANGE_UNIT.search(claim.value_text) and not change:
        return None  # a range or malformed amount must not become a point estimate or a margin level
    if change and not _is_change(claim):
        return None  # a margin level written in bps does not say that the margin changed
    if not change and (metric != "net_margin" or claim.unit != "%"):
        return None
    if not change and _is_change(claim) and not _margin_level_prefix(claim):
        return None  # "net margin rose 25%" is relative growth, not a 25% margin level
    typed = claim.model_copy(update={"metric": metric})
    checked = _level_reading(typed, company, None, growth=bool(change), why=why)
    if checked is None or checked.segment:
        return None
    return Reading(metric, metric if change else None, None, derived=metric)


def _ambiguous_margin_percent_change(claim: Claim) -> bool:
    """A percent-worded movement does not identify points versus relative growth."""
    return (claim.unit == "%" and not _MARGIN_CHANGE_UNIT.search(claim.value_text)
            and any(_level_metric_phrases(metric, claim.metric_text) for metric in _MARGIN_METRICS)
            and _is_change(claim)
            and not _margin_level_prefix(claim))


def _margin_level_prefix(claim: Claim) -> bool:
    """Endpoint/base wording can include a bound or hedge before its amount."""
    prefix = re.sub(r"\b(?:negative|minus)\s*$", "", _margin_value_prefix(claim), flags=re.I)
    prefix = strip_bound_currency_prefix(prefix)
    for _kind, _inclusive, pattern in _HEDGES:
        if hit := re.search(pattern.pattern, prefix, re.I):
            prefix = prefix[:hit.start()]
            break
    return bool(re.search(r"\b(?:to|at|from|was|were|is|of)\s*$", prefix, re.I))


def interpret(claim: Claim, why: Optional[list[str]] = None) -> Optional[Reading]:
    """How the verifier reads a claim, or None when it names nothing NoCap tracks (then "?", never "✗").

    * revenue_growth: a percent change of revenue (or of a segment's revenue).
    * A percent figure on a level metric with change wording ("operating income increased 11%"): its change.
    * metric "other": the base metric its own metric_text names. A percent change may also take its subject from
      the span ("Net income was $14.4 billion, up 12%") when at most one figure sits between the two. A level
      figure must be named by metric_text itself, so "a favorable impact of $0.11" is never read as EPS.
    * metric_text "operating margin": operating income / revenue for the company (no segment wording).

    * "net income attributable to <the company>" is plain net income; a bare "per share" figure attached to a net-income
      amount ("net income of $362 million, or $1.28 per share") is EPS; "the same quarter last year" after an amount
      names its period, not a qualifier; a bank's noninterest income is read only as a percent change.

    why, when given, receives the qualifying words that left a tracked metric unread."""
    claim = _without_parent_attribution(claim)
    m, unit = claim.metric, claim.unit
    company = _company_words(claim)
    seg = (claim.segment or "").strip() or None
    if _ambiguous_margin_percent_change(claim):
        return None
    if (_level_metric_phrases("net_margin", claim.metric_text)
            or (_MARGIN_CHANGE_UNIT.search(claim.value_text)
                and any(_level_metric_phrases(metric, claim.metric_text) for metric in _MARGIN_METRICS))):
        return _margin_reading(claim, company, why)
    if absolute_change(claim):  # "up $0.32": the change, checked as one, never against the level
        return _absolute_change_reading(claim, company, seg, why)
    if m == "revenue_growth":
        # A named segment's own wording ("Gaming business revenue") is segment revenue wording.
        return _growth_reading(claim, "segment_revenue" if seg else "revenue", company, seg, why)
    if m in GROWTH_BASES and unit == "%":
        if _MARGIN_RE.search(claim.metric_text or "") or not _is_change(claim):
            return Reading(m, None, seg)  # a level metric written in percent: the unit check says "?"
        return _growth_reading(claim, m, company, seg, why)
    if m in ("eps_diluted", "eps_basic") and _income_per_share(claim) is not None:
        return Reading(m, None, None, _eps_alternatives(m, claim))  # "net income of $X, or $Y per share"
    if m != "other":
        return _level_reading(claim, company, seg, why=why)
    mt = _without_defined_acronyms(claim.metric_text or "")
    if _TO_COMMON_RE.search(mt):
        return None
    if unit == "%" and re.fullmatch(r"\s*(?:(?:total|consolidated|gaap|non-gaap|adjusted)\s+)*operating\s+margins?\s*",
                                    mt, re.I):
        # The figure's own words in the span are read as for any level metric, as gross and net margins are: "core",
        # "comparable" or "excluding special items" there names another measure than the GAAP operating margin. "As
        # adjusted" stays the adjusted (non-GAAP) margin it was read as before.
        blank_as = lambda text: re.sub(r"\bas(?=\s+adjusted\b)", "  ", text or "", flags=re.I)  # noqa: E731
        own = claim.model_copy(update={"metric": "operating_margin", "span_text": blank_as(claim.span_text),
                                       "metric_text": blank_as(claim.metric_text)})
        checked = _level_reading(own, company, None, why=why)
        if checked is None or checked.segment:
            return None
        return Reading("operating_margin", None, None, derived="operating_margin")
    if _MARGIN_RE.search(mt):
        return None
    phrases = _metric_phrases(mt)
    if phrases:
        base, start, end = phrases[0]
        seg = seg or _qualifier_before(mt, start, company)
        leftover = _leftover_words(mt, (start, end), company, seg)
        if len(phrases) > 1 or leftover:
            if why is not None and len(phrases) == 1:
                why.extend(leftover)
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
        if not _is_change(claim):
            return None
        if base == "noninterest_income" and _SHARE_OF_RE.search(claim.span_text or ""):
            return None  # "noninterest income rose to 34% of revenue" is a share of revenue, not a change
        typed = claim if phrases else claim.model_copy(update={"metric_text": base.replace("_", " ")})
        return _growth_reading(typed, base, company, seg, why)
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


LONG_Q4_MAX_DAYS = 125


def _long_quarter(f: Fact, rp: ResolvedPeriod, start: Optional[date]) -> bool:
    """A quarter of 101-125 days that the stored calendar itself dates that way (`start` is the calendar's start for the
    claim's quarter), under the fact's own matching label: Costco's 16- or 17-week Q4 (112 or 119 days, derived as the
    fiscal year minus nine months) and Kroger's 16-week Q1. A long span under a label the calendar dates as an
    ordinary quarter is no quarter NoCap reads."""
    if (f.period_start is None or start is None or f.period_start != start
            or rp.fiscal_period not in ("Q1", "Q2", "Q3", "Q4") or (f.fiscal_period or "").upper() != rp.fiscal_period):
        return False
    return 100 < (f.period_end - f.period_start).days + 1 <= LONG_Q4_MAX_DAYS


def _source_rank(f: Fact) -> int:
    return SOURCE_RANK.get(f.source, 3)


# A release row whose own label names comparable, same-store or identical sales, or a constant-currency figure, is a
# measure of its own, never the company's revenue, whatever metric extraction filed it under: Sysco's "Comparable sales
# using a constant currency basis (Non-GAAP)". The label is the row text before the cell's value in its locator.
_NOT_REVENUE_ROW_RE = re.compile(rf"\bcomparable\b|\bcomps?\b|\bsame[\s-]+stores?\b|\bidentical\b|{_CURRENCY_NEUTRAL_RE.pattern}"
                                 r"|\b(?:fx|f/x|currency)[\s-]+adjusted\b|\bexcluding\b.*\bforeign[\s-]+(?:currenc|exchange)",
                                 re.I)


def _not_revenue_row(f: Fact) -> bool:
    return bool(_NOT_REVENUE_ROW_RE.search((f.locator or "").split("|", 1)[0]))


# A release row labelled organic or pro forma is a company-defined measure of its own as well, the row-side match of
# distinct_measure: FactSet's "Organic revenues", Estee Lauder's "Organic Net Sales, Non-GAAP", Comcast's "Pro Forma
# Consolidated Revenue". It is never the company's revenue either.
_DISTINCT_MEASURE_ROW_RE = re.compile(r"\borganic(?:ally)?\b|\bpro[\s-]*forma\b", re.I)


def _kept_out_of_revenue(f: Fact) -> bool:
    """A release row level() never reads as company revenue: _not_revenue_row, or an organic or pro forma row."""
    return _not_revenue_row(f) or bool(_DISTINCT_MEASURE_ROW_RE.search((f.locator or "").split("|", 1)[0]))


# A release table of non-GAAP figures often opens with the GAAP figure it reconciles from: "Operating income | 180,654"
# or "Operating income, as reported | 8,708" filed under the table's non-GAAP basis. Such a row is the GAAP figure when
# its label names no non-GAAP measure and either says GAAP or "(as) reported" or, for an amount, equals the same
# filing's GAAP figure for that line and period. A non-GAAP margin never takes it as its numerator or rate: "adjusted
# operating margin" was checked against the GAAP margin it gives. A row that names its measure ("Adjusted operating
# income") is kept even where it equals the GAAP figure, and so is a printed rate that does: NVIDIA's non-GAAP table
# prints "Gross margin | 75.0%", as its GAAP one does.
_ROW_NON_GAAP_RE = re.compile(r"\b(?:non[\s-]?gaap|adjusted|managed|fte|(?:fully[\s-])?taxable[\s-]equivalent|underlying"
                              r"|organic|pro[\s-]?forma|excluding|excl\.|ex-|ongoing|core|comparable|normali[sz]ed"
                              r"|recurring|cash)", re.I)
_ROW_GAAP_RE = re.compile(r"\bgaap\b|\breported\b", re.I)


def _gaap_row(f: Fact, gaap: Iterable[Obs] = ()) -> bool:
    """Whether a non-GAAP release fact is the GAAP figure of its reconciliation table (see above); gaap: the GAAP
    observations of the same amount line and period (none for a rate)."""
    label = re.sub(r"[‐‑‒–—―−]", "-", (f.locator or "").split("|", 1)[0])  # "Non‐GAAP" with a Unicode hyphen
    if _ROW_NON_GAAP_RE.search(label):
        return False
    return bool(_ROW_GAAP_RE.search(label)) or (f.unit == "USD" and any(
        o.primary.accession == f.accession and o.primary.value == f.value for o in gaap))


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
    calculation: str = ""  # endpoint margins for absolute point changes
    printed_change: Optional[float] = None  # difference of independently rounded margin endpoints, in claim units
    printed_calculation: str = ""
    printed_facts: tuple[Fact, ...] = ()  # stated rates supplying print precision, cited for printed-only matches

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
        try:
            return 0.5 * 10.0 ** -f.decimals
        except OverflowError:
            return math.inf  # the observation is excluded before it can influence a verdict
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


def strip_bound_currency_prefix(prefix: str) -> str:
    """Remove supported USD formatting immediately before a numeric token.

    Shared with extraction's source-bound recovery. Currency spellings are
    complete tokens: an unsupported currency or a word ending in "us" stays.
    """
    return re.sub(r"(?:\s|[$+−(\-]|(?<!\w)(?:US\s*\$|U\.S\.\s*\$|\$US|USD))+$", "", prefix, flags=re.I)


def hedge_of(claim: Claim) -> Optional[Hedge]:
    """The hedge or bound word right before the claim's number (or leading its value_text: 'over $28 billion')."""
    span, vt = claim.span_text or "", claim.value_text or ""
    i = span.find(vt) if vt else -1
    number = re.search(r"\d", vt)
    lead = vt[:number.start()] if number else vt
    text = " ".join(f"{span[:i] if i >= 0 else ''}{lead}".lower().split())[-80:]
    # Extraction may leave the currency sign in span_text while value_text starts at the digit.
    # It is formatting around the same amount, not a word separating the bound from its number.
    text = strip_bound_currency_prefix(text)
    for kind, inclusive, rx in _HEDGES:
        if rx.search(text):
            return Hedge(kind, inclusive)
    return None


# "over a year ago" right after a level amount: a year-ago label, or "more than a year ago". Unlike "over the same
# quarter last year" (_OVER_PERIOD_LABEL_RE) it names no period, so the amount is checked only in its own period and a
# mismatch is "?" (_Check.run), never a period or value "✗". Also "over last year", "over the prior year".
_OVER_A_YEAR_RE = re.compile(r"[\s,()]*(?P<words>over\s+(?:a\s+year\s+(?:ago|earlier|before)|(?:the\s+)?(?:prior|previous"
                             r"|last|past)[\s-]+year|(?:the\s+)?year[\s-]+(?:ago|earlier)))\b", re.I)


def _over_a_year(claim: Claim) -> Optional[str]:
    """The "over a year ago" wording directly after this claim's amount, or None."""
    pos = _value_pos(claim)
    hit = None if pos is None else _OVER_A_YEAR_RE.match(claim.span_text, pos + len(claim.value_text))
    return " ".join(hit["words"].split()) if hit else None


# A level figure introduced as the base of another figure's comparison, with no period words of its own: "Gross margin
# of 33.4% increased 668 basis points compared to 26.7%", "Revenue rose 5.3% to $26.5 billion from $25.2 billion",
# "Revenue increased from $25.2 billion to $26.5 billion". Its period is the base that comparison implies, the
# year-earlier period unless the sentence says sequential or quarter-over-quarter; _Check._implied_base checks it there,
# never against the current period. "Compared to" and "versus" may carry the figure's own metric words ("compared to
# a gross margin of 26.7%"); "from" may not ("revenue from continuing operations of $26.5 billion" names a source).
_BASE_INTRO_RE = re.compile(r"(?:\b(?:compared\s+(?:to|with)|versus|vs\.?)\s+"
                            r"(?:(?:(?:an?|the)\s+)?(?:[A-Za-z][\w'’&/-]*\s+){0,3}of\s+)?|\bfrom\s+)$", re.I)
_CHANGE_WORD_RE = re.compile(r"\b(?:increas\w*|decreas\w*|ris(?:e|es|ing)|rose|grew|grow\w*|up|down|declin\w*|fell"
                             r"|fall\w*|expand\w*|contract\w*|improv\w*|jump\w*|climb\w*|dropp?\w*|gain\w*)\b", re.I)


def _unlabelled_base(claim: Claim) -> Optional[str]:
    """How a comparison-base figure with no period of its own is compared (see above): "YoY" or "QoQ" when the
    sentence says so, "implied" when it doesn't (the year-earlier base, checked only for a match), "ambiguous" when it
    says both; None when the figure is not such a base."""
    pos = _value_pos(claim)
    if pos is None or claim.comparison != "none":
        return None
    span = claim.span_text or ""
    before = re.sub(r"(?:US\s*\$|USD\s*|\$)\s*$", "", span[:pos])
    start = max(sentence_breaks(before), default=0)
    intro = _BASE_INTRO_RE.search(before, start)
    if intro is None:
        return None
    head = before[start:intro.start()]
    if not _CHANGE_WORD_RE.search(head) and not any(
            re.search(r"\d", m.group(0)) for m in _FIGURE_RE.finditer(_without_period_phrases(head))):
        return None  # another figure's statement or change must lead into the comparison
    own = periods.parse(before[intro.start():])
    tail = _AFTER_CLAUSE_BREAK_RE.split(span[pos + len(claim.value_text):], maxsplit=1)[0]
    tail_period = periods.parse(tail)
    if (own.kind or own.shift or tail_period.kind or tail_period.shift or any(_comparison_period_after(claim))
            or _over_a_year(claim) or _YEAR_AGO_RE.search(tail.lower()) or _PRIOR_Q_RE.search(tail.lower())):
        return None  # the figure's own words name its period
    words = _NAMED_QUARTER_RE.sub(lambda m: " " * len(m.group()), head)
    kinds = {comparison for comparison, pattern in _GROWTH_COMPARISONS if pattern.search(words)}
    return "ambiguous" if len(kinds) > 1 else next(iter(kinds)) if kinds else "implied"


# An explicit base period a change names: "increased 8 percent compared to first quarter of 2025", "fell 12% from Q1
# 2025", "Compared with the second quarter of 2025, EPS fell 12%". The change is measured against that period, so it
# is the year-over-year or sequential change only when that period is the year-earlier period or the prior quarter of
# the one checked; NoCap never assumes either over a stated base (_Check._stated_base).
_BASE_PERIOD_INTRO_RE = re.compile(r"\b(?:compared\s+(?:to|with)|from|versus|vs\.?|over|than|against)\s+(?:the\s+)?",
                                   re.I)


def _period_phrases(text: str) -> list[str]:
    """Every explicit period phrase in text (periods.find_period_phrase), in order."""
    out = []
    while phrase := periods.find_period_phrase(text):
        out.append(phrase)
        text = text.replace(phrase, " " * len(phrase), 1)
    return out


def _stated_base_period(claim: Claim) -> Optional[str]:
    """The explicit base period a change's own words name, verbatim, or None (see above)."""
    local = _value_comparison_context(claim)
    if local is None:
        return None
    for part in (local[1], local[0]):
        for intro in _BASE_PERIOD_INTRO_RE.finditer(part):
            rest = part[intro.end():]
            if (phrase := periods.find_period_phrase(rest)) and rest.startswith(phrase):
                return phrase
    return None


def _comparison_period_after(claim: Claim) -> tuple[bool, bool]:
    """A year-ago / prior-quarter label directly after this amount ('$46.7 billion a year ago')."""
    local = _value_comparison_context(claim)
    if local is None:
        return False, False
    # An intervening "up from", "to", or another amount does not belong to this amount.
    after = re.sub(r"^[\s,()]*(?:(?:in|for|during)\s+)?(?:the\s+)?", "", local[1].lower())
    # "for the same quarter last year" labels a year-ago amount as "a year ago" does (_SAME_PERIOD_RE)
    year_ago, prior_quarter = _YEAR_AGO_RE.match(after) or _SAME_PERIOD_RE.match(after), _PRIOR_Q_RE.match(after)
    if not (year_ago or prior_quarter) and (over := _OVER_PERIOD_LABEL_RE.match(local[1].lower())):
        # "over the same quarter last year", "over the prior-year period"; never "over a year ago" (more than a year)
        after, year_ago = local[1].lower()[over.start("label"):], _SAME_PERIOD_RE.match(over.group("label"))
    label = year_ago or prior_quarter
    # "..., a year ago it was $30B", "(a year ago: $30B)" and "prior-year revenue was $30B" label the next one.
    if label and _INTRODUCES_AMOUNT_RE.match(after, label.end()):
        return False, False
    return bool(year_ago), bool(prior_quarter)


@dataclass(frozen=True)
class SpanTerms:
    """What a check takes from span_text and metric_text, beyond the claim's typed fields."""

    growth_base: Optional[str]
    comparison: str               # source YoY/QoQ wording bound to this value overrides the proposal
    comparison_ambiguous: bool
    derived: Optional[str]
    alternatives: tuple[str, ...]
    segment: Optional[str]
    metric: str
    basis: Optional[str]
    negative: bool                # "fell 5%", "a net loss of $1.2 billion", "negative $2.12 billion"
    hedge: Optional[Hedge]
    endpoint_ambiguous: bool      # "up to X from Y"
    comparison_period: tuple[bool, bool]  # a level amount labelled a year ago / the prior quarter


def span_terms(claim: Claim, r: Reading) -> SpanTerms:
    growth_base = r.growth_base
    source_comparisons = _source_growth_comparisons(claim) if growth_base else set()
    ambiguous = len(source_comparisons) > 1 or bool(
        growth_base and _value_pos(claim) is None
        and any(pattern.search(claim.span_text) for _, pattern in _GROWTH_COMPARISONS))
    comparison = next(iter(source_comparisons)) if len(source_comparisons) == 1 else claim.comparison
    segment = r.segment
    if segment is None and r.metric in ("revenue", "revenue_growth"):
        segment = implied_segment(claim)  # never check a segment figure against the consolidated total
    metric = "segment_revenue" if r.metric == "revenue" and segment else r.metric
    # The basis the draft states. With none stated, non-GAAP wording ("adjusted", "managed", "FTE") or a
    # non-GAAP-only measure still means press-release evidence only, and a GAAP mismatch is never "✗".
    non_gaap_words = (_NON_GAAP_WORDS_RE.search(f"{claim.metric_text} {claim.span_text}")
                      or _claim_measure_qualifiers(claim))
    basis = claim.basis or ("non-GAAP" if metric in NON_GAAP_BY_NATURE or non_gaap_words else None)
    return SpanTerms(
        growth_base, comparison, ambiguous, r.derived, r.alternatives, segment, metric, basis,
        (_margin_claimed_value(claim) if growth_base in _MARGIN_METRICS else
         claimed_value(claim, change=growth_base is not None)) < 0,
        hedge_of(claim), _ambiguous_growth_endpoint(claim),
        _comparison_period_after(claim) if claim.comparison == "none" and not growth_base else (False, False))


def span_reading(claim: Claim) -> tuple[Any, ...]:
    """Everything a verdict can take from span_text, as one comparable value.

    Extraction restores source wording a model quote left out, and keeps the
    shorter quote only when both give this same value, so the verdict cannot
    depend on which one is shown. Every span_text rule in this module must be
    reflected here: interpret(), currency_neutral(), basis_conflict(), distinct_measure(), span_terms(),
    _over_a_year(), _unlabelled_base() and _stated_base_period().
    """
    if not math.isfinite(claim.value) or claim.forward_looking or claim.metric == "guidance_tolerance":
        return ("not_read",)
    reading = interpret(claim)
    return (reading, currency_neutral(claim), span_terms(claim, reading or Reading(claim.metric, None, None)),
            basis_conflict(claim), distinct_measure(claim), _claim_measure_qualifiers(claim), _over_a_year(claim),
            _unlabelled_base(claim), _stated_base_period(claim))


SPLIT_RATIOS =(2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50, 100)


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


class Split(NamedTuple):
    """A stock split that a company's XBRL reports or its own per-share figures show (company_splits)."""

    ratio: int                         # n of an n-for-1 split, or of a 1-for-n reverse split
    forward: bool                      # n-for-1: figures on the new share basis are the old ones divided by n
    old_basis_until: Optional[datetime]  # the latest acceptance known to report on the old share basis (None: none)
    new_basis_from: datetime           # the first acceptance that reports on the new share basis
    evidence: tuple[Fact, ...]         # two versions of one figure that show it (old basis, new basis), if any held


# A restated per-share figure is recomputed on the new share count, so it can miss old / n by more than rounding:
# CrowdStrike's Q2 FY2026 diluted EPS was -$0.31, and -$0.07 after its 4-for-1. Allowed only for a split its XBRL reports.
SPLIT_RESTATEMENT_SLACK = 0.15
# A period that ended this long before a filing known to be on the old basis was first reported on the old basis too
# (10-Qs are due within 45 days, 10-Ks within 90).
FIRST_REPORT_DAYS = 95


def _restated_by(old: Fact, new: Fact, n: int, forward: bool) -> bool:
    """new is old restated for an n-for-1 (forward) or 1-for-n split, within SPLIT_RESTATEMENT_SLACK."""
    if not old.value or not new.value or (old.value > 0) != (new.value > 0):
        return False
    r = abs(old.value) / abs(new.value) if forward else abs(new.value) / abs(old.value)
    return abs(r - n) <= SPLIT_RESTATEMENT_SLACK * n


def company_splits(facts: Iterable[Fact], events: Iterable[Any] = ()) -> list[Split]:
    """Stock splits of one company, from two kinds of evidence:
    * the splits its XBRL reports (receipts.splits.SplitEvent, from the split-ratio fact the store doesn't hold), dated by
      that fact and by the per-share figures restated across it: Carvana's 5-for-1 of May 2026, whose EPS no period
      holds on both bases, and CrowdStrike's 4-for-1, whose restated -$0.07 against -$0.31 is not an exact quarter;
    * two versions of one per-share figure (same metric, basis and span) that differ by a split ratio: APH's Q3 2025
      diluted EPS was $0.97 as first filed and $0.49 in its Q3 2026 10-Q. Without a reported split, the ratio must show
      in two periods or more: one restated period is a restatement (Block's Q3 2023 EPS, -$0.05 then -$0.15).
    The split took effect after the old versions and by the new ones. A filing in between is on the old basis when it
    repeats, unchanged, a figure already filed on the old basis (APH's FY2025 10-K repeats FY2024's $1.92), so
    old_basis_until moves to it. Pass only the facts and events visible at as_of."""
    versions: dict[tuple[str, str, Optional[date], date], list[Fact]] = {}
    for f in facts:
        if f.period_start is not None and f.segment is None and math.isfinite(f.value) and math.isfinite(_half_unit(f)):
            versions.setdefault((f.metric, f.basis, f.period_start, f.period_end), []).append(f)
    for vs in versions.values():
        vs.sort(key=lambda f: to_utc(f.accepted_at))
    every = sorted((f for vs in versions.values() for f in vs), key=lambda f: to_utc(f.accepted_at))

    def telling(old: Fact, new: Fact) -> bool:  # two cents of rounding can't tell a split: MCHP's -$0.01 and -$0.01
        small = min((old, new), key=lambda f: abs(f.value))
        return (to_utc(new.accepted_at) > to_utc(old.accepted_at) and abs(small.value) >= 10 * _half_unit(small)
                and not within(old.value, new.value, _half_unit(old) + _half_unit(new)))

    def dated(n: int, forward: bool, shown: list[tuple[Fact, Fact]], until: Optional[datetime],
              new_from: datetime) -> Split:
        olds = [o for o, _ in shown if to_utc(o.accepted_at) < new_from]
        until = max([*(to_utc(o.accepted_at) for o in olds), *([until] if until else [])], default=None)
        for f in every:  # a later filing that repeats an old-basis figure unchanged is on the old basis too
            at = to_utc(f.accepted_at)
            # only a figure the split would visibly change can show the old basis ($0.00 reads the same on both)
            visible = abs(f.value) * (n - 1) / (n if forward else 1) > 2 * _half_unit(f)
            if until is not None and until < at < new_from and visible and any(
                    to_utc(g.accepted_at) <= until and within(f.value, g.value, _half_unit(f) + _half_unit(g))
                    for g in versions[(f.metric, f.basis, f.period_start, f.period_end)]):
                until = at
        pair = max(shown, key=lambda p: to_utc(p[0].accepted_at)) if shown else ()
        return Split(n, forward, until, new_from, tuple(pair))

    strict: list[tuple[int, bool, Fact, Fact]] = []
    for vs in versions.values():
        for i, old in enumerate(vs):
            for new in vs[i + 1:]:
                n = split_ratio(old, new) if telling(old, new) else None
                if n:
                    strict.append((n, abs(old.value) > abs(new.value), old, new))
    out, claimed = [], set()
    events = sorted(events, key=lambda e: e.starts)
    for k, e in enumerate(events):
        start = datetime(e.starts.year, e.starts.month, e.starts.day, tzinfo=timezone.utc)
        end = datetime(e.ends.year, e.ends.month, e.ends.day, tzinfo=timezone.utc)
        later = [datetime(x.starts.year, x.starts.month, x.starts.day, tzinfo=timezone.utc) for x in events[k + 1:]
                 if (x.ratio, x.forward) == (e.ratio, e.forward)]
        # versions restated across the date, by about the ratio; and exact-ratio pairs whose new version follows it
        # (the date can be the approval: IBKR's 10-Q of May 7, 2025, after its 2025-04-15, is still on the old basis)
        shown = [(o, w) for vs in versions.values() for i, o in enumerate(vs) for w in vs[i + 1:]
                 if to_utc(o.accepted_at) < start <= to_utc(w.accepted_at) and telling(o, w)
                 and not any(to_utc(o.accepted_at) < s <= to_utc(w.accepted_at) for s in later)
                 and _restated_by(o, w, e.ratio, e.forward)]
        shown += [(o, w) for n, forward, o, w in strict
                  if (n, forward) == (e.ratio, e.forward) and start <= to_utc(o.accepted_at)
                  and not any(s <= to_utc(w.accepted_at) for s in later)]
        claimed |= {(id(o), id(w)) for o, w in shown}
        after = [to_utc(f.accepted_at) for f in every if to_utc(f.accepted_at) > end]
        new_from = (min(to_utc(w.accepted_at) for _, w in shown) if shown else min(after) if after
                    else e.reported)
        before = [to_utc(f.accepted_at) for f in every if to_utc(f.accepted_at) < start]
        out.append(dated(e.ratio, e.forward, shown, max(before) if before else None, new_from))
    # Without a reported split, only EPS counts: per-share dividends switch between quarterly and annual amounts
    # (Bunge's $0.68 and $2.72 for one quarter), which no split explains
    groups: dict[tuple[int, bool], list[tuple[Fact, Fact]]] = {}
    for n, forward, old, new in strict:
        if (id(old), id(new)) not in claimed and old.metric in ("eps_diluted", "eps_basic"):
            groups.setdefault((n, forward), []).append((old, new))
    for (n, forward), shown in groups.items():
        if len({(o.period_start, o.period_end) for o, _ in shown}) < 2:
            continue  # one restated period: a restatement, not a split
        new_from = min(to_utc(w.accepted_at) for _, w in shown)
        reverted = any(to_utc(v.accepted_at) > to_utc(w.accepted_at) and within(v.value, o.value, 2 * _half_unit(o))
                       for o, w in shown for v in versions[(o.metric, o.basis, o.period_start, o.period_end)])
        if not reverted and any(to_utc(o.accepted_at) < new_from for o, _ in shown):
            out.append(dated(n, forward, shown, None, new_from))
    return out


def _filings(o: Obs) -> frozenset[str]:
    """The filings an observed figure comes from: its fact's accession and, for a derived quarter, the filings it is
    derived from (Q4 = the FY in the 10-K minus the nine months in the Q3 10-Q)."""
    f = o.primary
    return frozenset((*f.derived_from, f.accession))


def _was(label: str) -> str:
    """The verb a metric label takes: "operating expenses were", "revenue was" (a judge read "operating expenses was")."""
    return "were" if label.endswith("expenses") else "was"


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
    """The name, and without a closing possessive. Never without its legal suffix: the store decides whether a suffix
    fits the company ("Prudential plc" is not Prudential Financial, Inc.: code review H1)."""
    out = [name.strip()]
    out.append(re.sub(r"(?:'s|’s)$", "", out[0]).strip())
    return [v for v in dict.fromkeys(out) if v]


def _resolve(store: EvidenceStore, name: str) -> Optional[tuple[int, str]]:
    for cand in _name_variants(name):
        r = store.resolve_entity(cand)
        if r:
            return int(r[0]), str(r[1])
    return None


# Words a context line may add after a company name: "NVIDIA earnings Q2 FY2027", "Apple 10-Q", "Meta Q3 2026 press release".
_CONTEXT_DOCUMENT_WORDS = frozenset({"earnings", "results", "release", "report", "call", "transcript", "preview", "recap",
                                     "update", "outlook", "guidance", "letter", "presentation", "press", "10-k", "10-q",
                                     "8-k"})
_FORM_NAME_RE = re.compile(r"(?<![\w-])(?:10-[KQ]|8-K)(?:/A)?(?![\w-])", re.I)  # dropped before the period words go


def _droppable(word: str) -> bool:
    """A context word a shorter prefix may leave out: lowercase, or a document word. "Copper" in "Southern Copper" is
    neither, so the line never becomes Southern Co. (nor "Goldman Sachs BDC" Goldman Sachs). A legal suffix never is,
    also in lowercase: "Prudential plc" never becomes Prudential (code review H1)."""
    if re.sub(r"[^a-z]", "", word.casefold()) in _LEGAL_FORM_WORDS:
        return False
    return word == word.lower() or word.casefold() in _CONTEXT_DOCUMENT_WORDS


_LEGAL_FORM_WORDS = frozenset({"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
                               "llc", "nv", "sa", "ag", "se"})  # receipts.store._LEGAL_SUFFIXES


def context_entities(context: Optional[str], store: EvidenceStore) -> list[tuple[int, str, str]]:
    """Companies a context line names, as (cik, ticker, name as written): 'NVIDIA Q2 FY2027' -> NVIDIA."""
    if not context:
        return []
    out: dict[int, tuple[int, str, str]] = {}
    for chunk in re.split(r"[;,/|]|\s+(?:and|vs\.?|versus)\s+", context):
        # A parenthetical is an aside, not part of the name ("Apple (AAPL)", "NVIDIA (NASDAQ: NVDA)"), unless the name is
        # only there ("Q3 2026 (Apple)"). A form name is a document word, dropped here because the period words below
        # would leave "-K" of "10-K".
        chunk = _FORM_NAME_RE.sub(" ", chunk)
        name = " ".join(_CONTEXT_NOISE_RE.sub(" ", re.sub(r"\([^()]*\)", " ", chunk)).split())
        if not re.search(r"[A-Za-z]", name):
            name = " ".join(_CONTEXT_NOISE_RE.sub(" ", chunk).split())
        if not re.search(r"[A-Za-z]", name):
            continue
        hit = _resolve(store, name)
        words = name.split()
        for k in range(len(words) - 1, 0, -1):  # "NVIDIA earnings" or "Apple 10-Q": try shorter prefixes
            if hit or not _droppable(words[k]):  # every word left out must be droppable, so stop at the first that isn't
                break
            hit = _resolve(store, " ".join(words[:k]))
        if hit and hit[0] not in out:
            out[hit[0]] = (hit[0], hit[1], name)
    return list(out.values())


# "The comparable / same / corresponding period (or quarter) of 2025" is the same fiscal quarter as the story's, in
# that year: "$0.48 for the first quarter of 2026, compared to $0.45 in the comparable period of 2025" means Q1 2025,
# not fiscal 2025 (the year alone read it as the full year) and not Q1 2026 ("the same quarter of 2025" lost its year).
# The quarter is the one the claim's sentence names, else the context's; with neither, or several, nothing changes.
_COMPARABLE_PERIOD_RE = re.compile(r"\b(?:the\s+)?(?:comparable|same|corresponding|equivalent|like)\s+(?:fiscal\s+)?"
                                   r"(?:period|quarter|three[\s-]+months?(?:\s+period)?)\s+(?:of|in)\s+"
                                   r"(?P<fiscal>fiscal\s+(?:year\s+)?)?(?P<year>(?:19|20)\d{2})\b", re.I)
_QUARTER_NAME_RE = re.compile(r"\b(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?P<fiscal>fiscal[\s-]+)?"
                              r"quarter\b|(?<![A-Za-z0-9])(?P<f>F)?Q(?P<q>[1-4])(?![0-9])", re.I)
_QUARTER_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def _comparable_period_text(claim: Claim, context: Optional[str]) -> Optional[str]:
    """claim.period_text with "the comparable period of <year>" as that year's quarter, or None (see above)."""
    hit = _COMPARABLE_PERIOD_RE.search(claim.period_text or "")
    if hit is None:
        return None
    span = _COMPARABLE_PERIOD_RE.sub(lambda m: " " * len(m.group()), claim.span_text or "")
    named = {(_QUARTER_ORDINALS[m["ord"].lower()] if m["ord"] else int(m["q"]),
              bool(m["fiscal"] or m["f"] or re.match(r"\s*(?:of\s+)?fiscal\b", span[m.end():], re.I)))
             for m in _QUARTER_NAME_RE.finditer(span)}
    if len({q for q, _ in named}) == 1:
        quarter, fiscal = next(iter(named))[0], any(f for _, f in named)
    elif not named and context and (cp := periods.parse(context)).kind == "quarter" and cp.quarter:
        quarter, fiscal = cp.quarter, cp.fiscal
    else:
        return None
    ordinal = ("first", "second", "third", "fourth")[quarter - 1]
    return f"{ordinal} quarter of {'fiscal ' if fiscal or hit['fiscal'] else ''}{hit['year']}"


# An exchange listing in parentheses ("(NYSE: GS)", "(Nasdaq: AAPL)") names the company its sentence is about. A sentence
# whose listings name only another covered company than the claim's ("The Goldman Sachs Group, Inc. (NYSE: Vertex
# Pharmaceuticals) today reported net revenues of $17.23 billion", the full set's planted entity copy) can't say whose
# figure it gives, so the figure is "?" (listing_conflict).
_EXCHANGE_LISTING_RE = re.compile(r"\(\s*(?:NYSE(?:\s+(?:American|Arca|MKT))?|NASDAQ|Nasdaq|AMEX|Cboe|CBOE|TSX)"
                                  r"(?:\s*(?:GS|GM|CM)\b|\s+Global\s+(?:Select\s+)?Market|\s+Capital\s+Market)?"
                                  r"\s*:\s*(?P<listing>[^():]{1,60}?)\s*\)")


def listing_conflict(claim: Claim, store: EvidenceStore, context: Optional[str], line: Optional[DraftLine],
                     entity: Optional[tuple[int, str]] = None) -> Optional[str]:
    """The exchange listing in the claim's draft sentence that names another covered company, when no listing there
    names the claim's own company; else None. A ticker in a listing is a ticker even where it is a word ("KEY")."""
    listings = list(_EXCHANGE_LISTING_RE.finditer(line.text)) if line is not None else []
    if not listings:
        return None
    own = entity or resolve_claim_entity(claim, store, context)
    if own is None:
        return None
    by_ticker = getattr(store, "by_ticker", None)
    other = None
    for m in listings:
        listed = " ".join(m["listing"].split())
        if re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z]{1,2})?", listed) and callable(by_ticker):
            hit = by_ticker(listed)
        else:
            hit = _resolve(store, listed)
        if hit is None:
            continue
        if int(hit[0]) == int(own[0]):
            return None
        other = other or " ".join(m.group(0).split())
    return other


def resolve_claim_entity(claim: Claim, store: EvidenceStore, context: Optional[str] = None) -> Optional[tuple[int, str]]:
    """(cik, ticker) for the claim's company. A generic subject ('the company') falls back to a context that
    names exactly one company; a named company the store doesn't know is never swapped for the context's."""
    name = (claim.entity or "").strip()
    if name.casefold() not in GENERIC_ENTITIES:
        return _resolve(store, name)
    ents = context_entities(context, store)
    return ents[0][:2] if len(ents) == 1 else None


# Markdown emphasis markers ("**4%**", "__record__") around words and numbers; an underscore inside a word stays.
_MARKDOWN_EMPHASIS_RE = re.compile(r"\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)")


def _without_markdown(text: str) -> str:
    """text with markdown emphasis markers blanked, keeping every offset."""
    return _MARKDOWN_EMPHASIS_RE.sub(lambda m: " " * len(m.group()), text or "")


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
def verify(claim: Claim, store: EvidenceStore, *, as_of: Any = None, context: Optional[str] = None,
           named: Optional[Sequence[tuple[int, str, str]]] = None,
           entity: Optional[tuple[int, str]] = None, draft: Optional[str] = None) -> Verdict:
    """The verdict for one claim: the check against filed facts (_verify_filed, which documents the arguments),
    then the release-text hook."""
    verdict = _verify_filed(claim, store, as_of=as_of, context=context, named=named, entity=entity, draft=draft)
    # --- release-text hook: a "?" for lack of evidence (no_evidence, and nothing else) becomes ✓ only when the
    # company's own filed 8-K earnings release for the claim's quarter states the same figure in words
    # (receipts.release_text). It never makes a figure ✗, and no match returns the verdict unchanged. Imported here:
    # it imports this module.
    from receipts import release_text
    return release_text.fallback(verdict, claim, store, as_of=as_of, context=context, entity=entity, draft=draft)
    # --- end of the release-text hook


def _verify_filed(claim: Claim, store: EvidenceStore, *, as_of: Any = None, context: Optional[str] = None,
                  named: Optional[Sequence[tuple[int, str, str]]] = None,
                  entity: Optional[tuple[int, str]] = None, draft: Optional[str] = None) -> Verdict:
    """The verdict for one claim from filed facts. as_of: datetime or ISO string; evidence accepted later is look-ahead.
    context: the document-context line (company and fiscal period) used for extraction.
    named: the companies the same draft names (named_in_draft): the only ones a wrong figure may be attributed to,
    besides those its context line names.
    entity: (cik, ticker) when the caller already knows the company, as Ask does for a claim it wrote from one stored
    fact (Store.by_ticker): the claim's entity text is then not resolved again. A draft's claims never pass it.
    draft: the text the claim's span offsets point into, as receipts.check passes it. Only the sentence holding the
    claim's number is read (draft_line), and only where the claim's own span can't show what a rule needs: a list
    item's shared head ("a charge of $22 million, $22 million"). Without it, or when the span isn't at its offsets,
    the verdict is read from the claim alone, as before."""
    as_of_dt = to_utc(as_of)
    # Markdown emphasis is no wording: "increased approximately **4% year over year**" reads as it would unbolded.
    claim = claim.model_copy(update={"span_text": _without_markdown(claim.span_text),
                                     "metric_text": _without_markdown(claim.metric_text)})
    draft = _without_markdown(draft) if draft else draft

    def plain(status: str, reason: str, explanation: str) -> Verdict:
        return Verdict(claim_id=claim.id, status=status, reason=reason, explanation=explanation, as_of=as_of_dt)

    if not math.isfinite(claim.value):
        return plain("unverifiable", "ambiguous", "This figure is not a finite number and cannot be checked.")
    if claim.forward_looking or claim.metric == "guidance_tolerance":
        return plain("unverifiable", "forward_looking",
                     "This is guidance about a future period, so no filing can confirm it yet.")
    if _ambiguous_margin_percent_change(claim):
        return plain("unverifiable", "ambiguous",
                     "A margin change written with % could mean relative growth or percentage points. "
                     "Specify which meaning is intended before comparing it with a filing.")
    # Constant-currency wording is named before reading the metric: it is also what leaves the metric unread.
    if currency_neutral(claim):
        return plain("unverifiable", "no_evidence",
                     f"{claim.value_text} is a constant-currency (FX-neutral) figure; filings report results at actual "
                     f"exchange rates, so NoCap can't check it.")
    if measure := distinct_measure(claim):
        return plain("unverifiable", "no_evidence",
                     f"The wording “{measure}” describes a company-defined measure that filings don't report under "
                     f"that name, so NoCap can't check {claim.value_text}.")
    if wording := basis_conflict(claim):
        return plain("unverifiable", "ambiguous",
                     f"The wording “{wording}” puts {claim.value_text} on a non-GAAP basis, but it was read as GAAP. "
                     f"With its basis unclear, NoCap doesn't check it.")
    line = draft_line(claim, draft)  # read before any rewrite below: the span must sit at its offsets
    # Whether the draft itself names a period anywhere: a story's "third-quarter 2026" covers its later sentences, but a
    # context line passed beside a draft that names none does not (_Check._line_names_a_period).
    draft_period = bool(draft) and bool(periods.find_period_phrase(draft) or _PERIOD_WORDS_RE.search(draft))
    if conflict := listing_conflict(claim, store, context, line, entity):
        return plain("unverifiable", "ambiguous",
                     f"The sentence gives {claim.value_text} for {claim.entity or 'its company'}, but its exchange "
                     f"listing “{conflict}” names another company, so NoCap can't tell whose figure it is.")
    # "attributable to <its own company>" is read as plain net income; the draft's own words stay for display
    original, claim = claim, _without_parent_attribution(claim, _own_company_names(claim, store, context, entity))
    income_line = _named_income_line(claim)
    if income_line == "ambiguous":
        return plain("unverifiable", "ambiguous", "The draft names both consolidated and common-stockholder "
                     "net income for this amount, so its intended line is unclear.")
    if income_line and (claim.unit == "%" or claim.basis == "non-GAAP" or absolute_change(claim)):
        return plain("unverifiable", "no_evidence", "NoCap's alternative net-income evidence covers GAAP "
                     "level amounts, not changes or non-GAAP versions of the named line.")
    if not income_line and not _per_share_wording(claim) and (
            claim.metric == "net_income" or _level_metric_phrases("net_income", claim.metric_text)):
        income_claim = claim.model_copy(update={"metric": "net_income"})
        own_words = _local_level_description(income_claim, growth=claim.unit == "%") or claim.metric_text
        if _TO_COMMON_RE.search(own_words):
            return plain("unverifiable", "no_evidence",
                         "This claim is net income available to common stockholders, which can differ after "
                         "preferred dividends; NoCap doesn't resolve that basis here, so it cannot compare it "
                         "with net income before those dividends.")
    qualifiers: list[str] = []
    reading_claim = claim
    if income_line or (claim.metric == "net_income" and _local_level_description(claim)):
        reading_claim = _income_reading_claim(claim)
    reading = interpret(reading_claim, qualifiers)
    if reading is None:
        # --- balance-sheet hook: a figure no rule above reads may be a balance-sheet item ("cash and cash
        # equivalents", "total assets", "total debt", "shares outstanding"), read from its own words and checked at a
        # balance-sheet date by receipts.balance_sheet; None leaves it untracked. Imported here: it imports this module.
        from receipts import balance_sheet
        balance = balance_sheet.check(original, store, as_of=as_of_dt, context=context, entity=entity)
        if balance is not None:
            return _visible_money_correction(original, balance)
        # --- end of the balance-sheet hook
        what = f" ({original.metric_text})" if original.metric_text else ""
        if qualifiers:
            return plain("unverifiable", "no_evidence",
                         f"The wording “{' '.join(qualifiers)}” qualifies this figure{what} in a way NoCap can't "
                         f"match to a filing fact, so there is nothing to check it against.")
        if absolute_change(original):
            return plain("unverifiable", "no_evidence",
                         f"{original.value_text} is a change{what}, not its level, and NoCap computes changes only of "
                         f"the base figures it tracks, so it doesn't check this one against a filing.")
        return plain("unverifiable", "no_evidence",
                     f"NoCap doesn't track this kind of figure{what} yet, so there is no filing fact to check.")
    if line is not None and _needs_its_sentence(reading_claim, reading) and (
            sentence_words := _sentence_subject_words(reading_claim, reading, line)):
        # A quote that names nothing ("an increase of 3%") is a change of its sentence's subject, and that subject's
        # qualifiers are its own: "Same-store revenue (adjusted for dispositions) of $904 million ..., an increase of 3%".
        what = f" ({original.metric_text})" if original.metric_text else ""
        return plain("unverifiable", "no_evidence",
                     f"The wording “{' '.join(sentence_words)}” in its sentence qualifies this figure{what} in a way NoCap "
                     f"can't match to a filing fact, so there is nothing to check it against.")
    if words := item_wording(original, line):  # a figure NoCap would read: never check an item within it as it
        return plain("unverifiable", "no_evidence",
                     f"“{words}” makes {original.value_text} an item within a reported figure, not the figure itself, "
                     f"and NoCap holds no filing fact for that item, so it can't check it.")
    entity = entity or resolve_claim_entity(claim, store, context)
    if entity is None:
        return unresolved_entity(claim, store, as_of_dt)
    own = (int(entity[0]), str(entity[1]))
    verdict = _Check(claim, store, own, as_of_dt, context, reading, named, line=line, draft_period=draft_period).run()
    if (verdict.status == "wrong" and verdict.reason != "look_ahead" and claim.unit != "%"
            and claimed_value(claim, change=False) != claimed_value(claim, change=False, loss_scope=False)):
        # Its own clause reads the figure as income while a loss word elsewhere in its span would read it as a loss
        # (_income_subject). The figure stays "✗" only when that loss reading is wrong too.
        loss = _Check(claim, store, own, as_of_dt, context, reading, named, loss_scope=False, line=line,
                      draft_period=draft_period).run()
        if loss.status == "unverifiable":
            verdict = loss
        elif loss.status != "wrong" or loss.reason == "look_ahead":
            verdict = plain("unverifiable", "ambiguous",
                            f"Read as income, as its own words say, {claim.value_text} doesn't match the filing; "
                            f"read as a loss, as other words in the same text say, it fits. NoCap can't tell which "
                            f"the draft means.").model_copy(update={"evidence": loss.evidence})
    return _visible_money_correction(claim, verdict)


def _own_company_names(claim: Claim, store: EvidenceStore, context: Optional[str],
                       entity: Optional[tuple[int, str]]) -> Optional[Callable[[str], bool]]:
    """For _without_parent_attribution, with the store: whether a name or ticker ("MPC", "The Walt Disney Company")
    resolves to the claim's own company. None when the claim names no attribution or its company is unknown."""
    if not any(_PARENT_ATTRIBUTION_RE.search(text or "") for text in (claim.span_text, claim.metric_text)):
        return None
    own = entity or resolve_claim_entity(claim, store, context)
    if own is None:
        return None
    return lambda name: (hit := _resolve(store, name)) is not None and hit[0] == int(own[0])


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def unresolved_entity(claim: Claim, store: EvidenceStore, as_of: Optional[datetime] = None) -> Verdict:
    """'?' for a company the store doesn't resolve, saying why: a generic subject with no company in the context, a
    name that mixes or abbreviates covered companies, or else, in words that are always true, a name NoCap couldn't
    match to a company it covers: the company may be outside coverage, or covered under another name. In that case a
    "did you mean" names the one covered company the text most likely meant (Store.suggestion), without resolving it."""
    who = " ".join((claim.entity or "").split())

    def plain(reason: str, explanation: str) -> Verdict:
        return Verdict(claim_id=claim.id, status="unverifiable", reason=reason, explanation=explanation, as_of=as_of)

    if who.casefold() in GENERIC_ENTITIES:
        return plain("ambiguous", "The draft doesn't say which company this figure belongs to.")
    word_name = getattr(store, "word_name", None)
    owner = word_name(who) if callable(word_name) else None
    if owner:  # "Flex": an everyday word as well as Flex Ltd.'s name
        return plain("ambiguous", f"\"{who}\" is an everyday word as well as a company name, so NoCap doesn't match it "
                                  f"by itself; write the company's full name, for example \"{owner[2]}\".")
    covered = known_companies(store)
    if not covered:
        return plain("ambiguous", f"Couldn't match \"{who}\" to a company in the SEC filings database.")
    # The same free-text rules as a draft's own text: names in any case, a ticker only as that word in capitals, never a
    # word ticker or an ordinary word ("Groupe PSA" is not Public Storage, "Lin TV" not Linde).
    mentioned = [name for _cik, _ticker, name in named_in_draft(who, store)]
    if len(mentioned) > 1:
        return plain("ambiguous", f"\"{who}\" names more than one company ({_join(mentioned)}), so NoCap can't "
                                  f"tell whose figure this is.")
    if mentioned:
        # "Amazon Web Services" is a unit of Amazon, but "Coca-Cola Consolidated" or "Blackstone Mortgage Trust" is
        # another company: the name alone can't tell, so the wording claims neither, and the figure is never checked
        # against the covered company's company-wide filings
        return plain("ambiguous", f"\"{who}\" contains the name of {mentioned[0]}, which NoCap covers, but isn't a name "
                                  f"NoCap matches to it (it may be a unit, a subsidiary or another company), so NoCap "
                                  f"doesn't check it against {possessive(mentioned[0])} filings.")
    suggest = getattr(store, "suggestion", None)
    try:
        likely = suggest(who) if callable(suggest) else None
    except Exception:  # a hint only: a store that can't give one gives none
        likely = None
    hint = f" (did you mean {likely[2]}?)" if likely else ""
    listed = f" (it covers {len(covered)} companies today: {_join([n for _, _, n in covered])})" \
        if len(covered) <= LIST_COVERED_UP_TO and not likely else ""
    return plain("no_evidence", f"NoCap couldn't match \"{who}\" to a company it covers{hint}, so there is no filing "
                                f"data to check it against{listed}.")


# Period words in a figure's own sentence: with none, its period is only the context's (_Check._line_names_a_period).
_PERIOD_WORDS_RE = re.compile(r"\b(?:quarters?|quarterly|years?|yearly|annual(?:ly)?|fiscal|f?y\d{2,4}|fy|q[1-4]|[1-4]q"
                              r"|h[12]|halves|half|months?|monthly|periods?|ytd|year-to-date|weeks?|ended|ending|ago"
                              r"|prior|previous|preceding|last|sequential(?:ly)?|yoy|qoq|y/y|q/q|(?:19|20)\d{2}"
                              r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
                              r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", re.I)
# The two sides of a stated basis (_Check._sibling_basis): reported or GAAP, and the company's adjusted measures.
_NON_GAAP_SIDE_RE = re.compile(r"\b(?:non[\s‐‑-]?gaap|adjusted|comparable|core|underlying|managed|organic"
                               r"|(?:fully[\s-])?taxable[\s-]equivalent|fte)\b", re.I)
_GAAP_SIDE_RE = re.compile(r"\b(?:gaap|reported)\b", re.I)


def _sibling_figure(text: str, claim: Claim) -> Optional[tuple[float, float]]:
    """(value, half unit) in full units of a figure written like the claim's (a percent for a percent, dollars for
    dollars, a per-share amount for a per-share amount), or None."""
    number = _NUM_RE.search(text)
    if number is None:
        return None
    digits = number.group(0)
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    value, half = float(digits.replace(",", "")), 0.5 * 10.0 ** -decimals
    percent = bool(re.search(r"%|percent", text, re.I))
    if claim.unit == "%":
        return (value, half) if percent else None
    if percent or "$" not in text:
        return None
    word = re.search(r"(trillion|billion|million|thousand|[BMK])\b", text, re.I)
    if claim.unit == "USD/share":
        return None if word else (value, half)
    if claim.unit != "USD":
        return None
    factor = {"t": 1e12, "trillion": 1e12, "b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3,
              "thousand": 1e3}.get(word.group(1).lower(), 1.0) if word else SCALE_FACTOR.get(claim.scale, 1.0)
    return value * factor, half * factor


class _Check:
    def __init__(self, claim: Claim, store: EvidenceStore, entity: tuple[int, str], as_of: Optional[datetime],
                 context: Optional[str], reading: Optional[Reading] = None,
                 named: Optional[Sequence[tuple[int, str, str]]] = None, *, loss_scope: bool = True,
                 line: Optional[DraftLine] = None, draft_period: bool = False):
        self.c, self.store, self.as_of, self.context = claim, store, as_of, context
        self.line = line  # the draft sentence holding the number, when the draft was given (draft_line)
        self.draft_period = draft_period  # the draft names a period somewhere (verify)
        self.named = list(named or ())
        self.cik, self.ticker = entity
        context_companies = context_entities(context, store)
        self.context_named = context_companies  # kept for attribution even when the context stops steering periods
        if context_companies and all(cik != self.cik for cik, _, _ in context_companies):
            # A named month can share a calendar year/date across companies, but not another company's FY.
            cp = periods.parse(context)
            if periods.parse(claim.period_text).month is None or cp.fiscal:
                self.context = None
        name = " ".join(re.sub(r"\([^)]*\)", " ", claim.entity).split())  # "Amazon (AMZN)" -> "Amazon"
        self.company = name if name.casefold() not in GENERIC_ENTITIES else self.ticker
        self.co_s = possessive(self.company)
        self.cal = periods.calendar(store, self.cik)
        r = reading or interpret(claim) or Reading(claim.metric, None, None)
        terms = span_terms(claim, r)
        self.growth_base = terms.growth_base  # a percent change of this metric, or None for a level figure
        self.absolute = r.absolute  # the change in dollars or per share ("up $0.32"), not a percent
        self.comparison_ambiguous = terms.comparison_ambiguous
        self.comparison = terms.comparison
        self.derived = terms.derived
        margin_value = _margin_change_value(claim) if self.growth_base in _MARGIN_METRICS else None
        self.margin_unit = margin_value[0] if margin_value else None
        if margin_value:
            claim = claim.model_copy(update={"value": margin_value[1], "unit": "other", "scale": "units"})
            self.c = claim
        self.alternatives = terms.alternatives
        self.segment = terms.segment
        self.metric = terms.metric
        self.basis_stated = claim.basis is not None
        self.basis = terms.basis
        # The draft calls the figure (net) sales of a company whose total revenue includes more (above)
        own_words = _own_wording(claim)
        self.sales_only = (self.ticker in NET_SALES_BELOW_TOTAL_REVENUE and self.metric in ("revenue", "revenue_growth")
                           and not self.segment and bool(_SALES_NOUN_RE.search(own_words))
                           and not _REVENUE_NOUN_RE.search(own_words))
        self.claimed = claimed_value(claim, change=self.growth_base is not None, loss_scope=loss_scope)
        if self.margin_unit:
            self.claimed = _margin_claimed_value(claim)
        self.tol = tolerance(claim)
        self._facts: dict[tuple[Any, ...], list[Fact]] = {}
        self.invalid_numeric_evidence = False
        self.hedge = terms.hedge
        self.superseded: list[tuple[Obs, Obs, int]] = []  # (pre-split version, the version that replaced it, ratio)
        self._splits: Optional[list[Split]] = None  # _company_splits, computed on first use
        # (period, comparison) -> base period of a change whose two figures no single filing reports together
        self.unpaired: dict[tuple[ResolvedPeriod, str], ResolvedPeriod] = {}
        self.gross_margin_base_missing: set[ResolvedPeriod] = set()
        self._allow_not_revenue_rows = False  # _diag_not_revenue_row only: the rows level() keeps out of revenue
        self.rp: Optional[ResolvedPeriod] = None  # the period _run resolved for the claim
        # the period words resolved: "the comparable period of 2025" as that year's quarter (_comparable_period_text)
        self.period_words = _comparable_period_text(claim, self.context) or claim.period_text

    # -- verdicts ------------------------------------------------------------
    def verdict(self, status: str, reason: str, explanation: str, *, evidence: Iterable[Fact] = (),
                expected: Optional[float] = None) -> Verdict:
        evidence = _dedupe_facts(evidence)
        if evidence and (self.margin_unit or self.derived == "net_margin"):
            # Every outcome, including basis/period diagnoses, names all operands rather than attributing a
            # computed margin to a single filed cell. Receipts retain the actual Facts and their locators.
            groups: dict[tuple[str, date], list[Fact]] = {}
            for f in evidence:
                groups.setdefault((f.accession, f.period_end), []).append(f)
            inputs = []
            for (accession, end), facts in groups.items():
                values = [f"{f.basis} {METRIC_LABELS.get(f.metric, f.metric)} "
                          f"{f.value:g}%" if f.unit == "%" else
                          f"{f.basis} {METRIC_LABELS.get(f.metric, f.metric)} {fmt_money(f.value)}" for f in facts]
                inputs.append(f"{', '.join(values)} (period ended {end}; {accession})")
            explanation += " Inputs: " + "; ".join(inputs) + "."
        return Verdict(claim_id=self.c.id, status=status, reason=reason, explanation=explanation,
                       expected_value=None if expected is None else to_claim_units(expected, self.c),
                       evidence=evidence, as_of=self.as_of)

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
            kind = "change" if self.margin_unit or self.absolute else "growth"
            return f"{self.subject() if basis_word else METRIC_LABELS.get(self.growth_base, 'figure')} {kind}"
        base = METRIC_LABELS.get(self.metric, self.metric)
        if self.segment and self.metric in ("segment_revenue", "revenue_growth"):
            base = f"{self.segment} {base}"
        elif self.segment and self.metric not in ("revenue", "segment_revenue"):
            base = f"{self.segment} {base}"
        if basis_word and self.c.basis and self.metric not in ("revenue", "segment_revenue", "revenue_growth"):
            base = f"{self.c.basis} {base}"
        return base

    def fmt(self, v: float) -> str:
        if self.margin_unit:
            digits = max(1, decimals_of(self.c.value_text, self.c.value) + 1)
            return f"{v:.{digits}f} {self.margin_unit}"
        if self.absolute:
            return fmt_value(v, self.growth_base or self.metric, self.c)
        return fmt_value(v, "revenue_growth" if self.growth_base else self.metric, self.c)

    def fmt_observation(self, o: Obs) -> str:
        """A filed rate uses the cell's own decimals, never the draft's precision.

        Computed changes and ratios keep their calculated display precision: their operands' decimals
        describe the inputs, not the resulting rate.
        """
        f = o.primary
        if (not self.growth_base and len(o.facts) == 1 and f.unit == "%"
                and f.value == o.value and f.source != "derived"):
            if f.decimals is not None:
                return f"{o.value:.{max(0, f.decimals)}f}%"
            return f"{o.value:g}%"  # no recorded precision: do not invent trailing decimal places
        return self.fmt(o.value)

    def statement(self, o: Obs, rp: ResolvedPeriod, comparison: Optional[str] = None,
                  cal: Optional[list[CalEntry]] = None, *, filed_precision: bool = False) -> str:
        if self.growth_base:
            verb = "grew" if o.value >= 0 else "fell"
            return f"{self.subject()} {verb} {self.fmt(abs(o.value))} {_cmp_words(comparison or self.comparison)} " \
                   f"in {self.plabel(rp, cal)}{f' ({o.calculation})' if o.calculation else ''}"
        if self.derived == "net_margin":
            return f"{self.mlabel()} was {self.fmt(o.value)} for {self.plabel(rp, cal)} (net income / revenue × 100)"
        value = self.fmt_observation(o) if filed_precision else self.fmt(o.value)
        return f"{self.mlabel()} {_was(self.mlabel())} {value} for {self.plabel(rp, cal)}"

    def attribution(self, o: Obs, *, matched: bool = False) -> str:
        """A cross-filing change is computed, not reported by its current figure's filing (review L5)."""
        if self.margin_unit or self.derived == "net_margin":
            lead = "Matches the calculation from" if matched else "Computed from"
            return f"{lead} {self.co_s} {form_phrase(o.primary)}: "
        if self.growth_base and len(o.facts) == 2:
            c, p = (Obs(f.value, (f,)) for f in o.facts)
            if not _filings(p) <= _filings(c):
                sources = [f"{form_phrase(f)} ({fmt_value(f.value, f.metric, self.c.model_copy(update={'unit': f.unit}))} "
                           f"for the period ended {f.period_end})" for f in o.facts]
                lead = "Matches the change computed from" if matched else "Computed from"
                return f"{lead} {self.co_s} {sources[0]} and its {sources[1]}: "
        return (f"Matches {self.co_s} {form_phrase(o.primary)}: " if matched
                else f"{self.co_s} {form_phrase(o.primary)} reports that ")

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
        if metric == "noninterest_income":
            return self._noninterest_income(cik, rp, basis, segment)
        want = "instant" if metric in INSTANT_METRICS else "annual" if rp.fiscal_period == "FY" else "quarter"
        # The calendar's own start for this quarter, for a 16- or 17-week quarter (only the company's own calendar)
        start = next((e.start for e in self.cal if (e.fiscal_year, e.fiscal_period) == (rp.fiscal_year, rp.fiscal_period)),
                     None) if cik == self.cik else None
        out = []
        for f in self._store_facts(cik, metric, rp.period_end, segment):
            if f.cik != cik or f.metric != metric or f.period_end != rp.period_end:
                continue
            if (metric == "revenue" and segment is None and f.source == "press_release" and _kept_out_of_revenue(f)
                    and not self._allow_not_revenue_rows):
                continue  # a comparable, same-store, constant-currency, organic or pro forma row is not revenue
            if metric == "revenue" and segment is None and cik == self.cik and self.sales_only and not (
                    (f.concept or "").split(":")[-1] in ("SalesRevenueNet", "SalesRevenueGoodsNet")
                    or _NET_SALES_ROW_RE.fullmatch((f.locator or "").split("|", 1)[0])):
                continue  # total revenue is not the net sales the draft names
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
            elif _fact_kind(f) != want and not (want == "quarter" and _long_quarter(f, rp, start)):
                continue
            if not math.isfinite(f.value) or not math.isfinite(_half_unit(f)):
                self.invalid_numeric_evidence = True
                continue
            out.append(Obs(float(f.value), (f,)))
        if basis == "non-GAAP" and out and metric not in ("revenue", "segment_revenue"):
            # Never a GAAP row of a reconciliation table as the non-GAAP figure (_gaap_row), for every metric but revenue:
            # an adjusted figure is never confirmed or rejected by GAAP evidence. A rate is read from its label alone.
            # Revenue has one value either way, so a release's revenue row among its non-GAAP figures is its non-GAAP
            # revenue (AMD's non-GAAP revenue is its revenue).
            reported = self.level(cik, metric, rp, None, segment) if any(o.primary.unit == "USD" for o in out) else []
            out = [o for o in out if not _gaap_row(o.primary, reported if o.primary.unit == "USD" else ())]
        if metric in PER_SHARE_METRICS and len(out) > 1:
            out = self._split_consistent(out)
        return out

    def _noninterest_income(self, cik: int, rp: ResolvedPeriod, basis: Optional[str],
                            segment: Optional[str]) -> list[Obs]:
        """A bank's noninterest income as its filing tags it: the NoninterestIncome term of the revenue the store serves
        as net interest income plus noninterest income (receipts.ingest.BANK_REVENUE), cited from that revenue fact,
        whose locator shows both terms. GAAP, company-wide, one filing's own figure only; a bank whose revenue is
        tagged directly holds no such line, so its noninterest income stays "?"."""
        if basis == "non-GAAP" or segment:
            return []
        out = []
        for o in self.level(cik, "revenue", rp, basis):
            f = o.primary
            hit = _BANK_REVENUE_FORMULA_RE.fullmatch(f.locator or "")
            if (hit is None or f.source != "derived" or f.concept != "us-gaap:RevenuesNetOfInterestExpense"
                    or (hit["form"], hit["accession"]) != (f.form, f.accession)
                    or list(f.derived_from) != [f.accession]):
                continue
            nii, other = (float(hit[name].replace(",", "")) for name in ("nii", "other"))
            if math.isfinite(other) and math.isclose(nii + other, f.value, rel_tol=0.0, abs_tol=0.5):
                out.append(Obs(other, (f,)))
        return out

    def _split_consistent(self, obs: list[Obs]) -> list[Obs]:
        """Per-share versions of one figure that differ by a stock-split ratio: only those on the share basis of the
        latest version visible at as_of count (with no as_of, the latest version). A split restates every earlier
        per-share figure, so the pre-split number no longer describes the shares. Versions that differ for other
        reasons (BAC's 2026 restatement) all still count."""
        visible = [o for o in obs if self.public(o)]
        if len(visible) < 2:
            return obs
        def version_order(o: Obs) -> tuple[datetime, int]:
            return o.public_at, -_source_rank(o.primary)

        visible.sort(key=version_order, reverse=True)
        keep = []
        for o in obs:
            replacement = None
            if self.public(o):
                for newer in visible:
                    if version_order(newer) <= version_order(o):
                        continue
                    n = split_ratio(o.primary, newer.primary) or self._split_between(o.primary, newer.primary)
                    if n:
                        replacement = (o, newer, n)
                        break
            if replacement is None:
                keep.append(o)
            else:
                # A later genuine restatement can obscure the original ratio: 5.87 -> 0.59 -> 0.60.
                # The intervening split still supersedes 5.87; both post-split versions remain usable.
                self.superseded.append(replacement)
        return keep

    def _company_splits(self) -> list[Split]:
        """The company's stock splits visible at as_of: company_splits over all its per-share figures and the splits its
        XBRL reports (receipts.splits), once per check."""
        if self._splits is None:
            facts = [f for m in sorted(PER_SHARE_METRICS) for f in (self.store.facts(self.cik, m) or [])
                     if self.as_of is None or to_utc(f.accepted_at) <= self.as_of]
            events = [e for e in split_events(self.ticker) if self.as_of is None or e.reported <= self.as_of]
            self._splits = company_splits(facts, events)
        return self._splits

    def _split_between(self, old: Fact, new: Fact) -> Optional[int]:
        """The ratio of a stock split of this company when new restates old for it: old filed on the old share basis,
        new on the new one, their ratio near the split's (CrowdStrike's -$0.31 and -$0.07 across its 4-for-1)."""
        for s in self._company_splits():
            if (s.old_basis_until is not None and to_utc(old.accepted_at) <= s.old_basis_until
                    and to_utc(new.accepted_at) >= s.new_basis_from and _restated_by(old, new, s.ratio, s.forward)):
                return s.ratio
        return None

    def growth(self, cik: int, cal: list[CalEntry], rp: ResolvedPeriod, comparison: str, basis: Optional[str],
               segment: Optional[str]) -> list[Obs]:
        """Percent change of the base metric against the previous fiscal quarter (QoQ) or the same quarter a year
        earlier (YoY), from one filing's own figures: the base must come from the current figure's filing (the 10-Q's
        prior-year context, the release's prior column) or, for a derived quarter, from the filings it is derived from.
        A company that recast the base period (discontinued operations, a new non-GAAP definition, a stock split)
        reports the recast figure in the current filing, while another filing's version may be on another basis (S&P
        500 QA, T3: MLM's Q2 2025 revenue was $1,811M as first filed and $1,609M as recast; Q2 2026's $1,947M is 21%
        above the recast figure, not 7.5% above the original). With no such pair, figures from two filings count only
        when no recast took effect between them (_unrestated_pairs); else there is no change to check ("?").
        The inputs' own rounding is carried as err: per-share figures move a change by up to ~1 pp."""
        base = (periods.shift(rp, cal, quarters=-1) if comparison in ("QoQ", "sequential")
                else periods.shift(rp, cal, years=-1))
        metric = self.growth_base or "revenue"
        if metric == "revenue" and segment:
            metric = "segment_revenue"
        cur, prev = self.level(cik, metric, rp, basis, segment), self.level(cik, metric, base, basis, segment)
        pairs = [(c, p) for c in cur for p in prev if _filings(p) <= _filings(c)]
        # Noninterest income is a term of a revenue fact's locator: only one filing's own pair is read for it.
        if not pairs and cur and prev and metric != "noninterest_income":
            pairs = self._unrestated_pairs(cik, metric, cal, rp, cur, prev, basis, segment)
        if not pairs and base is not None and any(self.absolute or p.value > 0 for p in prev) and cur:
            self.unpaired[(rp, comparison)] = base
        out = []
        for c, p in pairs:
            if self.absolute:  # the change itself, carrying both figures' rounding
                level = lambda v: fmt_value(v, metric, self.c)  # noqa: E731
                out.append(Obs(c.value - p.value, c.facts + p.facts, _half_unit(c.primary) + _half_unit(p.primary),
                               f"from {level(p.value)} to {level(c.value)}"))
                continue
            if p.value <= 0:
                continue  # a change from zero or from a loss has no meaningful percent
            ratio = c.value / p.value
            rel = (_half_unit(c.primary) / abs(c.value) if c.value else 0.0) + _half_unit(p.primary) / p.value
            out.append(Obs((ratio - 1.0) * 100.0, c.facts + p.facts, abs(ratio) * rel * 100.0))
        return out

    def _unrestated_pairs(self, cik: int, metric: str, cal: list[CalEntry], rp: ResolvedPeriod, cur: list[Obs],
                          prev: list[Obs], basis: Optional[str], segment: Optional[str]) -> list[tuple[Obs, Obs]]:
        """Pairs across filings, when no filing reports both periods (a 10-Q never reports the previous quarter): the
        current figure with the latest version of the base filed by then, when nothing was restated between the two
        filings, as the prior-year figure shows (_restated_nothing_between). A recast restates the comparatives of every
        filing after it, so then none took effect in between and both figures are on one basis. A new non-GAAP
        definition, discontinued operations or a stock split shows as a changed comparative, and the change stays "?"."""
        prior = periods.shift(rp, cal, years=-1)
        out = []
        for c in cur:
            filed = [p for p in prev if p.public_at <= c.public_at]
            if not filed:
                continue
            latest = max(p.public_at for p in filed)
            out += [(c, p) for p in filed if p.public_at == latest
                    and self._restated_nothing_between(cik, metric, prior, p, c, basis, segment)]
        return out

    def _restated_nothing_between(self, cik: int, metric: str, prior: Optional[ResolvedPeriod], p: Obs, c: Obs,
                                  basis: Optional[str], segment: Optional[str]) -> bool:
        """Whether the filing of c reports the prior-year period at the value it had when the base p was filed (its
        latest version public then), as every version filed since did: then no recast took effect between the two
        filings. False when it restated the period, or there is nothing to compare. A version filed in between is one
        more that must agree, not a reason to refuse: Dollar Tree's FY2025 release, filed three minutes before its
        10-K, reports FY2024 as the 10-K does (code review L4). A derived fourth quarter's 10-K is compared on the prior
        fiscal year it reports."""
        if prior is None or prior.period_end is None:
            return False
        f = c.primary
        if f.source == "derived" and f.fiscal_period == "Q4":
            prior = ResolvedPeriod(prior.fiscal_year, "FY", prior.period_end)
        versions = self.level(cik, metric, prior, basis, segment)
        own = [o for o in versions if o.primary.accession == f.accession]
        if not own:
            return False
        at = min(o.public_at for o in own)
        earlier = [o for o in versions if o.primary.accession != f.accession and o.public_at < at]
        then = [o.public_at for o in earlier if o.public_at <= p.public_at]
        if not then:
            return False
        since = [o for o in earlier if o.public_at >= max(then)]  # the version current when p was filed, and later ones
        return all(within(m.value, o.value, _half_unit(m.primary) + _half_unit(o.primary)) for m in own for o in since)

    def operating_margin(self, cik: int, rp: ResolvedPeriod, basis: Optional[str]) -> list[Obs]:
        """Operating income / revenue for the company. A non-GAAP margin uses the release's non-GAAP operating
        income over revenue (revenue has one value either way); it is cited from the press release."""
        return self.income_margin(cik, rp, basis, "operating_income", strict=False)

    @staticmethod
    def _same_margin_filing(facts: tuple[Fact, ...]) -> bool:
        """All operands are this filing's own figures, including any derived inputs' provenance."""
        return len({a for f in facts for a in (f.accession, *f.derived_from)}) == 1

    def income_margin(self, cik: int, rp: ResolvedPeriod, basis: Optional[str], metric: str,
                      *, strict: bool = True, reported_base: bool = False) -> list[Obs]:
        """Shared income/revenue machinery; new margins keep both inputs within one filing and duration. A non-GAAP
        margin takes the release's own non-GAAP revenue as its base where the release gives one (Delta's "Operating
        revenue, adjusted"), else reported revenue; reported_base=True: reported revenue only (_diag_reported_base)."""
        revenue = self.level(cik, "revenue", rp, None)
        income = self.level(cik, metric, rp, basis)
        adjusted_revenue = []
        if basis == "non-GAAP":
            reported = self.level(cik, metric, rp, None)
            income = [o for o in income if not _gaap_row(o.primary, reported)]  # never a GAAP row as non-GAAP
            adjusted_revenue = [] if reported_base else self.level(cik, "revenue", rp, basis)
        out = []
        for i in income:
            # Use adjusted revenue if the release provides it; otherwise its reported revenue, as for
            # operating margin. Never borrow a denominator from another release or reporting version.
            own_adjusted = [r for r in adjusted_revenue if r.primary.accession == i.primary.accession]
            for r in own_adjusted or revenue:
                facts = i.facts + r.facts
                if not r.value or (strict and (r.value <= 0 or i.primary.unit != "USD" or r.primary.unit != "USD"
                                               or not self._same_margin_filing(facts)
                                               or i.primary.period_start != r.primary.period_start)):
                    continue
                # The rounding of both inputs bounds the margin, for an operating margin too (strict=False): Illumina's
                # non-GAAP operating margin of 22.5% is 260 / 1,159 = 22.43% from its rounded figures, 22.38-22.49%
                # with their rounding, and the release's own rate is computed before that rounding.
                out.append(Obs(i.value / r.value * 100.0, facts, self._margin_error(i.primary, r.primary)))
        return out

    @staticmethod
    def _margin_error(numerator: Fact, revenue: Fact) -> float:
        """Conservative ratio interval in percentage points, including losses and input rounding."""
        n, r = numerator.value, revenue.value
        ne, re = _half_unit(numerator), _half_unit(revenue)
        if r <= re:
            return math.inf
        ratio = n / r
        ends = [(n + dn) / (r + dr) for dn in (-ne, ne) for dr in (-re, re)]
        return max(abs(v - ratio) for v in ends) * 100.0

    def margin_levels(self, cik: int, rp: Optional[ResolvedPeriod], basis: Optional[str]) -> list[Obs]:
        """Margins for a point change: same filing, period, basis and company at each endpoint."""
        if rp is None:
            return []
        if self.metric != "gross_margin":
            return self.income_margin(cik, rp, basis,
                                      "net_income" if self.metric == "net_margin" else "operating_income")
        return self._gross_margin_levels(cik, rp, basis, rounding=True)

    @staticmethod
    def _net_sales(o: Obs) -> bool:
        """Only an explicit consolidated sales tag or cell identifies a sales denominator."""
        f = o.primary
        return ((f.concept or "").split(":")[-1] == "SalesRevenueNet"
                or bool(re.fullmatch(r"\s*net\s+sales\s*(?:\|.*)?", f.locator or "", re.I)))

    def _gross_margin_levels(self, cik: int, rp: ResolvedPeriod, basis: Optional[str],
                             *, rounding: bool) -> list[Obs]:
        """Prefer stated rates; otherwise use one filing's numerator and compatible sales base."""
        rates, gross_reported = self.level(cik, "gross_margin", rp, basis), []
        if basis == "non-GAAP":  # never a GAAP rate or gross profit row as the non-GAAP one (_gaap_row)
            rates = [o for o in rates if not _gaap_row(o.primary)]
            gross_reported = self.level(cik, "gross_profit", rp, None)
        stated = [Obs(o.value, o.facts, _half_unit(o.primary) if rounding else 0.0)
                  for o in rates if o.primary.unit == "%" and self._same_margin_filing(o.facts)]
        # A stated rate is the company's own figure. A calculation from one filing's sales base stands beside it only
        # where it rounds to a public stated rate (AMD prints "55%" and its dollars give 55.45%), so a claim finer
        # than the stated rate can still be checked, while a calculation on another base (Walmart's total revenues
        # beside its "gross profit rate" on net sales) cannot. A future release must not suppress an earlier,
        # available calculation.
        public_stated = [o for o in stated if self.public(o)]
        revenue = self.level(cik, "revenue", rp, None)
        gross = [o for o in self.level(cik, "gross_profit", rp, basis) if not _gaap_row(o.primary, gross_reported)]
        cost = self.level(cik, "cost_of_revenue", rp, None) if basis != "non-GAAP" else []

        def same_inputs(*obs: Obs) -> bool:
            return (self._same_margin_filing(tuple(f for o in obs for f in o.facts))
                    and len({o.primary.period_start for o in obs}) == 1
                    and all(o.primary.unit == "USD" for o in obs))

        # A reported gross profit that does not reconcile to total revenue less
        # costs reveals additional revenue outside the gross-profit base. Retail
        # merchandise costs likewise require net sales, not membership revenue.
        sales_required = any(self.public(k) and re.search(r"\bmerchandise\s+costs?\b", k.primary.locator or "", re.I)
                             for k in cost)
        sales_required |= any(self.public(g) and self.public(k) and self.public(r)
                              and same_inputs(g, k, r) and not self._net_sales(r)
                              and not within(g.value + k.value, r.value,
                                             sum(_half_unit(o.primary) for o in (g, k, r)))
                              for g in gross for k in cost for r in revenue)
        sales_required |= any(self.public(r) and re.search(r"\bmembership\b|\bother\s+income\b",
                                                          r.primary.locator or "", re.I) for r in revenue)
        out = list(stated)
        for numerator in gross or cost:
            own_revenue = [r for r in revenue if r.value > 0 and same_inputs(numerator, r)]
            sales = [r for r in own_revenue if self._net_sales(r)]
            if sales_required or sales:
                # Multiple conflicting sales cells leave the base unresolved.
                if not sales or any(not within(a.value, b.value, _half_unit(a.primary) + _half_unit(b.primary))
                                    for a in sales for b in sales):
                    if cik == self.cik and not public_stated:
                        self.gross_margin_base_missing.add(rp)
                    continue
                own_revenue = sales
            for r in own_revenue:
                value = numerator.value / r.value * 100.0
                is_cost = numerator.primary.metric == "cost_of_revenue"
                if is_cost:
                    value = 100.0 - value
                # Keep the level receipt's revenue-first order for cost-derived
                # margins; point-change receipts identify the numerator first.
                facts = r.facts + numerator.facts if is_cost and not rounding else numerator.facts + r.facts
                out.append(Obs(value, facts,
                               self._margin_error(numerator.primary, r.primary) if rounding else 0.0))
        if public_stated:
            # Only where every stated rate is coarser than the claim's own precision (in percentage points; a change
            # in basis points is a hundredth of that) does a calculation that rounds to one join it; otherwise the
            # stated rate decides, and corrects, alone.
            claim_tol = self.tol / 100.0 if self.margin_unit == "basis points" else self.tol
            if not self.margin_unit and not all(
                    _half_unit(st.primary) > claim_tol * (1.0 + 1e-9) for st in public_stated):
                return stated
            return list(stated) + [o for o in out[len(stated):]
                                   if any(within(o.value, st.value, _half_unit(st.primary)) for st in public_stated)]
        return out

    def margin_change(self, cik: int, cal: list[CalEntry], rp: ResolvedPeriod, comparison: str,
                      basis: Optional[str]) -> list[Obs]:
        base = (periods.shift(rp, cal, quarters=-1) if comparison in ("QoQ", "sequential")
                else periods.shift(rp, cal, years=-1))
        cur, prev = self.margin_levels(cik, rp, basis), self.margin_levels(cik, base, basis)
        factor = 100.0 if self.margin_unit == "basis points" else 1.0  # levels already use percent (×100)

        def stated(o: Obs) -> bool:
            return len(o.facts) == 1 and o.primary.unit == "%"

        def printed(o: Obs, levels: list[Obs]) -> tuple[Decimal, str, int, tuple[Fact, ...]]:
            # A public release can establish the print precision of the same period's 10-Q calculation.
            # Borrow only its precision, never another filing's endpoint value or future information.
            rates = [s for s in levels if stated(s) and self.public(s)
                     and s.primary.basis == o.primary.basis
                     and s.primary.period_start == o.primary.period_start
                     and within(o.value, s.value, _half_unit(s.primary))]
            own = [s for s in rates if s.primary.accession == o.primary.accession]
            rate = o if stated(o) else min(own or rates, key=lambda s: _half_unit(s.primary), default=None)
            digits = rate.primary.decimals if rate and rate.primary.decimals is not None else 1
            rounded = Decimal(str(o.value)).quantize(
                Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            return rounded, f"{rounded:f}%", digits, rate.facts if rate else ()

        pairs = [(c, p) for c in cur for p in prev
                 if self._same_margin_filing(c.facts + p.facts) and c.primary.basis == p.primary.basis
                 and {f.basis for f in c.facts if f.metric == "revenue"}
                     == {f.basis for f in p.facts if f.metric == "revenue"}]
        exact_pairs = {(c.primary.accession, c.primary.basis) for c, p in pairs
                       if not stated(c) and not stated(p)}
        out = []
        for c, p in pairs:
            # Prefer the exact pair for corrections, but preserve a complete printed pair if either dollar
            # endpoint is missing. A lone exact endpoint must never suppress two usable stated rates.
            if stated(c) and stated(p) and (c.primary.accession, c.primary.basis) in exact_pairs:
                continue
            cv, ct, cd, cf = printed(c, cur)
            pv, pt, pd, pf = printed(p, prev)
            precision = "one decimal" if cd == pd == 1 else f"{pd} and {cd} decimal places, respectively"
            out.append(Obs((c.value - p.value) * factor, c.facts + p.facts, (c.err + p.err) * factor,
                           f"from {p.value:.6g}% to {c.value:.6g}%; current margin minus comparison margin",
                           float((cv - pv) * Decimal(str(factor))),
                           f"{pt} and {ct}, rounded to {precision}", cf + pf))
        if not out and base is not None:
            self.unpaired[(rp, comparison)] = base
        return out

    def margin(self, cik: int, rp: ResolvedPeriod, basis: Optional[str]) -> list[Obs]:
        return self._gross_margin_levels(cik, rp, basis, rounding=False)

    def _stated_margin_precision(self, pub: list[Obs]) -> Optional[Verdict]:
        """A coarse stated rate cannot establish or refute a finer overlapping claim."""
        if self.metric != "gross_margin" or self.bounded:
            return None
        if self.margin_unit:
            # A change matching the exact figures or the subtraction of the printed rates checks out. Where only
            # coarse stated rates exist (55% and 54%), any change within their rounding (0 to 200 bps) may be true:
            # one that matches neither calculation is unknown, not wrong. An exact change refutes it instead.
            if self.matches(pub):
                return None
            coarse = [o for o in pub if any(f.metric == "gross_margin" for f in o.facts)
                      and o.err > self.tol * (1.0 + 1e-9)]
            if not coarse or any(o not in coarse for o in pub):
                return None
            overlaps = [o for o in coarse if within(self.claimed, o.value, self.tol + o.err)]
            if not overlaps:
                return None
            best = _best_obs(overlaps)
            return self.verdict("unverifiable", "ambiguous",
                                "The filing's rounded gross-margin rates are not precise enough to establish "
                                f"the draft's {self.c.value_text} change. NoCap needs more precise stated rates "
                                "or a stated change to check this figure.", evidence=best.facts)
        coarse = [o for o in pub if len(o.facts) == 1 and o.primary.metric == "gross_margin"
                  and _half_unit(o.primary) > self.tol * (1.0 + 1e-9)]
        if not coarse or self.matches(o for o in pub if o not in coarse):
            return None
        overlaps = [o for o in coarse if within(self.claimed, o.value,
                                               self.tol + _half_unit(o.primary))]
        if not overlaps:
            return None
        best = _best_obs(overlaps)
        return self.verdict("unverifiable", "ambiguous",
                            f"The filing states a rounded gross margin of {self.fmt_observation(best)}; its precision "
                            f"cannot establish the draft's {self.c.value_text}. NoCap needs a more precise "
                            "stated rate to check this figure.", evidence=best.facts)

    def observe(self, rp: Optional[ResolvedPeriod], *, cik: Optional[int] = None,
                cal: Optional[list[CalEntry]] = None, basis: Any = "same", comparison: Optional[str] = None) -> list[Obs]:
        cik = self.cik if cik is None else cik
        cal = self.cal if cal is None else cal
        basis = self.basis if basis == "same" else basis
        if rp is None or rp.period_end is None:
            return []
        if self.margin_unit:
            observations = self.margin_change(cik, cal, rp, comparison or self.comparison, basis)
        elif self.growth_base:
            observations = self.growth(cik, cal, rp, comparison or self.comparison, basis, self.segment)
        elif self.metric == "gross_margin":
            observations = self.margin(cik, rp, basis)
        elif self.derived == "operating_margin":
            observations = self.operating_margin(cik, rp, basis)
        elif self.derived == "net_margin":
            observations = self.income_margin(cik, rp, basis, "net_income")
        else:
            observations = self.level(cik, self.metric, rp, basis, self.segment)
        finite = [o for o in observations if math.isfinite(o.value) and math.isfinite(o.err)]
        self.invalid_numeric_evidence |= len(finite) != len(observations)
        return finite

    def matches(self, obs: Iterable[Obs], claimed: Optional[float] = None, tol: Optional[float] = None) -> list[Obs]:
        claimed = self.claimed if claimed is None else claimed
        tol = self.tol if tol is None else tol
        if self.margin_unit:
            # Two discrete calculations, never a wider tolerance band between or around them.
            return [o for o in obs if within(claimed, o.value, tol) or (
                not self.bounded and o.printed_change is not None and within(claimed, o.printed_change, tol))]
        return [o for o in obs if within(claimed, o.value, tol + o.err)]

    def alternative_matches(self, rp: ResolvedPeriod) -> tuple[Optional[str], list[Obs]]:
        """A match under another reading of the same wording ('EPS' read as basic rather than diluted). A change is
        compared with the other metric's change, never with its level."""
        for metric in self.alternatives:
            if self.growth_base:
                base, self.growth_base = self.growth_base, metric
                try:
                    obs = self.observe(rp)
                finally:
                    self.growth_base = base
            else:
                obs = self.level(self.cik, metric, rp, self.basis, self.segment)
            hits = [o for o in self.accepts(obs) if self.public(o)]
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
            if hi < x - band or lo >= x:
                return False
            return True if (lo >= x - band and hi < x) else None

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
        actual = self._non_gaap_mismatch_actual(pub)
        if any(t is None for _, t in tests):
            return self.verdict("unverifiable", "ambiguous",
                                f"{self.attribution(actual)}{self.statement(actual, rp, filed_precision=True)}, "
                                f"which is too close to {self.c.value_text} to tell, given the rounding of the figures "
                                f"it is computed from.", evidence=actual.facts)
        if not self.basis_stated:
            other = "GAAP" if self.basis == "non-GAAP" else "non-GAAP"
            alt = [o for o in self.observe(rp, basis=other) if self.public(o) and self.bound_test(o) is True]
            if alt:
                best = _best_obs(alt)
                return self.verdict("unverifiable", "ambiguous",
                                    f"{self.co_s} {other} {self.mlabel(False)} for {self.plabel(rp)} "
                                    f"{_was(self.mlabel(False))} "
                                    f"{self.fmt_observation(best)}, which is {words} {self.c.value_text}, but the draft doesn't "
                                    f"say {other}, and the {self.basis or 'GAAP'} figure is {self.fmt_observation(actual)}.",
                                    evidence=[*best.facts, *actual.facts])
        bank = self._bank_headline(rp, actual)
        if bank is not None:
            return bank
        if measure := self._non_gaap_measure_mismatch(actual):
            return measure
        return self.wrong("value_mismatch", actual,
                          f"{self.attribution(actual)}{self.statement(actual, rp, filed_precision=True)}, "
                          f"which is not {words} {self.c.value_text}.")

    # -- the rules -------------------------------------------------------------
    def run(self) -> Verdict:
        verdict = self._run()
        if sibling := self._sibling_basis(verdict):
            return sibling
        if (verdict.status == "wrong" and verdict.reason != "look_ahead" and self.c.period_type == "quarter"
                and self.rp is not None and self.rp.fiscal_period == "FY"):
            # a quarterly figure whose period words read as a full year ("in 2025"): never "✗" against the year
            return self.verdict("unverifiable", "ambiguous",
                                f"The draft gives {self.c.value_text} as a quarterly figure, but its period reads as "
                                f"{self.plabel(self.rp)}, so NoCap can't tell which period to check it against.",
                                evidence=verdict.evidence)
        if (verdict.status == "wrong" and verdict.reason != "look_ahead" and self.growth_base is None
                and not any(_comparison_period_after(self.c)) and (over := _over_a_year(self.c))):
            # "compared to $25.2 billion over a year ago": a year-ago figure, or one from more than a year earlier
            return self.verdict("unverifiable", "ambiguous",
                                f"“{over}” after {self.c.value_text} can mean a year earlier or more than a year "
                                f"earlier, so NoCap can't tell which period the figure is for.",
                                evidence=verdict.evidence)
        return verdict

    def _run(self) -> Verdict:
        c = self.c
        if self.comparison_ambiguous:
            return self.verdict("unverifiable", "ambiguous",
                                "The source does not bind this percentage to one clear year-over-year or "
                                "quarter-over-quarter comparison.")
        if _ambiguous_growth_endpoint(c):
            return self.verdict("unverifiable", "ambiguous",
                                "'Up to' followed by a starting amount can mean a growth endpoint or an upper "
                                "bound, so this figure needs clearer wording.")
        if not math.isfinite(self.claimed) or not math.isfinite(self.tol):
            return self.verdict("unverifiable", "ambiguous",
                                "This figure exceeds the supported numeric range after applying its units or precision.")
        if not self._unit_fits():
            return self.verdict("unverifiable", "no_evidence",
                                f"NoCap checks {self.mlabel(False)} in {_unit_words(self.metric)}, and this figure "
                                f"is written in {_unit_words_of(c.unit)}.")
        if self.metric == "segment_revenue" and not self.segment:
            return self.verdict("unverifiable", "ambiguous", "The draft doesn't say which segment this revenue is for.")
        level_claim = self.growth_base is None
        rp = periods.resolve_in_calendar(self.period_words, self.cal, context=self.context, period_type=c.period_type,
                                         comparison=c.comparison if level_claim else "YoY", as_of=self.as_of)
        self.rp = rp
        if rp is None:
            said = c.period_text.strip() or "no period"
            return self.verdict("unverifiable", "ambiguous",
                                periods.unresolved_reason(c.period_text, context=self.context, period_type=c.period_type) or
                                f"Couldn't tell which {self.company} fiscal period \"{said}\" refers to.")
        if self.growth_base and (stated := self._stated_base(rp)) is not None:
            return stated
        if self.growth_base and (self.comparison == "none" or rp.fiscal_period == "FY"):
            return self._growth_unstated(rp)
        obs_all = self.observe(rp)
        # A named income line must not borrow the served line's missing/future-value correction either.
        if _named_income_line(c) and self.metric == "net_income":
            named_income = self._net_income_alternative(rp, [o for o in obs_all if self.public(o)])
            if named_income is not None:
                return named_income
        if not obs_all:
            return self._no_evidence(rp)
        pub = [o for o in obs_all if self.public(o)]
        if not pub:
            return self._look_ahead(rp, obs_all)
        if self.metric in _MARGIN_METRICS and not self.growth_base:
            comparison_base = self._comparison_base(rp, pub)
            if comparison_base is not None:
                return comparison_base
        if alternative_income := self._net_income_alternative(rp, pub):
            return alternative_income
        if self.bounded:
            return self._bounded(rp, pub)
        if precision := self._stated_margin_precision(pub):
            return precision
        hits = self.matches(pub)
        if hits:
            return self._verified(rp, hits)
        for check in (self._approximate, self._comparison_base, self._implied_base, self._alternative_reading):
            v = check(rp, pub)
            if v is not None:
                return v
        if split_future := self._split_look_ahead(rp, pub, obs_all):
            return split_future
        if adjusted := self._split_adjusted(rp, pub):
            return adjusted
        actual = self._non_gaap_mismatch_actual(pub)
        if direction := self._wrong_direction(rp, pub):
            return direction
        if measure := self._non_gaap_measure_mismatch(actual):
            return measure
        for diagnose in (self._diag_reported_base, self._diag_not_revenue_row, self._diag_split, self._diag_split_basis,
                         self._diag_bank,
                         self._diag_period, self._diag_scale,
                         self._diag_basis, self._diag_entity):
            v = diagnose(rp, actual)
            if v is not None:
                return v
        return self.wrong("value_mismatch", actual,
                          f"{self.attribution(actual)}{self.statement(actual, rp, filed_precision=True)}, not {c.value_text}.")

    def _line_names_a_period(self) -> bool:
        """Whether the draft gives this figure a period: its own sentence (draft_line) has a period phrase or period
        words, or the draft names a period elsewhere, as a story does once in its opening ("Meta Platforms posted
        third-quarter 2026 revenue ..." covers "Total costs and expenses jumped to $42.0 billion"). A draft that names
        no period at all, such as the lone line "Adjusted gross margin of approximately 75.0%", leaves the period only
        to the context. Without the draft a span can't tell, so it counts as naming one, as before."""
        if self.line is None:
            return True
        return bool(self.draft_period or periods.find_period_phrase(self.line.text)
                    or _PERIOD_WORDS_RE.search(self.line.text))

    def _sibling_basis(self, verdict: Verdict) -> Optional[Verdict]:
        """A figure its own words put on one basis ("reported", "GAAP"; or "adjusted", "comparable", "non-GAAP") whose
        draft line states the same measure on the other basis, and whose evidence fits that other figure: Constellation
        Brands' "Tax rate: reported ~12%; comparable ~20%", where the release's rate NoCap holds for the quarter is
        20.2%. The line's bases, or its period, may then differ from the filing's, so the figure is "?", never a value
        "✗". A figure whose number is the other basis's own (a swapped basis) is diagnosed as before."""
        if (self.line is None or verdict.status != "wrong" or verdict.reason != "value_mismatch"
                or verdict.expected_value is None or self.growth_base or self.margin_unit):
            return None
        own = _own_wording(self.c)
        gaap = bool(_GAAP_SIDE_RE.search(_NON_GAAP_SIDE_RE.sub(" ", own)))
        if gaap == bool(_NON_GAAP_SIDE_RE.search(own)):
            return None  # its own words name no basis, or both
        other = _NON_GAAP_SIDE_RE if gaap else _GAAP_SIDE_RE
        text, start, end = self.line
        factor = SCALE_FACTOR.get(self.c.scale, 1.0) if self.c.unit in ("USD", "shares") else 1.0
        expected = verdict.expected_value * factor
        previous = 0
        for m in _FIGURE_RE.finditer(text):
            if not re.search(r"\d", m.group(0)):
                continue
            lead, previous = text[previous:m.start()], m.end()
            if m.start() < end and m.end() > start:
                continue  # the claim's own number
            figure = _sibling_figure(m.group(0), self.c)
            words = other.search(lead if other is _NON_GAAP_SIDE_RE else _NON_GAAP_SIDE_RE.sub(" ", lead))
            if figure is None or words is None:
                continue
            value, half = figure
            hedged = hedge_of(self.c.model_copy(update={"span_text": text[:m.end()], "value_text": m.group(0).strip()}))
            band = max(2 * half, 0.01 * abs(value)) if hedged and hedged.kind == "approx" else half
            if within(expected, value, band):
                filed = re.sub(rf",\s*not {re.escape(self.c.value_text)}\.$", ".", verdict.explanation)
                return self.verdict(
                    "unverifiable", "ambiguous",
                    f"{filed} The draft's line also gives this measure as “{words.group(0)}” {m.group(0).strip()}, "
                    f"which that figure fits, so the line's bases or period may differ from the filing's; NoCap "
                    f"doesn't call {self.c.value_text} wrong.", evidence=verdict.evidence)
        return None

    def _unit_fits(self) -> bool:
        u = self.c.unit
        if self.margin_unit:
            return True  # read from value_text, independently of the proposed unit and scale
        if self.absolute:  # a change is written in the base metric's own unit
            return u in ("USD/share", "USD") if self.growth_base in PER_SHARE_METRICS else u == "USD"
        if self.growth_base or self.derived in ("operating_margin", "net_margin"):
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
                            f"{self.attribution(best, matched=True)}{self._matched_statement(best, rp)}{note}.",
                            evidence=self._matched_facts(best) if self.margin_unit or self.derived == "net_margin" else
                            [f for o in hits for f in o.facts])

    def _matched_facts(self, o: Obs) -> tuple[Fact, ...]:
        if self.margin_unit and not self.bounded and not within(self.claimed, o.value, self.tol):
            return o.facts + o.printed_facts
        return o.facts

    def _matched_statement(self, o: Obs, rp: ResolvedPeriod, comparison: Optional[str] = None) -> str:
        if (self.margin_unit and not self.bounded and o.printed_change is not None
                and not within(self.claimed, o.value, self.tol)):
            verb = "expanded" if o.printed_change >= 0 else "contracted"
            return (f"{self.subject()} was {o.printed_calculation}: {verb} "
                    f"{abs(o.printed_change):g} {self.margin_unit} {_cmp_words(comparison or self.comparison)} "
                    f"in {self.plabel(rp)} ({self.fmt(o.value)} from the filed figures)")
        return self.statement(o, rp, comparison)

    def _wrong_direction(self, rp: ResolvedPeriod, pub: list[Obs],
                         comparison: Optional[str] = None) -> Optional[Verdict]:
        """A matching magnitude with the opposite sign is a direction error, not a numeric correction."""
        if not self.growth_base or self.bounded or not self.claimed:
            return None
        hits = [o for o in self.matches(pub, claimed=-self.claimed) if o.value * self.claimed < 0]
        if not hits:
            return None
        actual = _current_obs(hits)
        said = ("contracted" if self.claimed < 0 else "expanded") if self.margin_unit else (
            "fell" if self.claimed < 0 else "grew")
        statement = self.statement(actual, rp, comparison)
        if self.margin_unit:
            statement = statement.replace(" grew ", " expanded ", 1).replace(" fell ", " contracted ", 1)
        return self.wrong("value_mismatch", actual,
                          f"{self.attribution(actual)}{statement}; the draft says it {said}.")

    def _coverage_end_before(self, rp: ResolvedPeriod) -> Optional[ResolvedPeriod]:
        """A later fiscal label exceeds coverage only when no held fact extends past the labelled calendar."""
        def order(p: CalEntry | ResolvedPeriod) -> tuple[int, int]:
            return p.fiscal_year, 4 if p.fiscal_period == "FY" else int(p.fiscal_period[1])

        latest = max(self.cal, key=order, default=None)  # Q4 and FY share an endpoint; prefer Q4 on a tie
        if latest is None or order(rp) <= order(latest):
            return None
        held = False
        for metric in get_args(Metric):
            for f in self.store.facts(self.cik, metric, segment="*") or []:
                if f.period_end > latest.end:
                    return None  # a newer fact may have lost its fiscal label; don't claim the evidence ends earlier
                held |= f.period_end == latest.end
        return ResolvedPeriod(latest.fiscal_year, latest.fiscal_period, None) if held else None

    def _no_evidence(self, rp: ResolvedPeriod) -> Verdict:
        if self.sales_only:
            return self.verdict("unverifiable", "no_evidence",
                                f"The revenue {self.co_s} filings report is total revenue, which includes more than its "
                                f"net sales (such as membership fees or other revenue), and NoCap holds no net sales "
                                f"figure for {self.plabel(rp)}, so it can't check {self.c.value_text} as net sales.")
        if self.invalid_numeric_evidence:
            return self.verdict("unverifiable", "no_evidence",
                                "The available filing figures could not be compared as finite numbers.")
        unpaired = self.unpaired.get((rp, self.comparison)) or next(
            (base for (period, _), base in self.unpaired.items() if period == rp), None)
        if rp in self.gross_margin_base_missing or unpaired in self.gross_margin_base_missing:
            return self.verdict("unverifiable", "ambiguous",
                                "The held revenue figures do not establish the gross-margin sales base. "
                                "NoCap needs the filing's stated gross-margin rate or unambiguous net sales "
                                "from the same filing; total revenue can include income outside that base.")
        if rp.period_end is None:  # a future period, or a past one whose fiscal label ingest dropped as impossible
            latest = self._coverage_end_before(rp)
            if latest is not None:
                text = (f"NoCap's evidence for {self.company} ends with {self.plabel(latest)}, "
                        f"so it can't check {self.plabel(rp)} yet")
            else:
                text = f"NoCap couldn't match {self.plabel(rp)} to a fiscal period labelled in {possessive(self.company)} filings"
        elif self.growth_base and unpaired is not None:  # both figures are filed, but never in one filing (growth())
            text = (f"No single filing NoCap holds reports {self.co_s} {self.subject()} for both {self.plabel(rp)} and "
                    f"{self.plabel(unpaired)}, and separate filings may be on different bases, so NoCap doesn't "
                    f"compute the change")
            other = ("YoY" if self.comparison in ("QoQ", "sequential") else "QoQ" if self.comparison == "YoY"
                     else None)
            hits = [o for o in self.matches(self.observe(rp, comparison=other)) if self.public(o)] \
                if other and not self.bounded else []
            if hits:
                return self.verdict("unverifiable", "no_evidence",
                                    f"{text}; {self.c.value_text} matches its {_cmp_words(other)} {self.mlabel()}.",
                                    evidence=_best_obs(hits).facts)
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

    def _net_income_alternative(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """Alternative tags stay out of observe(); only explicit source wording can select one as the target."""
        if self.metric != "net_income" or self.growth_base or self.derived or self.segment or self.basis == "non-GAAP":
            return None
        named = _named_income_line(self.c)
        # Each candidate is paired with its own public, same-filing and exact-span served observation.
        candidates: list[tuple[str, Obs, Obs, bool]] = []
        for metric, (concept, _) in _INCOME_LINES.items():
            for alt in self.level(self.cik, metric, rp, "GAAP"):
                f = alt.primary
                if (not self.public(alt) or f.source != "xbrl" or f.unit != "USD" or f.concept != concept
                        or f.form not in ("10-Q", "10-Q/A", "10-K", "10-K/A")):
                    continue
                for own in pub:
                    g = own.primary
                    if (len(own.facts) == 1 and g.basis == "GAAP" and g.unit == "USD"
                            and g.source in ("xbrl", "derived")
                            and (f.accession, f.period_start, f.period_end) ==
                                (g.accession, g.period_start, g.period_end)):
                        candidates.append((metric, alt, own, False))
            # Some stores already serve this exact tag as net_income. Explicit wording may use it directly.
            direct = [o for o in pub if len(o.facts) == 1 and o.primary.concept == concept
                      and o.primary.source == "xbrl" and o.primary.unit == "USD" and o.primary.basis == "GAAP"]
            for alt in direct:
                peers = [o for o in pub if o.primary.concept == "us-gaap:NetIncomeLoss"
                         and o.primary.basis == "GAAP" and o.primary.unit == "USD"
                         and (o.primary.accession, o.primary.period_start, o.primary.period_end) ==
                             (alt.primary.accession, alt.primary.period_start, alt.primary.period_end)]
                candidates.extend((metric, alt, own, False) for own in peers or [alt])
        # Old snapshots retain ProfitLoss as an operand. Accept only the exact, internally consistent
        # direct formula, never FY-minus-9M or a formula joining different accessions.
        nci_concepts = (r"NetIncomeLossAttributableToNoncontrollingInterest|"
                        r"NetIncomeLossAttributableToRedeemableNoncontrollingInterest \+ "
                        r"NetIncomeLossAttributableToNonredeemableNoncontrollingInterest")
        number = r"-?\d[\d,]*(?:\.\d+)?"
        for own in pub:
            f = own.primary
            if (len(own.facts) != 1 or f.source != "derived" or f.concept != "us-gaap:NetIncomeLoss"
                    or f.unit != "USD" or f.basis != "GAAP" or set(f.derived_from) != {f.accession}):
                continue
            formula = re.fullmatch(
                rf"NetIncomeLoss = ProfitLoss ({number}) - (?:{nci_concepts}) ({number}) "
                rf"\({re.escape(f.form)} {re.escape(f.accession)}\)", f.locator or "")
            if formula is None:
                continue
            total, nci = (float(value.replace(",", "")) for value in formula.groups())
            if (math.isfinite(total) and math.isfinite(nci)
                    and math.isclose(total - nci, own.value, rel_tol=0.0, abs_tol=0.5)):
                candidates.append(("net_income_including_nci", Obs(total, own.facts), own, True))
        if named:
            label = _INCOME_LINES[named][1]
            selected = [alt for metric, alt, _, _ in candidates if metric == named]
            same_line = False
            if (not selected and named == "net_income_including_nci" and rp.period_end is not None
                    and not list(self.store.facts(self.cik, named, segment="*", period_end=rp.period_end) or [])):
                # The snapshot stores consolidated net income wherever it differs from a directly tagged NetIncomeLoss
                # in the same filing and span (receipts.net_income_common), so where it stores none at all for the
                # period the two are one figure: NVIDIA reports no noncontrolling interests. A stored one that is
                # unusable here (another filing or span, not yet public) never lets net income stand in, and a
                # derived figure (a Q4) proves nothing.
                selected = [o for o in pub if len(o.facts) == 1 and o.primary.source == "xbrl"
                            and o.primary.concept == "us-gaap:NetIncomeLoss" and o.primary.basis == "GAAP"
                            and o.primary.unit == "USD" and o.primary.form in ("10-Q", "10-Q/A", "10-K", "10-K/A")]
                same_line = bool(selected)
            if not selected:
                extra = " This line can differ after preferred dividends." if named == "net_income_to_common" else ""
                return self.verdict("unverifiable", "no_evidence",
                                    f"No public same-filing evidence for {self.co_s} {label} for {self.plabel(rp)}."
                                    f"{extra} NoCap cannot check this amount against a different net-income line.")
            hits = self.accepts(selected)
            actual = _best_obs(hits) if hits else _current_obs(selected)
            statement = (f"{self.co_s} {form_phrase(actual.primary)} reports {label} of "
                         f"{self.fmt(actual.value)} for {self.plabel(rp)}")
            if same_line:
                statement = (f"{self.co_s} {form_phrase(actual.primary)} reports net income of "
                             f"{self.fmt(actual.value)} for {self.plabel(rp)} and no separate {label}, so that is "
                             "its consolidated figure")
            if hits:
                note = f", which is {self.hedge.words} {self.c.value_text}" if self.bounded else ""
                return self.verdict("verified", "match", statement + note + ".", evidence=actual.facts)
            if self.hedge and self.hedge.kind == "approx":
                near = self.matches(selected, tol=max(2 * self.tol, abs(self.claimed) * 0.01))
                if near:
                    best = _best_obs(near)
                    return self.verdict("approximate", "approximate",
                                        f"{self.c.value_text} is close to {self.co_s} {label} of "
                                        f"{self.fmt(best.value)} for {self.plabel(rp)} "
                                        f"({form_phrase(best.primary)}), within the approximation band.",
                                        evidence=best.facts)
            if self.bounded and any(self.bound_test(o) is None for o in selected):
                return self.verdict("unverifiable", "ambiguous", statement + "; its rounding is too close to "
                                    f"the stated bound {self.c.value_text} to decide.", evidence=actual.facts)
            mismatch = (f", which is not {self.hedge.words} {self.c.value_text}" if self.bounded
                        else f", not {self.c.value_text}")
            return self.wrong("value_mismatch", actual, statement + mismatch + ".")
        for metric, alt, own, from_formula in candidates:
            alt_matches = self.accepts([alt])
            if self.hedge and self.hedge.kind == "approx":
                alt_matches = self.matches([alt], tol=max(2 * self.tol, abs(self.claimed) * 0.01))
            if not alt_matches or self.accepts([own]):
                continue
            label = _INCOME_LINES[metric][1]
            if from_formula:
                comparison = (f"; subtracting those interests gives {self.fmt(own.value)}, "
                              "the net-income figure NoCap serves. ")
            else:
                comparison = f", alongside {self.fmt(own.value)}, the net-income figure NoCap serves. "
            kind = "available-to-common" if metric == "net_income_to_common" else "consolidated"
            match = (f"The {kind} figure meets the stated bound" if self.bounded
                     else f"{self.c.value_text} is within the approximation band of the {kind} figure"
                     if self.hedge and self.hedge.kind == "approx" and not self.matches([alt])
                     else f"{self.c.value_text} matches the {kind} figure")
            return self.verdict("unverifiable", "ambiguous",
                                f"{self.co_s} {form_phrase(alt.primary)} reports {label} "
                                f"of {self.fmt(alt.value)} for {self.plabel(rp)}{comparison}"
                                f"{match}, so the draft's net-income meaning is ambiguous.",
                                evidence=[*own.facts, *alt.facts])
        return None

    def _alternative_reading(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """Unqualified 'EPS' that matches basic rather than diluted EPS: right under that reading, so verified."""
        metric, hits = self.alternative_matches(rp)
        if not hits:
            return None
        best = _best_obs(hits)
        label = METRIC_LABELS.get(metric or "", "figure")
        stated = (f"{label} {'grew' if best.value >= 0 else 'fell'} {self.fmt(abs(best.value))} "
                  f"{_cmp_words(self.comparison)} in {self.plabel(rp)}" if self.growth_base else
                  f"{label} was {self.fmt(best.value)} for {self.plabel(rp)}")
        return self.verdict("verified", "match",
                            f"Matches {self.co_s} {form_phrase(best.primary)}: {stated} (the draft doesn't say basic or "
                            f"diluted).", evidence=[f for o in hits for f in o.facts])

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
                    f"out by the as-of time {fmt_ts(self.as_of)}, but NoCap has no press-release figure for its "
                    f"{self.mlabel()}, and its {form_phrase(binding)} came later, at {fmt_ts(binding.accepted_at)}.")
        elif has_release_fact or release_at is not None:
            text = (f"{what} wasn't public until its {form_phrase(binding)} was accepted at "
                    f"{fmt_ts(binding.accepted_at)}, after the as-of time {fmt_ts(self.as_of)}")
        else:
            return self.verdict(
                "unverifiable", "no_evidence",
                f"The first filing with {what} that NoCap holds is its {form_phrase(binding)}, accepted at "
                f"{fmt_ts(binding.accepted_at)}, after the as-of time {fmt_ts(self.as_of)}, and the earnings release "
                f"that may have come earlier can't be dated.")
        if not value_ok:
            text += f", and the filed figure is {self.fmt_observation(filed)}"
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
        if precision := self._stated_margin_precision([o for _, obs in public for o in obs]):
            return precision
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
                                    f"{self.attribution(best, matched=True)}{self._matched_statement(best, rp, cmp)}.",
                                    evidence=self._matched_facts(best) if self.margin_unit else
                                    [f for o in hits for f in o.facts])
        parts = [f"{self.fmt(_current_obs(obs).value)} {_cmp_words(cmp)}" for cmp, obs in public if obs]
        # A dollar change against an unnamed base is never "✗": it may be measured against something else (guidance,
        # another period), and only the year-over-year and sequential changes are on file.
        if all(obs for _, obs in public) and not self.absolute:
            actual = _current_obs(public[0][1])
            if len(public) == 1 and (direction := self._wrong_direction(rp, public[0][1], public[0][0])):
                return direction
            tail = (f"neither of which is {self.hedge.words} {self.c.value_text}" if self.bounded and len(parts) > 1
                    else f"which is not {self.hedge.words} {self.c.value_text}" if self.bounded
                    else f"not {self.c.value_text}")
            if (not self.bounded and all(_current_obs(obs).value * self.claimed < 0 for _, obs in public)
                    and any(self.matches(obs, claimed=-self.claimed) for _, obs in public)):
                said = ("contracted" if self.claimed < 0 else "expanded") if self.margin_unit else (
                    "fell" if self.claimed < 0 else "grew")
                tail = f"the draft says it {said}"
            return self.wrong("value_mismatch", actual,
                              f"{self.co_s} {self.mlabel()} for {self.plabel(rp)} {_was(self.mlabel())} "
                              f"{' and '.join(parts)}, {tail}.",
                              extra=[f for _, obs in public for o in ([_current_obs(obs)] if self.margin_unit else obs)
                                     for f in o.facts])
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
                            f"Close enough for an approximation: {self.attribution(best)}{self.statement(best, rp)}.",
                            evidence=best.facts)

    def _split_look_ahead(self, rp: ResolvedPeriod, pub: list[Obs], obs_all: list[Obs]) -> Optional[Verdict]:
        """An actual future split-adjusted version of a public per-share figure: not public yet, not a bad value.

        Full-history splits are used only to explain a matching future receipt. The ordinary split and matching
        paths still see only the share basis public at as_of. A computed adjustment with no filed match, or a
        genuine restatement unrelated to a split, must not take this path.
        """
        if self.as_of is None or self.metric not in PER_SHARE_METRICS or self.growth_base:
            return None
        future = self.matches(o for o in obs_all if not self.public(o))
        if not future:
            return None
        facts = [f for m in sorted(PER_SHARE_METRICS) for f in (self.store.facts(self.cik, m) or [])]
        splits = company_splits(facts, split_events(self.ticker))
        # Acceptance precedes source preference here: a release can publish the adjusted comparative before a 10-Q.
        for new in sorted(future, key=lambda o: (o.public_at, _source_rank(o.primary))):
            for old in sorted(pub, key=lambda o: (_source_rank(o.primary), -o.public_at.timestamp())):
                if (old.primary.period_start, old.primary.period_end) != (new.primary.period_start,
                                                                         new.primary.period_end):
                    continue
                for split in splits:
                    if (split.old_basis_until is None or old.public_at > split.old_basis_until
                            or new.public_at < split.new_basis_from
                            or not _restated_by(old.primary, new.primary, split.ratio, split.forward)):
                        continue
                    kind = (f"{split.ratio}-for-1 stock split" if split.forward
                            else f"1-for-{split.ratio} reverse stock split")
                    return self.verdict(
                        "wrong", "look_ahead",
                        f"{self.c.value_text} matches {self.co_s} split-adjusted {self.mlabel()} for {self.plabel(rp)} "
                        f"after the {kind}. In the filings NoCap holds, this figure first became public in its "
                        f"{form_phrase(new.primary)} ({new.primary.accession}), accepted at {fmt_ts(new.public_at)}, "
                        f"after the as-of time {fmt_ts(self.as_of)}. At the as-of time, its "
                        f"{form_phrase(old.primary)} reported {self.fmt(old.value)} for that period.",
                        evidence=[*old.facts, *new.facts])
        return None

    def _split_adjusted(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """A per-share figure on the share basis after a stock split that the company's later filings show, for a
        period no filing NoCap holds has restated yet: "?", never ✗ (S&P 500 QA, T4: APH's FY2025 diluted EPS is $3.34
        in its 10-K, $1.67 after the 2-for-1 split its Q3 2026 10-Q shows). NoCap checks figures as filed, so it doesn't
        confirm the adjusted one. A figure filed on the new basis needs nothing: it matches as filed."""
        if self.metric not in PER_SHARE_METRICS or self.growth_base:
            return None
        for split in self._company_splits():
            for o in sorted(pub, key=lambda o: (_source_rank(o.primary), o.public_at)):
                if split.old_basis_until is None or o.public_at > split.old_basis_until:
                    continue  # possibly filed on the new basis already
                n = split.ratio
                adjusted, rounding = ((o.value / n, _half_unit(o.primary) / n) if split.forward
                                      else (o.value * n, _half_unit(o.primary) * n))
                if not within(self.claimed, adjusted, self.tol + rounding):
                    continue
                kind = f"{n}-for-1 stock split" if split.forward else f"1-for-{n} reverse stock split"
                return self.verdict(
                    "unverifiable", "no_evidence",
                    f"{self.c.value_text} matches {self.co_s} {self.mlabel()} for {self.plabel(rp)}, "
                    f"{self.fmt(o.value)} in its {form_phrase(o.primary)}, adjusted for the {kind} that its later "
                    f"filings show; no filing NoCap holds reports that period on the post-split share basis yet, so it "
                    f"can't confirm the adjusted figure.", evidence=o.facts)  # the period's own receipt only
        return None

    def _diag_split(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """The per-share figure as first reported, before a stock split restated it (Netflix's $5.87, now $0.59)."""
        for old, new, n in self.superseded:
            if old.primary.period_end != rp.period_end or not self.matches([old]):
                continue
            split = (f"a {n}-for-1 stock split" if abs(old.value) > abs(new.value)
                     else f"a 1-for-{n} reverse stock split")
            basis_time = f"visible at {fmt_ts(self.as_of)}" if self.as_of else "in the latest filing"
            return self.wrong("value_mismatch", actual,
                              f"{self.c.value_text} is {self.co_s} {self.mlabel()} for {self.plabel(rp)} as reported "
                              f"before {split}; on the share basis {basis_time} it is {self.fmt(actual.value)}.",
                              extra=old.facts)
        return None

    def _diag_split_basis(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """A per-share figure near the restated one put back on the share basis before a stock split, for a period
        NoCap holds only as restated: Carvana's Q3 2025 diluted EPS is $0.21 in its 2026 release, after the 5-for-1
        split, and $1.03 is close to $0.21 x 5 = $1.05. NoCap never saw the pre-split figure, and $0.21 x 5 is not it,
        so this is "?" with the split named, never a claim about the pre-split figure, and the receipt is the period's
        own (code review H3). When the period's pre-split version is held, _diag_split speaks for a claim that matches
        it, and any other figure is plainly wrong. The period must have ended long enough before a filing known to be
        pre-split to have been reported on the old basis first (FIRST_REPORT_DAYS)."""
        if self.metric not in PER_SHARE_METRICS or self.growth_base or rp.period_end is None:
            return None
        if any(old.primary.period_end == rp.period_end and old.primary.metric == self.metric
               and old.primary.basis == actual.primary.basis for old, _new, _n in self.superseded):
            return None  # the pre-split figure is held: the claim either is it (_diag_split) or is plainly wrong
        for split in self._company_splits():
            if split.old_basis_until is None or actual.public_at < split.new_basis_from:
                continue  # the figure NoCap holds may be on the old basis
            if rp.period_end + timedelta(days=FIRST_REPORT_DAYS) > split.old_basis_until.date():
                continue  # the period may never have been reported on the old basis
            n = split.ratio
            before, rounding = ((actual.value * n, _half_unit(actual.primary) * n) if split.forward
                                else (actual.value / n, _half_unit(actual.primary) / n))
            if not within(self.claimed, before, self.tol + rounding):
                continue
            kind = f"{n}-for-1 stock split" if split.forward else f"1-for-{n} reverse stock split"
            op = "x" if split.forward else "/"
            return self.verdict(
                "unverifiable", "no_evidence",
                f"{self.c.value_text} is close to {self.fmt(actual.value)} {op} {n} = {self.fmt(before)}: {self.co_s} "
                f"{form_phrase(actual.primary)} reports its {self.mlabel()} for {self.plabel(rp)} as "
                f"{self.fmt(actual.value)} after its {kind}, and {self.fmt(before)} is how that figure would read on "
                f"the old share basis. No filing NoCap holds reports the period on the old basis, so it can't confirm "
                f"{self.c.value_text}.", evidence=actual.facts)
        return None

    def _comparison_base(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """'up from $46.7 billion a year ago': a level figure for the comparison period the sentence names."""
        if self.c.comparison != "none" or self.growth_base:
            return None
        if self.metric in _MARGIN_METRICS and periods.parse(self.c.period_text).shift in ("year", "quarter"):
            return None  # period resolution already moved an explicitly labelled comparison period
        year_ago, prior_quarter = _comparison_period_after(self.c)
        margin_base = False
        if self.metric in _MARGIN_METRICS:
            pos = _value_pos(self.c)
            if pos is not None:
                # The last figure before this one must be the same margin's
                # endpoint, joined directly by "from". Another sentence's
                # amount or a nonnumeric "from operations" is not a base.
                before = self.c.span_text[:pos]
                figures = list(_FIGURE_RE.finditer(_without_period_phrases(before)))
                if figures:
                    previous = figures[-1]
                    local = before[max(sentence_breaks(before[:previous.start()]), default=0):previous.start()]
                    margin_base = bool(
                        re.fullmatch(r"\s*from\s*", before[previous.end():], re.I)
                        and _level_metric_phrases(self.metric, local)
                        and re.search(r"\b(?:to|at)\s*$", local, re.I)
                        and re.search(r"(?:%|percent)\s*$", previous.group(), re.I))
        shifted = []
        if year_ago:
            shifted.append(periods.shift(rp, self.cal, years=-1))
        if prior_quarter:
            shifted.append(periods.shift(rp, self.cal, quarters=-1))
        if margin_base and not shifted:
            shifted.append(periods.shift(rp, self.cal, years=-1))
            if rp.fiscal_period != "FY":
                shifted.append(periods.shift(rp, self.cal, quarters=-1))
        matches = []
        for srp in shifted:
            hits = [o for o in self.accepts(self.observe(srp)) if self.public(o)]
            if hits and srp is not None:
                if not margin_base:
                    return self._verified(srp, hits, note=", the comparison period the sentence cites")
                matches.append((srp, hits))
        if margin_base:
            if len(matches) == 1:
                srp, hits = matches[0]
                return self._verified(srp, hits, note=", matching the sentence's comparison value")
            return self.verdict(
                "unverifiable", "ambiguous",
                f"{self.c.value_text} is a comparison-period margin, not the current period's margin; "
                "NoCap cannot bind it to one supported comparison period.",
                evidence=[f for _, hits in matches for o in hits for f in o.facts])
        return None

    def _stated_base(self, rp: ResolvedPeriod) -> Optional[Verdict]:
        """A change whose own words name its base period (_stated_base_period): that period sets the comparison,
        whatever extraction typed. "?" when it is neither the year-earlier period nor the prior quarter of rp, when it
        can't be resolved, or when the change's own words date it to a period other than rp (a period taken from the
        context). None otherwise, with self.comparison set."""
        words = _stated_base_period(self.c)
        if words is None:
            return None
        c = self.c
        brp = periods.resolve_in_calendar(words, self.cal, as_of=self.as_of)
        if brp is None:
            return self.verdict("unverifiable", "ambiguous", f"Couldn't tell which {self.company} fiscal period "
                                f"“{words}” refers to, the period the draft measures {c.value_text} against.")
        local = _value_comparison_context(c) or ("", "")
        for phrase in _period_phrases(" ".join(local).replace(words, " " * len(words), 1)):
            own = periods.resolve_in_calendar(phrase, self.cal, as_of=self.as_of)
            if own is not None and own[:2] != rp[:2]:
                return self.verdict("unverifiable", "ambiguous",
                                    f"The draft dates {c.value_text} to {self.plabel(own)}, while its period reads as "
                                    f"{self.plabel(rp)}, so NoCap doesn't check it.")
        yoy = periods.shift(rp, self.cal, years=-1)
        qoq = periods.shift(rp, self.cal, quarters=-1) if rp.fiscal_period != "FY" else None
        if yoy is not None and brp[:2] == yoy[:2]:
            self.comparison = "YoY"
        elif qoq is not None and brp[:2] == qoq[:2]:
            self.comparison = "QoQ"
        else:
            return self.verdict("unverifiable", "ambiguous",
                                f"The draft measures {c.value_text} against {self.plabel(brp)}, which is neither the "
                                f"year-earlier period nor the prior quarter of {self.plabel(rp)}, so NoCap doesn't "
                                f"check it.")
        return None

    def _implied_base(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """A comparison base with no period of its own (_unlabelled_base): checked in the base period the sentence
        implies, never in the current one. Without the sentence's own year-over-year or sequential words only a match
        there counts ("✓"); anything else is "?", so a default never makes a figure "✗"."""
        how = _unlabelled_base(self.c) if self.growth_base is None else None
        if how is None or not self._names_current_period(rp):
            return None
        c = self.c
        unclear = (f"{c.value_text} is the figure the sentence compares with, and the draft doesn't say which period "
                   f"it is from")
        if how == "ambiguous":
            return self.verdict("unverifiable", "ambiguous", f"{unclear}: its wording reads both year-over-year and "
                                f"sequential.")
        quarterly = rp.fiscal_period != "FY"
        srp = (periods.shift(rp, self.cal, quarters=-1) if quarterly else None) if how == "QoQ" else (
            periods.shift(rp, self.cal, years=-1))
        obs = [o for o in self.observe(srp) if self.public(o)] if srp is not None else []
        if hits := self.accepts(obs):
            return self._verified(srp, hits, note=", the comparison period the sentence implies")
        if how == "implied" or not obs:
            seen = (f"; {self.co_s} {self.mlabel()} for {self.plabel(srp)} was {self.fmt_observation(_current_obs(obs))}"
                    if obs else "")
            return self.verdict("unverifiable", "ambiguous", f"{unclear}{seen}.",
                                evidence=[f for o in obs for f in o.facts])
        actual = _current_obs(obs)
        return self.wrong("value_mismatch", actual,
                          f"{self.attribution(actual)}{self.statement(actual, srp, filed_precision=True)}, the "
                          f"comparison period the sentence implies, not {c.value_text}.")

    def _names_current_period(self, rp: ResolvedPeriod) -> bool:
        """Whether rp is the period of the story rather than one the draft gave this figure: no period of its own, the
        context line's period, or the period the sentence names before the comparison."""
        c = self.c
        if not (c.period_text or "").strip():
            return True
        if self.context:
            ctx = periods.resolve_in_calendar("", self.cal, context=self.context, period_type=c.period_type,
                                              comparison="none", as_of=self.as_of)
            if ctx is not None and ctx[:2] == rp[:2]:
                return True
        pos = _value_pos(c)
        said = " ".join(periods._norm(c.period_text).split())
        return bool(said) and pos is not None and said in " ".join(periods._norm(c.span_text[:pos]).split())

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
            other = "YoY" if self.comparison in ("QoQ", "sequential") else "QoQ"
            hits = [o for o in self.matches(self.observe(rp, comparison=other)) if self.public(o)]
            if hits:
                return self.wrong("period_mismatch", actual,
                                  f"{c.value_text} is {self.co_s} {_cmp_words(other)} {self.mlabel()}; "
                                  f"{_cmp_words(self.comparison)} it was {self.fmt(actual.value)} in {self.plabel(rp)}.",
                                  extra=_best_obs(hits).facts)
        if relative_tolerance(c) > PEER_MATCH_MAX_REL_TOL:
            return None  # "3%" matches some quarter's change by chance; only a precise figure names its period
        hint = self._other_period_match(rp)
        if hint is None:
            return None
        orp, o = hint
        if not (c.period_text or "").strip() or not self._line_names_a_period():
            # The draft gives the figure no period: the story's period is only the context's, so a figure that fits
            # another period is "?", never a period "✗" against the current one. Extraction may have copied the
            # context's period into period_text, so the draft decides (_line_names_a_period).
            return self.verdict("unverifiable", "ambiguous",
                                f"{c.value_text} is {self.co_s} {self.mlabel()} for {self.plabel(orp)}, and the draft "
                                f"names no period for it; for {self.plabel(rp)} it was "
                                f"{self.fmt_observation(actual)}.", evidence=[*o.facts, *actual.facts])
        return self.wrong("period_mismatch", actual,
                          f"{c.value_text} is {self.co_s} {self.mlabel()} for {self.plabel(orp)}; for "
                          f"{self.plabel(rp)} it was {self.fmt_observation(actual)}.", extra=o.facts)

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

    def _non_gaap_mismatch_actual(self, pub: list[Obs]) -> Obs:
        """For a mismatch, prefer a held row naming the draft's measure over another non-GAAP definition."""
        drafted = _claim_measure_qualifiers(self.c)
        if self.basis == "non-GAAP" and drafted and not self.growth_base and not self.derived:
            same = [o for o in pub if o.primary.source == "press_release" and o.primary.basis == "non-GAAP"
                    and _non_gaap_measure_qualifiers((o.primary.locator or "").split("|", 1)[0]) == drafted]
            if same:
                return _current_obs(same)
        return _current_obs(pub)

    def _non_gaap_measure_mismatch(self, actual: Obs) -> Optional[Verdict]:
        """A mismatching adjusted total cannot be rejected using a different non-GAAP row such as TE revenue, nor using a
        row whose label names no non-GAAP measure at all: Revvity's "Operating income from continuing operations",
        filed with its non-GAAP figures, against the draft's "adjusted operating income" ($113 million). A row that says
        non-GAAP ("Non-GAAP operating income") is the company's non-GAAP figure and still rejects it."""
        if self.basis != "non-GAAP" or self.growth_base or self.derived:
            return None
        fact = actual.primary
        if fact.source != "press_release" or fact.basis != "non-GAAP":
            return None
        row = (fact.locator or "").split("|", 1)[0].strip()
        stored, drafted = _non_gaap_measure_qualifiers(row), _claim_measure_qualifiers(self.c)
        if not drafted or stored == drafted:
            return None
        if not stored:
            if _ROW_NON_GAAP_RE.search(re.sub(r"[‐‑‒–—―−]", "-", row)):
                return None
            return self.verdict(
                "unverifiable", "ambiguous",
                f"{self.co_s} {form_phrase(fact)} files “{row}” of {self.fmt(actual.value)} with its non-GAAP figures, "
                f"but the row names no {' or '.join(drafted)} measure, so it may not be the draft's "
                f"{self.c.metric_text or self.mlabel(False)}.", evidence=actual.facts)
        return self.verdict(
            "unverifiable", "ambiguous",
            f"{self.co_s} {form_phrase(fact)} reports {row} of {self.fmt(actual.value)}; "
            f"the draft's {self.c.metric_text or self.mlabel(False)} may be a different non-GAAP measure.",
            evidence=actual.facts)

    def _diag_reported_base(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """A non-GAAP income margin that misses the release's own adjusted revenue base but fits on reported revenue:
        "?", never "✗", since the draft may have divided by reported revenue."""
        if self.basis != "non-GAAP" or self.growth_base or self.derived not in ("operating_margin", "net_margin"):
            return None
        net = self.derived == "net_margin"
        hits = [o for o in self.matches(self.income_margin(self.cik, rp, self.basis,
                                                           "net_income" if net else "operating_income",
                                                           strict=net, reported_base=True)) if self.public(o)]
        if not hits:
            return None
        best = _best_obs(hits)
        return self.verdict("unverifiable", "ambiguous",
                            f"{self.c.value_text} is {self.co_s} non-GAAP {self.mlabel(False)} on reported revenue for "
                            f"{self.plabel(rp)}; on the adjusted revenue base its release reports, it is "
                            f"{self.fmt_observation(actual)}.", evidence=[*best.facts, *actual.facts])

    def _diag_not_revenue_row(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """A figure that matches only with a release row level() keeps out of revenue (a comparable-sales,
        constant-currency, organic or pro forma row): "?", never "✗", since the draft may mean that measure. A figure
        stated as GAAP can't."""
        uses_revenue = self.metric in ("revenue", "revenue_growth", *_MARGIN_METRICS) or self.derived is not None
        if (self.basis_stated and self.basis == "GAAP") or self.segment or not uses_revenue:
            return None
        self._allow_not_revenue_rows = True
        try:
            hits = [o for o in self.matches(self.observe(rp, basis="non-GAAP")) if self.public(o)
                    and any(f.metric == "revenue" and f.source == "press_release" and _kept_out_of_revenue(f)
                            for f in o.facts)]
        finally:
            self._allow_not_revenue_rows = False
        if not hits:
            return None
        best = _best_obs(hits)
        row = next(f for f in best.facts if f.metric == "revenue" and _kept_out_of_revenue(f))
        label = (row.locator or "").split("|", 1)[0].strip()
        reported = ("reported revenue" if self.metric == "revenue" and not self.growth_base
                    else f"the {self.mlabel()} on reported revenue")
        return self.verdict("unverifiable", "ambiguous",
                            f"{self.c.value_text} matches a figure from {self.co_s} release row “{label}” for "
                            f"{self.plabel(rp)}, a measure other than revenue, while {reported} is "
                            f"{self.fmt_observation(actual)}.", evidence=[*best.facts, *actual.facts])

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
                                f"{self.fmt_observation(actual)}.", evidence=[*best.facts, *actual.facts])
        return self.wrong("basis_mismatch", actual,
                          f"{self.c.value_text} is {self.co_s} {other} {label} for {self.plabel(rp)}; the "
                          f"{mine} figure is {self.fmt_observation(actual)}.", extra=best.facts)

    def _bank_headline(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """Bank revenue or net interest income with no basis stated may be the managed (FTE) figure, which only
        the earnings release carries: a GAAP mismatch is "?" until a press-release figure can rule that out."""
        measure = "revenue" if self.metric == "revenue_growth" else (self.growth_base or self.metric)
        if self.basis_stated or self.ticker not in BANK_TICKERS or measure not in BASIS_SENSITIVE_AT_BANKS:
            return None
        if any(self.public(o) for o in self.observe(rp, basis="non-GAAP")):
            return None  # both readings are on file and neither matched: a real mismatch
        return self.verdict("unverifiable", "ambiguous",
                            f"{self.attribution(actual)}{self.statement(actual, rp)}, "
                            f"but bank headline {self.mlabel(False)} is often on a managed (fully taxable-equivalent) "
                            f"basis that only the earnings release can confirm.", evidence=actual.facts)

    def _diag_bank(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """_bank_headline, run before the period and company diagnoses: a bank's managed figure that happens to equal
        another quarter's or another bank's GAAP figure is "?" too, not "✗". A x1000 slip is left to _diag_scale, since
        no basis explains it."""
        if (self.metric == "net_margin" and not self.growth_base and not self.basis_stated
                and self.basis is None and self.ticker in BANK_TICKERS):
            # A bank can retain GAAP net income while using managed revenue
            # as its denominator. That is not a non-GAAP-income margin, and
            # checking only that alternative would miss this possible basis.
            income = self.level(self.cik, "net_income", rp, "GAAP")
            revenue = self.level(self.cik, "revenue", rp, "non-GAAP")
            candidates = [Obs(i.value / r.value * 100, i.facts + r.facts,
                              self._margin_error(i.primary, r.primary))
                          for i in income for r in revenue
                          if r.value > 0 and i.primary.unit == r.primary.unit == "USD"
                          and i.primary.period_start == r.primary.period_start
                          and self._same_margin_filing(i.facts + r.facts)]
            hits = [o for o in self.matches(candidates) if self.public(o)]
            if hits:
                best = _best_obs(hits)
                return self.verdict(
                    "unverifiable", "ambiguous",
                    f"{self.c.value_text} matches net income divided by managed (non-GAAP) revenue for "
                    f"{self.plabel(rp)}, while net income divided by GAAP revenue is {self.fmt(actual.value)}. "
                    "The draft does not say which revenue basis its net margin uses.",
                    evidence=[*best.facts, *actual.facts])
        return None if self._diag_scale(rp, actual) is not None else self._bank_headline(rp, actual)

    def _diag_entity(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """A wrong figure that is the same metric of a company the context line names. While that line still sets the
        fiscal period (self.context), any match counts, as before; once it has stopped (it names only other companies),
        the line is just one more mention, so only a precise figure qualifies, as for the draft's peers."""
        c = self.c
        named = self.context_named
        precise = relative_tolerance(c) <= PEER_MATCH_MAX_REL_TOL
        for ocik, _oticker, oname in named:
            if ocik == self.cik or (self.context is None and not precise):
                continue
            ocal = periods.calendar(self.store, ocik)
            orp = periods.resolve_in_calendar(self.period_words, ocal, context=self.context, period_type=c.period_type,
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
        """A wrong figure that is exactly the same metric of another company the same draft names, for the matching
        quarter (the one ending within a week, or the one with the same fiscal label). Only precise figures qualify,
        and only when exactly one named company matches: the status is already "wrong", this only names whose number
        it is. Companies the draft never names are never considered: across hundreds of companies, an equal
        three-digit figure is far more often a coincidence than its owner. The canonical percentages (gross margin, tax
        rate, revenue growth) never qualify: they cluster in a narrow range, so even among the companies one roundup
        names, an equal value is a coincidence, not its owner."""
        if relative_tolerance(self.c) > PEER_MATCH_MAX_REL_TOL or self.c.metric in PERCENT_METRICS:
            return None
        found = []
        for ocik, _oticker, oname in self.named:
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


def named_in_draft(text: Optional[str], store: EvidenceStore) -> list[tuple[int, str, str]]:
    """Companies a draft's own text names, as (cik, ticker, display name). receipts.store.Store scans with its alias
    index (Store.names_in_text); another store gets an n-gram lookup of the same words under the same rules: a word
    ticker or an ordinary word (WORD_NAMES) never counts unless a legal suffix follows it ("Chevron Corp."), and a bare
    ticker counts only written in capitals outside an all-caps run ("NVDA", not "nvda")."""
    if not text:
        return []
    try:  # attribution only refines a reason: a store that can't answer means no named companies, never a failure
        scan = getattr(store, "names_in_text", None)
        if callable(scan):
            return list(scan(text))
        resolve = getattr(store, "resolve_entity", None)
        if not callable(resolve):
            return []
        from receipts.store import (_LEGAL_SUFFIXES, WORD_NAMES, in_caps_run, is_word_ticker, known_names,
                                    normalize_entity, scan_tokens, sentence_joins)
        s, tokens = scan_tokens(text)
        joins = sentence_joins(s, tokens)
        words, found, covered = [w.lower() for w, _, _ in tokens], {}, 0
        for i in range(len(words)):
            if i < covered:
                continue  # inside an earlier name ("Norfolk Southern Corp." is never also Southern Co.)
            for n in range(min(6, len(words) - i), 0, -1):
                if not all(joins[i:i + n - 1]):
                    continue  # no name runs across the end of a sentence
                phrase = " ".join(words[i:i + n])
                key = normalize_entity(phrase)
                if " " in key:
                    if key in WORD_NAMES:
                        continue  # a lookup-only phrase ("Charles River") never counts in running text
                elif (len(key) <= 2 or key in WORD_NAMES or is_word_ticker(key)) and not (
                        n > 1 and words[i + n - 1] in _LEGAL_SUFFIXES):
                    continue  # a word ("the target", "cost"), unless written out with a legal suffix ("Target Corp.")
                hit = resolve(phrase)
                if not hit:
                    continue
                ticker = str(hit[1])
                if n == 1 and key == normalize_entity(ticker) and key not in {normalize_entity(a) for a in known_names(ticker)}:
                    if tokens[i][0] != ticker.upper() or in_caps_run(s, tokens, i):
                        continue  # a bare ticker only as the all-caps word
                names = known_names(ticker) or [phrase]
                found.setdefault(int(hit[0]), (int(hit[0]), ticker, names[0]))
                covered = i + n
                break
        return list(found.values())
    except Exception as e:
        log.warning("named_in_draft failed (%s): no peer attribution for this draft", type(e).__name__)
        return []


def known_companies(store: EvidenceStore) -> list[tuple[int, str, str]]:
    """(cik, ticker, display name) for every company NoCap covers. receipts.store.Store lists its companies table;
    another store gets receipts.store.ALIASES, resolved by display name (a word ticker never resolves by itself)."""
    listing = getattr(store, "companies_list", None)
    if callable(listing):
        return list(listing())
    try:
        from receipts.store import ALIASES
    except Exception:  # an evidence layer without the alias table
        return []
    out = []
    for ticker, names in ALIASES.items():
        hit = None
        for probe in (names[0] if names else ticker, ticker):
            try:
                hit = store.resolve_entity(probe)
            except Exception:
                hit = None
            if hit and str(hit[1]) == ticker:
                break
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
