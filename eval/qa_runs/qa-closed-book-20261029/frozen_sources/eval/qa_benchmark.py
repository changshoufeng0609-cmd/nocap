"""Pre-registered closed-book raw-answer versus verified-answer financial QA.

Prepare and dry-run offline before any live run. A live target requires explicit
operator review, an unchanged manifest/code/snapshot, a fresh per-question slot,
and a durable reservation. No retry, SDK fallback, search, SEC or held-out calls.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import statistics
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(ROOT))
from eval import qa_core as core
from receipts import extract
from receipts.store import Store

LIMIT_USD = Decimal("3.50")
INSTRUCTIONS = """Answer the financial question directly from your own knowledge. This is closed-book: no filings, retrieval, search or tools are provided. Do not invent a value when you do not know it; return status abstain and null answer fields. If answering, supply the requested reported GAAP value, company, canonical metric, fiscal year/quarter and unit. This is an actual numeric answer, not a lookup plan. Use value_text such as '$1234 million' for USD amounts or '$1.23 per share' for EPS; retain negative signs. Supply one amount only, at the precision requested. reason is a brief uncertainty note, not chain of thought. Return only the JSON object described by the schema, without extra prose. Canonical metrics: """ + ", ".join(core.METRICS)


def backend(target: str, modal_base_url: str | None = None):
    if target in ("spark", "wonder"):
        return extract.make_backend("meta", model="muse-spark-1.3" if target == "spark" else "muse-wonder-1.0",
                                    effort="minimal", max_output_tokens=768)
    if target == "spark-open-baseten":
        return extract.make_backend("baseten", effort="low", max_output_tokens=2048)
    if target == "glimmer":
        return extract.make_backend("modal", base_url=modal_base_url or "https://not-started.us-east.modal.direct/v1",
                                    effort="low", max_output_tokens=2048)
    raise ValueError("Unknown target")


def build_request(question: core.Question, target: str) -> dict:
    b = backend(target)
    schema = extract.build_json_schema(core.RawAnswer)
    if b.protocol == "responses":
        return {"model": b.model, "instructions": INSTRUCTIONS, "input": question.text,
                "text": {"format": {"type": "json_schema", "name": "raw_financial_answer", "schema": schema, "strict": True}},
                "reasoning": {"effort": "minimal"}, "max_output_tokens": 768, "store": False}
    request = {"model": b.model,
               "messages": [{"role": "system", "content": INSTRUCTIONS + "\nJSON schema:\n" + json.dumps(schema, sort_keys=True)},
                            {"role": "user", "content": question.text}], "max_tokens": 2048}
    if target == "glimmer":
        request["response_format"] = {"type": "json_schema", "json_schema": {"name": "raw_financial_answer", "schema": schema, "strict": True}}
        request["extra_body"] = {"chat_template_kwargs": {"reasoning_strength": "low"}}
    else:
        request["reasoning_effort"] = "low"
    return request


def reserve_micros(request: dict, target: str) -> int:
    if target == "glimmer":
        return 0  # Separate shared wall-time GPU control, never reported as free compute.
    b = backend(target)
    input_bound = len(json.dumps(request, ensure_ascii=False).encode()) + 1024
    output_bound = request.get("max_output_tokens", request.get("max_tokens"))
    amount = (Decimal(input_bound) * Decimal(str(b.prices["input"])) +
              Decimal(output_bound) * Decimal(str(b.prices["output"])))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def usage_micros(usage: dict, target: str) -> int:
    b = backend(target)
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    amount = (Decimal(usage["input_tokens"] - cached) * Decimal(str(b.prices["input"])) +
              Decimal(cached) * Decimal(str(b.prices["cached_input"])) +
              Decimal(usage["output_tokens"]) * Decimal(str(b.prices["output"])))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


class AdmissionLedger:
    """SQLite reservations survive process failure and coordinate target processes."""
    def __init__(self, path: Path, manifest_hash: str, *, cap_micros: int = 3_500_000):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL, cap INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (target TEXT, question TEXT, request_hash TEXT NOT NULL, reservation INTEGER NOT NULL, charged INTEGER NOT NULL, status TEXT NOT NULL, PRIMARY KEY(target,question))")
            db.execute("INSERT OR IGNORE INTO config VALUES (1,?,?)", (manifest_hash, cap_micros))
            assert db.execute("SELECT manifest,cap FROM config WHERE id=1").fetchone() == (manifest_hash, cap_micros), "Never reset the manifest or budget cap"

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, target, question, request_hash, amount):
        if target not in core.TARGETS or not isinstance(amount, int) or amount < 0:
            raise ValueError("Invalid reservation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE target=?", (target,)).fetchone()[0]
            spent = db.execute("SELECT COALESCE(SUM(charged),0) FROM attempts").fetchone()[0]
            cap = db.execute("SELECT cap FROM config WHERE id=1").fetchone()[0]
            if count >= 50 or spent + amount > cap:
                raise RuntimeError("Admission cap reached; no dispatch")
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?)", (target, question, request_hash, amount, amount, "reserved"))

    def settle(self, target, question, known_amount: int | None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reservation,charged,status FROM attempts WHERE target=? AND question=?", (target, question)).fetchone()
            if row is None or row[2] != "reserved":
                raise RuntimeError("Missing or already settled reservation")
            amount = row[0] if known_amount is None else known_amount
            if not isinstance(amount, int) or amount < 0:
                raise ValueError("Invalid settlement")
            db.execute("UPDATE attempts SET charged=?,status=? WHERE target=? AND question=?",
                       (amount, "unknown_usage_retained" if known_amount is None else "known_usage", target, question))

    def report(self):
        with self.connect() as db:
            cap = db.execute("SELECT cap FROM config WHERE id=1").fetchone()[0]
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY target,question")]
        return {"cap_usd": cap / 1e6, "charged_or_reserved_usd": sum(row["charged"] for row in rows) / 1e6,
                "attempts": rows, "gpu_budget": "Glimmer has no token API tariff; shared Modal GPU usage is separately bounded and reported"}


def load_manifest() -> dict:
    manifest = json.loads(core.MANIFEST.read_text())
    if manifest["snapshot_hashes"] != core.evidence_hashes() or manifest["source_hashes"] != core.source_hashes():
        raise ValueError("Frozen snapshot or code changed; refuse paid calls")
    assert len(manifest["items"]) == 50
    for row in manifest["items"]:
        q = core.Question.model_validate(row["question"])
        assert row["question_sha256"] == core.json_hash(row["question"]) and row["gold_sha256"] == core.json_hash(row["gold"])
        for target in core.TARGETS:
            assert row["request_sha256"][target] == core.json_hash(build_request(q, target))
    return manifest


def plan(manifest):
    totals = {target: sum(reserve_micros(build_request(core.Question.model_validate(row["question"]), target), target)
                          for row in manifest["items"]) / 1e6 for target in core.TARGETS}
    return {"planned_questions": 50, "planned_calls_per_target": 50, "maximum_calls": 200,
            "token_provider_budget_usd": 3.5, "all_calls_full_reservation_usd": totals,
            "sum_full_token_reservations_usd": round(sum(totals.values()), 6),
            "gpu": "Excluded from token ledger; Glimmer batch <=720 seconds, shared operator GPU cap $2, no standalone deployment"}


def initialize_run(run_id: str, manifest: dict) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid run ID")
    folder = ROOT / "data/eval_runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copytree(core.SNAPSHOT, folder / "evidence")
    assert core.evidence_hashes(folder / "evidence") == manifest["snapshot_hashes"]
    core.save_new(folder / "plan.json", {"manifest_sha256": core.digest(core.MANIFEST.read_bytes()), **plan(manifest)})
    AdmissionLedger(folder / "admission.sqlite3", core.digest(core.MANIFEST.read_bytes()))
    return folder


def evaluate_record(row, output_text, *, complete, store, call_error=False):
    question = core.Question.model_validate(row["question"])
    if call_error:
        return {"question_id": question.id, "raw_outcome": "api_error", "gate": {"accepted": False, "reason": "api_error"}}
    try:
        if not complete:
            raise ValueError("Incomplete provider response")
        answer = core.parse_answer(output_text)
    except Exception as error:
        return {"question_id": question.id, "raw_outcome": "format_error", "parse_error_type": type(error).__name__,
                "gate": {"accepted": False, "reason": "format_error"}}
    # Only Question crosses into the gate; gold is passed separately to scoring.
    gate = core.gate_answer(question, answer, store)
    return {"question_id": question.id, "raw_answer": answer.model_dump(), "gate": gate,
            "raw_outcome": core.score_answer(question, answer, row["gold"], store)}


def make_client(target, *, modal_base_url=None, modal_api_key=None):
    import openai
    b = backend(target, modal_base_url)
    if target == "glimmer":
        from receipts.private import private_backend
        checked = private_backend({"RECEIPTS_PRIVATE_MODE": "true", "RECEIPTS_MODAL_BASE_URL": modal_base_url or ""})
        assert checked.base_url == b.base_url
    key = modal_api_key if target == "glimmer" and modal_api_key else extract._load_api_key(b)
    return openai.OpenAI(api_key=key, base_url=b.base_url, max_retries=0, timeout=120,
                         http_client=openai.DefaultHttpxClient(follow_redirects=False))


def run_target(target: str, *, run_id: str, manifest: dict, modal_base_url=None, modal_api_key=None,
               client=None, wall_seconds=720, concurrency=2) -> dict:
    if target not in core.TARGETS or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid target/run ID")
    if not 1 <= concurrency <= 2 or not 1 <= wall_seconds <= 720:
        raise ValueError("Invalid bounded execution settings")
    assert manifest == load_manifest(), "Manifest changed"
    folder = ROOT / "data/eval_runs" / run_id
    if not (folder / "plan.json").exists() or core.evidence_hashes(folder / "evidence") != manifest["snapshot_hashes"]:
        raise ValueError("Initialize and verify frozen run evidence before any target")
    target_dir = folder / target
    target_dir.mkdir(exist_ok=False)  # No resume/retry of an attempted target.
    manifest_hash = core.digest(core.MANIFEST.read_bytes())
    ledger = AdmissionLedger(folder / "admission.sqlite3", manifest_hash)
    start = time.monotonic()
    # Leave time for settlements/result writes before the external Modal watchdog.
    deadline = start + max(0, wall_seconds - 15)
    if target == "glimmer" and os.environ.get("PRIVATE_E2E_DEADLINE"):
        deadline = min(deadline, start + float(os.environ["PRIVATE_E2E_DEADLINE"]) - time.time() - 45)
    store = Store(folder / "evidence/facts.sqlite", folder / "evidence/pr_facts")
    result = {"target": target, "model": backend(target).model, "started_at": datetime.now(timezone.utc).isoformat(),
              "manifest_sha256": manifest_hash, "scope": manifest["scope"], "planned": 50,
              "source_hashes": manifest["source_hashes"], "snapshot_hashes": manifest["snapshot_hashes"],
              "max_output_tokens": backend(target).max_output_tokens, "reasoning_effort": backend(target).effort,
              "local_run_dir": str(folder.relative_to(ROOT)),
              "concurrency": concurrency, "timeout_s": 120, "batch_wall_limit_s": wall_seconds,
              "cleanup_margin_s": 15,
              "sdk_retries": 0, "redirects": False, "records": [], "not_started": [],
              "token_api_pricing_per_million": dict(backend(target).prices), "gpu_usage_separate": target == "glimmer"}
    lock = threading.Lock()
    supplied_client = client is not None
    client = client or make_client(target, modal_base_url=modal_base_url, modal_api_key=modal_api_key)

    def work(row):
        question = core.Question.model_validate(row["question"])
        request = build_request(question, target)
        request_hash = core.json_hash(request)
        try:
            with lock:
                remaining = deadline - time.monotonic()
                if remaining <= 2:
                    raise RuntimeError("Batch deadline reached before dispatch")
                if core.source_hashes() != manifest["source_hashes"]:
                    raise RuntimeError("Code changed before dispatch")
                assert request_hash == row["request_sha256"][target]
                ledger.reserve(target, question.id, request_hash, reserve_micros(request, target))
        except Exception as error:
            return None, {"question_id": question.id, "reason_type": type(error).__name__}
        began, usage, output, response_complete, error_type = time.monotonic(), None, "", False, None
        try:
            if time.monotonic() >= deadline:
                ledger.settle(target, question.id, 0)
                return None, {"question_id": question.id, "reason_type": "DeadlineBeforeDispatch"}
            endpoint = client.responses if backend(target).protocol == "responses" else client.chat.completions
            response = endpoint.create(**request, timeout=min(120, max(1, deadline - time.monotonic())))
            usage = extract._usage(response)
            if backend(target).protocol == "responses":
                output = response.output_text or ""
                response_complete = response.status == "completed"
            else:
                choices = response.choices or []
                output = (choices[0].message.content or "") if choices else ""
                response_complete = bool(choices and choices[0].finish_reason == "stop")
        except Exception as error:
            error_type = type(error).__name__  # Never serialize provider exception bodies or headers.
        elapsed = time.monotonic() - began
        known = usage_micros(usage, target) if usage is not None else None
        ledger.settle(target, question.id, known)
        record = evaluate_record(row, output, complete=response_complete, store=store, call_error=error_type is not None)
        record.update(request_sha256=request_hash, output_text=output, output_sha256=core.digest(output.encode()),
                      response_complete=response_complete, latency_s=round(elapsed, 6), usage=usage,
                      error_type=error_type, token_api_cost_usd=(known / 1e6 if known is not None and target != "glimmer" else None),
                      unknown_reservation_retained=known is None, gpu_cost_not_free=target == "glimmer")
        core.save_new(target_dir / f"{question.id}.json", record)
        return record, None

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(work, row) for row in manifest["items"]]
            for future in as_completed(futures):
                record, skipped = future.result()
                with lock:
                    if record:
                        result["records"].append(record)
                    if skipped:
                        result["not_started"].append(skipped)
                print(json.dumps({"target": target, "completed": len(result["records"]), "not_started": len(result["not_started"])}), flush=True)
    finally:
        result["records"].sort(key=lambda row: row["question_id"])
        result["score"] = core.aggregate(result["records"])
        result["wall_s"] = round(time.monotonic() - start, 6)
        latencies = [row["latency_s"] for row in result["records"]]
        result["latency_median_s"] = statistics.median(latencies) if latencies else None
        result["token_api_cost_usd"] = (sum(row["token_api_cost_usd"] or 0 for row in result["records"]) if target != "glimmer" else None)
        result["unknown_usage_calls"] = sum(row["unknown_reservation_retained"] for row in result["records"])
        result["ledger"] = ledger.report()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["status"] = "complete" if len(result["records"]) == 50 else "incomplete"
        core.save_new(ROOT / "eval/qa_runs" / run_id / f"{target}.json", result)
        store.close()
        if not supplied_client:
            client.close()
    return result


def recover_interrupted_target(target: str, run_id: str, manifest: dict) -> dict:
    """Offline only, after the operator has stopped the process; never retries work."""
    if target not in core.TARGETS or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid target/run ID")
    folder = ROOT / "data/eval_runs" / run_id
    manifest_hash = core.digest(core.MANIFEST.read_bytes())
    ledger = AdmissionLedger(folder / "admission.sqlite3", manifest_hash)
    report = ledger.report()
    attempts = [row for row in report["attempts"] if row["target"] == target]
    by_id = {row["question"]["id"]: row for row in manifest["items"]}
    records = []
    for attempt in attempts:
        qid = attempt["question"]
        assert qid in by_id and attempt["request_hash"] == by_id[qid]["request_sha256"][target]
        path = folder / target / f"{qid}.json"
        if path.exists():
            record = json.loads(path.read_text())
        else:
            record = {"question_id": qid, "raw_outcome": "api_error", "gate": {"accepted": False, "reason": "process_interrupted"},
                      "request_sha256": attempt["request_hash"], "output_text": None, "response_complete": False,
                      "latency_s": None, "usage": None, "error_type": "ProcessInterruptedUsageUnknown",
                      "token_api_cost_usd": None, "unknown_reservation_retained": True, "gpu_cost_not_free": target == "glimmer"}
        records.append(record)
    result = {"target": target, "model": backend(target).model, "status": "interrupted", "manifest_sha256": manifest_hash,
              "scope": manifest["scope"], "source_hashes": manifest["source_hashes"], "snapshot_hashes": manifest["snapshot_hashes"],
              "planned": 50, "records": records, "score": core.aggregate(records), "ledger": report,
              "unknown_usage_calls": sum(row["unknown_reservation_retained"] for row in records),
              "not_started": sorted(set(by_id) - {row["question_id"] for row in records}),
              "note": "Recovered offline after process termination; missing responses count as errors; reservations retained; no retry or fabricated response",
              "token_api_cost_usd": None, "gpu_usage_separate": target == "glimmer", "new_calls_in_recovery": 0}
    core.save_new(ROOT / "eval/qa_runs" / run_id / f"{target}.json", result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--initialize-run", action="store_true")
    ap.add_argument("--live-target", choices=core.TARGETS)
    ap.add_argument("--recover-stopped-target", choices=core.TARGETS)
    ap.add_argument("--run-id")
    ap.add_argument("--operator-reviewed", action="store_true")
    ap.add_argument("--wall-seconds", type=int, default=720)
    args = ap.parse_args()
    if args.prepare:
        core.save_new(core.MANIFEST, core.prepare_manifest(build_request))
        print(json.dumps(plan(json.loads(core.MANIFEST.read_text())), indent=2))
        return
    manifest = load_manifest()
    if args.recover_stopped_target:
        if not args.run_id:
            ap.error("Recovery requires --run-id and a stopped target process")
        print(json.dumps(recover_interrupted_target(args.recover_stopped_target, args.run_id, manifest)["score"]))
        return
    if args.initialize_run:
        if not args.run_id:
            ap.error("--initialize-run requires --run-id")
        print(str(initialize_run(args.run_id, manifest)))
        return
    if args.dry_run:
        store = Store(core.SNAPSHOT / "facts.sqlite", core.SNAPSHOT / "pr_facts")
        try:
            for row in manifest["items"]:
                q = core.Question.model_validate(row["question"])
                answer = core.gold_answer(q, row["gold"])
                assert core.gate_answer(q, answer, store)["accepted"]
                assert core.score_answer(q, answer, row["gold"], store) == "correct"
        finally:
            store.close()
        print(json.dumps({**plan(manifest), "offline_gold_checks": 50, "model_calls": 0}, indent=2))
        return
    if not args.live_target or not args.run_id or not args.operator_reviewed:
        ap.error("Live work requires --live-target, --run-id and --operator-reviewed")
    endpoint = os.environ.get("RECEIPTS_MODAL_BASE_URL") if args.live_target == "glimmer" else None
    result = run_target(args.live_target, run_id=args.run_id, manifest=manifest, modal_base_url=endpoint,
                        modal_api_key=os.environ.get("MODAL_PROXY_TOKEN"), wall_seconds=args.wall_seconds)
    print(json.dumps({"target": args.live_target, "status": result["status"], "score": result["score"],
                      "token_api_cost_usd": result["token_api_cost_usd"]}, indent=2))


if __name__ == "__main__":
    main()
