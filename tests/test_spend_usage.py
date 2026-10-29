"""Offline accounting regressions: unknown, retried, canceled and late model calls."""
import json
import threading
import time
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from receipts import extract, rewrite, stream
from test_rewrite_safety import CONTEXT, TEXT, claims_for
from test_verify import FakeStore


META = extract.BACKENDS["meta"]
TOKENS = {"input_tokens": 1000, "output_tokens": 100,
          "input_tokens_details": NS(cached_tokens=100),
          "output_tokens_details": NS(reasoning_tokens=20)}


def response(usage=None, *, output='{"claims": []}'):
    return NS(id="offline", status="completed", output_text=output, usage=usage)


def client(fn):
    return NS(responses=NS(create=fn), chat=NS(completions=NS(create=fn)))


def status_error(code=503, *, format_error=False):
    body = {"message": "response_format is unsupported" if format_error else "offline failure",
            "param": "response_format" if format_error else None}
    req = httpx.Request("POST", "https://offline.invalid/v1/responses")
    cls = openai.BadRequestError if code == 400 else openai.APIStatusError
    return cls(body["message"], response=httpx.Response(code, request=req), body=body)


def run_extract(fn, **kwargs):
    return list(extract.iter_extract("First passage.", "", client=client(fn), use_cache=False,
                                     backend=META, sleep=lambda _: None, **kwargs))


@pytest.mark.parametrize("changes", [
    {"input_tokens": None}, {"output_tokens": None}, {"input_tokens": True},
    {"output_tokens": -1}, {"output_tokens": 1.5}, {"input_tokens": "1000"},
    {"input_tokens": float("nan")}, {"output_tokens": float("inf")},
    {"input_tokens": 10 ** 400},
    {"input_tokens_details": NS(cached_tokens=1001)},
    {"output_tokens_details": NS(reasoning_tokens=101)},
    {"input_tokens_details": NS(cached_tokens=None)},
])
def test_invalid_usage_keeps_the_unknown_charge(changes):
    usage = NS(**{**TOKENS, **changes})
    assert extract._usage(response(usage)) is None
    item = run_extract(lambda **_: response(usage))[0]
    assert item.ok and item.cost_usd >= .05
    assert item.call["cost_is_estimate"] and item.call["estimated_calls"] == 1


def test_missing_required_usage_is_unknown_but_valid_zero_usage_is_free():
    assert extract._usage(response(NS(input_tokens=0))) is None
    assert extract._usage(response(NS(output_tokens=0))) is None
    item = run_extract(lambda **_: response(NS(input_tokens=0, output_tokens=0)))[0]
    assert item.cost_usd == 0 and not item.call["cost_is_estimate"]
    assert item.call["model_calls"] == 1


def test_chat_usage_uses_the_same_strict_validation():
    valid = NS(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=NS(cached_tokens=10))
    assert extract._usage(response(valid)) == {
        "input_tokens": 100, "output_tokens": 20, "cached_tokens": 10, "reasoning_tokens": 0}
    valid.completion_tokens = False
    assert extract._usage(response(valid)) is None


def test_retry_keeps_the_unknown_first_attempt_and_known_second_usage():
    attempts = []

    def create(**_):
        attempts.append(True)
        if len(attempts) == 1:
            raise status_error()
        return response(NS(**TOKENS))

    item = run_extract(create, max_attempts=2)[0]
    known = extract.cost_usd(extract._usage(response(NS(**TOKENS))))
    estimate = extract.estimate_request_cost(extract.build_request("First passage.", "", backend=META), META)
    assert item.ok and item.cost_usd == pytest.approx(estimate + known)
    assert item.call["model_calls"] == 2 and item.call["estimated_calls"] == 1


def test_schema_fallback_counts_both_http_attempts():
    backend = extract.make_backend("fireworks", structured="json_schema")
    request = extract.build_request("First passage.", "", backend=backend)
    meter = extract.RequestUsage(backend)
    requests = []

    def create(**body):
        requests.append(body)
        if len(requests) == 1:
            raise status_error(400, format_error=True)
        return NS(id="chat", usage=NS(**TOKENS), choices=[])

    result = extract.send_request(client(create), request, backend, usage=meter)
    billing = meter.finish()
    assert result.structured_fallback and len(requests) == 2
    assert "response_format" in requests[0] and "response_format" not in requests[1]
    assert billing["model_calls"] == 2 and billing["estimated_calls"] == 1
    assert billing["cost_usd"] == pytest.approx(
        extract.estimate_request_cost(request, backend) + extract.cost_usd(extract._usage(response(NS(**TOKENS)))))


