"""Operator live-window admission and late dispatch, with local fake providers only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from receipts import api, extract, private, stream
from test_api import parse_sse, settings
from test_ask import AskStore
from test_grounding import filing_fixture
from test_slides import image_url
from test_spend_usage import META, TOKENS, client as model_client, response


UNTIL = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)
TEXT = "NVIDIA revenue was $96.2 billion."


def forbidden(*args, **kwargs):
    pytest.fail("Paused requests must not create a provider client or admit ledger work")


def test_unconfigured_gate_keeps_the_existing_ledger(tmp_path):
    config = settings(tmp_path)
    assert config.live_requests_enabled and config.live_until is None
    assert isinstance(api._ledger(config), api.LocalLedger)
    assert not api._live_requests_paused(config)


@pytest.mark.parametrize("value,enabled", [
    ("true", True), (" TRUE ", True), ("false", False), ("", False),
    ("1", False), ("yes", False), ("truthy", False),
])
def test_environment_requires_explicit_true_when_flag_is_present(value, enabled):
    assert api.Settings.from_env({"RECEIPTS_LIVE_REQUESTS_ENABLED": value}).live_requests_enabled is enabled
    assert api.Settings.from_env({}).live_requests_enabled is True


@pytest.mark.parametrize("value", ["2026-11-05T02:00:00Z", "2026-11-05T02:00:00+00:00"])
def test_utc_deadline_pauses_at_the_boundary(value, monkeypatch):
    config = api.Settings.from_env({"RECEIPTS_LIVE_UNTIL": value})
    monkeypatch.setattr(api, "_utc_now", lambda: UNTIL - timedelta(microseconds=1))
    assert config.live_until == UNTIL and not api._live_requests_paused(config)
    monkeypatch.setattr(api, "_utc_now", lambda: UNTIL)
    assert api._live_requests_paused(config)


@pytest.mark.parametrize("value", [
    "", "tomorrow", "2026-11-05", "2026-11-05T02:00:00", "2026-11-05T10:00:00+08:00",
    "2026-02-30T02:00:00Z", "2026-11-05T25:00:00Z", "2026-11-05T02:00:00Z trailing",
])
def test_malformed_cutoff_cannot_silently_enable_live_calls(value, tmp_path, monkeypatch):
    config = api.Settings.from_env({"RECEIPTS_LIVE_REQUESTS_ENABLED": "true", "RECEIPTS_LIVE_UNTIL": value})
    config = replace(config, data_dir=tmp_path, api_cache_dir=tmp_path / "api", use_caches=False)
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    app = api.create_app(config, store=object(), client_factory=forbidden)
    result = parse_sse(TestClient(app).post("/api/check", json={"text": TEXT}).text)
    assert result[0][0] == "error" and result[0][1]["message"] == api.LIVE_PAUSED_MESSAGE
    assert result[0][1]["model_calls"] == 0 and result[0][1]["cost_usd"] == 0


@pytest.mark.parametrize("configuration", [{"live_requests_enabled": False}, {"live_until": UNTIL}])
def test_pause_blocks_all_six_paid_routes_before_provider_and_ledger_admission(tmp_path, monkeypatch, configuration):
    monkeypatch.setattr(api, "_utc_now", lambda: UNTIL)
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    monkeypatch.setattr(extract, "make_client", forbidden)
    monkeypatch.setattr(private, "make_private_client", forbidden)
    monkeypatch.setattr(extract, "settings_env", lambda: {
        "RECEIPTS_PRIVATE_MODE": "true", "RECEIPTS_MODAL_BASE_URL": "https://operator--glimmer.modal.run/v1"})
    monkeypatch.setattr(extract, "_load_api_key", lambda *_: "offline-test-not-a-real-credential")
    config = api.Settings(data_dir=tmp_path, api_cache_dir=tmp_path / "api", use_caches=False,
                          rate_limit_checks=20, **configuration)
    filing = filing_fixture(tmp_path / "filing_texts")
    app = api.create_app(config, store=AskStore(), client_factory=forbidden,
                         wonder_client_factory=forbidden, slide_client_factory=forbidden)
    routes = [
        ("/api/check", {"text": TEXT}), ("/api/fix", {"text": TEXT}),
        ("/api/slide", {"image_data_url": image_url()}),
        ("/api/ask", {"question": "NVIDIA GAAP revenue Q2 FY2027?"}),
        ("/api/grounding", {"question": "What risks?", "filing_id": filing["id"]}),
        ("/api/private/check", {"text": TEXT}),
    ]
    for route, body in routes:
        result = parse_sse(TestClient(app).post(route, json=body).text)
        assert len(result) == 1 and result[0][0] == "error", (route, result)
        assert result[0][1]["message"] == api.LIVE_PAUSED_MESSAGE, (route, result)
        assert result[0][1]["model_calls"] == result[0][1]["cost_usd"] == 0
    assert app.state.ledger.spent_today() == 0 and app.state.ledger.pending_count() == 0


def test_live_request_before_deadline_keeps_normal_usage_settlement(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_utc_now", lambda: UNTIL - timedelta(seconds=1))
    calls = []
    def provider(**_):
        calls.append(True)
        return response(NS(**TOKENS))
    config = settings(tmp_path, live_until=UNTIL, use_caches=False)
    app = api.create_app(config, store=object(), client_factory=lambda: model_client(provider))
    result = parse_sse(TestClient(app).post("/api/check", json={"text": TEXT}).text)
    assert result[-1][0] == "done" and result[-1][1]["model_calls"] == len(calls) == 1
    assert app.state.ledger.spent_today() == pytest.approx(result[-1][1]["cost_usd"], abs=1e-6)
    assert app.state.ledger.pending_count() == 0


def test_pause_keeps_recorded_example_without_reaching_provider_or_ledger(tmp_path, monkeypatch):
    config = settings(tmp_path)
    live = api.create_app(config, store=object(), client_factory=lambda: model_client(lambda **_: response(NS(**TOKENS))))
    original = parse_sse(TestClient(live).post("/api/check", json={"text": TEXT}).text)
    assert original[-1][0] == "done"
    original_charge = live.state.ledger.spent_today()
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    paused = api.create_app(replace(config, live_requests_enabled=False), store=object(), client_factory=forbidden)
    replay = parse_sse(TestClient(paused).post("/api/check", json={"text": TEXT}).text)
    assert replay[0][1]["cached"] is True and replay[1:] == original[1:]
    assert paused.state.ledger.spent_today() == original_charge and paused.state.ledger.pending_count() == 0


def test_pause_keeps_read_only_extraction_cache_and_new_cutoff_verification(tmp_path, monkeypatch):
    config = settings(tmp_path, live_requests_enabled=False)
    config.spark_cache_dir.mkdir()
    for key in extract.request_keys(TEXT, None):
        (config.spark_cache_dir / f"{key}.json").write_text(json.dumps({"output_text": '{"claims": []}'}))
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    monkeypatch.setattr(extract, "make_client", forbidden)
    app = api.create_app(config, store=object(), client_factory=forbidden)
    result = parse_sse(TestClient(app).post("/api/check", json={"text": TEXT, "as_of": "2026-01-01T00:00:00Z"}).text)
    assert result[0][1]["cached"] is False and result[-1][0] == "done"
    assert result[-1][1]["model_calls"] == result[-1][1]["cost_usd"] == 0
    assert app.state.ledger.spent_today() == 0


@pytest.mark.parametrize("known_usage", [True, False])
def test_expiry_blocks_queued_dispatch_but_settles_inflight_work_without_reset(tmp_path, monkeypatch, known_usage):
    now = [UNTIL - timedelta(seconds=1)]
    monkeypatch.setattr(api, "_utc_now", lambda: now[0])
    config = settings(tmp_path, live_until=UNTIL, daily_budget_usd=1, spend_namespace="fixed-shared")
    ledger = api._ledger(config)
    operation = api.OperationBudget(ledger, ledger.reserve(.2), .2)
    calls = []
    def provider():
        calls.append(True)
        now[0] = UNTIL  # The already admitted attempt finishes after the live window closes.
        return response(NS(**TOKENS) if known_usage else None)
    request = extract.build_request(TEXT, "", backend=META)
    first = extract.RequestUsage(META, before_call=operation.before_call)
    first.call(provider, request)
    queued = extract.RequestUsage(META, before_call=operation.before_call)
    with pytest.raises(extract.ModelCallRejected, match="paused"):
        queued.call(provider, request)
    assert calls == [True] and queued.finish()["model_calls"] == 0
    with pytest.raises(api.Rejected) as rejected:
        ledger.reserve(.1)
    assert rejected.value.reason == "live_paused"
    record = first.finish()
    stats = stream.CheckStats(cost_usd=record["cost_usd"], model_calls=record["model_calls"],
                              estimated_calls=record["estimated_calls"])
    operation.finish(stats)
    expected = record["cost_usd"] if known_usage else .2
    assert ledger.spent_today() == pytest.approx(expected, abs=1e-6) and ledger.pending_count() == 0
    reopened = api._ledger(replace(config, live_until=None))
    assert reopened.cap_usd == 1 and reopened.namespace == "fixed-shared"
    assert reopened.spent_today() == pytest.approx(expected, abs=1e-6)


def test_health_reports_policy_without_claiming_model_availability(tmp_path, monkeypatch):
    now = [UNTIL - timedelta(seconds=1)]
    monkeypatch.setattr(api, "_utc_now", lambda: now[0])
    info = {"ok": True, "data_snapshot": "2026-10-29", "companies": ["NVDA"], "facts": 1}
    monkeypatch.setattr(api, "data_info", lambda *_: info)
    app = api.create_app(settings(tmp_path, live_until=UNTIL), store=object(), client_factory=forbidden)
    c = TestClient(app)
    assert c.get("/api/health").json() == {**info, "live_requests_paused": False}
    now[0] = UNTIL
    assert c.get("/api/health").json() == {**info, "live_requests_paused": True}
