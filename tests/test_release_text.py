"""Release-text evidence (receipts.release_text, the release-text hook of receipts.verify.verify).

A figure NoCap's filed facts can't check becomes ✓ (reason release_statement) only when the company's own 8-K earnings
release for its quarter states it in words; no match leaves the "?" exactly as it was, and text never makes a figure ✗.

The fixture index (tests/fixtures/release_text/index/) holds real units of Microsoft's Q1 FY2027 release, Costco's Q4
FY2026 and Q4 FY2025 releases and McDonald's Q2 and Q1 2026 releases, copied from data/snapshot/release_text. The three
drafts are the owner's, as pasted on the live site. Claims are built offline from synthetic model proposals through the
real extraction post-processing, as in tests/test_context_inference.py: no model, network or SEC call.

The test sentences of the comprehensive runs (eval/spot500) come from these same releases, so the true figures they
turn ✓ say nothing about accuracy; the planted-error check below says only that no planted error is confirmed.
"""
import gzip
import importlib
import json
import sys
from pathlib import Path

import pytest

from receipts import extract as ex
from receipts import release_text as rt
from receipts import stream
from receipts import verify as verifier
from receipts.store import Store
from receipts.types import Claim
from receipts.verify import verify

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/snapshot"
FIXTURE_INDEX = ROOT / "tests/fixtures/release_text/index"
REAL_INDEX = SNAPSHOT / "release_text"
MSFT = (ROOT / "tests/fixtures/msft_fy2027_q1_release.txt").read_text()
COST = (ROOT / "tests/fixtures/cost_fy2026_q4_summary_full.txt").read_text()
MCD = (ROOT / "tests/fixtures/mcd_q2_2026_summary_full.txt").read_text()
MSFT_URL = "https://www.sec.gov/Archives/edgar/data/789019/000119312526436122/msft-ex99_1.htm"
real_index = pytest.mark.skipif(not (REAL_INDEX / "manifest.json").is_file(), reason="no release-text index built")


def open_store(index):
    store = Store(SNAPSHOT / "facts.sqlite", SNAPSHOT / "pr_facts")
    if index is not None:
        store.release_text_dir = index
    return store


@pytest.fixture(scope="module")
def fixture_store():
    store = open_store(FIXTURE_INDEX)
    yield store
    store.close()


@pytest.fixture(scope="module", params=["fixture", pytest.param("real", marks=real_index)])
def any_store(request):
    store = open_store(FIXTURE_INDEX if request.param == "fixture" else None)
    yield store
    store.close()


@pytest.fixture(scope="module")
def real_store():
    if not (REAL_INDEX / "manifest.json").is_file():
        pytest.skip("no release-text index built")
    store = open_store(None)
    assert rt.index_dir(store) == REAL_INDEX
    yield store
    store.close()


def p(entity, metric, metric_text, value_text, value, unit, scale, span, *, period="", comparison="none", bases=(),
      segment=None, forward=False):
    return ex.ModelClaim(metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                         scale=scale, period_text=period, period_type="quarter", comparison=comparison,
                         bases=list(bases), forward_looking=forward, entity=entity, segment=segment, span_text=span)


def build(draft, proposals):
    """Claims located by the real post-processing, each in the passage that holds its span."""
    passages = ex.split_passages(draft)
    per = {}
    for prop in proposals:
        i = next(i for i, (_, text) in enumerate(passages) if prop.span_text in text)
        per.setdefault(i, []).append(prop)
    claims, _ = ex.build_claims([(passages[i][0], passages[i][1], per[i]) for i in sorted(per)], [])
    assert all(draft[c.span_start:c.span_end] == c.span_text for c in claims)
    return claims


def check(draft, claims, store, *, as_of=None, context=None):
    context = ex.infer_context(draft) if context is None else context
    named = verifier.named_in_draft(draft, store)
    return [verify(c, store, as_of=as_of, context=context, draft=draft, **({"named": named} if named else {}))
            for c in claims]


def outcome(v):
    return {"verified": "✓", "wrong": "✗", "unverifiable": "?", "approximate": "≈"}[v.status], v.reason


