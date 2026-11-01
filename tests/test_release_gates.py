"""receipts.release_gates and scripts/gate_release_facts.py: release figures with nothing to pair them with (review
fixes 5 and 6, QA T2 and T5). SYNTHETIC filings and releases for fictional companies."""
import json
from datetime import date, datetime, timezone

from receipts.ingest import build_company
from receipts.release_gates import attributable_net_income, gate, noncontrolling_interest
from receipts.store import connect_rw, replace_company
from receipts.types import Fact
from scripts.gate_release_facts import run

Q2 = ("2026-04-01", "2026-06-30")


def entry(start, end, val, accn="0000000904-26-000002", form="10-Q", filed="2026-08-05"):
    return {"start": start, "end": end, "val": val, "accn": accn, "form": form, "filed": filed, "fy": 2026, "fp": "Q2"}


def usgaap(**concepts):
    return {c: {"units": {"USD": rows}} for c, rows in concepts.items()}


def release(metric, value, basis="GAAP", segment=None, period=Q2, decimals=-6, locator="row | 1"):
    return Fact(cik=904, ticker="EXMP", metric=metric, basis=basis, segment=segment, value=value, unit="USD",
                period_start=date.fromisoformat(period[0]), period_end=date.fromisoformat(period[1]),
                source="press_release", form="8-K", accession="0000000904-26-000009",
                accepted_at=datetime(2026, 8, 4, 20, tzinfo=timezone.utc), url="https://example.invalid/ex99.htm",
                locator=locator, decimals=decimals)


def test_a_noncontrolling_interest_shows_in_its_own_tag_or_as_profitloss_above_net_income():
    nci = usgaap(NetIncomeLossAttributableToNoncontrollingInterest=[entry(*Q2, 142e6)])
    assert "142,000,000" in noncontrolling_interest(nci)
    differ = usgaap(ProfitLoss=[entry(*Q2, 869e6)], NetIncomeLoss=[entry(*Q2, 727e6)])
    assert "ProfitLoss 869,000,000 against NetIncomeLoss 727,000,000" in noncontrolling_interest(differ)
    none = usgaap(ProfitLoss=[entry(*Q2, 727e6)], NetIncomeLoss=[entry(*Q2, 727e6)],
                  NetIncomeLossAttributableToNoncontrollingInterest=[entry(*Q2, 0)])
    assert noncontrolling_interest(none) is None
    proxy = usgaap(NetIncomeLossAttributableToNoncontrollingInterest=[entry(*Q2, 5e6, form="DEF 14A")])
    assert noncontrolling_interest(proxy) is None  # only 10-Q and 10-K periods count


def test_cf_shape_release_net_income_with_the_noncontrolling_interest_is_dropped():
    """CF's Q2 2026 release leads with "Net earnings 869" (NCI 142 included); its XBRL tags no NetIncomeLoss, so the
    store has nothing to check the 869 against."""
    cf = usgaap(ProfitLoss=[entry(*Q2, 869e6)], NetIncomeLossAttributableToNoncontrollingInterest=[entry(*Q2, 142e6)],
                NetIncomeLossAvailableToCommonStockholdersBasic=[entry(*Q2, 727e6)])
    facts = {"r.jsonl": [release("net_income", 869e6, locator="Net earnings | 869"), release("revenue", 2222e6)]}
    drop, records = gate(facts, {}, noncontrolling_interest(cf), attributable_net_income(cf))
    assert drop == {"r.jsonl": [0]} and records[0]["rule"] == "nci_net_income"
    # the attributable figure itself is checked by companyfacts, and an XBRL net income in the store pairs any figure
    assert gate({"r.jsonl": [release("net_income", 727e6)]}, {}, noncontrolling_interest(cf),
                attributable_net_income(cf))[0] == {}
    paired = {("net_income", *Q2, None): [(869e6, -6)]}
    assert gate(facts, paired, noncontrolling_interest(cf), attributable_net_income(cf))[0] == {}
    assert gate(facts, {}, None)[0] == {}  # no noncontrolling interest anywhere: the release figure stands
    # APA: only the redeemable NCI is tagged (as zero), and a later 10-Q shows nonredeemable NCI for the quarter
    apa = usgaap(ProfitLoss=[entry(*Q2, 869e6)],
                 NetIncomeLossAttributableToRedeemableNoncontrollingInterest=[entry(*Q2, 0)],
                 NetIncomeLossAttributableToNonredeemableNoncontrollingInterest=[
                     entry(*Q2, 142e6, accn="0000000904-27-000002", filed="2027-08-05")])
    assert attributable_net_income(apa) == {}
    assert attributable_net_income(cf)[Q2] == [727e6, 727e6]   # the tagged figure, and ProfitLoss minus the NCI


