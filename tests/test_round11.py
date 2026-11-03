"""Round 11: seven "?" gaps from the owner's live drafts (Alphabet, Centene, NVIDIA, Microsoft), each where NoCap's
evidence holds the figure, fixed both ways on the real snapshot with constructed claims (as tests/test_round10b.py).
"""
from datetime import date
from pathlib import Path

import pytest

from receipts import release_text as rt
from receipts import verify as V
from receipts.coverage import numeric_coverage
from receipts.store import Store
from receipts.types import Claim, Fact
from test_verify import FakeStore, utc

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def store():
    s = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield s
    s.close()


def make(draft, span, text, value, **kw):
    start = draft.index(span)
    args = dict(id="c1", metric="other", metric_text="", value_text=text, value=value, unit="USD", scale="units",
                period_text="", period_type="quarter", comparison="none", basis=None, forward_looking=False,
                entity="", segment=None, span_text=span, span_start=start, span_end=start + len(span))
    args.update(kw)
    return Claim(**args)


def check(store, draft, claim, context):
    named = V.named_in_draft(draft, store)
    return V.verify(claim, store, context=context, draft=draft, **({"named": named} if named else {}))


def status(v):
    return {"verified": "✓", "wrong": "✗", "unverifiable": "?", "approximate": "≈"}[v.status]


def planted(draft, claim, text, value):
    """The claim with another figure in its place (the same length, so offsets hold)."""
    assert len(text) == len(claim.value_text)
    at = claim.span_start + claim.span_text.index(claim.value_text)
    draft = draft[:at] + text + draft[at + len(claim.value_text):]
    return draft, claim.model_copy(update={"value_text": text, "value": value,
                                           "span_text": draft[claim.span_start:claim.span_end]})


# ---------------------------------------------------------------------------
# 1. A named to-common net income across the change its clause states
# ---------------------------------------------------------------------------
GOOG = ("Alphabet Q3 2026\nHowever, net income available to common stockholders declined 25% to $26.17 billion. "
        "Operating cash flow remained positive at $42.29 billion.")
GOOG_CONTEXT = "Alphabet, Q3 2026"
TO_COMMON = dict(metric="net_income", metric_text="net income available to common stockholders", scale="billions",
                 entity="Alphabet", period_text="Q3 2026")


def test_named_to_common_line_is_checked_against_that_line(store):
    claim = make(GOOG, "net income available to common stockholders declined 25% to $26.17 billion", "$26.17 billion",
                 26.17, **TO_COMMON)
    v = check(store, GOOG, claim, GOOG_CONTEXT)
    assert status(v) == "✓" and "available to common stockholders" in v.explanation, v.explanation
    draft, wrong = planted(GOOG, claim, "$27.17 billion", 27.17)
    v = check(store, draft, wrong, GOOG_CONTEXT)
    assert status(v) == "✗" and "$26.17 billion" in v.explanation, v.explanation


def test_the_named_lines_change_is_never_checked_against_plain_net_income(store):
    claim = make(GOOG, "net income available to common stockholders declined 25%", "25%", 25, metric="other",
                 metric_text="net income available to common stockholders declined", unit="%", comparison="YoY",
                 entity="Alphabet", period_text="Q3 2026")
    assert status(check(store, GOOG, claim, GOOG_CONTEXT)) == "?"   # no prior-year to-common fact is held


def test_plain_net_income_across_a_change_is_read_as_before(store):
    draft = "Alphabet Q3 2026\nNet income declined 24% to $26.47 billion."
    claim = make(draft, "Net income declined 24% to $26.47 billion", "$26.47 billion", 26.47, metric="net_income",
                 metric_text="Net income", scale="billions", entity="Alphabet", period_text="Q3 2026")
    assert status(check(store, draft, claim, GOOG_CONTEXT)) == "✓"


# ---------------------------------------------------------------------------
# 2. "remained positive at $X": the sign of a positive level
# ---------------------------------------------------------------------------
def ocf(draft, text="$42.29 billion", value=42.29):
    return make(draft, f"Operating cash flow remained positive at {text}", text, value, metric="operating_cash_flow",
                metric_text="Operating cash flow", scale="billions", entity="Alphabet", period_text="Q3 2026")


def test_positive_restates_a_positive_levels_sign(store):
    assert status(check(store, GOOG, ocf(GOOG), GOOG_CONTEXT)) == "✓"
    draft, wrong = planted(GOOG, ocf(GOOG), "$44.29 billion", 44.29)
    assert status(check(store, draft, wrong, GOOG_CONTEXT)) == "✗"


NEG = 5


