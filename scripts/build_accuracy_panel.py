#!/usr/bin/env python
"""Build web/app/benchmarks/accuracy.json, the numbers of the comprehensive-accuracy panel on /benchmarks.

Every number is computed by eval/spot500_report.py (load_run, then summarize) from a committed run record:

  * current: full2, the one live run of the round-8 verifier, over all items and over the items without
    company_in_development (the report's --slice-without company_in_development);
  * previous: full, the one live run of the version before it, over all items.

The commit each run measured comes from its frozen manifest; the run date, model, calls and cost from the record.
load_run refuses a record that was not made on its frozen corpus or whose summary its scorer does not reproduce.
Offline, deterministic and free: no model, network or clock, so the same records give the same bytes.

    .venv/bin/python scripts/build_accuracy_panel.py           # write web/app/benchmarks/accuracy.json
    .venv/bin/python scripts/build_accuracy_panel.py --check   # write nothing; exit 1 if the committed file differs
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from eval import spot500  # noqa: E402
from eval import spot500_report as report  # noqa: E402

OUTPUT = REPO / "web/app/benchmarks/accuracy.json"
GITHUB = "https://github.com/changshoufeng0609-cmd/nocap/blob/main/"
METHODS = "eval/spot500_methods.md"
REPORT = "eval/spot500_report.py"
DEVELOPMENT = "company_in_development"
# The rates the panel publishes, keyed as in spot500_report.RATES.
RATES = ("caught", "target_verified", "true_wrong", "true_verified", "verified_precision", "wrong_precision")
RUNS = {
    "current": ("eval/spot500_runs/full2-live-20261102.json",
                "results-of-the-one-live-run-november-2-2026-after-round-8", (DEVELOPMENT,)),
    "previous": ("eval/spot500_runs/full-live-20261102.json",
                 "results-of-the-one-live-run-november-2-2026-all-companies", ()),
}


def rate(row: dict) -> dict:
    low, high = row["ci95"]
    return {"label": row["label"], "k": row["k"], "n": row["n"], "rate": row["rate"], "ci95": [low, high],
            "pct": report.pct(row["rate"]), "ci95_pct": [report.pct(low), report.pct(high)]}


def measured(items: list[dict], records: dict[str, dict]) -> dict:
    summary = report.summarize(items, records)
    return {"items": summary["items"], "companies": summary["companies"],
            "rates": {key: rate(summary["rates"][key]) for key in RATES}}


def run_entry(record: str, anchor: str, without: tuple[str, ...]) -> dict:
    run, items = report.load_run(REPO / record)
    if run.get("status") != "completed" or run.get("inputs_unchanged") is not True:
        raise ValueError(f"{record} is not a completed run on unchanged inputs.")
    manifest = json.loads(spot500.set_files(run["set"])[1].read_text(encoding="utf-8"))
    entry = {"set": run["set"], "record": record, "run_date": run["started_at"][:10],
             "model": run["mode"].rsplit(":", 1)[-1], "model_calls": run["metering"]["calls"],
             "cost_usd": run["cost_usd"], "cost_is_estimate": run["cost_is_estimate"],
             "commit": manifest["commit"][:7], "methods": f"{METHODS}#{anchor}",
             **measured(items, run["records"])}
    for feature in without:
        subset = [item for item in items if feature not in item["features"]]
        if not subset:
            raise ValueError(f"Every item of {record} carries the feature {feature!r}.")
        entry[f"without_{feature}"] = {"slice": f"without {feature}", **measured(subset, run["records"])}
    return entry


def build() -> dict:
    return {
        "generated_by": "scripts/build_accuracy_panel.py",
        "report": REPORT,
        "github": GITHUB,
        "interval": (f"the wider of a Wilson score interval and a company-level cluster bootstrap "
                     f"({report.RESAMPLES:,} resamples, seed {report.SEED})"),
        **{name: run_entry(*spec) for name, spec in RUNS.items()},
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=OUTPUT, help="the file to write or check (default: %(default)s)")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the file differs")
    args = parser.parse_args(argv)
    text = render(build())
    out = args.output.resolve()
    shown = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    if args.check:
        old = out.read_text(encoding="utf-8") if out.is_file() else ""
        if old == text:
            print(f"{shown} is current")
            return 0
        diff = list(difflib.unified_diff(old.splitlines(), text.splitlines(), str(shown), "generated", lineterm="", n=1))
        print("\n".join(diff[:40]) + ("\n..." if len(diff) > 40 else ""))
        print(f"{shown} differs: run scripts/build_accuracy_panel.py")
        return 1
    out.write_text(text, encoding="utf-8")
    print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
