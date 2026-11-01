"""Facts of a 10-Q or 10-K that SEC's companyfacts lack, read from the filing's own XBRL instance.

SEC's companyfacts API (data.sec.gov/api/xbrl/companyfacts) can leave out a filing that the company's Submissions list:
AEP's Q2 and Q3 2026 10-Qs, Essex's Q3 2026 10-Q and Sempra's Q2 2026 10-Q among 53 such filings at 40 companies on
2026-11-01. The store's fiscal calendar then stopped early and a claim matching the company's release got "?". For
such a filing scripts/fetch_filing_xbrl.py keeps the instance document SEC extracts from it ("EXTRACTED XBRL INSTANCE
DOCUMENT", else the EX-101.INS) in data/archive/filing_xbrl, and this module reads it as companyfacts lists a filing:
every us-gaap and dei fact of a context with no dimension (the registrant itself, not a segment or a co-registrant
subsidiary), with the filing's own DEI fiscal year and period, its form and filing date. receipts.ingest merges these
entries into a company's companyfacts only for accessions the companyfacts lack (hole_filings); every concept rule,
the calendar and every gate then apply as to any filing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from lxml import etree

ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "archive" / "filing_xbrl"
XBRLI = "http://www.xbrl.org/2003/instance"
# the taxonomies companyfacts lists that receipts.ingest reads, by namespace URI (any prefix): us-gaap/2025, dei/2025
NAMESPACES = {"http://fasb.org/us-gaap/": "us-gaap", "http://xbrl.sec.gov/dei/": "dei"}
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _local(qname: str) -> str:
    return qname.split(":", 1)[-1]


def _units(root: etree._Element) -> dict[str, str]:
    """unit id -> companyfacts unit key: "USD", "shares", "pure", "USD/shares"."""
    out = {}
    for u in root.iter(f"{{{XBRLI}}}unit"):
        num = [_local(m.text.strip()) for m in u.iterfind(f"{{{XBRLI}}}divide/{{{XBRLI}}}unitNumerator/"
                                                           f"{{{XBRLI}}}measure")]
        den = [_local(m.text.strip()) for m in u.iterfind(f"{{{XBRLI}}}divide/{{{XBRLI}}}unitDenominator/"
                                                           f"{{{XBRLI}}}measure")]
        if num and den:
            out[u.get("id")] = f"{'*'.join(num)}/{'*'.join(den)}"
        else:
            out[u.get("id")] = "*".join(_local(m.text.strip()) for m in u.iterfind(f"{{{XBRLI}}}measure"))
    return out


def _contexts(root: etree._Element) -> dict[str, tuple[Optional[str], str]]:
    """context id -> (start, end) for a duration or (None, date) for an instant, contexts with no dimension only."""
    out = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        if c.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}segment") is not None or c.find(f"{{{XBRLI}}}scenario") is not None:
            continue
        p = c.find(f"{{{XBRLI}}}period")
        if p is None:
            continue
        start, end, instant = (p.findtext(f"{{{XBRLI}}}{k}") for k in ("startDate", "endDate", "instant"))
        if instant:
            out[c.get("id")] = (None, instant.strip()[:10])
        elif start and end:
            out[c.get("id")] = (start.strip()[:10], end.strip()[:10])
    return out


def _number(text: Optional[str]) -> Optional[float | int]:
    try:
        v = float((text or "").strip())
    except ValueError:
        return None
    return int(v) if v.is_integer() and abs(v) < 2 ** 53 else v


def _precision(decimals: Optional[str]) -> float:
    if decimals is None:
        return float("-inf")
    return float("inf") if decimals.strip().upper() == "INF" else float(decimals)


def parse_instance(xml: bytes, *, accession: str, form: str, filed: str) -> dict:
    """The instance's facts as companyfacts lists them: {"facts": {namespace: {concept: {"units": {unit: [...]}}}}}.
    One entry per concept, unit and period: of two that disagree, the more precise (its decimals); the filing's DEI
    fiscal year and period on every entry."""
    root = etree.fromstring(xml, parser=etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True))
    units, contexts = _units(root), _contexts(root)
    picked: dict[tuple, tuple[float, dict]] = {}
    dei: dict[str, str] = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or "}" not in el.tag:
            continue
        uri, concept = el.tag[1:].split("}", 1)
        ns = next((name for base, name in NAMESPACES.items() if uri.startswith(base)), None)
        if ns is None or el.get("contextRef") not in contexts or el.get(XSI_NIL) == "true":
            continue
        start, end = contexts[el.get("contextRef")]
        if ns == "dei" and el.get("unitRef") is None:
            dei.setdefault(concept, (el.text or "").strip())
            continue
        unit, value = units.get(el.get("unitRef") or ""), _number(el.text)
        if unit is None or value is None:
            continue
        entry = {"end": end, "val": value} | ({"start": start} if start else {})
        key = (ns, concept, unit, start, end)
        rank = _precision(el.get("decimals"))
        if key not in picked or rank > picked[key][0]:
            picked[key] = (rank, entry)
    fy = dei.get("DocumentFiscalYearFocus")
    common = {"accn": accession, "fy": int(fy) if fy and fy.isdigit() else None,
              "fp": dei.get("DocumentFiscalPeriodFocus") or None, "form": form, "filed": filed}
    facts: dict[str, dict] = {}
    for (ns, concept, unit, _, _), (_, entry) in sorted(picked.items(), key=lambda kv: str(kv[0])):
        facts.setdefault(ns, {}).setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(
            {**entry, **common})
    return {"facts": facts, "dei": dei}


def _index(archive: Path) -> dict:
    path = archive / "index.json"
    return json.loads(path.read_text()) if path.exists() else {}


def hole_filings(ciks: Iterable[int], have: set[str], archive: Optional[Path] = None) -> list[dict]:
    """Companyfacts-shaped facts of every archived filing of these CIKs whose accession `have` (the accessions the
    company's companyfacts list) lacks, oldest first."""
    archive = archive or ARCHIVE
    wanted = {int(c) for c in ciks}
    out = []
    for accession, rec in sorted(_index(archive).items(), key=lambda kv: kv[1]["filed"]):
        if int(rec["cik"]) not in wanted or accession in have:
            continue
        xml = gzip.decompress((archive / f"{accession}.xml.gz").read_bytes())
        if hashlib.sha256(xml).hexdigest() != rec["sha256"]:
            raise ValueError(f"{accession}: archived instance does not match its recorded sha256")
        parsed = parse_instance(xml, accession=accession, form=rec["form"], filed=rec["filed"])
        out.append({"facts": parsed["facts"]})
    return out


def accessions(companyfacts: dict) -> set[str]:
    """Every accession a companyfacts lists."""
    return {e["accn"] for concepts in companyfacts.get("facts", {}).values() for body in concepts.values()
            for entries in body.get("units", {}).values() for e in entries}
