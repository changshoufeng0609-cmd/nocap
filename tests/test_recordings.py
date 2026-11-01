"""Recorded sample runs (receipts.recordings): exact-request replay at $0, also while live requests are paused.

Synthetic recordings and fixtures only; the committed-snapshot test reads files, it makes no model call.
"""
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from receipts import api, ask, extract, private, recordings, stream
from test_api import parse_sse, settings
from test_grounding import filing_fixture

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()
IMAGE = f"data:image/png;base64,{PNG}"
QUESTION = "What does NVIDIA say about Data Center revenue?"
# The public synthetic Private sample (scripts/record_samples.py) and the key its recording is filed under
DRAFT = "NVIDIA reported revenue of $96.2 billion and GAAP diluted EPS of $2.22 in Q2 FY2027."
DRAFT_CONTEXT, DRAFT_AS_OF = "NVIDIA Q2 FY2027", "2026-10-29T00:00:00Z"
DRAFT_KEY = "57ae4142d93276c81b180ee95e031a3e852d0196b56f93e990cfb0e800ba0254"


def forbidden(*args, **kwargs):
    pytest.fail("A recorded replay must not create a provider client, admit rate work or reserve budget")


def write(directory: Path, kind: str, key: str, events, **fields) -> Path:
    path = directory / kind / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "kind": kind, "key": key, "recorded_utc": "2026-10-30T07:20:42Z",
                                "events": [{"event": n, "data": d} for n, d in events], **fields}))
    return path


