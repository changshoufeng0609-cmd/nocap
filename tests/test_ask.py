"""Financial Q&A lookup boundaries; provider responses are synthetic, never live."""
import json
import time
import threading
from types import SimpleNamespace

import pytest

from receipts import ask, extract
from receipts.types import Verdict
from test_verify import FACTS, FakeStore, utc


class AskStore(FakeStore):
    def _rows(self, *_):
        return [{"cik": 1045810, "ticker": "NVDA", "name": "NVIDIA"}]


class ProposalClient:
    def __init__(self, payload=None, *, usage=True, error=None):
        self.payload = payload if payload is not None else plan().model_dump()
        self.usage, self.error, self.calls = usage, error, []
        self.responses = self

    def with_options(self, **_):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise RuntimeError(self.error)
        return SimpleNamespace(status="completed", output_text=json.dumps(self.payload),
                               usage=({"input_tokens": 100, "output_tokens": 50} if self.usage else None))


def plan(**changes):
    data = dict(entity="NVIDIA", metrics=["revenue"], fiscal_year=2027, fiscal_period="Q2",
                basis="GAAP", segment=None, unsupported=False)
    return ask.LookupPlan(**{**data, **changes})


def test_fact_answer_passes_existing_verifier_and_deduplicates_footnotes():
    result = ask.answer_plan(plan(metrics=["revenue", "revenue", "eps_diluted"]), store=AskStore())
    assert len(result["items"]) == 2
    assert all(row["verdict"]["status"] == "verified" and not row["withheld"] for row in result["items"])
    assert "$96221000000" in result["answer"] and "$2.46" in result["answer"]
    assert len(result["footnotes"]) == len({(r["accession"], r["url"]) for r in result["footnotes"]})


@pytest.mark.parametrize("changes", [{"entity": "Unknown Corp"}, {"fiscal_year": None},
    {"fiscal_period": None}, {"unsupported": True}, {"metrics": ["other"]}, {"metrics": []}])
def test_missing_or_unsupported_question_never_invents_answer(changes):
    result = ask.answer_plan(plan(**changes), store=AskStore())
    assert result["answer"] == "" and not result["items"] and result["notice"]


def test_ambiguous_basis_requires_clarification():
    result = ask.answer_plan(plan(metrics=["eps_diluted"], basis=None), store=AskStore())
    assert not result["answer"] and "GAAP or non-GAAP" in result["notice"]


def test_future_filing_is_not_used_for_historical_answer():
    result = ask.answer_plan(plan(), store=AskStore(), as_of="2026-08-26T20:00:00Z")
    assert not result["answer"] and not result["footnotes"]
    assert "No eligible evidence" in result["notice"]


def test_latest_visible_version_not_future_restatement():
    old = FACTS[0].model_copy(update={"accepted_at": utc("2026-08-27T00:00:00Z"), "value": 111.0})
    newer = old.model_copy(update={"accepted_at": utc("2026-09-01T00:00:00Z"), "value": 222.0})
    store = AskStore(facts=[old, newer])
    result = ask.answer_plan(plan(), store=store, as_of="2026-08-28T00:00:00Z")
    assert "$111" in result["answer"] and "$222" not in result["answer"]


def test_equal_timestamp_conflicting_values_are_ambiguous():
    changed = FACTS[0].model_copy(update={"value": 1.0})
    result = ask.answer_plan(plan(), store=AskStore(facts=[FACTS[0], changed]))
    assert not result["answer"] and "Conflicting" in result["notice"]


def test_nonverified_sentences_are_withheld_even_when_lookup_returns_a_fact(monkeypatch):
    monkeypatch.setattr(ask.verify, "verify", lambda claim, *_args, **_kw:
                        Verdict(claim_id=claim.id, status="unverifiable", reason="ambiguous",
                                explanation="Synthetic ambiguity."))
    result = ask.answer_plan(plan(), store=AskStore())
    assert not result["answer"] and not result["footnotes"]
    assert result["items"][0]["withheld"]


