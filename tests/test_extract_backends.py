"""Extraction backends (RECEIPTS_EXTRACT_BACKEND, docs/open-weights.md).

The default path must send the same bytes as before, so every cached response stays valid; the open-weight backends
(Fireworks, Baseten, a Modal vLLM server) speak Chat Completions. The real OpenAI SDK runs against a mocked HTTP
transport: no network and no real keys.

SYNTHETIC TEST DATA: "Example Corp" is a fictional company and every number below is invented.
"""
import importlib.util
import json
import sys
import threading
from pathlib import Path

import httpx2
import openai
import pytest

from receipts import extract as ex
from receipts import stream

ROOT = Path(__file__).resolve().parent.parent
DUMMY_KEY = "LLM_0_test_dummy_not_a_real_key"
CONTEXT = "Example Corp, third quarter of fiscal 2025"
P1 = "Example Corp (a fictional company) reported revenue of $12.4 billion, up 8% from a year ago."
P2 = "GAAP and non-GAAP gross margins were both 61.5%."
TEXT = f"{P1}\n\n{P2}\n"
Q3 = "third quarter of fiscal 2025"
# request_hash(build_request(P1, CONTEXT)) on 2026-10-27, before and after backends were added
GOLDEN_KEY = "0221ea0f8262c53c4cd48327f01cbbc6a44e032a18d5a695fc5556e440896924"
NO_SLEEP = {"sleep": lambda s: None}


def mc(metric, value_text, value, unit, scale, span, *, bases=(), comparison="none"):
    return {"metric": metric, "metric_text": metric.replace("_", " "), "value_text": value_text, "value": value,
            "unit": unit, "scale": scale, "period_text": Q3, "period_type": "quarter", "comparison": comparison,
            "bases": list(bases), "forward_looking": False, "entity": "Example Corp", "segment": None,
            "span_text": span}


CLAIMS = {P1: [mc("revenue", "$12.4 billion", 12.4, "USD", "billions", "revenue of $12.4 billion"),
               mc("revenue_growth", "8%", 8, "%", "units", "up 8% from a year ago", comparison="YoY")],
          P2: [mc("gross_margin", "61.5%", 61.5, "%", "units", "GAAP and non-GAAP gross margins were both 61.5%",
                  bases=("GAAP", "non-GAAP"))]}
USAGE = {"prompt_tokens": 3000, "completion_tokens": 900, "total_tokens": 3900,
         "prompt_tokens_details": {"cached_tokens": 2000}, "completion_tokens_details": {"reasoning_tokens": 400}}
COST_PER_CALL = (1000 * 1.25 + 2000 * 0.15 + 900 * 4.25) / 1e6  # Muse Spark Open, uncached + cached + output


def legacy_build_request(passage, context, effort="minimal", max_output_tokens=8000):
    """build_request() as it was before backends existed: the default must still send exactly this."""
    return {"model": "muse-spark-1.3", "instructions": ex.INSTRUCTIONS, "input": ex.format_input(passage, context),
            "text": {"format": {"type": "json_schema", "name": ex.SCHEMA_NAME,
                                "description": "Typed numeric claims found in the passage.",
                                "schema": ex.SCHEMA, "strict": True}},
            "reasoning": {"effort": effort}, "max_output_tokens": max_output_tokens, "store": False}


def passage_of(body):
    text = body["messages"][1]["content"]
    return text.split("<passage>\n", 1)[1].rsplit("\n</passage>", 1)[0]


