"""Adversarial model proposals; all verdicts use the committed SEC snapshot offline."""
from pathlib import Path
import pytest

from receipts import extract, verify
from receipts.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def store():
    result = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield result
    result.close()


def proposal(span, value="$96.2 billion", **extra):
    fields = dict(metric="revenue", metric_text="revenue", value_text=value, value=96.2,
                  unit="USD", scale="billions", period_text="Q2 FY2027", period_type="quarter",
                  comparison="none", bases=["GAAP"], forward_looking=False, entity="NVIDIA",
                  segment=None, span_text=span)
    fields.update(extra)
    return extract.ModelClaim(**fields)


@pytest.mark.parametrize("quote", ["$96.2 billion", "Revenue was under $96.2 billion",
                                   "revenue was under $96.2 billion"])
def test_omitted_or_nonverbatim_bound_cannot_turn_false_claim_verified(store, quote):
    text = "NVIDIA Q2 FY2027 revenue was under $96.2 billion."
    claims, ranges = extract.build_claims([(0, text, [proposal(quote)])], [])
    assert len(claims) == 1 and len(ranges) == 1
    claim = claims[0]
    assert text[claim.span_start:claim.span_end] == claim.span_text
    assert "under" in claim.span_text
    verdict = verify.verify(claim, store)
    assert verdict.status == "wrong" and verdict.reason == "value_mismatch"


@pytest.mark.parametrize("operator,status", [
    ("over", "verified"), ("at least", "verified"), ("no less than", "verified"),
    ("under", "wrong"), ("at most", "wrong"), ("no more than", "wrong"),
    ("just under", "wrong"), ("nearly", "wrong"), ("approximately", "verified"),
])
def test_source_operators_are_authoritative_and_have_no_tolerance_slack(store, operator, status):
    text = f"NVIDIA Q2 FY2027 revenue was {operator} $96.2 billion."
    claims, _ = extract.build_claims([(0, text, [proposal("$96.2 billion")])], [])
    assert verify.hedge_of(claims[0]) is not None
    assert verify.verify(claims[0], store).status == status


def test_partial_operator_cannot_drop_no(store):
    text = "NVIDIA Q2 FY2027 revenue was no more than $96.2 billion."
    claims, _ = extract.build_claims([(0, text, [proposal("more than $96.2 billion")])], [])
    assert claims[0].span_text == "no more than $96.2 billion"
    assert verify.verify(claims[0], store).status == "wrong"


def test_currency_outside_model_value_and_global_offsets(store):
    passage = "NVIDIA Q2 FY2027 revenue was UNDER $96.2 billion."
    claims, _ = extract.build_claims([(37, passage, [proposal("96.2 billion", value="96.2 billion")])], [])
    claim = claims[0]
    assert passage[claim.span_start - 37:claim.span_end - 37] == "UNDER $96.2 billion"
    assert verify.verify(claim, store).status == "wrong"


@pytest.mark.parametrize("prefix", ["Profit was under $2 billion. ", "Profit was under $2 billion, and ",
                                    "Profit was under $2 billion; "])
def test_bound_on_another_number_does_not_leak(store, prefix):
    text = prefix + "NVIDIA Q2 FY2027 revenue was $96.2 billion."
    claims, _ = extract.build_claims([(0, text, [proposal("$96.2 billion")])], [])
    assert claims[0].span_text == "$96.2 billion"
    assert verify.hedge_of(claims[0]) is None
    assert verify.verify(claims[0], store).status == "verified"


def test_repeated_value_uses_its_own_source_operator(store):
    text = "NVIDIA revenue was over $96.2 billion, and revenue was under $96.2 billion."
    props = [proposal(text), proposal(text)]
    claims, ranges = extract.build_claims([(0, text, props)], [])
    assert len(claims) == len(ranges) == 2
    assert [verify.hedge_of(c).kind for c in claims] == ["lower", "upper"]
    assert [verify.verify(c, store).status for c in claims] == ["verified", "wrong"]
    assert all(text[c.span_start:c.span_end] == c.span_text for c in claims)


