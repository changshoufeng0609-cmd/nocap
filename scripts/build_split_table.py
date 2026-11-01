"""Build receipts/splits.py, the stock splits the companies' own XBRL reports, and its decision record.

The store keeps per-share figures but not the split-ratio fact, so receipts.verify.company_splits reads this table
(S&P 500 QA round 3, T4: Carvana's 5-for-1 showed in no period held on both share bases). For every company in the
store, each StockholdersEquityNoteStockSplitConversionRatio(1) fact in its archived companyfacts
(data/archive/companyfacts/CIK##########.json.gz, read gzip in memory) is read, grouped into events, and each event is
confirmed against the store's per-share figures (eps_diluted, eps_basic, dividend_per_share):

  * A value r names an n-for-1 or 1-for-n split when r or 1/r is within 1% of an n in verify.SPLIT_RATIOS; other
    values (1.5, 10,000, a spin-off's 0.0556) are ignored. Companies tag either way, so the tag never gives the
    direction: Carvana's 5-for-1 reads 0.2, DuPont's 1-for-3 reads 0.333.
  * Facts of one company and n within 120 days of each other are one event, dated by its earliest and latest instant
    (or, without one, its duration's start and end), and reported by the earliest filing among them.
  * Direction: versions of one period, the older accepted before the event and the newer on or after it, restated by
    about n (verify._restated_by: within 15%); else the median quarterly EPS filed in the 400 days after the event
    against the 400 days before, beyond half the ratio's logarithm either way. Neither, or pairs that disagree: the
    event is rejected, and NoCap's verdicts don't use it.

Writes receipts/splits.py and eval/evidence_expansion/sp500-splits-20261031.json. Offline, deterministic and free: no
network, no model, no clock; the same archive and store give the same bytes. Run it after scripts/build_snapshot.py
when the archive or the snapshot changes.

    .venv/bin/python scripts/build_split_table.py           # write both files
    .venv/bin/python scripts/build_split_table.py --check   # write nothing; exit 1 if either committed file differs
"""
from __future__ import annotations

import argparse
import difflib
import gzip
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts.store import Store  # noqa: E402
from receipts.verify import SPLIT_RATIOS, _fact_kind, _restated_by, to_utc  # noqa: E402

ARCHIVE = REPO / "data/archive/companyfacts"
DB = REPO / "data/snapshot/facts.sqlite"
PR = REPO / "data/snapshot/pr_facts"
MODULE = REPO / "receipts/splits.py"
RECORD = REPO / "eval/evidence_expansion/sp500-splits-20261031.json"
CONCEPT = "StockholdersEquityNoteStockSplitConversionRatio"  # and ...Ratio1
PER_SHARE = ("eps_diluted", "eps_basic", "dividend_per_share")
SAME_EVENT_DAYS, LEVEL_WINDOW_DAYS = 120, 400


def ratio_n(value) -> int | None:
    if not isinstance(value, (int, float)) or value <= 0 or value == 1:
        return None
    x = value if value > 1 else 1 / value
    n = round(x)
    return n if n in SPLIT_RATIOS and abs(x - n) <= 0.01 * n else None


def utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def ratio_facts(archive: Path, cik: int) -> list[dict]:
    """The company's split-ratio facts, as companyfacts lists them."""
    path = archive / f"CIK{int(cik):010d}.json.gz"
    if not path.is_file():
        return []
    facts = json.loads(gzip.decompress(path.read_bytes())).get("facts", {}).get("us-gaap", {})
    rows = []
    for concept in sorted(c for c in facts if c.startswith(CONCEPT)):
        for unit, entries in sorted(facts[concept].get("units", {}).items()):
            for e in entries:
                rows.append({"value": e.get("val"), "start": e.get("start"), "end": e.get("end"), "accn": e.get("accn"),
                             "form": e.get("form"), "filed": e.get("filed")})
    return sorted(rows, key=lambda r: (r["end"] or "", r["start"] or "", r["filed"] or "", r["accn"] or "",
                                       str(r["value"])))


