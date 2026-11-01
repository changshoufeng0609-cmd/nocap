"""Offline regression checks for the October 29 review's extraction findings (R3, R5).

Synthetic model proposals test how code binds a proposal to its source text: the
complete written number (R3) and wording a quote left out (R5). Verdicts use the
committed SEC snapshot. No model, SEC or held-out input is involved.
"""
from pathlib import Path

import pytest

from receipts import extract, verify
from receipts.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def store():
    result = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield result
    result.close()


def proposal(span, value_text="$96.221 billion", value=96.221, **changes):
    fields = dict(metric="revenue", metric_text="revenue", value_text=value_text, value=value,
                  unit="USD", scale="billions", period_text="Q2 FY2027", period_type="quarter",
                  comparison="none", bases=["GAAP"], forward_looking=False, entity="NVIDIA",
                  segment=None, span_text=span)
    fields.update(changes)
    return extract.ModelClaim(**fields)


def income(span, value_text="$59.688 billion", **changes):
    return proposal(span, value_text, 59.688, metric="net_income", metric_text="net income", **changes)


def growth(span, value=106, **changes):
    fields = dict(metric="revenue_growth", metric_text="revenue growth", unit="%", scale="units",
                  comparison="YoY")
    fields.update(changes)
    return proposal(span, f"{value}%", value, **fields)


def located(text, *proposals, base=0):
    warnings = []
    claims, ranges = extract.build_claims([(base, text, list(proposals))], warnings)
    for claim, (start, end) in zip(claims, ranges):
        # Offsets stay exact and global; the value sits once inside its own span.
        assert text[claim.span_start - base:claim.span_end - base] == claim.span_text
        assert text[start - base:end - base] == claim.value_text
        assert claim.span_start <= start and end <= claim.span_end
        assert claim.span_text.count(claim.value_text) == 1
    return claims, warnings


def one(text, prop, store):
    claims, warnings = located(text, prop)
    assert len(claims) == 1
    return claims[0], verify.verify(claims[0], store), warnings


# --- R3: the source decides the complete number ---------------------------------------------------

@pytest.mark.parametrize("source,value_text,expected", [
    ("NVIDIA Q2 FY2027 revenue was $96.221 million.", "$96.221", ("$96.221 million", 96.221, "millions")),
    ("NVIDIA Q2 FY2027 revenue was $96.221M.", "$96.221", ("$96.221M", 96.221, "millions")),
    ("NVIDIA Q2 FY2027 revenue was $96.221 billion.", "$96.221", ("$96.221 billion", 96.221, "billions")),
])
def test_cropped_scale_word_is_read_from_the_source(store, source, value_text, expected):
    claim, verdict, warnings = one(source, proposal(source, value_text), store)
    assert (claim.value_text, claim.value, claim.scale) == expected
    assert any(w.startswith("restored the complete source number") for w in warnings) == (value_text != expected[0])
    # 96.221 million is a thousandfold scale error, never the filed 96.221 billion.
    assert verdict.status == ("verified" if expected[2] == "billions" else "wrong")


@pytest.mark.parametrize("written", ["-$96.221 billion", "−$96.221 billion", "$-96.221 billion", "$−96.221 billion",
                                     "–$96.221 billion", "($96.221 billion)", "(US$96.221 billion)", "-US$96.221 billion"])
def test_source_sign_or_accounting_parentheses_make_the_amount_negative(store, written):
    source = f"NVIDIA Q2 FY2027 revenue was {written}."
    claim, verdict, _ = one(source, proposal(source, "96.221 billion"), store)
    assert claim.value_text == written and claim.value == pytest.approx(-96.221)
    assert verdict.status == "wrong" and verdict.status != "verified"


@pytest.mark.parametrize("written", ["−106%", "-106%"])
def test_source_minus_on_a_percent_is_kept(store, written):
    source = f"NVIDIA Q2 FY2027 revenue growth was {written} year over year."
    claim, verdict, _ = one(source, growth(source), store)
    assert claim.value_text == written and claim.value == pytest.approx(-106)
    assert verdict.status == "wrong"


