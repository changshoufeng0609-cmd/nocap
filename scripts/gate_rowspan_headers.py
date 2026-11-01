"""One-off gate: drop served release facts read under a period header that receipts.press_release's table grid moved
while it ignored rowspan, where the company's own XBRL proves the move or neither reading can vouch for the fact.

Until a40299d, receipts.press_release._grid_rows put each cell of a table row right after the row's previous cell.
A header cell with rowspan=2 also covers the grid columns below it, so HTML lays the next row's cells out to its
right; the grid laid them out from column 0 instead, and that row's period headers slid one column to the left.
Exxon Mobil's, Dow's, Coca-Cola's and Cboe's earnings tables put their unit label ("Dollars in millions",
rowspan=2) over the year row, so the grid read each year over the next column: Dow's Q2 2025 diluted EPS of -$1.18
was stored as Q2 2026's. The XBRL gate caught such a file when its GAAP rows met XBRL; a file whose shifted rows are
segment or non-GAAP figures, or whose period's 10-Q is not in companyfacts yet, was served.

The grid honors rowspan since a40299d; the files mapped before it still hold what the old grid read until they are
re-mapped. This gate re-reads each live release file's candidate tables twice: with the old grid, as the mapper
did, and with every cell placed after the grid columns an earlier row's rowspan still covers (as HTML does). A file
re-mapped since reads as HTML lays out, so its facts never sit under a header the layout moves from theirs. A table
whose data rows read the same both ways but whose column headers name other periods is proven moved when, in a
column whose header changed, a figure with at least four significant digits equals the company's XBRL value (a
company-level 10-Q/10-K figure, or one derived from them) for the corrected header's period and none for the old
header's, and no such figure anywhere in the table sides with an old header. In a proven table, a served fact whose
cell (row label and text) is found in exactly one column of the exhibit's candidate tables, whose period the old
header allows and the corrected one does not, is dropped ("rowspan_header"). Any other served fact whose cell sits in
a moved column of a table XBRL does not confirm as the mapper read it is dropped too ("rowspan_unproven") unless a
place its cell appears has a corrected header that allows its period: neither reading can vouch for it, and "?" is
the safe answer until the file is re-mapped. The files are rewritten as scripts/gate_release_facts.py
rewrites them (every other line byte for byte) and each drop is recorded under "release_gates" (with its rule) in
the file's .report.json. A re-run drops nothing. Offline: no network, no model.

    .venv/bin/python scripts/gate_rowspan_headers.py --dry-run --out rowspan.json   # list, write nothing
    .venv/bin/python scripts/gate_rowspan_headers.py --out rowspan.json             # rewrite the files
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts import press_release as pr  # noqa: E402
from receipts.types import Fact  # noqa: E402

DB = REPO / "data/facts.sqlite"
PR = REPO / "data/pr_facts"
EXHIBITS = REPO / "data/archive/exhibits"
CACHE = REPO / "data/cache/edgar"
RULE = "rowspan_header"
UNPROVEN = "rowspan_unproven"
SCALE = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}
SPANS = {"quarter": (80, 100), "ytd": (80, 290), "annual": (350, 380), "ttm": (350, 380)}  # a Q1 YTD is a quarter
# Year-to-date headers that name their length: six months (or 26 weeks, the first half) and nine months (39 weeks)
SIX_MONTHS = re.compile(r"\b(?:six|6)[- ]months?\b|\b2[56][- ]weeks?\b|\bfirst half\b", re.I)
NINE_MONTHS = re.compile(r"\b(?:nine|9)[- ]months?\b|\b(?:39|40)[- ]weeks?\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b\.?", re.I)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}


def _rowspan(value: Any) -> int:
    try:
        return max(1, min(int(str(value).strip()), 100))
    except (TypeError, ValueError):
        return 1


def grid_rows_html(el: Any) -> list[list[list[Any]]]:
    """receipts.press_release._grid_rows, with each cell placed after the grid columns that an earlier row's rowspan
    still covers, as HTML lays a table out."""
    rows, carry = [], {}
    for tr in el.find_all("tr"):
        if tr.find_parent("table") is not el:
            continue
        tds = tr.find_all(["td", "th"], recursive=False) or [
            c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]
        taken = {c for c, n in carry.items() if n > 0}
        col, cells, new = 0, [], {}
        for td in tds:
            while col in taken:
                col += 1
            span, down = pr._colspan(td.get("colspan")), _rowspan(td.get("rowspan"))
            cells.append((pr.clean_text(td.get_text(" ", strip=True)), col, col + span - 1))
            if down > 1:
                for c in range(col, col + span):
                    new[c] = down - 1
            col += span
        carry = {c: n - 1 for c, n in carry.items() if n > 1}
        carry.update(new)
        rows.append(pr.merge_cells(cells))
    return rows


def grid_rows_mapped(el: Any) -> list[list[list[Any]]]:
    """The grid receipts.press_release read the stored release files with until it honored rowspan (a40299d): each
    cell placed right after the row's previous cell."""
    rows = []
    for tr in el.find_all("tr"):
        if tr.find_parent("table") is not el:
            continue
        tds = tr.find_all(["td", "th"], recursive=False) or [
            c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]
        col, cells = 0, []
        for td in tds:
            span = pr._colspan(td.get("colspan"))
            cells.append((pr.clean_text(td.get_text(" ", strip=True)), col, col + span - 1))
            col += span
        rows.append(pr.merge_cells(cells))
    return rows


