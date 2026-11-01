"""Fiscal period resolution: a claim's period words -> (fiscal_year, fiscal_period, period_end).

The model only copies the draft's period phrase into Claim.period_text. Code resolves it on the
company's own fiscal calendar (EvidenceStore.fiscal_calendar), so "second quarter of fiscal 2027"
lands on NVIDIA's quarter that ended July 26, 2026, and "third quarter of 2026" lands on Meta's
quarter that ended September 30, 2026. Nothing is guessed: a phrase that can be read two ways
resolves to None, and the verifier turns None into "?" (ambiguity never yields "wrong").

Phrasings (case-insensitive):
  explicit end date   "second quarter ended July 26, 2026", "three months ended 2026-03-31",
                      "fiscal year ended January 25, 2026"
  fiscal quarter      "Q2 fiscal 2027", "Q2 FY27", "second-quarter fiscal 2027", "fiscal Q2 2027"
  fiscal year         "full year fiscal 2027", "fiscal 2026", "FY2026"
  calendar phrasing   "third quarter of 2026", "Q3 2026", "3Q26", "Q3'26", "2026": only for
                      companies whose fiscal year is the calendar year; ambiguous otherwise
  relative            "the quarter", "", "second quarter", "full year": filled from the context
  month-named quarter "June quarter", "the September quarter 2026", "December-quarter results"
                      (Apple's and Microsoft's own phrasing): the company's fiscal quarter that ends in
                      that month. The year comes from the phrase, else the context (its date or year),
                      else the latest such quarter that has ended by as_of (or today); that quarter may
                      not be reported yet (period_end None), and an earlier year is never guessed.
  comparison base     "a year ago", "previous quarter": for a level claim, the context period
                      shifted back; for a percent change, the context period itself
Unsupported, so None: year-to-date spans ("six months ended ...", "first half").

A period that is well defined but not in the calendar yet (a quarter nobody has reported) resolves
with period_end None, so the verifier can say "no evidence" instead of "ambiguous".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple, Optional

from receipts.types import EvidenceStore

QUARTERS = ("Q1", "Q2", "Q3", "Q4")


class ResolvedPeriod(NamedTuple):
    fiscal_year: int
    fiscal_period: str          # "Q1".."Q4" or "FY"
    period_end: Optional[date]  # None: well defined, but not in the company's calendar yet


class CalEntry(NamedTuple):
    fiscal_year: int
    fiscal_period: str
    start: Optional[date]
    end: date


# ---------------------------------------------------------------------------
# Fiscal calendar
# ---------------------------------------------------------------------------
def _as_date(x: Any) -> Optional[date]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def calendar(store: EvidenceStore, cik: int) -> list[CalEntry]:
    """The store's fiscal calendar, with FY and Q4 entries synthesized when only their parts are known."""
    entries: dict[tuple[int, str], CalEntry] = {}
    for row in store.fiscal_calendar(cik) or []:
        fy, fp, start, end = row
        end_d = _as_date(end)
        if end_d is None:
            continue
        key = (int(fy), str(fp).upper())
        entries.setdefault(key, CalEntry(key[0], key[1], _as_date(start), end_d))
    for fy in {k[0] for k in entries}:
        q = {fp: entries.get((fy, fp)) for fp in QUARTERS}
        if (fy, "FY") not in entries and q["Q4"] is not None:
            entries[(fy, "FY")] = CalEntry(fy, "FY", q["Q1"].start if q["Q1"] else None, q["Q4"].end)
        full = entries.get((fy, "FY"))
        if q["Q4"] is None and full is not None and q["Q3"] is not None and full.end > q["Q3"].end:
            entries[(fy, "Q4")] = CalEntry(fy, "Q4", q["Q3"].end + timedelta(days=1), full.end)
    return sorted(entries.values(), key=lambda e: (e.end, e.fiscal_period == "FY"))


def lookup(cal: list[CalEntry], fiscal_year: int, fiscal_period: str) -> Optional[CalEntry]:
    return next((e for e in cal if e.fiscal_year == fiscal_year and e.fiscal_period == fiscal_period), None)


def calendar_quarter(end: date) -> Optional[int]:
    """1..4 when `end` is within a week of a calendar quarter end (52/53-week years drift a few days)."""
    for q, (m, d) in enumerate(((3, 31), (6, 30), (9, 30), (12, 31)), 1):
        if abs((end - date(end.year, m, d)).days) <= 7:
            return q
    if abs((end - date(end.year - 1, 12, 31)).days) <= 7:
        return 4
    return None


