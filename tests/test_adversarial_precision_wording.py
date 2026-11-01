"""Written magnitudes and source-local margin words; archived filing values are noted inline."""
from pathlib import Path

import pytest

from receipts.store import Store
from receipts.verify import tolerance, verify
from test_verify import FakeStore, NVDA_10Q_Q2FY27, claim, fact
from test_verify_margins import change, margin_claim, ratio_facts


@pytest.mark.parametrize('word', ['trillion', 'tn', 'T'])
@pytest.mark.parametrize('unit', ['USD', 'shares'])
def test_trillion_precision_survives_normalization_to_billions(word, unit):
    c = claim(f'3.4 {word}', 3400, unit=unit, scale='billions')
    assert tolerance(c) == pytest.approx(50e9)


@pytest.mark.parametrize('metric', ['revenue', 'net_income', 'operating_income', 'gross_profit',
                                    'cost_of_revenue', 'operating_cash_flow', 'free_cash_flow'])
def test_trillion_precision_applies_to_every_money_metric(metric):
    c = claim('$3.4 trillion', 3400, metric=metric, basis='GAAP')
    v = verify(c, FakeStore([fact(NVDA_10Q_Q2FY27, metric, 3.427704e12, 2027, 'Q2')]))
    assert v.status == 'verified', v.explanation


@pytest.mark.parametrize('value_text,value,status', [('$3.4 trillion', 3400, 'verified'),
                                                    ('$3.40 trillion', 3400, 'wrong')])
def test_balance_sheet_precision_uses_written_magnitude(value_text, value, status):
    # Archive: BAC Assets 3,427,704M, 0000070858-26-000548, 2026-09-30.
    store = Store(Path(__file__).resolve().parents[1] / 'data/snapshot/facts.sqlite')
    try:
        c = claim(value_text, value, metric='other', metric_text='total assets', entity='Bank of America',
                  period_text='third quarter of 2026', period_type='instant',
                  span=f"Bank of America's total assets were {value_text}.")
        v = verify(c, store)
    finally:
        store.close()
    assert v.status == status, v.explanation


@pytest.mark.parametrize('label,metric', [('operating margin', 'operating_income'), ('gross margin', 'gross_profit'),
                                        ('net margin', 'net_income')])
@pytest.mark.parametrize('unit', ['points', 'pts', 'ppt', 'ppts', 'pp', 'percentage-point', 'percentage-points',
                                 'pct points'])
def test_point_spellings_are_margin_changes(label, metric, unit):
    v = verify(change(label, f'1.2 {unit}', 1.2, unit='%'), FakeStore(ratio_facts(metric)))
    assert v.status == 'verified', v.explanation


@pytest.mark.parametrize('label,metric', [('operating margin', 'operating_income'), ('gross margin', 'gross_profit'),
                                        ('net margin', 'net_income')])
@pytest.mark.parametrize('verb', ['rose', 'expanded', 'improved', 'contracted', 'narrowed', 'widened'])
def test_percent_worded_margin_change_is_ambiguous(label, metric, verb):
    v = verify(change(label, '1.2%', 1.2, direction=verb), FakeStore(ratio_facts(metric)))
    assert (v.status, v.reason) == ('unverifiable', 'ambiguous'), v.explanation
    assert 'relative' in v.explanation and 'percentage points' in v.explanation


@pytest.mark.parametrize('label,metric', [('operating margin', 'operating_income'), ('gross margin', 'gross_profit'),
                                        ('net margin', 'net_income')])
def test_rose_to_is_still_a_margin_level(label, metric):
    v = verify(margin_claim(label, span=f'{label} rose to 25.3%'), FakeStore(ratio_facts(metric)))
    assert v.status == 'verified', v.explanation


@pytest.mark.parametrize('verb,current,previous', [('widened', .253, .241), ('narrowed', .241, .253),
                                                   ('contracted', .241, .253)])
def test_margin_verbs_have_direction_without_leftover_qualifiers(verb, current, previous):
    v = verify(change('gross margin', '1.2 points', 1.2, direction=verb),
               FakeStore(ratio_facts('gross_profit', current, previous)))
    assert v.status == 'verified', v.explanation


@pytest.mark.parametrize('apostrophe', ["'", '’'])
def test_company_possessive_ending_in_s_is_not_a_qualifier(apostrophe):
    c = claim('$1.2 billion', 1.2, metric='net_income', entity='Meta Platforms', period_text='second quarter of 2026',
              span=f'Meta Platforms{apostrophe} net income was $1.2 billion.')
    from test_verify import META_10Q_Q2
    v = verify(c, FakeStore([fact(META_10Q_Q2, 'net_income', 1.2e9, 2026, 'Q2')]))
    assert v.status == 'verified', v.explanation


@pytest.mark.parametrize('metric', ['revenue', 'net_income'])
def test_precise_trillion_mismatch_displays_a_distinct_correction(metric):
    c = claim('$4.6400 trillion', 4640, metric=metric)
    v = verify(c, FakeStore([fact(NVDA_10Q_Q2FY27, metric, 4.640471e12, 2027, 'Q2')]))
    assert v.status == 'wrong', v.explanation
    assert '$4,640,471,000,000' in v.explanation
    assert 'not $4.6400 trillion' in v.explanation


def test_precise_balance_sheet_correction_is_not_rounded_back_to_claim():
    store = Store(Path(__file__).resolve().parents[1] / 'data/snapshot/facts.sqlite')
    try:
        c = claim('$4.6400 trillion', 4640, metric='other', metric_text='total liabilities', entity='JPMorgan',
                  period_text='second quarter of 2026', period_type='instant',
                  span="JPMorgan's total liabilities were $4.6400 trillion.")
        v = verify(c, store)
    finally:
        store.close()
    assert v.status == 'wrong', v.explanation
    assert '$4,640,471,000,000' in v.explanation
    assert 'not $4.6400 trillion' in v.explanation


@pytest.mark.parametrize('label,metric', [('operating margin', 'operating_income'), ('gross margin', 'gross_profit'),
                                        ('net margin', 'net_income')])
@pytest.mark.parametrize('hedge', ['about', 'approximately'])
def test_hedged_margin_endpoint_does_not_become_ambiguous_change(label, metric, hedge):
    c = margin_claim(label, span=f'{label} rose to {hedge} 25.3%')
    v = verify(c, FakeStore(ratio_facts(metric)))
    assert v.status == 'verified', v.explanation


def test_negative_margin_endpoint_is_a_level():
    c = margin_claim('net margin', span='net margin rose to negative 25.3%')
    v = verify(c, FakeStore(ratio_facts(current=-.253)))
    assert v.status == 'verified', v.explanation
