"""Fiscal period resolution. Calendars use the real period dates from SEC companyfacts:
NVIDIA's fiscal year ends late January, Microsoft's on June 30, Meta's on December 31."""
from datetime import date

import pytest

from receipts import periods
from receipts.periods import ResolvedPeriod

NVDA, MSFT, META = 1045810, 789019, 1326801

CALENDARS = {
    NVDA: [
        (2026, "Q1", date(2025, 1, 27), date(2025, 4, 27)),
        (2026, "Q2", date(2025, 4, 28), date(2025, 7, 27)),
        (2026, "Q3", date(2025, 7, 28), date(2025, 10, 26)),
        (2026, "Q4", date(2025, 10, 27), date(2026, 1, 25)),
        (2026, "FY", date(2025, 1, 27), date(2026, 1, 25)),
        (2027, "Q1", date(2026, 1, 26), date(2026, 4, 26)),
        (2027, "Q2", date(2026, 4, 27), date(2026, 7, 26)),
    ],
    MSFT: [
        (2025, "Q3", date(2025, 1, 1), date(2025, 3, 31)),
        (2025, "Q4", date(2025, 4, 1), date(2025, 6, 30)),
        (2025, "FY", date(2024, 7, 1), date(2025, 6, 30)),
        (2026, "Q1", date(2025, 7, 1), date(2025, 9, 30)),
        (2026, "Q2", date(2025, 10, 1), date(2025, 12, 31)),
        (2026, "Q3", date(2026, 1, 1), date(2026, 3, 31)),
    ],
    META: [
        (2025, "Q1", date(2025, 1, 1), date(2025, 3, 31)),
        (2025, "Q2", date(2025, 4, 1), date(2025, 6, 30)),
        (2025, "Q3", date(2025, 7, 1), date(2025, 9, 30)),
        (2025, "Q4", date(2025, 10, 1), date(2025, 12, 31)),
        (2025, "FY", date(2025, 1, 1), date(2025, 12, 31)),
        (2026, "Q1", date(2026, 1, 1), date(2026, 3, 31)),
        (2026, "Q2", date(2026, 4, 1), date(2026, 6, 30)),
        (2026, "Q3", date(2026, 7, 1), date(2026, 9, 30)),
    ],
}


class CalendarStore:
    """Only fiscal_calendar is needed for period resolution."""

    def fiscal_calendar(self, cik):
        return list(CALENDARS.get(cik, []))


STORE = CalendarStore()
NVDA_Q2_FY27 = ResolvedPeriod(2027, "Q2", date(2026, 7, 26))
META_Q3_2026 = ResolvedPeriod(2026, "Q3", date(2026, 9, 30))
MSFT_Q3_FY26 = ResolvedPeriod(2026, "Q3", date(2026, 3, 31))


def resolve(text, cik=NVDA, **kw):
    return periods.resolve_period(text, STORE, cik, **kw)


# --- the phrasings the spec names ---------------------------------------------
@pytest.mark.parametrize("text", [
    "second quarter ended July 26, 2026",
    "Second-quarter ended Jul. 26, 2026",
    "three months ended 2026-07-26",
    "Q2 fiscal 2027",
    "Q2 FY27",
    "Q2 FY2027",
    "second quarter of fiscal 2027",
    "second-quarter fiscal 2027",
    "fiscal Q2 2027",
])
def test_nvda_q2_fy2027_phrasings(text):
    assert resolve(text) == NVDA_Q2_FY27


def test_the_quarter_uses_the_context():
    assert resolve("the quarter", context="NVIDIA Q2 FY2027") == NVDA_Q2_FY27
    assert resolve("", context="NVIDIA, second quarter ended July 26, 2026") == NVDA_Q2_FY27


def test_the_quarter_without_context_is_ambiguous():
    assert resolve("the quarter") is None
    assert resolve("") is None


def test_full_year_fiscal():
    assert resolve("full year fiscal 2026") == ResolvedPeriod(2026, "FY", date(2026, 1, 25))
    assert resolve("fiscal 2026") == ResolvedPeriod(2026, "FY", date(2026, 1, 25))
    # well defined but not reported yet: no end date, so the verifier says "no evidence", not "ambiguous"
    assert resolve("full year fiscal 2027") == ResolvedPeriod(2027, "FY", None)


def test_calendar_year_phrasing_on_a_calendar_fiscal_year():
    for text in ("third quarter of 2026", "Q3 2026", "3Q26", "Q3'26", "Q3’26", "third-quarter 2026"):
        assert resolve(text, META) == META_Q3_2026, text
    assert resolve("2025", META) == ResolvedPeriod(2025, "FY", date(2025, 12, 31))


def test_calendar_year_phrasing_is_ambiguous_on_other_fiscal_years():
    assert resolve("third quarter of 2026", NVDA) is None
    assert resolve("Q2 2027", NVDA) is None
    assert resolve("third quarter of 2026", MSFT) is None
    assert resolve("2026", NVDA) is None


# --- explicit dates ---------------------------------------------------------------
def test_explicit_end_date_on_a_june_fiscal_year():
    assert resolve("quarter ended March 31, 2026", MSFT) == MSFT_Q3_FY26
    assert resolve("third quarter of fiscal 2026", MSFT) == MSFT_Q3_FY26


def test_calendar_ordinal_with_explicit_date_is_accepted():
    # "first quarter" is calendar Q1 2026, which is Microsoft's fiscal Q3: the date settles it
    assert resolve("first quarter ended March 31, 2026", MSFT) == MSFT_Q3_FY26


