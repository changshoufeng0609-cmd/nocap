"""A company whose registrant changed (receipts.ingest.PREDECESSOR_CIKS) is one company row under its current CIK:
each earlier registrant's filings and companyfacts are merged in, every filing keeps the URL of a registrant whose
Submissions list it, and one fiscal calendar runs across the switch."""
from datetime import date

import pytest

from receipts import ingest
from receipts.ingest import (build_company, company_inputs, merge_companyfacts, merge_filing_rows,
                             with_predecessors)

OLD, NEW = 9001, 9002
# (accession, form, filed, report date, fy, fp) - the old registrant files through Q1 2026; the Q2 2026 10-Q is
# filed after the reorganization and listed in both registrants' Submissions.
OLD_FILINGS = (("0000009001-25-000010", "10-Q", "2025-05-01", "2025-03-31", 2025, "Q1"),
               ("0000009001-25-000020", "10-Q", "2025-08-01", "2025-06-30", 2025, "Q2"),
               ("0000009001-25-000030", "10-Q", "2025-11-01", "2025-09-30", 2025, "Q3"),
               ("0000009001-26-000005", "8-K", "2026-01-30", "2026-01-30", None, None),
               ("0000009001-26-000010", "10-K", "2026-02-18", "2025-12-31", 2025, "FY"),
               ("0000009001-26-000020", "10-Q", "2026-05-04", "2026-03-31", 2026, "Q1"))
SHARED = ("0000009001-26-000030", "10-Q", "2026-08-03", "2026-06-30", 2026, "Q2")
NEW_ONLY = ("0000009002-26-000006", "8-K", "2026-07-31", "2026-07-31", None, None)
PERIODS = {"Q1-25": ("2025-01-01", "2025-03-31"), "Q2-25": ("2025-04-01", "2025-06-30"),
           "H1-25": ("2025-01-01", "2025-06-30"), "Q3-25": ("2025-07-01", "2025-09-30"),
           "M9-25": ("2025-01-01", "2025-09-30"), "FY-25": ("2025-01-01", "2025-12-31"),
           "Q1-26": ("2026-01-01", "2026-03-31"), "Q2-26": ("2026-04-01", "2026-06-30"),
           "H1-26": ("2026-01-01", "2026-06-30")}


def row(accession, form, filed, report, *_):
    return {"accessionNumber": accession, "form": form, "filingDate": filed, "reportDate": report,
            "acceptanceDateTime": f"{filed}T20:00:00.000Z", "primaryDocument": "doc.htm",
            "items": "2.02" if form == "8-K" else ""}


def entry(period, value, filing, frame=None):
    accession, form, filed, _, fy, fp = filing
    start, end = PERIODS[period]
    e = {"start": start, "end": end, "val": value, "accn": accession, "fy": fy, "fp": fp, "form": form,
         "filed": filed}
    return e | ({"frame": frame} if frame else {})


def companyfacts(revenue: list[dict], net_income: list[dict], name: str) -> dict:
    return {"entityName": name, "facts": {"us-gaap": {
        "Revenues": {"label": "Revenues", "units": {"USD": revenue}},
        "NetIncomeLoss": {"label": "Net income", "units": {"USD": net_income}}}}}


def registrants():
    q1, q2, q3, _, k, q1b = OLD_FILINGS
    old_cf = companyfacts(
        [entry("Q1-25", 100e6, q1), entry("Q2-25", 110e6, q2), entry("H1-25", 210e6, q2),
         entry("Q3-25", 120e6, q3), entry("M9-25", 330e6, q3), entry("FY-25", 460e6, k),
         entry("Q1-26", 140e6, q1b)],
        [entry("Q1-25", 10e6, q1), entry("Q2-25", 11e6, q2), entry("Q3-25", 12e6, q3), entry("FY-25", 46e6, k),
         entry("Q1-26", 14e6, q1b)], "Old Registrant Corp")
    new_cf = companyfacts(  # the Q2 2026 10-Q, with its Q2 2025 comparatives
        [entry("Q2-26", 150e6, SHARED, frame="CY2026Q2"), entry("H1-26", 290e6, SHARED),
         entry("Q2-25", 110e6, SHARED, frame="CY2025Q2"), entry("H1-25", 210e6, SHARED)],
        [entry("Q2-26", 15e6, SHARED), entry("Q2-25", 11e6, SHARED)], "New Holdings Corp")
    old_rows = [row(*f) for f in (*OLD_FILINGS, SHARED)]
    new_rows = [row(*f) for f in (SHARED, NEW_ONLY)]
    return old_rows, old_cf, new_rows, new_cf


