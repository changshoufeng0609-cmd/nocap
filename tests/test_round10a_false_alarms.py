"""Round 10a: the false "✗" verdicts the AI-draft benchmark's dev batch found (eval/ai_drafts_runs/dev), each fixed
both ways. A figure the company's own release or filing states is never "✗"; a planted wrong figure still is.

The dev cases run on the real snapshot (facts.sqlite, pr_facts and the release-text index), with each draft's own
sentences and the claims Muse Spark proposed for them, as recorded; the rest use the in-memory FakeStore.
- B: Accenture's release says "GAAP net income" for the consolidated line ($2.03 billion; $1.991 billion attributable).
- C: Deere's "Net sales" ($10.999 billion) is below its "Net sales and revenues" ($12.608 billion).
- D: Ametek's "Electromechanical Group sales grew ..., as ... operating margins reached 22.7%" is the group's margin.
- E: Gilead's release table maps U.S. Trodelvy sales ($336 million) as Trodelvy's; its text says $506 million.
- F: Brown-Forman's "Cash flows from operations grew to $1.0 billion", in a Q4 summary, is fiscal 2026's.
- T. Rowe Price Group, Inc. resolves (its SEC name is inverted around one initial and a given name), and its draft's
  inferred context ("third quarter of 2025", a comparison base) steers no figure into the base quarter.
"""
from datetime import date
from pathlib import Path

import pytest

from receipts import store as st
from receipts import verify as V
from receipts.store import Store
from receipts.types import Claim, Fact
from test_verify import FakeStore, utc

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/snapshot"
pytestmark = pytest.mark.skipif(not (SNAPSHOT / "release_text").is_dir(), reason="no release-text index built")


@pytest.fixture(scope="module")
def store():
    s = Store(SNAPSHOT / "facts.sqlite", SNAPSHOT / "pr_facts")
    yield s
    s.close()


def drafted(draft, span, value_text, value, *, cid="c1", **fields):
    """A claim whose span sits at its offsets in the draft, as extraction places it."""
    start = draft.index(span)
    values = dict(metric="other", metric_text="", unit="USD", scale="billions", period_text="", period_type="quarter",
                  comparison="none", basis=None, forward_looking=False, entity="", segment=None)
    values.update(fields)
    return Claim(id=cid, value_text=value_text, value=value, span_text=span, span_start=start,
                 span_end=start + len(span), **values)


def check(store, claim, draft, context):
    named = V.named_in_draft(draft, store)
    return V.verify(claim, store, context=context, draft=draft, **({"named": named} if named else {}))


def planted(claim, draft, value_text, value):
    """The same claim with another figure written in its place (the same length, so every offset holds)."""
    assert len(value_text) == len(claim.value_text)
    at = claim.span_start + claim.span_text.index(claim.value_text)
    draft = draft[:at] + value_text + draft[at + len(claim.value_text):]
    return claim.model_copy(update={"value_text": value_text, "value": value,
                                    "span_text": draft[claim.span_start:claim.span_end]}), draft


def status(v):
    return {"verified": "✓", "wrong": "✗", "unverifiable": "?", "approximate": "≈"}[v.status]


# ---------------------------------------------------------------------------
# B. Net income the release states for its consolidated line
# ---------------------------------------------------------------------------
ACN = ("Accenture Q4 fiscal 2026: higher profit and a stable margin\n"
       "GAAP net income climbed to $2.03 billion in the fourth quarter, with gross margin edging up to 32.0%.\n\n"
       "Profitability was broadly steady in the quarter. The GAAP effective tax rate slipped to 27.3%, compared with "
       "30.1% in the prior-year quarter, as GAAP net income grew from $1.45 billion.\n\n"
       "For the full year, GAAP net income increased to $8.52 billion, compared with $7.83 billion in fiscal 2025.")
