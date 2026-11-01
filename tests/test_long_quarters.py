"""16-week fiscal quarters: release facts take their true dates and labels from the fiscal calendar, and the label gate
(review fix 8) still drops a figure whose label its dates contradict. SYNTHETIC filings and releases for fictional
companies shaped like Costco (12 + 12 + 12 + 16 weeks, 17 in a 53-week year) and Kroger (16 + 12 + 12 + 12); every
number is invented."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from receipts import press_release as pr
from receipts.fiscal_labels import LONG_Q1_SOURCE, FilingPeriod, gate_message, label_periods
from receipts.ingest import build_company
from receipts.release_gates import XbrlValue, gate_all, label_conflict, served
from receipts.store import connect_rw, replace_company
from receipts.types import Fact
from scripts.gate_release_facts import run as gate_run
from scripts.relabel_release_facts import redate
from test_press_release import column, money_row, row, table_html, td

UTC = timezone.utc


def d(s: str) -> date:
    return date.fromisoformat(s)


def k(n: int, start: str, end: str, fy: int) -> FilingPeriod:
    return FilingPeriod(f"K{n}", "10-K", end, d(start), d(end), fy, "FY")


def q(n: int, start: str, end: str, fy: int, fp: str) -> FilingPeriod:
    return FilingPeriod(f"Q{n}", "10-Q", end, d(start), d(end), fy, fp)


def calendar_of(labels) -> dict:
    """{(fiscal year, period): (start, end)}, the shape receipts.release_gates reads from fiscal_periods."""
    return {(lab.fiscal_year, lab.fiscal_period): (lab.start, lab.end) for lab in labels}


# A Costco-shaped filer: 52/53-week years ending near August 31, named for the year they end in; quarters of 12, 12, 12
# and 16 weeks (17 in the 53-week FY2023). Filing key -> (accession, form, filed, DEI fy, DEI fp, own period).
WHOLESALE = {
    "K23": ("0000000905-23-000042", "10-K", "2023-10-11", 2023, "FY", ("2022-08-29", "2023-09-03")),
    "Q3_23": ("0000000905-23-000028", "10-Q", "2023-06-01", 2023, "Q3", ("2023-02-13", "2023-05-07")),
    "K24": ("0000000905-24-000049", "10-K", "2024-10-09", 2024, "FY", ("2023-09-04", "2024-09-01")),
    "Q3_24": ("0000000905-24-000029", "10-Q", "2024-06-06", 2024, "Q3", ("2024-02-19", "2024-05-12")),
    "K25": ("0000000905-25-000101", "10-K", "2025-10-08", 2025, "FY", ("2024-09-02", "2025-08-31")),
    "Q3_25": ("0000000905-25-000033", "10-Q", "2025-06-05", 2025, "Q3", ("2025-02-17", "2025-05-11")),
    "Q1_26": ("0000000905-25-000169", "10-Q", "2025-12-18", 2026, "Q1", ("2025-09-01", "2025-11-23")),
    "Q2_26": ("0000000905-26-000029", "10-Q", "2026-03-12", 2026, "Q2", ("2025-11-24", "2026-02-15")),
    "Q3_26": ("0000000905-26-000051", "10-Q", "2026-06-04", 2026, "Q3", ("2026-02-16", "2026-05-10")),
    "K26": ("0000000905-26-000093", "10-K", "2026-10-07", 2026, "FY", ("2025-09-01", "2026-08-30")),
}
REVENUE = {"K23": 245_000e6, "Q3_23": 53_000e6, "K24": 254_000e6, "Q3_24": 58_000e6, "K25": 272_156e6,
           "Q3_25": 63_000e6, "Q1_26": 67_000e6, "Q2_26": 69_000e6, "Q3_26": 70_000e6, "K26": 301_723e6}


def wholesale_facts(keys=tuple(WHOLESALE), nci: bool = False) -> dict:
    """companyfacts for the Costco-shaped filer: each filing's own revenue (and, for the 10-Ks, net income)."""
    usgaap: dict = {}
    for key in keys:
        accn, form, filed, fy, fp, (start, end) = WHOLESALE[key]
        e = {"start": start, "end": end, "accn": accn, "form": form, "filed": filed, "fy": fy, "fp": fp}
        usgaap.setdefault("Revenues", {"units": {"USD": []}})["units"]["USD"].append({**e, "val": REVENUE[key]})
        if form == "10-K":
            usgaap.setdefault("NetIncomeLoss", {"units": {"USD": []}})["units"]["USD"].append(
                {**e, "val": REVENUE[key] * 0.03})
            if nci:
                usgaap.setdefault("NetIncomeLossAttributableToNoncontrollingInterest", {"units": {"USD": []}})[
                    "units"]["USD"].append({**e, "val": 5e6})
    return {"facts": {"us-gaap": usgaap}}


