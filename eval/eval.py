#!/usr/bin/env python
"""Seeded-error eval for NoCap: run receipts.check.check() on the corpus and report catch and false-alarm rates.

    .venv/bin/python eval/eval.py --stub --split dev --limit 20      # offline: a fake check() proves the harness
    .venv/bin/python eval/eval.py --stub oracle --split dev          # offline: a perfect fake; must score 100% / 0%
    .venv/bin/python eval/eval.py --split dev --limit 40 --budget 0.50
    .venv/bin/python eval/eval.py --split test                       # held-out headline numbers: only on request
    .venv/bin/python eval/eval.py --corpus trap_v1 --stub oracle     # trap drafts (eval/build_traps.py), offline
    .venv/bin/python eval/eval.py --corpus trap_v1 --budget 0.40     # trap drafts, dev split, real verifier

What it measures (gold labels come from eval/build_corpus.py, never from a model):
  * catch rate per mutator: the mutated claim (gold `target_value_text` at `target_span`) comes back "wrong".
    Bonus column: its reason code equals the gold `expected_reason`.
  * false-alarm rate: share of unmodified originals with any claim marked "wrong". Forward-looking controls are
    reported separately: none may be "wrong", and their guidance figures should come back "unverifiable".
  * unverifiable rate, latency (fresh calls only), and cost (Meta Model API usage seen through the openai SDK).

Rules:
  * dev is the default split. Test-split numbers are printed only for an explicit --split test, because the
    held-out split is reserved for the headline numbers.
  * Results are cached by item hash plus a verifier fingerprint (receipts/**/*.py, data/facts.sqlite,
    data/pr_facts/), so a rerun is free and any verifier or evidence change re-runs. --no-cache forces a rerun.
  * At most 8 items run at once. The budget guard (--budget, USD) stops starting items once spent plus in-flight
    estimates would pass it; skipped items are reported, never silently dropped.
  * check() gets `as_of` as a timezone-aware UTC datetime (None for most items) and `context` as the corpus's
    document-context line, e.g. "NVIDIA (NVDA), second quarter of fiscal 2027, the quarter ended July 26, 2026".
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import inspect
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "eval" / "corpus"
CACHE_ROOT = ROOT / "data" / "eval_cache"
RUNS_DIR = ROOT / "data" / "eval_runs"
MAX_CONCURRENCY = 8
DEFAULT_BUDGET_USD = 0.50
DEFAULT_EST_COST_PER_ITEM = 0.005  # one single-sentence extraction call; Spike B2 measured ~$0.004 per passage
MUTATORS = ("value", "period", "scale", "basis", "look_ahead", "entity")
STUB_VERSION = "stub-v1"
CORPORA = ("v0", "trap_v1")
TRAP_TYPES = ("period", "scale", "basis", "entity", "value", "look_ahead")
TRAP_EST_COST_PER_ITEM = 0.03  # a ~190-word draft is 4-5 extraction passages

# USD per million tokens (dev.meta.ai pricing, copied from spikes/b_claims.py on 2026-10-27). cached_tokens is a
# subset of input_tokens. Unknown models are charged at the most expensive known rate, so the guard errs safe.
PRICES_PER_M = {
    "muse-spark-1.3": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
    "muse-wonder-1.0": {"input": 4.00, "cached_input": 0.20, "output": 20.00},
    # open-weight backends (RECEIPTS_EXTRACT_BACKEND, docs/open-weights.md); self-hosted Glimmer is billed per GPU-second
    "accounts/fireworks/models/muse-spark-open": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
    "meta-models/muse-spark-open-1026": {"input": 1.25, "cached_input": 0.15, "output": 4.25},
    "muse-glimmer": {"input": 0.0, "cached_input": 0.0, "output": 0.0},
}
FALLBACK_PRICE = max(PRICES_PER_M.values(), key=lambda p: p["output"])


# ============================================================ corpus

def load_split(split: str, corpus: str = "v0") -> list[dict]:
    if corpus == "trap_v1":
        return load_traps(split)
    path = CORPUS_DIR / f"v0_{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} not found; build it with: .venv/bin/python eval/build_corpus.py")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_traps(split: str) -> list[dict]:
    """eval/corpus/trap_v1.jsonl (one file; each draft carries its split), from eval/build_traps.py."""
    path = CORPUS_DIR / "trap_v1.jsonl"
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} not found; build it with: .venv/bin/python eval/build_traps.py")
    with path.open(encoding="utf-8") as f:
        return [it for it in (json.loads(line) for line in f if line.strip()) if it.get("split") == split]


def select_traps(items: list[dict], limit: Optional[int], ticker: Optional[str]) -> list[dict]:
    """Deterministic subset of trap drafts: ordered by item_hash, optionally one company."""
    pool = sorted((it for it in items if not ticker or it["ticker"] == ticker), key=lambda x: x["item_hash"])
    return pool[:limit] if limit is not None else pool


def _bucket(item: dict) -> str:
    if item["mutator"] == "none":
        return "original"
    if item["mutator"] == "forward_looking_control":
        return "control"
    return item["mutator"]


def batch_of(item: dict) -> str:
    """Corpus batch: v0 items carry no batch field; appended batches (the 4-5-number basis appendix) do."""
    return item.get("batch", "v0")


def select_items(items: list[dict], limit: Optional[int], mutator: Optional[str], ticker: Optional[str],
                 batch: Optional[str] = None) -> list[dict]:
    """Deterministic, representative subset: originals and mutants alternate, mutants cycle through the six
    mutators, and one forward-looking control joins each cycle. Inside a bucket, order is by item_hash."""
    pool = [it for it in items if (not mutator or _bucket(it) == mutator) and (not ticker or it["ticker"] == ticker)
            and (not batch or batch_of(it) == batch)]
    buckets: dict[str, list[dict]] = defaultdict(list)
    for it in sorted(pool, key=lambda x: x["item_hash"]):
        buckets[_bucket(it)].append(it)
    pattern: list[str] = []
    for m in MUTATORS:
        pattern += ["original", m]
    pattern.append("control")
    out: list[dict] = []
    while any(buckets.values()) and (limit is None or len(out) < limit):
        for b in pattern:
            if buckets[b] and (limit is None or len(out) < limit):
                out.append(buckets[b].pop(0))
    return out


# ============================================================ cost ledger (openai SDK hooks)

class Ledger:
    """Thread-safe running total of Model API spend, fed by wrappers around the openai SDK's create() calls."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.usd = 0.0
        self.calls = 0
        self.unknown_models: Counter = Counter()
        self.tokens = Counter()

    def record(self, model: Optional[str], usage: Any) -> None:
        if usage is None:
            return
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
        inp = int(get("input_tokens") or get("prompt_tokens") or 0)
        out = int(get("output_tokens") or get("completion_tokens") or 0)
        details = get("input_tokens_details") or get("prompt_tokens_details")
        cached = 0
        if details is not None:
            cached = int((details.get("cached_tokens") if isinstance(details, dict)
                          else getattr(details, "cached_tokens", 0)) or 0)
        price = PRICES_PER_M.get(model or "")
        with self.lock:
            if price is None:
                self.unknown_models[model or "?"] += 1
                price = FALLBACK_PRICE
            cached = min(cached, inp)
            self.usd += ((inp - cached) * price["input"] + cached * price["cached_input"]
                         + out * price["output"]) / 1e6
            self.calls += 1
            self.tokens.update(input=inp, cached=cached, output=out)

    def add_usd(self, usd: float) -> None:
        with self.lock:
            self.usd += usd
            self.calls += 1

    def total(self) -> float:
        with self.lock:
            return self.usd


