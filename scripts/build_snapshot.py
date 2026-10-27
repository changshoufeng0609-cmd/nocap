"""Build data/snapshot/: the read-only evidence and pre-baked demo streams that the deployed API serves.

    .venv/bin/python scripts/build_snapshot.py             # rebuild (safe to re-run at any time)
    .venv/bin/python scripts/build_snapshot.py --no-model  # fail rather than call the model for an uncached passage

What it writes (docs/deploy.md):
  facts.sqlite        a consistent copy of data/facts.sqlite (SQLite backup API, VACUUM, journal_mode DELETE)
  pr_facts/           every data/pr_facts/<TICKER>/*.jsonl, keeping only lines that parse as receipts.types.Fact
  manifest.json       a content fingerprint of the evidence, counts, and the snapshot date /api/health reports
  api_cache/          each example in examples/examples.json recorded through the real pipeline (receipts.stream),
                      stored under its draft key, so the demo replays it with cached: true and never waits on the model
  spark_cache/        the examples' Muse Spark responses, so an as-of re-check of an example costs nothing
  examples.json       the examples with their texts inlined, matching the recordings exactly

The snapshot is built beside the old one and swapped in only when complete and under 10 MB. A recording whose
draft, evidence and code are unchanged is reused as is, so a re-run with nothing new rewrites nothing. Model calls
happen only for passages missing from data/cache/spark (and the old snapshot): about $0.004 per passage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pydantic import ValidationError  # noqa: E402

from receipts import api, extract, stream  # noqa: E402
from receipts.store import Store  # noqa: E402
from receipts.types import Fact  # noqa: E402

DATA = REPO / "data"
OUT = DATA / "snapshot"
MAX_BYTES = 10 * 1024 * 1024
EXAMPLES = REPO / "examples" / "examples.json"


def fail(message: str) -> "SystemExit":
    return SystemExit(f"build_snapshot: {message}")


def copy_sqlite(src: Path, dst: Path) -> None:
    if not src.exists():
        raise fail(f"{src.relative_to(REPO)} is missing; build it with python -m receipts.ingest")
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)  # consistent even while another process writes the source
        target.execute("PRAGMA journal_mode=DELETE")  # no -wal/-shm files: opens on a read-only filesystem
        target.execute("VACUUM")
        target.commit()
    finally:
        source.close()
        target.close()


def sqlite_digest(db: Path) -> tuple[str, dict[str, int]]:
    """A digest of the database's rows (not its bytes, which VACUUM may lay out differently) and row counts."""
    h = hashlib.sha256()
    counts: dict[str, int] = {}
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            n = 0
            h.update(f"\n#{table}\n".encode())
            for row in con.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                h.update(json.dumps(row, separators=(",", ":"), default=str).encode())
                n += 1
            counts[table] = n
    finally:
        con.close()
    return h.hexdigest(), counts


