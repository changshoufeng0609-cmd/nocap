"""Expanded dev selection and spending admission; every provider result is synthetic."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest

from eval import expanded_dev as expanded
from eval.attempt_budget import AttemptBudget
from receipts import extract as ex


def setup_guard(tmp_path, **kwargs):
    backend = ex.make_backend("baseten")
    request = ex.build_request("NVIDIA revenue was $96.2 billion.", "NVIDIA Q2 FY2027", backend=backend)
    guard = AttemptBudget(tmp_path / "ledger.json", backend=backend,
                          allowed_keys={ex.request_key(request, backend)}, **kwargs)
    return backend, request, guard


def response(usage=None):
    return SimpleNamespace(usage=usage)


def test_success_usage_settles_and_original_method_restored(tmp_path):
    backend, request, guard = setup_guard(tmp_path)
    original = ex.RequestUsage.call
    with guard.installed():
        ex.RequestUsage(backend).call(lambda: response({"input_tokens": 100, "output_tokens": 50}), request)
    assert ex.RequestUsage.call is original
    state = guard.summary()
    assert state["calls"] == 1 and state["estimated_calls"] == 0
    assert state["charge_usd"] == pytest.approx(.0003375)
    assert json.loads(guard.path.read_text()) == state


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": -1, "output_tokens": 3},
                                   {"input_tokens": 20, "output_tokens": "3"},
                                   {"input_tokens": 20, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 21}}])
def test_missing_or_invalid_usage_retains_estimate(tmp_path, usage):
    backend, request, guard = setup_guard(tmp_path)
    with guard.installed():
        ex.RequestUsage(backend).call(lambda: response(usage), request)
    state = guard.summary()
    assert state["estimated_calls"] == 1
    assert state["charge_usd"] == ex.estimate_request_cost(request, backend)


def test_failure_after_dispatch_keeps_charge_without_provider_text(tmp_path):
    backend, request, guard = setup_guard(tmp_path)
    def fail():
        raise RuntimeError("SECRET_PROVIDER_PAYLOAD")
    with guard.installed(), pytest.raises(RuntimeError):
        ex.RequestUsage(backend).call(fail, request)
    assert guard.summary()["estimated_calls"] == 1
    assert "SECRET" not in guard.path.read_text()


def test_predispatch_rejection_refunds_without_call(tmp_path):
    backend, request, guard = setup_guard(tmp_path)
    def reject(_):
        raise ex.ModelCallRejected("denied")
    with guard.installed(), pytest.raises(ex.ModelCallRejected):
        ex.RequestUsage(backend, before_call=reject).call(lambda: pytest.fail("dispatched"), request)
    assert guard.summary()["charge_usd"] == 0 and guard.summary()["calls"] == 0


def test_concurrent_admission_never_oversubscribes(tmp_path):
    backend, request, _ = setup_guard(tmp_path)
    charge = ex.estimate_request_cost(request, backend)
    guard = AttemptBudget(tmp_path / "concurrent.json", backend=backend,
                          allowed_keys={ex.request_key(request, backend)}, cap_usd=charge * 2 + .000001)
    def work(_):
        try:
            ex.RequestUsage(backend).call(lambda: response(), request)
            return True
        except ex.ModelCallRejected:
            return False
    with guard.installed(), ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(work, range(12)))
    assert sum(outcomes) == 2 and guard.summary()["calls"] == 2
    assert guard.summary()["charge_usd"] <= guard.cap_usd


def test_attempt_limit_counts_each_retry(tmp_path):
    backend, request, guard = setup_guard(tmp_path, max_attempts=2)
    with guard.installed():
        for _ in range(2):
            ex.RequestUsage(backend).call(lambda: response({"input_tokens": 1, "output_tokens": 1}), request)
        with pytest.raises(ex.ModelCallRejected):
            ex.RequestUsage(backend).call(lambda: pytest.fail("third attempt"), request)
    assert guard.summary()["calls"] == 2


@pytest.mark.parametrize("change", [{"model": "other"}, {"max_tokens": 12001}, {"messages": []}])
def test_unapproved_request_cannot_dispatch(tmp_path, change):
    backend, request, guard = setup_guard(tmp_path)
    with guard.installed(), pytest.raises(ex.ModelCallRejected):
        ex.RequestUsage(backend).call(lambda: pytest.fail("unapproved"), {**request, **change})
    assert guard.summary()["calls"] == 0 and not guard.rows


def test_other_backend_and_existing_ledger_rejected(tmp_path):
    backend, request, guard = setup_guard(tmp_path)
    with pytest.raises(FileExistsError):
        AttemptBudget(guard.path, backend=backend, allowed_keys=set())
    with guard.installed(), pytest.raises(ex.ModelCallRejected):
        ex.RequestUsage(ex.make_backend("meta")).call(lambda: pytest.fail("Meta live"), request)


@pytest.mark.parametrize("kwargs", [{"cap_usd": float("nan")}, {"cap_usd": 3.01}, {"max_attempts": 61}])
def test_authorization_cannot_be_increased(tmp_path, kwargs):
    with pytest.raises(ValueError):
        setup_guard(tmp_path, **kwargs)


def test_selection_is_fixed_dev_only_with_required_counts():
    fixed = expanded.manifest()
    assert fixed == expanded.manifest()
    assert len(fixed["items"]) == len({i["item_id"] for i in fixed["items"]}) == 50
    assert fixed["bucket_counts"] == dict(expanded.BUCKETS)
    assert all(len(i["requests"][target]) == 1 for i in fixed["items"] for target in expanded.TARGETS)


def test_quality_comparison_ignores_only_runtime_fields():
    score = {"rows": [{"outcome": "caught", "cached": True, "latency_s": 0.0}], "originals": {"n": 24}}
    assert expanded.quality(score)["rows"] == [{"outcome": "caught"}]
    assert expanded.quality(score)["originals"] == {"n": 24}
