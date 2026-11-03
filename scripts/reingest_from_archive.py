"""Add a metric to the stored companies' facts by an offline re-ingest from the companyfacts archive.

    .venv/bin/python scripts/reingest_from_archive.py --all --proof OUT/proof.json             # add the metric
    .venv/bin/python scripts/reingest_from_archive.py --all --check-only --proof OUT/drift.json  # write nothing

receipts.ingest.main builds every company as a top-up's offline re-ingest builds it (scripts/topup_latest_quarter.py,
helper ingest): --offline, the store's own --quarters, and date.today() pinned to the date the store's window was
computed from, so every company keeps its window. Two inputs are read without restoring the archive into the EDGAR
cache, whose raw companyfacts for the S&P 500 are about 1.9 GB:
  * companyfacts: decompressed in memory from data/archive/companyfacts/CIK##########.json.gz and checked against the
    sha256 its index.json records (scripts/archive_companyfacts.py); nothing is written to the cache;
  * filings: the store's own filings rows, as Submissions rows (a predecessor registrant's keep its folder, from their
    URL), so the metric is added from exactly the filings the store was built from, never from a newer one a later
    Submissions fetch (a top-up's plan, for one) put in the EDGAR cache, and without the older Submissions pages a
    refetch may list but the cache lacks. Only each registrant's main Submissions JSON (name, SIC, fiscal year end) and
    company_tickers.json are read from the EDGAR cache (--edgar-cache; offline, never written); a ticker that the cached
    company_tickers.json maps to another CIK than the stored one fails.
Only --metric's facts rows are written (each company's are replaced, so a re-run changes nothing). Every other row the
build gives (companies, filings, fiscal_periods and the other facts, all columns but facts.id) is compared with the
stored one, and each difference is reported as drift, not written: such a row would move because the code changed
since the company was last ingested (a name added to receipts.store's alias tables, say: 97 companies' aliases on
2026-11-02, which the alias index adds at query time anyway), not because of the metric. Ingest also rewrites
data/ingest_audit/<TICKER>.json and the store's built_at, as every ingest does. With --check-only nothing at all is
written: ingest writes to an in-memory database and no audit file.
--proof writes the drift and, unless --check-only, the old-rows proof of scripts/topup_latest_quarter.py (step 18's
canonical rows of all four tables, release files, revenue-confirmation and derived-rejection entries) before and after,
with what a pure addition needs: no row of any table gone or changed, and every added row a --metric fact. Exit 0 when
ingest succeeded and the proof (if asked) holds, 1 otherwise, 2 on bad usage.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scripts.topup_latest_quarter as topup  # noqa: E402
from receipts import ingest as ing  # noqa: E402
from receipts.edgar import SEC_DATA, Edgar, EdgarError  # noqa: E402
from receipts.store import _FACT_COLUMNS, connect_rw, fact_row  # noqa: E402

TABLES = topup.TABLES
FOLDER_RE = re.compile(r"/Archives/edgar/data/(\d+)/")
COLUMNS = {"companies": ("cik", "ticker", "name", "aliases", "fiscal_year_end"),
           "filings": ("accession", "cik", "form", "filed", "accepted_at", "report_date", "items", "primary_doc", "url"),
           "fiscal_periods": ("cik", "fiscal_year", "fiscal_period", "period_start", "period_end", "source"),
           "facts": _FACT_COLUMNS}


def canon(row: Any) -> str:
    """A row as scripts/topup_latest_quarter.py row_state writes it."""
    return json.dumps(list(row), separators=(",", ":"))


def stored(db: Path) -> tuple[dict[str, int], dict[int, list[dict]], dict]:
    """(ticker -> CIK, CIK -> its stored filings as Submissions rows, meta) of the store."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        companies = {t: int(c) for c, t in con.execute("SELECT cik, ticker FROM companies")}
        rows: dict[int, list[dict]] = defaultdict(list)
        for accession, cik, form, filed, accepted, report, items, doc, url in con.execute(
                "SELECT accession, cik, form, filed, accepted_at, report_date, items, primary_doc, url FROM filings "
                "ORDER BY accepted_at DESC, accession"):
            row = {"accessionNumber": accession, "form": form, "filingDate": filed, "reportDate": report,
                   "acceptanceDateTime": accepted, "primaryDocument": doc, "items": items}
            folder = FOLDER_RE.search(url or "")
            if folder and int(folder.group(1)) != int(cik):
                row["filer_cik"] = int(folder.group(1))  # a predecessor registrant's filing (receipts.ingest)
            rows[int(cik)].append(row)
        meta = dict(con.execute("SELECT key, value FROM meta"))
    finally:
        con.close()
    return companies, rows, meta