LEDGER = Ledger()
_HOOKED = False


def install_sdk_hooks(ledger: Ledger = LEDGER) -> bool:
    """Wrap openai Responses / Chat Completions create() (sync and async) so every Model API call's usage lands in
    the ledger. Returns False if the openai package is missing. Streaming calls are not metered."""
    global _HOOKED
    if _HOOKED:
        return True
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
        from openai.resources.responses import AsyncResponses, Responses
    except Exception:  # noqa: BLE001
        return False

    def wrap_sync(cls: type) -> None:
        orig = cls.create

        @functools.wraps(orig)
        def create(self, *a, **kw):  # type: ignore[no-untyped-def]
            resp = orig(self, *a, **kw)
            if not kw.get("stream"):
                ledger.record(kw.get("model") or getattr(resp, "model", None), getattr(resp, "usage", None))
            return resp
        cls.create = create

    def wrap_async(cls: type) -> None:
        orig = cls.create

        @functools.wraps(orig)
        async def create(self, *a, **kw):  # type: ignore[no-untyped-def]
            resp = await orig(self, *a, **kw)
            if not kw.get("stream"):
                ledger.record(kw.get("model") or getattr(resp, "model", None), getattr(resp, "usage", None))
            return resp
        cls.create = create

    for c in (Responses, Completions):
        wrap_sync(c)
    for c in (AsyncResponses, AsyncCompletions):
        wrap_async(c)
    _HOOKED = True
    return True


# ============================================================ verifiers: real and stubs

def dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return obj


def normalize_pairs(result: Any) -> list[tuple[dict, dict]]:
    """check() output -> [(claim dict, verdict dict)]. Accepts tuples/lists of two pydantic models or dicts."""
    pairs = []
    for row in result or []:
        if isinstance(row, dict) and "claim" in row and "verdict" in row:
            pairs.append((dump(row["claim"]), dump(row["verdict"])))
        else:
            claim, verdict = row
            pairs.append((dump(claim), dump(verdict)))
    return pairs


def real_check() -> Callable[..., Any]:
    sys.path.insert(0, str(ROOT))
    try:
        from receipts.check import check  # noqa: E402
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"cannot import receipts.check.check ({type(e).__name__}: {e}). "
                         f"Use --stub to test the harness offline.")
    return check


_NUM_IN_TEXT = re.compile(r"(?:negative\s+)?\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|thousand|trillion)\b|[BMK]\b)?"
                          r"|[+−-]?\d[\d,]*(?:\.\d+)?\s?(?:%|percent\b|percentage points?\b|basis points\b)")


def _value_of(value_text: str) -> float:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value_text)
    return float(m.group(0).replace(",", "")) if m else 0.0


def _stub_claims(text: str, context: Optional[str]):
    from receipts.types import Claim

    entity = (context or "").split(" (")[0] or "Company"
    claims = []
    for i, m in enumerate(_NUM_IN_TEXT.finditer(text)):
        vt = m.group(0).strip()
        low = vt.lower()
        unit = "USD" if "$" in vt else "%"
        scale = next((s for k, s in (("billion", "billions"), ("million", "millions"), ("thousand", "thousands"),
                                     ("trillion", "billions"), ("b", "billions"), ("m", "millions"))
                      if low.endswith(k)), "units")
        claims.append(Claim(
            id=f"c{i + 1}", metric="other", metric_text="stub", value_text=vt, value=_value_of(vt), unit=unit,
            scale=scale, period_text="", period_type="quarter", comparison="none", basis=None,
            forward_looking=False, entity=entity, segment=None, span_text=vt, span_start=m.start(),
            span_end=m.end()))
    return claims