@pytest.mark.parametrize("source", [
    "NVIDIA Q2 FY2027 revenue - $96.221 billion.",           # a dash separating a label, not a minus
    "NVIDIA Q2 FY2027 revenue – $96.221 billion.",
    "NVIDIA Q2 FY2027 revenue (GAAP) was $96.221 billion.",  # a parenthesis elsewhere in the sentence
])
def test_detached_dashes_and_parentheses_are_not_signs(store, source):
    claim, verdict, _ = one(source, proposal("$96.221 billion"), store)
    assert claim.value_text == "$96.221 billion" and claim.value == pytest.approx(96.221)
    assert verdict.status == "verified"


def test_a_parenthesised_percent_restates_a_figure_rather_than_negating_it(store):
    source = "NVIDIA Q2 FY2027 revenue rose $49.478 billion (106%) year over year."
    claim, _, _ = one(source, growth(source, metric_text="revenue growth"), store)
    assert claim.value_text == "106%" and claim.value == pytest.approx(106)


@pytest.mark.parametrize("written", ["€96.221 billion", "EUR 96.221 billion", "C$96.221 billion", "NT$96.221 billion",
                                     "£96.221 billion", "96.221 billion euros", "96.221 billion EUR",
                                     "96.221 billion Canadian dollars"])
def test_unsupported_currency_is_never_checked_against_usd_evidence(store, written):
    source = f"NVIDIA Q2 FY2027 revenue was {written}."
    claim, verdict, _ = one(source, proposal(source, "96.221 billion"), store)
    assert claim.value_text == written and claim.unit == "other"
    assert verdict.status == "unverifiable"


@pytest.mark.parametrize("written", ["$96.221 billion", "US$96.221 billion", "USD 96.221 billion",
                                     "96.221 billion USD", "96.221 billion U.S. dollars"])
def test_us_dollar_spellings_still_verify(store, written):
    source = f"NVIDIA Q2 FY2027 revenue was {written}."
    claim, verdict, _ = one(source, proposal(source, "96.221 billion"), store)
    assert claim.unit == "USD" and verdict.status == "verified"


@pytest.mark.parametrize("source,value_text,written", [
    ("NVIDIA Q2 FY2027 revenue was $196.221 billion.", "96.221 billion", "$196.221 billion"),
    ("NVIDIA Q2 FY2027 revenue was $96.2215 billion.", "$96.221", "$96.2215 billion"),
    ("NVIDIA Q2 FY2027 revenue was $1,096.221 billion.", "096.221 billion", "$1,096.221 billion"),
])
def test_cropped_digits_read_the_whole_source_number(store, source, value_text, written):
    claim, verdict, _ = one(source, proposal(source, value_text), store)
    assert claim.value_text == written
    assert verdict.status == "wrong"


def test_a_whole_number_is_preferred_to_digits_inside_another(store):
    # "6%" also occurs inside "56%"; the proposal names the standalone figure.
    source = "NVIDIA revenue rose 56% year over year and 6% sequentially in Q2 FY2027."
    claims, _ = located(source, growth("6% sequentially", 6, comparison="QoQ"))
    assert claims[0].value_text == "6%"
    assert source[claims[0].span_start:claims[0].span_end] == "6% sequentially"


# --- Repeated numbers and quotes -------------------------------------------------------------------

def test_repeated_cropped_numbers_keep_their_own_scales(store):
    source = "NVIDIA Q2 FY2027 revenue was $96.221 billion; other income was $96.221 million."
    first = proposal("$96.221", "$96.221")
    second = proposal("$96.221", "$96.221", metric="other", metric_text="other income")
    claims, _ = located(source, first, second)
    assert [(c.value_text, c.scale) for c in claims] == [("$96.221 billion", "billions"),
                                                         ("$96.221 million", "millions")]
    assert extract.uncovered_numbers(source, [(c.span_start + c.span_text.index(c.value_text),
                                               c.span_start + c.span_text.index(c.value_text) + len(c.value_text))
                                              for c in claims]) == []
    assert verify.verify(claims[0], store).status == "verified"


