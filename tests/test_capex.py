"""Capital expenditures (receipts.capex, receipts.ingest): ingest on SYNTHETIC companyfacts, and constructed claims
against data/snapshot. Capex is never ✗; extraction is untouched (no capex metric in its vocabulary)."""
from datetime import date
from pathlib import Path
from typing import get_args

import pytest

from receipts import capex, extract, types
from receipts.ingest import ISOLATED_METRICS, NON_NEGATIVE_METRICS, build_company
from receipts.store import Store, connect_rw, fact_row, replace_company
from receipts.types import Claim
from receipts.verify import verify
from tests.test_store import CF, CIK, FILINGS, FY, H1, M9, Q1, Q1_26, Q2, Q3, TICKER, acc, companyfacts, \
    submission_rows

ROOT = Path(__file__).resolve().parents[1]
PPE, PA = "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"


# --------------------------------------------------------------------------- extraction is untouched
def test_extraction_never_proposes_capex():
    assert "capital_expenditures" not in get_args(types.Metric)
    assert "capital_expenditures" not in extract.METRICS
    assert "capital_expenditures" not in str(extract.SCHEMA)


# --------------------------------------------------------------------------- ingest (synthetic)
def build(cf: dict, tmp_path: Path, filings: dict = FILINGS):
    b = build_company(cik=CIK, ticker=TICKER, names=["Test Corp"], fiscal_year_end="1231",
                      filing_rows=submission_rows(filings), companyfacts=cf, facts_start=date(2020, 1, 1),
                      today=date(2026, 10, 27))
    conn = connect_rw(tmp_path / "facts.sqlite")
    replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    return Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr"), b


def with_entries(*entries) -> dict:
    """CF plus capex entries (concept, period, value, filing key)."""
    extra = companyfacts(FILINGS, *entries)["facts"]["us-gaap"]
    usgaap = {k: {"units": {u: list(es) for u, es in v["units"].items()}} for k, v in CF["facts"]["us-gaap"].items()}
    for concept, body in extra.items():
        usgaap.setdefault(concept, {"units": {"USD": []}})["units"]["USD"] += body["units"]["USD"]
    return {"facts": {"us-gaap": usgaap}}


YTD = ((Q1, 40e6, "A1"), (H1, 95e6, "A2"), (M9, 150e6, "A3"), (FY, 230e6, "AK"))


def test_quarters_derive_from_year_to_date_as_operating_cash_flow(tmp_path):
    store, _ = build(with_entries(*((PPE, p, v, k) for p, v, k in YTD)), tmp_path)
    q = {f.fiscal_period: f for f in store.facts(CIK, "capital_expenditures") if f.fiscal_year == 2025}
    assert q["Q1"].source == "xbrl" and q["Q1"].value == 40e6
    assert (q["Q2"].value, q["Q2"].derived_from) == (55e6, [acc("A2"), acc("A1")])  # H1 - Q1
    assert (q["Q3"].value, q["Q3"].derived_from) == (55e6, [acc("A3"), acc("A2")])  # 9M - H1
    assert (q["Q4"].value, q["Q4"].derived_from) == (80e6, [acc("AK"), acc("A3")])  # FY - 9M
    assert q["FY"].value == 230e6 and q["Q4"].concept == f"us-gaap:{PPE}"


def test_productive_assets_only_where_it_is_the_sole_capex_line(tmp_path):
    sole, _ = build(with_entries(*((PA, p, v, k) for p, v, k in YTD)), tmp_path / "a")
    assert {f.concept for f in sole.facts(CIK, "capital_expenditures")} == {f"us-gaap:{PA}"}
    # a company that tags the property-and-equipment line anywhere in the window never serves the other: not summed
    both, _ = build(with_entries(*((PA, p, v, k) for p, v, k in YTD), (PPE, Q1_26, 30e6, "B1")), tmp_path / "b")
    facts = both.facts(CIK, "capital_expenditures")
    assert {f.concept for f in facts} == {f"us-gaap:{PPE}"} and [f.value for f in facts] == [30e6]


