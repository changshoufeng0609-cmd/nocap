"""Fixed 50-item dev comparison; fresh Baseten is opt-in and strictly metered.

Prepare: python eval/expanded_dev.py --prepare
Run:     python eval/expanded_dev.py --live-baseten --run-id <unique-id>
Replay:  python eval/expanded_dev.py --replay <record.json> --output <new.json>
Rescore: python eval/expanded_dev.py --score-record <record.json> --output <new.json>
No held-out split, corpus mutation, extra gold calls, or original benchmark writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    # Direct execution otherwise resolves `eval` to sibling eval.py instead of
    # the repository's namespace package.
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import bench_models as bench
from eval.attempt_budget import AttemptBudget
from receipts import extract as ex
from receipts.store import Store

MANIFEST = ROOT / "eval/expanded_dev_manifest.json"
CORPUS = ROOT / "eval/corpus/v0_dev.jsonl"
SEED = 20261029
BUCKETS = (("none", 24), ("value", 4), ("period", 4), ("scale", 4),
           ("basis", 4), ("entity", 4), ("look_ahead", 4), ("forward_looking_control", 2))
TARGETS = ("spark", "spark-open-baseten")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_digest(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def save_new(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def manifest() -> dict[str, Any]:
    all_items = [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]
    if any(it["split"] != "dev" for it in all_items):
        raise ValueError("Only dev items may enter this benchmark.")
    rng, items = random.Random(SEED), []
    for bucket, count in BUCKETS:
        pool = sorted((r for r in all_items if r["mutator"] == bucket), key=lambda r: r["item_id"])
        items.extend(rng.sample(pool, count))
    selected = [{"item_id": it["item_id"], "item_hash": it["item_hash"],
                 "input_sha256": json_digest(it), "mutator": it["mutator"], "ticker": it["ticker"],
                 "requests": {target: ex.request_keys(it["text"], it["context"], backend=bench.make_target(target))
                              for target in TARGETS}} for it in items]
    return {"version": 1, "split": "dev", "corpus_sha256": digest(CORPUS.read_bytes()), "seed": SEED,
            "algorithm": "Python Random(seed); ordered bucket iteration; each pool sorted by item_id; sample without replacement",
            "bucket_counts": dict(BUCKETS), "available_counts": dict(Counter(it["mutator"] for it in all_items)),
            "ticker_counts": dict(Counter(it["ticker"] for it in items)),
            "overlap_with_original_13": sorted(set(bench.DEV_ITEM_IDS) & {it["item_id"] for it in items}),
            "items": selected}


def load_items():
    fixed = json.loads(MANIFEST.read_text())
    if fixed != manifest():
        raise ValueError("Corpus, request bytes or fixed selection changed; refuse a silent new benchmark.")
    by_id = {it["item_id"]: it for it in (json.loads(line) for line in CORPUS.read_text().splitlines() if line)}
    return fixed, [by_id[row["item_id"]] for row in fixed["items"]]


def source_hashes():
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in
            [ROOT / "receipts" / name for name in
             ("extract.py", "verify.py", "periods.py", "store.py", "stream.py", "types.py", "numeric.py")]
            if p.exists()}


def evidence_hashes(folder):
    paths = [folder / "facts.sqlite", *sorted((folder / "pr_facts").rglob("*.jsonl"))]
    return {str(p.relative_to(folder)): digest(p.read_bytes()) for p in paths}


def run_target(name, items, cache_dir, evidence_dir, *, live=False):
    backend = bench.make_target(name)
    # A cache miss cannot instantiate a provider client in offline/replay mode.
    client = ex.make_client(backend=backend) if live else bench.CACHE_ONLY_CLIENT
    ev = bench.load_eval_module()
    store = Store(evidence_dir / "facts.sqlite", evidence_dir / "pr_facts")

    def work(it):
        try:
            result = ex.extract_detailed(it["text"], it["context"], client=client, cache_dir=cache_dir, backend=backend)
            verdicts = bench.verdicts(result.claims, store, ev._as_of(it), result.context)
            rec = {"pairs": [[c.model_dump(mode="json"), v.model_dump(mode="json")]
                              for c, v in zip(result.claims, verdicts)],
                   "error": None, "latency_s": result.wall_s,
                   "cached": all(c.get("cached") for c in result.calls), "calls": result.calls}
        except ex.ExtractionError as error:
            rec = {"pairs": [], "error": type(error).__name__, "latency_s": None, "cached": False,
                   "calls": [{k: v for k, v in call.items() if k not in ("error", "problem")}
                             for call in error.calls]}
        return it["item_id"], rec

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = dict(pool.map(work, items))
    finally:
        store.close()
        if live:
            client.close()
    score = ev.score(items, records)
    calls = [call for row in records.values() for call in row["calls"]]
    cache_rows = []
    for key in sorted({call["request_sha256"] for call in calls}):
        path = cache_dir / f"{key}.json"
        if path.exists():
            cache = json.loads(path.read_text())
            cache_rows.append({"request_sha256": key, "cache_sha256": digest(path.read_bytes()),
                               "created_utc": cache.get("created_utc"), "usage": cache.get("usage"),
                               "latency_s": cache.get("latency_s")})
    lat = [c["latency_s"] for c in cache_rows if c["latency_s"] is not None]
    return {"target": name, "model": backend.model, "backend": backend.name, "base_url": backend.base_url,
            "mode": "fresh application cache" if live else "existing cache only; zero model calls",
            "cache_dir": str(cache_dir.relative_to(ROOT)), "score": score, "records": records,
            "response_cache_records": cache_rows, "recorded_call_p50_s": statistics.median(lat) if lat else None,
            "application_cache_hits": sum(c.get("cached", False) for c in calls),
            "application_cache_misses": sum(not c.get("cached", False) for c in calls)}


def quality(score):
    return {**{k: v for k, v in score.items() if k != "rows"},
            "rows": [{k: v for k, v in row.items() if k not in ("latency_s", "cached")} for row in score["rows"]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--live-baseten", action="store_true")
    modes.add_argument("--replay", type=Path)
    modes.add_argument("--score-record", type=Path)
    ap.add_argument("--run-id")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)
    if args.prepare:
        save_new(MANIFEST, manifest())
        print("Pinned 50 dev IDs and input/request hashes; no API calls.")
        return 0
    fixed, items = load_items()
    if args.score_record:
        if args.output is None:
            ap.error("--score-record requires a new --output path")
        old = json.loads(args.score_record.read_text())
        if old["manifest_sha256"] != digest(MANIFEST.read_bytes()):
            raise ValueError("Record does not use the fixed manifest.")
        ev = bench.load_eval_module()
        scores = [{"target": run["target"], "score": ev.score(items, run["records"])} for run in old["runs"]]
        matches = all(quality(a["score"]) == quality(b["score"]) for a, b in zip(scores, old["runs"]))
        save_new(args.output, {"source_record_sha256": digest(args.score_record.read_bytes()),
                              "quality_matches": matches, "new_model_calls": 0,
                              "mode": "rescore committed claims/verdicts; no raw response cache or evidence required",
                              "runs": scores})
        print(json.dumps({"quality_matches": matches, "new_model_calls": 0}))
        return 0 if matches else 1
    if args.replay:
        if args.output is None:
            ap.error("--replay requires a new --output path")
        old = json.loads(args.replay.read_text())
        if old["manifest_sha256"] != digest(MANIFEST.read_bytes()):
            raise ValueError("Record does not use the fixed manifest.")
        folder = ROOT / old["local_run_dir"]
        if evidence_hashes(folder / "evidence") != old["evidence_hashes"]:
            raise ValueError("Frozen evidence changed.")
        runs = [run_target(r["target"], items, ROOT / r["cache_dir"], folder / "evidence") for r in old["runs"]]
        matches = all(quality(a["score"]) == quality(b["score"]) for a, b in zip(runs, old["runs"]))
        save_new(args.output, {"source_record_sha256": digest(args.replay.read_bytes()),
                              "quality_matches": matches, "new_model_calls": 0,
                              "runs": [{"target": r["target"], "score": r["score"],
                                        "application_cache_hits": r["application_cache_hits"],
                                        "application_cache_misses": r["application_cache_misses"]} for r in runs]})
        print(json.dumps({"quality_matches": matches, "new_model_calls": 0}))
        return 0 if matches else 1
    if not args.run_id or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.run_id):
        ap.error("--live-baseten requires a unique safe --run-id")
    folder = ROOT / "data/eval_runs" / args.run_id
    folder.mkdir(exist_ok=False)
    output = args.output or ROOT / "eval/expanded_dev_runs" / f"{args.run_id}.json"
    if output.exists():
        raise FileExistsError("Refusing to overwrite results.")
    evidence = folder / "evidence"
    evidence.mkdir()
    shutil.copy2(ROOT / "data/facts.sqlite", evidence / "facts.sqlite")
    shutil.copytree(ROOT / "data/pr_facts", evidence / "pr_facts")
    meta_cache, base_cache = folder / "spark", folder / "baseten"
    meta_cache.mkdir(); base_cache.mkdir()
    for row in fixed["items"]:
        for key in row["requests"]["spark"]:
            shutil.copy2(ex.CACHE_DIR / f"{key}.json", meta_cache / f"{key}.json")
    started, hashes = datetime.now(timezone.utc).isoformat(), source_hashes()
    # Finish the cache-only comparison before even constructing a Baseten client.
    spark = run_target("spark", items, meta_cache, evidence)
    if spark["application_cache_misses"]:
        raise ValueError("Meta cache incomplete; no paid calls permitted.")
    backend = bench.make_target("spark-open-baseten")
    guard = AttemptBudget(folder / "attempts.json", backend=backend,
                          allowed_keys={k for row in fixed["items"] for k in row["requests"]["spark-open-baseten"]})
    with guard.installed():
        baseten = run_target("spark-open-baseten", items, base_cache, evidence, live=True)
    result = {"purpose": "Fixed expanded dev comparison; provisional labels; not held-out accuracy",
              "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
              "manifest_sha256": digest(MANIFEST.read_bytes()), "local_run_dir": str(folder.relative_to(ROOT)),
              "source_hashes": hashes, "source_hashes_after": source_hashes(),
              "evidence_hashes": evidence_hashes(evidence), "metering": guard.summary(),
              "runs": [spark, baseten]}
    save_new(output, result)
    print(json.dumps({"output": str(output.relative_to(ROOT)), "metering": {k: v for k, v in guard.summary().items() if k != "attempts"}}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Unexpected SDK/configuration failures must not print request or secret-bearing text.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