def test_a_row_whose_label_names_a_gaap_measure_is_dropped_from_non_gaap_and_true_non_gaap_rows_stay():
    """Dollar Tree's "Operating income (GAAP) | 690.1" heads a reconciliation table and was stored as non-GAAP, and
    O'Reilly's trailing "GAAP net income" has no GAAP figure to equal; both go. NVIDIA's non-GAAP diluted EPS equals
    its GAAP EPS ($1.30) but its label does not say GAAP, so it stays, as do Fortinet's "Non‐GAAP" (U+2010), free cash
    flow, and a segment figure stored under both bases."""
    facts = {"r.jsonl": [release("operating_income", 690.1e6, basis="non-GAAP", decimals=-5,
                                 locator="Operating income (GAAP) | 690.1"),
                         release("net_income", 2484.1e6, basis="non-GAAP", period=("2024-10-01", "2025-09-30"),
                                 locator="GAAP net income | 2,484,106"),
                         release("eps_diluted", 1.30, basis="non-GAAP", decimals=2,
                                 locator="Diluted earnings per share | $1.30"),
                         release("operating_income", 709.9e6, basis="non-GAAP",
                                 locator="Non\u2010GAAP operating income | 709.9"),
                         release("free_cash_flow", 400e6, basis="non-GAAP",
                                 locator="Free cash flow (GAAP operating cash flow less capital expenditures) | 400"),
                         release("segment_revenue", 1162e6, basis="non-GAAP", segment="Essential Health",
                                 locator="Essential Health | 1,162"),
                         release("operating_income", 236e6, basis="non-GAAP",
                                 locator="Adjusted operating income (GAAP basis) | 236")]}
    drop, records = gate(facts, {("eps_diluted", *Q2, None): [(1.30, 2)]}, None)
    assert drop == {"r.jsonl": [0, 1]} and {r["rule"] for r in records} == {"gaap_row_as_non_gaap"}


def test_the_script_rewrites_only_files_that_lose_a_fact_and_records_each_drop(tmp_path):
    rows = [{"accessionNumber": "0000000904-26-000002", "form": "10-Q", "filingDate": "2026-08-05",
             "acceptanceDateTime": "2026-08-05T20:00:00.000Z", "reportDate": "2026-06-30", "primaryDocument": "q.htm",
             "items": ""}]
    cf = {"facts": {"us-gaap": usgaap(Revenues=[entry(*Q2, 2222e6)], ProfitLoss=[entry(*Q2, 869e6)],
                                      NetIncomeLossAttributableToNoncontrollingInterest=[entry(*Q2, 142e6)])}}
    b = build_company(cik=904, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                      companyfacts=cf, facts_start=date(2026, 1, 1), today=date(2026, 10, 1))
    db = tmp_path / "facts.sqlite"
    conn = connect_rw(db)
    replace_company(conn, b.company, b.filings, b.periods, b.facts)
    conn.close()
    folder = tmp_path / "pr" / "EXMP"
    folder.mkdir(parents=True)
    lose = folder / "0000000904-26-000009.jsonl"
    keep = folder / "0000000904-26-000010.jsonl"
    lose.write_text("".join(f.model_dump_json() + "\n" for f in
                            [release("revenue", 2222e6), release("net_income", 869e6, locator="Net earnings | 869")]))
    keep.write_text(release("revenue", 2222e6).model_dump_json() + "\n")
    (folder / "0000000904-26-000009.report.json").write_text(json.dumps({"accession": "0000000904-26-000009"}))
    before_keep, first_line = keep.read_bytes(), lose.read_text().splitlines()[0]
    assert run(tmp_path / "pr", db, write=False, load_companyfacts=lambda cik: cf)["files_changed"] == 0
    result = run(tmp_path / "pr", db, write=True, load_companyfacts=lambda cik: cf)
    # the filing tags ProfitLoss and its NCI, so the store serves net income of $727M (derived), and the release's
    # NCI-inclusive 869 goes against it (fix 7) rather than for lack of XBRL (fix 5)
    assert [r["rule"] for r in result["dropped"]] == ["xbrl_derived"] and result["files_changed"] == 1
    assert "noncontrolling" in result["dropped"][0]["why"]
    assert lose.read_text().splitlines() == [first_line] and keep.read_bytes() == before_keep
    report = json.loads((folder / "0000000904-26-000009.report.json").read_text())
    assert report["release_gates"][0]["value"] == 869e6