def _h(*parts: Any) -> float:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def make_stub(kind: str, items: list[dict]) -> Callable[..., Any]:
    """An offline stand-in for receipts.check.check with the same signature and return types.

    random: regex 'extraction', verdicts drawn from a hash of the text (about 70% verified, 15% wrong, 15% ?),
            look-ahead as_of mostly yields look_ahead; 5-40 ms latency; a fake $0.0004 per call through the ledger.
    oracle: reads the gold labels, so the scorer must report 100% catch and 0% false alarms."""
    from receipts.types import Verdict

    gold = {it["item_hash"]: it for it in items}

    def item_hash(text: str, as_of: Any, context: Optional[str]) -> str:
        a = as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(as_of, datetime) else as_of
        return hashlib.sha256(json.dumps({"text": text, "as_of": a, "context": context}, sort_keys=True)
                              .encode("utf-8")).hexdigest()

    def check(text: str, *, as_of: Any = None, context: Optional[str] = None):
        claims = _stub_claims(text, context)
        time.sleep(0.005 + 0.035 * _h("lat", text))
        LEDGER.add_usd(0.0004)
        out = []
        it = gold.get(item_hash(text, as_of, context)) if kind == "oracle" else None
        for c in claims:
            if kind == "oracle" and it is not None:
                status, reason = ("unverifiable", "forward_looking") if it["mutator"] == "forward_looking_control" \
                    else ("verified", "match")
                if it["original_item_id"]:
                    hit = it["target_span"] and c.span_start == it["target_span"][0]
                    if hit or (it["mutator"] in ("look_ahead", "entity") and c.value_text in it["affected_value_texts"]):
                        status, reason = "wrong", it["expected_reason"]
            else:
                r = _h("v", text, c.value_text, as_of)
                if as_of is not None and r < 0.6:
                    status, reason = "wrong", "look_ahead"
                elif r < 0.70:
                    status, reason = "verified", "match"
                elif r < 0.85:
                    status, reason = "wrong", ("value_mismatch", "period_mismatch", "scale_mismatch",
                                               "basis_mismatch", "entity_mismatch")[int(r * 1000) % 5]
                else:
                    status, reason = "unverifiable", "no_evidence"
            out.append((c, Verdict(claim_id=c.id, status=status, reason=reason, explanation=f"stub {kind}",
                                   as_of=as_of)))
        return out

    check.__name__ = f"stub_{kind}"
    return check


def make_trap_stub(kind: str, items: list[dict]) -> Callable[..., Any]:
    """Offline stand-in for check() on trap drafts. oracle reads the gold (planted and accidental errors come back
    wrong with their expected reason, forward-looking figures '?', every other figure verified), so the scorer must
    report 100% catch and 0 false alarms; random draws verdicts from a hash of the text."""
    from receipts.types import Verdict

    gold = {it["item_hash"]: it for it in items}

    def item_hash(text: str, as_of: Any, context: Optional[str]) -> str:
        a = as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(as_of, datetime) else as_of
        return hashlib.sha256(json.dumps({"text": text, "as_of": a, "context": context}, sort_keys=True)
                              .encode("utf-8")).hexdigest()

    def check(text: str, *, as_of: Any = None, context: Optional[str] = None):
        claims = _stub_claims(text, context)
        LEDGER.add_usd(0.0004)
        it = gold.get(item_hash(text, as_of, context)) if kind == "oracle" else None
        errors = (it["planted"] + it["accidental"]) if it else []
        fl = [c for c in (it["true_claims"] if it else []) if c.get("forward_looking")]
        out = []
        for c in claims:
            if kind == "oracle":
                status, reason = "verified", "match"
                for e in errors:
                    if c.span_start < e["span"][1] and e["span"][0] < c.span_end:
                        status, reason = "wrong", e["expected_reason"]
                for t in fl:
                    if c.span_start < t["span"][1] and t["span"][0] < c.span_end:
                        status, reason = "unverifiable", "forward_looking"
            else:
                r = _h("trap", text, c.value_text, c.span_start)
                status, reason = (("verified", "match") if r < 0.6 else ("wrong", "value_mismatch") if r < 0.8
                                  else ("unverifiable", "no_evidence"))
            out.append((c, Verdict(claim_id=c.id, status=status, reason=reason, explanation=f"stub {kind}",
                                   as_of=as_of)))
        return out

    check.__name__ = f"trap_stub_{kind}"
    return check


def _value_range(c: dict) -> Optional[tuple[int, int]]:
    """Where a claim's figure sits in the draft: its value_text inside its span_text, else the whole span."""
    a, b = c.get("span_start"), c.get("span_end")
    if not isinstance(a, int) or not isinstance(b, int):
        return None
    vt, st = c.get("value_text") or "", c.get("span_text") or ""
    k = st.find(vt) if vt else -1
    return (a + k, a + k + len(vt)) if k >= 0 else (a, b)


def _claims_on(pairs: list[list[dict]], span: list[int], value_text: str) -> list[int]:
    """Claims that state the gold figure at `span`: the figure itself overlaps it, preferring the same value
    text (the extractor may split a both-bases figure into two claims; all of them count)."""
    s0, e0 = span
    over = [i for i, (c, _) in enumerate(pairs) if (r := _value_range(c)) and r[0] < e0 and s0 < r[1]]
    same = [i for i in over if norm_value_text(pairs[i][0].get("value_text", "")) == norm_value_text(value_text)]
    return same or over


