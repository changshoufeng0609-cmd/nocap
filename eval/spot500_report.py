#!/usr/bin/env python
"""Summarize one finished spot500 run: headline rates, verdict precision and slices.

.venv/bin/python eval/spot500_report.py eval/spot500_runs/v3-live-20261102.json
.venv/bin/python eval/spot500_report.py eval/spot500_runs/full-live-<date>.json \
    --slice company_in_development --slice company_fresh
.venv/bin/python eval/spot500_report.py eval/spot500_runs/full2-live-<date>.json \
    --slice company_new --slice-without company_in_development

Offline and deterministic. It reads only the run record and that set's frozen corpus,
rescoring with the runner's own scorer (and refusing if that differs from the record),
and makes no model or network calls. Each 95% interval is the wider of a Wilson score
interval and a company-level cluster bootstrap (fixed seed). The bootstrap resamples a
company's items together, a planted-error copy with its original sentence, so it reflects
that they are correlated; Wilson keeps a count of 0 or n from collapsing to a point.
Each rate is a measurement of this frozen sample, not of all possible drafts.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import spot500

SEED = 20261102
RESAMPLES = 2000
LOOK_AHEAD = "look_ahead"

# (key, label, numerator count, denominator count); counts come from claim_counts().
RATES = (
    ("caught", "Planted errors caught", "caught", "targets"),
    ("target_verified", "Planted errors marked ✓", "target_verified", "targets"),
    ("true_wrong", "True figures marked ✗ or ◷", "true_flagged", "true"),
    ("true_verified", "True figures verified", "true_verified", "true"),
    ("true_unverifiable", "True figures left ? (no ✓ or ✗)", "true_unverifiable", "true"),
    ("verified_precision", "✓ on a true figure", "true_verified", "verified_marks"),
    ("wrong_precision", "✗ on a planted error", "target_wrong_marks", "wrong_marks"),
    ("guidance_verified", "Guidance figures marked ✓", "guidance_verified", "guidance"),
)


def claim_counts(items: list[dict], records: dict[str, dict]) -> dict[str, Counter]:
    """Per-company counts. A gold figure counts once per verdict kind it received."""
    by_company: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        pairs = records.get(item["item_id"], {}).get("pairs", [])
        counts = by_company[item["ticker"]]
        used: set[int] = set()
        for gold in item["gold_claims"]:
            role = gold["role"]
            hits = spot500._matching(pairs, gold, used)
            if hits:
                used.add(hits[0])
            occurrence = spot500._matching(pairs, gold, set(), require_basis=False)
            verdicts = [pairs[i][1] for i in occurrence]
            statuses = {v.get("status") for v in verdicts}
            status_ok = bool(hits) and all(v.get("status") == gold["expected_status"] for v in verdicts)
            cross = any(v.get("status") == "wrong" and v.get("reason") != LOOK_AHEAD for v in verdicts)
            clock = any(v.get("status") == "wrong" and v.get("reason") == LOOK_AHEAD for v in verdicts)
            verified = "verified" in statuses
            counts["verified_marks"] += int(verified)
            counts["wrong_marks"] += int(cross)
            counts["clock_marks"] += int(clock)
            if role == "target":
                counts["targets"] += 1
                counts["caught"] += int(status_ok)
                counts["target_verified"] += int(verified)
                counts["target_wrong_marks"] += int(cross)
                counts["target_clock_marks"] += int(clock)
            elif role == "true":
                counts["true"] += 1
                counts["true_verified"] += int(verified)
                counts["true_flagged"] += int("wrong" in statuses)
                counts["true_unverifiable"] += int("unverifiable" in statuses and not statuses & {"verified", "wrong"})
                counts["true_missing"] += int(not hits)
            elif role == "guidance":
                counts["guidance"] += 1
                counts["guidance_verified"] += int(verified)
        counts["items"] += 1
        counts["extra_claims"] += len(pairs) - len(used)
        counts["extra_verified"] += sum(v.get("status") == "verified" for i, (_, v) in enumerate(pairs)
                                        if i not in used)
        counts["extra_wrong"] += sum(v.get("status") == "wrong" for i, (_, v) in enumerate(pairs) if i not in used)
    return dict(by_company)


def interval(by_company: dict[str, Counter], num: str, den: str, *, seed: int = SEED,
             resamples: int = RESAMPLES) -> tuple[float, float] | None:
    """95% percentile interval of sum(num)/sum(den) over companies resampled with replacement."""
    rows = [(c[num], c[den]) for _, c in sorted(by_company.items()) if c[den]]
    if not rows:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        picked = rng.choices(rows, k=len(rows))
        draws.append(sum(k for k, _ in picked) / sum(n for _, n in picked))
    draws.sort()
    return draws[int(0.025 * (resamples - 1))], draws[int(round(0.975 * (resamples - 1)))]


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    centre, spread = k / n + z * z / (2 * n), z * math.sqrt(k * (n - k) / n + z * z / 4) / n
    scale = 1 + z * z / n
    low = 0.0 if k == 0 else max(0.0, (centre - spread) / scale)
    return low, 1.0 if k == n else min(1.0, (centre + spread) / scale)


def summarize(items: list[dict], records: dict[str, dict], *, bootstrap: bool = True) -> dict:
    by_company = claim_counts(items, records)
    total = Counter()
    for counts in by_company.values():
        total.update(counts)
    rates = {}
    for key, label, num, den in RATES:
        if not total[den]:
            continue
        row = {"label": label, "k": total[num], "n": total[den], "rate": total[num] / total[den]}
        if bootstrap:
            low, high = wilson(total[num], total[den])
            boot = interval(by_company, num, den)
            row["ci95"] = (min(low, boot[0]), max(high, boot[1]))
        rates[key] = row
    by_mutator = {}
    for item in items:
        if item["mutator"] in ("none", "original", "forward_looking_control"):
            continue
        sub = claim_counts([item], records)
        row = by_mutator.setdefault(item["mutator"], Counter())
        for counts in sub.values():
            row.update({k: counts[k] for k in ("targets", "caught", "target_verified")})
    return {"companies": len(by_company), "items": total["items"], "counts": dict(total), "rates": rates,
            "by_mutator": {k: dict(v) for k, v in sorted(by_mutator.items())}}


def load_run(path: Path) -> tuple[dict, list[dict]]:
    run = json.loads(path.read_text())
    spot_set = run.get("set") or "v1"
    manifest, items = spot500.load_items(spot_set)
    if run.get("corpus_sha256") != manifest["corpus_sha256"]:
        raise ValueError("The run was not made on this frozen corpus.")
    rescored = spot500.score(items, run["records"])
    if rescored["total"] != run["summary"]["total"]:
        raise ValueError("Rescoring the record does not reproduce its own summary.")
    return run, items


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def render(name: str, summary: dict) -> list[str]:
    lines = [f"### {name}: {summary['items']} items, {summary['companies']} companies", "",
             "| Measure | Count | Rate | 95% interval |", "|---|---:|---:|---|"]
    for row in summary["rates"].values():
        ci = row.get("ci95")
        band = f"{pct(ci[0])}–{pct(ci[1])}" if ci else ""
        lines.append(f"| {row['label']} | {row['k']}/{row['n']} | {pct(row['rate'])} | {band} |")
    c = summary["counts"]
    lines += ["", f"◷ marks: {c.get('clock_marks', 0)} ({c.get('target_clock_marks', 0)} on planted errors). "
              f"Extra figures the model also extracted (unlabelled): {c.get('extra_claims', 0)}, "
              f"of which {c.get('extra_verified', 0)} ✓ and {c.get('extra_wrong', 0)} ✗ or ◷.", ""]
    if summary["by_mutator"]:
        lines += ["| Planted error type | Caught | Marked ✓ |", "|---|---:|---:|"]
        for mutator, row in summary["by_mutator"].items():
            lines.append(f"| {mutator} | {row['caught']}/{row['targets']} | {row['target_verified']} |")
        lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--slice", action="append", default=[], metavar="FEATURE",
                        help="also report the items tagged with this feature")
    parser.add_argument("--slice-without", action="append", default=[], metavar="FEATURE",
                        help="also report the items NOT tagged with this feature")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args(argv)
    run, items = load_run(args.run)
    report = {"run": args.run.name, "set": run.get("set") or "v1", "cost_usd": run.get("cost_usd"),
              "all": summarize(items, run["records"])}
    for feature in args.slice:
        subset = [item for item in items if feature in item["features"]]
        if not subset:
            raise SystemExit(f"No items carry the feature {feature!r}.")
        report[feature] = summarize(subset, run["records"])
    for feature in args.slice_without:
        subset = [item for item in items if feature not in item["features"]]
        if not subset:
            raise SystemExit(f"Every item carries the feature {feature!r}.")
        report[f"without {feature}"] = summarize(subset, run["records"])
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    lines = [f"## {args.run.name} (set {report['set']}, cost ${report['cost_usd']:.3f})", "",
             f"Intervals: the wider of Wilson and a company-level cluster bootstrap ({RESAMPLES} resamples, seed {SEED}).", ""]
    lines += render("All items", report["all"])
    for feature in args.slice:
        lines += render(feature, report[feature])
    for feature in args.slice_without:
        lines += render(f"without {feature}", report[f"without {feature}"])
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
