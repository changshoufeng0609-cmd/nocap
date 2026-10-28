"""NoCap HTTP API (docs/api-contract.md; deploy and local dev: docs/deploy.md).

    POST /api/check     server-sent events: meta, claim..., verdict..., then done (or error)
    POST /api/fix       constrained Wonder corrections, followed by deterministic re-verification
    POST /api/slide     SAM regions and Spark vision readings, verified against SEC evidence
    GET  /api/examples  demo drafts, pre-cached so the demo never waits on the model
    GET  /api/health    data snapshot date, companies and fact count

Local:   uvicorn receipts.api:app --port 8000
Vercel:  the Python service's entrypoint (vercel.json: services.api.entrypoint = "receipts.api:app")

Guards on live checks: drafts of at most 6,000 characters, a per-IP rate limit, a daily model-spend cap
(RECEIPTS_DAILY_BUDGET_USD, default $5, measured from token usage), per-call timeouts with one retry, and an overall
deadline. A replay from the cache, or a draft whose passages are all in the model-response cache, costs nothing, so
it skips the rate limit and the budget. Every failure reaches the client as an `error` event. Logs carry draft
hashes, never draft text, keys or client IPs.

Environment (all optional): RECEIPTS_DATA_DIR (default data/, or data/snapshot when data/facts.sqlite is absent),
RECEIPTS_DAILY_BUDGET_USD, RECEIPTS_RATE_LIMIT_CHECKS and RECEIPTS_RATE_LIMIT_WINDOW_S (10 per 600 s),
RECEIPTS_API_CACHE_DIR, RECEIPTS_SPARK_CACHE_DIR, RECEIPTS_NO_CACHE=1 (measure uncached latency),
RECEIPTS_CORS_ORIGINS (comma-separated, for a frontend that calls the API cross-origin). MODEL_API_KEY is read by
receipts.extract.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Iterator, Mapping, Optional, Sequence

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from receipts import extract as _extract
from receipts import stream as _stream
from receipts.stream import Event

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_CHARS = 6000
MAX_CONTEXT_CHARS = 300
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def _num(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("ignoring %s: not a number", name)
        return default
    return value if math.isfinite(value) else default


def default_data_dir(env: Mapping[str, str]) -> Path:
    """RECEIPTS_DATA_DIR, else data/ when it holds facts.sqlite (local dev), else data/snapshot (a deploy)."""
    from receipts.store import default_data_dir as resolve_data_dir
    return resolve_data_dir(env, root=REPO_ROOT)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    api_cache_dir: Optional[Path]                      # writable: recorded streams and the spend ledger
    prebaked_dirs: tuple[Path, ...] = ()               # read-only recorded streams (the snapshot)
    spark_cache_dir: Optional[Path] = None             # writable model-response cache
    spark_read_dirs: tuple[Path, ...] = ()             # read-only model-response caches (the snapshot)
    examples_paths: tuple[Path, ...] = ()
    daily_budget_usd: float = 5.0
    rate_limit_checks: int = 10
    rate_limit_window_s: float = 600.0
    max_chars: int = MAX_CHARS
    max_body_bytes: int = 64 * 1024
    model_timeout_s: float = _extract.INTERACTIVE_TIMEOUT_S
    model_attempts: int = _extract.INTERACTIVE_ATTEMPTS
    check_deadline_s: float = 100.0
    keepalive_s: float = 15.0
    est_cost_per_call_usd: float = 0.01                # reserved per model call; Spike B measured about $0.0045
    use_caches: bool = True
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        data_dir = default_data_dir(env)
        on_vercel = bool((env.get("VERCEL") or "").strip())
        tmp = Path(tempfile.gettempdir()) / "receipts"
        snapshot = REPO_ROOT / "data" / "snapshot"
        api_cache = (_path(env["RECEIPTS_API_CACHE_DIR"]) if (env.get("RECEIPTS_API_CACHE_DIR") or "").strip()
                     else tmp / "api_cache" if on_vercel else REPO_ROOT / "data" / "api_cache")
        spark_cache = (_path(env["RECEIPTS_SPARK_CACHE_DIR"]) if (env.get("RECEIPTS_SPARK_CACHE_DIR") or "").strip()
                       else tmp / "spark" if on_vercel else _extract.CACHE_DIR)
        origins = tuple(o.strip() for o in (env.get("RECEIPTS_CORS_ORIGINS") or "").split(",") if o.strip())
        return cls(
            data_dir=data_dir,
            api_cache_dir=api_cache,
            prebaked_dirs=tuple(dict.fromkeys([data_dir / "api_cache", snapshot / "api_cache"])),
            spark_cache_dir=spark_cache,
            spark_read_dirs=tuple(dict.fromkeys([data_dir / "spark_cache", snapshot / "spark_cache"])),
            examples_paths=tuple(dict.fromkeys([data_dir / "examples.json", REPO_ROOT / "examples" / "examples.json"])),
            daily_budget_usd=max(0.0, _num(env, "RECEIPTS_DAILY_BUDGET_USD", 5.0)),
            rate_limit_checks=int(_num(env, "RECEIPTS_RATE_LIMIT_CHECKS", 10)),
            rate_limit_window_s=max(1.0, _num(env, "RECEIPTS_RATE_LIMIT_WINDOW_S", 600.0)),
            use_caches=(env.get("RECEIPTS_NO_CACHE") or "").strip().lower() not in ("1", "true", "yes"),
            cors_origins=origins,
        )


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
class RateLimiter:
    """At most `limit` live checks per client IP in any `window_s` seconds (sliding window, per process: each Vercel
    instance keeps its own window). limit <= 0 disables the limit."""

    def __init__(self, limit: int, window_s: float, *, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = 10_000):
        self.limit, self.window_s, self.clock, self.max_clients = limit, window_s, clock, max_clients
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, client: str) -> Optional[float]:
        """None when a check may start now (and counts it); otherwise the seconds until one may."""
        if self.limit <= 0:
            return None
        now = self.clock()
        with self._lock:
            hits = self._hits.get(client)
            if hits is None:
                hits = self._hits[client] = deque()
                while len(self._hits) > self.max_clients:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(client)
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, self.window_s - (now - hits[0]))
            hits.append(now)
            return None


class SpendLedger:
    """Per-process daily admission guard using observed usage priced by receipts.extract.cost_usd.

    Live operations reserve an estimate before starting and settle reported usage afterward. Actual costs can
    exceed reservations, and failed or late text/Fix responses can leave usage unknown. Best-effort JSON
    persistence does not share totals across Vercel instances. This is not a provider-enforced spending cap.
    """

    def __init__(self, cap_usd: float, path: Optional[Path] = None, *,
                 today: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")):
        self.cap_usd, self.path, self._today = max(0.0, cap_usd), path, today
        self._lock = threading.Lock()
        self._reserved: dict[int, float] = {}
        self._next = 0
        self._day, self._spent = self._load()

    def _load(self) -> tuple[str, float]:
        day = self._today()
        if self.path is not None:
            try:
                rec = json.loads(self.path.read_text(encoding="utf-8"))
                if rec.get("day") == day:
                    return day, max(0.0, float(rec.get("spent_usd", 0.0)))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        return day, 0.0

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._spent = day, 0.0

    def spent_today(self) -> float:
        with self._lock:
            self._roll()
            return self._spent

    def reserve(self, estimate_usd: float) -> Optional[int]:
        """A reservation token, or None when the estimate doesn't fit in what is left of today's cap."""
        with self._lock:
            self._roll()
            if self._spent + sum(self._reserved.values()) + estimate_usd > self.cap_usd + 1e-12:
                return None
            self._next += 1
            self._reserved[self._next] = estimate_usd
            return self._next

    def settle(self, token: Optional[int], actual_usd: float) -> None:
        with self._lock:
            if token is not None:
                self._reserved.pop(token, None)
            self._roll()
            self._spent += max(0.0, actual_usd)
            day, spent = self._day, self._spent
        if self.path is not None:
            _stream.write_json_atomic(self.path, {"day": day, "spent_usd": round(spent, 6)})


def client_ip(request: Request) -> str:
    """The caller's IP: Vercel sets x-real-ip and x-forwarded-for; locally it's the socket peer or the dev proxy."""
    for header in ("x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value and value.split(",")[0].strip():
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Data: examples and health
# ---------------------------------------------------------------------------
def load_examples(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """The first examples manifest that exists: the snapshot's (texts inlined, matching its pre-baked streams), then
    examples/examples.json (texts in files next to it). Shape per docs/api-contract.md."""
    for path in paths:
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("examples", []) if isinstance(raw, dict) else raw
        out = []
        for item in items:
            text = item.get("text")
            if text is None:
                text = (path.parent / item["file"]).read_text(encoding="utf-8")
            out.append({"id": item["id"], "title": item["title"], "description": item["description"],
                        "text": text.rstrip(), "as_of": _stream.canonical_as_of(item.get("as_of")),
                        "context": _stream.normalize_context(item.get("context"))})
        return out
    return []


@functools.lru_cache(maxsize=8)
def _data_info(data_dir: Path, fingerprint: str) -> dict[str, Any]:
    db = data_dir / "facts.sqlite"
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        companies = [r[0] for r in con.execute("SELECT ticker FROM companies ORDER BY ticker")]
        n_facts = int(con.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
        row = con.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
    finally:
        con.close()
    for path in sorted((data_dir / "pr_facts").glob("*/*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            n_facts += sum(1 for line in fh if line.strip())
    built = row[0] if row else None
    try:
        built = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8")).get("built_at") or built
    except (OSError, ValueError, AttributeError):
        pass
    if not built:
        built = datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).isoformat()
    return {"ok": True, "data_snapshot": str(built)[:10], "companies": companies, "facts": n_facts}


def data_info(data_dir: Path) -> dict[str, Any]:
    return dict(_data_info(Path(data_dir), _stream.data_fingerprint(data_dir)))


# ---------------------------------------------------------------------------
# Server-sent events
# ---------------------------------------------------------------------------
class CheckBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    as_of: Optional[str] = None
    context: Optional[str] = None
    example_id: Optional[str] = None


class FixBody(BaseModel):
    """Only draft inputs cross the trust boundary; verdicts and receipts are recomputed here."""
    model_config = ConfigDict(extra="forbid")

    text: str
    as_of: Optional[str] = None
    context: Optional[str] = None


class Rejected(Exception):
    def __init__(self, reason: str, message: str, retryable: bool):
        super().__init__(reason)
        self.reason, self.message, self.retryable = reason, message, retryable


def sse_response(chunks: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(chunks, media_type="text/event-stream", headers=SSE_HEADERS)


async def _from_events(events: Iterable[Event]) -> AsyncIterator[str]:
    for name, data in events:
        yield _stream.sse(name, data)


def error_response(message: str, retryable: bool) -> StreamingResponse:
    return sse_response(_from_events([("error", {"message": message, "retryable": retryable})]))


async def _from_thread(events: Iterator[Event], on_finish: Callable[[], None], keepalive_s: float) -> AsyncIterator[str]:
    """Run the check in a worker thread and relay its events as they come, with an SSE comment every keepalive_s of
    silence. The check runs to completion (and on_finish records it) even if the client goes away."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    def put(item: Optional[str]) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # the event loop is gone (shutdown); the check still finishes and is recorded
            pass

    def produce() -> None:
        try:
            for name, data in events:
                put(_stream.sse(name, data))
        except Exception as e:  # check_events ends with its own error event; this is the last resort
            log.error("stream failed error=%s", type(e).__name__)
            put(_stream.sse("error", {"message": _stream.MSG_INTERNAL, "retryable": True}))
        finally:
            try:
                on_finish()
            except Exception as e:
                log.error("finishing a check failed error=%s", type(e).__name__)
            put(None)

    threading.Thread(target=produce, name="receipts-check", daemon=True).start()
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
        except asyncio.TimeoutError:
            yield _stream.KEEPALIVE
            continue
        if item is None:
            return
        yield item


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
def _configure_logging() -> None:
    for name in ("receipts.api", "receipts.stream"):
        lg = logging.getLogger(name)
        if not lg.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
            lg.propagate = False


