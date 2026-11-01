"""Keep the XBRL instance of each stored 10-Q and 10-K that SEC's companyfacts lack, for receipts.filing_xbrl.

    .venv/bin/python scripts/fetch_filing_xbrl.py --dry-run --out holes.json            # list the holes; no request
    SEC_USER_AGENT=... .venv/bin/python scripts/fetch_filing_xbrl.py --max-sec-requests 120 --delete-raw --out f.json

A hole is a 10-Q, 10-K or amendment in data/facts.sqlite's filings, filed in the store's window, whose accession the
company's archived companyfacts (predecessor registrants and filings already kept here merged in) do not list. For
each, the filing index in the folder its URL names gives the instance document SEC extracts from inline XBRL
("EXTRACTED XBRL INSTANCE DOCUMENT"), else the EX-101.INS. It is fetched (cache first), parsed as a check (it must hold
facts and the filing's DEI fiscal period), gzipped into data/archive/filing_xbrl/<accession>.xml.gz and recorded in
index.json with its URL, CIK, ticker, form, filing date, raw sha256 and sizes. --delete-raw then removes the cached
raw instance bodies. Re-ingest the companies offline afterwards (python -m receipts.ingest --offline --tickers ...).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts import filing_xbrl  # noqa: E402
from receipts.edgar import Edgar, EdgarError  # noqa: E402
from receipts.ingest import with_predecessors  # noqa: E402

DB = REPO / "data/facts.sqlite"
COMPANYFACTS = REPO / "data/archive/companyfacts"
PERIODIC = ("10-Q", "10-K", "10-Q/A", "10-K/A")


def holes(db: Path = DB, companyfacts_dir: Path = COMPANYFACTS, archive: Path = filing_xbrl.ARCHIVE) -> list[dict]:
    """Stored periodic filings in the window that the company's companyfacts (and this archive) do not list."""
    load = lambda c: json.loads(gzip.decompress((companyfacts_dir / f"CIK{int(c):010d}.json.gz").read_bytes()))
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        since = dict(con.execute("SELECT key, value FROM meta")).get("filings_start", "")
        out = []
        for cik, ticker in con.execute("SELECT cik, ticker FROM companies ORDER BY ticker").fetchall():
            if not (companyfacts_dir / f"CIK{int(cik):010d}.json.gz").exists():
                continue
            have = filing_xbrl.accessions(with_predecessors(cik, load)) | set(filing_xbrl._index(archive))
            for accession, form, filed, report, url in con.execute(
                    f"SELECT accession, form, filed, report_date, url FROM filings WHERE cik = ? AND form IN "
                    f"({', '.join('?' * len(PERIODIC))}) AND filed >= ? ORDER BY filed", (cik, *PERIODIC, since)):
                if accession not in have:
                    folder = re.search(r"/Archives/edgar/data/(\d+)/", url or "")
                    out.append({"ticker": ticker, "cik": int(cik), "folder_cik": int(folder.group(1)) if folder
                                else int(cik), "accession": accession, "form": form, "filed": filed,
                                "report_date": report})
        return out
    finally:
        con.close()


def instance_doc(docs: list[dict]) -> Optional[dict]:
    """The filing's XBRL instance in its index: SEC's extracted instance of an inline filing, else the EX-101.INS."""
    for doc in docs:
        if "INSTANCE" in doc["description"].upper() and doc["document"].lower().endswith(".xml"):
            return doc
    return next((d for d in docs if d["type"].upper() == "EX-101.INS"), None)


def keep(hole: dict, edgar: Any, archive: Path = filing_xbrl.ARCHIVE) -> dict:
    """Fetch, check and archive one hole's instance; the index entry, or {"error": ...}."""
    try:
        doc = instance_doc(edgar.filing_index(hole["folder_cik"], hole["accession"]))
        if doc is None:
            return {"error": "no XBRL instance in the filing index"}
        xml = edgar.get(doc["url"])
    except EdgarError as e:
        return {"error": str(e)[:300]}
    parsed = filing_xbrl.parse_instance(xml, accession=hole["accession"], form=hole["form"], filed=hole["filed"])
    n = sum(len(es) for concepts in parsed["facts"].values() for body in concepts.values()
            for es in body["units"].values())
    if not n or not parsed["dei"].get("DocumentFiscalPeriodFocus"):
        return {"error": f"the instance holds {n} facts and no DEI fiscal period", "url": doc["url"]}
    archive.mkdir(parents=True, exist_ok=True)
    gz = archive / f"{hole['accession']}.xml.gz"
    tmp = gz.with_suffix(".gz.tmp")
    tmp.write_bytes(gzip.compress(xml, compresslevel=9, mtime=0))
    if gzip.decompress(tmp.read_bytes()) != xml:
        raise SystemExit(f"{hole['accession']}: the archive does not decompress to the instance")
    tmp.replace(gz)
    return {"cik": hole["folder_cik"], "ticker": hole["ticker"], "form": hole["form"], "filed": hole["filed"],
            "report_date": hole["report_date"], "url": doc["url"], "sha256": hashlib.sha256(xml).hexdigest(),
            "bytes": len(xml), "gzip_bytes": gz.stat().st_size, "facts": n,
            "fiscal": " ".join(str(parsed["dei"].get(k)) for k in ("DocumentFiscalPeriodFocus",
                                                                  "DocumentFiscalYearFocus"))}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="list the holes; no SEC request, nothing written")
    ap.add_argument("--max-sec-requests", type=int, default=120)
    ap.add_argument("--delete-raw", action="store_true", help="remove the cached raw instance bodies once archived")
    ap.add_argument("--out", type=Path, help="write the run's report here (JSON)")
    args = ap.parse_args(argv)
    found = holes()
    print(f"{len(found)} stored periodic filings at {len({h['ticker'] for h in found})} companies are missing from "
          f"their companyfacts")
    result: dict[str, Any] = {"holes": found}
    if not args.dry_run:
        edgar = Edgar(max_live_requests=args.max_sec_requests)
        index_path = filing_xbrl.ARCHIVE / "index.json"
        index = filing_xbrl._index(filing_xbrl.ARCHIVE)
        kept, failed = [], []
        try:
            for h in found:
                rec = keep(h, edgar)
                if "error" in rec:
                    failed.append({**h, **rec})
                    print(f"  {h['ticker']:<6} {h['accession']}  FAILED: {rec['error']}")
                    continue
                index[h["accession"]] = rec
                kept.append(h["accession"])
                print(f"  {h['ticker']:<6} {h['accession']}  {h['form']:<6} {rec['fiscal']:<8} {rec['facts']:>5} "
                      f"facts {rec['bytes']:>10,} -> {rec['gzip_bytes']:>9,} bytes")
        finally:
            filing_xbrl.ARCHIVE.mkdir(parents=True, exist_ok=True)
            tmp = index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(dict(sorted(index.items())), indent=1) + "\n")
            tmp.replace(index_path)
            edgar.close()
        if args.delete_raw:
            for accession in kept:
                body, _ = edgar._paths(index[accession]["url"])
                body.unlink(missing_ok=True)
        result.update(kept=kept, failed=failed, sec_live_requests=edgar.live_requests)
        print(f"kept {len(kept)}, failed {len(failed)}; SEC live requests {edgar.live_requests}")
    if args.out:
        args.out.write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
