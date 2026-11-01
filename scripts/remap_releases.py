"""Re-map named release files with receipts.press_release's own functions: no SEC request, model calls under the cap.

    .venv/bin/python scripts/remap_releases.py XOM/0000034088-25-000059 DOW/0001751788-26-000144 --dry-run
    .venv/bin/python scripts/remap_releases.py --from-list files.txt --validate --budget-usd 50.90 --out remap.json

Each FILE names a release file under data/pr_facts, live or quarantined, as TICKER/ACCESSION. The release is mapped
again as receipts.press_release maps it, paired with the 10-Q or 10-K its report names: its 8-K from the company's
cached Submissions (predecessor registrants merged as receipts.ingest.company_inputs merges them, each 8-K's index
read in its filer's folder), its exhibits from the EDGAR cache or data/archive/exhibits, its fiscal calendar from
the archived companyfacts and the stored calendar (FiscalCalendar), and the paired-filing XBRL gate from the
archived companyfacts (apply_xbrl_gate). write_job then writes the file as live or quarantined and its report
afresh. Model calls go through SparkMapper, whose ledger the --budget-usd cap counts; a table the mapper reads as
before hits the response cache, and a call the cap refuses leaves its release for a re-run (the 2026-11-01 re-map
estimated $0.77 and spent $0.96: allow more than 1.15 times the estimate). Run the label gate, the release gates and
the XBRL gate re-run afterwards, as after any mapping.
"""
from __future__ import annotations

import argparse
import gzip
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
from receipts.ingest import PREDECESSOR_CIKS, merge_filing_rows, with_predecessors  # noqa: E402
from scripts.map_predecessor_releases import FilerFolders  # noqa: E402

PR = REPO / "data/pr_facts"
EXHIBITS = REPO / "data/archive/exhibits"
COMPANYFACTS = REPO / "data/archive/companyfacts"


class Archived:
    """An Edgar client that reads a body the cache lacks from data/archive/exhibits; never a live request."""

    def __init__(self, edgar: Any, archive: Path = EXHIBITS):
        index = archive / "index.json"
        self._edgar, self._archive = edgar, archive
        self._index = json.loads(index.read_text()) if index.exists() else {}

    def get(self, url: str, *, max_age: Optional[float] = None) -> bytes:
        if self._edgar.is_cached(url) or url not in self._index:
            return self._edgar.get(url, max_age=max_age)
        return gzip.decompress((self._archive / f"{self._index[url]['key']}.gz").read_bytes())

    def filing_index(self, cik: int, accession: str) -> list[dict]:
        return self._edgar.filing_index(cik, accession)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._edgar, name)


def archived_companyfacts(cik: int, archive: Path = COMPANYFACTS) -> dict:
    return json.loads(gzip.decompress((archive / f"CIK{int(cik):010d}.json.gz").read_bytes()))


