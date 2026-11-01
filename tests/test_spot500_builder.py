"""Source-only spot500 construction checks; no verifier or provider is involved."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import re

import pytest

from eval import build_corpus as base
from eval import build_spot500 as spot


DOC = {
    "doc_id": "EXCO-FY2026Q3", "ticker": "EXCO", "fy": 2026, "fp": "Q3",
    "period_end": "2026-09-30", "context": "Example Co (EXCO), third quarter of fiscal 2026",
    "quarter_days": 91,
}
COMPANY = {"name": "Example Co", "sector": "Industrials", "sub_industry": "Machinery"}


def sentence(text, *, growth=True, sign=1, metric="revenue"):
    numbers = []
    for number in base.find_numbers(text):
        number.metric = metric
        number.growth = growth
        number.sign = sign
        number.screen = "ok"
        number.screen_detail = f"xbrl={sign * number.value:g}"
        numbers.append(asdict(number))
    return {"sentence_id": "EXCO-FY2026Q3-s000001", "doc_id": DOC["doc_id"], "ticker": "EXCO",
            "kind": "verifiable", "text": text, "numbers": numbers}


@pytest.mark.parametrize("before,after,mention,metric,sign", [
    ("rose", "fell", "5%", "revenue", 1),
    ("fell", "rose", "5%", "revenue", -1),
    ("grew", "declined", "4.5%", "revenue", 1),
    ("expanded", "contracted", "35 basis points", "operating_margin", 1),
    ("contracted", "expanded", "0.5 percentage points", "gross_margin", -1),
    ("increased", "decreased", "2%", "net_income", 1),
    ("Rose", "Fell", "5%", "revenue", 1),
    ("ROSE", "FELL", "5%", "revenue", 1),
])
def test_direction_changes_only_one_word_and_preserves_numbers(before, after, mention, metric, sign):
    source = sentence(f"Reported {metric.replace('_', ' ')} {before} by {mention} year over year.",
                      metric=metric, sign=sign)
    unchanged = deepcopy(source)
    mutation = spot.mutate_direction(source, DOC, {})
    assert mutation is not None
    assert mutation.mutator == "direction"
    assert mutation.text == source["text"].replace(before, after, 1)
    assert mutation.edit["before"] == before
    assert mutation.edit["after"] == after
    assert re.findall(r"\d+(?:\.\d+)?", mutation.text) == re.findall(r"\d+(?:\.\d+)?", source["text"])
    assert mutation.text[slice(*mutation.target_span)] == mention
    assert mutation.affected_value_texts == [mention]
    assert mutation.expected_value == sign * source["numbers"][0]["value"]
    assert source == unchanged
    # Direction construction is repeatable and never depends on mutable RNG state.
    assert asdict(mutation) == asdict(spot.mutate_direction(source, DOC, {}))


@pytest.mark.parametrize("field,value", [
    ("value", 0), ("screen", "na"), ("screen", "mismatch"), ("growth", False),
    ("role", "prior"), ("role", "forward"), ("approx", "about"), ("fx_adjusted", True),
    ("is_range", True), ("qualifier", "Industrial segment"),
])
def test_direction_rejects_unproved_or_ambiguous_changes(field, value):
    source = sentence("Revenue rose 5% year over year.")
    source["numbers"][0][field] = value
    assert spot.mutate_direction(source, DOC, {}) is None


@pytest.mark.parametrize("screen_detail", ["xbrl=0", "xbrl=-0.01", "unavailable"])
def test_direction_requires_actual_nonzero_matching_sign(screen_detail):
    # Rounding tolerance alone can accept a small stated increase despite a flat
    # or negative actual change; that is not a sound source for flipping direction.
    source = sentence("Revenue rose 0.1% year over year.")
    source["numbers"][0]["screen_detail"] = screen_detail
    assert spot.mutate_direction(source, DOC, {}) is None


@pytest.mark.parametrize("text", [
    "Gross margin rose to 25% in the quarter.",
    "Gross margin fell from 25% in the quarter.",
    "Revenue and earnings rose 5% and 8% respectively.",
    "Revenue rose. Growth was 5% in the quarter.",
    "Revenue was unchanged at 5% year over year.",
])
def test_direction_rejects_levels_shared_verbs_and_remote_words(text):
    assert spot.mutate_direction(sentence(text), DOC, {}) is None


def test_direction_keeps_other_independently_worded_claims_true():
    source = sentence("Revenue rose 5%; operating margin expanded 30 basis points.")
    source["numbers"][1]["metric"] = "operating_margin"
    mutation = spot.mutate_direction(source, DOC, {})
    item = spot.make_item(source, DOC, COMPANY, mutation)
    assert [gold["role"] for gold in item["gold_claims"]] == ["target", "true"]
    assert [gold["expected_status"] for gold in item["gold_claims"]] == ["wrong", "verified"]
    assert [gold["expected_reason"] for gold in item["gold_claims"]] == ["value_mismatch", "match"]
    assert "operating margin expanded 30 basis points" in item["text"]
    assert all(item["text"][slice(*gold["span"])] == gold["value_text"] for gold in item["gold_claims"])


def test_gold_target_uses_occurrence_not_equal_value_text():
    source = sentence("Gross margin was 8%; revenue rose 8% year over year.")
    source["numbers"][0]["growth"] = False
    source["numbers"][0]["metric"] = "gross_margin"
    mutation = spot.mutate_direction(source, DOC, {})
    item = spot.make_item(source, DOC, COMPANY, mutation)
    assert [gold["role"] for gold in item["gold_claims"]] == ["true", "target"]
    assert len({tuple(gold["span"]) for gold in item["gold_claims"]}) == 2


def test_direction_gold_bytes_are_deterministic_across_input_dict_order():
    source = sentence("Revenue grew 12.5% year over year.")
    reordered = dict(reversed(list(source.items())))
    first = spot.make_item(source, DOC, COMPANY, spot.mutate_direction(source, DOC, {}))
    second = spot.make_item(reordered, dict(reversed(list(DOC.items()))), COMPANY,
                            spot.mutate_direction(reordered, DOC, {}))
    assert spot.canonical(first) == spot.canonical(second)


def selection_fixture():
    companies, docs, sentences = {}, {}, []
    special = iter(("COST", "KR", "PEP", "AZO"))
    for sector_index, (sector, industries) in enumerate(sorted(spot.SECTOR_GROUPS.items())):
        industry = "Diversified Banks" if sector == "Financials" else industries.split("|")[0]
        for offset in range(4):
            ticker = next(special) if sector == "Consumer Staples" else f"S{sector_index}C{offset}"
            company = {"ticker": ticker, "name": ticker, "sector": sector, "sub_industry": industry, "series": {}}
            doc = {**DOC, "doc_id": ticker + "-FY2026Q3", "ticker": ticker,
                   "quarter_days": 119 if ticker in spot.IRREGULAR else 91}
            source = sentence("Revenue grew 5% year over year.")
            source.update(ticker=ticker, doc_id=doc["doc_id"], sentence_id=doc["doc_id"] + "-s000001")
            # Source-defined feature coverage, independent of NoCap outcomes.
            metric = ("net_margin", "operating_margin", "gross_margin", "cash_and_equivalents")[offset]
            source["numbers"][0]["metric"] = "revenue" if sector == "Financials" else metric
            if metric == "operating_margin" and sector != "Financials":
                source["numbers"][0]["kind"] = "bps"
            companies[ticker], docs[doc["doc_id"]] = company, doc
            sentences.append(source)
    for ticker, kind in (("NVDA", "historical"), ("FORWARDONLY", "forward_looking")):
        companies[ticker] = {**COMPANY, "ticker": ticker, "sub_industry": "Industrial Conglomerates"}
        doc = {**DOC, "doc_id": ticker + "-FY2026Q3", "ticker": ticker}
        source = sentence("Revenue grew 5% year over year.")
        source.update(ticker=ticker, doc_id=doc["doc_id"], sentence_id=doc["doc_id"] + "-s000001", kind=kind)
        docs[doc["doc_id"]] = doc
        sentences.append(source)
    return companies, docs, sentences


def test_company_selection_is_order_independent_seeded_and_constrained():
    companies, docs, sources = selection_fixture()
    before = deepcopy((companies, docs, sources))
    selected, metadata = spot.select_companies(companies, docs, sources)
    reordered = spot.select_companies(dict(reversed(list(companies.items()))),
                                      dict(reversed(list(docs.items()))), list(reversed(sources)))
    assert (selected, metadata) == reordered
    assert (companies, docs, sources) == before
    assert len(selected) == len(set(selected)) == 30
    assert selected == sorted(selected)
    assert not set(selected) & spot.EXCLUDED
    assert "FORWARDONLY" not in selected
    assert sum(spot.is_bank(companies[t]) for t in selected) >= 3
    assert sum(spot.is_utility_reit(companies[t]) for t in selected) >= 2
    assert len(set(selected) & spot.IRREGULAR) >= 2
    assert {spot.SECTOR_BY_INDUSTRY[companies[t]["sub_industry"]] for t in selected} == set(spot.SECTOR_GROUPS)


def test_company_selection_refuses_insufficient_constraint_pool():
    companies, docs, sources = selection_fixture()
    sources = [s for s in sources if not spot.is_bank(companies[s["ticker"]])]
    with pytest.raises(ValueError, match="banks"):
        spot.select_companies(companies, docs, sources)


@pytest.mark.parametrize("ticker,industry,expected", [
    ("BLK", "Asset Management & Custody Banks", False),
    ("BEN", "Asset Management & Custody Banks", False),
    ("RF", "Regional Banks", True),
    ("WFC", "Diversified Banks", True),
    ("GS", "Investment Banking & Brokerage", False),
])
def test_bank_quota_uses_bank_industry_not_bank_substring(ticker, industry, expected):
    assert spot.is_bank({"ticker": ticker, "sub_industry": industry}) is expected


def test_company_selection_reserves_screened_margin_direction_when_available():
    companies, docs, sources = selection_fixture()
    for source in sources:
        for number in source["numbers"]:
            number["metric"] = "revenue"
    margin_source = sources[0]
    replacement = sentence("Operating margin expanded 30 basis points year over year.", metric="operating_margin")
    replacement.update({key: margin_source[key] for key in ("sentence_id", "doc_id", "ticker")})
    sources[0] = replacement
    selected, metadata = spot.select_companies(companies, docs, sources)
    assert replacement["ticker"] in selected
    reservation = next(r for r in metadata["reservations"] if r["constraint"] == "screened margin direction")
    assert reservation["minimum"] == 1


def test_frozen_writer_is_repeatable_and_refuses_changed_bytes(tmp_path):
    path = tmp_path / "new" / "corpus.jsonl"
    original = spot.encode_jsonl([{"b": 2, "a": "first"}, {"a": "second", "b": 3}])
    reordered = spot.encode_jsonl([{"a": "first", "b": 2}, {"b": 3, "a": "second"}])
    assert original == reordered
    spot.write_frozen(path, original)
    spot.write_frozen(path, reordered)
    with pytest.raises(ValueError, match="Refusing changed frozen output"):
        spot.write_frozen(path, b"different\n")
    assert path.read_bytes() == original


@pytest.mark.parametrize("text", [
    "Both GAAP and non-GAAP diluted EPS included a $0.11 favorable impact.",
    "This reduced adjusted diluted earnings per share by $0.14.",
    "Adjusted diluted EPS excludes $98 million of integration costs.",
    "Adjusted diluted EPS was $98 million.",
])
def test_basis_mutation_rejects_adjustment_amounts_and_wrong_eps_units(text):
    source = sentence(text, growth=False, metric="eps_diluted")
    source["numbers"][0].update(basis="non-GAAP", screen="na", screen_detail=None)
    companies = {"EXCO": {"series": {"eps_diluted": {DOC["period_end"]: 0.71}}}}
    assert spot.mutate_single_basis(source, DOC, companies) is None


def test_basis_mutation_accepts_explicit_reported_level_with_different_gaap_value():
    source = sentence("Adjusted diluted EPS was $1.25.", growth=False, metric="eps_diluted")
    source["numbers"][0].update(basis="non-GAAP", screen="na", screen_detail=None)
    companies = {"EXCO": {"series": {"eps_diluted": {DOC["period_end"]: 1.0}}}}
    mutation = spot.mutate_single_basis(source, DOC, companies)
    assert mutation is not None
    assert mutation.text == "GAAP diluted EPS was $1.25."
    assert mutation.expected_value == 1.0


def test_threshold_sentence_cannot_generate_false_scale_or_value_targets():
    source = sentence("The company surpassed $300 billion in total assets.",
                      growth=False, metric="total_assets")
    # Even if a legacy number tag missed the bound word, the candidate gate
    # must not pretend 'surpassed $300 million' is now false.
    assert source["numbers"][0]["approx"] is None
    assert not spot.mutation_candidates([source], {DOC["doc_id"]: DOC}, {"EXCO": COMPANY})


@pytest.mark.parametrize("text", [
    "The retrospective application resulted in an increase to non-GAAP net income of $19.7 million.",
    "Foreign exchange rate changes increased adjusted operating expenses by $15.7 million.",
    "This is compared to reported and adjusted EPS of $1.76 for the first quarter of 2025.",
    "Reported and adjusted EPS were $1.76 for the third quarter of 2026.",
    "Adjusted diluted EPS was $1.76 in the third quarter of 2025.",
    "Adjusted diluted EPS increased $1.76 in the quarter.",
])
def test_basis_mutation_rejects_deltas_prior_periods_and_shared_basis_values(text):
    # Even if legacy tagging calls a figure current, the source wording must
    # establish a current reported level before a single-basis swap is valid.
    source = sentence(text, growth=False, metric="eps_diluted")
    source["numbers"][0].update(basis="non-GAAP", screen="na", screen_detail=None)
    companies = {"EXCO": {"series": {"eps_diluted": {DOC["period_end"]: 0.71}}}}
    assert spot.mutate_single_basis(source, DOC, companies) is None


@pytest.mark.parametrize("number", ["0.911", "0.9137"])
def test_source_rejects_eps_precision_that_may_include_glued_footnotes(number):
    from eval import spot500_sources as sources

    with sources.screening_environment({**COMPANY, "ticker": "EXCO", "sec_title": "Example Co"}):
        analysis = sources.analyze_sentence(f"Non-GAAP earnings per share of ${number}.",
                                             {}, date(2026, 9, 30), (2026, "Q3"))
    assert "EPS precision may contain a glued numeric footnote" in analysis["reasons"]


def test_source_preserves_two_decimal_reported_eps():
    from eval import spot500_sources as sources

    with sources.screening_environment({**COMPANY, "ticker": "EXCO", "sec_title": "Example Co"}):
        analysis = sources.analyze_sentence("Non-GAAP earnings per share of $0.91.",
                                             {}, date(2026, 9, 30), (2026, "Q3"))
    assert not analysis["reasons"]
    assert analysis["numbers"][0].text == "$0.91"


def test_year_over_year_is_not_a_numeric_bound():
    assert spot.mutation_sentence_safe(sentence('Revenue rose 5% year over year.'))
    assert not spot.mutation_sentence_safe(sentence('Revenue was over $5 million.'))