# --- Review fixes 7-9 (QA round 2) -------------------------------------------------------------------------------
from receipts.release_gates import XbrlValue, gate_all, label_conflict  # noqa: E402

UTC = timezone.utc


def xv(value, source="xbrl", accepted=datetime(2026, 8, 5, 20, tzinfo=UTC), decimals=-6, concept="Revenues",
       accession="0000000904-26-000002", terms=1):
    return XbrlValue(value=value, decimals=decimals, source=source, accession=accession, accepted_at=accepted,
                     concept=concept, terms=terms if source == "derived" else 1)


def fact(metric, value, period, accepted=datetime(2026, 1, 20, 21, tzinfo=UTC), label=None, decimals=-6,
         locator="Total revenue | 1", basis="GAAP", unit="USD", segment=None, accession="0000000904-26-000009"):
    fy, fp = label if label else (None, None)
    return Fact(cik=904, ticker="EXMP", metric=metric, basis=basis, segment=segment, value=value, unit=unit,
                period_start=date.fromisoformat(period[0]), period_end=date.fromisoformat(period[1]),
                fiscal_year=fy, fiscal_period=fp, source="press_release", form="8-K",
                accession=accession, accepted_at=accepted, url="https://example.invalid/ex99.htm",
                locator=locator, decimals=decimals)


Q3_25, Q4_25, Q1_25 = ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"), ("2025-01-01", "2025-03-31")


def test_fix7_a_release_figure_that_reported_xbrl_contradicts_goes_unless_a_filing_documents_it():
    """Dow's Q2 2026 release stored the 2Q26 diluted EPS ($0.99) as Q2 2025's; the Q2 2025 10-Q says -$1.18. A figure a
    10-Q itself once reported for the period is a documented restatement and stays; agreement within rounding stands."""
    q2 = ("2025-04-01", "2025-06-30")
    swapped = fact("eps_diluted", 0.99, q2, unit="USD/share", decimals=2, locator="Diluted | 0.99")
    xbrl = {("eps_diluted", *q2): [xv(-1.18, decimals=2, concept="EarningsPerShareDiluted")]}
    drop, records, rejected = gate_all({"r.jsonl": [swapped]}, xbrl, {}, {}, None)
    assert drop == {"r.jsonl": [0]} and records[0]["rule"] == "xbrl_reported" and not rejected
    original = usgaap()
    original["EarningsPerShareDiluted"] = {"units": {"USD/shares": [
        {"start": q2[0], "end": q2[1], "val": 0.99, "accn": "0000000904-25-000003", "form": "10-Q", "filed": "2025-08-01"}]}}
    assert gate_all({"r.jsonl": [swapped]}, xbrl, {}, original, None)[0] == {}
    rounded = fact("revenue", 2222.4e6, q2, decimals=-5)
    assert gate_all({"r.jsonl": [rounded]}, {("revenue", *q2): [xv(2222e6)]}, {}, {}, None)[0] == {}


