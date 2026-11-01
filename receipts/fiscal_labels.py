"""Fiscal labels for a company's 10-Q and 10-K periods: each filing's DEI tag, checked against the company's own
convention. receipts.ingest builds the stored fiscal calendar with it and receipts.press_release labels release
facts with it, so the two always agree.

DEI DocumentFiscalYearFocus / DocumentFiscalPeriodFocus usually names a filing's own period, but filers slip:
  * a 10-Q tagged fp=FY (Dell's FY2024 10-Qs) gives no label at all;
  * a fiscal year repeated a year on (Oracle's Q1 FY2027 10-Q is tagged FY2026, Salesforce's FY2026 10-K FY2025),
    or one tagged a year early.

A company names every fiscal year the same way: fiscal year = anchor year of the year's last day + an offset. The
anchor year is the calendar year of (last day - 7 days), so a 52/53-week year that ends January 1-7 counts with the
December before it. The offset is 0 when a year is named for the calendar year it ends in (NVIDIA, Oracle,
Salesforce, Microsoft) and -1 when it is named for the year it starts in (Home Depot, Target, Lowe's). The majority
offset over the company's 10-Ks (over all of its filings when the 10-Ks tie) is its convention, unless the 10-Qs'
majority says otherwise: two slipped 10-Ks out of three outvote ten 10-Qs (CrowdStrike tagged its FY2024 and FY2025
10-Ks a year early; Kroger tagged two 10-Ks a year late). Then the offset that at least two thirds of all its 10-Qs
and 10-Ks share is the convention, and when none has two thirds every label is dropped; either way a gate warning
names the company. A filing whose DEI year disagrees is relabelled to the convention, and a 10-Q whose DEI quarter
disagrees with its place in the fiscal year loses its label. A label two periods still claim, a Q4 that would not
be a quarter and a fiscal year that does not contain its quarters are dropped: an unlabelled period answers "?",
which is safe.

Quarters are not all 13 weeks. Costco, PepsiCo, Domino's and AutoZone run 12 + 12 + 12 + 16 weeks (17 in a 53-week
year), and their Q4 (Q3 end + 1 .. FY end) runs 112 or 119 days. Kroger runs 16 + 12 + 12 + 12 (13): its first
quarter's 10-Q reports 16 weeks, which the callers never pass here (they pass a 10-Q's own 3-month period only), so
Q1 is labelled from its neighbours the way Q4 is: the fiscal year's first day (or the day after the previous fiscal
year ends) .. the day before Q2 starts, when that runs LONG_QUARTER_DAYS and no filing claims the label
(add_long_first_quarters). A first quarter of ordinary length is never inferred: its own 10-Q always reaches these
rules, so a missing one was rejected or is not on file.

The review that asked for this (.claude/judging/2026-10-31-sp500-phase-a-review.md, finding 1) suggested
year(last day - 182 days) as the anchor. That flips between consecutive years when a 52/53-week year ends around
July 1 (Seagate: 2025-06-27, then 2026-07-03, both named for the year they end in), so the anchor here is
year(last day - 7 days). It holds still for every fiscal-year end from January 8 through January 7.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Optional

QUARTERS = ("Q1", "Q2", "Q3")
YEAR_DAYS = 365.2425
QUARTER_DAYS = 91.31
# A 16- or 17-week fiscal quarter, counting both ends (Costco's Q4, Kroger's Q1): longer than any 13- or 14-week one.
LONG_QUARTER_DAYS = (101, 125)
LONG_Q1_SOURCE = "FY+Q2"  # Label.source of a first quarter labelled from its fiscal year and second quarter
# The messages that mean a rule dropped or withheld a label. A company that emits any of them is listed after its
# ingest batch and its affected labels are kept off its release facts as well (the label gate).
GATE_MARKERS = ("label dropped", "FY and Q4 labels dropped", "not labelled", "claimed for different periods",
                "all labels dropped", "naming convention overridden")


def gate_message(warning: str) -> bool:
    """True for a calendar warning that dropped or withheld a fiscal label (GATE_MARKERS)."""
    return warning.startswith("calendar:") and any(m in warning for m in GATE_MARKERS)


@dataclass(frozen=True)
class FilingPeriod:
    """One 10-Q's or 10-K's own current period and its DEI tag."""
    accession: str
    form: str            # "10-K" or "10-Q"
    order: Any           # sort key, oldest first: acceptance time or filing date
    start: date
    end: date
    fy: Optional[int]    # DEI DocumentFiscalYearFocus
    fp: Optional[str]    # DEI DocumentFiscalPeriodFocus


@dataclass(frozen=True)
class Label:
    fiscal_year: int
    fiscal_period: str   # Q1 Q2 Q3 Q4 FY
    start: date
    end: date
    source: str          # the accession whose period it is, or "Q3+FY" for a Q4


def anchor_year(year_end: date) -> int:
    """The calendar year a fiscal year ending on `year_end` is anchored to (see the module docstring)."""
    return (year_end - timedelta(days=7)).year


def year_end_for(end: date, year_ends: list[date]) -> Optional[date]:
    """About the last day of the fiscal year a period ending `end` belongs to: the nearest known year end moved by
    whole years. A quarter never ends within weeks of a year end, so the rounding is never close."""
    if not year_ends:
        return None
    nearest = min(year_ends, key=lambda e: abs((e - end).days))
    k = math.ceil((end - nearest).days / YEAR_DAYS)
    return nearest + timedelta(days=round(k * YEAR_DAYS))


def _majority(offsets: Counter) -> Optional[int]:
    top = offsets.most_common(2)
    if not top or (len(top) == 2 and top[0][1] == top[1][1]):
        return None
    return top[0][0]


class NamingConflict(Exception):
    """The 10-Ks and the 10-Qs name the fiscal years differently and no offset has two thirds of the filings."""


def convention(periods: list[tuple[FilingPeriod, str]], year_ends: list[date], warnings: list[str]) -> Optional[int]:
    """The company's offset: the majority over its 10-Ks, else over all of its filings; None on a tie.

    When the 10-K majority and the 10-Q majority disagree, the offset at least two thirds of all the filings share
    wins, with a gate warning; when no offset has two thirds, NamingConflict carries the gate warning instead and
    the caller drops every label."""
    tenk = Counter(p.fy - anchor_year(p.end) for p, fp in periods if fp == "FY")
    tenq: Counter = Counter()
    for p, fp in periods:
        if fp != "FY":
            ye = year_end_for(p.end, year_ends)
            if ye is not None:
                tenq[p.fy - anchor_year(ye)] += 1
    every = tenk + tenq
    by_tenk, by_tenq = _majority(tenk), _majority(tenq)
    if by_tenk is None:
        return _majority(every)
    if by_tenq is None or by_tenq == by_tenk:
        return by_tenk
    top, agree = every.most_common(1)[0]
    total = sum(every.values())
    detail = (f"calendar: the 10-Ks name fiscal years with offset {by_tenk:+d} "
              f"({tenk[by_tenk]} of {sum(tenk.values())}) and the 10-Qs with {by_tenq:+d} "
              f"({tenq[by_tenq]} of {sum(tenq.values())})")
    if 3 * agree >= 2 * total:
        warnings.append(f"{detail}; {agree} of {total} filings agree on {top:+d}: naming convention overridden")
        return top
    raise NamingConflict(f"{detail}; no offset has two thirds of the {total} filings: all labels dropped")


def plausible(offset: Optional[int], year_ends: list[date]) -> bool:
    """No company names a fiscal year for the year after it ends (+1), and only a year ending in January or February
    is named for the year it starts in (-1: Home Depot, Target, Lowe's)."""
    if offset is None or offset == 0:
        return True
    return offset == -1 and bool(year_ends) and all(e.month in (1, 2) for e in year_ends)


def label_periods(periods: Iterable[FilingPeriod], warnings: list[str]) -> list[Label]:
    """Q1-Q4 and FY labels from the filings' own periods, checked as the module docstring describes.

    Every valid filing's (fiscal year, period) claim is recorded before the position check, so a filing whose
    quarter is out of place still knocks out a label it shares with another period: a company that moves its year
    end can file a Q1 of the fiscal year it then abandons, and that stale Q1 must not keep the label."""
    valid: list[tuple[FilingPeriod, str]] = []
    for p in sorted(periods, key=lambda p: p.order):
        if p.form == "10-Q" and p.fp == "FY":
            warnings.append(f"calendar: ignored DEI fp=FY on 10-Q {p.accession} for {p.start}..{p.end}")
            continue
        fp = "FY" if p.form == "10-K" else p.fp
        if (not isinstance(p.fy, int) or isinstance(p.fy, bool) or fp not in (*QUARTERS, "FY")
                or not p.end.year - 1 <= p.fy <= p.end.year + 1):
            warnings.append(f"calendar: ignored DEI label fy={p.fy} fp={fp} for {p.form} {p.accession} ending {p.end}")
            continue
        valid.append((p, fp))
    year_ends = sorted({p.end for p, fp in valid if fp == "FY"})
    try:
        offset = convention(valid, year_ends, warnings)
    except NamingConflict as conflict:
        warnings.append(str(conflict))
        return []
    if not plausible(offset, year_ends):
        warnings.append(f"calendar: the DEI fiscal years imply naming offset {offset:+d}, which no company uses for "
                        f"year ends in months {sorted({e.month for e in year_ends})}: all labels dropped")
        return []
    claims: dict[tuple[int, str], dict[tuple[date, date], str]] = {}
    placed: list[tuple[tuple[int, str], date, date, str]] = []
    for p, fp in valid:
        fy = p.fy
        ye = p.end if fp == "FY" else year_end_for(p.end, year_ends)
        if offset is not None and ye is not None and anchor_year(ye) + offset != fy:
            want = anchor_year(ye) + offset
            warnings.append(f"calendar: {p.accession} tags {fp} FY{fy} for {p.start}..{p.end}; by the company's "
                            f"convention that period is {fp} FY{want}: relabelled")
            fy = want
        key = (fy, fp)
        claims.setdefault(key, {}).setdefault((p.start, p.end), p.accession)
        if fp in QUARTERS and ye is not None:
            n = round(((p.end - ye).days + YEAR_DAYS) / QUARTER_DAYS)  # quarters since its fiscal year began
            if n != int(fp[1]):
                warnings.append(f"calendar: {p.accession} tags {fp} for {p.start}..{p.end}, which is quarter {n} "
                                f"of its fiscal year: label dropped")
                continue
        placed.append((key, p.start, p.end, p.accession))
    clashes = {key: periods for key, periods in claims.items() if len(periods) > 1}
    for (fy, fp), periods in sorted(clashes.items()):
        warnings.append(f"calendar: {fp} FY{fy} is claimed for different periods by {', '.join(periods.values())}: "
                        f"label dropped")
    labels: dict[tuple[int, str], Label] = {}
    for key, start, end, accession in placed:
        if key not in clashes:
            labels.setdefault(key, Label(key[0], key[1], start, end, accession))
    add_q4_and_check(labels, warnings)
    add_long_first_quarters(labels, set(claims), warnings)
    return sorted(labels.values(), key=lambda lab: (lab.end, lab.fiscal_period == "FY"))


def is_long(start: date, end: date) -> bool:
    """A 16- or 17-week quarter (LONG_QUARTER_DAYS, both ends counted)."""
    lo, hi = LONG_QUARTER_DAYS
    return lo <= (end - start).days + 1 <= hi


def add_long_first_quarters(labels: dict[tuple[int, str], Label], claimed: set[tuple[int, str]],
                            warnings: list[str]) -> None:
    """Add Q1 = the fiscal year's first day .. the day before Q2 starts, for a labelled Q2 whose Q1 no filing claims,
    when that span is a 16- or 17-week quarter (Kroger). The year's first day is its FY label's start, else the day
    after the previous fiscal year's end (the current year, before its 10-K). Run after add_q4_and_check, so only a
    fiscal year that contains its quarters vouches for one; the Q1 lies inside that year by construction."""
    for (fy, fp), q2 in sorted(labels.items()):
        if fp != "Q2" or (fy, "Q1") in labels or (fy, "Q1") in claimed:
            continue
        if (fy, "FY") in labels:
            first = labels[(fy, "FY")].start
        elif (fy - 1, "FY") in labels:
            first = labels[(fy - 1, "FY")].end + timedelta(days=1)
        else:
            continue
        end = q2.start - timedelta(days=1)
        if is_long(first, end):
            labels[(fy, "Q1")] = Label(fy, "Q1", first, end, LONG_Q1_SOURCE)
            warnings.append(f"calendar: Q1 FY{fy} runs {first}..{end} ({(end - first).days + 1} days), from its "
                            f"fiscal year and Q2: labelled")


def add_q4_and_check(labels: dict[tuple[int, str], Label], warnings: list[str]) -> None:
    """Add Q4 = Q3 end + 1 .. FY end where both exist and that is a fiscal quarter, then drop every fiscal year that
    does not contain its quarters in order, with its Q4 and the quarters that are out of place. A fiscal Q4 runs 12
    to 17 weeks (Costco's is 16 or 17)."""
    for (fy, fp), year in sorted(labels.items()):
        if fp == "FY" and (fy, "Q3") in labels:
            start = labels[(fy, "Q3")].end + timedelta(days=1)
            if 80 <= (year.end - start).days + 1 <= 125:
                labels[(fy, "Q4")] = Label(fy, "Q4", start, year.end, "Q3+FY")
            else:
                warnings.append(f"calendar: Q4 FY{fy} would run {start}..{year.end}, which is not a quarter: "
                                f"not labelled")
    for (fy, fp), year in sorted(labels.items()):
        if fp != "FY":
            continue
        outside = [q for q in ("Q1", "Q2", "Q3", "Q4") if (fy, q) in labels and not (
            year.start <= labels[(fy, q)].start and labels[(fy, q)].end <= year.end
            and round((labels[(fy, q)].end - year.start).days / QUARTER_DAYS) == int(q[1]))]
        if outside:
            gone = sorted({"FY", "Q4", *outside} & {fp for f, fp in labels if f == fy}, key=("Q1", "Q2", "Q3", "Q4", "FY").index)
            warnings.append(f"calendar: FY{fy} {year.start}..{year.end} does not contain its "
                            f"{', '.join(outside)}: FY and Q4 labels dropped ({', '.join(gone)})")
            for q in gone:
                labels.pop((fy, q), None)
