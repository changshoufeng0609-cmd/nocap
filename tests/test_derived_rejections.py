"""receipts.ingest and scripts/gate_release_facts.py: derived quarters a company's own release outranks, and a cost of
revenue tagged beside a larger cost total (review fix 7, QA round 2). SYNTHETIC filings for a fictional company."""
import json
from datetime import date, datetime, timezone

from receipts.ingest import build_company
from receipts.store import connect_rw, replace_company
from scripts.gate_release_facts import run
from tests.test_period_checks import FILINGS, Q2, Q3, Q4, YEAR1, build, served
from tests.test_release_gates import fact


def test_a_rejected_derived_quarter_is_not_served_and_the_audit_says_why():
    rejected = [{"metric": "revenue", "period_start": Q4[0], "period_end": Q4[1], "value": 100e6, "why": "w",
                 "release": {"accession": "0000000905-26-000009"}}]
    plain = build(*YEAR1)
    assert served(plain, "revenue", "Q4", 2025) == [(100e6, "derived", FILINGS["K"][0])]
    usgaap: dict = {}
    for concept, (start, end), value, key in YEAR1:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    b = build_company(cik=905, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                      companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1),
                      today=date(2027, 3, 31), rejected_derived=rejected)
    assert served(b, "revenue", "Q4", 2025) == []
    assert any("outranked by the company's release 0000000905-26-000009" in s["why"]
               for s in b.stats["derivations_skipped"])
    rejected[0]["value"] = 99e6  # another value: not this derived quarter
    b = build_company(cik=905, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                      companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1),
                      today=date(2027, 3, 31), rejected_derived=rejected)
    assert served(b, "revenue", "Q4", 2025) == [(100e6, "derived", FILINGS["K"][0])]


def test_a_cost_of_revenue_tagged_beside_a_larger_cost_total_is_a_component():
    """Illinois Tool Works tags CostOfRevenue at $1,914M for Q2 2025 beside the income statement's
    CostOfGoodsAndServicesSold of $2,271M; the larger figure is cost of revenue. A company whose CostOfRevenue is the
    larger keeps it (cost of goods plus cost of services)."""
    itw = build(("CostOfRevenue", Q2, 1914e6, "Q2"), ("CostOfGoodsAndServicesSold", Q2, 2271e6, "Q2"))
    assert served(itw, "cost_of_revenue", "Q2", 2025) == [(2271e6, "xbrl", FILINGS["Q2"][0])]
    usual = build(("CostOfRevenue", Q2, 2500e6, "Q2"), ("CostOfGoodsAndServicesSold", Q2, 2271e6, "Q2"))
    assert served(usual, "cost_of_revenue", "Q2", 2025) == [(2500e6, "xbrl", FILINGS["Q2"][0])]


def test_the_script_records_what_a_release_outranks_and_runs_again_to_no_change(tmp_path):
    """The release reports Q4 at $90M where the store derives FY - 9M = $100M; the same row agrees with the 10-Qs."""
    b = build(*YEAR1)
    db = tmp_path / "facts.sqlite"
    conn = connect_rw(db)
    replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    at = datetime(2026, 1, 28, 21, tzinfo=timezone.utc)
    folder = tmp_path / "pr" / "EXMP"
    folder.mkdir(parents=True)
    rows = [fact("revenue", 100e6, Q3, accepted=at), fact("revenue", 90e6, Q4, accepted=at)]
    for f in rows:
        f.cik, f.accession = 905, "0000000905-26-000009"
    (folder / "0000000905-26-000009.jsonl").write_text("".join(f.model_dump_json() + "\n" for f in rows))
    rejections = tmp_path / "derived_rejections.json"
    cf = {"facts": {"us-gaap": {}}}
    dry = run(tmp_path / "pr", db, write=False, load_companyfacts=lambda cik: cf, rejections_path=rejections)
    assert dry["dropped"] == [] and dry["reingest"] == ["EXMP"] and not rejections.exists()
    done = run(tmp_path / "pr", db, write=True, load_companyfacts=lambda cik: cf, rejections_path=rejections)
    recorded = json.loads(rejections.read_text())
    assert [r["value"] for r in recorded["EXMP"]] == [100e6] and done["reingest"] == ["EXMP"]
    again = run(tmp_path / "pr", db, write=True, load_companyfacts=lambda cik: cf, rejections_path=rejections)
    assert again["new_rejections"] == {} and json.loads(rejections.read_text()) == recorded


