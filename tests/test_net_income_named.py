"""A named net-income line is checked only against evidence for that exact meaning."""
from datetime import date

import pytest

from receipts.verify import verify
from test_net_income_common_verify import common, income, store
from test_verify import claim, utc


def own_income():
    # Use the direct NetIncomeLoss path that hid ProfitLoss in the judge's F2.
    return income().model_copy(update={"source": "xbrl", "derived_from": [], "locator": None})


def including(**updates):
    return own_income().model_copy(update={
        "metric": "net_income_including_nci", "concept": "us-gaap:ProfitLoss", "value": 2996e6,
    } | updates)


def proposal(description, value="2.996", *, span=None, metric_text=None, **changes):
    c = claim(f"${value} billion", float(value), metric="net_income",
              metric_text=description if metric_text is None else metric_text,
              entity="Occidental", period_text="Q2 2026",
              span=span or f"Occidental's {description} was ${value} billion.")
    return c.model_copy(update=changes)


NAMES = [
    ("consolidated net income", "2.996", including, "including noncontrolling interests"),
    ("net income including noncontrolling interests", "2.996", including, "including noncontrolling interests"),
    ("net income including minority interests", "2.996", including, "including noncontrolling interests"),
    ("net income available to common stockholders", "2.787", common, "common stockholders"),
    ("net income attributable to common shareholders", "2.787", common, "common stockholders"),
]


@pytest.mark.parametrize("description,value,make_fact,label", NAMES)
@pytest.mark.parametrize("metric_text", [None, "net income"])
def test_explicit_name_matches_its_own_filed_line(description, value, make_fact, label, metric_text):
    v = verify(proposal(description, value, metric_text=metric_text), store([own_income(), make_fact()]))
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert label in v.explanation
    assert any(f.metric == make_fact().metric and f.value == float(value) * 1e9 for f in v.evidence)


@pytest.mark.parametrize("description,value,make_fact,label", NAMES)
def test_named_line_mismatch_corrects_to_that_line_even_when_served_value_matches(description, value, make_fact, label):
    v = verify(proposal(description, "2.977"), store([own_income(), make_fact()]))
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(float(value))
    assert label in v.explanation
    assert any(f.metric == make_fact().metric for f in v.evidence)


@pytest.mark.parametrize("description,value,make_fact,label", NAMES)
def test_unqualified_alternative_keeps_both_receipts(description, value, make_fact, label):
    # Round 10a: the consolidated line is what plain "net income" names on the income statement, so it verifies (it was
    # "?" before); net income available to common stockholders stays "?": preferred dividends make it another measure.
    v = verify(proposal("net income", value), store([own_income(), make_fact()]))
    consolidated = make_fact().metric == "net_income_including_nci"
    assert (v.status, v.reason, v.expected_value) == (("verified", "match", None) if consolidated else
                                                       ("unverifiable", "ambiguous", None)), v.explanation
    assert "$2.977 billion" in v.explanation and f"${value} billion" in v.explanation
    assert {f.metric for f in v.evidence} == {"net_income", make_fact().metric}


@pytest.mark.parametrize("description,value,make_fact,label", NAMES[3:])
@pytest.mark.parametrize("value_to_check", ["2.977", "7.123"])
def test_missing_common_line_never_falls_back_to_served_income(description, value, make_fact, label, value_to_check):
    v = verify(proposal(description, value_to_check), store([own_income()]))
    assert v.status == "unverifiable", v.explanation
    assert v.expected_value is None


@pytest.mark.parametrize("description,value,make_fact,label", NAMES[:3])
@pytest.mark.parametrize("value_to_check,status", [("2.977", "verified"), ("7.123", "wrong")])
def test_no_stored_consolidated_line_means_net_income_is_the_consolidated_figure(description, value, make_fact, label,
                                                                                 value_to_check, status):
    # The snapshot stores a consolidated figure wherever it differs from the directly tagged net income, so a company
    # with none stored for the period (NVIDIA: no noncontrolling interests) has one figure for both lines.
    v = verify(proposal(description, value_to_check), store([own_income()]))
    assert v.status == status, v.explanation
    assert "no separate consolidated net income" in v.explanation
    if status == "wrong":
        assert v.expected_value == pytest.approx(2.977)


