"""Command line: python -m receipts check draft.txt [--as-of ISO] [--context "NVIDIA Q2 FY2027"] [--json]

Prints the draft with a marker after every checked number (✓ verified, ✗ wrong, ? unverifiable,
≈ approximate), then one line per claim with its explanation and its receipt: form, accession,
EDGAR acceptance time and filing URL. Exit status: 0 when the check ran, 2 when it could not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from receipts.extract import ExtractionError
from receipts.types import Claim, Verdict

MARK = {"verified": "✓", "wrong": "✗", "unverifiable": "?", "approximate": "≈"}
_SCALE_WORD = {"thousands": "thousand", "millions": "million", "billions": "billion"}
MAX_RECEIPTS = 3


def _fmt_expected(claim: Claim, x: float) -> str:
    """expected_value in the claim's own terms: 81.273 for a '$82.9 billion' claim reads '$81.273 billion'."""
    if claim.unit == "%":
        return f"{x:,.2f}".rstrip("0").rstrip(".") + "%"
    if claim.unit == "USD/share":
        return f"${x:,.2f}"
    num = f"{x:,.3f}".rstrip("0").rstrip(".")
    word = _SCALE_WORD.get(claim.scale)
    if claim.unit == "USD":
        return f"${num} {word}" if word else f"${num}"
    return f"{num} {word}" if word else num


def annotate(text: str, results: Sequence[tuple[Claim, Verdict]]) -> str:
    """The draft with '[✓c1]' markers right after each claim's number (claims sharing a number share a marker)."""
    at: dict[int, list[str]] = {}
    for claim, verdict in results:
        k = text.find(claim.value_text, claim.span_start, claim.span_end) if claim.value_text else -1
        pos = k + len(claim.value_text) if k >= 0 else claim.span_end
        at.setdefault(pos, []).append(f"{MARK.get(verdict.status, '?')}{claim.id}")
    out = text
    for pos in sorted(at, reverse=True):
        out = f"{out[:pos]} [{' '.join(at[pos])}]{out[pos:]}"
    return out


def _receipt_lines(verdict: Verdict) -> list[str]:
    from receipts.verify import fmt_ts, form_phrase

    lines = []
    for f in verdict.evidence[:MAX_RECEIPTS]:
        lines.append(f"receipt: {form_phrase(f)} {f.accession} · accepted {fmt_ts(f.accepted_at)} · {f.url}")
    if len(verdict.evidence) > MAX_RECEIPTS:
        lines.append(f"         (+{len(verdict.evidence) - MAX_RECEIPTS} more)")
    return lines


def render(path_label: str, text: str, res: Any, *, inferred: bool) -> str:
    from receipts.verify import fmt_ts

    w: list[str] = []
    ctx = res.context or "none"
    asof = fmt_ts(res.as_of) if res.as_of else "latest filings"
    w.append(f"NoCap · {path_label}")
    w.append(f"context: {ctx}{' (inferred)' if inferred and res.context else ''} · as of: {asof}")
    w.append("─" * 78)
    w.append(annotate(text, res.results).rstrip())
    w.append("─" * 78)
    for claim, v in res.results:
        period = claim.period_text or "no period"
        basis = f" · {claim.basis}" if claim.basis else ""
        w.append(f"{MARK.get(v.status, '?')} {claim.id:<4} {claim.metric}{basis} · {claim.value_text} · {claim.entity} "
                 f"· {period}  [{v.reason}]")
        w.append(f"       {v.explanation}")
        if v.expected_value is not None:
            w.append(f"       expected: {_fmt_expected(claim, v.expected_value)}")
        w.extend(f"       {line}" for line in _receipt_lines(v))
    counts = {s: sum(v.status == s for _, v in res.results) for s in MARK}
    w.append("─" * 78)
    w.append(f"{counts['verified']} ✓ verified · {counts['wrong']} ✗ wrong · {counts['unverifiable']} ? unverifiable"
             f" · {counts['approximate']} ≈ approximate")
    coverage = getattr(res, "coverage", None)
    if coverage is not None:
        w.append(f"number coverage: {coverage['covered_count']}/{coverage['candidate_count']} digit-based "
                 "candidates linked to extracted claims (not a correctness verdict)")
        for candidate in coverage["uncovered"]:
            w.append(f"Not checked: {candidate['text']!r} at offset {candidate['start']}")
    else:
        w.append("number coverage: unavailable")
    ex = res.extraction
    if ex is not None:
        w.append(f"extraction: {len(ex.calls)} passage call(s), {ex.cached_calls} cached, ${ex.cost_usd:.4f}, "
                 f"{ex.wall_s:.1f} s")
    for note in res.warnings:
        w.append(f"note: {note}")
    return "\n".join(w)


def to_json(path_label: str, res: Any) -> str:
    ex = res.extraction
    payload = {
        "draft": path_label,
        "context": res.context,
        "as_of": res.as_of.isoformat() if res.as_of else None,
        "results": [{"claim": c.model_dump(mode="json"), "verdict": v.model_dump(mode="json")} for c, v in res.results],
        "warnings": res.warnings,
        "coverage": getattr(res, "coverage", None),
        "extraction": None if ex is None else {"calls": len(ex.calls), "cached_calls": ex.cached_calls,
                                               "cost_usd": ex.cost_usd, "wall_s": ex.wall_s},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m receipts",
                                description="NoCap: verify every financial number in a draft against SEC filings.")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="extract the draft's numeric claims and verify each against SEC filings")
    c.add_argument("draft", help="text file with the draft, or - for stdin")
    c.add_argument("--as-of", help="ISO time such as 2026-08-26T20:30:00Z; evidence accepted later is look-ahead "
                                   "(a bare date means 00:00 UTC)")
    c.add_argument("--context", help='document context line, e.g. "NVIDIA Q2 FY2027" (default: inferred from the '
                                     'first passage; "" for none)')
    c.add_argument("--json", action="store_true", help="print JSON instead of the annotated draft")
    c.add_argument("--no-cache", action="store_true", help="ignore data/cache/spark and call the model again")
    return p


def main(argv: Optional[Sequence[str]] = None, *, store: Any = None) -> int:
    args = _parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    from receipts.check import check_detailed

    try:
        if args.draft == "-":
            text, label = sys.stdin.read(), "<stdin>"
        else:
            path = Path(args.draft)
            text, label = path.read_text(encoding="utf-8"), args.draft
    except OSError as e:
        print(f"error: cannot read the draft: {e}", file=sys.stderr)
        return 2
    try:
        res = check_detailed(text, as_of=args.as_of, context=args.context, store=store, use_cache=not args.no_cache)
    except ValueError as e:  # a malformed --as-of
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, ExtractionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(to_json(label, res) if args.json else render(label, text, res, inferred=args.context is None))
    return 0
