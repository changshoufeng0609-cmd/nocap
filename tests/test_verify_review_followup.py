"""Offline regression checks for the October 29 review's verifier findings.

Synthetic proposals test deterministic source binding, not model error rates or
held-out performance. All receipts come from the committed SEC snapshot.
"""
from pathlib import Path

import pytest

from receipts.store import Store
from receipts.types import Claim
from receipts.verify import hedge_of, interpret, strip_bound_currency_prefix, verify


@pytest.fixture(scope="module")
def snapshot():
    root = Path(__file__).resolve().parents[1] / "data/snapshot"
    store = Store(root / "facts.sqlite", root / "pr_facts")
    try:
        yield store
    finally:
        store.close()


def proposal(span, value_text="$96.221 billion", value=96.221, **changes):
    fields = dict(id="review", metric="revenue", metric_text="revenue", value_text=value_text,
                  value=value, unit="USD", scale="billions", period_text="Q2 FY2027",
                  period_type="quarter", comparison="none", basis="GAAP", forward_looking=False,
                  entity="NVIDIA", segment=None, span_text=span, span_start=0, span_end=len(span))
    fields.update(changes)
    return Claim(**fields)


def growth(span, value=106, **changes):
    fields = dict(metric="revenue_growth", metric_text="revenue growth", unit="%", scale="units")
    fields.update(changes)
    return proposal(span, f"{value}%", value, **fields)


@pytest.mark.parametrize("span,value_text,value", [
    ("Revenue was $46.743 billion, up from $40 billion a year ago.", "$46.743 billion", 46.743),
    ("Revenue rose from $40 billion a year earlier to $46.743 billion.", "$46.743 billion", 46.743),
    ("Revenue rose 106% from a year earlier to $46.7 billion.", "$46.7 billion", 46.7),
    ("Revenue rose 18% from the previous quarter to $81.6 billion.", "$81.6 billion", 81.6),
    ("Revenue was $81.615 billion, compared with $40 billion in the prior quarter.", "$81.615 billion", 81.615),
])
def test_another_amounts_comparison_cannot_verify_the_current_amount(snapshot, span, value_text, value):
    verdict = verify(proposal(span, value_text, value), snapshot)
    assert (verdict.status, verdict.reason) == ("wrong", "period_mismatch")
    assert verdict.expected_value == pytest.approx(96.221)


@pytest.mark.parametrize("span", [
    "Revenue was $96.221 billion, up from $46.743 billion a year ago.",
    "Revenue rose from $46.743 billion a year earlier to $96.221 billion.",
    "Revenue was $96.221 billion, compared with $46.743 billion in the prior year.",
])
def test_each_level_amount_keeps_its_own_receipt_in_either_order(snapshot, span):
    current = verify(proposal(span), snapshot)
    prior = verify(proposal(span, "$46.743 billion", 46.743), snapshot)
    assert current.status == prior.status == "verified"
    assert {f.fiscal_year for f in current.evidence} == {2027}
    assert {f.fiscal_year for f in prior.evidence} == {2026}


@pytest.mark.parametrize("currency", ["$", "US$", "USD "])
@pytest.mark.parametrize("words,status", [
    ("more than", "wrong"), ("over", "wrong"), ("less than", "wrong"), ("under", "wrong"),
    ("at least", "verified"), ("at most", "verified"),
])
@pytest.mark.parametrize("cropped", [False, True])
def test_currency_spelling_preserves_strict_and_inclusive_bounds(snapshot, currency, words, status, cropped):
    amount = f"{currency}96.221 billion"
    claim = proposal(f"Revenue was {words} {amount}.", "96.221 billion" if cropped else amount)
    assert hedge_of(claim) is not None
    assert verify(claim, snapshot).status == status


@pytest.mark.parametrize("currency", ["US$", "USD "])
def test_currency_bound_does_not_borrow_equality_rounding(snapshot, currency):
    amount = f"{currency}59.7 billion"
    claim = proposal(f"Net income was more than {amount}.", amount, 59.7,
                     metric="net_income", metric_text="net income")
    assert verify(claim, snapshot).status == "wrong"
    control = claim.model_copy(update={"span_text": f"Net income was {amount}."})
    assert verify(control, snapshot).status == "verified"


