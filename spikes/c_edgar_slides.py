#!/usr/bin/env python3
"""Spike C helper: real earnings slides from EDGAR, and an XBRL check of the values read off them.

  find     recent Item 2.02 8-Ks for a ticker, and every file in each filing folder (index.json)
  fetch    download page images from one filing to spikes/out/c_slide_candidates/ (gitignored) for review
  select   copy chosen pages to spikes/inputs/slides/ with source metadata (accession, URL, acceptanceDateTime)
  compare  check a c_slides.py chart JSON against XBRL companyfacts for the same quarters

  .venv/bin/python spikes/c_edgar_slides.py find --ticker TSLA
  .venv/bin/python spikes/c_edgar_slides.py fetch --ticker TSLA --accn 0001628280-26-000000 --pattern 'ex991.*\\.jpg' --pages 1-12
  .venv/bin/python spikes/c_edgar_slides.py select --ticker TSLA --accn 0001628280-26-000000 --files page009.jpg
  .venv/bin/python spikes/c_edgar_slides.py compare --ticker TSLA --chart spikes/out/c_chart_<name>.json

All SEC traffic goes through spikes/a_edgar.py's Edgar client: a declared User-Agent from .env,
a content-hash cache, and at most 4 requests per second. Live requests are counted in
spikes/out/c_sec_requests.jsonl and refused past SEC_REQUEST_BUDGET; cache hits are free.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a_edgar  # noqa: E402  (spikes/a_edgar.py: the polite EDGAR client from Spike A)
from c_slides import canonical_period, unit_scale  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "spikes" / "out" / "c_slide_candidates"
SLIDES = ROOT / "spikes" / "inputs" / "slides"
LEDGER = ROOT / "spikes" / "out" / "c_sec_requests.jsonl"
SEC_REQUEST_BUDGET = 30  # live SEC requests for this whole task


def _used() -> int:
    if not LEDGER.exists():
        return 0
    return sum(json.loads(line)["live_requests"] for line in LEDGER.read_text().splitlines() if line.strip())


class BudgetedEdgar(a_edgar.Edgar):
    """Spike A's client, plus a hard cap on live (uncached) requests."""

    def __init__(self, user_agent: str, remaining: int):
        super().__init__(user_agent)
        self.remaining = remaining

    def get(self, url: str) -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest()
        cached = (a_edgar.CACHE / f"{key}.bin").exists() and (a_edgar.CACHE / f"{key}.json").exists()
        if not cached and self.live_requests >= self.remaining:
            raise SystemExit(f"SEC request budget reached ({SEC_REQUEST_BUDGET}); refusing {url}")
        return super().get(url)


def open_edgar() -> BudgetedEdgar:
    load_dotenv(ROOT / ".env")
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"')
    if not ua or "example.com" in ua or "@" not in ua:
        raise SystemExit("SEC_USER_AGENT is missing in .env (format: 'Name email@example.com').")
    used = _used()
    print(f"SEC requests used so far: {used}/{SEC_REQUEST_BUDGET}")
    return BudgetedEdgar(ua, max(0, SEC_REQUEST_BUDGET - used))


def close_edgar(ed: BudgetedEdgar, command: str) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "command": command, "live_requests": ed.live_requests,
                            "cache_hits": ed.cache_hits}) + "\n")
    print(f"SEC requests this command: {ed.live_requests} live, {ed.cache_hits} cached "
          f"(total {_used()}/{SEC_REQUEST_BUDGET})")


def folder_url(cik: int, accn: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}"


def folder_listing(ed: a_edgar.Edgar, cik: int, accn: str) -> list[dict[str, Any]]:
    return ed.json(f"{folder_url(cik, accn)}/index.json")["directory"]["item"]


