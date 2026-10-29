#!/usr/bin/env python3
"""Offline, read-only probes for the NoCap runtime review.

Run from the repository with:
    .venv/bin/python docs/reviews/opus-review-20261029/runtime_probe.py

The script reads receipts/api.py as AST, executes only selected definitions, and
uses Starlette Request objects and a controlled ledger stub. It does not import
the application's modules, read .env, contact a service, or call a model. Its only
write is the adjacent runtime_results.json evidence file.
"""
from __future__ import annotations

import ast
import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "receipts" / "api.py"
OUTPUT = Path(__file__).with_name("runtime_results.json")


def isolated_definitions() -> tuple[dict, dict]:
    """Avoid app construction, default ledger writes, and environment loading."""
    raw = SOURCE.read_bytes()
    tree = ast.parse(raw, filename=str(SOURCE))
    names = {"RateLimiter", "client_ip", "Rejected", "_reserve"}
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ]
    assert {node.name for node in selected} == names
    create_app = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )
    parse = next(
        node for node in create_app.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "parse"
    )
    # The oversized-body branch exits before parsing a model. Replace only its
    # CheckBody default argument so no product module needs to be imported.
    parse.args.defaults = [ast.Constant(None)]
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *selected, parse,
    ], type_ignores=[]))
    namespace = {
        "threading": threading,
        "time": time,
        "OrderedDict": OrderedDict,
        "deque": deque,
        "settings": SimpleNamespace(max_body_bytes=65_536),
        "BudgetUnavailable": type("BudgetUnavailable", (Exception,), {}),
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace, {
        "path": "receipts/api.py",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "definition_lines": {node.name: node.lineno for node in [*selected, parse]},
        "isolation": "Selected source AST definitions only; no application module imports.",
        "only_ast_change": "parse body_type default changed from CheckBody to None; function body unchanged.",
    }


def probe_forwarded_ip(namespace: dict) -> dict:
    limiter = namespace["RateLimiter"](1, 600)
    values = ["198.51.100.1", "198.51.100.1", "198.51.100.2"]
    observations = []
    for value in values:
        request = Request({
            "type": "http", "headers": [(b"x-real-ip", value.encode())],
            "client": ("127.0.0.1", 1234),
        })
        selected_ip = namespace["client_ip"](request)
        observations.append({
            "socket_peer": "127.0.0.1:1234",
            "x_real_ip": value,
            "selected_limiter_identity": selected_ip,
            "allowed": limiter.acquire(selected_ip) is None,
        })
    reproduced = [item["allowed"] for item in observations] == [True, False, True]
    assert reproduced, observations
    return {
        "id": "forwarded_ip_identity",
        "reproduced": reproduced,
        "severity": "P2",
        "code_reference": "receipts/api.py:323-328",
        "observations": observations,
        "finding": "An unchanged socket peer gets a fresh per-IP allowance by changing x-real-ip.",
        "scope": "Confirmed in the application helper and direct/untrusted-proxy scenario only.",
        "vercel_exploitability": "UNTESTED: no real Vercel request was sent; edge header rewriting was not inspected.",
        "daily_budget_bypass": False,
    }


async def probe_body_limit(namespace: dict) -> dict:
    reads = 0
    chunk_bytes = 65_537
    chunk_count = 3

    async def receive() -> dict:
        nonlocal reads
        reads += 1
        assert reads <= chunk_count
        return {
            "type": "http.request", "body": b"x" * chunk_bytes,
            "more_body": reads < chunk_count,
        }

    request = Request({
        "type": "http", "headers": [], "client": ("127.0.0.1", 1234),
    }, receive)
    reason = None
    try:
        await namespace["parse"](request)
    except namespace["Rejected"] as error:
        reason = error.reason
    retained = len(request._body)
    reproduced = reason == "body_too_large" and reads == 3 and retained == 196_611
    assert reproduced, (reason, reads, retained)
    return {
        "id": "body_limit_after_buffering",
        "reproduced": reproduced,
        "severity": "P2",
        "code_reference": "receipts/api.py:567-571; shared by /api/check and /api/fix",
        "configured_limit_bytes": 65_536,
        "input_chunk_bytes": chunk_bytes,
        "input_chunk_count": chunk_count,
        "chunks_read_before_rejection": reads,
        "bytes_retained_before_rejection": retained,
        "rejection_reason": reason,
        "finding": "The entire body is buffered before the configured body-size check rejects it.",
        "scope": "Controlled in-process ASGI receive stream; no oversized live request was sent.",
        "platform_limits": "Any Vercel or reverse-proxy request-size limit is outside this probe.",
    }


async def probe_event_loop(namespace: dict) -> dict:
    stub_delay_s = 0.2
    other_task_delay_s = 0.01

    class SlowOfflineLedger:
        def reserve(self, amount: float):
            time.sleep(stub_delay_s)
            return None

    started = time.perf_counter()

    async def other_request() -> float:
        await asyncio.sleep(other_task_delay_s)
        return time.perf_counter() - started

    task = asyncio.create_task(other_request())
    await asyncio.sleep(0)  # Let the unrelated coroutine schedule its 10ms wakeup.
    reservation = namespace["_reserve"](SlowOfflineLedger(), 0.05)
    actual_delay_s = await task
    reproduced = reservation is None and actual_delay_s >= stub_delay_s * 0.9
    assert reproduced, actual_delay_s
    return {
        "id": "synchronous_admission_blocks_event_loop",
        "reproduced": reproduced,
        "severity": "P2",
        "code_references": [
            "receipts/api.py:315-320,644,700,757",
            "receipts/ask_api.py:90",
            "receipts/private.py:159",
            "receipts/spend.py:328-336",
        ],
        "controlled_stub_delay_ms": stub_delay_s * 1000,
        "other_coroutine_requested_delay_ms": other_task_delay_s * 1000,
        "other_coroutine_observed_delay_ms": round(actual_delay_s * 1000, 3),
        "finding": "Calling synchronous _reserve from the event loop delays unrelated coroutines.",
        "measurement_scope": "CONTROLLED 200ms sleep stub, NOT real Neon/PostgreSQL latency.",
        "actual_database_contacted": False,
        "current_paused_behavior": "The configured live-paused wrapper rejects before database admission; this risk applies when live admission is enabled.",
    }


async def main() -> None:
    namespace, source = isolated_definitions()
    results = {
        "schema_version": 1,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": ".venv/bin/python docs/reviews/opus-review-20261029/runtime_probe.py",
        "source": source,
        "probes": [
            probe_forwarded_ip(namespace),
            await probe_body_limit(namespace),
            await probe_event_loop(namespace),
        ],
        "external_requests": 0,
        "model_calls": 0,
        "sec_requests": 0,
        "gpu_calls": 0,
        "cost_usd": 0,
        "env_or_credentials_read": False,
        "product_or_test_files_modified": False,
    }
    OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT.relative_to(ROOT)),
        "reproduced": sum(item["reproduced"] for item in results["probes"]),
        "total": len(results["probes"]),
        "external_requests": 0,
        "cost_usd": 0,
    }))


if __name__ == "__main__":
    asyncio.run(main())
