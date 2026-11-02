#!/usr/bin/env python
"""One-shot frozen spot500 evaluation; oracle mode tests scoring plumbing only.

.venv/bin/python eval/spot500.py --stub oracle --output /tmp/spot500-oracle.json
.venv/bin/python eval/spot500.py --live --output eval/spot500_runs/live-20261101.json
.venv/bin/python eval/spot500.py --set v2 --stub oracle --output eval/spot500_runs/v2-oracle-<date>.json
.venv/bin/python eval/spot500.py --set v2 --live --output eval/spot500_runs/v2-live-<date>.json
.venv/bin/python eval/spot500.py --set v3 --stub oracle --output eval/spot500_runs/v3-oracle-<date>.json
.venv/bin/python eval/spot500.py --set v3 --live --output eval/spot500_runs/v3-live-<date>.json

Live mode requires MODEL_API_KEY already exported; this runner never reads .env.
Each output and its .run directory are single use, including interrupted runs.
v1 is the default set. Later sets write only eval/spot500_runs/<set>-oracle-*.json
or <set>-live-*.json, and refuse a live run once any <set>-live-* record exists.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import eval as harness

CORPUS = ROOT / "eval/corpus/spot500_v1.jsonl"
MANIFEST = ROOT / "eval/spot500_manifest.json"
V2_CORPUS = ROOT / "eval/corpus/spot500_v2.jsonl"
V2_MANIFEST = ROOT / "eval/spot500_v2_manifest.json"
V3_CORPUS = ROOT / "eval/corpus/spot500_v3.jsonl"
V3_MANIFEST = ROOT / "eval/spot500_v3_manifest.json"
RUNS = ROOT / "eval/spot500_runs"
SETS = ("v1", "v2", "v3")
DEFAULT_BUDGET = 0.95


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_digest(obj: Any) -> str:
    return digest(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def file_digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def save(path: Path, obj: Any, *, new: bool = False) -> None:
    with path.open("x" if new else "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def set_files(spot_set: str = "v1") -> tuple[Path, Path]:
    """A set's frozen corpus and manifest; v1 reads the module paths at call time."""
    if spot_set == "v1":
        return CORPUS, MANIFEST
    if spot_set == "v2":
        return V2_CORPUS, V2_MANIFEST
    if spot_set == "v3":
        return V3_CORPUS, V3_MANIFEST
    raise ValueError(f"Unknown spot500 set: {spot_set}")


def load_items(spot_set: str = "v1") -> tuple[dict, list[dict]]:
    corpus, manifest_path = set_files(spot_set)
    manifest = json.loads(manifest_path.read_text())
    raw = corpus.read_bytes()
    if digest(raw) != manifest["corpus_sha256"]:
        raise ValueError("Frozen spot500 corpus hash changed.")
    items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    expected = manifest["items"]
    actual = [{"item_id": item["item_id"], "sha256": json_digest(item)} for item in items]
    if actual != [{"item_id": row["item_id"], "sha256": row["sha256"]} for row in expected]:
        raise ValueError("Frozen spot500 item order or hashes changed.")
    if not items or len({it["item_id"] for it in items}) != len(items):
        raise ValueError("Empty corpus or duplicate item IDs.")
    for item in items:
        if not item.get("gold_claims"):
            raise ValueError("Every item must label its numeric claims.")
        for claim in item["gold_claims"]:
            start, end = claim["span"]
            if item["text"][start:end] != claim["value_text"]:
                raise ValueError("Gold value does not match its frozen span.")
    if spot_set != "v1":
        # A later set must never run on another set's frozen files.
        version = f"spot500_{spot_set}"
        if manifest.get("version") != version or any(item.get("corpus_version") != version for item in items):
            raise ValueError(f"Frozen files are not labelled {version}.")
    return manifest, items


