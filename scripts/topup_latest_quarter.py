"""Latest-quarter top-up of the SEC evidence: refresh the covered companies that filed after data/snapshot was built.

    .venv/bin/python scripts/topup_latest_quarter.py --auto --dry-run --out-dir OUT        # offline plan and estimate
    .venv/bin/python scripts/topup_latest_quarter.py --record-dev-baseline OUT/dev-baseline.json
    SEC_USER_AGENT="Name email" .venv/bin/python scripts/topup_latest_quarter.py --auto --out-dir OUT \\
        --dev-baseline OUT/dev-baseline.json --max-ledger-usd 70

Plan. --tickers names covered companies; --auto takes every company in data/snapshot whose SEC Submissions show a
10-Q or 10-K (or an amendment), or an 8-K with Item 2.02 (the earnings release the mapper reads), accepted after the
snapshot's newest filing for that company. A release is mapped once its paired 10-Q or 10-K is filed
(receipts.press_release.earnings_pairs), so a company's new pairs set the mapper's --quarters; a company with a new
earnings 8-K but no paired report yet is ingested and waits for a later top-up. Each new release is estimated at the
mean cost of the company's own last four mapped releases (their recorded model-call usage at the Standard price),
else at the median release. --dry-run reads cached Submissions only: no SEC request, no model call, no subprocess.
A real run (and --plan-only) first refetches Submissions older than --submissions-max-age-hours (live SEC, no model).

Preflight of a real run (exit 3): SEC_USER_AGENT set in the environment, data/snapshot built from the current
data/facts.sqlite (unless earlier batches in the same OUT changed it), and, before batch 1 touches anything,
scripts/build_split_table.py --check passes, the dev replay equals --dev-baseline and a scratch snapshot reproduces the
served examples, recordings and qualifier grid.

Steps per batch, in this order, each stopping the run with its exit code:
  10 disk floor: --disk-floor-gib free on the repo's volume
  11 spend guard: ledger + --factor x the plan's estimate for the batch <= --max-ledger-usd
  12 canonical row hashes of data/facts.sqlite (every column but facts.id) and data/pr_facts, before
  13 XBRL ingest with --refresh (live SEC; SEC_USER_AGENT from the environment), today pinned to the store's window
     date so earlier rows keep their window; its exit code, its FAILED list, a fresh audit file per company and its
     window line are all checked. The batch's companyfacts are then archived (raw kept), so the release gates and
     the split check read what ingest read rather than the older archive
  14 release mapping: receipts.press_release --dry-run, whose estimate must pass the spend guard too, then mapping
     with --validate and the XBRL gate, its budget capped at ledger + --factor x that estimate
  15 revenue confirmation (scripts/confirm_revenue.py) and an offline re-ingest of the companies it confirms
  16 label gate (scripts/relabel_release_facts.py): dry run, where a change outside the batch stops; apply; a second
     dry run must change nothing
  17 release gates (scripts/gate_release_facts.py), an offline re-ingest of the batch companies it lists, a dry-run
     second pass that drops and rejects nothing, a relabel dry run of 0, and the XBRL gate re-run over every live
     release file with nothing dropped or quarantined
  18 old-rows proof: hashes after; every company outside the batch keeps identical company, filing, fact and
     fiscal-period rows, release files and revenue-confirmation and derived-rejection entries; the batch's own
     changes are counted and reported
  19 dev replay (no network, no model) identical to --dev-baseline
  20 a scratch snapshot under --out-dir (scripts/build_snapshot.py --no-model; data/snapshot is never touched): the
     examples score as served, the three recordings replay, the qualifier grid is unchanged
  21 scripts/build_split_table.py --check (against data/snapshot and the archive, which now holds the new companyfacts)
  22 archives: scripts/archive_companyfacts.py and scripts/archive_exhibits.py with --delete-raw; a Ctrl-C does not
     reach them and stops the run only once they finish (each saves its index only at the end)
Other exit codes: 0 done, 2 usage, 3 preflight or plan, 130 interrupted.

Every subprocess's exit code is checked and any non-zero code stops the run. Reports: OUT/plan.json, OUT/preflight.json,
OUT/batch_<n>.json and OUT/summary.json; logs and step outputs in OUT/batch_<n>/. Their paths are relative to the repo
or OUT, and SEC_USER_AGENT and MODEL_API_KEY are redacted from every log before it is written. A re-run with --auto
and the same OUT skips the companies an earlier batch there completed. data/snapshot is not rebuilt here: the summary
lists the commands that follow the last batch.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PY = sys.executable
SNAPSHOT_DB = "data/snapshot/facts.sqlite"
SNAPSHOT_MANIFEST = "data/snapshot/manifest.json"
STORE_DB = "data/facts.sqlite"
PR_DIR = "data/pr_facts"
EDGAR_CACHE = "data/cache/edgar"
LEDGER = "data/cache/spark/press_release_spend.jsonl"
AUDIT_DIR = "data/ingest_audit"
SIDE_FILES = ("data/revenue_confirmations.json", "data/derived_rejections.json")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
XBRL_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
EARNINGS_FORMS = ("8-K", "8-K/A")
MAX_MAP_QUARTERS = 4               # the S&P 500 expansion mapped each company's latest four paired quarters
DEFAULT_RELEASE_USD = 0.025        # Phase B: $40.21 over 1,611 release files, used only when no release has usage
HELD_BACK = "blocked until confirmed"  # receipts.ingest's warning while contract revenue awaits confirm_revenue.py
TABLES = ("companies", "filings", "facts", "fiscal_periods")
FACT_IDENTITY = ("metric", "basis", "segment", "period_start", "period_end", "source", "accession")
DRY_RUN_LINE = re.compile(r"dry run: (\d+) model calls planned, (\d+) cached, ~\$([\d.]+) for the rest; "
                          r"ledger so far \$([\d.]+)")
STEPS = (("disk_floor", 10), ("spend_guard", 11), ("hashes_before", 12), ("xbrl_ingest", 13),
         ("release_mapping", 14), ("revenue_confirmation", 15), ("label_gate", 16), ("release_gates", 17),
         ("old_rows_proof", 18), ("dev_replay", 19), ("scratch_snapshot", 20), ("split_table", 21), ("archives", 22))
EXIT_USAGE, EXIT_PREFLIGHT, EXIT_INTERRUPTED = 2, 3, 130


class Preflight(Exception):
    """The run cannot start (exit 3): nothing was ingested, mapped or archived."""


class StepFailed(Exception):
    def __init__(self, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.detail = detail or {}


# --------------------------------------------------------------------------- small utilities
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zulu(value: Optional[str]) -> Optional[str]:
    """'2026-10-31T08:00:12.345+00:00' -> '2026-10-31T08:00:12Z'."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def sha_lines(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def ledger_usd(root: Path) -> float:
    """The press-release spend ledger's total, summed as receipts.press_release.SparkMapper sums it."""
    try:
        text = (root / LEDGER).read_text()
    except OSError:
        return 0.0
    total = 0.0
    for line in text.splitlines():
        try:
            total += float(json.loads(line)["cost_usd"])
        except (ValueError, KeyError, TypeError):
            pass
    return total


def free_gib(root: Path) -> float:
    return shutil.disk_usage(root).free / 2 ** 30


def store_meta(root: Path) -> dict:
    con = ro(root / STORE_DB)
    try:
        return dict(con.execute("SELECT key, value FROM meta"))
    finally:
        con.close()


def pinned_today(meta: dict) -> str:
    """The date receipts.ingest computed the stored window from: facts_start = today - (quarters + YoY) quarters."""
    from receipts.ingest import YOY_QUARTERS

    quarters = int(meta.get("quarters", 8))
    start = date.fromisoformat(meta["facts_start"])
    return (start + timedelta(days=int((quarters + YOY_QUARTERS) * 91.5) + 45)).isoformat()


class Scrubber:
    """Redacts secrets and turns the machine's absolute paths into repo- or OUT-relative ones."""

    def __init__(self, secrets: Iterable[str], paths: Iterable[tuple[Path, str]]):
        self.secrets = sorted({s for s in secrets if s and len(s) >= 5}, key=len, reverse=True)
        pairs: dict[str, str] = {}
        for base, label in paths:
            for form in dict.fromkeys((str(base), str(base.resolve()))):
                pairs.setdefault(form + os.sep, "" if label == "." else label + "/")
                pairs.setdefault(form, label)
        self.paths = sorted(pairs.items(), key=lambda kv: len(kv[0]), reverse=True)
        self.absolute = [p for p, _ in self.paths if not p.endswith(os.sep)]

    def __call__(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "<redacted>")
        for p, label in self.paths:
            text = text.replace(p, label)
        return text

    def leaks(self, text: str) -> list[str]:
        found = []
        if any(s in text for s in self.secrets):
            found.append("a secret")
        if any(p in text for p in self.absolute):
            found.append("an absolute path")
        return found


def secrets_from_env(env: dict) -> list[str]:
    out = []
    raw = env.get("SEC_USER_AGENT", "")
    ua = raw.strip().strip('"').strip("'")
    if ua:
        out += [raw.strip(), ua, *(token for token in ua.split() if "@" in token)]
    key = env.get("MODEL_API_KEY", "").strip()
    if len(key) >= 8:
        out.append(key)
    return out


def sec_user_agent(env: dict) -> str:
    """SEC_USER_AGENT from the environment only (never a file): 'Name email', not the env.example placeholder."""
    ua = env.get("SEC_USER_AGENT", "").strip().strip('"').strip("'")
    if not ua or "@" not in ua or "example.com" in ua:
        raise Preflight("SEC_USER_AGENT is not set in the environment (SEC requires 'Name email'); export it for "
                        "this run. The driver never reads .env and never writes the value anywhere.")
    return ua