def test_a_repeated_quote_lands_on_its_next_occurrence(store):
    source = "Data Center revenue rose 5% sequentially. Gaming revenue rose 5% sequentially."
    props = [growth("rose 5% sequentially", 5, comparison="QoQ", segment="Data Center",
                    metric_text="Data Center revenue growth"),
             growth("rose 5% sequentially", 5, comparison="QoQ", segment="Gaming",
                    metric_text="Gaming revenue growth")]
    claims, _ = located(source, *props)
    assert [c.span_start for c in claims] == [source.index("rose 5%"), source.rindex("rose 5%")]
    assert len({c.span_start for c in claims}) == 2


def test_a_repeated_number_without_a_verbatim_quote_also_advances(store):
    source = "Revenue was $96.221 billion. A year later it was still $96.221 billion."
    props = [proposal("revenue: $96.221 billion"), proposal("revenue: $96.221 billion")]
    claims, warnings = located(source, *props)
    starts = [c.span_start + c.span_text.index(c.value_text) for c in claims]
    assert starts == [source.index("$96.221"), source.rindex("$96.221")]
    assert sum(w.startswith("the quote for") for w in warnings) == 2


def test_the_same_number_claimed_twice_still_shares_its_only_occurrence(store):
    source = "NVIDIA GAAP and non-GAAP gross margins were both 75.0% in Q2 FY2027."
    margin = dict(metric="gross_margin", metric_text="gross margin", unit="%", scale="units")
    props = [proposal("gross margins were both 75.0%", "75.0%", 75.0, bases=["GAAP"], **margin),
             proposal("gross margins were both 75.0%", "75.0%", 75.0, bases=["non-GAAP"], **margin)]
    claims, _ = located(source, *props)
    assert len(claims) == 2 and len({(c.span_start, c.span_end) for c in claims}) == 1


# --- R5: source wording a quote omitted ------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "NVIDIA Q2 FY2027 net income excluding stock compensation was $59.688 billion.",
    "NVIDIA Q2 FY2027 net income from discontinued operations was $59.688 billion.",
    "NVIDIA Q2 FY2027 European net income was $59.688 billion.",
    "NVIDIA Q2 FY2027 U.S. net income was $59.688 billion.",
    "NVIDIA Q2 FY2027 net income attributable to noncontrolling interests was $59.688 billion.",
])
@pytest.mark.parametrize("quote", ["$59.688 billion", "was $59.688 billion"])
def test_number_only_quote_cannot_drop_a_leading_qualification(store, source, quote):
    claim, verdict, warnings = one(source, income(quote), store)
    assert verdict.status == "unverifiable"
    assert any(w.startswith("restored source wording") for w in warnings)
    assert "net income" in claim.span_text


@pytest.mark.parametrize("region", ["U.S.", "Greater China"])
def test_number_only_quote_cannot_drop_a_region(store, region):
    source = f"NVIDIA Q2 FY2027 {region} revenue was $96.221 billion."
    claim, verdict, _ = one(source, proposal("$96.221 billion"), store)
    assert verdict.status == "unverifiable"
    assert claim.span_text.startswith(f"NVIDIA Q2 FY2027 {region} revenue")


@pytest.mark.parametrize("tail", [
    ", excluding stock compensation", " excluding stock compensation", " (excluding stock compensation)",
    " from discontinued operations", " in Europe", " attributable to noncontrolling interests",
    " before taxes",
])
@pytest.mark.parametrize("quote", ["$59.688 billion", "net income was $59.688 billion"])
def test_a_trailing_qualification_outside_the_quote_is_restored(store, tail, quote):
    source = f"NVIDIA Q2 FY2027 net income was $59.688 billion{tail}."
    claim, verdict, _ = one(source, income(quote), store)
    assert verdict.status == "unverifiable"
    assert claim.span_text.endswith(tail.strip(" ,"))


