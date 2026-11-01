"""receipts.ingest._drop_component_revenue: contract revenue stands for revenue only when it can be the total
(review finding 2). SYNTHETIC filings for a fictional company."""
from datetime import date

from receipts.ingest import AWAITING, build_company

RFC = "RevenueFromContractWithCustomerExcludingAssessedTax"
FILINGS = {  # key -> (accession, form, filed, accepted, report date, DEI fy, DEI fp)
    "Q1": ("0000000902-25-000001", "10-Q", "2025-04-30", "2025-04-30T20:00:00.000Z", "2025-03-31", 2025, "Q1"),
    "Q2": ("0000000902-25-000002", "10-Q", "2025-07-30", "2025-07-30T20:00:00.000Z", "2025-06-30", 2025, "Q2"),
}
Q1, Q2 = ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30")


def build(*entries, sic=None, confirmed=False, contract_total=False, broad_total=None):
    usgaap: dict = {}
    for concept, (start, end), value, key in entries:
        a, form, filed, _, _, fy, fp = FILINGS[key]
        usgaap.setdefault(concept, {"units": {}})["units"].setdefault("USD", []).append(
            {"start": start, "end": end, "val": value, "accn": a, "fy": fy, "fp": fp, "form": form, "filed": filed})
    rows = [{"accessionNumber": a, "form": form, "filingDate": filed, "acceptanceDateTime": at, "reportDate": report,
             "primaryDocument": "doc.htm", "items": ""} for a, form, filed, at, report, _, _ in FILINGS.values()]
    return build_company(cik=902, ticker="EXMP", names=["Example Corp"], fiscal_year_end="1231", filing_rows=rows,
                         companyfacts={"facts": {"us-gaap": usgaap}}, facts_start=date(2024, 1, 1),
                         today=date(2026, 10, 1), sic=sic, revenue_confirmed=confirmed,
                         contract_revenue_total=contract_total, broad_revenue_total=broad_total)


def revenue(b):
    return {(f.period_end, f.concept, f.value) for f, kind in b.facts if f.metric == "revenue" and kind == "quarter"}


