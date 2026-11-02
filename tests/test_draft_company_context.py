"""A draft that names one covered company once checks its company-less figures as that company's.

A ChatGPT-style summary names "Bank of America" in its opening and then reports "Net interest income increased 8% to
$16.4 billion" with no company; the inferred context ("Q3 2026", from the headline) names no company, so the figure
was "The draft doesn't say which company this figure belongs to." The context now takes the draft's only named
company. A draft naming two companies, a context that already names one, and a caller's own context are unchanged.
"""
from pathlib import Path

import pytest

from receipts import extract as ex
from receipts.store import Store
from receipts.types import Claim
from receipts.verify import named_in_draft, verify, with_draft_company

ROOT = Path(__file__).resolve().parents[1]
BAC = (ROOT / "tests/fixtures/bac_q3_2026_summary.txt").read_text()


@pytest.fixture(scope="module")
def store():
    s = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield s
    s.close()


def claim(text, span, metric, value_text, value, unit, scale, comparison="none", entity=""):
    start = text.index(span)
    return Claim(id="c1", metric=metric, metric_text="Net interest income", value_text=value_text, value=value,
                 unit=unit, scale=scale, period_text="Q3 2026", period_type="quarter", comparison=comparison,
                 basis=None, forward_looking=False, entity=entity, segment=None, span_text=span,
                 span_start=start, span_end=start + len(span))


def test_the_inferred_context_takes_the_drafts_only_company(store):
    context = with_draft_company(ex.infer_context(BAC), BAC, store)
    assert context == "Bank of America, Q3 2026"


def test_company_less_figures_check_as_that_companys(store):
    context = with_draft_company(ex.infer_context(BAC), BAC, store)
    named = named_in_draft(BAC, store)
    level = claim(BAC, "Net interest income increased 8% to $16.4 billion", "net_interest_income", "$16.4 billion",
                  16.4, "USD", "billions")
    growth = claim(BAC, "Net interest income increased 8%", "net_interest_income", "8%", 8, "%", "units", "YoY")
    for c in (level, growth):
        v = verify(c, store, context=context, draft=BAC, named=named)
        assert (v.status, v.reason) == ("verified", "match"), v.explanation
    # Without the company the same figure stays "?", as before.
    v = verify(level, store, context=ex.infer_context(BAC), draft=BAC, named=named)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous")


def test_two_companies_or_a_named_context_or_a_callers_context_stay_as_they_are(store):
    # A title naming two companies gives no subject, and neither does a draft naming two without a title company.
    two = "Bank of America and JPMorgan Chase: third-quarter results\n\nNet interest income increased 8% to $16.4 billion."
    assert len(named_in_draft(two, store)) == 2
    assert with_draft_company("Q3 2026", two, store) == "Q3 2026"
    # A title naming one company keeps it as the subject even when another is named later.
    titled = BAC + "\nJPMorgan Chase also reported results for the quarter."
    assert with_draft_company("Q3 2026", titled, store) == "Bank of America, Q3 2026"
    assert with_draft_company("Microsoft Corp., quarter ended September 30, 2026", BAC, store) == (
        "Microsoft Corp., quarter ended September 30, 2026")
    assert with_draft_company(None, "Revenue rose 5% in the quarter.", store) is None


def test_a_title_company_is_the_subject_even_with_partners_and_rivals_named_later(store):
    amd = (ROOT / "tests/fixtures/amd_q2_2026_summary.txt").read_text()
    assert len(named_in_draft(amd, store)) > 1  # AMD, Microsoft, NVIDIA, Intel
    context = with_draft_company(ex.infer_context(amd), amd, store)
    assert context.startswith("AMD") or context.startswith("Advanced Micro Devices"), context
    named = named_in_draft(amd, store)
    span = "gaming revenue declined 31% to $779 million"
    start = amd.index(span)
    c = Claim(id="c1", metric="segment_revenue", metric_text="gaming revenue", value_text="$779 million", value=779,
              unit="USD", scale="millions", period_text="Q2 2026", period_type="quarter", comparison="none",
              basis=None, forward_looking=False, entity="", segment="Gaming", span_text=span, span_start=start,
              span_end=start + len(span))
    v = verify(c, store, context=context, draft=amd, named=named)
    assert v.reason != "ambiguous" or "which company" not in (v.explanation or ""), v.explanation


def test_a_roundup_title_names_no_single_subject(store):
    roundup = ("Big Tech and big banks: the June-quarter scorecard\n\nJPMorgan Chase kicked off the season. Alphabet "
               "followed on July 22. Microsoft capped the stretch.")
    assert with_draft_company("quarter ended June 30, 2026", roundup, store) == "quarter ended June 30, 2026"