def filing_meta(ed: a_edgar.Edgar, cik: int, accn: str, days: int) -> dict[str, Any]:
    for f in a_edgar.load_filings(ed, cik, date.today() - timedelta(days=days)):
        if f["accessionNumber"] == accn:
            return f
    raise SystemExit(f"accession {accn} not found in the submissions window ({days} days)")


def _pages(spec: str | None, n: int) -> range:
    if not spec:
        return range(n)
    lo, _, hi = spec.partition("-")
    return range(int(lo) - 1, min(n, int(hi or lo)))


def cmd_find(args: argparse.Namespace) -> None:
    ed = open_edgar()
    try:
        cik, title = a_edgar.resolve_cik(ed, args.ticker)
        print(f"{args.ticker}: {title} (CIK {cik})")
        filings = a_edgar.load_filings(ed, cik, date.today() - timedelta(days=args.days))
        eightks = sorted((f for f in filings if f["form"] == "8-K" and "2.02" in (f["items"] or "")),
                         key=lambda f: f["acceptanceDateTime"], reverse=True)
        print(f"{len(eightks)} Item 2.02 8-K(s) in the last {args.days} days")
        for f in eightks[: args.n]:
            print(f"\n{f['filingDate']}  {f['accessionNumber']}  accepted {f['acceptanceDateTime']}  "
                  f"items {f['items']}  primary {f['primaryDocument']}")
            items = folder_listing(ed, cik, f["accessionNumber"])
            media = [i for i in items if re.search(r"\.(jpe?g|png|gif|pdf)$", i["name"], re.I)]
            others = [i for i in items if i not in media]
            for i in others:
                print(f"   {i['name']:<56} {i.get('size', '')}")
            if media:
                sizes = [int(i["size"]) for i in media if str(i.get("size", "")).isdigit()]
                print(f"   + {len(media)} image/PDF file(s), {sum(sizes) / 1e6:.1f} MB: "
                      f"{media[0]['name']} ... {media[-1]['name']}")
    finally:
        close_edgar(ed, f"find {args.ticker}")


def cmd_search(args: argparse.Namespace) -> None:
    """EDGAR full-text search (one request): 8-K exhibits whose description mentions a presentation."""
    from urllib.parse import urlencode
    ed = open_edgar()
    try:
        url = "https://efts.sec.gov/LATEST/search-index?" + urlencode(
            {"q": args.q, "forms": "8-K", "dateRange": "custom", "startdt": args.start, "enddt": args.end})
        hits = ed.json(url).get("hits", {}).get("hits", [])
        seen = set()
        for h in hits:
            s = h.get("_source", {})
            desc, ftype = str(s.get("file_description") or ""), str(s.get("file_type") or "")
            if not ftype.upper().startswith("EX-99") or not re.search(args.desc, desc, re.I):
                continue
            accn, _, fname = h.get("_id", "").partition(":")
            if accn in seen:
                continue
            seen.add(accn)
            print(f"{s.get('file_date')}  {accn}  {ftype:<8} {desc[:40]:<40}  {fname:<34} {'; '.join(s.get('display_names', []))[:70]}")
        print(f"{len(hits)} hit(s) on this page; {len(seen)} presentation exhibit(s) listed")
    finally:
        close_edgar(ed, f"search {args.q}")


def cmd_fetch(args: argparse.Namespace) -> None:
    ed = open_edgar()
    try:
        cik, _ = a_edgar.resolve_cik(ed, args.ticker)
        names = sorted(i["name"] for i in folder_listing(ed, cik, args.accn) if re.search(args.pattern, i["name"], re.I))
        chosen = [names[k] for k in _pages(args.pages, len(names))]
        out = CANDIDATES / f"{args.ticker.lower()}_{args.accn}"
        out.mkdir(parents=True, exist_ok=True)
        for name in chosen:
            (out / name).write_bytes(ed.get(f"{folder_url(cik, args.accn)}/{name}"))
        print(f"{len(chosen)} of {len(names)} matching file(s) saved to {out}")
    finally:
        close_edgar(ed, f"fetch {args.ticker} {args.accn} {args.pages}")


