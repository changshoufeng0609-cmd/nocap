"""An accented company name ("Estée Lauder") is the claim's subject, not an unread qualifier.

Found 2026-11-03 by the top-up QA round trips over all 500 companies: "Estée Lauder reported revenue of $3,627.0 million
for the fourth quarter of fiscal 2026." came back "?" (not tracked) while the same sentence spelled "Estee" was ✓,
because any non-ASCII letter outside the metric phrase rejected the reading. A non-ASCII word that is not the company's
name still leaves the figure unread. Offline: the committed snapshot, no model, network or SEC call.
"""
from pathlib import Path

import pytest

from receipts.store import Store
from receipts.types import Claim
from receipts.verify import verify

SNAPSHOT = Path(__file__).resolve().parents[1] / "data/snapshot"


@pytest.fixture(scope="module")
def store():
    return Store(SNAPSHOT / "facts.sqlite", SNAPSHOT / "pr_facts")


def claim(entity: str, span: str, value: float, value_text: str, metric: str = "revenue", metric_text: str = "revenue"):
    return Claim(id="c1", metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit="USD",
                 scale="millions", period_text="fourth quarter of fiscal 2026", period_type="quarter",
                 comparison="none", basis=None, forward_looking=False, entity=entity, segment=None, span_text=span,
                 span_start=0, span_end=len(span))


@pytest.mark.parametrize("entity", ["Estée Lauder", "Estee Lauder", "The Estée Lauder Companies"])
def test_the_true_figure_is_verified_whatever_the_spelling(store, entity):
    span = f"{entity} reported revenue of $3,627.0 million for the fourth quarter of fiscal 2026."
    v = verify(claim(entity, span, 3627.0, "$3,627.0 million"), store)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


def test_a_wrong_figure_under_the_accented_name_is_wrong(store):
    span = "Estée Lauder reported revenue of $3,880.9 million for the fourth quarter of fiscal 2026."
    v = verify(claim("Estée Lauder", span, 3880.9, "$3,880.9 million"), store)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation


def test_a_non_ascii_word_outside_the_name_still_leaves_the_figure_unread(store):
    span = "Estée Lauder reported Amériques revenue of $3,627.0 million for the fourth quarter of fiscal 2026."
    v = verify(claim("Estée Lauder", span, 3627.0, "$3,627.0 million", metric_text="Amériques revenue"), store)
    assert v.status == "unverifiable", v.explanation
