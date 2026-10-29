"""Local, read-only NoCap MCP tools. No model calls, SEC fetches or draft persistence.

Install requirements-mcp.txt, then launch with ``python -m receipts.mcp_server``.
The host owns its model/data policy; this subprocess only reads the configured evidence store.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Callable, Iterator, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from receipts.extract import parse_value_text
from receipts.store import Store, to_utc
from receipts.types import Basis, Claim, Fact, Metric
from receipts.verify import verify

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ImportError as exc:
    raise ImportError("Install the optional server dependencies: python -m pip install -r requirements-mcp.txt") from exc


RECEIPT_CHAR_LIMIT = 28_000
RECEIPT_LIMIT = 50


class AsOfInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    as_of: Optional[datetime] = Field(
        default=None, description="Only evidence accepted by this timezone-aware instant; e.g. 2026-08-26T20:00:00Z.")

    @field_validator("as_of")
    @classmethod
    def aware_cutoff(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must include a timezone, for example 2026-08-26T20:00:00Z")
        return to_utc(value) if value is not None else None


class SearchInput(AsOfInput):
    entity: str = Field(min_length=1, max_length=160, description="Exact company name, alias or ticker, e.g. NVIDIA or NVDA.")
    metric: Metric = Field(description="Canonical metric; discover the supported vocabulary with nocap_coverage.")
    period_end: Optional[date] = Field(default=None, description="Optional exact fiscal period end, e.g. 2026-07-26.")
    basis: Optional[Basis] = Field(default=None, description="GAAP, non-GAAP, or null for both.")
    segment: Optional[str] = Field(default=None, max_length=160, description="Null for consolidated; '*' for all segments; otherwise exact segment label.")
    limit: int = Field(default=10, ge=1, le=RECEIPT_LIMIT, strict=True, description="Maximum receipts in this page, 1–50.")
    offset: int = Field(default=0, ge=0, le=100_000, strict=True, description="Zero-based offset from the previous page's next_offset.")


class TypedClaim(Claim):
    """Bounded caller-proposed claim; numerical fields must agree with the quoted text."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_max_length=6000)

    id: str = Field(min_length=1, max_length=80)
    value: float = Field(allow_inf_nan=False)
    value_text: str = Field(min_length=1, max_length=160)
    span_text: str = Field(min_length=1, max_length=6000)
    span_start: int = Field(ge=0, le=1_000_000, strict=True)
    span_end: int = Field(ge=1, le=1_006_000, strict=True)
    forward_looking: bool = Field(strict=True)

    @model_validator(mode="after")
    def quoted_number_matches(self) -> "TypedClaim":
        if self.span_end - self.span_start != len(self.span_text):
            raise ValueError("span_end - span_start must equal the number of characters in span_text")
        if self.value_text not in self.span_text:
            raise ValueError("value_text must appear verbatim in span_text")
        parsed = parse_value_text(self.value_text)
        if parsed["n_numbers"] != 1 or not math.isclose(self.value, parsed["number"], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("value_text must contain exactly one number matching value")
        if parsed["scale"] is not None and parsed["scale"] != self.scale:
            raise ValueError("scale must agree with the scale explicitly written in value_text")
        if parsed["kind"] == "percent" and self.unit != "%":
            raise ValueError("a written percentage must use unit '%' ")
        if parsed["kind"] == "money" and self.unit not in {"USD", "USD/share"}:
            raise ValueError("a written dollar amount must use USD or USD/share")
        return self


class VerifyInput(AsOfInput):
    claim: TypedClaim = Field(description="One fully typed claim proposed by the caller; verdicts are computed locally.")
    context: Optional[str] = Field(default=None, max_length=600, description="Optional company and fiscal-period context; not evidence.")


@contextmanager
def _open_store(factory: Callable[[], Store]) -> Iterator[Store]:
    try:
        store = factory()
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise ToolError("Evidence is unavailable. Set RECEIPTS_DATA_DIR to a directory containing facts.sqlite and pr_facts, or use the bundled snapshot.") from exc
    try:
        yield store
    finally:
        store.close()


def _receipt_page(facts: list[Fact], offset: int, limit: int) -> dict:
    """Bound returned evidence without silently dropping the rest of the result set."""
    receipts: list[dict] = []
    used = 0
    for fact in facts[offset:offset + limit]:
        receipt = fact.model_dump(mode="json")
        if receipt["locator"] and len(receipt["locator"]) > 1500:
            receipt["locator"] = receipt["locator"][:1500]
            receipt["locator_truncated"] = True
        length = len(json.dumps(receipt, ensure_ascii=False))
        if receipts and used + length > RECEIPT_CHAR_LIMIT:
            break
        receipts.append(receipt)
        used += length
    next_offset = offset + len(receipts)
    return {"receipts": receipts, "total": len(facts), "offset": offset,
            "next_offset": next_offset if next_offset < len(facts) else None,
            "truncated": next_offset < len(facts)}


def coverage(store_factory: Callable[[], Store] = Store) -> dict:
    with _open_store(store_factory) as store:
        companies = [dict(row) for row in store._rows("SELECT cik, ticker, name FROM companies ORDER BY ticker")]
    return {"companies": companies, "metrics": list(get_args(Metric)), "model_calls": 0,
            "evidence_policy": "Local pre-ingested SEC snapshot only; coverage varies by company, period, metric and accounting basis.",
            "verification_policy": "Only deterministic verdicts are authoritative. Search receipts are evidence candidates, not a claim verdict.",
            "privacy": "This server sends no model or SEC requests and does not persist supplied claims. The MCP host may have its own model and retention policy."}


def search_evidence(query: SearchInput, store_factory: Callable[[], Store] = Store) -> dict:
    with _open_store(store_factory) as store:
        resolved = store.resolve_entity(query.entity)
        if resolved is None:
            raise ToolError("Company is not uniquely covered. Use nocap_coverage and choose an exact company name or ticker.")
        cik, ticker = resolved
        facts = store.facts(cik, query.metric, basis=query.basis, segment=query.segment,
                            period_end=query.period_end, as_of=query.as_of)
        # Keep each filing/version visible; callers must not treat the first hit as an adjudicated verdict.
        facts.reverse()
        result = _receipt_page(facts, query.offset, query.limit)
        result.update(entity=ticker, metric=query.metric,
                      as_of=query.as_of.isoformat() if query.as_of else None, model_calls=0)
        return result


def verify_claim(query: VerifyInput, store_factory: Callable[[], Store] = Store) -> dict:
    with _open_store(store_factory) as store:
        verdict = verify(query.claim, store, as_of=query.as_of, context=query.context)
    result = verdict.model_dump(mode="json", exclude={"evidence"})
    page = _receipt_page(verdict.evidence, 0, RECEIPT_LIMIT)
    result.update(evidence=page["receipts"], evidence_total=page["total"],
                  evidence_truncated=page["truncated"], model_calls=0)
    return result


def create_server(store_factory: Callable[[], Store] = Store) -> MCPServer:
    server = MCPServer(
        "nocap_mcp", version="1.0.0", log_level="WARNING",
        instructions="Use nocap_coverage to discover scope. Search SEC evidence, then verify each proposed numeric claim. Search results alone are not a verdict. Guidance and ambiguity remain unverifiable. No trading recommendations.")
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                  idempotent_hint=True, open_world_hint=False)

    @server.tool(name="nocap_coverage", annotations=annotations, structured_output=True)
    async def scope() -> dict[str, Any]:
        """List locally covered companies, canonical metrics and verification/privacy limits. No network calls."""
        return await asyncio.to_thread(coverage, store_factory)

    @server.tool(name="nocap_search_evidence", annotations=annotations, structured_output=True)
    async def search(query: SearchInput) -> dict[str, Any]:
        """Find SEC receipts by exact entity/metric, with optional period, basis, segment and as_of filters.

        Returns receipts, total, offset, next_offset and truncated. Repeat with next_offset to page.
        All filing versions remain visible; a retrieved value alone is not a verified claim.
        Use nocap_verify_claim for adjudication. Unsupported companies return an actionable tool error.
        """
        return await asyncio.to_thread(search_evidence, query, store_factory)

    @server.tool(name="nocap_verify_claim", annotations=annotations, structured_output=True)
    async def check(query: VerifyInput) -> dict[str, Any]:
        """Verify one caller-proposed typed numeric claim against locally stored SEC evidence.

        Numeric value, explicit units/scales and spans must agree with the supplied quotation.
        Returns status, reason, explanation, expected_value, evidence and evidence truncation metadata.
        No LLM extraction occurs. Guidance/ambiguity returns unverifiable; as_of prevents look-ahead.
        This does not attest that the caller quoted its external draft faithfully or extracted all numbers.
        """
        return await asyncio.to_thread(verify_claim, query, store_factory)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="NoCap local read-only MCP server (stdio; no model calls).")
    parser.parse_args()
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
