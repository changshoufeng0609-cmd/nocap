"""receipts.ingest: figures whose value contradicts their period (review fix 3, QA T1) and derived quarters on mixed
bases (review fix 4, QA T3 data side). SYNTHETIC filings for a fictional calendar-year company."""
from datetime import date

from receipts.ingest import build_company

FILINGS = {  # key -> (accession, form, filed, accepted, report date, DEI fy, DEI fp)
    "Q1": ("0000000905-25-000001", "10-Q", "2025-04-30", "2025-04-30T20:00:00.000Z", "2025-03-31", 2025, "Q1"),
    "Q2": ("0000000905-25-000002", "10-Q", "2025-07-30", "2025-07-30T20:00:00.000Z", "2025-06-30", 2025, "Q2"),
    "Q3": ("0000000905-25-000003", "10-Q", "2025-10-30", "2025-10-30T20:00:00.000Z", "2025-09-30", 2025, "Q3"),
    "K": ("0000000905-26-000001", "10-K", "2026-02-20", "2026-02-20T21:00:00.000Z", "2025-12-31", 2025, "FY"),
    "Q1n": ("0000000905-26-000002", "10-Q", "2026-04-30", "2026-04-30T20:00:00.000Z", "2026-03-31", 2026, "Q1"),
    "Q2n": ("0000000905-26-000003", "10-Q", "2026-07-30", "2026-07-30T20:00:00.000Z", "2026-06-30", 2026, "Q2"),
    "Q3n": ("0000000905-26-000004", "10-Q", "2026-10-29", "2026-10-29T20:00:00.000Z", "2026-09-30", 2026, "Q3"),
    "Kn": ("0000000905-27-000001", "10-K", "2027-02-19", "2027-02-19T21:00:00.000Z", "2026-12-31", 2026, "FY"),
}
Q1, Q2, Q3, Q4 = ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"), ("2025-07-01", "2025-09-30"), \
    ("2025-10-01", "2025-12-31")
H1, M9, FY = ("2025-01-01", "2025-06-30"), ("2025-01-01", "2025-09-30"), ("2025-01-01", "2025-12-31")
N1, N2, N3, N4 = ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30"), ("2026-07-01", "2026-09-30"), \
    ("2026-10-01", "2026-12-31")
NH1, NM9, NFY = ("2026-01-01", "2026-06-30"), ("2026-01-01", "2026-09-30"), ("2026-01-01", "2026-12-31")
# Every filing's own periods (so the calendar labels them) with a net income line the tests leave alone.
CALENDAR = [("NetIncomeLoss", Q1, 10e6, "Q1"), ("NetIncomeLoss", Q2, 10e6, "Q2"), ("NetIncomeLoss", Q3, 10e6, "Q3"),
            ("NetIncomeLoss", FY, 40e6, "K"), ("NetIncomeLoss", N1, 11e6, "Q1n"), ("NetIncomeLoss", N2, 11e6, "Q2n"),
            ("NetIncomeLoss", N3, 11e6, "Q3n"), ("NetIncomeLoss", NFY, 44e6, "Kn")]


def build(*entries, today=date(2027, 3, 31)):
    usgaap: dict = {}
    for concept, (start, end), value, key in (*CALENDAR, *entries):
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    return build_company(cik=905, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                         companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1), today=today)


def served(b, metric, fp, fy):
    return sorted((f.value, f.source, f.accession) for f, kind in b.facts
                  if f.metric == metric and f.fiscal_period == fp and f.fiscal_year == fy)


YEAR1 = [("Revenues", Q1, 100e6, "Q1"), ("Revenues", Q2, 100e6, "Q2"), ("Revenues", H1, 200e6, "Q2"),
         ("Revenues", Q3, 100e6, "Q3"), ("Revenues", M9, 300e6, "Q3"), ("Revenues", FY, 400e6, "K")]
YEAR2 = [("Revenues", N1, 110e6, "Q1n"), ("Revenues", N2, 110e6, "Q2n"), ("Revenues", NH1, 220e6, "Q2n"),
         ("Revenues", N3, 110e6, "Q3n"), ("Revenues", NM9, 330e6, "Q3n")]


