"""receipts.fiscal_labels: DEI fiscal labels checked against each company's convention (review finding 1)."""
from datetime import date

from receipts.fiscal_labels import FilingPeriod, Label, add_q4_and_check, gate_message, label_periods
from receipts.ingest import build_company


def d(s: str) -> date:
    return date.fromisoformat(s)


def k(n: int, start: str, end: str, fy) -> FilingPeriod:
    return FilingPeriod(f"K{n}", "10-K", end, d(start), d(end), fy, "FY")


def q(n: int, start: str, end: str, fy, fp) -> FilingPeriod:
    return FilingPeriod(f"Q{n}", "10-Q", end, d(start), d(end), fy, fp)


def labels(periods, warnings=None):
    warnings = [] if warnings is None else warnings
    return {(lab.fiscal_year, lab.fiscal_period): (lab.start, lab.end) for lab in label_periods(periods, warnings)}


# ------------------------------------------------------------------------------------------- conventions
def test_oracle_end_year_convention_relabels_a_reused_quarter_label():
    warnings = []
    got = labels([k(1, "2024-06-01", "2025-05-31", 2025), k(2, "2025-06-01", "2026-05-31", 2026),
                  q(1, "2025-06-01", "2025-08-31", 2026, "Q1"),
                  q(2, "2026-06-01", "2026-08-31", 2026, "Q1")], warnings)  # Q1 FY2027, tagged FY2026 again
    assert got[(2026, "Q1")] == (d("2025-06-01"), d("2025-08-31"))
    assert got[(2027, "Q1")] == (d("2026-06-01"), d("2026-08-31"))
    assert [w for w in warnings if "relabelled" in w] == [
        "calendar: Q2 tags Q1 FY2026 for 2026-06-01..2026-08-31; by the company's convention that period is "
        "Q1 FY2027: relabelled"]


def test_salesforce_reused_ten_k_label_is_relabelled():
    warnings = []
    got = labels([k(1, "2023-02-01", "2024-01-31", 2024), k(2, "2024-02-01", "2025-01-31", 2025),
                  k(3, "2025-02-01", "2026-01-31", 2025)], warnings)  # the FY2026 10-K is tagged FY2025
    assert got[(2025, "FY")] == (d("2024-02-01"), d("2025-01-31"))
    assert got[(2026, "FY")] == (d("2025-02-01"), d("2026-01-31"))
    assert sum("relabelled" in w for w in warnings) == 1


def test_home_depot_and_target_start_year_conventions_are_kept():
    for periods in ([k(1, "2024-01-29", "2025-02-02", 2024), k(2, "2025-02-03", "2026-02-01", 2025),  # HD
                     q(1, "2025-02-03", "2025-05-04", 2025, "Q1"), q(2, "2026-02-02", "2026-05-03", 2026, "Q1")],
                    [k(1, "2024-02-04", "2025-02-01", 2024), k(2, "2025-02-02", "2026-01-31", 2025),  # TGT
                     q(1, "2025-08-03", "2025-11-01", 2025, "Q3")]):
        warnings = []
        got = labels(periods, warnings)
        assert warnings == []
        assert {fy for fy, fp in got if fp == "FY"} == {2024, 2025}
    assert labels([k(1, "2024-02-04", "2025-02-01", 2024), k(2, "2025-02-02", "2026-01-31", 2025),
                   q(1, "2025-08-03", "2025-11-01", 2025, "Q3")])[(2025, "Q3")] == (d("2025-08-03"), d("2025-11-01"))


def test_nvidia_end_year_convention_is_kept():
    warnings = []
    got = labels([k(1, "2024-01-29", "2025-01-26", 2025), k(2, "2025-01-27", "2026-01-25", 2026),
                  q(1, "2026-01-26", "2026-04-26", 2027, "Q1"), q(2, "2025-07-28", "2025-10-26", 2026, "Q3")],
                 warnings)
    assert warnings == []
    assert got[(2027, "Q1")] == (d("2026-01-26"), d("2026-04-26"))
    assert got[(2026, "Q4")] == (d("2025-10-27"), d("2026-01-25"))


