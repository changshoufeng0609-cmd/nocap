"""Offline comparison bases, written cents, and yearless fiscal quarters (round 10b)."""
from datetime import date
from pathlib import Path

import pytest

from receipts import periods
from receipts.store import Store
from receipts.types import Claim
from receipts.verify import _labelled_comparison_base, claimed_value, tolerance, verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def store():
    s = Store(ROOT / 'data/snapshot/facts.sqlite', ROOT / 'data/snapshot/pr_facts')
    yield s
    s.close()


def claim(span, text, value, **kw):
    args = dict(id='c1', metric='eps_diluted', metric_text='diluted earnings per share',
                value_text=text, value=value, unit='USD/share', scale='units',
                period_text='Q2 2026', period_type='quarter', comparison='none', basis=None,
                forward_looking=False, entity='Loews', segment=None, span_text=span,
                span_start=0, span_end=len(span))
    args.update(kw)
    return Claim(**args)


@pytest.mark.parametrize('intro', ['compared with', 'compared to', 'versus', 'up from', 'down from'])
@pytest.mark.parametrize('label', ['a year earlier', 'in the same period a year earlier', 'last year',
                                   'in the second quarter 2025', 'in the comparable period of 2025'])
@pytest.mark.parametrize('which', ['amount', 'eps'])
def test_trailing_base_label_covers_amount_and_eps(store, intro, label, which):
    span = f'Net income was $444 million, or $2.16 per share, {intro} $391 million, or $1.87 per share {label}.'
    kw = dict(metric='net_income', metric_text='net income', unit='USD', scale='millions') if which == 'amount' else {}
    text, value = ('$391 million', 391) if which == 'amount' else ('$1.87', 1.87)
    c = claim(span, text, value, **kw)
    v = verify(c, store, context='Loews Q2 2026')
    assert v.status == 'verified', v.explanation
    assert 'Q2 2025' in v.explanation
    wrong = c.model_copy(update={'span_text': span.replace(text, '$999 million' if which == 'amount' else '$9.99'),
                                 'value_text': '$999 million' if which == 'amount' else '$9.99',
                                 'value': 999 if which == 'amount' else 9.99})
    assert verify(wrong, store, context='Loews Q2 2026').status == 'wrong'
    current = claim(span, '$444 million', 444, metric='net_income', metric_text='net income',
                    unit='USD', scale='millions')
    cv = verify(current, store, context='Loews Q2 2026')
    assert cv.status == 'verified' and 'Q2 2026' in cv.explanation


@pytest.mark.parametrize('comparison', ['none', 'YoY'])
@pytest.mark.parametrize('entity,span,text,value,period,context,kw', [
    ('ConocoPhillips', 'compared with $2.0 billion, or $1.56 per share in the same period a year earlier',
     '$1.56 per share', 1.56, 'the same period a year earlier', 'ConocoPhillips Q2 2026',
     {'metric': 'other', 'metric_text': 'earnings per share'}),
    ('Loews', 'up from $391 million', '$391 million', 391, 'a year earlier', 'Loews Q2 2026',
     {'metric': 'net_income', 'metric_text': 'net income', 'unit': 'USD', 'scale': 'millions'}),
    ('Synchrony', 'versus $2.86 a year earlier', '$2.86', 2.86, 'a year earlier', 'Synchrony Q3 2026', {}),
])
def test_level_base_is_not_a_percent_change(store, comparison, entity, span, text, value, period, context, kw):
    c = claim(span, text, value, entity=entity, period_text=period, comparison=comparison, **kw)
    v = verify(c, store, context=context)
    assert v.status == 'verified' and '2025' in v.explanation


@pytest.mark.parametrize('tail', [
    'and revenue was $500 million a year earlier',
    '; EPS was $1.87 a year earlier',
    '. EPS was $1.87 a year earlier',
    ', versus $500 million a year earlier',
    ', a year earlier it was $500 million',
])
def test_base_label_does_not_cross_a_new_clause(tail):
    c = claim('Net income was $444 million, compared with $391 million ' + tail, '$391 million', 391)
    assert _labelled_comparison_base(c) is None


def test_each_comparison_block_keeps_its_own_explicit_period():
    span = ('Net income was $444 million, compared with $391 million, or $1.87 per share in Q2 2024, '
            'versus $500 million a year earlier')
    assert _labelled_comparison_base(claim(span, '$391 million', 391)) == 'Q2 2024'