ACN_CONTEXT = "Accenture plc, Q4 fiscal 2026"
INCOME = dict(metric="net_income", metric_text="GAAP net income", basis="GAAP", entity="Accenture plc")
ACN_CLAIMS = {
    "quarter": drafted(ACN, "GAAP net income climbed to $2.03 billion in the fourth quarter", "$2.03 billion", 2.03,
                       **(INCOME | dict(metric_text="net income", entity="Accenture",
                                        period_text="in the fourth quarter"))),
    "quarter_base": drafted(ACN, " The GAAP effective tax rate slipped to 27.3%, compared with 30.1% in the prior-year "
                                 "quarter, as GAAP net income grew from $1.45 billion", "$1.45 billion", 1.45,
                            **(INCOME | dict(period_text="in the prior-year quarter"))),
    "year": drafted(ACN, "GAAP net income increased to $8.52 billion", "$8.52 billion", 8.52,
                    **(INCOME | dict(period_text="For the full year", period_type="annual", comparison="YoY"))),
    "year_base": drafted(ACN, "GAAP net income increased to $8.52 billion, compared with $7.83 billion in fiscal 2025",
                         "$7.83 billion", 7.83, **(INCOME | dict(period_text="in fiscal 2025", period_type="annual"))),
}


@pytest.mark.parametrize("name,filed", [("quarter", "$1.991 billion"), ("quarter_base", "$1.414 billion"),
                                        ("year", "$8.367 billion"), ("year_base", "$7.678 billion")])
def test_release_stated_net_income_is_not_wrong(store, name, filed):
    v = check(store, ACN_CLAIMS[name], ACN, ACN_CONTEXT)
    assert (status(v), v.reason) == ("?", "ambiguous"), v.explanation
    assert filed in v.explanation and "attributable" in v.explanation and "earnings release" in v.explanation
    assert {f.source for f in v.evidence} == {"xbrl", "release_text"}


@pytest.mark.parametrize("name,wrong,value", [("quarter", "$2.13 billion", 2.13), ("year", "$8.62 billion", 8.62),
                                              ("year_base", "$7.93 billion", 7.93),
                                              ("quarter", "$1.45 billion", 1.45),   # last year's figure, this quarter
                                              ("quarter", "$8.52 billion", 8.52)])  # the year's, in "the fourth quarter"
def test_a_figure_the_release_does_not_state_for_that_period_stays_wrong(store, name, wrong, value):
    claim, draft = planted(ACN_CLAIMS[name], ACN, wrong, value)
    assert status(check(store, claim, draft, ACN_CONTEXT)) == "✗"


def test_net_income_attributable_to_the_company_is_still_its_filed_line(store):
    draft = "Accenture Q4 fiscal 2026\nNet income attributable to Accenture plc was $2.03 billion in the fourth quarter."
    claim = drafted(draft, "Net income attributable to Accenture plc was $2.03 billion in the fourth quarter",
                    "$2.03 billion", 2.03, **(INCOME | dict(metric_text="Net income attributable to Accenture plc",
                                                           period_text="in the fourth quarter")))
    v = check(store, claim, draft, ACN_CONTEXT)
    assert status(v) == "✗" and "$1.991 billion" in v.explanation


def test_non_gaap_release_sentence_states_no_gaap_net_income(store):
    # AMD's "On a non-GAAP(*) basis, ... net income was $2.8 billion": the asterisk hides the basis phrase from the
    # figure reader, but the sentence still states no GAAP figure (the dev replay's planted GAAP label stays "✗")
    from receipts import release_text as rt
    cik, ticker = store.resolve_entity("AMD")
    assert rt.net_income_statement(store, cik, ticker, "2.8", "usd:billion", {(2026, "Q2")}) is None


# Consolidated net income filed beside the attributable line: the line the income statement calls net income
PG, PG_ACC = 80424, "0000080424-26-000099"


def pg_income(metric, value, concept):
    return Fact(cik=PG, ticker="PG", metric=metric, concept=concept, basis="GAAP", value=value, unit="USD",
                period_start=date(2026, 7, 1), period_end=date(2026, 9, 30), fiscal_year=2027, fiscal_period="Q1",
                source="xbrl", derived_from=[], form="10-Q", accession=PG_ACC, accepted_at=utc("2026-10-24T12:00:00Z"),
                url=f"https://www.sec.gov/Archives/edgar/data/{PG}/{PG_ACC.replace('-', '')}/pg.htm", decimals=-6)


PG_STORE = FakeStore([pg_income("net_income", 4_731e6, "us-gaap:NetIncomeLoss"),
                      pg_income("net_income_including_nci", 4_757e6, "us-gaap:ProfitLoss")],
                     aliases={"procter & gamble": (PG, "PG")},
                     periods={(PG, 2027, "Q1"): (date(2026, 7, 1), date(2026, 9, 30))})


