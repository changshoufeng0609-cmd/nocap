"""Correct the fiscal labels of existing press-release facts from the stored XBRL calendar (data/facts.sqlite).

A release fact whose period is in its company's stored fiscal_periods (same end date; a quarter of 80-125 days or a
year of 350-380 days) takes that period's label. A release fact whose period a stored 10-Q or 10-K reports with
XBRL facts, but which the calendar rules left unlabelled (receipts.fiscal_labels), loses its label, so a receipt
never shows a label the store rejected. Nothing else changes: every other field of the fact, and every
other fact, stays byte for byte, and a line is rewritten only through the writer's own serializer
(Fact.model_dump_json), after checking that it reproduces the line exactly. Facts whose period the store does not
have keep their labels and are counted. Quarantined files (*.jsonl.quarantined) are never read by the store and are
left alone.

Why: receipts.press_release labelled release facts from companyfacts DEI tags, which some filers reuse a year on
(Oracle's Q1 FY2027 10-Q says FY2026, Salesforce's FY2026 10-K FY2025), so a receipt could show the wrong fiscal
period. FiscalCalendar now takes the stored label; this brings facts mapped before that change into line.

--redate TICKER ... first re-derives the period dates of those companies' release facts. The mapper used to date a
quarter that no filing tags as three calendar months back from its end, so a 16-week quarter (Costco's, PepsiCo's
and Domino's fourth, Kroger's first) got 13-week dates, and the label gate (scripts/gate_release_facts.py, fix 8)
dropped the fourth quarters, whose labels those dates contradict. Each release is decided again offline
(receipts.press_release.replay_job) from its archived exhibit (data/archive/exhibits, else the EDGAR cache), the
candidate tables and model calls its report records, the model's stored table maps (data/cache/spark) and today's
FiscalCalendar (archived companyfacts and the stored calendar): no SEC request, no model call. Then
  * a live fact the replay decides again from the same cell (metric, basis, segment, value, row and cell text,
    exhibit, period end) with another start takes the replay's start and the store's label for the new period, unless
    that label contradicts the new dates (it never does for the fixed calendar);
  * a fact the label gate dropped (its report's "release_gates" records, rule label_dates) comes back when the replay
    decides its cell again, the mapper's XBRL gate (receipts.press_release.xbrl_gate, re-run on the replayed release)
    keeps it, and its new dates fit the label the store gives them (receipts.release_gates.label_conflict). A drop
    whose dates still contradict its label (a trailing-twelve-month column) stays dropped; drops by any other rule
    are never touched.
Restored facts are appended to their file; each rewritten file's report records what changed under "redate". Run
scripts/gate_release_facts.py afterwards, as after any mapping: the restored facts face every release gate again.

    .venv/bin/python scripts/relabel_release_facts.py --dry-run --out changes.json   # list, write nothing
    .venv/bin/python scripts/relabel_release_facts.py --out changes.json             # rewrite changed files
    .venv/bin/python scripts/relabel_release_facts.py --redate COST DPZ PEP KR --dry-run --out redate.json
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts.types import Fact  # noqa: E402

EXHIBITS = REPO / "data/archive/exhibits"
COMPANYFACTS = REPO / "data/archive/companyfacts"
EDGAR_CACHE = REPO / "data/cache/edgar"
SPARK_CACHE = REPO / "data/cache/spark"


def kind(f: Fact) -> str | None:
    return span_kind(f.period_start, f.period_end)


def span_kind(start: Optional[date], end: date) -> str | None:
    if start is None:
        return None
    days = (end - start).days + 1
    return "quarter" if 80 <= days <= 125 else "annual" if 350 <= days <= 380 else None


def stored_labels(db: Path) -> dict[str, dict[tuple[date, str], tuple[int, str]]]:
    """ticker -> {(period end, quarter|annual): (fiscal year, Q1..Q4 | FY)} from fiscal_periods."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        out: dict[str, dict] = {}
        for ticker, fy, fp, end in con.execute(
                "SELECT c.ticker, p.fiscal_year, p.fiscal_period, p.period_end FROM fiscal_periods p "
                "JOIN companies c ON c.cik = p.cik"):
            out.setdefault(ticker, {})[(date.fromisoformat(end), "annual" if fp == "FY" else "quarter")] = (fy, fp)
        return out
    finally:
        con.close()