def test_capex_never_moves_another_metrics_rows(tmp_path):
    """A capex value with finer precision (thousands) and a stray 3-month capex period in a 10-Q change neither the
    other metrics' rows (their decimals included) nor the fiscal calendar."""
    _, plain = build(CF, tmp_path / "plain")
    entries = [(PPE, p, v, k) for p, v, k in YTD] + [(PPE, Q2, 54_321_000.0, "A2"), (PPE, ("2025-05-01", "2025-07-31"),
                                                                                       7e6, "A3")]
    _, added = build(with_entries(*entries), tmp_path / "added")
    rows = lambda b: sorted(str(fact_row(f, k)) for f, k in b.facts if f.metric != "capital_expenditures")  # noqa: E731
    assert rows(added) == rows(plain) and added.periods == plain.periods
    assert any(f.metric == "capital_expenditures" for f, _ in added.facts)
    assert "capital_expenditures" in ISOLATED_METRICS


def test_a_negative_derived_capex_quarter_is_not_served(tmp_path):
    """Capex payments are never negative: a 9M below its H1 (a spin-off restated one of them) derives nothing."""
    assert "capital_expenditures" in NON_NEGATIVE_METRICS
    entries = ((PPE, Q1, 40e6, "A1"), (PPE, H1, 95e6, "A2"), (PPE, M9, 90e6, "A3"))
    store, b = build(with_entries(*entries), tmp_path)
    assert not [f for f in store.facts(CIK, "capital_expenditures") if f.fiscal_period == "Q3"]
    assert any(s["metric"] == "capital_expenditures" and s["why"] == "negative" for s in b.stats["derivations_skipped"])


# --------------------------------------------------------------------------- reading
def claim(span: str, text: str, value: float, **kw) -> Claim:
    args = dict(id="c1", metric="other", metric_text="capital expenditures", value_text=text, value=value, unit="USD",
                scale="billions", period_text="Q3 2026", period_type="quarter", comparison="none", basis=None,
                forward_looking=False, entity="Alphabet", segment=None, span_text=span, span_start=0,
                span_end=len(span))
    args.update(kw)
    return Claim(**args)


@pytest.mark.parametrize("words", ["capital expenditures", "Capital expenditure", "capex", "CapEx", "cap-ex",
                                   "capital spending", "purchases of property and equipment",
                                   "additions to property, plant and equipment", "property and equipment purchases",
                                   "Quarterly capital expenditures", "total capital expenditures",
                                   "cash capital expenditures"])
def test_plain_capex_wording_reads_as_a_level(words):
    r = capex.read(claim(f"{words} were $51.36 billion", "$51.36 billion", 51.36, metric_text=words))
    assert r is not None and r.kind == "level" and r.scope == ()


@pytest.mark.parametrize("words,scope", [
    ("capital expenditures including finance leases", "including"), ("AI capital expenditures", "AI"),
    ("growth capex", "growth"), ("maintenance capital expenditures", "maintenance"),
    ("net capital expenditures", "net"), ("capital expenditures and investments", "investments"),
    ("capex for data centers", "data"), ("operating cash flow less capital expenditures", "operating cash flow"),
])
def test_scoped_capex_wording_has_scope(words, scope):
    r = capex.read(claim(f"{words} were $51.36 billion", "$51.36 billion", 51.36, metric_text=words))
    assert r is not None and scope in " ".join(r.scope)


@pytest.mark.parametrize("words", ["free cash flow", "capital returned to shareholders", "capital investments",
                                   "operating cash flow", "revenue"])
def test_other_measures_are_not_capex(words):
    assert capex.read(claim(f"{words} was $51.36 billion", "$51.36 billion", 51.36, metric_text=words)) is None


