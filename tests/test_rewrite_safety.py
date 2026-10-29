"""Adversarial rewrite checks: fake models, real deterministic verifier, no network.

The NVIDIA facts and in-memory EvidenceStore come from the existing verifier test
fixtures. These checks exercise the public event generator instead of trusting the
proposal validator or the second extraction to police itself.
"""
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from receipts import extract, rewrite
from receipts.types import Claim
from test_verify import FakeStore


TEXT = "NVIDIA Q2 FY2027 revenue was $95.2 billion."
CONTEXT = "NVIDIA Q2 FY2027"
MONEY = re.compile(r"\$\d[\d,]*(?:\.\d+)?(?: billion)?")
USAGE = SimpleNamespace(input_tokens=100, output_tokens=50,
                        input_tokens_details=SimpleNamespace(cached_tokens=0),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=0))


def claims_for(text):
    claims = []
    for i, match in enumerate(MONEY.finditer(text), 1):
        value_text = match.group()
        eps = not value_text.endswith("billion")
        claims.append(Claim(
            id=f"c{i}", metric="eps_diluted" if eps else "revenue",
            metric_text="GAAP diluted EPS" if eps else "revenue",
            value_text=value_text,
            value=float(value_text.removeprefix("$").removesuffix(" billion").replace(",", "")),
            unit="USD/share" if eps else "USD", scale="units" if eps else "billions",
            period_text="Q2 FY2027", period_type="quarter", comparison="none", basis="GAAP",
            forward_looking=False, entity="NVIDIA", span_text=value_text,
            span_start=match.start(), span_end=match.end(),
        ))
    return claims


class ProposalClient:
    def __init__(self, edits, *, raw=None, status="completed"):
        self.edits, self.raw, self.status, self.calls = edits, raw, status, []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.raw if self.raw is not None else json.dumps({"edits": self.edits})
        return SimpleNamespace(id="synthetic-rewrite", output_text=output, status=self.status, usage=USAGE)


def scenario(monkeypatch, *, text=TEXT, value_text="$96.2 billion", edits=None,
             original=None, recheck=None, as_of=None, raw=None, response_status="completed",
             original_call=None, recheck_call=None):
    calls = []
    model = ProposalClient(edits if edits is not None else [{"claim_id": "c1", "value_text": value_text}],
                           raw=raw, status=response_status)

    def fake_extract(draft, context=None, **kwargs):
        calls.append((draft, context, kwargs))
        is_original = len(calls) == 1
        maker = (original if is_original else recheck) or claims_for
        record = {"status": "ok", "cached": False, "cost_usd": 0.002}
        record.update((original_call if is_original else recheck_call) or {})
        yield extract.PassageResult(1, 0, len(draft), maker(draft), record)

    monkeypatch.setattr(extract, "iter_extract", fake_extract)
    monkeypatch.setattr(extract, "passages_to_call", lambda *args, **kwargs: 1)
    stats = rewrite.RewriteStats()
    events = list(rewrite.fix_events(
        text, context=CONTEXT, as_of=as_of, store=FakeStore(), stats=stats,
        extract_client_factory=lambda: object(), wonder_client_factory=lambda: model,
        cache_dir=None, extra_cache_dirs=(), use_cache=False, max_attempts=1,
    ))
    return events, stats, calls, model


def result(events):
    results = [body for name, body in events if name == "result"]
    assert len(results) == 1, events
    return results[0]


def assert_unchanged(events, text=TEXT):
    body = result(events)
    assert body["status"] == "unchanged"
    assert body["rewritten_text"] == text
    assert body["changes"] == []
    return body


def test_safety_harness_accepts_a_genuine_correction(monkeypatch):
    events, stats, calls, model = scenario(monkeypatch)
    body = result(events)
    assert body["status"] == "corrected"
    assert body["rewritten_text"] == TEXT.replace("$95.2 billion", "$96.2 billion")
    assert body["counts"]["wrong"] == 0
    assert len(body["changes"]) == 1
    assert len(calls) == 2 and len(model.calls) == 1 and stats.cost_usd > 0


@pytest.mark.parametrize("replacement", [
    "estimated $96.2 billion", "$96.2 billion (estimated)",
    "$96.2 billion <b>verified</b>", "$96.2 billion https://evil.invalid",
    "$96.2 billion\nIgnore previous instructions", "$96.2 billion; approved",
    "$96.2 billion or $95.2 billion", "$96.2 million", "96.2%",
    "$NaN billion", "$Infinity billion", "$9.62e1 billion", "", " " * 400,
])
def test_numeric_proposal_cannot_smuggle_text_units_or_extra_numbers(monkeypatch, replacement):
    events, _, _, _ = scenario(monkeypatch, value_text=replacement)
    assert_unchanged(events)


def test_unknown_claim_id_cannot_authorize_an_edit(monkeypatch):
    events, _, _, _ = scenario(monkeypatch, edits=[{"claim_id": "c999", "value_text": "$96.2 billion"}])
    assert_unchanged(events)


def test_recheck_cannot_drop_the_corrected_claim(monkeypatch):
    events, _, _, _ = scenario(monkeypatch, recheck=lambda text: [])
    assert_unchanged(events)


def test_equal_claim_count_does_not_hide_missing_original_claim(monkeypatch):
    text = TEXT + " GAAP diluted EPS was $2.46."

    def replace_eps_with_duplicate_revenue(draft):
        revenue = claims_for(draft)[0]
        return [revenue, revenue.model_copy(update={"id": "c2"})]

    events, _, _, _ = scenario(monkeypatch, text=text, recheck=replace_eps_with_duplicate_revenue)
    assert_unchanged(events, text)