def test_a_per_share_dividend_is_served_as_filed_and_never_derived():
    """Eli Lilly declares two dividends in its second quarter and none in its third: 9M - H1 would be $0 for Q3 and
    FY - 9M twice the quarterly rate for Q4. The filed quarter and year are served; no quarter is derived."""
    from tests.test_period_checks import CALENDAR, FY, H1, M9
    usgaap: dict = {}
    for concept, (start, end), value, key in CALENDAR:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    for (start, end), value, key in ((Q2, 3.0, "Q2"), (H1, 3.0, "Q2"), (M9, 3.0, "Q3"), (FY, 6.23, "K")):
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault("CommonStockDividendsPerShareDeclared", {"units": {}})["units"].setdefault(
            "USD/shares", []).append({"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp,
                                      "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    b = build_company(cik=905, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                      companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1), today=date(2027, 3, 31))
    by_period = sorted((f.fiscal_period, f.value, f.source) for f, kind in b.facts if f.metric == "dividend_per_share")
    # Q2's $3.00 is two declarations and no neighbouring quarter files a rate (review fix 12): not served either
    assert by_period == [("9M", 3.0, "xbrl"), ("FY", 6.23, "xbrl"), ("H1", 3.0, "xbrl")]


def test_profitloss_stands_for_net_income_only_where_no_noncontrolling_interest_shows():
    """Ares tags ProfitLoss for Q3 2023 and no NCI concept, but its 10-K shows FY ProfitLoss of $885.8M against
    NetIncomeLoss of $474.3M: the Q3 ProfitLoss is not its net income (review fix 15). Broadcom tags only ProfitLoss
    and no NCI, so its ProfitLoss is its net income; so is a period whose whole NCI is tagged as zero, unless a filing
    shows an NCI for it (APA tags its redeemable NCI as zero while a later 10-Q reports nonredeemable NCI)."""
    from tests.test_period_checks import CALENDAR, FY, Q1
    nci, red, nonred = (f"NetIncomeLossAttributableTo{k}NoncontrollingInterest"
                        for k in ("", "Redeemable", "Nonredeemable"))

    def company(*entries):
        usgaap: dict = {}
        calendar = [("Revenues", per, v * 10, k) for _, per, v, k in CALENDAR]   # labels the periods
        for concept, (start, end), value, key in calendar + [*entries]:
            a, form, filed, _, _, fy, fp = FILINGS[key]
            usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
                {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
        rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at,
                 "reportDate": report, "primaryDocument": "doc.htm", "items": ""}
                for a, form, filed, at, report, _, _ in FILINGS.values()]
        return build_company(cik=907, ticker="EXMR", names=["Example Three"], fiscal_year_end="1231", filing_rows=rows,
                             companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1),
                             today=date(2027, 3, 31))

    year = (("ProfitLoss", FY, 885.8e6, "K"), ("NetIncomeLoss", FY, 474.3e6, "K"))   # an NCI shows in the 10-K
    ares = company(("ProfitLoss", Q3, 196.97e6, "Q3"), *year)
    assert served(ares, "net_income", "Q3", 2025) == []
    assert served(ares, "net_income", "FY", 2025) == [(474.3e6, "xbrl", FILINGS["K"][0])]
    avgo = company(("ProfitLoss", Q3, 4.14e9, "Q3"), ("ProfitLoss", FY, 9.9e9, "K"))
    assert served(avgo, "net_income", "Q3", 2025) == [(4.14e9, "xbrl", FILINGS["Q3"][0])]
    q1 = ("ProfitLoss", Q1, 50e6, "Q1")
    assert served(company(q1, (nci, Q1, 0.0, "Q1"), *year), "net_income", "Q1", 2025) == [
        (50e6, "xbrl", FILINGS["Q1"][0])]
    assert served(company(q1, (red, Q1, 0.0, "Q1"), (nonred, Q1, 0.0, "Q1"), *year), "net_income", "Q1", 2025) != []
    assert served(company(q1, (red, Q1, 0.0, "Q1"), *year), "net_income", "Q1", 2025) == []   # only part of it
    shown_later = company(q1, (nci, Q1, 0.0, "Q1"), (nonred, Q1, 9e6, "Q1n"), *year)
    assert served(shown_later, "net_income", "Q1", 2025) == []