# ---------------------------------------------------------------------------
# The owner's three drafts: exactly which figures turn ✓
# ---------------------------------------------------------------------------
Q1 = "quarter ended September 30, 2026"
MS = "Microsoft Corp."
MSFT_PROPOSALS = [
    p(MS, "revenue", "Revenue", "$92.3 billion", 92.3, "USD", "billions", "Revenue was $92.3 billion", period=Q1),
    p(MS, "revenue_growth", "Revenue", "19%", 19, "%", "units", "Revenue was $92.3 billion and increased 19%",
      period=Q1, comparison="YoY"),
    p(MS, "operating_income", "Operating income", "$45.9 billion", 45.9, "USD", "billions",
      "Operating income was $45.9 billion", period=Q1),
    p(MS, "operating_income", "Operating income increased", "21%", 21, "%", "units",
      "Operating income was $45.9 billion and increased 21%", period=Q1, comparison="YoY"),
    p(MS, "operating_income", "up in constant currency", "22%", 22, "%", "units",
      "Operating income was $45.9 billion and increased 21% (up 22% in constant currency)", period=Q1,
      comparison="YoY"),
    p(MS, "net_income", "Net income", "$36.6 billion", 36.6, "USD", "billions",
      "Net income was $36.6 billion and increased 32% on a GAAP basis", period=Q1, bases=["GAAP"]),
    p(MS, "net_income", "Net income increased", "32%", 32, "%", "units",
      "Net income was $36.6 billion and increased 32% on a GAAP basis", period=Q1, comparison="YoY", bases=["GAAP"]),
    p(MS, "net_income", "Net income", "$36.4 billion", 36.4, "USD", "billions",
      "was $36.4 billion and increased 18% (up 19% in constant currency) on a non-GAAP basis", period=Q1,
      bases=["non-GAAP"]),
    p(MS, "net_income", "Net income increased", "18%", 18, "%", "units",
      "was $36.4 billion and increased 18% (up 19% in constant currency) on a non-GAAP basis", period=Q1,
      comparison="YoY", bases=["non-GAAP"]),
    p(MS, "net_income", "Net income up in constant currency", "19%", 19, "%", "units",
      "was $36.4 billion and increased 18% (up 19% in constant currency) on a non-GAAP basis", period=Q1,
      comparison="YoY", bases=["non-GAAP"]),
    p(MS, "eps_diluted", "Diluted earnings per share", "$4.92", 4.92, "USD/share", "units",
      "Diluted earnings per share was $4.92 and increased 32% on a GAAP basis", period=Q1, bases=["GAAP"]),
    p(MS, "eps_diluted", "Diluted earnings per share increased", "32%", 32, "%", "units",
      "Diluted earnings per share was $4.92 and increased 32% on a GAAP basis", period=Q1, comparison="YoY",
      bases=["GAAP"]),
    p(MS, "eps_diluted", "Diluted earnings per share", "$4.89", 4.89, "USD/share", "units",
      "was $4.89 and increased 18% (up 19% in constant currency) on a non-GAAP basis", period=Q1,
      bases=["non-GAAP"]),
    p(MS, "eps_diluted", "Diluted earnings per share increased", "18%", 18, "%", "units",
      "was $4.89 and increased 18% (up 19% in constant currency) on a non-GAAP basis", period=Q1, comparison="YoY",
      bases=["non-GAAP"]),
    p(MS, "eps_diluted", "Diluted earnings per share increased in constant currency", "19%", 19, "%", "units",
      "was $4.89 and increased 18% (up 19% in constant currency) on a non-GAAP basis", period=Q1, comparison="YoY",
      bases=["non-GAAP"]),
    p("Microsoft", "segment_revenue", "Microsoft Cloud revenue", "$63.9 billion", 63.9, "USD", "billions",
      "Microsoft Cloud revenue of $63.9 billion", period=Q1, segment="Microsoft Cloud"),
    p("Microsoft", "revenue_growth", "growth", "30%", 30, "%", "units",
      "Microsoft Cloud revenue of $63.9 billion, with growth accelerating to 30%", period=Q1, comparison="YoY",
      segment="Microsoft Cloud"),
    p("Microsoft", "revenue_growth", "up in constant currency", "31%", 31, "%", "units",
      "with growth accelerating to 30% (up 31% in constant currency) year-over-year", period=Q1, comparison="YoY",
      segment="Microsoft Cloud"),
]
MSFT_EXPECTED = [  # (value_text, outcome) in document order; "match" is the filed-facts check, as before
    ("$92.3 billion", ("✓", "match")), ("19%", ("✓", "match")), ("$45.9 billion", ("✓", "match")),
    ("21%", ("✓", "match")), ("22%", ("✓", "release_statement")), ("$36.6 billion", ("✓", "match")),
    ("32%", ("✓", "match")), ("$36.4 billion", ("✓", "match")), ("18%", ("✓", "match")),
    ("19%", ("✓", "release_statement")), ("$4.92", ("✓", "match")), ("32%", ("✓", "match")),
    ("$4.89", ("✓", "match")), ("18%", ("✓", "match")), ("19%", ("✓", "release_statement")),
    ("$63.9 billion", ("✓", "release_statement")), ("30%", ("✓", "release_statement")),
    ("31%", ("✓", "release_statement")),
]

