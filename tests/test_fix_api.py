"""Fix API trust boundary, shared admission guards, and cost settlement. No live model calls."""
import json

import pytest
from fastapi.testclient import TestClient

from receipts import api, extract, rewrite


TEXT = "Example Corp revenue was $12 billion."


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
            for block in response.text.split("\n\n") if block.startswith("event:")]


def make_client(tmp_path, **overrides):
    settings = api.Settings(data_dir=tmp_path, api_cache_dir=tmp_path / "api", **overrides)
    return TestClient(api.create_app(settings, store=object()))


@pytest.fixture
def fake_fix(monkeypatch):
    calls = []

    def run(text, **kwargs):
        calls.append((text, kwargs))
        kwargs["stats"].cost_usd = 0.023
        kwargs["stats"].model_calls = 2
        yield "progress", {"stage": "checking"}
        kwargs["stats"].complete = True
        yield "result", {"original_text": text, "rewritten_text": text, "status": "unchanged"}

    monkeypatch.setattr(rewrite, "fix_events", run)
    return calls


def test_fix_receives_canonical_inputs_and_settles_usage(tmp_path, fake_fix):
    c = make_client(tmp_path)
    result = events(c.post("/api/fix", json={"text": TEXT, "context": "  Example Corp Q3 2025 ",
                                           "as_of": "2025-11-01T04:00:00+08:00"}))
    assert [name for name, _ in result] == ["progress", "result"]
    text, kwargs = fake_fix[0]
    assert text == TEXT
    assert kwargs["context"] == "Example Corp Q3 2025"
    assert kwargs["as_of"] == "2025-10-31T20:00:00Z"
    assert kwargs["deadline"] is not None
    assert kwargs["extract_client_factory"] and kwargs["wonder_client_factory"]
    assert c.app.state.ledger.spent_today() == pytest.approx(0.023)
    assert c.app.state.ledger.pending_count() == 0


@pytest.mark.parametrize("body", [
    {"text": ""}, {"text": "x" * 12001}, {"text": TEXT, "as_of": "tomorrow"},
    {"text": TEXT, "context": "x" * 301}, {"text": TEXT, "verdicts": []},
    {"text": TEXT, "evidence": []}, {"text": TEXT, "example_id": "trusted"},
    {"text": TEXT, "edits": [{"start": 0, "end": 1}]},
])
def test_fix_rejects_invalid_inputs_and_client_authority(tmp_path, fake_fix, body):
    c = make_client(tmp_path)
    result = events(c.post("/api/fix", json=body))
    assert [name for name, _ in result] == ["error"]
    assert result[0][1]["retryable"] is False
    assert not fake_fix and c.app.state.ledger.pending_count() == 0


def test_fix_rejects_oversized_json_and_malformed_body(tmp_path, fake_fix):
    c = make_client(tmp_path)
    for body in ('{"text":', '"' + "x" * 66000 + '"'):
        assert events(c.post("/api/fix", content=body))[0][0] == "error"
    assert not fake_fix


def test_fix_and_check_share_rate_limiter(tmp_path, fake_fix):
    c = make_client(tmp_path, rate_limit_checks=1)
    assert events(c.post("/api/fix", json={"text": TEXT}))[-1][0] == "result"
    error = events(c.post("/api/fix", json={"text": TEXT}))[0]
    assert error[0] == "error" and error[1]["retryable"] is True
    assert "Too many" in error[1]["message"]
    assert c.app.state.limiter.acquire("testclient") is not None
    assert len(fake_fix) == 1


def test_fix_reservation_blocks_work_when_daily_cap_cannot_fit(tmp_path, fake_fix):
    c = make_client(tmp_path, daily_budget_usd=0.34)
    error = events(c.post("/api/fix", json={"text": TEXT}))[0]
    assert error[0] == "error" and "budget" in error[1]["message"]
    assert not fake_fix and c.app.state.ledger.pending_count() == 0


def test_fix_accounts_for_both_extractions_on_many_passages(tmp_path, fake_fix):
    c = make_client(tmp_path, daily_budget_usd=0.50)
    draft = "\n\n".join([TEXT] * 4)
    assert events(c.post("/api/fix", json={"text": draft}))[0][0] == "error"
    assert not fake_fix


@pytest.mark.parametrize("crash", [False, True])
def test_fix_failure_settles_usage_and_never_leaks_error_details(tmp_path, monkeypatch, crash):
    secret = "PRIVATE_DRAFT_OR_PROVIDER_SECRET"

    def fail(text, **kwargs):
        kwargs["stats"].cost_usd = 0.017
        yield "progress", {"stage": "rewriting"}
        if crash:
            raise RuntimeError(secret)
        yield "error", {"message": "The correction could not be verified.", "retryable": True}

    monkeypatch.setattr(rewrite, "fix_events", fail)
    c = make_client(tmp_path)
    response = c.post("/api/fix", json={"text": TEXT})
    assert events(response)[-1][0] == "error"
    assert secret not in response.text
    assert c.app.state.ledger.spent_today() == pytest.approx(0.017)
    assert c.app.state.ledger.pending_count() == 0


def test_wonder_client_uses_standard_meta_separate_from_extraction(tmp_path, monkeypatch):
    made = []

    def client_factory(**kwargs):
        made.append(kwargs)
        return object()

    def run(text, **kwargs):
        kwargs["wonder_client_factory"]()
        kwargs["wonder_client_factory"]()  # reused; no second factory call
        yield "error", {"message": "Synthetic stop", "retryable": False}

    monkeypatch.setattr(extract, "make_client", client_factory)
    monkeypatch.setattr(rewrite, "fix_events", run)
    c = make_client(tmp_path)
    events(c.post("/api/fix", json={"text": TEXT}))
    assert len(made) == 1
    assert made[0]["backend"].name == "meta"
    assert made[0]["backend"].model == "muse-wonder-1.0"
    assert made[0]["backend"].base_url == "https://api.meta.ai/v1"