def test_short_span_recovers_base_label_only_from_its_own_draft_sentence(store):
    span = 'up from $391 million'
    draft = '- Net income: $444 million, or $2.16 per share, ' + span + ', or $1.87 per share a year earlier'
    start = draft.index(span)
    c = claim(span, '$391 million', 391, metric='net_income', metric_text='net income', unit='USD',
              scale='millions', span_start=start, span_end=start + len(span))
    v = verify(c, store, context='Loews Q2 2026', draft=draft)
    assert v.status == 'verified' and 'Q2 2025' in v.explanation


@pytest.mark.parametrize('text', ['11 cents', '11¢', '11 cents per diluted share'])
@pytest.mark.parametrize('proposal', [11, .11])
def test_cents_use_written_number_and_precision(store, text, proposal):
    c = claim(f'Diluted earnings per share were {text}.', text, proposal,
              entity='Weyerhaeuser', period_text='Q3 2025')
    assert claimed_value(c) == pytest.approx(.11)
    assert tolerance(c) == pytest.approx(.005)
    assert verify(c, store).status == 'verified'
    wrong = c.model_copy(update={'span_text': c.span_text.replace('11', '19'),
                                 'value_text': text.replace('11', '19'), 'value': 19})
    v = verify(wrong, store)
    assert v.status == 'wrong' and v.expected_value == pytest.approx(11)


@pytest.mark.parametrize('metric', ['eps_diluted', 'eps_basic', 'dividend_per_share'])
@pytest.mark.parametrize('text,want,tol', [('11.5 cents', .115, .0005), ('-11¢', -.11, .005),
                                         ('(11 cents)', -.11, .005)])
def test_cents_signs_and_decimal_precision(metric, text, want, tol):
    c = claim(text, text, 11, metric=metric)
    assert claimed_value(c) == pytest.approx(want)
    assert tolerance(c) == pytest.approx(tol)


def test_cents_do_not_rescale_unrelated_units():
    c = claim('fuel margin 11 cents per gallon', '11 cents', 11, metric='other', unit='other')
    assert claimed_value(c) == 11 and tolerance(c) == .5
    dollar = claim('EPS $0.11', '$0.11', .11)
    assert claimed_value(dollar) == .11 and tolerance(dollar) == .005


@pytest.mark.parametrize('period', ['first quarter', 'Q1', 'first-quarter', 'fiscal first-quarter'])
def test_nike_latest_ended_quarter_is_not_latest_reported(store, period):
    c = claim('Revenue was $11.2 billion.', '$11.2 billion', 11.2, entity='Nike', metric='revenue',
              metric_text='revenue', unit='USD', scale='billions', period_text=period)
    v = verify(c, store, context='Nike', as_of='2026-11-02')
    assert v.status == 'verified' and 'Q1 FY2027' in v.explanation
    future = verify(c, store, context='Nike', as_of='2027-09-01')
    assert future.status == 'unverifiable' and 'Q1 FY2028' in future.explanation
    override = verify(c, store, context='Nike fiscal 2028', as_of='2026-11-02')
    assert override.status == 'unverifiable' and 'Q1 FY2028' in override.explanation
    wrong = c.model_copy(update={'value': 99.9, 'value_text': '$99.9 billion',
                                 'span_text': 'Revenue was $99.9 billion.'})
    assert verify(wrong, store, context='Nike', as_of='2026-11-02').status == 'wrong'


@pytest.mark.parametrize('quarter', ['first', 'second', 'third', 'fourth'])
def test_hyphenation_matches_plain_quarter_with_context(store, quarter):
    for ticker, context in [('CINF', 'Cincinnati Financial Q3 2026'), ('NKE', 'Nike fiscal 2027')]:
        cik = store.by_ticker(ticker)[0]
        plain = periods.resolve_period(f'{quarter} quarter', store, cik, context=context)
        assert plain is not None
        assert periods.resolve_period(f'{quarter}-quarter', store, cik, context=context) == plain


def test_ambiguous_calendar_and_ytd_are_not_guessed(store):
    cik = store.by_ticker('NKE')[0]
    assert periods.resolve_period('first-quarter 2026', store, cik) is None
    assert periods.resolve_period('first-quarter', store, cik, context='Nike year-to-date 2026') is None
    assert periods.resolve_period('year-to-date', store, cik, context='Nike fiscal 2027') is None
    assert periods.resolve_in_calendar('first-quarter', [], as_of='2026-11-02') is None
    drifting = [periods.CalEntry(2025, 'Q1', None, date(2025, 3, 31)),
                periods.CalEntry(2026, 'Q1', None, date(2026, 4, 30))]
    assert periods.resolve_in_calendar('Q1', drifting, as_of='2026-11-02') is None