def test_a_52_53_week_year_ending_in_early_january_keeps_its_label():
    warnings = []
    got = labels([k(1, "2023-12-31", "2024-12-28", 2024), k(2, "2024-12-29", "2026-01-03", 2025),
                  q(1, "2026-01-04", "2026-04-04", 2026, "Q1")], warnings)
    assert warnings == []
    assert got[(2025, "FY")] == (d("2024-12-29"), d("2026-01-03"))
    assert got[(2026, "Q1")] == (d("2026-01-04"), d("2026-04-04"))


def test_a_52_53_week_year_ending_around_july_first_keeps_its_label():
    """Seagate-style: year(end - 182 days) would call these offsets 1 and 0 and relabel FY2026 as FY2027."""
    warnings = []
    got = labels([k(1, "2023-07-01", "2024-06-28", 2024), k(2, "2024-06-29", "2025-06-27", 2025),
                  k(3, "2025-06-28", "2026-07-03", 2026)], warnings)
    assert warnings == []
    assert got[(2026, "FY")] == (d("2025-06-28"), d("2026-07-03"))


# ------------------------------------------------------------------------------------------- mis-tags
def test_a_ten_q_tagged_fy_repeated_a_year_later_never_takes_the_ten_ks_label():
    warnings = []
    got = labels([q(1, "2024-02-03", "2024-05-03", 2025, "FY"),   # Q1 FY2025 tagged fp=FY
                  k(1, "2024-02-03", "2025-01-31", 2025),
                  q(2, "2025-02-01", "2025-05-02", 2025, "FY"),   # Q1 FY2026 tagged with last year's fy and fp=FY
                  q(3, "2025-05-03", "2025-08-01", 2026, "Q2"),
                  k(2, "2025-02-01", "2026-01-30", 2026)], warnings)
    assert got[(2025, "FY")] == (d("2024-02-03"), d("2025-01-31"))
    assert got[(2026, "FY")] == (d("2025-02-01"), d("2026-01-30"))
    assert all(end not in (d("2024-05-03"), d("2025-05-02")) for _, end in got.values())
    assert sum("ignored DEI fp=FY on 10-Q" in w for w in warnings) == 2


def test_a_premature_label_does_not_cascade():
    warnings = []
    got = labels([k(1, "2023-01-01", "2023-12-31", 2023), k(2, "2024-01-01", "2024-12-31", 2024),
                  q(1, "2024-01-01", "2024-03-31", 2025, "Q1"),   # a year early
                  q(2, "2025-01-01", "2025-03-31", 2025, "Q1"),   # right
                  q(3, "2026-01-01", "2026-03-31", 2026, "Q1")], warnings)  # right
    assert got[(2024, "Q1")] == (d("2024-01-01"), d("2024-03-31"))
    assert got[(2025, "Q1")] == (d("2025-01-01"), d("2025-03-31"))
    assert got[(2026, "Q1")] == (d("2026-01-01"), d("2026-03-31"))
    assert sum("relabelled" in w for w in warnings) == 1


def test_a_q4_that_would_end_before_it_starts_is_dropped_with_its_fiscal_year_and_stray_quarter():
    warnings = []
    labs = {(2024, "FY"): Label(2024, "FY", d("2023-02-04"), d("2023-05-05"), "Q1"),   # Dell's Q1 under FY
            (2024, "Q3"): Label(2024, "Q3", d("2023-08-05"), d("2023-11-03"), "Q3")}
    add_q4_and_check(labs, warnings)
    assert labs == {}  # the Q3 lies outside the year that claims it, so it goes too
    assert any("Q4 FY2024 would run 2023-11-04..2023-05-05" in w for w in warnings)
    assert any("FY2024" in w and "FY and Q4 labels dropped (Q3, FY)" in w for w in warnings)


def test_a_fiscal_year_end_change_never_leaves_a_stale_q1_labelled():
    """June year ends, then a move to December: the abandoned fiscal year's Jul-Sep 10-Q and the new year's Jan-Mar
    10-Q are both tagged Q1 FY2026. The second is out of place against the old year end, yet it still knocks out
    the shared label, so neither period is Q1 FY2026."""
    warnings = []
    got = labels([k(1, "2023-07-01", "2024-06-30", 2024), k(2, "2024-07-01", "2025-06-30", 2025),
                  q(1, "2025-07-01", "2025-09-30", 2026, "Q1"),
                  q(2, "2026-01-01", "2026-03-31", 2026, "Q1")], warnings)
    assert (2026, "Q1") not in got
    assert all(end not in (d("2025-09-30"), d("2026-03-31")) for _, end in got.values())
    assert any("Q1 FY2026 is claimed for different periods" in w for w in warnings)