def test_schema_fallback_cannot_start_after_deadline(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(extract.time, "monotonic", lambda: now[0])
    backend = extract.make_backend("fireworks", structured="json_schema")
    meter = extract.RequestUsage(backend, deadline=2.0)
    attempts = []

    def create(**_):
        attempts.append(True)
        now[0] = 3.0
        raise status_error(400, format_error=True)

    with pytest.raises(extract._RequestStopped):
        extract.send_request(client(create), extract.build_request("Draft.", "", backend=backend),
                             backend, usage=meter)
    assert len(attempts) == 1 and meter.finish()["model_calls"] == 1


def test_expired_deadline_never_starts_or_charges_a_call():
    def never(**_):
        pytest.fail("expired requests must not reach the SDK")

    item = run_extract(never, deadline=time.monotonic() - 1)[0]
    assert not item.ok and item.cost_usd == 0
    assert item.call["model_calls"] == item.call["estimated_calls"] == 0


def test_deadline_charges_only_started_calls_and_late_retry_cannot_change_settlement():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    attempts, records = [], {}

    def create(**_):
        attempts.append(True)
        started.set()
        try:
            assert release.wait(2)
            raise status_error()
        finally:
            finished.set()

    try:
        items = list(extract.iter_extract(
            "First passage.\n\nSecond passage.\n\nThird passage.", "", client=client(create),
            backend=META, use_cache=False, concurrency=1, deadline=time.monotonic() + .1,
            sleep=lambda _: None, usage_callback=lambda passage, rec: records.setdefault(passage, rec),
        ))
        assert started.is_set() and len(attempts) == 1
        assert len(items) == len(records) == 3
        assert sum(r["model_calls"] for r in records.values()) == 1
        assert sum(r["estimated_calls"] for r in records.values()) == 1
        before = json.dumps(records, sort_keys=True)
        assert items[0].cost_usd >= .05 and all(item.cost_usd == 0 for item in items[1:])
    finally:
        release.set()
        assert finished.wait(2)
    # The first worker may wake after settlement, but no retry can pass the sealed meter.
    assert len(attempts) == 1 and json.dumps(records, sort_keys=True) == before


def test_closing_extractor_accounts_pending_workers_once_and_freezes_late_usage():
    blocked, release, finished = threading.Event(), threading.Event(), threading.Event()
    records = []

    def create(**request):
        if "Second passage." in request["input"]:
            blocked.set()
            try:
                assert release.wait(2)
                return response(NS(**TOKENS))
            finally:
                finished.set()
        assert blocked.wait(2)
        return response(NS(**TOKENS))

    items = extract.iter_extract("First passage.\n\nSecond passage.", "", client=client(create),
                                 backend=META, use_cache=False, concurrency=2,
                                 usage_callback=lambda passage, rec: records.append((passage, rec)))
    try:
        assert next(items).passage == 1
        items.close()
        assert len(records) == 2 and {p for p, _ in records} == {1, 2}
        assert sum(r["model_calls"] for _, r in records) == 2
        assert sum(r["estimated_calls"] for _, r in records) == 1
        before = json.dumps(records)
    finally:
        release.set()
        assert finished.wait(2)
    assert json.dumps(records) == before


def test_cache_hit_remains_zero_new_calls_even_when_original_usage_was_unknown(tmp_path):
    first = list(extract.iter_extract("Draft.", "", client=client(lambda **_: response()),
                                      backend=META, cache_dir=tmp_path))
    assert first[0].call["cost_is_estimate"]

    def never(**_):
        pytest.fail("cache hit must not call the model")

    stats = stream.CheckStats()
    events = list(stream.check_events("Draft.", draft_hash="offline", as_of=None, context="",
                                      store=FakeStore(), stats=stats, client=client(never),
                                      backend=META, cache_dir=tmp_path))
    assert events[-1][0] == "done"
    assert stats.cost_usd == 0 and stats.model_calls == 0 and not stats.cost_is_estimate


def test_check_error_reports_the_started_unknown_cost_without_double_charging():
    def timeout(**_):
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://offline.invalid"))

    stats = stream.CheckStats()
    events = list(stream.check_events("Draft.", draft_hash="offline", as_of=None, context="",
                                      store=FakeStore(), stats=stats, client=client(timeout),
                                      backend=META, use_cache=False))
    assert events[-1][0] == "error" and not stats.complete
    payload = events[-1][1]
    assert payload["cost_usd"] == pytest.approx(stats.cost_usd)
    assert stats.cost_usd >= .05 and stats.model_calls == stats.estimated_calls == 1
    assert payload["cost_is_estimate"] and payload["model_calls"] == 1


def test_handling_error_retains_known_provider_usage(monkeypatch):
    def broken(*_):
        raise ValueError("synthetic parsing bug")

    monkeypatch.setattr(extract, "_read_response", broken)
    item = run_extract(lambda **_: response(NS(**TOKENS)))[0]
    assert not item.ok and item.call["status"] == "internal_error"
    assert item.cost_usd == pytest.approx(extract.cost_usd(extract._usage(response(NS(**TOKENS)))))
    assert not item.call["cost_is_estimate"] and item.call["model_calls"] == 1


def test_partial_executor_submission_failure_still_settles_started_work(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_pool = extract.ThreadPoolExecutor

    class PartialPool(original_pool):
        submitted = 0

        def submit(self, fn, *args, **kwargs):
            self.submitted += 1
            if self.submitted == 2:
                assert started.wait(2)
                raise RuntimeError("offline thread-start failure")
            return super().submit(fn, *args, **kwargs)

    def create(**_):
        started.set()
        try:
            assert release.wait(2)
            return response(NS(**TOKENS))
        finally:
            finished.set()

    monkeypatch.setattr(extract, "ThreadPoolExecutor", PartialPool)
    stats = stream.CheckStats()
    try:
        events = list(stream.check_events(
            "First passage.\n\nSecond passage.", draft_hash="offline", as_of=None, context="",
            store=FakeStore(), stats=stats, client=client(create), backend=META, use_cache=False))
        assert events[-1][0] == "error"
        assert stats.cost_usd >= .05 and stats.model_calls == stats.estimated_calls == 1
        assert events[-1][1]["cost_is_estimate"]
    finally:
        release.set()
        assert finished.wait(2)


@pytest.mark.parametrize("outcome", ["timeout", "missing_usage", "invalid_output"])
def test_wonder_failure_or_missing_usage_retains_its_estimate(monkeypatch, outcome):
    monkeypatch.setattr(extract, "active_backend", lambda: META)
    monkeypatch.setattr(extract, "passages_to_call", lambda *_, **__: 0)

    def cached(passage, *_, **__):
        yield extract.PassageResult(1, 0, len(passage), claims_for(passage),
                                    {"status": "ok", "cached": True, "cost_usd": 0})

    monkeypatch.setattr(extract, "iter_extract", cached)

    def create(**_):
        if outcome == "timeout":
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://offline.invalid"))
        return response(output="not JSON" if outcome == "invalid_output" else '{"edits": []}')

    stats = rewrite.RewriteStats()
    events = list(rewrite.fix_events(TEXT, context=CONTEXT, store=FakeStore(), stats=stats,
                                     wonder_client_factory=lambda: client(create)))
    assert events[-1][0] == ("result" if outcome == "missing_usage" else "error")
    assert stats.cost_usd >= .15 and stats.model_calls == stats.estimated_calls == 1
    assert events[-1][1]["cost_is_estimate"]
    assert events[-1][1]["cost_usd"] == pytest.approx(stats.cost_usd)


def test_fix_extraction_timeout_is_charged_before_aborting():
    def timeout(**_):
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://offline.invalid"))

    stats = rewrite.RewriteStats()
    events = list(rewrite.fix_events(TEXT, context=CONTEXT, store=FakeStore(), stats=stats,
                                     extract_client_factory=lambda: client(timeout), use_cache=False))
    assert events[-1][0] == "error" and not stats.complete
    assert stats.cost_usd >= .05 and stats.model_calls == stats.estimated_calls == 1
    assert events[-1][1]["cost_is_estimate"]


def test_detailed_extraction_error_preserves_unknown_started_usage():
    def timeout(**_):
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://offline.invalid"))

    with pytest.raises(extract.ExtractionError) as caught:
        extract.extract_detailed("Draft.", "", client=client(timeout), backend=META, use_cache=False)
    error = caught.value
    assert error.cost_usd >= .05 and error.model_calls == error.estimated_calls == 1
    assert error.cost_is_estimate and len(error.calls) == 1


@pytest.mark.parametrize("stage", ["_read_response", "build_claims"])
def test_detailed_postprocessing_failure_preserves_known_cost(monkeypatch, stage):
    def broken(*_):
        raise ValueError("synthetic parsing bug")

    monkeypatch.setattr(extract, stage, broken)
    with pytest.raises(extract.ExtractionError) as caught:
        extract.extract_detailed("Draft.", "", client=client(lambda **_: response(NS(**TOKENS))),
                                 backend=META, use_cache=False)
    assert caught.value.cost_usd == pytest.approx(extract.cost_usd(extract._usage(response(NS(**TOKENS)))))
    assert caught.value.model_calls == 1 and not caught.value.cost_is_estimate


def test_cache_disappearing_after_preflight_cannot_bypass_admission(tmp_path):
    list(extract.iter_extract("Draft.", "", client=client(lambda **_: response(NS(**TOKENS))),
                              backend=META, cache_dir=tmp_path))
    assert extract.passages_to_call("Draft.", "", backend=META, cache_dirs=(tmp_path,)) == 0
    for cache_file in tmp_path.glob("*.json"):
        cache_file.unlink()
    admissions = []

    def reject(estimate):
        admissions.append(estimate)
        raise extract.ModelCallRejected()

    def never(**_):
        pytest.fail("a cached-only operation may not start a newly needed SDK call")

    stats = stream.CheckStats()
    events = list(stream.check_events("Draft.", draft_hash="offline", as_of=None, context="",
                                      store=FakeStore(), stats=stats, client=client(never),
                                      backend=META, cache_dir=tmp_path, before_model_call=reject))
    assert len(admissions) == 1 and admissions[0] >= .05
    assert stats.model_calls == stats.estimated_calls == 0 and stats.cost_usd == 0
    assert events[-1][0] == "error" and "spending guard" in events[-1][1]["message"]


def test_rejected_retry_is_not_charged_and_does_not_discard_the_first_attempt():
    admissions, sdk_calls = [], []

    def admit_once(estimate):
        admissions.append(estimate)
        if len(admissions) > 1:
            raise extract.ModelCallRejected()

    def create(**_):
        sdk_calls.append(True)
        raise status_error()

    item = run_extract(create, max_attempts=4, before_model_call=admit_once)[0]
    assert item.call["status"] == "admission_rejected"
    assert len(admissions) == 2 and len(sdk_calls) == 1
    assert item.call["model_calls"] == item.call["estimated_calls"] == 1
    assert item.cost_usd == admissions[0]


def test_schema_fallback_must_pass_admission_before_its_own_sdk_call():
    admissions, sdk_calls = [], []
    backend = extract.make_backend("fireworks", structured="json_schema")

    def admit_once(estimate):
        admissions.append(estimate)
        if len(admissions) > 1:
            raise extract.ModelCallRejected()

    def create(**_):
        sdk_calls.append(True)
        raise status_error(400, format_error=True)

    item = list(extract.iter_extract("Draft.", "", client=client(create), backend=backend,
                                     use_cache=False, max_attempts=4, before_model_call=admit_once))[0]
    assert len(admissions) == 2 and len(sdk_calls) == 1
    assert item.call["status"] == "admission_rejected" and item.call["model_calls"] == 1
    assert item.cost_usd == admissions[0]


def test_shared_admission_gate_limits_concurrent_passages():
    lock, admitted, sdk_calls = threading.Lock(), [], []

    def admit_once(estimate):
        with lock:
            if admitted:
                raise extract.ModelCallRejected()
            admitted.append(estimate)

    def create(**_):
        sdk_calls.append(True)
        return response(NS(**TOKENS))

    stats = stream.CheckStats()
    events = list(stream.check_events("First.\n\nSecond.\n\nThird.", draft_hash="offline", as_of=None,
                                      context="", store=FakeStore(), stats=stats, client=client(create),
                                      backend=META, use_cache=False, concurrency=3, before_model_call=admit_once))
    assert len(sdk_calls) == stats.model_calls == 1
    assert not stats.cost_is_estimate and stats.cost_usd > 0
    assert stats.passages_failed == 2 and events[-1][0] == "error"


@pytest.mark.parametrize("original_cached", [False, True])
def test_fix_passes_admission_to_extraction_and_wonder(monkeypatch, original_cached):
    monkeypatch.setattr(extract, "active_backend", lambda: META)
    if original_cached:
        monkeypatch.setattr(extract, "passages_to_call", lambda *_, **__: 0)

        def cached(passage, *_, **__):
            yield extract.PassageResult(1, 0, len(passage), claims_for(passage),
                                        {"status": "ok", "cached": True, "cost_usd": 0})

        monkeypatch.setattr(extract, "iter_extract", cached)
    estimates = []

    def reject(estimate):
        estimates.append(estimate)
        raise extract.ModelCallRejected()

    def never(**_):
        pytest.fail("admission was rejected before either model could be called")

    stats = rewrite.RewriteStats()
    events = list(rewrite.fix_events(TEXT, context=CONTEXT, store=FakeStore(), stats=stats,
                                     extract_client_factory=lambda: client(never),
                                     wonder_client_factory=lambda: client(never),
                                     use_cache=original_cached, before_model_call=reject))
    assert len(estimates) == 1 and estimates[0] >= (.15 if original_cached else .05)
    assert events[-1][0] == "error" and "spending guard" in events[-1][1]["message"]
    assert stats.cost_usd == 0 and stats.model_calls == stats.estimated_calls == 0


def test_aggregate_estimate_covers_retry_and_chat_fallback_without_cache_assumptions():
    backend = extract.make_backend("fireworks", structured="json_schema")
    observed, sdk_calls = [], []

    def create(**_):
        sdk_calls.append(True)
        if len(sdk_calls) in (1, 3):
            raise status_error(400, format_error=True)
        if len(sdk_calls) == 2:
            raise status_error()
        return NS(id="chat", usage=NS(**TOKENS), choices=[NS(
            finish_reason="stop", message=NS(content='{"claims": []}'))])

    estimate = extract.extraction_estimate("Draft.", "", max_attempts=2, backend=backend)
    item = list(extract.iter_extract("Draft.", "", client=client(create), backend=backend,
                                     use_cache=False, max_attempts=2, sleep=lambda _: None,
                                     before_model_call=observed.append))[0]
    assert item.ok and len(sdk_calls) == len(observed) == 4
    assert sum(observed) <= estimate and item.cost_usd <= estimate
    assert extract.extraction_estimate("", "", backend=backend) == 0


@pytest.mark.parametrize("backend", [
    extract.make_backend("modal", base_url="https://offline.invalid/v1"),
    extract.make_backend("meta", self_hosted=True),
    extract.make_backend("meta", prices={"input": 0, "cached_input": 0, "output": 0}),
])
def test_token_usage_cannot_claim_zero_or_exact_compute_billed_cost(backend):
    meter = extract.RequestUsage(backend)
    request = extract.build_request("Draft.", "", backend=backend)
    meter.call(lambda: response(NS(**TOKENS)), request)
    billing = meter.finish()
    assert billing["cost_usd"] == extract.estimate_request_cost(request, backend)
    assert billing["cost_usd"] > 0 and billing["cost_is_estimate"]
    assert billing["model_calls"] == billing["estimated_calls"] == 1


def test_detailed_pre_dispatch_guard_preserves_diagnostic_and_zero_charge():
    class CacheOnlyClient:
        def __getattr__(self, name):
            raise extract.ExtractionError("cache-only benchmark: no request was sent")

    with pytest.raises(extract.ExtractionError, match="cache-only benchmark") as caught:
        extract.extract_detailed("Draft.", "", client=CacheOnlyClient(), backend=META, use_cache=False)
    assert caught.value.cost_usd == 0 and caught.value.model_calls == caught.value.estimated_calls == 0
    assert not caught.value.cost_is_estimate and len(caught.value.calls) == 1