@contextlib.contextmanager
def grid(rows_of: Any) -> Iterator[None]:
    original = pr._grid_rows
    pr._grid_rows = rows_of
    try:
        yield
    finally:
        pr._grid_rows = original


def both_readings(html: bytes) -> tuple[dict[int, Any], dict[int, Any]]:
    """{table index: PTable} as the mapper read the exhibit, and as HTML lays its tables out."""
    with grid(grid_rows_mapped):
        mapper = {t.index: t for t in pr.parse_tables(html)}
    with grid(grid_rows_html):
        laid_out = {t.index: t for t in pr.parse_tables(html)}
    return mapper, laid_out


class ExhibitHtml:
    """An exhibit's HTML by URL: data/archive/exhibits, else the EDGAR cache; None when neither holds it."""

    def __init__(self, archive: Path = EXHIBITS, cache: Path = CACHE):
        index = archive / "index.json"
        self.index = json.loads(index.read_text()) if index.exists() else {}
        self.archive, self.cache = archive, cache

    def __call__(self, url: str) -> Optional[bytes]:
        meta = self.index.get(url)
        if meta and (self.archive / f"{meta['key']}.gz").exists():
            return gzip.decompress((self.archive / f"{meta['key']}.gz").read_bytes())
        body = self.cache / f"{hashlib.sha256(url.encode()).hexdigest()}.bin"
        return body.read_bytes() if body.exists() else None


def header(t: Any, k: int) -> dict:
    """The period a column header names: printed dates, else years and months; and the durations it allows."""
    text = " ".join(t.columns[k]) if k < len(t.columns) else ""
    hints = pr.duration_hints(text) or set()
    spans = [SPANS[d] for d in hints if d in SPANS and d != "ytd"]
    if "ytd" in hints:
        spans.append((150, 200) if SIX_MONTHS.search(text) else (250, 290) if NINE_MONTHS.search(text)
                     else SPANS["ytd"])
    return {"text": " / ".join(t.columns[k]) if k < len(t.columns) else "", "dates": pr.parse_dates(text),
            "years": {int(y) for y in YEAR.findall(text)},
            "months": {MONTHS[m.group(1)[:3].lower()] for m in MONTH.finditer(text)}, "spans": spans}


def same_period(h: dict, other: dict) -> bool:
    return (h["dates"], h["years"], h["months"]) == (other["dates"], other["years"], other["months"])


def allows(h: dict, end: date, start: Optional[date], fiscal_year: Optional[int]) -> bool:
    """Whether a header allows a period: a printed date within 7 days of its end, else its year (or fiscal year) and
    month; a header that names neither allows anything. A duration it names must fit the period's length."""
    if h["spans"] and start is not None:
        days = (end - start).days + 1
        if not any(lo <= days <= hi for lo, hi in h["spans"]):
            return False
    if h["dates"]:
        return any(abs((d - end).days) <= 7 for d in h["dates"])
    if h["years"] and end.year not in h["years"] and fiscal_year not in h["years"]:
        return False
    return not h["months"] or end.month in h["months"]


