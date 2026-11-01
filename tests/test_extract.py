"""Offline extraction tests: the real OpenAI SDK against a mocked HTTP transport. No network, no API key.

SYNTHETIC TEST DATA: "Example Corp" is a fictional company and every number below is invented.
"""
import json
import logging
import threading
import time
from typing import get_args

import httpx2
import openai
import pytest

from receipts import extract as ex
from receipts import types

DUMMY_KEY = "LLM_0_test_dummy_not_a_real_key"
CONTEXT = "Example Corp, third quarter of fiscal 2025"
P1 = ("Example Corp (a fictional company) reported revenue for the third quarter of fiscal 2025 of $12.4 billion, "
      "up 8% from a year ago. GAAP and non-GAAP gross margins were both 61.5%.")
P2 = "• Cloud revenue was $4.2 billion, up 5%. Devices revenue was $3.1 billion, up 5%."
P3 = "• Revenue is expected to be $13.0 billion, plus or minus 2%."
TEXT = f"{P1}\n\n{P2}\n\n{P3}\n"
Q3 = "third quarter of fiscal 2025"


def mc(metric, value_text, value, unit, scale, span, *, bases=(), segment=None, comparison="none", forward=False,
       period_text=Q3):
    return {"metric": metric, "metric_text": metric.replace("_", " "), "value_text": value_text, "value": value,
            "unit": unit, "scale": scale, "period_text": period_text, "period_type": "quarter",
            "comparison": comparison, "bases": list(bases), "forward_looking": forward, "entity": "Example Corp",
            "segment": segment, "span_text": span}


REPLIES = {
    P1: [mc("revenue", "$12.4 billion", 12.4, "USD", "billions",
            "revenue for the third quarter of fiscal 2025 of $12.4 billion"),
         mc("revenue_growth", "8%", 8, "%", "units", "up 8% from a year ago", comparison="YoY"),
         mc("gross_margin", "61.5%", 61.5, "%", "units", "GAAP and non-GAAP gross margins were both 61.5%",
            bases=("GAAP", "non-GAAP")),
         mc("other", "$9.9 billion", 9.9, "USD", "billions", "a backlog of $9.9 billion")],  # DELIBERATELY invented
    P2: [mc("segment_revenue", "$4.2 billion", 4.2, "USD", "billions", "Cloud revenue was $4.2 billion", segment="Cloud"),
         mc("revenue_growth", "5%", 5, "%", "units", "up 5%", segment="Cloud"),
         mc("segment_revenue", "$3.1 billion", 3.1, "USD", "billions", "Devices revenue was $3.1 billion",
            segment="Devices"),
         mc("revenue_growth", "5%", 5, "%", "units", "up 5%", segment="Devices")],  # a repeated quote
    P3: [mc("revenue", "$13.0 billion", 13.0, "USD", "billions", "Revenue is expected to be $13.0 billion", forward=True),
         mc("guidance_tolerance", "2%", 2, "%", "units", "plus or minus 2%", forward=True)],
}


def body(claims, n=0):
    return {
        "id": f"resp_mock_{n}", "object": "response", "created_at": 1761550000, "status": "completed",
        "model": ex.MODEL,
        "output": [{"id": f"rs_{n}", "type": "reasoning", "summary": []},
                   {"id": f"msg_{n}", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps({"claims": claims}), "annotations": []}]}],
        "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800}, "output_tokens": 500,
                  "output_tokens_details": {"reasoning_tokens": 200}, "total_tokens": 1500},
        "incomplete_details": None, "error": None, "instructions": None, "metadata": {}, "parallel_tool_calls": True,
        "temperature": 1.0, "top_p": 1.0, "tool_choice": "auto", "tools": [], "store": False,
    }


def passage_of(request_body):
    text = request_body["input"]
    return text.split("<passage>\n", 1)[1].rsplit("\n</passage>", 1)[0]


class MockAPI:
    """Canned replies routed by passage, safe under concurrent requests."""

    def __init__(self, replies=REPLIES, statuses=None, delay=0.0):
        self.replies, self.statuses, self.delay = replies, list(statuses or []), delay
        self.requests, self.lock = [], threading.Lock()
        self.in_flight = self.max_in_flight = 0

    def handler(self, request):
        b = json.loads(request.content.decode("utf-8"))
        with self.lock:
            self.requests.append({"url": str(request.url), "body": b,
                                  "auth_ok": request.headers.get("authorization") == f"Bearer {DUMMY_KEY}"})
            status = self.statuses.pop(0) if self.statuses else 200
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if status != 200:
                return httpx2.Response(status, json={"error": {"message": "mock", "type": "x", "param": None,
                                                               "code": None}})
            return httpx2.Response(200, json=body(self.replies[passage_of(b)], len(self.requests)))
        finally:
            with self.lock:
                self.in_flight -= 1

    def client(self):
        return ex.make_client(DUMMY_KEY, timeout=30,
                              http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(self.handler)))


