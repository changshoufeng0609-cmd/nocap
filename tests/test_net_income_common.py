"""Evidence-only common-stockholder income: same stored filing/period, never a new claim metric."""
import gzip
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from receipts import net_income_common as common
from receipts.ingest import PREDECESSOR_CIKS
from receipts.store import Store, connect_rw, replace_company
from receipts.types import Claim, Fact, Metric
from scripts.build_snapshot import compact_facts, copy_sqlite

CIK = 797468
ACC = "0001628280-26-053388"
TAG = "NetIncomeLossAvailableToCommonStockholdersBasic"


def income(**updates):
    return Fact(cik=CIK, ticker="OXY", metric="net_income", concept="us-gaap:NetIncomeLoss", basis="GAAP",
                value=2_977e6, unit="USD", period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
                fiscal_year=2026, fiscal_period="Q2", source="derived", derived_from=[ACC], form="10-Q",
                accession=ACC, accepted_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
                url="https://example.invalid/oxy.htm", decimals=-6).model_copy(update=updates)


def companyfacts(value=2_787e6, **updates):
    entry = {"start": "2026-04-01", "end": "2026-06-30", "val": value, "accn": ACC, "form": "10-Q",
             "filed": "2026-08-05", **updates}
    return {"facts": {"us-gaap": {TAG: {"units": {"USD": [entry]}}}}}


def build(cf=None, own=None):
    return common.company_facts(companyfacts=cf or companyfacts(), net_income=own or [(income(), "quarter")])


def write_db(path):
    con = connect_rw(path)
    f = income()
    filing = {"accession": ACC, "form": "10-Q", "filed": "2026-08-05", "accepted_at": "2026-08-05T00:00:00Z",
              "report_date": "2026-06-30", "items": "", "primary_doc": "oxy.htm", "url": f.url}
    replace_company(con, {"cik": CIK, "ticker": "OXY", "name": "Occidental", "aliases": ["oxy", "occidental"]},
                    [filing], [(2026, "Q2", f.period_start, f.period_end, ACC)], [(f, "quarter")])
    return con


def archive(folder, records):
    folder.mkdir(parents=True, exist_ok=True)
    index = {}
    for cik, cf in records.items():
        raw = json.dumps(cf).encode()
        (folder / f"CIK{cik:010d}.json.gz").write_bytes(gzip.compress(raw))
        index[str(cik)] = {"sha256": hashlib.sha256(raw).hexdigest()}
    (folder / "index.json").write_text(json.dumps(index))
    return folder


def test_to_common_stays_outside_extraction_vocabulary():
    assert "net_income_to_common" not in get_args(Metric)
    assert Fact.model_validate({**income().model_dump(), "metric": "net_income_to_common"}).metric == "net_income_to_common"
    with pytest.raises(ValidationError):
        Claim(id="c1", metric="net_income_to_common", metric_text="net income", value_text="$2.79 billion",
              value=2.79, unit="USD", scale="billions", period_text="Q2 2026", period_type="quarter",
              comparison="none", basis="GAAP", forward_looking=False, entity="OXY", span_text="net income",
              span_start=0, span_end=10)


def test_build_oxy_retains_direct_to_common_receipt():
    facts, audit = build()
    assert len(facts) == 1 and audit["facts"] == 1
    f, kind = facts[0]
    assert kind == "quarter" and f.metric == "net_income_to_common"
    assert f.value == 2_787e6 and f.concept == f"us-gaap:{TAG}"
    assert f.source == "xbrl" and f.derived_from == [] and f.decimals == -6
    assert f.accession == ACC and f.period_start == date(2026, 4, 1)
    assert "common stockholders" in f.locator


@pytest.mark.parametrize("value", [2_977e6, 2_977e6 + 400_000, 2_977e6 - 500_000])
def test_equal_or_rounded_to_served_net_income_adds_no_fact(value):
    facts, _ = build(companyfacts(value))
    assert not facts


@pytest.mark.parametrize("updates", [{"accn": "missing"}, {"start": "2026-01-01"}, {"end": "2026-06-29"}])
def test_only_same_filing_and_exact_served_span_counts(updates):
    assert not build(companyfacts(**updates))[0]


