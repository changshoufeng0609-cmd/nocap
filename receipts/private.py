"""Explicit Modal-only extraction without persistent draft or response caches.

Private means operator-controlled inference on Modal, not offline processing. The
browser, API host and operator's Modal deployment necessarily handle the draft.
"""
from __future__ import annotations

import time
import re
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.parse import urlsplit

import openai
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from receipts import extract, stream

PRIVATE_HEADERS = {"Cache-Control": "no-store, no-transform", "Pragma": "no-cache",
                   "X-Accel-Buffering": "no", "Referrer-Policy": "no-referrer"}


class PrivateUnavailable(Exception):
    pass


class PrivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    context: Optional[str] = None
    as_of: Optional[str] = None


def private_backend(env: Optional[Mapping[str, str]] = None) -> extract.Backend:
    """Ignore shared extractor overrides; only an explicit operator Modal endpoint is legal."""
    env = extract.settings_env() if env is None else env
    if env.get("RECEIPTS_PRIVATE_MODE", "").strip().lower() not in ("1", "true", "yes"):
        raise PrivateUnavailable("Private mode is not enabled on this server.")
    endpoint = env.get("RECEIPTS_MODAL_BASE_URL", "").strip().rstrip("/")
    try:
        url = urlsplit(endpoint)
        # Web Functions use modal.run; app.server uses a regional modal.direct
        # hostname. Keep the latter to the documented Server routing regions.
        host = url.hostname or ""
        modal_host = host.endswith(".modal.run") or bool(re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.(?:us-east|us-west|ca-central|eu-west|ap-south|ap-southeast-2)\.modal\.direct", host))
        valid = (url.scheme == "https" and modal_host
                 and url.path == "/v1" and not url.username and not url.password
                 and not url.query and not url.fragment and url.port is None)
    except ValueError:
        valid = False
    if not valid:
        raise PrivateUnavailable("Private mode needs the operator's authenticated Modal deployment.")
    return extract.make_backend("modal", base_url=endpoint)


def configured_private_backend() -> extract.Backend:
    backend = private_backend()
    try:
        extract._load_api_key(backend)
    except extract.ExtractionError:
        raise PrivateUnavailable("Private mode needs the operator's Modal proxy credentials.") from None
    return backend


def make_private_client(backend: extract.Backend, *, timeout: float, transport: Any = None) -> Any:
    # An HTTP redirect must not forward the draft POST to a different provider.
    # The OpenAI default client follows redirects; explicitly disable that here.
    http = openai.DefaultHttpxClient(follow_redirects=False, transport=transport)
    try:
        return extract.make_client(backend=backend, timeout=timeout, http_client=http)
    except Exception:
        http.close()
        raise


def private_events(text: str, *, backend: extract.Backend, client: Any, store: Any,
                   stats: stream.CheckStats, context: Optional[str], as_of: Optional[str],
                   timeout_s: float, deadline: float,
                   before_model_call: Callable[[float], None]) -> Iterator[stream.Event]:
    if backend.name != "modal" or backend.model != "muse-glimmer" or not backend.self_hosted:
        raise PrivateUnavailable("Private extraction requires the operator's Modal Glimmer backend.")
    events = stream.check_events(
        text, draft_hash="", as_of=as_of, context=context, store=store, stats=stats,
        backend=backend, client=client, use_cache=False, cache_dir=None, extra_cache_dirs=(),
        concurrency=1, timeout_s=timeout_s, max_attempts=1, deadline=deadline,
        before_model_call=before_model_call)
    try:
        for name, data in events:
            if name == "meta":
                data = {k: v for k, v in data.items() if k != "draft_hash"}
                data.update(private=True, backend="modal", model=backend.model,
                            hosting="Operator's Modal workspace", persistence="no_response_cache")
            elif name == "done":
                data = {**data, "model": backend.model, "private": True,
                        "billing_note": "Estimated admission only; Modal bills GPU time separately."}
            elif name == "error":
                data = {**data, "message": "The private check could not finish. No hosted-model fallback was used."}
            yield name, data
    finally:
        events.close()
        # The ordinary stream retains emitted events for its optional persistent
        # cache. Private mode has no such cache; release this extra in-memory copy.
        stats.events.clear()


def create_private_router(*, settings: Any, limiter: Any, ledger: Any,
                          get_store: Callable[[], Any],
                          backend_factory: Callable[[], extract.Backend] = configured_private_backend,
                          client_factory: Optional[Callable[[extract.Backend], Any]] = None) -> APIRouter:
    """Mount once inside create_app, using its existing shared ledger and rate limiter."""
    from receipts.api import OperationBudget, Rejected, _from_events, _from_thread, _reserve, client_ip

    router = APIRouter()

    def fail(message: str, retryable: bool = False) -> StreamingResponse:
        return StreamingResponse(_from_events([("error", {"message": message, "retryable": retryable,
            "cost_usd": 0.0, "cost_is_estimate": False, "model_calls": 0})]),
            media_type="text/event-stream", headers=PRIVATE_HEADERS)

    @router.get("/api/private/status")
    async def status() -> JSONResponse:
        try:
            backend_factory()
            available, message = True, "Configured for the operator's Modal workspace; live availability is not guaranteed."
        except Exception:
            available, message = False, "Private mode is unavailable until the operator configures Modal Glimmer."
        return JSONResponse({"available": available, "backend": "modal", "model": "muse-glimmer",
            "message": message, "offline": False, "response_cache": False}, headers=PRIVATE_HEADERS)

    @router.post("/api/private/check")
    async def check(request: Request) -> StreamingResponse:
        started = time.monotonic()
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.max_body_bytes:
                return fail("The private request is too large.")
        try:
            req = PrivateBody.model_validate_json(bytes(body))
            if not req.text.strip() or len(req.text) > settings.max_chars:
                return fail(f"Paste a draft of 1–{settings.max_chars:,} characters.")
            if req.context is not None and len(req.context) > 300:
                return fail("Company and period must be 300 characters or fewer.")
            as_of = stream.canonical_as_of(req.as_of)
        except (ValidationError, ValueError):
            return fail("Use text, optional context and an ISO 8601 as-of time only.")
        try:
            backend = backend_factory()
        except Exception:
            return fail("Private mode is unavailable. No draft was sent to a model.")
        if limiter.acquire(client_ip(request)) is not None:
            return fail("Too many live checks. Wait before trying private mode again.", True)
        context = stream.normalize_context(req.context)
        try:
            store = get_store()
            estimate = extract.extraction_estimate(req.text, context, backend=backend, max_attempts=1)
            token = _reserve(ledger, estimate)
        except Rejected as exc:
            return fail(exc.message, exc.retryable)
        except Exception:
            return fail("Private checks are unavailable because evidence or spending controls could not be loaded.")
        if token is None:
            return fail("Today's live-check allowance is exhausted. No draft was sent to a model.")
        stats = stream.CheckStats()
        budget = OperationBudget(ledger, token, estimate)
        try:
            client = (client_factory(backend) if client_factory else
                      make_private_client(backend, timeout=settings.model_timeout_s))
        except Exception:
            budget.finish(stats)
            return fail("Private mode could not connect to its configured backend. No fallback was used.")
        events = private_events(req.text, backend=backend, client=client, store=store, stats=stats,
            context=context, as_of=as_of, timeout_s=settings.model_timeout_s,
            deadline=started + settings.check_deadline_s, before_model_call=budget.before_call)

        def finish() -> None:
            try:
                budget.finish(stats)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        return StreamingResponse(_from_thread(events, finish, settings.keepalive_s, stats, budget),
                                 media_type="text/event-stream", headers=PRIVATE_HEADERS)

    return router
