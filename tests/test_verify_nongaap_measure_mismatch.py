"""Non-GAAP is a basis, not an assurance that two row labels name the same measure."""
import pytest

from receipts.extract import ModelClaim, build_claims
from receipts.verify import span_reading, verify
from test_verify import FakeStore, NVDA, NVDA_8K_Q2FY27, claim, fact


def revenue_store(label="Total revenue - TE", value=3.79e9):
    row = fact(NVDA_8K_Q2FY27, "revenue", value, 2027, "Q2", basis="non-GAAP")
    row = row.model_copy(update={"locator": f"{label} | 3,790"})
    return FakeStore([row], aliases={"truist": (NVDA, "TFC")})


def revenue_claim(wording="adjusted revenue", value_text="$5.3 billion", value=5.3, basis="non-GAAP", **kw):
    return claim(value_text, value, metric_text=wording, entity="Truist", basis=basis,
                 span=f"Truist's {wording} was {value_text}.", **kw)


@pytest.mark.parametrize("basis", [None, "non-GAAP"])
@pytest.mark.parametrize("label,wording", [
    ("Total revenue - TE", "adjusted revenue"),
    ("Total revenue - FTE", "adjusted revenue"),
    ("Total revenue - taxable-equivalent", "adjusted revenue"),
    ("Total revenue - managed", "adjusted revenue"),
    ("Adjusted revenue", "managed revenue"),
    ("Total revenue - TE", "adjusted taxable-equivalent revenue"),
])
def test_different_nongaap_measure_mismatch_abstains(label, wording, basis):
    v = verify(revenue_claim(wording, basis=basis), revenue_store(label))
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert label in v.explanation
    assert "$3.79 billion" in v.explanation
    assert "different non-GAAP measure" in v.explanation
    assert v.expected_value is None
    assert [f.locator for f in v.evidence] == [f"{label} | 3,790"]


@pytest.mark.parametrize("wording", ["TE revenue", "FTE revenue", "taxable-equivalent revenue",
                                     "taxable equivalent revenue", "fully taxable-equivalent revenue",
                                     "fully-taxable-equivalent revenue"])
@pytest.mark.parametrize("basis", [None, "non-GAAP"])
def test_taxable_equivalent_aliases_match_the_stored_measure(wording, basis):
    c = revenue_claim(wording, "$3.79 billion", 3.79, basis)
    v = verify(c, revenue_store())
    assert v.status == "verified", v.explanation
    v = verify(revenue_claim(wording, basis=basis), revenue_store())
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == 3.79


@pytest.mark.parametrize("label,wording", [("Adjusted revenue", "adjusted revenue"),
                                          ("Revenue - managed", "managed revenue"),
                                          ("Total revenue - TE", "non-GAAP revenue"),
                                          ("Total revenue - TE", "revenue"),
                                          ("Non-GAAP revenue", "adjusted revenue")])
def test_same_measure_or_no_specific_qualifier_keeps_mismatch(label, wording):
    v = verify(revenue_claim(wording), revenue_store(label))
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation


def test_different_measure_bound_mismatch_also_abstains():
    c = revenue_claim().model_copy(update={"span_text": "Truist's adjusted revenue exceeded $5.3 billion."})
    v = verify(c, revenue_store())
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


def test_qualifier_for_another_amount_does_not_change_this_measure():
    c = revenue_claim("revenue").model_copy(update={
        "span_text": "Adjusted EPS was $1.00, and revenue was $5.3 billion."})
    v = verify(c, revenue_store())
    assert v.status == "wrong", v.explanation


def test_same_measure_remains_wrong_when_another_measure_is_newer():
    store = revenue_store("Adjusted revenue", 5.3e9)
    other = revenue_store()._facts[0].model_copy(update={
        "accepted_at": store._facts[0].accepted_at.replace(day=27)})
    store._facts.append(other)
    v = verify(revenue_claim(value_text="$6.0 billion", value=6.0), store)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == 5.3
    assert all(f.locator.startswith("Adjusted revenue") for f in v.evidence)


def test_company_name_te_connectivity_does_not_imply_taxable_equivalent():
    store = revenue_store()
    store._aliases = {"te connectivity": (NVDA, "TEL")}
    store._facts = [store._facts[0].model_copy(update={"basis": "GAAP"})]
    c = claim("$3.79 billion", 3.79, entity="TE Connectivity",
              span="TE Connectivity's revenue was $3.79 billion.")
    v = verify(c, store)
    assert v.status == "verified", v.explanation


@pytest.mark.parametrize("wording", ["equivalent revenue", "taxable revenue", "fully revenue"])
def test_partial_taxable_equivalent_words_do_not_drop_unknown_qualifiers(wording):
    v = verify(revenue_claim(wording, "$3.79 billion", 3.79), revenue_store())
    assert v.status == "unverifiable", v.explanation


def test_short_model_span_restores_the_measure_qualifier():
    sentence = "On an adjusted basis, Truist's revenue was $5.3 billion."
    proposal = ModelClaim(metric="revenue", metric_text="revenue", value_text="$5.3 billion", value=5.3,
                          unit="USD", scale="billions", period_text="second quarter of fiscal 2027",
                          period_type="quarter", comparison="none", bases=["non-GAAP"],
                          forward_looking=False, entity="Truist", segment=None,
                          span_text="revenue was $5.3 billion")
    claims, _ = build_claims([(0, sentence, [proposal])], [])
    assert len(claims) == 1
    v = verify(claims[0], revenue_store())
    assert v.status == "unverifiable", v.explanation
    shortened = claims[0].model_copy(update={"span_text": "revenue was $5.3 billion"})
    assert span_reading(claims[0]) != span_reading(shortened)