def decide(ticker: str, cik: int, rows: list[dict], per_share: list) -> list[dict]:
    """Every fact's or event's decision (split, rejected or ignored), with its evidence."""
    out, groups = [], []
    for r in rows:
        n = ratio_n(r["value"])
        if n is None:
            out.append({"ticker": ticker, "value": r["value"], "end": r["end"], "start": r["start"], "accn": r["accn"],
                        "decision": "ignored", "reason": "the value is no split ratio NoCap knows (2, 3, ..., 100)"})
            continue
        end = date.fromisoformat(r["end"])
        g = next((g for g in groups if g["n"] == n
                  and abs((end - date.fromisoformat(g["rows"][-1]["end"])).days) <= SAME_EVENT_DAYS), None)
        if g is None:
            groups.append({"n": n, "rows": [r]})
        else:
            g["rows"].append(r)
    versions = defaultdict(list)
    for f in per_share:
        versions[(f.metric, f.basis, f.period_start, f.period_end)].append(f)
    for vs in versions.values():
        vs.sort(key=lambda f: (to_utc(f.accepted_at), f.accession, f.source))
    for g in groups:
        n, rs = g["n"], g["rows"]
        instants = [date.fromisoformat(r["end"]) for r in rs if not r["start"]]
        if instants:
            starts, ends = min(instants), max(instants)
        else:
            starts = min(date.fromisoformat(r["start"]) for r in rs)
            ends = max(date.fromisoformat(r["end"]) for r in rs)
        first = min(rs, key=lambda r: (r["filed"], r["accn"]))
        pairs = []
        for vs in versions.values():
            for i, old in enumerate(vs):
                for new in vs[i + 1:]:
                    if not to_utc(old.accepted_at) < utc(starts) <= to_utc(new.accepted_at):
                        continue
                    for forward in (True, False):
                        if _restated_by(old, new, n, forward):
                            pairs.append((forward, old, new))
        directions = {p[0] for p in pairs}
        if len(directions) == 1:
            forward = directions.pop()
            evidence = f"{len(pairs)} per-share versions restated by the ratio"
        elif not directions:
            quarters = [f for f in per_share
                        if f.metric != "dividend_per_share" and f.value and _fact_kind(f) == "quarter"]
            window = timedelta(days=LEVEL_WINDOW_DAYS)
            before = sorted(abs(f.value) for f in quarters
                            if utc(starts - window) <= to_utc(f.accepted_at) < utc(starts))
            after = sorted(abs(f.value) for f in quarters if utc(ends) < to_utc(f.accepted_at) <= utc(ends + window))
            forward = None
            if before and after:
                shift = math.log(after[len(after) // 2] / before[len(before) // 2])
                if abs(shift) >= math.log(n) / 2:
                    forward = shift < 0
                evidence = (f"level shift: quarterly per-share figures {math.exp(shift):.2f}x after"
                            if forward is not None else f"no level shift ({math.exp(shift):.2f}x)")
            else:
                evidence = "no per-share figures around the date"
        else:
            forward, evidence = None, "pairs disagree on the direction"
        entry = {"ticker": ticker, "cik": cik, "n": n, "values": sorted({r["value"] for r in rs}),
                 "contexts": sorted({(r["start"] or "", r["end"]) for r in rs}),
                 "d_old": str(starts), "d_new": str(ends),
                 "first_filed": first["filed"], "first_accn": first["accn"], "first_form": first["form"],
                 "pairs": len(pairs), "pair_examples": [[p[1].metric, p[1].basis, str(p[1].period_end), p[1].value,
                                                         p[2].value] for p in pairs[:3]], "evidence": evidence}
        if forward is None:
            out.append({**entry, "decision": "rejected", "reason": evidence})
        else:
            out.append({**entry, "decision": "split", "direction": "forward" if forward else "reverse"})
    return out


def build(archive: Path, db: Path, pr: Path) -> list[dict]:
    store = Store(db, pr_dir=pr)
    try:
        decisions = []
        for cik, ticker, _name, _aliases in sorted(store._index().rows, key=lambda r: r[1]):
            rows = ratio_facts(archive, cik)
            if not rows:
                continue
            per_share = [f for m in PER_SHARE for f in (store.facts(cik, m, basis=None) or [])
                         if f.period_start is not None and f.segment is None and math.isfinite(f.value)]
            entries = decide(ticker, cik, rows, per_share)
            filings = {f["accession"]: f["accepted_at"] for f in store.filings(cik)}
            for d in entries:
                if d["decision"] == "split":
                    # Never guess midnight from a filing date: as-of checks need the public instant (review L7).
                    d["first_accepted_at"] = filings[d["first_accn"]]
            decisions += entries
        return decisions
    finally:
        store.close()


HEAD = '''"""Stock splits the companies' own XBRL reports (StockholdersEquityNoteStockSplitConversionRatio1), for
receipts.verify.company_splits. Generated by scripts/build_split_table.py: do not edit by hand.

The store keeps per-share figures but not the split-ratio fact, so a split shows there only when some period was filed
on both share bases. Carvana's 5-for-1 (May 2026) never did: its EPS is dimensional in XBRL, so only releases carry it,
and none holds a period on both bases. This table, from the archived companyfacts of the covered companies, gives the
ratio and dates of each split since NoCap's filings begin; the per-share figures still date it more precisely.

Each event was confirmed against the served per-share figures: the tag names a ratio n (or 1/n: companies tag either
way, so Carvana's 5-for-1 reads 0.2 and DuPont's 1-for-3 reads 0.333), and the direction comes from figures, never from
the tag: versions of one period restated by about n across the date, or else the level of quarterly per-share figures
filed in the year after the date against the year before (Carvana's are 0.23x). Rejected or ignored facts (a 2021 split
repeated in a 2025 context, a spin-off's distribution ratio, ratios like 1.5 or 10,000) and every decision's evidence:
eval/evidence_expansion/sp500-splits-20261031.json.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import NamedTuple


class SplitEvent(NamedTuple):
    """A split a company's XBRL reports: filings accepted before `starts` are on the old share basis; after `ends`, on
    the new one unless restated figures show a later date; `reported` is the acceptance of its first reporting filing."""

    ratio: int        # n of an n-for-1 split, or of a 1-for-n reverse split
    forward: bool     # n-for-1: figures on the new share basis are the old ones divided by n
    starts: date      # the first date the facts give (an instant, or a duration's start)
    ends: date        # the last date the facts give (an instant, or a duration's end)
    reported: datetime  # UTC acceptance of the first filing that reports the ratio


SPLIT_EVENTS: dict[str, tuple[SplitEvent, ...]] = {
'''
TAIL = '''}


def split_events(ticker: str) -> tuple[SplitEvent, ...]:
    return SPLIT_EVENTS.get((ticker or "").upper(), ())
'''


def py_date(s: str) -> str:
    d = date.fromisoformat(s)
    return f"date({d.year}, {d.month}, {d.day})"


def render_module(decisions: list[dict]) -> str:
    splits = defaultdict(list)
    for d in decisions:
        if d["decision"] == "split":
            splits[d["ticker"]].append(d)
    lines = []
    for ticker in sorted(splits):
        items = [f"SplitEvent({d['n']}, {d['direction'] == 'forward'}, {py_date(d['d_old'])}, {py_date(d['d_new'])}, "
                 f"datetime.fromisoformat({d['first_accepted_at']!r}))" for d in sorted(splits[ticker], key=lambda d: d["d_old"])]
        head = f'    "{ticker}": ('
        if len(items) == 1:
            lines.append(f"{head}{items[0]},),")
        else:
            pad = " " * len(head)
            lines.append(head + f",\n{pad}".join(items) + "),")
    return HEAD + "\n".join(lines) + "\n" + TAIL


def render_record(decisions: list[dict], db: Path) -> str:
    manifest = db.parent / "manifest.json"
    fingerprint = json.loads(manifest.read_text()).get("fingerprint") if manifest.is_file() else None
    store = db.parent.relative_to(REPO) if db.parent.is_relative_to(REPO) else db.parent.name
    kinds = (("splits", "split"), ("rejected_events", "rejected"), ("ignored_facts", "ignored"))
    record = {
        "generated_by": "scripts/build_split_table.py",
        "purpose": ("Stock-split events for receipts/splits.py: every StockholdersEquityNoteStockSplitConversionRatio"
                    "(1) fact in the archived companyfacts of the covered companies, grouped into events and confirmed "
                    "against the per-share figures the store serves."),
        "inputs": {"companyfacts": "data/archive/companyfacts", "store": str(store), "store_fingerprint": fingerprint},
        "rule": ("A fact's value r names an n-for-1 or 1-for-n split when r or 1/r is within 1% of an n in "
                 "receipts.verify.SPLIT_RATIOS; other values are ignored. Facts of one company and n within 120 days "
                 "are one event, dated by its earliest and latest instant (or, without one, its duration's start and "
                 "end). The direction comes from the per-share figures, never from the tag: versions of one period, "
                 "the older filed before the event and the newer on or after it, restated by about n (within 15%); "
                 "or, without such versions, the median quarterly EPS filed in the 400 days after the event against "
                 "the 400 days before, beyond half the ratio's logarithm either way. An event neither confirms is "
                 "rejected."),
        "summary": {"decisions": len(decisions), **{k: sum(d["decision"] == v for d in decisions) for k, v in kinds}},
        "decisions": decisions,
    }
    return json.dumps(record, indent=1, default=str) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--archive", type=Path, default=ARCHIVE,
                    help="the companyfacts archive (default data/archive/companyfacts)")
    ap.add_argument("--db", type=Path, default=DB, help="the store's facts.sqlite (default data/snapshot/facts.sqlite)")
    ap.add_argument("--pr-dir", type=Path, default=PR,
                    help="the store's release facts (default data/snapshot/pr_facts)")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if a committed file differs")
    args = ap.parse_args(argv)
    if not args.archive.is_dir():
        print(f"no companyfacts archive at {args.archive}", file=sys.stderr)
        return 2
    decisions = build(args.archive, args.db, args.pr_dir)
    outputs = {MODULE: render_module(decisions), RECORD: render_record(decisions, args.db)}
    splits = sum(d["decision"] == "split" for d in decisions)
    if args.check:
        stale = [p for p, text in outputs.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for p in stale:
            old = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
            diff = list(difflib.unified_diff(old, outputs[p].splitlines(), str(p.relative_to(REPO)), "generated",
                                             lineterm="", n=1))
            print("\n".join(diff[:40]) + ("\n..." if len(diff) > 40 else ""))
        print(f"{splits} splits; " + ("committed files are current" if not stale
                                       else f"{len(stale)} file(s) differ: run scripts/build_split_table.py"))
        return 1 if stale else 0
    for p, text in outputs.items():
        p.write_text(text, encoding="utf-8")
    print(f"{splits} splits; wrote {MODULE.relative_to(REPO)} and {RECORD.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
