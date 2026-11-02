"""The document-context line a release gives when it opens with a headline (round 8).

Microsoft's Q1 FY2027 release opens with "Microsoft Announces First Quarter Results"; its lead, the next passage, names
the company ("Microsoft Corp. today announced", no ticker) and the period ("the quarter ended September 30, 2026"). The
bullets after it name no period, so with only the headline read every claim was "?" ("Couldn't tell which ... fiscal
period"). The fixture is the release's opening, as the owner pasted it on the live site. Claims are built offline from
synthetic model proposals through the real extraction post-processing; no model, network or SEC call.
"""
from pathlib import Path

import pytest

from receipts import extract as ex
from receipts.store import Store
from receipts.verify import verify

ROOT = Path(__file__).resolve().parents[1]
MSFT_RELEASE = (ROOT / "tests/fixtures/msft_fy2027_q1_release.txt").read_text()
MSFT_CONTEXT = "Microsoft Corp., quarter ended September 30, 2026"
MSFT_10Q = "0000950170-26-"  # accession prefix of Microsoft's filings in the snapshot
NVDA_LEAD = ("SANTA CLARA, Calif.—Aug. 26, 2026― NVIDIA (NASDAQ: NVDA) today reported revenue for the second quarter "
             "ended July 26, 2026, of $96.2 billion, up 18% from the previous quarter.")
NVDA_CONTEXT = "NVIDIA, second quarter ended July 26, 2026"


@pytest.fixture(scope="module")
def snapshot():
    store = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    try:
        yield store
    finally:
        store.close()


def test_a_headline_first_takes_the_context_from_the_release_lead():
    assert ex.split_passages(MSFT_RELEASE)[0][1] == "Microsoft Announces First Quarter Results"
    assert ex.infer_context(MSFT_RELEASE) == MSFT_CONTEXT


def proposal(metric, metric_text, value_text, value, unit, scale, span_text, *, bases=(), period_text=""):
    return ex.ModelClaim(metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                         scale=scale, period_text=period_text, period_type="quarter", comparison="none",
                         bases=list(bases), forward_looking=False, entity="Microsoft Corp.", segment=None,
                         span_text=span_text)


def msft_claims(period_text=""):
    """The release's five headline figures, located by the real post-processing in their bullets."""
    passages = ex.split_passages(MSFT_RELEASE)
    bullets = {i: passage for i, (_, passage) in enumerate(passages)}
    wanted = {
        2: [proposal("revenue", "Revenue", "$92.3 billion", 92.3, "USD", "billions", "Revenue was $92.3 billion",
                     period_text=period_text)],
        3: [proposal("operating_income", "Operating income", "$45.9 billion", 45.9, "USD", "billions",
                     "Operating income was $45.9 billion", period_text=period_text)],
        4: [proposal("net_income", "Net income", "$36.6 billion", 36.6, "USD", "billions",
                     "Net income was $36.6 billion and increased 32% on a GAAP basis", bases=["GAAP"],
                     period_text=period_text)],
        5: [proposal("eps_diluted", "Diluted earnings per share", "$4.92", 4.92, "USD/share", "units",
                     "Diluted earnings per share was $4.92 and increased 32% on a GAAP basis", bases=["GAAP"],
                     period_text=period_text),
            proposal("eps_diluted", "Diluted earnings per share", "$4.89", 4.89, "USD/share", "units",
                     "was $4.89 and increased 18% (up 19% in constant currency) on a non-GAAP basis",
                     bases=["non-GAAP"], period_text=period_text)],
    }
    assert all(bullets[i].startswith("·") for i in wanted)
    claims, _ = ex.build_claims([(passages[i][0], passages[i][1], wanted[i]) for i in sorted(wanted)], [])
    assert [c.value_text for c in claims] == ["$92.3 billion", "$45.9 billion", "$36.6 billion", "$4.92", "$4.89"]
    for c in claims:
        assert MSFT_RELEASE[c.span_start:c.span_end] == c.span_text
    return claims


