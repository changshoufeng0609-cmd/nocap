"""Gates on press-release facts that need the company's XBRL picture or the release's own row label (review fixes 5
and 6).

receipts.press_release's XBRL gate drops a release figure the paired 10-Q/10-K contradicts. Two kinds of wrong
figure slip past it, and scripts/gate_release_facts.py applies these rules to the stored release files after mapping
(as scripts/relabel_release_facts.py does for labels):

  * Net income including noncontrolling interests (QA T2). CF Industries' release leads its income statement with
    "Net earnings" ($869M in Q2 2026, including $142M for noncontrolling interests); its XBRL tags the attributable
    $727M only as NetIncomeLossAvailableToCommonStockholdersBasic, and ingest refuses NCI-inclusive ProfitLoss, so
    the store held no XBRL net income for the gate to compare with. A GAAP release net income for a period without
    an XBRL net income is dropped when the company reports a noncontrolling interest in any 10-Q/10-K period of the
    window (an NCI net income that is not zero, or ProfitLoss differing from NetIncomeLoss): it may well include it.
    A figure that companyfacts shows to be the attributable net income for its period (NetIncomeLoss, the
    available-to-common figure, or ProfitLoss minus the NCI) counts as paired and stays.
  * GAAP rows stored as non-GAAP (QA T5). A reconciliation table starts from the GAAP figure ("Operating income
    (GAAP) | 690.1" at Dollar Tree, "GAAP net income" at O'Reilly), and that row was stored as non-GAAP. A non-GAAP
    release fact is dropped when its row label names a GAAP measure, "GAAP" with no "non-GAAP", "adjusted" or
    "excluding" (any hyphen: Fortinet writes "Non‐GAAP" with U+2010), whether or not a GAAP figure equals it,
    because its basis cannot be trusted. A non-GAAP fact whose label does not say GAAP stays even when it equals the
    GAAP figure: NVIDIA's non-GAAP diluted EPS was $1.30 in Q3 FY2026, as its GAAP EPS was, and both are reported.
    So does a row of a measure that is non-GAAP by definition (free cash flow, capital returned) whose label merely
    mentions GAAP.

Round 2 of the QA found release figures that the paired-filing comparison cannot see (review fixes 7 to 9):

  * Every release GAAP figure against all same-period XBRL (fix 7). The mapper compares a release with its paired
    10-Q/10-K, and excuses a mismatch with an older filing as a possible recast; Dow's Q2 2026 release (its 10-Q was
    missing from companyfacts) stored the 2Q26 diluted EPS, $0.99, as Q2 2025's (true -$1.18). Each served release
    GAAP, company-level figure is now compared with every XBRL value the store holds for the same metric and dates,
    reported or derived. A disagreement beyond rounding is excused only by a documented restatement: the same concept
    reports the release value for that period in some 10-Q/10-K, a stock split the store documents, or a file whose
    other columns show XBRL's restated figures (Bank of America's 2026 releases). Against a reported value the release
    figure is dropped; a reported XBRL fact is never dropped. Against a derived quarter (Q4 = FY - 9M), whose inputs
    can sit on two bases (BAC's restated FY2025 minus its original 9M gave Q4 revenue of $31,180M; its release says
    $28,367M), the release row's own record decides: when the same row of the company's releases agrees with reported
    XBRL in some period and disagrees in none, the release was published with that quarter, its figure is not a
    consolidated net income or a scale or sign slip, and no other release agrees with the derived value, the derived
    value is rejected (ingest reads data/derived_rejections.json) and the release figure stays. Otherwise the release
    figure goes: Arch's "Net premiums earned" as revenue, a cost line printed in parentheses.
  * Labels against dates (fix 8). A release figure labelled FY or Q1-Q4 must cover that label's dates in the store's
    fiscal calendar (or the same label a year or two away, shifted by whole years), within 7 days. An unlabelled
    twelve-month figure that does not end at a fiscal year end is a trailing-twelve-month figure (Charter, United) and
    is not served.
  * GAAP lines stored as non-GAAP (fix 11, QA round 3 T1). A section header ("Expenses Excluding Interest" over
    Schwab's net income), a table heading ("Adjusted Diluted EPS" over Vulcan's reconciliation, whose first row is
    GAAP EPS) or a footnote parsed as a heading ("* Non-GAAP financial measure." over GE HealthCare's income
    statement) made a GAAP line the period's non-GAAP figure, so a true adjusted claim met the GAAP number. A
    non-GAAP figure of a measure with an adjusted variant that equals the same-period GAAP filing value is dropped
    unless its own cell says non-GAAP: its row label or column header, a table titled non-GAAP in which no row is
    singled out as adjusted (NVIDIA's "Non-GAAP" summary, whose $1.30 diluted EPS equalled GAAP in Q3 FY2026), or an
    adjusted row of the same measure showing the same number beside it (Walmart's adjusted operating income).
  * x1000 slips (fix 9). Where no same-period XBRL can check it, a release figure about 1,000 times smaller or larger
    than the company's other figures for that row and metric (other columns and files, reported XBRL for nearby
    periods) whose own precision is off by about three places from theirs is a misread scale: Block's 2024 cash-flow
    columns read in thousands beside the same rows in millions, Mosaic's "Net Sales - billions $" stored as $3.0M. A
    small figure printed at the table's own precision (a $1M segment) is real and stays.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional

from receipts.press_release import _NONGAAP_RE, _RECON_RE, is_served
from receipts.types import Fact

NCI_CONCEPTS = ("NetIncomeLossAttributableToNoncontrollingInterest",
                "NetIncomeLossAttributableToRedeemableNoncontrollingInterest",
                "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest")
PERIODIC_FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A")
# Metrics with a GAAP definition: a row of one labelled as GAAP is the GAAP figure, whatever its stored basis.
GAAP_MEASURES = frozenset({"revenue", "segment_revenue", "gross_margin", "gross_profit", "cost_of_revenue",
                           "research_and_development", "operating_expenses", "operating_income", "net_income",
                           "eps_basic", "eps_diluted", "dividend_per_share", "tax_rate", "operating_cash_flow",
                           "net_interest_income", "repurchase_authorization_remaining"})
_HYPHENS = str.maketrans({c: "-" for c in "‐‑‒–—−"})
_GAAP = re.compile(r"\bgaap\b", re.I)
_NOT_GAAP = re.compile(r"non\s*-?\s*gaap|adjusted|excluding", re.I)

Key = tuple[str, Optional[str], str, Optional[str]]  # metric, period start, period end (ISO), segment


def noncontrolling_interest(usgaap: dict, since: str = "") -> Optional[str]:
    """Why the company has a noncontrolling interest in some 10-Q/10-K period filed since `since`, or None: an NCI net
    income that is not zero, or ProfitLoss differing from NetIncomeLoss for the same filing and period."""
    def entries(concept: str) -> Iterable[dict]:
        for e in usgaap.get(concept, {}).get("units", {}).get("USD", []):
            if e.get("form") in PERIODIC_FORMS and e.get("filed", "") >= since and e.get("val") is not None:
                yield e
    for concept in NCI_CONCEPTS:
        for e in entries(concept):
            if e["val"]:
                return f"{concept} {e['val']:,.0f} in {e['accn']} ({e.get('start')}..{e['end']})"
    profit = {(e["accn"], e.get("start"), e["end"]): e["val"] for e in entries("ProfitLoss")}
    for e in entries("NetIncomeLoss"):
        k = (e["accn"], e.get("start"), e["end"])
        if k in profit and profit[k] != e["val"]:
            return f"ProfitLoss {profit[k]:,.0f} against NetIncomeLoss {e['val']:,.0f} in {k[0]} ({k[1]}..{k[2]})"
    return None


def attributable_net_income(usgaap: dict, since: str = "") -> dict[tuple[Optional[str], str], list[float]]:
    """(start, end) -> the company's own net income as its 10-Qs and 10-Ks tag it, whatever the concept:
    NetIncomeLoss, NetIncomeLossAvailableToCommonStockholdersBasic, or ProfitLoss minus the noncontrolling
    interest of the same filing and period. A release figure equal to one of these is checked, not NCI-inclusive
    (Costco's release net income is its attributable figure, which the store does not serve as XBRL)."""
    def entries(concept: str) -> Iterable[dict]:
        for e in usgaap.get(concept, {}).get("units", {}).get("USD", []):
            if e.get("form") in PERIODIC_FORMS and e.get("filed", "") >= since and e.get("val") is not None:
                yield e
    out: dict[tuple[Optional[str], str], list[float]] = defaultdict(list)
    for concept in ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"):
        for e in entries(concept):
            out[(e.get("start"), e["end"])].append(float(e["val"]))
    nci: dict[tuple, float] = defaultdict(float)
    tagged: dict[tuple, set[str]] = defaultdict(set)
    for concept in NCI_CONCEPTS:
        for e in entries(concept):
            nci[(e["accn"], e.get("start"), e["end"])] += float(e["val"])
            tagged[(e["accn"], e.get("start"), e["end"])].add(concept)
    # Only the whole NCI makes ProfitLoss the company's own net income: APA tags its redeemable NCI as zero for Q3
    # 2023 while a later 10-Q reports $96M of nonredeemable NCI for it (review fix 15, as receipts.ingest does).
    total, redeemable, nonredeemable = NCI_CONCEPTS
    shown = {(e.get("start"), e["end"]) for concept in NCI_CONCEPTS for e in entries(concept) if e["val"]}
    for e in entries("ProfitLoss"):
        k = (e["accn"], e.get("start"), e["end"])
        if (total in tagged[k] or {redeemable, nonredeemable} <= tagged[k]) and (nci[k] or k[1:] not in shown):
            out[(e.get("start"), e["end"])].append(float(e["val"]) - nci[k])
    return out


def key(f: Fact) -> Key:
    return (f.metric, f.period_start.isoformat() if f.period_start else None, f.period_end.isoformat(), f.segment)


def _unit(decimals: Optional[int]) -> float:
    return 0.0 if decimals is None else 10.0 ** -decimals


def same_figure(a: float, a_decimals: Optional[int], b: float, b_decimals: Optional[int]) -> bool:
    """One number at the coarser of the two precisions: $727M (a release in $1M) and $727,000,000 (XBRL)."""
    return abs(a - b) <= 0.5 * max(_unit(a_decimals), _unit(b_decimals)) * (1 + 1e-9) + 1e-9


def gaap_row(f: Fact) -> bool:
    """The fact's row label names a GAAP measure: "GAAP" in it, and no "non-GAAP", "adjusted" or "excluding"."""
    label = (f.locator or "").split("|")[0].translate(_HYPHENS)
    return bool(_GAAP.search(label)) and not _NOT_GAAP.search(label)


def gate(facts: dict[str, list[Fact]], xbrl: dict[Key, list[tuple[float, Optional[int]]]],
         nci: Optional[str], attributable: Optional[dict[tuple[Optional[str], str], list[float]]] = None,
         vouched: Iterable[tuple[str, str, str, float]] = ()) -> tuple[dict[str, list[int]], list[dict]]:
    """One company's release facts, by file. `xbrl`: the store's XBRL and derived values by key (segment None);
    `attributable`: attributable_net_income() of its companyfacts, which also pairs a release net income; `vouched`:
    (metric, start, end, value) of release figures that outranked a derived value under fix 7, which count as paired
    once that value has left the store. Returns the line numbers to drop by file and a record per dropped fact."""
    attributable = attributable or {}
    vouched = set(vouched)
    drop: dict[str, set[int]] = defaultdict(set)
    records: list[dict] = []

    def dropped(name: str, i: int, f: Fact, rule: str, why: str) -> None:
        drop[name].add(i)
        records.append({"file": name, "line": i + 1, "rule": rule, "metric": f.metric, "basis": f.basis,
                        "segment": f.segment, "period": f"{key(f)[1]}..{key(f)[2]}", "value": f.value,
                        "locator": f.locator, "why": why})

    for name, rows in facts.items():
        for i, f in enumerate(rows):
            # Fix 5: net income that may include noncontrolling interests, with no XBRL net income to check it.
            if (nci and f.metric == "net_income" and f.basis == "GAAP" and f.segment is None and f.period_start
                    and not xbrl.get(key(f))
                    and not any(m == f.metric and (s, e) == key(f)[1:3] and same_figure(f.value, f.decimals, v, None)
                                for m, s, e, v in vouched)
                    and not any(same_figure(f.value, f.decimals, v, None)
                                for v in attributable.get((key(f)[1], key(f)[2]), []))):
                dropped(name, i, f, "nci_net_income",
                        f"no XBRL net income for the period, and the company reports a noncontrolling interest ({nci})")
            # Fix 6: a GAAP row stored as non-GAAP.
            elif f.basis == "non-GAAP" and f.metric in GAAP_MEASURES and gaap_row(f):
                dropped(name, i, f, "gaap_row_as_non_gaap", "the row label names a GAAP measure")
    return {n: sorted(s) for n, s in drop.items() if s}, records


# --------------------------------------------------------------------------------------------------
# Review fixes 7-9 (QA round 2)
# --------------------------------------------------------------------------------------------------
NON_NEGATIVE = frozenset({"revenue", "cost_of_revenue", "research_and_development", "operating_expenses"})
PER_SHARE = frozenset({"eps_basic", "eps_diluted", "dividend_per_share"})
SPLIT_RATIOS = (2, 3, 4, 5, 10, 20, 25, 50)
DERIVED_SLACK = 0.0005    # a company's own quarter and FY - 9M can differ by a hair of rounding (0.05%)
RESTATED_MIN = 0.001      # versions closer than 0.1% are one figure rounded differently, not a restatement
CONTEMPORARY_DAYS = 100   # a release published within about a quarter of the filing a derived value comes from
PERIOD_DAYS = 7           # 52/53-week calendars move period ends by up to a week
SCALE_BAND = (300.0, 3000.0)
# Bounds on a Q4 against its fiscal year, for metrics never negative: receipts.ingest.derive serves no derived Q4
# outside them, and fix 7 trusts no release Q4 outside them (review fix 16). A quarter is about a quarter of its year:
# above three quarters the other three would average under a twelfth, below a tenth they would average over 30%. In
# the 499-company store the share above 75% is only Crown Castle's cost of revenue (FY CostOfRevenue minus a 9M of the
# narrower CostOfGoodsAndServicesSold: 86% and 95%), while real impairment quarters sit at 61-65% (TTWO, CCI operating
# expenses); the share below 10% is only OXY's FY2025 revenue (a 10-K without OxyChem minus a 9M with it: 8.1%) and
# Atmos and CenterPoint cost lines whose quarters do not add up (0-1%), while the most seasonal real quarter, Atmos'
# summer revenue, is 13.7%.
MIN_Q4_SHARE, MAX_Q4_SHARE = 0.10, 0.75

XKey = tuple[str, str, str]  # metric, period start, period end (ISO): the store's XBRL is GAAP and company-level


@dataclass(frozen=True)
class XbrlValue:
    """One XBRL value the store holds for a metric and period: reported by a 10-Q/10-K, or derived from two."""
    value: float
    decimals: Optional[int]
    source: str              # "xbrl" or "derived"
    accession: str
    accepted_at: datetime
    concept: str = ""        # us-gaap local name
    terms: int = 1           # a derived value combines this many filed figures (Q4 = FY - 9M: 2)


def _decimals(value: float, decimals: Optional[int]) -> int:
    """The stated precision, else the one the value's trailing zeros imply ($434,700,000 is a figure in $0.1M)."""
    if decimals is not None:
        return decimals
    n, zeros = abs(round(value)), 0
    while n and n % 10 == 0 and zeros < 9:
        n //= 10
        zeros += 1
    return -zeros


def _half(value: float, decimals: Optional[int]) -> float:
    return 0.5 * 10.0 ** -_decimals(value, decimals)


def agrees(f: Fact, x: XbrlValue) -> bool:
    """The release figure and the XBRL value are one number at their precision. A derived value carries the rounding
    of each filed figure it combines, and a company's own quarter may differ from FY - 9M by DERIVED_SLACK."""
    diff = abs(f.value - x.value)
    if x.source == "derived":
        tol = _half(f.value, f.decimals) + x.terms * _half(x.value, x.decimals)
        return diff <= tol * (1 + 1e-9) + 1e-9 or diff <= DERIVED_SLACK * abs(f.value)
    return diff <= max(_half(f.value, f.decimals), _half(x.value, x.decimals)) * (1 + 1e-9) + 1e-9


def served(f: Fact) -> bool:
    """receipts.store serves instants, quarters (80-100 days) and years (350-380 days); kept in step with
    receipts.press_release.is_served, which also counts a labelled 16- or 17-week quarter (Costco's Q4), so fix 7
    checks it against same-period XBRL like any quarter."""
    return is_served(f)


def row_label(f: Fact) -> str:
    """The release row a figure comes from, normalised: footnote marks and "(loss)" do not make another row."""
    return locator_row(f.locator)


def locator_row(locator: Optional[str]) -> str:
    """row_label() of a locator string, as a gate record keeps it."""
    label = (locator or "").rsplit(" | ", 1)[0].translate(_HYPHENS).casefold()
    label = re.sub(r"\(\s*[0-9a-z]{1,2}\s*\)|[*†‡]", " ", label)
    label = re.sub(r"\((?:loss|losses|income)\)", " ", label)
    return re.sub(r"\s+", " ", label).strip(" :")


def _xkey(f: Fact) -> XKey:
    return (f.metric, f.period_start.isoformat() if f.period_start else "", f.period_end.isoformat())


def _unit_key(f: Fact) -> str:
    return "USD/shares" if f.unit == "USD/share" else "USD"


def _m(v: float) -> str:
    return f"{v / 1e6:,.1f}M" if abs(v) >= 1e5 else f"{v:g}"


def documented_splits(xbrl: dict[XKey, list[XbrlValue]]) -> set[int]:
    """Stock splits the store documents: a per-share period whose reported versions differ by a whole ratio."""
    out: set[int] = set()
    for (metric, _, _), values in xbrl.items():
        if metric not in PER_SHARE:
            continue
        reported = [abs(v.value) for v in values if v.source == "xbrl" and v.value]
        for a in reported:
            for b in reported:
                out.update(n for n in SPLIT_RATIOS if b > a and math.isclose(b / a, n, rel_tol=0.02))
    return out


def documented(f: Fact, versions: list[XbrlValue], usgaap: dict) -> Optional[str]:
    """A 10-Q or 10-K tags the release value for this very period under a concept the store uses for it: the period
    was restated after that filing (the mapper's own "restated" exception)."""
    start, end = f.period_start.isoformat(), f.period_end.isoformat()
    for concept in sorted({v.concept for v in versions if v.source == "xbrl" and v.concept}):
        for x in usgaap.get(concept, {}).get("units", {}).get(_unit_key(f), []):
            if (x.get("form") in PERIODIC_FORMS and x.get("start") == start and x.get("end") == end
                    and x.get("val") is not None
                    and abs(f.value - x["val"]) <= max(_half(f.value, f.decimals), _half(x["val"], None)) * (1 + 1e-9)):
                return f"{concept} in {x['accn']} reports {_m(f.value)} for the period"
    return None


def _split(f: Fact, versions: list[XbrlValue], splits: set[int]) -> Optional[str]:
    if f.metric not in PER_SHARE or not splits:
        return None
    for v in versions:
        if v.value and f.value:
            for n in splits:
                for r in (n, 1 / n):
                    if math.isclose(f.value / v.value, r, rel_tol=0.02):
                        return f"a {n}-for-1 split the store documents ({v.accession} reports {v.value:g})"
    return None


def restated_basis(f: Fact, file_facts: Iterable[Fact], xbrl: dict[XKey, list[XbrlValue]]) -> Optional[str]:
    """Another column of the same release row shows a figure that a later filing restated, and not the original: the
    release is on the restated basis, so its other columns may be restated too (Bank of America's 2026 releases)."""
    label = row_label(f)
    for o in file_facts:
        if o is f or o.metric != f.metric or o.basis != "GAAP" or o.segment or not o.period_start or row_label(o) != label:
            continue
        reported = sorted((v for v in xbrl.get(_xkey(o), []) if v.source == "xbrl"), key=lambda v: v.accepted_at)
        if len(reported) < 2 or agrees(o, reported[0]):
            continue
        first = reported[0]
        for v in reported[1:]:
            if abs(v.value - first.value) > RESTATED_MIN * abs(first.value) and agrees(o, v):
                return (f"the same row's {o.period_start}..{o.period_end} column equals {v.accession}'s restated "
                        f"{_m(v.value)} (originally {_m(first.value)})")
    return None


def consolidated_net_income(usgaap: dict, start: str, end: str) -> list[float]:
    """ProfitLoss (net income including noncontrolling interests) for the period: filed directly, or as a
    year-to-date difference (a fourth quarter is FY - 9M)."""
    rows = [x for x in usgaap.get("ProfitLoss", {}).get("units", {}).get("USD", [])
            if x.get("form") in PERIODIC_FORMS and x.get("start") and x.get("val") is not None]
    out = [x["val"] for x in rows if x["start"] == start and x["end"] == end]
    day_before = date.fromisoformat(start) - timedelta(days=1)
    for ytd in rows:
        if ytd["end"] == end and ytd["start"] < start:
            out += [ytd["val"] - p["val"] for p in rows if p["start"] == ytd["start"]
                    and abs((date.fromisoformat(p["end"]) - day_before).days) <= 3]
    return out


def _scale_or_sign(f: Fact, x: XbrlValue) -> Optional[str]:
    if f.value and x.value:
        ratio = abs(f.value / x.value)
        for k in (1e3, 1e6, 1e-3, 1e-6):
            if math.isclose(ratio, k, rel_tol=0.02):
                return f"scale: {ratio:g} times the derived {_m(x.value)}"
    if f.metric in NON_NEGATIVE and f.value < 0 <= x.value:
        return f"sign: a {f.metric.replace('_', ' ')} of {_m(f.value)} against the derived {_m(x.value)}"
    return None


def _one_number(a: Fact, b: Fact) -> bool:
    """Two release figures are one number at the coarser of their precisions."""
    return abs(a.value - b.value) <= max(_half(a.value, a.decimals), _half(b.value, b.decimals)) * (1 + 1e-9) + 1e-9


def _year_share(f: Fact, xbrl: dict[XKey, list[XbrlValue]]) -> Optional[str]:
    """Why a release quarter of a metric never negative cannot be the last quarter of the fiscal year the 10-K reports
    ending with it: outside MIN_Q4_SHARE..MAX_Q4_SHARE of every reported version of that year. None if it fits one,
    or if no reported year ends with the quarter."""
    if f.metric not in NON_NEGATIVE or f.period_start is None:
        return None
    years = [x.value for (metric, start, end), xs in xbrl.items() if metric == f.metric and start
             and abs((date.fromisoformat(end) - f.period_end).days) <= PERIOD_DAYS
             and 350 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 380
             for x in xs if x.source == "xbrl" and x.value > 0]
    if not years or any(MIN_Q4_SHARE <= f.value / y <= MAX_Q4_SHARE for y in years):
        return None
    y = min(years, key=lambda y: abs(f.value / y - 0.25))
    return (f"{f.value / y:.0%} of the fiscal year's reported {_m(y)}, outside "
            f"{MIN_Q4_SHARE:.0%}-{MAX_Q4_SHARE:.0%} of a year for a quarter")


def _shift(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def calendar_period(calendar: dict[tuple[int, str], tuple[Optional[date], date]], fiscal_year: int,
                    fiscal_period: str) -> Optional[tuple[Optional[date], date, str]]:
    """The dates of a fiscal label: the store's, else the same label a year or two away moved by whole years."""
    if (fiscal_year, fiscal_period) in calendar:
        s, e = calendar[(fiscal_year, fiscal_period)]
        return s, e, "the store's calendar"
    for k in (1, -1, 2, -2):
        if (fiscal_year - k, fiscal_period) in calendar:
            s, e = calendar[(fiscal_year - k, fiscal_period)]
            return (_shift(s, k) if s else None), _shift(e, k), f"FY{fiscal_year - k}'s {fiscal_period} moved {k:+d}y"
    return None


def label_conflict(f: Fact, calendar: dict[tuple[int, str], tuple[Optional[date], date]]) -> Optional[str]:
    """Fix 8: a label the figure's dates contradict, or an unlabelled twelve-month figure that does not end at a fiscal
    year end (a trailing-twelve-month column). None when the label fits or nothing can check it."""
    if f.period_start is None:
        return None
    if f.fiscal_period in ("Q1", "Q2", "Q3", "Q4", "FY") and f.fiscal_year:
        ref = calendar_period(calendar, f.fiscal_year, f.fiscal_period)
        if ref is None:
            return None
        s, e, how = ref
        if abs((f.period_end - e).days) > PERIOD_DAYS or (s and abs((f.period_start - s).days) > PERIOD_DAYS):
            return (f"{f.fiscal_period} FY{f.fiscal_year} is {s}..{e} ({how}); the figure covers "
                    f"{f.period_start}..{f.period_end}")
        return None
    if 350 <= (f.period_end - f.period_start).days <= 380:
        ends = [e for (_, fp), (_, e) in calendar.items() if fp == "FY"]
        if ends and not any(abs((f.period_end - _shift(x, k)).days) <= PERIOD_DAYS for x in ends for k in range(-3, 4)):
            return f"twelve months ending {f.period_end}, not a fiscal year end: a trailing-twelve-month figure"
    return None


def scale_slip(f: Fact, comparators: list[tuple[float, Optional[int], bool]]) -> Optional[str]:
    """Fix 9: `comparators` are (value, decimals, from_a_release) for the company's other figures of this metric,
    basis, segment and duration nearby. A slip is about 1,000 times off their median while the figure's own precision
    is about three places off theirs: the table's scale was misread. A small figure at the row's precision is real."""
    vals = [(abs(v), d, r) for v, d, r in comparators if v]
    if len(vals) < 2 or not f.value or f.decimals is None:
        return None
    ratio = abs(f.value) / statistics.median(v for v, _, _ in vals)
    decs = [d for _, d, r in vals if r and d is not None] or [d for _, d, _ in vals if d is not None]
    if not decs:
        return None
    gap = f.decimals - statistics.median(decs)
    lo, hi = SCALE_BAND
    if 1 / hi <= ratio <= 1 / lo and gap >= 2:
        return f"{ratio:.2g} times the median of {len(vals)} figures of the same row and metric, at {gap:g} more decimals"
    if lo <= ratio <= hi and gap <= -2:
        return f"{ratio:.3g} times the median of {len(vals)} figures of the same row and metric, at {-gap:g} fewer decimals"
    return None


def same_period_slip(f: Fact, others: Iterable[Fact], majority: Optional[float]) -> Optional[str]:
    """Fix 9, same period: another release's figure for the same row and dates is exactly 1,000 times this one, and of
    the two only this one is off the precision most of the company's figures of the row use (`majority`): Block's
    $457,000 beside its own $457M. Mosaic's "$3.0" read in millions beside $3.0 billion is the same slip."""
    if majority is None or f.decimals is None or not f.value:
        return None
    gap = f.decimals - majority
    for o in others:
        if not o.value or o.decimals is None or abs(o.decimals - majority) > 1:
            continue
        ratio = abs(f.value / o.value)
        if (math.isclose(ratio, 1e-3, rel_tol=0.02) and gap >= 2) or (math.isclose(ratio, 1e3, rel_tol=0.02) and gap <= -2):
            return f"{ratio:g} times {o.accession}'s {_m(o.value)} for the same period, at {gap:+g} decimals"
    return None


ADJUSTED_METRICS = frozenset({"net_income", "eps_diluted", "eps_basic", "operating_income", "gross_profit"})
# Row words that name each measure, for an adjusted row of the same measure ("Adjusted operating income").
MEASURE_WORDS = {"net_income": re.compile(r"net (?:income|earnings|loss)|earnings attributable|\bearnings\b", re.I),
                 "eps_diluted": re.compile(r"\beps\b|per (?:diluted )?(?:common )?share|diluted|earnings per", re.I),
                 "eps_basic": re.compile(r"\beps\b|per (?:basic )?(?:common )?share|basic|earnings per", re.I),
                 "operating_income": re.compile(r"operating (?:income|profit|earnings|loss)|income from operations", re.I),
                 "gross_profit": re.compile(r"gross (?:profit|margin)", re.I)}
_FOOTNOTE = re.compile(r"^\s*(?:[*†‡]|\(\w\)|\d+\s)")


def table_places(f: Fact, tables: Iterable) -> list[dict]:
    """Every cell of the exhibit's parsed tables (receipts.press_release.parse_tables) that holds the fact's row label
    and cell text, and what marks it non-GAAP there: its column header, its row label, a table titled non-GAAP (a
    short heading line, not a footnote or a sentence) in which no row carries a non-GAAP marker of its own, or an
    adjusted row of the same measure whose cell in that column is the same number (Walmart's adjusted operating
    income equalled GAAP in Q4 FY2026: the release's figure is the adjusted one too)."""
    label, _, cell = (f.locator or "").rpartition(" | ")
    # A transposed table's locator is "period row | measure column | cell" (review L13); a row label may itself hold
    # " | ", so both readings are tried.
    parts = (f.locator or "").split(" | ")
    transposed = (parts[0], parts[1]) if len(parts) == 3 else None
    words = MEASURE_WORDS.get(f.metric)
    out = []
    for t in tables:
        marked = sum(1 for r in t.rows if _NONGAAP_RE.search(r.label))
        titled = any(_NONGAAP_RE.search(h) and len(h.split()) <= 8 and not _FOOTNOTE.match(h) for h in t.heading)
        for r in t.rows:
            whole = r.label == label
            if not whole and not (transposed and r.label == transposed[0]):
                continue
            for ci, text in r.cells.items():
                if text == cell and (whole or (t.columns[ci][-1] if t.columns[ci] else None) == transposed[1]):
                    same = bool(words) and any(o is not r and _NONGAAP_RE.search(o.label) and words.search(o.label)
                                               and _number(o.cells.get(ci)) == _number(text) for o in t.rows)
                    out.append({"column": bool(_NONGAAP_RE.search(t.column_text(ci))),
                                "label": bool(_NONGAAP_RE.search(r.label)),
                                "summary": titled and marked == 0 and not _NONGAAP_RE.search(r.section),
                                "adjusted_equal": same})
    return out


def _number(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    t = text.replace("$", "").replace(",", "").replace(" ", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        return f"{(-1 if neg else 1) * float(t):.6f}"
    except ValueError:
        return None


def gate_all(facts: dict[str, list[Fact]], xbrl: dict[XKey, list[XbrlValue]],
             calendar: dict[tuple[int, str], tuple[Optional[date], date]], usgaap: dict, nci: Optional[str],
             attributable: Optional[dict[tuple[Optional[str], str], list[float]]] = None,
             vouched: Iterable[tuple[str, str, str, float]] = (),
             exhibit_tables: Optional[Callable[[str], list]] = None,
             prior_disagreements: Iterable[tuple[str, str]] = ()
             ) -> tuple[dict[str, list[int]], list[dict], list[dict]]:
    """Fixes 5 and 6 (gate()), then 11, 8, 9 and 7, for one company's live release files. `exhibit_tables(url)`: the
    parsed tables of a release exhibit, for fix 11 (without it fix 11 is skipped). `prior_disagreements`: (metric,
    locator_row) of each figure an earlier run dropped against reported XBRL, which still counts against its row, so a
    second run decides as the first did (review L2). Returns the line numbers to drop by file, a record per dropped
    fact, and the derived XBRL values its own releases contradict."""
    legacy: dict[Key, list[tuple[float, Optional[int]]]] = defaultdict(list)
    for (metric, start, end), values in xbrl.items():
        legacy[(metric, start or None, end, None)] += [(v.value, v.decimals) for v in values]
    drop56, records = gate(facts, legacy, nci, attributable, vouched)
    gone = {(name, i) for name, idx in drop56.items() for i in idx}

    def dropped(name: str, i: int, f: Fact, rule: str, why: str) -> None:
        gone.add((name, i))
        records.append({"file": name, "line": i + 1, "rule": rule, "metric": f.metric, "basis": f.basis,
                        "segment": f.segment, "period": f"{key(f)[1]}..{key(f)[2]}", "value": f.value,
                        "locator": f.locator, "why": why})

    live = lambda: [(n, i, f) for n, rows in facts.items() for i, f in enumerate(rows) if (n, i) not in gone]
    # Fix 11: a GAAP line stored as the period's non-GAAP figure.
    if exhibit_tables is not None:
        for name, i, f in live():
            if (f.basis != "non-GAAP" or f.segment is not None or f.metric not in ADJUSTED_METRICS or not f.period_start
                    or _NONGAAP_RE.search((f.locator or "").rpartition(" | ")[0])):
                continue
            equal = [v for v in xbrl.get(_xkey(f), []) if agrees(f, v)]
            if not equal:
                continue
            places = table_places(f, exhibit_tables(f.url))
            if places and not any(p["column"] or p["label"] or p["summary"] or p["adjusted_equal"] for p in places):
                dropped(name, i, f, "gaap_line_as_non_gaap",
                        f"equals the filed GAAP {_m(equal[0].value)} ({equal[0].source} {equal[0].accession}), and "
                        f"neither its row, its column nor a non-GAAP table title marks its cell non-GAAP")
    # Fix 8: labels and dates.
    for name, i, f in live():
        why = label_conflict(f, calendar)
        if why:
            dropped(name, i, f, "label_dates", why)
    # Fix 9: x1000 slips where no same-period XBRL can check the figure.
    rows = live()
    groups: dict[tuple, list[tuple[str, int, Fact]]] = defaultdict(list)
    for name, i, f in rows:
        groups[(f.metric, f.basis, f.segment, f.unit)].append((name, i, f))
    reported_by_metric: dict[str, list[tuple[Optional[date], date, XbrlValue]]] = defaultdict(list)
    for (metric, start, end), values in xbrl.items():
        for v in values:
            if v.source == "xbrl":
                reported_by_metric[metric].append((date.fromisoformat(start) if start else None,
                                                   date.fromisoformat(end), v))
    for name, i, f in rows:
        if f.unit != "USD" or not f.value or (f.basis == "GAAP" and f.segment is None and xbrl.get(_xkey(f))):
            continue
        span = None if f.period_start is None else (f.period_end - f.period_start).days

        def near(g_start: Optional[date], g_end: date) -> bool:
            g_span = None if g_start is None else (g_end - g_start).days
            if (span is None) != (g_span is None) or (span is not None and abs(span - g_span) > 10):
                return False
            return abs((g_end - f.period_end).days) <= 400

        peers = [g for n, j, g in groups[(f.metric, f.basis, f.segment, f.unit)]
                 if (n, j) != (name, i) and near(g.period_start, g.period_end)]
        same = [g for g in peers if g.period_start == f.period_start and g.period_end == f.period_end]
        comps = [(g.value, g.decimals, True) for g in peers]
        if f.basis == "GAAP" and f.segment is None:
            comps += [(v.value, v.decimals, False) for s, e, v in reported_by_metric[f.metric] if near(s, e)]
        release_decs = [g.decimals for g in peers if g.decimals is not None]
        xbrl_decs = [d for _, d, r in comps if not r and d is not None]
        majority = statistics.median(release_decs or xbrl_decs) if (release_decs or xbrl_decs) else None
        why = same_period_slip(f, same, majority) or scale_slip(f, comps)
        if why:
            dropped(name, i, f, "scale_slip", why)
    # Fix 7: every served release GAAP figure against all same-period XBRL.
    splits = documented_splits(xbrl)
    rows = live()
    by_file: dict[str, list[Fact]] = defaultdict(list)
    for name, _, f in rows:
        by_file[name].append(f)
    cand = [(n, i, f) for n, i, f in rows if f.basis == "GAAP" and f.segment is None and f.period_start
            and served(f) and xbrl.get(_xkey(f))]

    def excuse(name: str, f: Fact, versions: list[XbrlValue]) -> Optional[str]:
        return (documented(f, versions, usgaap) or _split(f, versions, splits)
                or restated_basis(f, by_file[name], xbrl))

    track: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"agree": 0, "excused": 0, "disagree": 0})
    for name, _, f in cand:
        reported = [v for v in xbrl[_xkey(f)] if v.source == "xbrl"]
        if reported:
            t = track[(f.metric, row_label(f))]
            if any(agrees(f, v) for v in reported):
                t["agree"] += 1
            else:
                t["excused" if excuse(name, f, reported) else "disagree"] += 1
    for metric, label in prior_disagreements:
        track[(metric, label)]["disagree"] += 1

    release_rows: dict[str, list[Fact]] = defaultdict(list)  # every company-level GAAP figure of a release
    for all_rows in facts.values():
        for o in all_rows:
            if o.basis == "GAAP" and o.segment is None and o.period_start is not None:
                release_rows[o.accession].append(o)

    def distrust(f: Fact, v: XbrlValue) -> Optional[str]:
        """Why the release figure cannot outrank the derived value v; None when it does."""
        if f.accepted_at > v.accepted_at + timedelta(days=CONTEMPORARY_DAYS):
            return f"the release came {(f.accepted_at - v.accepted_at).days} days after the derived value's filing"
        if "preliminary" in row_label(f):
            return "a preliminary figure"
        slip = _scale_or_sign(f, v)
        if slip:
            return slip
        # Review fix 16: a cell of another period read as this one (a twelve-month column read as Q4) is no slip, and
        # the row's record in other periods does not vouch for it.
        twin = next((o for o in release_rows[f.accession] if o.metric == f.metric and o.unit == f.unit
                     and (o.period_start, o.period_end) != (f.period_start, f.period_end) and _one_number(f, o)), None)
        if twin is not None:
            return f"the release gives the same figure for {twin.period_start}..{twin.period_end}"
        share = _year_share(f, xbrl)
        if share is not None:
            return share
        if nci and f.metric == "net_income" and any(
                abs(f.value - c) <= _half(f.value, f.decimals) + 1.5 * _half(c, None)
                for c in consolidated_net_income(usgaap, *_xkey(f)[1:])):
            return "net income including noncontrolling interests (ProfitLoss), not the attributable figure"
        if any(o is not f and _xkey(o) == _xkey(f) and o.basis == "GAAP" and o.segment is None and agrees(o, v)
               for _, _, o in rows):
            return "another release agrees with the derived value"
        t = track[(f.metric, row_label(f))]
        if not t["agree"] or t["disagree"]:
            return (f"the row's record against reported XBRL: {t['agree']} agree, {t['disagree']} disagree "
                    f"({t['excused']} restated)")
        return None

    rejections: dict[tuple, dict] = {}
    trusted: set[tuple[str, int]] = set()
    for name, i, f in cand:
        versions = xbrl[_xkey(f)]
        if any(agrees(f, v) for v in versions) or any(v.source == "xbrl" for v in versions):
            continue
        for v in versions:
            if not distrust(f, v):
                trusted.add((name, i))
                t = track[(f.metric, row_label(f))]
                rejections.setdefault((f.metric, *_xkey(f)[1:], v.value), {
                    "metric": f.metric, "period_start": _xkey(f)[1], "period_end": _xkey(f)[2], "value": v.value,
                    "accession": v.accession, "concept": v.concept,
                    "why": (f"the company's release {f.accession} reports {_m(f.value)} for the quarter; the same row "
                            f"agrees with reported XBRL in {t['agree']} other periods and disagrees in none"),
                    "release": {"file": name, "line": i + 1, "accession": f.accession, "value": f.value,
                                "locator": f.locator, "accepted_at": f.accepted_at.isoformat()},
                    "track_record": dict(t)})
    for name, i, f in cand:
        if (name, i) in trusted:
            continue
        versions = [v for v in xbrl[_xkey(f)] if (f.metric, *_xkey(f)[1:], v.value) not in rejections]
        if not versions or any(agrees(f, v) for v in versions):
            continue
        reported = [v for v in versions if v.source == "xbrl"]
        if excuse(name, f, reported or versions):
            continue
        listed = ", ".join(f"{_m(v.value)} ({v.source} {v.accession})" for v in (reported or versions)[:3])
        if reported:
            dropped(name, i, f, "xbrl_reported", f"reported XBRL for the period: {listed}")
        else:
            reasons = sorted({distrust(f, v) or "" for v in versions} - {""})
            dropped(name, i, f, "xbrl_derived", f"derived XBRL for the period: {listed}; " + "; ".join(reasons))
    # Review L1: once a release outranks a period's derived value, the period has no XBRL left to check its other
    # release figures, in this run or later ones (`vouched`: the decisions recorded before), so they are checked
    # against the figure that outranked it.
    outranking: dict[XKey, list[tuple[float, Optional[int]]]] = defaultdict(list)
    for name, i in trusted:
        outranking[_xkey(facts[name][i])].append((facts[name][i].value, facts[name][i].decimals))
    for metric, start, end, value in vouched:
        outranking[(metric, start or "", end)].append((value, None))
    for name, i, f in live():
        figures = outranking.get(_xkey(f)) if f.basis == "GAAP" and f.segment is None and f.period_start else None
        if (not figures or (name, i) in trusted or not served(f)
                or any(same_figure(f.value, f.decimals, v, d) for v, d in figures)
                or any(agrees(f, x) for x in xbrl.get(_xkey(f), [])
                       if (f.metric, *_xkey(f)[1:], x.value) not in rejections)):
            continue
        dropped(name, i, f, "outranked_period", f"another release outranks the period's derived value with "
                                                 f"{_m(figures[0][0])}, and this figure disagrees with it")
    drop: dict[str, set[int]] = defaultdict(set)
    for name, i in gone:
        drop[name].add(i)
    return {n: sorted(s) for n, s in drop.items()}, records, list(rejections.values())