def is_calendar_fiscal_year(cal: list[CalEntry]) -> Optional[bool]:
    """True when Q1..Q4 end with the calendar quarters (Meta, AMD); None when the calendar is empty."""
    if not cal:
        return None
    for e in cal:
        want = 4 if e.fiscal_period == "FY" else int(e.fiscal_period[1])
        if calendar_quarter(e.end) != want:
            return False
        if not (e.end.year == e.fiscal_year or (e.end.month == 1 and e.end.year == e.fiscal_year + 1)):
            return False
    return True


# ---------------------------------------------------------------------------
# Parsing period words
# ---------------------------------------------------------------------------
_TRANS = str.maketrans({"’": "'", "‘": "'", "–": "-", "—": "-", "―": "-", "‑": "-",
                        " ": " "})


def _norm(text: Optional[str]) -> str:
    """Lowercase with quotes and dashes unified. Length-preserving, so match offsets index the original."""
    return "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in (text or "").translate(_TRANS))


_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
        r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), 1)}
_DATE_RE = re.compile(rf"\b(?P<mon>{_MON})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<yr>\d{{4}})\b"
                      r"|\b(?P<iso>\d{4}-\d{2}-\d{2})\b")
_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
_ORD_Q_RE = re.compile(r"\b(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:fiscal[\s-]+)?(?:quarter|qtr)\b")
_Q_NUM_RE = re.compile(r"(?<![a-z0-9])(?P<f>f?)q(?P<q>[1-4])(?![0-9])"
                       r"(?:\s*(?:fy|fiscal)?\s*'?\s*(?P<yr>\d{4}|\d{2})(?![0-9]))?")
_NQ_RE = re.compile(r"(?<![a-z0-9])(?P<q>[1-4])q(?:\s*(?:fy)?\s*'?\s*(?P<yr>\d{4}|\d{2}))?(?![0-9a-z])")
_FY_RE = re.compile(r"\bfiscal\s+(?:year\s+)?(?P<y4>\d{4})\b|(?<![a-z])fy\s*'?\s*(?P<yfy>\d{4}|\d{2})(?![0-9])")
_YEAR_RE = re.compile(r"(?<![0-9])(?P<y>(?:19|20)\d{2})(?![0-9])")
_FISCAL_WORD_RE = re.compile(r"\bfiscal\b|(?<![a-z])fy(?![a-z])")
_YTD_RE = re.compile(r"\b(?:six|nine|6|9)[\s-]+months?\b|\b(?:first|second)\s+half\b|(?<![a-z0-9])(?:h[12]|[12]h)"
                     r"(?![a-z0-9])|\byear[\s-]to[\s-]date\b|\bytd\b|\b(?:24|26|28|36|39|40)[\s-]+weeks?\b")
_WEEKS_RE = re.compile(r"\b(?P<weeks>\d+)[\s-]+weeks?\b")
_ANNUAL_RE = re.compile(r"\bfull[\s-]+(?:fiscal[\s-]+)?year\b|\btwelve[\s-]+months\b|\b12[\s-]+months\b"
                        r"|\b5[23][\s-]+weeks?\b|\bannual\b|\byear\s+ended\b"
                        r"|\b(?:the|this|that)\s+(?:fiscal\s+)?year\b(?![\s-]+(?:ago|earlier))")
_FISCAL_YEAR_RE = re.compile(r"\bfiscal\s+year\b")
_QUARTER_RE = re.compile(r"\bquarter(?:ly)?\b|\bthree[\s-]+months\b|\b3[\s-]+months\b|\b1[2-7][\s-]+weeks?\b|\bqtr\b")
_SHIFT_YEAR_RE = re.compile(r"\ba\s+year\s+(?:ago|earlier|before)\b|\byear[\s-]+(?:ago|earlier)\b|\bprior[\s-]+year\b"
                            r"|\bprevious\s+year\b|\bsame\s+(?:quarter|period)\s+(?:of\s+)?(?:a|the\s+prior|the\s+previous"
                            r"|last)\s+year\b")
_SHIFT_QUARTER_RE = re.compile(r"\b(?:previous|prior|preceding)\s+quarter\b|\bsequential(?:ly)?\b"
                               r"|\bquarter[\s-]+over[\s-]+quarter\b")