def covered_periods(db: Path) -> dict[str, list[tuple[str, date]]]:
    """ticker -> [(form, report date)] of every stored 10-Q and 10-K with XBRL facts in the store: the periods the
    calendar rules saw. A filing companyfacts omitted (no XBRL stored) covers nothing, so a release for it keeps
    the label inferred on earnings day."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        out: dict[str, list] = {}
        for ticker, form, report in con.execute(
                "SELECT c.ticker, f.form, f.report_date FROM filings f JOIN companies c ON c.cik = f.cik "
                "WHERE f.form IN ('10-Q', '10-K') AND f.report_date IS NOT NULL AND EXISTS "
                "(SELECT 1 FROM facts x WHERE x.accession = f.accession AND x.source = 'xbrl')"):
            out.setdefault(ticker, []).append((form, date.fromisoformat(report)))
        return out
    finally:
        con.close()


def is_covered(f: Fact, k: str, filings: list[tuple[str, date]]) -> bool:
    return any(abs((f.period_end - report).days) <= 3 and (form == "10-K" or k == "quarter")
               for form, report in filings)


def store_label(f: Fact, table: dict[tuple[date, str], tuple[int, str]],
                filings: list[tuple[str, date]]) -> Optional[tuple[Optional[int], Optional[str]]]:
    """The label the store gives the fact's period: its stored (fiscal year, period); (None, None) when a stored 10-Q
    or 10-K reports the period but the calendar rules left it unlabelled; None when the store does not have the
    period (the fact keeps its label)."""
    k = kind(f)
    if not k:
        return None
    want = table.get((f.period_end, k))
    if want is None and is_covered(f, k, filings):
        return None, None  # the rules rejected this period's label: the release fact must not keep one
    return want


def relabel(pr_dir: Path, db: Path, write: bool) -> dict:
    labels, covered = stored_labels(db), covered_periods(db)
    changes, unlabelled, seen = [], 0, 0
    for path in sorted(pr_dir.glob("*/*.jsonl")):
        table = labels.get(path.parent.name, {})
        lines = path.read_text(encoding="utf-8").splitlines()
        out, touched = [], False
        for n, line in enumerate(lines, 1):
            if not line.strip():
                out.append(line)
                continue
            f = Fact.model_validate_json(line)
            if f.model_dump_json() != line:
                raise SystemExit(f"{path}:{n}: the serializer does not reproduce this line; refusing to rewrite")
            seen += 1
            k = kind(f)
            if k and table.get((f.period_end, k)) is None:
                unlabelled += 1
            want = store_label(f, table, covered.get(path.parent.name, []))
            if want == (None, None) and (f.fiscal_year, f.fiscal_period) == (None, None):
                want = None
            if want and want != (f.fiscal_year, f.fiscal_period):
                new = f.model_copy(update={"fiscal_year": want[0], "fiscal_period": want[1]}).model_dump_json()
                changes.append({"file": path.relative_to(pr_dir).as_posix(), "line": n, "metric": f.metric,
                                "basis": f.basis, "segment": f.segment, "period": f"{f.period_start}..{f.period_end}",
                                "value": f.value, "old": [f.fiscal_year, f.fiscal_period], "new": list(want)})
                out.append(new)
                touched = True
            else:
                out.append(line)
        if touched and write:
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            tmp.replace(path)
    return {"facts_read": seen, "changed": len(changes), "period_not_in_store": unlabelled, "changes": changes}


# --------------------------------------------------------------------------------------------------
# --redate: period dates from the archived exhibits, the stored table maps and the fixed fiscal calendar
# --------------------------------------------------------------------------------------------------
def archived_exhibit(archive: Path = EXHIBITS, cache: Path = EDGAR_CACHE) -> Callable[[str], Optional[bytes]]:
    """An exhibit's bytes by URL: data/archive/exhibits (gzip, by index.json), else the EDGAR cache; None if neither."""
    index_path = archive / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}

    def read(url: str) -> Optional[bytes]:
        meta = index.get(url)
        gz = archive / f"{meta['key']}.gz" if meta else None
        if gz is not None and gz.exists():
            return gzip.decompress(gz.read_bytes())
        body = cache / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
        return body.read_bytes() if body.exists() else None
    return read


def archived_companyfacts(cik: int, archive: Path = COMPANYFACTS, cache: Path = EDGAR_CACHE) -> dict:
    gz = archive / f"CIK{cik:010d}.json.gz"
    if gz.exists():
        return json.loads(gzip.decompress(gz.read_bytes()))
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    return json.loads((cache / f"{hashlib.sha256(url.encode()).hexdigest()}.bin").read_bytes())