class ChatAPI:
    """An OpenAI-compatible /chat/completions server with canned replies, routed by passage."""

    def __init__(self, *, render=json.dumps, claims=CLAIMS, reject_response_format=False):
        self.render, self.claims, self.reject_response_format = render, claims, reject_response_format
        self.requests, self.lock = [], threading.Lock()

    def handler(self, request):
        body = json.loads(request.content.decode("utf-8"))
        with self.lock:
            self.requests.append({"url": str(request.url), "body": body, "auth": request.headers.get("authorization"),
                                  "read_timeout": (request.extensions.get("timeout") or {}).get("read")})
            n = len(self.requests)
        if self.reject_response_format and "response_format" in body:
            return httpx2.Response(400, json={"error": {"message": "response_format is not supported", "type": "x",
                                                        "param": None, "code": None}})
        return httpx2.Response(200, json={
            "id": f"chatcmpl-{n}", "object": "chat.completion", "created": 1761550000, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": self.render({"claims": self.claims[passage_of(body)]}),
                "reasoning": "private chain of thought"}}],
            "usage": USAGE})

    def http(self):
        return openai.DefaultHttpxClient(transport=httpx2.MockTransport(self.handler))

    def client(self, backend):
        return ex.make_client(DUMMY_KEY, timeout=30, backend=backend, http_client=self.http())


def expected_claims():
    """What code builds from CLAIMS when no backend is involved at all."""
    per = [(s, p, ex.ModelResponse.model_validate_json(json.dumps({"claims": CLAIMS[p]})).claims)
           for s, p in ex.split_passages(TEXT)]
    return [c.model_dump() for c in ex.build_claims(per, [])[0]]


# ---------------------------------------------------------------------------
# The default path: Meta Model API, muse-spark-1.3, unchanged bytes
# ---------------------------------------------------------------------------
def test_default_backend_is_meta_spark_and_untagged():
    b = ex.active_backend()
    assert (b.name, b.model, b.base_url, b.protocol, b.api_key_env) == (
        "meta", "muse-spark-1.3", "https://api.meta.ai/v1", "responses", "MODEL_API_KEY")
    assert ex.backend_tag() == "" and stream.model_name() == "muse-spark-1.3"


@pytest.mark.parametrize("passage,context", [(P1, CONTEXT), (P2, None), ("Revenue was $1.", "")])
def test_default_request_bytes_are_unchanged(passage, context):
    new, old = ex.build_request(passage, context), legacy_build_request(passage, context)
    assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True)
    assert ex.request_key(new) == ex.request_hash(old)


def test_default_request_hash_is_pinned():
    """Every cached response (data/cache/spark, data/snapshot/spark_cache) is keyed by this hash. A failure means the
    request bytes changed and every cached passage is now a paid miss; update GOLDEN_KEY only for intended changes."""
    assert ex.request_hash(ex.build_request(P1, CONTEXT)) == GOLDEN_KEY


# ---------------------------------------------------------------------------
# Selecting a backend
# ---------------------------------------------------------------------------
def test_presets_and_env_overrides(monkeypatch):
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "fireworks")
    b = ex.active_backend()
    assert (b.base_url, b.model, b.api_key_env, b.structured) == (
        "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/muse-spark-open", "FIREWORKS_API_KEY",
        "json_object")
    assert ex.make_backend("baseten").model == "meta-models/muse-spark-open-1026"  # the hackathon slug
    m = ex.resolve_backend({
        "RECEIPTS_EXTRACT_BACKEND": "Modal", "RECEIPTS_EXTRACT_BASE_URL": "https://ws--g-server.us-east.modal.direct/v1/",
        "RECEIPTS_EXTRACT_MODEL": "glimmer-test", "RECEIPTS_EXTRACT_API_KEY_ENV": "MY_TOKEN",
        "RECEIPTS_EXTRACT_STRUCTURED": "prompt", "RECEIPTS_EXTRACT_EFFORT": "medium",
        "RECEIPTS_EXTRACT_MAX_TOKENS": "4000", "RECEIPTS_EXTRACT_TIMEOUT_S": "240"})
    assert (m.name, m.base_url, m.model, m.api_key_env, m.structured, m.effort, m.max_output_tokens,
            m.min_timeout_s) == ("modal", "https://ws--g-server.us-east.modal.direct/v1", "glimmer-test", "MY_TOKEN",
                                 "prompt", "medium", 4000, 240.0)
    assert m.self_hosted and m.open_weights and ex.backend_tag(m)


