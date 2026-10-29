"""Lexical omission warnings are independent of model extraction and verdicts."""
from copy import deepcopy

import pytest

from receipts.coverage import numeric_coverage, validate_coverage
from receipts.types import Claim


def claim(text, value, *, span=None, start=None, **changes):
    span = span if span is not None else text
    start = text.index(span) if start is None else start
    fields = dict(id="c1", metric="revenue", metric_text="revenue", value_text=value,
                  value=1, unit="USD", scale="units", period_text="Q2 FY2027",
                  period_type="quarter", comparison="none", basis="GAAP",
                  forward_looking=False, entity="NVIDIA", span_text=span,
                  span_start=start, span_end=start + len(span))
    fields.update(changes)
    return Claim(**fields)


def missed(result):
    return [number["text"] for number in result["uncovered"]]


def test_whole_sentence_claim_does_not_cover_unextracted_second_value():
    text = "Revenue was $10 billion and operating income was $4 billion."
    result = numeric_coverage(text, [claim(text, "$10 billion")])
    assert result["candidate_count"] == 2
    assert result["covered_count"] == 1
    assert missed(result) == ["$4 billion"]
    assert validate_coverage(result, text)


def test_ambiguous_repeated_values_remain_uncovered():
    text = "Revenue was $10 and costs were $10."
    result = numeric_coverage(text, [claim(text, "$10")])
    assert result["covered_count"] == 0
    assert missed(result) == ["$10", "$10"]


def test_repeated_value_can_be_located_by_a_unique_claim_span():
    text = "Revenue was $10 and costs were $10."
    result = numeric_coverage(text, [claim(text, "$10", span="costs were $10")])
    assert result["covered_count"] == 1
    assert result["uncovered"][0]["start"] == text.index("$10")


def test_dual_basis_and_duplicate_claims_count_one_occurrence():
    text = "Revenue was $10."
    original = claim(text, "$10")
    result = numeric_coverage(text, [original, original.model_copy(update={"id": "c2", "basis": "non-GAAP"})])
    assert result["candidate_count"] == result["covered_count"] == 1
    assert not result["uncovered"]


@pytest.mark.parametrize("text,value", [
    ("Margin was 12.5%.", "12"),
    ("Revenue was $12.", "2"),
    ("Revenue was $100.", "$10"),
    ("Values were 12 and 13.", "12 and 13"),
])
def test_partial_or_multiple_numeric_values_do_not_cover_candidates(text, value):
    result = numeric_coverage(text, [claim(text, value)])
    assert result["covered_count"] == 0
    assert result["candidate_count"] == len(result["uncovered"])


@pytest.mark.parametrize("changes", [
    {"span_start": -1}, {"span_end": 999}, {"span_text": "Revenue is $10."},
    {"span_start": 1}, {"value_text": ""}, {"value_text": "$99"},
])
def test_invalid_claim_spans_cannot_hide_numbers(changes):
    text = "Revenue was $10."
    assert missed(numeric_coverage(text, [claim(text, "$10", **changes)])) == ["$10"]


@pytest.mark.parametrize("context", [
    "Q2 FY2027", "Q 2 FY 2027", "quarter 2 of 2027", "fiscal year 2027", "fiscal 2025",
    "year 2025", "Q3 of 2025", "second quarter of 2025", "third-quarter 2026",
    "calendar year ended 2026", "2026 calendar year", "in 2026", "during 2026",
    "August 26, 2026", "august 26, 2026", "26 August 2026", "August 2026",
    "2026-08-26", "2026/08/26", "08/26/2026", "26-08-2026",
    "Form 10-Q", "form 10-q", "EXHIBIT 99.1", "Item 2.02", "Section 12(a)",
    "CIK: 1045810", "0001045810-26-000154", "https://sec.gov/123/2026.htm",
    "person123@example.com", "(555) 123-4567",
])
def test_dates_periods_and_identifiers_are_not_omissions(context):
    result = numeric_coverage(f"{context}; revenue was $10.", [])
    assert missed(result) == ["$10"]