def bac_shape(q4_accepted=datetime(2026, 1, 14, 11, tzinfo=UTC), q3_release=28088e6, nci=None):
    """Bank of America: Q3 2025 revenue agrees with its 10-Q, while Q4 2025 exists only as the 10-K's restated FY
    minus the Q3 10-Q's original 9M ($31,180M); the January release reports $28,367M."""
    facts = {"r.jsonl": [fact("revenue", q3_release, Q3_25, accepted=q4_accepted),
                         fact("revenue", 28367e6, Q4_25, accepted=q4_accepted)]}
    xbrl = {("revenue", *Q3_25): [xv(28088e6, accepted=datetime(2025, 10, 31, 20, tzinfo=UTC))],
            ("revenue", *Q4_25): [xv(31180e6, source="derived", terms=2, accession="0000000904-26-000005",
                                     accepted=datetime(2026, 2, 25, 21, tzinfo=UTC))]}
    return gate_all(facts, xbrl, {}, {}, nci)


def test_fix7_the_companys_own_release_outranks_a_derived_quarter_on_two_bases():
    drop, records, rejected = bac_shape()
    assert drop == {} and len(rejected) == 1
    r = rejected[0]
    assert (r["metric"], r["period_start"], r["value"]) == ("revenue", "2025-10-01", 31180e6)
    assert r["release"]["value"] == 28367e6 and r["track_record"] == {"agree": 1, "excused": 0, "disagree": 0}


def test_fix7_otherwise_the_release_figure_goes_and_the_derived_value_stays():
    # a year-later comparative may be on a new basis: it cannot outrank the filing of its time
    drop, records, rejected = bac_shape(q4_accepted=datetime(2027, 1, 20, 21, tzinfo=UTC))
    assert drop == {"r.jsonl": [1]} and not rejected and "days after" in records[0]["why"]
    # a row that disagrees with reported XBRL elsewhere (Arch's "Net premiums earned" as revenue) is not trusted
    drop, records, rejected = bac_shape(q3_release=27000e6)
    assert drop == {"r.jsonl": [0, 1]} and not rejected and {r["rule"] for r in records} == {"xbrl_reported", "xbrl_derived"}


def test_fix7_consolidated_net_income_and_a_cost_printed_in_parentheses_never_outrank_xbrl():
    nets = usgaap(ProfitLoss=[entry("2025-01-01", "2025-12-31", 2000e6, form="10-K", filed="2026-02-20"),
                              entry("2025-01-01", "2025-09-30", 1400e6, filed="2025-10-30")])
    facts = {"r.jsonl": [fact("net_income", 600e6, Q4_25, locator="Net earnings | 600"),
                         fact("cost_of_revenue", -1316.4e6, Q4_25, decimals=-5, locator="Cost of revenues | (1,316.4)")]}
    xbrl = {("net_income", *Q4_25): [xv(560e6, "derived", datetime(2026, 2, 20, tzinfo=UTC), terms=2)],
            ("cost_of_revenue", *Q4_25): [xv(1316.5e6, "derived", datetime(2026, 2, 20, tzinfo=UTC), decimals=-5, terms=2)]}
    drop, records, rejected = gate_all(facts, xbrl, {}, nets, "NetIncomeLossAttributableToNoncontrollingInterest 40")
    assert drop == {"r.jsonl": [0, 1]} and not rejected
    assert "noncontrolling" in records[0]["why"] and "sign" in records[1]["why"]


def test_fix7_a_file_on_the_restated_basis_keeps_its_prior_columns():
    """Bank of America's April 2026 release shows Q1 2025 at the restated $28,247M a later 10-Q reports and Q3 2025 at
    $29,040M, which no filing reports yet (the Q3 10-Q's original is $28,088M): the file is on the new basis."""
    facts = {"r.jsonl": [fact("revenue", 28247e6, Q1_25), fact("revenue", 29040e6, Q3_25)]}
    xbrl = {("revenue", *Q1_25): [xv(27366e6, accepted=datetime(2025, 4, 30, tzinfo=UTC)),
                                  xv(28247e6, accepted=datetime(2026, 5, 1, tzinfo=UTC))],
            ("revenue", *Q3_25): [xv(28088e6, accepted=datetime(2025, 10, 31, tzinfo=UTC))]}
    assert gate_all(facts, xbrl, {}, {}, None)[0] == {}
    facts["r.jsonl"][0] = fact("revenue", 27366e6, Q1_25)  # the original basis: Q3's figure is unexplained
    assert gate_all(facts, xbrl, {}, {}, None)[0] == {"r.jsonl": [1]}


