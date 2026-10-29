"""Exercise the real streaming extractor through HTTP, using local mock transports only."""
import pytest
from fastapi.testclient import TestClient

from receipts import api, extract, stream
from test_api import fake_verify, parse_sse, settings
from test_extract_backends import ChatAPI, CONTEXT, TEXT


@pytest.mark.parametrize('name', ['fireworks', 'baseten', 'modal'])
def test_chat_backend_http_stream_and_cached_replay(name, tmp_path, monkeypatch):
    monkeypatch.setenv('RECEIPTS_EXTRACT_BACKEND', name)
    if name == 'modal':
        monkeypatch.setenv('RECEIPTS_MODAL_BASE_URL', 'https://private.example.test/v1')
    backend = extract.active_backend()
    wire = ChatAPI()
    monkeypatch.setattr(stream._verify, 'verify', fake_verify)
    app = api.create_app(settings(tmp_path), store=object(), client_factory=lambda: wire.client(backend))
    with TestClient(app) as client:
        payload = {'text': TEXT, 'context': CONTEXT}
        first = parse_sse(client.post('/api/check', json=payload).text)
        assert first[0][0] == 'meta' and first[-1][0] == 'done'
        assert first[-1][1]['model'] == backend.model
        assert len(wire.requests) == 2
        assert all(r['url'] == backend.base_url + '/chat/completions' for r in wire.requests)
        assert sum(name == 'claim' for name, _ in first) == 4
        second = parse_sse(client.post('/api/check', json=payload).text)
        assert second[0][1]['cached'] is True
        assert len(wire.requests) == 2


def test_invalid_backend_configuration_is_a_safe_sse_error(tmp_path, monkeypatch):
    monkeypatch.setenv('RECEIPTS_EXTRACT_BACKEND', 'misspelled-provider')
    app = api.create_app(settings(tmp_path), store=object(),
                         client_factory=lambda: pytest.fail('invalid config reached the model'))
    with TestClient(app) as client:
        response = client.post('/api/check', json={'text': TEXT})
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events == [('error', {'message': stream.MSG_NO_MODEL, 'retryable': False,
                                'cost_usd': 0.0, 'cost_is_estimate': False, 'model_calls': 0})]
