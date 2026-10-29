"""Offline slide accounting: one settlement per terminal and honest unknown costs."""
import json
import time
from types import SimpleNamespace as NS

import pytest

from receipts import extract, slides
from test_slides import Model, run
from test_verify import FakeStore


IMAGE = slides.SlideImage("data:image/png;base64,AA==", 100, 100)


def terminal(kind="completed", usage=None, *, status=None):
    return NS(type=f"response.{kind}", response=NS(status=status or kind, usage=usage))


class SamStream:
    def __init__(self, events, *, forbid_tail=False):
        self.events, self.forbid_tail = events, forbid_tail
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for event in self.events:
            self.reads += 1
            yield event
        if self.forbid_tail:
            raise AssertionError("The terminal stream tail must not be read")

    def close(self):
        self.closed = True


def sam(stream, *, stats=None):
    stats = stats if stats is not None else slides.SlideStats()
    client = NS(responses=NS(create=lambda **_: stream))
    boxes = slides._sam(client, IMAGE, "table", stats, timeout_s=15)
    return boxes, stats


@pytest.mark.parametrize("images", [None, False, True, -1, 0.5, 1.0, "1", float("nan"),
                                    float("inf"), 2 ** 63, 10 ** 400])
def test_invalid_sam_count_keeps_one_image_estimate(images):
    stream = SamStream([terminal(usage=NS(images_processed=images))], forbid_tail=True)
    boxes, stats = sam(stream)
    assert boxes == [] and stream.closed
    assert stats.cost_usd == pytest.approx(slides.SAM_PRICE_USD)
    assert stats.model_calls == stats.estimated_calls == 1 and stats.cost_is_estimate


@pytest.mark.parametrize("usage", [None, NS(), {}])
def test_missing_sam_count_keeps_the_estimate(usage):
    stream = SamStream([terminal(usage=usage)], forbid_tail=True)
    _, stats = sam(stream)
    assert stream.closed and stats.estimated_calls == 1
    assert stats.cost_usd == pytest.approx(slides.SAM_PRICE_USD)


@pytest.mark.parametrize("images", [0, 1, 2])
@pytest.mark.parametrize("mapping", [False, True])
def test_valid_sam_count_replaces_estimate_once(images, mapping):
    usage = {"images_processed": images} if mapping else NS(images_processed=images)
    stream = SamStream([terminal(usage=usage)], forbid_tail=True)
    _, stats = sam(stream)
    assert stream.closed and stream.reads == 1
    assert stats.cost_usd == pytest.approx(images * slides.SAM_PRICE_USD)
    assert stats.model_calls == 1 and stats.estimated_calls == 0 and not stats.cost_is_estimate


def test_duplicate_terminal_cannot_reconcile_the_same_call_twice():
    stream = SamStream([terminal(usage=NS(images_processed=0)),
                        terminal(usage=NS(images_processed=9))], forbid_tail=True)
    _, stats = sam(stream)
    assert stream.reads == 1 and stream.closed
    assert stats.cost_usd == 0 and stats.estimated_calls == 0 and stats.model_calls == 1


@pytest.mark.parametrize("kind,status", [("failed", "failed"), ("incomplete", "incomplete"),
                                        ("completed", "failed")])
def test_failed_terminal_records_usage_once_and_rejects_result(kind, status):
    stream = SamStream([terminal(kind, NS(images_processed=2), status=status),
                        terminal(usage=NS(images_processed=0))], forbid_tail=True)
    stats = slides.SlideStats()
    with pytest.raises(slides.SlideError, match="did not finish"):
        sam(stream, stats=stats)
    assert stream.closed and stream.reads == 1
    assert stats.cost_usd == pytest.approx(2 * slides.SAM_PRICE_USD)
    assert stats.model_calls == 1 and stats.estimated_calls == 0


def test_late_terminal_records_known_usage_before_rejecting_result(monkeypatch):
    ticks = iter([100, 116])
    monkeypatch.setattr(slides.time, "monotonic", lambda: next(ticks))
    stream = SamStream([terminal(usage=NS(images_processed=2))], forbid_tail=True)
    stats = slides.SlideStats()
    with pytest.raises(slides.SlideError, match="ran out of time"):
        sam(stream, stats=stats)
    assert stream.closed and stats.estimated_calls == 0
    assert stats.cost_usd == pytest.approx(2 * slides.SAM_PRICE_USD)


@pytest.mark.parametrize("events", [[], [NS(type="error")], [terminal("failed", NS(images_processed=False))]])
def test_missing_or_invalid_failure_usage_retains_the_estimate(events):
    stream, stats = SamStream(events), slides.SlideStats()
    with pytest.raises(slides.SlideError):
        sam(stream, stats=stats)
    assert stream.closed and stats.model_calls == stats.estimated_calls == 1
    assert stats.cost_usd == pytest.approx(slides.SAM_PRICE_USD)


class SparkUsageModel(Model):
    def __init__(self, usage, **kwargs):
        super().__init__(**kwargs)
        self.spark_usage = usage

    def create(self, **request):
        response = super().create(**request)
        if request["model"] == slides.SPARK_MODEL:
            response.usage = self.spark_usage
        return response


@pytest.mark.parametrize("usage", [None, NS(input_tokens=100, output_tokens=None),
                                   NS(input_tokens=100, output_tokens=-1_000_000),
                                   NS(input_tokens=True, output_tokens=1),
                                   NS(input_tokens=1.5, output_tokens=1),
                                   NS(input_tokens=1, output_tokens=1,
                                      input_tokens_details=NS(cached_tokens=2))])
