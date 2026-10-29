"""API tests (docs/api-contract.md): SSE order and shapes, cache replay, guards, and the streaming extractor.

Extraction and verification are mocked: no network, no API key, no data files.
SYNTHETIC TEST DATA: "Example Corp" is a fictional company and every number below is invented.
"""
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from receipts import api, extract, stream
from receipts.extract import PassageResult
from receipts.types import Claim, Fact, Verdict

REPO = Path(__file__).resolve().parent.parent
P1 = "Example Corp (a fictional company) reported revenue of $12.4 billion for the third quarter of fiscal 2025."
P2 = "Net income was $3.1 billion, or $1.25 per diluted share."
TEXT = f"{P1}\n\n{P2}\n"
SECRET_SENTENCE = "Example Corp (a fictional company) reported revenue"
USAGE = {"input_tokens": 1000, "cached_tokens": 0, "output_tokens": 600, "reasoning_tokens": 300}
MONEY = re.compile(r"\$\d+(?:\.\d+)?(?: billion)?")
FACT = Fact(cik=1, ticker="EXCO", metric="revenue", concept="us-gaap:Revenues", basis="GAAP", value=12.4e9, unit="USD",
            period_start=date(2025, 7, 1), period_end=date(2025, 9, 30), fiscal_year=2025, fiscal_period="Q3",
            source="xbrl", form="10-Q", accession="0000000001-25-000001",
            accepted_at=datetime(2025, 10, 30, 20, 5, tzinfo=timezone.utc),
            url="https://www.sec.gov/Archives/edgar/data/1/000000000125000001/exco-20250930.htm", decimals=-6)
FACT_FIELDS = {"form", "accession", "accepted_at", "url", "locator", "concept", "source", "fiscal_year",
               "fiscal_period", "value", "unit"}


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------
def make_claim(cid: str, value_text: str, start: int) -> Claim:
    billions = value_text.endswith("billion")
    return Claim(id=cid, metric="revenue", metric_text="revenue", value_text=value_text,
                 value=float(value_text.strip("$").replace(" billion", "")), unit="USD",
                 scale="billions" if billions else "units", period_text="third quarter of fiscal 2025",
                 period_type="quarter", comparison="none", basis=None, forward_looking=False, entity="Example Corp",
                 span_text=value_text, span_start=start, span_end=start + len(value_text))