_SHIFT_AMBIGUOUS_RE = re.compile(r"\blast\s+(?:quarter|year)\b")  # "last quarter" may mean the latest one
# "June quarter", "June-quarter", "the September quarter", "quarter ended in June" (a month with no day: dates are
# matched by _DATE_RE first and blanked out of the text this runs on)
_MONTH_Q_RE = re.compile(rf"\b(?P<mon>{_MON})\.?[\s-]+quarters?\b"
                         rf"|\bquarters?\s+end(?:ed|ing)?\s+(?:in\s+)?(?P<mon2>{_MON})\b(?!\.?\s+\d{{1,2}}\b)")


@dataclass(frozen=True)
class ParsedPeriod:
    kind: Optional[str] = None      # "quarter" | "annual" | "ytd" | "conflict" | None
    quarter: Optional[int] = None
    year: Optional[int] = None
    fiscal: bool = False            # "fiscal" or "FY" was written
    end: Optional[date] = None      # an explicit end date
    shift: Optional[str] = None     # "year" | "quarter" | "ambiguous": a comparison-base phrase
    month: Optional[int] = None     # "June quarter": the quarter that ends in this calendar month
    weeks: Optional[int] = None     # an explicit duration, checked against inclusive calendar dates


def _year(s: str) -> int:
    return int(s) if len(s) == 4 else 2000 + int(s)


def _date_of(m: re.Match[str]) -> Optional[date]:
    try:
        if m.group("iso"):
            return date.fromisoformat(m.group("iso"))
        return date(int(m.group("yr")), _MONTHS[m.group("mon")[:3]], int(m.group("day")))
    except ValueError:
        return None


def parse(text: Optional[str]) -> ParsedPeriod:
    s = _norm(text)
    if not s.strip():
        return ParsedPeriod()
    end, rest = None, s
    dm = _DATE_RE.search(s)
    if dm:
        end = _date_of(dm)
        rest = s[:dm.start()] + " " * (dm.end() - dm.start()) + s[dm.end():]
    quarter = year = None
    fiscal = bool(_FISCAL_WORD_RE.search(rest))
    mq = _MONTH_Q_RE.search(rest)
    month = _MONTHS[(mq.group("mon") or mq.group("mon2"))[:3]] if mq else None
    m = _ORD_Q_RE.search(rest)
    if m:
        quarter = _ORD[m.group("ord")]
    for rx in (_Q_NUM_RE, _NQ_RE):
        m = rx.search(rest)
        if m:
            quarter = quarter or int(m.group("q"))
            if m.group("yr") and year is None:
                year = _year(m.group("yr"))
            if "f" in m.groupdict() and m.group("f"):
                fiscal = True
    fm = _FY_RE.search(rest)
    if fm:
        year, fiscal = _year(fm.group("y4") or fm.group("yfy")), True
    if year is None:
        ym = _YEAR_RE.search(rest)
        if ym:
            year = int(ym.group("y"))
    quarterish = quarter is not None or bool(_QUARTER_RE.search(rest))
    annual = bool(_ANNUAL_RE.search(rest)) or (not quarterish and bool(_FISCAL_YEAR_RE.search(rest)))
    if _YTD_RE.search(s):
        kind: Optional[str] = "ytd"
    elif quarterish and annual:
        kind = "conflict"  # "fourth quarter and full year"
    elif quarterish:
        kind = "quarter"
    elif annual or year is not None:
        kind = "annual"
    else:
        kind = None
    shift = ("year" if _SHIFT_YEAR_RE.search(s) else "quarter" if _SHIFT_QUARTER_RE.search(s)
             else "ambiguous" if _SHIFT_AMBIGUOUS_RE.search(s) else None)
    counts = {int(wm.group("weeks")) for wm in _WEEKS_RE.finditer(s)}
    if len(counts) > 1:
        kind = "conflict"
    return ParsedPeriod(kind, quarter, year, fiscal, end, shift, month, min(counts) if counts else None)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _entry_period(e: CalEntry) -> ResolvedPeriod:
    return ResolvedPeriod(e.fiscal_year, e.fiscal_period, e.end)


