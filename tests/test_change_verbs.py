"""Offline wording regressions against the committed SEC snapshot."""
from pathlib import Path

import pytest

from receipts import verify as V
from receipts.store import Store
from receipts.types import Claim

ROOT = Path(__file__).resolve().parents[1]
UP = 'surged soared jumped climbed rose grew increased expanded improved gained advanced rebounded'.split()
DOWN = 'fell declined decreased dropped slipped slid plunged tumbled sank contracted shrank eased dipped'.split()


@pytest.fixture(scope='module')
def snapshot():
    store = Store(ROOT / 'data/snapshot/facts.sqlite', ROOT / 'data/snapshot/pr_facts')
    yield store
    store.close()


def claim(text, value_text, value, *, metric='net_income', metric_text='Net income', entity='NVIDIA',
          period='Q2 FY2027', unit='%', scale='units', comparison='none'):
    return Claim(id='c1', metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                 scale=scale, entity=entity, period_text=period, period_type='quarter', comparison=comparison,
                 basis=None, segment=None, forward_looking=False, span_text=text, span_start=0, span_end=len(text))


@pytest.mark.parametrize('entity,period,rate,amount,filed', [
    ('NVIDIA', 'Q2 FY2027', 126, '59.69', '125.9%'),
    ('AMD', 'Q2 2026', 163, '2.30', '163.4%'),
])
def test_owner_net_income_examples(snapshot, entity, period, rate, amount, filed):
    text = f'Net income surged {rate}% to ${amount} billion'
    growth = claim(text, f'{rate}%', rate, entity=entity, period=period)
    result = V.verify(growth, snapshot)
    assert result.status == 'verified', result.explanation
    assert filed in result.explanation
    level = claim(text, f'${amount} billion', float(amount), entity=entity, period=period,
                  unit='USD', scale='billions')
    assert V.verify(level, snapshot).status == 'verified'
    for original in (growth, level):
        wrong_text = original.value_text.replace(str(rate), '999') if original.unit == '%' else '$99.99 billion'
        wrong = original.model_copy(update={'value_text': wrong_text, 'value': 999 if original.unit == '%' else 99.99,
                                           'span_text': text.replace(original.value_text, wrong_text)})
        assert V.verify(wrong, snapshot).status in ('wrong', 'unverifiable')


@pytest.mark.parametrize('verb', UP + DOWN)
def test_change_family_preserves_direction(snapshot, verb):
    # Regions Q2 2026 noninterest income fell from $646M to $630M: -2.4768%.
    text = f'Noninterest income {verb} 2.5%'
    c = claim(text, '2.5%', 2.5, metric='other', metric_text='Noninterest income',
              entity='Regions', period='Q2 2026')
    v = V.verify(c, snapshot)
    if verb in DOWN:
        assert v.status == 'verified', v.explanation
    else:
        assert v.status in ('wrong', 'unverifiable'), v.explanation
    assert V.claimed_value(c, change=True) == (-2.5 if verb in DOWN else 2.5)


@pytest.mark.parametrize('verb', UP)
def test_up_family_checks_positive_growth(snapshot, verb):
    c = claim(f'Net income {verb} 126%', '126%', 126)
    assert V.verify(c, snapshot).status == 'verified'


@pytest.mark.parametrize('verb', UP + DOWN)
def test_family_reads_absolute_changes_and_endpoint_levels(verb):
    change = claim(f'Net income {verb} $16 million', '$16 million', 16, unit='USD', scale='millions')
    reading = V.interpret(change)
    assert reading and reading.absolute and reading.growth_base == 'net_income'
    assert V.claimed_value(change, change=True) == (-16e6 if verb in DOWN else 16e6)
    level = change.model_copy(update={'span_text': f'Net income {verb} to $16 million'})
    reading = V.interpret(level)
    assert reading and not reading.absolute and reading.growth_base is None
    # An unlabelled 'from' amount must keep its comparison-period reading.
    base = level.model_copy(update={'span_text': f'Net income {verb} from $16 million to $20 million'})
    assert V._unlabelled_base(base) == 'implied'


@pytest.mark.parametrize('wording', ['remained nearly unchanged at', 'remained flat at', 'held steady at',
                                     'essentially unchanged at', 'remained almost unchanged at'])