def wholesale_rows(keys=tuple(WHOLESALE)) -> list[dict]:
    return [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": f"{filed}T20:00:00.000Z",
             "reportDate": end, "primaryDocument": "doc.htm", "items": ""}
            for a, form, filed, _, _, (_, end) in (WHOLESALE[key] for key in keys)]


RELEASE_ACCESSION, RELEASE_ACCEPTED = "0000000905-26-000084", "2026-09-24T20:17:37.000Z"
RELEASE_URL = "https://example.invalid/wholesale-ex99-1.htm"
RELEASE = table_html(
    ["Example Wholesale (SYNTHETIC)", "CONSOLIDATED STATEMENTS OF INCOME",
     "(dollars in millions, except per share data)"],
    [td() + td("16 Weeks Ended", 6) + td("52 Weeks Ended", 6),
     td() + td("August 30, 2026", 3) + td("August 31, 2025", 3) + td("August 30, 2026", 3) + td("August 31, 2025", 3)],
    [money_row("Total revenue", ("95,723", ""), ("86,156", ""), ("301,723", ""), ("272,156", "")),
     money_row("Net income", ("2,900", ""), ("2,600", ""), ("9,051.69", ""), ("8,164.68", ""))])
RELEASE_MAP = [pr.TableMap(table=0, scale="millions",
                           columns=[column(0, "2026-08-30"), column(1, "2025-08-31"), column(2, "2026-08-30", "annual"),
                                    column(3, "2025-08-31", "annual")],
                           rows=[row(0, "revenue", [95723, 86156, 301723, 272156]),
                                 row(1, "net_income", [2900, 2600, 9051.69, 8164.68])])]


def decided(calendar: pr.FiscalCalendar, html: str = RELEASE, accepted: str = RELEASE_ACCEPTED) -> list[Fact]:
    tables, cands, _ = pr._exhibit_tables(html)
    ctx = pr._context_for("EXWH", 905, "Example Wholesale", {"accessionNumber": RELEASE_ACCESSION,
                                                             "acceptanceDateTime": accepted},
                          {"url": RELEASE_URL, "type": "EX-99.1"}, cands, calendar)
    facts, _ = pr.decide_table(cands[0], RELEASE_MAP[0], ctx)
    return facts


def thirteen_week_calendar(cf: dict, stored=None) -> pr.FiscalCalendar:
    """The calendar as the mapper had it before: a quarter no filing tags started three calendar months back."""
    cal = pr.FiscalCalendar(cf, stored=stored)
    cal._quarter_start = {}
    cal._long_quarter_after = lambda end: None
    return cal


# ------------------------------------------------------------------------------------------- a 16-week Q4
def test_a_costco_shaped_16_week_fourth_quarter_takes_its_dates_and_label_from_the_calendar():
    cal = pr.FiscalCalendar(wholesale_facts())
    assert cal.period_start(d("2026-08-30"), "quarter") == d("2026-05-11")   # Q3 ended 2026-05-10: 16 weeks
    assert cal.period_start(d("2025-08-31"), "quarter") == d("2025-05-12")
    assert cal.period_start(d("2023-09-03"), "quarter") == d("2023-05-08")   # 17 weeks in the 53-week year
    quarters = {(f.metric, f.period_end): f for f in decided(cal) if (f.period_end - f.period_start).days < 200}
    q4 = quarters[("revenue", d("2026-08-30"))]
    assert (q4.period_start, q4.fiscal_year, q4.fiscal_period, q4.value) == (d("2026-05-11"), 2026, "Q4", 95_723e6)
    assert (quarters[("revenue", d("2025-08-31"))].period_start, quarters[("revenue", d("2025-08-31"))].fiscal_period) \
        == (d("2025-05-12"), "Q4")
    stored = calendar_of(label_periods([
        k(1, "2024-09-02", "2025-08-31", 2025), q(1, "2025-02-17", "2025-05-11", 2025, "Q3"),
        k(2, "2025-09-01", "2026-08-30", 2026), q(2, "2026-02-16", "2026-05-10", 2026, "Q3")], []))
    assert stored[(2026, "Q4")] == (d("2026-05-11"), d("2026-08-30"))  # what ingest stores: Q3 end + 1 .. FY end
    assert all(label_conflict(f, stored) is None for f in quarters.values())  # fix 8 passes them on the dates alone
    # The old dates, three calendar months back, contradict the same label: that is what fix 8 dropped.
    old = {(f.metric, f.period_end): f for f in decided(thirteen_week_calendar(wholesale_facts()))
           if (f.period_end - f.period_start).days < 200}
    assert old[("revenue", d("2026-08-30"))].period_start == d("2026-05-31")
    assert "Q4 FY2026 is 2026-05-11..2026-08-30" in label_conflict(old[("revenue", d("2026-08-30"))], stored)


