"""Synthetic regressions for reconciliation captions leaking into EPS row basis.

The two captions/labels reproduce the shapes in IVZ and PWR's archived releases;
the compact tables and their mapper responses below are synthetic, offline fixtures.
"""
from datetime import date, datetime, timezone

import pytest

from receipts import press_release as pr


def scenario(section, gaap_label="Diluted EPS", *, column_basis="row"):
    html = f"""<p>Reconciliation of net income to adjusted net income</p>
    <table><tr><th>Metric</th><th>Three Months Ended June 30, 2026</th>
    <th>Three Months Ended June 30, 2025</th></tr>
    <tr><td>Net income</td><td>100</td><td>90</td></tr>
    <tr><td>{section}</td></tr>
    <tr><td>{gaap_label}</td><td>0.51</td><td>0.38</td></tr>
    <tr><td>Adjusted diluted EPS</td><td>0.57</td><td>0.44</td></tr>
    <tr><td>Adjusted basic EPS</td><td>0.59</td><td>0.46</td></tr>
    <tr><td>EPS impact of tax adjustments</td><td>0.06</td><td>0.06</td></tr>
    <tr><td>Dividend per share</td><td>0.20</td><td>0.15</td></tr>
    </table>"""
    table, = pr.parse_tables(html)
    stored = [(y, "Q2", date(y, 4, 1), date(y, 6, 30)) for y in (2025, 2026)]
    ctx = pr.FilingContext(ticker="EXMP", cik=1, company="Synthetic EPS example",
                          accession="0000000001-26-000019",
                          accepted_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
                          url="https://example.invalid/eps.htm", exhibit="EX-99.1",
                          calendar=pr.FiscalCalendar(stored=stored))
    columns = [pr.ColumnMap(col=i, use=True, period_end=f"{y}-06-30", duration="quarter",
                            basis=column_basis) for i, y in enumerate((2026, 2025))]
    rows = [pr.RowMap(row=i, metric=metric, basis=basis, segment="", values=values)
            for i, metric, basis, values in (
                (0, "skip", "GAAP", [None, None]),
                (1, "eps_diluted", "GAAP", [0.51, 0.38]),
                (2, "eps_diluted", "non-GAAP", [0.57, 0.44]),
                (3, "eps_basic", "non-GAAP", [0.59, 0.46]),
                (4, "skip", "non-GAAP", [None, None]),
                (5, "dividend_per_share", "GAAP", [0.20, 0.15]),
            )]
    mapping = pr.TableMap(table=table.index, scale="units", columns=columns, rows=rows)
    return table, mapping, ctx


@pytest.mark.parametrize("section,gaap_label", [
    ("Adjustments (excluding tax):", "Diluted EPS"),
    ("Reconciliation of adjusted diluted earnings per share:",
     "Diluted earnings per share attributable to common stock (GAAP as reported)"),
])
def test_reconciliation_caption_preserves_both_gaap_and_adjusted_eps(section, gaap_label):
    table, mapping, ctx = scenario(section, gaap_label)
    request_before = pr.build_request("Synthetic context", [table], effort="minimal", max_output_tokens=1000)
    facts, rejected = pr.decide_table(table, mapping, ctx)
    kept, conflicts = pr.dedupe(facts)
    assert not rejected and not conflicts
    eps = [f for f in kept if f.metric == "eps_diluted"]
    assert {(f.basis, f.period_end, f.value) for f in eps} == {
        ("GAAP", date(2026, 6, 30), 0.51), ("GAAP", date(2025, 6, 30), 0.38),
        ("non-GAAP", date(2026, 6, 30), 0.57), ("non-GAAP", date(2025, 6, 30), 0.44),
    }
    # Basic EPS, an adjustment component and a dividend never enter adjusted diluted EPS.
    assert {f.value for f in eps if f.basis == "non-GAAP"} == {0.57, 0.44}
    assert all(f.metric == "eps_basic" for f in kept if f.value in (0.59, 0.46))
    assert all(f.metric == "dividend_per_share" for f in kept if f.value in (0.20, 0.15))
    assert not any(f.value == 0.06 for f in kept)
    assert request_before == pr.build_request("Synthetic context", [table], effort="minimal",
                                              max_output_tokens=1000)


@pytest.mark.parametrize("section", ["Non-GAAP results", "Adjusted diluted EPS", "Core earnings"])
def test_genuine_nongaap_section_still_marks_unqualified_rows(section):
    table, mapping, ctx = scenario(section)
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    assert {f.basis for f in facts if f.locator.startswith("Diluted EPS |")} == {"non-GAAP"}