def test_currency_prefix_helper_requires_a_complete_supported_token():
    assert strip_bound_currency_prefix("more than US$ ") == "more than"
    assert strip_bound_currency_prefix("more than USD -") == "more than"
    for prefix in ("more than EUR", "more than €", "status", "census", "more than XUSD"):
        assert strip_bound_currency_prefix(prefix) == prefix


@pytest.mark.parametrize("metric", ["revenue_growth", "revenue"])
@pytest.mark.parametrize("description", [
    "revenue growth excluding China", "revenue growth from discontinued operations",
    "European revenue growth", "revenue growth excluding stock compensation",
])
def test_growth_enum_does_not_discard_metric_qualifications(snapshot, metric, description):
    claim = growth(f"NVIDIA {description} was 106% year over year.", metric=metric, metric_text=description)
    verdict = verify(claim, snapshot)
    assert verdict.status == "unverifiable" and verdict.expected_value is None


@pytest.mark.parametrize("span", [
    "Revenue excluding China grew 106% year over year.",
    "European revenue grew 106% year over year.",
    "Revenue grew 106% excluding China.",
    "Revenue excluding China was $96.221 billion, up 106% year over year.",
    "Net income was $59.688 billion, while revenue excluding China grew 106% year over year.",
])
def test_growth_reads_its_value_local_source_qualifications(snapshot, span):
    verdict = verify(growth(span), snapshot)
    assert verdict.status == "unverifiable" and verdict.expected_value is None


@pytest.mark.parametrize("metric", ["net_income", "other"])
def test_nonrevenue_growth_also_preserves_source_scope(snapshot, metric):
    claim = growth("Net income excluding stock compensation rose 106% year over year.",
                   metric=metric, metric_text="net income growth")
    verdict = verify(claim, snapshot)
    assert verdict.status == "unverifiable" and verdict.expected_value is None


@pytest.mark.parametrize("description,span", [
    ("revenue growth", "Revenue grew 106% year over year."),
    ("reported sales growth", "NVIDIA reported sales grew 106% year over year."),
    ("top line growth", "The top line grew 106% year over year."),
    ("revenue growth", "Revenue was $96.221 billion, up 106% year over year."),
    ("revenue growth", "European net income rose 12%, while revenue grew 106% year over year."),
    ("revenue growth", "Revenue grew 106% year over year, while European net income rose 12%."),
])
def test_supported_growth_aliases_and_neighboring_qualifiers_remain_supported(snapshot, description, span):
    assert verify(growth(span, metric_text=description), snapshot).status == "verified"


@pytest.mark.parametrize("segment", [None, "Data Center"])
def test_segment_growth_still_uses_segment_receipts(snapshot, segment):
    claim = growth("Data Center revenue grew 117% year over year.", 117,
                   metric_text="Data Center revenue growth", segment=segment)
    verdict = verify(claim, snapshot)
    assert verdict.status == "verified"
    assert {f.segment for f in verdict.evidence} == {"Data Center"}
    qualified = claim.model_copy(update={"metric_text": "Data Center revenue growth excluding China"})
    assert verify(qualified, snapshot).status == "unverifiable"


@pytest.mark.parametrize("source_words,wrong_value,correct_value,comparison", [
    ("year over year", 18, 106, "QoQ"),
    ("year-over-year", 18, 106, "QoQ"),
    ("YoY", 18, 106, "QoQ"),
    ("from a year earlier", 18, 106, "QoQ"),
    ("quarter over quarter", 106, 18, "YoY"),
    ("QoQ", 106, 18, "YoY"),
    ("sequentially", 106, 18, "YoY"),
    ("from the previous quarter", 106, 18, "YoY"),
])
@pytest.mark.parametrize("missing", [False, True])
def test_explicit_source_comparison_overrides_missing_or_conflicting_model_metadata(
        snapshot, source_words, wrong_value, correct_value, comparison, missing):
    comparison = "none" if missing else comparison
    wrong = growth(f"Revenue rose {wrong_value}% {source_words}.", wrong_value, comparison=comparison)
    verdict = verify(wrong, snapshot)
    assert (verdict.status, verdict.reason) == ("wrong", "period_mismatch")
    correct = growth(f"Revenue rose {correct_value}% {source_words}.", correct_value, comparison=comparison)
    assert verify(correct, snapshot).status == "verified"