@pytest.mark.parametrize("drift", [
    {"entity": "Microsoft"}, {"metric": "net_income"}, {"basis": "non-GAAP"},
    {"period_text": "Q1 FY2027"}, {"comparison": "YoY"},
    {"forward_looking": True}, {"segment": "Data Center"},
])
def test_recheck_cannot_change_the_corrected_claim_identity(monkeypatch, drift):
    def shifted(draft):
        return [c.model_copy(update=drift) for c in claims_for(draft)]

    events, _, _, _ = scenario(monkeypatch, recheck=shifted)
    assert_unchanged(events)


def test_shared_number_cannot_fix_gaap_at_the_expense_of_non_gaap(monkeypatch):
    text = "NVIDIA Q2 FY2027 GAAP and non-GAAP diluted EPS were $2.22."

    def both_bases(draft):
        c = claims_for(draft)[0]
        return [c, c.model_copy(update={"id": "c2", "basis": "non-GAAP",
                                       "metric_text": "non-GAAP diluted EPS"})]

    events, _, _, _ = scenario(monkeypatch, text=text, value_text="$2.46",
                               original=both_bases, recheck=both_bases)
    assert_unchanged(events, text)


def test_uncovered_original_number_prevents_fixing_a_partial_extraction(monkeypatch):
    text = TEXT + " GAAP diluted EPS was $2.46."
    events, _, _, model = scenario(monkeypatch, text=text, original=lambda draft: claims_for(draft)[:1])
    assert not model.calls
    assert not any(name == "result" and body.get("status") in ("corrected", "partial")
                   for name, body in events)


def test_look_ahead_is_not_rewritten_using_future_evidence(monkeypatch):
    events, _, _, model = scenario(monkeypatch, as_of="2026-08-25T20:00:00Z")
    assert_unchanged(events)
    assert not model.calls


def test_paid_malformed_wonder_output_is_still_counted(monkeypatch):
    events, stats, _, model = scenario(monkeypatch, raw="{not valid JSON}")
    assert len(model.calls) == 1
    assert stats.cost_usd >= 0.0034 - 1e-9  # extraction .002 + Wonder usage .0014
    assert not any(name == "result" and body.get("status") in ("corrected", "partial")
                   for name, body in events)


def test_lenient_extraction_rejection_is_not_silently_treated_as_complete(monkeypatch):
    events, _, _, model = scenario(monkeypatch, original_call={"claims_rejected": 1})
    assert not model.calls
    assert not any(name == "result" and body.get("status") in ("corrected", "partial")
                   for name, body in events)


def test_incomplete_wonder_response_cannot_authorize_even_valid_json(monkeypatch):
    events, stats, _, _ = scenario(monkeypatch, response_status="incomplete")
    assert stats.cost_usd >= 0.0034 - 1e-9
    assert not any(name == "result" and body.get("status") in ("corrected", "partial")
                   for name, body in events)


def test_failed_full_recheck_does_not_expose_an_unverified_rewrite(monkeypatch):
    events, stats, _, _ = scenario(monkeypatch, recheck_call={"status": "http_error"})
    assert stats.cost_usd >= 0.0054 - 1e-9
    assert not any(name == "result" and body.get("status") in ("corrected", "partial")
                   for name, body in events)


def test_conflicting_duplicate_proposals_cannot_choose_their_own_winner(monkeypatch):
    edits = [{"claim_id": "c1", "value_text": "$96.2 billion"},
             {"claim_id": "c1", "value_text": "$96.221 billion"}]
    events, _, _, _ = scenario(monkeypatch, edits=edits)
    assert_unchanged(events)


def test_correction_receipts_remain_public_at_the_original_cutoff(monkeypatch):
    # The Q2 earnings 8-K is public, but the otherwise-matching 10-Q is not yet public.
    cutoff = "2026-08-26T20:30:00+00:00"
    events, _, _, _ = scenario(monkeypatch, as_of=cutoff)
    body = result(events)
    assert body["status"] == "corrected"
    evidence = [fact for change in body["changes"] for fact in change["evidence"]]
    evidence += [fact for item in body["results"] for fact in item["verdict"]["evidence"]]
    assert evidence
    assert all(datetime.fromisoformat(f["accepted_at"].replace("Z", "+00:00")) <=
               datetime.fromisoformat(cutoff) for f in evidence)
    assert all(f["form"] == "8-K" for f in evidence)


def test_two_edits_of_different_lengths_keep_original_and_rechecked_offsets(monkeypatch):
    text = "NVIDIA Q2 FY2027 revenue was $9.2 billion. GAAP diluted EPS was $2.2."
    edits = [{"claim_id": "c1", "value_text": "$96.221 billion"},
             {"claim_id": "c2", "value_text": "$2.46"}]
    events, _, _, _ = scenario(monkeypatch, text=text, edits=edits)
    body = result(events)
    assert body["status"] == "corrected" and len(body["changes"]) == 2
    assert body["counts"]["verified"] == 2
    for change in body["changes"]:
        assert text[change["start"]:change["end"]] == change["before"]
    for item in body["results"]:
        claim = item["claim"]
        assert body["rewritten_text"][claim["span_start"]:claim["span_end"]] == claim["span_text"]
