"""Exercise fixed development workflows through a real local MCP stdio subprocess.

No LLM client, SEC request or held-out corpus is used. This measures protocol/integration behavior,
not whether an autonomous model can correctly select tools or formulate the typed claims.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import Client, StdioServerParameters

from receipts.store import default_data_dir

SCENARIOS = Path(__file__).with_name("mcp_dev_scenarios.json")
READ_TIMEOUT_S = 30
RUN_TIMEOUT_S = 90


async def evaluate(data_dir: Path) -> dict:
    scenarios = json.loads(SCENARIOS.read_text())["scenarios"]
    params = StdioServerParameters(command=sys.executable, args=["-m", "receipts.mcp_server"],
                                   cwd=str(ROOT), env={"PATH": os.environ.get("PATH", ""),
                                                       "RECEIPTS_DATA_DIR": str(data_dir)})
    results = []
    started = time.monotonic()
    # SDK initialization is the readiness handshake. Leave time for fresh-process
    # imports under CPU contention, while retaining an overall deadline for hangs.
    async with Client(params, read_timeout_seconds=READ_TIMEOUT_S) as client:
        startup_s = round(time.monotonic() - started, 3)
        scope = await client.call_tool("nocap_coverage", {})
        if scope.is_error:
            raise RuntimeError("MCP coverage discovery failed")
        for case in scenarios:
            search = await client.call_tool("nocap_search_evidence", {"query": case["search"]})
            check = await client.call_tool("nocap_verify_claim", {"query": case["verify"]})
            found = search.structured_content or {}
            verdict = check.structured_content or {}
            cutoff = case["search"]["as_of"]
            visibility_ok = not cutoff or all(
                datetime.fromisoformat(f["accepted_at"].replace("Z", "+00:00")) <= datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
                for f in found.get("receipts", []))
            ok = (not search.is_error and not check.is_error and visibility_ok
                  and verdict.get("status") == case["expected_status"]
                  and verdict.get("reason") == case["expected_reason"]
                  and verdict.get("model_calls") == 0)
            results.append({"id": case["id"], "passed": ok, "status": verdict.get("status"),
                            "reason": verdict.get("reason"), "evidence_visible_at_cutoff": visibility_ok,
                            "search_tool_error": search.is_error, "verify_tool_error": check.is_error})
    return {"ran_at": datetime.now(timezone.utc).isoformat(), "transport": "stdio", "sdk": "mcp==2.3.0",
            "stdio_startup_s": startup_s, "read_timeout_s": READ_TIMEOUT_S,
            "run_timeout_s": RUN_TIMEOUT_S,
            "scope": "Fixed deterministic tool workflows, not LLM agent accuracy or held-out evaluation",
            "scenarios_sha256": hashlib.sha256(SCENARIOS.read_bytes()).hexdigest(),
            "database_sha256": hashlib.sha256((data_dir / "facts.sqlite").read_bytes()).hexdigest(),
            "passed": sum(r["passed"] for r in results), "total": len(results), "tool_calls": 1 + 2 * len(results),
            "model_calls": 0, "sec_requests": 0, "model_cost_usd": 0, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    result = asyncio.run(asyncio.wait_for(evaluate(args.data_dir.resolve()), timeout=RUN_TIMEOUT_S))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"MCP dev workflows: {result['passed']}/{result['total']}; {result['tool_calls']} stdio tool calls; 0 model/SEC calls.")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