def build(rows, cf):
    return build_company(cik=NEW, ticker="NEWH",
                         names=["New Holdings Corp", "NEW HOLDINGS CORP", "OLD REGISTRANT CORP"],
                         fiscal_year_end="1231", filing_rows=rows, companyfacts=cf, facts_start=date(2025, 1, 1),
                         today=date(2026, 10, 31))


def test_the_predecessors_filings_join_one_company_row_with_each_url_in_its_registrants_folder():
    old_rows, old_cf, new_rows, new_cf = registrants()
    rows = merge_filing_rows(sorted(new_rows, key=lambda r: r["acceptanceDateTime"], reverse=True), OLD, old_rows)
    assert [r["accessionNumber"] for r in rows].count(SHARED[0]) == 1  # listed by both, kept once
    b = build(rows, merge_companyfacts(new_cf, old_cf))

    assert b.company["cik"] == NEW and b.company["name"] == "New Holdings Corp"
    assert {"new holdings", "old registrant"} <= b.company["aliases"]
    urls = {f["accession"]: f["url"] for f in b.filings}
    assert urls[SHARED[0]] == f"https://www.sec.gov/Archives/edgar/data/{NEW}/000000900126000030/doc.htm"
    assert urls[NEW_ONLY[0]] == f"https://www.sec.gov/Archives/edgar/data/{NEW}/000000900226000006/doc.htm"
    for accession, *_ in OLD_FILINGS:
        assert urls[accession] == (f"https://www.sec.gov/Archives/edgar/data/{OLD}/"
                                   f"{accession.replace('-', '')}/doc.htm")
    facts = [f for f, _ in b.facts]
    assert {f.cik for f in facts} == {NEW} and {f.ticker for f in facts} == {"NEWH"}
    assert all(f.url == urls[f.accession] for f in facts)


def test_one_fiscal_calendar_runs_across_the_switch_and_both_registrants_agree_on_shared_periods():
    old_rows, old_cf, new_rows, new_cf = registrants()
    b = build(merge_filing_rows(new_rows, OLD, old_rows), merge_companyfacts(new_cf, old_cf))
    labels = {(fy, fp): (s.isoformat(), e.isoformat()) for fy, fp, s, e, _ in b.periods}
    assert labels[(2025, "Q4")] == ("2025-10-01", "2025-12-31")
    assert labels[(2026, "Q1")] == PERIODS["Q1-26"] and labels[(2026, "Q2")] == PERIODS["Q2-26"]
    assert len(labels) == len(b.periods)  # no period labelled twice
    revenue = [f for f, k in b.facts if f.metric == "revenue" and k == "quarter"]
    q4 = [f for f in revenue if f.fiscal_period == "Q4"]
    assert [(f.value, f.source) for f in q4] == [(130e6, "derived")]  # FY - 9M, both from the old registrant
    q2_2025 = {f.accession: f.value for f in revenue if f.period_end == date(2025, 6, 30)}
    assert q2_2025 == {OLD_FILINGS[1][0]: 110e6, SHARED[0]: 110e6}  # original and comparative, one value
    assert len({(f.metric, f.accession, f.period_start, f.period_end) for f, _ in b.facts}) == len(b.facts)