def neg_store(value):
    f = Fact(cik=NEG, ticker="NEGC", metric="operating_cash_flow", basis="GAAP", value=value, unit="USD",
             period_start=date(2026, 7, 1), period_end=date(2026, 9, 30), fiscal_year=2026, fiscal_period="Q3",
             source="xbrl", derived_from=[], form="10-Q", accession="0000000005-26-000001",
             accepted_at=utc("2026-10-30T12:00:00Z"), url="https://www.sec.gov/Archives/edgar/data/5/x.htm")
    return FakeStore([f], aliases={"negco": (NEG, "NEGC")},
                     periods={(NEG, 2026, "Q3"): (date(2026, 7, 1), date(2026, 9, 30))})


def test_positive_never_lets_a_negative_filed_value_pass():
    span = "NegCo's operating cash flow remained positive at $42.29 billion"
    claim = Claim(id="c1", metric="operating_cash_flow", metric_text="operating cash flow", value_text="$42.29 billion",
                  value=42.29, unit="USD", scale="billions", period_text="Q3 2026", period_type="quarter",
                  comparison="none", basis=None, forward_looking=False, entity="NegCo", segment=None,
                  span_text=span, span_start=0, span_end=len(span))
    assert V.verify(claim, neg_store(42.29e9)).status == "verified"
    assert V.verify(claim, neg_store(-42.29e9)).status == "wrong"


# ---------------------------------------------------------------------------
# 3. A loss in a comparison base: the base period's negative net income
# ---------------------------------------------------------------------------
CNC = ("Centene Q3 2026\nThe company reported a net loss attributable to shareholders of $71 million, significantly "
       "improving from a $6.63 billion loss in Q3 2025.")
CNC_CONTEXT = "Centene, Q3 2026"


def cnc(draft, text="$6.63 billion", value=6.63, metric="net_income"):
    return make(draft, f"significantly improving from a {text} loss in Q3 2025", text, value, metric=metric,
                metric_text="loss", scale="billions", entity="Centene", period_text="Q3 2025")


@pytest.mark.parametrize("metric", ["net_income", "other"])
def test_a_base_periods_loss_is_its_negative_net_income(store, metric):
    v = check(store, CNC, cnc(CNC, metric=metric), CNC_CONTEXT)
    assert status(v) == "✓" and "-$6.631 billion" in v.explanation, v.explanation
    draft, wrong = planted(CNC, cnc(CNC, metric=metric), "$6.93 billion", 6.93)
    assert status(check(store, draft, wrong, CNC_CONTEXT)) == "✗"


def test_the_sign_never_flips():
    span = "CNCo reported a net loss of $71 million, improving from a $6.63 billion loss in Q3 2025"
    base = Fact(cik=NEG, ticker="NEGC", metric="net_income", basis="GAAP", value=6.631e9, unit="USD",
                period_start=date(2025, 7, 1), period_end=date(2025, 9, 30), fiscal_year=2025, fiscal_period="Q3",
                source="xbrl", derived_from=[], form="10-Q", accession="0000000005-25-000001",
                accepted_at=utc("2025-10-30T12:00:00Z"), url="https://www.sec.gov/Archives/edgar/data/5/y.htm")
    s = FakeStore([base], aliases={"cnco": (NEG, "NEGC")},
                  periods={(NEG, 2025, "Q3"): (date(2025, 7, 1), date(2025, 9, 30))})
    i = span.index("$6.63 billion")
    claim = Claim(id="c1", metric="net_income", metric_text="loss", value_text="$6.63 billion", value=6.63, unit="USD",
                  scale="billions", period_text="Q3 2025", period_type="quarter", comparison="none", basis=None,
                  forward_looking=False, entity="CNCo", segment=None, span_text=span, span_start=0,
                  span_end=len(span))
    v = V.verify(claim, s, draft=span)
    assert v.status == "wrong", v.explanation  # a filed income of +$6.631 billion is no $6.63 billion loss
    assert i > 0


# ---------------------------------------------------------------------------
# 4. Exact measure aliases in the release-statement matcher
# ---------------------------------------------------------------------------
CNC2 = ("Centene Q3 2026\nThe adjusted selling, general, and administrative expense ratio decreased from 7.0% to 6.5%. "
        "The company's health benefits ratio (HBR), which measures medical expenses relative to premium revenue, "
        "remained at 92.7%.")


def test_sga_and_hbr_read_as_their_short_names(store):
    sga = make(CNC2, "The adjusted selling, general, and administrative expense ratio decreased from 7.0% to 6.5%",
               "6.5%", 6.5, metric_text="adjusted selling, general, and administrative expense ratio", unit="%",
               basis="non-GAAP", entity="Centene", period_text="Q3 2026")
    v = check(store, CNC2, sga, CNC_CONTEXT)
    assert (status(v), v.reason) == ("✓", "release_statement") and "SG&A" in v.explanation, v.explanation
    hbr = make(CNC2, "health benefits ratio (HBR), which measures medical expenses relative to premium revenue, "
                     "remained at 92.7%", "92.7%", 92.7, metric_text="health benefits ratio (HBR)", unit="%",
               entity="Centene", period_text="Q3 2026")
    v = check(store, CNC2, hbr, CNC_CONTEXT)
    assert (status(v), v.reason) == ("✓", "release_statement") and "HBR" in v.explanation, v.explanation