@pytest.mark.parametrize("source,quote", [
    ("NVIDIA Q2 FY2027 net income was $59.688 billion.", "$59.688 billion"),
    ("NVIDIA Q2 FY2027 net income was $59.688 billion.", "net income was $59.688 billion"),
    ("NVIDIA Q2 FY2027 GAAP net income was $59.688 billion.", "$59.688 billion"),
    ("NVIDIA Q2 FY2027 net income was $59.688 billion for the quarter.", "$59.688 billion"),
    ("In Q2 FY2027, NVIDIA net income was $59.688 billion, compared with $26.4 billion a year ago.",
     "net income was $59.688 billion"),
])
def test_unqualified_controls_verify_and_keep_the_model_quote(store, source, quote):
    claim, verdict, warnings = one(source, income(quote), store)
    assert verdict.status == "verified"
    assert claim.span_text == quote  # nothing restored: no reading changed
    assert not any(w.startswith("restored source wording") for w in warnings)


def test_a_change_inherits_a_qualified_subject_across_its_level_amount(store):
    source = "NVIDIA revenue excluding China was $96.221 billion, up 106% year over year in Q2 FY2027."
    claim, verdict, _ = one(source, growth("up 106% year over year"), store)
    assert verdict.status == "unverifiable"
    assert claim.span_text.startswith("NVIDIA revenue excluding China")


def test_an_unqualified_change_keeps_its_short_quote(store):
    source = "NVIDIA revenue was $96.221 billion, up 106% year over year in Q2 FY2027."
    claim, verdict, _ = one(source, growth("up 106% year over year"), store)
    assert verdict.status == "verified"
    assert claim.span_text == "up 106% year over year"


def test_restoration_never_crosses_another_amount_for_a_level_figure(store):
    source = "NVIDIA net income excluding stock compensation was $10 billion, and revenue was $96.221 billion."
    claim, verdict, _ = one(source, proposal("$96.221 billion"), store)
    assert verdict.status == "verified" and claim.span_text == "$96.221 billion"


def test_restoration_stays_inside_the_sentence(store):
    source = "Net income excluding stock compensation rose. NVIDIA Q2 FY2027 net income was $59.688 billion."
    claim, verdict, _ = one(source, income("$59.688 billion"), store)
    assert verdict.status == "verified" and claim.span_text == "$59.688 billion"


@pytest.mark.parametrize("comparison", ["QoQ", "none"])
def test_comparison_wording_outside_the_quote_decides_the_base(store, comparison):
    source = "NVIDIA Q2 FY2027 revenue climbed 106% from a year earlier."
    claim, verdict, _ = one(source, growth("revenue climbed 106%", comparison=comparison), store)
    assert verdict.status == "verified"
    assert claim.span_text == "revenue climbed 106% from a year earlier"


def test_matching_comparison_metadata_keeps_the_short_quote(store):
    source = "NVIDIA Q2 FY2027 revenue climbed 106% from a year earlier."
    claim, verdict, _ = one(source, growth("revenue climbed 106%"), store)
    assert verdict.status == "verified" and claim.span_text == "revenue climbed 106%"


def test_a_sign_word_in_the_next_clause_is_not_borrowed(store):
    # Only "from a year ago" belongs to 106%; "down from" describes the following figure.
    source = "NVIDIA Q2 FY2027 revenue grew 106% from a year ago, down from 122% growth a quarter earlier."
    claim, verdict, _ = one(source, growth("revenue grew 106%", comparison="none"), store)
    assert "down" not in claim.span_text
    assert verdict.status == "verified"


def test_up_to_from_a_starting_amount_outside_the_quote_is_ambiguous(store):
    source = "NVIDIA Q2 FY2027 revenue was up to $96.2 billion from $46.743 billion a year earlier."
    claim, verdict, _ = one(source, proposal("up to $96.2 billion", "$96.2 billion", 96.2), store)
    assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous")
    assert claim.span_text == "up to $96.2 billion from $46.743 billion"


