"""Ambiguous net-income concepts from the November 1 offline adversarial review."""
import pytest

from receipts.verify import verify
from test_verify import FakeStore, NVDA_10Q_Q2FY27, NVDA_10Q_Q1FY27, claim, fact


def derived_income(total=1_338_000_000, nci=1_026_000_000):
    # Archived IBKR 0001381197-26-000147 values; synthetic NVIDIA filing metadata
    # keeps this a self-contained verifier test, independent of snapshot contents.
    f = fact(NVDA_10Q_Q2FY27, "net_income", total - nci, 2027, "Q2", source="derived",
             derived_from=[NVDA_10Q_Q2FY27[3]])
    return f.model_copy(update={
        "concept": "us-gaap:NetIncomeLoss",
        "locator": (f"NetIncomeLoss = ProfitLoss {total:,.0f} - "
                    f"NetIncomeLossAttributableToNoncontrollingInterest {nci:,.0f} "
                    f"(10-Q {f.accession})"),
    })


@pytest.mark.parametrize("total,nci,text,value", [
    (1_338_000_000, 1_026_000_000, "$1.34 billion", 1.34),
    (2_996_000_000, 19_000_000, "$2.996 billion", 2.996),
    (869_000_000, 142_000_000, "$0.869 billion", .869),
])
def test_consolidated_income_in_derived_locator_verifies_plain_net_income(total, nci, text, value):
    # Round 10a: unqualified net income that matches the filed consolidated figure is ✓ (it was "?" before): that is
    # the line the income statement calls net income. The receipt keeps the filing whose locator shows both.
    verdict = verify(claim(text, value, metric="net_income"), FakeStore([derived_income(total, nci)]))
    assert (verdict.status, verdict.reason) == ("verified", "match"), verdict.explanation
    assert "including noncontrolling interests" in verdict.explanation
    assert "attributable to the company" in verdict.explanation
    assert verdict.expected_value is None
    assert verdict.evidence[0].value == total - nci


def test_alternative_income_explanation_names_both_actual_figures():
    verdict = verify(claim("$1.34 billion", 1.34, metric="net_income"), FakeStore([derived_income()]))
    assert "$1.338 billion" in verdict.explanation
    assert "$312 million" in verdict.explanation


@pytest.mark.parametrize("text,value,status", [("$312 million", 312, "verified"),
                                               ("$350 million", 350, "wrong"),
                                               ("$1,340 million", 1340, "wrong")])
def test_nci_ambiguity_preserves_ordinary_matches_mismatches_and_written_precision(text, value, status):
    verdict = verify(claim(text, value, metric="net_income", scale="millions"), FakeStore([derived_income()]))
    assert verdict.status == status, verdict.explanation


@pytest.mark.parametrize("mutation", [
    {"source": "xbrl"},
    {"derived_from": [NVDA_10Q_Q2FY27[3], NVDA_10Q_Q1FY27[3]]},
    {"value": 310_000_000},
    {"concept": "us-gaap:SomeOtherIncome"},
])
def test_unbound_or_inconsistent_locator_cannot_supply_an_alternative(mutation):
    f = derived_income().model_copy(update=mutation)
    verdict = verify(claim("$1.34 billion", 1.34, metric="net_income"), FakeStore([f]))
    assert verdict.status == "wrong", verdict.explanation


def test_later_consolidated_income_does_not_leak_through_as_of():
    older = fact(NVDA_10Q_Q1FY27, "net_income", 312_000_000, 2027, "Q2")
    verdict = verify(claim("$1.34 billion", 1.34, metric="net_income"),
                     FakeStore([older, derived_income()]), as_of="2026-06-01T00:00:00Z")
    assert verdict.status == "wrong", verdict.explanation
    assert "including noncontrolling interests" not in verdict.explanation


def test_separately_stored_same_filing_income_concepts_verify_plain_net_income_too():
    own = derived_income().model_copy(update={"source": "xbrl", "derived_from": [], "locator": None})
    total = own.model_copy(update={"concept": "us-gaap:ProfitLoss", "value": 1_338_000_000})
    verdict = verify(claim("$1.34 billion", 1.34, metric="net_income"), FakeStore([own, total]))
    assert (verdict.status, verdict.reason) == ("verified", "match"), verdict.explanation  # "?" before round 10a
    assert set(f.value for f in verdict.evidence) == {312_000_000, 1_338_000_000}


def test_profitloss_without_a_conflicting_attributable_figure_remains_a_match():
    total = derived_income().model_copy(update={
        "source": "xbrl", "derived_from": [], "locator": None,
        "concept": "us-gaap:ProfitLoss", "value": 1_338_000_000,
    })
    verdict = verify(claim("$1.34 billion", 1.34, metric="net_income"), FakeStore([total]))
    assert verdict.status == "verified", verdict.explanation


def test_two_nci_components_preserved_in_the_locator_are_supported():
    f = derived_income()
    f = f.model_copy(update={"locator": f.locator.replace(
        "NetIncomeLossAttributableToNoncontrollingInterest",
        "NetIncomeLossAttributableToRedeemableNoncontrollingInterest + "
        "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest")})
    verdict = verify(claim("$1.34 billion", 1.34, metric="net_income"), FakeStore([f]))
    assert (verdict.status, verdict.reason) == ("verified", "match"), verdict.explanation  # "?" before round 10a


@pytest.mark.parametrize("word", ["attributable", "available", "applicable"])
@pytest.mark.parametrize("placement", ["description", "before", "after"])
def test_to_common_income_is_explicitly_not_checked_against_before_dividend_income(word, placement):
    phrase = f"net income {word} to common stockholders"
    proposal = claim("$312 million", 312, metric="net_income", scale="millions",
                     metric_text=phrase if placement == "description" else "net income",
                     span=(f"{phrase} was $312 million." if placement != "after"
                           else f"Net income was $312 million {word} to common stockholders."))
    verdict = verify(proposal, FakeStore([derived_income()]))
    assert verdict.status == "unverifiable", verdict.explanation
    assert "preferred dividends" in verdict.explanation


@pytest.mark.parametrize("span", [
    "Net income available to common stockholders was $10 million; net income was $312 million.",
    "Net income was $312 million, and net income available to common stockholders was $10 million.",
])
def test_to_common_words_in_another_claim_do_not_change_this_claim(span):
    verdict = verify(claim("$312 million", 312, metric="net_income", scale="millions", span=span),
                     FakeStore([derived_income()]))
    assert verdict.status == "verified", verdict.explanation