def score_traps(items: list[dict], results: dict[str, dict]) -> dict:
    """Catch rate per error type (the claim at each gold error's span comes back 'wrong'), false alarms on true
    claims and on true sentences (sentences with no gold error), the '?' rate, and per-item outcomes."""
    per_type: dict[str, Counter] = defaultdict(Counter)
    true_c, sent_c, claims_c = Counter(), Counter(), Counter()
    rows = []
    for it in items:
        rec = results.get(it["item_id"])
        errors = [{**e, "origin": "planted"} for e in it["planted"]] + \
                 [{**e, "origin": "accidental"} for e in it["accidental"]]
        if rec is None or rec.get("skipped") or rec.get("error"):
            for e in errors:
                per_type[e["type"]]["skipped_or_error"] += 1
            rows.append({"item_id": it["item_id"], "outcome": "error" if rec and rec.get("error") else "skipped",
                         "error": rec and rec.get("error")})
            continue
        pairs = rec["pairs"]
        claims_c["items"] += 1
        claims_c["claims"] += len(pairs)
        for _, v in pairs:
            claims_c[v.get("status") or "?"] += 1
        used: set[int] = set()
        row = {"item_id": it["item_id"], "ticker": it["ticker"], "n_claims": len(pairs), "errors": [],
               "false_alarms": [], "latency_s": rec.get("latency_s"), "cached": rec.get("cached", False)}
        for e in errors:
            c = per_type[e["type"]]
            c["n"] += 1
            c[e["origin"]] += 1
            idx = _claims_on(pairs, e["span"], e["value_text"])
            used.update(idx)
            verdicts = [pairs[i][1] for i in idx]
            st = {v.get("status") for v in verdicts}
            if not idx:
                c["missing"] += 1
                outcome = "missing"
            elif "wrong" in st:
                c["caught"] += 1
                outcome = "caught"
                if "verified" in st:
                    c["conflicting_verified"] += 1
                if any(v.get("status") == "wrong" and v.get("reason") == e["expected_reason"] for v in verdicts):
                    c["reason_ok"] += 1
            else:
                outcome = "target_" + ("unverifiable" if "unverifiable" in st else
                                       "approximate" if "approximate" in st else "verified")
                c[outcome] += 1
            row["errors"].append({"type": e["type"], "value_text": e["value_text"], "outcome": outcome,
                                  "conflicting_verified": "wrong" in st and "verified" in st,
                                  "verdicts": [(v.get("status"), v.get("reason")) for v in verdicts]})
        for t in it["true_claims"]:
            idx = _claims_on(pairs, t["span"], t["value_text"])
            used.update(idx)
            key = "fl" if t.get("forward_looking") else "true"
            true_c[f"{key}_n"] += 1
            st = {pairs[i][1].get("status") for i in idx}
            if not idx:
                true_c[f"{key}_missing"] += 1
            elif "wrong" in st:
                true_c[f"{key}_wrong"] += 1
                row["false_alarms"].append({"value_text": t["value_text"], "fact_id": t["fact_id"],
                                            "verdicts": [(pairs[i][1].get("status"), pairs[i][1].get("reason"),
                                                          pairs[i][1].get("explanation")) for i in idx]})
            elif "unverifiable" in st:
                true_c[f"{key}_unverifiable"] += 1
            elif "approximate" in st:
                true_c[f"{key}_approximate"] += 1
            else:
                true_c[f"{key}_verified"] += 1
        extra = [i for i in range(len(pairs)) if i not in used]
        true_c["extra_claims"] += len(extra)
        true_c["extra_wrong"] += sum(1 for i in extra if pairs[i][1].get("status") == "wrong")
        err_spans = [e["span"] for e in errors]
        for a, b in it.get("sentences") or []:
            if any(a <= es < b for es, _ in err_spans):
                continue
            inside = [i for i, (cl, _) in enumerate(pairs) if (r := _value_range(cl)) and a <= r[0] < b]
            if not inside:
                continue
            sent_c["true_sentences"] += 1
            if any(pairs[i][1].get("status") == "wrong" for i in inside):
                sent_c["false_alarm_sentences"] += 1
        row["all_caught"] = all(x["outcome"] == "caught" for x in row["errors"])
        rows.append(row)
    return {"per_type": {k: dict(v) for k, v in per_type.items()}, "true_claims": dict(true_c),
            "sentences": dict(sent_c), "claims": dict(claims_c), "rows": rows}


def render_traps(summary: dict, *, split: str, verifier: str, n_items: int, stats: "RunStats",
                 results: dict[str, dict], cost_note: str) -> str:
    L = [f"NoCap trap-draft eval: corpus trap_v1, split={split}, drafts={n_items} (fresh {stats.fresh}, cached "
         f"{stats.cached}, skipped by budget {stats.skipped_budget}, errors {stats.errors}), verifier={verifier}"]
    if split == "test":
        L.append("*** HELD-OUT TEST SPLIT: headline numbers. Do not tune against these. ***")
    L.append("")
    hdr = (f"{'error type':<12}{'n':>4}{'caught':>8}{'catch%':>8}{'reason✓':>9}{'reason%':>9}{'target ?':>9}"
           f"{'target ✓':>9}{'✗+✓':>9}{'missing':>9}{'skip/err':>9}")
    L += [hdr, "-" * len(hdr)]
    tot = Counter()
    for t in TRAP_TYPES:
        c = Counter(summary["per_type"].get(t, {}))
        tot.update(c)
        L.append(f"{t:<12}{c['n']:>4}{c['caught']:>8}{pct(c['caught'], c['n']):>8}{c['reason_ok']:>9}"
                 f"{pct(c['reason_ok'], c['n']):>9}{c['target_unverifiable']:>9}{c['target_verified']:>9}"
                 f"{c['conflicting_verified']:>9}{c['missing']:>9}{c['skipped_or_error']:>9}")
    L.append(f"{'ALL ERRORS':<12}{tot['n']:>4}{tot['caught']:>8}{pct(tot['caught'], tot['n']):>8}"
             f"{tot['reason_ok']:>9}{pct(tot['reason_ok'], tot['n']):>9}{tot['target_unverifiable']:>9}"
             f"{tot['target_verified']:>9}{tot['conflicting_verified']:>9}{tot['missing']:>9}{tot['skipped_or_error']:>9}")
    L.append(f"  of which accidental (found by code, not planted): {tot['accidental']}")
    L.append(f"  conflicting verdicts (✗+✓): {tot['conflicting_verified']} errors were flagged wrong and also "
             "marked verified; these remain included in the any-✗ catch count")
    rows = [r for r in summary["rows"] if "errors" in r]
    if rows:
        L.append(f"  drafts with every error caught: {sum(r['all_caught'] for r in rows)}/{len(rows)}")
    tc, sc, cc = Counter(summary["true_claims"]), Counter(summary["sentences"]), Counter(summary["claims"])
    L.append("")
    L.append(f"true figures (not errors): {tc['true_n']}  ✗ false alarms {tc['true_wrong']} "
             f"({pct(tc['true_wrong'], tc['true_n']).strip()})  ? {tc['true_unverifiable']} "
             f"({pct(tc['true_unverifiable'], tc['true_n']).strip()})  ✓ {tc['true_verified']}  "
             f"≈ {tc['true_approximate']}  missing {tc['true_missing']}")
    L.append(f"true sentences (no gold error) with any ✗: {sc['false_alarm_sentences']}/{sc['true_sentences']} "
             f"({pct(sc['false_alarm_sentences'], sc['true_sentences']).strip()})")
    L.append(f"guidance figures: {tc['fl_n']}  ? {tc['fl_unverifiable']}  ✗ {tc['fl_wrong']}  ✓ {tc['fl_verified']}  "
             f"≈ {tc['fl_approximate']}  missing {tc['fl_missing']}")
    L.append(f"claims extracted: {cc['claims']}  ✗ {cc['wrong']}  ? {cc['unverifiable']} "
             f"({pct(cc['unverifiable'], cc['claims']).strip()} '?' rate)  ✓ {cc['verified']}  ≈ {cc['approximate']}; "
             f"not matched to any gold figure: {tc['extra_claims']} (✗ {tc['extra_wrong']})")
    lat = [r["latency_s"] for r in results.values() if r.get("latency_s") is not None and not r.get("cached")]
    L.append("")
    L.append(f"latency (fresh drafts, n={len(lat)}): p50 {quantile(lat, 0.5):.2f}s  p95 {quantile(lat, 0.95):.2f}s  "
             f"max {max(lat):.2f}s" if lat else "latency: no fresh calls (all cached)")
    L.append(f"cost: {cost_note}")
    for n in stats.notes:
        L.append(f"note: {n}")
    return "\n".join(L)