def pg(value_text, value, words="net earnings were"):
    span = f"Procter & Gamble's {words} {value_text}"
    return Claim(id="c1", metric="net_income", metric_text=words.rsplit(" ", 1)[0], value_text=value_text,
                 value=value, unit="USD", scale="billions", period_text="Q1 FY2027", period_type="quarter",
                 comparison="none", basis=None, forward_looking=False, entity="Procter & Gamble", segment=None,
                 span_text=span, span_start=0, span_end=len(span))


@pytest.mark.parametrize("value_text,value,outcome", [("$4.757 billion", 4.757, "✓"), ("$4.8 billion", 4.8, "✓"),
                                                      ("$4.731 billion", 4.731, "✓"), ("$4.9 billion", 4.9, "✗")])
def test_consolidated_net_income_verifies_a_plain_net_income(value_text, value, outcome):
    v = V.verify(pg(value_text, value), PG_STORE)
    assert status(v) == outcome, v.explanation
    if outcome == "✓" and value != 4.731:
        assert "consolidated" in v.explanation and "$4.731 billion" in v.explanation


def test_attributed_net_income_never_takes_the_consolidated_line():
    # attributed to the company, the consolidated figure is not its line: "?" as before round 10a, never ✓
    v = V.verify(pg("$4.757 billion", 4.757, "net earnings attributable to Procter & Gamble were"), PG_STORE)
    assert (status(v), v.reason) == ("?", "ambiguous"), v.explanation
    assert status(V.verify(pg("$4.9 billion", 4.9, "net earnings attributable to Procter & Gamble were"),
                           PG_STORE)) == "✗"


# ---------------------------------------------------------------------------
# C. Net sales below a total that names net sales and other revenues
# ---------------------------------------------------------------------------
DE = ("Deere & Company — Q3 FY2026\nResults:\n- Worldwide net sales and revenues: $12.608 billion, up 5 percent\n"
      "- Net sales: $10.999 billion, versus $10.357 billion a year earlier\n")
DE_CONTEXT = "Deere & Co, Q3 FY2026"
SALES = dict(metric="revenue", metric_text="Net sales", entity="Deere & Company")
DE_CLAIMS = {
    "current": drafted(DE, "Net sales: $10.999 billion", "$10.999 billion", 10.999,
                       **(SALES | dict(period_text="Q3 FY2026"))),
    "year_ago": drafted(DE, "versus $10.357 billion a year earlier", "$10.357 billion", 10.357,
                        **(SALES | dict(period_text="a year earlier"))),
}


@pytest.mark.parametrize("name", ["current", "year_ago"])
def test_net_sales_below_net_sales_and_revenues_is_not_wrong(store, name):
    v = check(store, DE_CLAIMS[name], DE, DE_CONTEXT)
    assert (status(v), v.reason) == ("?", "no_evidence"), v.explanation
    assert "Net sales and revenues" in v.explanation


@pytest.mark.parametrize("name,wrong,value", [("current", "$13.999 billion", 13.999),   # above the total
                                              ("year_ago", "$12.357 billion", 12.357)])
def test_net_sales_above_the_total_are_still_checked(store, name, wrong, value):
    claim, draft = planted(DE_CLAIMS[name], DE, wrong, value)
    assert status(check(store, claim, draft, DE_CONTEXT)) == "✗"


def test_the_same_figure_called_revenue_is_still_checked_against_revenue(store):
    draft = "Deere & Company — Q3 FY2026\n- Revenue: $10.999 billion\n"
    claim = drafted(draft, "Revenue: $10.999 billion", "$10.999 billion", 10.999,
                    **(SALES | dict(metric_text="Revenue", period_text="Q3 FY2026")))
    assert status(check(store, claim, draft, DE_CONTEXT)) == "✗"


SOLO = 7  # hand-made company whose release row names net sales as the whole top line
SOLO_ACC = "0000000007-26-000001"