def write_json(path: Path, obj: Any, scrub: Scrubber) -> None:
    """Write a report: scrubbed, and refused outright if a secret or an absolute path survived the scrub."""
    text = scrub(json.dumps(obj, indent=1, default=str, ensure_ascii=False)) + "\n"
    leaks = scrub.leaks(text)
    if leaks:
        raise RuntimeError(f"refusing to write {path.name}: it would contain {', '.join(leaks)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_gz(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), compresslevel=6, mtime=0))
    tmp.replace(path)


def run_command(argv: list[str], *, cwd: Path, log: Path, scrub: Scrubber, protect: bool = False) -> tuple[int, str]:
    """Run one step's subprocess, streaming its output through the scrubber into `log` (secrets never reach disk).

    protect: the child gets its own session, so a Ctrl-C at the terminal does not reach it; the driver notes the
    Ctrl-C, waits for the child and then stops (the archive scripts save their index only at the end)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    interrupted: list[int] = []

    def defer(signum: int, frame: Any) -> None:
        interrupted.append(signum)
        print("\nCtrl-C noted: waiting for this archive step to finish (its index is saved only at the end)",
              file=sys.stderr, flush=True)

    previous = signal.signal(signal.SIGINT, defer) if protect else None
    chunks: list[str] = []
    try:
        with log.open("w", encoding="utf-8") as fh, subprocess.Popen(
                argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", env=env, start_new_session=protect) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = scrub(line)
                fh.write(line)
                fh.flush()
                chunks.append(line)
            code = proc.wait()
    finally:
        if protect:
            signal.signal(signal.SIGINT, previous)
    if interrupted:
        raise KeyboardInterrupt
    return code, "".join(chunks)


# --------------------------------------------------------------------------- planning
def covered_companies(root: Path) -> list[dict]:
    """Every company in data/snapshot with its newest stored filing's acceptance time."""
    con = ro(root / SNAPSHOT_DB)
    try:
        rows = con.execute("SELECT c.ticker, c.cik, MAX(f.accepted_at) FROM companies c "
                           "LEFT JOIN filings f ON f.cik = c.cik GROUP BY c.cik ORDER BY c.ticker").fetchall()
    finally:
        con.close()
    return [{"ticker": t, "cik": int(cik), "newest": newest} for t, cik, newest in rows]


def snapshot_status(root: Path) -> dict:
    manifest = read_json(root / SNAPSHOT_MANIFEST)
    meta = store_meta(root)
    source = manifest.get("source_built_at")
    return {"fingerprint": manifest.get("fingerprint"), "built_at": manifest.get("built_at"),
            "source_built_at": source, "store_built_at": meta.get("built_at"),
            "current": bool(source) and source == meta.get("built_at"),
            "window": {"facts_start": meta.get("facts_start"), "filings_start": meta.get("filings_start"),
                       "quarters": int(meta.get("quarters", 8)), "pinned_today": pinned_today(meta)}}


def submissions_paths(root: Path, cik: int) -> tuple[str, Path]:
    url = SUBMISSIONS_URL.format(cik=cik)
    return url, root / EDGAR_CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


def load_submissions(root: Path, companies: list[dict], *, offline: bool, ua: Optional[str] = None,
                     max_age_s: Optional[float] = None) -> tuple[dict[int, dict], dict]:
    """Submissions JSON per CIK: the cache only when offline (no network at all), else refetched when older than
    max_age_s. The User-Agent is passed in, so receipts.edgar never loads .env here."""
    from receipts.edgar import Edgar, EdgarError

    ed = Edgar(user_agent=ua, cache_dir=root / EDGAR_CACHE, offline=offline,  # retries count as live requests
               max_live_requests=None if offline else 2 * len(companies) + 20)
    subs, missing, errors, fetched = {}, [], {}, []
    try:
        for c in companies:
            url, sidecar = submissions_paths(root, c["cik"])
            try:
                subs[c["cik"]] = ed.json(url, max_age=None if offline else max_age_s)
            except EdgarError as e:
                if offline:
                    missing.append(c["ticker"])
                else:
                    errors[c["ticker"]] = str(e)[:300]
                continue
            except ValueError as e:
                errors[c["ticker"]] = f"unreadable Submissions: {e}"
                continue
            stamp = read_json(sidecar).get("fetched_at")
            if stamp:
                fetched.append(zulu(stamp))
    finally:
        ed.close()
    return subs, {"companies": len(companies), "read": len(subs), "not_cached": missing, "errors": errors,
                  "live_requests": ed.live_requests, "cache_hits": ed.cache_hits,
                  "fetched_from": min(fetched) if fetched else None, "fetched_to": max(fetched) if fetched else None}


def recent_rows(sub: dict) -> list[dict]:
    """The Submissions `recent` block as receipts.edgar.Edgar.submissions shapes its rows, newest first."""
    from receipts.edgar import SUBMISSION_FIELDS

    block = sub.get("filings", {}).get("recent", {})
    rows = [{k: (block[k][i] if k in block else None) for k in SUBMISSION_FIELDS}
            for i in range(len(block.get("accessionNumber", [])))]
    rows.sort(key=lambda r: r["acceptanceDateTime"] or r["filingDate"] or "", reverse=True)
    return rows


def release_costs(pr_dir: Path) -> tuple[dict[str, list[tuple[str, float]]], float, str]:
    """ticker -> [(accepted_at, cost)] of every mapped release, priced from the usage its report records, and the
    median release cost as the fallback."""
    from receipts.press_release import cost_usd

    out: dict[str, list[tuple[str, float]]] = {}
    for path in sorted(pr_dir.glob("*/*.report.json")):
        report = read_json(path)
        if not isinstance(report, dict):
            continue
        cost = sum(cost_usd(o.get("usage")) for o in report.get("model_calls") or [] if isinstance(o, dict))
        if cost > 0:
            out.setdefault(path.parent.name, []).append((str(report.get("accepted_at") or ""), cost))
    every = [c for rows in out.values() for _, c in rows]
    if every:
        return out, statistics.median(every), f"median of {len(every)} mapped releases"
    return out, DEFAULT_RELEASE_USD, "Phase B mean per release file (no recorded usage here)"


def mapped_releases(folder: Path) -> set[str]:
    """Accessions of the 8-Ks this company already has a release file or report for."""
    return {p.name.split(".")[0] for p in folder.iterdir()} if folder.is_dir() else set()


def plan_company(company: dict, sub: Optional[dict], pr_dir: Path, costs: dict, fallback: tuple[float, str]) -> dict:
    from receipts.edgar import parse_accepted
    from receipts.press_release import earnings_pairs

    newest = parse_accepted(company["newest"]) if company["newest"] else None

    def after(row: dict) -> bool:
        acc = row.get("acceptanceDateTime")
        return bool(acc) and (newest is None or parse_accepted(acc) > newest)

    rows = recent_rows(sub) if sub else []
    new = [r for r in rows if after(r) and (r["form"] in XBRL_FORMS or (
        r["form"] in EARNINGS_FORMS and "2.02" in (r.get("items") or "")))]
    mapped = mapped_releases(pr_dir / company["ticker"])
    pairs = earnings_pairs(rows, MAX_MAP_QUARTERS)
    new_pairs = [(i, k, q) for i, (k, q) in enumerate(pairs) if after(q) and k["accessionNumber"] not in mapped]
    history = sorted(costs.get(company["ticker"], []))[-4:]
    if history:
        per_release, basis = sum(c for _, c in history) / len(history), f"mean of its last {len(history)} releases"
    else:
        per_release, basis = fallback
    return {
        "ticker": company["ticker"], "cik": company["cik"], "snapshot_newest": company["newest"],
        "submissions": "read" if sub else "not cached",
        "new_filings": [{"form": r["form"], "accession": r["accessionNumber"],
                         "accepted": zulu(r["acceptanceDateTime"]), "report_date": r.get("reportDate") or None,
                         "items": r.get("items") or None} for r in new],
        "new_pairs": [{"eightk": k["accessionNumber"], "eightk_accepted": zulu(k["acceptanceDateTime"]),
                       "report": q["accessionNumber"], "form": q["form"], "report_date": q.get("reportDate"),
                       "report_accepted": zulu(q["acceptanceDateTime"])} for _, k, q in new_pairs],
        "map_quarters": max((i + 1 for i, _, _ in new_pairs), default=0),
        "release_usd": round(per_release, 4), "estimate_usd": round(per_release * len(new_pairs), 4),
        "estimate_basis": basis,
    }


def completed_tickers(out: Path) -> tuple[set[str], int]:
    """Tickers of the batches an earlier run in OUT completed, and the highest batch number there."""
    done, highest = set(), 0
    for path in out.glob("batch_*.json"):
        m = re.fullmatch(r"batch_(\d+)\.json", path.name)
        if not m:
            continue
        highest = max(highest, int(m.group(1)))
        report = read_json(path)
        if report.get("ok") is True:
            done.update(report.get("tickers", []))
    return done, highest


def build_plan(root: Path, out: Path, args: argparse.Namespace, *, offline: bool, ua: Optional[str]) -> dict:
    status = snapshot_status(root)
    companies = covered_companies(root)
    by_ticker = {c["ticker"]: c for c in companies}
    if args.tickers:
        unknown = [t for t in args.tickers if t not in by_ticker]
        if unknown:
            raise Preflight(f"not covered by data/snapshot: {', '.join(unknown)} (a top-up refreshes covered "
                            "companies; receipts.ingest adds new ones)")
        selected = [by_ticker[t] for t in args.tickers]
    else:
        selected = companies
    subs, info = load_submissions(root, selected, offline=offline, ua=ua,
                                  max_age_s=args.submissions_max_age_hours * 3600)
    if info["errors"]:
        raise Preflight("Submissions could not be read for " + "; ".join(f"{t}: {e}" for t, e in
                                                                       sorted(info["errors"].items())[:5]))
    costs, median, basis = release_costs(root / PR_DIR)
    done, highest = completed_tickers(out) if args.auto else (set(), completed_tickers(out)[1])
    planned, skipped = [], []
    for c in selected:
        p = plan_company(c, subs.get(c["cik"]), root / PR_DIR, costs, (median, basis))
        if args.auto and not p["new_filings"]:
            continue
        if c["ticker"] in done:
            skipped.append(c["ticker"])
            continue
        planned.append(p)
    batches = []
    for i in range(0, len(planned), args.batch_size):
        chunk = planned[i:i + args.batch_size]
        mapped = [p for p in chunk if p["map_quarters"]]
        batches.append({"batch": highest + 1 + len(batches), "tickers": [p["ticker"] for p in chunk],
                        "map_tickers": [p["ticker"] for p in mapped],
                        "map_quarters": max((p["map_quarters"] for p in mapped), default=0),
                        "new_releases": sum(len(p["new_pairs"]) for p in chunk),
                        "estimate_usd": round(sum(p["estimate_usd"] for p in chunk), 4)})
    ledger = ledger_usd(root)
    guard, projected = [], ledger
    for b in batches:
        total = projected + args.factor * b["estimate_usd"]
        guard.append({"batch": b["batch"], "check": f"{projected:.4f} + {args.factor} x {b['estimate_usd']:.4f} = "
                                                   f"{total:.4f}" + (f" vs {args.max_ledger_usd:.2f}"
                                                                     if args.max_ledger_usd is not None else ""),
                      "ok": None if args.max_ledger_usd is None else total <= args.max_ledger_usd})
        projected += b["estimate_usd"]
    return {
        "generated_by": "scripts/topup_latest_quarter.py", "created_at": utc_now(),
        "mode": "dry-run" if offline else "plan-only" if args.plan_only else "run",
        "selection": "tickers" if args.tickers else "auto",
        "snapshot": {k: v for k, v in status.items() if k != "window"}, "window": status["window"],
        "submissions": info, "covered_companies": len(companies), "skipped_done_earlier": skipped,
        "release_cost_fallback": {"usd": round(median, 4), "basis": basis},
        "companies": planned, "batches": batches,
        "estimate_usd": round(sum(b["estimate_usd"] for b in batches), 4),
        "ledger_usd": round(ledger, 4), "factor": args.factor, "max_ledger_usd": args.max_ledger_usd,
        "guard": guard,
    }


def print_plan(plan: dict) -> None:
    mode = {"dry-run": "dry run: cached Submissions only, no SEC request, no model call",
            "plan-only": "plan only: Submissions refreshed from SEC, nothing ingested, no model call",
            "run": "run"}[plan["mode"]]
    snap, win, sub = plan["snapshot"], plan["window"], plan["submissions"]
    print(f"NoCap latest-quarter top-up · {mode}")
    print(f"snapshot {str(snap['fingerprint'])[:12]} built {snap['built_at']} from the store of "
          f"{snap['source_built_at']}" + ("" if snap["current"] else
                                          f"; data/facts.sqlite was rebuilt at {snap['store_built_at']}: STALE"))
    print(f"window: facts since {win['facts_start']}, filings since {win['filings_start']} "
          f"({win['quarters']} quarters); ingest runs with today pinned to {win['pinned_today']}")
    print(f"Submissions: {sub['read']} of {sub['companies']} read ({sub['live_requests']} live requests), cached "
          f"{sub['fetched_from']} .. {sub['fetched_to']}" + (f"; not cached: {', '.join(sub['not_cached'][:10])}"
                                                            if sub["not_cached"] else ""))
    if plan["mode"] == "dry-run":
        print("  (a real run first refetches Submissions cached longer ago than --submissions-max-age-hours)")
    if plan["skipped_done_earlier"]:
        print(f"skipped, completed by an earlier batch in this out-dir: {', '.join(plan['skipped_done_earlier'])}")
    if plan["companies"]:
        print(f"{'ticker':<7}{'newest in snapshot':<22}{'releases':>9}{'~USD':>9}  new filings")
        for c in plan["companies"]:
            filings = ", ".join(f"{f['form']}{'(2.02)' if f['form'] in EARNINGS_FORMS else ''} {f['accepted'][:10]}"
                                for f in c["new_filings"][:4]) + (" ..." if len(c["new_filings"]) > 4 else "")
            print(f"{c['ticker']:<7}{str(c['snapshot_newest'] or '-'):<22}{len(c['new_pairs']):>9}"
                  f"{c['estimate_usd']:>9.4f}  {filings or '(nothing new in Submissions)'}")
    n, fallback = len(plan["companies"]), plan["release_cost_fallback"]
    print(f"{n} companies in {len(plan['batches'])} batches; {sum(b['new_releases'] for b in plan['batches'])} new "
          f"releases to map; mapping-cost estimate ${plan['estimate_usd']:.4f} (each new release at its company's "
          f"recent cost, else the {fallback['basis']}: ${fallback['usd']:.4f})")
    cap = plan["max_ledger_usd"]
    print(f"ledger ${plan['ledger_usd']:.4f}; spend guard per batch, ledger + {plan['factor']} x estimate"
          + (f" <= ${cap:.2f}:" if cap is not None else " (pass --max-ledger-usd to check it):"))
    for b, g in zip(plan["batches"], plan["guard"]):
        verdict = "" if g["ok"] is None else "  ok" if g["ok"] else "  STOP"
        print(f"  batch {b['batch']}: {len(b['tickers'])} companies, map {len(b['map_tickers'])} at --quarters "
              f"{b['map_quarters']}: {g['check']}{verdict}")


# --------------------------------------------------------------------------- row hashes and the old-rows proof
def side_entries(path: Path) -> dict[str, str]:
    data = read_json(path)
    return {str(k): hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()
            for k, v in data.items()} if isinstance(data, dict) else {}


def row_state(root: Path, batch_ciks: Iterable[int]) -> dict:
    """Canonical rows of every table (all columns but facts.id, the rowid a re-ingest renumbers): digests overall
    and per company, the batch companies' rows themselves, a hash of every release file and of every entry of the
    revenue-confirmation and derived-rejection files."""
    wanted = {int(c) for c in batch_ciks}
    con = ro(root / STORE_DB)
    state: dict[str, Any] = {"tables": {}, "batch_rows": {}, "meta": {}}
    try:
        state["meta"] = dict(con.execute("SELECT key, value FROM meta"))
        for table in TABLES:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            keep = [c for c in cols if not (table == "facts" and c == "id")]
            at = keep.index("cik")
            quoted = ", ".join(f'"{c}"' for c in keep)
            by_cik: dict[int, list[str]] = {}
            for row in con.execute(f'SELECT {quoted} FROM "{table}"'):
                by_cik.setdefault(int(row[at]), []).append(json.dumps(list(row), separators=(",", ":")))
            for lines in by_cik.values():
                lines.sort()
            every = sorted(line for lines in by_cik.values() for line in lines)
            state["tables"][table] = {"columns": keep, "rows": len(every), "sha256": sha_lines(every),
                                      "by_cik": {str(c): [len(v), sha_lines(v)] for c, v in sorted(by_cik.items())}}
            state["batch_rows"][table] = {str(c): by_cik.get(c, []) for c in sorted(wanted)}
    finally:
        con.close()
    pr = root / PR_DIR
    state["pr_files"] = {p.relative_to(pr).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(pr.rglob("*")) if p.is_file()} if pr.is_dir() else {}
    state["side_files"] = {name: side_entries(root / name) for name in SIDE_FILES}
    return state


def old_rows_proof(before: dict, after: dict, batch_ciks: set[str], batch_tickers: set[str],
                   names: dict[str, str]) -> dict:
    """Rows and files outside the batch must be identical; the batch's own changes are counted."""
    proof: dict[str, Any] = {"ok": True, "violations": [], "tables": {}, "batch_companies_with_removals": {},
                             "pr_files": {}, "side_files": {}}
    for table in TABLES:
        b, a = before["tables"][table], after["tables"][table]
        outside = sorted((c for c in set(b["by_cik"]) | set(a["by_cik"])
                          if c not in batch_ciks and b["by_cik"].get(c) != a["by_cik"].get(c)), key=int)
        cols = a["columns"]
        key_at = [cols.index(k) for k in FACT_IDENTITY] if table == "facts" else []
        removed = changed = gone = added = 0
        samples = []
        for cik in sorted(batch_ciks, key=int):
            old = Counter(before["batch_rows"][table].get(cik, []))
            new = Counter(after["batch_rows"][table].get(cik, []))
            lost, came = old - new, new - old
            added += sum(came.values())
            n_lost = sum(lost.values())
            removed += n_lost
            if table == "facts" and n_lost:
                keys = {tuple(json.loads(line)[i] for i in key_at) for line in came}
                for line, n in lost.items():
                    row = json.loads(line)
                    if tuple(row[i] for i in key_at) in keys:
                        changed += n
                    else:
                        gone += n
                        if len(samples) < 10:
                            samples.append(dict(zip(cols, row)))
            elif n_lost and len(samples) < 5:
                samples += [dict(zip(cols, json.loads(line))) for line in list(lost)[:5 - len(samples)]]
            if n_lost:
                proof["batch_companies_with_removals"].setdefault(names.get(cik, cik), {})[table] = n_lost
        entry = {"before": b["rows"], "after": a["rows"], "before_sha256": b["sha256"], "after_sha256": a["sha256"],
                 "outside_batch_changed": len(outside), "batch_removed": removed, "batch_added": added}
        if table == "facts":
            entry.update(batch_removed_changed=changed, batch_removed_gone=gone)
        if samples:
            entry["batch_removed_samples"] = samples
        proof["tables"][table] = entry
        if outside:
            proof["ok"] = False
            proof["violations"].append(f"{table}: rows changed for {len(outside)} companies outside the batch: "
                                       + ", ".join(names.get(c, c) for c in outside[:10]))
    bf, af = before["pr_files"], after["pr_files"]

    def owner(rel: str) -> Optional[str]:
        return rel.split("/")[0] if "/" in rel else None

    outside_files = sorted(k for k in set(bf) | set(af) if owner(k) not in batch_tickers and bf.get(k) != af.get(k))
    proof["pr_files"] = {
        "before": len(bf), "after": len(af), "outside_batch_changed": outside_files[:50],
        "batch_added": sorted(k for k in af if k not in bf and owner(k) in batch_tickers),
        "batch_changed": sorted(k for k in bf if k in af and bf[k] != af[k] and owner(k) in batch_tickers),
        "batch_removed": sorted(k for k in bf if k not in af and owner(k) in batch_tickers)}
    if outside_files:
        proof["ok"] = False
        proof["violations"].append(f"{len(outside_files)} release files outside the batch changed: "
                                   + ", ".join(outside_files[:5]))
    for name in SIDE_FILES:
        b, a = before["side_files"].get(name, {}), after["side_files"].get(name, {})
        outside = sorted(k for k in set(b) | set(a) if k not in batch_tickers and b.get(k) != a.get(k))
        proof["side_files"][name] = {"outside_batch_changed": outside,
                                     "batch_changed": sorted(k for k in set(b) | set(a)
                                                             if k in batch_tickers and b.get(k) != a.get(k))}
        if outside:
            proof["ok"] = False
            proof["violations"].append(f"{name}: entries outside the batch changed: {', '.join(outside[:10])}")
    return proof


def compare_replay(baseline: dict, now: dict) -> dict:
    keys = sorted(set(baseline) | set(now))
    diff = [k for k in keys if baseline.get(k) != now.get(k)]
    return {"drafts": len(now), "claims": sum(len(v) for v in now.values()), "identical": not diff,
            "differences": len(diff),
            "first_differences": [{"key": k, "baseline": baseline.get(k), "now": now.get(k)} for k in diff[:10]]}


# --------------------------------------------------------------------------- the run
class TopUp:
    def __init__(self, root: Path, out: Path, args: argparse.Namespace, scrub: Scrubber):
        self.root, self.out, self.args, self.scrub = root, out, args, scrub
        self.scratch = (args.scratch_snapshot or out / "scratch_snapshot").resolve()
        self.plan: dict = {}
        self.by_ticker: dict[str, dict] = {}
        self.window: dict = {}
        self.baseline: dict = {}

    def rel(self, path: Path) -> str:
        return self.scrub(str(path))

    def helper(self, name: str, *args: Any) -> list[str]:
        return [PY, str(SCRIPT), "helper", name, "--repo-root", str(self.root), *map(str, args)]

    def ingest_cmd(self, tickers: list[str], *, refresh: bool = False, offline: bool = False) -> list[str]:
        args = ["--tickers", ",".join(tickers), "--quarters", str(self.window["quarters"])]
        if refresh:
            args += ["--refresh", "--max-requests", str(self.args.ingest_max_requests)]
        if offline:
            args.append("--offline")
        return [*self.helper("ingest", "--pin-date", self.window["pinned_today"]), "--", *args]

    def execute(self, name: str, argv: list[str], log_dir: Path, record: list, *,
                protect: bool = False) -> tuple[int, str]:
        log = log_dir / f"{name}.log"
        t0 = time.monotonic()
        code, text = run_command(argv, cwd=self.root, log=log, scrub=self.scrub, protect=protect)
        record.append({"name": name, "argv": [self.scrub("python" if a == PY else str(a)) for a in argv],
                       "exit_code": code, "seconds": round(time.monotonic() - t0, 1), "log": self.rel(log)})
        return code, text

    def call(self, name: str, argv: list[str], log_dir: Path, record: list, *, protect: bool = False) -> str:
        code, text = self.execute(name, argv, log_dir, record, protect=protect)
        if code != 0:
            raise StepFailed(f"{name} exited {code}; see {self.rel(log_dir / f'{name}.log')}")
        return text

    def split_check(self, log_dir: Path, record: list) -> dict:
        """scripts/build_split_table.py --check; on a difference, say whether the table itself or only its decision
        record would change (a repeated split-ratio fact in a new context changes only the record)."""
        code, text = self.execute("split_check", [PY, "scripts/build_split_table.py", "--check"], log_dir, record)
        lines = text.strip().splitlines()
        info = {"result": lines[-1] if lines else "", "differs": re.findall(r"^--- (\S+)", text, re.M)}
        if code != 0:
            what = ("receipts/splits.py would change: a split the committed table lacks"
                    if "receipts/splits.py" in info["differs"] else
                    "only the split decision record would change (new split-ratio facts, no new split)"
                    if info["differs"] else "see the log")
            raise StepFailed(f"split_check exited {code}: {what}. Run scripts/build_split_table.py, review and commit "
                             f"its output, then re-run with the same --out-dir (log "
                             f"{self.rel(log_dir / 'split_check.log')})", info)
        return info

    def replan(self, tickers: list[str]) -> dict[str, dict]:
        """The batch's companies planned again from the Submissions ingest just cached (offline, no network)."""
        companies = [{"ticker": t, "cik": self.by_ticker[t]["cik"], "newest": self.by_ticker[t]["snapshot_newest"]}
                     for t in tickers]
        subs, _ = load_submissions(self.root, companies, offline=True)
        costs, median, basis = release_costs(self.root / PR_DIR)
        return {c["ticker"]: plan_company(c, subs.get(c["cik"]), self.root / PR_DIR, costs, (median, basis))
                for c in companies}

    # ---------------------------------------------------------------- preflight gates (untouched data)
    def preflight_gates(self) -> None:
        folder, record = self.out / "preflight", []
        report: dict[str, Any] = {"started_at": utc_now(), "commands": record}
        try:
            report["split_table"] = self.split_check(folder, record)["result"]
            replay = folder / "dev_replay.json"
            self.call("dev_replay", self.helper("dev-replay", "--out", replay), folder, record)
            report["dev_replay"] = compare_replay(self.baseline, json.loads(replay.read_text()))
            if not report["dev_replay"]["identical"]:
                raise StepFailed("the dev replay of the untouched data differs from --dev-baseline: record a new "
                                 "baseline (--record-dev-baseline) for the current code and data")
            report["scratch_snapshot"] = self.check_scratch_snapshot(folder, record)
        except Exception as e:  # StepFailed, or an unreadable output: nothing has been touched yet
            reason = str(e) if isinstance(e, StepFailed) else f"{type(e).__name__}: {e}"
            report.update(ok=False, reason=self.scrub(reason))
            write_json(self.out / "preflight.json", report, self.scrub)
            raise Preflight(f"preflight: {reason}") from e
        report["ok"] = True
        write_json(self.out / "preflight.json", report, self.scrub)

    def check_scratch_snapshot(self, folder: Path, record: list) -> dict:
        text = self.call("scratch_snapshot", self.helper("scratch-snapshot", self.scratch), folder, record)
        out = folder / "snapshot_check.json"
        self.call("snapshot_check", self.helper("check-snapshot", self.scratch, "--expected",
                                                self.root / "data/snapshot", "--out", out), folder, record)
        check = json.loads(out.read_text())
        info = {"build": next(iter(re.findall(r"^data/snapshot: .*$", text, re.M)), None),
                "recordings": check["recordings"], "qualifier_grid_identical": check["qualifier_grid_identical"],
                "examples": check["examples"], "examples_identical": check["examples_identical"],
                "bytes": check["snapshot_bytes"], "facts_sqlite_bytes": check["facts_sqlite_bytes"]}
        problems = [f"recording {k} no longer replays" for k, ok in check["recordings"].items() if not ok]
        if not check["qualifier_grid_identical"]:
            problems.append("the qualifier grid changed")
        if not check["examples_identical"]:
            problems.append("the examples no longer score as served: " + json.dumps(
                {k: v["counts"] for k, v in check["examples"].items()}, sort_keys=True))
        if problems:
            raise StepFailed("; ".join(problems), info)
        return info

    # ---------------------------------------------------------------- driver
    def run_batches(self) -> int:
        led0, results = ledger_usd(self.root), []
        code = 0
        for spec in self.plan["batches"]:
            code = Batch(self, spec).run()
            results.append({"batch": spec["batch"], "report": f"batch_{spec['batch']}.json", "ok": code == 0,
                            "exit_code": code, "companies": len(spec["tickers"])})
            if code:
                break
        led1 = ledger_usd(self.root)
        summary = {"generated_by": "scripts/topup_latest_quarter.py", "finished_at": utc_now(), "plan": "plan.json",
                   "batches": results, "ledger_usd_start": round(led0, 4), "ledger_usd_end": round(led1, 4),
                   "spent_usd": round(led1 - led0, 4), "free_gib_end": round(free_gib(self.root), 2),
                   "ok": code == 0 and len(results) == len(self.plan["batches"]),
                   "next": NEXT_STEPS if code == 0 else []}
        write_json(self.out / "summary.json", summary, self.scrub)
        print(f"top-up {'done' if summary['ok'] else 'STOPPED'}: {sum(r['ok'] for r in results)} of "
              f"{len(self.plan['batches'])} batches, spend ${summary['spent_usd']:.4f}, ledger "
              f"${led1:.4f}, {summary['free_gib_end']:.2f} GiB free")
        if summary["ok"]:
            print("next, from the repo root:\n  " + "\n  ".join(NEXT_STEPS))
        return code


NEXT_STEPS = [".venv/bin/python scripts/build_snapshot.py --no-model",
              ".venv/bin/python scripts/build_split_table.py",
              "git diff --stat receipts/splits.py eval/evidence_expansion/sp500-splits-20261031.json data/snapshot",
              ".venv/bin/python -m pytest -q -p no:cacheprovider"]


class Batch:
    def __init__(self, run: TopUp, spec: dict):
        self.topup, self.spec, self.n = run, spec, spec["batch"]
        self.root, self.args = run.root, run.args
        self.tickers = list(spec["tickers"])
        self.dir = run.out / f"batch_{self.n}"
        self.ciks = {t: run.by_ticker[t]["cik"] for t in self.tickers}
        self.names = {str(c): t for t, c in self.ciks.items()}
        self.record: list = []
        self.before: Optional[dict] = None
        self.report: dict[str, Any] = {
            "batch": self.n, "tickers": self.tickers, "map_tickers": spec["map_tickers"],
            "map_quarters": spec["map_quarters"], "estimate_usd": spec["estimate_usd"], "started_at": utc_now(),
            "window": run.window, "step_order": [], "steps": {}}

    def save(self) -> None:
        write_json(self.topup.out / f"batch_{self.n}.json", self.report, self.topup.scrub)

    def cmd(self, name: str, argv: list[str], *, protect: bool = False) -> str:
        return self.topup.call(name, argv, self.dir, self.record, protect=protect)

    def run(self) -> int:
        self.dir.mkdir(parents=True, exist_ok=True)
        print(f"batch {self.n}: {len(self.tickers)} companies ({', '.join(self.tickers[:8])}"
              f"{' ...' if len(self.tickers) > 8 else ''})", flush=True)
        for i, (name, code) in enumerate(STEPS, 1):
            self.record = []
            t0 = time.monotonic()
            print(f"  [{i:>2}/{len(STEPS)}] {name} ...", end="", flush=True)
            try:
                result = getattr(self, f"step_{name}")() or {}
            except KeyboardInterrupt:
                return self.stop(name, EXIT_INTERRUPTED, "interrupted (Ctrl-C)", {}, t0)
            except StepFailed as e:
                return self.stop(name, code, str(e), e.detail, t0)
            except Exception as e:  # a bug or an unreadable step output: stop with this step's code
                return self.stop(name, code, f"{type(e).__name__}: {e}", {}, t0)
            self.report["step_order"].append(name)
            self.report["steps"][name] = {"ok": True, **result, "commands": self.record,
                                          "seconds": round(time.monotonic() - t0, 1)}
            self.save()
            print(f" ok ({time.monotonic() - t0:.1f} s)", flush=True)
        end = {"ledger_usd": round(ledger_usd(self.root), 4), "free_gib": round(free_gib(self.root), 2)}
        self.report.update(ok=True, finished_at=utc_now(), end=end)
        self.save()
        print(f"BATCH {self.n} OK", flush=True)
        return 0

    def stop(self, name: str, code: int, reason: str, detail: dict, t0: float) -> int:
        reason = self.topup.scrub(reason)
        self.report["step_order"].append(name)
        self.report["steps"][name] = {"ok": False, "reason": reason, **detail, "commands": self.record,
                                      "seconds": round(time.monotonic() - t0, 1)}
        self.report.update(ok=False, failed={"step": name, "exit_code": code, "reason": reason},
                           finished_at=utc_now())
        self.save()
        print(f" FAILED\nBATCH {self.n} STOPPED at {name} (exit {code}): {reason}", flush=True)
        return code

    # ---------------------------------------------------------------- helpers
    def audits(self, tickers: Optional[Iterable[str]] = None) -> dict[str, dict]:
        folder = self.root / AUDIT_DIR
        return {t: read_json(folder / f"{t}.json") for t in (tickers or self.tickers)
                if (folder / f"{t}.json").is_file()}

    def check_ingest(self, text: str, tickers: list[str], t0: float) -> dict:
        """The exit code was 0; also require ingest's own report: no FAILED company, 'All companies ingested.', an
        audit file it wrote for every ticker, and the stored window (batch 3 of Phase B lost 7 companies to a crash
        whose exit code was accepted)."""
        failed = dict(re.findall(r"^(\S+)\s+FAILED: (.*)$", text, re.M))
        edgar = re.findall(r"^EDGAR: .*$", text, re.M)
        window = re.search(r"facts ending since (\S+), filings since (\S+)", text)
        folder = self.root / AUDIT_DIR
        stale = [t for t in tickers if not (folder / f"{t}.json").is_file()
                 or (folder / f"{t}.json").stat().st_mtime < t0 - 2]
        want = (self.topup.window["facts_start"], self.topup.window["filings_start"])
        info = {"failed": failed, "edgar": edgar[-1] if edgar else None,
                "window": list(window.groups()) if window else None}
        problems = []
        if failed:
            problems.append(f"ingest FAILED for {', '.join(sorted(failed))}")
        if not edgar or "All companies ingested." not in edgar[-1]:
            problems.append("ingest did not report every company ingested")
        if stale:
            problems.append(f"no audit file written for {', '.join(stale)}")
        if not window or window.groups() != want:
            problems.append(f"ingest's window {info['window']} is not the stored {list(want)}")
        if problems:
            raise StepFailed("; ".join(problems), info)
        return info

    def relabel(self, name: str) -> dict:
        out = self.dir / f"{name}.json"
        self.cmd(name, [PY, "scripts/relabel_release_facts.py", "--dry-run", "--out", str(out)])
        return json.loads(out.read_text())

    def gates(self, name: str, *, dry: bool) -> dict:
        out = self.dir / f"{name}.json"
        self.cmd(name, [PY, "scripts/gate_release_facts.py", *(["--dry-run"] if dry else []), "--out", str(out),
                        *self.tickers])
        return json.loads(out.read_text())

    # ---------------------------------------------------------------- steps
    def step_disk_floor(self) -> dict:
        free = free_gib(self.root)
        info = {"free_gib": round(free, 2), "floor_gib": self.args.disk_floor_gib}
        if free < self.args.disk_floor_gib:
            raise StepFailed(f"{free:.2f} GiB free on the repo's volume, under the {self.args.disk_floor_gib} GiB "
                             "floor", info)
        return info

    def step_spend_guard(self) -> dict:
        led, est, f, cap = ledger_usd(self.root), self.spec["estimate_usd"], self.args.factor, self.args.max_ledger_usd
        total = led + f * est
        info = {"ledger_usd": round(led, 4), "estimate_usd": est, "factor": f, "max_ledger_usd": cap,
                "check": f"{led:.4f} + {f} x {est:.4f} = {total:.4f} vs {cap:.2f}"}
        if total > cap:
            raise StepFailed(f"spend guard: ledger {led:.4f} + {f} x estimate {est:.4f} = {total:.4f} > {cap:.2f}",
                             info)
        return info

    def step_hashes_before(self) -> dict:
        self.before = row_state(self.root, self.ciks.values())
        write_gz(self.dir / "hashes_before.json.gz", self.before)
        return {"rows": {t: v["rows"] for t, v in self.before["tables"].items()},
                "sha256": {t: v["sha256"] for t, v in self.before["tables"].items()},
                "release_files": len(self.before["pr_files"]),
                "file": self.topup.rel(self.dir / "hashes_before.json.gz")}

    def step_xbrl_ingest(self) -> dict:
        t0 = time.time()
        text = self.cmd("ingest", self.topup.ingest_cmd(self.tickers, refresh=True))
        info = self.check_ingest(text, self.tickers, t0)
        audits = self.audits()
        moved = {t: a.get("cik") for t, a in audits.items() if a.get("cik") != self.ciks[t]}
        if moved:
            raise StepFailed(f"SEC now maps these tickers to other CIKs: {moved}", info)
        ciks = sorted({int(a["cik"]) for a in audits.values()})
        self.cmd("archive_companyfacts_sync", [PY, "scripts/archive_companyfacts.py", "archive", *map(str, ciks)])
        info["companyfacts_archived"] = len(ciks)
        info["warnings"] = {t: a.get("warnings", []) for t, a in audits.items() if a.get("warnings")}
        return info

    def step_release_mapping(self) -> dict:
        replanned = self.topup.replan(self.tickers)
        mapped = [t for t in self.tickers if replanned[t]["map_quarters"]]
        quarters = max((replanned[t]["map_quarters"] for t in mapped), default=0)
        info: dict[str, Any] = {"map_tickers": mapped, "quarters": quarters,
                                "new_releases": {t: [p["eightk"] for p in replanned[t]["new_pairs"]] for t in mapped},
                                "planned_map_tickers": self.spec["map_tickers"]}
        if not mapped:
            info["skipped"] = "no new earnings 8-K has its paired 10-Q or 10-K filed yet"
            return info
        n = str(self.args.map_max_sec_requests)
        text = self.cmd("map_dry_run", [PY, "-m", "receipts.press_release", "--tickers", *mapped, "--quarters",
                                        str(quarters), "--validate", "--dry-run", "--max-sec-requests", n])
        m = DRY_RUN_LINE.search(text)
        if not m:
            raise StepFailed("the release dry run printed no estimate line", info)
        calls, cached, est, led = int(m.group(1)), int(m.group(2)), float(m.group(3)), float(m.group(4))
        f, cap = self.args.factor, self.args.max_ledger_usd
        info["dry_run"] = {"calls": calls, "cached": cached, "estimate_usd": est, "ledger_usd": led,
                           "check": f"{led:.4f} + {f} x {est:.4f} = {led + f * est:.4f} vs {cap:.2f}",
                           "errors": re.findall(r"ERROR (.*)$", text, re.M)[:50]}
        if led + f * est > cap:
            raise StepFailed(f"spend guard on the release dry run: {info['dry_run']['check']}", info)
        budget = min(round(led + f * est + 0.01, 2), cap)
        text = self.cmd("map", [PY, "-m", "receipts.press_release", "--tickers", *mapped, "--quarters", str(quarters),
                                "--validate", "--budget-usd", f"{budget:.2f}", "--concurrency",
                                str(self.args.concurrency), "--max-sec-requests", n,
                                "--validation-out", str(self.dir / "validation.json")])
        info["map"] = {"budget_usd": budget, "spark": next(iter(re.findall(r"^Spark: .*$", text, re.M)), None),
                       "sec": (re.findall(r"^SEC: .*$", text, re.M) or [None])[-1],
                       "xbrl_gate": next(iter(re.findall(r"^XBRL gate.*$", text, re.M)), None),
                       "ledger_usd": round(ledger_usd(self.root), 4)}
        written, missing, failed_calls = [], [], []
        for t in mapped:
            folder = self.root / PR_DIR / t
            for p in replanned[t]["new_pairs"]:
                report = read_json(folder / f"{p['eightk']}.report.json")
                if not report:
                    missing.append(f"{t} {p['eightk']}")
                    continue
                kind = ("quarantined" if (folder / f"{p['eightk']}.jsonl.quarantined").is_file()
                        else "live" if (folder / f"{p['eightk']}.jsonl").is_file() else "no file")
                calls_ = report.get("model_calls") or []
                written.append({"ticker": t, "accession": p["eightk"], "file": kind, "facts": report.get("facts"),
                                "calls": len(calls_)})
                failed_calls += [f"{t} {p['eightk']} tables {o.get('tables')}: {o.get('status')}"
                                 for o in calls_ if o.get("status") != "ok"]
        info.update(written=written, not_written=missing, failed_calls=failed_calls)
        if missing or failed_calls:
            print(f"\n      warning: {len(missing)} new releases not written, {len(failed_calls)} model calls not ok "
                  "(see the batch report); a re-run retries them", end="", flush=True)
        return info

    def step_revenue_confirmation(self) -> dict:
        held = sorted(t for t, a in self.audits().items() if any(HELD_BACK in w for w in a.get("warnings", [])))
        confirmed: list[str] = []
        info: dict[str, Any] = {"held_back": held}
        if held:
            text = self.cmd("confirm_revenue", [PY, "scripts/confirm_revenue.py", *held])
            confirmed = sorted(set(re.findall(r"^(\S+)\s+CONFIRMED", text, re.M)) & set(held))
            if confirmed:
                t0 = time.time()
                info["reingest"] = self.check_ingest(
                    self.cmd("reingest_confirmed", self.topup.ingest_cmd(confirmed, offline=True)), confirmed, t0)
        info.update(confirmed=confirmed, still_held=sorted(set(held) - set(confirmed)))
        return info

    def step_label_gate(self) -> dict:
        dry = self.relabel("relabel_dry")
        outside = sorted({c["file"] for c in dry["changes"] if c["file"].split("/")[0] not in self.tickers})
        info: dict[str, Any] = {"relabelled": dry["changed"], "changes": dry["changes"][:500]}
        if outside:
            raise StepFailed(f"the relabel would change {len(outside)} files outside the batch: {outside[:5]}", info)
        if dry["changed"]:
            self.cmd("relabel", [PY, "scripts/relabel_release_facts.py", "--out", str(self.dir / "relabel.json")])
            again = self.relabel("relabel_dry2")
            if again["changed"]:
                raise StepFailed(f"label gate: {again['changed']} mismatches remain after the relabel", info)
        info["after"] = 0
        return info

    def step_release_gates(self) -> dict:
        g = self.gates("release_gates", dry=False)
        reingest = sorted(set(g["reingest"]) & set(self.tickers))
        info: dict[str, Any] = {
            "dropped": len(g["dropped"]), "by_rule": dict(Counter(r.get("rule") for r in g["dropped"])),
            "files_changed": g["files_changed"], "new_rejections": {t: len(v) for t, v in g["new_rejections"].items()},
            "reingest": reingest, "reingest_listed_outside_batch": sorted(set(g["reingest"]) - set(self.tickers))}
        if reingest:
            t0 = time.time()
            info["reingest_check"] = self.check_ingest(
                self.cmd("reingest_rejected", self.topup.ingest_cmd(reingest, offline=True)), reingest, t0)
        g2 = self.gates("release_gates_dry", dry=True)
        again = sorted(set(g2["reingest"]) & set(self.tickers))
        info["second_pass"] = {"dropped": len(g2["dropped"]), "new_rejections": sorted(g2["new_rejections"]),
                               "reingest": again}
        if g2["dropped"] or g2["new_rejections"] or again:
            raise StepFailed("the release gates' dry-run second pass still drops, rejects or lists a re-ingest", info)
        relabel = self.relabel("relabel_dry3")
        info["relabel_after_gates"] = relabel["changed"]
        if relabel["changed"]:
            raise StepFailed(f"after the release gates the relabel dry run finds {relabel['changed']} changes", info)
        out = self.dir / "xbrl_gate_rerun.json"
        self.cmd("xbrl_gate_rerun", self.topup.helper("xbrl-gate-rerun", "--out", out))
        x = json.loads(out.read_text())
        info["xbrl_gate_rerun"] = {"files": x["files"], "facts": x["facts"], "dropped": x["dropped"][:50],
                                   "quarantined": x["quarantined"][:50], "missing_report": x["missing_report"][:50]}
        if x["dropped"] or x["quarantined"] or x["missing_report"]:
            raise StepFailed(f"the XBRL gate re-run over {x['files']} live files drops {len(x['dropped'])} facts, "
                             f"quarantines {len(x['quarantined'])} files and finds {len(x['missing_report'])} without "
                             "a report", info)
        return info

    def step_old_rows_proof(self) -> dict:
        audits = self.audits()
        batch_ciks = {str(c) for c in self.ciks.values()} | {str(a["cik"]) for a in audits.values() if "cik" in a}
        after = row_state(self.root, (int(c) for c in batch_ciks))
        write_gz(self.dir / "hashes_after.json.gz", after)
        if self.before is None:
            raise StepFailed("no canonical hashes from before the batch")
        names = {c: t for c, t in self.names.items()}
        con = ro(self.root / STORE_DB)
        try:
            names.update({str(c): t for c, t in con.execute("SELECT cik, ticker FROM companies")})
        finally:
            con.close()
        proof = old_rows_proof(self.before, after, batch_ciks, set(self.tickers), names)
        proof["file"] = self.topup.rel(self.dir / "hashes_after.json.gz")
        if not proof["ok"]:
            raise StepFailed("; ".join(proof["violations"]), proof)
        return proof

    def step_dev_replay(self) -> dict:
        out = self.dir / "dev_replay.json"
        self.cmd("dev_replay", self.topup.helper("dev-replay", "--out", out))
        result = compare_replay(self.topup.baseline, json.loads(out.read_text()))
        if not result["identical"]:
            raise StepFailed(f"the dev replay differs from --dev-baseline in {result['differences']} drafts", result)
        return result

    def step_scratch_snapshot(self) -> dict:
        return self.topup.check_scratch_snapshot(self.dir, self.record)

    def step_split_table(self) -> dict:
        return self.topup.split_check(self.dir, self.record)

    def step_archives(self) -> dict:
        ciks = sorted({int(a["cik"]) for a in self.audits().values() if "cik" in a})
        cf = self.cmd("archive_companyfacts", [PY, "scripts/archive_companyfacts.py", "archive", *map(str, ciks),
                                               "--delete-raw"], protect=True)
        ex = self.cmd("archive_exhibits", [PY, "scripts/archive_exhibits.py", "archive", "--delete-raw"], protect=True)
        last = (lambda t: t.strip().splitlines()[-1] if t.strip() else "")
        return {"companyfacts": len(ciks), "companyfacts_last": last(cf), "exhibits": last(ex)}


# --------------------------------------------------------------------------- helpers (run as subprocesses)
def forbid_network() -> None:
    import socket

    def refuse(*a: Any, **k: Any) -> None:
        raise RuntimeError("network access is forbidden in this step")

    socket.create_connection = refuse
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    try:
        import openai
    except ImportError:
        return
    openai.OpenAI.__init__ = refuse
    openai.AsyncOpenAI.__init__ = refuse


def helper_ingest(root: Path, pin: date, argv: list[str]) -> int:
    """receipts.ingest.main with date.today() pinned, so a re-ingest keeps the window the store was built with."""
    import datetime as _dt

    import receipts.ingest as ing

    if not Path(ing.__file__).resolve().is_relative_to(root):
        raise SystemExit(f"receipts.ingest did not come from {root}")

    class PinnedDate(_dt.date):
        @classmethod
        def today(cls):  # type: ignore[override]
            return cls(pin.year, pin.month, pin.day)

    ing.date = PinnedDate
    return ing.main(argv)


def helper_dev_replay(root: Path, out: Path) -> int:
    """The v0 and trap_v1 dev splits through receipts.check with cached model responses only: per claim its id,
    value text, span, status, reason and explanation (no test split, no network, no model)."""
    import threading

    forbid_network()
    from eval import eval as harness
    from receipts import extract as ex
    from receipts.check import check_detailed
    from receipts.store import Store

    if not Path(ex.__file__).resolve().is_relative_to(root):
        raise SystemExit(f"receipts.extract did not come from {root}")

    def refuse(*a: Any, **k: Any) -> None:
        raise RuntimeError("model clients are forbidden in the dev replay")

    ex.make_client = refuse
    lock, rows = threading.Lock(), {}

    def check(text: str, *, as_of: Any = None, context: Optional[str] = None):
        store = Store(root / STORE_DB, root / PR_DIR)
        try:
            result = check_detailed(text, as_of=as_of, context=context, store=store, backend=ex.BACKENDS["meta"],
                                    cache_dir=root / "data/cache/spark",
                                    extra_cache_dirs=(root / "data/snapshot/spark_cache",), concurrency=2)
            if not all(c.get("status") == "ok" and c.get("cached") for c in result.extraction.calls):
                raise RuntimeError("a model response is not cached")
            key = hashlib.sha256(f"{text}\0{as_of}\0{context}".encode()).hexdigest()[:16]
            with lock:
                rows[key] = [[c.id, c.value_text, c.span_start, v.status, v.reason, v.explanation]
                             for c, v in result.results]
            return result.results
        finally:
            store.close()

    harness.real_check = lambda: check
    out.parent.mkdir(parents=True, exist_ok=True)
    for corpus in ("v0", "trap_v1"):
        run_record = out.with_name(f"{out.stem}.{corpus}.run.json")
        code = harness.main(["--corpus", corpus, "--split", "dev", "--no-cache", "--concurrency", "2", "--budget",
                             "0", "--est-cost-per-item", "0", "--quiet", "--json-out", str(run_record)])
        if code:
            return code
        run_record.write_text(run_record.read_text().replace(str(out.parent) + os.sep, "")
                              .replace(str(root) + os.sep, ""))
    out.write_text(json.dumps(rows, indent=0, sort_keys=True))
    print("drafts", len(rows), "claims", sum(len(v) for v in rows.values()))
    return 0


def helper_scratch_snapshot(root: Path, target: Path) -> int:
    """scripts/build_snapshot.py --no-model into `target` instead of data/snapshot. The target starts from the served
    snapshot's model cache and manifest, so re-recording the examples needs no model."""
    import importlib.util

    target = target.resolve()
    data = (root / "data").resolve()
    if target == root or root.is_relative_to(target) or target.is_relative_to(data):
        raise SystemExit(f"refusing to build a scratch snapshot at {target}: it must lie outside the repo's data/")
    if target.exists() and any(target.iterdir()) and not (target / "manifest.json").is_file():
        raise SystemExit(f"refusing to replace {target}: it is not empty and holds no snapshot manifest")
    spec = importlib.util.spec_from_file_location("build_snapshot", root / "scripts/build_snapshot.py")
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    served = root / "data/snapshot"
    if (served / "spark_cache").is_dir():
        shutil.copytree(served / "spark_cache", target / "spark_cache")
    if (served / "manifest.json").is_file():
        shutil.copyfile(served / "manifest.json", target / "manifest.json")
    bs.OUT = target
    return bs.main(["--no-model"])


def example_scores(snapshot: Path) -> dict:
    """example id -> claims and verdict counts, read from the snapshot's recorded example streams."""
    from receipts import stream

    out = {}
    for ex in read_json(snapshot / "examples.json").get("examples", []):
        key = stream.draft_key(ex["text"], ex.get("as_of"), ex.get("context"))
        events = read_json(snapshot / "api_cache" / f"{key}.json").get("events") or []
        done = next((e.get("data", {}) for e in reversed(events) if e.get("event") == "done"), {})
        out[ex.get("id", key[:12])] = {"claims": sum(e.get("event") == "claim" for e in events),
                                       "counts": done.get("counts")}
    return out


def helper_check_snapshot(root: Path, snap: Path, expected: Path, out: Path) -> int:
    """Replay the Slide, filing-question and Private recordings and the qualifier grid against a snapshot, and read
    its examples' scores beside the served snapshot's (no network, no model)."""
    import base64
    import tempfile

    forbid_network()
    from fastapi.testclient import TestClient

    import eval.qualifier_robustness as qr
    from receipts import api, ask, private, recordings, stream
    from receipts.store import Store

    def refuse(*a: Any, **k: Any) -> None:
        raise RuntimeError("no model client and no spend in this check")

    for obj in list(vars(api).values()):
        if isinstance(obj, type) and obj.__module__ == api.__name__ and callable(getattr(obj, "reserve", None)):
            obj.reserve = refuse  # live requests are off; a reservation would mean the replay missed

    def unavailable(env: Any = None) -> Any:
        raise private.PrivateUnavailable("the private backend is off in this check")

    private.private_backend = unavailable
    private.make_private_client = refuse

    def sse(body: str) -> list:
        events = []
        for block in body.split("\n\n"):
            lines = [line for line in block.split("\n") if line and not line.startswith(":")]
            if lines:
                fields = dict(line.split(": ", 1) for line in lines)
                events.append([fields["event"], json.loads(fields["data"])])
        return events

    samples = json.loads((root / "web/lib/samples.json").read_text())
    rec = recordings.Recordings([snap / "recordings"])
    replays = {}
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        tmp_path = Path(tmp)
        cfg = api.Settings(data_dir=snap, api_cache_dir=tmp_path / "a", prebaked_dirs=(),
                           spark_cache_dir=tmp_path / "s", spark_read_dirs=(), examples_paths=(tmp_path / "e.json",),
                           recording_dirs=(snap / "recordings",), live_requests_enabled=False)
        store = Store(snap / "facts.sqlite", snap / "pr_facts")
        try:
            client = TestClient(api.create_app(cfg, store=store, client_factory=refuse, wonder_client_factory=refuse,
                                               slide_client_factory=refuse))

            def same(kind: str, key: Optional[str], response: Any) -> bool:
                record = rec.load(kind, key)
                return record is not None and sse(response.text) == [list(e) for e in recordings.replay(record)]

            s = samples["slides"][0]
            image = (root / "web/public" / s["image"].lstrip("/")).read_bytes()
            url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
            replays["slide"] = same("slide", recordings.slide_key(
                url, stream.normalize_context(s["context"]), stream.canonical_as_of(s["as_of"])), client.post(
                "/api/slide", json={"image_data_url": url, "context": s["context"], "as_of": s["as_of"]}))
            s = samples["filing_questions"][0]
            replays["grounding"] = same("grounding", recordings.grounding_key(
                s["filing_id"], ask.validate_question(s["question"]), stream.canonical_as_of(s["as_of"])), client.post(
                "/api/grounding", json={"question": s["question"], "filing_id": s["filing_id"], "as_of": s["as_of"]}))
            s = samples["private"][0]
            replays["private"] = same("private", recordings.private_key(
                s["text"], stream.normalize_context(s["context"]), stream.canonical_as_of(s["as_of"])), client.post(
                "/api/private/check", json={"text": s["text"], "context": s["context"], "as_of": s["as_of"]}))
        finally:
            store.close()
    store = Store(snap / "facts.sqlite", snap / "pr_facts")
    try:
        grid = qr.run(store) == json.loads((root / "eval/qualifier_robustness.json").read_text())
    finally:
        store.close()
    got, want = example_scores(snap), example_scores(expected)
    result = {"recordings": replays, "qualifier_grid_identical": grid, "examples": got, "examples_expected": want,
              "examples_identical": bool(want) and got == want,
              "snapshot_bytes": sum(p.stat().st_size for p in snap.rglob("*") if p.is_file()),
              "facts_sqlite_bytes": (snap / "facts.sqlite").stat().st_size}
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("recordings", "qualifier_grid_identical", "examples_identical")}))
    return 0