def test_a_wrong_ratio_or_another_measure_is_never_confirmed(store):
    draft = CNC2.replace("to 6.5%", "to 6.7%")
    sga = make(draft, "The adjusted selling, general, and administrative expense ratio decreased from 7.0% to 6.7%",
               "6.7%", 6.7, metric_text="adjusted selling, general, and administrative expense ratio", unit="%",
               basis="non-GAAP", entity="Centene", period_text="Q3 2026")
    assert status(check(store, draft, sga, CNC_CONTEXT)) != "✓"
    other = CNC2.replace("selling, general, and administrative", "selling and marketing")
    sm = make(other, "The adjusted selling and marketing expense ratio decreased from 7.0% to 6.5%", "6.5%", 6.5,
              metric_text="adjusted selling and marketing expense ratio", unit="%", basis="non-GAAP",
              entity="Centene", period_text="Q3 2026")
    assert status(check(store, other, sm, CNC_CONTEXT)) != "✓"
    long = "Selling, general and administrative"
    assert rt._aliased(long) == "SG&A".ljust(len(long))  # offsets kept


# ---------------------------------------------------------------------------
# 5. A figure quoted without its clause's basis word; reporting verbs in a claim's sentence
# ---------------------------------------------------------------------------
MS = "Microsoft Q1 FY2027\nExcluding the impact of investments in OpenAI, adjusted EPS increased 18% to $4.89."
MS_CONTEXT = "Microsoft, Q1 FY2027"


def eps(draft, span, text, value, metric_text="EPS"):
    return make(draft, span, text, value, metric="eps_diluted", metric_text=metric_text, unit="USD/share",
                entity="Microsoft", period_text="Q1 FY2027")


@pytest.mark.parametrize("span", ["to $4.89", "increased 18% to $4.89"])
def test_a_figure_takes_its_clause_subjects_adjusted_basis(store, span):
    v = check(store, MS, eps(MS, span, "$4.89", 4.89), MS_CONTEXT)
    assert status(v) == "✓" and "non-GAAP" in v.explanation, v.explanation
    draft, wrong = planted(MS, eps(MS, span, "$4.89", 4.89), "$4.79", 4.79)
    assert status(check(store, draft, wrong, MS_CONTEXT)) == "✗"


def test_a_gaap_subject_keeps_its_figure_gaap(store):
    draft = "Microsoft Q1 FY2027\nGAAP EPS increased 32% to $4.92, while adjusted EPS increased 18% to $4.89."
    v = check(store, draft, eps(draft, "to $4.92", "$4.92", 4.92), MS_CONTEXT)
    assert status(v) == "✓" and "non-GAAP" not in v.explanation, v.explanation


def test_a_reporting_verb_in_the_claims_sentence_is_no_measure_word(store):
    draft = ("NVIDIA Q2 FY2027\nData Center led the way, generating a record $89.0 billion in revenue, representing a "
             "117% year-over-year increase.")
    claim = make(draft, "representing a 117% year-over-year increase", "117%", 117, metric="revenue_growth",
                 metric_text="Data Center revenue", unit="%", comparison="YoY", entity="NVIDIA", segment="Data Center",
                 period_text="Q2 FY2027")
    cik, ticker = store.resolve_entity("NVIDIA")
    period = rt.claim_period(claim, store, cik, "NVIDIA, Q2 FY2027", None, True)
    st = rt.release_statement(claim, store, (cik, ticker), period, None, V.draft_line(claim, draft))
    assert st is not None and "117%" in st.quote and "from a year ago" in st.quote
    wrong = claim.model_copy(update={"value_text": "118%", "value": 118})
    assert rt.release_statement(wrong, store, (cik, ticker), period, None, None) is None


# ---------------------------------------------------------------------------
# 6. Coverage: a product name's numeral is no figure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,candidates", [
    ("Microsoft 365 commercial cloud revenue increased 18%.", 1),
    ("Alphabet said Gemini 4 drove usage.", 0),
    ("Windows 11 adoption grew while iPhone 17 sales rose 4%.", 1),
    ("Q4 2026 revenue was $365 million.", 1),
    ("Nearly 30 new markets opened.", 1),
    ("Opened 30 new stores in FY27.", 1),
])
def test_product_numerals_are_no_candidates(text, candidates):
    assert numeric_coverage(text, [])["candidate_count"] == candidates