def create_app(settings: Optional[Settings] = None, *, store: Any = None,
               client_factory: Optional[Callable[[], Any]] = None,
               wonder_client_factory: Optional[Callable[[], Any]] = None,
               slide_client_factory: Optional[Callable[[], Any]] = None) -> FastAPI:
    """The API. Tests pass their own settings, a store and a client factory; the module-level app reads the env."""
    settings = settings or Settings.from_env()
    _configure_logging()
    app = FastAPI(title="NoCap API", version="0.3.0", docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"],
                           allow_headers=["content-type"])
    cache = _stream.EventCache(settings.api_cache_dir if settings.use_caches else None,
                               settings.prebaked_dirs if settings.use_caches else ())
    limiter = RateLimiter(settings.rate_limit_checks, settings.rate_limit_window_s)
    ledger = SpendLedger(settings.daily_budget_usd,
                         settings.api_cache_dir / "_spend.json" if settings.api_cache_dir else None)
    make_client = client_factory or (lambda: _extract.make_client(timeout=settings.model_timeout_s))
    lock = threading.Lock()
    shared: dict[str, Any] = {"store": store, "client": None, "wonder_client": None, "slide_client": None}

    def get_store() -> Any:
        with lock:
            if shared["store"] is None:
                from receipts.store import Store

                shared["store"] = Store(db_path=settings.data_dir / "facts.sqlite",
                                        pr_dir=settings.data_dir / "pr_facts")
            return shared["store"]

    def get_client() -> Any:
        with lock:
            if shared["client"] is None:
                shared["client"] = make_client()  # ExtractionError when MODEL_API_KEY is missing
            return shared["client"]

    def get_wonder_client() -> Any:
        with lock:
            if shared["wonder_client"] is None:
                shared["wonder_client"] = (wonder_client_factory() if wonder_client_factory else
                    _extract.make_client(timeout=settings.model_timeout_s,
                                         backend=_extract.make_backend("meta", model="muse-wonder-1.0")))
            return shared["wonder_client"]

    def get_slide_client() -> Any:
        with lock:
            if shared["slide_client"] is None:
                shared["slide_client"] = (slide_client_factory() if slide_client_factory else
                    _extract.make_client(timeout=settings.model_timeout_s,
                                         backend=_extract.make_backend("meta", model="muse-spark-1.3")))
            return shared["slide_client"]

    app.state.settings, app.state.limiter, app.state.ledger, app.state.cache = settings, limiter, ledger, cache

    async def parse(request: Request, body_type: type[BaseModel] = CheckBody
                    ) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            raise Rejected("body_too_large", "The request is too large.", False)
        try:
            req = body_type.model_validate_json(body)
        except ValidationError:
            raise Rejected("bad_request", "The request must be JSON with the draft in a text field.", False)
        text = req.text
        if not text.strip():
            raise Rejected("empty", "Paste a draft to check.", False)
        if len(text) > settings.max_chars:
            raise Rejected("too_long", f"Drafts are limited to {settings.max_chars:,} characters; this one has "
                                       f"{len(text):,}.", False)
        try:
            as_of = _stream.canonical_as_of(req.as_of)
        except ValueError:
            raise Rejected("bad_as_of", "as_of must be an ISO 8601 time, for example 2026-08-26T20:00:00Z.", False)
        context = _stream.normalize_context(req.context)
        if context is not None and len(context) > MAX_CONTEXT_CHARS:
            raise Rejected("bad_context", f"context must be one short line ({MAX_CONTEXT_CHARS} characters or "
                                          f"fewer).", False)
        return text, as_of, context, (getattr(req, "example_id", None) or None)

    @app.post("/api/check")
    async def check(request: Request) -> StreamingResponse:
        started = time.monotonic()
        try:
            text, as_of, context, example_id = await parse(request)
        except Rejected as r:
            log.info("check rejected reason=%s", r.reason)
            return error_response(r.message, r.retryable)
        key = _stream.draft_key(text, as_of, context)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "", example_id or "")[:40]  # client-supplied: no log injection
        tag = f"draft={key[:12]}" + (f" example={safe_id}" if safe_id else "")
        fingerprint = _stream.cache_fingerprint(settings.data_dir)
        recorded = cache.get(key, fingerprint) if settings.use_caches else None
        if recorded:
            log.info("check %s cached=true claims=%d", tag, sum(n == "claim" for n, _ in recorded))
            return sse_response(_from_events(_stream.replay(recorded)))

        try:
            store = get_store()
        except Exception as e:
            log.error("check %s store unavailable error=%s", tag, type(e).__name__)
            return error_response("The evidence database isn't available on this server.", False)
        ctx = _extract.infer_context(text) if context is None else context
        spark_dirs = [d for d in (settings.spark_cache_dir, *settings.spark_read_dirs) if d is not None]
        try:
            calls = (_extract.passages_to_call(text, ctx or "", cache_dirs=spark_dirs) if settings.use_caches
                     else len(_extract.split_passages(text)))
        except _extract.ExtractionError:
            log.error("check %s rejected reason=model_configuration", tag)
            return error_response(_stream.MSG_NO_MODEL, False)
        token: Optional[int] = None
        client = None
        if calls:
            wait = limiter.acquire(client_ip(request))
            if wait is not None:
                minutes = max(1, math.ceil(wait / 60))
                log.info("check %s rejected reason=rate_limit", tag)
                return error_response(f"Too many live checks from your network. Try again in {minutes} minute"
                                      f"{'s' if minutes != 1 else ''}, or open one of the examples.", True)
            try:
                client = get_client()
            except _extract.ExtractionError:
                log.error("check %s rejected reason=no_api_key", tag)
                return error_response(_stream.MSG_NO_MODEL, False)
            token = ledger.reserve(calls * settings.est_cost_per_call_usd)
            if token is None:
                log.warning("check %s rejected reason=daily_budget spent=$%.4f cap=$%.2f", tag,
                            ledger.spent_today(), settings.daily_budget_usd)
                return error_response("NoCap has reached today's budget for live checks. The examples still "
                                      "work, and live checks resume tomorrow (UTC).", False)

        stats = _stream.CheckStats()
        events = _stream.check_events(
            text, draft_hash=key, as_of=as_of, context=context, store=store, stats=stats, started=started,
            client=client, cache_dir=settings.spark_cache_dir if settings.use_caches else None,
            extra_cache_dirs=settings.spark_read_dirs, use_cache=settings.use_caches,
            timeout_s=settings.model_timeout_s, max_attempts=settings.model_attempts,
            deadline=started + settings.check_deadline_s)

        def finish() -> None:
            if token is not None or stats.cost_usd:
                ledger.settle(token, stats.cost_usd)
            stored = bool(stats.complete and settings.use_caches and cache.put(key, fingerprint, stats.events))
            log.info("check %s cached=false passages=%d calls=%d claims=%d wrong=%d unverifiable=%d latency=%.2fs "
                     "cost=$%.4f stored=%s%s", tag, stats.passages, stats.model_calls, stats.claims,
                     stats.counts["wrong"], stats.counts["unverifiable"], time.monotonic() - started,
                     stats.cost_usd, stored, f" error={stats.error}" if stats.error else "")

        return sse_response(_from_thread(events, finish, settings.keepalive_s))

    @app.post("/api/fix")
    async def fix(request: Request) -> StreamingResponse:
        from receipts.rewrite import RewriteStats, fix_events

        started = time.monotonic()
        try:
            text, as_of, context, _ = await parse(request, FixBody)
        except Rejected as r:
            log.info("fix rejected reason=%s", r.reason)
            return error_response(r.message, r.retryable)
        key = _stream.draft_key(text, as_of, context)
        try:
            evidence_store = get_store()
        except Exception as e:
            log.error("fix draft=%s store unavailable error=%s", key[:12], type(e).__name__)
            return error_response("The evidence database isn't available on this server.", False)
        wait = limiter.acquire(client_ip(request))
        if wait is not None:
            return error_response("Too many live checks or fixes from your network. Try again in a few minutes.", True)
        # The original and revised draft can both need extraction. Wonder has a separate, larger allowance;
        # cached extraction saves actual spend, but never bypasses a fix request's admission guard.
        estimate = max(0.35, 0.15 + 2 * len(_extract.split_passages(text)) *
                       max(0.05, settings.est_cost_per_call_usd))
        token = ledger.reserve(estimate)
        if token is None:
            return error_response("NoCap has reached today's budget for fixes. Checks of the examples still work.",
                                  False)
        stats = RewriteStats()
        events = fix_events(
            text, as_of=as_of, context=context, store=evidence_store, stats=stats,
            extract_client_factory=get_client, wonder_client_factory=get_wonder_client,
            cache_dir=settings.spark_cache_dir, extra_cache_dirs=settings.spark_read_dirs,
            use_cache=settings.use_caches, timeout_s=settings.model_timeout_s,
            max_attempts=settings.model_attempts, deadline=started + settings.check_deadline_s)

        def finish_fix() -> None:
            ledger.settle(token, stats.cost_usd)
            log.info("fix draft=%s complete=%s calls=%d latency=%.2fs cost=$%.4f", key[:12], stats.complete,
                     stats.model_calls, time.monotonic() - started, stats.cost_usd)

        return sse_response(_from_thread(events, finish_fix, settings.keepalive_s))

    @app.post("/api/slide")
    async def slide(request: Request) -> StreamingResponse:
        from receipts import slides

        started = time.monotonic()
        body = bytearray()
        try:
            # Bound the incoming stream before retaining the entire JSON/base64 request.
            async for chunk in request.stream():
                if len(body) + len(chunk) > slides.MAX_BODY_BYTES:
                    return error_response("The upload is too large. Use a PNG or JPEG of at most 2 MiB.", False)
                body.extend(chunk)
            req = slides.SlideBody.model_validate_json(bytes(body))
        except (ValidationError, ValueError):
            return error_response("Send a PNG or JPEG in image_data_url, with optional context and as_of only.", False)
        try:
            as_of = _stream.canonical_as_of(req.as_of)
        except ValueError:
            return error_response("as_of must be an ISO 8601 time, for example 2026-10-28T00:00:00Z.", False)
        context = _stream.normalize_context(req.context)
        if context is not None and len(context) > MAX_CONTEXT_CHARS:
            return error_response("Context must be one short line of 300 characters or fewer.", False)
        try:
            image = await asyncio.to_thread(slides.decode_image, req.image_data_url)
        except slides.SlideError as exc:
            return error_response(str(exc), False)
        try:
            evidence_store = get_store()
        except Exception as exc:
            log.error("slide store unavailable error=%s", type(exc).__name__)
            return error_response("The evidence database isn't available on this server.", False)
        if limiter.acquire(client_ip(request)) is not None:
            return error_response("Too many live checks from your network. Try again in a few minutes.", True)
        token = ledger.reserve(slides.RESERVE_USD)
        if token is None:
            return error_response("NoCap has reached today's budget for slide checks. Text examples still work.", False)
        stats = slides.SlideStats()
        events = slides.slide_events(image, store=evidence_store, context=context, as_of=as_of, stats=stats,
                                     client_factory=get_slide_client, timeout_s=settings.model_timeout_s,
                                     deadline=started + settings.check_deadline_s)

        def finish_slide() -> None:
            ledger.settle(token, stats.cost_usd)
            log.info("slide complete=%s calls=%d latency=%.2fs cost=$%.4f estimated_calls=%d", stats.complete,
                     stats.model_calls, time.monotonic() - started, stats.cost_usd, stats.estimated_calls)

        return sse_response(_from_thread(events, finish_slide, settings.keepalive_s))

    @app.get("/api/examples")
    def examples() -> list[dict[str, Any]]:
        return load_examples(settings.examples_paths)

    @app.get("/api/health")
    def health() -> JSONResponse:
        try:
            return JSONResponse(data_info(settings.data_dir))
        except Exception as e:
            log.error("health check failed error=%s", type(e).__name__)
            return JSONResponse({"ok": False, "data_snapshot": None, "companies": [], "facts": 0}, status_code=503)

    return app


app = create_app()
