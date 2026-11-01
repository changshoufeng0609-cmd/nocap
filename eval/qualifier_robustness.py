"""Qualifier robustness: a qualified figure must never be confirmed by a figure reported on another basis.

    .venv/bin/python eval/qualifier_robustness.py            # prints the summary, writes eval/qualifier_robustness.json

A deterministic grid over the committed snapshot, with typed claims built the way extraction delivers them: no
model, network or SEC call. Each of eight real figures (its value equals the filed GAAP figure, so a careless
check would confirm it) is wrapped in each of sixteen qualifying phrases, at four positions, with the phrase in
metric_text or not, and with the model's basis left empty or set to GAAP, the hard case. The same eight figures
without a phrase are controls and must verify.

A qualified variant may verify only against a figure the company itself reports on that basis (for example a
non-GAAP revenue row in its 8-K earnings tables, cited as the receipt). It must never verify against GAAP evidence.
"""
from __future__ import annotations

import itertools
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from receipts.store import Store  # noqa: E402
from receipts.types import Claim  # noqa: E402
from receipts.verify import verify  # noqa: E402

OUT = ROOT / "eval" / "qualifier_robustness.json"
# (company as written, metric wording, metric, value_text, value, unit, scale, period wording): each value is the
# filed GAAP figure at the draft's precision.
FIGURES = [
    ("NVIDIA", "revenue", "revenue", "$96.2 billion", 96.2, "USD", "billions", "the second quarter of fiscal 2027"),
    ("NVIDIA", "diluted earnings per share", "eps_diluted", "$2.46", 2.46, "USD/share", "units",
     "the second quarter of fiscal 2027"),
    ("Meta", "revenue", "revenue", "$64.6 billion", 64.6, "USD", "billions", "the third quarter of 2026"),
    ("Meta", "net income", "net_income", "$9.86 billion", 9.86, "USD", "billions", "the third quarter of 2026"),
    ("Microsoft", "revenue", "revenue", "$82.9 billion", 82.9, "USD", "billions", "the quarter ended March 31, 2026"),
    ("JPMorgan Chase", "net income", "net_income", "$16.5 billion", 16.5, "USD", "billions", "the first quarter of 2026"),
    ("Alphabet", "revenue", "revenue", "$119.8 billion", 119.8, "USD", "billions", "the quarter ended June 30, 2026"),
    ("AMD", "revenue", "revenue", "$10.3 billion", 10.3, "USD", "billions", "the quarter ended March 28, 2026"),
]
QUALIFIERS = [
    "excluding acquisitions", "excluding foreign exchange effects", "in constant currency", "on an adjusted basis",
    "on a non-GAAP basis", "on a pro forma basis", "excluding one-time items", "on an organic basis",
    "excluding the impact of divestitures", "on an FX-neutral basis", "excluding special items",
    "before restructuring charges", "excluding stock-based compensation", "on an underlying basis",
    "excluding litigation charges", "on a managed basis",
]
POSITIONS = {
    "leading": "{Q}, {E}'s {M} for {P} was {V}.",
    "trailing": "{E}'s {M} for {P} was {V}, {q}.",
    "inside": "{E}'s {M} {q} for {P} was {V}.",
    "parenthetical": "{E}'s {M} for {P} was {V} ({q}).",
}
CONTROL = "{E}'s {M} for {P} was {V}."


def claim(span: str, figure: tuple, metric_text: str, basis) -> Claim:
    entity, _, metric, value_text, value, unit, scale, period = figure
    return Claim(id="c1", metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                 scale=scale, period_text=period, period_type="quarter", comparison="none", basis=basis,
                 forward_looking=False, entity=entity, segment=None, span_text=span, span_start=0,
                 span_end=len(span))


def run(store) -> dict:
    controls, outcomes, rows = Counter(), Counter(), []
    for figure in FIGURES:
        entity, words, *_ , period = figure
        for basis in (None, "GAAP"):
            span = CONTROL.format(E=entity, M=words, P=period, V=figure[3])
            controls[verify(claim(span, figure, words, basis), store).status] += 1
        for qualifier, (position, template), described, basis in itertools.product(
                QUALIFIERS, POSITIONS.items(), (False, True), (None, "GAAP")):
            span = template.format(Q=qualifier[0].upper() + qualifier[1:], q=qualifier, E=entity, M=words, P=period,
                                   V=figure[3])
            verdict = verify(claim(span, figure, f"{words} {qualifier}" if described else words, basis), store)
            evidence = sorted({f.basis for f in verdict.evidence}) if verdict.status == "verified" else []
            outcome = ("verified on reported non-GAAP evidence" if evidence == ["non-GAAP"] else
                       "verified on GAAP evidence" if verdict.status == "verified" else verdict.status)
            outcomes[outcome] += 1
            if verdict.status == "verified":
                rows.append({"span": span, "metric_text_has_qualifier": described, "model_basis": basis,
                             "evidence_basis": evidence, "receipt": verdict.evidence[0].accession})
    return {"figures": len(FIGURES), "qualifiers": len(QUALIFIERS), "positions": list(POSITIONS),
            "variants": sum(outcomes.values()), "outcomes": dict(outcomes),
            "controls": {"variants": sum(controls.values()), "outcomes": dict(controls)}, "verified_rows": rows}


def main() -> int:
    snapshot = ROOT / "data" / "snapshot"
    store = Store(snapshot / "facts.sqlite", snapshot / "pr_facts")
    try:
        result = run(store)
    finally:
        store.close()
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"{result['variants']} qualified variants: {result['outcomes']}")
    print(f"{result['controls']['variants']} unqualified controls: {result['controls']['outcomes']}")
    print(f"written: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
