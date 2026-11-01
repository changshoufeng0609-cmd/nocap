"""receipts.press_release.xbrl_gate compares a release figure with the figure the store serves (store_values) as well
as with the paired filing's first tagged concept: a figure equal to receipts.ingest's choice is kept, one that equals
neither is still dropped."""
import sqlite3
from datetime import date, datetime, timezone

from receipts import press_release as pr
from receipts.types import Fact

TENQ = "0000000907-26-000059"
Q2_26, Q2_25 = (date(2026, 4, 1), date(2026, 6, 30)), (date(2025, 4, 1), date(2025, 6, 30))


def release(metric: str, period, value: float, unit: str = "USD") -> Fact:
    return Fact(cik=907, ticker="EXGT", metric=metric, basis="GAAP", value=value, unit=unit, period_start=period[0],
                period_end=period[1], source="press_release", form="8-K", accession="0000000907-26-000055",
                accepted_at=datetime(2026, 7, 29, tzinfo=timezone.utc), url="https://example.invalid/ex99.htm",
                locator=f"{metric} | {value}", decimals=-6)


def gaap(*entries) -> dict:
    out: dict = {}
    for concept, period, value in entries:
        out.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": period[0].isoformat(), "end": period[1].isoformat(), "val": value, "accn": TENQ, "fy": 2026,
             "fp": "Q2", "form": "10-Q", "filed": "2026-07-30"})
    return out


def gate(facts, xbrl, served=None) -> pr.GateResult:
    job = pr.FilingJob(ticker="EXGT", cik=907, company="Example", eightk={"accessionNumber": "0000000907-26-000055"},
                       tenq={"accessionNumber": TENQ}, facts=list(facts))
    return pr.xbrl_gate(job, xbrl, None, served)


def test_attributable_net_income_the_store_derives_from_profitloss_less_the_nci_is_kept():
    """AEP: its Q2 2026 10-Q tags ProfitLoss ($1,289M for Q2 2025) and no NetIncomeLoss; the store serves ProfitLoss
    less the noncontrolling interest, $1,226M, the release's figure."""
    facts = [release("net_income", Q2_25, 1226e6)]
    xbrl = gaap(("ProfitLoss", Q2_25, 1289e6))
    assert [d["press_release"] for d in gate(facts, xbrl).dropped] == [1226e6]  # the paired concept alone
    assert gate(facts, xbrl, {("net_income", *Q2_25): [(1226e6, -6)]}).dropped == []


def test_a_comparison_total_the_releases_confirm_is_kept_and_does_not_quarantine_the_file():
    """Sempra and Duke: the 10-Q tags contract revenue first ($2,799M), the store serves the confirmed
    RegulatedAndUnregulatedOperatingRevenue ($2,997M); two such rows in one column would quarantine the file."""
    facts = [release("revenue", Q2_26, 2997e6), release("revenue", Q2_25, 3000e6),
             release("operating_expenses", Q2_25, 2500e6)]
    xbrl = gaap(("RevenueFromContractWithCustomerExcludingAssessedTax", Q2_26, 2799e6),
                ("RevenueFromContractWithCustomerExcludingAssessedTax", Q2_25, 2700e6),
                ("CostsAndExpenses", Q2_25, 2600e6))
    assert gate(facts, xbrl).quarantine is not None  # 2 unexplained mismatches in the Q2 2025 column
    served = {("revenue", *Q2_26): [(2997e6, -6)], ("revenue", *Q2_25): [(3000e6, -6)]}
    g = gate(facts, xbrl, served)
    assert g.quarantine is None and [(d["metric"], d["press_release"]) for d in g.dropped] == [
        ("operating_expenses", 2500e6)]  # equal to neither: still dropped


def test_net_income_after_preferred_dividends_and_a_banks_summed_revenue_are_kept():
    """Invesco's -$12.5M is its available-to-common figure (ProfitLoss $197.4M); Regions' $1,905M is net interest
    income plus noninterest income, while its 10-Q tags a smaller contract revenue."""
    facts = [release("net_income", Q2_25, -12.5e6), release("revenue", Q2_25, 1905e6)]
    xbrl = gaap(("ProfitLoss", Q2_25, 197.4e6), ("RevenueFromContractWithCustomerExcludingAssessedTax", Q2_25, 612e6))
    served = {("net_income", *Q2_25): [(-12.5e6, -5)], ("revenue", *Q2_25): [(1905e6, -6)]}
    assert gate(facts, xbrl, served).findings == []
    assert len(gate(facts, xbrl).findings) == 2


def test_a_figure_off_from_both_the_filing_and_the_store_is_dropped():
    facts = [release("net_income", Q2_25, 1500e6)]
    g = gate(facts, gaap(("ProfitLoss", Q2_25, 1289e6)), {("net_income", *Q2_25): [(1226e6, -6)]})
    assert [(d["press_release"], d["xbrl"], d["cause"]) for d in g.dropped] == [
        (1500e6, 1289e6, "value or definition differs")]


def test_store_values_reads_the_company_level_figures_the_store_serves(tmp_path):
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE facts (cik INT, metric TEXT, segment TEXT, source TEXT, period_start TEXT, "
                "period_end TEXT, value REAL, decimals INT)")
    con.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (907, "net_income", None, "derived", "2025-04-01", "2025-06-30", 1226e6, -6),
        (907, "net_income", None, "press_release", "2025-04-01", "2025-06-30", 1.0, -6),   # not the store's XBRL
        (907, "revenue", "Cloud", "xbrl", "2025-04-01", "2025-06-30", 2.0, -6),           # a segment
        (908, "net_income", None, "xbrl", "2025-04-01", "2025-06-30", 3.0, -6)])          # another company
    con.commit()
    con.close()
    assert pr.store_values(907, db) == {("net_income", *Q2_25): [(1226e6, -6)]}
    assert pr.store_values(907, tmp_path / "missing.sqlite") == {}