def plan(edgar: Any, files: list[str], *, effort: str, pr_dir: Path = PR) -> tuple[list[pr.FilingJob], dict, list]:
    """FilingJobs for the named files, the merged companyfacts by CIK, and the files that could not be planned."""
    by_ticker: dict[str, list[str]] = {}
    for name in files:
        ticker, _, accession = name.strip().partition("/")
        by_ticker.setdefault(ticker.upper(), []).append(accession.removesuffix(".jsonl").removesuffix(".quarantined"))
    jobs, companyfacts, missing = [], {}, []
    for ticker, accessions in sorted(by_ticker.items()):
        reports = {a: json.loads((pr_dir / ticker / f"{a}.report.json").read_text()) for a in accessions
                   if (pr_dir / ticker / f"{a}.report.json").exists()}
        missing += [f"{ticker}/{a}: no report" for a in accessions if a not in reports]
        if not reports:
            continue
        cik, company = edgar.ticker_to_cik(ticker)
        since = min(date.fromisoformat(r["accepted_at"][:10]) for r in reports.values()) - timedelta(days=1)
        _, rows = edgar.submissions(cik, since=since)
        for earlier in PREDECESSOR_CIKS.get(int(cik), ()):  # as receipts.ingest.company_inputs merges them
            rows = merge_filing_rows(rows, earlier, edgar.submissions(earlier, since=since)[1])
        by_accession = {r["accessionNumber"]: r for r in rows}
        filer = {r["accessionNumber"]: int(r.get("filer_cik") or cik) for r in rows}
        companyfacts[cik] = with_predecessors(cik, archived_companyfacts)
        calendar = pr.FiscalCalendar(companyfacts[cik], stored=pr.stored_fiscal_periods(cik))
        for accession, report in sorted(reports.items()):
            eightk, tenq = by_accession.get(accession), by_accession.get(report.get("tenq") or "")
            if eightk is None:
                missing.append(f"{ticker}/{accession}: not in the cached Submissions")
                continue
            jobs.append(pr.prepare_job(FilerFolders(edgar, filer), ticker, cik, company, eightk, tenq, calendar,
                                       effort=effort, secondary=True))
    return jobs, companyfacts, missing


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", help="TICKER/ACCESSION of each release file")
    ap.add_argument("--from-list", type=Path, help="a file with one TICKER/ACCESSION per line")
    ap.add_argument("--effort", choices=("minimal", "low"), default="minimal")
    ap.add_argument("--budget-usd", type=float, default=1.0, help="cap on the press-release ledger's total")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--validate", action="store_true", help="compare GAAP facts with 10-Q/10-K XBRL facts")
    ap.add_argument("--dry-run", action="store_true", help="plan and estimate; no model calls, nothing written")
    ap.add_argument("--out", type=Path, help="write the run's report here (JSON)")
    args = ap.parse_args(argv)
    files = list(args.files) + ([x for x in args.from_list.read_text().split() if x] if args.from_list else [])
    edgar = Archived(Edgar(offline=True))
    try:
        jobs, companyfacts, missing = plan(edgar, files, effort=args.effort)
    except EdgarError as e:
        print(f"EDGAR (offline): {e}")
        return 1
    reqs = [r for j in jobs for p in j.parts() for r, _ in p.requests]
    mapper = pr.SparkMapper(effort=args.effort, budget_usd=args.budget_usd, offline=args.dry_run)
    todo = [r for r in reqs if not mapper.cached(r)]
    est = sum(pr.SparkMapper.estimate_usd(r) for r in todo)
    print(f"{len(jobs)} releases planned ({len(missing)} not: {missing[:5]}); {len(reqs)} model calls, "
          f"{len(reqs) - len(todo)} cached, ~${est:.3f} for the rest; ledger so far ${mapper.prior_spend:.4f}")
    for job in jobs:
        if job.error:
            print(f"  {job.ticker} {job.eightk['accessionNumber']}: ERROR {job.error}")
    result: dict[str, Any] = {"planned": len(jobs), "missing": missing, "calls": len(reqs),
                              "cached": len(reqs) - len(todo), "estimate_usd": round(est, 4),
                              "ledger_before": round(mapper.prior_spend, 4)}
    if args.dry_run:
        if args.out:
            args.out.write_text(json.dumps(result, indent=1))
        return 0
    t0 = time.monotonic()
    pr.run_model(jobs, mapper, concurrency=args.concurrency)
    for job in jobs:
        pr.decide_job(job)
    gate = pr.apply_xbrl_gate(jobs, companyfacts)
    result["files"] = []
    for job in jobs:
        path = pr.write_job(job)
        outcomes = [o for p in job.parts() for o in p.outcomes]
        g = job.gate
        result["files"].append({
            "file": f"{job.ticker}/{job.eightk['accessionNumber']}", "written": path.name if path else None,
            "facts": len(job.facts), "held": len(job.held), "quarantine": g.quarantine if g else None,
            "gate_dropped": len(g.dropped) if g else 0,
            "calls": dict(Counter(o["status"] + ("(cached)" if o["cached"] else "") for o in outcomes)),
            "error": job.error})
        print(f"  {job.ticker:<6} {job.eightk['accessionNumber']}  facts {len(job.facts):>3}  held {len(job.held):>3}"
              + (f"  QUARANTINED ({g.quarantine})" if g and g.quarantine else "")
              + (f"  gate dropped {len(g.dropped)}" if g and g.dropped else "")
              + f"  calls {result['files'][-1]['calls']}" + (f"  ERROR {job.error}" if job.error else ""))
    result.update(spend_usd=round(mapper.spent_run, 4), ledger_after=round(mapper.total_spent, 4),
                  live_calls=mapper.live_calls, cached_calls=mapper.cached_calls, sec_live=edgar.live_requests,
                  xbrl_gate={k: gate[k] for k in ("files", "facts_before", "facts_after", "served_before",
                                                  "served_after")} | {"quarantined": len(gate["quarantined"]),
                                                                      "dropped": len(gate["dropped"])})
    print(f"Spark: live calls {mapper.live_calls}, cached {mapper.cached_calls}, spend this run "
          f"${mapper.spent_run:.4f}, ledger total ${mapper.total_spent:.4f} (cap ${args.budget_usd:.2f}); "
          f"wall {time.monotonic() - t0:.1f}s; SEC live requests {edgar.live_requests}")
    pr.print_gate(gate)
    if args.validate:
        report = pr.validate(jobs, companyfacts)
        result["validation"] = report.get("overall")
        pr.print_validation(report)
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