def test_fix16_a_cell_of_another_period_read_as_q4_never_outranks_the_derived_quarter():
    """Review M3: a twelve-month cell read as Q4 ($1,000M, the year) is no x1000 or sign slip, and the row's clean
    record in Q1-Q3 does not vouch for it. The derived Q4 (FY $1,000M - 9M $750M) stays and the release figure goes;
    so does a Q4 the year's own figures rule out, or one that repeats the release's prior-year quarter."""
    q2, fy, q4_24 = ("2025-04-01", "2025-06-30"), ("2025-01-01", "2025-12-31"), ("2024-10-01", "2024-12-31")
    k_at, release_at = datetime(2026, 2, 20, 21, tzinfo=UTC), datetime(2026, 2, 10, 21, tzinfo=UTC)
    xbrl = {("revenue", *Q1_25): [xv(240e6)], ("revenue", *q2): [xv(250e6)], ("revenue", *Q3_25): [xv(260e6)],
            ("revenue", *fy): [xv(1000e6, accepted=k_at, accession="0000000904-26-000005")],
            ("revenue", *Q4_25): [xv(250e6, "derived", k_at, terms=2, accession="0000000904-26-000005")]}
    quarters = {f"q{n}.jsonl": [fact("revenue", v, per, accession=f"0000000904-25-00000{n}")]
                for n, (v, per) in enumerate(((240e6, Q1_25), (250e6, q2), (260e6, Q3_25)), 1)}

    def q4_release(*rows):
        return gate_all({**quarters, "q4.jsonl": list(rows)}, xbrl, {}, {}, None)

    drop, records, rejected = q4_release(fact("revenue", 1000e6, Q4_25, release_at),
                                         fact("revenue", 1000e6, fy, release_at))
    assert drop == {"q4.jsonl": [0]} and not rejected and records[0]["rule"] == "xbrl_derived"
    assert "the release gives the same figure for 2025-01-01..2025-12-31" in records[0]["why"]
    drop, records, rejected = q4_release(fact("revenue", 900e6, Q4_25, release_at))
    assert drop == {"q4.jsonl": [0]} and not rejected and "90% of the fiscal year's reported" in records[0]["why"]
    drop, records, rejected = q4_release(fact("revenue", 230e6, Q4_25, release_at),
                                         fact("revenue", 230e6, q4_24, release_at))
    assert drop == {"q4.jsonl": [0]} and not rejected and "same figure for 2024-10-01..2024-12-31" in records[0]["why"]
    drop, records, rejected = q4_release(fact("revenue", 245e6, Q4_25, release_at))   # a plausible quarter: trusted
    assert drop == {} and [r["value"] for r in rejected] == [250e6]


def test_fix8_a_label_its_dates_contradict_and_a_trailing_twelve_month_figure_go():
    cal = {(2025, "FY"): (date(2025, 1, 1), date(2025, 12, 31)), (2025, "Q1"): (date(2025, 1, 1), date(2025, 3, 31)),
           (2025, "Q4"): (date(2025, 9, 8), date(2025, 12, 28))}
    ltm_as_fy = fact("operating_cash_flow", 10307e6, ("2024-04-01", "2025-03-31"), label=(2025, "FY"))
    assert "FY2025 is 2025-01-01..2025-12-31" in label_conflict(ltm_as_fy, cal)
    thirteen_weeks = fact("revenue", 1535.7e6, ("2025-09-29", "2025-12-28"), label=(2025, "Q4"))  # store: 16 weeks
    assert label_conflict(thirteen_weeks, cal)
    assert "trailing-twelve-month" in label_conflict(fact("free_cash_flow", 9649e6, ("2024-07-01", "2025-06-30")), cal)
    assert label_conflict(fact("revenue", 4e9, ("2025-01-01", "2025-12-31")), cal) is None  # ends at the FY end
    assert label_conflict(fact("revenue", 1e9, ("2026-01-01", "2026-03-31"), label=(2026, "Q1")), cal) is None
    assert label_conflict(fact("revenue", 1e9, ("2027-07-01", "2027-09-30"), label=(2027, "Q3")), cal) is None


