"""Map the latest paired earnings releases of a company whose filings span registrants, with receipts.press_release.

receipts.press_release reads one CIK's Submissions and companyfacts. A company whose registrant changed
(receipts.ingest.PREDECESSOR_CIKS: XOM's reports were filed by Exxon Mobil Corp, CIK 34088, until ExxonMobil Holdings
Corp, CIK 2115436, became the registrant on 2026-07-01) has its earlier releases and reports under another CIK. This
script reads every registrant's (receipts.ingest.company_inputs), pairs releases with reports across them
(earnings_pairs), reads each 8-K's exhibits in the EDGAR folder of a registrant that lists it, labels the facts from
the stored calendar and the merged companyfacts (FiscalCalendar), and gates them against the merged companyfacts
(apply_xbrl_gate), so every fact carries the company's one CIK and ticker. Mapping, deciding, gating, writing and
validating are press_release's own functions, with its spend ledger and budget cap.

    .venv/bin/python scripts/map_predecessor_releases.py XOM --quarters 4 --dry-run      # plan and estimate
    SEC_USER_AGENT=... .venv/bin/python scripts/map_predecessor_releases.py XOM --quarters 4 --validate \\
        --budget-usd 57.64 --validation-out OUT/validation.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts import press_release as pr  # noqa: E402
from receipts.edgar import Edgar, EdgarError  # noqa: E402
from receipts.ingest import PREDECESSOR_CIKS, company_inputs  # noqa: E402


class FilerFolders:
    """An Edgar client whose filing_index reads each filing in the folder of a registrant that lists it."""

    def __init__(self, edgar: Any, filer: dict[str, int]):
        self._edgar, self._filer = edgar, filer

    def filing_index(self, cik: int, accession: str) -> list[dict]:
        return self._edgar.filing_index(self._filer.get(accession, cik), accession)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._edgar, name)


def plan(edgar: Any, ticker: str, quarters: int, *, effort: str, secondary: bool,
         since: date) -> tuple[list[pr.FilingJob], dict]:
    """The company's FilingJobs for its latest `quarters` paired releases, and the merged companyfacts."""
    cik, company = edgar.ticker_to_cik(ticker)
    if not PREDECESSOR_CIKS.get(cik):
        raise SystemExit(f"{ticker} (CIK {cik}) has no predecessor registrant: map it with receipts.press_release")
    _, rows, companyfacts, _ = company_inputs(edgar, cik, since=since)
    filer = {r["accessionNumber"]: int(r.get("filer_cik") or cik) for r in rows}
    calendar = pr.FiscalCalendar(companyfacts, stored=pr.stored_fiscal_periods(cik))
    routed = FilerFolders(edgar, filer)
    jobs = [pr.prepare_job(routed, ticker, cik, company, eightk, tenq, calendar, effort=effort, secondary=secondary)
            for eightk, tenq in pr.earnings_pairs(rows, quarters)]
    return jobs, {cik: companyfacts}


def parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ticker", help="a company in receipts.ingest.PREDECESSOR_CIKS, by its current ticker")
    p.add_argument("--quarters", type=int, default=4, help="most recent paired fiscal quarters (default 4)")
    p.add_argument("--validate", action="store_true", help="compare GAAP facts with 10-Q/10-K XBRL facts")
    p.add_argument("--effort", choices=("minimal", "low"), default="minimal", help="reasoning.effort")
    p.add_argument("--budget-usd", type=float, default=1.0,
                   help="cap on total Meta API spend recorded in the press-release ledger (default 1.00)")
    p.add_argument("--max-sec-requests", type=int, default=30, help="cap on live SEC requests (default 30)")
    p.add_argument("--concurrency", type=int, default=6, help="model calls in flight (default 6)")
    p.add_argument("--offline", action="store_true", help="caches only: no SEC or Model API network calls")
    p.add_argument("--no-secondary", action="store_true", help="map only the primary exhibit")
    p.add_argument("--dry-run", action="store_true", help="parse and pick tables, print the plan; no model calls")
    p.add_argument("--validation-out", type=Path, help="where --validate writes its report")
    args = p.parse_args(argv)
    if not 1 <= args.quarters <= 8:
        p.error("--quarters must be between 1 and 8")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    ticker = args.ticker.strip().upper()
    ed = Edgar(offline=args.offline, max_live_requests=args.max_sec_requests)
    since = date.today() - timedelta(days=int(args.quarters * 92) + 200)  # as receipts.press_release reads
    try:
        jobs, companyfacts = plan(ed, ticker, args.quarters, effort=args.effort, secondary=not args.no_secondary,
                                  since=since)
    except EdgarError as e:
        print(f"{ticker}: EDGAR error: {e}")
        return 1
    print(f"{'ticker':<7}{'8-K accession':<22}{'exhibit':<9}{'tables':>7}{'cands':>6}{'calls':>6}  url / paired report")
    for job in jobs:
        for part in job.parts():
            print(f"{job.ticker:<7}{job.eightk['accessionNumber']:<22}{(part.ctx.exhibit if part.ctx else '-'):<9}"
                  f"{len(part.tables):>7}{len(part.candidates):>6}{len(part.requests):>6}  "
                  + (part.ctx.url if part.ctx else "")
                  + (f"  <- {job.tenq['form']} {job.tenq['accessionNumber']} ({job.tenq.get('reportDate')})"
                     if job.tenq and part is job else "")
                  + (f"  ERROR {job.error}" if job.error and part is job else ""))
    print(f"SEC: live requests {ed.live_requests}, cache hits {ed.cache_hits}")
    if args.dry_run:
        mapper = pr.SparkMapper(effort=args.effort, budget_usd=args.budget_usd, offline=True)
        reqs = [r for j in jobs for p in j.parts() for r, _ in p.requests]
        todo = [r for r in reqs if not mapper.cached(r)]
        est = sum(pr.SparkMapper.estimate_usd(r) for r in todo)
        print(f"dry run: {len(reqs)} model calls planned, {len(reqs) - len(todo)} cached, ~${est:.3f} for the rest; "
              f"ledger so far ${mapper.prior_spend:.4f}")
        return 0

    mapper = pr.SparkMapper(effort=args.effort, budget_usd=args.budget_usd, offline=args.offline)
    t0 = time.monotonic()
    pr.run_model(jobs, mapper, concurrency=args.concurrency)
    for job in jobs:
        pr.decide_job(job)
    before = pr.validate(jobs, companyfacts) if args.validate else None
    gate = pr.apply_xbrl_gate(jobs, companyfacts)  # before anything is written
    for job in jobs:
        path = pr.write_job(job)
        outcomes = [o for p in job.parts() for o in p.outcomes]
        calls = Counter(o["status"] + ("(cached)" if o["cached"] else "") for o in outcomes)
        g = job.gate
        gated = ("" if g is None else f"  QUARANTINED ({g.quarantine})" if g.quarantine
                 else f"  gate dropped {len(g.dropped)}" if g.dropped else "")
        print(f"{job.ticker:<6} {job.eightk['accessionNumber']}  facts {len(job.facts):>3}  "
              f"(GAAP {sum(f.basis == 'GAAP' for f in job.facts)}, non-GAAP "
              f"{sum(f.basis == 'non-GAAP' for f in job.facts)}, segment {sum(bool(f.segment) for f in job.facts)})"
              f"  calls {dict(calls)}  rejected {dict(Counter(r['reason'] for r in job.rejections))}{gated}"
              + (f"  -> {path.relative_to(REPO)}" if path else "") + (f"  ERROR {job.error}" if job.error else ""))
        for o in outcomes:
            if o["status"] != "ok":
                print(f"    call for tables {o['tables']}: {o['status']} {o['problem'] or ''}")
    print(f"Spark: live calls {mapper.live_calls}, cached {mapper.cached_calls}, spend this run "
          f"${mapper.spent_run:.4f}, ledger total ${mapper.total_spent:.4f} (cap ${args.budget_usd:.2f}); "
          f"wall {time.monotonic() - t0:.1f}s")
    print(f"SEC: live requests {ed.live_requests}, cache hits {ed.cache_hits}")
    pr.print_gate(gate)
    if args.validate:
        report = pr.validate(jobs, companyfacts)  # the facts that were written: what the gate kept
        report["xbrl_gate"] = gate
        report["before_gate"] = {"overall": before["overall"], "mismatches": before["mismatches"]} if before else None
        pr.print_validation(report)
        if args.validation_out:
            args.validation_out.parent.mkdir(parents=True, exist_ok=True)
            args.validation_out.write_text(json.dumps(report, indent=1, default=str))
    ed.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