Q4 = "fourth quarter of fiscal year 2026"
LAST = "same period last year"
COST_PROPOSALS = [
    p("Costco", "revenue_growth", "quarterly net sales increased", "11.2%", 11.2, "%", "units",
      "Costco's quarterly net sales increased **11.2% year over year to $93.9 billion**", period=Q4, comparison="YoY"),
    p("Costco", "revenue", "quarterly net sales", "$93.9 billion", 93.9, "USD", "billions",
      "Costco's quarterly net sales increased **11.2% year over year to $93.9 billion**", period=Q4),
    p("Costco", "revenue", "quarterly net sales", "$84.4 billion", 84.4, "USD", "billions",
      "compared with $84.4 billion in the same period last year", period=LAST),
    p("Costco", "net_income", "Net income rose", "14.9%", 14.9, "%", "units",
      "Net income rose 14.9% to approximately $3.0 billion", period=Q4, comparison="YoY"),
    p("Costco", "net_income", "Net income", "$3.0 billion", 3.0, "USD", "billions",
      "Net income rose 14.9% to approximately $3.0 billion", period=Q4),
    p("Costco", "eps_diluted", "diluted earnings per share (EPS)", "$5.87", 5.87, "USD/share", "units",
      "diluted earnings per share (EPS) increased from $5.87 to $6.75", period=LAST),
    p("Costco", "eps_diluted", "diluted earnings per share (EPS)", "$6.75", 6.75, "USD/share", "units",
      "diluted earnings per share (EPS) increased from $5.87 to $6.75", period=Q4),
    p("Costco", "other", "one-time benefit from tariff refunds to EPS", "$0.15", 0.15, "USD/share", "units",
      "EPS included a one-time benefit of $0.15 from tariff refunds", period=Q4),
    p("Costco", "other", "EPS growth excluding this benefit", "12.4%", 12.4, "%", "units",
      "Excluding this benefit, EPS growth was approximately 12.4%", period=Q4, comparison="YoY"),
    p("Costco", "revenue_growth", "Membership fee revenue growth", "7.3%", 7.3, "%", "units",
      "Membership fee revenue grew 7.3% to $1.85 billion", period=Q4, comparison="YoY", segment="Membership fees"),
    p("Costco", "segment_revenue", "Membership fee revenue", "$1.85 billion", 1.85, "USD", "billions",
      "Membership fee revenue grew 7.3% to $1.85 billion", period=Q4, segment="Membership fees"),
    p("Costco", "other", "paid members", "84.1 million", 84.1, "other", "millions",
      "The company ended the quarter with 84.1 million paid members", period=Q4),
    p("Costco", "other", "cardholders", "150.4 million", 150.4, "other", "millions",
      "84.1 million paid members and 150.4 million cardholders", period=Q4),
    p("Costco", "other", "membership renewal rate", "92.3%", 92.3, "%", "units",
      "Its membership renewal rate reached 92.3% in the United States and Canada", period=Q4),
    p("Costco", "other", "membership renewal rate", "89.8%", 89.8, "%", "units",
      "Its membership renewal rate reached 92.3% in the United States and Canada and 89.8% worldwide", period=Q4),
    p("Costco", "other", "digitally enabled comparable sales increasing", "19.5%", 19.5, "%", "units",
      "digitally enabled comparable sales increasing 19.5% year over year", period=Q4, comparison="YoY"),
    p("Costco", "other", "Overall comparable sales grew", "9.4%", 9.4, "%", "units",
      "Overall comparable sales grew 9.4%", period=Q4),
    p("Costco", "other", "Overall comparable sales grew", "6.7%", 6.7, "%", "units",
      "Overall comparable sales grew 9.4%, or 6.7% after adjusting for gasoline prices and foreign exchange effects",
      period=Q4),
    p("Costco", "other", "global warehouse network to locations", "939", 939, "other", "units",
      "The company also expanded its global warehouse network to 939 locations", period=Q4),
    p("Costco", "gross_margin", "gross margin", "11.02%", 11.02, "%", "units",
      "Its gross margin declined slightly to 11.02%", period=Q4),
    p("Costco", "gross_margin", "gross margin", "11.13%", 11.13, "%", "units",
      "compared with 11.13% a year earlier", period="a year earlier"),
]
COST_EXPECTED = [
    ("11.2%", ("✓", "release_statement")), ("$93.9 billion", ("✓", "release_statement")),
    ("$84.4 billion", ("✓", "release_statement")),  # the Q4 FY2025 release's own net sales, not the base of FY2026's
    ("14.9%", ("✓", "match")), ("$3.0 billion", ("✓", "match")), ("$5.87", ("✓", "match")), ("$6.75", ("✓", "match")),
    ("$0.15", ("✓", "release_statement")),
    # not in the release's prose: the release states none of these
    ("12.4%", ("?", "no_evidence")), ("7.3%", ("?", "no_evidence")), ("$1.85 billion", ("?", "no_evidence")),
    ("84.1 million", ("?", "no_evidence")), ("150.4 million", ("?", "no_evidence")), ("92.3%", ("?", "no_evidence")),
    ("89.8%", ("?", "no_evidence")), ("19.5%", ("?", "no_evidence")), ("9.4%", ("?", "no_evidence")),
    ("6.7%", ("?", "no_evidence")),
    ("939", ("✓", "release_statement")),
    ("11.02%", ("?", "ambiguous")), ("11.13%", ("?", "ambiguous")),  # ambiguous: never asked
]