def cmd_select(args: argparse.Namespace) -> None:
    ed = open_edgar()
    try:
        cik, title = a_edgar.resolve_cik(ed, args.ticker)
        meta = filing_meta(ed, cik, args.accn, args.days)
        SLIDES.mkdir(parents=True, exist_ok=True)
        for name in args.files:
            src = CANDIDATES / f"{args.ticker.lower()}_{args.accn}" / name
            if not src.is_file():
                raise SystemExit(f"not fetched yet: {src}")
            dst = SLIDES / f"{args.ticker.lower()}_{args.accn}_{name}"
            shutil.copyfile(src, dst)
            body = dst.read_bytes()
            record = {
                "ticker": args.ticker.upper(), "company": title, "cik": cik,
                "accession": args.accn, "form": meta["form"], "items": meta["items"],
                "filing_date": meta["filingDate"], "report_date": meta["reportDate"],
                "acceptance_datetime": meta["acceptanceDateTime"], "primary_document": meta["primaryDocument"],
                "url": f"{folder_url(cik, args.accn)}/{name}", "folder_index": f"{folder_url(cik, args.accn)}/index.json",
                "file": dst.name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                "fetched_via": "spikes/a_edgar.py Edgar (declared User-Agent, content-hash cache, 4 req/s)",
                "selected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": args.note or "",
            }
            dst.with_suffix(".source.json").write_text(json.dumps(record, indent=2) + "\n")
            print(f"{dst}  (+ {dst.with_suffix('.source.json').name})")
    finally:
        close_edgar(ed, f"select {args.ticker} {args.accn}")


# ---- XBRL comparison ---------------------------------------------------------