FILED = [("revenue", 92.279e9), ("operating_income", 45.915e9), ("net_income", 36.629e9), ("eps_diluted", 4.92),
         ("eps_diluted", 4.89)]


@pytest.mark.parametrize("period_text", ["", "first quarter", "quarter ended September 30, 2026"])
def test_the_microsoft_release_verifies_with_the_inferred_context(snapshot, period_text):
    context = ex.infer_context(MSFT_RELEASE)
    for c, (metric, filed) in zip(msft_claims(period_text), FILED):
        v = verify(c, snapshot, context=context)
        assert (v.status, v.reason) == ("verified", "match"), (c.value_text, v.explanation)
        assert "Q1 FY2027 (quarter ended Sep 30, 2026)" in v.explanation
        assert any(f.metric == metric and f.value == pytest.approx(filed) and f.period_end.isoformat() == "2026-09-30"
                   for f in v.evidence), v.evidence


def test_the_bullets_alone_name_no_period(snapshot):
    # What the live site showed before: with the headline as the only passage read, there is no context.
    assert ex.infer_context(ex.split_passages(MSFT_RELEASE)[0][1]) is None
    for c in msft_claims():
        v = verify(c, snapshot, context=None)
        assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


def test_the_nvidia_lead_keeps_its_context():
    assert ex.infer_context(NVDA_LEAD + "\n\nMore text.") == NVDA_CONTEXT
    # A first passage that names a period with its year still decides it, as before.
    assert ex.infer_context("NVIDIA Announces Financial Results for Second Quarter Fiscal 2027\n\n" + NVDA_LEAD) == \
        "Second Quarter Fiscal 2027"
    assert ex.infer_context("NVIDIA Announces Results\n\n" + NVDA_LEAD) == NVDA_CONTEXT


@pytest.mark.parametrize("text", [
    "Microsoft Announces First Quarter Results",
    "Microsoft Announces First Quarter Results\n\n",
    # a past period mentioned in passing, not a lead
    "Microsoft Announces First Quarter Results\n\nIn the quarter ended September 30, 2025, the company recorded a "
    "charge of $1.2 billion.",
    "Cloud demand stayed strong.\n\nRevenue rose.\n\nMargins held.\n\nIn the quarter ended September 30, 2025, the "
    "company recorded a charge of $1.2 billion.",
    # a lead too deep in the draft: only the first three passages are read
    "Microsoft Announces First Quarter Results\n\nCloud demand stayed strong.\n\nMargins held.\n\nREDMOND, Wash. — "
    "October 28, 2026 — Microsoft Corp. today announced the following results for the quarter ended September 30, "
    "2026.",
    # the lead's first period follows a comparison: it is the comparison's period
    "Microsoft Announces Buyback\n\nREDMOND, Wash. — October 28, 2026 — Microsoft Corp. today announced a buyback, "
    "compared with the quarter ended September 30, 2025, when it announced none.",
])
def test_no_context_from_a_headline_alone_or_a_period_in_passing(text):
    assert ex.infer_context(text) is None


def test_a_dateline_lead_without_a_verb_gives_its_period():
    text = "Quarterly Results\n\nNEW YORK, April 13, 2026 – Results for the first quarter ended March 31, 2026 were strong."
    assert ex.infer_context(text) == "first quarter ended March 31, 2026"


@pytest.mark.parametrize("passage,company", [
    ("REDMOND, Wash. — October 28, 2026 — Microsoft Corp. today announced the following results", "Microsoft Corp."),
    ("CUPERTINO, CALIFORNIA — Apple Inc. today reported results", "Apple Inc."),
    ("CUPERTINO, CALIFORNIA — Apple today announced financial results", None),  # no legal form, no ticker
    ("NEW YORK — The Goldman Sachs Group, Inc. (NYSE: GS) today reported", "The Goldman Sachs Group, Inc."),
    ("Microsoft reported revenue of $92.3 billion. Separately, Activision Blizzard Inc. today announced a deal.",
     "Microsoft"),  # an earlier form wins over a later legal-form lead
])
def test_the_lead_company(passage, company):
    assert ex._first_company(passage) == company