def test_l3harris_shape_full_year_figures_under_q4_dates_are_never_served_as_a_quarter():
    """FY2025: the 10-K tags the full year under its Q4 dates too (rule a), so Q4 is FY - 9M. FY2026: the 10-K tags
    the full year only under Q4 dates (rule c: larger than the whole 9M), so there is no Q4 to serve."""
    b = build(*YEAR1, ("Revenues", Q4, 400e6, "K"), *YEAR2, ("Revenues", N4, 440e6, "Kn"))
    assert served(b, "revenue", "Q4", 2025) == [(100e6, "derived", FILINGS["K"][0])]
    assert served(b, "revenue", "Q4", 2026) == []          # "?": no 12-month figure to derive from
    rules = {(r["fiscal"], r["rule"]) for r in b.stats["misdated_dropped"]}
    assert rules == {("Q4 FY2025", "a"), ("Q4 FY2026", "c")}
    assert any(w.startswith("periods: 2 quarterly or full-year figures") for w in b.warnings)


def test_a_q4_that_breaks_the_sum_goes_only_in_a_year_never_restated():
    b = build(*YEAR1, ("Revenues", Q4, 150e6, "K"))       # 100 + 100 + 100 + 150 against 400
    assert served(b, "revenue", "Q4", 2025) == [(100e6, "derived", FILINGS["K"][0])]
    assert [r["rule"] for r in b.stats["misdated_dropped"]] == ["b"]
    restated = build(*YEAR1, ("Revenues", Q4, 150e6, "K"), ("Revenues", FY, 450e6, "Kn"))  # FY recast a year on
    assert (150e6, "xbrl", FILINGS["K"][0]) in served(restated, "revenue", "Q4", 2025)
    assert restated.stats["misdated_dropped"] == []         # two bases: the sum proves nothing


def test_a_quarter_under_a_full_year_date_in_a_10q_is_dropped():
    """Comfort Systems' Q1 2026 10-Q tags its Q1 2025 figures under a full-year 2025 date."""
    b = build(*YEAR1, ("Revenues", FY, 100e6, "Q1n"), ("Revenues", FY, 400e6, "Q2n"))
    assert sorted(v for v, _, _ in served(b, "revenue", "FY", 2025)) == [400e6, 400e6]
    assert [(r["rule"], r["accession"]) for r in b.stats["misdated_dropped"]] == [("d", FILINGS["Q1n"][0])]
    assert served(b, "revenue", "Q1", 2025)[0][0] == 100e6  # the true quarter is untouched


def test_dollar_tree_shape_only_the_latest_pair_is_derived_after_the_9m_is_restated():
    """The FY2025 10-K already excludes a discontinued business; the original 9M includes it. The 9M is restated a
    year on and the FY2026 10-K repeats the FY2025 total, so only that pair is consistent."""
    b = build(("CostOfGoodsAndServicesSold", M9, 15661.2e6, "Q3"), ("CostOfGoodsAndServicesSold", FY, 11284.1e6, "K"),
              ("CostOfGoodsAndServicesSold", M9, 8167.2e6, "Q3n"), ("CostOfGoodsAndServicesSold", FY, 11284.1e6, "Kn"))
    assert served(b, "cost_of_revenue", "Q4", 2025) == [(3116.9e6, "derived", FILINGS["Kn"][0])]


def test_a_negative_derived_quarter_of_a_metric_never_negative_is_not_served():
    b = build(("OperatingExpenses", FY, 100e6, "K"), ("OperatingExpenses", M9, 150e6, "Q3"))
    assert served(b, "operating_expenses", "Q4", 2025) == []
    assert [s["why"] for s in b.stats["derivations_skipped"]] == ["negative"]
    two = build(("OperatingExpenses", FY, 100e6, "K"), ("CostsAndExpenses", M9, 150e6, "Q3"))  # never paired now
    assert served(two, "operating_expenses", "Q4", 2025) == []
    assert [s["why"] for s in two.stats["derivations_skipped"]] == ["its components are filed under another concept"]