def test_invalid_spark_usage_cannot_erase_known_sam_costs(usage):
    events, stats, _ = run(SparkUsageModel(usage))
    assert events[-1][0] == "done"
    assert stats.cost_usd == pytest.approx(2 * slides.SAM_PRICE_USD + slides.SPARK_UNKNOWN_USAGE_USD)
    assert stats.model_calls == 3 and stats.estimated_calls == 1
    assert events[-1][1]["cost_is_estimate"] and events[-1][1]["model_calls"] == 3


@pytest.mark.parametrize("model,estimated", [(Model(spark_error="PRIVATE_PROVIDER_KEY"), True),
                                             (Model(body={"not": "a slide response"}), False),
                                             (Model(status="incomplete"), False)])
def test_terminal_errors_expose_accounting_without_provider_details(model, estimated):
    events, stats, _ = run(model)
    name, payload = events[-1]
    assert name == "error" and not stats.complete
    assert payload["cost_usd"] == round(stats.cost_usd, 6) and stats.cost_usd > 0
    assert payload["model_calls"] == stats.model_calls == 3
    assert payload["cost_is_estimate"] is stats.cost_is_estimate is estimated
    assert "PRIVATE_PROVIDER_KEY" not in json.dumps(events)
    assert not any(name == "done" for name, _ in events)


def test_failed_sam_sdk_calls_remain_estimated_through_spark_fallback():
    events, stats, model = run(Model(sam="fail"))
    assert events[-1][0] == "done" and len(model.requests) == 3
    assert stats.model_calls == 3 and stats.estimated_calls == 2
    assert events[-1][1]["cost_is_estimate"]
    assert events[-1][1]["cost_usd"] == round(stats.cost_usd, 6)


def test_missing_key_reports_zero_dispatch_cost():
    def missing():
        raise extract.ExtractionError("PRIVATE_KEY_CONFIGURATION")

    stats = slides.SlideStats()
    events = list(slides.slide_events(IMAGE, store=FakeStore(), stats=stats, client_factory=missing))
    assert len(events) == 1 and events[0][0] == "error"
    assert events[0][1]["model_calls"] == 0 and events[0][1]["cost_usd"] == 0
    assert events[0][1]["cost_is_estimate"] is False
    assert "PRIVATE_KEY_CONFIGURATION" not in json.dumps(events)


def test_expired_deadline_reports_zero_dispatch_cost():
    events, stats, model = run(deadline=time.monotonic() - 1)
    assert events[-1][0] == "error" and model.requests == []
    assert events[-1][1]["model_calls"] == 0 and events[-1][1]["cost_usd"] == 0
    assert not stats.cost_is_estimate and not events[-1][1]["cost_is_estimate"]


@pytest.mark.parametrize("denied_call", [1, 2, 3])
def test_admission_rejection_never_dispatches_or_charges_the_denied_call(denied_call):
    model, admissions = Model(), []

    def before_call(estimate):
        assert len(model.requests) == len(admissions)
        admissions.append(estimate)
        if len(admissions) == denied_call:
            raise extract.ModelCallRejected("PRIVATE_LEDGER_DETAILS")

    events, stats, _ = run(model, before_model_call=before_call)
    name, payload = events[-1]
    assert name == "error" and payload["retryable"] is False
    assert len(model.requests) == stats.model_calls == denied_call - 1
    assert admissions == [slides.SAM_PRICE_USD, slides.SAM_PRICE_USD, slides.SPARK_UNKNOWN_USAGE_USD][:denied_call]
    assert stats.cost_usd == pytest.approx((denied_call - 1) * slides.SAM_PRICE_USD)
    assert payload["cost_usd"] == round(stats.cost_usd, 6)
    assert stats.estimated_calls == 0 and not payload["cost_is_estimate"]
    assert "PRIVATE_LEDGER_DETAILS" not in json.dumps(events)
    assert not any(name == "done" for name, _ in events)


def test_admission_rejection_preserves_prior_unknown_sam_cost():
    admissions = []

    def before_call(estimate):
        admissions.append(estimate)
        if len(admissions) == 2:
            raise extract.ModelCallRejected()

    events, stats, model = run(Model(sam="fail"), before_model_call=before_call)
    assert events[-1][0] == "error" and events[-1][1]["retryable"] is False
    assert len(model.requests) == stats.model_calls == stats.estimated_calls == 1
    assert stats.cost_usd == pytest.approx(slides.SAM_PRICE_USD)
    assert events[-1][1]["cost_is_estimate"]


def test_unexpected_admission_failure_is_redacted_and_never_dispatches():
    def before_call(_estimate):
        raise RuntimeError("PRIVATE_STORAGE_DETAILS")

    events, stats, model = run(before_model_call=before_call)
    assert events[-1][0] == "error" and events[-1][1]["retryable"] is False
    assert not model.requests and stats.model_calls == stats.cost_usd == stats.estimated_calls == 0
    assert "PRIVATE_STORAGE_DETAILS" not in json.dumps(events)


def test_successful_slide_admits_each_sdk_call_within_reserved_amount():
    model, admissions = Model(), []

    def before_call(estimate):
        assert len(model.requests) == len(admissions)
        admissions.append(estimate)

    events, stats, _ = run(model, before_model_call=before_call)
    assert events[-1][0] == "done" and stats.model_calls == 3
    assert admissions == [slides.SAM_PRICE_USD, slides.SAM_PRICE_USD, slides.SPARK_UNKNOWN_USAGE_USD]
    assert sum(admissions) <= slides.RESERVE_USD