class FakeExtract:
    """Stands in for receipts.extract.iter_extract: one claim per dollar figure, one result per passage."""

    def __init__(self, *, fail=(), raise_exc=None, delays=None, cost=None, cached=False):
        self.fail, self.raise_exc, self.delays, self.cached = set(fail), raise_exc, delays or {}, cached
        self.cost = extract.cost_usd(USAGE) if cost is None else cost
        self.calls = []

    def __call__(self, text, context=None, **kwargs):
        self.calls.append({"context": context, **kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        n = 0
        for i, (start, passage) in enumerate(extract.split_passages(text), 1):
            time.sleep(self.delays.get(i, 0.0))
            if i in self.fail:
                yield PassageResult(i, start, len(passage), [], {"status": "http_error", "cached": False,
                                                                  "cost_usd": 0.0})
                continue
            claims = []
            for m in MONEY.finditer(passage):
                n += 1
                claims.append(make_claim(f"c{n}", m.group(0), start + m.start()))
            yield PassageResult(i, start, len(passage), claims, {"status": "ok", "cached": self.cached,
                                                                 "usage": USAGE, "cost_usd": self.cost})


def fake_verify(claim, store, *, as_of=None, context=None):
    if claim.value_text == "$3.1 billion":
        return Verdict(claim_id=claim.id, status="wrong", reason="value_mismatch",
                       explanation="Example Corp's 10-Q reports net income of $3.0 billion.", expected_value=3.0,
                       evidence=[FACT], as_of=as_of)
    if claim.value_text == "$1.25":
        return Verdict(claim_id=claim.id, status="unverifiable", reason="no_evidence",
                       explanation="No filing fact for this figure.", as_of=as_of)
    return Verdict(claim_id=claim.id, status="verified", reason="match", explanation="Matches Example Corp's 10-Q.",
                   evidence=[FACT], as_of=as_of)


@pytest.fixture
def fake(monkeypatch):
    fx = FakeExtract()
    monkeypatch.setattr(extract, "iter_extract", fx)
    monkeypatch.setattr(stream._verify, "verify", fake_verify)
    return fx


def settings(tmp_path, **overrides) -> api.Settings:
    (tmp_path / "data").mkdir(exist_ok=True)
    base = dict(data_dir=tmp_path / "data", api_cache_dir=tmp_path / "api_cache", prebaked_dirs=(),
                spark_cache_dir=tmp_path / "spark", spark_read_dirs=(), examples_paths=(tmp_path / "examples.json",))
    base.update(overrides)
    return api.Settings(**base)


def client(tmp_path, *, client_factory=lambda: object(), **overrides) -> TestClient:
    return TestClient(api.create_app(settings(tmp_path, **overrides), store=object(), client_factory=client_factory))


def parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        fields = dict(line.split(": ", 1) for line in lines)
        assert set(fields) == {"event", "data"}
        out.append((fields["event"], json.loads(fields["data"])))
    return out


def post(c: TestClient, text: str = TEXT, ip: str = "203.0.113.7", **body):
    r = c.post("/api/check", json={"text": text, **body}, headers={"x-forwarded-for": ip})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def only_error(events):
    assert len(events) == 1 and events[0][0] == "error", events
    assert set(events[0][1]) == {"message", "retryable", "cost_usd", "cost_is_estimate", "model_calls"}
    return events[0][1]


# ---------------------------------------------------------------------------
# Order and shapes
# ---------------------------------------------------------------------------
def test_events_follow_the_contract_order_and_shapes(tmp_path, fake):
    events = post(client(tmp_path), as_of="2026-08-26T20:00:00+00:00", context="Example Corp, Q3 FY2025")
    names = [n for n, _ in events]
    assert names[0] == "meta" and names[-1] == "done" and names.count("meta") == names.count("done") == 1
    meta = events[0][1]
    assert set(meta) == {"draft_hash", "cached", "passages", "as_of"}
    assert meta == {"draft_hash": stream.draft_key(TEXT, "2026-08-26T20:00:00Z", "Example Corp, Q3 FY2025"),
                    "cached": False, "passages": 2, "as_of": "2026-08-26T20:00:00Z"}
    claims, seen = {}, set()
    for i, (name, data) in enumerate(events[1:-1], 1):
        if name == "claim":
            assert set(data) == {"claim"}
            claim = Claim.model_validate(data["claim"])
            assert TEXT[claim.span_start:claim.span_end] == claim.span_text  # offsets index the request text
            assert claim.id not in claims
            claims[claim.id] = i
        else:
            assert name == "verdict" and set(data) == {"claim_id", "verdict"}
            verdict = Verdict.model_validate(data["verdict"])
            assert data["claim_id"] == verdict.claim_id and claims[verdict.claim_id] < i  # claim first, then verdict
            assert verdict.claim_id not in seen
            seen.add(verdict.claim_id)
            for fact in data["verdict"]["evidence"]:  # full Fact receipts
                assert FACT_FIELDS <= set(fact) and fact["accepted_at"].startswith("2025-10-30T20:05:00")
    assert seen == set(claims) and len(claims) == 3
    # passage 1's verdict is sent before passage 2's claims: verdicts don't wait for the whole draft
    assert names[1:-1] == ["claim", "verdict", "claim", "claim", "verdict", "verdict"]
    done = events[-1][1]
    assert set(done) == {"counts", "latency_s", "cost_usd", "model", "cost_is_estimate", "model_calls", "coverage"} and done["model"] == "muse-spark-1.3"
    assert done["counts"] == {"verified": 1, "wrong": 1, "unverifiable": 1, "approximate": 0}
    assert done["cost_usd"] == pytest.approx(2 * extract.cost_usd(USAGE)) and done["latency_s"] >= 0
    assert fake.calls[0]["context"] == "Example Corp, Q3 FY2025" and fake.calls[0]["deadline"] is not None


def test_null_context_is_inferred_like_the_cli(tmp_path, fake):
    post(client(tmp_path), context="  ")
    assert fake.calls[0]["context"] == extract.infer_context(TEXT)


def test_first_verdict_streams_before_a_slow_passage_finishes(tmp_path, monkeypatch):
    fx = FakeExtract(delays={2: 0.8})
    monkeypatch.setattr(extract, "iter_extract", fx)
    monkeypatch.setattr(stream._verify, "verify", fake_verify)
    c = client(tmp_path)
    t0 = time.monotonic()
    first_verdict = None
    with c.stream("POST", "/api/check", json={"text": TEXT}) as r:
        for line in r.iter_lines():
            if line == "event: verdict" and first_verdict is None:
                first_verdict = time.monotonic() - t0
    total = time.monotonic() - t0
    assert first_verdict is not None and first_verdict < 0.6 <= total


def test_keepalive_comments_fill_long_silences(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "iter_extract", FakeExtract(delays={1: 0.4}))
    monkeypatch.setattr(stream._verify, "verify", fake_verify)
    r = client(tmp_path, keepalive_s=0.05).post("/api/check", json={"text": TEXT})
    assert ": keep-alive\n\n" in r.text
    assert [n for n, _ in parse_sse(r.text)][0::len(parse_sse(r.text)) - 1] == ["meta", "done"]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def test_second_request_replays_the_recording_with_cached_true(tmp_path, fake):
    c = client(tmp_path)
    first = post(c)
    second = post(c)
    assert len(fake.calls) == 1  # no second extraction
    assert second[0][1] == {**first[0][1], "cached": True}
    assert second[1:] == first[1:]  # same claims, verdicts and done
    recorded = [p.stem for p in (tmp_path / "api_cache").glob("*.json") if not p.stem.startswith("_")]
    assert recorded == [first[0][1]["draft_hash"]]  # stored under the draft hash (beside the SQLite ledger)


def test_cache_key_covers_text_as_of_and_context(tmp_path, fake):
    c = client(tmp_path)
    post(c, as_of="2026-08-26T20:00:00Z")
    assert post(c, as_of="2026-08-26T20:00:00+00:00")[0][1]["cached"] is True  # same instant, other spelling
    assert post(c, as_of="2026-08-27T20:00:00Z")[0][1]["cached"] is False
    assert post(c, as_of="2026-08-26T20:00:00Z", context="Example Corp")[0][1]["cached"] is False
    assert post(c, text=TEXT + " ")[0][1]["cached"] is False
    assert len(fake.calls) == 4


def test_prebaked_stream_is_served_without_the_model(tmp_path, fake, monkeypatch):
    recorded = post(client(tmp_path))
    prebaked = tmp_path / "prebaked"
    prebaked.mkdir()
    key = recorded[0][1]["draft_hash"]
    (prebaked / f"{key}.json").write_text((tmp_path / "api_cache" / f"{key}.json").read_text())
    monkeypatch.setattr(extract, "iter_extract", FakeExtract(raise_exc=AssertionError("the model was called")))
    c = TestClient(api.create_app(settings(tmp_path, api_cache_dir=None, prebaked_dirs=(prebaked,)), store=object(),
                                  client_factory=lambda: pytest.fail("no client needed")))
    replayed = post(c)
    assert replayed[0][1]["cached"] is True and replayed[1:] == recorded[1:]


def test_recording_from_other_data_is_not_replayed(tmp_path, fake):
    c = client(tmp_path)
    post(c)
    pr = tmp_path / "data" / "pr_facts" / "EXCO"
    pr.mkdir(parents=True)
    (pr / "0000000001-25-000002.jsonl").write_text(FACT.model_dump_json() + "\n")  # the evidence changed
    assert post(c)[0][1]["cached"] is False and len(fake.calls) == 2


def test_failed_or_partial_checks_end_in_error_and_are_not_cached(tmp_path, monkeypatch):
    fx = FakeExtract(fail={2})
    monkeypatch.setattr(extract, "iter_extract", fx)
    monkeypatch.setattr(stream._verify, "verify", fake_verify)
    c = client(tmp_path)
    events = post(c)
    assert [n for n, _ in events] == ["meta", "claim", "verdict", "error"]  # what was checked, then the error
    assert events[-1][1]["retryable"] is True and "1 of 2 passages" in events[-1][1]["message"]
    post(c)
    assert len(fx.calls) == 2 and not [p for p in (tmp_path / "api_cache").glob("*.json") if p.stem != "_spend"]


def test_missing_api_key_is_a_non_retryable_error(tmp_path, fake):
    def no_key():
        raise extract.ExtractionError("MODEL_API_KEY is not set")

    err = only_error(post(client(tmp_path, client_factory=no_key)))
    assert err["retryable"] is False and "examples" in err["message"] and not fake.calls


def test_unexpected_extraction_crash_is_an_error_event(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "iter_extract", FakeExtract(raise_exc=RuntimeError("boom")))
    events = post(client(tmp_path))
    assert [n for n, _ in events] == ["meta", "error"] and events[-1][1]["retryable"] is True
    assert "boom" not in events[-1][1]["message"]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_text_longer_than_6000_characters_is_rejected(tmp_path, fake):
    c = client(tmp_path)
    err = only_error(post(c, text="Revenue was $1 billion. " + "x" * 6000))
    assert err["retryable"] is False and "6,000" in err["message"] and not fake.calls
    assert [n for n, _ in post(c, text="y" * 6000)] == ["meta", "done"]  # exactly at the limit is fine


@pytest.mark.parametrize("body,needle", [
    ({"text": "   "}, "Paste a draft"),
    ({"text": TEXT, "as_of": "last Tuesday"}, "ISO 8601"),
    ({"text": TEXT, "context": "x" * 301}, "context"),
    ({"txt": TEXT}, "JSON"),
    ({"text": 42}, "JSON"),
])
def test_bad_requests_are_error_events(tmp_path, fake, body, needle):
    r = client(tmp_path).post("/api/check", json=body)
    err = only_error(parse_sse(r.text))
    assert err["retryable"] is False and needle in err["message"] and not fake.calls


def test_non_json_body_is_an_error_event(tmp_path, fake):
    r = client(tmp_path).post("/api/check", content=b"not json", headers={"content-type": "application/json"})
    assert only_error(parse_sse(r.text))["retryable"] is False


def test_rate_limit_is_per_ip_and_skips_cached_replays(tmp_path, fake):
    c = client(tmp_path, rate_limit_checks=2, rate_limit_window_s=600)
    assert post(c, text="Revenue was $1 billion.")[-1][0] == "done"
    assert post(c, text="Revenue was $2 billion.")[-1][0] == "done"
    err = only_error(post(c, text="Revenue was $3 billion."))
    assert err["retryable"] is True and "Try again in 10 minutes" in err["message"]
    other = TestClient(c.app, client=("198.51.100.9", 50000))
    assert post(other, text="Revenue was $3 billion.")[-1][0] == "done"  # another socket peer
    replay = post(c, text="Revenue was $1 billion.")  # a cached replay costs nothing, so it isn't limited
    assert replay[0][1]["cached"] is True and replay[-1][0] == "done"
    assert len(fake.calls) == 3


def test_daily_budget_counts_token_cost_and_blocks_when_spent(tmp_path, monkeypatch):
    fx = FakeExtract(cost=0.02)  # $0.02 per passage call, as computed from token usage
    monkeypatch.setattr(extract, "iter_extract", fx)
    monkeypatch.setattr(stream._verify, "verify", fake_verify)
    c = client(tmp_path, daily_budget_usd=0.21, est_cost_per_call_usd=0.01)
    assert post(c)[-1][1]["cost_usd"] == pytest.approx(0.04)
    err = only_error(post(c, text="Revenue was $9 billion.\n\nIt rose 5%."))  # needs $0.20 conservatively: 0.04 + 0.20 > 0.21
    assert err["retryable"] is False and "budget" in err["message"]
    assert c.app.state.ledger.spent_today() == pytest.approx(0.04)
    assert (tmp_path / "api_cache" / "_spend.sqlite3").exists()
    assert post(c)[0][1]["cached"] is True  # cached drafts still work after the cap
    assert len(fx.calls) == 1
    restarted = client(tmp_path, daily_budget_usd=0.21, est_cost_per_call_usd=0.01)  # the total survives a restart
    assert only_error(post(restarted, text="Revenue was $9 billion.\n\nIt rose 5%."))["retryable"] is False


def test_zero_budget_blocks_every_live_check(tmp_path, fake):
    err = only_error(post(client(tmp_path, daily_budget_usd=0.0)))
    assert err["retryable"] is False and not fake.calls


def test_drafts_already_in_the_model_cache_skip_rate_limit_and_budget(tmp_path, fake):
    spark = tmp_path / "spark"
    spark.mkdir()
    for key in extract.request_keys(TEXT, None):
        (spark / f"{key}.json").write_text(json.dumps({"output_text": json.dumps({"claims": []})}))
    c = client(tmp_path, rate_limit_checks=1, daily_budget_usd=0.0)
    for as_of in ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"):  # time travel is free
        assert post(c, as_of=as_of)[-1][0] == "done"


def test_rate_limiter_window_slides():
    now = [0.0]
    rl = api.RateLimiter(2, 600, clock=lambda: now[0])
    assert rl.acquire("a") is None and rl.acquire("a") is None
    assert rl.acquire("a") == pytest.approx(600)
    now[0] = 599.0
    assert rl.acquire("a") == pytest.approx(1)
    now[0] = 600.0
    assert rl.acquire("a") is None and rl.acquire("b") is None
    assert api.RateLimiter(0, 600).acquire("a") is None  # disabled


def test_spend_ledger_reserves_settles_and_resets_each_day(tmp_path):
    day = ["2026-10-27"]
    ledger = api.SpendLedger(0.10, tmp_path / "spend.sqlite3", today=lambda: day[0])
    a = ledger.reserve(0.06)
    assert a is not None and ledger.reserve(0.06) is None  # two in flight can't overshoot the cap together
    ledger.settle(a, 0.03)
    assert ledger.spent_today() == pytest.approx(0.03) and ledger.reserve(0.06) is not None
    day[0] = "2026-10-28"
    assert ledger.spent_today() == 0.0
    path = tmp_path / "spend.sqlite3"  # persisted: a restart the same day keeps the total, the next day starts at 0
    assert api.SpendLedger(0.10, path, today=lambda: "2026-10-27").spent_today() == pytest.approx(0.09)  # includes durable pending charge
    assert api.SpendLedger(0.10, path, today=lambda: "2026-10-28").spent_today() == 0.0


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def test_logs_carry_hashes_never_draft_text(tmp_path, fake):
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Grab()
    loggers = [logging.getLogger(n) for n in ("receipts.api", "receipts.stream")]
    c = client(tmp_path, rate_limit_checks=1)
    for lg in loggers:
        lg.addHandler(handler)
    try:
        key = post(c, example_id="demo")[0][1]["draft_hash"]
        post(c)
        post(c, text=TEXT + "\n\nMore text with $5 billion.")  # rate limited
        post(c, text="Revenue was $1 billion. " * 400)  # too long
    finally:
        for lg in loggers:
            lg.removeHandler(handler)
    assert records and any(key[:12] in m for m in records)
    for m in records:
        assert SECRET_SENTENCE not in m and "$12.4" not in m and "Revenue was" not in m


# ---------------------------------------------------------------------------
# Examples and health
# ---------------------------------------------------------------------------
def test_examples_endpoint_shape(tmp_path):
    (tmp_path / "demo.txt").write_text(TEXT)
    (tmp_path / "examples.json").write_text(json.dumps({"examples": [
        {"id": "demo", "title": "Demo", "description": "A demo.", "file": "demo.txt",
         "as_of": "2026-08-26T20:00:00+00:00", "context": "Example Corp, Q3 FY2025"}]}))
    r = client(tmp_path).get("/api/examples")
    assert r.status_code == 200
    assert r.json() == [{"id": "demo", "title": "Demo", "description": "A demo.", "text": TEXT.rstrip(),
                         "as_of": "2026-08-26T20:00:00Z", "context": "Example Corp, Q3 FY2025"}]


def test_repo_examples_are_demo_ready():
    examples = api.load_examples([REPO / "examples" / "examples.json"])
    assert len(examples) >= 3 and len({e["id"] for e in examples}) == len(examples)
    for e in examples:
        assert set(e) == {"id", "title", "description", "text", "as_of", "context"}
        assert 120 <= len(e["text"].split()) <= 250 and len(e["text"]) <= api.MAX_CHARS
        assert e["title"] and e["description"] and e["context"]
    assert any(e["as_of"] for e in examples)  # the look-ahead demo has its own as_of


def test_health_reports_snapshot_companies_and_facts(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "facts.sqlite")
    con.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); "
                      "CREATE TABLE companies (cik INTEGER, ticker TEXT); CREATE TABLE facts (id INTEGER);"
                      "INSERT INTO meta VALUES ('built_at', '2026-10-27T09:28:16Z');"
                      "INSERT INTO companies VALUES (2, 'ZZZ'), (1, 'EXCO'); INSERT INTO facts VALUES (1), (2);")
    con.commit()
    con.close()
    pr = data / "pr_facts" / "EXCO"
    pr.mkdir(parents=True)
    (pr / "a.jsonl").write_text(FACT.model_dump_json() + "\n\n" + FACT.model_dump_json() + "\n")
    r = client(tmp_path).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data_snapshot": "2026-10-27", "companies": ["EXCO", "ZZZ"], "facts": 4,
                        "live_requests_paused": False, "draft_cache_policy": "persistent"}


