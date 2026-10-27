"""Streaming checks for POST /api/check: the server-sent event sequence of docs/api-contract.md.

    meta, then per passage as soon as its extraction finishes: its claim events and their verdict events, then done.
    Any failure ends the stream with an error event instead of done.

check_events() runs one live check: Muse Spark proposes the claims (receipts.extract.iter_extract) and deterministic
code decides each verdict (receipts.verify.verify) right after its claim is sent. replay() re-sends a recorded
check with cached: true. EventCache stores recorded event lists under draft_key(text, as_of, context), tagged with a
fingerprint of the evidence data and of the code that decides verdicts, so a stale result is never replayed.

Nothing here logs draft text: log lines carry the draft hash, claim ids and error types only.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from pydantic import ValidationError

from receipts import extract as _extract
from receipts import verify as _verify
from receipts.types import Claim, EvidenceStore, Verdict

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL = _extract.MODEL
STATUSES = ("verified", "wrong", "unverifiable", "approximate")
CACHE_VERSION = 1
KEEPALIVE = ": keep-alive\n\n"  # an SSE comment; EventSource-style parsers ignore it
# The modules whose code decides what a check returns. A stream recorded under other code is not replayed.
RESULT_MODULES = ("types.py", "extract.py", "periods.py", "verify.py", "store.py", "stream.py")

Event = tuple[str, dict[str, Any]]

MSG_INTERNAL = "Receipts hit an internal error while checking this draft. Please try again."
MSG_NO_MODEL = "Live checks are unavailable right now because the model API is not configured. The examples still work."


def sse(event: str, data: Any) -> str:
    """One server-sent event: an event line and one single-line JSON data line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


# ---------------------------------------------------------------------------
# Request normalization and the cache key
# ---------------------------------------------------------------------------
def canonical_as_of(as_of: Any) -> Optional[str]:
    """as_of as 'YYYY-MM-DDTHH:MM:SSZ' (UTC, whole seconds), or None. Raises ValueError if it isn't an ISO time."""
    if as_of is None or (isinstance(as_of, str) and not as_of.strip()):
        return None
    try:
        dt = _verify.to_utc(as_of)
    except (TypeError, AttributeError) as e:
        raise ValueError(f"not an ISO 8601 time: {type(as_of).__name__}") from e
    assert dt is not None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_context(context: Optional[str]) -> Optional[str]:
    """The request's context line. None or blank means: infer it from the first passage."""
    return (context or "").strip() or None


def draft_key(text: str, as_of: Optional[str], context: Optional[str]) -> str:
    """The cache key, also sent as draft_hash: sha256 over text, as_of and context (canonical forms), joined with a
    separator so the three parts can't run together."""
    blob = "\x1f".join((text, as_of or "", context or ""))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fingerprints: a recorded stream is replayed only for the data and code that produced it
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def code_fingerprint() -> str:
    h = hashlib.sha256()
    for name in RESULT_MODULES:
        path = REPO_ROOT / "receipts" / name
        h.update(name.encode())
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def data_fingerprint(data_dir: Path) -> str:
    """The content fingerprint in <data_dir>/manifest.json (scripts/build_snapshot.py writes it), else the size and
    mtime of facts.sqlite and of every press-release JSONL, so editing data/ invalidates recorded streams."""
    data_dir = Path(data_dir)
    try:
        fp = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8")).get("fingerprint")
        if isinstance(fp, str) and fp:
            return fp
    except (OSError, ValueError, AttributeError):
        pass
    h = hashlib.sha256()
    for path in [data_dir / "facts.sqlite", *sorted((data_dir / "pr_facts").glob("*/*.jsonl"))]:
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{path.relative_to(data_dir).as_posix()}:{st.st_size}:{st.st_mtime_ns};".encode())
    return "stat-" + h.hexdigest()[:16]