def test_an_earnings_day_16_week_quarter_is_dated_and_labelled_like_the_same_quarter_a_year_earlier():
    """Before the FY2026 10-K: the span after Q3 is 16 weeks, as FY2025's Q4 was, so it is that quarter."""
    cal = pr.FiscalCalendar(wholesale_facts([key for key in WHOLESALE if key != "K26"]))
    assert cal.period_start(d("2026-08-30"), "quarter") == d("2026-05-11")
    assert cal.fiscal(d("2026-08-30"), "quarter") == (2026, "Q4")
    assert cal.fiscal(d("2026-08-30"), "annual") == (2026, "FY")
    assert cal.period_start(d("2026-08-30"), "annual") == d("2025-09-01")
    # A 13-week filer never gets one: 16 weeks after its last labelled end is not a quarter it has (unchanged).
    from test_press_release import CALENDAR_FACTS, companyfacts
    plain = pr.FiscalCalendar(companyfacts(*CALENDAR_FACTS))
    assert plain.fiscal(d("2026-10-20"), "quarter") == (None, None)
    assert plain.period_start(d("2026-10-20"), "quarter") == d("2026-07-21")


# ------------------------------------------------------------------------------------------- Kroger's 16-week Q1
KROGER = [k(1, "2024-02-04", "2025-02-01", 2024), k(2, "2025-02-02", "2026-01-31", 2025),
          q(1, "2025-05-25", "2025-08-16", 2025, "Q2"), q(2, "2025-08-17", "2025-11-08", 2025, "Q3"),
          q(3, "2026-05-24", "2026-08-15", 2026, "Q2")]  # the 16-week Q1 10-Qs never reach the rules


def test_krogers_16_week_first_quarter_is_labelled_from_its_fiscal_year_and_second_quarter():
    warnings: list[str] = []
    labels = {(lab.fiscal_year, lab.fiscal_period): lab for lab in label_periods(KROGER, warnings)}
    q1 = labels[(2025, "Q1")]
    assert (q1.start, q1.end, q1.source) == (d("2025-02-02"), d("2025-05-24"), LONG_Q1_SOURCE)
    # FY2026 has no 10-K yet: its first day is the day after FY2025 ends.
    assert (labels[(2026, "Q1")].start, labels[(2026, "Q1")].end) == (d("2026-02-01"), d("2026-05-23"))
    assert [w for w in warnings if gate_message(w)] == []  # labelling it drops nothing and gates no company
    # A first quarter of ordinary length is never inferred: its own 10-Q always reaches the rules.
    calendar_year = [k(1, "2024-01-01", "2024-12-31", 2024), k(2, "2025-01-01", "2025-12-31", 2025),
                     q(1, "2025-04-01", "2025-06-30", 2025, "Q2")]
    assert (2025, "Q1") not in calendar_of(label_periods(calendar_year, []))
    # Nor one a filing claims: a 10-Q tagged Q1 for the second quarter's period loses its label and keeps Q1 open.
    claimed = calendar_of(label_periods([*KROGER, q(9, "2025-05-25", "2025-08-16", 2025, "Q1")], []))
    assert (2025, "Q1") not in claimed and (2026, "Q1") in claimed