def test_explicit_nongaap_column_remains_authoritative_in_reconciliation():
    table, mapping, ctx = scenario("Reconciliation of adjusted EPS", column_basis="non-GAAP")
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    assert {f.basis for f in facts if f.metric == "eps_diluted"} == {"non-GAAP"}


def test_prior_year_column_cannot_supply_current_adjusted_eps():
    table, mapping, ctx = scenario("Adjustments (excluding tax):")
    mapping.columns[1].period_end = "2026-06-30"
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert [(r["reason"], r["col"]) for r in rejected] == [("period_end_not_in_header", 1)]
    assert [(f.period_end, f.value) for f in facts
            if f.metric == "eps_diluted" and f.basis == "non-GAAP"] == [(date(2026, 6, 30), 0.57)]


def test_real_adjusted_eps_disagreement_still_drops_both_values():
    table, mapping, ctx = scenario("Reconciliation of adjusted EPS")
    # The caption fix must not weaken the same-period disagreement gate.
    mapping.rows[1].basis = "non-GAAP"
    facts, rejected = pr.decide_table(table, mapping, ctx)
    kept, conflicts = pr.dedupe(facts)
    assert not rejected
    assert len(conflicts) == 2
    assert not any(f.metric == "eps_diluted" for f in kept)


@pytest.mark.parametrize("section", [
    "Adjusted earnings excluding tax adjustments",
    "Non-GAAP results after tax adjustments",
])
def test_adjustments_word_does_not_disable_genuine_adjusted_section(section):
    table, mapping, ctx = scenario(section)
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    assert {f.basis for f in facts if f.locator.startswith("Diluted EPS |")} == {"non-GAAP"}


def test_explicit_adjusted_row_remains_authoritative_over_gaap_mapping():
    table, mapping, ctx = scenario("Adjustments (excluding tax):", column_basis="GAAP")
    mapping.rows[2].basis = "GAAP"
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    assert {(f.basis, f.value) for f in facts if f.locator.startswith("Adjusted diluted EPS |")} == {
        ("non-GAAP", 0.57), ("non-GAAP", 0.44),
    }


@pytest.mark.parametrize("header,reason", [
    ("EPS impact", "change_or_adjustment_column"),
    ("Adjustments", "change_or_adjustment_column"),
    ("Forecast", "forward_looking_column"),
])
def test_adjustment_or_forecast_column_cannot_supply_adjusted_eps(header, reason):
    table, mapping, ctx = scenario("Reconciliation of adjusted EPS")
    # The mapper incorrectly says this is a reported column. Printed headers win.
    table.columns[0].append(header)
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert [(r["reason"], r["col"]) for r in rejected] == [(reason, 0)]
    assert not any(f.period_end == date(2026, 6, 30) for f in facts)
    assert [(f.value, f.period_end) for f in facts
            if f.metric == "eps_diluted" and f.basis == "non-GAAP"] == [(0.44, date(2025, 6, 30))]


def test_caption_fix_does_not_bypass_the_printed_cell_match():
    table, mapping, ctx = scenario("Adjustments (excluding tax):")
    mapping.rows[2].values[0] = 0.58
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert [(r["reason"], r["row"], r["col"]) for r in rejected] == [("value_mismatch", 2, 0)]
    assert not any(f.metric == "eps_diluted" and f.basis == "non-GAAP"
                   and f.period_end == date(2026, 6, 30) for f in facts)


def test_caption_fix_never_promotes_unmapped_or_skipped_rows():
    table, mapping, ctx = scenario("Reconciliation of adjusted diluted earnings per share:")
    mapping.rows[2].metric = "skip"
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    assert not any(f.metric == "eps_diluted" and f.basis == "non-GAAP" for f in facts)
    assert not any(f.value == 0.06 for f in facts)


@pytest.mark.parametrize("metric,label", [
    ("eps_basic", "Basic EPS"),
    ("dividend_per_share", "Dividend per share"),
])
def test_caption_change_is_scoped_to_diluted_eps(metric, label):
    table, mapping, ctx = scenario("Reconciliation of adjusted EPS")
    table.rows[1].label = label
    mapping.rows[1].metric = metric
    facts, rejected = pr.decide_table(table, mapping, ctx)
    assert not rejected
    # This patch leaves the pre-existing basis rules for every other metric intact.
    assert {(f.metric, f.basis, f.value) for f in facts if f.value in (0.51, 0.38)} == {
        (metric, "non-GAAP", 0.51), (metric, "non-GAAP", 0.38),
    }
    assert {f.value for f in facts if f.metric == "eps_diluted"} == {0.57, 0.44}