def test_a_level_amount_labelled_a_year_ago_outside_the_quote_uses_that_period(store):
    source = "NVIDIA revenue was $46.743 billion a year ago."
    claim, verdict, _ = one(source, proposal("revenue was $46.743 billion", "$46.743 billion", 46.743), store)
    assert verdict.status == "verified" and claim.span_text.endswith("a year ago")
    assert {f.fiscal_year for f in verdict.evidence} == {2026}


def test_global_offsets_survive_restoration(store):
    source = "NVIDIA Q2 FY2027 net income excluding stock compensation was $59.688 billion."
    claims, _ = located(source, income("$59.688 billion"), base=1234)
    assert claims[0].span_start == 1234 + source.index("NVIDIA")


# --- The restoration oracle stays in step with the checker -----------------------------------------

@pytest.mark.parametrize("span,value_text,changes", [
    ("Revenue climbed 106% from a year earlier.", "106%", dict(metric="revenue_growth", unit="%", scale="units",
                                                               value=106, comparison="QoQ")),
    ("GAAP net income fell to $59.688 billion.", "$59.688 billion", dict(metric="net_income", value=59.688)),
    ("Adjusted operating loss was more than $1.2 billion a year ago.", "$1.2 billion",
     dict(metric="operating_income", value=1.2)),
    ("Data Center revenue was $89.0 billion.", "$89.0 billion", dict(value=89.0)),
])
def test_span_reading_matches_what_the_check_uses(store, span, value_text, changes):
    fields = dict(id="c1", metric="revenue", metric_text="revenue", value_text=value_text, value=96.221,
                  unit="USD", scale="billions", period_text="Q2 FY2027", period_type="quarter",
                  comparison="none", basis=None, forward_looking=False, entity="NVIDIA", segment=None,
                  span_text=span, span_start=0, span_end=len(span))
    fields.update(changes)
    claim = verify.Claim(**fields)
    reading = verify.interpret(claim) or verify.Reading(claim.metric, None, None)
    check = verify._Check(claim, store, verify.resolve_claim_entity(claim, store), None, None, reading)
    terms = verify.span_reading(claim)[2]
    assert (terms.growth_base, terms.comparison, terms.comparison_ambiguous, terms.segment, terms.metric,
            terms.basis, terms.hedge, terms.negative) == (
        check.growth_base, check.comparison, check.comparison_ambiguous, check.segment, check.metric,
        check.basis, check.hedge, check.claimed < 0)


# --- Adversarial review follow-up ------------------------------------------------------------------

@pytest.mark.parametrize("written", ["C$ 96.221 billion", "€ 96.221 billion", "£ 96.221 billion", "NT$ 96.221 billion",
                                     "96.221 billion Japanese yen", "96.221 billion Swiss francs",
                                     "96.221 billion British pounds", "96.221 billion ringgit", "Rs. 96.221 billion"])
def test_spaced_and_named_currencies_are_never_usd(store, written):
    source = f"NVIDIA Q2 FY2027 revenue was {written}."
    for value_text in ("96.221 billion", written):
        claim, verdict, _ = one(source, proposal(source, value_text), store)
        assert claim.unit == "other" and verdict.status == "unverifiable", (value_text, claim.value_text)


@pytest.mark.parametrize("written", ["US $96.221 billion", "U.S. $96.221 billion", "U.S.$96.221 billion",
                                     "$ 96.221 billion"])
def test_other_us_dollar_spellings_keep_strict_bounds(store, written):
    source = f"NVIDIA Q2 FY2027 revenue was more than {written}."
    claim, verdict, _ = one(source, proposal(source, "$96.221 billion" if "$9" in written else "96.221 billion"), store)
    assert claim.unit == "USD" and verify.hedge_of(claim) is not None
    assert verdict.status == "wrong"