def test_fix9_a_x1000_slip_goes_and_a_small_figure_at_the_rows_precision_stays():
    """Block's 2024 free cash flow columns were read in thousands ($457,000) beside the same row in millions; another
    release has $457M for the same quarter. HPE's Routing segment really earned $1M in a quarter."""
    q1_24, q1_25, q2_25 = ("2024-01-01", "2024-03-31"), ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30")
    slip = fact("free_cash_flow", 457e3, q1_24, decimals=-3, basis="non-GAAP", locator="Free Cash Flow | 457")
    good = fact("free_cash_flow", 457e6, q1_24, basis="non-GAAP", locator="Free Cash Flow | 457")
    rows = [fact("free_cash_flow", v, p, basis="non-GAAP", locator="Free Cash Flow | 1") for v, p in
            ((101e6, q1_25), (343e6, q2_25))]
    drop, records, _ = gate_all({"a.jsonl": [slip, *rows], "b.jsonl": [good]}, {}, {}, {}, None)
    assert drop == {"a.jsonl": [0]} and records[0]["rule"] == "scale_slip"
    tiny = fact("segment_revenue", 1e6, q1_25, segment="Routing", locator="Routing | 1")
    peers = [fact("segment_revenue", 780e6, p, segment="Routing", locator="Routing | 780") for p in (q2_25, q1_24)]
    assert gate_all({"a.jsonl": [tiny, *peers]}, {}, {}, {}, None)[0] == {}


def test_fix11_a_gaap_line_stored_as_the_periods_non_gaap_figure_goes_and_real_equal_values_stay():
    """Schwab's "Net Income" sits under the section "Expenses Excluding Interest"; Vulcan's reconciliation headed
    "Adjusted Diluted EPS" starts from GAAP EPS; GE HealthCare's income statement sits under a footnote parsed as a
    heading. Each stored a GAAP line as non-GAAP. NVIDIA's "Non-GAAP" summary reports $1.30 diluted EPS, equal to
    GAAP that quarter, a figure under a "Non-GAAP" column is non-GAAP whatever its value, and Walmart's adjusted
    operating income row repeats its GAAP figure (no adjustments that quarter): all three stay."""
    from receipts.press_release import PRow, PTable

    def table(heading, rows, column="Q2 2026"):
        return PTable(index=0, title=list(heading), heading=list(heading), unit_header="(in millions)", scale="millions",
                      columns=[[column]], rows=[PRow(rid=i, label=label, cells={0: cell}, section=section)
                                                for i, (label, cell, section) in enumerate(rows)], lines=[])

    q2 = Q2
    xbrl = {("net_income", *q2): [xv(2800e6)], ("eps_diluted", *q2): [xv(2.48, decimals=2), xv(1.30, decimals=2)],
            ("operating_income", *q2): [xv(8708e6)]}
    cases = [
        (table(["Consolidated Statement of Income"], [("Net Income", "2,800", "Expenses Excluding Interest")]),
         release("net_income", 2800e6, basis="non-GAAP", locator="Net Income | 2,800"), True),
        (table(["Adjusted Diluted EPS from continuing operations"],
               [("Net earnings attributable to Vulcan", "2.48", ""), ("Adjusted diluted EPS attributable to Vulcan", "2.59", "")]),
         fact("eps_diluted", 2.48, q2, basis="non-GAAP", unit="USD/share", decimals=2,
              locator="Net earnings attributable to Vulcan | 2.48"), True),
        (table(["* Non-GAAP financial measure.", "Financial statements"], [("Net income", "2,800", "")]),
         release("net_income", 2800e6, basis="non-GAAP", locator="Net income | 2,800"), True),
        (table(["Non-GAAP"], [("Revenue", "35,082", ""), ("Diluted earnings per share", "1.30", "")]),
         fact("eps_diluted", 1.30, q2, basis="non-GAAP", unit="USD/share", decimals=2,
              locator="Diluted earnings per share | 1.30"), False),
        (table(["Summary"], [("Net income", "2,800", "")], column="Non-GAAP / Q2 2026"),
         release("net_income", 2800e6, basis="non-GAAP", locator="Net income | 2,800"), False),
        (table(["Consolidated Statement of Income"], [("Net Income", "2,930", "Expenses Excluding Interest")]),
         release("net_income", 2930e6, basis="non-GAAP", locator="Net Income | 2,930"), False),  # not the GAAP figure
        (table(["Reconciliation of adjusted operating income"],
               [("Operating income", "8,708", ""), ("Adjusted operating income", "8,708", "")]),
         release("operating_income", 8708e6, basis="non-GAAP", locator="Operating income | 8,708"), False),
    ]
    for t, f, goes in cases:
        drop, records, _ = gate_all({"r.jsonl": [f]}, xbrl, {}, {}, None, exhibit_tables=lambda url, t=t: [t])
        assert (drop == {"r.jsonl": [0]}) == goes, (t.heading, f.locator, records)
        if goes:
            assert records[0]["rule"] == "gaap_line_as_non_gaap"
    # without the exhibit's tables nothing is decided
    assert gate_all({"r.jsonl": [cases[0][1]]}, xbrl, {}, {}, None)[0] == {}