def solo_store(row):
    f = Fact(cik=SOLO, ticker="SOLO", metric="revenue", concept=None, basis="GAAP", value=12_608e6, unit="USD",
             period_start=date(2026, 4, 1), period_end=date(2026, 6, 30), fiscal_year=2026, fiscal_period="Q2",
             source="press_release", derived_from=[], form="8-K", accession=SOLO_ACC,
             accepted_at=utc("2026-07-30T12:00:00Z"), url="https://www.sec.gov/Archives/edgar/data/7/x.htm",
             locator=f"{row} | 12,608", decimals=-6)
    return FakeStore([f], aliases={"solo": (SOLO, "SOLO")},
                     periods={(SOLO, 2026, "Q2"): (date(2026, 4, 1), date(2026, 6, 30))})


def solo_claim(value_text, value, words="net sales"):
    span = f"Solo's {words} were {value_text}"
    return Claim(id="c1", metric="revenue", metric_text=words, value_text=value_text, value=value, unit="USD",
                 scale="billions", period_text="Q2 2026", period_type="quarter", comparison="none", basis=None,
                 forward_looking=False, entity="Solo", segment=None, span_text=span, span_start=0, span_end=len(span))


@pytest.mark.parametrize("row,outcome", [("Net sales and revenues", "?"), ("Total net sales and revenue", "?"),
                                         ("Net sales", "✗"), ("Total net sales", "✗"), ("Total revenues", "✗"),
                                         ("Sales and other operating revenues", "✗")])
def test_only_a_row_naming_net_sales_and_other_revenues_shows_a_distinct_line(row, outcome):
    assert status(V.verify(solo_claim("$10.999 billion", 10.999), solo_store(row))) == outcome


def test_a_single_line_company_still_verifies_its_net_sales():
    assert status(V.verify(solo_claim("$12.608 billion", 12.608), solo_store("Net sales"))) == "✓"
    assert status(V.verify(solo_claim("$12.608 billion", 12.608), solo_store("Net sales and revenues"))) == "✓"


# ---------------------------------------------------------------------------
# D. A segment as its sentence's subject governs the clauses joined to it by "as" and "and"
# ---------------------------------------------------------------------------
AME = ("Ametek Q4 2025: Record Sales and Earnings Close the Year\n"
       "Electromechanical Group sales grew to $628.9 million, up 15%, as operating income jumped to $142.5 million "
       "and operating margins reached 22.7%, supporting broad-based profitability across the portfolio.")
AME_CONTEXT = "Ametek Inc/, Q4 2025"


def ame(draft, span, value_text="22.7%", value=22.7, **fields):
    return drafted(draft, span, value_text, value, **(dict(metric="other", metric_text="operating margins", unit="%",
                                                           scale="units", period_text="Q4 2025", entity="Ametek Inc")
                                                      | fields))


def test_a_later_clause_margin_is_the_subject_segments(store):
    # as recorded: the model named the segment, which the operating-margin reading dropped
    v = check(store, ame(AME, "operating margins reached 22.7%", segment="Electromechanical Group"), AME, AME_CONTEXT)
    assert (status(v), v.reason) == ("?", "no_evidence"), v.explanation
    assert "Electromechanical Group" in v.explanation
    # without it, the release's own sentence for the group may confirm it; never the company's 25.29%
    v = check(store, ame(AME, "operating margins reached 22.7%"), AME, AME_CONTEXT)
    assert status(v) in ("?", "✓") and "25.29%" not in v.explanation, v.explanation
    if status(v) == "✓":
        assert "EMG" in v.explanation


@pytest.mark.parametrize("clause", ["while operating margins reached 22.7%",            # the sentence turns
                                    "and total operating margins reached 22.7%",       # an owner of its own
                                    "and Ametek's operating margins reached 22.7%"])   # the company itself
def test_a_clause_that_names_its_own_owner_or_turns_is_the_companys(store, clause):
    draft = ("Ametek Q4 2025\nElectromechanical Group sales grew to $628.9 million, up 15%, "
             f"{clause}.")
    v = check(store, ame(draft, clause.split(" ", 1)[1] if clause.startswith(("while", "and")) else clause), draft,
              AME_CONTEXT)
    assert status(v) == "✗" and "25.29%" in v.explanation, v.explanation


def test_a_company_measure_in_a_later_clause_is_still_checked(store):
    draft = "Ametek Q4 2025\nElectromechanical Group sales grew to $628.9 million, and diluted earnings were $1.73 per share."
    claim = drafted(draft, "diluted earnings were $1.73 per share", "$1.73", 1.73, metric="eps_diluted",
                    metric_text="diluted earnings per share", unit="USD/share", scale="units", period_text="Q4 2025",
                    entity="Ametek Inc")
    assert status(check(store, claim, draft, AME_CONTEXT)) == "✓"


