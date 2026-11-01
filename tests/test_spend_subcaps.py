"""Daily sub-caps on the existing shared ledger, with no schema change: Slide, Ask and quote search leave the end of
the daily budget to Check and Fix, and new whole-filing questions are counted per UTC day across instances.

Local SQLite and fakes only; no model, SEC or network call.
"""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from receipts import api, ask, recordings, spend, stream
from test_api import parse_sse, settings
from test_ask import AskStore, ProposalClient, plan
from test_grounding import QUOTE, filing_fixture
from test_slides import image_url
from test_spend_usage import TOKENS, client as model_client, response

DAY = "2026-10-28"
QUOTES = "nocap.filing_questions"
TEXT = "NVIDIA revenue was $96.2 billion."


def local(tmp_path, cap, *, namespace="nocap", day=DAY):
    return spend.LocalLedger(cap, tmp_path / "spend.sqlite3", namespace=namespace, today=lambda: day)


def count(tmp_path, limit, *, day=DAY):
    return spend.DailyCount(local(tmp_path, spend.DailyCount.cap(limit), namespace=QUOTES, day=day), limit)


def test_keep_usd_narrows_one_admission_without_changing_the_cap_or_settlement(tmp_path):
    ledger = local(tmp_path, 2.0)
    assert ledger.reserve(.6, keep_usd=1.5) is None              # .60 + 1.50 kept > 2.00
    token = ledger.reserve(.5, keep_usd=1.5)                     # exactly fits
    assert token is not None and ledger.reserve(.01, keep_usd=1.5) is None
    assert ledger.reserve(1.5) is not None                       # Check and Fix may use the kept end
    ledger.settle(token, .2)
    assert ledger.cap_usd == 2.0 and ledger.spent_today() == pytest.approx(1.7)
    assert ledger.reserve(.3) is not None and ledger.reserve(.01) is None
    for bad in (-1, float("nan"), True, "1.5"):
        with pytest.raises(spend.BudgetUnavailable):
            ledger.reserve(.01, keep_usd=bad)


def test_daily_count_is_shared_returns_unused_slots_and_resets_each_utc_day(tmp_path):
    first, second = count(tmp_path, 2), count(tmp_path, 2)       # two instances, one storage
    a, b = first.acquire(), second.acquire()
    assert a is not None and b is not None
    assert first.acquire() is None and second.acquire() is None
    first.release(a)                                             # never dispatched: the slot comes back
    c = second.acquire()
    assert c is not None and first.used_today() == 2
    second.keep(b)
    first.keep(c)
    assert first.acquire() is None and second.used_today() == 2
    assert count(tmp_path, 2, day="2026-10-29").acquire() is not None  # a new UTC day starts at zero
    dollars = local(tmp_path, 5.0)                               # same file, separate namespace
    assert dollars.spent_today() == 0 and dollars.reserve(5.0) is not None


def test_a_changed_limit_fails_closed_for_that_day_instead_of_racing(tmp_path):
    assert count(tmp_path, 5).acquire() is not None
    with pytest.raises(spend.BudgetUnavailable):
        count(tmp_path, 6).acquire()
    assert count(tmp_path, 6, day="2026-10-29").acquire() is not None


class QuotesOrPlan(ProposalClient):
    """Spark for both routes: quotes for a whole-filing request, a lookup plan for Ask."""

    def create(self, **kwargs):
        self.payload = {"quotes": [QUOTE]} if kwargs["text"]["format"]["name"] == "filing_quotes" else plan().model_dump()
        return super().create(**kwargs)


def quote_app(tmp_path, model=None, **overrides):
    """A whole app, so its routes share one ledger, filing quota and rate limiter (the default 10 per window)."""
    row = filing_fixture(tmp_path / "data" / "filing_texts")
    model = model or QuotesOrPlan()
    created = []

    def quote_client():
        created.append(True)
        return model

    app = api.create_app(settings(tmp_path, recording_dirs=(tmp_path / "recordings",), **overrides),
                         store=AskStore(), slide_client_factory=quote_client,
                         client_factory=lambda: model_client(lambda **_: response(NS(**TOKENS))))
    return TestClient(app), row, model, created


def quote(client, row, question):
    return parse_sse(client.post("/api/grounding", json={"question": question, "filing_id": row["id"]}).text)


def test_new_whole_filing_questions_stop_at_the_daily_limit_across_instances(tmp_path):
    first, row, model, _ = quote_app(tmp_path, filing_questions_per_day=2)
    second, _, _, _ = quote_app(tmp_path, model, filing_questions_per_day=2)  # another instance, same storage
    results = [quote(first, row, "What risks?")[-1], quote(second, row, "What drives demand?")[-1]]
    assert [name for name, _ in results] == ["result", "result"]
    spent = first.app.state.ledger.spent_today()
    assert spent == pytest.approx(sum(r["cost_usd"] for _, r in results), abs=2e-6)
    refused = quote(second, row, "What else is at risk?")
    assert len(refused) == 1 and refused[0][0] == "error"
    error = refused[0][1]
    assert error["reason"] == "filing_question_limit" and error["retryable"] is False
    assert error["model_calls"] == 0 and error["cost_usd"] == 0
    assert "2 new whole-filing questions" in error["message"] and "replay at $0" in error["message"]
    assert len(model.calls) == 2 and first.app.state.ledger.spent_today() == spent  # nothing reserved for it
    assert first.app.state.filing_quota.used_today() == 2
    # Ask's lookup questions are not whole-filing questions.
    assert parse_sse(first.post("/api/ask", json={"question": "NVIDIA GAAP revenue Q2 FY2027?"}).text)[-1][0] == "result"
    assert len(model.calls) == 3 and second.app.state.filing_quota.used_today() == 2