def _at(cal: list[CalEntry], fiscal_year: int, fiscal_period: str) -> ResolvedPeriod:
    e = lookup(cal, fiscal_year, fiscal_period)
    return ResolvedPeriod(fiscal_year, fiscal_period, e.end if e else None)


def _by_end(cal: list[CalEntry], end: date, *, kind: Optional[str], quarter: Optional[int]) -> Optional[ResolvedPeriod]:
    cands = [e for e in cal if e.end == end]
    if kind == "quarter":
        cands = [e for e in cands if e.fiscal_period in QUARTERS]
    elif kind == "annual":
        cands = [e for e in cands if e.fiscal_period == "FY"]
    if len(cands) != 1:  # unknown date, or Q4 and FY end together and nothing says which
        return None
    e = cands[0]
    if quarter is not None and e.fiscal_period != f"Q{quarter}" and calendar_quarter(end) != quarter:
        return None  # "first quarter ended July 26, 2026" contradicts NVIDIA's calendar
    return _entry_period(e)


def _by_year(cal: list[CalEntry], p: ParsedPeriod) -> Optional[ResolvedPeriod]:
    if not p.fiscal and is_calendar_fiscal_year(cal) is not True:
        return None  # "third quarter of 2026" on a non-calendar fiscal year reads two ways
    assert p.year is not None
    return _at(cal, p.year, f"Q{p.quarter}" if p.quarter else "FY")


def _merge(p: ParsedPeriod, base: ResolvedPeriod, cal: list[CalEntry], hint: Optional[str]) -> Optional[ResolvedPeriod]:
    """Claim words without a year, filled in from the context period."""
    if p.kind == "annual":
        fp = "FY"
    elif p.quarter is not None:
        fp = f"Q{p.quarter}"
    elif p.kind == "quarter":
        if base.fiscal_period == "FY":
            return None  # "the quarter" against a full-year context
        fp = base.fiscal_period
    elif hint == "annual":
        fp = "FY"
    elif hint == "quarter" and base.fiscal_period == "FY":
        return None
    else:
        fp = base.fiscal_period
    return _at(cal, base.fiscal_year, fp)


def shift(rp: ResolvedPeriod, cal: list[CalEntry], *, years: int = 0, quarters: int = 0) -> Optional[ResolvedPeriod]:
    """The period `years` fiscal years and `quarters` fiscal quarters away (negative: earlier)."""
    fy, fp = rp.fiscal_year, rp.fiscal_period
    if quarters:
        if fp == "FY":
            return None
        n = int(fp[1]) - 1 + quarters
        fy, fp = fy + n // 4, f"Q{n % 4 + 1}"
    return _at(cal, fy + years, fp)


def _hint(period_type: Optional[str]) -> Optional[str]:
    return {"quarter": "quarter", "annual": "annual", "year_to_date": "ytd"}.get(period_type or "")


def _check_weeks(rp: Optional[ResolvedPeriod], cal: list[CalEntry], weeks: Optional[int]) -> Optional[ResolvedPeriod]:
    if rp is None or weeks is None:
        return rp
    entry = lookup(cal, rp.fiscal_year, rp.fiscal_period)
    if entry is None or entry.start is None or (entry.end - entry.start).days + 1 != weeks * 7:
        return None
    return rp


def _nominal(end: date) -> date:
    """The month a quarter end is named for. 52/53-week calendars can end a quarter in the first week of the next month
    (Apple's June quarter of fiscal 2023 ended July 1), so such an end stands for the last day of the previous month."""
    return end - timedelta(days=end.day) if end.day <= 7 else end


def _last_day(year: int, month: int) -> date:
    return (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))