def _sig_digits(text: str) -> int:
    return len(re.sub(r"^0+", "", re.sub(r"\D", "", text.split(".")[0] + "".join(text.split(".")[1:]))))


def _amount(text: str, scale: Optional[str]) -> Optional[tuple[float, float]]:
    """(value, half unit) of a money cell with four or more significant digits in the table's scale."""
    if not scale or scale not in SCALE or "%" in text or _sig_digits(text) < 4:
        return None
    n = pr.parse_number(text)
    if n is None:
        return None
    return float(n) * SCALE[scale], 0.5 * SCALE[scale] * 10.0 ** -pr.frac_digits(text) + 1e-6


def xbrl_values(db: Path) -> dict[str, list[tuple[float, Optional[date], date, Optional[int]]]]:
    """ticker -> company-level USD XBRL and derived values: (value, start, end, fiscal year)."""
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        out: dict[str, list] = defaultdict(list)
        for ticker, value, start, end, fy in con.execute(
                "SELECT ticker, value, period_start, period_end, fiscal_year FROM facts WHERE segment IS NULL "
                "AND source IN ('xbrl', 'derived') AND unit = 'USD'"):
            out[ticker].append((float(value), date.fromisoformat(start) if start else None, date.fromisoformat(end),
                                fy))
        return out
    finally:
        con.close()


def _matches(amount: tuple[float, float], h: dict, xbrl: list) -> bool:
    value, half = amount
    return any(abs(v - value) <= half and allows(h, end, start, fy) for v, start, end, fy in xbrl)


def table_shift(old: Any, new: Any, xbrl: list) -> Optional[dict]:
    """Columns whose header the HTML layout changes, paired by identical cells, and the XBRL vote between the two
    readings; None when the data rows differ (the readings disagree about more than headers)."""
    if [(r.label, r.cells) for r in old.rows] != [(r.label, r.cells) for r in new.rows]:
        return None
    cells = lambda t, k: tuple(r.cells.get(k) for r in t.rows)
    new_cols = {cells(new, j): j for j in range(len(new.columns))}
    moved, votes = {}, {"corrected": [], "old": []}
    for k in range(len(old.columns)):
        vector = cells(old, k)
        j = new_cols.get(vector)
        if j is None or not any(vector):
            continue
        h_old, h_new = header(old, k), header(new, j)
        if same_period(h_old, h_new):
            continue
        moved[k] = (h_old, h_new)
        for row in old.rows:
            text = row.cells.get(k)
            amount = _amount(text, old.scale) if text else None
            if amount is None:
                continue
            on_new, on_old = _matches(amount, h_new, xbrl), _matches(amount, h_old, xbrl)
            if on_new and not on_old:
                votes["corrected"].append(f"{row.label} | {text} ({h_new['text']})")
            elif on_old and not on_new:
                votes["old"].append(f"{row.label} | {text} ({h_old['text']})")
    if not moved:
        return {"moved": {}, "votes": votes, "proven": False}
    return {"moved": moved, "votes": votes, "proven": bool(votes["corrected"]) and not votes["old"]}


