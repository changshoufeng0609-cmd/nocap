# NoCap MCP server

NoCap exposes a **local stdio MCP server** through the official Python SDK, pinned to `mcp==2.3.0`.
It lets an MCP host discover coverage, search SEC receipts and verify typed numeric claims. The server
uses the same deterministic verifier as Check; it makes **no LLM or SEC requests**, accepts no arbitrary
URLs or SQL, writes no evidence and does not save caller-supplied claims.

## Install and connect

From the project root:

```bash
.venv/bin/python -m pip install -r requirements-mcp.txt
.venv/bin/python -m receipts.mcp_server --help
```

Configure a stdio-capable MCP host with absolute paths, replacing the placeholders:

```json
{
  "mcpServers": {
    "nocap": {
      "command": "/absolute/path/to/Meta/.venv/bin/python",
      "args": ["-m", "receipts.mcp_server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/Meta",
        "RECEIPTS_DATA_DIR": "/absolute/path/to/Meta/data/snapshot"
      }
    }
  }
}
```

No key is required. The host launches and stops the subprocess; do not wait for the stdio command to
exit as though it were a one-shot CLI. No HTTP listener or public MCP deployment is created. The host
may send its conversation and tool results to its own model provider; this server's local execution
does not override the host's privacy policy.

## Tools

| Tool | Inputs | Output |
|---|---|---|
| `nocap_coverage` | None | Companies, canonical metrics and scope/privacy limits. |
| `nocap_search_evidence` | `query`: exact entity, metric; optional period end, basis, segment, timezone-aware `as_of`, `limit` and `offset` | Filing versions with receipt provenance, `total`, `next_offset`, `truncated`. |
| `nocap_verify_claim` | `query`: one fully typed `claim`; optional context and timezone-aware `as_of` | Deterministic status, reason, explanation, expected value and evidence. |

Every tool advertises read-only, non-destructive, idempotent, closed-world annotations. Query and claim inputs reject
unknown fields, non-finite values, oversized text, invalid pagination, naive timestamps and numeric
fields that contradict their quoted value. The verifier owns verdicts; the input cannot set a status.
Search returns at most 50 receipts and bounds returned receipt text. `next_offset` enables continuation;
truncated locators are marked explicitly. Verification also reports `evidence_total` and
`evidence_truncated` when its receipt list is bounded.

Example search tool arguments:

```json
{
  "query": {
    "entity": "NVIDIA",
    "metric": "revenue",
    "period_end": "2026-07-26",
    "as_of": "2026-08-26T21:00:00Z",
    "limit": 5
  }
}
```

Search results are evidence candidates, not a verdict. They may contain multiple periods, filing
versions or bases, depending on the filters. Construct the proposed claim from the actual draft and
pass it to `nocap_verify_claim`; do not use a found number to silently replace the draft's number.
The complete claim schema is discoverable through MCP `tools/list` and follows
[`receipts.types.Claim`](../receipts/types.py). Its character offsets describe `span_text`, and
`value_text` must appear verbatim inside that span. A caller can still mislabel the surrounding prose;
typed verification does not prove faithful extraction or complete numeric coverage of an external draft.

## Validation

```bash
.venv/bin/python -m pytest -q tests/test_mcp_server.py
.venv/bin/python eval/eval_mcp.py --data-dir data/snapshot
```

The protocol tests use the official client, including a real stdio subprocess, handshake, tools/list,
tool calls, structured output and shutdown. They exercise request bounds, numerical consistency,
unknown-company errors, as-of filtering and SQLite read-only behavior. Core API installations without
the optional SDK skip this dedicated test module; install `requirements-mcp.txt` to run it.

The real subprocess test also installs a child-process audit hook that rejects socket connections and
DNS lookups, and checks that no attempt was recorded. Readiness is the SDK's actual initialization
handshake, not a sleep. Integration checks allow 30 seconds per stdio request and 90 seconds overall:
these are hang bounds, not startup performance targets. The dev runner records measured handshake time;
the subprocess test exposes it as `mcp_stdio_startup_s` in pytest's report properties. This accommodates
fresh Python/SDK imports under local or CI CPU contention without removing the deadline or correctness checks.

The initial validation passed **28 dedicated tests** and **10/10 development workflows** with **21 actual
stdio tool calls**, no model calls and no SEC requests. The [retained workflow record](../eval/mcp_dev_result.json)
identifies the database and scenario hashes; its initial run used the expanded local evidence database.

The ten fixed development scenarios are deterministic tool-workflow checks, **not an LLM agent accuracy
benchmark**, independent human labeling or held-out evaluation. They cover value, scale, accounting basis,
growth period, guidance, filing acceptance and Tesla's XBRL-only extension. Human-readable tasks are in
[`eval/mcp_dev.xml`](../eval/mcp_dev.xml); each workflow searches relevant evidence before verification.
The harness supplies the typed proposals itself and never creates a model client.

References checked on 2026-10-29: [official Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[tools and annotations](https://py.sdk.modelcontextprotocol.io/servers/tools/),
[structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/) and
[stdio client transport](https://py.sdk.modelcontextprotocol.io/client/transports/).