@pytest.mark.parametrize("written,scale", [("$96.221 Million", "millions"), ("$96.221 MILLION", "millions"),
                                           ("$96.221 mln", "millions"), ("$96.221-million", "millions"),
                                           ("$96.221 Billion", "billions"), ("$96.221 bln", "billions")])
def test_scale_words_in_any_case_or_abbreviation_come_from_the_source(store, written, scale):
    source = f"NVIDIA Q2 FY2027 revenue was {written}."
    claim, verdict, _ = one(source, proposal(source, "$96.221"), store)
    assert claim.scale == scale
    assert verdict.status == ("verified" if scale == "billions" else "wrong")


def test_a_parenthesised_percentage_is_a_restatement_not_a_negative(store):
    source = "NVIDIA Q2 FY2027 revenue rose $49.478 billion (106%) year over year."
    for value_text in ("(106%)", "106%"):
        claim, verdict, _ = one(source, growth("$49.478 billion (106%) year over year") .model_copy(
            update={"value_text": value_text}), store)
        assert claim.value == pytest.approx(106) and verdict.status == "verified"


def test_a_parenthesised_plain_figure_is_an_accounting_negative(store):
    source = "NVIDIA Q2 FY2027 net income (in millions) was (59,688)."
    prop = proposal("net income (in millions) was (59,688)", "59,688", 59688, metric="net_income",
                    metric_text="net income", scale="millions")
    claim, verdict, _ = one(source, prop, store)
    assert claim.value_text == "(59,688)" and claim.value == pytest.approx(-59688)
    assert verdict.status != "verified"  # a filed profit never supports a negative amount


@pytest.mark.parametrize("source", [
    "NVIDIA Q2 FY2027 revenue was $46.743 billion, a year ago it was $30.040 billion.",
    "NVIDIA Q2 FY2027 revenue was $46.743 billion (a year ago: $30.0 billion).",
    "NVIDIA Q2 FY2027 revenue was $46.743 billion, prior-year revenue was $30 billion.",
])
@pytest.mark.parametrize("quote", ["revenue was $46.743 billion", None])
def test_a_label_introducing_the_next_amount_does_not_date_this_one(store, source, quote):
    claim, verdict, _ = one(source, proposal(quote or source, "$46.743 billion", 46.743), store)
    assert (verdict.status, verdict.reason) == ("wrong", "period_mismatch")


@pytest.mark.parametrize("source,quote", [
    ("NVIDIA Q2 FY2027 European net income was $59.688 billion.", "net income was $59.688 billion"),
    ("NVIDIA Q2 FY2027 U.S. revenue was $96.221 billion.", "revenue was $96.221 billion"),
    ("Excluding stock compensation, NVIDIA Q2 FY2027 net income was $59.688 billion.", "$59.688 billion"),
    ("Excluding stock compensation, NVIDIA Q2 FY2027 net income was $59.688 billion.", "net income was $59.688 billion"),
])
def test_modifiers_and_fronted_qualifications_outside_a_named_metric_quote(store, source, quote):
    prop = income(quote) if "net income" in source else proposal(quote)
    claim, verdict, _ = one(source, prop, store)
    assert verdict.status == "unverifiable"


def test_data_center_wording_outside_a_named_metric_quote_is_not_checked_against_the_total(store):
    source = "NVIDIA Q2 FY2027 Data Center revenue was $89.0 billion."
    claim, verdict, _ = one(source, proposal("revenue was $89.0 billion", "$89.0 billion", 89.0), store)
    assert verdict.status != "wrong" and claim.span_text.startswith("NVIDIA Q2 FY2027 Data Center revenue")


def test_a_qualification_after_the_comparison_is_restored(store):
    source = "NVIDIA revenue grew 106% year over year excluding China in Q2 FY2027."
    for quote in ("revenue grew 106% year over year", source):
        claim, verdict, _ = one(source, growth(quote), store)
        assert verdict.status == "unverifiable", quote


def test_a_direction_word_outside_the_quote_signs_the_change(store):
    source = "NVIDIA Q2 FY2027 revenue was $96.221 billion. That was down 106% from a year earlier."
    claim, verdict, _ = one(source, growth("106% from a year earlier"), store)
    assert claim.span_text.startswith("down") and verdict.status == "wrong"