@pytest.mark.parametrize("description,value,make_fact,label", NAMES[:3])
def test_derived_net_income_never_stands_in_for_a_named_consolidated_line(description, value, make_fact, label):
    derived = own_income().model_copy(update={"source": "derived", "locator": "Q4 = FY (10-K a) - 9M YTD (10-Q b)"})
    v = verify(proposal(description, "2.977"), store([derived]))
    assert v.status == "unverifiable", v.explanation


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[1], NAMES[3]])
@pytest.mark.parametrize("updates", [
    {"accession": "another-filing"}, {"period_start": date(2026, 1, 1)},
    {"basis": "non-GAAP"}, {"segment": "Oil"}, {"source": "derived"},
    {"unit": "shares"}, {"concept": "us-gaap:UnrelatedIncome"},
    {"accepted_at": utc("2026-09-01T00:00:00Z")},
])
def test_named_evidence_requires_same_filing_span_direct_public_gaap_dollars(description, value, make_fact, label, updates):
    v = verify(proposal(description, value), store([own_income(), make_fact(**updates)]),
               as_of="2026-08-06T00:00:00Z")
    assert v.status == "unverifiable", v.explanation
    assert v.expected_value is None
    assert all(f.metric == "net_income" for f in v.evidence)


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[0], NAMES[3]])
def test_future_named_line_is_never_corrected_to_another_income_line(description, value, make_fact, label):
    s = store([own_income(), make_fact()])
    s.filings = lambda cik, forms=None: [{"accession": "release", "form": "8-K", "items": "2.02",
        "report_date": "2026-06-30", "accepted_at": "2026-08-05T19:00:00Z"}]
    v = verify(proposal(description, value), s, as_of="2026-08-01T00:00:00Z")
    assert v.status == "unverifiable" or v.reason == "look_ahead", v.explanation
    assert v.expected_value is None
    assert "the filed figure is $2.977 billion" not in v.explanation


@pytest.mark.parametrize("qualifier,value,make_fact", [
    ("including noncontrolling interests", "2.996", including),
    ("including minority interests", "2.996", including),
    ("available to common stockholders", "2.787", common),
    ("attributable to common shareholders", "2.787", common),
])
def test_line_name_after_the_amount_still_belongs_to_it(qualifier, value, make_fact):
    c = proposal("net income", value, span=f"Net income was ${value} billion {qualifier}.")
    assert verify(c, store([own_income(), make_fact()])).status == "verified"


@pytest.mark.parametrize("other", ["consolidated net income", "net income available to common stockholders"])
@pytest.mark.parametrize("span", ["{other} was $9 billion; net income was $2.977 billion.",
                                  "Net income was $2.977 billion, and {other} was $9 billion."])
def test_another_numbers_line_name_does_not_change_this_number(other, span):
    c = proposal("net income", "2.977", span=span.format(other=other))
    v = verify(c, store([own_income(), common(), including()]))
    assert v.status == "verified", v.explanation
    assert all(f.metric == "net_income" for f in v.evidence)


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[0], NAMES[3]])
def test_model_invented_line_name_cannot_qualify_unqualified_source_words(description, value, make_fact, label):
    # The source says plain "net income", so the model's line name changes nothing: it reads as unqualified net income,
    # which the consolidated line verifies since round 10a, and the available-to-common line leaves "?"
    c = proposal(description, value, span=f"Occidental's net income was ${value} billion.")
    v = verify(c, store([own_income(), make_fact()]))
    plain = verify(proposal("net income", value), store([own_income(), make_fact()]))
    assert (v.status, v.reason) == (plain.status, plain.reason), v.explanation
    assert v.status == ("verified" if make_fact().metric == "net_income_including_nci" else "unverifiable")
    assert v.expected_value is None


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[0], NAMES[3]])
@pytest.mark.parametrize("span", [
    "Net income was $1 billion last year, compared with ${value} billion this year.",
    "It reported ${value} billion in the second quarter.",
    "Net income was ${value} billion; {description} was also ${value} billion.",
])
def test_unrecoverable_source_wording_cannot_borrow_model_line_name(description, value, make_fact, label, span):
    # Read as unqualified net income whatever line the model names: ✓ on the consolidated line since round 10a, "?" on
    # the available-to-common one
    c = proposal(description, value, span=span.format(value=value, description=description))
    v = verify(c, store([own_income(), make_fact()]))
    assert v.status == ("verified" if make_fact().metric == "net_income_including_nci" else "unverifiable"), \
        v.explanation
    assert v.expected_value is None