def _by_month(cal: list[CalEntry], p: ParsedPeriod, context: Optional[str],
              as_of: Optional[date]) -> Optional[ResolvedPeriod]:
    """'June quarter': the company's fiscal quarter that ends in that month. Which year: the phrase's own ("June quarter
    2026" is a calendar year, "June quarter of fiscal 2026" a fiscal one), else the context's (its date, or its year),
    else the latest one that has ended by as_of (today without one). A year the anchor rules out is never guessed:
    when the latest such quarter isn't reported yet, it resolves with period_end None ("no evidence yet")."""
    assert p.month is not None
    pattern = {(e.fiscal_period, e.fiscal_year - _nominal(e.end).year) for e in cal
               if e.fiscal_period in QUARTERS and _nominal(e.end).month == p.month}
    if len(pattern) != 1:
        return None  # no quarter of this company ends in that month (NVIDIA has no June quarter), or it moved
    fp, offset = pattern.pop()
    if p.year is not None:
        return _at(cal, p.year if p.fiscal else p.year + offset, fp)
    c = parse(context) if context else None
    if (c is not None and c.weeks is not None and (c.end is None or any(e.end == c.end for e in cal))
            and resolve_in_calendar(context, cal, as_of=as_of) is None):
        return None  # a context's unsupported duration cannot anchor a month-named quarter
    if c is not None and c.end is not None:
        anchor = c.end + timedelta(days=7)  # another company's quarter may end a few days later in the same month
    elif c is not None and c.year is not None and c.kind not in ("ytd", "conflict"):
        return _at(cal, c.year if c.fiscal else c.year + offset, fp)
    else:
        anchor = _as_date(as_of) or date.today()

    def end_in(year: int) -> date:
        e = lookup(cal, year + offset, fp)
        return e.end if e is not None else _last_day(year, p.month)

    year = anchor.year
    while end_in(year) > anchor:
        year -= 1
    return _at(cal, year + offset, fp)


def _resolve_in_calendar(period_text: Optional[str], cal: list[CalEntry], *, context: Optional[str] = None,
                         period_type: Optional[str] = None, comparison: Optional[str] = None,
                         as_of: Any = None) -> Optional[ResolvedPeriod]:
    p = parse(period_text)
    hint = _hint(period_type)
    level = comparison in (None, "none")  # a comparison-base phrase moves a level claim, not a percent change
    if p.kind in ("ytd", "conflict") or hint == "ytd":
        return None
    if p.end is not None:
        return _by_end(cal, p.end, kind=p.kind or hint, quarter=p.quarter)
    if p.month is not None:
        rp = _by_month(cal, p, context, _as_date(as_of))
        if rp is not None and level and p.shift in ("year", "quarter"):
            rp = shift(rp, cal, years=-1 if p.shift == "year" else 0, quarters=-1 if p.shift == "quarter" else 0)
        return rp
    if p.year is not None and (p.kind != "quarter" or p.quarter is not None):
        return _by_year(cal, p)
    if level and p.shift == "ambiguous":
        return None
    c = parse(context) if context else None
    if c is None or c.kind in ("ytd", "conflict") or (c.end is None and c.year is None and c.month is None):
        return None
    if c.month is not None:
        base = _by_month(cal, c, None, _as_date(as_of))
    elif c.end is not None:
        base = _by_end(cal, c.end, kind=c.kind, quarter=c.quarter)
    elif c.kind == "quarter" and c.quarter is None:
        return None
    else:
        base = _by_year(cal, c)
    base = _check_weeks(base, cal, c.weeks)
    if base is None:
        return None
    rp = _merge(p, base, cal, hint)
    if rp is not None and level and p.shift in ("year", "quarter"):
        rp = shift(rp, cal, years=-1 if p.shift == "year" else 0, quarters=-1 if p.shift == "quarter" else 0)
    return rp


def resolve_in_calendar(period_text: Optional[str], cal: list[CalEntry], *, context: Optional[str] = None,
                        period_type: Optional[str] = None, comparison: Optional[str] = None,
                        as_of: Any = None) -> Optional[ResolvedPeriod]:
    rp = _resolve_in_calendar(period_text, cal, context=context, period_type=period_type,
                              comparison=comparison, as_of=as_of)
    return _check_weeks(rp, cal, parse(period_text).weeks)


def unresolved_reason(period_text: Optional[str], *, context: Optional[str] = None,
                      period_type: Optional[str] = None) -> Optional[str]:
    """Explain a rejected week duration or YTD hint; keep other ambiguity explanations unchanged."""
    p = parse(period_text)
    c = parse(context)
    if (p.weeks is not None and p.kind == "ytd") or (period_type == "year_to_date" and p.kind != "ytd") or (
            p.weeks is None and c.weeks is not None and c.kind == "ytd"):
        return "NoCap does not serve year-to-date figures, so this period cannot be checked against a single quarter."
    if p.kind == "conflict" and p.weeks is not None:
        return "The period wording gives conflicting durations, so NoCap cannot identify one fiscal period."
    weeks = p.weeks if p.weeks is not None else c.weeks
    if weeks is not None:
        return (f"Couldn't match the stated {weeks}-week duration and period to a fiscal period with known "
                "start and end dates.")
    return None


