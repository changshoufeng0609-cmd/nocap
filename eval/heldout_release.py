"""Freeze and run the owner-authorized held-out evaluation once, without tuning.

--freeze hashes source/evidence/corpus bytes without parsing held-out labels.
--run requires the exact frozen files and records both test corpora, including
errors/skips. Existing extraction responses may replay; result-cache reuse is off.
The $1.50 process-local estimate gate is not a provider billing ceiling.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
# Direct script execution otherwise resolves eval/eval.py as the top-level
# module instead of the repository namespace package. Module execution is unchanged.
if __package__ in (None, ""):
    sys.path = [entry for entry in sys.path if Path(entry).resolve() != ROOT / "eval"]
sys.path.insert(0, str(ROOT))
from eval import eval as harness
from receipts import extract as ex

FREEZE = ROOT / "eval/heldout_freeze.json"
OUT = ROOT / "eval/heldout_runs/release-20261029"
CAP_USD = 1.50
MAX_ATTEMPTS = 250
EVIDENCE = ROOT / "data/snapshot"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")


def source_hashes() -> dict[str, str]:
    paths = [*sorted((ROOT / "receipts").rglob("*.py")), ROOT / "eval/eval.py", Path(__file__),
             ROOT / "eval/corpus/v0_test.jsonl", ROOT / "eval/corpus/trap_v1.jsonl",
             *sorted((ROOT / "data/snapshot").rglob("*"))]
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths if p.is_file()}


def freeze() -> None:
    source = source_hashes()
    tracked_dirty = subprocess.check_output(["git", "diff", "--name-only", "HEAD", "--", *source], cwd=ROOT).decode()
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", *source], cwd=ROOT).decode()
    if tracked_dirty.strip() or untracked.strip():
        raise RuntimeError("Commit source and evidence before freezing the held-out evaluation.")
    save_new(FREEZE, {"frozen_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "authorization": "Owner explicitly selected remaining-work item 8 on 2026-10-29.",
        "policy": "Run v0+basis5 test and trap_v1 test once; retain all results; no result-driven code/prompt tuning.",
        "model": ex.MODEL, "backend": "meta", "cap_usd": CAP_USD,
        "evidence": "Committed public data/snapshot, explicitly bound; no ambient local evidence.",
        "max_attempts": MAX_ATTEMPTS, "file_sha256": source})
    print(json.dumps({"status": "frozen", "files": len(source), "path": str(FREEZE.relative_to(ROOT))}))


class HeldoutBudget:
    def __init__(self, path: Path, allowed_keys: set[str], cap_usd: float = CAP_USD):
        if not 0 < cap_usd <= CAP_USD:
            raise ValueError("Held-out allocation cannot exceed $1.50.")
        if path.exists():
            raise FileExistsError("Refusing to overwrite a held-out attempt ledger.")
        self.path, self.allowed_keys, self.cap_usd = path, frozenset(allowed_keys), cap_usd
        self.backend = ex.BACKENDS["meta"]
        self.original = ex.RequestUsage.call
        self.lock, self.rows = threading.RLock(), []
        self._save()

    def summary(self) -> dict:
        with self.lock:
            return {"cap_usd": self.cap_usd, "max_attempts": MAX_ATTEMPTS,
                "charge_usd": round(sum(r["charge_usd"] for r in self.rows), 8),
                "calls": sum(r["started"] for r in self.rows),
                "estimated_calls": sum(r["started"] and r["estimated"] for r in self.rows),
                "attempts": [dict(r) for r in self.rows]}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.summary(), indent=2) + "\n")
        tmp.replace(self.path)

    def call(self, meter, fn, request):
        key = ex.request_key(request, meter.backend)
        if (meter.backend != self.backend or request.get("model") != "muse-spark-1.3"
                or request.get("max_output_tokens") != ex.MAX_OUTPUT_TOKENS or key not in self.allowed_keys):
            raise ex.ModelCallRejected("Only the frozen held-out Meta requests are admitted.")
        estimate = ex.estimate_request_cost(request, self.backend)
        with self.lock:
            if len(self.rows) >= MAX_ATTEMPTS or sum(r["charge_usd"] for r in self.rows) + estimate > self.cap_usd:
                raise ex.ModelCallRejected("Held-out admission allocation reached.")
            row = {"attempt": len(self.rows) + 1, "request_sha256": key, "started": False,
                   "charge_usd": estimate, "estimate_usd": estimate, "estimated": True, "status": "reserved"}
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
                    row.update(charge_usd=0, estimated=False)
                self._save()
            raise
        usage = ex._usage(response)
        with self.lock:
            row["status"] = "completed"
            if usage is not None:
                row.update(usage=usage, charge_usd=ex.cost_usd(usage, self.backend.prices), estimated=False)
            self._save()
        return response

    @contextmanager
    def installed(self):
        if ex.RequestUsage.call is not self.original:
            raise RuntimeError("Another attempt guard is installed.")
        def guarded(meter, fn, request):
            return self.call(meter, fn, request)
        ex.RequestUsage.call = guarded
        try:
            yield self
        finally:
            ex.RequestUsage.call = self.original
            with self.lock:
                self._save()


def run() -> None:
    from receipts.check import check_detailed
    from receipts.store import Store
    frozen = json.loads(FREEZE.read_text())
    if source_hashes() != frozen["file_sha256"]:
        raise RuntimeError("Frozen source/evidence/corpus changed; the held-out run was not started.")
    if OUT.exists():
        raise FileExistsError("This held-out release run already exists; no silent rerun.")
    backend = ex.BACKENDS["meta"]
    os.environ["RECEIPTS_EXTRACT_BACKEND"] = "meta"
    # Only after the immutable freeze do we load labels to construct exact requests.
    datasets = {"v0": harness.load_split("test"), "trap_v1": harness.load_traps("test")}
    allowed = {key for items in datasets.values() for item in items
               for key in ex.request_keys(item["text"], item["context"], backend=backend)}
    OUT.mkdir(parents=True, exist_ok=False)
    # Snapshot only matching, schema-valid response-cache entries. Keep their bytes
    # and hashes separate from the source/evidence freeze and never read a mutable
    # ambient cache during evaluation. New responses go to this run's own directory.
    input_cache = OUT / "cache_input"
    input_cache.mkdir()
    cache_inventory = []
    for key in sorted(allowed):
        for base in (ex.CACHE_DIR, ROOT / "data/spark_cache"):
            path = base / f"{key}.json"
            if path.is_file() and ex._first_hit([ex._Cache(base)], key, backend) is not None:
                copied = input_cache / path.name
                copied.write_bytes(path.read_bytes())
                if ex._first_hit([ex._Cache(input_cache)], key, backend) is None:
                    raise RuntimeError("A response cache changed while being snapshotted; no paid work started.")
                cache_inventory.append({"request_sha256": key, "source": str(path.relative_to(ROOT)),
                                        "file_sha256": digest(copied.read_bytes())})
                break
    save_new(OUT / "cache_inputs.json", {"scope": "Exact-request model responses snapshotted at run start; distinct from the source/evidence freeze.",
                                        "entries": cache_inventory})
    save_new(OUT / "started.json", {"started_at": datetime.now(timezone.utc).isoformat(),
        "freeze_sha256": digest(FREEZE.read_bytes()), "source_commit": frozen["source_commit"],
        "item_counts": {k: len(v) for k, v in datasets.items()},
        "allowed_request_sha256": sorted(allowed), "result_cache_reused": False,
        "extraction_cache_policy": "Schema-valid exact request-key responses may replay; every hit is counted."})
    guard = HeldoutBudget(OUT / "attempts.json", allowed)
    extraction_rows, row_lock = [], threading.Lock()
    def check(text, *, as_of=None, context=None):
        store = Store(EVIDENCE / "facts.sqlite", EVIDENCE / "pr_facts")
        try:
            result = check_detailed(text, as_of=as_of, context=context, store=store,
                backend=backend, concurrency=2, cache_dir=OUT / "cache_new", extra_cache_dirs=(input_cache,))
        finally:
            store.close()
        calls = result.extraction.calls
        safe = [{k: c[k] for k in ("request_sha256", "status", "cached", "attempts", "usage", "model_calls", "cost_usd") if k in c} for c in calls]
        with row_lock:
            extraction_rows.append({"text_sha256": digest(text.encode()), "calls": safe})
            tmp = OUT / "extractions.tmp"
            tmp.write_text(json.dumps(extraction_rows, indent=2) + "\n")
            tmp.replace(OUT / "extractions.json")
        if any(c.get("status") != "ok" for c in calls):
            raise RuntimeError("Extraction failed; recorded as an evaluation error, not silently dropped.")
        return result.results
    original_check = harness.real_check
    harness.real_check = lambda: check
    try:
        with guard.installed():
            for corpus in ("v0", "trap_v1"):
                harness.main(["--corpus", corpus, "--split", "test", "--concurrency", "2",
                    "--budget", str(CAP_USD), "--no-cache", "--quiet", "--json-out", str(OUT / f"{corpus}.json")])
    finally:
        harness.real_check = original_check
        summary = guard.summary()
        save_new(OUT / "finished.json", {"finished_at": datetime.now(timezone.utc).isoformat(),
            "freeze_unchanged": source_hashes() == frozen["file_sha256"],
            "metering": {k: v for k, v in summary.items() if k != "attempts"},
            "extraction_cache_hits": sum(c.get("cached", False) for r in extraction_rows for c in r["calls"]),
            "extraction_fresh_attempts": summary["calls"],
            "corpus_records_written": [c for c in datasets if (OUT / f"{c}.json").exists()]})
    print(json.dumps({"status": "recorded", "output": str(OUT.relative_to(ROOT)),
                      "metering": {k: v for k, v in summary.items() if k != "attempts"}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--freeze", action="store_true")
    actions.add_argument("--run", action="store_true")
    args = parser.parse_args()
    freeze() if args.freeze else run()