def _matching(pairs: list, gold: dict, used: set[int], *, require_basis: bool = True) -> list[int]:
    """Match the frozen number at its exact occurrence, never a remote equal value."""
    start, end = gold["span"]
    want = harness.norm_value_text(gold["value_text"])
    hits = []
    for index, (claim, _) in enumerate(pairs):
        if index in used or harness.norm_value_text(claim.get("value_text", "")) != want:
            continue
        left, right = claim.get("span_start"), claim.get("span_end")
        if not (isinstance(left, int) and isinstance(right, int) and left <= start and end <= right):
            continue
        if require_basis and gold.get("basis") and claim.get("basis") != gold["basis"]:
            continue
        hits.append(index)
    return hits


def score(items: list[dict], records: dict[str, dict]) -> dict:
    total, by_mutator, by_feature = Counter(), defaultdict(Counter), defaultdict(Counter)
    rows = []
    for item in items:
        rec = records.get(item["item_id"], {})
        counts = Counter(items=1, gold_claims=len(item["gold_claims"]))
        pairs = rec.get("pairs", [])
        counts["errors"] = int(bool(rec.get("error")))
        counts["skipped"] = int(bool(rec.get("skipped")))
        counts["missing_records"] = int(not rec)
        used, details = set(), []
        is_original = item["mutator"] in ("none", "original")
        if is_original:
            counts["original_items"] += 1
            counts["false_alarm_original_items"] += int(any(v.get("status") == "wrong" for _, v in pairs))
        if item["mutator"] == "forward_looking_control":
            counts["guidance_items"] += 1
            counts["verified_guidance_items"] += int(any(v.get("status") == "verified" for _, v in pairs))
        for gold in item["gold_claims"]:
            role = gold["role"]
            counts[f"{role}_claims"] += 1
            hits = _matching(pairs, gold, used)
            occurrence_hits = _matching(pairs, gold, set(), require_basis=False)
            verdict = pairs[hits[0]][1] if hits else {}
            if hits:
                used.add(hits[0])
            else:
                counts["missing_claims"] += 1
                counts[f"missing_{role}"] += 1
            status, reason = verdict.get("status"), verdict.get("reason")
            # A contradictory duplicate must never turn an incorrect extraction
            # into a caught error simply because its first verdict was correct.
            status_ok = bool(hits) and all(pairs[i][1].get("status") == gold["expected_status"] for i in occurrence_hits)
            reason_ok = status_ok and all(pairs[i][1].get("reason") == gold["expected_reason"] for i in hits)
            counts["duplicate_matches"] += max(0, len(hits) - 1)
            counts["conflicting_matches"] += int(len({(pairs[i][1].get("status"), pairs[i][1].get("reason"))
                                                        for i in hits}) > 1)
            counts["status_correct"] += int(status_ok)
            counts["status_and_reason_correct"] += int(reason_ok)
            if role == "target":
                counts["caught_errors"] += int(status_ok)
                counts["expected_reasons"] += int(reason_ok)
                counts["target_verified"] += int(any(pairs[i][1].get("status") == "verified" for i in occurrence_hits))
                counts["target_unverifiable"] += int(any(pairs[i][1].get("status") == "unverifiable" for i in occurrence_hits))
            if role == "true":
                observed = {pairs[i][1].get("status") for i in occurrence_hits}
                counts["false_alarm_true_claims"] += int("wrong" in observed)
                counts["unverifiable_true_claims"] += int("unverifiable" in observed)
                counts["verified_true_claims"] += int("verified" in observed)
                if is_original:
                    counts["original_true_claims"] += 1
                    counts["original_true_unverifiable"] += int("unverifiable" in observed)
                    counts["original_true_wrong"] += int("wrong" in observed)
            if role == "guidance":
                counts["verified_guidance"] += int(any(pairs[i][1].get("status") == "verified" for i in occurrence_hits))
                counts["unverifiable_guidance"] += int(status == "unverifiable")
                counts["guidance_expected_reason"] += int(reason_ok)
            details.append({"gold": gold, "matched_index": hits[0] if hits else None,
                            "matching_indices": hits,
                            "status": status, "reason": reason, "correct": reason_ok})
        counts["extra_claims"] += len(pairs) - len(used)
        counts["extra_wrong"] += sum(v.get("status") == "wrong" for i, (_, v) in enumerate(pairs) if i not in used)
        total.update(counts)
        by_mutator[item["mutator"]].update(counts)
        for feature in item["features"]:
            by_feature[feature].update(counts)
        rows.append({"item_id": item["item_id"], "counts": dict(counts), "claims": details})
    return {"total": dict(total), "by_mutator": {k: dict(v) for k, v in sorted(by_mutator.items())},
            "by_feature": {k: dict(v) for k, v in sorted(by_feature.items())}, "rows": rows}