def helper_xbrl_gate_rerun(root: Path, out: Path) -> int:
    """receipts.press_release.xbrl_gate over every live release file, as the mapper applies it: what it would drop or
    quarantine now (companyfacts from data/archive/companyfacts, else the EDGAR cache; no network, no model)."""
    forbid_network()
    from receipts import press_release as pr
    from receipts.types import Fact

    from receipts.ingest import with_predecessors

    loaded: dict[int, dict] = {}

    def registrant(cik: int) -> dict:
        gz = root / f"data/archive/companyfacts/CIK{cik:010d}.json.gz"
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        raw = root / EDGAR_CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
        return json.loads(gzip.decompress(gz.read_bytes()) if gz.is_file() else raw.read_bytes())

    def gaap(cik: int) -> dict:
        if cik not in loaded:
            loaded.clear()  # files go company by company: keep one companyfacts in memory
            loaded[cik] = with_predecessors(cik, registrant).get("facts", {}).get("us-gaap", {})  # as ingest read it
        return loaded[cik]

    stored: dict[int, dict] = {}

    def served(cik: int) -> dict:  # the figures the store serves, as the mapper's gate reads them
        if cik not in stored:
            stored.clear()
            stored[cik] = pr.store_values(cik, root / STORE_DB)
        return stored[cik]

    concepts = pr.xbrl_concepts()
    result: dict[str, Any] = {"files": 0, "facts": 0, "dropped": [], "quarantined": [], "missing_report": []}
    for path in sorted((root / PR_DIR).glob("*/*.jsonl")):
        name = path.relative_to(root / PR_DIR).as_posix()
        report = read_json(path.with_suffix(".report.json"))
        if not report.get("cik"):
            result["missing_report"].append(name)
            continue
        facts = [Fact.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        job = pr.FilingJob(ticker=report["ticker"], cik=int(report["cik"]), company=report["ticker"],
                           eightk={"accessionNumber": report["accession"]},
                           tenq={"accessionNumber": report["tenq"]} if report.get("tenq") else None, facts=facts)
        g = pr.xbrl_gate(job, gaap(int(report["cik"])), concepts, served(int(report["cik"])))
        result["files"] += 1
        result["facts"] += len(facts)
        if g.quarantine:
            result["quarantined"].append({"file": name, "reason": g.quarantine})
        result["dropped"] += [{"file": name, **r} for r in g.dropped]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str))
    print(f"XBRL gate re-run: {result['files']} files, {result['facts']} facts, {len(result['dropped'])} would drop, "
          f"{len(result['quarantined'])} would quarantine, {len(result['missing_report'])} without a report")
    return 0


