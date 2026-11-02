"""A figure whose own sentence names no period takes the period its draft names elsewhere.

The saved Meta example plants last quarter's cost figure in a third-quarter story: "Total costs and expenses jumped to
$42.0 billion" is Q2 2026's figure, and the story's opening names the third quarter of 2026, so it is a period ✗. The
same sentence as a draft of its own names no period; its period is then only the context line's, so a figure that fits
another period is "?", never a period ✗ (round 8, from the comprehensive run's Estée Lauder line).
"""
import json
from pathlib import Path

import pytest

from receipts.store import Store
from receipts.types import Claim
from receipts.verify import verify

ROOT = Path(__file__).resolve().parents[1]
CLAIM = {"id": "c3", "metric": "operating_expenses", "metric_text": "Total costs and expenses",
         "value_text": "$42.0 billion", "value": 42.0, "unit": "USD", "scale": "billions",
         "period_text": "third quarter of 2026", "period_type": "quarter", "comparison": "none", "basis": None,
         "forward_looking": False, "entity": "Meta Platforms", "segment": None,
         "span_text": "Total costs and expenses jumped to $42.0 billion"}


@pytest.fixture(scope="module")
def store():
    s = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield s
    s.close()


def meta_example():
    examples = json.loads((ROOT / "data/snapshot/examples.json").read_text())["examples"]
    return next(e for e in examples if e["id"] == "meta-ai-story")


def test_a_period_the_story_names_once_covers_its_later_sentences(store):
    example = meta_example()
    text = example["text"]
    start = text.index(CLAIM["span_text"])
    claim = Claim(**CLAIM, span_start=start, span_end=start + len(CLAIM["span_text"]))
    v = verify(claim, store, context=example["context"], draft=text)
    assert (v.status, v.reason) == ("wrong", "period_mismatch")
    assert "Q2 2026" in v.explanation and "$51.613 billion" in v.explanation


def test_a_draft_that_names_no_period_leaves_a_period_fit_unverifiable(store):
    text = "Total costs and expenses jumped to $42.0 billion, driven by depreciation on new data centers."
    claim = Claim(**CLAIM, span_start=0, span_end=len(CLAIM["span_text"]))
    v = verify(claim, store, context="Meta Platforms, third quarter of 2026", draft=text)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")
    assert "names no period" in v.explanation