# ---------------------------------------------------------------------------
# E. A release table cell against the release's own words
# ---------------------------------------------------------------------------
GILD = ("Gilead Q3 2026\nTrodelvy sales surged to $506 million on higher demand, partly offsetting weakness elsewhere.")
GILD_CONTEXT = "Gilead Sciences, Inc., Q3 2026"


def gild(draft, value_text="$506 million", value=506.0):
    return drafted(draft, f"Trodelvy sales surged to {value_text}", value_text, value, metric="segment_revenue",
                   metric_text="Trodelvy sales", scale="millions", period_text="Q3 2026",
                   entity="Gilead Sciences, Inc.", segment="Trodelvy")


def test_a_table_cell_never_makes_the_releases_own_figure_wrong(store):
    v = check(store, gild(GILD), GILD, GILD_CONTEXT)
    assert (status(v), v.reason) == ("?", "ambiguous"), v.explanation
    assert "$336 million" in v.explanation and "sacituzumab" in v.explanation
    assert [f.source for f in v.evidence] == ["press_release", "release_text"]


def test_a_figure_neither_the_table_nor_the_text_states_stays_wrong(store):
    claim, draft = planted(gild(GILD), GILD, "$560 million", 560.0)
    assert status(check(store, claim, draft, GILD_CONTEXT)) == "✗"


def test_the_ordinary_release_statement_matcher_is_unchanged(store):
    # table_conflict alone reads past a name in parentheses; the ✓ fallback still needs the measure's words together
    from receipts import release_text as rt
    claim = gild(GILD)
    cik, ticker = store.resolve_entity("Gilead Sciences, Inc.")
    period = rt.claim_period(claim, store, cik, GILD_CONTEXT, None, False)
    line = V.draft_line(claim, GILD)
    assert rt.release_statement(claim, store, (cik, ticker), period, None, line) is None
    assert rt.release_statement(claim, store, (cik, ticker), period, None, line, skip_names=True) is not None


# ---------------------------------------------------------------------------
# F. A fourth quarter's figure that is the fiscal year's
# ---------------------------------------------------------------------------
BF = ("# Brown-Forman Q4 FY2026 Earnings Summary\n\n**Cash Flow and Capital Returns**\n"
      "- Cash flows from operations grew to **$1.0 billion**, and free cash flow rose to **$893 million**.\n")
BF_CONTEXT = "Brown Forman Corp, Q4 FY2026"


def bf(draft, span, value_text="$1.0 billion", value=1.0):
    return drafted(draft, span, value_text, value, metric="operating_cash_flow",
                   metric_text="Cash flows from operations", period_text="Q4 FY2026", entity="Brown Forman Corp")


def test_a_q4_summary_figure_that_is_the_years_is_not_wrong(store):
    v = check(store, bf(BF, "Cash flows from operations grew to **$1.0 billion**"), BF, BF_CONTEXT)
    assert (status(v), v.reason) == ("?", "ambiguous"), v.explanation
    assert "$291 million" in v.explanation and "fiscal 2026" in v.explanation


def test_a_figure_its_sentence_dates_to_the_quarter_stays_wrong(store):
    draft = "# Brown-Forman Q4 FY2026\n- Fourth-quarter cash flows from operations grew to $1.0 billion.\n"
    claim = bf(draft, "Fourth-quarter cash flows from operations grew to $1.0 billion")
    assert status(check(store, claim, draft, BF_CONTEXT)) == "✗"


def test_a_figure_that_is_neither_the_quarters_nor_the_years_stays_wrong(store):
    claim, draft = planted(bf(BF, "Cash flows from operations grew to **$1.0 billion**"), BF, "$1.4 billion", 1.4)
    assert status(check(store, claim, draft, BF_CONTEXT)) == "✗"


# ---------------------------------------------------------------------------
# T. Rowe Price Group, Inc., and a context period that is only a comparison base
# ---------------------------------------------------------------------------
TROW = ("Revenue rises in third quarter as client assets decline\n"
        "T. Rowe Price Group, Inc. reported third-quarter diluted earnings per common share of $2.79, with adjusted "
        "diluted earnings per common share of $2.86, as net revenues reached $2.0 billion. Net revenues rose 5.9% from "
        "the third quarter of 2025 and climbed 5.1% from the second quarter of 2026.")