def test_request_has_standard_model_schema_and_no_model_answer_fields():
    request = ask.build_request("NVIDIA GAAP revenue Q2 FY2027?", available_companies=ask.companies(AskStore()))
    assert request["model"] == "muse-spark-1.3" and request["store"] is False
    assert request["reasoning"] == {"effort": "minimal"}
    assert "response_format" not in request
    schema = request["text"]["format"]["schema"]
    assert not extract.strict_subset_problems(schema)
    assert not ({"answer", "value", "verdict", "url", "sql"} & schema["properties"].keys())


def run(client, *, guard=lambda _: None, deadline=None):
    stats = ask.AskStats()
    request = ask.build_request("NVIDIA GAAP revenue Q2 FY2027?", available_companies=ask.companies(AskStore()))
    events = list(ask.ask_events("NVIDIA GAAP revenue Q2 FY2027?", request=request, store=AskStore(), stats=stats,
                                client_factory=lambda: client, before_model_call=guard, deadline=deadline))
    return events, stats, request


def test_success_has_one_accounted_call():
    model = ProposalClient()
    events, stats, _ = run(model)
    assert events[-1][0] == "result" and stats.complete and len(model.calls) == stats.model_calls == 1
    assert stats.cost_usd == pytest.approx((100 * 1.25 + 50 * 4.25) / 1e6)
    assert not stats.cost_is_estimate


@pytest.mark.parametrize("mode", ["missing_usage", "provider_failure", "invalid_json"])
def test_failure_and_unknown_usage_are_accounted_without_leaking_provider_text(mode):
    model = ProposalClient(usage=mode != "missing_usage", error="SECRET_PROVIDER_OUTPUT" if mode == "provider_failure" else None,
                           payload={"answer": "INVENTED"} if mode == "invalid_json" else None)
    events, stats, request = run(model)
    if mode != "missing_usage":
        assert events[-1][0] == "error"
    assert "SECRET_PROVIDER_OUTPUT" not in str(events) and "INVENTED" not in str(events)
    assert stats.model_calls == 1
    if mode != "invalid_json":
        assert stats.cost_is_estimate and stats.cost_usd == extract.estimate_request_cost(request, ask.SPARK)


def test_rejection_or_expired_deadline_cannot_dispatch():
    def blocked(_):
        raise extract.ModelCallRejected()
    for kwargs in ({"guard": blocked}, {"deadline": time.monotonic() - 1}, {"guard": None}):
        model = ProposalClient()
        events, stats, _ = run(model, **kwargs)
        assert events[-1][0] == "error" and not model.calls and stats.model_calls == 0


def test_model_cannot_add_extraneous_claim_or_citation():
    payload = plan().model_dump()
    payload["answer"] = "The revenue is $999 trillion."
    events, stats, _ = run(ProposalClient(payload))
    assert events[-1][0] == "error" and stats.model_calls == 1


@pytest.mark.parametrize("question,changes", [
    ("What was NVIDIA GAAP revenue in Q2 FY2027?", {"metrics": ["eps_diluted"]}),
    ("What was NVIDIA GAAP revenue in Q2 FY2027?", {"entity": "Microsoft"}),
    ("What was NVIDIA GAAP revenue in Q2 FY2027?", {"fiscal_period": "Q1"}),
    ("What was NVIDIA GAAP revenue in Q2 FY2027?", {"fiscal_year": 2026}),
    ("What was NVIDIA non-GAAP revenue in Q2 FY2027?", {}),
    ("What was NVIDIA revenue in Q2 FY2027?", {}),
    ("What was NVIDIA GAAP revenue?", {}),
    ("What was NVIDIA GAAP revenue in Q2?", {}),
    ("What was NVIDIA GAAP revenue in Q2 and Q1 FY2027?", {}),
    ("What was NVIDIA GAAP revenue in Q2 FY2026 and FY2027?", {}),
    ("Should I buy NVIDIA based on GAAP revenue in Q2 FY2027?", {}),
    ("What was NVIDIA Data Center GAAP revenue in Q2 FY2027?", {}),
])
def test_model_cannot_change_or_invent_explicit_question_fields(question, changes):
    stats = ask.AskStats()
    model = ProposalClient(plan(**changes).model_dump())
    request = ask.build_request(question, available_companies=ask.companies(AskStore()))
    events = list(ask.ask_events(question, request=request, store=AskStore(), stats=stats,
                                client_factory=lambda: model, before_model_call=lambda _: None))
    assert events[-1][0] == "error" and not stats.complete
    assert "matched safely" in events[-1][1]["message"]


