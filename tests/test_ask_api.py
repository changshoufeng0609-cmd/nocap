"""Ask/quote API shares admission and settlement with Check/Fix/Slide; no network calls."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from receipts import api, ask_api
from test_ask import AskStore, ProposalClient
from test_grounding import QUOTE, filing_fixture


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
            for block in response.text.split("\n\n") if block.startswith("event:")]


def client(tmp_path, model=None, **overrides):
    settings = api.Settings(data_dir=tmp_path, api_cache_dir=tmp_path / "api", **overrides)
    ledger, limiter = api._ledger(settings), api.RateLimiter(settings.rate_limit_checks, settings.rate_limit_window_s)
    model = model or ProposalClient()
    created = []
    def factory():
        created.append(True)
        return model
    app = FastAPI()
    app.include_router(ask_api.create_router(settings=settings, get_store=AskStore,
                                            get_client=factory, limiter=limiter, ledger=ledger))
    return TestClient(app), ledger, limiter, model, created


def test_question_end_to_end_verification_and_usage_settlement(tmp_path):
    c, ledger, _, model, _ = client(tmp_path)
    rows = events(c.post("/api/ask", json={"question": "NVIDIA GAAP revenue Q2 FY2027?",
                                          "as_of": "2026-08-27T04:36:00+08:00"}))
    assert [r[0] for r in rows] == ["progress", "progress", "result"]
    result = rows[-1][1]
    assert result["as_of"] == "2026-08-26T20:36:00Z"
    assert "$96221000000" in result["answer"] and result["items"][0]["verdict"]["status"] == "verified"
    assert result["model_calls"] == len(model.calls) == 1
    assert ledger.spent_today() == pytest.approx(result["cost_usd"], abs=1e-6)
    assert ledger.pending_count() == 0


def test_options_follow_store_companies_and_no_model_call(tmp_path):
    c, _, _, model, created = client(tmp_path)
    r = c.get("/api/ask/options")
    assert r.status_code == 200 and r.json()["companies"][0]["ticker"] == "NVDA"
    assert not model.calls and not created


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "x" * 1501},
    {"question": "x", "as_of": "tomorrow"}, {"question": "x", "answer": "trusted"},
    {"question": "x", "model": "muse-spark-1.3-contributor"},
    {"question": "x", "url": "http://localhost/private"}])
def test_invalid_question_cannot_add_authority_or_dispatch(tmp_path, body):
    c, ledger, _, model, created = client(tmp_path)
    r = events(c.post("/api/ask", json=body))
    assert r[0][0] == "error" and r[0][1]["model_calls"] == 0
    assert not model.calls and not created and ledger.pending_count() == 0


def test_large_stream_is_rejected_without_admission(tmp_path):
    c, ledger, _, model, _ = client(tmp_path)
    assert events(c.post("/api/ask", content='{"question":"' + 'x' * 17000 + '"}'))[0][0] == "error"
    assert not model.calls and ledger.pending_count() == 0


@pytest.mark.parametrize("config", [{"daily_budget_usd": 0}, {"require_shared_budget": True}])
def test_missing_ledger_or_budget_stops_before_model_client(tmp_path, config):
    c, _, _, model, created = client(tmp_path, **config)
    result = events(c.post("/api/ask", json={"question": "NVIDIA GAAP revenue Q2 FY2027?"}))
    assert result[0][0] == "error" and not model.calls and not created


def test_ask_and_grounding_use_same_rate_limiter(tmp_path):
    row = filing_fixture(tmp_path / "filing_texts")
    c, ledger, limiter, model, _ = client(tmp_path, rate_limit_checks=1)
    assert events(c.post("/api/ask", json={"question": "NVIDIA GAAP revenue Q2 FY2027?"}))[-1][0] == "result"
    r = events(c.post("/api/grounding", json={"question": "What risks?", "filing_id": row["id"]}))
    assert r[0][0] == "error" and "Too many" in r[0][1]["message"]
    assert len(model.calls) == 1 and ledger.pending_count() == 0
    assert limiter.acquire("testclient") is not None


def test_grounding_whole_filing_result_and_listing(tmp_path):
    row = filing_fixture(tmp_path / "filing_texts")
    c, ledger, _, model, _ = client(tmp_path, ProposalClient({"quotes": [QUOTE]}))
    listed = c.get("/api/grounding/filings").json()["filings"]
    assert listed == [row] and not model.calls
    result = events(c.post("/api/grounding", json={"question": "What risks?", "filing_id": row["id"]}))[-1][1]
    assert result["quotes"][0]["text"] == QUOTE and result["whole_document"] is True
    assert ledger.spent_today() > 0 and ledger.pending_count() == 0


def test_future_filing_or_arbitrary_url_is_not_sent(tmp_path):
    row = filing_fixture(tmp_path / "filing_texts")
    c, ledger, _, model, created = client(tmp_path)
    for body in ({"question": "What risks?", "filing_id": row["id"], "as_of": "2026-08-26T00:00:00Z"},
                 {"question": "What risks?", "filing_id": "https://evil.example/"}):
        result = events(c.post("/api/grounding", json=body))
        assert result[0][0] == "error"
    assert not model.calls and not created and ledger.pending_count() == 0


def test_provider_failure_retains_estimate_and_redacts_details(tmp_path):
    c, ledger, _, model, _ = client(tmp_path, ProposalClient(error="PRIVATE_PROVIDER_SECRET"))
    response = c.post("/api/ask", json={"question": "NVIDIA GAAP revenue Q2 FY2027?"})
    result = events(response)[-1][1]
    assert result["model_calls"] == 1 and result["cost_is_estimate"]
    assert result["cost_usd"] >= .05 and "PRIVATE_PROVIDER_SECRET" not in response.text
    assert ledger.spent_today() >= .05 and ledger.pending_count() == 0


def test_model_omitted_qualifier_cannot_publish_a_verified_total(tmp_path):
    c, ledger, _, model, _ = client(tmp_path)
    rows = events(c.post("/api/ask", json={
        "question": "NVIDIA GAAP revenue in Q2 FY2027 (excluding China)?",
    }))
    assert rows[-1][0] == "error" and not any(name == "result" for name, _ in rows)
    assert rows[-1][1]["model_calls"] == len(model.calls) == 1
    assert ledger.spent_today() > 0 and ledger.pending_count() == 0
