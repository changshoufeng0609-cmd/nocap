"""Net income after preferred dividends (receipts.ingest.AVAILABLE_TO_COMMON): served as a company's net income once
its own releases show that is the figure they report (scripts/confirm_revenue.py), revoked when one shows the other.
Invesco's "Net income/(loss) attributable to Invesco Ltd." is -$12.5M for Q2 2025, its
NetIncomeLossAvailableToCommonStockholdersBasic, where ProfitLoss less the noncontrolling interest is $203.4M."""
import gzip
import json
import sqlite3
from datetime import date, datetime, timezone

from receipts.ingest import AVAILABLE_TO_COMMON, build_company
from receipts.types import Fact

FILINGS = {  # key -> (accession, form, filed, accepted, report date, DEI fy, DEI fp)
    "Q1": ("0000000903-25-000001", "10-Q", "2025-04-30", "2025-04-30T20:00:00.000Z", "2025-03-31", 2025, "Q1"),
    "Q2": ("0000000903-25-000002", "10-Q", "2025-07-30", "2025-07-30T20:00:00.000Z", "2025-06-30", 2025, "Q2"),
    "Q3": ("0000000903-25-000003", "10-Q", "2025-10-30", "2025-10-30T20:00:00.000Z", "2025-09-30", 2025, "Q3"),
}
PERIOD = {"Q1": ("2025-01-01", "2025-03-31"), "Q2": ("2025-04-01", "2025-06-30"), "Q3": ("2025-07-01", "2025-09-30")}
NCI = "NetIncomeLossAttributableToNoncontrollingInterest"
LABEL = "Net income/(loss) attributable to Example Ltd."
# Q1 tags both definitions; Q2 and Q3 tag only the available-to-common figure beside ProfitLoss and the NCI
ENTRIES = (("NetIncomeLoss", "Q1", 100e6), (AVAILABLE_TO_COMMON, "Q1", 80e6), ("ProfitLoss", "Q1", 110e6),
           (NCI, "Q1", 10e6), (AVAILABLE_TO_COMMON, "Q2", -5e6), ("ProfitLoss", "Q2", 120e6), (NCI, "Q2", 10e6),
           (AVAILABLE_TO_COMMON, "Q3", 90e6), ("ProfitLoss", "Q3", 125e6), (NCI, "Q3", 15e6))


