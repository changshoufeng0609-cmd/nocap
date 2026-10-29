"""Actual SDK protocol tests and boundary tests for the optional local MCP server."""
import asyncio
import hashlib
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

pytest.importorskip("mcp", reason="Install requirements-mcp.txt to run optional MCP protocol tests")

from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from receipts import edgar, extract
from receipts.mcp_server import SearchInput, TypedClaim, VerifyInput, create_server, search_evidence, verify_claim
from receipts.store import Store

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "snapshot"
# These are bounded integration checks, not startup-speed requirements. Importing
# the SDK in a fresh process can exceed 10 s on a busy developer/CI machine.
MCP_READ_TIMEOUT_S = 30
MCP_TEST_TIMEOUT_S = 90


def store_factory():
    return Store(SNAPSHOT / "facts.sqlite", SNAPSHOT / "pr_facts")


def claim_data(**changes):
    data = dict(id="m1", metric="revenue", metric_text="revenue", value_text="$96.2 billion",
                value=96.2, unit="USD", scale="billions", period_text="Q2 FY2027", period_type="quarter",
                comparison="none", basis="GAAP", forward_looking=False, entity="NVIDIA", segment=None,
                span_text="NVIDIA Q2 FY2027 revenue was $96.2 billion.", span_start=0)
    data.update(changes)
    data.setdefault("span_end", len(data["span_text"]))
    return data


@pytest.fixture(autouse=True)
def forbid_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("MCP must never call an LLM or SEC")
    monkeypatch.setattr(extract, "make_client", forbidden)
    monkeypatch.setattr(edgar.Edgar, "_fetch", forbidden)


def test_search_pages_and_cutoff_preserve_receipts():
    query = SearchInput(entity="NVDA", metric="revenue", limit=2, period_end="2026-07-26")
    page = search_evidence(query, store_factory)
    assert page["total"] >= 2
    assert all(f["metric"] == "revenue" and f["period_end"] == "2026-07-26" for f in page["receipts"])
    assert page["model_calls"] == 0
    assert page["next_offset"] == 2 if page["total"] > 2 else page["next_offset"] is None
    if page["next_offset"] is not None:
        second = search_evidence(query.model_copy(update={"offset": page["next_offset"]}), store_factory)
        assert second["offset"] == 2
        assert second["total"] == page["total"]
    before = search_evidence(SearchInput(entity="NVDA", metric="revenue", period_end="2026-07-26",
                                        as_of="2026-08-26T20:00:00Z"), store_factory)
    assert before["total"] == 0 and before["receipts"] == []
    cutoff = datetime(2026, 8, 26, 20, 30, tzinfo=timezone.utc)
    early = search_evidence(SearchInput(entity="NVDA", metric="revenue", as_of=cutoff), store_factory)
    assert all(datetime.fromisoformat(f["accepted_at"].replace("Z", "+00:00")) <= cutoff for f in early["receipts"])


def test_verifier_match_wrong_lookahead_and_guidance():
    valid = VerifyInput(claim=claim_data())
    result = verify_claim(valid, store_factory)
    assert (result["status"], result["reason"], result["model_calls"]) == ("verified", "match", 0)
    assert result["evidence"] and result["evidence_total"] >= 1
    wrong = VerifyInput(claim=claim_data(value_text="$90.2 billion", value=90.2,
                                        span_text="NVIDIA Q2 FY2027 revenue was $90.2 billion."))
    assert verify_claim(wrong, store_factory)["status"] == "wrong"
    past = VerifyInput(claim=claim_data(), as_of="2026-08-26T20:00:00Z")
    assert verify_claim(past, store_factory)["reason"] == "look_ahead"
    future = VerifyInput(claim=claim_data(forward_looking=True))
    assert verify_claim(future, store_factory)["reason"] == "forward_looking"


@pytest.mark.parametrize("update", [
    {"value": float("nan")}, {"value": float("inf")}, {"value": 42},
    {"scale": "millions"}, {"unit": "%"}, {"value_text": "$88 billion"},
    {"span_start": -1}, {"span_end": 3}, {"span_start": True},
    {"span_text": "x" * 6001}, {"id": "x" * 81},
    {"forward_looking": "false"}, {"status": "verified"},
])
def test_typed_claim_rejects_invalid_proposals(update):
    with pytest.raises(ValidationError):
        TypedClaim.model_validate(claim_data(**update))


