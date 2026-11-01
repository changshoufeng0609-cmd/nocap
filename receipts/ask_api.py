"""Ask and whole-filing routes; installed by the existing app with its shared guards."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from receipts import ask, extract, grounding, stream
from receipts import recordings as recorded
from receipts.spend import BudgetUnavailable, DailyCount, Reservation

log = logging.getLogger(__name__)


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=ask.MAX_QUESTION_CHARS)
    as_of: Optional[str] = Field(default=None, max_length=80)


class GroundingBody(AskBody):
    filing_id: str = Field(min_length=1, max_length=64)


def create_router(*, settings: Any, get_store: Callable[[], Any], get_client: Callable[[], Any],
                  limiter: Any, ledger: Any, filing_directory: Optional[Path] = None,
                  recordings: Optional[recorded.Recordings] = None,
                  filing_quota: Optional[DailyCount] = None) -> APIRouter:
    """filing_quota: today's new whole-filing questions across instances (default: from settings and the
    configured ledger storage). Ask and quote search also leave settings.check_fix_reserve_usd of the daily
    budget to Check and Fix."""
    # Import only at installation time: api.py has defined these helpers before calling create_router.
    from receipts.api import (LEDGER_UNAVAILABLE_MESSAGE, REFUSAL_REASONS, OperationBudget, Rejected,
                              _filing_quota, _from_events, _from_thread, _require_live, _reserve_async,
                              client_ip, sse_response)

    router = APIRouter()
    texts = grounding.FilingTexts(filing_directory or settings.data_dir / "filing_texts")
    quota = filing_quota if filing_quota is not None else _filing_quota(settings)

    def reject(message: str, retryable: bool = False, reason: Optional[str] = None):
        data = {"message": message, "retryable": retryable, "cost_usd": 0, "model_calls": 0,
                "cost_is_estimate": False}
        if reason in REFUSAL_REASONS:
            data["reason"] = reason
        return sse_response(_from_events([("error", data)]))

    def settle_slot(slot: Optional[Reservation], *, used: bool) -> None:
        """Count a dispatched question; return the slot of one that never reached the model."""
        if slot is None:
            return
        try:
            (quota.keep if used else quota.release)(slot)
        except BudgetUnavailable:
            log.error("whole-filing question count could not be settled; the slot stays used")

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
        # Every company with its most recent fiscal periods (ask.OPTIONS_PERIODS): bounded by the number of companies.
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
            entries = texts.entries()
        except ask.AskError:
            return JSONResponse({"message": "The local filing-text index is unavailable."}, status_code=503)
        # Questions with a recorded live answer: offered as samples that replay at $0.
        listed = {entry["id"] for entry in entries}
        samples = [{"filing_id": r["request"]["filing_id"], "question": r["request"]["question"],
                    "as_of": r["request"].get("as_of"), "recorded_utc": r["recorded_utc"]}
                   for r in (recordings.entries("grounding") if recordings is not None else [])
                   if r["request"].get("filing_id") in listed and r["request"].get("question")]
        return {"filings": entries, "samples": samples}

    async def dispatch(request: Request, *, is_grounding: bool):
        started = time.monotonic()
        try:
            req = await parse(request, GroundingBody if is_grounding else AskBody)
            # A built-in whole-filing sample replays its recorded live run at $0, even while live work is paused.
            record = (recordings.load("grounding", recorded.grounding_key(req.filing_id, req.question, req.as_of))
                      if is_grounding and recordings is not None else None)
            if record is not None:
                return sse_response(_from_events(recorded.replay(record)))
            _require_live(settings)
            store = get_store()
            if is_grounding:
                filing, text = await asyncio.to_thread(texts.load, req.filing_id, as_of=req.as_of)
                proposal = grounding.build_request(req.question, filing, text)
            else:
                proposal = ask.build_request(req.question, available_companies=ask.lookup_companies(store))
        except ask.AskError as exc:
            return reject(str(exc))
        except Rejected as exc:
            return reject(exc.message, exc.retryable, exc.reason)
        except Exception:
            return reject("The local evidence needed for this question is unavailable.")
        if limiter.acquire(client_ip(request, settings)) is not None:
            return reject("Too many live requests from your network. Try again in a few minutes.", True, "rate_limited")
        estimate = extract.estimate_request_cost(proposal, ask.SPARK)
        slot: Optional[Reservation] = None
        if is_grounding:
            # A whole filing is the largest request: at most a fixed number of new ones per UTC day.
            try:
                slot = await asyncio.to_thread(quota.acquire)
            except BudgetUnavailable:
                return reject(LEDGER_UNAVAILABLE_MESSAGE, True, "budget_unavailable")
            if slot is None:
                limit = quota.limit
                return reject((f"NoCap has answered today's {limit} new whole-filing question{'s' if limit != 1 else ''}. "
                               "Recorded sample questions still replay at $0, and new questions resume tomorrow (UTC)."
                               if limit > 0 else "New whole-filing questions are off on this server. Recorded sample "
                               "questions still replay at $0."), False, "filing_question_limit")
        try:
            # The end of the daily budget stays with Check and Fix.
            token = await _reserve_async(ledger, estimate, settings.check_fix_reserve_usd)
        except Rejected as exc:
            await asyncio.to_thread(settle_slot, slot, used=False)
            return reject(exc.message, exc.retryable, exc.reason)
        if token is None:
            await asyncio.to_thread(settle_slot, slot, used=False)
            return reject("Today's live budget for new questions is used up; NoCap keeps the rest for Check and Fix. "
                          "Recorded sample questions and the saved examples still work at $0, and new questions "
                          "resume tomorrow (UTC).", False, "daily_budget")
        budget = OperationBudget(ledger, token, estimate)
        stats = ask.AskStats()
        common = dict(request=proposal, stats=stats, client_factory=get_client,
                      before_model_call=budget.before_call, as_of=req.as_of,
                      timeout_s=settings.model_timeout_s, deadline=started + settings.check_deadline_s)
        events = (grounding.grounding_events(req.question, filing=filing, text=text, **common) if is_grounding
                  else ask.ask_events(req.question, store=store, **common))

        def finish() -> None:
            try:
                budget.finish(stats)
            finally:
                settle_slot(slot, used=budget.started_calls > 0)

        return sse_response(_from_thread(events, finish, settings.keepalive_s, stats, budget))

    @router.post("/api/ask")
    async def ask_question(request: Request):
        return await dispatch(request, is_grounding=False)

    @router.post("/api/grounding")
    async def ground_question(request: Request):
        return await dispatch(request, is_grounding=True)

    return router