KR_FILINGS = {  # key -> (accession, form, filed, DEI fy, DEI fp, own period)
    "K24": ("0000000906-25-004267", "10-K", "2025-04-01", 2024, "FY", ("2024-02-04", "2025-02-01")),
    "Q1_25": ("0000000906-25-008960", "10-Q", "2025-06-27", 2025, "Q1", ("2025-02-02", "2025-05-24")),
    "Q2_25": ("0000000906-25-091648", "10-Q", "2025-09-19", 2025, "Q2", ("2025-05-25", "2025-08-16")),
    "Q3_25": ("0000000906-25-120642", "10-Q", "2025-12-12", 2025, "Q3", ("2025-08-17", "2025-11-08")),
    "K25": ("0000000906-26-037723", "10-K", "2026-04-01", 2025, "FY", ("2025-02-02", "2026-01-31")),
    "Q1_26": ("0000000906-26-078236", "10-Q", "2026-06-26", 2026, "Q1", ("2026-02-01", "2026-05-23")),
    "Q2_26": ("0000000906-26-108926", "10-Q", "2026-09-18", 2026, "Q2", ("2026-05-24", "2026-08-15")),
}
KR_REVENUE = {"K24": 147_000e6, "Q1_25": 45_118e6, "Q2_25": 33_900e6, "Q3_25": 33_800e6, "K25": 147_600e6,
              "Q1_26": 46_121e6, "Q2_26": 34_500e6}


def kroger_store(tmp_path):
    usgaap: dict = {"Revenues": {"units": {"USD": []}}}
    for key, (accn, form, filed, fy, fp, (start, end)) in KR_FILINGS.items():
        usgaap["Revenues"]["units"]["USD"].append({"start": start, "end": end, "val": KR_REVENUE[key], "accn": accn,
                                                   "form": form, "filed": filed, "fy": fy, "fp": fp})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": f"{filed}T20:00:00.000Z",
             "reportDate": end, "primaryDocument": "doc.htm", "items": ""}
            for a, form, filed, _, _, (_, end) in KR_FILINGS.values()]
    cf = {"facts": {"us-gaap": usgaap}}
    b = build_company(cik=906, ticker="EXGR", names=["Example Grocer"], fiscal_year_end="0131", filing_rows=rows,
                      companyfacts=cf, facts_start=date(2024, 2, 1), today=date(2026, 10, 1))
    db = tmp_path / "facts.sqlite"
    conn = connect_rw(db)
    replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    return db, b, cf


KR_URL, KR_ACCESSION, KR_ACCEPTED = "https://example.invalid/grocer-ex99-1.htm", "0000000906-26-075395", \
    "2026-06-18T10:52:56.000Z"
KR_RELEASE = table_html(["Example Grocer (SYNTHETIC)", "CONSOLIDATED STATEMENTS OF OPERATIONS", "(in millions)"],
                        [td() + td("FIRST QUARTER", 6), td() + td("May 23, 2026", 3) + td("May 24, 2025", 3)],
                        [money_row("SALES", ("46,121", ""), ("45,118", "")),
                         money_row("Gross profit", ("10,473", ""), ("10,356", ""))])
KR_MAP = [pr.TableMap(table=0, scale="millions", columns=[column(0, "2026-05-23"), column(1, "2025-05-24")],
                      rows=[row(0, "revenue", [46121, 45118]), row(1, "gross_profit", [10473, 10356])])]


def test_krogers_first_quarter_through_ingest_and_the_release_calendar(tmp_path):
    db, b, cf = kroger_store(tmp_path)
    periods = {(fy, fp): (start, end, src) for fy, fp, start, end, src in b.periods}
    assert periods[(2025, "Q1")] == (d("2025-02-02"), d("2025-05-24"), LONG_Q1_SOURCE)
    assert periods[(2026, "Q1")] == (d("2026-02-01"), d("2026-05-23"), LONG_Q1_SOURCE)
    stored = [(fy, fp, start, end) for fy, fp, start, end, _ in b.periods]
    cal = pr.FiscalCalendar(cf, stored=stored)
    assert cal.period_start(d("2026-05-23"), "quarter") == d("2026-02-01")
    assert cal.fiscal(d("2026-05-23"), "quarter") == (2026, "Q1")
    # The mapping prompt's calendar lines stay what they were, so every cached request is still a cache hit.
    assert not any("Q1" in line for line in cal.context_lines(None))
    tables, cands, _ = pr._exhibit_tables(KR_RELEASE)
    ctx = pr._context_for("EXGR", 906, "Example Grocer", {"accessionNumber": KR_ACCESSION,
                                                           "acceptanceDateTime": KR_ACCEPTED},
                          {"url": KR_URL, "type": "EX-99.1"}, cands, cal)
    facts, _ = pr.decide_table(cands[0], KR_MAP[0], ctx)
    sales = {f.period_end: f for f in facts if f.metric == "revenue"}
    assert (sales[d("2026-05-23")].period_start, sales[d("2026-05-23")].fiscal_period) == (d("2026-02-01"), "Q1")
    assert (sales[d("2025-05-24")].period_start, sales[d("2025-05-24")].fiscal_year) == (d("2025-02-02"), 2025)
    store_cal = {(fy, fp): (start, end) for fy, fp, start, end in stored}
    assert all(label_conflict(f, store_cal) is None for f in facts)