def archive_edgar(cache: Path, archive: Path, held: dict[int, list[dict]], ciks: dict[str, int]) -> type[Edgar]:
    """An offline Edgar that reads companyfacts from the archive and gives the stored filings as Submissions rows."""
    index = json.loads((archive / "index.json").read_text())

    class ArchiveEdgar(Edgar):
        def __init__(self, *args: Any, **kwargs: Any):
            kwargs.update(cache_dir=cache, offline=True, max_live_requests=0)
            super().__init__(*args, **kwargs)

        def ticker_to_cik(self, ticker: str) -> tuple[int, str]:
            cik, title = super().ticker_to_cik(ticker)
            want = ciks.get(ticker.strip().upper())
            if want is not None and cik != want:
                raise EdgarError(f"{ticker}: company_tickers.json gives CIK {cik}, the store holds {want}")
            return cik, title

        def companyfacts(self, cik: int, *, max_age: Optional[float] = None) -> dict:
            entry = index.get(str(int(cik)))
            path = archive / f"CIK{int(cik):010d}.json.gz"
            if entry is None or not path.exists():
                raise EdgarError(f"CIK {cik}: no archived companyfacts in {archive}")
            body = gzip.decompress(path.read_bytes())
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise EdgarError(f"CIK {cik}: the archive does not match the sha256 its index records")
            self.cache_hits += 1
            return json.loads(body)

        def submissions(self, cik: int, *, since: Optional[date] = None,
                        max_age: Optional[float] = None) -> tuple[dict, list[dict]]:
            """The main Submissions JSON's metadata and the store's filings of the company (none for a predecessor
            registrant: the store lists its filings under the current one, with their folder)."""
            sub = self.json(f"{SEC_DATA}/submissions/CIK{int(cik):010d}.json")
            meta = {k: v for k, v in sub.items() if k != "filings"}
            return meta, [dict(r) for r in held.get(int(cik), [])]

    return ArchiveEdgar


def metric_writer(db: Path, metric: str, drift: dict[str, dict], *, write: bool):
    """A stand-in for receipts.store.replace_company: compares every row but --metric's facts with the stored ones
    (drift), then replaces that company's --metric facts only (unless write is False)."""
    ro = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    insert = f"INSERT INTO facts ({', '.join(_FACT_COLUMNS)}) VALUES ({', '.join('?' * len(_FACT_COLUMNS))})"

    def replace(conn: sqlite3.Connection, company: dict, filings: list, periods: list, facts: list) -> None:
        cik = int(company["cik"])
        built = {
            "companies": [(cik, company["ticker"], company["name"], json.dumps(sorted(company["aliases"])),
                           company.get("fiscal_year_end"))],
            "filings": [(f["accession"], cik, f["form"], f["filed"], f["accepted_at"], f["report_date"], f["items"],
                         f["primary_doc"], f["url"]) for f in filings],
            "fiscal_periods": [(cik, fy, fp, start.isoformat(), end.isoformat(), src)
                               for fy, fp, start, end, src in periods],
            "facts": [fact_row(f, kind) for f, kind in facts if f.metric != metric],
        }
        for table, rows in built.items():
            where = "cik = ? AND metric != ?" if table == "facts" else "cik = ?"
            have = Counter(canon(r) for r in ro.execute(f"SELECT {', '.join(COLUMNS[table])} FROM {table} "
                                                        f"WHERE {where}", (cik, metric) if table == "facts" else (cik,)))
            want = Counter(canon(r) for r in rows)
            if have != want:
                gone, new = have - want, want - have
                drift[table][company["ticker"]] = {
                    "stored_not_built": sum(gone.values()), "built_not_stored": sum(new.values()),
                    "samples": [{"stored": dict(zip(COLUMNS[table], json.loads(a))) if a else None,
                                 "built": dict(zip(COLUMNS[table], json.loads(b))) if b else None}
                                for a, b in zip(list(gone)[:3] + [None] * 3, list(new)[:3] + [None] * 3)
                                if a or b]}
        if write:
            with conn:
                conn.execute("DELETE FROM facts WHERE cik = ? AND metric = ?", (cik, metric))
                conn.executemany(insert, [fact_row(f, kind) for f, kind in facts if f.metric == metric])

    return replace, ro