def run(api, tmp_path, text=TEXT, context=CONTEXT, **kw):
    kw.setdefault("sleep", lambda s: None)
    return ex.extract_detailed(text, context, client=api.client(), cache_dir=tmp_path / "spark", **kw)


def test_schema_is_strict_and_matches_the_contract():
    assert ex.strict_subset_problems(ex.SCHEMA) == []
    item = ex.SCHEMA["properties"]["claims"]["items"]
    assert item["properties"]["metric"]["enum"] == list(get_args(types.Metric))  # 21 classes incl. gross_profit
    assert item["properties"]["bases"]["items"]["enum"] == ["GAAP", "non-GAAP"]
    assert not {"span_start", "span_end", "id"} & set(item["properties"])  # offsets come from code


@pytest.mark.parametrize("text", [
    "x" * 12000,
    ("Revenue was $12 million. " * 500)[:12000],
    "  " + "x" * 5990 + "  revenue $12 million. " + "y" * 5988,
    ("x\n\n" * 4000),
])
def test_long_draft_passages_preserve_source_offsets_and_size_bound(text):
    passages = ex.split_passages(text)
    assert all(0 < len(p) <= ex.MAX_PASSAGE_CHARS for _, p in passages)
    assert all(text[start:start + len(p)] == p for start, p in passages)
    cursor = 0
    for start, passage in passages:
        assert not text[cursor:start].strip()  # no non-whitespace text was lost or duplicated
        cursor = start + len(passage)
    assert not text[cursor:].strip()
    assert len(passages) <= 4000  # max blank-line fanout of a 12,000-character draft


def test_old_passages_keep_the_same_requests_and_cache_keys():
    backend = ex.BACKENDS["meta"]
    text = "  " + "x" * 6000 + "\n\n " + P2 + " \n"
    passages = ex.split_passages(text)
    assert passages == [(2, "x" * 6000), (6005, P2)]
    expected = [ex.request_key(ex.build_request(p, CONTEXT, backend=backend), backend) for _, p in passages]
    assert ex.request_keys(text, CONTEXT, backend=backend) == expected


def test_12000_character_extraction_keeps_existing_concurrency_and_output_bounds(tmp_path):
    text = "\n\n".join(f"Passage {i}.".ljust(598, "x") for i in range(20)) + "xx"
    assert len(text) == 12000
    passages = ex.split_passages(text)
    api = MockAPI(replies={p: [] for _, p in passages}, delay=.005)
    result = run(api, tmp_path, text=text, use_cache=False)
    assert len(result.calls) == len(passages) == 20
    assert api.max_in_flight <= ex.CONCURRENCY
    assert all(r["body"]["max_output_tokens"] == ex.MAX_OUTPUT_TOKENS for r in api.requests)


def test_one_call_per_passage_with_the_context_line(tmp_path):
    api = MockAPI()
    res = run(api, tmp_path)
    bodies = [r["body"] for r in api.requests]
    assert len(bodies) == 3 and all(r["auth_ok"] and r["url"].endswith("/v1/responses") for r in api.requests)
    for b in bodies:
        assert b["model"] == "muse-spark-1.3" and b["reasoning"] == {"effort": "minimal"} and b["store"] is False
        assert b["text"]["format"]["type"] == "json_schema" and b["text"]["format"]["strict"] is True
        assert "response_format" not in b
        assert b["input"].startswith(f"<context>{CONTEXT}</context>\n<passage>\n")
    assert sorted(passage_of(b) for b in bodies) == sorted([P1, P2, P3])
    assert res.context == CONTEXT and res.cost_usd > 0 and res.cached_calls == 0


def test_bases_expand_and_offsets_come_from_code(tmp_path):
    claims = run(MockAPI(), tmp_path).claims
    assert [c.id for c in claims] == [f"c{i}" for i in range(1, len(claims) + 1)]
    assert all(TEXT[c.span_start:c.span_end] == c.span_text for c in claims)
    margins = [c for c in claims if c.metric == "gross_margin"]
    assert [m.basis for m in margins] == ["GAAP", "non-GAAP"] and margins[0].span_start == margins[1].span_start
    assert len(claims) == 10  # 9 grounded model claims, one expanded into two; the invented one is gone
    assert [c.span_start for c in claims] == sorted(c.span_start for c in claims)


