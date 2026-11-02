#!/usr/bin/env python
"""Why each S&P 500 company without items in the frozen full spot500 set is outside it (read-only, offline).

.venv/bin/python eval/spot500_frame_gaps.py [--data-root data]

The full set takes every company whose stored filings pair an earnings release (an 8-K item 2.02 with an EX-99.1
exhibit, filed after the quarter ends and no later than the 10-Q) with a 10-Q that has XBRL facts, and that still
has an unused sentence passing the source screen. This script follows the loader's checks in
`spot500_sources.load_sources`, in its order, and names the first check each other company fails on its latest
Q1-Q3 quarters. A company with a pair but no items failed the sentence screen, or every passing sentence was
already used by v1-v3. It needs the gitignored SEC archive under `data/archive`, so it does not run in a public clone.
It never runs NoCap and makes no network or model calls.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import build_corpus as base
from eval import spot500_sources as sources

NOT_ARCHIVED = "the release exhibit for the quarter is not in the archive"
REASONS = {
    "screen": "a release/10-Q pair exists, but no unused sentence passes the screen",
    "ex991": "the archived release is not labelled EX-99.1 (it says EX-99 or EX-99.01, or has no label)",
    "tenq": "no 10-Q with XBRL facts for the quarter in the archived companyfacts",
    "archive": NOT_ARCHIVED,
    "release": "no 8-K earnings release filed after the quarter and by the 10-Q",
    "periods": "no Q1-Q3 quarters in the snapshot",
    "quarter": "the release does not name the quarter",
    "list": "not in the archived S&P list the builder uses",
}


def gaps(data_root: Path) -> dict[str, str]:
    universe = {r["ticker"]: r for r in json.loads(sources.UNIVERSE.read_text())["universe"]}
    exhibits = json.loads((data_root / "archive/exhibits/index.json").read_text())
    cf_index = json.loads((data_root / "archive/companyfacts/index.json").read_text())
    by_accession = defaultdict(list)
    for url, row in sorted(exhibits.items()):
        by_accession[row["accession"]].append((url, row))
    con = sqlite3.connect(f"file:{data_root / 'snapshot/facts.sqlite'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    companies = {r["ticker"] for r in con.execute("SELECT ticker FROM companies")}
    filings, periods = defaultdict(list), defaultdict(list)
    for r in con.execute("SELECT * FROM filings ORDER BY accepted_at, accession"):
        filings[r["cik"]].append(dict(r))
    for r in con.execute("SELECT * FROM fiscal_periods WHERE fiscal_period IN ('Q1','Q2','Q3') ORDER BY period_end DESC"):
        periods[r["cik"]].append(dict(r))
    con.close()
    manifest = json.loads((ROOT / "eval/spot500_full_manifest.json").read_text())
    selected = set(manifest["selection"]["selected_companies"])
    out = {}
    for ticker in sorted(companies | set(universe)):
        if ticker in selected:
            continue
        if ticker not in universe:
            out[ticker] = "list"
            continue
        cik = int(universe[ticker]["cik"])
        path = data_root / f"archive/companyfacts/CIK{cik:010d}.json.gz"
        if str(cik) not in cf_index or not path.exists():
            out[ticker] = "tenq"
            continue
        facts = json.loads(gzip.decompress(path.read_bytes()))
        accessions = {r["accn"] for ns in facts.get("facts", {}).values() for concept in ns.values()
                      for observations in concept.get("units", {}).values() for r in observations
                      if r.get("form") in ("10-Q", "10-Q/A")}
        failures, pairs, named, latest = Counter(), 0, 0, None
        for period in periods[cik]:
            tenqs = [f for f in filings[cik] if f["form"] == "10-Q" and f["report_date"] == period["period_end"]
                     and f["accession"] in accessions]
            if not tenqs:
                failures["tenq"] += 1
                latest = latest or ("tenq" if "tenq" != "archive" else None)
                continue
            q = min(tenqs, key=lambda f: (f["accepted_at"], f["accession"]))
            releases = [f for f in filings[cik] if f["form"] == "8-K" and "2.02" in (f["items"] or "")
                        and period["period_end"] < f["filed"] <= q["filed"]]
            if not releases:
                failures["release"] += 1
                latest = latest or ("release" if "release" != "archive" else None)
                continue
            archived = [f for f in releases if f["accession"] in by_accession]
            if not archived:
                failures["archive"] += 1
                latest = latest or ("archive" if "archive" != "archive" else None)
                continue
            release = min(archived, key=lambda f: (f["accepted_at"], f["accession"]))
            bodies = [(url, gzip.decompress((data_root / "archive/exhibits" / (row["key"] + ".gz")).read_bytes()))
                      for url, row in by_accession[release["accession"]]]
            options = sorted((url, body) for url, body in bodies if sources.is_exhibit_991(url, body))
            if not options:
                failures["ex991"] += 1
                latest = latest or ("ex991" if "ex991" != "archive" else None)
                continue
            pairs += 1
            named += sources.release_names_quarter("\n".join(base.html_blocks(options[0][1])), period)
            if pairs == 2:
                break
        if pairs:
            out[ticker] = "screen" if named else "quarter"
        elif not failures:
            out[ticker] = "periods"
        else:
            # Older quarters fall outside the archive (it holds each company's latest four releases), so name
            # the failure on the latest quarter that is archived, when there is one.
            out[ticker] = latest or "archive"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    args = parser.parse_args(argv)
    result = gaps(args.data_root)
    by_reason = defaultdict(list)
    for ticker, key in result.items():
        by_reason[key].append(ticker)
    print(f"{len(result)} companies without items")
    print("| Reason | Companies | Tickers |\n| --- | ---: | --- |")
    for key, tickers in sorted(by_reason.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"| {REASONS[key]} | {len(tickers)} | {', '.join(sorted(tickers))} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