# ------------------------------------------------------------------------------------------- fix 8 still drops
def test_a_label_its_dates_contradict_is_still_dropped():
    cal = calendar_of(label_periods([*KROGER, k(3, "2023-01-29", "2024-02-03", 2023),
                                     q(4, "2026-02-16", "2026-05-10", 2026, "Q3")], []))
    cal[(2026, "Q4")] = (d("2026-05-11"), d("2026-08-30"))

    def release(start, end, label, value=1e9):
        return Fact(cik=906, ticker="EXGR", metric="revenue", basis="GAAP", value=value, unit="USD",
                    period_start=d(start), period_end=d(end), fiscal_year=label[0], fiscal_period=label[1],
                    source="press_release", form="8-K", accession="0000000906-26-000009",
                    accepted_at=datetime(2026, 9, 24, 20, tzinfo=UTC), url="https://example.invalid/ex99.htm",
                    locator=f"Total revenue | {value / 1e6:,.0f}", decimals=-6)

    right_q1, wrong_q1 = release("2026-02-01", "2026-05-23", (2026, "Q1")), release("2026-02-24", "2026-05-23",
                                                                                    (2026, "Q1"), 2e9)
    right_q4, wrong_q4 = release("2026-05-11", "2026-08-30", (2026, "Q4"), 3e9), release("2026-05-31", "2026-08-30",
                                                                                         (2026, "Q4"), 4e9)
    assert label_conflict(right_q1, cal) is None and label_conflict(right_q4, cal) is None
    assert "Q1 FY2026 is 2026-02-01..2026-05-23" in label_conflict(wrong_q1, cal)
    assert "Q4 FY2026 is 2026-05-11..2026-08-30" in label_conflict(wrong_q4, cal)
    drop, records, _ = gate_all({"f.jsonl": [right_q1, wrong_q1, right_q4, wrong_q4]}, {}, cal, {}, None)
    assert drop == {"f.jsonl": [1, 3]} and {r["rule"] for r in records} == {"label_dates"}


def test_a_labelled_16_week_quarter_is_served_so_fix7_checks_it_against_same_period_xbrl():
    def release(start, end, label, value):
        return Fact(cik=905, ticker="EXWH", metric="revenue", basis="GAAP", value=value, unit="USD",
                    period_start=d(start), period_end=d(end), fiscal_year=label[0], fiscal_period=label[1],
                    source="press_release", form="8-K", accession=RELEASE_ACCESSION,
                    accepted_at=datetime(2026, 9, 24, 20, tzinfo=UTC), url=RELEASE_URL,
                    locator=f"Total revenue | {value / 1e6:,.0f}", decimals=-6)

    long_q4 = release("2026-05-11", "2026-08-30", (2026, "Q4"), 95_000e6)
    assert served(long_q4) and pr.is_served(long_q4)
    assert not served(release("2026-05-11", "2026-08-30", (None, None), 95_000e6))  # unlabelled: a span, not a quarter
    reported = {("revenue", "2026-05-11", "2026-08-30"): [XbrlValue(
        value=95_723e6, decimals=-6, source="xbrl", accession="0000000905-26-000093",
        accepted_at=datetime(2026, 10, 7, 20, tzinfo=UTC), concept="Revenues")]}
    cal = {(2026, "Q4"): (d("2026-05-11"), d("2026-08-30"))}
    drop, records, _ = gate_all({"f.jsonl": [long_q4]}, reported, cal, {}, None)
    assert drop == {"f.jsonl": [0]} and records[0]["rule"] == "xbrl_reported"