Q2 = "Q2 2026"
TTM = "over the trailing twelve months"
MC = "McDonald's"
MCD_PROPOSALS = [
    p(MC, "revenue_growth", "quarterly revenue increased approximately **4% year over year", "4%", 4, "%", "units",
      "McDonald's quarterly revenue increased approximately **4% year over year to $7.10 billion**", period=Q2,
      comparison="YoY"),
    p(MC, "revenue", "quarterly revenue", "$7.10 billion", 7.10, "USD", "billions",
      "McDonald's quarterly revenue increased approximately **4% year over year to $7.10 billion**", period=Q2),
    p(MC, "revenue", "quarterly revenue", "$6.84 billion", 6.84, "USD", "billions",
      "compared with $6.84 billion in the same period last year", period="in the same period last year"),
    p(MC, "net_income", "Net income rose 5%", "5%", 5, "%", "units", "Net income rose 5% to $2.36 billion",
      period=Q2, comparison="YoY"),
    p(MC, "net_income", "Net income", "$2.36 billion", 2.36, "USD", "billions", "Net income rose 5% to $2.36 billion",
      period=Q2),
    p(MC, "eps_diluted", "diluted earnings per share (EPS) increased 6%", "6%", 6, "%", "units",
      "diluted earnings per share (EPS) increased 6% from $3.14 to $3.32", period=Q2, comparison="YoY"),
    p(MC, "eps_diluted", "diluted earnings per share (EPS)", "$3.14", 3.14, "USD/share", "units",
      "diluted earnings per share (EPS) increased 6% from $3.14 to $3.32", period=Q2),
    p(MC, "eps_diluted", "diluted earnings per share (EPS)", "$3.32", 3.32, "USD/share", "units",
      "diluted earnings per share (EPS) increased 6% from $3.14 to $3.32", period=Q2),
    p(MC, "operating_income", "Operating income also grew 3%", "3%", 3, "%", "units",
      "Operating income also grew 3% to $3.34 billion", period=Q2, comparison="YoY"),
    p(MC, "operating_income", "Operating income", "$3.34 billion", 3.34, "USD", "billions",
      "Operating income also grew 3% to $3.34 billion", period=Q2),
    p("McDonald", "other", "Global comparable sales", "1.3%", 1.3, "%", "units",
      "Global comparable sales increased 1.3%", period=Q2),
    p("McDonald", "other", "U.S. comparable sales", "0.8%", 0.8, "%", "units", "U.S. comparable sales rose 0.8%",
      period=Q2),
    p("McDonald", "other", "International Operated Markets growth", "1.5%", 1.5, "%", "units",
      "while International Operated Markets recorded 1.5% growth", period=Q2),
    p("McDonald", "other", "International Developmental Licensed Markets growth", "1.9%", 1.9, "%", "units",
      "International Developmental Licensed Markets achieved 1.9% growth", period=Q2),
    p(MC, "other", "global systemwide sales increased", "5%", 5, "%", "units",
      "global systemwide sales increased 5% to approximately $37 billion", period=Q2),
    p(MC, "other", "global systemwide sales", "$37 billion", 37, "USD", "billions",
      "global systemwide sales increased 5% to approximately $37 billion", period=Q2),
    p(MC, "other", "loyalty markets", "70", 70, "other", "units", "Across 70 loyalty markets", period=TTM),
    p(MC, "other", "sales to loyalty members", "$40 billion", 40, "USD", "billions",
      "sales to loyalty members reached approximately $40 billion over the trailing twelve months", period=TTM),
    p(MC, "other", "growth of sales to loyalty members", "20%", 20, "%", "units",
      "representing growth of more than 20%", period=TTM),
    p(MC, "other", "active loyalty users increased", "13%", 13, "%", "units",
      "The number of active loyalty users increased 13% to nearly 220 million", period=Q2),
    p(MC, "other", "active loyalty users", "220 million", 220, "other", "millions",
      "The number of active loyalty users increased 13% to nearly 220 million", period=Q2),
]
MCD_EXPECTED = [
    ("4%", ("✓", "match")), ("$7.10 billion", ("✓", "match")), ("$6.84 billion", ("✓", "match")),
    ("5%", ("✓", "match")), ("$2.36 billion", ("✓", "match")), ("6%", ("✓", "match")), ("$3.14", ("✓", "match")),
    ("$3.32", ("✓", "match")), ("3%", ("✓", "match")), ("$3.34 billion", ("✓", "match")),
    ("1.3%", ("✓", "release_statement")), ("0.8%", ("✓", "release_statement")),
    # the release's bullets are comparable sales, and these two claims don't say comparable: qualifiers both ways
    ("1.5%", ("?", "no_evidence")), ("1.9%", ("?", "no_evidence")),
    ("5%", ("✓", "release_statement")), ("$37 billion", ("✓", "release_statement")),
    ("70", ("✓", "release_statement")),
    # the release's figures are Systemwide sales to loyalty members; the draft doesn't say systemwide
    ("$40 billion", ("?", "no_evidence")), ("20%", ("?", "no_evidence")),
    ("13%", ("✓", "release_statement")), ("220 million", ("✓", "release_statement")),
]