def test_the_modal_url_has_its_own_variable_and_never_redirects_another_backend():
    url = "https://ws--receipts-glimmer-server.us-east.modal.direct/v1"
    env = {"RECEIPTS_MODAL_BASE_URL": url + "/"}
    assert ex.resolve_backend({**env, "RECEIPTS_EXTRACT_BACKEND": "modal"}).base_url == url
    assert ex.resolve_backend({**env, "RECEIPTS_EXTRACT_BACKEND": "fireworks"}).base_url == \
        "https://api.fireworks.ai/inference/v1"
    assert ex.resolve_backend(env).base_url == "https://api.meta.ai/v1"


def test_wonder_on_meta_keeps_the_responses_request_and_its_own_prices():
    w = ex.make_backend("meta", model="muse-wonder-1.0")
    assert w.prices["output"] == 20.0 and ex.backend_tag(w) != ""
    assert ex.build_request(P1, CONTEXT, backend=w) == {**legacy_build_request(P1, CONTEXT), "model": "muse-wonder-1.0"}


@pytest.mark.parametrize("env", [
    {"RECEIPTS_EXTRACT_BACKEND": "openai"},
    {"RECEIPTS_EXTRACT_MODEL": "muse-spark-1.3-contributor"},
    {"RECEIPTS_EXTRACT_BACKEND": "fireworks", "RECEIPTS_EXTRACT_MODEL": "accounts/x/models/y-contributor"},
    {"RECEIPTS_EXTRACT_MODEL": "gpt-4o"},
    {"RECEIPTS_EXTRACT_BACKEND": "baseten", "RECEIPTS_EXTRACT_STRUCTURED": "xml"},
    {"RECEIPTS_EXTRACT_TIMEOUT_S": "soon"},
    {"RECEIPTS_EXTRACT_TIMEOUT_S": "0"},
    {"RECEIPTS_EXTRACT_TIMEOUT_S": "-1"},
    {"RECEIPTS_EXTRACT_TIMEOUT_S": "nan"},
    {"RECEIPTS_EXTRACT_TIMEOUT_S": "inf"},
    {"RECEIPTS_EXTRACT_MAX_TOKENS": "0"},
    {"RECEIPTS_EXTRACT_MAX_TOKENS": "-10"},
    {"RECEIPTS_EXTRACT_MAX_TOKENS": "1.5"},
    {"RECEIPTS_EXTRACT_EFFORT": "none"},
    {"RECEIPTS_EXTRACT_BACKEND": "fireworks", "RECEIPTS_EXTRACT_EFFORT": "minimal"},
    {"RECEIPTS_EXTRACT_BACKEND": "modal", "RECEIPTS_EXTRACT_EFFORT": "typo"},
    {"RECEIPTS_EXTRACT_BASE_URL": "not-a-url"},
    {"RECEIPTS_EXTRACT_BASE_URL": "https://example.test/v1?key=not-real"},
    {"RECEIPTS_EXTRACT_BASE_URL": "https://user:password@example.test/v1"},
    {"RECEIPTS_EXTRACT_API_KEY_ENV": "a-key-is-not-a-variable-name"},
])
def test_bad_settings_fail_loudly(env):
    with pytest.raises(ex.ExtractionError):
        ex.resolve_backend(env)


def test_dotenv_can_select_the_backend_but_the_shell_wins(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("RECEIPTS_EXTRACT_BACKEND=baseten\nUNRELATED_SECRET=do-not-read\n")
    monkeypatch.setattr(ex, "DOTENV_PATH", dotenv)
    assert ex.active_backend().name == "baseten"
    assert "UNRELATED_SECRET" not in ex.settings_env()  # only RECEIPTS_EXTRACT_* lines are read
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "meta")
    assert ex.active_backend().name == "meta"