def test_an_entry_both_registrants_list_counts_once_and_the_current_ones_entries_stay_as_listed():
    _, old_cf, _, new_cf = registrants()
    old_cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(entry("Q2-26", 150e6, SHARED, frame="CY2026Q2I"))
    merged = merge_companyfacts(new_cf, old_cf)
    rev = merged["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    assert sum(e["accn"] == SHARED[0] and e["end"] == "2026-06-30" and e["start"] == "2026-04-01" for e in rev) == 1
    assert rev[:4] == new_cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]  # SEC's order and frames, untouched
    assert len(rev) == 4 + 7
    assert merged["entityName"] == "New Holdings Corp"
    assert len(new_cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 4  # the inputs are not modified


class FakeEdgar:
    def __init__(self, data):
        self.data, self.calls = data, []

    def submissions(self, cik, *, since=None, max_age=None):
        self.calls.append(("submissions", cik))
        meta, rows = self.data[cik][0]
        return meta, [r for r in rows if since is None or r["filingDate"] >= since.isoformat()]

    def companyfacts(self, cik, *, max_age=None):
        self.calls.append(("companyfacts", cik))
        return self.data[cik][1]


def test_company_inputs_and_with_predecessors_read_every_registrant_of_a_mapped_cik(monkeypatch):
    old_rows, old_cf, new_rows, new_cf = registrants()
    monkeypatch.setitem(ingest.PREDECESSOR_CIKS, NEW, (OLD,))
    ed = FakeEdgar({NEW: (({"name": "New Holdings Corp"}, new_rows), new_cf),
                    OLD: (({"name": "OLD REGISTRANT CORP"}, old_rows), old_cf)})
    meta, rows, cf, names = company_inputs(ed, NEW, since=date(2025, 1, 1))
    assert names == ["New Holdings Corp", "OLD REGISTRANT CORP"] and meta["name"] == "New Holdings Corp"
    assert len(rows) == len(OLD_FILINGS) + 2
    assert {r["accessionNumber"]: r.get("filer_cik") for r in rows}[SHARED[0]] is None  # the current CIK's folder
    assert all(r["filer_cik"] == OLD for r in rows if r["accessionNumber"].startswith("0000009001-2")
               and r["accessionNumber"] != SHARED[0])
    assert cf == merge_companyfacts(new_cf, old_cf)
    assert with_predecessors(NEW, lambda c: ed.data[c][1]) == cf
    assert with_predecessors(OLD, lambda c: ed.data[c][1]) is old_cf  # no predecessor: read as is


@pytest.mark.parametrize("cik", [1045810, 320193])
def test_a_company_without_predecessors_reads_exactly_as_before(cik):
    rows = [row("0000000001-26-000001", "10-Q", "2026-05-01", "2026-03-31")]
    cf = {"facts": {"us-gaap": {}}}
    ed = FakeEdgar({cik: (({"name": "X Corp"}, rows), cf)})
    meta, got_rows, got_cf, names = company_inputs(ed, cik)
    assert (got_rows, got_cf, names) == (rows, cf, ["X Corp"])
    assert ed.calls == [("submissions", cik), ("companyfacts", cik)]


def test_an_exhibit_in_a_predecessor_registrants_folder_is_archived_with_the_company(tmp_path, monkeypatch):
    import hashlib
    import json
    import sqlite3

    import scripts.archive_exhibits as ax
    monkeypatch.setitem(ingest.PREDECESSOR_CIKS, NEW, (OLD,))
    monkeypatch.setattr(ax, "PREDECESSOR_CIKS", ingest.PREDECESSOR_CIKS)
    monkeypatch.setattr(ax, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(ax, "ARCHIVE", tmp_path / "archive")
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE filings (cik INTEGER, accession TEXT, form TEXT)")
    con.executemany("INSERT INTO filings VALUES (?, ?, '8-K')", [(NEW, OLD_FILINGS[3][0]), (NEW, NEW_ONLY[0])])
    con.commit()
    con.close()
    monkeypatch.setattr(ax.eight_k_folders, "__defaults__", (db,))
    ax.CACHE.mkdir()
    urls = [f"https://www.sec.gov/Archives/edgar/data/{OLD}/{OLD_FILINGS[3][0].replace('-', '')}/ex99.htm",
            f"https://www.sec.gov/Archives/edgar/data/{NEW}/{NEW_ONLY[0].replace('-', '')}/ex99.htm",
            f"https://www.sec.gov/Archives/edgar/data/{OLD}/{OLD_FILINGS[0][0].replace('-', '')}/q.htm"]  # a 10-Q
    for url in urls:
        name, body = hashlib.sha256(url.encode()).hexdigest(), url.encode()
        (ax.CACHE / f"{name}.bin").write_bytes(body)
        (ax.CACHE / f"{name}.json").write_text(json.dumps({"url": url, "sha256": hashlib.sha256(body).hexdigest()}))
    ax.archive(delete_raw=True)
    index = ax.load_index()
    assert sorted(index) == sorted(urls[:2])
    assert {u: r["cik"] for u, r in index.items()} == {urls[0]: OLD, urls[1]: NEW}


def test_the_release_mapper_reads_each_8k_in_the_folder_of_a_registrant_that_lists_it():
    from scripts.map_predecessor_releases import FilerFolders

    calls = []

    class Client:
        live_requests = 3

        def filing_index(self, cik, accession):
            calls.append((cik, accession))
            return []

    routed = FilerFolders(Client(), {OLD_FILINGS[3][0]: OLD, NEW_ONLY[0]: NEW})
    routed.filing_index(NEW, OLD_FILINGS[3][0])
    routed.filing_index(NEW, NEW_ONLY[0])
    routed.filing_index(NEW, "0000009002-26-000099")  # not listed: the company's own folder
    assert calls == [(OLD, OLD_FILINGS[3][0]), (NEW, NEW_ONLY[0]), (NEW, "0000009002-26-000099")]
    assert routed.live_requests == 3  # everything else is the client's own