@pytest.mark.parametrize("span", [
    "Revenue rose 106% year over year and 18% sequentially.",
    "Revenue rose 18% sequentially and 106% year over year.",
    "Revenue rose 18% sequentially; year-over-year revenue growth was 106%.",
])
def test_each_growth_amount_keeps_its_own_source_comparison(snapshot, span):
    assert verify(growth(span, 106, comparison="QoQ"), snapshot).status == "verified"
    assert verify(growth(span, 18, comparison="YoY"), snapshot).status == "verified"


@pytest.mark.parametrize("span,value,comparison", [
    ("Year-over-year revenue growth was 106%.", 106, "QoQ"),
    ("Sequential revenue growth was 18%.", 18, "YoY"),
    ("Revenue rose 106% year over year and sequential revenue growth was 18%.", 18, "YoY"),
])
def test_source_comparison_can_precede_its_own_percentage(snapshot, span, value, comparison):
    assert verify(growth(span, value, comparison=comparison), snapshot).status == "verified"


def test_conflicting_source_comparisons_withhold_a_verdict(snapshot):
    claim = growth("Revenue rose 106% year over year / sequentially.")
    verdict = verify(claim, snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")


def test_unspecified_growth_still_allows_both_comparison_readings(snapshot):
    assert verify(growth("Revenue grew 18%.", 18), snapshot).status == "verified"
    assert verify(growth("Revenue grew 106%."), snapshot).status == "verified"


@pytest.mark.parametrize("currency", ["$", "US$", "USD "])
def test_up_to_a_growth_endpoint_is_ambiguous_and_keeps_true_upper_bounds(snapshot, currency):
    amount = f"{currency}96.2 billion"
    claim = proposal(f"Revenue was up to {amount} from $46.7 billion a year earlier.", amount, 96.2)
    verdict = verify(claim, snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")
    endpoint = claim.model_copy(update={"span_text": f"Revenue rose to {amount} from $46.7 billion a year earlier."})
    assert verify(endpoint, snapshot).status == "verified"
    upper = claim.model_copy(update={"span_text": f"Revenue was up to {amount}."})
    assert verify(upper, snapshot).status == "wrong"
    inclusive = proposal(f"Revenue was up to {currency}96.221 billion.", f"{currency}96.221 billion")
    assert verify(inclusive, snapshot).status == "verified"


@pytest.mark.parametrize("value_text", ["96.2 billion", "US$96.2 billion", "up to US$96.2 billion"])
def test_growth_endpoint_ambiguity_survives_different_value_quote_widths(snapshot, value_text):
    claim = proposal("Revenue was up to US$96.2 billion from US$46.7 billion a year earlier.", value_text, 96.2)
    verdict = verify(claim, snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")


# --- Integration follow-up: R4 scope checks keep ordinary change wording -------------------------
# The development replay showed the first R4 patch rejecting legitimate change descriptions. These
# shapes come from that replay's model proposals; qualifications must still be "?".

def change(span, metric_text, value=26, segment=None, metric="revenue_growth", **changes):
    return growth(span, value, metric=metric, metric_text=metric_text, segment=segment, **changes)


@pytest.mark.parametrize("span,metric_text,segment", [
    ("an increase of 26% year-over-year", "increase", None),
    ("increased 26%", "increased", None),
    ("up 26% year-over-year", "Gaming business revenue growth", "Gaming"),
    ("Revenue in Q1 grew 26% year over year", "Revenue", None),
    ("posting AWS revenue growth of 26% year over year", "AWS revenue growth", "AWS"),
    ("revenue of $64.6 billion that rose 26% year over year", "revenue", None),
    ("the segment grew 26% year over year", "segment grew", "Intelligent Cloud"),
    ("rose 26% year over year", "rose 26% year over year", "Data Center"),
])
def test_ordinary_change_wording_is_still_read_as_revenue_growth(span, metric_text, segment):
    reading = interpret(change(span, metric_text, segment=segment))
    assert reading is not None and reading.growth_base == "revenue" and reading.segment == segment


def test_a_change_subject_after_a_clause_break_is_read():
    span = "profit growth that kept the focus on markets, as net income reached $16.5 billion, up 13% year over year"
    reading = interpret(change(span, "profit growth", 13, metric="other"))
    assert reading is not None and reading.growth_base == "net_income"


@pytest.mark.parametrize("span,metric_text", [
    ("Revenue in Greater China grew 26% year over year", "Revenue"),
    ("posting AWS revenue growth excluding FX of 26% year over year", "AWS revenue growth"),
    ("the segment grew 26% excluding acquisitions", "segment grew"),
    ("rose 26% from discontinued operations", "rose 26%"),
])
def test_change_qualifications_remain_unverifiable(snapshot, span, metric_text):
    claim = change(span, metric_text, segment="AWS" if "AWS" in span else None)
    assert interpret(claim) is None
    assert verify(claim, snapshot).status == "unverifiable"


@pytest.mark.parametrize("span", ["$59.688 billion excluding stock compensation",
                                  "$59.688 billion, attributable to noncontrolling interests"])
def test_number_first_quote_keeps_its_trailing_qualification(snapshot, span):
    claim = proposal(span, "$59.688 billion", 59.688, metric="net_income", metric_text="net income")
    assert verify(claim, snapshot).status == "unverifiable"


@pytest.mark.parametrize("span,metric,metric_text,value_text,value", [
    ("generated $96.221 billion in revenue", "revenue", "revenue", "$96.221 billion", 96.221),
    ("$96.221 billion for the second quarter of fiscal 2027", "revenue", "revenue", "$96.221 billion", 96.221),
    ("$46.743 billion in the prior year period", "revenue", "revenue", "$46.743 billion", 46.743),
])
def test_trailing_metric_or_period_wording_is_not_a_qualification(span, metric, metric_text, value_text, value):
    claim = proposal(span, value_text, value, metric=metric, metric_text=metric_text)
    assert interpret(claim) is not None


# The October 30 judging pass: a qualified figure stays "?", and its explanation names the qualification instead
# of claiming the metric itself (revenue, verified elsewhere on the same page) is untracked.
@pytest.mark.parametrize("claim,wording", [
    (growth("Excluding acquisitions, revenue rose 106%."), "Excluding acquisitions"),
    (growth("Revenue rose 106% excluding acquisitions."), "excluding acquisitions"),
    (proposal("Revenue excluding acquisitions was $96.221 billion."), "excluding acquisitions"),
])
def test_qualified_figure_explanation_names_the_qualification(snapshot, claim, wording):
    verdict = verify(claim, snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "no_evidence")
    assert f"“{wording}”" in verdict.explanation
    assert "doesn't track" not in verdict.explanation


def test_constant_currency_explanation_wins_when_the_wording_also_blocks_the_reading(snapshot):
    claim = growth("Revenue rose 106% in constant currency.")
    assert interpret(claim) is None
    verdict = verify(claim, snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "no_evidence")
    assert "constant-currency (FX-neutral)" in verdict.explanation


def test_untracked_figure_keeps_the_untracked_explanation(snapshot):
    claim = proposal("A favorable impact of $0.11 per share.", "$0.11", 0.11, metric="other",
                     metric_text="favorable impact", unit="USD/share", scale="units")
    verdict = verify(claim, snapshot)
    assert (verdict.status, verdict.reason) == ("unverifiable", "no_evidence")
    assert "doesn't track this kind of figure (favorable impact)" in verdict.explanation
