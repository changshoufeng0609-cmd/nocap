"""`counts` buckets (receipts.types.count_key): a ◷ look-ahead result is not_public, never wrong, in Check, Fix,
Slide and Private, as the web UI and the CLI already show it. Local fakes only; no model or network call."""
import pytest

from receipts import extract, stream
from receipts.types import COUNT_KEYS, Verdict, count_key, verdict_counts
from test_rewrite_safety import TEXT as FIX_TEXT, claims_for, result, scenario
from test_slides import Model, output, reading, run
from test_verify import FakeStore


def verdict(status, reason):
    return Verdict(claim_id="c1", status=status, reason=reason, explanation="x.")


def test_count_buckets_follow_the_displayed_status():
    assert COUNT_KEYS == ("verified", "wrong", "not_public", "unverifiable", "approximate")
    assert count_key(verdict("wrong", "look_ahead")) == "not_public"
    assert count_key(verdict("unverifiable", "look_ahead")) == "not_public"  # e.g. a look-ahead axis estimate
    assert count_key(verdict("wrong", "value_mismatch")) == "wrong"
    assert verdict_counts([verdict("wrong", "look_ahead"), verdict("wrong", "scale_mismatch"),
                           verdict("verified", "match")]) == {
        "verified": 1, "wrong": 1, "not_public": 1, "unverifiable": 0, "approximate": 0}


def test_check_done_counts_a_look_ahead_as_not_public(monkeypatch):
    [claim] = claims_for(FIX_TEXT)

    def one_claim(text, context, **_):
        yield extract.PassageResult(1, 0, len(text), [claim], {"status": "ok", "cached": True, "cost_usd": 0.0})

    monkeypatch.setattr(extract, "iter_extract", one_claim)
    monkeypatch.setattr(stream, "verdict_for", lambda *args, **kwargs: verdict("wrong", "look_ahead"))
    stats = stream.CheckStats()
    events = list(stream.check_events(FIX_TEXT, draft_hash="0" * 64, as_of="2026-08-25T20:00:00Z",
                                      context="NVIDIA Q2 FY2027", store=FakeStore(), stats=stats))
    done = events[-1]
    assert done[0] == "done" and done[1]["counts"] == {
        "verified": 0, "wrong": 0, "not_public": 1, "unverifiable": 0, "approximate": 0}
    assert stats.claims == 1 and stats.counts["wrong"] == 0


@pytest.mark.parametrize("kind,status", [("printed", "wrong"), ("axis_estimate", "unverifiable")])
def test_slide_done_counts_a_look_ahead_as_not_public(kind, status):
    events, _, _ = run(Model(output(reading(reading=kind))), as_of="2026-08-26T20:00:00Z")
    verdicts = [data for name, data in events if name == "verdict"]
    assert [(v["status"], v["reason"]) for v in verdicts] == [(status, "look_ahead")]
    done = next(data for name, data in events if name == "done")
    assert done["counts"]["not_public"] == 1 and done["counts"][status] == 0 and done["claims"] == 1


def test_fix_result_counts_a_look_ahead_as_not_public(monkeypatch):
    events, _, _, _ = scenario(monkeypatch, as_of="2026-08-25T20:00:00Z")
    body = result(events)
    assert body["status"] == "unchanged" and body["rewritten_text"] == FIX_TEXT
    assert body["counts"]["not_public"] == 1 and body["counts"]["wrong"] == 0
    # It stays listed for manual review: a look-ahead error is never rewritten from later evidence.
    assert [r["reason"] for r in body["remaining"]] == ["look_ahead"]