def run_ingest(root: Path, tickers: list[str], quarters: int, pin: date, db: Path, edgar_cls: type[Edgar],
               replace: Any, *, check_only: bool) -> int:
    """receipts.ingest.main with date.today() pinned (helper_ingest's pinning), the archive-backed Edgar and the
    metric writer; with check_only, ingest's own database is in memory and no audit file is written."""
    import datetime as _dt

    if not Path(ing.__file__).resolve().is_relative_to(root):
        raise SystemExit(f"receipts.ingest did not come from {root}")

    class PinnedDate(_dt.date):
        @classmethod
        def today(cls):  # type: ignore[override]
            return cls(pin.year, pin.month, pin.day)

    patches = {"date": PinnedDate, "Edgar": edgar_cls, "replace_company": replace}
    if check_only:
        patches.update(connect_rw=lambda _db: connect_rw(":memory:"), write_audit=lambda build, folder: None)
    saved = {name: getattr(ing, name) for name in patches}
    for name, value in patches.items():
        setattr(ing, name, value)
    try:
        return ing.main(["--tickers", ",".join(tickers), "--quarters", str(quarters), "--offline", "--db", str(db)])
    finally:
        for name, value in saved.items():
            setattr(ing, name, value)


def addition_proof(before: dict, after: dict, metric: str) -> dict:
    """Every row before is still there, unchanged (with its multiplicity); the rows added, per table and facts metric."""
    proof: dict[str, Any] = {"ok": True, "violations": [], "tables": {}}
    for table in TABLES:
        cols = after["tables"][table]["columns"]
        if cols != before["tables"][table]["columns"]:
            proof["ok"] = False
            proof["violations"].append(f"{table}: columns changed")
            continue
        old = Counter(line for lines in before["batch_rows"][table].values() for line in lines)
        new = Counter(line for lines in after["batch_rows"][table].values() for line in lines)
        gone, added = old - new, new - old
        entry: dict[str, Any] = {"before": sum(old.values()), "after": sum(new.values()),
                                 "removed_or_changed": sum(gone.values()), "added": sum(added.values()),
                                 "before_sha256": before["tables"][table]["sha256"],
                                 "after_sha256": after["tables"][table]["sha256"]}
        if gone:
            proof["ok"] = False
            entry["removed_samples"] = [dict(zip(cols, json.loads(line))) for line in list(gone)[:5]]
            proof["violations"].append(f"{table}: {sum(gone.values())} rows gone or changed")
        if table == "facts" and added:
            at = {c: cols.index(c) for c in ("metric", "cik", "fiscal_period", "period_kind", "source")}
            rows = [json.loads(line) for line in added.elements()]
            entry["added_by_metric"] = dict(Counter(r[at["metric"]] for r in rows))
            entry["added_companies"] = len({r[at["cik"]] for r in rows})
            entry["added_by_kind"] = dict(Counter(f"{r[at['period_kind']]} {r[at['source']]}" for r in rows))
            other = sum(n for m, n in entry["added_by_metric"].items() if m != metric)
            if other:
                proof["ok"] = False
                proof["violations"].append(f"facts: {other} added rows are not {metric}")
        elif added:
            proof["ok"] = False
            entry["added_samples"] = [dict(zip(cols, json.loads(line))) for line in list(added)[:5]]
            proof["violations"].append(f"{table}: {sum(added.values())} rows added")
        proof["tables"][table] = entry
    for name in ("pr_files", "side_files"):
        if before[name] != after[name]:
            proof["ok"] = False
            proof["violations"].append(f"{name} changed")
    return proof


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every company the store holds")
    which.add_argument("--tickers", help="comma-separated stored tickers")
    ap.add_argument("--metric", default="capital_expenditures", help="the facts metric to add (receipts.store)")
    ap.add_argument("--check-only", action="store_true", help="write nothing; report the drift only")
    ap.add_argument("--edgar-cache", type=Path, default=REPO / "data" / "cache" / "edgar",
                    help="EDGAR cache holding the Submissions (read only)")
    ap.add_argument("--archive", type=Path, default=REPO / "data" / "archive" / "companyfacts")
    ap.add_argument("--proof", type=Path, help="write the drift and the old-rows proof here")
    args = ap.parse_args(argv)

    db = ing.DB_PATH
    if not db.exists():
        print(f"no store at {db}", file=sys.stderr)
        return 2
    if args.metric not in ing.METRIC_CONCEPTS and not args.check_only:
        print(f"{args.metric} is not in receipts.store.METRIC_CONCEPTS", file=sys.stderr)
        return 2
    ciks, held, meta = stored(db)
    tickers = sorted(ciks) if args.all else [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    unknown = [t for t in tickers if t not in ciks]
    if unknown or not tickers:
        print(f"not in the store: {', '.join(unknown) or '(no tickers)'}", file=sys.stderr)
        return 2
    pin = date.fromisoformat(topup.pinned_today(meta))
    quarters = int(meta.get("quarters", 8))
    print(f"{'check' if args.check_only else 'add'} {args.metric} for {len(tickers)} companies from {args.archive}, "
          f"Submissions from {args.edgar_cache}, --quarters {quarters}, today pinned to {pin}", flush=True)
    batch = [ciks[t] for t in tickers]
    before = topup.row_state(REPO, batch) if args.proof and not args.check_only else None
    drift: dict[str, dict] = defaultdict(dict)
    replace, ro = metric_writer(db, args.metric, drift, write=not args.check_only)
    try:
        code = run_ingest(REPO, tickers, quarters, pin, db, archive_edgar(args.edgar_cache, args.archive, held, ciks),
                          replace, check_only=args.check_only)
    finally:
        ro.close()
    summary = {table: sorted(drift.get(table, {})) for table in TABLES}
    print("drift (rows the build gives differently, not written): "
          + ", ".join(f"{t} {len(v)} companies" for t, v in summary.items()))
    ok = code == 0
    if args.proof:
        report: dict[str, Any] = {"metric": args.metric, "check_only": args.check_only, "ingest_exit": code,
                                  "tickers": len(tickers), "pinned_today": pin.isoformat(), "quarters": quarters,
                                  "drift_companies": summary, "drift": {t: dict(v) for t, v in drift.items()}}
        if before is not None:
            proof = addition_proof(before, topup.row_state(REPO, batch), args.metric)
            report["proof"] = proof
            ok = ok and proof["ok"]
            print(f"proof: {'OK' if proof['ok'] else 'FAILED'}")
            for v in proof["violations"]:
                print(f"  {v}")
        args.proof.parent.mkdir(parents=True, exist_ok=True)
        args.proof.write_text(json.dumps(report, indent=1, default=str) + "\n")
        print(f"report -> {args.proof}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
