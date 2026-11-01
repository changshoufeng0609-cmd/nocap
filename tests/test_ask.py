"""Financial Q&A lookup boundaries; provider responses are synthetic, never live."""
import json
import time
import threading
from datetime import date
from types import SimpleNamespace

import pytest

from receipts import ask, extract
from receipts.types import Fact, Verdict
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
    assert "$96,221,000,000" in result["answer"] and "$2.46" in result["answer"]
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


class WordTickerStore(AskStore):
    """NVIDIA beside companies whose tickers are everyday words (COST, NOW, ALL, IT, ON)."""
    def _rows(self, *_):
        return [{"cik": 1045810, "ticker": "NVDA", "name": "NVIDIA", "aliases": '["nvda", "nvidia"]'},
                {"cik": 909832, "ticker": "COST", "name": "COSTCO WHOLESALE CORP /NEW", "aliases": '["cost", "costco"]'},
                {"cik": 1373715, "ticker": "NOW", "name": "ServiceNow, Inc.", "aliases": '["now", "servicenow"]'},
                {"cik": 899051, "ticker": "ALL", "name": "ALLSTATE CORP", "aliases": '["all", "allstate"]'},
                {"cik": 749251, "ticker": "IT", "name": "GARTNER INC", "aliases": '["it", "gartner"]'},
                {"cik": 1097864, "ticker": "ON", "name": "ON SEMICONDUCTOR CORP", "aliases": '["on", "on semiconductor"]'}]


def test_word_tickers_are_never_a_second_company_in_a_question():
    # "cost" is a word here, not Costco (before word tickers were excluded, this question was refused)
    ask.bind_question("What was NVIDIA's GAAP cost of revenue in Q2 FY2027?", plan(metrics=["cost_of_revenue"]),
                      WordTickerStore())


def test_a_named_word_ticker_company_still_counts_as_a_second_company():
    with pytest.raises(ask.AskError, match="matched safely"):
        ask.bind_question("What was NVIDIA and Costco GAAP revenue in Q2 FY2027?", plan(), WordTickerStore())


# --------------------------------------------------------------------------- S&P 500 scale (review of 2026-10-31)
def stored(tmp_path, companies):
    """A real receipts.store.Store: companies is [(cik, ticker, SEC name, [(fy, fp, start, end)], [Fact])]."""
    from receipts.store import Store, company_aliases, connect_rw, replace_company
    conn = connect_rw(tmp_path / "facts.sqlite")
    for cik, ticker, name, cal, facts in companies:
        replace_company(conn, {"cik": cik, "ticker": ticker, "name": name, "aliases": company_aliases(ticker, name),
                               "fiscal_year_end": "1231"}, [],
                        [(fy, fp, start, end, "0000000000-00-000000") for fy, fp, start, end in cal],
                        [(f, "quarter") for f in facts])
    conn.close()
    return Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr")