# ---------------------------------------------------------------------------
# Chat Completions backends
# ---------------------------------------------------------------------------
def test_fireworks_sends_chat_completions_with_json_mode(tmp_path):
    b = ex.make_backend("fireworks")
    api = ChatAPI()
    res = ex.extract_detailed(TEXT, CONTEXT, client=api.client(b), cache_dir=tmp_path, backend=b, **NO_SLEEP)
    assert len(api.requests) == 2
    r = api.requests[0]
    assert r["url"] == "https://api.fireworks.ai/inference/v1/chat/completions" and r["auth"] == f"Bearer {DUMMY_KEY}"
    body = r["body"]
    assert body["model"] == "accounts/fireworks/models/muse-spark-open"
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 12000  # "minimal" is Meta-only
    system, user = body["messages"]
    assert system["role"] == "system" and system["content"].startswith(ex.INSTRUCTIONS)
    assert json.dumps(ex.SCHEMA, ensure_ascii=False, separators=(",", ":")) in system["content"]
    assert user["content"].startswith(f"<context>{CONTEXT}</context>\n<passage>\n")
    assert not {"input", "instructions", "text", "store", "reasoning"} & set(body)
    assert (res.backend, res.model) == ("fireworks", b.model)
    assert [c.model_dump() for c in res.claims] == expected_claims()
    assert res.cost_usd == pytest.approx(2 * COST_PER_CALL)
    assert res.calls[0]["usage"] == {"input_tokens": 3000, "cached_tokens": 2000, "output_tokens": 900,
                                     "reasoning_tokens": 400}


def test_baseten_puts_the_schema_in_the_prompt_only(tmp_path):
    b = ex.make_backend("baseten")
    api = ChatAPI()
    res = ex.extract_detailed(TEXT, CONTEXT, client=api.client(b), cache_dir=tmp_path, backend=b, **NO_SLEEP)
    body = api.requests[0]["body"]
    assert api.requests[0]["url"] == "https://inference.baseten.co/v1/chat/completions"
    assert "response_format" not in body and body["model"] == "meta-models/muse-spark-open-1026"
    assert body["reasoning_effort"] == "low"
    assert [c.model_dump() for c in res.claims] == expected_claims()


def test_modal_glimmer_streams_with_a_proxy_token_and_a_longer_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "modal")
    monkeypatch.setenv("RECEIPTS_MODAL_BASE_URL", "https://ws--receipts-glimmer-server.us-east.modal.direct/v1")
    monkeypatch.delenv("MODAL_PROXY_TOKEN", raising=False)
    monkeypatch.setenv("MODAL_PROXY_TOKEN_ID", "wk-test")
    monkeypatch.setenv("MODAL_PROXY_TOKEN_SECRET", "ws-test")
    api = ChatAPI()
    client = ex.make_client(timeout=30, http_client=api.http())  # key and URL from the environment
    results = list(ex.iter_extract(TEXT, CONTEXT, client=client, cache_dir=tmp_path, timeout_s=30, **NO_SLEEP))
    r = api.requests[0]
    assert r["url"] == "https://ws--receipts-glimmer-server.us-east.modal.direct/v1/chat/completions"
    assert r["auth"] == "Bearer wk-test.ws-test"  # a Modal Proxy Token is id.secret
    body = r["body"]
    assert body["model"] == "muse-glimmer" and body["chat_template_kwargs"] == {"reasoning_strength": "low"}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == ex.SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is True and "reasoning_effort" not in body
    assert all(x["read_timeout"] >= 180 for x in api.requests)  # the self-hosted floor beats the API's 30 s
    assert all(res.ok for res in results) and sum(len(res.claims) for res in results) == 4
    assert all(res.cost_usd > 0 and res.call["cost_is_estimate"] for res in results)  # unknown GPU billing


