"""Muse Glimmer 30B LoRA SFT on NoCap claim extraction (Fireworks Serverless Training API), then base vs tuned.

The same steps as the fw-ai/cookbook Muse Glimmer starter's serverless path (examples/sft/muse_glimmer_starter.py at
57968a2: LoRA rank 8, alpha 32, MLP + attention + unembedding, cross-entropy on the last assistant turn, Adam
lr 1e-4 b1 0.9 b2 0.999, grad clip 1.0, batch 16, a sampler snapshot before and after training, in-session
sampling, promotion of the tuned snapshot), with three differences: the chat is rendered by glimmer_format.py
(the cookbook renderer needs torch and transformers), the evaluation passages are a fixed test file instead of a
random 40-row holdout, and full answers are saved for NoCap's own checks (evaluate.py) instead of exact match.

Cost controls
  * training: LoRA SFT on muse-glimmer-30b started before 2026-12-01 00:00 PT is promotional $0 (Fireworks Models
    page and Glimmer cookbook). This script refuses to start after that deadline and has no paid-training switch.
  * sampling: billed per token (prefill $1.96, cached prefill $0.39, sample $4.88 per 1M, Fireworks Serverless
    Training pricing, 2026-10-31). Every test passage is sampled base + tuned as a pair; a pair is admitted only if
    the worst case (uncached prompt plus max_tokens on both, one attempt each) still fits under budget - margin.
    Each attempt is settled at the provider-reported cached/uncached prompt tokens when given, else uncached; a
    failed attempt with unknown output is charged max_tokens. No deployment is created.
  * the training session is deleted at the end; promoted LoRA models are stored at no charge and serve nothing.

FIREWORKS_API_KEY must be in the process environment (this script never reads .env files).

    PY=<venv>/bin/python
    $PY eval/glimmer_sft/train_and_sample.py --data <dir> --tokenizer <tokenizer.json> --run-dir <dir> --dry-run
    $PY eval/glimmer_sft/train_and_sample.py --data <dir> --tokenizer <tokenizer.json> --run-dir <dir>
    $PY eval/glimmer_sft/train_and_sample.py ... --resume      # finish sampling from saved snapshots, no training
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import FIRST_COMPLETED, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fw_account  # noqa: E402
import glimmer_format as gf  # noqa: E402

BASE_MODEL = fw_account.BASE_MODEL
SERVERLESS_URL = "https://api.fireworks.ai/training/v1/serverless"
PRICE_PER_M = {"prefill": 1.96, "cached_prefill": 0.39, "sample": 4.88, "train": 5.86}
LORA = {"rank": 8, "alpha": 32, "train_mlp": True, "train_attn": True, "train_unembed": True}
SNAPSHOT_NAMES = {"base": "nocap-base", "tuned": "nocap-tuned"}  # <= 17 characters each
SAMPLER_ATTEMPTS = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Ledger:
    """Sampling spend: settled amounts plus reservations for requests in flight, persisted after every change."""

    def __init__(self, cap: float, path: Path):
        self.cap, self.path = cap, path
        self.spent, self.reserved, self.requests = 0.0, 0.0, 0
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.spent, self.requests = float(saved["spent_usd"]), int(saved["requests"])

    def reserve(self, amount: float) -> bool:
        if self.spent + self.reserved + amount > self.cap + 1e-12:
            return False
        self.reserved += amount
        return True

    def settle(self, reserved: float, actual: float) -> None:
        self.reserved = max(0.0, self.reserved - reserved)
        self.spent += actual
        self.requests += 1
        self.path.write_text(json.dumps({"spent_usd": round(self.spent, 6), "requests": self.requests,
                                         "cap_usd": self.cap, "updated_utc": now()}) + "\n", encoding="utf-8")


def worst_request_usd(prompt_tokens: int, max_tokens: int) -> float:
    return (prompt_tokens * PRICE_PER_M["prefill"] + max_tokens * PRICE_PER_M["sample"]) / 1e6


def attempt_usd(prompt_tokens: int, cached: Optional[int], completion: int) -> float:
    c = min(max(cached or 0, 0), prompt_tokens)
    return ((prompt_tokens - c) * PRICE_PER_M["prefill"] + c * PRICE_PER_M["cached_prefill"]
            + completion * PRICE_PER_M["sample"]) / 1e6


def plan(train: list[dict], test: list[dict], fmt: gf.GlimmerFormat, args: argparse.Namespace) -> dict[str, Any]:
    sup = [fmt.supervised(r["messages"]) for r in train]
    lengths = [len(t) for t, _ in sup]
    trained = [int(sum(w > 0 for w in ws)) for _, ws in sup]
    prompts = [len(fmt.prompt(r["messages"])) for r in test]
    targets = [len(fmt.encode(r["messages"][-1]["content"])) + 4 for r in test]
    steps = args.epochs * math.ceil(len(train) / args.batch_size)
    cap = args.budget - args.margin
    pair_worst = [2 * worst_request_usd(p, args.max_tokens) for p in prompts]
    admitted, total = 0, 0.0
    for w in pair_worst:  # pairs the guard admits if every answer ran to max_tokens with nothing cached
        if total + w > cap:
            break
        total, admitted = total + w, admitted + 1

    def scenario(base_tokens: int, cached: bool) -> float:
        usd = 0.0
        for p, t in zip(prompts, targets):
            hit = max(p - 64, 0) if cached else 0
            usd += attempt_usd(p, hit, base_tokens + t) + attempt_usd(p, hit, t)
        return round(usd, 4)

    return {
        "train_rows": len(train), "test_rows": len(test), "epochs": args.epochs, "batch_size": args.batch_size,
        "steps": steps, "max_seq_len": args.max_seq_len, "longest_train_row": max(lengths),
        "oversized_rows": sum(n > args.max_seq_len for n in lengths),
        "train_tokens_per_epoch": sum(lengths), "trained_tokens_per_epoch": sum(trained),
        "training_usd_if_not_promotional": round(args.epochs * sum(lengths) * PRICE_PER_M["train"] / 1e6, 4),
        "promotion_window_open": datetime.now(timezone.utc) < fw_account.PROMOTION_DEADLINE,
        "sampling": {"max_tokens": args.max_tokens, "temperature": 0.0, "reasoning_strength": fmt.reasoning_strength,
                     "budget_usd": args.budget, "guard_cap_usd": round(cap, 4),
                     "prompt_tokens": {"min": min(prompts), "median": statistics.median(prompts), "max": max(prompts),
                                       "sum_per_model": sum(prompts)},
                     "pairs_admitted_in_the_worst_case": admitted,
                     "expected_usd_all_pairs": {f"base_{b}_tokens_{'cached' if c else 'uncached'}": scenario(b, c)
                                                for b in (500, 1500) for c in (False, True)}},
    }


def datum(sdk: Any, tokens: list[int], weights: list[float]) -> Any:
    return sdk.Datum(model_input=sdk.ModelInput.from_ints(tokens[:-1]),
                     loss_fn_inputs={"target_tokens": tokens[1:], "weights": weights[1:]})


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_utc"] = now()
    path.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def train(service: Any, sdk: Any, fmt: gf.GlimmerFormat, rows: list[dict], args: argparse.Namespace,
          state: dict[str, Any], state_path: Path) -> Any:
    sup = [fmt.supervised(r["messages"]) for r in rows]
    if any(len(t) > args.max_seq_len for t, _ in sup):
        raise SystemExit("a training row exceeds --max-seq-len; nothing was trained")
    datums = [datum(sdk, t, w) for t, w in sup]
    client = service.create_lora_training_client(base_model=BASE_MODEL, rank=LORA["rank"], alpha=LORA["alpha"],
                                                 train_mlp=LORA["train_mlp"], train_attn=LORA["train_attn"],
                                                 train_unembed=LORA["train_unembed"])
    state.update(session_id=service.training_session_id, session_name=service.training_session_name,
                 training_run_id=getattr(client, "run_id", None), training_started_utc=now(), phase="training")
    save_state(state_path, state)
    print(f"session {state['session_id']} run {state['training_run_id']}", flush=True)
    state["snapshots"] = {"base": client.save_weights_for_sampler(SNAPSHOT_NAMES["base"]).result().path}
    save_state(state_path, state)
    adam = sdk.AdamParams(learning_rate=args.learning_rate, beta1=0.9, beta2=0.999, eps=1e-8, weight_decay=0.0,
                          grad_clip_norm=1.0)
    rng = random.Random(args.seed)
    step = 0
    with (args.run_dir / "metrics.jsonl").open("a", encoding="utf-8") as log:
        for epoch in range(args.epochs):
            order = list(range(len(datums)))
            rng.shuffle(order)
            for start in range(0, len(order), args.batch_size):
                batch = [datums[i] for i in order[start:start + args.batch_size]]
                fb = client.forward_backward(batch, "cross_entropy").result()
                opt = client.optim_step(adam, emit_grad_norm_metrics=True).result()
                step += 1
                metrics = dict(getattr(fb, "metrics", None) or {})
                tokens = metrics.get("response_tokens") or metrics.get("num_loss_tokens") or 1.0
                loss = metrics.get("loss:sum", 0.0) / max(float(tokens), 1.0)
                grad = {k: v for k, v in (getattr(opt, "metrics", None) or {}).items() if "grad" in k}
                row = {"step": step, "epoch": epoch + 1, "rows": len(batch), "loss": round(loss, 4), **grad,
                       "utc": now()}
                log.write(json.dumps(row) + "\n")
                log.flush()
                print(json.dumps(row), flush=True)
    state["snapshots"]["tuned"] = client.save_weights_for_sampler(SNAPSHOT_NAMES["tuned"]).result().path
    state.update(steps=step, training_finished_utc=now(), phase="trained")
    save_state(state_path, state)
    return client


def promote(service: Any, state: dict[str, Any], state_path: Path, run_id: str) -> None:
    from fireworks.training.sdk import FireworksClient

    with FireworksClient(api_key=fw_account.api_key()) as fw:
        rows = [r for r in fw.list_training_session_checkpoints(service.training_session_name)
                if r.get("promotable") and SNAPSHOT_NAMES["tuned"] in (r.get("checkpointName") or r.get("name") or "")]
        if not rows:
            print("no promotable tuned checkpoint found; the adapter stays session-only", flush=True)
            return
        target = max(rows, key=lambda r: r.get("createTime", ""))
        model_id = f"nocap-glimmer-sft-{run_id}".lower()[:63].rstrip("-")
        fw.promote_session_checkpoint(name=target["name"], output_model_id=model_id, base_model=BASE_MODEL)
    state.update(promoted_model_id=model_id, promoted_from=target.get("checkpointName"), promoted_utc=now())
    save_state(state_path, state)
    print(json.dumps({"promoted_model_id": model_id}), flush=True)


def record(row: dict, model: str, prompt: list[int], res: Any, fmt: gf.GlimmerFormat,
           max_tokens: int) -> tuple[dict[str, Any], float]:
    completion = res.completions[0] if res.completions else None
    ids = list(completion.full_tokens[completion.prompt_len:]) if completion else []
    parsed = fmt.parse(ids, completion.finish_reason if completion else None)
    usd, cached_seen = 0.0, False
    attempts = list(getattr(res, "server_attempts", None) or [])
    if not attempts:
        attempts = [type("A", (), {"outcome": "succeeded", "server_metrics": res.server_metrics})()]
    for att in attempts:
        m = att.server_metrics
        p = m.prompt_tokens if m and m.prompt_tokens is not None else len(prompt)
        cached = m.cached_prompt_tokens if m else None
        cached_seen |= cached is not None
        if att.outcome == "succeeded":
            out = len(ids)
        else:
            out = m.completion_tokens if m and m.completion_tokens is not None else max_tokens
        usd += attempt_usd(p, cached, out)
    m = res.server_metrics
    rec = {"id": row["id"], "corpus": row.get("corpus"), "model": model, "ok": completion is not None,
           "prompt_tokens": len(prompt), "server_prompt_tokens": m.prompt_tokens if m else None,
           "cached_prompt_tokens": m.cached_prompt_tokens if m else None, "completion_tokens": len(ids),
           "finish_reason": completion.finish_reason if completion else None, "terminated": parsed["terminated"],
           "visible": parsed["visible"], "visible_tokens": len(fmt.encode(parsed["visible"])) if parsed["visible"] else 0,
           "reasoning": parsed["reasoning"], "reasoning_tokens_approx": len(fmt.encode(parsed["reasoning"])),
           "other_recipients": parsed["other_recipients"], "attempts": res.attempts,
           "wall_s": round(res.wall_seconds, 2), "request_id": res.logical_request_id,
           "upstream_response_id": res.upstream_response_id, "cost_usd": round(usd, 6),
           "cost_basis": "provider cached-token counts" if cached_seen else "uncached upper bound", "utc": now()}
    return rec, usd


def failure(row: dict, model: str, prompt: list[int], error: BaseException, max_tokens: int) -> tuple[dict, float]:
    attempts = list(getattr(error, "server_attempts", None) or [])
    usd = sum(attempt_usd(len(prompt), None, (a.server_metrics.completion_tokens
                                              if a.server_metrics and a.server_metrics.completion_tokens is not None
                                              else max_tokens)) for a in attempts) if attempts \
        else worst_request_usd(len(prompt), max_tokens)
    return ({"id": row["id"], "corpus": row.get("corpus"), "model": model, "ok": False,
             "error": fw_account.redact(f"{type(error).__name__}: {str(error)[:300]}"),
             "attempts": len(attempts) or None, "cost_usd": round(usd, 6), "cost_basis": "conservative",
             "utc": now()}, usd)


def sample_all(samplers: dict[str, Any], test: list[dict], fmt: gf.GlimmerFormat, args: argparse.Namespace,
               ledger: Ledger, out_path: Path) -> dict[str, Any]:
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("ok"):
                done.add((r["id"], r["model"]))
    for s in samplers.values():
        s.deployment_sampler._RETRY_MAX_ATTEMPTS = SAMPLER_ATTEMPTS  # bound retries: each attempt can bill
    queue = [r for r in test if not all((r["id"], m) in done for m in samplers)]
    pending: dict[Any, tuple[dict, str, list[int], float]] = {}
    stopped_for_budget: list[str] = []
    in_flight_rows: set[str] = set()
    with out_path.open("a", encoding="utf-8") as out:
        while queue or pending:
            while queue and len(in_flight_rows) < args.pairs_in_flight and not stopped_for_budget:
                row = queue[0]
                prompt = fmt.prompt(row["messages"])
                models = [m for m in samplers if (row["id"], m) not in done]
                need = len(models) * worst_request_usd(len(prompt), args.max_tokens)
                if not ledger.reserve(need):
                    stopped_for_budget = [r["id"] for r in queue]
                    break
                queue.pop(0)
                in_flight_rows.add(row["id"])
                for m in models:
                    coro = samplers[m].deployment_sampler.sample_with_prompt_tokens_result(
                        prompt, max_tokens=args.max_tokens, temperature=0.0, stop=[gf.EOT],
                        user=f"nocap-sft-{args.run_id}-{m}")  # stable per-model affinity: one shared prompt prefix
                    fut = samplers[m]._submit(coro)
                    pending[fut] = (row, m, prompt, need / len(models))
            if not pending:
                break
            finished, _ = wait(list(pending), timeout=900, return_when=FIRST_COMPLETED)
            for fut in finished:
                row, m, prompt, reserved = pending.pop(fut)
                try:
                    rec, usd = record(row, m, prompt, fut.result(), fmt, args.max_tokens)
                except Exception as e:  # noqa: BLE001 - recorded, charged conservatively, and the row is incomplete
                    rec, usd = failure(row, m, prompt, e, args.max_tokens)
                ledger.settle(reserved, usd)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                if not any(p[0]["id"] == row["id"] for p in pending.values()):
                    in_flight_rows.discard(row["id"])
                print(f"{m:5s} {row['id'][:10]} ok={rec['ok']} out={rec.get('completion_tokens')} "
                      f"term={rec.get('terminated')} ${rec['cost_usd']:.4f} total=${ledger.spent:.4f}", flush=True)
    return {"spent_usd": round(ledger.spent, 6), "requests": ledger.requests,
            "not_sampled_budget": stopped_for_budget}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="directory with train.jsonl and test.jsonl")
    ap.add_argument("--tokenizer", type=Path, required=True, help="Muse Glimmer tokenizer.json")
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--learning-rate", type=float, default=1e-4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--max-tokens", type=int, default=4096, help="identical for base and tuned")
    ap.add_argument("--budget", type=float, default=1.00, help="owner-approved ceiling for sampling, USD")
    ap.add_argument("--margin", type=float, default=0.05, help="kept unspent below --budget")
    ap.add_argument("--pairs-in-flight", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true", help="offline plan only: no key, no network")
    ap.add_argument("--resume", action="store_true", help="sample from the saved snapshots; never trains")
    ap.add_argument("--no-promote", action="store_true")
    args = ap.parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.run_dir / "state.json"
    state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    args.run_id = args.run_id or state.get("run_id") or f"r{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    train_rows, test_rows = read_rows(args.data / "train.jsonl"), read_rows(args.data / "test.jsonl")
    fmt = gf.GlimmerFormat(str(args.tokenizer), reasoning_strength="low")
    p = plan(train_rows, test_rows, fmt, args)
    print(json.dumps(p, indent=1), flush=True)
    if p["oversized_rows"]:
        raise SystemExit("rows exceed --max-seq-len")
    if args.dry_run:
        return 0
    if not p["promotion_window_open"]:
        raise SystemExit("the Muse Glimmer LoRA SFT promotion has ended; this script never runs paid training")
    fw_account.api_key()
    from fireworks.training import sdk
    from fireworks.training.sdk import FiretitanServiceClient

    state.setdefault("run_id", args.run_id)
    state.setdefault("config", {"base_model": BASE_MODEL, "lora": LORA, "epochs": args.epochs,
                                "batch_size": args.batch_size, "learning_rate": args.learning_rate, "seed": args.seed,
                                "max_seq_len": args.max_seq_len, "max_tokens": args.max_tokens, "temperature": 0.0,
                                "stop": [gf.EOT], "reasoning_strength": "low", "budget_usd": args.budget,
                                "margin_usd": args.margin, "plan": p})
    save_state(state_path, state)
    try:
        before = fw_account.snapshot()
        (args.run_dir / "account_before.json").write_text(json.dumps(before, indent=1) + "\n", encoding="utf-8")
    except SystemExit as e:
        print(f"billing snapshot unavailable before the run: {e}", flush=True)
    service = FiretitanServiceClient(api_key=fw_account.api_key(), base_url=SERVERLESS_URL)
    getattr(service, "_enable_serverless_supervised_409_retry", lambda: None)()  # cookbook serverless SFT opt-in
    samplers: dict[str, Any] = {}
    finished = False
    try:
        if not args.resume:
            if state.get("snapshots", {}).get("tuned"):
                raise SystemExit("this run directory already holds a trained snapshot; use --resume")
            train(service, sdk, fmt, train_rows, args, state, state_path)
            if not args.no_promote:
                promote(service, state, state_path, args.run_id)
        snaps = state.get("snapshots") or {}
        if not (snaps.get("base") and snaps.get("tuned")):
            raise SystemExit("no saved snapshots to sample")
        samplers = {m: service.create_sampling_client(model_path=snaps[m], tokenizer=None) for m in ("base", "tuned")}
        ledger = Ledger(args.budget - args.margin, args.run_dir / "ledger.json")
        state["phase"] = "sampling"
        save_state(state_path, state)
        result = sample_all(samplers, test_rows, fmt, args, ledger, args.run_dir / "samples.jsonl")
        state.update(sampling=result, phase="sampled", sampled_utc=now())
        save_state(state_path, state)
        finished = True
    finally:
        for s in samplers.values():
            try:
                s.close()
            except Exception as e:  # noqa: BLE001
                print(f"sampler close: {type(e).__name__}", flush=True)
        if finished:
            try:
                service.delete_training_session()
                state.update(session_deleted_utc=now(), phase="done")
            except Exception as e:  # noqa: BLE001
                state["session_delete_error"] = fw_account.redact(f"{type(e).__name__}: {str(e)[:200]}")
        try:
            service.close()
        except Exception as e:  # noqa: BLE001
            print(f"service close: {type(e).__name__}", flush=True)
        save_state(state_path, state)
        print("No deployment was created. " + ("The training session was deleted." if state.get("session_deleted_utc")
              else "The training session was left to expire on its own (it bills nothing while idle)."), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