def cell_key(f: Fact) -> tuple:
    """One release cell decided as one fact: what the replay must reproduce for a live line to be the same fact. The
    span's kind is part of it: a row can print one figure for the quarter and the year to date ("Gross margin | 22.8%"
    at Kroger), and only a quarter's start ever moves."""
    return (f.metric, f.basis, (f.segment or "").casefold(), f.unit, f.period_end, kind(f) or
            ("instant" if f.period_start is None else "other"), f.value, f.locator, f.url)


def _period(f: Fact) -> str:
    return f"{f.period_start}..{f.period_end}"


def _brief(f: Fact) -> dict:
    return {"metric": f.metric, "basis": f.basis, "segment": f.segment, "value": f.value, "locator": f.locator,
            "period": _period(f), "label": [f.fiscal_year, f.fiscal_period]}


def redate(pr_dir: Path, db: Path, tickers: Iterable[str], *, write: bool,
           exhibit_html: Optional[Callable[[str], Optional[bytes]]] = None,
           maps: Optional[Callable] = None, companyfacts: Callable[[int], dict] = archived_companyfacts,
           run_date: Optional[date] = None) -> dict:
    """--redate for `tickers` (see the module docstring). Returns per file the re-dated lines, the restored facts, the
    label-gate drops left alone and why, the live lines the replay did not decide from the same cell, and the replay's
    own record (each model call, and whether today's request is the recorded one byte for byte)."""
    from receipts.ingest import store_run_date
    from receipts.press_release import FiscalCalendar, replay_job, stored_fiscal_periods, stored_map, xbrl_gate
    from receipts.release_gates import label_conflict

    exhibit_html = exhibit_html or archived_exhibit()
    maps = maps or stored_map(SPARK_CACHE)
    labels, covered = stored_labels(db), covered_periods(db)
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        companies = {t: (cik, name) for cik, t, name in con.execute("SELECT cik, ticker, name FROM companies")}
        calendars: dict[int, dict] = {}
        for cik, fy, fp, start, end in con.execute(
                "SELECT cik, fiscal_year, fiscal_period, period_start, period_end FROM fiscal_periods"):
            calendars.setdefault(cik, {})[(fy, fp)] = (date.fromisoformat(start), date.fromisoformat(end))
        run_date = run_date or store_run_date(dict(con.execute("SELECT key, value FROM meta"))) or date.today()
    finally:
        con.close()
    files: list[dict] = []
    for ticker in sorted({t.upper() for t in tickers}):
        if ticker not in companies:
            files.append({"file": f"{ticker}/", "error": "not a stored company"})
            continue
        cik, name = companies[ticker]
        cf = companyfacts(cik)
        gaap = cf.get("facts", {}).get("us-gaap", {})
        calendar = FiscalCalendar(cf, stored=stored_fiscal_periods(cik, db))
        table, filings, stored_cal = labels.get(ticker, {}), covered.get(ticker, []), calendars.get(cik, {})

        def labelled(f: Fact) -> Fact:
            want = store_label(f, table, filings)
            return f.model_copy(update={"fiscal_year": want[0], "fiscal_period": want[1]}) if want else f

        for path in sorted((pr_dir / ticker).glob("*.jsonl")):
            report_path = path.with_name(path.name[: -len(".jsonl")] + ".report.json")
            rec: dict = {"file": path.relative_to(pr_dir).as_posix(), "redated": [], "restored": [],
                         "left_dropped": [], "unmatched": []}
            files.append(rec)
            if not report_path.exists():
                rec["error"] = "no mapping report"
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            job = replay_job(report, calendar, exhibit_html, maps, company=name)
            rec["replay"] = job.replay
            if job.error:
                rec["error"] = job.error
                continue
            gate = xbrl_gate(job, gaap)
            gated = {id(f) for f in gate.drop_facts}
            cells: dict[tuple, list[Fact]] = {}
            for f in job.facts:
                cells.setdefault(cell_key(f), []).append(f)
            lines = path.read_text(encoding="utf-8").splitlines()
            facts = []
            for n, line in enumerate(lines, 1):
                f = Fact.model_validate_json(line) if line.strip() else None
                if f is not None and f.model_dump_json() != line:
                    raise SystemExit(f"{path}:{n}: the serializer does not reproduce this line; refusing to rewrite")
                facts.append(f)
            live = {cell_key(f) for f in facts if f is not None}
            dated = {(cell_key(f), f.period_start) for f in facts if f is not None}
            out = []
            for n, (line, f) in enumerate(zip(lines, facts), 1):
                if f is None:
                    out.append(line)
                    continue
                hits = cells.get(cell_key(f), [])
                if len(hits) != 1:
                    rec["unmatched"].append({"line": n, **_brief(f)})
                    out.append(line)
                    continue
                if hits[0].period_start == f.period_start:
                    out.append(line)
                    continue
                g = labelled(f.model_copy(update={"period_start": hits[0].period_start}))
                why = label_conflict(g, stored_cal)
                if why or (cell_key(g), g.period_start) in dated:
                    rec["left_dropped"].append({"line": n, **_brief(g), "why": f"re-dating refused: "
                                                f"{why or 'another line already holds the cell with these dates'}"})
                    out.append(line)
                    continue
                rec["redated"].append({"line": n, **_brief(g), "old_period": _period(f),
                                       "old_label": [f.fiscal_year, f.fiscal_period]})
                out.append(g.model_dump_json())
            restored: list[Fact] = []
            for drop in report.get("release_gates", []):
                if drop.get("rule") != "label_dates":
                    continue
                start, end = drop["period"].split("..")
                was = span_kind(None if start == "None" else date.fromisoformat(start), date.fromisoformat(end))
                hits = [f for f in job.facts if f.metric == drop["metric"] and f.basis == drop["basis"]
                        and f.segment == drop["segment"] and f.value == drop["value"]
                        and f.locator == drop["locator"] and f.period_end.isoformat() == end and kind(f) == was]
                brief = {k: drop.get(k) for k in ("metric", "basis", "segment", "value", "locator", "period")}
                if len(hits) != 1:
                    rec["left_dropped"].append({**brief, "why": f"the replay decides {len(hits)} facts from its cell"})
                    continue
                if cell_key(hits[0]) in live or any(cell_key(hits[0]) == cell_key(r) for r in restored):
                    continue
                if gate.quarantine or id(hits[0]) in gated:
                    rec["left_dropped"].append({**brief, "why": f"the XBRL gate drops it: {gate.quarantine or ''}"})
                    continue
                g = labelled(hits[0])
                why = label_conflict(g, stored_cal)
                if why:
                    rec["left_dropped"].append({**brief, "why": f"still contradicts its label: {why}"})
                    continue
                restored.append(g)
                rec["restored"].append({**_brief(g), "url": g.url, "dropped_as": drop["period"],
                                        "dropped_why": drop.get("why")})
            if write and (rec["redated"] or restored):
                tmp = path.with_suffix(".jsonl.tmp")
                tmp.write_text("".join(line + "\n" for line in out) + "".join(g.model_dump_json() + "\n"
                                                                              for g in restored), encoding="utf-8")
                tmp.replace(path)
                report.setdefault("redate", []).append({"decided_on": run_date.isoformat(),
                                                        "redated": rec["redated"], "restored": rec["restored"]})
                rtmp = report_path.with_suffix(".json.tmp")
                rtmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
                rtmp.replace(report_path)
    return {"redated": sum(len(r["redated"]) for r in files), "restored": sum(len(r["restored"]) for r in files),
            "files": files}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pr-dir", type=Path, default=REPO / "data/pr_facts")
    ap.add_argument("--db", type=Path, default=REPO / "data/facts.sqlite")
    ap.add_argument("--dry-run", action="store_true", help="list the changes; write nothing")
    ap.add_argument("--out", type=Path, help="write the change list here (JSON)")
    ap.add_argument("--redate", nargs="+", metavar="TICKER",
                    help="first re-derive these companies' release dates offline and restore what the label gate "
                         "dropped only for 13-week dates (see the module docstring)")
    args = ap.parse_args(argv)
    dated = redate(args.pr_dir, args.db, args.redate, write=not args.dry_run) if args.redate else None
    result = relabel(args.pr_dir, args.db, write=not args.dry_run)
    if dated is not None:
        result["redate"] = dated
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    if dated is not None:
        print(f"{'would re-date' if args.dry_run else 're-dated'} {dated['redated']} release facts and "
              f"{'would restore' if args.dry_run else 'restored'} {dated['restored']}")
        for r in dated["files"]:
            if r.get("error") or r["redated"] or r["restored"] or r["left_dropped"]:
                print(f"  {r['file']}: re-dated {len(r['redated'])}, restored {len(r['restored'])}, label-gate drops "
                      f"left {len(r['left_dropped'])}, unmatched {len(r['unmatched'])}"
                      + (f"  ERROR {r['error']}" if r.get("error") else ""))
    by_file: dict[str, int] = {}
    for c in result["changes"]:
        by_file[c["file"]] = by_file.get(c["file"], 0) + 1
    print(f"{'would change' if args.dry_run else 'changed'} {result['changed']} of {result['facts_read']} release "
          f"facts; {result['period_not_in_store']} quarterly or annual facts have a period the store does not label")
    for name, count in sorted(by_file.items()):
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