def test_json_is_recovered_from_fences_prose_think_blocks_and_bare_lists():
    obj = {"claims": CLAIMS[P2]}
    assert ex.json_from_message(json.dumps(obj)) == (json.dumps(obj), None)
    for message, how in ((f"Here you go:\n```json\n{json.dumps(obj)}\n```", "code_fence"),
                         (f"Sure. {json.dumps(obj)} Done.", "surrounding_text"),
                         (f"<think>scratch {{not json}}</think>{json.dumps(CLAIMS[P2])}", "think_block+bare_list")):
        text, repair = ex.json_from_message(message)
        assert json.loads(text) == obj and repair == how


def test_a_fenced_reply_still_extracts_and_the_repair_is_recorded(tmp_path):
    b = ex.make_backend("fireworks")
    api = ChatAPI(render=lambda obj: "```json\n" + json.dumps(obj) + "\n```")
    res = ex.extract_detailed(TEXT, CONTEXT, client=api.client(b), cache_dir=tmp_path, backend=b, **NO_SLEEP)
    assert [c.model_dump() for c in res.claims] == expected_claims()
    assert all(c["json_repair"] == "code_fence" for c in res.calls)


def test_one_malformed_claim_costs_that_claim_not_the_passage(tmp_path):
    bad = {**CLAIMS[P1][1], "unit": "percent", "confidence": 0.9}  # not in the Unit enum; an unknown key
    b = ex.make_backend("baseten")
    api = ChatAPI(claims={**CLAIMS, P1: [CLAIMS[P1][0], bad]})
    res = ex.extract_detailed(TEXT, CONTEXT, client=api.client(b), cache_dir=tmp_path, backend=b, **NO_SLEEP)
    first = next(c for c in res.calls if c["passage"] == 1)
    assert first["status"] == "ok" and first["claims_rejected"] == 1 and "unit" in first["problem"]
    assert sorted(c.value_text for c in res.claims) == ["$12.4 billion", "61.5%", "61.5%"]
    assert any("no claim covers the number '8%'" in w for w in res.warnings)  # code still reports the gap


def test_a_rejected_response_format_falls_back_to_the_schema_in_the_prompt(tmp_path):
    b = ex.make_backend("modal", base_url="https://example.test/v1")
    api = ChatAPI(reject_response_format=True)
    res = ex.extract_detailed(TEXT, CONTEXT, client=api.client(b), cache_dir=tmp_path, backend=b, concurrency=1,
                              **NO_SLEEP)
    assert [("response_format" in r["body"]) for r in api.requests] == [True, False, True, False]
    assert all(c["status"] == "ok" and c["structured_fallback"] for c in res.calls)
    assert [c.model_dump() for c in res.claims] == expected_claims()


@pytest.mark.parametrize("message,param", [
    ("maximum context length exceeded", None),
    ("model is not supported", "model"),
    ("invalid schema in response_format: required must include every property", "response_format"),
])
def test_other_bad_requests_never_retry_without_the_schema(message, param):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(400, json={"error": {"message": message, "param": param}})

    b = ex.make_backend("modal", base_url="https://example.test/v1")
    with ex.make_client(DUMMY_KEY, backend=b,
                        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))) as client:
        with pytest.raises(openai.BadRequestError):
            ex.send_request(client, ex.build_request(P1, CONTEXT, backend=b), b)
    assert len(requests) == 1 and "response_format" in requests[0]


def test_custom_responses_endpoints_cannot_reuse_official_meta_cache(tmp_path):
    meta = ex.make_backend("meta")
    custom = ex.make_backend("meta", base_url="https://other.example/v1")
    request = ex.build_request(P1, CONTEXT, backend=meta)
    assert request == ex.build_request(P1, CONTEXT, backend=custom)  # same body, different destination
    original_key, custom_key = ex.request_key(request, meta), ex.request_key(request, custom)
    assert original_key == GOLDEN_KEY and custom_key != original_key
    (tmp_path / f"{original_key}.json").write_text(json.dumps({"output_text": json.dumps({"claims": CLAIMS[P1]})}))
    assert ex.passages_to_call(P1, CONTEXT, cache_dirs=[tmp_path], backend=meta) == 0
    assert ex.passages_to_call(P1, CONTEXT, cache_dirs=[tmp_path], backend=custom) == 1