def resolve_period(period_text: Optional[str], store: EvidenceStore, cik: int, *, context: Optional[str] = None,
                   period_type: Optional[str] = None, comparison: Optional[str] = None,
                   as_of: Any = None) -> Optional[ResolvedPeriod]:
    """Claim.period_text (plus an optional document context such as "NVIDIA Q2 FY2027") resolved on the
    company's fiscal calendar to (fiscal_year, fiscal_period, period_end), or None when ambiguous.

    period_type ("quarter" / "annual") breaks the Q4-versus-full-year tie when both end on the same
    date. comparison ("none", "QoQ", "YoY", "sequential") says whether "a year ago" moves the period
    (a level figure) or describes the comparison base (a percent change). as_of anchors a month-named quarter
    with no year ("the June quarter") to the latest one that had ended by then.
    """
    return resolve_in_calendar(period_text, calendar(store, cik), context=context, period_type=period_type,
                               comparison=comparison, as_of=as_of)


resolve = resolve_period


def nearest(cal: list[CalEntry], rp: ResolvedPeriod, *, max_days: int = 7) -> Optional[ResolvedPeriod]:
    """The same kind of period (quarter or year) ending within `max_days` of rp, on another calendar."""
    if rp.period_end is None:
        return None
    want_fy = rp.fiscal_period == "FY"
    cands = [e for e in cal if (e.fiscal_period == "FY") == want_fy and abs((e.end - rp.period_end).days) <= max_days]
    if not cands:
        return None
    return _entry_period(min(cands, key=lambda e: abs((e.end - rp.period_end).days)))


def label(rp: ResolvedPeriod, cal: list[CalEntry]) -> str:
    """'Q2 FY2027 (quarter ended Jul 26, 2026)' or, on a calendar fiscal year, 'Q3 2026 (quarter ended ...)'."""
    cal_fy = is_calendar_fiscal_year(cal) is True
    if rp.fiscal_period == "FY":
        text, word = (f"full year {rp.fiscal_year}" if cal_fy else f"fiscal {rp.fiscal_year}"), "year"
    else:
        text, word = (f"{rp.fiscal_period} {rp.fiscal_year}" if cal_fy else f"{rp.fiscal_period} FY{rp.fiscal_year}"), "quarter"
    if rp.period_end is not None:
        e = rp.period_end
        text += f" ({word} ended {e:%b} {e.day}, {e.year})"
    return text


# ---------------------------------------------------------------------------
# Finding a period phrase in running text (used to infer a document context line)
# ---------------------------------------------------------------------------
_PHRASE_RES = (
    re.compile(rf"\b{_MON}\.?[\s-]+quarter(?:\s+(?:of\s+)?(?:fiscal\s+)?\d{{4}})?\b"),
    re.compile(rf"\b(?:(?:first|second|third|fourth)[\s-]+(?:fiscal[\s-]+)?quarter|three\s+months|fiscal\s+year|quarter"
               rf"|year|twelve\s+months)\s+ended\s+{_MON}\.?\s+\d{{1,2}},?\s+\d{{4}}"),
    re.compile(r"\b(?:first|second|third|fourth)[\s-]+(?:fiscal[\s-]+)?quarter\s+(?:of\s+)?(?:fiscal\s+(?:year\s+)?)?"
               r"\d{4}\b"),
    re.compile(r"(?<![a-z0-9])q[1-4]\s*(?:(?:fy|fiscal)\s*'?\s*\d{2,4}|'?\s*\d{4}|'\d{2})(?![0-9])"),
    re.compile(r"\bfull[\s-]+year\s+(?:fiscal\s+)?\d{4}\b"),
    re.compile(r"\bfiscal\s+(?:year\s+)?\d{4}\b"),
    re.compile(r"(?<![a-z])fy\s*'?\s*\d{2,4}(?![0-9])"),
)


def find_period_phrase(text: str) -> Optional[str]:
    """The first explicit period phrase in `text`, verbatim, e.g. 'second quarter ended July 26, 2026'."""
    s = _norm(text)
    best: Optional[re.Match[str]] = None
    for rx in _PHRASE_RES:
        m = rx.search(s)
        if m and (best is None or m.start() < best.start()):
            best = m
    return text[best.start():best.end()] if best else None