def test_ordinal_that_contradicts_the_date_is_ambiguous():
    assert resolve("first quarter ended July 26, 2026", NVDA) is None


def test_unknown_end_date_is_ambiguous():
    assert resolve("quarter ended July 27, 2026", NVDA) is None


def test_q4_and_full_year_share_an_end_date():
    assert resolve("fourth quarter of fiscal 2026") == ResolvedPeriod(2026, "Q4", date(2026, 1, 25))
    assert resolve("quarter ended January 25, 2026") == ResolvedPeriod(2026, "Q4", date(2026, 1, 25))
    assert resolve("fiscal year ended January 25, 2026") == ResolvedPeriod(2026, "FY", date(2026, 1, 25))
    assert resolve("January 25, 2026") is None  # either one
    assert resolve("January 25, 2026", period_type="annual") == ResolvedPeriod(2026, "FY", date(2026, 1, 25))


def test_year_to_date_and_mixed_spans_are_unsupported():
    assert resolve("six months ended July 26, 2026") is None
    assert resolve("first half of fiscal 2027") is None
    assert resolve("fourth quarter and full year fiscal 2026") is None


# --- context merging and comparison bases ------------------------------------------
def test_quarter_without_year_takes_the_context_year():
    assert resolve("second quarter", context="NVIDIA fiscal 2027") == NVDA_Q2_FY27
    assert resolve("end of the second quarter", context="NVIDIA Q2 FY2027") == NVDA_Q2_FY27
    assert resolve("first quarter", context="NVIDIA, second quarter ended July 26, 2026") == \
        ResolvedPeriod(2027, "Q1", date(2026, 4, 26))
    assert resolve("second quarter") is None


def test_full_year_without_year_takes_the_context_fiscal_year():
    assert resolve("full year", context="NVIDIA Q2 FY2027") == ResolvedPeriod(2027, "FY", None)


def test_year_ago_moves_a_level_claim_but_not_a_growth_claim():
    year_ago = ResolvedPeriod(2026, "Q2", date(2025, 7, 27))
    assert resolve("a year ago", context="NVIDIA Q2 FY2027", comparison="none") == year_ago
    assert resolve("the year-ago quarter", context="NVIDIA Q2 FY2027") == year_ago
    assert resolve("a year ago", context="NVIDIA Q2 FY2027", comparison="YoY") == NVDA_Q2_FY27


def test_previous_quarter_crosses_the_fiscal_year():
    assert resolve("previous quarter", context="NVIDIA Q2 FY2027") == ResolvedPeriod(2027, "Q1", date(2026, 4, 26))
    assert resolve("prior quarter", context="NVIDIA Q1 FY2027") == ResolvedPeriod(2026, "Q4", date(2026, 1, 25))
    assert resolve("last quarter", context="NVIDIA Q2 FY2027") is None  # the latest one, or the one before?


# --- calendar helpers -------------------------------------------------------------------
def test_calendar_fiscal_year_detection():
    assert periods.is_calendar_fiscal_year(periods.calendar(STORE, META)) is True
    assert periods.is_calendar_fiscal_year(periods.calendar(STORE, MSFT)) is False
    assert periods.is_calendar_fiscal_year(periods.calendar(STORE, NVDA)) is False
    assert periods.is_calendar_fiscal_year([]) is None


def test_shift_and_synthesized_full_year():
    cal = periods.calendar(STORE, META)
    q1 = ResolvedPeriod(2026, "Q1", date(2026, 3, 31))
    assert periods.shift(q1, cal, quarters=-1) == ResolvedPeriod(2025, "Q4", date(2025, 12, 31))
    assert periods.shift(META_Q3_2026, cal, years=-1) == ResolvedPeriod(2025, "Q3", date(2025, 9, 30))
    assert periods.shift(ResolvedPeriod(2025, "FY", date(2025, 12, 31)), cal, quarters=-1) is None
    # NVIDIA's calendar has Q1..Q4 FY2026 but suppose the store left out the FY row: it is synthesized
    no_fy = [row for row in CALENDARS[NVDA] if row[1] != "FY"]
    cal = periods.calendar(type("S", (), {"fiscal_calendar": lambda self, cik: no_fy})(), NVDA)
    assert periods.lookup(cal, 2026, "FY").end == date(2026, 1, 25)


def test_labels():
    assert periods.label(NVDA_Q2_FY27, periods.calendar(STORE, NVDA)) == "Q2 FY2027 (quarter ended Jul 26, 2026)"
    assert periods.label(META_Q3_2026, periods.calendar(STORE, META)) == "Q3 2026 (quarter ended Sep 30, 2026)"
    assert periods.label(ResolvedPeriod(2027, "FY", None), periods.calendar(STORE, NVDA)) == "fiscal 2027"


def test_find_period_phrase_for_the_context_line():
    gold_first = ("SANTA CLARA, Calif.—Aug. 26, 2026― NVIDIA (NASDAQ: NVDA) today reported revenue for the "
                  "second quarter ended July 26, 2026, of $96.2 billion, up 18% from the previous quarter.")
    assert periods.find_period_phrase(gold_first) == "second quarter ended July 26, 2026"
    assert periods.find_period_phrase("Revenue for the second quarter of fiscal 2027 was $96.2 billion.") == \
        "second quarter of fiscal 2027"
    assert periods.find_period_phrase("Meta's Q3 2026 revenue rose.") == "Q3 2026"
    assert periods.find_period_phrase("No period here.") is None
