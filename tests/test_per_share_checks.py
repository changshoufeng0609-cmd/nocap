"""receipts.ingest: a quarter's dividend per share is served only as the company's quarterly rate (review fix 12, QA
round 3 T2), and per-share values beyond plausible limits are never served (review fix 13, T3). SYNTHETIC filings
for a fictional calendar-year company."""
from datetime import date

from receipts.ingest import build_company
from tests.test_period_checks import CALENDAR, FILINGS, Q1, Q2, Q3

DPS, EPS = "CommonStockDividendsPerShareDeclared", "EarningsPerShareDiluted"


def build(*entries):
    usgaap: dict = {}
    for concept, (start, end), value, key in CALENDAR:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    for concept, (start, end), value, key in entries:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD/shares", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    return build_company(cik=906, ticker="EXMQ", names=["Example Two"], fiscal_year_end="1231", filing_rows=rows,
                         companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1),
                         today=date(2027, 3, 31))


def quarters(b, metric="dividend_per_share"):
    return sorted((f.fiscal_period, f.value) for f, kind in b.facts if f.metric == metric and kind == "quarter")


def test_a_regular_quarterly_dividend_is_served():
    b = build((DPS, Q1, 0.51, "Q1"), (DPS, Q2, 0.51, "Q2"), (DPS, Q3, 0.51, "Q3"))
    assert quarters(b) == [("Q1", 0.51), ("Q2", 0.51), ("Q3", 0.51)]


def test_declaration_timing_quarters_are_not_served():
    """Pfizer declares its first-quarter dividend in December: Q1 files $0, Q2 $0.86 (two declarations of $0.43),
    Q3 $0.43, whose only filed neighbour is Q2. None of them is a rate a neighbour confirms."""
    b = build((DPS, Q1, 0.0, "Q1"), (DPS, Q2, 0.86, "Q2"), (DPS, Q3, 0.43, "Q3"))
    assert quarters(b) == []
    why = {d["start"]: d["why"] for d in b.stats["dividend_quarters_not_served"]}
    assert why[Q1[0]] == "zero" and why[Q2[0]] == why[Q3[0]] == "not the rate of a neighbouring quarter"


def test_a_raise_is_served_once_the_next_quarter_files_the_same_rate():
    b = build((DPS, Q1, 0.50, "Q1"), (DPS, Q2, 0.52, "Q2"), (DPS, Q3, 0.52, "Q3"))
    assert quarters(b) == [("Q2", 0.52), ("Q3", 0.52)]  # Q1's only filed neighbour already pays the new rate


def test_a_per_share_value_scaled_a_millionfold_is_never_served():
    """Halliburton tagged Q3 2023 diluted EPS as 790,000 (true $0.79); Mosaic a $0.22 dividend as $220,000."""
    b = build((EPS, Q1, 0.79, "Q1"), (EPS, Q2, 790000.0, "Q2"), (DPS, Q1, 0.22, "Q1"), (DPS, Q2, 220000.0, "Q2"),
              (DPS, Q3, 0.22, "Q3"))
    assert quarters(b, "eps_diluted") == [("Q1", 0.79)]
    assert sorted(d["value"] for d in b.stats["per_share_implausible"]) == [220000.0, 790000.0]
    assert any(w.startswith("per share: 2 values") for w in b.warnings)