def test_multiple_companies_cannot_be_silently_reduced_to_one():
    class TwoCompanies(AskStore):
        def _rows(self, *_):
            return super()._rows() + [{"cik": 789019, "ticker": "MSFT", "name": "Microsoft"}]
    with pytest.raises(ask.AskError):
        ask.bind_question("NVIDIA and Microsoft GAAP revenue in Q2 FY2027?", plan(), TwoCompanies())


def test_unambiguous_full_year_and_multiple_metrics_bind():
    ask.bind_question("What were NVIDIA GAAP revenue and diluted EPS for Q2 FY2027?",
                      plan(metrics=["revenue", "eps_diluted"]), AskStore())
    ask.bind_question("NVIDIA GAAP revenue for FY2026?", plan(fiscal_year=2026, fiscal_period="FY"), AskStore())


@pytest.mark.parametrize("question,changes", [
    ("NVIDIA GAAP revenue Q2 FY2027 (excluding China)?", {}),
    ("NVIDIA GAAP revenue Q2 FY2027 （excluding China）?", {}),
    ("NVIDIA and Samsung GAAP revenue Q2 FY2027?", {}),
    ("NVIDIA GAAP revenue Q2 FY2027 in constant currency?", {}),
    ("NVIDIA GAAP revenue Q2 FY2027 for China?", {}),
    ("NVIDIA GAAP revenue Q2 FY2027 除中國以外?", {}),
    ("NVIDIA GAAP revenue Q2 FY2027 / 2?", {}),
    ("NVIDIA GAAP revenue Q2 FY2027 + net income?", {"metrics": ["revenue", "net_income"]}),
    ("NVIDIA GAAP revenue Q2 FY2027 − net income?", {"metrics": ["revenue", "net_income"]}),
    ("NVIDIA GAAP net income attributable to common shareholders Q2 FY2027?", {"metrics": ["net_income"]}),
    ("Was NVIDIA GAAP revenue Q2 FY2027 higher than last quarter?", {}),
    ("NVIDIA GAAP revenue for Q2 FY2027 full year?", {}),
    ("NVIDIA GAAP revenue in a quarter of FY2027?", {"fiscal_period": "FY"}),
])
def test_unbound_question_qualifiers_are_not_silently_dropped(question, changes):
    # A syntactically valid model lookup still must account for the complete question.
    with pytest.raises(ask.AskError, match="matched safely"):
        ask.bind_question(question, plan(**changes), AskStore())


@pytest.mark.parametrize("question", [
    "What was NVIDIA's GAAP revenue in Q2 FY2027?",
    "Please show NVIDIA (NVDA) GAAP revenue for the second quarter of FY2027.",
    "What was NVIDIA GAAP revenue for the full fiscal year 2027?",
])
def test_supported_question_scaffolding_and_parenthetical_ticker_bind(question):
    ask.bind_question(question, plan(fiscal_period="FY" if "full fiscal year" in question else "Q2"), AskStore())


def test_full_legal_company_name_remains_supported():
    class LegalNameStore(AskStore):
        def _rows(self, *_):
            return [{"cik": 1045810, "ticker": "NVDA", "name": "NVIDIA CORP", "aliases": '["nvidia"]'}]
    ask.bind_question("What was NVIDIA CORP GAAP revenue in Q2 FY2027?", plan(), LegalNameStore())


def test_late_success_cannot_publish_answer_or_refund_estimated_charge():
    gate = threading.Event()
    class SlowClient(ProposalClient):
        def create(self, **kwargs):
            gate.wait(.2)
            return super().create(**kwargs)
    model = SlowClient()
    try:
        events, stats, request = run(model, deadline=time.monotonic() + .02)
        assert events[-1][0] == "error" and not stats.complete
        assert "deadline" in events[-1][1]["message"]
        assert stats.model_calls == 1 and stats.cost_is_estimate
        expected = extract.estimate_request_cost(request, ask.SPARK)
        assert stats.cost_usd == expected
    finally:
        gate.set()
    time.sleep(.03)
    assert stats.cost_usd == expected and not stats.complete