@pytest.mark.parametrize("draft,proposals,expected", [
    (MSFT, MSFT_PROPOSALS, MSFT_EXPECTED), (COST, COST_PROPOSALS, COST_EXPECTED), (MCD, MCD_PROPOSALS, MCD_EXPECTED),
], ids=["msft", "cost", "mcd"])
def test_the_three_drafts(any_store, draft, proposals, expected):
    claims = build(draft, proposals)
    got = [(c.value_text, outcome(v)) for c, v in zip(claims, check(draft, claims, any_store))]
    assert got == expected


def test_without_the_index_every_figure_keeps_its_filed_facts_verdict(tmp_path):
    store = open_store(tmp_path)  # an empty index directory
    try:
        for draft, proposals, expected in ((MSFT, MSFT_PROPOSALS, MSFT_EXPECTED), (COST, COST_PROPOSALS, COST_EXPECTED),
                                           (MCD, MCD_PROPOSALS, MCD_EXPECTED)):
            claims = build(draft, proposals)
            for (value_text, (mark, reason)), v in zip(expected, check(draft, claims, store)):
                if reason == "release_statement":
                    assert outcome(v) == ("?", "no_evidence"), value_text
                else:
                    assert outcome(v) == (mark, reason), value_text
    finally:
        store.close()


def test_the_receipt_quotes_the_release_and_links_to_edgar(fixture_store):
    claims = build(MSFT, MSFT_PROPOSALS)
    cloud = next(c for c in claims if c.value_text == "$63.9 billion")
    v = verify(cloud, fixture_store, context=ex.infer_context(MSFT), draft=MSFT)
    quote = "Microsoft Cloud revenue was $63.9 billion and increased 30% (up 31% in constant currency)"
    assert v.explanation == ("Matches Microsoft's 8-K earnings release for Q1 FY2027 (accepted Oct 28, 2026): "
                             f"“{quote}”")
    (f,) = v.evidence
    assert (f.source, f.form, f.accession, f.url, f.locator) == ("release_text", "8-K", "0001193125-26-436122",
                                                                 MSFT_URL, quote)
    assert f.accepted_at.isoformat() == "2026-10-28T20:04:44+00:00"
    assert (f.fiscal_year, f.fiscal_period, str(f.period_start), str(f.period_end)) == (2027, "Q1", "2026-07-01",
                                                                                        "2026-09-30")
    assert (f.value, f.unit, f.metric, f.basis, f.decimals) == (63.9e9, "USD", "segment_revenue", "GAAP", -8)
    # A constant-currency figure's receipt is marked non-GAAP; a bullet that takes its lead's measure quotes the lead
    cc = next(c for c in claims if c.value_text == "31%")
    assert verify(cc, fixture_store, context=ex.infer_context(MSFT), draft=MSFT).evidence[0].basis == "non-GAAP"
    mcd = build(MCD, MCD_PROPOSALS)
    us = next(c for c in mcd if c.value_text == "0.8%")
    v = verify(us, fixture_store, context=ex.infer_context(MCD), draft=MCD)
    assert v.evidence[0].locator == "Global comparable sales increased 1.3%: U.S. increased 0.8%"
    assert v.explanation.startswith("Matches McDonald's 8-K earnings release for Q2 2026 (accepted Aug 4, 2026)")


def test_the_receipt_streams_as_a_contract_shaped_verdict(fixture_store):
    claims = build(MSFT, MSFT_PROPOSALS)
    cloud = next(c for c in claims if c.value_text == "$63.9 billion")
    v = verify(cloud, fixture_store, context=ex.infer_context(MSFT), draft=MSFT)
    events = [{"event": "meta", "data": {"draft_hash": "x", "cached": False, "passages": 1, "as_of": None}},
              {"event": "claim", "data": {"claim": cloud.model_dump(mode="json")}},
              {"event": "verdict", "data": {"claim_id": cloud.id, "verdict": v.model_dump(mode="json")}},
              {"event": "done", "data": {"counts": {"verified": 1}}}]
    assert stream.valid_events(json.loads(json.dumps(events)))
    data = v.model_dump(mode="json")
    assert data["reason"] == "release_statement" and data["evidence"][0]["source"] == "release_text"


# ---------------------------------------------------------------------------
# Negatives: none of these may be ✓, and none becomes ✗
# ---------------------------------------------------------------------------
MSFT_CTX = "Microsoft Corp., quarter ended September 30, 2026"
MCD_CTX = "McDonald's, second quarter ended June 30, 2026"
COST_CTX = "Costco Wholesale, fourth quarter of fiscal year 2026"