# --- Code review lows L1, L2, L13 -----------------------------------------------------------------------------------
def test_l1_a_periods_other_release_figures_must_agree_with_the_release_that_outranks_its_derived_value():
    """Once the Q4 release (300) outranks the derived quarter (310), the period has no XBRL left, so a year-later
    release's 777 for it went unchecked, in that run and in later ones (`vouched`). It now goes, and a figure equal to
    the release that outranked it stays."""
    q1_xbrl = xv(100e6, accepted=datetime(2025, 5, 1, tzinfo=UTC))
    q4_derived = xv(310e6, "derived", datetime(2026, 2, 20, tzinfo=UTC), terms=2, accession="0000000904-26-000005")
    facts = {"q1.jsonl": [fact("revenue", 100e6, Q1_25, accession="0000000904-25-000001")],
             "q4.jsonl": [fact("revenue", 300e6, Q4_25, datetime(2026, 2, 10, tzinfo=UTC))],
             "next.jsonl": [fact("revenue", v, Q4_25, datetime(2027, 2, 10, tzinfo=UTC), accession="0000000904-27-1")
                            for v in (777e6, 300e6)]}
    xbrl = {("revenue", *Q1_25): [q1_xbrl], ("revenue", *Q4_25): [q4_derived]}
    drop, records, rejected = gate_all(facts, xbrl, {}, {}, None)
    assert [r["value"] for r in rejected] == [310e6] and drop == {"next.jsonl": [0]}
    assert records[0]["rule"] == "outranked_period"
    # a later run: the derived value has left the store, and the decision is recorded
    drop, records, _ = gate_all({k: v for k, v in facts.items() if k != "q4.jsonl"}, {("revenue", *Q1_25): [q1_xbrl]},
                                {}, {}, None, vouched=[("revenue", *Q4_25, 300e6)])
    assert drop == {"next.jsonl": [0]} and records[0]["rule"] == "outranked_period"


