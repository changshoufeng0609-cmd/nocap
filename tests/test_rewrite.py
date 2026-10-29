"""Correction orchestration and precision guards; all model calls are synthetic and offline."""
import json
import time

import pytest

from receipts import extract, rewrite, verify
from test_rewrite_safety import CONTEXT, TEXT, ProposalClient, claims_for, result
from test_verify import FACTS, FakeStore


@pytest.fixture(autouse=True)
def meta_backend(monkeypatch):
    monkeypatch.setattr(extract, "active_backend", lambda: extract.BACKENDS["meta"])


def run_fix(monkeypatch, *, text=TEXT, value="$96.2 billion", store=None, context=CONTEXT,
            deadline=None, cached=True, warnings=()):
    model = ProposalClient([{"claim_id": "c1", "value_text": value}])
    calls = []
    clients = []

    def fake_extract(passage, inferred, **kwargs):
        calls.append((passage, inferred, kwargs))
        yield extract.PassageResult(1, 0, len(passage), claims_for(passage),
                                    {"status": "ok", "cached": cached, "cost_usd": 0 if cached else .002},
                                    warnings=list(warnings))

    def get_client():
        clients.append(True)
        return object()

    monkeypatch.setattr(extract, "iter_extract", fake_extract)
    monkeypatch.setattr(extract, "passages_to_call", lambda *a, **kw: 0 if cached else 1)
    stats = rewrite.RewriteStats()
    events = list(rewrite.fix_events(
        text, store=store or FakeStore(), context=context, stats=stats, deadline=deadline,
        extract_client_factory=get_client, wonder_client_factory=lambda: model,
        cache_dir=None, extra_cache_dirs=(), use_cache=True,
    ))
    return events, stats, model, calls, clients


def test_request_uses_standard_wonder_strict_responses_and_only_trusted_proposals(monkeypatch):
    events, _, model, _, _ = run_fix(monkeypatch)
    assert result(events)["status"] == "corrected"
    request = model.calls[0]
    assert request["model"] == "muse-wonder-1.0"
    assert request["reasoning"] == {"effort": "minimal"}
    assert request["max_output_tokens"] == rewrite.MAX_OUTPUT_TOKENS
    assert request["store"] is False
    assert "response_format" not in request
    schema = request["text"]["format"]
    assert schema["type"] == "json_schema" and schema["strict"] is True
    assert extract.strict_subset_problems(schema["schema"]) == []
    proposal_fields = schema["schema"]["properties"]["edits"]["items"]["properties"]
    assert set(proposal_fields) == {"claim_id", "value_text"}
    correction = json.loads(request["input"])["corrections"][0]
    assert correction["expected_value"] == pytest.approx(96.221)
    assert correction["receipts"][0]["accession"] in {f.accession for f in FACTS}


def test_cached_correct_draft_needs_neither_client(monkeypatch):
    text = TEXT.replace("$95.2 billion", "$96.2 billion")
    events, stats, model, _, clients = run_fix(monkeypatch, text=text)
    payload = result(events)
    assert payload["status"] == "unchanged"
    assert payload["remaining"] == [] and payload["counts"]["verified"] == 1
    assert not clients and not model.calls and stats.cost_usd == 0
    assert stats.complete and stats.model_calls == 0


def test_complete_progress_and_cost_include_only_new_calls(monkeypatch):
    events, stats, model, _, clients = run_fix(monkeypatch)
    assert [(name, data.get("stage")) for name, data in events] == [
        ("progress", "checking"), ("progress", "rewriting"), ("progress", "verifying"), ("result", None),
    ]
    assert len(model.calls) == 1 and not clients
    assert stats.model_calls == 1 and stats.complete
    assert result(events)["cost_usd"] == stats.cost_usd == pytest.approx(.0014)


def test_extraction_keeps_usage_and_bounds_each_call_without_orphan_deadline(monkeypatch):
    events, stats, _, calls, clients = run_fix(monkeypatch, cached=False, deadline=time.monotonic() + 10)
    assert result(events)["status"] == "corrected"
    assert stats.cost_usd == pytest.approx(.0054) and stats.model_calls == 3
    assert len(clients) == 2
    for _, _, options in calls:
        assert options["deadline"] > time.monotonic()
        assert options["concurrency"] == 1 and options["max_attempts"] == 1
        assert 0 < options["timeout_s"] <= 10
        assert options["backend"].min_timeout_s == 0


def test_expired_deadline_calls_no_model(monkeypatch):
    events, stats, model, calls, clients = run_fix(monkeypatch, deadline=time.monotonic() - 1)
    assert events[-1][0] == "error"
    assert not calls and not clients and not model.calls
    assert stats.cost_usd == 0 and not stats.complete


def test_same_inferred_context_is_used_for_both_passes(monkeypatch):
    monkeypatch.setattr(extract, "infer_context", lambda text: CONTEXT if "$95.2" in text else "WRONG")
    events, _, _, calls, _ = run_fix(monkeypatch, context=None)
    assert result(events)["status"] == "corrected"
    assert [c[1] for c in calls] == [CONTEXT, CONTEXT]


@pytest.mark.parametrize("proposed,accepted", [("$0 billion", False), ("$0.001 billion", True)])
def test_small_nonzero_amount_cannot_be_rounded_to_zero(monkeypatch, proposed, accepted):
    # Synthetic revenue of $1 million. Plain '$0 billion' would fit the verifier's
    # half-a-billion rounding tolerance, so correction needs its own precision guard.
    store = FakeStore(facts=[FACTS[0].model_copy(update={"value": 1_000_000.0})])
    events, _, _, _, _ = run_fix(monkeypatch, value=proposed, store=store)
    assert (result(events)["status"] == "corrected") is accepted


