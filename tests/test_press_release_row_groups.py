"""A rowspan ends at its HTML row group's boundary, including between two tbody elements."""
from datetime import date, datetime, timezone

import pytest
from bs4 import BeautifulSoup

from receipts import press_release as pr


ROW_GROUP_RELEASE = """
<p>SYNTHETIC TEST DATA — In millions</p>
<table><thead><tr><th rowspan="2">Metric</th>
<th>Three Months Ended June 30, 2026</th><th>Three Months Ended June 30, 2025</th>
</tr></thead><tbody><tr><td>Adjusted revenue</td><td>100</td><td>90</td></tr></tbody></table>
"""


@pytest.mark.parametrize("first,second", [("thead", "tbody"), ("tbody", "tfoot"), ("tbody", "tbody"),
                                          ("thead", "tfoot"), ("tfoot", "tbody"), ("tbody", "")])
def test_rowspan_cannot_reserve_columns_in_the_next_row_group(first, second):
    next_row = "<tr><td>Adjusted revenue</td><td>100</td><td>90</td></tr>"
    if second:
        next_row = f"<{second}>{next_row}</{second}>"
    html = (f'<table><{first}><tr><th rowspan="3">Metric</th><th>2026</th><th>2025</th>'
            f"</tr></{first}>{next_row}</table>")
    grid = pr._grid_rows(BeautifulSoup(html, "lxml").table)
    assert grid[1] == [["Adjusted revenue", 0, 0], ["100", 1, 1], ["90", 2, 2]]


def test_rowspan_still_reserves_columns_within_the_same_group():
    html = ('<table><thead><tr><th rowspan="2">Metric</th><th colspan="2">Quarter ended</th></tr>'
            '<tr><th>2026</th><th>2025</th></tr></thead><tbody>'
            '<tr><td>Adjusted revenue</td><td>100</td><td>90</td></tr></tbody></table>')
    grid = pr._grid_rows(BeautifulSoup(html, "lxml").table)
    assert grid[1] == [["2026", 1, 1], ["2025", 2, 2]]
    assert grid[2] == [["Adjusted revenue", 0, 0], ["100", 1, 1], ["90", 2, 2]]


def mapped_release_facts(*, cik=1, ticker="EXMP"):
    """A synthetic mapper follows the parsed column headers; code still validates every proposal."""
    table, = pr.parse_tables(ROW_GROUP_RELEASE)
    entries = [dict(start=f"{y}-04-01", end=f"{y}-06-30", val=1, fy=y, fp="Q2", form="10-Q",
                    accn=f"0000000001-{y % 100}-000001", filed=f"{y}-07-31") for y in (2025, 2026)]
    calendar = pr.FiscalCalendar({"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}})
    ctx = pr.FilingContext(ticker=ticker, cik=cik, company="Synthetic release scenario",
                           accession="0000000001-26-000019", accepted_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
                           url="https://example.invalid/synthetic-release.htm", exhibit="EX-99.1",
                           calendar=calendar, doc_dates={date(2026, 6, 30), date(2025, 6, 30)})
    columns = []
    for i, header in enumerate(table.columns):
        dates = pr.parse_dates(" ".join(header))
        columns.append(pr.ColumnMap(col=i, use=bool(dates), period_end=str(next(iter(dates))) if dates else "",
                                    duration="quarter", basis="row"))
    mapping = pr.TableMap(table=table.index, scale="millions", columns=columns,
                          rows=[pr.RowMap(row=0, metric="revenue", basis="non-GAAP", segment="", values=[100, 90])])
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    job = pr.FilingJob(ticker=ticker, cik=cik, company=ctx.company, eightk={}, tenq=None, ctx=ctx, facts=facts)
    gate = pr.xbrl_gate(job, {})
    assert not gate.quarantine and not gate.dropped
    return facts


def test_row_group_boundary_preserves_each_release_values_year_through_mapping_and_gate():
    facts = mapped_release_facts()
    assert [(f.period_end, f.value) for f in facts] == [(date(2026, 6, 30), 100e6), (date(2025, 6, 30), 90e6)]