def test_an_implausible_naming_offset_drops_every_label():
    for periods in ([k(1, "2024-01-01", "2024-12-31", 2025), k(2, "2025-01-01", "2025-12-31", 2026)],   # +1
                    [k(1, "2024-01-01", "2024-12-31", 2023), k(2, "2025-01-01", "2025-12-31", 2024)]):  # -1 in December
        warnings = []
        assert labels(periods, warnings) == {}
        assert any("all labels dropped" in w for w in warnings)


def test_crowdstrikes_two_slipped_ten_ks_lose_to_its_ten_qs():
    """CrowdStrike tagged its FY2024 and FY2025 10-Ks a year early; its FY2026 10-K and its 10-Qs name each year for
    the calendar year it ends in. The 10-K majority (-1) would shift every label a year early."""
    warnings = []
    got = labels([k(1, "2023-02-01", "2024-01-31", 2023), k(2, "2024-02-01", "2025-01-31", 2024),
                  k(3, "2025-02-01", "2026-01-31", 2026),
                  q(1, "2023-02-01", "2023-04-30", 2024, "Q1"), q(2, "2023-05-01", "2023-07-31", 2024, "Q2"),
                  q(3, "2024-02-01", "2024-04-30", 2025, "Q1"), q(4, "2024-05-01", "2024-07-31", 2025, "Q2"),
                  q(5, "2025-05-01", "2025-07-31", 2026, "Q2"), q(6, "2026-02-01", "2026-04-30", 2027, "Q1")],
                 warnings)
    assert got[(2024, "FY")] == (d("2023-02-01"), d("2024-01-31"))
    assert got[(2026, "FY")] == (d("2025-02-01"), d("2026-01-31"))
    assert got[(2027, "Q1")] == (d("2026-02-01"), d("2026-04-30"))
    assert (2023, "FY") not in got
    assert [w for w in warnings if gate_message(w)] == [
        "calendar: the 10-Ks name fiscal years with offset -1 (2 of 3) and the 10-Qs with +0 (6 of 6); 7 of 9 "
        "filings agree on +0: naming convention overridden"]
    assert sum("relabelled" in w for w in warnings) == 2  # the two slipped 10-Ks


def test_krogers_two_slipped_ten_ks_lose_to_its_ten_qs():
    """Kroger names each year for the calendar year it starts in (-1), as its 10-Qs do; two of its three 10-Ks are
    tagged a year late, and the 10-K majority (0) would shift every label a year late."""
    warnings = []
    got = labels([k(1, "2023-01-29", "2024-02-03", 2024), k(2, "2024-02-04", "2025-02-01", 2025),
                  k(3, "2025-02-02", "2026-01-31", 2025),
                  q(1, "2023-05-21", "2023-08-12", 2023, "Q2"), q(2, "2023-08-13", "2023-11-04", 2023, "Q3"),
                  q(3, "2024-05-26", "2024-08-17", 2024, "Q2"), q(4, "2024-08-18", "2024-11-09", 2024, "Q3"),
                  q(5, "2025-05-25", "2025-08-16", 2025, "Q2"), q(6, "2025-08-17", "2025-11-08", 2025, "Q3"),
                  q(7, "2026-05-24", "2026-08-15", 2026, "Q2")], warnings)
    assert got[(2023, "FY")] == (d("2023-01-29"), d("2024-02-03"))
    assert got[(2025, "FY")] == (d("2025-02-02"), d("2026-01-31"))
    assert got[(2025, "Q4")] == (d("2025-11-09"), d("2026-01-31"))
    assert got[(2026, "Q2")] == (d("2026-05-24"), d("2026-08-15"))
    assert (2026, "FY") not in got
    assert [w for w in warnings if gate_message(w)] == [
        "calendar: the 10-Ks name fiscal years with offset +0 (2 of 3) and the 10-Qs with -1 (7 of 7); 8 of 10 "
        "filings agree on -1: naming convention overridden"]