def test_health_without_data_is_503(tmp_path):
    r = client(tmp_path).get("/api/health")
    assert r.status_code == 503 and r.json()["ok"] is False


# ---------------------------------------------------------------------------
# receipts.extract.iter_extract: the real streaming extractor over a mocked HTTP transport
# ---------------------------------------------------------------------------
def _model_claim(value_text, span, metric="revenue"):
    return {"metric": metric, "metric_text": metric.replace("_", " "), "value_text": value_text,
            "value": float(value_text.strip("$").replace(" billion", "")), "unit": "USD",
            "scale": "billions" if "billion" in value_text else "units", "period_text": "third quarter of fiscal 2025",
            "period_type": "quarter", "comparison": "none", "bases": [], "forward_looking": False,
            "entity": "Example Corp", "segment": None, "span_text": span}


REPLIES = {P1: [_model_claim("$12.4 billion", "revenue of $12.4 billion")],
           P2: [_model_claim("$3.1 billion", "Net income was $3.1 billion", "net_income"),
                _model_claim("$1.25", "$1.25 per diluted share", "eps_diluted")]}


class MockModel:
    def __init__(self, delays=None, statuses=None):
        self.delays, self.statuses, self.requests = delays or {}, statuses or {}, []
        self.lock = threading.Lock()

    def handler(self, request):
        body = json.loads(request.content.decode("utf-8"))
        passage = body["input"].split("<passage>\n", 1)[1].rsplit("\n</passage>", 1)[0]
        with self.lock:
            self.requests.append(passage)
        time.sleep(self.delays.get(passage, 0.0))
        if self.statuses.get(passage, 200) != 200:
            return httpx2.Response(self.statuses[passage], json={"error": {"message": "mock", "type": "x"}})
        text = json.dumps({"claims": REPLIES[passage]})
        return httpx2.Response(200, json={
            "id": "resp_mock", "object": "response", "created_at": 1761550000, "status": "completed",
            "model": extract.MODEL, "output": [{"id": "msg", "type": "message", "role": "assistant",
                                                "status": "completed", "content": [
                                                    {"type": "output_text", "text": text, "annotations": []}]}],
            "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 400,
                      "output_tokens_details": {"reasoning_tokens": 100}, "total_tokens": 1400},
            "incomplete_details": None, "error": None, "instructions": None, "metadata": {},
            "parallel_tool_calls": True, "temperature": 1.0, "top_p": 1.0, "tool_choice": "auto", "tools": [],
            "store": False})

    def client(self):
        return extract.make_client("LLM_0_test_dummy_not_a_real_key", timeout=10,
                                   http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(self.handler)))