@pytest.mark.parametrize('word', ['hitting', 'reaching', 'results'])
def test_neutral_words_check_right_and_wrong_numbers(store, word):
    span = f'Operating cash flow {word} $584.3 million'
    c = claim(span, '$584.3 million', 584.3, entity='AMETEK', metric='operating_cash_flow',
              metric_text='operating cash flow', unit='USD', scale='millions', period_text='Q4 2025')
    v = verify(c, store)
    assert v.status == 'verified', v.explanation
    wrong = c.model_copy(update={'span_text': span.replace('584.3', '984.3'), 'value_text': '$984.3 million',
                                 'value': 984.3})
    assert verify(wrong, store).status == 'wrong'


@pytest.mark.parametrize('qualifier', ['core', 'U.S.', 'before taxes'])
def test_measure_qualifiers_still_abstain(store, qualifier):
    c = claim(f'{qualifier} operating cash flow reaching $584.3 million', '$584.3 million', 584.3,
              entity='AMETEK', metric='operating_cash_flow', metric_text=f'{qualifier} operating cash flow',
              unit='USD', scale='millions', period_text='Q4 2025')
    assert verify(c, store).status == 'unverifiable'


def test_title_quarter_is_verification_only_and_names_the_resolved_period(store):
    from receipts.extract import infer_context
    from receipts.verify import with_draft_company
    text = 'Nike Fiscal First-Quarter Earnings Note\n\nRevenue was $11.2 billion.'
    span = text.split('\n\n')[1]
    start = text.index(span)
    c = claim(span, '$11.2 billion', 11.2, entity='Nike', metric='revenue', metric_text='revenue',
              unit='USD', scale='billions', period_text='', span_start=start, span_end=start + len(span))
    context = with_draft_company(infer_context(text), text, store)
    assert context == 'Nike'
    v = verify(c, store, context=context, draft=text, as_of='2026-11-02')
    assert v.status == 'verified' and 'Q1 FY2027' in v.explanation
    assert with_draft_company(infer_context(text), text, store) == context
    future = verify(c, store, context=context, draft=text, as_of='2027-09-01')
    assert future.status == 'unverifiable' and 'Q1 FY2028' in future.explanation
    override = verify(c, store, context='Nike fiscal Q1 2028', draft=text, as_of='2026-11-02')
    assert override.status == 'unverifiable' and 'Q1 FY2028' in override.explanation


@pytest.mark.parametrize('title', ['Nike First-Quarter and Second-Quarter Earnings',
                                    'Nike First-Quarter and Full-Year Earnings',
                                    'Nike First-Quarter Outlook',
                                    'Microsoft First-Quarter Earnings',
                                    'Nike Earnings'])
def test_title_must_name_one_unambiguous_quarter_for_this_company(store, title):
    span = 'Revenue was $11.2 billion.'
    text = title + '\n\n' + span
    start = text.index(span)
    c = claim(span, '$11.2 billion', 11.2, entity='Nike', metric='revenue', metric_text='revenue',
              unit='USD', scale='billions', period_text='', span_start=start, span_end=start + len(span))
    assert verify(c, store, context='Nike', draft=text, as_of='2026-11-02').status == 'unverifiable'


def test_a_single_prose_line_is_not_a_title(store):
    from receipts.verify import _with_title_period
    text = 'Nike reported revenue of $11.2 billion in the first quarter.'
    c = claim(text, '$11.2 billion', 11.2, entity='Nike')
    assert _with_title_period('Nike', text, c, store, '2026-11-02', None) == 'Nike'


@pytest.mark.parametrize('text', ['first quarter or second quarter', 'Q1 / Q2', 'first-quarter and Q2'])
def test_yearless_quarter_alternatives_are_ambiguous(store, text):
    assert periods.resolve_period(text, store, store.by_ticker('NKE')[0], as_of='2026-11-02') is None


@pytest.mark.parametrize('intro', ['over', 'over the', 'than', 'against the'])
def test_bounds_are_not_comparison_block_introducers(intro):
    c = claim(f'Net income was {intro} $391 million a year earlier', '$391 million', 391)
    assert _labelled_comparison_base(c) is None


def test_latest_quarter_without_as_of_uses_today(store, monkeypatch):
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 2)
    monkeypatch.setattr(periods, 'date', Today)
    rp = periods.resolve_period('first-quarter', store, store.by_ticker('NKE')[0], context='Nike')
    assert rp[:2] == (2027, 'Q1') and str(rp.period_end) == '2026-08-31'