# ------------------------------------------------------------------------------------------- --redate
def wholesale_store(tmp_path, nci: bool = True):
    cf = wholesale_facts(nci=nci)
    b = build_company(cik=905, ticker="EXWH", names=["Example Wholesale"], fiscal_year_end="0830",
                      filing_rows=wholesale_rows(), companyfacts=cf, facts_start=date(2024, 9, 1),
                      today=date(2026, 10, 15))
    db = tmp_path / "facts.sqlite"
    conn = connect_rw(db)
    replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    return db, b, cf


def map_release(tmp_path, ticker, cik, company, html, url, accession, accepted, table_maps, calendar):
    """Map a release the way receipts.press_release.main does, with the model's answer already in the cache."""
    tables, cands, skipped = pr._exhibit_tables(html)
    eightk = {"accessionNumber": accession, "acceptanceDateTime": accepted}
    job = pr.FilingJob(ticker, cik, company, eightk, {"accessionNumber": "q", "form": "10-K"}, tables=tables,
                       candidates=cands, skipped=skipped,
                       ctx=pr._context_for(ticker, cik, company, eightk, {"url": url, "type": "EX-99.1"}, cands,
                                           calendar))
    pr.plan_requests(job, effort="minimal")
    spark = tmp_path / "spark"
    spark.mkdir(exist_ok=True)
    for request, _ in job.requests:
        (spark / f"{pr.request_hash(request)}.json").write_text(
            json.dumps({"output_text": pr.ExhibitMap(tables=table_maps).model_dump_json()}))
    pr.run_model([job], pr.SparkMapper(offline=True, cache_dir=spark))
    pr.decide_job(job)
    return pr.write_job(job, out_dir=tmp_path / "pr")


def test_redate_restores_what_the_label_gate_dropped_for_13_week_dates_and_nothing_else(tmp_path):
    db, b, cf = wholesale_store(tmp_path)
    stored = [(fy, fp, s, e) for fy, fp, s, e, _ in b.periods]
    path = map_release(tmp_path, "EXWH", 905, "Example Wholesale", RELEASE, RELEASE_URL, RELEASE_ACCESSION,
                       RELEASE_ACCEPTED, RELEASE_MAP, thirteen_week_calendar(cf, stored))
    assert {f.period_start for f in pr.load_facts(path) if f.fiscal_period == "Q4"} == {d("2026-05-31"),
                                                                                        d("2025-06-01")}
    gates = dict(load_companyfacts=lambda cik: cf, rejections_path=tmp_path / "derived_rejections.json",
                 exhibit_tables=lambda url: pr.parse_tables(RELEASE))
    first = gate_run(tmp_path / "pr", db, write=True, **gates)
    assert sorted((r["rule"], r["metric"]) for r in first["dropped"]) == [
        ("label_dates", "revenue"), ("label_dates", "revenue"), ("nci_net_income", "net_income"),
        ("nci_net_income", "net_income")]
    kept = path.read_text().splitlines()
    sources = dict(exhibit_html=lambda url: RELEASE.encode() if url == RELEASE_URL else None,
                   maps=pr.stored_map(tmp_path / "spark"), companyfacts=lambda cik: cf)
    dry = redate(tmp_path / "pr", db, ["EXWH"], write=False, **sources)
    assert path.read_text().splitlines() == kept and dry["restored"] == 2  # a dry run writes nothing
    result = redate(tmp_path / "pr", db, ["EXWH"], write=True, **sources)
    (rec,) = result["files"]
    assert all(c["same_request"] for c in rec["replay"])  # today's request is the recorded one, byte for byte
    after = path.read_text().splitlines()
    assert after[:len(kept)] == kept  # every live line stays byte for byte; the restored facts follow them
    restored = [Fact.model_validate_json(line) for line in after[len(kept):]]
    assert sorted((f.metric, f.period_start, f.period_end, f.fiscal_year, f.fiscal_period, f.value) for f in restored) \
        == [("revenue", d("2025-05-12"), d("2025-08-31"), 2025, "Q4", 86_156e6),
            ("revenue", d("2026-05-11"), d("2026-08-30"), 2026, "Q4", 95_723e6)]
    assert {f.locator for f in restored} == {"Total revenue | 95,723", "Total revenue | 86,156"}
    # The net income quarters went to another rule (noncontrolling interest): never touched.
    assert not any(f.metric == "net_income" and f.fiscal_period == "Q4" for f in pr.load_facts(path))
    report = json.loads(path.with_name(f"{RELEASE_ACCESSION}.report.json").read_text())
    assert [r["period"] for r in report["redate"][0]["restored"]] == ["2026-05-11..2026-08-30",
                                                                      "2025-05-12..2025-08-31"]
    # Every release gate passes the restored facts (fix 7 against the derived Q4 among them), and nothing repeats.
    assert gate_run(tmp_path / "pr", db, write=False, **gates)["dropped"] == []
    assert redate(tmp_path / "pr", db, ["EXWH"], write=False, **sources)["restored"] == 0