def one(text, entity, metric, metric_text, value_text, value, unit, scale, *, comparison="none", bases=(), segment=None,
        forward=False, period=""):
    """A one-sentence draft and its claim at its own value_text."""
    (c,) = build(text, [p(entity, metric, metric_text, value_text, value, unit, scale, text, comparison=comparison,
                          bases=bases, segment=segment, forward=forward, period=period)])
    return text, c


NEGATIVES = {
    "wrong number": (one("Microsoft Cloud revenue was $63.8 billion.", "Microsoft", "segment_revenue",
                         "Microsoft Cloud revenue", "$63.8 billion", 63.8, "USD", "billions",
                         segment="Microsoft Cloud"), MSFT_CTX),
    "wrong number, finer precision": (one("Costco's net sales for the quarter were $93.87 billion.", "Costco",
                                          "revenue", "net sales", "$93.87 billion", 93.87, "USD", "billions"), COST_CTX),
    "wrong segment": (one("U.S. comparable sales increased 1.5%.", "McDonald's", "other", "U.S. comparable sales",
                          "1.5%", 1.5, "%", "units"), MCD_CTX),  # 1.5% is International Operated Markets'
    "segment figure as the total": (one("McDonald's comparable sales increased 0.8%.", "McDonald's", "other",
                                        "comparable sales", "0.8%", 0.8, "%", "units"), MCD_CTX),  # the U.S. figure
    "lead borrowed by a sibling bullet": (one("Global comparable sales increased 5%.", "McDonald's", "other",
                                              "Global comparable sales", "5%", 5, "%", "units"), MCD_CTX),
    "wrong direction": (one("Microsoft Cloud revenue decreased 30%.", "Microsoft", "revenue_growth",
                            "Microsoft Cloud revenue", "30%", 30, "%", "units", comparison="YoY",
                            segment="Microsoft Cloud"), MSFT_CTX),
    "constant currency missing": (one("Microsoft Cloud revenue increased 31%.", "Microsoft", "revenue_growth",
                                      "Microsoft Cloud revenue", "31%", 31, "%", "units", comparison="YoY",
                                      segment="Microsoft Cloud"), MSFT_CTX),
    "constant currency on the reported figure": (one("Operating income increased 21% in constant currency.",
                                                     "Microsoft Corp.", "operating_income", "Operating income", "21%",
                                                     21, "%", "units", comparison="YoY"), MSFT_CTX),
    "GAAP label on an adjusted figure": (one("Net income increased 19% in constant currency on a GAAP basis.",
                                             "Microsoft Corp.", "net_income", "Net income", "19%", 19, "%", "units",
                                             comparison="YoY", bases=["GAAP"]), MSFT_CTX),
    "sequential change": (one("Microsoft Cloud revenue grew 30% sequentially.", "Microsoft", "revenue_growth",
                              "Microsoft Cloud revenue", "30%", 30, "%", "units", comparison="QoQ",
                              segment="Microsoft Cloud"), MSFT_CTX),
    "another quarter's release": (one("U.S. comparable sales rose 3.9%.", "McDonald's", "other",
                                      "U.S. comparable sales", "3.9%", 3.9, "%", "units"), MCD_CTX),  # Q1's figure
    "a comparison base is not the current figure": (one("Costco's net sales for the quarter were $84.4 billion.",
                                                        "Costco", "revenue", "net sales", "$84.4 billion", 84.4, "USD",
                                                        "billions"), COST_CTX),
    "a bare figure is not an over figure": (one(
        "Systemwide sales to loyalty members for the trailing twelve months increased 20%.", "McDonald's", "other",
        "Systemwide sales to loyalty members", "20%", 20, "%", "units", period=TTM), MCD_CTX),
    "guidance wording": (one("Microsoft expects Microsoft Cloud revenue of $63.9 billion.", "Microsoft",
                             "segment_revenue", "Microsoft Cloud revenue", "$63.9 billion", 63.9, "USD", "billions",
                             segment="Microsoft Cloud"), MSFT_CTX),
    "the release's fiscal-year figure": (one("Costco's net sales increased 10.1%.", "Costco", "revenue_growth",
                                             "net sales", "10.1%", 10.1, "%", "units", comparison="YoY"), COST_CTX),
}


@pytest.mark.parametrize("case", NEGATIVES, ids=list(NEGATIVES))
def test_no_matching_release_statement_leaves_the_question_mark(any_store, case):
    (text, c), context = NEGATIVES[case]
    v = verify(c, any_store, context=context, draft=text)
    assert v.status == "unverifiable" and v.reason != "release_statement", (case, v.explanation)


