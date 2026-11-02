"""Round 10c: a sentence's basis or scope words reach the figures they govern beyond the claim's own span.

Three false "✗" the held-out fresh re-check found once rounds 10a/10b let the claims be checked, each fixed both ways
on the real snapshot (constructed claims, the draft sentence given as check_detailed passes it):
- Expand Energy: ", or $1.46 per diluted share" restates adjusted net income, so it is adjusted EPS;
- DoorDash: "Excluding Deliveroo, ..." puts the sentence's revenue growth on a company-defined basis;
- Starbucks: "North America net revenues grew 7% ..., with operating margin reaching 13.6%" is North America's margin.
"""
from pathlib import Path

import pytest

from receipts import verify as V
from receipts.store import Store
from receipts.types import Claim

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def store():
    s = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield s
    s.close()


def check(store, draft, span, text, value, context, **kw):
    start = draft.index(span)
    args = dict(id="c1", metric="other", metric_text="", value_text=text, value=value, unit="USD", scale="units",
                period_text="", period_type="quarter", comparison="none", basis=None, forward_looking=False,
                entity="", segment=None, span_text=span, span_start=start, span_end=start + len(span))
    args.update(kw)
    named = V.named_in_draft(draft, store)
    return V.verify(Claim(**args), store, context=context, draft=draft, **({"named": named} if named else {}))


def status(v):
    return {"verified": "✓", "wrong": "✗", "unverifiable": "?", "approximate": "≈"}[v.status]


# ---------------------------------------------------------------------------
# 1. A per-share restatement takes the basis of the amount it restates
# ---------------------------------------------------------------------------
EXE_CONTEXT = "Expand Energy, Q3 2026"
EPS = dict(metric="eps_diluted", metric_text="diluted earnings per share", unit="USD/share", entity="Expand Energy",
           period_text="Q3 2026")


def exe(sentence):
    return f"Expand Energy Q3 2026\n{sentence}"


@pytest.mark.parametrize("sentence,span", [
    ("Expand Energy reported net income of $519 million, or $2.24 per fully diluted share, while adjusted net income "
     "came to $338 million, or $1.46 per diluted share.", "or $1.46 per diluted share"),
    ("Adjusted earnings were $338 million, or $1.46 per share.", "or $1.46 per share"),
    ("Non-GAAP net income was $338 million, or $1.46 per diluted share.", "or $1.46 per diluted share"),
    ("Adjusted net income was $338 million ($1.46 per diluted share).", "$1.46 per diluted share"),
])
def test_an_adjusted_amounts_restatement_is_never_checked_as_gaap_eps(store, sentence, span):
    v = check(store, exe(sentence), span, "$1.46", 1.46, EXE_CONTEXT, **EPS)
    assert (status(v), v.reason) == ("?", "ambiguous"), v.explanation
    assert "non-GAAP" in v.explanation


@pytest.mark.parametrize("sentence,span,text,value,outcome", [
    ("Expand Energy reported net income of $519 million, or $2.24 per fully diluted share, while adjusted net income "
     "came to $338 million, or $1.46 per diluted share.", "or $2.24 per fully diluted share", "$2.24", 2.24, "✓"),
    ("GAAP net income was $519 million, or $2.24 per diluted share.", "or $2.24 per diluted share", "$2.24", 2.24, "✓"),
    ("Net income was $519 million, or $1.46 per diluted share.", "or $1.46 per diluted share", "$1.46", 1.46, "✗"),
])
def test_a_gaap_amounts_restatement_stays_gaap(store, sentence, span, text, value, outcome):
    assert status(check(store, exe(sentence), span, text, value, EXE_CONTEXT, **EPS)) == outcome


# ---------------------------------------------------------------------------
# 2. An exclusion that opens a sentence or clause scopes the figures after it
# ---------------------------------------------------------------------------
DASH_CONTEXT = "DoorDash, Q3 2026"
GROWTH = dict(metric="revenue_growth", metric_text="revenue", unit="%", comparison="YoY", entity="DoorDash",
              period_text="Q3 2026")
REST = "the company said total orders grew 17%, with Marketplace GOV up 23% and revenue also advancing 23%."


@pytest.mark.parametrize("prefix", ["Excluding Deliveroo, ", "Excluding the impact of Deliveroo, ", "Ex-Deliveroo, ",
                                    "Excluding acquisitions, ", "Orders were strong; excluding Deliveroo, "])
def test_a_leading_exclusion_puts_the_sentence_on_a_company_basis(store, prefix):
    draft = f"DoorDash Q3 2026\n{prefix}{REST[0].upper() if not prefix.endswith(', ') else REST[0]}{REST[1:]}"
    v = check(store, draft, "revenue also advancing 23%", "23%", 23, DASH_CONTEXT, **GROWTH)
    assert status(v) == "?" and "company-defined" in v.explanation, v.explanation


@pytest.mark.parametrize("draft", [
    "DoorDash Q3 2026\nThe company said total orders grew 17%, with revenue also advancing 23%.",   # no prefix
    "DoorDash Q3 2026\nExcluding Deliveroo, orders grew 17%. The company said revenue also advanced 23%.",  # another
    "DoorDash Q3 2026\nThe company said revenue also advanced 23%, excluding nothing.",          # not a leading one
])
def test_without_a_leading_exclusion_the_figure_is_checked(store, draft):
    span = "revenue also advancing 23%" if "advancing" in draft else "revenue also advanced 23%"
    v = check(store, draft, span, "23%", 23, DASH_CONTEXT, **GROWTH)
    assert status(v) == "✗" and "34.0%" in v.explanation, v.explanation
    right = check(store, draft.replace("23%", "34%"), span.replace("23%", "34%"), "34%", 34, DASH_CONTEXT, **GROWTH)
    assert status(right) == "✓", right.explanation


# ---------------------------------------------------------------------------
# 3. A segment subject governs a later ", with ..." clause
# ---------------------------------------------------------------------------
SBUX_CONTEXT = "Starbucks, Q3 FY2026"
MARGIN = dict(metric="other", metric_text="operating margin", unit="%", entity="Starbucks", period_text="Q3 FY2026")


@pytest.mark.parametrize("segment", [None, "North America"])
def test_a_with_clause_margin_is_the_subject_segments(store, segment):
    draft = ("Starbucks Q3 FY2026\nBy segment, North America net revenues grew 7% to $7.4 billion, with operating "
             "margin reaching 13.6%.")
    v = check(store, draft, "operating margin reaching 13.6%", "13.6%", 13.6, SBUX_CONTEXT,
              **(MARGIN | {"segment": segment}))
    assert status(v) == "?" and "North America" in v.explanation, v.explanation


@pytest.mark.parametrize("clause", ["with Starbucks' operating margin reaching 13.6%",
                                    "with consolidated operating margin reaching 13.6%"])
def test_a_with_clause_that_names_the_company_is_the_companys(store, clause):
    draft = f"Starbucks Q3 FY2026\nBy segment, North America net revenues grew 7% to $7.4 billion, {clause}."
    span = clause.split(" ", 1)[1]
    v = check(store, draft, span, "13.6%", 13.6, SBUX_CONTEXT, **MARGIN)
    assert status(v) == "✗" and "10.52%" in v.explanation, v.explanation