def test_cache_keys_never_cross_backends_and_records_say_where_they_came_from(tmp_path):
    meta, fw = ex.make_backend("meta"), ex.make_backend("fireworks")
    other = ex.make_backend("fireworks", base_url="https://other.example/v1")
    keys = {name: ex.request_keys(TEXT, CONTEXT, backend=bk) for name, bk in (("meta", meta), ("fw", fw),
                                                                               ("other", other))}
    assert len({k for ks in keys.values() for k in ks}) == 6
    first = ex.extract_detailed(TEXT, CONTEXT, client=ChatAPI().client(fw), cache_dir=tmp_path, backend=fw, **NO_SLEEP)
    offline = ChatAPI()
    again = ex.extract_detailed(TEXT, CONTEXT, client=offline.client(fw), cache_dir=tmp_path, backend=fw, **NO_SLEEP)
    assert offline.requests == [] and again.cached_calls == 2
    assert [c.model_dump() for c in again.claims] == [c.model_dump() for c in first.claims]
    rec = json.loads((tmp_path / f"{keys['fw'][0]}.json").read_text())
    assert (rec["backend"], rec["model"], rec["effort"]) == ("fireworks", fw.model, "low") and "latency_s" in rec
    assert ex.passages_to_call(TEXT, CONTEXT, cache_dirs=[tmp_path], backend=fw) == 0
    assert ex.passages_to_call(TEXT, CONTEXT, cache_dirs=[tmp_path], backend=meta) == 2  # never another model's answer


def test_private_mode_never_falls_back_to_meta(monkeypatch):
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "modal")
    monkeypatch.setenv("MODEL_API_KEY", "a-meta-key-that-must-not-be-used")
    with pytest.raises(ex.ExtractionError, match="RECEIPTS_MODAL_BASE_URL"):
        ex.make_client()
    monkeypatch.setenv("RECEIPTS_MODAL_BASE_URL", "https://example.test/v1")
    for name in ("MODAL_PROXY_TOKEN", "MODAL_PROXY_TOKEN_ID", "MODAL_PROXY_TOKEN_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ex.ExtractionError, match="MODAL_PROXY_TOKEN"):
        ex.extract_detailed(TEXT, CONTEXT, use_cache=False)


# ---------------------------------------------------------------------------
# What the rest of NoCap sees
# ---------------------------------------------------------------------------
def test_stream_reports_the_backend_model_and_keeps_recordings_apart(tmp_path, monkeypatch):
    default_fp = stream.cache_fingerprint(tmp_path)
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "modal")
    monkeypatch.setenv("RECEIPTS_EXTRACT_BASE_URL", "https://example.test/v1")
    assert stream.model_name() == "muse-glimmer" and stream.cache_fingerprint(tmp_path) != default_fp
    monkeypatch.setattr(ex, "iter_extract", lambda text, context=None, **kw: iter(
        [ex.PassageResult(1, 0, len(text), [], {"status": "ok", "cached": False, "cost_usd": 0.0})]))
    events = list(stream.check_events("No numbers here.", draft_hash="h", as_of=None, context="Example Corp",
                                      store=object()))
    assert events[-1][0] == "done" and events[-1][1]["model"] == "muse-glimmer"
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "no-such-backend")
    assert stream.cache_fingerprint(tmp_path) != default_fp  # a misconfigured backend replays nothing


def test_eval_results_are_cached_per_backend(monkeypatch):
    spec = importlib.util.spec_from_file_location("receipts_eval_backends", ROOT / "eval" / "eval.py")
    ev = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ev
    spec.loader.exec_module(ev)
    default = ev.verifier_fingerprint(None)
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "fireworks")
    assert ev.verifier_fingerprint(None) != default