def test_positive_controls_of_the_negatives(fixture_store):
    """The same wording with the release's own figure and qualifiers is ✓, so the negatives fail for their reason."""
    for text, entity, metric, words, value_text, value, unit, scale, kw, context in [
        ("Systemwide sales to loyalty members for the trailing twelve months increased more than 20%.", "McDonald's",
         "other", "Systemwide sales to loyalty members", "20%", 20, "%", "units", {"period": TTM}, MCD_CTX),
        ("Microsoft Cloud revenue increased 31% in constant currency.", "Microsoft", "revenue_growth",
         "Microsoft Cloud revenue", "31%", 31, "%", "units", {"comparison": "YoY", "segment": "Microsoft Cloud"},
         MSFT_CTX),
        ("U.S. comparable sales increased 0.8%.", "McDonald's", "other", "U.S. comparable sales", "0.8%", 0.8, "%",
         "units", {}, MCD_CTX),
        ("Costco's net sales for the quarter were $93.9 billion.", "Costco", "revenue", "net sales", "$93.9 billion",
         93.9, "USD", "billions", {}, COST_CTX),
    ]:
        draft, c = one(text, entity, metric, words, value_text, value, unit, scale, **kw)
        v = verify(c, fixture_store, context=context, draft=draft)
        assert (v.status, v.reason) == ("verified", "release_statement"), (text, v.explanation)


def test_a_release_accepted_after_as_of_is_not_evidence(fixture_store):
    claims = build(MSFT, MSFT_PROPOSALS)
    cloud = next(c for c in claims if c.value_text == "$63.9 billion")
    before = verify(cloud, fixture_store, as_of="2026-10-28T20:00:00Z", context=MSFT_CTX, draft=MSFT)
    assert outcome(before) == ("?", "no_evidence")  # accepted 20:04:44 UTC: "?", never ◷ from text
    after = verify(cloud, fixture_store, as_of="2026-10-28T20:05:00Z", context=MSFT_CTX, draft=MSFT)
    assert outcome(after) == ("✓", "release_statement") and after.as_of.isoformat() == "2026-10-28T20:05:00+00:00"


def test_forward_looking_ambiguous_and_unresolved_claims_are_never_asked(fixture_store):
    text = "Microsoft Cloud revenue of $63.9 billion"
    cloud = ("Microsoft", "segment_revenue", "Microsoft Cloud revenue", "$63.9 billion", 63.9, "USD", "billions")
    _, guided = one(text, *cloud, forward=True, segment="Microsoft Cloud")
    assert outcome(verify(guided, fixture_store, context=MSFT_CTX, draft=text)) == ("?", "forward_looking")
    _, nobody = one(text, "the company", *cloud[1:], segment="Microsoft Cloud")
    assert outcome(verify(nobody, fixture_store, context=None, draft=text)) == ("?", "ambiguous")
    _, no_period = one(text, *cloud, segment="Microsoft Cloud")
    assert outcome(verify(no_period, fixture_store, context="Microsoft Corp.", draft=text))[0] == "?"


def test_a_failing_index_never_changes_a_verdict(fixture_store, monkeypatch):
    claims = build(MSFT, MSFT_PROPOSALS)
    cloud = next(c for c in claims if c.value_text == "$63.9 billion")

    def boom(*_args, **_kw):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(rt, "release_statement", boom)
    assert outcome(verify(cloud, fixture_store, context=MSFT_CTX, draft=MSFT)) == ("?", "no_evidence")


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------
def test_units_load_lazily_and_skip_bad_lines(tmp_path):
    good = json.loads((FIXTURE_INDEX / "MSFT.jsonl").read_text().splitlines()[3])
    rows = [json.dumps(good), "{not json", json.dumps({**good, "fiscal_period": "FY"}),
            json.dumps({**good, "url": "https://example.com/x.htm"}), json.dumps({k: v for k, v in good.items()
                                                                                  if k != "accepted_at"})]
    with gzip.open(tmp_path / "MSFT.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")

    class Plain:  # any store with a release_text_dir
        release_text_dir = tmp_path

    (unit,) = rt.units(Plain(), "MSFT")
    assert (unit.accession, unit.fiscal_period, unit.text) == (good["accession"], "Q1", good["text"])
    assert rt.units(Plain(), "msft") == (unit,)
    assert rt.units(Plain(), "AAPL") == () and rt.units(Plain(), "../MSFT") == ()


def test_the_index_directory(tmp_path, monkeypatch):
    class Bare:  # a test double without a data directory reads no release text
        pass

    monkeypatch.delenv(rt.ENV_DIR, raising=False)
    assert rt.index_dir(Bare()) is None and rt.units(Bare(), "MSFT") == ()
    store = open_store(None)
    try:
        assert rt.index_dir(store) == (REAL_INDEX if REAL_INDEX.is_dir() else None)
        monkeypatch.setenv(rt.ENV_DIR, str(tmp_path))
        assert rt.index_dir(store) == tmp_path and rt.index_dir(Bare()) == tmp_path
        store.release_text_dir = FIXTURE_INDEX
        assert rt.index_dir(store) == FIXTURE_INDEX  # the store's own setting wins
    finally:
        store.close()
    (tmp_path / "facts.sqlite").write_bytes(b"")
    (tmp_path / "snapshot" / "release_text").mkdir(parents=True)
    monkeypatch.delenv(rt.ENV_DIR)

    class Dev:
        db_path = tmp_path / "facts.sqlite"

    assert rt.index_dir(Dev()) == tmp_path / "snapshot" / "release_text"  # data/ beside data/snapshot


