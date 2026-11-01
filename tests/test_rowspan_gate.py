"""scripts/gate_rowspan_headers.py: a served release fact read under a period header that receipts.press_release's
grid moved (it ignores rowspan) is dropped once the company's XBRL proves the HTML layout's header."""
import json
import sqlite3
from datetime import date, datetime, timezone

import scripts.gate_rowspan_headers as g
from receipts.types import Fact

URL = "https://www.sec.gov/Archives/edgar/data/1751788/000175178826000144/ex991.htm"
# Dow's Q2 2026 income statement: the unit label spans two header rows, so HTML lays the date row out from the
# first number column; a grid that ignores rowspan reads "Jun 30, 2025" over the 2026 column.
SHIFTED = """
<table>
<tr><td colspan="3" rowspan="2">In millions, except per share amounts</td><td colspan="6">Three Months Ended</td>
<td colspan="6">Six Months Ended</td></tr>
<tr><td colspan="3">Jun 30, 2026</td><td colspan="3">Jun 30, 2025</td><td colspan="3">Jun 30, 2026</td>
<td colspan="3">Jun 30, 2025</td></tr>
<tr><td colspan="3">Net sales</td><td>$</td><td>12,092</td><td></td><td>$</td><td>10,104</td><td></td>
<td>$</td><td>21,886</td><td></td><td>$</td><td>20,535</td><td></td></tr>
<tr><td colspan="3">Cost of sales</td><td></td><td>9,925</td><td></td><td></td><td>9,521</td><td></td>
<td></td><td>19,079</td><td></td><td></td><td>19,281</td><td></td></tr>
</table>"""
ALIGNED = SHIFTED.replace(' rowspan="2"', "").replace('<tr><td colspan="3">Jun 30, 2026',
                                                       '<tr><td colspan="3"></td><td colspan="3">Jun 30, 2026')


def fact(value: float, start: str, end: str, locator: str, fy: int) -> Fact:
    return Fact(cik=1751788, ticker="DOW", metric="cost_of_revenue", concept=None, basis="GAAP", segment=None,
                value=value, unit="USD", period_start=date.fromisoformat(start), period_end=date.fromisoformat(end),
                fiscal_year=fy, fiscal_period="Q2", source="press_release", form="8-K",
                accession="0001751788-26-000144", accepted_at=datetime(2026, 7, 23, 10, tzinfo=timezone.utc),
                url=URL, locator=locator, decimals=-6)


def setup(tmp_path, html: str, xbrl: list[tuple]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE facts (ticker TEXT, value REAL, period_start TEXT, period_end TEXT, fiscal_year INT, "
                "segment TEXT, source TEXT, unit TEXT)")
    con.executemany("INSERT INTO facts VALUES ('DOW', ?, ?, ?, ?, NULL, 'xbrl', 'USD')", xbrl)
    con.commit()
    con.close()
    folder = tmp_path / "pr_facts" / "DOW"
    folder.mkdir(parents=True)
    # as the mapper read the shifted table: 9,925 (the 2026 column) as Q2 2025, 9,521 (2025) as Q2 2026
    facts = [fact(9925e6, "2025-04-01", "2025-06-30", "Cost of sales | 9,925", 2025),
             fact(9521e6, "2026-04-01", "2026-06-30", "Cost of sales | 9,521", 2026)]
    path = folder / "0001751788-26-000144.jsonl"
    path.write_text("".join(f.model_dump_json() + "\n" for f in facts))
    (folder / "0001751788-26-000144.report.json").write_text(json.dumps({"url": URL, "candidates": [0]}))
    return db, path


def test_the_mapper_grid_moves_the_date_row_and_the_html_layout_does_not():
    old, new = g.both_readings(SHIFTED.encode())
    assert [" / ".join(c) for c in old[0].columns] == [
        "Three Months Ended / Jun 30, 2025", "Three Months Ended / Jun 30, 2026", "Six Months Ended / Jun 30, 2025",
        "Six Months Ended"]
    assert [" / ".join(c) for c in new[0].columns] == [
        "Three Months Ended / Jun 30, 2026", "Three Months Ended / Jun 30, 2025", "Six Months Ended / Jun 30, 2026",
        "Six Months Ended / Jun 30, 2025"]
    old, new = g.both_readings(ALIGNED.encode())
    assert old[0].columns == new[0].columns


def test_facts_under_a_moved_header_are_dropped_once_xbrl_proves_the_layout(tmp_path):
    db, path = setup(tmp_path, SHIFTED, [(9521e6, "2025-04-01", "2025-06-30", 2025)])  # Q2 2025 cost of sales
    before = path.read_text()
    dry = g.run(tmp_path / "pr_facts", db, write=False, html_of=lambda url: SHIFTED.encode())
    assert [(d["value"], d["period"]) for d in dry["dropped"]] == [(9925e6, "2025-04-01..2025-06-30"),
                                                                  (9521e6, "2026-04-01..2026-06-30")]
    assert path.read_text() == before  # a dry run writes nothing
    done = g.run(tmp_path / "pr_facts", db, write=True, html_of=lambda url: SHIFTED.encode())
    assert done["files_changed"] == 1 and path.read_text() == ""
    report = json.loads(path.with_name("0001751788-26-000144.report.json").read_text())
    assert [r["rule"] for r in report["release_gates"]] == ["rowspan_header"] * 2
    assert "Cost of sales | 9,521" in report["release_gates"][0]["why"]
    again = g.run(tmp_path / "pr_facts", db, write=True, html_of=lambda url: SHIFTED.encode())
    assert again["dropped"] == []  # a re-run drops nothing


def test_without_xbrl_proof_a_fact_under_a_moved_header_is_dropped_as_unproven(tmp_path):
    db, path = setup(tmp_path, SHIFTED, [])  # no XBRL for either quarter: neither reading can vouch for the facts
    done = g.run(tmp_path / "pr_facts", db, write=True, html_of=lambda url: SHIFTED.encode())
    assert done["proven_tables"] == 0 and [d["rule"] for d in done["dropped"]] == ["rowspan_unproven"] * 2
    assert path.read_text() == ""
    report = json.loads(path.with_name("0001751788-26-000144.report.json").read_text())
    assert "Jun 30, 2025' -> 'Three Months Ended / Jun 30, 2026'" in report["release_gates"][0]["why"]


def test_nothing_is_dropped_when_xbrl_sides_with_the_mapper(tmp_path):
    db, path = setup(tmp_path, SHIFTED, [(9521e6, "2026-04-01", "2026-06-30", 2026)])  # Q2 2026 as the mapper read
    before = path.read_text()
    done = g.run(tmp_path / "pr_facts", db, write=True, html_of=lambda url: SHIFTED.encode())
    assert done["dropped"] == [] and done["proven_tables"] == 0 and path.read_text() == before


def test_the_gate_reads_the_old_grid_whatever_grid_the_mapper_has_now():
    from receipts import press_release as pr
    old, _ = g.both_readings(SHIFTED.encode())
    assert pr._grid_rows is not g.grid_rows_mapped and pr._grid_rows is not g.grid_rows_html  # restored
    now = {t.index: t for t in pr.parse_tables(SHIFTED.encode())}
    assert old[0].columns != now[0].columns  # the stored files were read with the old grid


def test_a_table_html_and_the_grid_lay_out_alike_is_left_alone(tmp_path):
    db, path = setup(tmp_path, ALIGNED, [(9521e6, "2025-04-01", "2025-06-30", 2025)])
    result = g.run(tmp_path / "pr_facts", db, write=True, html_of=lambda url: ALIGNED.encode())
    assert result["dropped"] == [] and result["tables"] == []
