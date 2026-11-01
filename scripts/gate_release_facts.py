"""Apply receipts.release_gates to the stored press-release facts (review fixes 5 to 9).

For every company with live release files (data/pr_facts/<TICKER>/*.jsonl), the store's XBRL and derived values and
fiscal calendar (data/facts.sqlite) and the company's companyfacts (data/archive/companyfacts, else the EDGAR cache)
decide which release facts to drop: net income that may include noncontrolling interests with no XBRL net income to
check it against (5), GAAP rows stored as non-GAAP (6), a figure that same-period XBRL contradicts without a
documented restatement (7), a label its dates contradict or a trailing-twelve-month figure (8), a x1000 scale
slip (9), and a GAAP line stored as non-GAAP (11, read from the release exhibit's tables in data/archive/exhibits or
the EDGAR cache). A file is rewritten only when it loses a fact; every other line stays byte for byte, and the dropped facts
are recorded under "release_gates" in the file's .report.json. Quarantined files are never read by the store and are
left alone. A derived XBRL value (Q4 = FY - 9M) that the company's own release outranks under fix 7 is written to
data/derived_rejections.json, which receipts.ingest reads: re-ingest the listed companies (offline) afterwards, as
after scripts/confirm_revenue.py. Offline: no network and no model. Run it after mapping, as
scripts/relabel_release_facts.py is.

    .venv/bin/python scripts/gate_release_facts.py --dry-run --out gates.json [TICKER ...]   # list, write nothing
    .venv/bin/python scripts/gate_release_facts.py --out gates.json [TICKER ...]             # rewrite files
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts.press_release import parse_tables  # noqa: E402
from receipts.ingest import store_run_date, with_predecessors  # noqa: E402
from receipts.release_gates import (XbrlValue, attributable_net_income, gate_all, key, locator_row,  # noqa: E402
                                    noncontrolling_interest)
from receipts.types import Fact  # noqa: E402

DB = REPO / "data/facts.sqlite"
PR = REPO / "data/pr_facts"
ARCHIVE = REPO / "data/archive/companyfacts"
CACHE = REPO / "data/cache/edgar"
REJECTIONS = REPO / "data/derived_rejections.json"
EXHIBITS = REPO / "data/archive/exhibits"


class ExhibitTables:
    """Parsed tables of a release exhibit by URL: data/archive/exhibits, else the EDGAR cache; [] when neither holds
    it (fix 11 then leaves the figure alone). Cached until clear()."""

    def __init__(self, archive: Path = EXHIBITS, cache: Path = CACHE):
        index = archive / "index.json"
        self.index = json.loads(index.read_text()) if index.exists() else {}
        self.archive, self.raw, self.cache = archive, cache, {}

    def __call__(self, url: str) -> list:
        if url not in self.cache:
            meta = self.index.get(url)
            gz = self.archive / f"{meta['key']}.gz" if meta else None
            if gz is not None and gz.exists():
                html = gzip.decompress(gz.read_bytes())
            else:
                body = self.raw / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
                html = body.read_bytes() if body.exists() else None
            self.cache[url] = parse_tables(html) if html else []
        return self.cache[url]

    def clear(self) -> None:
        self.cache.clear()


def registrant_companyfacts(cik: int) -> dict:
    gz = ARCHIVE / f"CIK{cik:010d}.json.gz"
    if gz.exists():
        return json.loads(gzip.decompress(gz.read_bytes()))
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    return json.loads((CACHE / f"{hashlib.sha256(url.encode()).hexdigest()}.bin").read_bytes())


def companyfacts(cik: int) -> dict:
    """The company's companyfacts as ingest read them: each predecessor registrant's merged in (PREDECESSOR_CIKS)."""
    return with_predecessors(cik, registrant_companyfacts)


def _accepted(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def prior_disagreements(folder: Path, facts: dict[str, list[Fact]]) -> list[tuple[str, str]]:
    """(metric, locator_row) of each company-level GAAP figure an earlier run dropped against reported XBRL (its
    file's report, "release_gates") that is no longer live: it still counts against its row's record, so a second run
    decides as the first did (review L2)."""
    live = {(f.metric, "{}..{}".format(*key(f)[1:3]), f.value, f.locator) for rows in facts.values() for f in rows}
    return [(g["metric"], locator_row(g.get("locator"))) for p in sorted(folder.glob("*.report.json"))
            for g in json.loads(p.read_text(encoding="utf-8")).get("release_gates", [])
            if g.get("rule") == "xbrl_reported" and g.get("basis") == "GAAP" and g.get("segment") is None
            and (g["metric"], g.get("period"), g.get("value"), g.get("locator")) not in live]


def run(pr_dir: Path, db: Path, *, write: bool, tickers: Iterable[str] = (),
        load_companyfacts: Callable[[int], dict] = companyfacts, rejections_path: Path = REJECTIONS,
        exhibit_tables: Optional[Callable[[str], list]] = None, run_date: Optional[date] = None) -> dict:
    """Gate every company's live release files; rewrite the files that lose a fact and record the derived values the
    releases outrank when `write`, dated `run_date` (default: the store's own run date)."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        since = meta.get("filings_start", "")
        run_date = run_date or store_run_date(meta) or date.today()
        companies = dict(con.execute("SELECT ticker, cik FROM companies"))
        xbrl: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        for ticker, metric, start, end, value, decimals, source, accession, accepted, concept, locator in con.execute(
                "SELECT ticker, metric, period_start, period_end, value, decimals, source, accession, accepted_at, "
                "concept, locator FROM facts WHERE source IN ('xbrl', 'derived') AND segment IS NULL"):
            xbrl[ticker][(metric, start or "", end)].append(XbrlValue(
                value=value, decimals=decimals, source=source, accession=accession, accepted_at=_accepted(accepted),
                concept=(concept or "").removeprefix("us-gaap:"),
                terms=(locator or "").count(" - ") + 1 if source == "derived" else 1))
        calendars: dict[int, dict] = defaultdict(dict)
        for cik, fy, fp, start, end in con.execute(
                "SELECT cik, fiscal_year, fiscal_period, period_start, period_end FROM fiscal_periods"):
            calendars[cik][(fy, fp)] = (date.fromisoformat(start) if start else None, date.fromisoformat(end))
        stored = {(t, m, s or "", e, v) for t, m, s, e, v in con.execute(
            "SELECT ticker, metric, period_start, period_end, value FROM facts WHERE source = 'derived'")}
    finally:
        con.close()
    wanted = {t.upper() for t in tickers}
    tables = exhibit_tables if exhibit_tables is not None else ExhibitTables()
    dropped, files_changed, rejections = [], 0, {}
    recorded = json.loads(rejections_path.read_text()) if rejections_path.exists() else {}
    for folder in sorted(p for p in pr_dir.iterdir() if p.is_dir()):
        ticker = folder.name
        if (wanted and ticker not in wanted) or ticker not in companies:
            continue
        lines = {p.name: p.read_text(encoding="utf-8").splitlines() for p in sorted(folder.glob("*.jsonl"))}
        facts = {name: [Fact.model_validate_json(line) for line in rows] for name, rows in lines.items()}
        if not any(facts.values()):
            continue
        prior = prior_disagreements(folder, facts)
        usgaap = load_companyfacts(companies[ticker]).get("facts", {}).get("us-gaap", {})
        nci = noncontrolling_interest(usgaap, since)
        vouched = [(r["metric"], r["period_start"], r["period_end"], r["release"]["value"])
                   for r in recorded.get(ticker, [])]
        drop, records, rejected = gate_all(facts, xbrl[ticker], calendars[companies[ticker]], usgaap, nci,
                                           attributable_net_income(usgaap, since) if nci else None, vouched, tables,
                                           prior)
        if hasattr(tables, "clear"):
            tables.clear()
        for r in records:
            r["file"] = f"{ticker}/{r['file']}"
        for r in rejected:
            r["release"]["file"] = f"{ticker}/{r['release']['file']}"
        dropped += records
        if rejected:
            rejections[ticker] = rejected
        if not write:
            continue
        for name, idx in drop.items():
            path, gone = folder / name, set(idx)
            tmp = path.with_suffix(".jsonl.tmp")
            body = "".join(line + "\n" for i, line in enumerate(lines[name]) if i not in gone)
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
            report_path = folder / name.replace(".jsonl", ".report.json")
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report.setdefault("release_gates", []).extend(
                    {k: v for k, v in r.items() if k != "file"} for r in records if r["file"] == f"{ticker}/{name}")
                rtmp = report_path.with_suffix(".json.tmp")
                rtmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
                rtmp.replace(report_path)
            files_changed += 1
    new = {t: [r for r in rs if not any((o["metric"], o["period_start"], o["period_end"], o["value"])
                                        == (r["metric"], r["period_start"], r["period_end"], r["value"])
                                        for o in recorded.get(t, []))]
           for t, rs in rejections.items()}
    new = {t: rs for t, rs in new.items() if rs}
    if write and new:
        for t, rs in new.items():
            recorded.setdefault(t, []).extend({**r, "decided_on": run_date.isoformat()} for r in rs)
        tmp = rejections_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(sorted(recorded.items())), indent=1, default=str) + "\n")
        tmp.replace(rejections_path)
    everything = {**recorded, **{t: recorded.get(t, []) + rs for t, rs in new.items()}} if not write else recorded
    reingest = sorted(t for t, rs in everything.items()
                      if any((t, r["metric"], r["period_start"], r["period_end"], r["value"]) in stored for r in rs))
    return {"dropped": dropped, "files_changed": files_changed, "rejections": rejections, "new_rejections": new,
            "reingest": reingest}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--out", type=Path, help="JSON list of every dropped fact")
    ap.add_argument("--run-date", type=date.fromisoformat,
                    help="date recorded with new decisions (default: the store's own run date, from its window)")
    args = ap.parse_args(argv)
    result = run(PR, DB, write=not args.dry_run, tickers=args.tickers, run_date=args.run_date)
    dropped, changed = result["dropped"], result["files_changed"]
    by_rule = Counter(r["rule"] for r in dropped)
    by_company = Counter(r["file"].split("/")[0] for r in dropped)
    rewritten = "" if args.dry_run else f"; {changed} files rewritten"
    print(f"{'would drop' if args.dry_run else 'dropped'} {len(dropped)} release facts ({dict(by_rule)}) at "
          f"{len(by_company)} companies{rewritten}")
    for rule in sorted(by_rule):
        hit = Counter(r["file"].split("/")[0] for r in dropped if r["rule"] == rule)
        print(f"  {rule}: " + ", ".join(f"{t} {n}" for t, n in sorted(hit.items())))
    rejected = result["rejections"]
    if rejected:
        print(f"derived values the releases outrank: {sum(len(v) for v in rejected.values())} at {len(rejected)} "
              f"companies: " + ", ".join(f"{t} {len(v)}" for t, v in sorted(rejected.items())))
    if result["reingest"]:
        print("re-ingest offline so the rejected derived values leave the store: " + ",".join(result["reingest"]))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