def test_net_income_is_profitloss_less_the_whole_nci_one_filing_tags_for_the_period():
    """CF Industries tags ProfitLoss ($869M) and the NCI ($142M) for Q2 but no NetIncomeLoss: net income is the
    difference, $727M, served as derived with the filing as its input. One side alone, part of the NCI, or a total
    beside parts it does not add up to stays "?"; a filing that tags NetIncomeLoss is served that."""
    from tests.test_period_checks import CALENDAR, FY, Q1, Q2, Q3
    nci, red, nonred = (f"NetIncomeLossAttributableTo{k}NoncontrollingInterest"
                        for k in ("", "Redeemable", "Nonredeemable"))

    def company(*entries):
        usgaap: dict = {}
        calendar = [("Revenues", per, v * 10, k) for _, per, v, k in CALENDAR]
        for concept, (start, end), value, key in calendar + [*entries]:
            a, form, filed, _, _, fy, fp = FILINGS[key]
            usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
                {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
        rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at,
                 "reportDate": report, "primaryDocument": "doc.htm", "items": ""}
                for a, form, filed, at, report, _, _ in FILINGS.values()]
        return build_company(cik=908, ticker="EXMQ", names=["Example Four"], fiscal_year_end="1231", filing_rows=rows,
                             companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2025, 1, 1),
                             today=date(2027, 3, 31))

    b = company(("ProfitLoss", Q2, 869e6, "Q2"), (nci, Q2, 142e6, "Q2"),            # both sides: 727
                ("ProfitLoss", Q1, 800e6, "Q1"),                                     # ProfitLoss alone
                ("ProfitLoss", Q3, 900e6, "Q3"), (red, Q3, 10e6, "Q3"), (nonred, Q3, 20e6, "Q3"),  # both parts: 870
                ("ProfitLoss", FY, 3500e6, "K"), (nci, FY, 150e6, "K"), (red, FY, 10e6, "K"))      # total and a part
    q2 = [(f.value, f.source, f.derived_from, f.locator) for f, _ in b.facts
          if f.metric == "net_income" and f.fiscal_period == "Q2" and f.fiscal_year == 2025]
    assert q2 == [(727e6, "derived", [FILINGS["Q2"][0]], "NetIncomeLoss = ProfitLoss 869,000,000 - "
                   f"{nci} 142,000,000 (10-Q {FILINGS['Q2'][0]})")]
    assert served(b, "net_income", "Q3", 2025) == [(870e6, "derived", FILINGS["Q3"][0])]
    assert served(b, "net_income", "Q1", 2025) == [] and served(b, "net_income", "FY", 2025) == []
    tagged = company(("ProfitLoss", Q2, 869e6, "Q2"), (nci, Q2, 142e6, "Q2"), ("NetIncomeLoss", Q2, 727e6, "Q2"))
    assert served(tagged, "net_income", "Q2", 2025) == [(727e6, "xbrl", FILINGS["Q2"][0])]
