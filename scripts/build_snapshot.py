"""Build data/snapshot/: the read-only evidence and pre-baked demo streams that the deployed API serves.

    .venv/bin/python scripts/build_snapshot.py             # rebuild (safe to re-run at any time)
    .venv/bin/python scripts/build_snapshot.py --no-model  # fail rather than call the model for an uncached passage

What it writes (docs/deploy.md):
  facts.sqlite        a consistent copy of data/facts.sqlite (SQLite backup API, VACUUM, journal_mode DELETE) with
                      its balance-sheet facts rebuilt from data/archive/companyfacts (receipts.balance_sheet), whose
                      common-stockholder and consolidated income alternatives are rebuilt from the hash-checked
                      companyfacts and filing_xbrl archives (receipts.net_income_common; evidence-only metrics), and whose
                      facts rows are stored without the columns their filing and company rows repeat and are read
                      back, unchanged, through a view named facts (compact_facts)
  pr_facts/           every data/pr_facts/<TICKER>/*.jsonl, keeping only lines that parse as receipts.types.Fact
  manifest.json       a content fingerprint of the evidence, counts, and the snapshot date /api/health reports
  api_cache/          each example in examples/examples.json recorded through the real pipeline (receipts.stream),
                      stored under its draft key, so the demo replays it with cached: true and never waits on the model
  spark_cache/        the examples' Muse Spark responses, so an as-of re-check of an example costs nothing
  examples.json       the examples with their texts inlined, matching the recordings exactly

The snapshot is built beside the old one and swapped in only when complete and under MAX_BYTES. A recording whose
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
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pydantic import ValidationError  # noqa: E402

from receipts import api, balance_sheet, extract, net_income_common, recordings, stream  # noqa: E402
from receipts.edgar import Edgar  # noqa: E402
from receipts.grounding import FilingTexts, normalize_filing_html  # noqa: E402
from receipts.store import Store  # noqa: E402
from receipts.types import Fact  # noqa: E402
from receipts.verify import to_utc  # noqa: E402

DATA = REPO / "data"
OUT = DATA / "snapshot"
# 2026-10-31: 112 MiB for the S&P 500 build, about 87 MiB projected; the owner approved up to 160 MiB that day.
# (Earlier the same day it went from 10 to 16 MiB for the 35-company coverage.)
MAX_BYTES = 112 * 1024 * 1024
EXAMPLES = REPO / "examples" / "examples.json"
BALANCE_SHEET_ARCHIVE = DATA / "archive" / "companyfacts"  # the companyfacts receipts.balance_sheet reads
# Snapshot facts are stored in FACTS_COMPACT without the columns their filing and company rows repeat; a view named
# facts joins them back in the table's own column order, so Store and the API read the snapshot unchanged. At the
# full S&P 500 this keeps facts.sqlite near 38 MB rather than 56 MB, under GitHub's 50 MB per-file warning.
FACTS_COMPACT = "facts_compact"
FACTS_JOINED = {"ticker": "c.ticker", "form": "g.form", "accepted_at": "g.accepted_at", "url": "g.url"}


def fail(message: str) -> "SystemExit":
    return SystemExit(f"build_snapshot: {message}")


def copy_sqlite(src: Path, dst: Path, *, balance_sheet_archive: Optional[Path] = None,
                net_income_archive: Optional[Path] = None) -> Optional[dict]:
    """Copy src to dst as a snapshot database. With balance_sheet_archive, every balance-sheet fact in the copy is
    rebuilt from that companyfacts archive first (receipts.balance_sheet.add_to_db), and its summary is returned.
    net_income_archive separately rebuilds evidence-only common-stockholder and consolidated income alternatives."""
    summary = None
    if not src.exists():
        raise fail(f"{src.relative_to(REPO)} is missing; build it with python -m receipts.ingest")
    source = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)  # consistent even while another process writes the source
        target.execute("PRAGMA journal_mode=DELETE")  # no -wal/-shm files: opens on a read-only filesystem
        if balance_sheet_archive is not None:  # balance-sheet instants, from the same filings as the other facts
            summary = balance_sheet.add_to_db(target, balance_sheet_archive)
        if net_income_archive is not None:
            alternatives = net_income_common.add_to_db(target, net_income_archive)
            if summary is None:
                summary = {}
            summary["net_income_alternatives"] = alternatives
            summary.update(alternatives["by_metric"])
        compact_facts(target)
        target.execute("VACUUM")
        target.commit()
    finally:
        source.close()
        target.close()
    return summary


def facts_digest(con: sqlite3.Connection, columns: list[str]) -> tuple[str, int]:
    """sha256 of the facts rows (the table or its view) in id order, and how many there are."""
    h, n = hashlib.sha256(), 0
    for row in con.execute(f'SELECT {", ".join(columns)} FROM facts ORDER BY id'):
        h.update(json.dumps(row, separators=(",", ":"), default=str).encode())
        n += 1
    return h.hexdigest(), n


def compact_facts(con: sqlite3.Connection) -> None:
    """Move the facts rows into FACTS_COMPACT without the FACTS_JOINED columns, rebuild the table's indexes there, and
    put a view named facts in the table's place that joins those columns back from filings and companies.

    The build fails unless the view returns exactly the rows the table held, in id order: a fact whose ticker, form,
    acceptance time or URL differed from its company or filing row, or that had no such row, would otherwise change.
    The digest is taken from this backup copy, which is the source as it was at backup time. CHECK constraints are
    not carried over: nothing writes to a snapshot."""
    info = con.execute("PRAGMA table_info(facts)").fetchall()  # cid, name, type, notnull, default, pk
    columns = [r[1] for r in info]
    if [r[1] for r in info if r[5]] != ["id"] or not set(FACTS_JOINED) <= set(columns):
        raise fail("data/facts.sqlite has no facts table in the layout compact_facts expects")
    kept = [r for r in info if r[1] not in FACTS_JOINED]
    ddl = ", ".join(f'"{name}" {type_}' + (" PRIMARY KEY" if pk else " NOT NULL" if notnull else "")
                    for _, name, type_, notnull, _, pk in kept)
    indexes = []
    for _, name, unique, origin, partial in con.execute("PRAGMA index_list(facts)").fetchall():
        cols = [r[2] for r in con.execute(f'PRAGMA index_info("{name}")')]
        if origin != "c" or partial or None in cols or set(cols) & set(FACTS_JOINED):
            raise fail(f"index {name} on facts cannot move to {FACTS_COMPACT}")
        quoted = ", ".join(f'"{c}"' for c in cols)
        indexes.append(f'CREATE {"UNIQUE " if unique else ""}INDEX "{name}" ON {FACTS_COMPACT} ({quoted});')
    before = facts_digest(con, columns)
    names = ", ".join(f'"{r[1]}"' for r in kept)
    selected = ", ".join(FACTS_JOINED.get(c, f'f."{c}"') for c in columns)
    # Each join is one primary-key probe per fact row; Store's lookups still go through ix_facts_lookup. A fact the
    # joins could not complete would read back with NULLs, which the digest check below rejects.
    con.executescript(f"""
        BEGIN;
        CREATE TABLE {FACTS_COMPACT} ({ddl});
        INSERT INTO {FACTS_COMPACT} ({names}) SELECT {names} FROM facts ORDER BY id;
        DROP TABLE facts;
        {" ".join(indexes)}
        CREATE VIEW facts ({", ".join(f'"{c}"' for c in columns)}) AS
            SELECT {selected} FROM {FACTS_COMPACT} AS f
            LEFT JOIN filings AS g ON g.accession = f.accession
            LEFT JOIN companies AS c ON c.cik = f.cik;
        COMMIT;
    """)
    after = facts_digest(con, columns)
    if after != before:
        raise fail(f"the compact facts view does not reproduce data/facts.sqlite ({after[1]} rows read back of "
                   f"{before[1]}): a fact's {', '.join(FACTS_JOINED)} must equal its company's and filing's")


def sqlite_digest(db: Path) -> tuple[str, dict[str, int]]:
    """A digest of the database's rows (not its bytes, which VACUUM may lay out differently) and row counts.

    A compacted snapshot is digested and counted through its facts view, in id order, in place of FACTS_COMPACT, so
    it digests exactly as the database it was copied from."""
    h = hashlib.sha256()
    counts: dict[str, int] = {}
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        objects = dict(con.execute("SELECT name, type FROM sqlite_master WHERE type = 'table' "
                                   "OR (type = 'view' AND name = 'facts')"))
        if objects.get("facts") == "view":
            objects.pop(FACTS_COMPACT, None)
        for name in sorted(objects):
            n = 0
            h.update(f"\n#{name}\n".encode())
            order = "rowid" if objects[name] == "table" else "id"
            for row in con.execute(f'SELECT * FROM "{name}" ORDER BY {order}'):
                h.update(json.dumps(row, separators=(",", ":"), default=str).encode())
                n += 1
            counts[name] = n
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


def copy_filing_texts(src: Path, dst: Path, evidence: Store, edgar: Edgar) -> tuple[str, int]:
    """Bind every packaged quotation source to the filing store and original cached SEC bytes."""
    texts = FilingTexts(src)
    entries = texts.entries()
    h = hashlib.sha256()
    for entry in entries:
        row, text = texts.load(entry["id"])
        company = evidence.by_ticker(row["ticker"])  # the packaged ticker itself, also when it is a word ("FLEX")
        if company is None or str(company[0]) != str(row["cik"]):
            raise fail("filing-text company does not match the evidence store")
        filings = [f for f in evidence.filings(company[0], (row["form"],))
                   if f["accession"] == row["accession"]]
        if len(filings) != 1 or filings[0]["url"] != row["url"] or (
                to_utc(filings[0]["accepted_at"]) != to_utc(row["accepted_at"])):
            raise fail("filing-text provenance or acceptance time differs from the evidence store")
        source = edgar.get(row["url"])
        if hashlib.sha256(source).hexdigest() != row.get("source_sha256") or normalize_filing_html(source) != text:
            raise fail("filing text does not reproduce its original cached SEC document")
        dst.mkdir(parents=True, exist_ok=True)
        (dst / f"{row['sha256']}.txt").write_text(text, encoding="utf-8")
        h.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
    if entries:
        stream.write_json_atomic(dst / "index.json", {"version": 1, "filings": entries})
    return h.hexdigest() if entries else "", len(entries)


def copy_recordings(src: Path, dst: Path, filing_texts: Path) -> int:
    """Package the recorded sample runs (receipts.recordings), each bound to the input it replays: a Slide recording
    to the committed sample image's bytes, a quote recording to a filing text packaged in this snapshot, a Private
    recording to its exact draft text (checked by Recordings.load itself)."""
    store = recordings.Recordings([src])
    listed = {entry["id"] for entry in FilingTexts(filing_texts).entries()} if filing_texts.exists() else set()
    count = 0
    for kind in recordings.KINDS:
        for path in sorted((src / kind).glob("*.json")) if (src / kind).is_dir() else []:
            record = store.load(kind, path.stem)
            if record is None:
                raise fail(f"recording {os.path.relpath(path, REPO)} is damaged or does not match its key")
            request = record.get("request", {})
            if kind == "slide":
                image = REPO / "web/public" / str(request.get("image", "")).lstrip("/")
                if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != request.get("image_sha256"):
                    raise fail(f"recording {path.name} no longer matches its sample image")
            elif kind == "grounding" and request.get("filing_id") not in listed:
                raise fail(f"recording {path.name} quotes a filing that is not packaged in this snapshot")
            (dst / kind).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dst / kind / path.name)
            count += 1
    return count


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
        balance = copy_sqlite(DATA / "facts.sqlite", build / "facts.sqlite",
                              balance_sheet_archive=BALANCE_SHEET_ARCHIVE,
                              net_income_archive=BALANCE_SHEET_ARCHIVE)
        db_digest, rows = sqlite_digest(build / "facts.sqlite")
        pr_digest, pr_facts, dropped = copy_pr_facts(DATA / "pr_facts", build / "pr_facts")
        evidence = Store(db_path=build / "facts.sqlite", pr_dir=build / "pr_facts")
        edgar = Edgar(offline=True, max_live_requests=0)
        try:
            text_digest, text_count = copy_filing_texts(DATA / "filing_texts", build / "filing_texts", evidence, edgar)
        finally:
            evidence.close()
            edgar.close()
        recorded = copy_recordings(DATA / "recordings", build / "recordings", build / "filing_texts")
        material = f"{db_digest}|{pr_digest}" + (f"|{text_digest}" if text_count else "")
        fingerprint = hashlib.sha256(material.encode()).hexdigest()[:24]
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
                    "filings": rows.get("filings", 0), "filing_texts": text_count, "recordings": recorded}
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
    print(f"data/snapshot: {size / 1e6:.2f} MB · {len(companies)} companies · {rows.get('facts', 0)} SQLite facts "
          f"({balance['facts'] if balance else 0} balance-sheet) · "
          f"{balance['net_income_to_common']['facts']} common-stockholder alternatives · "
          f"{balance['net_income_including_nci']['facts']} consolidated-income alternatives · "
          f"{pr_facts} press-release facts" + (f" ({dropped} bad lines dropped)" if dropped else "")
          + f" · fingerprint {fingerprint[:12]} · {time.monotonic() - t0:.1f} s")
    for row in report:
        c = row["counts"]
        print(f"  {row['id']:<24} {row['recorded']:<6} {row['claims']:>2} claims  {c['verified']} ✓ {c['wrong']} ✗ "
              f"{c.get('not_public', 0)} ◷ {c['unverifiable']} ? {c['approximate']} ≈  ${row['cost_usd']:.4f}  ({row['key']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
