"""Ask and whole-filing routes; installed by the existing app with its shared guards."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from receipts import ask, extract, grounding, stream


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=ask.MAX_QUESTION_CHARS)
    as_of: Optional[str] = Field(default=None, max_length=80)


class GroundingBody(AskBody):
    filing_id: str = Field(min_length=1, max_length=64)


def create_router(*, settings: Any, get_store: Callable[[], Any], get_client: Callable[[], Any],
                  limiter: Any, ledger: Any, filing_directory: Optional[Path] = None) -> APIRouter:
    # Import only at installation time: api.py has defined these helpers before calling create_router.
    from receipts.api import OperationBudget, Rejected, _from_events, _from_thread, _reserve, client_ip, sse_response

    router = APIRouter()
    texts = grounding.FilingTexts(filing_directory or settings.data_dir / "filing_texts")

    def reject(message: str, retryable: bool = False):
        return sse_response(_from_events([("error", {"message": message, "retryable": retryable,
                                                       "cost_usd": 0, "model_calls": 0,
                                                       "cost_is_estimate": False})]))

    async def parse(request: Request, body_type: type[AskBody]) -> AskBody:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 16_384:
                raise ask.AskError("The question request is too large.")
            body.extend(chunk)
        try:
            req = body_type.model_validate_json(bytes(body))
            req.question = ask.validate_question(req.question)
            req.as_of = stream.canonical_as_of(req.as_of)
            return req
        except (ValueError, ValidationError):
            raise ask.AskError("Send a question and an optional ISO 8601 as_of time; choose a listed filing for quote search.") from None

    @router.get("/api/ask/options")
    def options():
        try:
            available = ask.companies(get_store())
        except Exception:
            return JSONResponse({"message": "The evidence database is unavailable."}, status_code=503)
        return {"companies": available, "metrics": list(ask.ASK_METRICS), "examples": [
            "What was NVIDIA's GAAP revenue in Q2 FY2027?",
            "What was Meta's GAAP net income in Q3 FY2026?",
            "What was Apple's GAAP diluted EPS in Q3 FY2026?",
        ]}

    @router.get("/api/grounding/filings")
    def filings():
        try:
            return {"filings": texts.entries()}
        except ask.AskError:
            return JSONResponse({"message": "The local filing-text index is unavailable."}, status_code=503)

    async def dispatch(request: Request, *, is_grounding: bool):
        started = time.monotonic()
        try:
            req = await parse(request, GroundingBody if is_grounding else AskBody)
            store = get_store()
            if is_grounding:
                filing, text = await asyncio.to_thread(texts.load, req.filing_id, as_of=req.as_of)
                proposal = grounding.build_request(req.question, filing, text)
            else:
                proposal = ask.build_request(req.question, available_companies=ask.companies(store))
        except ask.AskError as exc:
            return reject(str(exc))
        except Exception:
            return reject("The local evidence needed for this question is unavailable.")
        if limiter.acquire(client_ip(request)) is not None:
            return reject("Too many live requests from your network. Try again in a few minutes.", True)
        estimate = extract.estimate_request_cost(proposal, ask.SPARK)
        try:
            token = _reserve(ledger, estimate)
        except Rejected as exc:
            return reject(exc.message, exc.retryable)
        if token is None:
            return reject("NoCap has reached today's budget for new questions. Cached Check examples still work.")
        budget = OperationBudget(ledger, token, estimate)
        stats = ask.AskStats()
        common = dict(request=proposal, stats=stats, client_factory=get_client,
                      before_model_call=budget.before_call, as_of=req.as_of,
                      timeout_s=settings.model_timeout_s, deadline=started + settings.check_deadline_s)
        events = (grounding.grounding_events(req.question, filing=filing, text=text, **common) if is_grounding
                  else ask.ask_events(req.question, store=store, **common))
        return sse_response(_from_thread(events, lambda: budget.finish(stats), settings.keepalive_s, stats, budget))

    @router.post("/api/ask")
    async def ask_question(request: Request):
        return await dispatch(request, is_grounding=False)

    @router.post("/api/grounding")
    async def ground_question(request: Request):
        return await dispatch(request, is_grounding=True)

    return router
