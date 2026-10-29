"""Synthetic-only admission tests; never open the held-out corpus."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from eval.heldout_release import HeldoutBudget
from receipts import extract as ex


@pytest.fixture
def setup_guard(tmp_path):
    backend = ex.BACKENDS["meta"]
    request = ex.build_request("Synthetic draft with no financial claims.", None, backend=backend)
    key = ex.request_key(request, backend)
    return HeldoutBudget(tmp_path / "attempts.json", {key}, cap_usd=.10), request, backend


@pytest.mark.parametrize("usage", [None, {"input_tokens": -1, "output_tokens": 2}])
def test_missing_or_invalid_usage_keeps_reservation(setup_guard, usage):
    guard, request, backend = setup_guard
    guard.call(ex.RequestUsage(backend), lambda: SimpleNamespace(usage=usage), request)
    assert guard.summary()["charge_usd"] == ex.estimate_request_cost(request, backend)
    assert guard.summary()["estimated_calls"] == 1


def test_dispatched_failure_is_not_refunded(setup_guard):
    guard, request, backend = setup_guard
    def failure():
        raise TimeoutError("synthetic transport failure")
    with pytest.raises(TimeoutError):
        guard.call(ex.RequestUsage(backend), failure, request)
    row = guard.summary()["attempts"][0]
    assert row["started"] and row["estimated"] and row["charge_usd"] > 0


def test_rejection_before_dispatch_can_refund(setup_guard):
    guard, request, backend = setup_guard
    meter = ex.RequestUsage(backend)
    meter.finish()
    with pytest.raises(ex._RequestStopped):
        guard.call(meter, lambda: pytest.fail("No dispatch allowed"), request)
    assert guard.summary()["calls"] == 0 and guard.summary()["charge_usd"] == 0


def test_complete_usage_replaces_estimate(setup_guard):
    guard, request, backend = setup_guard
    guard.call(ex.RequestUsage(backend), lambda: SimpleNamespace(usage={"input_tokens": 100, "output_tokens": 10}), request)
    assert guard.summary()["charge_usd"] == .0001675
    assert guard.summary()["estimated_calls"] == 0


def test_unfrozen_request_rejected_before_dispatch(setup_guard):
    guard, request, backend = setup_guard
    changed = {**request, "input": "A different synthetic draft"}
    with pytest.raises(ex.ModelCallRejected):
        guard.call(ex.RequestUsage(backend), lambda: pytest.fail("No dispatch allowed"), changed)
    assert not guard.summary()["attempts"]


def test_parallel_reservations_cannot_overspend(setup_guard):
    guard, request, backend = setup_guard
    entered = threading.Event()
    release = threading.Event()
    def held():
        entered.set()
        assert release.wait(2)
        return SimpleNamespace(usage=None)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(guard.call, ex.RequestUsage(backend), held, request)
            assert entered.wait(2)
            second = pool.submit(guard.call, ex.RequestUsage(backend), held, request)
            # Both admitted reservations remain charged until released; a third
            # cannot pass even though neither provider result has arrived.
            while len(guard.summary()["attempts"]) < 2:
                if second.done():
                    second.result()
                threading.Event().wait(.001)
            with pytest.raises(ex.ModelCallRejected):
                guard.call(ex.RequestUsage(backend), lambda: pytest.fail("No third dispatch"), request)
            release.set()
            first.result(); second.result()
    finally:
        release.set()
    assert guard.summary()["charge_usd"] <= .10


def test_global_wrapper_restored_even_after_error(setup_guard):
    guard, _, _ = setup_guard
    original = ex.RequestUsage.call
    with pytest.raises(RuntimeError):
        with guard.installed():
            assert ex.RequestUsage.call is not original
            raise RuntimeError("synthetic interruption")
    assert ex.RequestUsage.call is original