def paused_app(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    config = settings(tmp_path, live_requests_enabled=False, recording_dirs=(tmp_path / "recordings",), **overrides)
    app = api.create_app(config, store=object(), client_factory=forbidden, wonder_client_factory=forbidden,
                         slide_client_factory=forbidden)
    return TestClient(app)


def test_slide_key_follows_image_bytes_context_and_as_of():
    key = recordings.slide_key(IMAGE, "NVIDIA", None)
    assert key == recordings.slide_key(IMAGE, "NVIDIA", None)
    assert key != recordings.slide_key(IMAGE, "NVIDIA Q2", None)
    assert key != recordings.slide_key(IMAGE, "NVIDIA", "2026-10-28T00:00:00Z")
    assert recordings.slide_key("data:image/gif;base64," + PNG, None, None) is None
    assert recordings.slide_key("data:image/png;base64,not base64!", None, None) is None


def test_grounding_key_ignores_whitespace_but_not_wording():
    key = recordings.grounding_key("NVDA-0001045810-26-000075", QUESTION, None)
    assert key == recordings.grounding_key("NVDA-0001045810-26-000075", f"  {QUESTION}\n", None)
    assert key != recordings.grounding_key("NVDA-0001045810-26-000075", QUESTION.lower(), None)
    assert key != recordings.grounding_key("NVDA-0001045810-26-000021", QUESTION, None)


def test_damaged_or_mismatched_recordings_are_ignored(tmp_path):
    key = recordings.grounding_key("X-1", QUESTION, None)
    store = recordings.Recordings([tmp_path])
    (tmp_path / "grounding").mkdir()
    (tmp_path / "grounding" / f"{key}.json").write_text("{not json")
    assert store.load("grounding", key) is None
    write(tmp_path, "grounding", key, [("result", {})])
    assert store.load("grounding", key) is not None
    other = recordings.grounding_key("X-2", QUESTION, None)
    (tmp_path / "grounding" / f"{other}.json").write_text((tmp_path / "grounding" / f"{key}.json").read_text())
    assert store.load("grounding", other) is None  # a copied file still names its original key
    assert store.load("grounding", "../escape") is None and store.load("unknown", key) is None


def test_recorded_slide_replays_while_paused_and_others_stay_paused(tmp_path, monkeypatch):
    key = recordings.slide_key(IMAGE, "NVIDIA", None)
    write(tmp_path / "recordings", "slide", key, [
        ("meta", {"models": ["sam-3.1", "muse-spark-1.3"], "image": {"width": 1, "height": 1}, "cached": False}),
        ("done", {"counts": {"verified": 1}, "claims": 1, "cost_usd": 0.03, "model_calls": 3})])
    c = paused_app(tmp_path, monkeypatch)
    events = parse_sse(c.post("/api/slide", json={"image_data_url": IMAGE, "context": "  NVIDIA ",
                                                  "as_of": None}).text)
    assert [n for n, _ in events] == ["meta", "done"]
    assert events[0][1]["cached"] is True and events[0][1]["recorded_utc"] == "2026-10-30T07:20:42Z"
    assert events[1][1]["cost_usd"] == 0.03  # the original run's cost, kept for reference
    other = parse_sse(c.post("/api/slide", json={"image_data_url": IMAGE, "context": "Apple"}).text)
    assert other[0][0] == "error" and other[0][1]["message"] == api.LIVE_PAUSED_MESSAGE


def test_recorded_quotes_replay_and_are_offered_as_samples(tmp_path, monkeypatch):
    row = filing_fixture(tmp_path / "data" / "filing_texts")
    key = recordings.grounding_key(row["id"], ask.validate_question(QUESTION), stream.canonical_as_of(None))
    write(tmp_path / "recordings", "grounding", key,
          [("progress", {"stage": "reading_filing"}),
           ("result", {"kind": "filing_quotes", "quotes": [], "usage": {"input_tokens": 1200}, "cost_usd": 0.01})],
          request={"filing_id": row["id"], "question": QUESTION, "as_of": None})
    write(tmp_path / "recordings", "grounding", recordings.grounding_key("GONE-1", QUESTION, None), [("result", {})],
          request={"filing_id": "GONE-1", "question": QUESTION, "as_of": None})
    c = paused_app(tmp_path, monkeypatch)
    listing = c.get("/api/grounding/filings").json()
    assert [f["id"] for f in listing["filings"]] == [row["id"]]
    assert listing["samples"] == [{"filing_id": row["id"], "question": QUESTION, "as_of": None,
                                   "recorded_utc": "2026-10-30T07:20:42Z"}]  # no sample for an unpackaged filing
    events = parse_sse(c.post("/api/grounding", json={"question": QUESTION, "filing_id": row["id"]}).text)
    assert events[-1][0] == "result" and events[-1][1]["cached"] is True
    assert events[-1][1]["usage"] == {"input_tokens": 1200}
    other = parse_sse(c.post("/api/grounding", json={"question": "Another question?", "filing_id": row["id"]}).text)
    assert other[0][0] == "error" and other[0][1]["message"] == api.LIVE_PAUSED_MESSAGE


def private_stream():
    return [("meta", {"private": True, "backend": "modal", "model": "muse-glimmer", "cached": False,
                      "passages": 1, "as_of": DRAFT_AS_OF, "persistence": "no_response_cache"}),
            ("claim", {"claim": {"id": "c1", "span_start": 19, "span_end": 43}}),
            ("verdict", {"claim_id": "c1", "verdict": {"status": "verified"}}),
            ("done", {"counts": {"verified": 1}, "model": "muse-glimmer", "private": True, "cost_usd": 0.0,
                      "cost_is_estimate": False, "model_calls": 1})]


def private_request(**changes):
    return {"sample_id": "nvda-q2-fy2027-private", "text": DRAFT, "context": DRAFT_CONTEXT, "as_of": DRAFT_AS_OF,
            **changes}


def test_private_key_is_stable_and_follows_the_exact_draft_context_and_as_of():
    key = recordings.private_key(DRAFT, DRAFT_CONTEXT, DRAFT_AS_OF)
    assert key == DRAFT_KEY  # the recording's file name: a change here orphans it
    # The browser's toISOString() spelling and a padded context reach the same key once the API canonicalizes them
    assert key == recordings.private_key(DRAFT, stream.normalize_context(f"  {DRAFT_CONTEXT} "),
                                         stream.canonical_as_of("2026-10-29T00:00:00.000Z"))
    assert key != recordings.private_key(DRAFT + "\n", DRAFT_CONTEXT, DRAFT_AS_OF)  # spans index the exact text
    assert key != recordings.private_key(DRAFT.replace("2.22", "2.46"), DRAFT_CONTEXT, DRAFT_AS_OF)
    assert key != recordings.private_key(DRAFT, None, DRAFT_AS_OF)
    assert key != recordings.private_key(DRAFT, DRAFT_CONTEXT, None)
    assert all(recordings.private_key(text, None, None) is None for text in ("", "  \n", None, 42))
    assert recordings.private_key("\ud800 lone surrogate", None, None) is not None  # valid JSON input never raises


def test_private_recording_is_ignored_unless_bound_to_its_draft_and_complete(tmp_path):
    store = recordings.Recordings([tmp_path])
    path = write(tmp_path, "private", DRAFT_KEY, private_stream(), request=private_request())
    assert store.load("private", DRAFT_KEY) is not None
    assert store.entries("private") == [{"request": private_request(), "recorded_utc": "2026-10-30T07:20:42Z"}]
    path.write_text("{not json")
    assert store.load("private", DRAFT_KEY) is None
    events = private_stream()
    for broken, request in (
            (events, private_request(text=DRAFT.replace("2.22", "2.46"))),  # an edited draft no longer matches
            (events, private_request(context=None)),
            (events, None),
            ([("meta", {**events[0][1], "private": False}), *events[1:]], private_request()),
            (events[:-1], private_request()),  # no done: an interrupted run
            ([*events[:-1], ("error", {"message": "x"}), events[-1]], private_request()),
            ([], private_request())):
        write(tmp_path, "private", DRAFT_KEY, broken, **({} if request is None else {"request": request}))
        assert store.load("private", DRAFT_KEY) is None
    assert store.entries("private") == []
    write(tmp_path, "private", DRAFT_KEY, events, request=private_request())
    other = recordings.private_key(DRAFT, None, None)
    (tmp_path / "private" / f"{other}.json").write_text(path.read_text())
    assert store.load("private", other) is None  # a copied file still names its original key
    assert store.load("private", "../" + DRAFT_KEY) is None


def test_private_replay_marks_meta_cached_and_keeps_the_original_accounting(tmp_path):
    write(tmp_path, "private", DRAFT_KEY, private_stream(), request=private_request())
    events = recordings.replay(recordings.Recordings([tmp_path]).load("private", DRAFT_KEY))
    assert [n for n, _ in events] == ["meta", "claim", "verdict", "done"]
    meta = events[0][1]
    assert meta["cached"] is True and meta["recorded_utc"] == "2026-10-30T07:20:42Z"
    assert meta["private"] is True and meta["model"] == "muse-glimmer"
    assert events[1:] == private_stream()[1:]  # claims, verdicts and the original run's call count, unchanged


@pytest.mark.parametrize("live", [False, True])
def test_recorded_private_sample_replays_before_the_live_gate_without_modal_or_budget(tmp_path, monkeypatch, live):
    """The public sample replays where Private mode isn't configured, live or paused: no backend, Modal client or
    reservation. The browser's spelling of the same request reaches the same key; any other draft never does."""
    backends = []

    def unconfigured(env=None):
        backends.append(1)
        raise private.PrivateUnavailable("not configured here")

    monkeypatch.setattr(private, "private_backend", unconfigured)
    monkeypatch.setattr(private, "make_private_client", forbidden)
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    write(tmp_path / "recordings", "private", DRAFT_KEY, private_stream(), request=private_request())
    config = settings(tmp_path, live_requests_enabled=live, recording_dirs=(tmp_path / "recordings",))
    c = TestClient(api.create_app(config, store=object(), client_factory=forbidden, wonder_client_factory=forbidden,
                                  slide_client_factory=forbidden))
    response = c.post("/api/private/check", json={"text": DRAFT, "context": f"  {DRAFT_CONTEXT} ",
                                                  "as_of": "2026-10-29T00:00:00.000Z"})
    events = parse_sse(response.text)
    assert [n for n, _ in events] == ["meta", "claim", "verdict", "done"] and not backends
    assert events[0][1]["cached"] is True and events[0][1]["recorded_utc"] == "2026-10-30T07:20:42Z"
    assert events[1:] == private_stream()[1:]  # the recorded claims, verdicts and original accounting
    assert "no-store" in response.headers["cache-control"]
    other = parse_sse(c.post("/api/private/check", json={"text": DRAFT + " ", "context": DRAFT_CONTEXT,
                                                         "as_of": DRAFT_AS_OF}).text)
    assert len(other) == 1 and other[0][0] == "error" and other[0][1]["model_calls"] == 0
    assert other[0][1]["message"] == ("Private mode is unavailable. No draft was sent to a model." if live
                                      else api.LIVE_PAUSED_MESSAGE)
    assert backends == ([1] if live else [])


def test_private_status_lists_the_recorded_samples_it_replays(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "settings_env", lambda: {})  # Private mode not configured, as on the public site
    assert paused_app(tmp_path, monkeypatch).get("/api/private/status").json()["recorded_samples"] == []
    write(tmp_path / "recordings", "private", DRAFT_KEY, private_stream(), request=private_request())
    status = paused_app(tmp_path, monkeypatch).get("/api/private/status")
    assert status.json()["available"] is False and "no-store" in status.headers["cache-control"]
    assert status.json()["recorded_samples"] == [{"sample_id": "nvda-q2-fy2027-private",
                                                  "recorded_utc": "2026-10-30T07:20:42Z"}]
    disabled = paused_app(tmp_path, monkeypatch, use_caches=False).get("/api/private/status").json()
    assert disabled["recorded_samples"] == []  # caches off: nothing is replayed, so nothing is offered


def test_record_script_keys_the_private_sample_exactly_as_the_api_will():
    from scripts import record_samples

    [item] = [item for item in record_samples.plan() if item["kind"] == "private"]
    body = item["body"]
    assert item["endpoint"] == "/api/private/check" and set(body) == {"text", "context", "as_of"}
    assert item["key"] == DRAFT_KEY == recordings.private_key(
        body["text"], stream.normalize_context(body["context"]), stream.canonical_as_of(body["as_of"]))
    assert item["request"] == private_request()


@pytest.mark.parametrize("flags, data_dir", [(["--private"], ROOT / "data/snapshot"), ([], None)])
def test_only_private_recordings_verify_against_the_snapshot(tmp_path, monkeypatch, flags, data_dir):
    """--private records against the evidence the deployed site serves (its content fingerprint is the provenance);
    Slide and quote recordings keep the default data directory. Stops before any server, client or model call."""
    from scripts import record_samples

    class Stop(Exception):
        pass

    def capture(env):
        seen.update(env)
        raise Stop

    seen: dict = {}
    monkeypatch.setattr(record_samples, "OUT", tmp_path)  # nothing is recorded here, so every sample is missing
    monkeypatch.setattr(record_samples, "private_setup", (lambda timeout: {}) if flags else forbidden)
    monkeypatch.setattr(record_samples.api.Settings, "from_env", staticmethod(capture))
    with pytest.raises(Stop):
        record_samples.main([*flags, "--confirm-spend"])
    assert seen.get("RECEIPTS_DATA_DIR") == (str(data_dir) if data_dir else None)
    assert seen["RECEIPTS_LIVE_REQUESTS_ENABLED"] == "true"


def test_snapshot_packages_a_bound_private_recording(tmp_path):
    from scripts.build_snapshot import copy_recordings

    src, dst = tmp_path / "recordings", tmp_path / "snapshot"
    write(src, "private", DRAFT_KEY, private_stream(), request=private_request())
    assert copy_recordings(src, dst, tmp_path / "no-filing-texts") == 1
    assert (dst / "private" / f"{DRAFT_KEY}.json").read_bytes() == (src / "private" / f"{DRAFT_KEY}.json").read_bytes()
    write(src, "private", DRAFT_KEY, private_stream(), request=private_request(text=DRAFT.replace("$", "US$")))
    with pytest.raises(SystemExit, match="damaged or does not match its key"):
        copy_recordings(src, tmp_path / "again", tmp_path / "no-filing-texts")
    quotes = tmp_path / "quotes"
    write(quotes, "grounding", recordings.grounding_key("GONE-1", QUESTION, None), [("result", {})],
          request={"filing_id": "GONE-1", "question": QUESTION, "as_of": None})
    with pytest.raises(SystemExit, match="not packaged"):  # quotes still need their filing in the snapshot
        copy_recordings(quotes, tmp_path / "quotes-out", tmp_path / "no-filing-texts")


def test_caches_disabled_also_disable_recordings(tmp_path, monkeypatch):
    key = recordings.slide_key(IMAGE, None, None)
    write(tmp_path / "recordings", "slide", key, [("meta", {}), ("done", {})])
    write(tmp_path / "recordings", "private", DRAFT_KEY, private_stream(), request=private_request())
    c = paused_app(tmp_path, monkeypatch, use_caches=False)
    events = parse_sse(c.post("/api/slide", json={"image_data_url": IMAGE}).text)
    assert events[0][0] == "error"
    body = {"text": DRAFT, "context": DRAFT_CONTEXT, "as_of": DRAFT_AS_OF}
    assert parse_sse(c.post("/api/private/check", json=body).text)[0][0] == "error"


def test_committed_samples_match_the_packaged_recordings():
    """The web client's sample requests (web/lib/samples.json) are exactly the requests the snapshot recorded."""
    spec = json.loads((ROOT / "web/lib/samples.json").read_text())
    store = recordings.Recordings([ROOT / "data/snapshot/recordings"])
    for sample in spec["slides"]:
        raw = (ROOT / "web/public" / sample["image"].lstrip("/")).read_bytes()
        url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        key = recordings.slide_key(url, stream.normalize_context(sample["context"]), stream.canonical_as_of(sample["as_of"]))
        record = store.load("slide", key)
        assert record is not None and record["request"]["source"]["url"] == sample["source"]["url"]
        assert any(n["event"] == "done" for n in record["events"])
    for sample in spec["filing_questions"]:
        key = recordings.grounding_key(sample["filing_id"], ask.validate_question(sample["question"]),
                                       stream.canonical_as_of(sample["as_of"]))
        record = store.load("grounding", key)
        assert record is not None
        result = [e["data"] for e in record["events"] if e["event"] == "result"][0]
        assert result["whole_document"] is True and result["quotes"] and result["usage"]["input_tokens"] > 50_000
    assert spec["private"]
    for sample in spec["private"]:
        context, as_of = stream.normalize_context(sample["context"]), stream.canonical_as_of(sample["as_of"])
        record = store.load("private", recordings.private_key(sample["text"], context, as_of))
        assert record is not None and record["request"] == {"sample_id": sample["id"], "text": sample["text"],
                                                             "context": context, "as_of": as_of}
        runtime = record["runtime"]  # what produced it: a committed tree, the operator's Modal Glimmer, the snapshot
        assert runtime["backend"] == "modal" and runtime["model"] == "muse-glimmer" and runtime["source_dirty"] is False
        assert runtime["data_dir"] == "data/snapshot" and runtime["weights_revision"] and record["source_commit"]
        done = record["events"][-1]["data"]
        assert done["private"] is True and done["model_calls"] >= 1 and sum(done["counts"].values()) >= 1