def helper_main(argv: list[str]) -> int:
    if not argv:
        print("usage: topup_latest_quarter.py helper {ingest,dev-replay,scratch-snapshot,check-snapshot,"
              "xbrl-gate-rerun} --repo-root ROOT ...", file=sys.stderr)
        return EXIT_USAGE
    name, rest = argv[0], argv[1:]
    extra: list[str] = []
    if "--" in rest:
        i = rest.index("--")
        rest, extra = rest[:i], rest[i + 1:]
    ap = argparse.ArgumentParser(prog=f"topup_latest_quarter.py helper {name}")
    ap.add_argument("--repo-root", type=Path, required=True)
    if name == "ingest":
        ap.add_argument("--pin-date", type=date.fromisoformat, required=True)
    elif name in ("dev-replay", "xbrl-gate-rerun"):
        ap.add_argument("--out", type=Path, required=True)
    elif name == "scratch-snapshot":
        ap.add_argument("dir", type=Path)
    elif name == "check-snapshot":
        ap.add_argument("dir", type=Path)
        ap.add_argument("--expected", type=Path, required=True)
        ap.add_argument("--out", type=Path, required=True)
    else:
        ap.error(f"unknown helper {name}")
    a = ap.parse_args(rest)
    root = a.repo_root.resolve()
    sys.path.insert(0, str(root))
    if name == "ingest":
        return helper_ingest(root, a.pin_date, extra)
    if name == "dev-replay":
        return helper_dev_replay(root, a.out.resolve())
    if name == "scratch-snapshot":
        return helper_scratch_snapshot(root, a.dir)
    if name == "check-snapshot":
        return helper_check_snapshot(root, a.dir.resolve(), a.expected.resolve(), a.out.resolve())
    return helper_xbrl_gate_rerun(root, a.out.resolve())