# ============================================================ cache and fingerprint

def verifier_fingerprint(stub: Optional[str]) -> str:
    if stub:
        return hashlib.sha256(f"{STUB_VERSION}:{stub}".encode()).hexdigest()
    h = hashlib.sha256()
    for p in sorted((ROOT / "receipts").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    # press-release facts live in per-ticker folders (data/pr_facts/<TICKER>/<accession>.jsonl): rglob, not glob
    for p in [ROOT / "data" / "facts.sqlite", *sorted((ROOT / "data" / "pr_facts").rglob("*.jsonl"))]:
        if p.exists():
            st = p.stat()
            h.update(f"{p.relative_to(ROOT)}:{st.st_size}:{st.st_mtime_ns}".encode())
    try:  # the extraction backend: a Muse Glimmer run must never reuse Muse Spark results ('' on the default)
        sys.path.insert(0, str(ROOT))
        from receipts.extract import backend_tag
        tag = backend_tag()
    except Exception:  # noqa: BLE001 - a misconfigured backend shares no cache
        tag = "invalid-backend"
    if tag:
        h.update(f"backend:{tag}".encode())
    return h.hexdigest()


class ResultCache:
    def __init__(self, fingerprint: str, enabled: bool) -> None:
        self.dir = CACHE_ROOT / fingerprint[:16]
        self.enabled = enabled

    def path(self, item_hash: str) -> Path:
        return self.dir / f"{item_hash}.json"

    def get(self, item_hash: str) -> Optional[dict]:
        p = self.path(item_hash)
        if not self.enabled or not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def put(self, item_hash: str, rec: dict) -> None:
        if not self.enabled or rec.get("error"):
            return  # errors are retried next run
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path(item_hash).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False))
        tmp.replace(self.path(item_hash))


# ============================================================ running

def _as_of(item: dict) -> Optional[datetime]:
    return datetime.fromisoformat(item["as_of"].replace("Z", "+00:00")) if item["as_of"] else None


