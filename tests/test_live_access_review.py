"""Independent end-to-end check of Slide's direct SAM dispatch at live-window expiry."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from receipts import api, slides
from test_api import parse_sse, settings
from test_slides import Model, image_url
from test_verify import FakeStore


@pytest.mark.parametrize("known_usage", [True, False])
def test_slide_expiry_after_first_sam_cannot_start_remaining_models_or_erase_charge(
        tmp_path, monkeypatch, known_usage):
    until = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)
    now = [until - timedelta(seconds=1)]
    monkeypatch.setattr(api, "_utc_now", lambda: now[0])

    class ExpiringModel(Model):
        def create(self, **request):
            # The first, already admitted SAM request may complete after the cutoff.
            response = super().create(**request)
            now[0] = until
            return response

    model = ExpiringModel(sam="box" if known_usage else "truncated")
    config = settings(tmp_path, live_until=until, daily_budget_usd=1,
                      spend_namespace="same-namespace-after-expiry")
    app = api.create_app(config, store=FakeStore(), slide_client_factory=lambda: model)
    events = parse_sse(TestClient(app).post("/api/slide", json={"image_data_url": image_url()}).text)

    assert [request["model"] for request in model.requests] == [slides.SAM_MODEL]
    assert events[-1][0] == "error" and not any(kind == "done" for kind, _ in events)
    assert events[-1][1]["model_calls"] == 1
    assert events[-1][1]["cost_usd"] == pytest.approx(slides.SAM_PRICE_USD)
    assert events[-1][1]["cost_is_estimate"] is not known_usage
    assert all(response.closed for response in model.streams)

    # Known usage settles the first request. Missing usage retains the original
    # operation reservation, including when the time gate is subsequently reopened.
    expected = slides.SAM_PRICE_USD if known_usage else slides.RESERVE_USD
    assert app.state.ledger.spent_today() == pytest.approx(expected)
    assert app.state.ledger.pending_count() == 0
    reopened = api._ledger(replace(config, live_until=None))
    assert reopened.namespace == config.spend_namespace and reopened.cap_usd == 1
    assert reopened.spent_today() == pytest.approx(expected) and reopened.pending_count() == 0