def test_iter_extract_yields_each_passage_as_it_finishes(tmp_path):
    model = MockModel(delays={P1: 0.4})
    results = list(extract.iter_extract(TEXT, "Example Corp, Q3 FY2025", client=model.client(),
                                        cache_dir=tmp_path / "spark", sleep=lambda s: None))
    assert [r.passage for r in results] == [2, 1]  # the slow first passage comes last
    claims = [c for r in results for c in r.claims]
    assert [c.id for c in claims] == ["c1", "c2", "c3"]  # ids follow the order they are sent
    assert all(TEXT[c.span_start:c.span_end] == c.span_text for c in claims)
    assert all(r.ok and r.cost_usd > 0 and not r.call["cached"] for r in results)
    assert extract.passages_to_call(TEXT, "Example Corp, Q3 FY2025", cache_dirs=[tmp_path / "spark"]) == 0
    again = list(extract.iter_extract(TEXT, "Example Corp, Q3 FY2025", client=MockModel(statuses={P1: 500, P2: 500})
                                      .client(), cache_dir=None, extra_cache_dirs=[tmp_path / "spark"]))
    assert [r.passage for r in again] == [1, 2] and all(r.call["cached"] and r.cost_usd == 0 for r in again)


def test_iter_extract_deadline_and_failures_fail_only_their_passage(tmp_path):
    model = MockModel(delays={P1: 1.5}, statuses={P2: 400})
    t0 = time.monotonic()
    results = list(extract.iter_extract(TEXT, "Example Corp", client=model.client(), use_cache=False,
                                        deadline=time.monotonic() + 0.5, sleep=lambda s: None))
    assert time.monotonic() - t0 < 1.4  # the deadline, not the slow call, ended the stream
    status = {r.passage: r.call["status"] for r in results}
    assert status == {1: "deadline", 2: "http_error"} and not any(r.claims for r in results)
    assert all(not r.ok and r.warnings for r in results)


def test_call_with_deadline_retries_once_then_stops():
    calls, slept = [], []

    def flaky():
        calls.append(1)
        raise openai.APIConnectionError(request=httpx2.Request("POST", "https://api.meta.ai/v1/responses"))

    out = extract.call_with_deadline(flaky, sleep=slept.append, max_attempts=2)
    assert not out.ok and out.attempts == 2 and len(calls) == 2 and len(slept) == 1
    calls.clear()
    out = extract.call_with_deadline(flaky, sleep=slept.append, max_attempts=3, deadline=time.monotonic() - 1)
    assert not out.ok and len(calls) == 0  # no initial call or retry past the deadline