def test_meta_demo_hyphenated_quarter_does_not_create_a_year_omission():
    text = "Meta Platforms posted third-quarter 2026 revenue of $55.0 billion."
    result = numeric_coverage(text, [claim(text, "$55.0 billion")])
    assert result["candidate_count"] == result["covered_count"] == 1
    assert result["excluded_count"] == 1
    assert result["uncovered"] == []


@pytest.mark.parametrize("text,value", [
    ("We have 2000 employees.", "2000"), ("Revenue was 2025.", "2025"),
    ("Revenue was $2025.", "$2025"), ("Expenses were 2025 million.", "2025 million"),
    ("EPS was (5).", "(5)"), ("Notes total 5 million.", "5 million"),
])
def test_numbers_resembling_years_or_footnotes_remain_candidates(text, value):
    assert missed(numeric_coverage(text, [])) == [value]
    assert numeric_coverage(text, [claim(text, value)])["covered_count"] == 1


@pytest.mark.parametrize("value", ["-$12.5 million", "−$12.5 million", "($12.5 million)",
                                    "$(12.5)", "(-$12.5)", "$ -12.5", "-12.5%", "12.5 percent",
                                    "35 bps", "1.2x", "0", "1,234", ".5%", "$.5 million", "$12.5b"])
def test_numeric_formats_are_preserved_and_matchable(value):
    text = f"The result was {value}."
    result = numeric_coverage(text, [])
    assert missed(result) == [value]
    assert numeric_coverage(text, [claim(text, value)])["covered_count"] == 1
    assert validate_coverage(result, text)


@pytest.mark.parametrize("text,value", [
    ("May 20% gross margin.", "20%"), ("May 20 million revenue.", "20 million"),
    ("Revenue in 2025 million dollars.", "2025 million"), ("May 20 dollars revenue.", "20"),
    ("May 20 employees departed.", "20"), ("May 20.5 revenue.", "20.5"),
])
def test_date_like_context_cannot_hide_explicit_quantities(text, value):
    assert missed(numeric_coverage(text, [])) == [value]


def test_unicode_offsets_are_code_points_not_utf16_units():
    text = "💰 營收 $10; income $4."
    result = numeric_coverage(text, [claim(text, "$10")])
    value = result["uncovered"][0]
    assert value == {"start": text.index("$4"), "end": text.index("$4") + 2, "text": "$4"}
    assert validate_coverage(result, text)


def test_empty_and_non_numeric_drafts_are_empty_scans():
    expected = {"version": 1, "candidate_count": 0, "covered_count": 0,
                "excluded_count": 0, "uncovered": []}
    assert numeric_coverage("", []) == expected
    assert numeric_coverage("Revenue increased.", []) == expected


def test_excluded_and_candidate_counts_partition_the_scanned_tokens():
    result = numeric_coverage("On August 26, 2026, revenue was $10.", [])
    assert result["excluded_count"] == 2
    assert result["candidate_count"] == 1
    assert result["covered_count"] == 0


@pytest.mark.parametrize("patch", [
    {"version": True}, {"version": 2}, {"candidate_count": True},
    {"candidate_count": -1}, {"covered_count": 2}, {"excluded_count": 1.2},
    {"uncovered": None}, {"extra": "unexpected"},
    {"uncovered": [{"start": False, "end": 2, "text": "$1"}]},
    {"uncovered": [{"start": -1, "end": 1, "text": "$1"}]},
    {"uncovered": [{"start": 0, "end": 0, "text": ""}]},
    {"uncovered": [{"start": 0, "end": 2, "text": "$99"}]},
])
def test_validation_rejects_malformed_counts_and_spans(patch):
    value = numeric_coverage("$1", [])
    value.update(patch)
    assert not validate_coverage(value, "$1")


def test_validation_checks_source_and_nonoverlapping_order():
    value = numeric_coverage("$1 and $2", [])
    assert validate_coverage(value)
    assert not validate_coverage(value, "$9 and $2")
    duplicated = deepcopy(value)
    duplicated["uncovered"][1] = duplicated["uncovered"][0]
    assert not validate_coverage(duplicated)
    reversed_ = deepcopy(value)
    reversed_["uncovered"].reverse()
    assert not validate_coverage(reversed_)
    assert not validate_coverage(None)