def oracle_pairs(item: dict) -> list:
    return [[{"id": f"g{i}", "value_text": gold["value_text"], "span_start": gold["span"][0],
              "span_end": gold["span"][1], "basis": gold.get("basis"), "metric": gold.get("metric", "other")},
             {"claim_id": f"g{i}", "status": gold["expected_status"], "reason": gold["expected_reason"],
              "explanation": "Oracle gold-label stub; not a verifier result."}]
            for i, gold in enumerate(item["gold_claims"])]


class RetainedRecords:
    """Never replay results; retain each finished item before the next dispatch."""
    def __init__(self, path: Path, by_hash: dict[str, dict], details: dict):
        self.path, self.by_hash, self.details = path, by_hash, details
        path.touch(exist_ok=False)

    def get(self, _key):
        return None

    def put(self, key, record):
        item = self.by_hash[key]
        record.update(self.details.get(key, {}))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"item_id": item["item_id"], **record}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def source_hashes() -> dict:
    paths = [*sorted((ROOT / "receipts").glob("*.py")), Path(__file__), ROOT / "eval/eval.py",
             ROOT / "eval/attempt_budget.py"]
    return {str(path.relative_to(ROOT)): file_digest(path) for path in paths}


def evidence_hashes(folder: Path) -> dict:
    paths = [folder / "facts.sqlite", *sorted((folder / "pr_facts").rglob("*.jsonl"))]
    return {str(path.relative_to(folder)): file_digest(path) for path in paths}


def spark_guard(path: Path, backend, allowed_keys: set[str], cap: float):
    """Reuse existing attempt ledger persistence and context manager with a Meta policy."""
    from eval.attempt_budget import AttemptBudget
    from receipts import extract as ex

    class SparkAttemptBudget(AttemptBudget):
        def __init__(self):
            if not math.isfinite(cap) or not 0 < cap < 1:
                raise ValueError("Spot500 admission cap must be below $1.")
            if path.exists():
                raise FileExistsError("Refusing to overwrite attempt ledger.")
            self.path, self.backend, self.allowed_keys = path, backend, allowed_keys
            self.cap_usd, self.max_attempts = cap, len(allowed_keys)
            self.lock, self.rows = threading.RLock(), []
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.original = ex.RequestUsage.call
            self._save()

        def call(self, meter, fn, request):
            key = ex.request_key(request, meter.backend)
            if (meter.backend != backend or backend != ex.make_backend("meta")
                    or request.get("model") != "muse-spark-1.3"
                    or request.get("max_output_tokens") != ex.MAX_OUTPUT_TOKENS
                    or key not in allowed_keys):
                raise ex.ModelCallRejected("Only frozen spot500 Muse Spark requests are admitted.")
            estimate = ex.estimate_request_cost(request, backend)
            with self.lock:
                if (any(row["request_sha256"] == key for row in self.rows)
                        or len(self.rows) >= self.max_attempts
                        or sum(row["charge_usd"] for row in self.rows) + estimate > cap):
                    raise ex.ModelCallRejected("Spot500 budget or one-attempt limit reached.")
                row = {"attempt": len(self.rows) + 1, "request_sha256": key, "started": False,
                       "estimate_usd": estimate, "charge_usd": estimate, "cost_is_estimate": True,
                       "status": "reserved"}
                self.rows.append(row)
                self._save()

            def dispatch():
                with self.lock:
                    row.update(started=True, status="dispatched")
                    self._save()
                return fn()

            try:
                response = self.original(meter, dispatch, request)
            except BaseException as error:
                with self.lock:
                    row["status"] = type(error).__name__
                    if not row["started"]:
                        row.update(charge_usd=0, cost_is_estimate=False)
                    self._save()
                raise
            usage = ex._usage(response)
            with self.lock:
                row["status"] = "completed"
                if usage is not None:
                    row.update(usage=usage, charge_usd=ex.cost_usd(usage, backend.prices), cost_is_estimate=False)
                self._save()
            return response

    return SparkAttemptBudget()