@pytest.mark.parametrize("changes,kind", [({"basis": "non-GAAP"}, "quarter"), ({"segment": "Oil"}, "quarter"),
                                         ({"unit": "shares"}, "quarter"), ({"form": "8-K"}, "quarter"),
                                         ({}, "ytd"), ({}, "ttm")])
def test_only_served_company_wide_gaap_quarter_and_annual_income(changes, kind):
    assert not build(own=[(income(**changes), kind)])[0]


def test_conflicting_values_in_a_filing_are_not_evidence():
    cf = companyfacts()
    cf["facts"]["us-gaap"][TAG]["units"]["USD"].extend(companyfacts(2_780e6)["facts"]["us-gaap"][TAG]["units"]["USD"])
    facts, audit = build(cf)
    assert not facts and len(audit["conflicts"]) == 1


def test_annual_and_direct_q4_only_never_annual_minus_nine_months():
    annual = income(period_start=date(2026, 1, 1), period_end=date(2026, 12, 31), fiscal_period="FY", form="10-K")
    q4 = annual.model_copy(update={"period_start": date(2026, 10, 1), "fiscal_period": "Q4"})
    cf = companyfacts(start="2026-01-01", end="2026-12-31", form="10-K")
    facts, _ = build(cf, [(annual, "annual"), (q4, "quarter")])
    assert [(f.fiscal_period, k) for f, k in facts] == [("FY", "annual")]
    cf["facts"]["us-gaap"][TAG]["units"]["USD"].extend(companyfacts(start="2026-10-01", end="2026-12-31", form="10-K")["facts"]["us-gaap"][TAG]["units"]["USD"])
    facts, _ = build(cf, [(annual, "annual"), (q4, "quarter")])
    assert {f.fiscal_period for f, _ in facts} == {"FY", "Q4"}


def test_add_to_db_idempotent_and_compact_compatible(tmp_path):
    con = write_db(tmp_path / "facts.sqlite")
    folder = archive(tmp_path / "companyfacts", {CIK: companyfacts()})
    first = common.add_to_db(con, folder)
    assert first["facts"] == 1 and first["by_company"] == {"OXY": 1}
    before = con.execute("SELECT * FROM facts ORDER BY id").fetchall()
    assert common.add_to_db(con, folder)["replaced"] == 1
    assert con.execute("SELECT * FROM facts ORDER BY id").fetchall() == before
    compact_facts(con)
    con.commit()
    common.add_to_db(con, folder)
    assert con.execute("SELECT * FROM facts ORDER BY id").fetchall() == before
    con.close()


@pytest.mark.parametrize("damage", ["bad_hash", "no_hash", "no_index"])
def test_archive_requires_matching_index_sha256(tmp_path, damage):
    con = write_db(tmp_path / "facts.sqlite")
    folder = archive(tmp_path / "companyfacts", {CIK: companyfacts()})
    idx = folder / "index.json"
    if damage == "bad_hash":
        idx.write_text(json.dumps({str(CIK): {"sha256": "wrong"}}))
    elif damage == "no_hash":
        idx.write_text("{}")
    else:
        idx.unlink()
    with pytest.raises(SystemExit, match="sha256"):
        common.add_to_db(con, folder)
    assert con.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    con.close()


def test_predecessor_archive_is_merged_and_checked(tmp_path, monkeypatch):
    con = write_db(tmp_path / "facts.sqlite")
    monkeypatch.setitem(PREDECESSOR_CIKS, CIK, (1234,))
    folder = archive(tmp_path / "companyfacts", {CIK: {"facts": {}}, 1234: companyfacts()})
    assert common.add_to_db(con, folder)["by_company"] == {"OXY": 1}
    (folder / "CIK0000001234.json.gz").unlink()
    with pytest.raises(SystemExit, match="predecessor"):
        common.add_to_db(con, folder)
    con.close()


def test_snapshot_copy_adds_common_evidence_without_touching_source(tmp_path):
    db = tmp_path / "facts.sqlite"
    write_db(db).close()
    folder = archive(tmp_path / "companyfacts", {CIK: companyfacts()})
    copy_sqlite(db, tmp_path / "snap.sqlite", net_income_archive=folder)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    store = Store(tmp_path / "snap.sqlite", tmp_path / "pr_facts")
    try:
        assert [f.value for f in store.facts(CIK, "net_income_to_common")] == [2_787e6]
        assert [f.value for f in store.facts(CIK, "net_income")] == [2_977e6]
    finally:
        store.close()