# --------------------------------------------------------------------------- CLI
def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog="Steps and exit codes: see the module "
                                 "docstring (docs/evidence-expansion.md, 'Latest-quarter top-up').")
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--tickers", nargs="+", help="covered companies, space or comma separated")
    pick.add_argument("--auto", action="store_true", help="every covered company with a filing newer than the snapshot")
    pick.add_argument("--record-dev-baseline", type=Path, metavar="FILE",
                      help="record the dev replay of the current code and data to FILE and exit (offline)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="cached Submissions only: print the plan and estimate")
    mode.add_argument("--plan-only", action="store_true",
                      help="refetch stale Submissions (live SEC), print and write the plan, stop before batch 1")
    ap.add_argument("--out-dir", type=Path, help="reports, logs and every scratch output")
    ap.add_argument("--dev-baseline", type=Path, help="dev replay JSON the batches must reproduce (real run)")
    ap.add_argument("--max-ledger-usd", type=float, help="cap on the press-release spend ledger (real run)")
    ap.add_argument("--factor", type=float, default=1.15, help="headroom on each estimate (default 1.15)")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--disk-floor-gib", type=float, default=1.0)
    ap.add_argument("--scratch-snapshot", type=Path, help="scratch snapshot folder inside --out-dir "
                                                          "(default OUT/scratch_snapshot)")
    ap.add_argument("--repo-root", type=Path, help="checkout whose data and code to use (default: this script's)")
    ap.add_argument("--submissions-max-age-hours", type=float, default=12.0)
    ap.add_argument("--ingest-max-requests", type=int, default=600, help="live SEC requests per batch ingest")
    ap.add_argument("--map-max-sec-requests", type=int, default=1200, help="live SEC requests per mapper run")
    ap.add_argument("--concurrency", type=int, default=6, help="model calls in flight while mapping")
    ap.add_argument("--allow-stale-snapshot", action="store_true",
                    help="run although data/facts.sqlite changed after data/snapshot was built")
    args = ap.parse_args(argv)
    if args.tickers:
        args.tickers = list(dict.fromkeys(t.strip().upper() for raw in args.tickers for t in raw.split(",")
                                          if t.strip()))
    if args.record_dev_baseline:
        if args.dry_run or args.plan_only:
            ap.error("--record-dev-baseline runs alone")
        return args
    if args.out_dir is None:
        ap.error("--out-dir is required")
    if not (args.dry_run or args.plan_only):
        if args.dev_baseline is None or args.max_ledger_usd is None:
            ap.error("a real run needs --dev-baseline and --max-ledger-usd")
    if args.batch_size < 1 or args.factor < 1:
        ap.error("--batch-size must be at least 1 and --factor at least 1")
    return args


def check_locations(root: Path, out: Path, scratch: Path) -> Optional[str]:
    if not (root / "data").is_dir():
        return f"{root} has no data/ folder (pass --repo-root)"
    if out == root or root.is_relative_to(out) or out.is_relative_to(root / "data/snapshot"):
        return "--out-dir must not be the repo, contain it, or lie in data/snapshot"
    if scratch == out or not scratch.is_relative_to(out) or scratch.is_relative_to(root / "data"):
        return "--scratch-snapshot must lie inside --out-dir and outside the repo's data/"
    return None


def record_dev_baseline(root: Path, path: Path, scrub: Scrubber) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = [PY, str(SCRIPT), "helper", "dev-replay", "--repo-root", str(root), "--out", str(path)]
    code, text = run_command(argv, cwd=root, log=path.with_suffix(".log"), scrub=scrub)
    print(text.strip().splitlines()[-1] if text.strip() else "", flush=True)
    if code:
        print(f"the dev replay exited {code}; see {scrub(str(path.with_suffix('.log')))}")
    return code


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["helper"]:
        return helper_main(argv[1:])
    args = parse_args(argv)
    root = (args.repo_root or REPO).resolve()
    secrets = secrets_from_env(dict(os.environ))
    if args.record_dev_baseline:
        target = args.record_dev_baseline.resolve()
        return record_dev_baseline(root, target, Scrubber(secrets, [(target.parent, "<out>"), (root, "."),
                                                                     (REPO, "<driver>"), (Path.home(), "~")]))
    out = args.out_dir.resolve()
    scratch = (args.scratch_snapshot or out / "scratch_snapshot").resolve()
    problem = check_locations(root, out, scratch)
    if problem:
        print(f"topup_latest_quarter.py: {problem}", file=sys.stderr)
        return EXIT_USAGE
    out.mkdir(parents=True, exist_ok=True)
    scrub = Scrubber(secrets, [(out, "<out>"), (root, "."), (REPO, "<driver>"), (Path.home(), "~")])
    run = TopUp(root, out, args, scrub)
    try:
        if args.dry_run:
            plan = build_plan(root, out, args, offline=True, ua=None)
            write_json(out / "plan.json", plan, scrub)
            print_plan(plan)
            return 0
        ua = sec_user_agent(dict(os.environ))
        if not args.plan_only:
            status = snapshot_status(root)
            resuming = completed_tickers(out)[1] > 0  # earlier batches in this OUT changed the store on purpose
            if not status["current"] and not (args.allow_stale_snapshot or resuming):
                raise Preflight(f"data/snapshot was built from the store of {status['source_built_at']}, but "
                                f"data/facts.sqlite was rebuilt at {status['store_built_at']}: rebuild the snapshot "
                                "(scripts/build_snapshot.py --no-model, then scripts/build_split_table.py) so the "
                                "top-up starts from the served state, or pass --allow-stale-snapshot")
            if not status["current"] and resuming:
                print("note: data/facts.sqlite changed after data/snapshot was built; resuming the top-up in this "
                      "--out-dir, whose earlier batches made those changes", flush=True)
            if os.stat(out).st_dev != os.stat(root / "data").st_dev:
                raise Preflight("--out-dir must be on the repo's volume: the scratch snapshot is renamed into place")
        if not args.plan_only:
            run.baseline = read_json(args.dev_baseline.resolve())
            if not run.baseline or not isinstance(run.baseline, dict):
                raise Preflight(f"--dev-baseline {scrub(str(args.dev_baseline))} is missing or empty: record it with "
                                "--record-dev-baseline before the top-up")
        run.plan = build_plan(root, out, args, offline=False, ua=ua)
        write_json(out / "plan.json", run.plan, scrub)
        print_plan(run.plan)
        if args.plan_only:
            return 0
        run.by_ticker = {c["ticker"]: c for c in run.plan["companies"]}
        run.window = run.plan["window"]
        if not run.plan["batches"]:
            print("nothing to top up")
            write_json(out / "summary.json", {"generated_by": "scripts/topup_latest_quarter.py",
                                              "finished_at": utc_now(), "plan": "plan.json", "batches": [],
                                              "ok": True, "next": []}, scrub)
            return 0
        print("preflight: split table, dev replay and a scratch snapshot on the untouched data ...", flush=True)
        run.preflight_gates()
        return run.run_batches()
    except Preflight as e:
        print(f"topup_latest_quarter.py: {scrub(str(e))}", file=sys.stderr)
        return EXIT_PREFLIGHT
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