# --- A bare headline period gives way to a dated one (Costco) --------------------------------------------------------
# A Costco summary the owner pasted opens with "# Costco Q4 2026 Earnings Summary: ...". "Q4 2026" is ambiguous for
# Costco, whose fiscal year ends in August, so every claim without a period of its own was "?". Its next passage says
# "the fourth quarter of fiscal year 2026, which ended on August 30, 2026", with no dateline and no "today".
COST_SUMMARY = (ROOT / "tests/fixtures/cost_fy2026_q4_summary.txt").read_text()
COST_CONTEXT = "Costco Wholesale, fourth quarter of fiscal year 2026"


def test_a_bare_headline_period_gives_way_to_a_dated_one():
    assert ex.split_passages(COST_SUMMARY)[0][1].startswith("# Costco Q4 2026 Earnings Summary")
    assert ex.infer_context(COST_SUMMARY) == COST_CONTEXT


def cost_claims():
    passages = ex.split_passages(COST_SUMMARY)
    start, passage = passages[3]
    proposals = [
        ex.ModelClaim(metric="net_income", metric_text="Net income", value_text="$3.0 billion", value=3.0, unit="USD",
                      scale="billions", period_text="", period_type="quarter", comparison="none", bases=[],
                      forward_looking=False, entity="Costco", segment=None,
                      span_text="Net income rose 14.9% to approximately $3.0 billion"),
        ex.ModelClaim(metric="eps_diluted", metric_text="diluted earnings per share", value_text="$6.75", value=6.75,
                      unit="USD/share", scale="units", period_text="", period_type="quarter", comparison="none",
                      bases=[], forward_looking=False, entity="Costco", segment=None,
                      span_text="diluted earnings per share (EPS) increased from $5.87 to $6.75"),
    ]
    claims, _ = ex.build_claims([(start, passage, proposals)], [])
    assert [c.value_text for c in claims] == ["$3.0 billion", "$6.75"]
    return claims


def test_the_costco_summary_verifies_with_the_inferred_context(snapshot):
    income, eps = (verify(c, snapshot, context=ex.infer_context(COST_SUMMARY), draft=COST_SUMMARY)
                   for c in cost_claims())
    assert (income.status, eps.status) == ("verified", "verified"), (income.explanation, eps.explanation)
    assert "net income was $2.998 billion for Q4 FY2026 (quarter ended Aug 30, 2026)" in income.explanation
    assert "diluted EPS was $6.75 for Q4 FY2026" in eps.explanation
    for c in cost_claims():  # the headline's "Q4 2026" alone names no Costco quarter
        v = verify(c, snapshot, context="Q4 2026")
        assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


@pytest.mark.parametrize("second", [
    "In the fourth quarter of fiscal 2025, Costco opened twelve warehouses.",          # another year
    "Costco reported results for the third quarter of fiscal year 2026.",               # another quarter
    "Compared with the fourth quarter of fiscal year 2026 plan, sales were higher.",    # after a comparison word
    "In the quarter ended March 31, 2026, the company issued debt.",                    # no quarter named
])
def test_a_bare_headline_period_stays_unless_a_passage_dates_the_same_one(second):
    assert ex.infer_context(f"# Costco Q4 2026 Earnings Summary\n\n{second}") == "Q4 2026"


def test_a_dated_period_of_the_same_quarter_or_the_release_lead_refines_a_bare_one():
    assert ex.infer_context("# Costco Q4 2026 Earnings Summary\n\nCostco reported results for the fourth quarter "
                            "ended August 30, 2026.") == "Costco, fourth quarter ended August 30, 2026"
    # The release lead's dated period wins over a bare headline even across fiscal and calendar years.
    lead = MSFT_RELEASE.split("\n\n")[1]
    assert ex.infer_context(f"# Microsoft Q1 2027 Earnings\n\n{lead}") == MSFT_CONTEXT