def test_genuinely_zero_fact_can_be_corrected_to_zero(monkeypatch):
    store = FakeStore(facts=[FACTS[0].model_copy(update={"value": 0.0})])
    events, _, _, _, _ = run_fix(monkeypatch, value="$0 billion", store=store)
    assert result(events)["status"] == "corrected"


def test_coarse_precision_does_not_create_an_illusory_correction(monkeypatch):
    # The expected $2.46 and '$2' are within a whole-dollar tolerance, but this
    # proposal throws away precision instead of correcting the stated number.
    text = "NVIDIA Q2 FY2027 GAAP diluted EPS was $2.22."
    events, _, _, _, _ = run_fix(monkeypatch, text=text, value="$2")
    assert result(events)["status"] == "unchanged"


@pytest.mark.parametrize("warning", [
    "dropped a claim the text does not support", "the quote for a value was not verbatim",
])
def test_damaged_extraction_cannot_be_used_as_a_rewrite_baseline(monkeypatch, warning):
    events, _, model, _, _ = run_fix(monkeypatch, warnings=[warning])
    assert events[-1][0] == "error" and not model.calls


def test_provider_error_never_exposes_raw_message(monkeypatch):
    def failure(self, **kwargs):
        raise RuntimeError("private-provider-secret and raw draft")

    monkeypatch.setattr(ProposalClient, "create", failure)
    events, stats, _, _, _ = run_fix(monkeypatch)
    assert events[-1][0] == "error"
    assert "private-provider-secret" not in json.dumps(events)
    assert "raw draft" not in json.dumps(events)
    assert not stats.complete


@pytest.mark.parametrize("before,after,value", [
    ("$2.22", "$2.46", 2.46), ("18%", "105.85%", 105.85),
    ("$12.99 million", "$12,988 million", 12988),
    ("-$2.22", "-$2.46", -2.46), ("$(2.22)", "$(2.46)", -2.46),
    ("$2.22", "-$2.46", None), ("18%", "105.85 percent", None),
    ("$2.22", "$2.46\n", None), ("$2.22", "$2.46 per share", None),
    # Source-complete values keep their written sign or enclosing parentheses unchanged.
    ("+14%", "+15%", 15), ("+14%", "-15%", None), ("14%", "+15%", None),
    ("($2.22 billion)", "($2.46 billion)", -2.46), ("–$2.22", "–$2.46", -2.46),
])
def test_only_digit_characters_can_change(before, after, value):
    assert rewrite.numeric_replacement(before, after) == value


def test_bounded_candidate_batch_leaves_omitted_errors_visible(monkeypatch):
    text = " ".join([TEXT] * (rewrite.MAX_CANDIDATES + 2))
    original_create = ProposalClient.create

    def offer_every_claim(self, **kwargs):
        # An overeager model even guesses IDs absent from the bounded input.
        self.edits = [{"claim_id": f"c{i}", "value_text": "$96.2 billion"}
                      for i in range(1, rewrite.MAX_CANDIDATES + 3)]
        return original_create(self, **kwargs)

    monkeypatch.setattr(ProposalClient, "create", offer_every_claim)
    events, _, model, _, _ = run_fix(monkeypatch, text=text)
    body = result(events)
    request = model.calls[0]
    requested = json.loads(request["input"])["corrections"]
    assert 0 < len(requested) <= rewrite.MAX_CANDIDATES
    assert body["status"] == "partial"
    assert len(body["changes"]) == len(requested)
    assert len(body["remaining"]) == rewrite.MAX_CANDIDATES + 2 - len(requested)
    assert body["counts"]["wrong"] == len(body["remaining"])
    assert len(json.dumps({k: v for k, v in request.items() if k != "timeout"},
                          ensure_ascii=False).encode("utf-8")) <= rewrite.MAX_REQUEST_BYTES


def test_utf8_request_byte_cap_skips_oversized_evidence_without_truncating_it(monkeypatch):
    text = TEXT + " " + TEXT
    claims = claims_for(text)
    rows = [(c, verify.verify(c, FakeStore(), context=CONTEXT)) for c in claims]
    candidates = rewrite._eligible(text, rows, None)
    claim, verdict, slot = candidates["c1"]
    enormous = verdict.evidence[0].model_copy(update={"locator": "證據" * 20_000})
    candidates["c1"] = claim, verdict.model_copy(update={"evidence": [enormous]}), slot
    request = rewrite.build_request(text, candidates, context=CONTEXT, as_of=None)
    offered = json.loads(request["input"])["corrections"]
    assert [e["claim_id"] for e in offered] == ["c2"]
    assert len(json.dumps(request, ensure_ascii=False).encode("utf-8")) <= rewrite.MAX_REQUEST_BYTES


def test_oversized_base_draft_is_rejected_before_calling_wonder():
    with pytest.raises(rewrite.RewriteError, match="too large"):
        rewrite.build_request("證據" * 20_000, {}, context=CONTEXT, as_of=None)


def test_no_candidate_fits_the_request_cap_means_no_wonder_call(monkeypatch):
    base_request = rewrite.build_request(TEXT, {}, context=CONTEXT, as_of=None)
    monkeypatch.setattr(rewrite, "MAX_REQUEST_BYTES",
                        len(json.dumps(base_request, ensure_ascii=False).encode("utf-8")) + 10)
    events, _, model, _, _ = run_fix(monkeypatch)
    body = result(events)
    assert body["status"] == "unchanged" and len(body["remaining"]) == 1
    assert not model.calls