def gate_file(path: Path, report: dict, xbrl: list, html_of: Any) -> tuple[list[int], list[dict], list[dict]]:
    """(line indexes to drop, their records, the tables examined) for one live release file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    facts = [(i, Fact.model_validate_json(x)) for i, x in enumerate(lines) if x.strip()]
    parts = [(report.get("url"), report.get("candidates") or [])] + [
        (s.get("url"), s.get("candidates") or []) for s in report.get("secondary") or []]
    drop, records, tables = [], [], []
    for url, candidates in parts:
        if not url or not candidates:
            continue
        html = html_of(url)
        if html is None or b"rowspan" not in html.lower():
            continue  # no rowspan: both layouts are one
        old, new = both_readings(html)
        shifts = {i: table_shift(old[i], new[i], xbrl) for i in candidates if i in old and i in new}
        for i, s in shifts.items():
            if s is not None and s["moved"]:
                tables.append({"url": url, "table": i, "proven": s["proven"], "votes": s["votes"],
                               "moved": {k: [a["text"], b["text"]] for k, (a, b) in s["moved"].items()}})
        def corrected(i: int, k: int) -> dict:
            s = shifts.get(i)
            return s["moved"][k][1] if s and k in s["moved"] else header(old[i], k)

        for n, f in facts:
            if f.url != url or not f.locator or " | " not in f.locator or n in drop:
                continue
            label, text = f.locator.split(" | ", 1)
            spots = [(i, k) for i in candidates if i in old for r in old[i].rows if r.label == label
                     for k, v in r.cells.items() if v == text]
            moved = [(i, k) for i, k in spots if shifts.get(i) and k in shifts[i]["moved"]]
            if not moved:
                continue
            fits = lambda h: allows(h, f.period_end, f.period_start, f.fiscal_year)
            record = {"metric": f.metric, "basis": f.basis, "segment": f.segment,
                      "period": f"{f.period_start}..{f.period_end}", "value": f.value, "locator": f.locator}
            if len(spots) == 1 and shifts[spots[0][0]]["proven"]:
                i, k = spots[0]
                s = shifts[i]
                h_old, h_new = s["moved"][k]
                if fits(h_old) and not fits(h_new):
                    drop.append(n)
                    records.append({"rule": RULE, **record,
                                    "why": f"table {i} column header '{h_old['text']}' reads '{h_new['text']}' laid "
                                           f"out as HTML does (rowspan); XBRL sides with the corrected header: "
                                           + "; ".join(s["votes"]["corrected"][:3])})
                    continue
            # Not proven: a fact in a moved column of a table XBRL does not confirm as the mapper read it is dropped
            # unless a place its cell appears has a corrected header that allows its period ("?" until a re-map).
            settled = lambda i: bool(shifts[i]["votes"]["old"]) and not shifts[i]["votes"]["corrected"]
            if all(settled(i) for i, _ in moved) or any(fits(corrected(i, k)) for i, k in spots):
                continue
            drop.append(n)
            places = "; ".join(f"table {i} '{header(old[i], k)['text']}' -> '{corrected(i, k)['text']}'"
                               for i, k in spots[:3])
            records.append({"rule": UNPROVEN, **record,
                            "why": f"its cell sits under a period header the HTML layout (rowspan) moves, and no "
                                   f"reading XBRL can confirm allows {f.period_start}..{f.period_end}: {places}"})
    return drop, records, tables


def run(pr_dir: Path = PR, db: Path = DB, *, write: bool, html_of: Any = None) -> dict:
    xbrl, html_of = xbrl_values(db), html_of or ExhibitHtml()
    out: dict[str, Any] = {"files": 0, "dropped": [], "files_changed": 0, "tables": []}
    for report_path in sorted(pr_dir.glob("*/*.report.json")):
        path = report_path.with_name(report_path.name.replace(".report.json", ".jsonl"))
        if not path.exists():
            continue  # quarantined or empty: never served
        out["files"] += 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
        ticker = report_path.parent.name
        drop, records, tables = gate_file(path, report, xbrl.get(ticker, []), html_of)
        name = f"{ticker}/{path.name}"
        out["tables"] += [{"file": name, **t} for t in tables]
        out["dropped"] += [{"file": name, **r} for r in records]
        if not drop or not write:
            continue
        lines, gone = path.read_text(encoding="utf-8").splitlines(), set(drop)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(line + "\n" for n, line in enumerate(lines) if n not in gone), encoding="utf-8")
        tmp.replace(path)
        report.setdefault("release_gates", []).extend(records)
        rtmp = report_path.with_suffix(".json.tmp")
        rtmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        rtmp.replace(report_path)
        out["files_changed"] += 1
    out["proven_tables"] = sum(1 for t in out["tables"] if t["proven"])
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pr-dir", type=Path, default=PR)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--dry-run", action="store_true", help="list the drops; write nothing")
    ap.add_argument("--out", type=Path, help="write the result here (JSON)")
    args = ap.parse_args(argv)
    result = run(args.pr_dir, args.db, write=not args.dry_run)
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str))
    verb = "would drop" if args.dry_run else "dropped"
    print(f"{result['files']} live release files; {len(result['tables'])} tables whose period headers move under the "
          f"HTML layout, {result['proven_tables']} proven by XBRL; {verb} {len(result['dropped'])} release facts"
          + ("" if args.dry_run else f" in {result['files_changed']} files"))
    for d in result["dropped"]:
        print(f"  {d['file']}: {d['metric']} {d['basis']} {d['segment'] or ''} {d['period']} {d['value']:,} "
              f"({d['locator'][:60]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