def test_change_and_dollar_change_kinds():
    pct = claim("Capital expenditures rose 120% year over year", "120%", 120, unit="%", scale="units", comparison="YoY")
    assert capex.read(pct).kind == "change"
    bare = pct.model_copy(update={"metric_text": "increase"})  # the subject right before the number names capex
    assert capex.read(bare).kind == "change"
    up = claim("Capital expenditures increased $42.2 billion", "$42.2 billion", 42.2)
    assert capex.read(up).kind == "absolute"
    share = claim("capital expenditures were 12% of revenue", "12%", 12, unit="%", scale="units")
    assert capex.read(share).kind == "share"


# --------------------------------------------------------------------------- verdicts against data/snapshot
@pytest.fixture(scope="module")
def store():
    s = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield s
    s.close()


def capex_facts(store, ticker: str, fy: int, fp: str) -> list[float]:
    cik = store.by_ticker(ticker)[0]
    return sorted({f.value for f in store.facts(cik, "capital_expenditures") if (f.fiscal_year, f.fiscal_period) == (fy, fp)})


def test_filed_values_of_the_probes(store):
    assert capex_facts(store, "GOOGL", 2026, "Q3") == [51_355e6]  # 9M 131,953 - H1 80,598
    assert capex_facts(store, "GOOGL", 2025, "Q3") == [23_953e6]  # 9M 63,596 - H1 39,643
    assert capex_facts(store, "MSFT", 2027, "Q1") == [39_367e6]   # filed as a 3-month figure
    assert capex_facts(store, "META", 2026, "Q3") == [33_942e6]
    assert capex_facts(store, "AMZN", 2026, "Q3") == [77_259e6]   # PaymentsToAcquireProductiveAssets


SENTENCE = "Quarterly capital expenditures reached $51.36 billion, compared with $23.95 billion a year earlier"


@pytest.mark.parametrize("text,value,period,fp", [("$51.36 billion", 51.36, "Q3 2026", "Q3 2026"),
                                                  ("$23.95 billion", 23.95, "a year earlier", "Q3 2025")])
def test_alphabet_q3_2026_draft_sentence(store, text, value, period, fp):
    c = claim(SENTENCE, text, value, metric_text="Quarterly capital expenditures", period_text=period)
    v = verify(c, store, context="Alphabet, third quarter of 2026", draft=SENTENCE)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert fp in v.explanation and v.evidence[0].metric == "capital_expenditures"


def test_microsoft_45_2_billion_is_not_the_filed_cash_figure(store):
    c = claim("Capital expenditures reached $45.2 billion", "$45.2 billion", 45.2, entity="Microsoft",
              period_text="Q1 FY2027")
    v = verify(c, store)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "$39.367 billion" in v.explanation and "define capital expenditures" in v.explanation
    assert v.evidence and v.evidence[0].value == 39_367e6 and v.expected_value is None
    true = c.model_copy(update={"span_text": "Capital expenditures reached $39.4 billion", "value_text": "$39.4 billion",
                                "value": 39.4})
    assert verify(true, store).status == "verified"


@pytest.mark.parametrize("entity,span,text,value", [
    ("Meta", "Capital expenditures were $33.94 billion", "$33.94 billion", 33.94),
    ("Amazon", "Purchases of property and equipment were $77.26 billion", "$77.26 billion", 77.26),
    ("Amazon", "Amazon's capex hit $77.3 billion in the third quarter", "$77.3 billion", 77.3),
])
def test_true_capex_is_verified(store, entity, span, text, value):
    v = verify(claim(span, text, value, entity=entity), store)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