@pytest.mark.parametrize("qualifier,value,make_fact", [
    ("including noncontrolling interests", "2.996", including),
    ("available to common stockholders", "2.787", common),
])
@pytest.mark.parametrize("extra", ["excluding stock compensation", "from discontinued operations", "in Europe"])
def test_conjoined_qualifier_after_named_line_stays_attached_to_same_amount(qualifier, value, make_fact, extra):
    c = proposal("net income", value,
                 span=f"Net income was ${value} billion, {qualifier} and {extra}.")
    v = verify(c, store([own_income(), make_fact()]))
    assert v.status == "unverifiable", v.explanation
    assert v.expected_value is None


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[0], NAMES[3]])
@pytest.mark.parametrize("extra", [" excluding stock compensation", " from discontinued operations", " in Europe"])
def test_named_line_does_not_discard_additional_qualifiers(description, value, make_fact, label, extra):
    v = verify(proposal(description + extra, value), store([own_income(), make_fact()]))
    assert v.status == "unverifiable", v.explanation
    assert v.expected_value is None


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[0], NAMES[3]])
def test_non_gaap_name_cannot_use_gaap_alternative(description, value, make_fact, label):
    v = verify(proposal(description, value, basis="non-GAAP"), store([own_income(), make_fact()]))
    assert v.status == "unverifiable", v.explanation
    assert v.expected_value is None


@pytest.mark.parametrize("description,value,make_fact,bound,status", [
    (NAMES[0][0], "2.98", including, "more than", "verified"),
    (NAMES[0][0], "2.98", including, "less than", "wrong"),
    (NAMES[3][0], "2.80", common, "less than", "verified"),
    (NAMES[3][0], "2.80", common, "more than", "wrong"),
])
def test_named_line_is_selected_before_the_bound_is_evaluated(description, value, make_fact, bound, status):
    c = proposal(description, value, span=f"{description} was {bound} ${value} billion.")
    v = verify(c, store([own_income(), make_fact()]))
    assert v.status == status, v.explanation
    assert any(f.metric == make_fact().metric for f in v.evidence)
    if status == "wrong":
        assert v.expected_value == pytest.approx(make_fact().value / 1e9)


@pytest.mark.parametrize("description,value,make_fact", [(NAMES[0][0], "2.9", including),
                                                        (NAMES[3][0], "2.7", common)])
def test_named_line_preserves_existing_approximation_band(description, value, make_fact):
    c = proposal(description, value, span=f"{description} was about ${value} billion.")
    v = verify(c, store([own_income(), make_fact()]))
    assert (v.status, v.reason) == ("approximate", "approximate"), v.explanation
    assert v.expected_value is None
    assert any(f.metric == make_fact().metric for f in v.evidence)


@pytest.mark.parametrize("description,value,make_fact,label", [NAMES[0], NAMES[3]])
def test_alternative_growth_is_not_silently_replaced_by_ordinary_income_growth(description, value, make_fact, label):
    previous = own_income().model_copy(update={"value": 1000e6, "period_start": date(2025, 4, 1),
        "period_end": date(2025, 6, 30), "fiscal_year": 2025})
    s = store([own_income(), make_fact(), previous])
    s._cal[previous.cik].append((2025, "Q2", previous.period_start, previous.period_end))
    c = proposal(description, "197.7", value_text="197.7%", unit="%", scale="units",
                 comparison="YoY", span=f"{description} grew 197.7% year over year.")
    v = verify(c, s)
    assert v.status == "unverifiable", v.explanation


@pytest.mark.parametrize("make_fact,description,value", [(including, NAMES[0][0], "2.996"),
                                                        (common, NAMES[3][0], "2.787")])
def test_named_line_already_served_under_net_income_is_usable(make_fact, description, value):
    f = make_fact().model_copy(update={"metric": "net_income"})
    v = verify(proposal(description, value), store([f]))
    assert v.status == "verified", v.explanation
    assert v.evidence[0].concept == make_fact().concept


def test_exact_held_profitloss_derivation_can_support_a_named_consolidated_line():
    v = verify(proposal("consolidated net income"), store([income()]))
    assert v.status == "verified", v.explanation
    assert "including noncontrolling interests" in v.explanation


def test_conflicting_line_names_abstain_instead_of_picking_one():
    v = verify(proposal("consolidated net income attributable to common shareholders", "2.787"),
               store([own_income(), common(), including()]))
    assert v.status == "unverifiable", v.explanation
