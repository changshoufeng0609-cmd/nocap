"""Slide endpoint upload trust boundary, shared guards and usage settlement."""
import json

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from receipts import api, extract, slides
from test_slides import Model, image_url, output
from test_verify import FakeStore


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
            for block in response.text.split("\n\n") if block.startswith("event:")]


def client(tmp_path, model=None, **settings):
    return TestClient(api.create_app(api.Settings(data_dir=tmp_path, api_cache_dir=tmp_path / "api", **settings),
                                    store=FakeStore(), slide_client_factory=lambda: model or Model()))


def test_slide_real_pipeline_via_http_and_no_image_cache(tmp_path):
    model = Model()
    c = client(tmp_path, model)
    result = events(c.post("/api/slide", json={"image_data_url": image_url(), "context": "  NVIDIA Q2 FY2027  ",
                                             "as_of": "2026-08-27T08:00:00+08:00"}))
    assert result[0][0] == "meta" and result[-1][0] == "done"
    assert result[0][1]["as_of"] == "2026-08-27T00:00:00Z"
    assert any(name == "verdict" and data["status"] == "verified" for name, data in result)
    assert json.loads(model.requests[-1]["input"][0]["content"][0]["text"])["context"] == "NVIDIA Q2 FY2027"
    # Admission debits round upward to micro-USD; display prices round to nearest.
    reported = result[-1][1]["cost_usd"]
    assert reported <= c.app.state.ledger.spent_today() <= reported + 0.000001001
    assert c.app.state.ledger.pending_count() == 0
    assert [p.name for p in (tmp_path / "api").iterdir()] == ["_spend.sqlite3"]


@pytest.mark.parametrize("body", [
    {}, {"image_data_url": "https://example.com/photo.png"}, {"image_data_url": "data:image/png;base64,AA=="},
    {"image_data_url": image_url(), "context": "x" * 301},
    {"image_data_url": image_url(), "as_of": "next year"},
    {"image_data_url": image_url(), "verdicts": []}, {"image_data_url": image_url(), "regions": []},
    {"image_data_url": image_url(), "claims": []}, {"image_data_url": image_url(), "context": 1},
])
def test_upload_invalid_or_client_supplied_authority_cannot_start_models(tmp_path, body):
    model = Model()
    c = client(tmp_path, model)
    result = events(c.post("/api/slide", json=body))
    assert result[0][0] == "error" and result[0][1]["retryable"] is False
    assert not model.requests and c.app.state.ledger.pending_count() == 0


def test_oversized_and_invalid_json_body_are_bounded(tmp_path):
    model = Model()
    c = client(tmp_path, model)
    for payload in (b"{", b"x" * (slides.MAX_BODY_BYTES + 1)):
        assert events(c.post("/api/slide", content=payload))[0][0] == "error"
    assert not model.requests


def test_slide_budget_reservation_prevents_model_work(tmp_path):
    model = Model()
    c = client(tmp_path, model, daily_budget_usd=slides.RESERVE_USD - 0.01)
    error = events(c.post("/api/slide", json={"image_data_url": image_url()}))[0]
    assert error[0] == "error" and "budget" in error[1]["message"]
    assert not model.requests and c.app.state.ledger.pending_count() == 0


def test_slide_and_fix_share_rate_limit(tmp_path):
    c = client(tmp_path, rate_limit_checks=1)
    assert events(c.post("/api/slide", json={"image_data_url": image_url()}))[-1][0] == "done"
    error = events(c.post("/api/fix", json={"text": "NVIDIA revenue was $96.2 billion."}))[0]
    assert error[0] == "error" and "Too many" in error[1]["message"]


def test_failed_model_settles_estimate_and_redacts_provider_message(tmp_path):
    c = client(tmp_path, Model(spark_error="SECRET_UPLOAD_AND_KEY"))
    response = c.post("/api/slide", json={"image_data_url": image_url()})
    error = events(response)[-1]
    assert error[0] == "error"
    assert "SECRET_UPLOAD_AND_KEY" not in response.text
    # Every attempt was accounted, so the failed Spark call keeps its own $0.10 estimate, not the whole precharge.
    assert error[1]["cost_is_estimate"] is True
    assert slides.SPARK_UNKNOWN_USAGE_USD <= error[1]["cost_usd"] < slides.RESERVE_USD
    # The ledger rounds each settlement up to a whole micro-dollar, so float noise can add one.
    assert error[1]["cost_usd"] <= c.app.state.ledger.spent_today() <= error[1]["cost_usd"] + 2e-6
    assert c.app.state.ledger.pending_count() == 0


def test_unexpected_worker_error_still_settles_usage(tmp_path, monkeypatch):
    def fail(image, **kw):
        kw["stats"].cost_usd = 0.025
        yield "progress", {"stage": "reading"}
        raise RuntimeError("SECRET_UPLOAD_AND_KEY")

    monkeypatch.setattr(slides, "slide_events", fail)
    c = client(tmp_path)
    response = c.post("/api/slide", json={"image_data_url": image_url()})
    assert events(response)[-1][0] == "error" and "SECRET_UPLOAD_AND_KEY" not in response.text
    assert c.app.state.ledger.spent_today() == pytest.approx(0.025)
    assert c.app.state.ledger.pending_count() == 0


def test_server_meta_client_is_reused_and_separate_from_extraction(tmp_path, monkeypatch):
    made = []
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "fireworks")
    monkeypatch.setattr(extract, "make_client", lambda **kw: made.append(kw) or Model())
    app = api.create_app(api.Settings(data_dir=tmp_path, api_cache_dir=None), store=FakeStore())
    c = TestClient(app)
    for _ in range(2):
        assert events(c.post("/api/slide", json={"image_data_url": image_url()}))[-1][0] == "done"
    assert len(made) == 1 and made[0]["backend"].name == "meta"
    assert made[0]["backend"].model == "muse-spark-1.3"


def test_real_sdk_parses_sam_stream_and_spark_schema_through_http(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "https://api.meta.ai/v1/responses"
        usage = {"input_tokens": 100, "output_tokens": 200, "input_tokens_details": {"cached_tokens": 0},
                 "output_tokens_details": {"reasoning_tokens": 0}, "images_processed": 1}
        response = {"id": "test-response", "object": "response", "created_at": 1, "status": "completed",
                    "model": body["model"], "output": [], "usage": usage}
        if body["model"] == slides.SAM_MODEL:
            stream = [
                {"type": "response.output_text.delta", "delta": "<0f>0<|box;x1=10;y1=10;x2=90;y2=90;w=100;h=100;c=0.9|>"
                 "<|mask;x=0;y=0;data=81,81,!opaque|>", "item_id": "test-item", "output_index": 0,
                 "content_index": 0, "sequence_number": 0, "logprobs": []},
                {"type": "response.completed", "response": response, "sequence_number": 1},
            ]
            text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in stream)
            return httpx2.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})
        response["output"] = [{"type": "message", "id": "test-message", "role": "assistant", "status": "completed",
                               "content": [{"type": "output_text", "text": json.dumps(output()), "annotations": []}]}]
        return httpx2.Response(200, json=response)

    sdk = extract.make_client(api_key="nonsecret-test-value", backend=slides.META,
                              http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    c = client(tmp_path, sdk)
    result = events(c.post("/api/slide", json={"image_data_url": image_url()}))
    assert result[-1][0] == "done" and result[-1][1]["counts"]["verified"] == 1
    assert result[-1][1]["cost_is_estimate"] is False and len(requests) == 3
    assert requests[-1]["text"]["format"]["type"] == "json_schema"
    sdk.close()