@pytest.mark.parametrize('amount,expected', [('28.05', 'verified'), ('30.05', 'wrong')])
def test_tesla_stable_level(snapshot, wording, amount, expected):
    text = f"Tesla's quarterly revenue {wording} **${amount} billion**"
    c = claim(text, f'${amount} billion', float(amount), metric='revenue', metric_text='quarterly revenue',
              entity='Tesla', period='Q3 2026', unit='USD', scale='billions')
    result = V.verify(c, snapshot)
    assert result.status == expected, result.explanation
    if expected == 'verified':
        assert '$28.046 billion' in result.explanation


@pytest.mark.parametrize('text,value_text,value,unit,scale', [
    ('Revenue was nearly $28.04 billion', '$28.04 billion', 28.04, 'USD', 'billions'),
    ('Revenue remained nearly unchanged at nearly $28.04 billion', '$28.04 billion', 28.04, 'USD', 'billions'),
    ('Shares remained flat at nearly 220 million', '220 million', 220, 'shares', 'millions'),
])
def test_immediate_bounds_remain_bounds(snapshot, text, value_text, value, unit, scale):
    c = claim(text, value_text, value, metric='revenue', metric_text='Revenue', entity='Tesla', period='Q3 2026',
              unit=unit, scale=scale)
    assert V.hedge_of(c).kind == 'nearly'
    assert V.verify(c, snapshot).status != 'verified'


@pytest.mark.parametrize('metric,metric_text', [('revenue', 'Revenue'), ('gross_margin', 'Gross margin'),
                                              ('other', 'Operating margin'), ('revenue_growth', 'Revenue growth')])
@pytest.mark.parametrize('wording', ['unchanged at', 'nearly unchanged at', 'remained flat at', 'held steady at'])
def test_stable_percent_is_not_a_measure_level(snapshot, metric, metric_text, wording):
    c = claim(f'{metric_text} {wording} 0%', '0%', 0, metric=metric, metric_text=metric_text)
    assert V.interpret(c) is None
    assert V.verify(c, snapshot).status == 'unverifiable'


@pytest.mark.parametrize('verb,value', [('accelerated', 30), ('slowed', 5)])
def test_rate_level_wording_stays_unread(snapshot, verb, value):
    c = claim(f'Revenue growth {verb} to {value}%', f'{value}%', value,
              metric='revenue_growth', metric_text='Revenue growth')
    assert V.interpret(c) is None
    assert V.verify(c, snapshot).status == 'unverifiable'


@pytest.mark.parametrize('value,status', [(33.9, 'verified'), (35.4, 'wrong')])
def test_explicit_nonzero_margin_endpoint_keeps_its_existing_check(snapshot, value, status):
    c = claim(f'Gross margin was unchanged at {value} percent', f'{value} percent', value,
              metric='gross_margin', metric_text='Gross margin', entity='General Mills', period='Q1 FY2027')
    assert V.verify(c, snapshot).status == status


@pytest.mark.parametrize('metric_text', ['quarterly revenue', 'quarterly revenue remained nearly unchanged at'])
def test_stability_wording_in_metric_text_is_also_framing(snapshot, metric_text):
    c = claim('Quarterly revenue remained nearly unchanged at $28.05 billion', '$28.05 billion', 28.05,
              metric='revenue', metric_text=metric_text, entity='Tesla', period='Q3 2026', unit='USD', scale='billions')
    assert V.verify(c, snapshot).status == 'verified'


def test_stability_does_not_erase_a_scope_qualifier(snapshot):
    c = claim('Revenue excluding China remained nearly unchanged at $28.05 billion', '$28.05 billion', 28.05,
              metric='revenue', metric_text='Revenue', entity='Tesla', period='Q3 2026', unit='USD', scale='billions')
    assert V.verify(c, snapshot).status == 'unverifiable'


@pytest.mark.parametrize('verb', DOWN)
def test_down_family_cannot_verify_positive_filed_growth(snapshot, verb):
    c = claim(f'Net income {verb} 126%', '126%', 126)
    assert V.verify(c, snapshot).status in ('wrong', 'unverifiable')
