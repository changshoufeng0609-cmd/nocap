"""Keep the frozen closed-book Q&A benchmark reproducible after an evidence top-up.

eval/qa_reproduce.py reads each pinned data/snapshot file live while its bytes still match the pin in
eval/qa_manifest.json, and otherwise only the copy archived with the run, eval/qa_runs/<run>/evidence/<name>.
After a snapshot rebuild changes a pinned file, this script finds the pinned bytes in git history and archives them:

    .venv/bin/python eval/qa_archive_pins.py [--dry-run]

It never writes a copy whose SHA-256 differs from the pin, and it reports a pin it cannot find instead of guessing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "eval/qa_runs/qa-closed-book-20261029"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pinned_bytes(name: str, expected: str) -> bytes | None:
    """The pinned bytes of data/snapshot/<name> from any commit that touched it (or its parent), else None."""
    revs = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", f"data/snapshot/{name}"],
                          capture_output=True, text=True, check=True).stdout.split()
    for rev in revs:
        for ref in (rev, f"{rev}~1"):
            got = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:data/snapshot/{name}"], capture_output=True)
            if got.returncode == 0 and digest(got.stdout) == expected:
                return got.stdout
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report what would be archived; write nothing")
    args = parser.parse_args(argv)
    pins = json.loads((ROOT / "eval/qa_manifest.json").read_text(encoding="utf-8"))["snapshot_hashes"]
    missing = 0
    for name, expected in sorted(pins.items()):
        if name == "manifest.json":  # read from the run's frozen source bundle, not data/snapshot
            continue
        live = ROOT / "data/snapshot" / name
        if live.is_file() and digest(live.read_bytes()) == expected:
            continue
        archived = RUN / "evidence" / name
        if archived.is_file():
            ok = digest(archived.read_bytes()) == expected
            print(f"{'archived ' if ok else 'MISMATCH '} {name}")
            missing += not ok
            continue
        raw = pinned_bytes(name, expected)
        if raw is None:
            print(f"NOT FOUND {name}")
            missing += 1
            continue
        if not args.dry_run:
            archived.parent.mkdir(parents=True, exist_ok=True)
            archived.write_bytes(raw)
        print(f"{'would add' if args.dry_run else 'added    '} {name}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
