"""Keep SEC companyfacts gzip-compressed per company, so ingest can be re-run offline after the raw cache is cleared.

    .venv/bin/python scripts/archive_companyfacts.py archive CIK [CIK ...] [--delete-raw]
    .venv/bin/python scripts/archive_companyfacts.py restore CIK [CIK ...]

archive: reads data/cache/edgar/<sha256(url)>.bin for each company's companyfacts URL, checks it against the sha256
in its .json sidecar, writes data/archive/companyfacts/CIK##########.json.gz and records url, raw sha256, raw and
gzip bytes in data/archive/companyfacts/index.json. With --delete-raw the cached body is then removed (its sidecar
stays, so the fetch time and hash remain on record).
restore: decompresses an archive back into the EDGAR cache after checking the recorded sha256, writing each file
to a temporary name and renaming it, and rewrites the .json sidecar when it describes other bytes, so
`python -m receipts.ingest --offline` and the release mapper read it exactly as fetched.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "data/cache/edgar"
ARCHIVE = REPO / "data/archive/companyfacts"


def url_for(cik: int) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"


def paths(cik: int) -> tuple[Path, Path, Path]:
    key = hashlib.sha256(url_for(cik).encode()).hexdigest()
    return CACHE / f"{key}.bin", CACHE / f"{key}.json", ARCHIVE / f"CIK{int(cik):010d}.json.gz"


def load_index() -> dict:
    p = ARCHIVE / "index.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_index(index: dict) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVE / "index.json.tmp"
    tmp.write_text(json.dumps(dict(sorted(index.items())), indent=1) + "\n")
    tmp.replace(ARCHIVE / "index.json")


def archive(ciks: list[int], delete_raw: bool) -> int:
    """Archive each CIK's cached body. The index is saved for every CIK archived, even when a later one fails, and a
    raw body is deleted only once the index that records its archive is saved (review L3)."""
    index, archived = load_index(), []
    try:
        for cik in ciks:
            body_path, meta_path, gz_path = paths(cik)
            if not body_path.exists():
                if str(cik) in index and gz_path.exists():
                    print(f"CIK {cik}: already archived, no raw body cached")
                    continue
                raise SystemExit(f"CIK {cik}: no cached companyfacts body to archive")
            body = body_path.read_bytes()
            meta = json.loads(meta_path.read_text())
            digest = hashlib.sha256(body).hexdigest()
            if digest != meta["sha256"]:
                raise SystemExit(f"CIK {cik}: cached body does not match its recorded sha256")
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            tmp = gz_path.with_suffix(".gz.tmp")
            tmp.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))
            if hashlib.sha256(gzip.decompress(tmp.read_bytes())).hexdigest() != digest:
                raise SystemExit(f"CIK {cik}: archive does not decompress to the cached body")
            tmp.replace(gz_path)
            index[str(cik)] = {"url": url_for(cik), "sha256": digest, "bytes": len(body),
                               "gzip_bytes": gz_path.stat().st_size, "fetched_at": meta.get("fetched_at")}
            archived.append(body_path)
            print(f"CIK {cik}: {len(body):,} -> {gz_path.stat().st_size:,} bytes")
    finally:
        save_index(index)
    if delete_raw:
        for body_path in archived:
            body_path.unlink()
        print(f"deleted {len(archived)} raw bodies")
    return 0


def restore(ciks: list[int]) -> int:
    index = load_index()
    for cik in ciks:
        body_path, meta_path, gz_path = paths(cik)
        rec = index.get(str(cik))
        if rec is None or not gz_path.exists():
            raise SystemExit(f"CIK {cik}: not archived")
        body = gzip.decompress(gz_path.read_bytes())
        if hashlib.sha256(body).hexdigest() != rec["sha256"]:
            raise SystemExit(f"CIK {cik}: archive does not match its recorded sha256")
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = body_path.with_suffix(".bin.tmp")
        tmp.write_bytes(body)
        tmp.replace(body_path)  # body first: the EDGAR cache requires both files
        sidecar = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if sidecar.get("sha256") != rec["sha256"]:  # missing, or left by a later fetch of different bytes
            tmp = meta_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"url": rec["url"], "fetched_at": rec["fetched_at"],
                                       "sha256": rec["sha256"], "bytes": rec["bytes"]}))
            tmp.replace(meta_path)
        print(f"CIK {cik}: restored {len(body):,} bytes")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("archive", "restore"))
    ap.add_argument("ciks", nargs="+", type=int)
    ap.add_argument("--delete-raw", action="store_true", help="archive: remove the cached raw body afterwards")
    args = ap.parse_args(argv)
    return archive(args.ciks, args.delete_raw) if args.action == "archive" else restore(args.ciks)


if __name__ == "__main__":
    sys.exit(main())