def test_the_data_fingerprint_covers_the_index(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"fingerprint": "abc"}))
    assert stream.data_fingerprint(tmp_path) == "abc"
    (tmp_path / "release_text").mkdir()
    (tmp_path / "release_text" / "manifest.json").write_text("{}")
    with_index = stream.data_fingerprint(tmp_path)
    assert with_index.startswith("abc+rt-")
    (tmp_path / "release_text" / "manifest.json").write_text('{"documents": 1}')
    assert stream.data_fingerprint(tmp_path) != with_index


# ---------------------------------------------------------------------------
# The real index: figures the filed facts leave unread that the release states (round-9 cases)
# ---------------------------------------------------------------------------
REAL_CASES = [
    (Claim(id="c1", metric="other", metric_text="noninterest income", value_text="5.9%", value=5.9, unit="%",
           scale="units", period_text="Q2 2026", period_type="quarter", comparison="none", basis=None,
           forward_looking=False, entity="Truist", segment=None,
           span_text="Truist's noninterest income rose 5.9% from the prior quarter.", span_start=0, span_end=61),
     None, "Noninterest income was up $91 million, or 5.9%, compared to the first quarter of 2026."),
    (Claim(id="c1", metric="revenue", metric_text="net sales", value_text="$93.9 billion", value=93.9, unit="USD",
           scale="billions", period_text="", period_type="quarter", comparison="none", basis=None,
           forward_looking=False, entity="Costco", segment=None,
           span_text="Costco's quarterly net sales increased 11.2% year over year to $93.9 billion", span_start=0,
           span_end=77), COST_CTX,
     "Net sales for the quarter increased 11.2 percent, to $93.9 billion, from $84.4 billion last year."),
]


@pytest.mark.parametrize("c,context,quote", REAL_CASES, ids=["truist-sequential", "costco-net-sales"])
def test_the_real_index_confirms_what_the_filed_facts_leave_unread(real_store, c, context, quote):
    assert c.span_text[c.span_start:c.span_end] == c.span_text
    filed = verifier._verify_filed(c, real_store, context=context)
    assert (filed.status, filed.reason) == ("unverifiable", "no_evidence"), filed.explanation
    v = verify(c, real_store, context=context)
    assert (v.status, v.reason) == ("verified", "release_statement") and v.evidence[0].locator == quote


# ---------------------------------------------------------------------------
# Planted errors: the comprehensive runs' recorded claims, re-verified on this code
# ---------------------------------------------------------------------------
def _spot500():
    """eval.spot500 and its harness eval.eval, also after another test put eval/ itself on sys.path: eval is a namespace
    package, so eval/eval.py would otherwise be imported as "eval"."""
    saved = list(sys.path)
    shadow = sys.modules.get("eval")
    try:
        sys.path[:] = [str(ROOT)] + [x for x in saved if Path(x).resolve() != ROOT / "eval"]
        if shadow is not None and not hasattr(shadow, "__path__"):
            del sys.modules["eval"]
        return importlib.import_module("eval.spot500"), importlib.import_module("eval.eval")
    finally:
        sys.path[:] = saved


@pytest.mark.parametrize("which", ["full", "full2"])
@pytest.mark.parametrize("index", ["fixture", pytest.param("real", marks=real_index)])
def test_no_planted_error_or_guidance_is_confirmed_by_release_text(which, index):
    spot500, harness = _spot500()
    run_file = ROOT / f"eval/spot500_runs/{which}-live-20261102.json"
    if not run_file.is_file():
        pytest.skip("no recorded run")
    run = json.loads(run_file.read_text())
    _, items = spot500.load_items(which)
    store = open_store(FIXTURE_INDEX if index == "fixture" else None)
    confirmed, moved = [], 0
    try:
        for item in items:
            pairs = run["records"][item["item_id"]].get("pairs", [])
            text, context, as_of = item["text"], item["context"], harness._as_of(item)
            named = verifier.named_in_draft(text, store)
            reasons = [verify(Claim(**c), store, as_of=as_of, context=context, draft=text,
                              **({"named": named} if named else {})).reason for c, _ in pairs]
            moved += reasons.count("release_statement")
            for gold in item["gold_claims"]:
                hits = spot500._matching(pairs, gold, set(), require_basis=False)
                if gold["role"] in ("target", "guidance") and any(reasons[i] == "release_statement" for i in hits):
                    confirmed.append((item["item_id"], gold["value_text"]))
    finally:
        store.close()
    assert confirmed == []
    assert moved > 0 if index == "real" else moved >= 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"] + sys.argv[1:]))
