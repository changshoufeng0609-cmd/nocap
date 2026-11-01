"""Retry-After on interactive extraction (Meta API audit P1-3): a wait longer than a person can sit through is not
retried; the error says how long Meta asked NoCap to wait. Local fakes only; no network or model call."""
import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from receipts import api, extract
from test_api import parse_sse, settings
from test_spend_usage import client as model_client, response

TEXT = "NVIDIA revenue was $96.2 billion."


def busy(retry_after=None, *, code=503, header="retry-after"):
    headers = {} if retry_after is None else {header: str(retry_after)}
    request = httpx.Request("POST", "https://offline.invalid/v1/responses")
    body = {"message": "offline overload", "code": "service_overloaded"}
    return openai.APIStatusError(body["message"], response=httpx.Response(code, request=request, headers=headers),
                                 body=body)


def scripted(*outcomes):
    calls = []

    def fn():
        outcome = outcomes[len(calls)]
        calls.append(outcome)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return fn, calls


@pytest.mark.parametrize("error", [busy(60), busy(15, code=429), busy(60_000, header="retry-after-ms")])
def test_a_wait_longer_than_the_interactive_maximum_is_not_retried(error):
    fn, calls = scripted(error, "never sent")
    slept = []
    out = extract.call_with_deadline(fn, sleep=slept.append, max_attempts=2)
    assert not out.ok and out.attempts == 1 and len(calls) == 1 and slept == []
    assert out.error["kind"] == "http_error" and out.error["retry_after_s"] > extract.INTERACTIVE_MAX_SLEEP_S


def test_a_short_retry_after_is_honored_and_retried():
    # server_shutting_down asks for a few seconds: wait at least that long, then retry once.
    fn, calls = scripted(busy(2), "ok")
    slept = []
    out = extract.call_with_deadline(fn, sleep=slept.append, max_attempts=2)
    assert out.ok and out.response == "ok" and out.attempts == 2
    assert len(slept) == 1 and 2 <= slept[0] <= extract.INTERACTIVE_MAX_SLEEP_S


@pytest.mark.parametrize("value", [None, "soon", "inf", "nan"])
def test_without_a_usable_retry_after_the_backoff_is_unchanged(value):
    fn, calls = scripted(busy(value, code=429), "ok")
    slept = []
    out = extract.call_with_deadline(fn, sleep=slept.append, max_attempts=2)
    assert out.ok and len(calls) == 2 and len(slept) == 1 and 1 <= slept[0] <= 1.25


@pytest.mark.parametrize("value", ["-5", "Wed, 21 Oct 2015 07:28:00 GMT"])
def test_a_past_or_negative_retry_after_retries_at_once_without_crashing(value):
    fn, calls = scripted(busy(value), "ok")
    slept = []
    out = extract.call_with_deadline(fn, sleep=slept.append, max_attempts=2)
    assert out.ok and len(calls) == 2 and 0 <= slept[0] <= 0.25


def test_the_wait_phrase_reads_naturally():
    assert [extract.wait_phrase(s) for s in (0.2, 1, 15, 59.5, 60, 61, 3600, 7200)] == [
        "about 1 second", "about 1 second", "about 15 seconds", "about a minute", "about a minute",
        "about 2 minutes", "about 60 minutes", "about 2 hours"]


def app(tmp_path, fn, **options):
    return TestClient(api.create_app(settings(tmp_path, use_caches=False, **options), store=object(),
                                     client_factory=lambda: model_client(fn)))


def test_check_reports_the_wait_and_settles_only_the_attempt_made(tmp_path):
    calls = []

    def overloaded(**_):
        calls.append(True)
        raise busy(60)

    c = app(tmp_path, overloaded)
    events = parse_sse(c.post("/api/check", json={"text": TEXT}).text)
    name, error = events[-1]
    assert name == "error" and error["retryable"] is True and error["retry_after_s"] == 60
    assert "asked NoCap to wait about a minute" in error["message"] and "1 of 1 passage was not checked" in error["message"]
    assert "offline overload" not in error["message"]  # the provider's own text never reaches the reader
    assert len(calls) == 1 and error["model_calls"] == 1 and error["cost_is_estimate"] is True
    # Two attempts were reserved; the one made settles at its own estimate, the unused retry is refunded.
    assert c.app.state.ledger.spent_today() == pytest.approx(error["cost_usd"], abs=2e-6)
    assert error["cost_usd"] == pytest.approx(.05)


def test_check_without_retry_after_keeps_the_generic_message(tmp_path):
    c = app(tmp_path, lambda **_: (_ for _ in ()).throw(busy(None, code=500)))
    name, error = parse_sse(c.post("/api/check", json={"text": TEXT}).text)[-1]
    assert name == "error" and error["retryable"] is True and "retry_after_s" not in error
    assert error["message"].startswith("The model didn't answer for 1 of 1 passage") and error["model_calls"] == 2


def test_fix_says_how_long_meta_asked_to_wait(tmp_path):
    c = app(tmp_path, lambda **_: (_ for _ in ()).throw(busy(90)))
    events = parse_sse(c.post("/api/fix", json={"text": TEXT}).text)
    name, error = events[-1]
    assert name == "error" and error["retryable"] is True
    assert "asked NoCap to wait about 2 minutes" in error["message"] and "draft is unchanged" in error["message"]


def test_a_known_response_is_still_used_after_a_short_wait(tmp_path, monkeypatch):
    outcomes = [busy(1), response()]

    def flaky(**_):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    c = TestClient(api.create_app(settings(tmp_path, use_caches=False), store=object(),
                                  client_factory=lambda: model_client(flaky)))
    slept, original = [], extract.call_with_deadline
    monkeypatch.setattr(extract, "call_with_deadline",
                        lambda fn, *, sleep, **kwargs: original(fn, sleep=slept.append, **kwargs))
    done = parse_sse(c.post("/api/check", json={"text": TEXT}).text)[-1]
    assert done[0] == "done" and done[1]["model_calls"] == 2 and len(slept) == 1 and 1 <= slept[0] <= 1.25