def test_constant_currency_wording_outside_the_quote_is_unverifiable(store):
    source = "NVIDIA Q2 FY2027 revenue grew 106% at constant currency."
    claim, verdict, _ = one(source, growth("revenue grew 106%"), store)
    assert verdict.status == "unverifiable" and claim.span_text.endswith("constant currency")


def test_a_named_quarter_of_last_year_is_not_a_year_over_year_comparison(store):
    source = "NVIDIA Q1 FY2027 revenue rose 19.8% from the fourth quarter of last year."
    for quote in ("revenue rose 19.8%", source):
        claim, verdict, _ = one(source, growth(quote, 19.8, comparison="QoQ", period_text="Q1 FY2027"), store)
        assert verdict.status == "verified", quote


def test_up_to_compared_with_a_starting_amount_is_ambiguous(store):
    source = "NVIDIA Q2 FY2027 revenue was up to $96.2 billion, compared with $46.743 billion a year earlier."
    for quote in ("up to $96.2 billion", source):
        claim, verdict, _ = one(source, proposal(quote, "$96.2 billion", 96.2), store)
        assert (verdict.status, verdict.reason) == ("unverifiable", "ambiguous"), quote


def test_two_claims_on_one_number_never_borrow_digits_inside_another(store):
    source = "Gaming revenue rose 15% year over year. GAAP and non-GAAP operating income each rose 5% year over year."
    props = [growth("5% year over year", 5, metric="operating_income", metric_text="operating income growth",
                    bases=[basis]) for basis in ("GAAP", "non-GAAP")]
    claims, _ = located(source, *props)
    assert [c.value_text for c in claims] == ["5%", "5%"]
    assert {c.span_start + c.span_text.index("5%") for c in claims} == {source.index(" 5%") + 1}


def test_a_later_repeat_of_the_value_is_cut_from_the_quote(store):
    source = ("NVIDIA Q2 FY2027 net income excluding stock compensation was $59.688 billion, "
              "unchanged from $59.688 billion a year ago.")
    claim, verdict, _ = one(source, income(source), store)
    assert claim.span_text.count("$59.688 billion") == 1 and verdict.status == "unverifiable"


# --- Remaining tokenizer limits ----------------------------------------------------------------------

@pytest.mark.parametrize("written,scale,status", [
    ("$US96.221 billion", "billions", "verified"),
    ("$96.221 B", "billions", "verified"),
    ("$96.221 M", "millions", "wrong"),
    ("96.221 bn", "billions", "verified"),
])
def test_dollar_us_and_spaced_scale_abbreviations(store, written, scale, status):
    source = f"NVIDIA Q2 FY2027 revenue was {written}."
    claim, verdict, _ = one(source, proposal(source, "96.221"), store)
    assert claim.value_text == written and claim.scale == scale and claim.unit == "USD"
    assert verdict.status == status


def test_dollar_us_keeps_a_strict_bound(store):
    source = "NVIDIA Q2 FY2027 revenue was more than $US96.221 billion."
    claim, verdict, _ = one(source, proposal(source, "96.221 billion"), store)
    assert verify.hedge_of(claim) is not None and verdict.status == "wrong"


def test_a_scale_after_accounting_parentheses_is_read(store):
    source = "NVIDIA Q2 FY2027 revenue was $(96.221) million."
    claim, verdict, _ = one(source, proposal(source, "$(96.221)", -96.221), store)
    assert claim.value_text == "$(96.221) million" and claim.scale == "millions"
    assert claim.value == pytest.approx(-96.221) and verdict.status == "wrong"


@pytest.mark.parametrize("text", ["completed 5 M&A deals", "a 2 B2B pilot", "7 K-12 schools"])
def test_spaced_letters_after_a_count_are_not_scales(text):
    assert [t["scale"] for t in extract.scan_numbers(text)] == [None]