def test_repeated_quote_lands_on_successive_occurrences(tmp_path):
    ups = [c for c in run(MockAPI(), tmp_path).claims if c.span_text == "up 5%"]
    first = TEXT.find("up 5%")
    assert [(c.segment, c.span_start) for c in ups] == [("Cloud", first), ("Devices", TEXT.find("up 5%", first + 1))]


def test_invented_claim_is_dropped_and_uncovered_numbers_are_reported(tmp_path):
    replies = {**REPLIES, P3: REPLIES[P3][:1]}  # the model forgets "plus or minus 2%"
    res = run(MockAPI(replies), tmp_path)
    assert not any(c.value_text == "$9.9 billion" for c in res.claims)
    assert any("$9.9 billion" in w and "does not support" in w for w in res.warnings)
    assert [t["text"] for t in res.uncovered] == ["2%"]
    assert any("no claim covers the number '2%'" in w for w in res.warnings)


def test_value_and_scale_are_read_back_from_value_text(tmp_path):
    bad = mc("revenue", "$12.4 billion", 12.4e9, "USD", "millions",
             "revenue for the third quarter of fiscal 2025 of $12.4 billion")  # scale applied, wrong scale word
    res = run(MockAPI({**REPLIES, P1: [bad]}), tmp_path)
    c = next(c for c in res.claims if c.value_text == "$12.4 billion")
    assert (c.value, c.scale, c.unit) == (12.4, "billions", "USD")
    assert any("corrected" in w for w in res.warnings)


def test_cached_responses_skip_the_network(tmp_path):
    first = run(MockAPI(), tmp_path)
    offline = MockAPI(statuses=[500] * 20)  # would fail if it were called
    again = run(offline, tmp_path)
    assert offline.requests == [] and again.cached_calls == 3 and again.cost_usd == 0
    assert [c.model_dump() for c in again.claims] == [c.model_dump() for c in first.claims]
    other = MockAPI()
    run(other, tmp_path, context="Example Corp, fiscal 2025")  # a new context line is a new request
    assert len(other.requests) == 3
    assert len(list((tmp_path / "spark").glob("*.json"))) == 6


def test_calls_run_in_parallel(tmp_path):
    api = MockAPI(delay=0.2)
    run(api, tmp_path, use_cache=False)
    assert api.max_in_flight >= 2


def test_retry_after_503_then_success(tmp_path):
    slept = []
    api = MockAPI(statuses=[503])
    res = run(api, tmp_path, concurrency=1, sleep=slept.append)
    assert len(api.requests) == 4 and len(slept) == 1 and len(res.claims) == 10


def test_every_call_failing_is_an_error_not_an_empty_result(tmp_path):
    with pytest.raises(ex.ExtractionError, match="every passage call failed"):
        run(MockAPI(statuses=[400] * 10), tmp_path, use_cache=False)


def test_one_failed_passage_is_a_warning(tmp_path):
    res = run(MockAPI(statuses=[400]), tmp_path, concurrency=1, use_cache=False)
    assert any("was not extracted" in w for w in res.warnings) and res.claims


def test_missing_api_key_fails_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setattr(ex, "DOTENV_PATH", tmp_path / "missing.env")
    with pytest.raises(ex.ExtractionError, match="MODEL_API_KEY"):
        ex.extract_detailed(TEXT, CONTEXT, cache_dir=tmp_path / "spark")


def test_context_is_inferred_from_the_first_passage():
    gold_first = ("SANTA CLARA, Calif.—Aug. 26, 2026― NVIDIA (NASDAQ: NVDA) today reported revenue for the "
                  "second quarter ended July 26, 2026, of $96.2 billion, up 18% from the previous quarter.")
    assert ex.infer_context(gold_first + "\n\nMore text.") == "NVIDIA, second quarter ended July 26, 2026"
    assert ex.infer_context(TEXT) == CONTEXT
    assert ex.infer_context("Microsoft's revenue for the quarter ended March 31, 2026 was $82.9 billion.") == \
        "Microsoft, quarter ended March 31, 2026"
    assert ex.infer_context("Revenue grew strongly.") is None


def test_extract_logs_its_warnings(tmp_path, caplog):
    api = MockAPI()
    with caplog.at_level(logging.WARNING, logger="receipts.extract"):
        claims = ex.extract(TEXT, CONTEXT, client=api.client(), cache_dir=tmp_path / "spark", sleep=lambda s: None)
    assert len(claims) == 10 and any("$9.9 billion" in r.getMessage() for r in caplog.records)


def test_only_standard_mode_models():
    with pytest.raises(ValueError, match="allowlist"):
        ex._require_allowed("muse-spark-1.3-contributor")
