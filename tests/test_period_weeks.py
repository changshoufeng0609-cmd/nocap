"""Explicit week counts must bind the exact filed duration, including long fiscal quarters.

Dates/values below come from archived COST 10-Q 0000909832-26-000051 and
EX-99 0000909832-26-000084, KR 10-Q 0001104659-26-078236, and AZO FY2024 10-K.
"""
from datetime import date, datetime, timezone

import pytest

from receipts import periods
from receipts.periods import CalEntry, ResolvedPeriod
from receipts.types import Claim, Fact
from receipts.verify import verify

COST_CAL = [
    CalEntry(2026, 'Q1', date(2025, 9, 1), date(2025, 11, 23)),
    CalEntry(2026, 'Q2', date(2025, 11, 24), date(2026, 2, 15)),
    CalEntry(2026, 'Q3', date(2026, 2, 16), date(2026, 5, 10)),
    CalEntry(2026, 'Q4', date(2026, 5, 11), date(2026, 8, 30)),
    CalEntry(2026, 'FY', date(2025, 9, 1), date(2026, 8, 30)),
]


@pytest.mark.parametrize('weeks', [24, 26, 28, 36, 39, 40])
@pytest.mark.parametrize('period_type', ['quarter', 'year_to_date'])
def test_multi_quarter_weeks_are_ytd(weeks, period_type):
    phrase = f'{weeks} weeks ended May 10, 2026'
    assert periods.parse(phrase).kind == 'ytd'
    assert periods.resolve_in_calendar(phrase, COST_CAL, period_type=period_type) is None


@pytest.mark.parametrize('phrase', ['', 'May 10, 2026', 'third quarter of fiscal 2026'])
def test_year_to_date_hint_cannot_fall_back_to_a_quarter(phrase):
    assert periods.resolve_in_calendar(phrase, COST_CAL, period_type='year_to_date',
                                       context='Costco Q3 FY2026') is None


@pytest.mark.parametrize('weeks,end,fp', [(12, '2026-05-10', 'Q3'), (16, '2026-08-30', 'Q4'),
                                       (52, '2026-08-30', 'FY')])
def test_weeks_resolve_without_a_model_hint(weeks, end, fp):
    assert periods.resolve_in_calendar(f'{weeks} weeks ended {end}', COST_CAL) == \
        ResolvedPeriod(2026, fp, date.fromisoformat(end))


@pytest.mark.parametrize('phrase', ['12 weeks ended August 30, 2026', '16 weeks ended May 10, 2026',
                                     '53 weeks ended August 30, 2026', '10 weeks ended May 10, 2026',
                                     '16-week third quarter of fiscal 2026'])
def test_wrong_duration_never_resolves(phrase):
    assert periods.resolve_in_calendar(phrase, COST_CAL, period_type='quarter') is None


def test_context_weeks_are_checked_before_inheriting_the_period():
    assert periods.resolve_in_calendar('', COST_CAL, context='12 weeks ended August 30, 2026',
                                       period_type='quarter') is None
    assert periods.resolve_in_calendar('', COST_CAL, context='16 weeks ended August 30, 2026',
                                       period_type='quarter') == ResolvedPeriod(2026, 'Q4', date(2026, 8, 30))


@pytest.mark.parametrize('phrase,context', [
    ('August quarter', '12 weeks ended August 30, 2026'),
    ('May quarter', '16 weeks ended May 10, 2026'),
    ('May quarter', '36 weeks ended May 10, 2026'),
])
def test_month_named_quarter_cannot_bypass_context_duration(phrase, context):
    assert periods.resolve_in_calendar(phrase, COST_CAL, context=context, period_type='quarter') is None


def test_month_named_quarter_can_take_only_the_date_anchor_from_another_calendar():
    assert periods.resolve_in_calendar('August quarter', COST_CAL, period_type='quarter',
                                       context='13 weeks ended August 29, 2026') == \
        ResolvedPeriod(2026, 'Q4', date(2026, 8, 30))


