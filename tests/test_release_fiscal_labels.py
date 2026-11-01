"""Release facts carry the same fiscal labels as the stored XBRL calendar (receipts.press_release.FiscalCalendar).

SYNTHETIC data for a fictional January-year-end filer whose FY2026 10-K reuses last year's DEI fiscal year, the way
Salesforce's does.
"""
from datetime import date

from receipts import press_release as pr
from test_press_release import _fact, column, companyfacts, ctx_for, money_row, only_table, row, table_html, td

REUSED = (  # FY2025 10-K, three FY2026 10-Qs, and the FY2026 10-K tagged fy=2025 again
    _fact("NetIncomeLoss", "2024-02-01", "2025-01-31", 1.0, 2025, "FY", "10-K", "0000000001-25-000002", "2025-03-05"),
    _fact("NetIncomeLoss", "2025-02-01", "2025-04-30", 1.0, 2026, "Q1", "10-Q", "0000000001-25-000010", "2025-06-01"),
    _fact("NetIncomeLoss", "2025-05-01", "2025-07-31", 1.0, 2026, "Q2", "10-Q", "0000000001-25-000020", "2025-09-01"),
    _fact("NetIncomeLoss", "2025-08-01", "2025-10-31", 1.0, 2026, "Q3", "10-Q", "0000000001-25-000030", "2025-12-01"),
    _fact("NetIncomeLoss", "2025-02-01", "2026-01-31", 1.0, 2025, "FY", "10-K", "0000000001-26-000002", "2026-03-05"),
)


def test_two_periods_in_one_release_never_share_a_label():
    html = table_html(["Example Corp", "(In millions)"],
                      [td() + td("Three Months Ended", 6), td() + td("January 31, 2026", 3) + td("January 31, 2025", 3)],
                      [money_row("Revenue", ("11,201", ""), ("10,000", ""))])
    t = only_table(html)
    m = pr.TableMap(table=t.index, scale="millions", columns=[column(0, "2026-01-31"), column(1, "2025-01-31")],
                    rows=[row(0, "revenue", [11201, 10000])])
    facts, _ = pr.decide_table(t, m, ctx_for([t], REUSED))
    assert sorted((f.period_end, f.fiscal_year, f.fiscal_period) for f in facts) == [
        (date(2025, 1, 31), 2025, "Q4"), (date(2026, 1, 31), 2026, "Q4")]


def test_the_release_calendar_labels_like_ingest():
    cal = pr.FiscalCalendar(companyfacts(*REUSED))
    assert cal.fiscal(date(2026, 1, 31), "annual") == (2026, "FY")
    assert cal.fiscal(date(2025, 1, 31), "annual") == (2025, "FY")
    assert cal.fiscal(date(2025, 10, 31), "quarter") == (2026, "Q3")
    assert any("relabelled" in w for w in cal.warnings)
    assert "FY2026 Q4 and full year ended 2026-01-31" in cal.context_lines(None)


def test_the_stored_calendar_wins_and_the_rules_cover_the_rest():
    stored = [(2027, "Q1", date(2026, 2, 1), date(2026, 4, 30))]  # what ingest stored for a later quarter
    cal = pr.FiscalCalendar(companyfacts(*REUSED[:2]), stored=stored)
    assert cal.fiscal(date(2026, 4, 30), "quarter") == (2027, "Q1")      # from the store
    assert cal.fiscal(date(2025, 4, 30), "quarter") == (2026, "Q1")      # from the filings, by the shared rules
    assert pr.FiscalCalendar(None, stored=[(2026, "FY", date(2025, 2, 1), date(2026, 1, 31))]).fiscal(
        date(2026, 1, 31), "quarter") == (2026, "Q4")


def test_a_label_the_rules_dropped_is_never_inferred_back():
    """A 10-Q tagged Q1 whose period is the fiscal year's second quarter loses its label; the release calendar must
    not infer one for it, but still infers the quarter no filing covers yet (earnings day)."""
    facts = (REUSED[0],  # FY2025 10-K, year ending 2025-01-31
             _fact("NetIncomeLoss", "2025-05-01", "2025-07-31", 1.0, 2026, "Q1", "10-Q", "0000000001-25-000020", "2025-09-01"))
    cal = pr.FiscalCalendar(companyfacts(*facts))
    assert any("label dropped" in w for w in cal.warnings)
    assert cal.fiscal(date(2025, 7, 31), "quarter") == (None, None)       # covered by a 10-Q, rejected: stays unlabelled
    assert cal.fiscal(date(2025, 4, 30), "quarter") == (2026, "Q1")       # no filing covers it: inferred from FY2025