@pytest.mark.parametrize("entity,span,text,value,scale", [
    ("Alphabet", "Capital expenditures reached $45.2 billion", "$45.2 billion", 45.2, "billions"),
    ("Alphabet", "Capital expenditures reached $51.36 million", "$51.36 million", 51.36, "millions"),  # scale slip
    ("Meta", "Capital expenditures were $18.83 billion", "$18.83 billion", 18.83, "billions"),  # the year-ago figure
    ("Amazon", "Capital expenditures were $51.36 billion", "$51.36 billion", 51.36, "billions"),  # Alphabet's figure
])
def test_wrong_capex_is_never_wrong(store, entity, span, text, value, scale):
    v = verify(claim(span, text, value, entity=entity, scale=scale), store)
    assert v.status == "unverifiable" and v.reason == "no_evidence", v.explanation
    assert "define capital expenditures" in v.explanation and v.evidence


@pytest.mark.parametrize("span", [
    "Capital expenditures, including finance leases, were $45.2 billion",
    "Capital expenditures (including finance leases) were $45.2 billion",
    "Including finance leases, capital expenditures were $45.2 billion",
    "Capital expenditures were $45.2 billion, including finance leases",
])
def test_finance_lease_capex_is_unverifiable(store, span):
    c = claim(span, "$45.2 billion", 45.2, entity="Microsoft", period_text="Q1 FY2027")
    v = verify(c, store, draft=span)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence")
    assert "including" in v.explanation.lower() and "finance" in v.explanation


def test_capex_growth(store):
    up = claim("Capital expenditures rose 120% year over year", "120%", 120, entity="Amazon", unit="%", scale="units",
               comparison="YoY")
    v = verify(up, store)
    assert v.status == "verified" and "120.1%" in v.explanation  # both quarters in one 10-Q: $77.259B / $35.095B
    wrong = up.model_copy(update={"span_text": "Capital expenditures rose 83% year over year", "value_text": "83%",
                                  "value": 83})
    assert (verify(wrong, store).status, verify(wrong, store).reason) == ("unverifiable", "no_evidence")
    # Alphabet's quarters are both derived from other filings' year-to-date figures: no change computed, "?"
    googl = up.model_copy(update={"entity": "Alphabet"})
    assert verify(googl, store).status == "unverifiable"


def test_capex_guidance_stays_guidance(store):
    c = claim("Alphabet expects 2027 capital expenditures of $185 billion", "$185 billion", 185, forward_looking=True,
              period_text="2027", period_type="annual")
    assert (verify(c, store).status, verify(c, store).reason) == ("unverifiable", "forward_looking")


def test_capex_before_it_was_public_is_look_ahead(store):
    v = verify(claim("Capital expenditures reached $51.36 billion", "$51.36 billion", 51.36), store,
               as_of="2026-10-01T00:00:00Z")
    assert (v.status, v.reason) == ("wrong", "look_ahead")  # ◷, counted as not public, never ✗
    assert types.count_key(v) == "not_public"


def test_release_statement_still_confirms_a_capex_figure_the_filing_reports_otherwise(store):
    """C.H. Robinson's release: "Capital expenditures totaled $17.5 million in the quarter." Its 10-Q's purchases of
    property and equipment are $3.602 million (capitalized software is a line of its own): the filed check says "?" and
    names that figure, and the release-text fallback then confirms the company's own number. A planted figure stays "?"."""
    from receipts.verify import _verify_filed

    draft = "Capital expenditures totaled $17.5 million in the quarter."
    span = "Capital expenditures totaled $17.5 million"
    c = claim(span, "$17.5 million", 17.5, entity="C.H. Robinson", scale="millions")
    ctx = "C.H. Robinson, third quarter of 2026"
    filed = _verify_filed(c, store, context=ctx, draft=draft)
    assert (filed.status, filed.reason) == ("unverifiable", "no_evidence") and "$3.602 million" in filed.explanation
    v = verify(c, store, context=ctx, draft=draft)
    assert (v.status, v.reason) == ("verified", "release_statement"), v.explanation
    planted = draft.replace("17.5", "19.5")
    p = c.model_copy(update={"span_text": span.replace("17.5", "19.5"), "value_text": "$19.5 million", "value": 19.5})
    assert verify(p, store, context=ctx, draft=planted).status == "unverifiable"