@pytest.mark.parametrize('phrase', ['12 weeks and 16 weeks ended May 10, 2026',
                                    '16 weeks and 12 weeks ended August 30, 2026'])
def test_multiple_conflicting_week_counts_are_ambiguous(phrase):
    assert periods.resolve_in_calendar(phrase, COST_CAL, period_type='quarter') is None


def test_17_week_quarter_and_53_week_year():
    cal = [CalEntry(2024, 'Q4', date(2024, 5, 5), date(2024, 8, 31)),
           CalEntry(2024, 'FY', date(2023, 8, 27), date(2024, 8, 31))]
    assert periods.resolve_in_calendar('17 weeks ended August 31, 2024', cal).fiscal_period == 'Q4'
    assert periods.resolve_in_calendar('53 weeks ended August 31, 2024', cal).fiscal_period == 'FY'
    assert periods.resolve_in_calendar('16 weeks ended August 31, 2024', cal, period_type='quarter') is None
    assert periods.resolve_in_calendar('52 weeks ended August 31, 2024', cal) is None


def test_unknown_start_cannot_establish_week_length():
    cal = [CalEntry(2026, 'Q3', None, date(2026, 5, 10))]
    assert periods.resolve_in_calendar('12 weeks ended May 10, 2026', cal) is None


class WeekStore:
    def resolve_entity(self, name):
        return (909832, 'COST') if name.lower() in ('costco', 'cost') else None

    def fiscal_calendar(self, cik):
        return COST_CAL

    def facts(self, cik, metric, **kwargs):
        end = kwargs.get('period_end')
        fp = 'Q3' if end == date(2026, 5, 10) else 'Q4'
        entry = next(e for e in COST_CAL if e.fiscal_period == fp)
        if metric not in ('revenue', 'net_income'):
            return []
        value = {'Q3': {'revenue': 70_527e6, 'net_income': 2_192e6},
                 'Q4': {'revenue': 95_723e6, 'net_income': 2_998e6}}[fp][metric]
        return [Fact(cik=909832, ticker='COST', metric=metric, basis='GAAP', value=value, unit='USD',
                     period_start=entry.start, period_end=entry.end, fiscal_year=2026, fiscal_period=fp,
                     source='xbrl', form='10-Q', accession='0000909832-26-000051',
                     accepted_at=datetime(2026, 10, 1, tzinfo=timezone.utc), url='https://www.sec.gov/')]


def week_claim(weeks, end, value, *, metric='revenue', period_type='quarter'):
    phrase = f'{weeks} weeks ended {end}'
    text = f"Costco's {metric.replace('_', ' ')} for the {phrase} was ${value} billion."
    return Claim(id='c1', metric=metric, metric_text=metric.replace('_', ' '), value_text=f'${value} billion',
                 value=value, unit='USD', scale='billions', period_text=phrase, period_type=period_type,
                 comparison='none', basis='GAAP', forward_looking=False, entity='Costco', span_text=text,
                 span_start=0, span_end=len(text))


@pytest.mark.parametrize('period_type', ['quarter', 'year_to_date'])
def test_true_costco_ytd_cannot_be_wrong_against_quarter(period_type):
    v = verify(week_claim(36, 'May 10, 2026', 6.23, metric='net_income', period_type=period_type), WeekStore())
    assert (v.status, v.reason) == ('unverifiable', 'ambiguous')
    assert 'year-to-date' in v.explanation
    assert not v.evidence


def test_incorrect_week_length_abstains_with_plain_reason():
    v = verify(week_claim(12, 'August 30, 2026', 95.7), WeekStore())
    assert (v.status, v.reason) == ('unverifiable', 'ambiguous')
    assert '12-week' in v.explanation and 'duration' in v.explanation
    assert not v.evidence


def test_correct_long_quarter_still_verifies():
    assert verify(week_claim(16, 'August 30, 2026', 95.7), WeekStore()).status == 'verified'
