#!/usr/bin/env python
"""Model benchmark: what each model's raw extraction gets wrong, and what Receipts still lets through.

    .venv/bin/python eval/bench_models.py --list                      # targets, and which are configured
    .venv/bin/python eval/bench_models.py --budget 0.60               # every configured target
    .venv/bin/python eval/bench_models.py --targets spark,wonder      # just these
    .venv/bin/python eval/bench_models.py --targets glimmer --allow-self-hosted  # explicitly allow GPU billing
    .venv/bin/python eval/bench_models.py --offline                   # re-score saved responses; no calls
    .venv/bin/python eval/bench_models.py --report-only               # rebuild eval/bench_results.md only

The same two inputs for every model:
  * gold: spikes/inputs/gold_text.txt, ten passages of NVIDIA's Q2 FY2027 press release with 27 hand-labeled claims
    (spikes/inputs/gold_claims.json). Extracted exactly as the product does (context inferred, all passages in
    parallel), so its wall time is the product's latency for that draft.
  * dev: a fixed, mutator-balanced 13-item subset of the seeded-error corpus (eval/corpus/v0_dev.jsonl, picked by
    eval.py's select_items): 6 real sentences, 6 copies with one injected error each (value, period, scale, basis,
    look-ahead, entity) and 1 guidance control. Never the test split.

Per model (definitions: docs/open-weights.md, "Benchmark"):
  raw output, the model's JSON before any Receipts code
    * raw claims wrong as returned (the raw hallucination rate): claims whose number is not what the text says:
      value_text absent from the passage (invented), or value, scale or unit disagreeing with value_text
    * label errors (gold): gold claims whose number the model found but mislabeled (metric, GAAP/non-GAAP or period)
    * verbatim spans: raw span_text found character for character in the passage
    * schema: calls with no usable JSON, and claims the schema rejected
  shipped by Receipts, after code locates, re-reads and expands every claim and receipts.verify decides
    * strict recall and precision against the 27 gold claims (value, metric, basis and period must all match)
    * unflagged errors: shipped claims that are wrong yet marked verified. The number that matters; the target is 0.
    * dev: injected errors caught (wrong), dangerous misses (marked verified), false alarms on real sentences
  latency: wall time of the gold check, plus per-call p50 and max. cost: token usage at the backend's prices; a
  self-hosted model bills per GPU-second, estimated here from wall time.

Responses are cached in data/cache/bench/ by request and endpoint, so a rerun is free (--fresh re-measures). Dev
items reuse data/cache/spark when the identical request is already there. The budget guard (--budget, USD) stops
starting calls once spent plus an estimate would pass it; it is not a hard provider billing cap. --budget 0 and
--offline never contact a server. Self-hosted GPU billing requires --allow-self-hosted and is not limited by the
token budget (cold starts, idle time, CPU and RAM also cost money). Skipped work is reported. Each target's
latest result is kept in data/bench/latest/, and eval/bench_results.md is rebuilt from all of them.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import shutil
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openai  # noqa: E402

from receipts import extract as ex  # noqa: E402
from receipts import types  # noqa: E402

GOLD_TEXT = ROOT / "spikes" / "inputs" / "gold_text.txt"
GOLD_CLAIMS = ROOT / "spikes" / "inputs" / "gold_claims.json"
BENCH_CACHE = ROOT / "data" / "cache" / "bench"
OUT_DIR = ROOT / "data" / "bench"
RESULTS_MD = ROOT / "eval" / "bench_results.md"
DEV_ITEMS = 13            # one full select_items cycle: 6 originals, one mutant per mutator, one control
# eval.py's select_items(dev, 13) on 2026-10-27, pinned so every model sees the same items even if the corpus grows
DEV_ITEM_IDS = (
    "v0-MSFT-FY2026Q2-s002843", "v0-NFLX-FY2026Q3-s013638-value", "v0-BAC-FY2026Q2-s002519",
    "v0-BAC-FY2025Q3-s002429-period", "v0-NFLX-FY2026Q1-s016451", "v0-AAPL-FY2026Q1-s000277-scale",
    "v0-AMZN-FY2025Q3-s000212", "v0-MSFT-FY2026Q2-s000578-basis", "v0-NVDA-FY2026Q3-s000525",
    "v0-NVDA-FY2026Q3-s000142-look_ahead", "v0-AAPL-FY2026Q3-s000498", "v0-AMZN-FY2026Q1-s000206-entity",
    "v0-NFLX-FY2026Q1-s004131",
)
DEV_CONCURRENCY = 4
DEFAULT_BUDGET_USD = 0.60
H100_USD_PER_S = 0.001097  # modal.com/pricing, 2026-10-27; deploy/modal_glimmer.py runs on one H100

# name -> (backend preset, field changes, label, spend estimate per uncached passage in USD)
TARGETS: dict[str, tuple[str, dict[str, Any], str, float]] = {
    "spark": ("meta", {}, "Muse Spark 1.3 · Meta Model API", 0.006),
    "wonder": ("meta", {"model": "muse-wonder-1.0"}, "Muse Wonder 1.0 · Meta Model API", 0.03),
    "spark-open-fireworks": ("fireworks", {}, "Muse Spark Open · Fireworks (open weights)", 0.015),
    "spark-open-baseten": ("baseten", {}, "Muse Spark Open · Baseten (open weights)", 0.015),
    "glimmer": ("modal", {}, "Muse Glimmer 30B · Modal, self-hosted (open weights)", 0.0),
}


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
def make_target(name: str) -> ex.Backend:
    preset, changes, _, _ = TARGETS[name]
    b = ex.BACKENDS[preset]
    if b.base_url_env:  # a per-deployment URL (Modal) comes from its own setting, as in the product
        changes = {**changes, "base_url": (ex.settings_env().get(b.base_url_env) or "").strip().rstrip("/")}
    return ex.make_backend(preset, **changes)


def missing_setup(b: ex.Backend) -> Optional[str]:
    """None when the target can run; otherwise what is missing. Never prints or returns a key."""
    try:
        ex.make_client(backend=b)  # loads .env; raises naming the missing variable; sends nothing
    except ex.ExtractionError as e:
        return str(e).replace(" Nothing was sent.", "")
    return None


def warm_up(client: openai.OpenAI, timeout_s: float, *, sleep=time.sleep) -> float:
    """Wait for a scale-to-zero server: Modal answers 503 until vLLM is listening. Returns the seconds waited."""
    t0 = time.monotonic()
    while True:
        try:
            client.models.list()
            return time.monotonic() - t0
        except openai.APIStatusError as e:
            if e.status_code not in (502, 503, 504):
                raise
        except (openai.APIConnectionError, openai.APITimeoutError):
            pass
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"the server was not ready after {timeout_s:.0f} s")
        print(f"    waiting for the server ({time.monotonic() - t0:.0f} s)...", flush=True)
        sleep(15)


# ---------------------------------------------------------------------------
# Gold labels (spikes/b2_claims.py: canonical metric and basis from the label names, period by word overlap)
# ---------------------------------------------------------------------------
_GOLD_RULES = (  # first match wins, on the lowercased label
    (r"\brange\b|tolerance|plus or minus", "guidance_tolerance"),
    (r"repurchase authori", "repurchase_authorization_remaining"),
    (r"returned to shareholders|shareholder returns?|capital returned", "capital_returned"),
    (r"dividend", "dividend_per_share"),
    (r"tax rate", "tax_rate"),
    (r"gross margin", "gross_margin"),
    (r"operating expense", "operating_expenses"),
    (r"operating income", "operating_income"),
    (r"net income", "net_income"),
    (r"\bbasic\b.*(\beps\b|earnings per)|(\beps\b|earnings per).*\bbasic\b", "eps_basic"),
    (r"\beps\b|earnings per", "eps_diluted"),
    (r"revenue\b.*\b(growth|change|increase|decrease|decline)|(growth|change|increase|decrease|decline)\b.*\brevenue",
     "revenue_growth"),
    (r"revenue", "revenue"),
)
_SEGMENT_LABEL = re.compile(r"^(?!(?i:total|net|gaap|non-gaap|expected)\b)[A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*)*\s+revenue\b")
_STOP = frozenset("the a an of for in on and to per its our company s".split())
_EXPAND = (
    (re.compile(r"\bq([1-4])\b"), lambda m: ("first", "second", "third", "fourth")[int(m.group(1)) - 1] + " quarter"),
    (re.compile(r"\bfy\s?(\d{4})\b"), lambda m: "fiscal " + m.group(1)),
    (re.compile(r"\bfy\s?(\d{2})\b"), lambda m: "fiscal 20" + m.group(1)),
    (re.compile(r"\beps\b"), lambda m: "earnings per share"),
    (re.compile(r"\byoy\b"), lambda m: "year over year"),
    (re.compile(r"\bnon[\s-]?gaap\b"), lambda m: "nongaap"),
)
SCALE = {**types.SCALE_FACTOR, "trillions": 1e12}


def gold_canonical(label: str) -> str:
    low = label.casefold()
    for pattern, metric in _GOLD_RULES:
        if re.search(pattern, low):
            return "segment_revenue" if metric == "revenue" and _SEGMENT_LABEL.match(label) else metric
    return "other"


def _words(s: str) -> set[str]:
    s = s.casefold()
    for rx, fn in _EXPAND:
        s = rx.sub(fn, s)
    return {w for w in re.findall(r"[a-z0-9]+", s) if w not in _STOP}


def _text_match(pred: str, alternatives: list[str]) -> bool:
    a = _words(pred)
    all_words = set().union(*(_words(alt) for alt in alternatives))
    # Generic aliases such as "the quarter" must not hide an explicitly contradictory quarter or year.
    quarters = {"first", "second", "third", "fourth"}
    if a & quarters and all_words & quarters and not a & all_words & quarters:
        return False
    years = {w for w in a if re.fullmatch(r"(?:19|20)\d\d", w)}
    gold_years = {w for w in all_words if re.fullmatch(r"(?:19|20)\d\d", w)}
    if years and gold_years and not years & gold_years:
        return False
    for alt in alternatives:
        b = _words(alt)
        if not a or not b or ("nongaap" in a) != ("nongaap" in b):
            continue
        ya = {w for w in a if re.fullmatch(r"(?:19|20)\d\d", w)}
        yb = {w for w in b if re.fullmatch(r"(?:19|20)\d\d", w)}
        if ya and yb and not ya & yb:
            continue
        if len(a & b) / min(len(a), len(b)) >= 0.5:
            return True
    return False


def load_gold() -> list[dict[str, Any]]:
    gold = []
    for g in json.loads(GOLD_CLAIMS.read_text(encoding="utf-8"))["claims"]:
        labels = [g["metric"]] if isinstance(g["metric"], str) else g["metric"]
        period = g.get("period") or []
        basis = ("non-GAAP" if any(re.search(r"non[\s-]?gaap", s, re.I) for s in labels)
                 else "GAAP" if any(re.search(r"\bgaap\b", s, re.I) for s in labels) else None)
        gold.append({"labels": labels, "canonical": {gold_canonical(m) for m in labels}, "basis": basis,
                     "value": float(g["value"]), "scale": g.get("scale", "units"), "value_text": g["value_text"],
                     "period": [period] if isinstance(period, str) else period})
    return gold


def _assign(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Maximum one-to-one matching; broad gold aliases must not steal a stricter label's only match."""
    choices: dict[int, list[int]] = {}
    for g, p in pairs:
        choices.setdefault(g, []).append(p)
    assigned: dict[int, int] = {}

    def augment(g: int, seen: set[int]) -> bool:
        for p in choices[g]:
            if p in seen:
                continue
            seen.add(p)
            if p not in assigned or augment(assigned[p], seen):
                assigned[p] = g
                return True
        return False

    for g in choices:
        augment(g, set())
    return sorted((g, p) for p, g in assigned.items())


