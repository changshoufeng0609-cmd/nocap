"""Review runtime regressions: bounded bodies, trusted identity, admission and retention.

All provider work is forbidden or replaced by local test doubles. No credentials,
network, held-out evaluation or existing cache mutation is needed.
"""
import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS

from fastapi import Request
from fastapi.testclient import TestClient
import pytest

from receipts import api, extract, stream
from receipts.spend import BudgetUnavailable
from test_api import parse_sse, settings
from test_ask import AskStore
from test_grounding import filing_fixture
from test_slides import image_url
from test_spend_usage import TOKENS, client as model_client, response


TEXT = "NVIDIA revenue was $96.2 billion."


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected provider or admission call")


def request(path="/api/check", *, peer="198.51.100.20", headers=None, chunks=()):
    consumed = []
    parts = iter(chunks)

    async def receive():
        chunk = next(parts)
        consumed.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    scope = {"type": "http", "method": "POST", "path": path, "client": (peer, 30000),
             "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    return Request(scope, receive), consumed


def endpoint(app, path):
    def routes(router):
        for route in router.routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from routes(included)
            else:
                yield route
    return next(route.endpoint for route in routes(app) if getattr(route, "path", None) == path)


async def output(response):
    return parse_sse("".join([part async for part in response.body_iterator]))


@pytest.mark.parametrize("path", ["/api/check", "/api/fix", "/api/private/check"])
def test_oversize_body_stops_before_reading_remaining_chunks(tmp_path, path):
    app = api.create_app(settings(tmp_path, max_body_bytes=32), store=object(), client_factory=forbidden)
    req, consumed = request(path, headers={"content-length": "1"},
                            chunks=[b"a" * 20, b"b" * 20, b"c" * 1_000_000])

    async def run():
        events = await output(await endpoint(app, path)(req))
        assert events[0][0] == "error" and "too large" in events[0][1]["message"]
        assert events[0][1]["model_calls"] == 0

    asyncio.run(run())
    assert len(consumed) == 2 and not hasattr(req, "_body")
    assert app.state.ledger.pending_count() == 0 and not app.state.limiter._hits


@pytest.mark.parametrize("headers", [
    {"x-real-ip": "203.0.113.1"}, {"x-forwarded-for": "203.0.113.2"},
    {"x-vercel-forwarded-for": "203.0.113.3"},
])
def test_direct_socket_ignores_all_forwarded_identity_headers(tmp_path, headers):
    req, _ = request(headers=headers)
    assert api.client_ip(req, settings(tmp_path)) == "198.51.100.20"
    assert api.client_ip(req) == "198.51.100.20"


@pytest.mark.parametrize("peer,xff,expected", [
    ("10.0.0.2", "192.0.2.99, 198.51.100.20", "198.51.100.20"),
    ("10.0.0.2", "192.0.2.99, 198.51.100.20, 10.0.0.3", "198.51.100.20"),
    ("198.51.100.20", "192.0.2.99", "198.51.100.20"),
    ("10.0.0.2", "bad-address", "10.0.0.2"),
    ("10.0.0.2", "192.0.2.99, bad-address", "10.0.0.2"),
    ("10.0.0.2", "", "10.0.0.2"),
    ("::1", "2001:db8::1", "2001:db8::1"),
])
def test_trusted_proxy_walk_stops_at_first_untrusted_hop(tmp_path, peer, xff, expected):
    config = settings(tmp_path, trusted_proxy_cidrs=("10.0.0.0/8", "::1/128"))
    req, _ = request(peer=peer, headers={"x-forwarded-for": xff, "x-real-ip": "192.0.2.100"})
    assert api.client_ip(req, config) == expected


@pytest.mark.parametrize("value,expected", [
    ("203.0.113.5", "203.0.113.5"), ("2001:db8::1", "2001:db8::1"),
    ("", "198.51.100.20"), ("not-an-ip", "198.51.100.20"),
    ("203.0.113.5, 192.0.2.1", "198.51.100.20"),
])
def test_vercel_only_uses_valid_single_platform_owned_header(value, expected):
    config = api.Settings.from_env({"VERCEL": "1"})
    req, _ = request(headers={"x-vercel-forwarded-for": value, "x-forwarded-for": "192.0.2.99",
                              "x-real-ip": "192.0.2.100"})
    assert api.client_ip(req, config) == expected


def test_malformed_proxy_configuration_cannot_create_trust():
    config = api.Settings.from_env({"RECEIPTS_TRUSTED_PROXY_CIDRS": "10.0.0.0/8, invalid"})
    assert config.trusted_proxy_cidrs == () and config.vercel_proxy is False


def test_rotating_spoofed_headers_cannot_obtain_a_new_allowance(tmp_path, monkeypatch):
    monkeypatch.setattr(api.LocalLedger, "reserve", lambda *args: None)
    app = api.create_app(settings(tmp_path, rate_limit_checks=1, use_caches=False),
                         store=object(), client_factory=object)
    client = TestClient(app, client=("198.51.100.20", 30000))
    first = parse_sse(client.post("/api/check", json={"text": TEXT},
                                 headers={"x-real-ip": "192.0.2.1"}).text)
    second = parse_sse(client.post("/api/check", json={"text": TEXT},
                                  headers={"x-real-ip": "192.0.2.2", "x-forwarded-for": "192.0.2.2"}).text)
    assert "budget" in first[0][1]["message"] and "Too many" in second[0][1]["message"]
    assert list(app.state.limiter._hits) == ["198.51.100.20"]


def paid_routes(tmp_path):
    filing = filing_fixture(tmp_path / "filing_texts")
    return [
        ("/api/check", {"text": TEXT}), ("/api/fix", {"text": TEXT}),
        ("/api/slide", {"image_data_url": image_url()}),
        ("/api/ask", {"question": "NVIDIA GAAP revenue Q2 FY2027?"}),
        ("/api/grounding", {"question": "What risks?", "filing_id": filing["id"]}),
        ("/api/private/check", {"text": TEXT}),
    ]


def test_paused_requests_never_consume_rate_allowance_on_any_paid_route(tmp_path, monkeypatch):
    monkeypatch.setattr(api.LocalLedger, "reserve", forbidden)
    app = api.create_app(settings(tmp_path, rate_limit_checks=1, live_requests_enabled=False,
                                  use_caches=False), store=AskStore(), client_factory=forbidden)
    with TestClient(app) as client:
        for path, body in paid_routes(tmp_path / "data") * 2:
            result = parse_sse(client.post(path, json=body).text)
            assert result[0][1]["message"] == api.LIVE_PAUSED_MESSAGE, (path, result)
    assert not app.state.limiter._hits and app.state.ledger.pending_count() == 0


@pytest.mark.parametrize("route_index", range(6))
def test_every_async_route_offloads_transactional_admission(tmp_path, monkeypatch, route_index):
    entered, release = threading.Event(), threading.Event()
    threads = []

    class BlockingLedger:
        def reserve(self, amount):
            threads.append(threading.get_ident())
            entered.set()
            assert release.wait(2), "event loop did not release the admission worker"
            return None

    monkeypatch.setattr(api, "_ledger", lambda *_: BlockingLedger())
    monkeypatch.setattr(extract, "settings_env", lambda: {"RECEIPTS_PRIVATE_MODE": "true",
        "RECEIPTS_MODAL_BASE_URL": "https://operator--glimmer.modal.run/v1"})
    monkeypatch.setattr(extract, "_load_api_key", lambda *_: "offline-not-a-real-credential")
    app = api.create_app(settings(tmp_path, use_caches=False), store=AskStore(), client_factory=object)
    path, body = paid_routes(tmp_path / "data")[route_index]

    async def run():
        encoded = json.dumps(body).encode()
        req, _ = request(path)
        async def receive():
            return {"type": "http.request", "body": encoded, "more_body": False}
        req._receive = receive
        task = asyncio.create_task(endpoint(app, path)(req))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            # This coroutine can progress while a synchronous ledger waits.
            assert threads == [threads[0]] and threads[0] != threading.get_ident()
        finally:
            release.set()
        result = await output(await task)
        assert result[0][0] == "error" and result[0][1]["model_calls"] == 0

    asyncio.run(run())


def test_cancellation_during_admission_cannot_dispatch_or_refund_an_unknown_commit(tmp_path, monkeypatch):
    ledger = api.LocalLedger(1, tmp_path / "spend.sqlite3")
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    class SlowLedger:
        def reserve(self, amount):
            entered.set()
            assert release.wait(2)
            try:
                return ledger.reserve(amount)
            finally:
                completed.set()

    monkeypatch.setattr(api, "_ledger", lambda *_: SlowLedger())
    monkeypatch.setattr(stream, "check_events", forbidden)
    app = api.create_app(settings(tmp_path, use_caches=False), store=object(), client_factory=object)

    async def run():
        req, _ = request()
        async def receive():
            return {"type": "http.request", "body": json.dumps({"text": TEXT}).encode(), "more_body": False}
        req._receive = receive
        task = asyncio.create_task(endpoint(app, "/api/check")(req))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        assert await asyncio.to_thread(completed.wait, 1)

    asyncio.run(run())
    assert ledger.pending_count() == 1 and ledger.spent_today() > 0


def test_offloaded_admission_still_fails_closed():
    class BrokenLedger:
        def reserve(self, amount):
            raise BudgetUnavailable()
    with pytest.raises(api.Rejected) as caught:
        asyncio.run(api._reserve_async(BrokenLedger(), .1))
    assert caught.value.reason == "budget_unavailable"


def test_deployment_defaults_ignore_ordinary_cache_paths_and_keep_snapshot():
    config = api.Settings.from_env({"VERCEL": "1", "RECEIPTS_DATA_DIR": "/tmp/ordinary-private-data",
        "RECEIPTS_API_CACHE_DIR": "/tmp/ordinary-event-cache", "RECEIPTS_SPARK_CACHE_DIR": "/tmp/ordinary-spark"})
    assert config.api_cache_dir is config.spark_cache_dir is None
    assert config.prebaked_dirs == (api.REPO_ROOT / "data/snapshot/api_cache",)
    assert config.spark_read_dirs == (api.REPO_ROOT / "data/snapshot/spark_cache",)
    assert config.use_caches and config.require_shared_budget


def test_explicit_cache_persistence_is_required_to_reenable_deployment_paths():
    config = api.Settings.from_env({"VERCEL": "1", "RECEIPTS_PERSIST_DRAFT_CACHES": "true",
                                   "RECEIPTS_API_CACHE_DIR": "/tmp/opted-in-cache"})
    assert config.api_cache_dir == Path("/tmp/opted-in-cache") and config.spark_cache_dir is not None
    assert api.Settings.from_env({}).api_cache_dir is not None


def test_disabling_local_draft_caches_preserves_existing_accounting(tmp_path):
    base = {"RECEIPTS_API_CACHE_DIR": str(tmp_path / "api"), "RECEIPTS_DAILY_BUDGET_USD": "1"}
    before = api._ledger(api.Settings.from_env(base))
    assert before.reserve(.2) is not None
    config = api.Settings.from_env({**base, "RECEIPTS_PERSIST_DRAFT_CACHES": "false"})
    after = api._ledger(config)
    assert config.api_cache_dir is config.spark_cache_dir is None
    assert after.path == before.path and after.pending_count() == 1
    assert after.spent_today() == pytest.approx(.2)


@pytest.mark.parametrize("path,last_event", [("/api/check", "done"), ("/api/fix", "result")])
def test_ordinary_live_drafts_never_create_or_reuse_response_caches_by_default_on_vercel(
        tmp_path, path, last_event):
    config = api.Settings.from_env({"VERCEL": "1", "RECEIPTS_REQUIRE_SHARED_BUDGET": "0",
        "RECEIPTS_API_CACHE_DIR": str(tmp_path / "api"),
        "RECEIPTS_SPARK_CACHE_DIR": str(tmp_path / "spark")})
    calls = []

    def provider(**request):
        calls.append(request)
        return response(NS(**TOKENS))

    app = api.create_app(config, store=object(), client_factory=lambda: model_client(provider),
                         wonder_client_factory=forbidden)
    with TestClient(app) as client:
        for _ in range(2):
            rows = parse_sse(client.post(path, json={"text": "This unpublished draft has no financial figures."}).text)
            assert rows[-1][0] == last_event and rows[-1][1]["model_calls"] == 1
    assert len(calls) == 2 and not list(tmp_path.rglob("*.json"))
    assert app.state.ledger.pending_count() == 0 and app.state.ledger.spent_today() > 0


def test_snapshot_only_policy_keeps_saved_examples_and_time_travel_without_cache_writes(monkeypatch):
    config = api.Settings.from_env({"VERCEL": "1", "RECEIPTS_DATA_DIR": "data/snapshot",
                                   "RECEIPTS_LIVE_REQUESTS_ENABLED": "false"})
    monkeypatch.setattr(extract, "make_client", forbidden)
    original_put = extract._Cache.put

    def no_writable_cache(cache, key, record):
        assert cache.dir is None
        return original_put(cache, key, record)

    monkeypatch.setattr(extract._Cache, "put", no_writable_cache)
    app = api.create_app(config, client_factory=forbidden)
    assert app.state.cache.write_dir is None
    assert app.state.cache.read_dirs == list(config.prebaked_dirs)
    with TestClient(app) as client:
        for example in client.get("/api/examples").json():
            for as_of in (example["as_of"], "2026-01-01T00:00:00Z"):
                rows = parse_sse(client.post("/api/check", json={"text": example["text"],
                    "context": example["context"], "as_of": as_of}).text)
                assert rows[-1][0] == "done", (example["id"], rows[-1])
                assert rows[-1][1]["model_calls"] == 0 and rows[-1][1]["cost_usd"] == 0
        assert client.get("/api/health").json()["draft_cache_policy"] == "snapshot_only"


@pytest.mark.parametrize("options,policy", [
    ({}, "persistent"), ({"api_cache_dir": None, "spark_cache_dir": None}, "snapshot_only"),
    ({"use_caches": False}, "disabled"),
])
def test_health_discloses_active_cache_policy_even_when_evidence_is_unavailable(tmp_path, options, policy):
    app = api.create_app(settings(tmp_path, **options), store=object())
    response = TestClient(app).get("/api/health")
    assert response.status_code == 503 and response.json()["draft_cache_policy"] == policy


# --- Integration review follow-up -------------------------------------------------------------------

def raw_request(peer, pairs):
    """A request whose headers may repeat, as proxies that append their own line produce."""
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    scope = {"type": "http", "method": "POST", "path": "/api/check", "client": (peer, 30000),
             "headers": [(k.encode(), v.encode()) for k, v in pairs]}
    return Request(scope, receive)


def test_a_trusted_proxys_own_appended_line_decides_the_identity(tmp_path):
    # HAProxy-style forwarding: the client's line first, the trusted proxy's own line last.
    config = settings(tmp_path, trusted_proxy_cidrs=("10.0.0.0/8",))
    seen = {api.client_ip(raw_request("10.0.0.2", [("x-forwarded-for", f"198.51.100.{n}"),
                                                   ("x-forwarded-for", "203.0.113.7")]), config)
            for n in range(5)}
    assert seen == {"203.0.113.7"}


def test_a_repeated_vercel_header_falls_back_to_the_socket_peer():
    config = api.Settings.from_env({"VERCEL": "1"})
    req = raw_request("198.51.100.20", [("x-vercel-forwarded-for", "203.0.113.5"),
                                        ("x-vercel-forwarded-for", "192.0.2.1")])
    assert api.client_ip(req, config) == "198.51.100.20"


def test_no_worker_thread_at_all_still_settles_the_reservation_once(monkeypatch):
    finished = []

    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    async def no_executor(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    fake_asyncio = NS(**{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")})
    fake_asyncio.to_thread = no_executor
    monkeypatch.setattr(api, "threading", NS(Thread=NoThread))
    monkeypatch.setattr(api, "asyncio", fake_asyncio)

    async def run():
        return [part async for part in api._from_thread(iter(()), lambda: finished.append(1), 15.0)]

    parts = asyncio.run(run())
    assert finished == [1]
    assert parse_sse("".join(parts))[-1][0] == "error"


def test_private_settlement_failure_still_answers_with_a_private_error(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from receipts import private

    class SettleFails:
        def reserve(self, amount):
            return api.Reservation("op", "nocap", "2026-10-30", 1, 1_000_000)

        def settle(self, *args, **kwargs):
            raise BudgetUnavailable()

    def no_client(_backend):
        raise RuntimeError("Modal unreachable")

    config = settings(tmp_path, use_caches=False)
    app = FastAPI()
    app.include_router(private.create_private_router(
        settings=config, limiter=api.RateLimiter(5, 60), ledger=SettleFails(), get_store=object,
        backend_factory=lambda: private.private_backend({
            "RECEIPTS_PRIVATE_MODE": "1", "RECEIPTS_MODAL_BASE_URL": "https://operator--glimmer.modal.run/v1"}),
        client_factory=no_client))
    response = TestClient(app).post("/api/private/check", json={"text": TEXT})
    assert response.status_code == 200 and response.headers["cache-control"].startswith("no-store")
    events = parse_sse(response.text)
    assert events[-1][0] == "error" and "No fallback was used" in events[-1][1]["message"]


def test_an_ipv4_mapped_proxy_peer_is_still_the_configured_proxy(tmp_path):
    config = settings(tmp_path, trusted_proxy_cidrs=("127.0.0.1/32",))
    req = raw_request("::ffff:127.0.0.1", [("x-forwarded-for", "203.0.113.9")])
    assert api.client_ip(req, config) == "203.0.113.9"