def test_a_derived_q4_outside_a_tenth_to_three_quarters_of_its_year_is_not_served():
    """Crown Castle: FY CostOfRevenue $1,935M minus a 9M of the narrower CostOfGoodsAndServicesSold $268M (86% of the
    year); OXY: a 10-K without OxyChem ($21,593M) minus a 9M with it ($19,841M) (8.1%)."""
    high = build(("CostOfRevenue", FY, 1935e6, "K"), ("CostOfGoodsAndServicesSold", M9, 268e6, "Q3"))
    assert served(high, "cost_of_revenue", "Q4", 2025) == []
    low = build(("Revenues", FY, 21593e6, "K"), ("Revenues", M9, 19841e6, "Q3"))
    assert served(low, "revenue", "Q4", 2025) == []
    assert [s["why"] for s in high.stats["derivations_skipped"]] == ["its components are filed under another concept"]
    assert [s["why"] for s in low.stats["derivations_skipped"]] == ["outside 10%-75% of its fiscal year"]
    seasonal = build(("Revenues", FY, 4275.4e6, "K"), ("Revenues", M9, 3687.8e6, "Q3"))  # Atmos' summer: 13.7%
    assert [v for v, _, _ in served(seasonal, "revenue", "Q4", 2025)] == [587.6e6]


def test_martin_marietta_shape_a_rounded_9m_is_not_a_restatement():
    """The 9M is $3,629.8M in its 10-Q and $3,630M as a comparative; the FY is recast a year on ($4,754.6M to
    $4,106M). Recast FY minus the rounded 9M (476) is a restated total with stale components."""
    b = build(("CostOfGoodsAndServicesSold", M9, 3629.8e6, "Q3"), ("CostOfGoodsAndServicesSold", FY, 4754.6e6, "K"),
              ("CostOfGoodsAndServicesSold", M9, 3630e6, "Q3n"), ("CostOfGoodsAndServicesSold", FY, 4106e6, "Kn"))
    assert [v for v, _, _ in served(b, "cost_of_revenue", "Q4", 2025)] == [1124.8e6]


def test_a_derived_quarter_pairs_only_components_of_the_totals_concept():
    """Hasbro: FY Revenues minus a nine-month RevenueFromContractWithCustomer gave Q4 2025 revenue of $2,110.5M against
    a released $1,445.9M (review fix 14). Where the total's own 10-K reports the year under both concepts at one value,
    the two are one measure and the quarter derives."""
    rfc = "RevenueFromContractWithCustomerExcludingAssessedTax"
    mixed = build(("Revenues", FY, 4135e6, "K"), (rfc, M9, 2024.5e6, "Q3"))
    assert served(mixed, "revenue", "Q4", 2025) == []
    assert [s["why"] for s in mixed.stats["derivations_skipped"]] == ["its components are filed under another concept"]
    one = build(("Revenues", FY, 4135e6, "K"), (rfc, FY, 4135e6, "K"), (rfc, M9, 3000e6, "Q3"))
    assert [v for v, _, _ in served(one, "revenue", "Q4", 2025)] == [1135e6]


def test_crown_castle_shape_a_total_restated_since_first_filed_under_its_concept_derives_nothing():
    """Crown Castle's FY2023 10-K serves CostOfRevenue ($1,935M) and also tags the narrower CostOfGoodsAndServicesSold
    ($316M) its 10-Qs serve ($268M for the 9M). Its FY2025 10-K restates that line to $304M: $304M - $268M mixes
    bases, so the quarter is "?" (review fix 14). Without the restatement the later 10-K's figure derives it."""
    restated = build(("CostOfRevenue", FY, 1935e6, "K"), ("CostOfGoodsAndServicesSold", FY, 316e6, "K"),
                     ("CostOfGoodsAndServicesSold", M9, 268e6, "Q3"), ("CostOfGoodsAndServicesSold", FY, 304e6, "Kn"))
    assert served(restated, "cost_of_revenue", "Q4", 2025) == []
    assert [s["why"] for s in restated.stats["derivations_skipped"]] == [
        "its components are filed under another concept",
        "restated since first filed, against the components of that filing"]
    same = build(("CostOfRevenue", FY, 1935e6, "K"), ("CostOfGoodsAndServicesSold", FY, 316e6, "K"),
                 ("CostOfGoodsAndServicesSold", M9, 268e6, "Q3"), ("CostOfGoodsAndServicesSold", FY, 316e6, "Kn"))
    assert served(same, "cost_of_revenue", "Q4", 2025) == [(48e6, "derived", FILINGS["Kn"][0])]
