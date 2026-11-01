"""Recorded live results for NoCap's built-in samples: a real SEC slide, a whole-filing question and a Private check.

A request that exactly matches a recording replays the recorded event stream at $0, marked cached, even while
live requests are paused, so these samples stay available after the live window closes. Recordings come from
real runs through the same API (scripts/record_samples.py). They are not re-verified on replay; each carries the
time and source commit it was recorded with, and the client labels it as a recorded result.

The Private recording is one public synthetic draft that the operator checked on their own Modal Glimmer
deployment. It is keyed by that draft's exact text, so it never stands in for any other draft; drafts sent to
the Private route are still never written anywhere.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

KINDS = ("slide", "grounding", "private")
_DATA_URL = re.compile(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)")
_KEY = re.compile(r"[a-f0-9]{64}\Z")


def _digest(kind: str, fields: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"kind": kind, **fields}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def slide_key(image_data_url: str, context: Optional[str], as_of: Optional[str]) -> Optional[str]:
    """Keyed by the uploaded image bytes, not their base64 spelling; None for anything that isn't an image."""
    match = _DATA_URL.fullmatch(image_data_url or "")
    if not match:
        return None
    try:
        raw = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error):
        return None
    return _digest("slide", {"image_sha256": hashlib.sha256(raw).hexdigest(), "context": context, "as_of": as_of})


def grounding_key(filing_id: str, question: str, as_of: Optional[str]) -> str:
    return _digest("grounding", {"filing_id": filing_id, "question": " ".join(question.split()), "as_of": as_of})


def private_key(text: Any, context: Optional[str], as_of: Optional[str]) -> Optional[str]:
    """Keyed by the exact draft text, not a whitespace-folded form: recorded claim spans are offsets into it. Context
    and as_of are the canonical forms the API uses. None for anything that isn't a non-blank string."""
    if not isinstance(text, str) or not text.strip():
        return None
    text_sha256 = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return _digest("private", {"text_sha256": text_sha256, "context": context, "as_of": as_of})


def _bound_private(record: dict[str, Any]) -> bool:
    """A Private recording replays only for the draft it re-derives its key from, and only a complete private
    stream: meta marked private first, done last, no error."""
    request, events = record.get("request"), record["events"]
    return (isinstance(request, dict) and bool(events)
            and private_key(request.get("text"), request.get("context"), request.get("as_of")) == record["key"]
            and events[0]["event"] == "meta" and events[0]["data"].get("private") is True
            and events[-1]["event"] == "done" and all(e["event"] != "error" for e in events))


class Recordings:
    """Read-only recordings under <dir>/<kind>/<key>.json; a damaged or mismatched file is ignored."""

    def __init__(self, dirs: Iterable[Path]):
        self.dirs = tuple(Path(d) for d in dirs)

    def load(self, kind: str, key: Optional[str]) -> Optional[dict[str, Any]]:
        if kind not in KINDS or not key or not _KEY.fullmatch(key):
            return None
        for directory in self.dirs:
            path = directory / kind / f"{key}.json"
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (isinstance(record, dict) and record.get("version") == 1 and record.get("kind") == kind
                    and record.get("key") == key and isinstance(record.get("events"), list)
                    and all(isinstance(e, dict) and isinstance(e.get("event"), str) and isinstance(e.get("data"), dict)
                            for e in record["events"])
                    and (kind != "private" or _bound_private(record))):
                return record
        return None

    def entries(self, kind: str) -> list[dict[str, Any]]:
        """The sample requests on offer (no event streams), first directory winning for a repeated key."""
        seen: dict[str, dict[str, Any]] = {}
        for directory in self.dirs:
            for path in sorted((directory / kind).glob("*.json")) if (directory / kind).is_dir() else []:
                record = self.load(kind, path.stem)
                if record is not None and record["key"] not in seen:
                    seen[record["key"]] = {"request": record.get("request", {}), "recorded_utc": record.get("recorded_utc")}
        return list(seen.values())


def replay(record: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The recorded events, marked as a cached replay. The meta event (Slide, Private) or each result event (quotes)
    carries cached: true and the recording time; billing fields keep the original run's cost for reference."""
    marked = {"cached": True, "recorded_utc": record.get("recorded_utc")}
    out: list[tuple[str, dict[str, Any]]] = []
    for event in record["events"]:
        name, data = event["event"], dict(event["data"])
        if name in ("meta", "result"):
            data.update(marked)
        out.append((name, data))
    return out