def test_a_bank_is_served_its_revenue_net_of_interest_expense_not_its_fee_income():
    b = build(("RevenuesNetOfInterestExpense", Q1, 17.0e9, "Q1"), (RFC, Q1, 9.9e9, "Q1"),
              ("NoninterestExpense", Q1, 11.0e9, "Q1"),            # fee income is 0.9x noninterest expense
              ("InterestIncomeExpenseNet", Q1, 4.0e9, "Q1"), sic="6021")
    assert revenue(b) == {(date(2025, 3, 31), "us-gaap:RevenuesNetOfInterestExpense", 17.0e9)}
    (line,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert line["served"] == "RevenuesNetOfInterestExpense"
    (gone,) = line["dropped"]
    assert gone["concept"] == RFC and len(gone["conditions"]) == 3
    assert "RevenuesNetOfInterestExpense 17,000,000,000 is larger" in gone["conditions"]
    assert "a bank's contract revenue is only its in-scope fee income" in gone["conditions"]
    assert not any(w.startswith("revenue:") for w in b.warnings)  # served RevenuesNetOfInterestExpense: no flag


def test_a_true_total_survives_a_quarter_whose_costs_are_2_4_times_revenue():
    b = build((RFC, Q1, 2.65e9, "Q1"), ("CostsAndExpenses", Q1, 6.36e9, "Q1"))  # a $4.6B IPR&D quarter
    assert revenue(b) == {(date(2025, 3, 31), f"us-gaap:{RFC}", 2.65e9)}
    assert b.stats["component_revenue_skipped"] == 0


def test_a_lessors_contract_revenue_is_dropped_and_a_financial_filer_is_flagged_and_blocked():
    entries = ((RFC, Q1, 1.0e6, "Q1"), ("OperatingLeaseLeaseIncome", Q1, 140e6, "Q1"),   # a REIT's non-lease income
               (RFC, Q2, 160e6, "Q2"))                                                   # Q2 tags no lease income
    b = build(*entries, sic="6798")
    (line,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert line["served"] is None and "operating-lease income 140,000,000 is at least 1%" in line["dropped"][0]["conditions"]
    assert any(w.startswith("revenue: SIC 6798 (financial or real estate)") for w in b.warnings)
    assert revenue(b) == set()  # flagged and not yet confirmed: Q2's contract revenue is held back too
    (q2,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-06-30"]
    assert q2["served"] is None and q2["dropped"][0]["conditions"] == [AWAITING]
    assert any("blocked until confirmed" in w for w in b.warnings)
    assert revenue(build(*entries, sic="6798", confirmed=True)) == {(date(2025, 6, 30), f"us-gaap:{RFC}", 160e6)}


def test_baxters_shape_keeps_its_contract_revenue():
    """Net sales tagged as contract revenue with the leases inside: lease income rules out nothing outside real
    estate (Baxter, Q3 2026: $2,976M of net sales, $88M of lease income)."""
    b = build((RFC, Q1, 2976e6, "Q1"), ("OperatingLeaseLeaseIncome", Q1, 88e6, "Q1"),
              ("CostOfGoodsAndServicesSold", Q1, 1982e6, "Q1"), sic="3841")
    assert revenue(b) == {(date(2025, 3, 31), f"us-gaap:{RFC}", 2976e6)}
    (line,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert line["dropped"] == [] and "warning" not in line
    assert not any(w.startswith("revenue:") for w in b.warnings)


def test_a_non_real_estate_lessor_with_a_review_note_is_served_only_once_confirmed():
    entries = ((RFC, Q1, 1000e6, "Q1"), ("OperatingLeaseLeaseIncome", Q1, 150e6, "Q1"))
    b = build(*entries, sic="7359")
    assert revenue(b) == set()
    assert any("operating-lease income is at least 10% of the served contract revenue in 1" in w for w in b.warnings)
    b = build(*entries, sic="7359", confirmed=True)
    assert revenue(b) == {(date(2025, 3, 31), f"us-gaap:{RFC}", 1000e6)}
    (line,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert line["warning"].startswith("operating-lease income 150,000,000 is at least 10%")


def test_non_positive_contract_revenue_is_never_served():
    b = build((RFC, Q1, 0.0, "Q1"), ("OperatingExpenses", Q1, 50e6, "Q1"))
    assert revenue(b) == set()
    (line,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert "not positive" in line["dropped"][0]["conditions"]


def test_invitation_homes_zero_contract_revenue_with_no_lease_income_tagged_is_dropped_without_a_crash():
    """INVH's Q1 2026 10-Q tags $0 of contract revenue for Q1 2025 and no lease income for that column, while other
    periods carry lease income. The REIT lease test once read the missing entry and stopped the ingest (KeyError);
    a figure that is not positive is now dropped on that alone, with no ratio test against it."""
    b = build((RFC, Q1, 0.0, "Q1"), ("Revenues", Q1, 674.479e6, "Q1"),
              (RFC, Q2, 43.745e6, "Q2"), ("Revenues", Q2, 734.112e6, "Q2"),
              ("OperatingLeaseLeaseIncome", Q2, 650e6, "Q2"), sic="6798")
    assert revenue(b) == {(date(2025, 3, 31), "us-gaap:Revenues", 674.479e6),
                          (date(2025, 6, 30), "us-gaap:Revenues", 734.112e6)}
    (q1,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert [d["conditions"] for d in q1["dropped"]] == [["not positive"]]
    (q2,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-06-30"]
    assert "operating-lease income 650,000,000 is at least 1%" in q2["dropped"][0]["conditions"]
    assert "Revenues 734,112,000 is larger" in q2["dropped"][0]["conditions"]


def test_a_utility_total_tagged_outside_the_revenue_concepts_rules_out_its_contract_revenue():
    b = build((RFC, Q1, 1.15e9, "Q1"), ("RegulatedAndUnregulatedOperatingRevenue", Q1, 1.20e9, "Q1"), sic="4911")
    assert revenue(b) == set()  # the larger total is for comparison only, so nothing is served
    (line,) = [a for a in b.stats["revenue_audit"] if a["end"] == "2025-03-31"]
    assert "RegulatedAndUnregulatedOperatingRevenue 1,200,000,000 is larger" in line["dropped"][0]["conditions"]


def test_a_bank_never_serves_contract_revenue():
    b = build((RFC, Q1, 2.0e9, "Q1"), ("NoninterestExpense", Q1, 2.1e9, "Q1"), sic="6022", confirmed=True)
    assert revenue(b) == set()


def test_confirmation_needs_an_exact_release_match_in_every_period(tmp_path):
    import json
    from datetime import datetime, timezone
    from receipts.types import Fact
    from scripts.confirm_revenue import check
    audit = {"revenue": [
        {"start": "2025-04-01", "end": "2025-06-30", "served": None, "value": None,
         "dropped": [{"value": 3220.8e6, "conditions": [AWAITING]}, {"value": 3222.0e6, "conditions": [AWAITING]}]},
        {"start": "2026-04-01", "end": "2026-06-30", "served": None, "value": None,
         "dropped": [{"value": 4003.0e6, "conditions": [AWAITING]}]}]}
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "EXMP.json").write_text(json.dumps(audit))

    def release(start, end, value):
        return Fact(cik=1, ticker="EXMP", metric="revenue", basis="GAAP", value=value, unit="USD",
                    period_start=date.fromisoformat(start), period_end=date.fromisoformat(end), source="press_release",
                    form="8-K", accession="0000000001-26-000001", accepted_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
                    url="https://example.invalid/ex99.htm", decimals=-6)
    (tmp_path / "pr" / "EXMP").mkdir(parents=True)
    path = tmp_path / "pr" / "EXMP" / "x.jsonl"
    path.write_text("".join(f.model_dump_json() + "\n" for f in (
        release("2025-04-01", "2025-06-30", 3222.0e6), release("2026-04-01", "2026-06-30", 4003.0e6))))
    assert check("EXMP", tmp_path / "audit", tmp_path / "pr")["confirmed"]  # a restated version may differ slightly
    path.write_text("".join(f.model_dump_json() + "\n" for f in (
        release("2025-04-01", "2025-06-30", 3300.0e6), release("2026-04-01", "2026-06-30", 4003.0e6))))
    assert not check("EXMP", tmp_path / "audit", tmp_path / "pr")["confirmed"]  # no version equals the total


def test_hasbro_shape_contract_revenue_serves_where_the_releases_show_it_is_the_total():
    """Hasbro's filings from 2025 tag a Revenues line 13-15% above its net revenues ($1,006.1M against $887.1M for
    Q1 2025). By default the larger broad figure stands; once its releases show contract revenue is the total
    (scripts/confirm_revenue.py), contract revenue serves where the two disagree, and a filing that tags only the
    broad figure keeps it."""
    entries = (("Revenues", Q1, 1006.1e6, "Q1"), (RFC, Q1, 887.1e6, "Q1"), ("Revenues", Q2, 980.8e6, "Q2"))
    assert revenue(build(*entries)) == {(date(2025, 3, 31), "us-gaap:Revenues", 1006.1e6),
                                        (date(2025, 6, 30), "us-gaap:Revenues", 980.8e6)}
    b = build(*entries, contract_total=True)
    assert revenue(b) == {(date(2025, 3, 31), f"us-gaap:{RFC}", 887.1e6),
                          (date(2025, 6, 30), "us-gaap:Revenues", 980.8e6)}
    assert [d["concept"] for d in b.stats["component_revenue_dropped"]] == ["Revenues"]


def _audit_and_releases(tmp_path, releases):
    """A Hasbro-shaped audit (Revenues served above contract revenue in Q1-Q3 and the year) and release files."""
    import json
    from datetime import datetime, timezone
    from receipts.types import Fact
    def larger(broad, contract):
        return [{"concept": RFC, "value": contract, "conditions": [f"Revenues {broad:,.0f} is larger"]}]
    lines = [("2025-01-01", "2025-03-31", "Q1 FY2025", 1006.1e6, 887.1e6), ("2025-04-01", "2025-06-30", "Q2 FY2025",
             1133.9e6, 980.8e6), ("2025-07-01", "2025-09-30", "Q3 FY2025", 1590.1e6, 1387.5e6),
             ("2025-01-01", "2025-12-31", "FY FY2025", 5365.9e6, 4701.3e6)]
    audit = {"revenue": [{"start": s, "end": e, "fiscal": f, "kind": None, "served": "Revenues", "value": b,
                          "dropped": larger(b, c)} for s, e, f, b, c in lines]
             + [{"start": "2025-01-01", "end": "2025-09-30", "fiscal": "9M FY2025", "kind": "ytd", "served": RFC,
                 "value": 3255.4e6, "dropped": []}]}
    (tmp_path / "audit").mkdir(exist_ok=True)
    (tmp_path / "audit" / "EXMP.json").write_text(json.dumps(audit))
    (tmp_path / "pr" / "EXMP").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pr" / "EXMP" / "x.jsonl").write_text("".join(Fact(
        cik=1, ticker="EXMP", metric="revenue", basis="GAAP", value=v, unit="USD", period_start=date.fromisoformat(s),
        period_end=date.fromisoformat(e), source="press_release", form="8-K", accession="0000000001-26-000001",
        accepted_at=datetime(2026, 2, 10, tzinfo=timezone.utc), url="https://example.invalid/ex99.htm",
        decimals=-5).model_dump_json() + "\n" for s, e, v in releases))
    return tmp_path / "audit", tmp_path / "pr"


def test_the_contract_revenue_decision_needs_two_release_periods_and_no_broad_match(tmp_path):
    from scripts.confirm_revenue import contract_total
    q3, q4 = ("2025-07-01", "2025-09-30", 1387.5e6), ("2025-10-01", "2025-12-31", 1445.9e6)  # Q4: 4,701.3 - 3,255.4
    d = contract_total("EXMP", *_audit_and_releases(tmp_path, [q3, q4]))
    assert d["decided"] and [p["agrees_with"] for p in d["periods"]] == ["contract", "contract"]
    assert not contract_total("EXMP", *_audit_and_releases(tmp_path, [q3]))["decided"]  # one period is not enough
    broad_q1 = ("2025-01-01", "2025-03-31", 1006.1e6)
    d = contract_total("EXMP", *_audit_and_releases(tmp_path, [q3, q4, broad_q1]))
    assert not d["decided"] and d["reason"] == "a release agrees with the broad figure"


def test_main_records_and_revokes_decisions_with_the_stores_run_date(tmp_path):
    """Review L11: a confirmation whose re-check disagrees is revoked; L12: decisions carry the store's run date."""
    import json
    import sqlite3
    from scripts.confirm_revenue import main
    audit_dir, pr_dir = _audit_and_releases(tmp_path, [("2025-07-01", "2025-09-30", 1387.5e6),
                                                       ("2025-10-01", "2025-12-31", 1445.9e6)])
    db = tmp_path / "facts.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("facts_start", "2023-09-14"), ("quarters", "8")])
    con.commit()
    con.close()
    path = tmp_path / "confirmations.json"
    held = {"start": "2025-07-01", "end": "2025-09-30", "served": None, "value": None,
            "dropped": [{"concept": RFC, "value": 1300e6, "conditions": [AWAITING]}]}
    (audit_dir / "HELD.json").write_text(json.dumps({"revenue": [held]}))
    (pr_dir / "HELD").mkdir()
    (pr_dir / "HELD" / "x.jsonl").write_text((pr_dir / "EXMP" / "x.jsonl").read_text().replace('"EXMP"', '"HELD"'))
    path.write_text(json.dumps({"HELD": {"confirmed_on": "2026-01-01", "method": "press-release totals",
                                         "periods": []}}))
    kw = dict(audit_dir=audit_dir, pr_dir=pr_dir, confirmations_path=path, db=db)
    assert main(["--all", "--recheck"], **kw) == 0
    out = json.loads(path.read_text())
    assert out["EXMP"]["contract_revenue_is_total"]["decided_on"] == "2026-10-31"   # the store's run date
    assert "confirmed_on" not in out["HELD"] and out["HELD"]["revoked"]["on"] == "2026-10-31"
    _audit_and_releases(tmp_path, [("2025-07-01", "2025-09-30", 1387.5e6), ("2025-10-01", "2025-12-31", 1445.9e6),
                                   ("2025-01-01", "2025-03-31", 1006.1e6)])
    assert main(["--all"], **kw) == 0 and "EXMP" not in json.loads(path.read_text())


def test_duke_shape_a_comparison_total_serves_where_the_releases_show_it_is_the_total():
    """Duke tags its "Total operating revenues" as RegulatedAndUnregulatedOperatingRevenue ($9,178M for Q1 2026) and a
    narrower contract revenue ($8,995M), which a utility's filing holds back: no revenue is served. Once its releases
    show the comparison total is its revenue (scripts/confirm_revenue.py), it is served, except where the same filing
    tags Revenues for the period."""
    rr = "RegulatedAndUnregulatedOperatingRevenue"
    entries = ((rr, Q1, 9178e6, "Q1"), (RFC, Q1, 8995e6, "Q1"), (rr, Q2, 7592e6, "Q2"), ("Revenues", Q2, 7592e6, "Q2"))
    assert revenue(build(*entries[:3], sic="4931")) == set()
    b = build(*entries, sic="4931", broad_total=rr)
    assert revenue(b) == {(date(2025, 3, 31), f"us-gaap:{rr}", 9178e6), (date(2025, 6, 30), "us-gaap:Revenues", 7592e6)}


def test_the_comparison_total_decision_needs_two_matching_release_periods_and_none_that_differ(tmp_path):
    import gzip
    import json
    from datetime import datetime, timezone
    from receipts.types import Fact
    from scripts.confirm_revenue import broad_total
    rr = "RegulatedAndUnregulatedOperatingRevenue"
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "EXMP.json").write_text(json.dumps({"cik": 902, "revenue": []}))
    (tmp_path / "cf").mkdir()
    filed = [("2025-07-01", "2025-09-30", 8542e6), ("2026-01-01", "2026-03-31", 9178e6),
             ("2025-01-01", "2025-12-31", 32237e6)]
    entries = [{"start": s, "end": e, "val": v, "accn": "0000000902-26-000001", "form": "10-Q"} for s, e, v in filed]
    body = {"facts": {"us-gaap": {rr: {"units": {"USD": entries}}}}}
    (tmp_path / "cf" / "CIK0000000902.json.gz").write_bytes(gzip.compress(json.dumps(body).encode()))
    folder = tmp_path / "pr" / "EXMP"
    folder.mkdir(parents=True)

    def write(name, rows):
        (folder / name).write_text("".join(Fact(
            cik=902, ticker="EXMP", metric="revenue", basis="GAAP", value=v, unit="USD",
            period_start=date.fromisoformat(s), period_end=date.fromisoformat(e), source="press_release", form="8-K",
            accession="0000000902-26-000009", accepted_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
            url="https://example.invalid/ex99.htm", decimals=-6).model_dump_json() + "\n" for s, e, v in rows))
    write("a.jsonl.quarantined", [filed[0]])            # held back by the XBRL gate: still the company's own figures
    write("b.jsonl", [filed[1]])
    d = broad_total("EXMP", tmp_path / "audit", tmp_path / "pr", tmp_path / "cf")
    assert d["decided"] and d["concept"] == rr and len(d["periods"]) == 2
    write("c.jsonl", [("2025-01-01", "2025-12-31", 30000e6)])
    d = broad_total("EXMP", tmp_path / "audit", tmp_path / "pr", tmp_path / "cf")
    assert not d["decided"] and d["reason"].startswith("a release total differs")