def test_ten_ks_and_ten_qs_that_disagree_without_a_two_thirds_majority_drop_every_label():
    """FedEx Freight's one 10-K names the May year it ends in, its one 10-Q the new calendar year; the second company
    splits 3 to 4. No offset has two thirds of the filings, so no label is trusted and claims answer "?"."""
    for periods, n in (([k(1, "2025-06-01", "2026-05-31", 2026), q(1, "2026-07-01", "2026-09-30", 2026, "Q3")], 2),
                       ([k(1, "2023-02-01", "2024-01-31", 2024), k(2, "2024-02-01", "2025-01-31", 2025),
                         k(3, "2025-02-01", "2026-01-31", 2025),
                         q(1, "2024-02-01", "2024-04-30", 2024, "Q1"), q(2, "2024-05-01", "2024-07-31", 2024, "Q2"),
                         q(3, "2025-02-01", "2025-04-30", 2025, "Q1"),
                         q(4, "2025-05-01", "2025-07-31", 2026, "Q2")], 7)):
        warnings = []
        assert labels(periods, warnings) == {}
        (gate,) = [w for w in warnings if gate_message(w)]
        assert gate.endswith(f"no offset has two thirds of the {n} filings: all labels dropped")


def test_a_quarter_whose_dei_period_disagrees_with_its_position_loses_its_label():
    warnings = []
    got = labels([k(1, "2024-01-01", "2024-12-31", 2024), q(1, "2025-04-01", "2025-06-30", 2025, "Q1")], warnings)
    assert (2025, "Q1") not in got and any("quarter 2 of its fiscal year: label dropped" in w for w in warnings)


# ------------------------------------------------------------------------------------------- through ingest
FILINGS = {  # key -> (accession, form, filed, accepted, report date, DEI fy, DEI fp): Dell's fp=FY 10-Q in the window
    "Q1": ("0000000901-25-000001", "10-Q", "2025-06-05", "2025-06-05T20:00:00.000Z", "2025-05-02", 2026, "FY"),
    "Q2": ("0000000901-25-000002", "10-Q", "2025-09-05", "2025-09-05T20:00:00.000Z", "2025-08-01", 2026, "Q2"),
    "Q3": ("0000000901-25-000003", "10-Q", "2025-12-05", "2025-12-05T20:00:00.000Z", "2025-10-31", 2026, "Q3"),
    "K": ("0000000901-26-000001", "10-K", "2026-03-20", "2026-03-20T20:00:00.000Z", "2026-01-30", 2026, "FY"),
}


def test_a_ten_q_tagged_fy_never_stores_q1_figures_under_q4():
    usgaap: dict = {}
    for concept, (start, end), value, key in (
            ("Revenues", ("2025-02-01", "2025-05-02"), 101e6, "Q1"), ("Revenues", ("2025-05-03", "2025-08-01"), 102e6, "Q2"),
            ("Revenues", ("2025-08-02", "2025-10-31"), 103e6, "Q3"), ("Revenues", ("2025-02-01", "2025-10-31"), 306e6, "Q3"),
            ("Revenues", ("2025-02-01", "2026-01-30"), 410e6, "K")):
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    b = build_company(cik=901, ticker="DLLX", names=["Dell-like Corp"], fiscal_year_end="0130", filing_rows=rows,
                      companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2024, 1, 1), today=date(2026, 10, 1))
    periods = {(fy, fp): (start, end) for fy, fp, start, end, _ in b.periods}
    assert periods[(2026, "FY")] == (d("2025-02-01"), d("2026-01-30"))
    assert periods[(2026, "Q4")] == (d("2025-11-01"), d("2026-01-30"))
    labelled = {(f.period_end, f.fiscal_year, f.fiscal_period) for f, kind in b.facts if f.metric == "revenue"}
    assert (d("2025-05-02"), None, None) in labelled            # Q1 is unlabelled ("?"), never Q4 or FY
    assert (d("2026-01-30"), 2026, "Q4") in labelled and (d("2026-01-30"), 2026, "FY") in labelled
    assert [w for w in b.warnings if gate_message(w)] == []  # only the ignored fp=FY tag, which drops nothing