def match_gold(entries: list[dict[str, Any]], gold: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    """(strictly matched entry indices, value-matched entry indices). An entry is a claim with one basis:
    {metric, value, scale, basis, period_text}. Maximum one-to-one assignment."""
    strict_pairs, value_pairs = [], []
    for gi, g in enumerate(gold):
        target = g["value"] * SCALE[g["scale"]]
        for ei, e in enumerate(entries):
            if not math.isclose(e["value"] * SCALE.get(e["scale"], 1.0), target, rel_tol=1e-6, abs_tol=1e-9):
                continue
            value_pairs.append((gi, ei))
            if (e["metric"] in g["canonical"] and (g["basis"] is None or e["basis"] == g["basis"])
                    and (not g["period"] or _text_match(e["period_text"], g["period"]))):
                strict_pairs.append((gi, ei))
    return {e for _, e in _assign(strict_pairs)}, {e for _, e in _assign(value_pairs)}


# ---------------------------------------------------------------------------
# Raw output: what the model returned, checked against the passage only
# ---------------------------------------------------------------------------
def raw_errors(c: ex.ModelClaim, passage: str) -> list[str]:
    """Why this raw claim would put a wrong number on the page if shipped as is ([] when it wouldn't)."""
    if not c.value_text or c.value_text not in passage:
        return ["invented"]  # the number is not in the text at all
    p = ex.parse_value_text(c.value_text)
    if p["n_numbers"] != 1 or p["number"] is None:
        return []  # a range or a ratio: code can't read one number back, so it isn't judged here
    number, scale = p["number"], p["scale"]
    if scale == "trillions":
        number, scale = number * 1000, "billions"
    out = []
    if not math.isclose(c.value, number, rel_tol=1e-9, abs_tol=1e-12):
        out.append("value")
    if scale and scale != c.scale:
        out.append("scale")
    want = {"percent": {"%"}, "money": {"USD", "USD/share"}, "points": {"other"}}.get(p["kind"])
    if want and c.unit not in want:
        out.append("unit")
    return out


def raw_report(res: ex.ExtractResult, text: str, b: ex.Backend, cache_dir: Path) -> dict[str, Any]:
    """The model's own JSON for every passage call, read back from the response cache and checked."""
    passages = dict(ex.split_passages(text))  # start offset -> passage
    claims, errors, rejected, failed, entries = 0, Counter(), 0, 0, []
    verbatim, examples = 0, []
    for call in res.calls:
        rec_path = cache_dir / f"{call['request_sha256']}.json"
        if call.get("status") != "ok" or not rec_path.exists():
            failed += 1
            continue
        status, resp, _, n_rejected = ex.check_output(json.loads(rec_path.read_text(encoding="utf-8"))["output_text"], b)
        if status != "ok" or resp is None:
            failed += 1
            continue
        rejected += n_rejected
        passage = passages[call["start"]]
        for c in resp.claims:
            claims += 1
            why = raw_errors(c, passage)
            errors.update(why)
            if why:
                errors["any"] += 1
                examples.append({"value_text": c.value_text, "value": c.value, "scale": c.scale, "unit": c.unit,
                                 "errors": why})
            verbatim += bool(c.span_text and c.span_text in passage and c.value_text in c.span_text)
            entries.extend({"metric": c.metric, "value": c.value, "scale": c.scale, "basis": basis,
                            "period_text": c.period_text, "raw_error": bool(why)}
                           for basis in (list(dict.fromkeys(c.bases)) or [None]))
    return {"claims": claims, "wrong": errors["any"], "by_reason": {k: v for k, v in errors.items() if k != "any"},
            "verbatim_spans": verbatim, "schema_rejected": rejected, "failed_calls": failed,
            "examples": examples[:10], "entries": entries}


# ---------------------------------------------------------------------------
# Running one target
# ---------------------------------------------------------------------------
class Budget:
    def __init__(self, cap_usd: float):
        if not math.isfinite(cap_usd) or cap_usd < 0:
            raise ValueError("budget must be a finite, nonnegative USD amount")
        self.cap, self.spent = cap_usd, 0.0

    def allows(self, estimate: float) -> bool:
        return self.spent + estimate <= self.cap + 1e-12


class CacheOnlyClient:
    """Defense against a cache file disappearing between the preflight and extraction reads."""

    def __getattr__(self, name: str) -> Any:
        raise ex.ExtractionError("cache-only benchmark: a response is missing; no request was sent")


CACHE_ONLY_CLIENT = CacheOnlyClient()


def seed_from_shared_cache(text: str, context: Optional[str], b: ex.Backend, cache_dir: Path) -> int:
    """Copy identical-request responses from data/cache/spark, so the dev subset doesn't pay twice for them."""
    copied = 0
    for key in ex.request_keys(text, context, backend=b):
        src, dst = ex.CACHE_DIR / f"{key}.json", cache_dir / f"{key}.json"
        if src.exists() and not dst.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            copied += 1
    return copied


def load_eval_module():
    spec = importlib.util.spec_from_file_location("receipts_eval_for_bench", ROOT / "eval" / "eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def verdicts(claims: list[types.Claim], store: Any, as_of: Any, context: Optional[str]) -> list[types.Verdict]:
    from receipts.stream import verdict_for
    return [verdict_for(c, store, as_of, context) for c in claims]


def call_latencies(res: ex.ExtractResult, cache_dir: Path) -> list[float]:
    """Seconds per call: measured now, or recorded when the response was first fetched (cache records)."""
    out = []
    for call in res.calls:
        if not call.get("cached") and call.get("latency_s"):
            out.append(float(call["latency_s"]))
            continue
        rec_path = cache_dir / f"{call['request_sha256']}.json"
        if rec_path.exists():
            lat = json.loads(rec_path.read_text(encoding="utf-8")).get("latency_s")
            if lat:
                out.append(float(lat))
    return out


def run_target(name: str, *, budget: Budget, store: Any, ev: Any, dev_items: list[dict], gold: list[dict],
               cache_dir: Path, offline: bool, warmup_timeout_s: float,
               allow_self_hosted: bool = False, reuse_shared_cache: bool = True) -> dict[str, Any]:
    b = make_target(name)
    _, _, label, est_passage = TARGETS[name]
    out: dict[str, Any] = {"target": name, "label": label, "backend": b.name, "model": b.model,
                           "hosting": b.hosting, "open_weights": b.open_weights, "self_hosted": b.self_hosted,
                           "base_url": b.base_url, "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "notes": []}
    client = None
    setup_problem = None

    def blocked(need: int, cost: float, reserved: float = 0.0) -> Optional[str]:
        if not need:
            return None
        if offline:
            return "not cached (offline)"
        if budget.cap == 0:
            return "not cached (--budget 0 forbids all server requests)"
        if b.self_hosted and not allow_self_hosted:
            return "self-hosted GPU billing requires --allow-self-hosted; token budget does not cap GPU charges"
        if not budget.allows(cost + reserved):
            return f"budget: needs about ${cost:.2f}, ${max(0.0, budget.cap - budget.spent - reserved):.2f} left"
        return setup_problem

    def client_for(need: int) -> Any:
        nonlocal client
        if not need:
            return CACHE_ONLY_CLIENT
        if client is None:
            client = ex.make_client(backend=b)
            if b.self_hosted:
                print(f"  {name}: checking that the server is up (cold starts take minutes)...", flush=True)
                out["warmup_s"] = round(warm_up(client, warmup_timeout_s), 1)
                out["notes"].append("GPU charges are outside the token budget; estimates exclude cold start, "
                                    "idle time, CPU and RAM.")
        return client

    # --- gold: the product's own call pattern ------------------------------------------------------------
    gold_text = GOLD_TEXT.read_text(encoding="utf-8")
    if reuse_shared_cache:
        seed_from_shared_cache(gold_text, None, b, cache_dir)
    need = ex.passages_to_call(gold_text, None, cache_dirs=[cache_dir], backend=b)
    problem = blocked(need, need * est_passage)
    if problem is None:
        try:
            gold_client = client_for(need)
        except ex.ExtractionError as e:
            problem = setup_problem = str(e)
    if problem:
        out["gold"] = {"skipped": problem}
    else:
        res = ex.extract_detailed(gold_text, None, client=gold_client, cache_dir=cache_dir, backend=b)
        budget.spent += res.cost_usd
        fresh = [c for c in res.calls if not c.get("cached")]
        if fresh and res.cost_usd:
            est_passage = max(est_passage, 1.25 * res.cost_usd / len(fresh))  # learn the real price per call
        raw = raw_report(res, gold_text, b, cache_dir)
        raw_strict, raw_value = match_gold(raw["entries"], gold)
        entries = [{"metric": c.metric, "value": c.value, "scale": c.scale, "basis": c.basis,
                    "period_text": c.period_text} for c in res.claims]
        strict, by_value = match_gold(entries, gold)
        vs = verdicts(res.claims, store, None, res.context)
        status = Counter(v.status for v in vs)
        unmatched = [i for i in range(len(res.claims)) if i not in strict]
        lat = call_latencies(res, cache_dir)
        usage = [c.get("usage") for c in res.calls if c.get("usage")]
        cost_per_check = round(sum(ex.cost_usd(u, b.prices) for u in usage), 6)
        # A mixed cached/live run does not measure an uncached whole-document check.
        wall = res.wall_s if fresh and len(fresh) == len(res.calls) else (max(lat) if lat else None)
        out["gold"] = {
            "passages": len(res.calls), "failed_passages": sum(c.get("status") != "ok" for c in res.calls),
            "context": res.context, "fresh_calls": len(fresh),
            "raw": {k: v for k, v in raw.items() if k != "entries"},
            "raw_label_errors": max(0, len(raw_value) - len(raw_strict)),
            "shipped_claims": len(res.claims), "strict_matched": len(strict), "value_matched": len(by_value),
            "recall": len(strict) / len(gold), "precision": len(strict) / len(res.claims) if res.claims else None,
            "verdicts": dict(status),
            "unmatched": [{"value_text": res.claims[i].value_text, "metric": res.claims[i].metric,
                           "basis": res.claims[i].basis, "period_text": res.claims[i].period_text,
                           "verdict": vs[i].status, "reason": vs[i].reason} for i in unmatched],
            "unflagged_errors": sum(vs[i].status == "verified" for i in unmatched),
            "uncovered_numbers": [t["text"] for t in res.uncovered],
            "wall_s": wall, "wall_from": "this run" if fresh and len(fresh) == len(res.calls) else
            "recorded call latencies (max; not end-to-end)",
            "call_latency_p50_s": round(statistics.median(lat), 2) if lat else None,
            "call_latency_max_s": round(max(lat), 2) if lat else None,
            "tokens": {k: sum((u or {}).get(k, 0) for u in usage) for k in
                       ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")},
            "cost_per_check_usd": cost_per_check, "spent_usd": res.cost_usd,
            "gpu_cost_estimate_usd": round(wall * H100_USD_PER_S, 4) if b.self_hosted and wall else None,
        }

    # --- dev subset: seeded errors through the real verifier ------------------------------------------
    results: dict[str, dict] = {}
    raw_totals: Counter = Counter()
    lat_dev: list[float] = []
    skipped: list[str] = []
    skip_reasons: dict[str, str] = {}
    dev_spent = 0.0

    def work(it: dict, work_client: Any) -> tuple[dict, ex.ExtractResult]:
        ctx = ex.infer_context(it["text"]) if it["context"] is None else (it["context"].strip() or None)
        return it, ex.extract_detailed(it["text"], ctx if ctx is not None else "", client=work_client,
                                       cache_dir=cache_dir, backend=b)

    queue = []
    for it in dev_items:
        if reuse_shared_cache:
            seed_from_shared_cache(it["text"], it["context"], b, cache_dir)
        queue.append(it)
    with ThreadPoolExecutor(max_workers=DEV_CONCURRENCY) as pool:
        inflight: dict[Future, tuple[dict, float]] = {}
        while queue or inflight:
            while queue and len(inflight) < DEV_CONCURRENCY:
                it = queue[0]
                need = ex.passages_to_call(it["text"], it["context"], cache_dirs=[cache_dir], backend=b)
                cost = need * est_passage
                reserved = sum(r for _, r in inflight.values())
                problem = blocked(need, cost, reserved)
                if problem is None:
                    try:
                        work_client = client_for(need)
                    except ex.ExtractionError as e:
                        problem = setup_problem = str(e)
                if problem:
                    skipped.append(it["item_id"])
                    skip_reasons[it["item_id"]] = problem
                    queue.pop(0)
                    continue
                queue.pop(0)
                inflight[pool.submit(work, it, work_client)] = (it, cost)
            if not inflight:
                break
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in done:
                it, _ = inflight.pop(fut)
                try:
                    _, res = fut.result()
                except ex.ExtractionError as e:
                    results[it["item_id"]] = {"pairs": [], "error": str(e)[:200], "latency_s": None}
                    continue
                budget.spent += res.cost_usd
                dev_spent += res.cost_usd
                ctx = res.context
                vs = verdicts(res.claims, store, ev._as_of(it), ctx)
                results[it["item_id"]] = {"pairs": [[c.model_dump(mode="json"), v.model_dump(mode="json")]
                                                    for c, v in zip(res.claims, vs)],
                                          "error": None, "latency_s": res.wall_s, "cached": not res.calls or
                                          all(c.get("cached") for c in res.calls)}
                raw = raw_report(res, it["text"], b, cache_dir)
                raw_totals.update({"claims": raw["claims"], "wrong": raw["wrong"],
                                   "verbatim_spans": raw["verbatim_spans"],
                                   "schema_rejected": raw["schema_rejected"], "failed_calls": raw["failed_calls"]})
                raw_totals.update({f"reason:{k}": v for k, v in raw["by_reason"].items()})
                lat_dev.extend(call_latencies(res, cache_dir))
    ran = [it for it in dev_items if it["item_id"] in results]
    summary = ev.score(ran, results)
    mut = Counter()
    for m in ev.MUTATORS:
        mut.update(summary["per_mutator"].get(m, {}))
    orig = Counter(summary["originals"])
    ctrl = Counter(summary["controls"])
    out["dev"] = {
        "items": len(dev_items), "ran": len(ran), "skipped_budget": skipped, "skip_reasons": skip_reasons,
        "errors": sum(1 for r in results.values() if r.get("error")),
        "raw": dict(raw_totals),
        "mutants": mut["n"], "caught": mut["caught"], "reason_ok": mut["reason_ok"],
        "missed_marked_verified": mut["target_verified"], "missed_unverifiable": mut["target_unverifiable"],
        "target_not_extracted": mut["not_extracted"],
        "originals": orig["n"], "false_alarm_items": orig["false_alarm_items"],
        "controls": ctrl["n"], "controls_all_unverifiable": ctrl["fl_all_unverifiable"],
        "call_latency_p50_s": round(statistics.median(lat_dev), 2) if lat_dev else None,
        "spent_usd": round(dev_spent, 6),
        "rows": summary["rows"],
    }
    out["spent_usd"] = round((out.get("gold") or {}).get("spent_usd", 0.0) + dev_spent, 6)
    if out["gold"].get("skipped") and not ran:
        out["skipped"] = out["gold"]["skipped"]
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def pct(a: Optional[float], b: Optional[float]) -> str:
    return f"{100.0 * a / b:.0f}%" if a is not None and b else "–"


def raw_cell(wrong: int, claims: int, reasons: dict[str, int]) -> str:
    why = ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
    return f"{wrong}/{claims} ({pct(wrong, claims)})" + (f": {why}" if why else "")


def row(r: dict[str, Any]) -> str:
    if r.get("skipped"):
        return f"| {r['label']} | not run yet (below) |" + " |" * 8
    g, d = r.get("gold") or {}, r.get("dev") or {}
    if g.get("skipped") or not g:
        gold_cells = ["–"] * 5
        lat = cost = "–"
    else:
        raw = g["raw"]
        gold_cells = [raw_cell(raw["wrong"], raw["claims"], raw["by_reason"]),
                      f"{g['raw_label_errors']}",
                      f"{pct(g['strict_matched'], 27)} / {pct(g['strict_matched'], g['shipped_claims'])}",
                      f"{raw['verbatim_spans']}/{raw['claims']}",
                      f"**{g['unflagged_errors']}** of {g['shipped_claims']}"]
        lat = (f"{g['wall_s']:.1f} s" + (" (recorded max)" if g.get("wall_from") != "this run" else " (live)")
               if g.get("wall_s") else "–")
        cost = (f"${g['cost_per_check_usd']:.3f}" if not r["self_hosted"] else
                f"GPU ≈ ${g['gpu_cost_estimate_usd']:.3f}" if g.get("gpu_cost_estimate_usd") else "GPU time")
    dev_raw = d.get("raw") or {}
    dev_reasons = {k.split(":", 1)[1]: v for k, v in dev_raw.items() if k.startswith("reason:")}
    dev_cells = (raw_cell(dev_raw.get("wrong", 0), dev_raw.get("claims", 0), dev_reasons),
                 f"{d.get('caught', 0)}/{d.get('mutants', 0)} caught · {d.get('missed_marked_verified', 0)} "
                 f"marked ✓ · {d.get('false_alarm_items', 0)}/{d.get('originals', 0)} false alarms"
                 f" · {d.get('ran', 0)}/{d.get('items', 0)} items"
                 + (f" · {d['errors']} failed" if d.get("errors") else "")
                 ) if d.get("ran") else ("–", "–")
    return f"| {r['label']} | " + " | ".join([*gold_cells, *dev_cells, lat, cost]) + " |"


HEADER = ("| Model | Raw claims wrong as returned (gold) | Label errors (gold) | Strict recall / precision (27 gold) "
          "| Verbatim spans | Unflagged errors after Receipts (gold) | Raw claims wrong as returned (dev) "
          "| Verifier on dev seeded errors | Gold check latency | Cost per gold check |\n"
          "|---|---|---|---|---|---|---|---|---|---|")


def write_markdown(latest: list[dict[str, Any]]) -> str:
    order = {name: i for i, name in enumerate(TARGETS)}
    latest = sorted(latest, key=lambda r: order.get(r["target"], 99))
    lines = [
        "# Model benchmark: raw extraction vs what Receipts ships",
        "",
        f"Generated by `eval/bench_models.py` on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Each row is that model's latest run. Definitions: `docs/open-weights.md` (Benchmark).",
        "",
        "- **Gold:** NVIDIA's Q2 FY2027 press release, 10 passages, 27 draft hand-labeled claims "
        "(independent of model outputs; awaiting human review).",
        f"- **Dev:** {DEV_ITEMS} fixed items from the seeded-error corpus (6 real sentences, one injected error per "
        "mutator, 1 guidance control), checked against SEC filings by `receipts.verify`.",
        "- **Raw claims wrong as returned:** claims in the model's own JSON whose number is not what the text says: "
        "`invented` (the quoted number is not in the passage), or `value`, `scale` or `unit` disagreeing with it "
        "(for example \"18%\" typed as USD). Receipts' code re-reads all three from the quoted text and drops "
        "invented claims.",
        "  This measures extraction consistency with the input, not factual accuracy of unrestricted model answers.",
        "- **Label errors:** gold claims whose number the model found but labeled with the wrong metric, "
        "GAAP/non-GAAP basis or period.",
        "- **Unflagged errors:** shipped claims that match no gold claim yet came back ✓ verified. Target: 0.",
        "",
        HEADER,
        *(row(r) for r in latest),
        "",
        "Latency is the wall time of the whole gold check (10 passages in parallel, as the product runs them); a "
        "re-scored run reports the slowest recorded call instead. Cost per gold check is token usage at the "
        "provider's list prices. Self-hosted cost is GPU time (Modal H100 "
        f"${H100_USD_PER_S * 3600:.2f}/h) for that wall time, excluding cold start, idle time, CPU and RAM. "
        "It is an estimate, not measured provider billing. Cached rescoring adds $0 model spend.",
        "",
        "`--offline` and `--budget 0` make no server requests. Live self-hosted runs require "
        "`--allow-self-hosted`; the token budget does not limit GPU charges.",
    ]
    for r in latest:
        if r.get("skipped"):
            continue
        g, d = r.get("gold") or {}, r.get("dev") or {}
        if g.get("skipped"):
            lines.append(f"- {r['label']}, gold not run: {g['skipped']}.")
        if g.get("failed_passages"):
            lines.append(f"- {r['label']}: {g['failed_passages']}/{g['passages']} gold passages failed; "
                         "the reported extraction metrics include missing gold claims.")
        if d.get("skip_reasons"):
            reasons = "; ".join(sorted(set(d["skip_reasons"].values())))
            lines.append(f"- {r['label']}: {len(d['skip_reasons'])} dev items skipped: {reasons}.")
    notes = [f"- {r['label']}: {r['skipped'].rstrip('.')}." for r in latest if r.get("skipped")]
    if notes:
        lines += ["", "Not run yet (setup: `docs/open-weights.md`):", *notes]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", default=",".join(TARGETS), help=f"comma-separated, from: {', '.join(TARGETS)}")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD,
                    help="USD guard based on token-cost estimates, not a hard billing cap; 0 forbids server requests")
    ap.add_argument("--offline", action="store_true", help="re-score cached responses only; make no calls")
    ap.add_argument("--fresh", action="store_true", help="ignore cached responses (new cache folder) to re-measure")
    ap.add_argument("--allow-self-hosted", action="store_true",
                    help="allow self-hosted server requests and GPU billing outside the token budget")
    ap.add_argument("--warmup-timeout", type=float, default=900.0, help="seconds to wait for a cold self-hosted server")
    ap.add_argument("--list", action="store_true", help="show the targets and whether each is configured")
    ap.add_argument("--report-only", action="store_true", help="rebuild eval/bench_results.md from the latest runs")
    args = ap.parse_args(argv)
    if not math.isfinite(args.budget) or args.budget < 0:
        ap.error("--budget must be finite and nonnegative")

    names = [n.strip() for n in args.targets.split(",") if n.strip()]
    unknown = [n for n in names if n not in TARGETS]
    if unknown:
        raise SystemExit(f"unknown target(s) {unknown}; choose from {', '.join(TARGETS)}")
    if args.list:
        for n in TARGETS:
            b = make_target(n)
            print(f"{n:<22} {TARGETS[n][2]:<55} {missing_setup(b) or 'ready'}")
        return 0
    if args.report_only:
        latest = [json.loads(p.read_text()) for p in sorted((OUT_DIR / "latest").glob("*.json"))]
        RESULTS_MD.write_text(write_markdown(latest), encoding="utf-8")
        print(RESULTS_MD.read_text(encoding="utf-8"))
        return 0

    from receipts.store import Store

    store = Store()
    ev = load_eval_module()
    by_id = {it["item_id"]: it for it in ev.load_split("dev")}
    missing = [i for i in DEV_ITEM_IDS if i not in by_id]
    if missing:
        raise SystemExit(f"dev items missing from eval/corpus/v0_dev.jsonl: {missing}")
    dev_items = [by_id[i] for i in DEV_ITEM_IDS]
    gold = load_gold()
    assert len(gold) == 27, f"expected 27 gold claims, found {len(gold)}"
    cache_dir = BENCH_CACHE / (datetime.now(timezone.utc).strftime("fresh-%Y%m%dT%H%M%SZ") if args.fresh else "")
    budget = Budget(args.budget)
    runs = []
    for n in names:
        print(f"== {n}: {TARGETS[n][2]}", flush=True)
        r = run_target(n, budget=budget, store=store, ev=ev, dev_items=dev_items, gold=gold, cache_dir=cache_dir,
                       offline=args.offline, warmup_timeout_s=args.warmup_timeout,
                       allow_self_hosted=args.allow_self_hosted, reuse_shared_cache=not args.fresh)
        print(f"   {r.get('skipped') or 'done'}; spent so far ${budget.spent:.4f}", flush=True)
        runs.append(r)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (OUT_DIR / "latest").mkdir(parents=True, exist_ok=True)
    record = {"ran_at": stamp, "budget_usd": args.budget, "spent_usd": round(budget.spent, 6), "runs": runs,
              "allow_self_hosted": args.allow_self_hosted, "offline": args.offline,
              "dev_item_ids": [it["item_id"] for it in dev_items]}
    (OUT_DIR / f"{stamp}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str))
    for r in runs:
        latest_path = OUT_DIR / "latest" / f"{r['target']}.json"
        if r.get("skipped") and latest_path.exists():
            continue  # keep the last real result for a target that couldn't run this time
        latest_path.write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str))
    latest = [json.loads(p.read_text()) for p in sorted((OUT_DIR / "latest").glob("*.json"))]
    md = write_markdown(latest)
    RESULTS_MD.write_text(md, encoding="utf-8")
    print()
    print(md)
    print(f"this run spent ${budget.spent:.4f} of ${args.budget:.2f}; full record: "
          f"{(OUT_DIR / f'{stamp}.json').relative_to(ROOT)}; table: {RESULTS_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
