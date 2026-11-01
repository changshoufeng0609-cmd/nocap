"""Provider dispatch and API accounting boundaries; all responses are local fakes."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from receipts import api, extract, stream
from test_api import settings, parse_sse
from test_spend_usage import client as model_client, response, TOKENS
from test_slides import image_url

TEXT = "NVIDIA revenue was $96.2 billion."


def app_client(tmp_path, fn, **options):
    return TestClient(api.create_app(settings(tmp_path, **options), store=object(),
        client_factory=lambda: model_client(fn)))


def check(client, text=TEXT):
    return parse_sse(client.post('/api/check', json={'text': text}).text)


def test_unknown_usage_is_labeled_and_settles_at_the_attempts_own_estimate(tmp_path):
    # The one attempt reached the request's accounting, so unknown usage settles at that attempt's conservative
    # estimate ($0.05 floor), not at the whole $0.10 precharge (2 attempts admitted, 1 made).
    c = app_client(tmp_path, lambda **_: response(), daily_budget_usd=.15, use_caches=False)
    result = check(c)[-1]
    assert result[0] == 'done' and result[1]['cost_is_estimate'] is True
    assert result[1]['cost_usd'] == pytest.approx(.05) and result[1]['model_calls'] == 1
    assert c.app.state.ledger.spent_today() == pytest.approx(.05, abs=1e-6)
    assert c.app.state.ledger.pending_count() == 0
    assert check(c)[-1][0] == 'done'  # the unused half of the precharge came back, so a second check fits
    refused = check(c)[-1]
    assert refused[0] == 'error' and refused[1]['reason'] == 'daily_budget'  # a third reservation does not
    assert c.app.state.ledger.spent_today() == pytest.approx(.1, abs=1e-6)


def test_two_app_instances_share_one_local_budget(tmp_path):
    calls = []
    def provider(**_):
        calls.append(True)
        return response()
    # $0.12 holds one $0.10 precharge, and still refuses a second after the first settles at $0.05.
    clients = [app_client(tmp_path, provider, daily_budget_usd=.12, use_caches=False) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(check, clients))
    assert sorted(events[-1][0] for events in outcomes) == ['done', 'error']
    assert len(calls) == 1
    assert clients[0].app.state.ledger.spent_today() == pytest.approx(.05, abs=1e-6)
    assert clients[1].app.state.ledger.spent_today() == pytest.approx(.05, abs=1e-6)


def test_deadline_keeps_estimate_and_late_usage_does_not_refund(tmp_path):
    release = threading.Event()
    finished = threading.Event()
    def provider(**_):
        release.wait(2)
        finished.set()
        return response(NS(**TOKENS))
    c = app_client(tmp_path, provider, check_deadline_s=.05, use_caches=False)
    try:
        result = check(c)[-1]
        assert result[0] == 'error' and result[1]['cost_is_estimate']
        assert result[1]['model_calls'] == 1 and result[1]['cost_usd'] > 0
        # The abandoned attempt was accounted at dispatch, so it settles at its own conservative estimate.
        assert c.app.state.ledger.spent_today() == pytest.approx(.05, abs=1e-6)
        assert c.app.state.ledger.spent_today() == pytest.approx(result[1]['cost_usd'], abs=1e-6)
    finally:
        release.set()
    assert finished.wait(2)
    # The late, much cheaper usage never refunds that settled estimate.
    assert c.app.state.ledger.spent_today() == pytest.approx(.05, abs=1e-6)


def test_shared_required_blocks_all_paid_routes_but_preserves_cached_example(tmp_path):
    calls = []
    def provider(**_):
        calls.append(True)
        return response(NS(**TOKENS))
    local = app_client(tmp_path, provider)
    assert check(local)[-1][0] == 'done'
    required = app_client(tmp_path, provider, require_shared_budget=True)
    cached = check(required)
    assert cached[0][1]['cached'] is True and cached[-1][0] == 'done'
    for endpoint, body in [('/api/check', {'text': 'Different draft: $1.'}),
                           ('/api/fix', {'text': TEXT}),
                           ('/api/slide', {'image_data_url': image_url()})]:
        events = parse_sse(required.post(endpoint, json=body).text)
        assert events[-1][0] == 'error'
        assert 'ledger' in events[-1][1]['message']
        assert events[-1][1]['model_calls'] == 0
    assert len(calls) == 1


def test_vercel_defaults_to_shared_required_and_secret_is_not_in_repr():
    cfg = api.Settings.from_env({'VERCEL': '1', 'RECEIPTS_SPEND_DATABASE_URL': 'postgresql://u:PRIVATE@db/nocap'})
    assert cfg.require_shared_budget and 'PRIVATE' not in repr(cfg)
    assert not api.Settings.from_env({}).require_shared_budget


def test_malformed_shared_configuration_never_falls_back(tmp_path):
    c = app_client(tmp_path, lambda **_: pytest.fail('provider must not run'),
                   spend_database_url='https://user:PRIVATE@invalid/db')
    result = check(c)[-1]
    assert result[0] == 'error' and 'PRIVATE' not in str(result)
    assert 'ledger' in result[1]['message']


def test_cache_preflight_race_cannot_start_unreserved_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, 'passages_to_call', lambda *_, **__: 0)
    c = app_client(tmp_path, lambda **_: pytest.fail('unreserved provider must not run'))
    result = check(c)[-1]
    assert result[0] == 'error' and result[1]['cost_usd'] == 0
    assert result[1]['model_calls'] == 0
    assert c.app.state.ledger.spent_today() == 0


def test_worker_start_failure_releases_unused_precharge(tmp_path, monkeypatch):
    start = threading.Thread.start
    def fail_worker(thread):
        if thread.name == 'receipts-check':
            raise RuntimeError('PRIVATE startup details')
        return start(thread)
    monkeypatch.setattr(threading.Thread, 'start', fail_worker)
    c = app_client(tmp_path, lambda **_: pytest.fail('provider must not run'))
    result = check(c)[-1]
    assert result[0] == 'error' and result[1]['cost_usd'] == 0 and result[1]['model_calls'] == 0
    assert 'PRIVATE' not in str(result)
    assert c.app.state.ledger.spent_today() == 0 and c.app.state.ledger.pending_count() == 0


def test_unexpected_worker_exception_cannot_report_an_admitted_call_as_free(tmp_path, monkeypatch):
    def broken(*_, **kwargs):
        kwargs['before_model_call'](.05)
        raise RuntimeError('PRIVATE response')
        yield
    monkeypatch.setattr(stream, 'check_events', broken)
    c = app_client(tmp_path, lambda **_: object(), use_caches=False)
    result = check(c)[-1]
    assert result[0] == 'error' and result[1]['cost_is_estimate']
    assert result[1]['cost_usd'] == pytest.approx(.1) and result[1]['model_calls'] == 1
    assert c.app.state.ledger.spent_today() == pytest.approx(.1)
    assert 'PRIVATE' not in str(result)


def test_operation_refuses_attempt_beyond_prepaid_amount(tmp_path):
    ledger = api.LocalLedger(.1, tmp_path / 'spend.sqlite3')
    token = ledger.reserve(.1)
    operation = api.OperationBudget(ledger, token, .1)
    operation.before_call(.05)
    operation.before_call(.05)
    with pytest.raises(extract.ModelCallRejected):
        operation.before_call(.001)
    assert operation.started_calls == 2


def test_settlement_permanently_closes_dispatch_gate(tmp_path):
    ledger = api.LocalLedger(.1, tmp_path / 'spend.sqlite3')
    operation = api.OperationBudget(ledger, ledger.reserve(.1), .1)
    operation.finish(stream.CheckStats())
    assert ledger.spent_today() == 0
    with pytest.raises(extract.ModelCallRejected):
        operation.before_call(.05)
