"""receipts.filing_xbrl: a filing companyfacts lack is read from its own XBRL instance as companyfacts lists a filing
(on AEP's and Essex's Q1 2026 10-Qs the parser reproduced companyfacts' 348 and 253 entries exactly), and
receipts.ingest merges it only where the companyfacts lack the accession."""
import gzip
import hashlib
import json

from receipts import filing_xbrl, ingest

INSTANCE = b"""<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:us-gaap="http://fasb.org/us-gaap/2025"
  xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <xbrli:context id="q2"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000905</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>
    <xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="q2b"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000905</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>
    <xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="q2sub"><xbrli:entity>
    <xbrli:identifier scheme="http://www.sec.gov/CIK">0000000905</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">x:SubsidiaryMember</xbrldi:explicitMember>
    </xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>
    <xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="end"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000905</xbrli:identifier>
    </xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure>
    </xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
    </xbrli:divide></xbrli:unit>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  <dei:DocumentFiscalYearFocus contextRef="q2">2026</dei:DocumentFiscalYearFocus>
  <dei:DocumentFiscalPeriodFocus contextRef="q2">Q2</dei:DocumentFiscalPeriodFocus>
  <dei:EntityCommonStockSharesOutstanding contextRef="end" unitRef="shares" decimals="INF">531000000
    </dei:EntityCommonStockSharesOutstanding>
  <us-gaap:Revenues contextRef="q2" unitRef="usd" decimals="-6">5445000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="q2b" unitRef="usd" decimals="-5">5444900000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="q2sub" unitRef="usd" decimals="-6">2000000000</us-gaap:Revenues>
  <us-gaap:EarningsPerShareDiluted contextRef="q2" unitRef="usdPerShare" decimals="2">1.30
    </us-gaap:EarningsPerShareDiluted>
  <us-gaap:NetIncomeLoss contextRef="q2" unitRef="usd" xsi:nil="true"/>
  <x:Custom xmlns:x="http://example.invalid/x" contextRef="q2" unitRef="usd">1</x:Custom>
</xbrli:xbrl>"""
ACCESSION = "0000000905-26-000059"


def test_an_instance_reads_as_companyfacts_lists_a_filing():
    p = filing_xbrl.parse_instance(INSTANCE, accession=ACCESSION, form="10-Q", filed="2026-07-30")
    common = {"accn": ACCESSION, "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-30"}
    g = p["facts"]["us-gaap"]
    # the more precise of two values for one period, never the subsidiary's dimensioned context
    assert g["Revenues"]["units"]["USD"] == [{"end": "2026-06-30", "val": 5444900000, "start": "2026-04-01", **common}]
    assert g["EarningsPerShareDiluted"]["units"]["USD/shares"] == [
        {"end": "2026-06-30", "val": 1.3, "start": "2026-04-01", **common}]
    assert "NetIncomeLoss" not in g and set(p["facts"]) == {"us-gaap", "dei"}  # nil facts and other taxonomies out
    assert p["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"] == [
        {"end": "2026-06-30", "val": 531000000, **common}]
    assert p["dei"]["DocumentFiscalPeriodFocus"] == "Q2"


def _archive(tmp_path, cik=905):
    (tmp_path / f"{ACCESSION}.xml.gz").write_bytes(gzip.compress(INSTANCE))
    (tmp_path / "index.json").write_text(json.dumps({ACCESSION: {
        "cik": cik, "form": "10-Q", "filed": "2026-07-30", "sha256": hashlib.sha256(INSTANCE).hexdigest()}}))
    return tmp_path


def test_a_kept_filing_fills_only_a_companyfacts_hole(tmp_path, monkeypatch):
    archive = _archive(tmp_path)
    assert len(filing_xbrl.hole_filings([905], set(), archive)) == 1
    assert filing_xbrl.hole_filings([905], {ACCESSION}, archive) == []      # companyfacts list it: never twice
    assert filing_xbrl.hole_filings([906], set(), archive) == []            # another company's filing
    monkeypatch.setattr(filing_xbrl, "ARCHIVE", archive)
    cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"start": "2026-01-01", "end": "2026-03-31", "val": 6.0e9, "accn": "0000000905-26-000034", "fy": 2026,
         "fp": "Q1", "form": "10-Q", "filed": "2026-05-05"}]}}}}}
    merged = ingest.fill_holes(905, cf)
    assert [e["accn"] for e in merged["facts"]["us-gaap"]["Revenues"]["units"]["USD"]] == [
        "0000000905-26-000034", ACCESSION]
    assert ingest.fill_holes(906, cf) is cf


def test_an_archived_instance_that_was_altered_is_refused(tmp_path):
    archive = _archive(tmp_path)
    (archive / f"{ACCESSION}.xml.gz").write_bytes(gzip.compress(INSTANCE.replace(b"5445000000", b"5445000001")))
    try:
        filing_xbrl.hole_filings([905], set(), archive)
    except ValueError as e:
        assert "sha256" in str(e)
    else:
        raise AssertionError("an altered archive must not be read")


def test_the_fetch_script_picks_the_extracted_instance_over_the_linkbases():
    from scripts.fetch_filing_xbrl import instance_doc
    docs = [{"description": "XBRL TAXONOMY EXTENSION LABEL LINKBASE DOCUMENT", "document": "aep_lab.xml",
             "type": "EX-101.LAB"},
            {"description": "EXTRACTED XBRL INSTANCE DOCUMENT", "document": "aep-20260630_htm.xml", "type": "XML"}]
    assert instance_doc(docs)["document"] == "aep-20260630_htm.xml"
    assert instance_doc([{"description": "", "document": "x.xml", "type": "EX-101.INS"}])["document"] == "x.xml"
    assert instance_doc(docs[:1]) is None
