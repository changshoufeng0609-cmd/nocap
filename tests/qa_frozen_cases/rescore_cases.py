"""Synthetic adversarial tests for post-run metadata sensitivity; no provider calls."""
import inspect
import json
from decimal import Decimal

import pytest

from eval import qa_core as core, qa_rescore as sensitivity
from receipts.store import Store


def answer(**changes):
    data = dict(status="answer", entity="Apple", metric="revenue", fiscal_year=2023,
                fiscal_period="Q4", basis="GAAP", unit="USD", value_text="$1234 million", reason="")
    return core.RawAnswer(**{**data, **changes})


@pytest.mark.parametrize("unit,expression,canonical", [
    ("USD millions", "$1234 million", "USD"), ("USD_millions", "$1234 million", "USD"),
    ("USD million", "$1234 million", "USD"), ("USD billions", "$1.234 billion", "USD"),
    ("USD thousands", "$1234000 thousand", "USD"),
    ("USD per share", "$1.23 per share", "USD/share"),
    ("USD_per_share", "$1.23 per share", "USD/share"), ("$ per share", "$1.23 per share", "USD/share"),
])
def test_explicit_unit_alias_preserves_every_value_character(unit, expression, canonical):
    before = answer(unit=unit, value_text=expression)
    after, changes = sensitivity.normalize_answer(before)
    assert after.unit == canonical and after.value_text == before.value_text
    assert before.unit == unit and changes == {"unit": canonical}


@pytest.mark.parametrize("unit,expression", [
    ("USD millions", "$1234"), ("USD millions", "$1234 billion"),
    ("USD millions", "$1234 million per share"),
    ("USD per share", "$1.23"), ("USD per share", "$1.23 million per share"),
    ("USD billions", "$1.23 million"),
])
def test_conflicting_or_missing_scale_and_unit_never_inferred(unit, expression):
    with pytest.raises(ValueError):
        sensitivity.normalize_answer(answer(unit=unit, value_text=expression))


@pytest.mark.parametrize("period", ["Q4 FY2023", "Q4 2023", "FY2023 Q4", "FY 2023 Q4", "q4 fy2023"])
def test_unambiguous_fiscal_period_alias_requires_same_answer_year(period):
    normalized, changes = sensitivity.normalize_answer(answer(fiscal_period=period))
    assert normalized.fiscal_period == "Q4" and normalized.fiscal_year == 2023
    assert changes == {"fiscal_period": "Q4"}
    with pytest.raises(ValueError):
        sensitivity.normalize_answer(answer(fiscal_period=period, fiscal_year=2024))


@pytest.mark.parametrize("changes", [
    {"unit": "USD_mln"}, {"unit": "EUR millions"}, {"unit": "USD millions excluding China"},
    {"fiscal_period": "2023-09-30"}, {"fiscal_period": "quarter ended 2023-09-30"},
    {"fiscal_period": "Q4 FY2023 or Q1 FY2024"}, {"entity": "Apple (Samsung)"},
    {"metric": "revenue excluding China"}, {"basis": "non-GAAP"},
])
def test_no_speculative_label_repair_or_question_derived_fill(changes):
    before = answer(**changes)
    after, normalized = sensitivity.normalize_answer(before)
    assert after == before and normalized == {}
    assert list(inspect.signature(sensitivity.normalize_answer).parameters) == ["answer"]


def test_sensitivity_accepts_valid_metadata_without_touching_primary_or_gold():
    manifest = json.loads(core.MANIFEST.read_text())
    row = next(row for row in manifest["items"] if row["question"]["unit"] == "USD")
    question = core.Question.model_validate(row["question"])
    original = core.gold_answer(question, row["gold"]).model_copy(update={"unit": "USD millions",
                "fiscal_period": f"{question.fiscal_period} FY{question.fiscal_year}"})
    record = {"output_text": original.model_dump_json(), "response_complete": True, "error_type": None}
    before = json.dumps(record, sort_keys=True)
    store = Store(core.SNAPSHOT / "facts.sqlite", core.SNAPSHOT / "pr_facts")
    try:
        assert not core.gate_answer(question, original, store)["accepted"]
        result = sensitivity.assess(row, record, store)
        assert result["outcome"] == "correct" and result["gate"]["accepted"]
        assert result["normalized_answer"]["value_text"] == original.value_text
        wrong_gold = {**row, "gold": {**row["gold"], "value_decimal": "999999999999999"}}
        changed = sensitivity.assess(wrong_gold, record, store)
        assert changed["outcome"] == "numeric_wrong" and changed["gate"] == result["gate"]
    finally:
        store.close()
    assert json.dumps(record, sort_keys=True) == before


def test_unassessable_metadata_is_not_automatically_counted_as_financial_wrong():
    records = [{"outcome": outcome, "gate": {"accepted": False}} for outcome in
               ("unassessable", "format_error", "api_error", "abstain")]
    summary = sensitivity.summarize(records)
    assert summary["wrong"] == 0 and summary["not_run"] == 46
    assert summary["verified_precision"] is None and summary["raw_answer_precision"] is None


def test_independent_usage_audit_includes_reasoning_in_output_only_once():
    usage = {"input_tokens": 1000, "cached_tokens": 200, "output_tokens": 300, "reasoning_tokens": 250}
    micros = int(Decimal(800) * Decimal("1.25") + Decimal(200) * Decimal("0.15") + Decimal(300) * Decimal("4.25"))
    run = {"target": "spark", "records": [{"question_id": "q", "request_sha256": "h", "usage": usage,
                                              "token_api_cost_usd": micros / 1e6}]}
    ledger = [{"target": "spark", "question": "q", "request_hash": "h", "charged": micros, "reservation": 50000}]
    assert sensitivity.usage_audit(run, ledger)["token_api_cost_usd"] == micros / 1e6
    ledger[0]["charged"] += 1
    with pytest.raises(AssertionError):
        sensitivity.usage_audit(run, ledger)