TROW_CONTEXT = "T. Rowe Price, third quarter of 2025"  # as inferred: the lead's first dated period is a base


def test_t_rowe_price_group_resolves(store):
    for name in ("T. Rowe Price Group, Inc.", "T. Rowe Price Group", "T Rowe Price Group Inc"):
        assert store.resolve_entity(name) == store.by_ticker("TROW")
    assert store.resolve_entity("T. Rowe Price Group plc") is None
    assert "t rowe price group" in st.company_aliases("TROW", "PRICE T ROWE GROUP INC")
    for ticker, name in (("AOS", "SMITH A O CORP"), ("JBHT", "HUNT J B TRANSPORT SERVICES INC"),
                         ("PG", "PROCTER & GAMBLE Co"), ("MSFT", "MICROSOFT CORP")):
        assert st._uninverted_given_name(name) == name


def trow(value_text="$2.79", value=2.79):
    return drafted(TROW, f"reported third-quarter diluted earnings per common share of {value_text}", value_text, value,
                   metric="eps_diluted", metric_text="diluted earnings per common share", unit="USD/share",
                   scale="units", period_text="third-quarter", entity="T. Rowe Price Group, Inc.")


def test_t_rowe_price_figures_are_checked_in_the_storys_quarter(store):
    v = check(store, trow(), TROW, TROW_CONTEXT)
    assert status(v) == "✓" and "Q3 2026" in v.explanation, v.explanation
    claim, draft = planted(trow(), TROW, "$2.87", 2.87)  # Q3 2025's diluted EPS: the base quarter's, not this one's
    assert status(check(store, claim, draft, TROW_CONTEXT)) == "✗"


@pytest.mark.parametrize("context,draft,expected", [
    ("T. Rowe Price, third quarter of 2025", TROW, "T. Rowe Price, third quarter of 2026"),
    # no bare quarter elsewhere: the base period steers nothing
    ("T. Rowe Price, third quarter of 2025", "Net revenues rose 5.9% from the third quarter of 2025.", "T. Rowe Price"),
    # another year in the story: no inference
    ("T. Rowe Price, third quarter of 2025", "Third-quarter revenue rose 5.9% from the third quarter of 2025 and the "
                                             "second quarter of 2027.", "T. Rowe Price"),
    # the period is the story's own somewhere: unchanged
    ("T. Rowe Price, third quarter of 2025", "In the third quarter of 2025, revenue rose 5.9%.",
     "T. Rowe Price, third quarter of 2025"),
    # an explicit context the draft doesn't repeat: unchanged
    ("T. Rowe Price Q3 2025", TROW, "T. Rowe Price Q3 2025"),
    (None, TROW, None),
])
def test_story_context(context, draft, expected):
    assert V.story_context(context, draft) == expected


# ---------------------------------------------------------------------------
# Follow-up: a year is never the release's quarter (receipts.release_text)
# ---------------------------------------------------------------------------
def test_brown_formans_fiscal_year_free_cash_flow_no_longer_confirms_the_quarter(store):
    # Its release states $893 million under "Fiscal 2026 Financial Stewardship"; the draft's Q4 summary names no period
    claim = drafted(BF, "free cash flow rose to **$893 million**", "$893 million", 893.0, metric="free_cash_flow",
                    metric_text="free cash flow", scale="millions", period_text="Q4 FY2026", entity="Brown Forman Corp")
    v = check(store, claim, BF, BF_CONTEXT)
    assert status(v) == "?", v.explanation


YR, YR_ACC = 9, "0000000009-26-000002"


def year_index(tmp_path, *texts, seqs=None):
    """A one-release index of hand-made Q4 FY2026 units (fiscal year May to April, as Brown-Forman's)."""
    import json
    rows = [{"ticker": "YRCO", "cik": YR, "accession": YR_ACC, "exhibit": "EX-99.1",
             "url": "https://www.sec.gov/Archives/edgar/data/9/x.htm", "accepted_at": "2026-06-04T12:00:00Z",
             "fiscal_year": 2026, "fiscal_period": "Q4", "period_start": "2026-02-01", "period_end": "2026-04-30",
             "seq": seq, "text": text, "lead": ""} for seq, text in zip(seqs or range(1, len(texts) + 1), texts)]
    (tmp_path / "YRCO.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    store = FakeStore([], aliases={"yrco": (YR, "YRCO")},
                      periods={(YR, 2026, "Q4"): (date(2026, 2, 1), date(2026, 4, 30)),
                               (YR, 2026, "FY"): (date(2025, 5, 1), date(2026, 4, 30))})
    store.release_text_dir = tmp_path
    return store