# (label pattern, concepts summed by quarter, or a ratio of two concepts for % charts)
CONCEPTS = [
    # Adjusted, non-GAAP, and fully taxable-equivalent (FTE) figures are never compared with GAAP XBRL facts.
    (r"\bFTE\b|taxable.equivalent|non-GAAP|\badjusted\b", ("none", "non-GAAP (adjusted or FTE), not in XBRL", None)),
    (r"net interest income", ("value", ["InterestIncomeExpenseNet"], None)),
    (r"noninterest expense", ("value", ["NoninterestExpense"], None)),
    (r"compensation and benefits", ("value", ["LaborAndRelatedExpense"], None)),
    (r"gross margin", ("ratio", ["GrossProfit"], ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"])),
    (r"operating margin", ("ratio", ["OperatingIncomeLoss"], ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"])),
    (r"revenue", ("value", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"], None)),
    (r"gross profit", ("value", ["GrossProfit"], None)),
    (r"operating income|income from operations", ("value", ["OperatingIncomeLoss"], None)),
    (r"net income", ("value", ["NetIncomeLoss"], None)),
    (r"operating cash flow|cash (provided by|from) operat", ("value", ["NetCashProvidedByUsedInOperatingActivities"], None)),
    (r"capital expenditure|capex", ("value", ["PaymentsToAcquirePropertyPlantAndEquipment"], None)),
]
QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}  # calendar fiscal year assumed (true for TSLA)
CUMULATIVE_DAYS = {1: (80, 100), 2: (170, 190), 3: (260, 285), 4: (350, 380)}


def _days(f: dict[str, Any]) -> int:
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


FINANCIAL_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}  # not DEF 14A pay-versus-performance tables


def _latest(facts: list[dict[str, Any]], end: str, window: tuple[int, int]) -> dict[str, Any] | None:
    """Most recently filed 10-Q/10-K fact for this period, so recast values win over originals."""
    hits = [f for f in facts if f.get("end") == end and f.get("start") and window[0] <= _days(f) <= window[1]
            and f.get("form") in FINANCIAL_FORMS]
    return max(hits, key=lambda f: f.get("filed", "")) if hits else None


def quarter_value(facts: dict[str, Any], concepts: list[str], year: int, q: int) -> dict[str, Any] | None:
    """3-month USD value for calendar quarter q of `year`: a reported 3-month fact, else YTD minus prior YTD."""
    end = f"{year}-{QUARTER_END[q]}"
    for concept in concepts:
        units = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {}).get("USD", [])
        if not units:
            continue
        f3 = _latest(units, end, (80, 100))
        if f3:
            return {"concept": concept, "usd": f3["val"], "how": "3-month fact", "accn": f3["accn"],
                    "form": f3["form"], "filed": f3["filed"]}
        if q > 1:
            cum = _latest(units, end, CUMULATIVE_DAYS[q])
            prev = _latest(units, f"{year}-{QUARTER_END[q - 1]}", CUMULATIVE_DAYS[q - 1])
            if cum and prev:
                return {"concept": concept, "usd": cum["val"] - prev["val"],
                        "how": f"{_days(cum)}-day YTD minus {_days(prev)}-day YTD", "accn": cum["accn"],
                        "form": cum["form"], "filed": cum["filed"],
                        "parts": [{k: f[k] for k in ("start", "end", "val", "accn", "form", "filed")} for f in (cum, prev)]}
    return None


def xbrl_value(facts: dict[str, Any], label: str, period: str) -> dict[str, Any] | None:
    m = re.fullmatch(r"Q([1-4]) (\d{4})", canonical_period(period))
    if not m:
        return None
    q, year = int(m.group(1)), int(m.group(2))
    for rx, (kind, num, den) in CONCEPTS:
        if re.search(rx, label, re.I):
            if kind == "none":
                return {"kind": "none", "reason": num}
            a = quarter_value(facts, num, year, q)
            if kind == "value" or a is None:
                return a and {**a, "kind": "USD"}
            b = quarter_value(facts, den, year, q)
            return b and {**a, "usd": None, "pct": 100 * a["usd"] / b["usd"], "kind": "%",
                          "how": f"{a['concept']} / {b['concept']} ({a['how']})"}
    return None


def _decimals(v: Any) -> int:
    s = str(v)
    return len(s.split(".")[1]) if "." in s and "e" not in s.lower() else 0


def cmd_compare(args: argparse.Namespace) -> None:
    doc = json.loads(Path(args.chart).read_text())
    chart = doc["chart"]
    ed = open_edgar()
    try:
        cik, title = a_edgar.resolve_cik(ed, args.ticker)
        facts = ed.json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    finally:
        close_edgar(ed, f"compare {args.ticker}")
    rows = []
    for item in chart["series"]:
        label = f"{item['label']} {chart.get('chart_title', '')}" if args.use_title else item["label"]
        x = xbrl_value(facts, label, item["period"])
        read, d = Decimal(str(item["value"])), _decimals(item["value"])
        row = {**item, "xbrl": x, "status": "no XBRL fact"}
        if x is not None and x["kind"] == "none":
            rows.append({**row, "status": x["reason"]})
            continue
        if x is not None:
            if x["kind"] == "%":
                truth = Decimal(str(x["pct"]))
            else:
                scale = unit_scale(item.get("unit", "")) or args.scale
                if not scale:
                    rows.append({**row, "status": f"unknown scale for unit {item.get('unit')!r}"})
                    continue
                truth = Decimal(x["usd"]) / Decimal(int(scale))
            step = Decimal(1).scaleb(-d)
            rounded = truth.quantize(step, rounding=ROUND_HALF_UP)
            diff = abs(read - truth)
            row.update(status="compared", truth_in_unit=float(truth), truth_rounded=float(rounded),
                       exact=read == rounded, within_rounding=diff <= step,
                       within_1pct=diff <= abs(truth) * Decimal("0.01"))
        rows.append(row)
    summary: dict[str, dict[str, int]] = {}
    for r in rows:
        s = summary.setdefault(r.get("source", "?"), {"read": 0, "compared": 0, "exact": 0, "within_rounding": 0, "within_1pct": 0})
        s["read"] += 1
        if r["status"] == "compared":
            s["compared"] += 1
            for k in ("exact", "within_rounding", "within_1pct"):
                s[k] += bool(r[k])
    print(f"\n{title}: {len(rows)} value(s) from {args.chart}")
    print(f"{'label':<26} {'period':<9} {'read':>9} {'unit':<5} {'source':<14} {'XBRL':>10} {'rounded':>9}  result  (how)")
    for r in rows:
        if r["status"] == "compared":
            verdict = "EXACT" if r["exact"] else ("round" if r["within_rounding"] else ("±1%" if r["within_1pct"] else "MISS"))
            print(f"{r['label'][:26]:<26} {r['period']:<9} {r['value']:>9} {r.get('unit', ''):<5} {r.get('source', ''):<14} "
                  f"{r['truth_in_unit']:>10.4f} {r['truth_rounded']:>9}  {verdict:<6}  ({r['xbrl']['how']}, {r['xbrl']['form']} {r['xbrl']['accn']})")
        else:
            print(f"{r['label'][:26]:<26} {r['period']:<9} {r['value']:>9} {r.get('unit', ''):<5} {r.get('source', ''):<14} {'-':>10} {'-':>9}  ?       ({r['status']})")
    for s, c in summary.items():
        print(f"{s}: {c['read']} read, {c['compared']} with an XBRL fact: exact {c['exact']}, "
              f"within rounding {c['within_rounding']}, within ±1% {c['within_1pct']}")
    out = ROOT / "spikes" / "out" / f"c_xbrl_{Path(args.chart).stem.removeprefix('c_chart_')}.json"
    out.write_text(json.dumps({"chart": args.chart, "ticker": args.ticker.upper(), "cik": cik, "rows": rows,
                               "summary": summary,
                               "rules": {"exact": "read == XBRL rounded half-up to the printed decimals",
                                         "within_rounding": "|read - XBRL| <= one unit in the last printed digit",
                                         "within_1pct": "|read - XBRL| <= 1% of XBRL"}}, indent=2, default=str))
    print(f"saved {out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("--ticker", required=True)
    f.add_argument("--n", type=int, default=1, help="how many recent Item 2.02 8-Ks to list (one request each)")
    f.add_argument("--days", type=int, default=200)
    q = sub.add_parser("search")
    q.add_argument("--q", default='"earnings presentation"')
    q.add_argument("--desc", default=r"presentation|slides", help="regex over the exhibit description")
    q.add_argument("--start", required=True)
    q.add_argument("--end", required=True)
    g = sub.add_parser("fetch")
    g.add_argument("--ticker", required=True)
    g.add_argument("--accn", required=True)
    g.add_argument("--pattern", default=r"\.(jpe?g|png)$", help="regex over file names in the filing folder")
    g.add_argument("--pages", help="1-based range over the sorted matches, e.g. 1-12")
    s = sub.add_parser("select")
    s.add_argument("--ticker", required=True)
    s.add_argument("--accn", required=True)
    s.add_argument("--files", nargs="+", required=True)
    s.add_argument("--days", type=int, default=200)
    s.add_argument("--note")
    c = sub.add_parser("compare")
    c.add_argument("--ticker", required=True)
    c.add_argument("--chart", required=True, help="spikes/out/c_chart_<name>.json from c_slides.py")
    c.add_argument("--scale", type=float, help="USD per chart unit when the unit is blank, e.g. 1e9")
    c.add_argument("--use-title", action="store_true", help="also match the chart title when mapping labels to XBRL")
    args = p.parse_args(argv)
    {"find": cmd_find, "search": cmd_search, "fetch": cmd_fetch, "select": cmd_select,
     "compare": cmd_compare}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