def test_redate_moves_a_13_week_start_to_the_calendars_and_the_store_labels_it(tmp_path):
    """Kroger's case: its Q1 release facts were never dropped, only dated from three months back and unlabelled."""
    db, b, cf = kroger_store(tmp_path)
    stored = [(fy, fp, s, e) for fy, fp, s, e, _ in b.periods]
    path = map_release(tmp_path, "EXGR", 906, "Example Grocer", KR_RELEASE, KR_URL, KR_ACCESSION, KR_ACCEPTED, KR_MAP,
                       thirteen_week_calendar(cf, stored))
    lines = [Fact.model_validate_json(line).model_copy(update={"fiscal_year": None, "fiscal_period": None})
             for line in path.read_text().splitlines()]
    assert {f.period_start for f in lines} == {d("2026-02-24"), d("2025-02-25")}
    path.write_text("".join(f.model_dump_json() + "\n" for f in lines))
    result = redate(tmp_path / "pr", db, ["EXGR"], write=True,
                    exhibit_html=lambda url: KR_RELEASE.encode() if url == KR_URL else None,
                    maps=pr.stored_map(tmp_path / "spark"), companyfacts=lambda cik: cf)
    assert result["redated"] == 4 and result["restored"] == 0
    after = pr.load_facts(path)
    assert sorted({(f.period_start, f.period_end, f.fiscal_year, f.fiscal_period) for f in after}) == [
        (d("2025-02-02"), d("2025-05-24"), 2025, "Q1"), (d("2026-02-01"), d("2026-05-23"), 2026, "Q1")]
    assert [(f.metric, f.value, f.locator) for f in after] == [(f.metric, f.value, f.locator) for f in lines]
    gates = dict(load_companyfacts=lambda cik: cf, rejections_path=tmp_path / "derived_rejections.json",
                 exhibit_tables=lambda url: pr.parse_tables(KR_RELEASE))
    assert gate_run(tmp_path / "pr", db, write=False, **gates)["dropped"] == []


def test_the_store_serves_and_the_verifier_reads_a_labelled_16_week_quarter_as_its_quarter():
    """Kroger's 16-week Q1 and Costco's 16-week Q4: the store keeps a labelled long quarter (as is_served does), and the
    verifier reads it only as the claim's own quarter; an unlabelled span that long stays out."""
    from types import SimpleNamespace

    from receipts.store import _duration_ok
    from receipts.verify import _long_quarter

    def release(start, end, label):
        return Fact(cik=905, ticker="EXWH", metric="revenue", basis="GAAP", value=40_000e6, unit="USD",
                    period_start=d(start), period_end=d(end), fiscal_year=label[0], fiscal_period=label[1],
                    source="press_release", form="8-K", accession=RELEASE_ACCESSION,
                    accepted_at=datetime(2026, 6, 20, 20, tzinfo=UTC), url=RELEASE_URL,
                    locator="Total revenue | 40,000", decimals=-6)

    long_q1 = release("2026-02-01", "2026-05-23", (2026, "Q1"))
    long_q4 = release("2026-05-11", "2026-08-30", (2026, "Q4"))
    assert _duration_ok(long_q1) and _duration_ok(long_q4) and pr.is_served(long_q1)
    assert not _duration_ok(release("2026-02-01", "2026-05-23", (None, None)))
    assert _long_quarter(long_q1, SimpleNamespace(fiscal_period="Q1"), d("2026-02-01"))
    assert _long_quarter(long_q4, SimpleNamespace(fiscal_period="Q4"), d("2026-05-11"))
    assert not _long_quarter(long_q1, SimpleNamespace(fiscal_period="Q4"), d("2026-02-01"))  # never another quarter's figure
    assert not _long_quarter(long_q1, SimpleNamespace(fiscal_period="Q1"), d("2026-03-01"))  # the calendar dates Q1 otherwise
    assert not _long_quarter(long_q1, SimpleNamespace(fiscal_period="Q1"), None)  # no calendar start: no long quarter