def write_instance(folder, *, value=2_787e6, corrupt=False, tag=TAG):
    folder.mkdir()
    xml = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
        xmlns:gaap="http://fasb.org/us-gaap/2026" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
      <xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>
      <xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <gaap:{tag} contextRef="q" unitRef="usd" decimals="-6">{value:.0f}</gaap:{tag}>
    </xbrli:xbrl>'''.encode()
    (folder / f"{ACC}.xml.gz").write_bytes(gzip.compress(xml))
    (folder / "index.json").write_text(json.dumps({ACC: {"cik": CIK, "form": "10-Q", "filed": "2026-08-05",
        "sha256": hashlib.sha256(xml + (b" " if corrupt else b"")).hexdigest()}}))
    return folder


def test_missing_companyfacts_filing_uses_hashed_kept_xbrl_instance(tmp_path):
    con = write_db(tmp_path / "facts.sqlite")
    folder = archive(tmp_path / "companyfacts", {CIK: {"facts": {}}})
    write_instance(tmp_path / "filing_xbrl")
    result = common.add_to_db(con, folder)
    assert result["facts"] == 1 and result["audit"]["OXY"]["filing_xbrl_accessions"] == [ACC]
    assert con.execute("SELECT value FROM facts WHERE metric='net_income_to_common'").fetchone()[0] == 2_787e6
    con.close()


def test_instance_cannot_replace_a_filing_companyfacts_already_cover(tmp_path):
    con = write_db(tmp_path / "facts.sqlite")
    folder = archive(tmp_path / "companyfacts", {CIK: companyfacts(2_977e6)})
    write_instance(tmp_path / "filing_xbrl", corrupt=True)
    result = common.add_to_db(con, folder)
    assert result["facts"] == 0 and not result["audit"]["OXY"]["filing_xbrl_accessions"]
    con.close()


def test_instance_hash_must_match_before_any_write(tmp_path):
    con = write_db(tmp_path / "facts.sqlite")
    folder = archive(tmp_path / "companyfacts", {CIK: {"facts": {}}})
    write_instance(tmp_path / "filing_xbrl", corrupt=True)
    with pytest.raises(ValueError, match="sha256"):
        common.add_to_db(con, folder)
    assert con.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    con.close()


def test_instance_for_a_filing_not_held_is_never_used(tmp_path):
    con = write_db(tmp_path / "facts.sqlite")
    folder = archive(tmp_path / "companyfacts", {CIK: {"facts": {}}})
    instance = write_instance(tmp_path / "filing_xbrl", corrupt=True)
    idx = json.loads((instance / "index.json").read_text())
    (instance / "index.json").write_text(json.dumps({"not-stored": idx[ACC]}))
    assert common.add_to_db(con, folder)["facts"] == 0
    con.close()


def consolidated_companyfacts(value=2_996e6, **updates):
    cf = companyfacts(value, **updates)
    cf["facts"]["us-gaap"]["ProfitLoss"] = cf["facts"]["us-gaap"].pop(TAG)
    return cf


def test_consolidated_income_stays_outside_extraction_vocabulary():
    metric = "net_income_including_nci"
    assert metric not in get_args(Metric)
    assert Fact.model_validate({**income().model_dump(), "metric": metric}).metric == metric
    with pytest.raises(ValidationError):
        Claim(id="c1", metric=metric, metric_text="consolidated net income", value_text="$2.996 billion",
              value=2.996, unit="USD", scale="billions", period_text="Q2 2026", period_type="quarter",
              comparison="none", basis="GAAP", forward_looking=False, entity="OXY", span_text="net income",
              span_start=0, span_end=10)


def test_consolidated_tag_retained_beside_direct_net_income_loss():
    facts, audit = build(consolidated_companyfacts(), [(income(source="xbrl", derived_from=[]), "quarter")])
    assert len(facts) == 1 and audit["facts"] == 1
    f, kind = facts[0]
    assert f.metric == "net_income_including_nci" and f.concept == "us-gaap:ProfitLoss"
    assert f.value == 2_996e6 and f.source == "xbrl" and f.derived_from == [] and f.decimals == -6
    assert f.accession == ACC and f.period_start == date(2026, 4, 1) and kind == "quarter"
    assert "including noncontrolling interests" in f.locator


@pytest.mark.parametrize("updates", [{"accn": "missing"}, {"start": "2026-01-01"}, {"end": "2026-06-29"}])
def test_consolidated_only_same_filing_exact_span(updates):
    assert not build(consolidated_companyfacts(**updates))[0]


@pytest.mark.parametrize("value", [2_977e6, 2_977e6 + 400_000, 2_977e6 - 500_000])
def test_consolidated_equal_or_rounded_adds_no_fact(value):
    assert not build(consolidated_companyfacts(value))[0]


def test_consolidated_conflict_does_not_drop_independent_common_tag():
    cf = companyfacts()
    cf["facts"]["us-gaap"].update(consolidated_companyfacts()["facts"]["us-gaap"])
    cf["facts"]["us-gaap"]["ProfitLoss"]["units"]["USD"].extend(
        consolidated_companyfacts(2_998e6)["facts"]["us-gaap"]["ProfitLoss"]["units"]["USD"])
    facts, audit = build(cf)
    assert [f.metric for f, _ in facts] == ["net_income_to_common"]
    assert audit["by_metric"]["net_income_including_nci"]["conflicts"]


def test_consolidated_annual_and_only_direct_q4():
    annual = income(period_start=date(2026, 1, 1), period_end=date(2026, 12, 31), fiscal_period="FY", form="10-K")
    q4 = annual.model_copy(update={"period_start": date(2026, 10, 1), "fiscal_period": "Q4"})
    cf = consolidated_companyfacts(start="2026-01-01", end="2026-12-31", form="10-K")
    facts, _ = build(cf, [(annual, "annual"), (q4, "quarter")])
    assert [(f.fiscal_period, k) for f, k in facts] == [("FY", "annual")]
    cf["facts"]["us-gaap"]["ProfitLoss"]["units"]["USD"].extend(
        consolidated_companyfacts(start="2026-10-01", end="2026-12-31", form="10-K")["facts"]["us-gaap"]["ProfitLoss"]["units"]["USD"])
    facts, _ = build(cf, [(annual, "annual"), (q4, "quarter")])
    assert {f.fiscal_period for f, _ in facts} == {"FY", "Q4"}


def test_both_income_alternatives_rebuild_idempotently_and_report_counts(tmp_path):
    con = write_db(tmp_path / "facts.sqlite")
    cf = companyfacts()
    cf["facts"]["us-gaap"].update(consolidated_companyfacts()["facts"]["us-gaap"])
    folder = archive(tmp_path / "companyfacts", {CIK: cf})
    first = common.add_to_db(con, folder)
    assert first["facts"] == 2 and first["by_company"] == {"OXY": 2}
    for metric in ("net_income_to_common", "net_income_including_nci"):
        assert first["by_metric"][metric]["facts"] == 1
        assert first["by_metric"][metric]["by_company"] == {"OXY": 1}
    before = con.execute("SELECT * FROM facts ORDER BY id").fetchall()
    assert common.add_to_db(con, folder)["replaced"] == 2
    assert con.execute("SELECT * FROM facts ORDER BY id").fetchall() == before
    compact_facts(con)
    con.commit()
    common.add_to_db(con, folder)
    assert con.execute("SELECT * FROM facts ORDER BY id").fetchall() == before
    con.close()


def test_consolidated_predecessor_and_kept_instance_sources(tmp_path, monkeypatch):
    con = write_db(tmp_path / "facts.sqlite")
    monkeypatch.setitem(PREDECESSOR_CIKS, CIK, (1234,))
    folder = archive(tmp_path / "companyfacts", {CIK: {"facts": {}}, 1234: consolidated_companyfacts()})
    assert common.add_to_db(con, folder)["by_metric"]["net_income_including_nci"]["facts"] == 1
    archive(folder, {CIK: {"facts": {}}, 1234: {"facts": {}}})
    write_instance(tmp_path / "filing_xbrl", tag="ProfitLoss", value=2_996e6)
    result = common.add_to_db(con, folder)
    assert result["by_metric"]["net_income_including_nci"]["facts"] == 1
    assert result["audit"]["OXY"]["filing_xbrl_accessions"] == [ACC]
    con.close()