def call_one(check: Callable[..., Any], item: dict) -> dict:
    t0 = time.perf_counter()
    spent0 = LEDGER.total()
    try:
        result = check(item["text"], as_of=_as_of(item), context=item["context"])
        if inspect.isawaitable(result):
            import asyncio
            result = asyncio.run(result)
        pairs = normalize_pairs(result)
        err = None
    except Exception as e:  # noqa: BLE001
        pairs, err = [], f"{type(e).__name__}: {str(e)[:300]}"
        if os.environ.get("EVAL_TRACEBACK"):
            traceback.print_exc()
    return {"item_hash": item["item_hash"], "pairs": [[c, v] for c, v in pairs], "error": err,
            "latency_s": round(time.perf_counter() - t0, 3),
            # spend seen while this item ran; exact only at --concurrency 1 (other items' calls overlap otherwise)
            "cost_usd_approx": round(max(0.0, LEDGER.total() - spent0), 6),
            "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _thread_safety_error(err: Optional[str]) -> bool:
    return bool(err) and ("same thread" in err or "check_same_thread" in err)


@dataclass
class RunStats:
    fresh: int = 0
    cached: int = 0
    skipped_budget: int = 0
    errors: int = 0
    sequential_retries: int = 0
    spent_usd: float = 0.0   # Model API spend of this run, from the ledger (exact even with concurrency)
    notes: list[str] = field(default_factory=list)


def run_items(check: Callable[..., Any], items: list[dict], cache: ResultCache, *, concurrency: int, budget: float,
              est_per_item: float, progress: bool = True) -> tuple[dict[str, dict], RunStats]:
    stats = RunStats()
    results: dict[str, dict] = {}
    todo = []
    for it in items:
        rec = cache.get(it["item_hash"])
        if rec is not None:
            rec["cached"] = True
            results[it["item_id"]] = rec
            stats.cached += 1
        else:
            todo.append(it)
    if not todo:
        return results, stats  # spent_usd stays 0: everything came from the cache
    upfront = len(todo) * est_per_item
    stats.notes.append(f"up-front estimate {len(todo)} uncached items x ${est_per_item:.4f} = ${upfront:.2f} "
                       f"(budget ${budget:.2f}); the guard reserves the estimate for every item in flight and stops "
                       f"starting items when spend + reservations would pass the budget")
    spent_start = LEDGER.total()
    done = 0

    def spent() -> float:
        return LEDGER.total() - spent_start

    def estimate() -> float:  # adapt the per-item estimate once real spend is observed
        return max(est_per_item, (spent() / done) * 1.5) if done >= 3 and spent() > 0 else est_per_item

    def finish(it: dict, rec: dict) -> None:
        nonlocal done
        rec["cached"] = False
        results[it["item_id"]] = rec
        cache.put(it["item_hash"], rec)
        done += 1
        stats.fresh += 1
        if rec["error"]:
            stats.errors += 1
        if progress:
            print(f"  [{done}/{len(todo)}] {it['item_id']}: {len(rec['pairs'])} claims"
                  f"{' ERROR ' + rec['error'][:80] if rec['error'] else ''}  ${spent():.4f}", flush=True)

    retry_seq: list[dict] = []
    queue = list(todo)
    workers = max(1, min(concurrency, MAX_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        inflight: dict[Future, dict] = {}
        while queue or inflight:
            while queue and len(inflight) < workers:
                if spent() + (len(inflight) + 1) * estimate() > budget + 1e-12:
                    break
                it = queue.pop(0)
                inflight[pool.submit(call_one, check, it)] = it
            if not inflight:
                break
            finished, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in finished:
                it = inflight.pop(fut)
                rec = fut.result()
                if _thread_safety_error(rec["error"]):
                    retry_seq.append(it)
                    continue
                finish(it, rec)
    for it in retry_seq:  # the verifier is not thread-safe (e.g. a shared sqlite3 connection): run in this thread
        stats.sequential_retries += 1
        if spent() + estimate() > budget + 1e-12:
            queue.append(it)
            continue
        finish(it, call_one(check, it))
    if retry_seq:
        stats.notes.append(f"{len(retry_seq)} items hit a thread-safety error and were rerun sequentially; "
                           f"consider --concurrency 1")
    stats.skipped_budget = len(queue)
    stats.spent_usd = round(spent(), 6)
    for it in queue:
        results[it["item_id"]] = {"item_hash": it["item_hash"], "pairs": [], "error": None, "skipped": "budget",
                                  "latency_s": None, "cost_usd_approx": 0.0, "cached": False}
    return results, stats


# ============================================================ scoring

def norm_value_text(s: str) -> str:
    s = s.lower().replace("−", "-").replace("percent", "%").replace("per cent", "%")
    s = re.sub(r"\b(?:approximately|about|negative|nearly|over|more than)\b", "", s)
    return re.sub(r"[\s+,]", "", s)


def match_target(pairs: list[list[dict]], item: dict) -> tuple[Optional[int], str]:
    """Index of the claim that is the gold target, and how it was matched."""
    if not pairs:
        return None, "no_claims"
    ts, te = item["target_span"]
    tv = norm_value_text(item["target_value_text"])
    tval = _value_of(item["target_value_text"])
    want_basis = (item.get("mutation") or {}).get("target_basis")

    def covers(c: dict) -> bool:
        a, b = c.get("span_start"), c.get("span_end")
        return isinstance(a, int) and isinstance(b, int) and a <= ts and te <= b

    def overlaps(c: dict) -> bool:
        a, b = c.get("span_start"), c.get("span_end")
        return isinstance(a, int) and isinstance(b, int) and a < te and ts < b

    def basis_ok(c: dict) -> bool:
        return want_basis is None or c.get("basis") in (None, want_basis)

    tiers = [
        ("value_text+span", lambda c: norm_value_text(c.get("value_text", "")) == tv and covers(c) and basis_ok(c)),
        ("value_text+overlap", lambda c: norm_value_text(c.get("value_text", "")) == tv and overlaps(c)),
        ("value_text", lambda c: norm_value_text(c.get("value_text", "")) == tv),
        ("value+span", lambda c: abs(float(c.get("value", -1e18)) - tval) < 1e-9 and overlaps(c)),
        ("value", lambda c: abs(float(c.get("value", -1e18)) - tval) < 1e-9),
    ]
    for name, pred in tiers:
        hits = [i for i, (c, _) in enumerate(pairs) if pred(c)]
        if hits:
            if want_basis:
                exact = [i for i in hits if pairs[i][0].get("basis") == want_basis]
                hits = exact or hits
            return hits[0], name
    return None, "not_extracted"


def score(items: list[dict], results: dict[str, dict]) -> dict:
    per_mut: dict[str, Counter] = defaultdict(Counter)
    orig, ctrl = Counter(), Counter()
    rows = []
    for it in items:
        rec = results.get(it["item_id"])
        if rec is None or rec.get("skipped"):
            (per_mut[it["mutator"]] if it["original_item_id"] else
             (ctrl if it["mutator"] == "forward_looking_control" else orig))["skipped"] += 1
            continue
        pairs = rec["pairs"]
        statuses = Counter(v.get("status") for _, v in pairs)
        row = {"item_id": it["item_id"], "mutator": it["mutator"], "ticker": it["ticker"], "n_claims": len(pairs),
               "statuses": dict(statuses), "error": rec["error"], "latency_s": rec["latency_s"],
               "cached": rec.get("cached", False)}
        if rec["error"]:
            bucket = per_mut[it["mutator"]] if it["original_item_id"] else (
                ctrl if it["mutator"] == "forward_looking_control" else orig)
            bucket["errors"] += 1
            row["outcome"] = "error"
            rows.append(row)
            continue
        if it["original_item_id"]:
            c = per_mut[it["mutator"]]
            c["n"] += 1
            idx, how = match_target(pairs, it)
            row["match"] = how
            if idx is None:
                c["not_extracted"] += 1
                row["outcome"] = "target_not_extracted"
            else:
                v = pairs[idx][1]
                row["target_status"], row["target_reason"] = v.get("status"), v.get("reason")
                if v.get("status") == "wrong":
                    c["caught"] += 1
                    row["outcome"] = "caught"
                    if v.get("reason") == it["expected_reason"]:
                        c["reason_ok"] += 1
                else:
                    row["outcome"] = f"missed_{v.get('status')}"
                    c[f"target_{v.get('status')}"] += 1
                if v.get("reason") == it["expected_reason"]:
                    c["reason_any_status"] += 1
            affected = {norm_value_text(x) for x in it.get("affected_value_texts") or []}
            if any(v.get("status") == "wrong" for cl, v in pairs if norm_value_text(cl.get("value_text", "")) in affected):
                c["any_affected_wrong"] += 1
        else:
            c = ctrl if it["mutator"] == "forward_looking_control" else orig
            c["n"] += 1
            c["claims"] += len(pairs)
            for s in ("verified", "wrong", "unverifiable", "approximate"):
                c[s] += statuses.get(s, 0)
            if statuses.get("wrong", 0):
                c["false_alarm_items"] += 1
                row["outcome"] = "false_alarm"
            else:
                row["outcome"] = "ok"
            if not pairs:
                c["no_claims"] += 1
            if it["mutator"] == "forward_looking_control":
                # controls hold guidance figures only (build_corpus rejects ones mixed with actuals)
                if pairs and all(v.get("status") == "unverifiable" for _, v in pairs):
                    c["fl_all_unverifiable"] += 1
                if any(v.get("reason") == "forward_looking" for _, v in pairs):
                    c["fl_reason_seen"] += 1
        rows.append(row)
    return {"per_mutator": {k: dict(v) for k, v in per_mut.items()}, "originals": dict(orig),
            "controls": dict(ctrl), "rows": rows}


def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:5.1f}%" if b else "    -"


def quantile(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def render(summary: dict, *, split: str, verifier: str, n_items: int, stats: RunStats, results: dict[str, dict],
           cost_note: str) -> str:
    L = []
    L.append(f"NoCap seeded-error eval: corpus v0, split={split}, items={n_items} "
             f"(fresh {stats.fresh}, cached {stats.cached}, skipped by budget {stats.skipped_budget}, "
             f"errors {stats.errors}), verifier={verifier}")
    if split == "test":
        L.append("*** HELD-OUT TEST SPLIT: headline numbers. Do not tune against these. ***")
    L.append("")
    hdr = (f"{'mutator':<12}{'n':>4}{'caught':>8}{'catch%':>8}{'reason✓':>9}{'reason%':>9}"
           f"{'target ?':>9}{'target ✓':>9}{'missing':>9}{'skip/err':>9}")
    L.append(hdr)
    L.append("-" * len(hdr))
    tot = Counter()
    for m in MUTATORS:
        c = Counter(summary["per_mutator"].get(m, {}))
        tot.update(c)
        L.append(f"{m:<12}{c['n']:>4}{c['caught']:>8}{pct(c['caught'], c['n']):>8}{c['reason_ok']:>9}"
                 f"{pct(c['reason_ok'], c['n']):>9}{c['target_unverifiable']:>9}{c['target_verified']:>9}"
                 f"{c['not_extracted']:>9}{c['skipped'] + c['errors']:>9}")
    L.append(f"{'ALL MUTANTS':<12}{tot['n']:>4}{tot['caught']:>8}{pct(tot['caught'], tot['n']):>8}"
             f"{tot['reason_ok']:>9}{pct(tot['reason_ok'], tot['n']):>9}{tot['target_unverifiable']:>9}"
             f"{tot['target_verified']:>9}{tot['not_extracted']:>9}{tot['skipped'] + tot['errors']:>9}")
    la = Counter(summary["per_mutator"].get("look_ahead", {}))
    if la["n"]:
        L.append(f"  look_ahead: reason look_ahead on the target regardless of status: "
                 f"{la['reason_any_status']}/{la['n']} ({pct(la['reason_any_status'], la['n']).strip()})")
    L.append("")
    hdr2 = (f"{'originals':<12}{'n':>4}{'false-alarm items':>19}{'FA%':>8}{'claims':>8}{'✗':>5}{'?':>5}{'✓':>5}"
            f"{'≈':>5}{'?%':>8}{'no claims':>10}")
    L.append(hdr2)
    L.append("-" * len(hdr2))
    for name, key in (("unmodified", "originals"), ("fl controls", "controls")):
        c = Counter(summary[key])
        L.append(f"{name:<12}{c['n']:>4}{c['false_alarm_items']:>19}{pct(c['false_alarm_items'], c['n']):>8}"
                 f"{c['claims']:>8}{c['wrong']:>5}{c['unverifiable']:>5}{c['verified']:>5}{c['approximate']:>5}"
                 f"{pct(c['unverifiable'], c['claims']):>8}{c['no_claims']:>10}")
    cc = Counter(summary["controls"])
    if cc["n"]:
        L.append(f"  fl controls with every claim '?': {cc['fl_all_unverifiable']}/{cc['n']}; "
                 f"forward_looking reason seen: {cc['fl_reason_seen']}/{cc['n']}")
    lat = [r["latency_s"] for r in results.values() if r.get("latency_s") is not None and not r.get("cached")]
    L.append("")
    if lat:
        L.append(f"latency (fresh calls, n={len(lat)}): p50 {quantile(lat, 0.5):.2f}s  p95 {quantile(lat, 0.95):.2f}s  "
                 f"max {max(lat):.2f}s  mean {sum(lat) / len(lat):.2f}s")
    else:
        L.append("latency: no fresh calls (all cached)")
    L.append(f"cost: {cost_note}")
    for n in stats.notes:
        L.append(f"note: {n}")
    return "\n".join(L)


# ============================================================ main

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", choices=CORPORA, default="v0",
                    help="v0 (seeded single sentences, default) or trap_v1 (Muse Wonder trap drafts, eval/build_traps.py)")
    ap.add_argument("--split", choices=("dev", "test"), default="dev",
                    help="dev (default). test is the held-out split: only for the headline numbers")
    ap.add_argument("--limit", type=int, help="run a deterministic, mutator-balanced subset of N items")
    ap.add_argument("--mutator", choices=("original", "control", *MUTATORS), help="only this kind of item")
    ap.add_argument("--ticker", help="only this company")
    ap.add_argument("--batch", help="only this corpus batch: v0 (the original 211 dev / 92 test items) or basis5 "
                                    "(the 4-5-number basis appendix)")
    ap.add_argument("--stub", nargs="?", const="random", choices=("random", "oracle"),
                    help="offline fake check(): random (default) exercises every path; oracle must score 100%%/0%%")
    ap.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY, help=f"items in flight (max {MAX_CONCURRENCY})")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD, help="USD cap on Model API spend for this run")
    ap.add_argument("--est-cost-per-item", type=float, default=None,
                    help="spend estimate per uncached item, used by the budget guard until real spend is observed")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached results and rerun every item")
    ap.add_argument("--quiet", action="store_true", help="no per-item progress lines")
    ap.add_argument("--json-out", type=Path, help="where to write the run record (default data/eval_runs/...)")
    args = ap.parse_args(argv)
    if args.est_cost_per_item is None:
        args.est_cost_per_item = TRAP_EST_COST_PER_ITEM if args.corpus == "trap_v1" else DEFAULT_EST_COST_PER_ITEM

    sys.path.insert(0, str(ROOT))
    if args.corpus == "trap_v1":
        return main_traps(args)
    items_all = load_split(args.split)
    items = select_items(items_all, args.limit, args.mutator, args.ticker, args.batch)
    if not items:
        raise SystemExit("no items selected")
    if args.stub:
        check = make_stub(args.stub, items_all)
        verifier = f"stub:{args.stub}"
        metered = "stub (fake $0.0004 per call)"
    else:
        missing = [p for p in ("receipts/check.py", "data/facts.sqlite") if not (ROOT / p).exists()]
        if missing:
            raise SystemExit(f"missing {', '.join(missing)}; the real verifier cannot run yet. Use --stub.")
        hooked = install_sdk_hooks()
        check = real_check()
        verifier = "receipts.check"
        metered = "Model API usage via openai SDK hooks" if hooked else "UNMETERED (openai SDK not importable)"
    if args.split == "test":
        print("*** --split test: reporting the HELD-OUT split. Use it for headline numbers only. ***", flush=True)
    fp = verifier_fingerprint(args.stub)
    cache = ResultCache(fp, enabled=not args.no_cache)
    t0 = time.perf_counter()
    results, stats = run_items(check, items, cache, concurrency=args.concurrency, budget=args.budget,
                               est_per_item=args.est_cost_per_item, progress=not args.quiet)
    wall = time.perf_counter() - t0
    summary = score(items, results)
    fresh_cost = stats.spent_usd  # per-item cost_usd values overlap under concurrency; the ledger total does not
    unknown = dict(LEDGER.unknown_models)
    cost_note = (f"${fresh_cost:.4f} this run ({metered}; {LEDGER.calls} metered calls"
                 f"{', unknown models priced at the max rate: ' + json.dumps(unknown) if unknown else ''}); "
                 f"budget ${args.budget:.2f}; wall {wall:.1f}s")
    print()
    print(render(summary, split=args.split, verifier=verifier, n_items=len(items), stats=stats, results=results,
                 cost_note=cost_note))
    out = args.json_out or RUNS_DIR / (f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{args.split}-"
                                       f"{verifier.replace(':', '_').replace('.', '_')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "split": args.split, "verifier": verifier, "fingerprint": fp, "args": {k: str(v) for k, v in vars(args).items()},
        "items": [it["item_id"] for it in items], "stats": vars(stats), "cost_usd": round(fresh_cost, 6),
        "summary": {k: v for k, v in summary.items() if k != "rows"}, "rows": summary["rows"],
    }, indent=1, ensure_ascii=False))
    print(f"run record: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


def main_traps(args: argparse.Namespace) -> int:
    """--corpus trap_v1: the same runner, cache, budget guard and cost ledger, scored per error type."""
    if args.mutator or args.batch:
        raise SystemExit("--mutator and --batch apply to corpus v0 only")
    items_all = load_traps(args.split)
    items = select_traps(items_all, args.limit, args.ticker)
    if not items:
        raise SystemExit("no trap drafts selected")
    if args.stub:
        check = make_trap_stub(args.stub, items_all)
        verifier, metered = f"stub:{args.stub}", "stub (fake $0.0004 per call)"
    else:
        missing = [p for p in ("receipts/check.py", "data/facts.sqlite") if not (ROOT / p).exists()]
        if missing:
            raise SystemExit(f"missing {', '.join(missing)}; the real verifier cannot run yet. Use --stub.")
        hooked = install_sdk_hooks()
        check = real_check()
        verifier = "receipts.check"
        metered = "Model API usage via openai SDK hooks" if hooked else "UNMETERED (openai SDK not importable)"
    if args.split == "test":
        print("*** --split test: reporting the HELD-OUT split. Use it for headline numbers only. ***", flush=True)
    fp = verifier_fingerprint(args.stub)
    cache = ResultCache(fp, enabled=not args.no_cache)
    t0 = time.perf_counter()
    results, stats = run_items(check, items, cache, concurrency=args.concurrency, budget=args.budget,
                               est_per_item=args.est_cost_per_item, progress=not args.quiet)
    wall = time.perf_counter() - t0
    summary = score_traps(items, results)
    unknown = dict(LEDGER.unknown_models)
    cost_note = (f"${stats.spent_usd:.4f} this run ({metered}; {LEDGER.calls} metered calls"
                 f"{', unknown models priced at the max rate: ' + json.dumps(unknown) if unknown else ''}); "
                 f"budget ${args.budget:.2f}; wall {wall:.1f}s")
    print()
    print(render_traps(summary, split=args.split, verifier=verifier, n_items=len(items), stats=stats, results=results,
                       cost_note=cost_note))
    out = args.json_out or RUNS_DIR / (f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-trap_v1-{args.split}-"
                                       f"{verifier.replace(':', '_').replace('.', '_')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "corpus": "trap_v1", "split": args.split, "verifier": verifier, "fingerprint": fp,
        "args": {k: str(v) for k, v in vars(args).items()}, "items": [it["item_id"] for it in items],
        "stats": vars(stats), "cost_usd": round(stats.spent_usd, 6),
        "summary": {k: v for k, v in summary.items() if k != "rows"}, "rows": summary["rows"],
    }, indent=1, ensure_ascii=False))
    print(f"run record: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