def test_a_recorded_sample_replays_at_zero_after_the_limit_without_taking_a_slot(tmp_path):
    c, row, model, created = quote_app(tmp_path, filing_questions_per_day=1)
    question = ask.validate_question("What does the filing say about risk?")
    key = recordings.grounding_key(row["id"], question, stream.canonical_as_of(None))
    path = tmp_path / "recordings" / "grounding" / f"{key}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1, "kind": "grounding", "key": key, "recorded_utc": "2026-10-30T07:20:42Z",
        "request": {"filing_id": row["id"], "question": question, "as_of": None},
        "events": [{"event": "result", "data": {"kind": "filing_quotes", "quotes": [], "cost_usd": 0.13}}]}))
    assert quote(c, row, "A new question?")[-1][0] == "result"
    limited = quote(c, row, "Another new question?")[-1][1]
    assert limited["reason"] == "filing_question_limit" and "today's 1 new whole-filing question." in limited["message"]
    replay = quote(c, row, question)
    assert replay[-1][0] == "result" and replay[-1][1]["cached"] is True
    assert len(model.calls) == 1 and c.app.state.filing_quota.used_today() == 1


def test_a_zero_limit_turns_new_questions_off_but_keeps_ask(tmp_path):
    c, row, model, created = quote_app(tmp_path, filing_questions_per_day=0)
    refused = quote(c, row, "What risks?")
    assert refused[0][1]["reason"] == "filing_question_limit" and "are off on this server" in refused[0][1]["message"]
    assert not model.calls and not created
    assert parse_sse(c.post("/api/ask", json={"question": "NVIDIA GAAP revenue Q2 FY2027?"}).text)[-1][0] == "result"


def test_a_question_refused_before_its_model_call_returns_its_slot(tmp_path):
    # $1.50 cap minus the $1.50 Check/Fix reserve: the question takes a slot, then fails budget admission.
    c, row, model, created = quote_app(tmp_path, filing_questions_per_day=1, daily_budget_usd=1.5)
    refused = quote(c, row, "What risks?")[-1]
    assert refused[0] == "error" and refused[1]["reason"] == "daily_budget"
    assert not model.calls and not created and c.app.state.filing_quota.used_today() == 0


def test_a_dispatched_question_keeps_its_slot_even_when_the_provider_fails(tmp_path):
    c, row, model, _ = quote_app(tmp_path, ProposalClient(error="PRIVATE_PROVIDER_SECRET"), filing_questions_per_day=1)
    failed = quote(c, row, "What risks?")
    assert failed[-1][0] == "error" and len(model.calls) == 1
    assert c.app.state.filing_quota.used_today() == 1          # it may still be billed, so it counts
    assert quote(c, row, "What else?")[-1][1]["reason"] == "filing_question_limit"


def test_slide_ask_and_quotes_leave_the_check_fix_reserve(tmp_path):
    # A $1.54 cap minus the $1.50 Check/Fix reserve leaves $0.04: below every Slide, Ask or quote estimate.
    c, row, model, created = quote_app(tmp_path, daily_budget_usd=1.54)
    for path, body in [("/api/slide", {"image_data_url": image_url()}),
                       ("/api/ask", {"question": "NVIDIA GAAP revenue Q2 FY2027?"}),
                       ("/api/grounding", {"question": "What risks?", "filing_id": row["id"]})]:
        events = parse_sse(c.post(path, json=body).text)
        assert len(events) == 1 and events[0][0] == "error", (path, events)
        assert events[0][1]["reason"] == "daily_budget" and "keeps the rest for Check and Fix" in events[0][1]["message"]
        assert events[0][1]["model_calls"] == 0 and events[0][1]["retryable"] is False
    assert not model.calls and not created and c.app.state.ledger.spent_today() == 0
    assert c.app.state.filing_quota.used_today() == 0
    # Check may use that reserved end of the day.
    done = parse_sse(c.post("/api/check", json={"text": TEXT}).text)[-1]
    assert done[0] == "done" and c.app.state.ledger.spent_today() == pytest.approx(done[1]["cost_usd"], abs=2e-6)


def test_sub_cap_settings_read_from_the_environment():
    config = api.Settings.from_env({"RECEIPTS_CHECK_FIX_RESERVE_USD": "2.25", "RECEIPTS_FILING_QUESTIONS_PER_DAY": "3"})
    assert config.check_fix_reserve_usd == 2.25 and config.filing_questions_per_day == 3
    defaults = api.Settings.from_env({})
    assert defaults.daily_budget_usd == 5.0 and defaults.check_fix_reserve_usd == 1.5
    assert defaults.filing_questions_per_day == 5
    clamped = api.Settings.from_env({"RECEIPTS_CHECK_FIX_RESERVE_USD": "-1", "RECEIPTS_FILING_QUESTIONS_PER_DAY": "-2"})
    assert clamped.check_fix_reserve_usd == 0 and clamped.filing_questions_per_day == 0