def copy_pr_facts(src: Path, dst: Path) -> tuple[str, int, int]:
    """Valid Fact lines of every <TICKER>/*.jsonl, byte for byte. Returns (digest, facts kept, lines dropped)."""
    h = hashlib.sha256()
    kept = dropped = 0
    for path in sorted(src.glob("*/*.jsonl")):
        good = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                Fact.model_validate_json(line)
            except (ValueError, ValidationError):
                dropped += 1  # a half-written line from a writer that is still running
                continue
            good.append(line)
        if not good:
            continue
        rel = path.relative_to(src)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text("\n".join(good) + "\n", encoding="utf-8")
        h.update(f"\n#{rel.as_posix()}\n".encode())
        h.update("\n".join(good).encode())
        kept += len(good)
    return h.hexdigest(), kept, dropped


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def bake_examples(build: Path, old: Path, *, allow_model: bool) -> list[dict]:
    """Record every example through receipts.stream.check_events against the new snapshot's data."""
    examples = api.load_examples([EXAMPLES])
    if not examples:
        raise fail(f"no examples in {EXAMPLES.relative_to(REPO)}")
    fingerprint = stream.cache_fingerprint(build)
    store = Store(db_path=build / "facts.sqlite", pr_dir=build / "pr_facts")
    spark_dirs = [extract.CACHE_DIR, old / "spark_cache"]
    old_cache = stream.EventCache(None, [old / "api_cache"])
    new_cache = stream.EventCache(build / "api_cache")
    (build / "spark_cache").mkdir(parents=True, exist_ok=True)
    report = []
    try:
        for ex in examples:
            text, as_of, context = ex["text"], ex["as_of"], ex["context"]
            key = stream.draft_key(text, as_of, context)
            ctx = extract.infer_context(text) if context is None else context
            row = {"id": ex["id"], "key": key[:12], "cost_usd": 0.0}
            reused = old_cache.get(key, fingerprint)
            if reused:  # same draft, evidence and code: keep the old file byte for byte
                (build / "api_cache").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(old / "api_cache" / f"{key}.json", build / "api_cache" / f"{key}.json")
                events, row["recorded"] = reused, "reused"
            else:
                missing = extract.passages_to_call(text, ctx or "", cache_dirs=spark_dirs)
                if missing and not allow_model:
                    raise fail(f"example {ex['id']}: {missing} passage(s) are not in the model cache (--no-model)")
                stats = stream.CheckStats()
                events = list(stream.check_events(
                    text, draft_hash=key, as_of=as_of, context=context, store=store, stats=stats,
                    cache_dir=extract.CACHE_DIR, extra_cache_dirs=[old / "spark_cache"], timeout_s=60.0,
                    max_attempts=3))
                if not stats.complete:
                    raise fail(f"example {ex['id']} did not complete: {events[-1][1].get('message')}")
                new_cache.put(key, fingerprint, events)
                row.update(recorded="new", cost_usd=round(stats.cost_usd, 6))
            for k in extract.request_keys(text, ctx or ""):
                source = next((d / f"{k}.json" for d in spark_dirs if (d / f"{k}.json").exists()), None)
                if source is None:
                    raise fail(f"example {ex['id']}: model response {k[:12]} is not cached")
                shutil.copyfile(source, build / "spark_cache" / f"{k}.json")
            done = events[-1][1]
            row.update(counts=done["counts"], claims=sum(n == "claim" for n, _ in events))
            report.append(row)
    finally:
        store.close()
    stream.write_json_atomic(build / "examples.json", {"examples": examples})
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-model", action="store_true", help="fail instead of calling the model")
    args = parser.parse_args(argv)
    t0 = time.monotonic()
    build = DATA / f".snapshot-build-{os.getpid()}"
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    try:
        copy_sqlite(DATA / "facts.sqlite", build / "facts.sqlite")
        db_digest, rows = sqlite_digest(build / "facts.sqlite")
        pr_digest, pr_facts, dropped = copy_pr_facts(DATA / "pr_facts", build / "pr_facts")
        fingerprint = hashlib.sha256(f"{db_digest}|{pr_digest}".encode()).hexdigest()[:24]
        previous = read_json(OUT / "manifest.json")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        built_at = previous.get("built_at") if previous.get("fingerprint") == fingerprint else None
        con = sqlite3.connect((build / "facts.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            companies = [r[0] for r in con.execute("SELECT ticker FROM companies ORDER BY ticker")]
            source_built = (con.execute("SELECT value FROM meta WHERE key='built_at'").fetchone() or [None])[0]
        finally:
            con.close()
        manifest = {"fingerprint": fingerprint, "built_at": built_at or now, "source_built_at": source_built,
                    "companies": companies, "facts": {"sqlite": rows.get("facts", 0), "press_release": pr_facts},
                    "filings": rows.get("filings", 0)}
        stream.write_json_atomic(build / "manifest.json", manifest)  # the fingerprint the recordings are keyed to
        report = bake_examples(build, OUT, allow_model=not args.no_model)
        size = dir_bytes(build)
        if size > MAX_BYTES:
            raise fail(f"the snapshot is {size / 1e6:.1f} MB, over the {MAX_BYTES / 1e6:.0f} MB limit")
        old = DATA / f".snapshot-old-{os.getpid()}"
        if OUT.exists():
            OUT.rename(old)
        build.rename(OUT)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(build, ignore_errors=True)
        raise
    print(f"data/snapshot: {size / 1e6:.2f} MB · {len(companies)} companies · {rows.get('facts', 0)} SQLite facts · "
          f"{pr_facts} press-release facts" + (f" ({dropped} bad lines dropped)" if dropped else "")
          + f" · fingerprint {fingerprint[:12]} · {time.monotonic() - t0:.1f} s")
    for row in report:
        c = row["counts"]
        print(f"  {row['id']:<24} {row['recorded']:<6} {row['claims']:>2} claims  {c['verified']} ✓ {c['wrong']} ✗ "
              f"{c['unverifiable']} ? {c['approximate']} ≈  ${row['cost_usd']:.4f}  ({row['key']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
