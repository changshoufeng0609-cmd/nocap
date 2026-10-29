"""Slide trust boundaries and deterministic verification. All model calls are mocked."""
import base64
import io
import json
import time
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from receipts import extract, slides
from test_verify import FakeStore


def image_url(size=(100, 100), fmt="PNG", **save):
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format=fmt, **save)
    return f"data:image/{'jpeg' if fmt == 'JPEG' else fmt.lower()};base64," + base64.b64encode(out.getvalue()).decode()


def reading(**changes):
    value = {"metric": "revenue", "metric_text": "revenue", "value_text": "$96.2 billion", "value": 96.2,
             "unit": "USD", "scale": "billions", "period_text": "Q2 FY2027", "period_type": "quarter",
             "comparison": "none", "bases": ["GAAP"], "forward_looking": False, "entity": "NVIDIA",
             "segment": None, "span_text": "NVIDIA Q2 FY2027 revenue $96.2 billion", "box": [200, 300, 400, 400],
             "reading": "printed", "uncertain": False}
    value.update(changes)
    return value


def output(*claims):
    return {"regions": [{"label": "Revenue", "box": [0, 0, 1000, 1000], "claims": list(claims or [reading()])}]}


class Stream(list):
    closed = False

    def close(self):
        self.closed = True


class Model:
    def __init__(self, body=None, sam="box", spark_error=None, status="completed", usage=True):
        self.body = body if body is not None else output()
        self.sam, self.spark_error, self.status, self.usage = sam, spark_error, status, usage
        self.requests, self.streams = [], []
        self.responses = self

    def create(self, **request):
        self.requests.append(request)
        if request["model"] == slides.SAM_MODEL:
            if self.sam == "fail":
                raise RuntimeError("PRIVATE_PROVIDER_SECRET")
            text = ("<0f>0<|box;x1=10;y1=10;x2=90;y2=90;w=100;h=100;c=0.9|>"
                    "<|mask;x=0;y=0;data=81,81,!opaque|>") if self.sam == "box" else ""
            stream = Stream([NS(type="response.output_text.delta", delta=text)])
            if self.sam != "truncated":
                stream.append(NS(type="response.completed", response=NS(status="completed", usage=NS(images_processed=1))))
            self.streams.append(stream)
            return stream
        if self.spark_error:
            raise RuntimeError(self.spark_error)
        return NS(status=self.status, output_text=json.dumps(self.body),
                  usage=NS(input_tokens=100, output_tokens=200, input_tokens_details=None,
                           output_tokens_details=None) if self.usage else None)


def run(model=None, **kwargs):
    model = model or Model()
    stats = slides.SlideStats()
    events = list(slides.slide_events(slides.decode_image(image_url()), store=FakeStore(), stats=stats,
                                     client_factory=lambda: model, **kwargs))
    return events, stats, model


def test_image_orientation_metadata_and_size_are_normalized():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private description"
    image = slides.decode_image(image_url((80, 40), "JPEG", exif=exif))
    assert (image.width, image.height) == (40, 80)
    raw = base64.b64decode(image.data_url.split(",")[1])
    decoded = Image.open(io.BytesIO(raw))
    assert decoded.size == (40, 80) and not decoded.getexif()
    assert b"private description" not in raw
    image = slides.decode_image(image_url((4000, 1000)))
    assert (image.width, image.height) == (2048, 512)


@pytest.mark.parametrize("url", [
    "https://attacker.example/image.png", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,%%%%",
    "data:image/png;base64,AA==", image_url((10, 100)), image_url((4097, 32)),
    image_url((3000, 3000)), image_url(fmt="GIF").replace("image/gif", "image/png"),
    "data:image/png;base64," + base64.b64encode(b"x" * (slides.MAX_IMAGE_BYTES + 1)).decode(),
])
def test_image_rejects_urls_corruption_spoofed_types_and_oversized_decodes(url):
    with pytest.raises(slides.SlideError):
        slides.decode_image(url)


def test_model_schema_uses_supported_meta_strict_subset():
    assert extract.strict_subset_problems(extract.build_json_schema(slides.SlideResponse)) == []


def test_printed_label_reuses_verifier_and_only_filing_receipts():
    events, stats, model = run()
    assert events[0][0] == "meta" and events[-1][0] == "done"
    claim = next(data for name, data in events if name == "claim")
    verdict = next(data for name, data in events if name == "verdict")
    assert claim["region_id"] == "r1" and claim["reading"] == "printed"
    assert verdict["status"] == "verified" and verdict["evidence"]
    assert all(f["url"].startswith("https://www.sec.gov/") for f in verdict["evidence"])
    assert stats.complete and stats.model_calls == 3 and stats.estimated_calls == 0
    assert stats.cost_usd == pytest.approx(0.005 + 100 * 1.25 / 1e6 + 200 * 4.25 / 1e6)
    assert all(stream.closed for stream in model.streams)
    assert [r["model"] for r in model.requests] == ["sam-3.1", "sam-3.1", "muse-spark-1.3"]
    assert [r["input"][0]["content"][0]["text"] for r in model.requests[:2]] == ["bar chart", "table"]
    assert all(r["store"] is False for r in model.requests)
    assert model.requests[0]["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"}
    assert model.requests[-1]["reasoning"] == {"effort": "minimal"}
    assert model.requests[-1]["max_output_tokens"] == slides.MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("changes,status,reason", [
    ({"value": 80, "value_text": "$80 billion", "span_text": "Revenue $80 billion"}, "wrong", "value_mismatch"),
    ({"uncertain": True}, "unverifiable", "ambiguous"),
    ({"forward_looking": True}, "unverifiable", "forward_looking"),
    ({"entity": "Unknown Corporation"}, "unverifiable", "no_evidence"),
])
def test_numeric_errors_uncertainty_guidance_and_unsupported_entities(changes, status, reason):
    events, _, _ = run(Model(output(reading(**changes))))
    verdict = next(data for name, data in events if name == "verdict")
    assert verdict["status"] == status and verdict["reason"] == reason