def cache_fingerprint(data_dir: Path) -> str:
    return hashlib.sha256(f"{data_fingerprint(data_dir)}|{code_fingerprint()}".encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Recorded streams
# ---------------------------------------------------------------------------
def valid_events(raw: Any) -> Optional[list[Event]]:
    """A recorded event list as (event, data) pairs, or None unless it is a complete, contract-shaped check:
    meta first, done last, and every claim and verdict valid under today's receipts.types."""
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    events: list[Event] = []
    try:
        for item in raw:
            name, data = item["event"], item["data"]
            if not isinstance(name, str) or not isinstance(data, dict):
                return None
            if name == "claim":
                Claim.model_validate(data["claim"])
            elif name == "verdict":
                Verdict.model_validate(data["verdict"])
            elif name not in ("meta", "done"):
                return None
            events.append((name, data))
    except (KeyError, TypeError, ValidationError):
        return None
    if events[0][0] != "meta" or events[-1][0] != "done":
        return None
    return events


class EventCache:
    """Recorded event lists, one JSON file per draft key: <dir>/<key>.json. Reads try the writable directory, then
    the read-only ones (the pre-baked snapshot); writes go to write_dir only, atomically, and never fail a check."""

    def __init__(self, write_dir: Optional[Path], read_dirs: Sequence[Path] = ()):
        self.write_dir = Path(write_dir) if write_dir else None
        dirs = ([self.write_dir] if self.write_dir else []) + [Path(d) for d in read_dirs]
        self.read_dirs = list(dict.fromkeys(dirs))

    def get(self, key: str, fingerprint: str) -> Optional[list[Event]]:
        for d in self.read_dirs:
            try:
                rec = json.loads((d / f"{key}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (not isinstance(rec, dict) or rec.get("version") != CACHE_VERSION or rec.get("key") != key
                    or rec.get("fingerprint") != fingerprint):
                continue
            events = valid_events(rec.get("events"))
            if events:
                return events
        return None

    def put(self, key: str, fingerprint: str, events: Sequence[Event]) -> bool:
        if self.write_dir is None:
            return False
        record = {"version": CACHE_VERSION, "key": key, "fingerprint": fingerprint, "model": MODEL,
                  "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "events": [{"event": n, "data": d} for n, d in events]}
        return write_json_atomic(self.write_dir / f"{key}.json", record)


def write_json_atomic(path: Path, payload: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem[:16]}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return True
    except OSError as e:  # a read-only deploy still answers; it just doesn't remember
        log.warning("could not write %s: %s", path.name, type(e).__name__)
        return False


def replay(events: Iterable[Event]) -> Iterator[Event]:
    """A recorded check, event by event, with meta.cached set to true. done keeps the original latency and cost."""
    for name, data in events:
        yield name, ({**data, "cached": True} if name == "meta" else data)


# ---------------------------------------------------------------------------
# A live check
# ---------------------------------------------------------------------------
@dataclass
class CheckStats:
    """Filled in by check_events() while it runs: what the check cost and whether it completed."""

    cost_usd: float = 0.0
    complete: bool = False          # done was sent: every passage was extracted and every claim verified
    passages: int = 0
    passages_failed: int = 0
    model_calls: int = 0
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATUSES, 0))
    events: list[Event] = field(default_factory=list)  # every event sent, in order, for the cache
    error: Optional[str] = None     # short internal reason when the stream ended with an error event

    @property
    def claims(self) -> int:
        return sum(self.counts.values())


def verdict_for(claim: Claim, store: EvidenceStore, as_of: Optional[datetime], context: Optional[str],
                draft_hash: str = "") -> Verdict:
    """receipts.verify.verify with the receipts.check fallback: one bad claim must not sink the draft."""
    try:
        return _verify.verify(claim, store, as_of=as_of, context=context)
    except Exception as e:
        log.error("verify failed draft=%s claim=%s error=%s", draft_hash[:12], claim.id, type(e).__name__)
        return Verdict(claim_id=claim.id, status="unverifiable", reason="no_evidence", as_of=as_of,
                       explanation=f"Receipts hit an internal error ({type(e).__name__}) checking this claim.")


def check_events(text: str, *, draft_hash: str, as_of: Optional[str], context: Optional[str],
                 store: EvidenceStore, stats: Optional[CheckStats] = None, started: Optional[float] = None,
                 **extract_kwargs: Any) -> Iterator[Event]:
    """One live check as (event, data) pairs in contract order.

    as_of: canonical_as_of() output or None. context: None infers the context line from the first passage, exactly
    like receipts.check.check_detailed, so the API and the CLI see the same claims and verdicts. extract_kwargs go
    to receipts.extract.iter_extract (client, cache_dir, extra_cache_dirs, use_cache, timeout_s, deadline, ...).
    started: a time.monotonic() value for done.latency_s (defaults to now).
    """
    stats = stats if stats is not None else CheckStats()
    t0 = time.monotonic() if started is None else started
    as_of_dt = _verify.to_utc(as_of)

    def emit(name: str, data: dict[str, Any]) -> Event:
        stats.events.append((name, data))
        return name, data

    stats.passages = len(_extract.split_passages(text))
    yield emit("meta", {"draft_hash": draft_hash, "cached": False, "passages": stats.passages, "as_of": as_of})
    ctx = _extract.infer_context(text) if context is None else (context.strip() or None)
    try:
        for res in _extract.iter_extract(text, ctx if ctx is not None else "", **extract_kwargs):
            stats.cost_usd += res.cost_usd
            stats.model_calls += not res.call.get("cached")
            if not res.ok:
                stats.passages_failed += 1
                log.warning("passage failed draft=%s passage=%d status=%s", draft_hash[:12], res.passage,
                            res.call.get("status"))
                continue
            for claim in res.claims:
                yield emit("claim", {"claim": claim.model_dump(mode="json")})
            for claim in res.claims:
                verdict = verdict_for(claim, store, as_of_dt, ctx, draft_hash)
                stats.counts[verdict.status] += 1
                yield emit("verdict", {"claim_id": claim.id, "verdict": verdict.model_dump(mode="json")})
    except _extract.ExtractionError:
        stats.error = "no_api_key"
        yield emit("error", {"message": MSG_NO_MODEL, "retryable": False})
        return
    except Exception as e:  # the stream must end with an event, whatever broke
        stats.error = type(e).__name__
        log.error("check failed draft=%s error=%s", draft_hash[:12], type(e).__name__)
        yield emit("error", {"message": MSG_INTERNAL, "retryable": True})
        return
    if stats.passages_failed:
        stats.error = "passages_failed"
        n, m = stats.passages_failed, stats.passages
        yield emit("error", {"message": f"The model didn't answer for {n} of {m} passage{'s' if m != 1 else ''}, so "
                                        f"{'their' if n != 1 else 'its'} numbers weren't checked. Please try again.",
                             "retryable": True})
        return
    stats.complete = True
    yield emit("done", {"counts": dict(stats.counts), "latency_s": round(time.monotonic() - t0, 2),
                        "cost_usd": round(stats.cost_usd, 6), "model": MODEL})