def q4_claim(span, value_text, value, metric="free_cash_flow", metric_text="free cash flow", scale="millions",
             unit="USD"):
    return Claim(id="c1", metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                 scale=scale, period_text="fourth quarter of fiscal 2026", period_type="quarter", comparison="none",
                 basis=None, forward_looking=False, entity="YRCO", segment=None, span_text=span, span_start=0,
                 span_end=len(span))


FCF = q4_claim("YRCO's free cash flow was $893 million", "$893 million", 893.0)


@pytest.mark.parametrize("sentence", [
    "For fiscal 2026, free cash flow was $893 million.",
    "Free cash flow was $893 million in fiscal 2026.",
    "In 2026, free cash flow was $893 million.",
    "Free cash flow for FY2026 was $893 million.",
])
def test_a_year_only_sentence_never_confirms_a_quarter(tmp_path, sentence):
    v = V.verify(FCF, year_index(tmp_path, sentence))
    assert v.status == "unverifiable" and v.reason != "release_statement", v.explanation


@pytest.mark.parametrize("sentence", [
    "Fourth quarter of fiscal 2026 free cash flow was $893 million.",
    "Q4 2026 free cash flow was $893 million.",
    "In the fourth quarter, free cash flow was $893 million.",
    "Free cash flow was $893 million.",                                       # a bare quarter sentence
    "Free cash flow was $893 million, compared with $431 million in fiscal 2025.",   # a prior-year base
    "Free cash flow was $893 million for the quarter ended April 30, 2026.",
])
def test_a_quarter_sentence_still_confirms_also_beside_its_fiscal_year(tmp_path, sentence):
    v = V.verify(FCF, year_index(tmp_path, sentence))
    assert (v.status, v.reason) == ("verified", "release_statement"), v.explanation


def test_in_a_sentence_naming_both_the_years_figure_never_confirms_the_quarter(tmp_path):
    store = year_index(tmp_path, "Fourth-quarter free cash flow was $291 million and fiscal 2026 free cash flow was "
                                 "$893 million.")
    assert V.verify(FCF, store).reason != "release_statement"
    # the quarter's figure stays "?" as before: the sentence states the measure at two numbers (a conflict)
    quarter = q4_claim("YRCO's free cash flow was $291 million", "$291 million", 291.0)
    assert V.verify(quarter, store).status == "unverifiable"


DATELINE = "LOUISVILLE, KY, June 4, 2026 -- YRCO reported its results for the fourth quarter today."


def test_a_year_section_heading_scopes_the_sentences_under_it(tmp_path):
    under = [DATELINE, "Fiscal 2026 Financial Stewardship", "The company paid regular dividends.",
             "Free cash flow increased $462 million to $893 million."]
    assert V.verify(FCF, year_index(tmp_path, *under)).reason != "release_statement"
    # a quarter heading, or an unindexed unit (perhaps another heading) between, leaves the sentence as before
    quarter = [DATELINE, "Fourth Quarter 2026 Results", *under[2:]]
    assert V.verify(FCF, year_index(tmp_path, *quarter)).reason == "release_statement"
    assert V.verify(FCF, year_index(tmp_path, *under, seqs=[1, 2, 3, 5])).reason == "release_statement"


def test_a_title_block_heading_scopes_nothing(tmp_path):
    # CoStar's "Raises Full-Year 2026 Adjusted EBITDA Guidance" and Honeywell Aerospace's "updates 2026 outlook" head
    # the quarter's own figures, above the dateline
    block = ["Raises Full-Year 2026 Free Cash Flow Guidance", "Free cash flow of $893 million, up 40%", DATELINE,
             "Free cash flow was $893 million, up $462 million year-over-year."]
    assert V.verify(FCF, year_index(tmp_path, *block)).reason == "release_statement"
