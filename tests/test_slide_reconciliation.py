"""Spatial duplicate/conflict handling; generated readings are claims, not evidence."""
from test_slides import Model, output, reading, run


def result(body):
    events, _, _ = run(Model(body))
    assert events[-1][0] == 'done'
    return ([d for n, d in events if n == 'claim'],
            [d for n, d in events if n == 'verdict'], events[-1][1])


def test_same_label_returned_in_overlapping_regions_is_only_counted_once():
    body = output(reading())
    body['regions'].append({'label': 'Same chart', 'box': [100, 200, 800, 800], 'claims': [reading()]})
    claims, verdicts, done = result(body)
    assert len(claims) == len(verdicts) == done['claims'] == 1
    assert done['reading_audit']['duplicates_removed'] == 1
    assert done['reading_audit']['image_completeness_measured'] is False


def test_equal_values_at_distinct_positions_remain_two_readings():
    claims, _, done = result(output(reading(), reading(box=[600, 300, 800, 400])))
    assert len(claims) == 2 and [c['id'] for c in claims] == ['c1', 'c2']
    assert done['reading_audit']['duplicates_removed'] == 0


def test_conflicting_digits_for_same_visual_label_cannot_be_verified_or_wrong():
    other = reading(value=80, value_text='$80 billion', span_text='NVIDIA revenue $80 billion')
    claims, verdicts, done = result(output(reading(), other))
    assert len(claims) == 2 and all(v['status'] == 'unverifiable' for v in verdicts)
    assert all(v['reason'] == 'ambiguous' for v in verdicts)
    assert done['reading_audit']['conflicting_readings'] == 2


def test_duplicate_uncertainty_is_not_overruled_by_confident_copy():
    claims, verdicts, done = result(output(reading(), reading(uncertain=True)))
    assert len(claims) == 1 and verdicts[0]['status'] == 'unverifiable'
    assert done['reading_audit']['duplicates_removed'] == 1


def test_joint_gaap_bases_survive_deduplication_and_ids_are_contiguous():
    shared = reading(bases=['GAAP', 'non-GAAP'])
    claims, _, done = result(output(shared, shared, reading(box=[600, 300, 800, 400])))
    assert [c['id'] for c in claims] == ['c1', 'c2', 'c3'] and done['claims'] == 3
    assert [c['basis'] for c in claims[:2]] == ['GAAP', 'non-GAAP']


def test_axis_and_printed_disagreement_at_same_position_is_ambiguous():
    _, verdicts, _ = result(output(reading(), reading(reading='axis_estimate')))
    assert all(v['status'] == 'unverifiable' for v in verdicts)


def test_numeric_reading_limit_and_empty_regions_are_disclosed():
    body = output(*[reading(box=[i * 25, 100, i * 25 + 20, 200]) for i in range(32)])
    body['regions'].append({'label': 'Unreadable table', 'box': [0, 250, 1000, 500], 'claims': []})
    _, _, done = result(body)
    assert any('32-reading limit' in w for w in done['warnings'])
    assert any('no financial readings' in w for w in done['warnings'])


def test_conflicting_transcription_cannot_disappear_during_deduplication():
    original = reading(metric_text='GAAP revenue')
    conflict = reading(metric_text='GAAP revenue', span_text='NVIDIA non-GAAP revenue $96.2 billion')
    claims, verdicts, done = result(output(original, conflict))
    assert len(claims) == 2 and all(v['status'] == 'unverifiable' for v in verdicts)
    assert done['reading_audit']['conflicting_readings'] == 2


def test_overlap_chain_is_reconciled_before_middle_duplicate_is_removed():
    first = reading(box=[200, 300, 400, 400])
    middle = reading(box=[220, 300, 420, 400])
    last = reading(box=[240, 300, 440, 400], value=80, value_text='$80 billion', span_text='Revenue $80 billion')
    claims, verdicts, done = result(output(first, middle, last))
    assert len(claims) == 2 and all(v['status'] == 'unverifiable' for v in verdicts)
    assert done['reading_audit']['duplicates_removed'] == 1
    assert done['reading_audit']['conflicting_readings'] == 2