def quarter_fact(cik, ticker, metric, value, fy, fp, start, end, accession):  # hand-made figures
    return Fact(cik=cik, ticker=ticker, metric=metric, basis="GAAP", value=value, unit="USD", period_start=start,
                period_end=end, fiscal_year=fy, fiscal_period=fp, source="xbrl", derived_from=[], form="10-Q",
                accession=accession, accepted_at=utc("2026-11-02T20:00:00Z"),
                url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/doc.htm")


GS_Q3 = (2026, "Q3", date(2026, 7, 1), date(2026, 9, 30))
FLEX_Q2 = (2026, "Q2", date(2025, 6, 28), date(2025, 9, 26))  # Flex's fiscal year ends in March


@pytest.fixture
def word_ticker_companies(tmp_path):
    s = stored(tmp_path, [
        (886982, "GS", "GOLDMAN SACHS GROUP INC", [GS_Q3],
         [quarter_fact(886982, "GS", "net_income", 4_100e6, *GS_Q3, "0000886982-26-000150")]),
        (866374, "FLEX", "FLEX LTD.", [FLEX_Q2],
         [quarter_fact(866374, "FLEX", "revenue", 6_800e6, *FLEX_Q2, "0000866374-25-000120")]),
        (1045810, "NVDA", "NVIDIA CORP", [], [])])
    yield s
    s.close()


@pytest.mark.parametrize("question, lookup, ticker, value", [
    ("What was Goldman Sachs's GAAP net income in Q3 FY2026?",
     dict(entity="Goldman Sachs", metrics=["net_income"], fiscal_year=2026, fiscal_period="Q3"), "GS", "$4,100,000,000"),
    ("What was Flex Ltd.'s GAAP revenue in Q2 FY2026?",
     dict(entity="Flex Ltd.", metrics=["revenue"], fiscal_year=2026, fiscal_period="Q2"), "FLEX", "$6,800,000,000"),
])
def test_a_word_ticker_company_is_answered_end_to_end(word_ticker_companies, question, lookup, ticker, value):
    """The answer's own claim is checked against the company stored under the fact's ticker (Store.by_ticker), so the
    sentence verifies instead of being withheld. GS now also resolves as an explicit alias (review L9), and FLEX as
    the lookup-only short name "Flex"."""
    store, stats, model = word_ticker_companies, ask.AskStats(), ProposalClient(plan(**lookup).model_dump())
    assert store.resolve_entity("GS") == store.by_ticker("GS")
    request = ask.build_request(question, available_companies=[])
    events = list(ask.ask_events(question, request=request, store=store, stats=stats, client_factory=lambda: model,
                                 before_model_call=lambda _: None))
    kind, result = events[-1]
    assert kind == "result" and result["notice"] is None, result
    (item,) = result["items"]
    assert item["verdict"]["status"] == "verified" and not item["withheld"] and value in result["answer"]
    assert item["claim"]["entity"] == ticker and item["sentence"].startswith(f"{ticker} GAAP")


@pytest.mark.parametrize("question, ticker", [("What was Goldman Sachs's GAAP net income in Q3 FY2026?", "GS"),
                                              ("What was Flex Ltd.'s GAAP revenue in Q2 FY2026?", "FLEX"),
                                              ("What was NVDA's GAAP revenue in Q2 FY2027?", "NVDA")])
def test_the_request_names_the_company_the_question_names(word_ticker_companies, question, ticker):
    request = ask.build_request(question, available_companies=ask.lookup_companies(word_ticker_companies))
    assert json.loads(request["input"])["companies"] == [
        {"ticker": ticker, "name": {"GS": "GOLDMAN SACHS GROUP INC", "FLEX": "FLEX LTD.", "NVDA": "NVIDIA CORP"}[ticker]}]


def test_the_request_stays_under_its_guard_at_500_companies():
    """The request names only the companies the question names, at most five, without periods: its size no longer
    grows with coverage. The full list of 500 companies with their periods would not fit."""
    periods = [{"fiscal_year": 2023 + i // 5, "fiscal_period": ("Q1", "Q2", "Q3", "Q4", "FY")[i % 5]} for i in range(17)]
    available = [{"ticker": f"C{i:03d}", "name": f"COMPANY {i:03d} HOLDINGS INC", "periods": periods} for i in range(500)]
    assert len(json.dumps({"companies": available}).encode()) > ask.MAX_REQUEST_BYTES
    request = ask.build_request("What was Company 042 Holdings' GAAP revenue in Q2 FY2027?", available_companies=available)
    assert len(json.dumps(request, ensure_ascii=False).encode()) <= ask.MAX_REQUEST_BYTES
    assert json.loads(request["input"])["companies"] == [{"ticker": "C042", "name": "COMPANY 042 HOLDINGS INC"}]
    many = "GAAP revenue in Q2 FY2027 of " + ", ".join(f"Company {i:03d} Holdings" for i in range(40, 60))
    request = ask.build_request(many, available_companies=available)
    assert [c["ticker"] for c in json.loads(request["input"])["companies"]] == ["C040", "C041", "C042", "C043", "C044"]
    assert len(json.dumps(request, ensure_ascii=False).encode()) <= ask.MAX_REQUEST_BYTES
    assert json.loads(ask.build_request("What was Acme's revenue?", available_companies=available)["input"])[
        "companies"] == []


def test_options_list_each_company_with_its_most_recent_periods():
    class LongHistory(AskStore):
        def fiscal_calendar(self, cik):
            return [(2020 + i // 5, ("Q1", "Q2", "Q3", "Q4", "FY")[i % 5], date(2020, 1, 1), date(2020, 3, 31))
                    for i in range(30)]
    (row,) = ask.companies(LongHistory())
    assert len(row["periods"]) == ask.OPTIONS_PERIODS == 8
    assert row["periods"][-1] == {"fiscal_year": 2025, "fiscal_period": "FY"}


@pytest.fixture
def word_names(tmp_path):
    """Visa, Target and Chevron (one-word names that are ordinary words), Costco and Coca-Cola beside NVIDIA."""
    s = stored(tmp_path, [(1403161, "V", "VISA INC.", [], []), (27419, "TGT", "TARGET CORP", [], []),
                          (93410, "CVX", "CHEVRON CORP", [], []), (909832, "COST", "COSTCO WHOLESALE CORP /NEW", [], []),
                          (21344, "KO", "COCA COLA CO", [], []), (1045810, "NVDA", "NVIDIA CORP", [], [])])
    yield s
    s.close()


def named(question, store, proposed):
    """The tickers question_mentions (the helper bind_question decides with) finds, for the company the model proposed."""
    ciks = ask.question_mentions(question, store, proposed=store.resolve_entity(proposed)[0])[0]
    return {ticker for cik, ticker, _ in store.companies_list() if cik in ciks}


def test_a_one_word_name_counts_only_for_the_company_the_lookup_proposed(word_names):
    q = "What was Chevron's revenue in Q2 2026?"
    assert named(q, word_names, "Chevron") == {"CVX"}
    assert named(q, word_names, "NVIDIA") == set()  # never for any other company
    assert named("What was Target's revenue?", word_names, "Target") == {"TGT"}
    ask.bind_question("What was Chevron's GAAP revenue in Q2 FY2026?", plan(entity="Chevron", fiscal_year=2026), word_names)
    ask.bind_question("What was Target's GAAP revenue in Q2 FY2026?", plan(entity="Target", fiscal_year=2026), word_names)
    with pytest.raises(ask.AskError, match="matched safely"):
        ask.bind_question("What was Chevron's GAAP revenue in Q2 FY2026?", plan(entity="NVIDIA", fiscal_year=2026),
                          word_names)


def test_cost_of_revenue_never_brings_in_costco(word_names):
    q = "What was NVIDIA's GAAP cost of revenue in Q2 FY2027?"
    for proposed in ("NVIDIA", "Costco"):
        assert "COST" not in named(q, word_names, proposed)
    ask.bind_question(q, plan(metrics=["cost_of_revenue"]), word_names)


def test_visa_and_a_revenue_target_name_visa_alone_and_are_refused(word_names):
    """"Target" is Visa's revenue target here, not Target Corp.; the question asks for a target, so it is refused."""
    q = "What was Visa's GAAP revenue target in Q3 FY2026?"
    assert named(q, word_names, "Visa") == {"V"}
    for entity in ("Visa", "Target"):
        with pytest.raises(ask.AskError, match="matched safely"):
            ask.bind_question(q, plan(entity=entity, fiscal_year=2026, fiscal_period="Q3"), word_names)


def test_a_question_names_a_bare_ticker_only_in_capitals():
    ask.bind_question("What was NVDA GAAP revenue in Q2 FY2027?", plan(), AskStore())
    ask.bind_question("What was NVDA's GAAP revenue in Q2 FY2027?", plan(), AskStore())
    with pytest.raises(ask.AskError, match="matched safely"):
        ask.bind_question("What was nvda GAAP revenue in Q2 FY2027?", plan(), AskStore())


def test_a_hyphenated_name_is_one_name(word_names):
    ask.bind_question("What was Coca-Cola's GAAP revenue in Q3 FY2026?",
                      plan(entity="Coca-Cola", fiscal_year=2026, fiscal_period="Q3"), word_names)


@pytest.fixture
def known_by_ticker(tmp_path):
    """UPS, FICO and MSCI, whose tickers are their WORD_NAMES names, beside Ford and NVIDIA."""
    s = stored(tmp_path, [(1090727, "UPS", "UNITED PARCEL SERVICE INC", [], []),
                          (814547, "FICO", "FAIR ISAAC CORP", [], []), (1408198, "MSCI", "MSCI Inc.", [], []),
                          (37996, "F", "FORD MOTOR CO", [], []), (1045810, "NVDA", "NVIDIA CORP", [], [])])
    yield s
    s.close()


@pytest.mark.parametrize("question, proposed, tickers", [
    ("What was UPS's revenue in Q3 2026?", "UPS", {"UPS"}),
    ("What was FICO's revenue in Q3 2026?", "FICO", {"FICO"}),
    ("What was MSCI's revenue in Q3 2026?", "MSCI", {"MSCI"}),
    ("What was UPS's revenue in Q3 2026?", "NVIDIA", set()),       # never for another company
    ("What was ups's revenue in Q3 2026?", "UPS", set()),          # only in capitals
    ("What was start-ups' revenue in Q3 2026?", "UPS", set()),
    ("FICO score trends and MSCI revenue", "MSCI", {"MSCI"}),      # FICO is not the proposed company
])
def test_a_company_known_by_its_ticker_counts_only_for_the_proposed_company(known_by_ticker, question, proposed,
                                                                            tickers):
    assert named(question, known_by_ticker, proposed) == tickers


def test_a_question_about_ups_fico_or_msci_binds_that_company(known_by_ticker):
    for entity in ("UPS", "FICO", "MSCI"):
        ask.bind_question(f"What was {entity}'s GAAP revenue in Q3 FY2026?",
                          plan(entity=entity, fiscal_year=2026, fiscal_period="Q3"), known_by_ticker)
    for question, entity in (("What was start-ups' GAAP revenue in Q3 FY2026?", "UPS"),
                             ("What was the FICO score GAAP revenue in Q3 FY2026?", "NVIDIA")):
        with pytest.raises(ask.AskError, match="matched safely"):
            ask.bind_question(question, plan(entity=entity, fiscal_year=2026, fiscal_period="Q3"), known_by_ticker)


def test_a_one_word_short_name_counts_for_the_proposed_company(known_by_ticker):
    """"Ford" (store.SHORT_NAMES, lookup-only) resolves as the proposed company and binds; never for another one."""
    assert named("What was Ford's revenue in Q3 2026?", known_by_ticker, "Ford") == {"F"}
    assert named("What was Ford's revenue in Q3 2026?", known_by_ticker, "NVIDIA") == set()
    ask.bind_question("What was Ford's GAAP revenue in Q3 FY2026?",
                      plan(entity="Ford", fiscal_year=2026, fiscal_period="Q3"), known_by_ticker)


@pytest.fixture
def common_names(tmp_path):
    """Companies the S&P 500 QA (round 2) typed by their common names, stored under their SEC names only."""
    s = stored(tmp_path, [(27904, "DAL", "DELTA AIR LINES, INC.", [], []),
                          (1091667, "CHTR", "CHARTER COMMUNICATIONS, INC. /MO/", [], []),
                          (1289490, "EXR", "Extra Space Storage Inc.", [], []),
                          (1035002, "VLO", "VALERO ENERGY CORP/TX", [], []),
                          (1501585, "HII", "HUNTINGTON INGALLS INDUSTRIES, INC.", [], []),
                          (49196, "HBAN", "HUNTINGTON BANCSHARES INC /MD/", [], [])])
    yield s
    s.close()


@pytest.mark.parametrize("entity, ticker", [("Delta", "DAL"), ("Charter", "CHTR"), ("Extra Space", "EXR"),
                                            ("Valero", "VLO"), ("Huntington Ingalls", "HII")])
def test_a_common_name_binds_the_company_the_lookup_proposed(common_names, entity, ticker):
    """Fourth round: lookup-only names (Delta, Charter, Extra Space) count only for the proposed company; ordinary
    ones (Valero, Huntington Ingalls) as any company name. Bare "Huntington" names neither HII nor HBAN."""
    question = f"What was {entity}'s revenue in Q3 2026?"
    assert named(question, common_names, entity) == {ticker}
    ask.bind_question(f"What was {entity}'s GAAP revenue in Q3 FY2026?",
                      plan(entity=entity, fiscal_year=2026, fiscal_period="Q3"), common_names)
    assert common_names.resolve_entity("Huntington") is None