@pytest.mark.parametrize("update", [
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"offset": -1}, {"offset": 100001},
    {"entity": ""}, {"metric": "execute_sql"}, {"endpoint": "https://example.org"},
    {"as_of": "2026-08-26"}, {"as_of": "2026-08-26T20:00:00"},
])
def test_search_rejects_invalid_filters(update):
    with pytest.raises(ValidationError):
        SearchInput.model_validate({"entity": "NVDA", "metric": "revenue", **update})


def test_unknown_entity_is_actionable_and_database_is_read_only():
    with pytest.raises(ToolError, match="nocap_coverage"):
        search_evidence(SearchInput(entity="unknown-company", metric="revenue"), store_factory)
    store = store_factory()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store._conn.execute("DELETE FROM companies")
    finally:
        store.close()


def test_real_mcp_protocol_tool_schemas_validation_and_results():
    async def exercise():
        async with Client(create_server(store_factory)) as client:
            tools = (await client.list_tools()).tools
            assert {t.name for t in tools} == {"nocap_coverage", "nocap_search_evidence", "nocap_verify_claim"}
            assert all(t.annotations.read_only_hint and not t.annotations.open_world_hint for t in tools)
            assert all(t.output_schema for t in tools)
            result = await client.call_tool("nocap_verify_claim", {"query": {"claim": claim_data()}})
            assert not result.is_error and result.structured_content["status"] == "verified"
            invalid = await client.call_tool("nocap_search_evidence", {"query": {"entity": "NVDA", "metric": "revenue", "limit": 100}})
            assert invalid.is_error
            unexpected = await client.call_tool("nocap_search_evidence", {"query": {"entity": "NVDA", "metric": "revenue", "url": "https://example.com"}})
            assert unexpected.is_error
    asyncio.run(asyncio.wait_for(exercise(), timeout=MCP_TEST_TIMEOUT_S))


def test_real_stdio_subprocess_handshake_and_snapshot_no_writes(tmp_path, record_property):
    before = hashlib.sha256((SNAPSHOT / "facts.sqlite").read_bytes()).hexdigest()
    network_attempt = tmp_path / "mcp-network-attempt.txt"
    # Parent monkeypatches do not cross the subprocess boundary. A child audit
    # hook both prevents socket/DNS calls and records attempted network access,
    # even if server code were to swallow the resulting exception.
    launch = """
import pathlib, runpy, sys
marker = pathlib.Path(sys.argv[1])
def deny_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'}:
        marker.write_text(event)
        raise AssertionError('MCP stdio must not attempt network access')
sys.addaudithook(deny_network)
sys.argv = ['receipts.mcp_server']
runpy.run_module('receipts.mcp_server', run_name='__main__', alter_sys=True)
"""

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=["-c", launch, str(network_attempt)],
                                       cwd=str(ROOT), env={"PATH": os.environ.get("PATH", ""),
                                                           "RECEIPTS_DATA_DIR": str(SNAPSHOT)})
        started = time.monotonic()
        # Entering Client performs the actual initialize handshake; no arbitrary
        # sleep or polling bypasses readiness, and the outer bound catches hangs.
        async with Client(params, read_timeout_seconds=MCP_READ_TIMEOUT_S) as client:
            record_property("mcp_stdio_startup_s", round(time.monotonic() - started, 3))
            scope = await client.call_tool("nocap_coverage", {})
            assert not scope.is_error
            assert "NVDA" in {c["ticker"] for c in scope.structured_content["companies"]}
            assert scope.structured_content["model_calls"] == 0
            page = await client.call_tool("nocap_search_evidence", {"query": {
                "entity": "NVDA", "metric": "revenue", "period_end": "2026-07-26", "limit": 1}})
            assert not page.is_error and len(page.structured_content["receipts"]) == 1
    asyncio.run(asyncio.wait_for(exercise(), timeout=MCP_TEST_TIMEOUT_S))
    assert not network_attempt.exists(), "MCP subprocess attempted network access"
    assert hashlib.sha256((SNAPSHOT / "facts.sqlite").read_bytes()).hexdigest() == before