def companyfacts() -> dict:
    usgaap: dict = {}
    for concept, key, value in ENTRIES:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        start, end = PERIOD[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    return {"facts": {"us-gaap": usgaap}}


def build(label=None):
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    return build_company(cik=903, ticker="EXLT", names=["Example Ltd."], fiscal_year_end="1231", filing_rows=rows,
                         companyfacts=companyfacts(), facts_start=date(2024, 1, 1), today=date(2026, 10, 1),
                         available_to_common=label)


def net_income(b):
    return {(f.period_end.isoformat(), f.concept.removeprefix("us-gaap:"), f.source, f.value)
            for f, kind in b.facts if f.metric == "net_income" and kind == "quarter"}


def test_a_decided_company_serves_its_available_to_common_figure_and_never_profitloss():
    assert net_income(build()) == {("2025-03-31", "NetIncomeLoss", "xbrl", 100e6),
                                   ("2025-06-30", "NetIncomeLoss", "derived", 110e6),   # ProfitLoss less the NCI
                                   ("2025-09-30", "NetIncomeLoss", "derived", 110e6)}
    b = build(LABEL)
    assert net_income(b) == {("2025-03-31", AVAILABLE_TO_COMMON, "xbrl", 80e6),
                             ("2025-06-30", AVAILABLE_TO_COMMON, "xbrl", -5e6),
                             ("2025-09-30", AVAILABLE_TO_COMMON, "xbrl", 90e6)}
    q2 = next(f for f, _ in b.facts if f.metric == "net_income" and f.period_end == date(2025, 6, 30))
    assert q2.locator == (f"{LABEL} (after preferred dividends), as the company reports it: {AVAILABLE_TO_COMMON} "
                          f"(10-Q {FILINGS['Q2'][0]})")
    assert q2.derived_from == [] and q2.url.endswith("/doc.htm")


def _company(tmp_path, live, dropped=(), recorded=()):
    """An audit, archived companyfacts and one release file: `live` (start, end, value) facts, `dropped` ones the
    paired-filing XBRL gate removed and `recorded` ones scripts/gate_release_facts.py removed (both in the report)."""
    (tmp_path / "audit").mkdir(parents=True, exist_ok=True)
    (tmp_path / "audit" / "EXLT.json").write_text(json.dumps({"cik": 903, "revenue": []}))
    (tmp_path / "cf").mkdir(exist_ok=True)
    (tmp_path / "cf" / "CIK0000000903.json.gz").write_bytes(gzip.compress(json.dumps(companyfacts()).encode()))
    folder = tmp_path / "pr" / "EXLT"
    folder.mkdir(parents=True, exist_ok=True)
    cell = lambda v: f"{LABEL} | " + (f"({abs(v) / 1e6:.1f})" if v < 0 else f"{v / 1e6:.1f}")
    (folder / "r.jsonl").write_text("".join(Fact(
        cik=903, ticker="EXLT", metric="net_income", basis="GAAP", value=v, unit="USD",
        period_start=date.fromisoformat(s), period_end=date.fromisoformat(e), source="press_release", form="8-K",
        accession="0000000903-25-000009", accepted_at=datetime(2025, 10, 28, tzinfo=timezone.utc),
        url="https://example.invalid/ex99.htm", locator=cell(v), decimals=-5).model_dump_json() + "\n"
        for s, e, v in live))
    report = {"xbrl_gate": {"dropped": [{"metric": "net_income", "period": f"{s}..{e}", "press_release": v,
                                         "locator": cell(v)} for s, e, v in dropped]},
              "release_gates": [{"rule": "xbrl_derived", "metric": "net_income", "basis": "GAAP", "segment": None,
                                 "period": f"{s}..{e}", "value": v, "locator": cell(v)} for s, e, v in recorded]}
    (folder / "r.report.json").write_text(json.dumps(report))
    return tmp_path / "audit", tmp_path / "pr", tmp_path / "cf"


def test_two_periods_where_the_releases_equal_the_available_figure_decide_and_one_on_the_other_figure_blocks(
        tmp_path):
    from scripts.confirm_revenue import available_to_common
    q1, q2, q3 = ((*PERIOD[k], v) for k, v in (("Q1", 80e6), ("Q2", -5e6), ("Q3", 90e6)))
    d = available_to_common("EXLT", *_company(tmp_path, live=[q3], dropped=[q2]))
    assert d["decided"] and d["label"] == LABEL
    assert [(p["period"], p["agrees_with"]) for p in d["periods"]] == [
        ("2025-04-01..2025-06-30", "available"), ("2025-07-01..2025-09-30", "available")]
    assert not available_to_common("EXLT", *_company(tmp_path / "one", live=[q3]))["decided"]
    d = available_to_common("EXLT", *_company(tmp_path / "other", live=[q3], dropped=[q2],
                                              recorded=[(*PERIOD["Q1"], 100e6)]))  # Q1's NetIncomeLoss
    assert not d["decided"] and d["reason"] == "a release agrees with the net income before preferred dividends"
    assert available_to_common("EXLT", *_company(tmp_path / "q1", live=[q1, q3]))["periods"][0]["agrees_with"] == \
        "available"


def test_main_records_the_decision_and_a_recheck_revokes_it(tmp_path):
    from scripts.confirm_revenue import NET_INCOME_KEY, main
    q2, q3 = (*PERIOD["Q2"], -5e6), (*PERIOD["Q3"], 90e6)
    audit_dir, pr_dir, cf_dir = _company(tmp_path, live=[q3], dropped=[q2])
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("facts_start", "2023-09-14"), ("quarters", "8")])
    con.commit()
    con.close()
    path = tmp_path / "confirmations.json"
    kw = dict(audit_dir=audit_dir, pr_dir=pr_dir, confirmations_path=path, db=db, cf_dir=cf_dir)
    assert main(["EXLT"], **kw) == 0
    decision = json.loads(path.read_text())["EXLT"][NET_INCOME_KEY]
    assert decision["concept"] == AVAILABLE_TO_COMMON and decision["decided_on"] == "2026-10-31"
    _company(tmp_path, live=[q3], dropped=[q2], recorded=[(*PERIOD["Q1"], 100e6)])  # a later release: Q1's 100
    assert main(["--recheck"], **kw) == 0
    assert "EXLT" not in json.loads(path.read_text())