@pytest.mark.parametrize("value", [96.2, 80])
def test_axis_estimates_cannot_earn_verified_or_wrong(value):
    body = output(reading(reading="axis_estimate", value=value, value_text=f"${value} billion",
                          span_text=f"Revenue ${value} billion"))
    events, _, _ = run(Model(body))
    verdict = next(data for name, data in events if name == "verdict")
    assert verdict["status"] == ("approximate" if value == 96.2 else "unverifiable")


@pytest.mark.parametrize("kind,status", [("printed", "wrong"), ("axis_estimate", "unverifiable")])
def test_as_of_uses_evidence_acceptance_time(kind, status):
    events, _, _ = run(Model(output(reading(reading=kind))), as_of="2026-08-26T20:00:00Z")
    verdict = next(data for name, data in events if name == "verdict")
    assert verdict["status"] == status and verdict["reason"] == "look_ahead"


def test_code_reads_numeric_value_from_label_and_expands_bases():
    events, _, _ = run(Model(output(reading(value=1, bases=["GAAP", "non-GAAP", "GAAP"]))))
    claims = [data for name, data in events if name == "claim"]
    assert len(claims) == 2 and all(c["value"] == 96.2 for c in claims)
    assert [c["id"] for c in claims] == ["c1", "c2"]


@pytest.mark.parametrize("value_text,span", [
    ("96.2", "NVIDIA Q2 FY2027 revenue 196.2 billion"),
    ("96.2", "NVIDIA Q2 FY2027 revenue -96.2 billion"),
    ("96.2", "NVIDIA Q2 FY2027 revenue (96.2 billion)"),
    ("96.2", "NVIDIA revenue 96.2 billion and adjusted revenue 96.2 billion"),
])
def test_partial_numeric_tokens_sign_conflicts_and_duplicate_anchors_are_rejected(value_text, span):
    events, _, _ = run(Model(output(reading(value_text=value_text, span_text=span))))
    assert events[-1][0] == "error" and not any(n == "verdict" for n, _ in events)


def test_scale_in_full_transcribed_token_overrules_model_scale():
    events, _, _ = run(Model(output(reading(value_text="96.2", scale="millions"))))
    claim = next(d for n, d in events if n == "claim")
    assert claim["scale"] == "billions"


def test_scaling_a_finite_number_must_not_overflow_into_a_verified_infinity():
    digits = "9" * 300
    events, _, _ = run(Model(output(reading(value=1.0, value_text=digits,
                                          span_text=f"NVIDIA Q2 FY2027 revenue {digits} billion"))))
    assert events[-1][0] == "error" and not any(n == "verdict" for n, _ in events)


@pytest.mark.parametrize("fields", [
    {"metric": "eps_diluted", "metric_text": "GAAP diluted EPS", "unit": "USD/share", "value": 2.46,
     "value_text": "$2.46 million", "span_text": "NVIDIA Q2 FY2027 GAAP diluted EPS $2.46 million"},
    {"metric": "gross_margin", "metric_text": "GAAP gross margin", "unit": "%", "value": 75.0,
     "value_text": "75.0 million percent", "span_text": "NVIDIA Q2 FY2027 GAAP gross margin 75.0 million percent"},
])
def test_percent_and_per_share_claims_cannot_silently_ignore_scale(fields):
    events, _, _ = run(Model(output(reading(scale="millions", **fields))))
    assert events[-1][0] == "error" and not any(n == "verdict" for n, _ in events)


@pytest.mark.parametrize("label,bases", [("non-GAAP", ["GAAP"]), ("adjusted", ["GAAP"]),
                                        ("GAAP", ["non-GAAP"])])
def test_transcribed_basis_cannot_be_overridden_by_model(label, bases):
    body = output(reading(metric="eps_diluted", metric_text=f"{label} diluted EPS", value=2.46,
                          value_text="$2.46", unit="USD/share", scale="units", bases=bases,
                          span_text=f"NVIDIA Q2 FY2027 {label} diluted EPS $2.46"))
    events, _, _ = run(Model(body))
    verdict = next(d for n, d in events if n == "verdict")
    assert verdict["status"] == "unverifiable" and verdict["reason"] == "ambiguous"