def test_l2_a_second_run_still_counts_the_disagreement_the_first_run_dropped():
    """The review's repro: Q2's 205 disagrees with its 10-Q (200), so the row may not outrank a derived quarter. Run 1
    drops the 205 and keeps the derived Q4 (310 against a release of 300); run 2, without the 205, would have trusted
    the row and rejected the derived value. With the first run's record it decides the same."""
    from receipts.release_gates import locator_row
    from scripts.gate_release_facts import prior_disagreements
    q2, q4p = ("2025-04-01", "2025-06-30"), ("2024-10-01", "2024-12-31")
    xbrl = {("revenue", *Q1_25): [xv(100e6)], ("revenue", *q2): [xv(200e6)],
            ("revenue", *q4p): [xv(240e6, accepted=datetime(2025, 2, 20, tzinfo=UTC)),
                                xv(250e6, accepted=datetime(2026, 2, 20, tzinfo=UTC))],
            ("revenue", *Q4_25): [xv(310e6, "derived", datetime(2026, 2, 20, tzinfo=UTC), terms=2)]}
    files = {"a.jsonl": [fact("revenue", 100e6, Q1_25, datetime(2025, 4, 30, tzinfo=UTC), accession="pr-q1")],
             "b.jsonl": [fact("revenue", 205e6, q2, datetime(2025, 7, 30, tzinfo=UTC), accession="pr-q2")],
             "c.jsonl": [fact("revenue", 300e6, Q4_25, datetime(2026, 2, 10, tzinfo=UTC), accession="pr-q4"),
                         fact("revenue", 250e6, q4p, datetime(2026, 2, 10, tzinfo=UTC), accession="pr-q4")]}
    drop, records, rejected = gate_all(files, xbrl, {}, {}, None)   # c.jsonl is on the restated basis: excused
    assert not rejected and [(r["file"], r["rule"]) for r in records] == [("b.jsonl", "xbrl_reported")]
    second = {n: [f for i, f in enumerate(rows) if i not in set(drop.get(n, []))] for n, rows in files.items()}
    assert gate_all(second, xbrl, {}, {}, None)[2] != []           # the old behaviour: run 2 rejects the derived value
    prior = [(r["metric"], locator_row(r["locator"])) for r in records if r["rule"] == "xbrl_reported"]
    assert gate_all(second, xbrl, {}, {}, None, prior_disagreements=prior)[2] == []


def test_l2_the_script_reads_earlier_drops_from_the_reports_and_skips_figures_still_live(tmp_path):
    from scripts.gate_release_facts import prior_disagreements
    live = fact("revenue", 300e6, Q4_25, locator="Total revenue | 300")
    report = {"release_gates": [
        {"line": 1, "rule": "xbrl_reported", "metric": "revenue", "basis": "GAAP", "segment": None,
         "period": "2025-04-01..2025-06-30", "value": 205e6, "locator": "Total revenue (1) | 205", "why": "x"},
        {"line": 2, "rule": "xbrl_reported", "metric": "revenue", "basis": "GAAP", "segment": None,
         "period": "2025-10-01..2025-12-31", "value": 300e6, "locator": "Total revenue | 300", "why": "x"},
        {"line": 3, "rule": "label_dates", "metric": "revenue", "basis": "GAAP", "segment": None,
         "period": "2025-01-01..2025-03-31", "value": 1e6, "locator": "Total revenue | 1", "why": "x"}]}
    (tmp_path / "x.report.json").write_text(json.dumps(report))
    assert prior_disagreements(tmp_path, {"x.jsonl": [live]}) == [("revenue", "total revenue")]


def test_l13_fix11_reads_a_transposed_tables_three_part_locator():
    """A transposed table's figures carry "period row | measure column | cell" locators (Microsoft's "2024 As
    Reported (GAAP) | Revenue | $61,858"); fix 11 never found their cells. A non-GAAP figure read from the GAAP row
    now goes, and the adjusted row's figure stays."""
    from receipts.press_release import PRow, PTable
    t = PTable(index=0, title=["Reconciliation"], heading=["Reconciliation"], unit_header="(in millions)",
               scale="millions", columns=[["Net income"]], lines=[],
               rows=[PRow(rid=0, label="Q2 2026 As reported", cells={0: "2,800"}, section=""),
                     PRow(rid=1, label="Q2 2026 As adjusted", cells={0: "3,100"}, section="")])
    xbrl = {("net_income", *Q2): [xv(2800e6)]}
    gaap_row = release("net_income", 2800e6, basis="non-GAAP", locator="Q2 2026 As reported | Net income | 2,800")
    adjusted = release("net_income", 3100e6, basis="non-GAAP", locator="Q2 2026 As adjusted | Net income | 3,100")
    drop, records, _ = gate_all({"r.jsonl": [gaap_row, adjusted]}, xbrl, {}, {}, None, exhibit_tables=lambda url: [t])
    assert drop == {"r.jsonl": [0]} and records[0]["rule"] == "gaap_line_as_non_gaap"
