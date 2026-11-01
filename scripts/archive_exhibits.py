"""Keep the cached earnings exhibits gzip-compressed, so releases can be re-mapped offline after the raw cache is cleared.

    .venv/bin/python scripts/archive_exhibits.py archive [--delete-raw]      # every cached 8-K exhibit
    .venv/bin/python scripts/archive_exhibits.py restore CIK [CIK ...]        # one company's, back into the cache
                                                       # (a predecessor registrant's folder by its own CIK: XOM 34088)

An exhibit is a cached EDGAR document whose URL lies in the folder of an 8-K or 8-K/A that data/facts.sqlite lists,
other than the filing's index page (the mapper reads indexes again cheaply). archive gzips each body into
data/archive/exhibits/<sha256(url)>.gz after checking it against its .json sidecar and records url, CIK, raw sha256
and sizes in data/archive/exhibits/index.json; --delete-raw then removes the cached body (the sidecar stays).
restore decompresses a company's exhibits back into the EDGAR cache, writing each file under a temporary name and
renaming it, and rewrites a sidecar that describes other bytes. Submissions and companyfacts are not touched here
(scripts/archive_companyfacts.py keeps companyfacts).
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

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts.ingest import PREDECESSOR_CIKS  # noqa: E402

CACHE = REPO / "data/cache/edgar"
ARCHIVE = REPO / "data/archive/exhibits"
DB = REPO / "data/facts.sqlite"
FOLDER = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/([^/?#]+)$")


def eight_k_folders(db: Path = DB) -> dict[tuple[int, str], str]:
    """(CIK, accession without dashes) -> accession, for every stored 8-K and 8-K/A, under the company's CIK and each
    predecessor registrant's (receipts.ingest.PREDECESSOR_CIKS), in whose EDGAR folder its earlier releases are."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {(c, a.replace("-", "")): a for cik, a in
                con.execute("SELECT cik, accession FROM filings WHERE form IN ('8-K', '8-K/A')")
                for c in (int(cik), *PREDECESSOR_CIKS.get(int(cik), ()))}
    finally:
        con.close()


def load_index() -> dict:
    p = ARCHIVE / "index.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_index(index: dict) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVE / "index.json.tmp"
    tmp.write_text(json.dumps(dict(sorted(index.items())), indent=1) + "\n")
    tmp.replace(ARCHIVE / "index.json")


def archive(delete_raw: bool) -> int:
    """Archive every cached 8-K exhibit body. The index is saved for every exhibit archived, even when a later one
    fails, and a raw body is deleted only once the index that records its archive is saved (review L3)."""
    folders, index = eight_k_folders(), load_index()
    raw = packed = 0
    archived = []
    try:
        for meta_path in sorted(CACHE.glob("*.json")):
            try:
                meta = json.loads(meta_path.read_text())
            except ValueError:
                continue
            m = FOLDER.search(meta.get("url", ""))
            body_path = meta_path.with_suffix(".bin")
            if not m or not body_path.exists() or (int(m.group(1)), m.group(2)) not in folders:
                continue
            if m.group(3).lower().endswith(("-index.htm", "-index.html", "-index-headers.html")):
                continue
            body = body_path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != meta.get("sha256"):
                raise SystemExit(f"{meta['url']}: cached body does not match its recorded sha256")
            gz_path = ARCHIVE / f"{body_path.stem}.gz"
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            tmp = gz_path.with_suffix(".gz.tmp")
            tmp.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))
            if hashlib.sha256(gzip.decompress(tmp.read_bytes())).hexdigest() != digest:
                raise SystemExit(f"{meta['url']}: archive does not decompress to the cached body")
            tmp.replace(gz_path)
            index[meta["url"]] = {"cik": int(m.group(1)), "accession": folders[(int(m.group(1)), m.group(2))],
                                  "sha256": digest, "bytes": len(body), "gzip_bytes": gz_path.stat().st_size,
                                  "fetched_at": meta.get("fetched_at"), "key": body_path.stem}
            archived.append(body_path)
            raw, packed = raw + len(body), packed + gz_path.stat().st_size
    finally:
        save_index(index)
    if delete_raw:
        for body_path in archived:
            body_path.unlink()
    print(f"archived {len(archived)} exhibits: {raw:,} -> {packed:,} bytes"
          + (" (raw bodies deleted)" if delete_raw else ""))
    return 0


def restore(ciks: list[int]) -> int:
    index = load_index()
    n = 0
    for url, rec in sorted(index.items()):
        if rec["cik"] not in ciks:
            continue
        body = gzip.decompress((ARCHIVE / f"{rec['key']}.gz").read_bytes())
        if hashlib.sha256(body).hexdigest() != rec["sha256"]:
            raise SystemExit(f"{url}: archive does not match its recorded sha256")
        body_path, meta_path = CACHE / f"{rec['key']}.bin", CACHE / f"{rec['key']}.json"
        tmp = body_path.with_suffix(".bin.tmp")
        tmp.write_bytes(body)
        tmp.replace(body_path)
        sidecar = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if sidecar.get("sha256") != rec["sha256"]:
            tmp = meta_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"url": url, "fetched_at": rec["fetched_at"], "sha256": rec["sha256"],
                                       "bytes": rec["bytes"]}))
            tmp.replace(meta_path)
        n += 1
    print(f"restored {n} exhibits")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("archive", "restore"))
    ap.add_argument("ciks", nargs="*", type=int)
    ap.add_argument("--delete-raw", action="store_true")
    args = ap.parse_args(argv)
    return archive(args.delete_raw) if args.action == "archive" else restore(args.ciks)


if __name__ == "__main__":
    sys.exit(main())