def test_explicitly_shared_gaap_and_non_gaap_label_can_expand():
    events, _, _ = run(Model(output(reading(metric_text="GAAP and non-GAAP revenue", bases=["GAAP", "non-GAAP"]))))
    assert sum(n == "claim" for n, _ in events) == 2
    assert next(d for n, d in events if n == "verdict")["status"] == "verified"


def test_conflicting_basis_fields_are_not_mistaken_for_shared_basis():
    body = output(reading(metric="eps_diluted", metric_text="GAAP diluted EPS", value=2.46,
                          value_text="$2.46", unit="USD/share", scale="units", bases=["GAAP"],
                          span_text="NVIDIA Q2 FY2027 non-GAAP diluted EPS $2.46"))
    events, _, _ = run(Model(body))
    verdict = next(d for n, d in events if n == "verdict")
    assert verdict["status"] == "unverifiable" and verdict["reason"] == "ambiguous"


@pytest.mark.parametrize("sam", ["empty", "fail", "truncated"])
def test_sam_empty_failure_and_truncated_stream_fall_back_without_losing_cost(sam):
    events, stats, model = run(Model(sam=sam))
    regions = [data for name, data in events if name == "region"]
    assert events[-1][0] == "done" and regions[0]["source"] == "spark"
    assert stats.cost_usd >= 0.005 and stats.model_calls == 3
    assert stats.estimated_calls == (0 if sam == "empty" else 2)
    assert "PRIVATE_PROVIDER_SECRET" not in json.dumps(events)
    assert all(stream.closed for stream in model.streams)


@pytest.mark.parametrize("body", [
    {"regions": [], "verdict": "verified"},
    output(reading(box=[500, 400, 200, 300])),
    output(reading(value_text="$96.2 and $90", span_text="Revenue $96.2 and $90")),
    output(reading(value_text="$96.2 billion", span_text="no numeric label")),
    output(*[reading() for _ in range(33)]),
    output(reading(), reading(box=[0, 1, 2])),
])
def test_bad_or_oversized_model_result_emits_no_partial_verdicts(body):
    events, stats, _ = run(Model(body))
    assert events[-1][0] == "error" and not stats.complete
    assert not any(name in ("claim", "verdict", "done") for name, _ in events)
    assert stats.cost_usd > 0


def test_provider_failure_preserves_estimate_and_redacts_error():
    events, stats, _ = run(Model(spark_error="PRIVATE_IMAGE_OR_KEY"))
    assert events[-1][0] == "error" and stats.estimated_calls == 1
    assert stats.cost_usd == pytest.approx(0.105)
    assert "PRIVATE_IMAGE_OR_KEY" not in json.dumps(events)


def test_incomplete_response_keeps_usage_but_rejects_result():
    events, stats, _ = run(Model(status="incomplete"))
    assert events[-1][0] == "error" and stats.estimated_calls == 0 and stats.cost_usd > 0


def test_deadline_prevents_any_model_calls():
    events, stats, model = run(deadline=time.monotonic() - 1)
    assert events[-1][0] == "error" and stats.cost_usd == 0 and model.requests == []


def test_sam_continuous_chunks_cannot_ignore_elapsed_deadline(monkeypatch):
    ticks = iter([100, 116])
    monkeypatch.setattr(slides.time, "monotonic", lambda: next(ticks))
    model, stats = Model(), slides.SlideStats()
    with pytest.raises(slides.SlideError, match="time"):
        slides._sam(model, slides.SlideImage("data:image/png;base64,AA==", 100, 100), "chart", stats, timeout_s=15)
    assert model.streams[0].closed and stats.cost_usd == pytest.approx(0.0025)


def test_no_financial_labels_is_honest_empty_result():
    events, stats, _ = run(Model({"regions": []}, sam="empty"))
    assert stats.complete and events[-1][0] == "done" and events[-1][1]["claims"] == 0
    assert "No legible" in events[-1][1]["warnings"][-1]


def test_sam_pixel_coordinates_are_checked_padded_and_merged():
    image = slides.SlideImage("", 1000, 1000)
    text = "<0f>" + ",".join(f"{i}<|box;x1={x};y1=300;x2={x+40};y2=800;w=1000;h=1000;c=0.9|>"
                           "<|mask;x=0;y=0;data=501,41,!opaque|>" for i, x in enumerate([100, 200, 300]))
    boxes = slides.sam_boxes(text, image)
    assert boxes == [[75, 265, 366, 836]]
    assert slides.sam_boxes(text.replace("w=1000", "w=999"), image) == []
    assert slides.sam_boxes(text.replace("c=0.9", "c=NaN"), image) == []


def test_meta_client_is_independent_of_extraction_backend(monkeypatch):
    selected = []
    monkeypatch.setenv("RECEIPTS_EXTRACT_BACKEND", "fireworks")
    model = Model()
    monkeypatch.setattr(extract, "make_client", lambda **kw: selected.append(kw["backend"]) or model)
    events = list(slides.slide_events(slides.decode_image(image_url()), store=FakeStore()))
    assert events[-1][0] == "done" and selected[0].name == "meta" and selected[0].model == slides.SPARK_MODEL