def input_key(text, context, as_of):
    return text, context, as_of.isoformat() if as_of else None


def check_set_output(output: Path, spot_set: str, *, stub: bool) -> None:
    """A later set writes only single-use, set-named records under eval/spot500_runs/.

    Its live run happens once: any earlier <set>-live-* record, finished or
    interrupted, refuses another live attempt. Oracle records name their mode.
    """
    runs = RUNS.resolve()
    mode = "oracle" if stub else "live"
    prefix = f"{spot_set}-{mode}-"
    if output.parent != runs or not output.name.startswith(prefix) or output.suffix != ".json":
        raise ValueError(f"A {spot_set} {mode} output must be eval/spot500_runs/{prefix}<date>.json.")
    if not stub:
        earlier = sorted(path.name for path in runs.glob(f"{spot_set}-live-*"))
        if earlier:
            raise FileExistsError(f"{spot_set} was already run live ({earlier[0]}); its live run is single use.")


def run(items: list[dict], output: Path, *, stub: bool, evidence_dir: Path, budget: float,
        spot_set: str = "v1") -> dict:
    corpus, manifest = set_files(spot_set)
    if not math.isfinite(budget) or not 0 < budget < 1:
        raise ValueError("Budget must be positive and below $1.")
    estimate = len(items) * harness.DEFAULT_EST_COST_PER_ITEM
    if estimate >= 1 or estimate > budget:
        raise ValueError("Harness per-item estimate exceeds the authorized budget.")
    by_input = {input_key(it["text"], it["context"], harness._as_of(it)): it for it in items}
    if len(by_input) != len(items) or len({it["item_hash"] for it in items}) != len(items):
        raise ValueError("Duplicate input and cutoff cannot be scored separately.")
    output = output.resolve()
    if spot_set != "v1":
        check_set_output(output, spot_set, stub=stub)
    output.parent.mkdir(parents=True, exist_ok=True)
    folder = output.with_suffix(".run")
    if output.exists() or folder.exists():
        raise FileExistsError("Refusing reused output or run directory, including interrupted runs.")
    # Preflight without creating a client or loading dotenv.
    if not stub and not os.environ.get("MODEL_API_KEY", "").strip():
        raise ValueError("Export MODEL_API_KEY before --live; this runner never reads .env.")
    evidence_before = {} if stub else evidence_hashes(evidence_dir)
    code_before = source_hashes()
    folder.mkdir(exist_ok=False)
    started = {"status": "started", "mode": "stub:oracle" if stub else "live:meta:muse-spark-1.3",
               "started_at": datetime.now(timezone.utc).isoformat(), "corpus_sha256": file_digest(corpus),
               "manifest_sha256": file_digest(manifest), "items": [it["item_id"] for it in items],
               "estimated_cost_usd": estimate, "estimate_per_item_usd": harness.DEFAULT_EST_COST_PER_ITEM,
               "budget_usd": budget, "evidence_dir": str(evidence_dir), "source_hashes": code_before,
               "evidence_hashes": evidence_before}
    if spot_set != "v1":
        started["set"] = spot_set
    save(output, started, new=True)
    save(folder / "started.json", started, new=True)
    details = {}
    retained = RetainedRecords(folder / "records.jsonl", {it["item_hash"]: it for it in items}, details)
    guard = client = store = None
    ledger_before = harness.LEDGER.total()
    if stub:
        def check(text, *, context, as_of):
            return oracle_pairs(by_input[input_key(text, context, as_of)])
    else:
        from receipts import extract as ex
        from receipts.check import check_detailed
        from receipts.store import Store
        backend = ex.make_backend("meta")
        keys = {key for it in items for key in ex.request_keys(it["text"], it["context"], backend=backend)}
        guard = spark_guard(folder / "attempts.json", backend, keys, budget)
        if not harness.install_sdk_hooks():
            raise RuntimeError("SDK usage ledger unavailable; no model call permitted.")
        store = Store(evidence_dir / "facts.sqlite", evidence_dir / "pr_facts")
        client = ex.make_client(api_key=os.environ["MODEL_API_KEY"], backend=backend)
        cache_dir = folder / "spark_cache"
        cache_dir.mkdir()

        def check(text, *, context, as_of):
            item = by_input[input_key(text, context, as_of)]
            try:
                result = check_detailed(text, as_of=as_of, context=context, store=store,
                                        client=client, backend=backend, cache_dir=cache_dir,
                                        extra_cache_dirs=(), concurrency=1)
                details[item["item_hash"]] = {"calls": result.extraction.calls,
                                              "warnings": result.warnings, "coverage": result.coverage}
                return result.results
            except Exception as error:
                details[item["item_hash"]] = {"calls": [
                    {k: v for k, v in call.items() if k not in ("error", "problem")}
                    for call in getattr(error, "calls", [])]}
                # Do not let SDK exception messages leak credentials into run logs.
                raise RuntimeError(type(error).__name__) from None

    @contextmanager
    def admitted():
        if guard is None:
            yield
        else:
            with guard.installed():
                yield

    try:
        with admitted():
            records, stats = harness.run_items(check, items, retained, concurrency=1, budget=budget,
                                              est_per_item=harness.DEFAULT_EST_COST_PER_ITEM, progress=True)
    finally:
        if store is not None:
            store.close()
        if client is not None:
            client.close()
    # Include skipped records too; every selected item remains in all denominators.
    for item in items:
        if records[item["item_id"]].get("skipped"):
            retained.put(item["item_hash"], records[item["item_id"]])
    summary = score(items, records)
    metering = guard.summary() if guard else {"calls": 0, "charge_usd": 0.0, "estimated_calls": 0}
    final = {**started, "status": "completed", "finished_at": datetime.now(timezone.utc).isoformat(),
             "records": records, "summary": summary, "stats": vars(stats),
             "cost_usd": metering["charge_usd"], "cost_is_estimate": bool(metering["estimated_calls"]),
             "sdk_usage_cost_usd": 0.0 if stub else harness.LEDGER.total() - ledger_before,
             "metering": metering,
             "source_hashes_after": source_hashes(),
             "evidence_hashes_after": {} if stub else evidence_hashes(evidence_dir)}
    final["inputs_unchanged"] = (final["source_hashes_after"] == code_before
                                  and final["evidence_hashes_after"] == evidence_before
                                  and file_digest(corpus) == started["corpus_sha256"]
                                  and file_digest(manifest) == started["manifest_sha256"])
    temporary = folder / "finished.tmp"
    save(temporary, final, new=True)
    temporary.replace(output)
    return final


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--stub", choices=("oracle",))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "data/snapshot")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    parser.add_argument("--set", dest="spot_set", choices=SETS, default="v1",
                        help="frozen set to run (default v1); a later set writes eval/spot500_runs/<set>-*.json")
    args = parser.parse_args(argv)
    _, items = load_items(args.spot_set)
    result = run(items, args.output, stub=bool(args.stub), evidence_dir=args.evidence_dir, budget=args.budget,
                 spot_set=args.spot_set)
    total = result["summary"]["total"]
    print(json.dumps({"output": str(args.output), "total": total, "cost_usd": result["cost_usd"],
                      "estimated_cost_usd": result["estimated_cost_usd"], "inputs_unchanged": result["inputs_unchanged"]}))
    if args.stub and (total.get("status_and_reason_correct", 0) != total["gold_claims"]
                      or total.get("extra_claims", 0)):
        return 1
    return int(not result["inputs_unchanged"] or bool(total.get("errors") or total.get("skipped")))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
