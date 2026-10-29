"""Closed-book QA boundaries and accounting; every model response is offline/synthetic."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from eval import qa_core as core, qa_benchmark as runner
from receipts.store import Store


@pytest.fixture(scope="module")
def manifest():
    return core.prepare_manifest(runner.build_request)


@pytest.fixture
def store():
    value = Store(core.SNAPSHOT / "facts.sqlite", core.SNAPSHOT / "pr_facts")
    yield value
    value.close()


@pytest.fixture
def sample(manifest):
    row = next(row for row in manifest["items"] if row["question"]["metric"] == "revenue")
    question = core.Question.model_validate(row["question"])
    return row, question, core.gold_answer(question, row["gold"])


def test_fifty_fixed_diverse_questions_are_supported_before_model_answers(manifest, store):
    assert len(manifest["items"]) == 50 and len(manifest["ticker_counts"]) == 11
    assert len(manifest["metric_counts"]) == 8 and all(n in (4, 5) for n in manifest["ticker_counts"].values())
    assert manifest == core.prepare_manifest(runner.build_request)
    for row in manifest["items"]:
        question = core.Question.model_validate(row["question"])
        answer = core.gold_answer(question, row["gold"])
        assert core.gate_answer(question, answer, store)["accepted"]
        assert core.score_answer(question, answer, row["gold"], store) == "correct"
        assert all(source["accepted_at"].replace("+00:00", "Z") <= question.as_of for source in row["gold"]["sources"])


def test_request_is_raw_numeric_qa_without_sources_gold_search_or_planner(sample):
    row, question, _ = sample
    for target in core.TARGETS:
        request = runner.build_request(question, target)
        encoded = json.dumps(request)
        assert "tools" not in request and "previous_response_id" not in request
        assert "actual numeric answer, not a lookup plan" in encoded
        assert row["gold"]["value_decimal"] not in encoded
        assert all(source["accession"] not in encoded and source["url"] not in encoded for source in row["gold"]["sources"])
        assert question.text in (request.get("input"), (request.get("messages") or [{}, {}])[-1].get("content"))
        assert request["model"] in {"muse-spark-1.3", "muse-wonder-1.0", "meta-models/muse-spark-open-1026", "muse-glimmer"}
    assert runner.build_request(question, "spark")["text"]["format"]["type"] == "json_schema"
    assert "response_format" not in runner.build_request(question, "spark-open-baseten")


@pytest.mark.parametrize("change", [
    {"entity": "Unknown Corporation"}, {"entity": "NVIDIA"}, {"entity": "Amazon (Samsung)"},
    {"metric": "eps_diluted"}, {"fiscal_year": 1999}, {"fiscal_period": "FY"},
    {"basis": "non-GAAP"}, {"unit": "USD/share"}, {"value_text": "$NaN million"},
    {"value_text": "$Infinity million"}, {"value_text": "$1 million to $2 million"},
    {"value_text": "$100 billion"}, {"value_text": "$1 million (excluding China)"},
    {"value_text": "$999999999999 million"},
])
def test_wrong_bindings_unknowns_units_precision_and_values_cannot_pass(sample, store, change):
    row, question, good = sample
    answer = good.model_copy(update=change)
    assert not core.gate_answer(question, answer, store)["accepted"]
    assert core.score_answer(question, answer, row["gold"], store) == "wrong"


def test_wrong_periods_correct_value_still_withheld(sample, store):
    _, question, good = sample
    answer = good.model_copy(update={"fiscal_year": question.fiscal_year + 1})
    assert core.gate_answer(question, answer, store)["reason"] == "fiscal_year_mismatch"


def test_gold_changes_scoring_but_cannot_change_gold_blind_gate(sample, store):
    row, question, answer = sample
    before = core.gate_answer(question, answer, store)
    wrong_gold = {**row["gold"], "value_decimal": "999999999999999"}
    assert core.score_answer(question, answer, wrong_gold, store) == "wrong"
    assert core.gate_answer(question, answer, store) == before
    assert "gold" not in core.gate_answer.__code__.co_varnames


def test_point_in_time_gate_cannot_use_future_filing(sample, store):
    _, question, answer = sample
    old = question.model_copy(update={"as_of": "2000-01-01T00:00:00Z"})
    assert not core.gate_answer(old, answer, store)["accepted"]


def test_abstention_and_zero_accepted_are_not_perfect_precision(sample, store):
    _, question, answer = sample
    abstention = core.RawAnswer(status="abstain", entity=None, metric=None, fiscal_year=None, fiscal_period=None,
                               basis=None, unit=None, value_text=None, reason="I do not know")
    assert not core.gate_answer(question, abstention, store)["accepted"]
    result = core.aggregate([{"raw_outcome": "abstain", "gate": {"accepted": False}}], planned=50)
    assert result["verified_precision"] is None and result["raw_answer_precision"] is None
    assert result["verified_coverage"] == 0 and result["not_run"] == 49
    with pytest.raises(ValueError):
        core.RawAnswer.model_validate({**answer.model_dump(), "status": "abstain"})


def test_independent_score_detects_unsafe_passed_and_keeps_all_denominators():
    rows = [{"raw_outcome": outcome, "gate": {"accepted": accepted}} for outcome, accepted in (
        ("correct", True), ("wrong", True), ("wrong", False), ("abstain", False), ("format_error", False), ("api_error", False))]
    score = core.aggregate(rows, planned=10)
    assert score["verified_precision"] == .5 and score["unsafe_passed"] == 1
    assert score["verified_coverage"] == .2 and score["raw_answer_coverage"] == .3
    assert score["not_run"] == 4 and score["api_errors"] == 1 and score["format_errors"] == 1


def test_numeric_parser_does_not_hide_additional_answers_or_prose(sample):
    _, _, answer = sample
    valid = answer.model_dump_json()
    assert core.parse_answer("```json\n" + valid + "\n```") == answer
    with pytest.raises(ValueError):
        core.parse_answer("Actually the value is $2 billion. " + valid)
    with pytest.raises(ValueError):
        core.parse_answer(json.dumps({**answer.model_dump(), "extra_answer": "$2 billion"}))


def test_budget_plan_full_reservations_fit_authorized_cap(manifest):
    plan = runner.plan(manifest)
    assert 0 < plan["sum_full_token_reservations_usd"] < 3.5
    assert plan["all_calls_full_reservation_usd"]["glimmer"] == 0
    assert "Excluded" in plan["gpu"]


def test_durable_admission_concurrency_unknown_cost_and_duplicate_guard(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ledger = runner.AdmissionLedger(path, "fixed", cap_micros=100)

    def attempt(index):
        try:
            ledger.reserve("spark", f"q{index}", "request", 60)
            return index
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = [i for i in pool.map(attempt, range(4)) if i is not None]
    assert len(accepted) == 1
    runner.AdmissionLedger(path, "fixed", cap_micros=100).settle("spark", f"q{accepted[0]}", None)
    assert ledger.report()["attempts"][0]["charged"] == 60
    assert ledger.report()["attempts"][0]["status"] == "unknown_usage_retained"
    with pytest.raises(sqlite3.IntegrityError):
        ledger.reserve("spark", f"q{accepted[0]}", "request", 0)
    with pytest.raises(AssertionError):
        runner.AdmissionLedger(path, "different", cap_micros=100)
    with pytest.raises(AssertionError):
        runner.AdmissionLedger(path, "fixed", cap_micros=101)


def test_settlement_preserves_actual_cost_and_disallows_repeated_settlement(tmp_path):
    ledger = runner.AdmissionLedger(tmp_path / "ledger.sqlite3", "fixed", cap_micros=100)
    ledger.reserve("wonder", "q1", "h", 50)
    ledger.settle("wonder", "q1", 120)
    with pytest.raises(RuntimeError):
        ledger.reserve("wonder", "q2", "h", 1)
    with pytest.raises(RuntimeError):
        ledger.settle("wonder", "q1", 0)


def test_frozen_manifest_refuses_request_or_evidence_changes(tmp_path, monkeypatch, manifest):
    path = tmp_path / "manifest.json"
    core.save_new(path, manifest)
    monkeypatch.setattr(core, "MANIFEST", path)
    assert runner.load_manifest() == manifest
    bad = copy.deepcopy(manifest)
    bad["items"][0]["question"]["text"] += " changed"
    path.write_text(json.dumps(bad))
    with pytest.raises(AssertionError):
        runner.load_manifest()


def test_full_fake_runner_keeps_failure_charge_and_never_retries(tmp_path, monkeypatch, manifest):
    path = tmp_path / "manifest.json"
    core.save_new(path, manifest)
    monkeypatch.setattr(core, "MANIFEST", path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    runner.initialize_run("offline-fixture", manifest)
    answers = {row["question"]["text"]: core.gold_answer(core.Question.model_validate(row["question"]), row["gold"]).model_dump_json()
               for row in manifest["items"]}

    class FakeClient:
        def __init__(self):
            self.responses = self
            self.calls = []

        def create(self, **request):
            self.calls.append(request["input"])
            if request["input"] == manifest["items"][0]["question"]["text"]:
                raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_APPEAR_IN_RESULT")
            return SimpleNamespace(status="completed", output_text=answers[request["input"]],
                                   usage={"input_tokens": 200, "output_tokens": 100})

    client = FakeClient()
    result = runner.run_target("spark", run_id="offline-fixture", manifest=manifest, client=client)
    assert len(client.calls) == len(set(client.calls)) == 50
    assert result["score"]["raw_correct"] == 49 and result["score"]["api_errors"] == 1
    assert result["unknown_usage_calls"] == 1 and result["score"]["unsafe_passed"] == 0
    assert "SYNTHETIC_SECRET" not in json.dumps(result)
    failed = next(row for row in result["ledger"]["attempts"] if row["status"] == "unknown_usage_retained")
    assert failed["charged"] > 0
    with pytest.raises(FileExistsError):
        runner.run_target("spark", run_id="offline-fixture", manifest=manifest, client=client)


def test_interrupted_recovery_retains_unknown_charge_without_rerun(tmp_path, monkeypatch, manifest):
    path = tmp_path / "manifest.json"
    core.save_new(path, manifest)
    monkeypatch.setattr(core, "MANIFEST", path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    folder = runner.initialize_run("interrupted-fixture", manifest)
    (folder / "wonder").mkdir()
    first = manifest["items"][0]
    ledger = runner.AdmissionLedger(folder / "admission.sqlite3", core.digest(path.read_bytes()))
    ledger.reserve("wonder", first["question"]["id"], first["request_sha256"]["wonder"], 50000)
    result = runner.recover_interrupted_target("wonder", "interrupted-fixture", manifest)
    assert result["score"]["api_errors"] == 1 and result["score"]["not_run"] == 49
    assert result["score"]["verified_precision"] is None and result["new_calls_in_recovery"] == 0
    assert result["ledger"]["attempts"][0]["charged"] == 50000
    assert result["records"][0]["output_text"] is None
