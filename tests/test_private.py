"""Modal-only privacy routing, using real SDK serialization and fake HTTP only."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx2
import pytest

from receipts import api, extract, private, stream
from test_api import fake_verify, parse_sse, settings
from test_extract_backends import ChatAPI, CONTEXT, TEXT


def backend():
    return private.private_backend({'RECEIPTS_PRIVATE_MODE': '1',
                                    'RECEIPTS_MODAL_BASE_URL': 'https://operator--glimmer.modal.run/v1'})


def app_client(tmp_path, monkeypatch, *, factory=backend, wire=None, **opts):
    wire = wire or ChatAPI()
    monkeypatch.setattr(stream._verify, 'verify', fake_verify)
    # Isolated dependency injection; the production installer is exercised below.
    app = FastAPI()
    app.state.settings = settings(tmp_path, **opts)
    app.state.limiter = api.RateLimiter(app.state.settings.rate_limit_checks,
                                      app.state.settings.rate_limit_window_s)
    app.state.ledger = api._ledger(app.state.settings)
    app.include_router(private.create_private_router(settings=app.state.settings,
        limiter=app.state.limiter, ledger=app.state.ledger, get_store=object,
        backend_factory=factory, client_factory=wire.client))
    return TestClient(app), wire


def test_private_configuration_is_fixed_modal_not_generic_overrides():
    b = private.private_backend({'RECEIPTS_PRIVATE_MODE': 'true',
        'RECEIPTS_MODAL_BASE_URL': 'https://operator--glimmer.modal.run/v1/',
        'RECEIPTS_EXTRACT_BACKEND': 'meta', 'RECEIPTS_EXTRACT_BASE_URL': 'https://attacker.test/v1',
        'RECEIPTS_EXTRACT_MODEL': 'untrusted', 'RECEIPTS_EXTRACT_API_KEY_ENV': 'MODEL_API_KEY'})
    assert b.name == 'modal' and b.self_hosted and b.model == 'muse-glimmer'
    assert b.api_key_env == 'MODAL_PROXY_TOKEN'
    assert b.base_url == 'https://operator--glimmer.modal.run/v1'


@pytest.mark.parametrize('endpoint', ['', 'http://operator.modal.run/v1', 'https://inference.baseten.co/v1',
    'https://operator.us-east.modal.direct.attacker.test/v1', 'https://modal.direct/v1',
    'https://operator.unknown.modal.direct/v1', 'https://user:secret@operator.us-east.modal.direct/v1',
    'https://operator.us-east.modal.direct/v1?secret=x', 'https://operator.us-east.modal.direct:443/v1',
    'https://operator.modal.run.attacker.test/v1', 'https://user:secret@operator.modal.run/v1',
    'https://operator.modal.run/v1?secret=x', 'https://operator.modal.run/v1#secret',
    'https://operator.modal.run:443/v1', 'https://127.0.0.1/v1'])
def test_private_rejects_non_modal_or_unsafe_endpoints(endpoint):
    with pytest.raises(private.PrivateUnavailable):
        private.private_backend({'RECEIPTS_PRIVATE_MODE': '1', 'RECEIPTS_MODAL_BASE_URL': endpoint})


@pytest.mark.parametrize('region', ['us-east', 'us-west', 'ca-central', 'eu-west', 'ap-south', 'ap-southeast-2'])
def test_private_accepts_official_regional_server_hostname(region):
    endpoint = f'https://operator--glimmer.{region}.modal.direct/v1'
    backend = private.private_backend({'RECEIPTS_PRIVATE_MODE': '1', 'RECEIPTS_MODAL_BASE_URL': endpoint})
    assert backend.name == 'modal' and backend.base_url == endpoint


def test_unconfigured_private_mode_never_constructs_public_or_private_client(tmp_path, monkeypatch):
    def unavailable():
        raise private.PrivateUnavailable('SECRET setup details')
    c, wire = app_client(tmp_path, monkeypatch, factory=unavailable)
    r = c.post('/api/private/check', json={'text': TEXT})
    events = parse_sse(r.text)
    assert events[-1][0] == 'error' and events[-1][1]['model_calls'] == 0
    assert not wire.requests and 'SECRET' not in r.text
    assert 'no-store' in r.headers['cache-control']
    status = c.get('/api/private/status')
    assert status.json()['available'] is False and status.json()['offline'] is False


def test_private_request_routes_only_to_modal_never_reads_or_writes_response_cache(tmp_path, monkeypatch, caplog):
    c, wire = app_client(tmp_path, monkeypatch)
    monkeypatch.setattr(extract._Cache, 'get', lambda *_: pytest.fail('private cache read'))
    monkeypatch.setattr(stream.EventCache, 'get', lambda *_: pytest.fail('private stream cache read'))
    monkeypatch.setattr(stream.EventCache, 'put', lambda *_: pytest.fail('private stream cache write'))
    monkeypatch.setenv('RECEIPTS_EXTRACT_BACKEND', 'meta')
    for _ in range(2):
        response = c.post('/api/private/check', json={'text': TEXT, 'context': CONTEXT})
        events = parse_sse(response.text)
        assert events[0][0] == 'meta' and events[0][1]['private'] is True
        assert 'draft_hash' not in events[0][1] and events[0][1]['model'] == 'muse-glimmer'
        assert events[-1][0] == 'done' and events[-1][1]['model'] == 'muse-glimmer'
        assert events[-1][1]['model_calls'] == 2 and events[-1][1]['cost_is_estimate']
        assert events[-1][1]['coverage']['candidate_count'] == 3
    assert len(wire.requests) == 4
    assert all(r['url'] == backend().base_url + '/chat/completions' for r in wire.requests)
    assert all(r['body']['model'] == 'muse-glimmer' for r in wire.requests)
    assert not list(tmp_path.rglob('*.json'))
    assert 'Example Corp' not in caplog.text
    assert c.app.state.ledger.pending_count() == 0


@pytest.mark.parametrize('field', ['backend', 'base_url', 'model', 'example_id', 'verdicts'])
def test_client_cannot_select_destination_or_supply_verdicts(tmp_path, monkeypatch, field):
    c, wire = app_client(tmp_path, monkeypatch)
    result = parse_sse(c.post('/api/private/check', json={'text': TEXT, field: 'attacker'}).text)
    assert result[-1][0] == 'error' and not wire.requests


@pytest.mark.parametrize('options', [{'daily_budget_usd': 0}, {'require_shared_budget': True}])
def test_private_mode_requires_shared_spend_admission(tmp_path, monkeypatch, options):
    c, wire = app_client(tmp_path, monkeypatch, **options)
    events = parse_sse(c.post('/api/private/check', json={'text': TEXT}).text)
    assert events[-1][0] == 'error' and events[-1][1]['model_calls'] == 0
    assert not wire.requests


def test_private_response_format_retry_stays_modal_and_is_metered(tmp_path, monkeypatch):
    c, wire = app_client(tmp_path, monkeypatch, wire=ChatAPI(reject_response_format=True))
    events = parse_sse(c.post('/api/private/check', json={'text': TEXT, 'context': CONTEXT}).text)
    assert events[-1][0] == 'done' and events[-1][1]['model_calls'] == 4
    assert len(wire.requests) == 4 and all('.modal.run/v1/' in r['url'] for r in wire.requests)
    assert c.app.state.ledger.spent_today() > 0


def test_private_status_does_not_imply_live_endpoint_was_tested(tmp_path, monkeypatch):
    c, wire = app_client(tmp_path, monkeypatch)
    status = c.get('/api/private/status')
    assert status.json()['available'] is True and 'not guaranteed' in status.json()['message']
    assert not wire.requests and 'no-store' in status.headers['cache-control']


def test_production_app_mounts_private_routes_and_unconfigured_mode_fails_closed(tmp_path, monkeypatch):
    # Exercise create_app's actual installation, without replacing its router or factory.
    monkeypatch.setattr(extract, 'settings_env', lambda: {})
    monkeypatch.setattr(extract, 'make_client', lambda *a, **k: pytest.fail('unconfigured mode created a model client'))
    monkeypatch.setattr(private, 'make_private_client', lambda *a, **k: pytest.fail('unconfigured mode created a Modal client'))
    app = api.create_app(settings(tmp_path), store=object())
    paths = app.openapi()['paths']
    assert '/api/private/status' in paths and '/api/private/check' in paths
    with TestClient(app) as client:
        status = client.get('/api/private/status')
        response = client.post('/api/private/check', json={'text': TEXT})
    assert status.status_code == 200 and status.json()['available'] is False
    assert status.json()['model'] == 'muse-glimmer' and status.json()['offline'] is False
    events = parse_sse(response.text)
    assert len(events) == 1 and events[0][0] == 'error'
    assert events[0][1]['model_calls'] == 0 and 'No draft was sent' in events[0][1]['message']
    assert 'no-store' in status.headers['cache-control'] and 'no-store' in response.headers['cache-control']
    assert app.state.ledger.pending_count() == 0 and app.state.ledger.spent_today() == 0


@pytest.mark.parametrize('status', [307, 308])
def test_private_client_never_forwards_draft_on_redirect(monkeypatch, status):
    seen = []
    monkeypatch.setattr(extract, '_load_api_key', lambda _: 'test-proxy-not-a-real-secret')
    def redirect(request):
        seen.append(str(request.url))
        return httpx2.Response(status, headers={'Location': 'https://api.fireworks.ai/inference/v1/chat/completions'})
    client = private.make_private_client(backend(), timeout=30, transport=httpx2.MockTransport(redirect))
    try:
        with pytest.raises(Exception):
            client.chat.completions.create(model='muse-glimmer', messages=[{'role': 'user', 'content': 'PRIVATE DRAFT'}])
    finally:
        client.close()
    assert seen == [backend().base_url + '/chat/completions']
