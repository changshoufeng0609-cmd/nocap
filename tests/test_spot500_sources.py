"""Source-only screening tests: these never query the verifier or model."""
from datetime import date

from eval import spot500_sources as sources


COMPANY = {"ticker": "EXAMPLE", "name": "Example", "sec_title": "EXAMPLE CORPORATION"}


def observation(value, start="2026-01-01", end="2026-03-31", **extra):
    return {"val": value, "start": start, "end": end, "form": "10-Q", "filed": "2026-05-01",
            "accn": "test", **extra}


def facts(**concepts):
    return {"facts": {"us-gaap": {name: {"units": {"USD": rows}} for name, rows in concepts.items()}}}


def test_source_series_includes_16_week_only_for_declared_week_calendar():
    raw = facts(Revenues=[observation(100, end="2026-04-21")])
    assert sources.series_from_companyfacts(raw).get("revenue", {}) == {}
    assert sources.series_from_companyfacts(raw, irregular=True)["revenue"] == {"2026-04-21": 100}


def test_bank_total_revenue_uses_companyfacts_components():
    raw = facts(InterestIncomeExpenseNet=[observation(60)], NoninterestIncome=[observation(40)],
                NetIncomeLoss=[observation(20)])
    series = sources.series_from_companyfacts(raw)
    assert series["revenue"]["2026-03-31"] == 100
    assert series["net_margin"]["2026-03-31"] == 20


def test_margin_points_and_basis_points_are_screened_in_their_own_unit():
    series = {"operating_margin": {"2025-06-30": 20, "2026-06-30": 21.5}}
    with sources.screening_environment(COMPANY):
        for text in ("Operating margin expanded by 150 basis points year over year.",
                     "Operating margin increased by 1.5 percentage points year over year."):
            result = sources.analyze_sentence(text, series, date(2026, 6, 30), (2026, "Q2"))
            assert not result["reasons"]
            assert result["numbers"][0].screen == "ok"
        bad = sources.analyze_sentence("Operating margin contracted by 150 basis points year over year.",
                                       series, date(2026, 6, 30), (2026, "Q2"))
        assert any("XBRL mismatch" in r for r in bad["reasons"])


def test_balance_sheet_date_must_equal_the_filed_quarter_date():
    series = {"cash_and_equivalents": {"2026-06-30": 5e9}}
    with sources.screening_environment(COMPANY):
        good = sources.analyze_sentence("Cash and cash equivalents were $5 billion as of June 30, 2026.",
                                        series, date(2026, 6, 30))
        assert not good["reasons"]
        bad = sources.analyze_sentence("Cash and cash equivalents were $5 billion as of May 30, 2026.",
                                       series, date(2026, 6, 30))
        assert "balance-sheet date differs from paired quarter" in bad["reasons"]


def test_shares_outstanding_screen_uses_scaled_shares():
    with sources.screening_environment(COMPANY):
        result = sources.analyze_sentence("Common shares outstanding were 125 million.",
            {"shares_outstanding": {"2026-06-30": 125e6}}, date(2026, 6, 30))
    assert not result["reasons"]
    assert result["numbers"][0].kind == "shares"
    assert result["numbers"][0].screen == "ok"


def test_share_number_does_not_consume_balance_sheet_date():
    numbers = sources.find_numbers("June 30, 2026: 100 million shares outstanding.")
    assert [n.text for n in numbers] == ["100 million"]


def test_release_header_must_name_the_paired_quarter():
    period = {"period_end": "2026-06-30", "fiscal_year": 2026, "fiscal_period": "Q2"}
    assert sources.release_names_quarter("Example second-quarter 2026 earnings", period)
    assert sources.release_names_quarter("Results for the period ended June 30, 2026.", period)
    assert not sources.release_names_quarter("Example first-quarter 2026 earnings", period)


def test_exhibit_identification_rejects_slides_and_unknown_documents():
    assert sources.is_exhibit_991("https://sec/release.htm", b"<title>EX-99.1</title>")
    assert sources.is_exhibit_991("https://sec/costex9918-k.htm", b"<p>Earnings</p>")
    assert not sources.is_exhibit_991("https://sec/release.htm", b"<title>EX-99.2</title>")
    assert not sources.is_exhibit_991("https://sec/earnings.htm", b"<p>Earnings</p>")


def test_balance_ratios_and_diluted_share_counts_are_not_instants():
    with sources.screening_environment(COMPANY):
        ratio = sources.analyze_sentence("Total debt and preferred stock, as adjusted, to gross assets of 30%.", {}, date(2026, 6, 30))
        shares = sources.analyze_sentence("Approximately 66 million fully diluted shares outstanding.",
            {"shares_outstanding": {"2026-06-30": 66e6}}, date(2026, 6, 30))
    assert "balance-sheet ratio or change is not an instant amount" in ratio["reasons"]
    assert any("not point-in-time" in r for r in shares["reasons"])


def test_unsupported_ebitda_is_not_mistagged_as_following_cash_flow():
    text = "Alongside net income margin of 3.3%, an Adjusted EBITDA margin of 26.8% and positive operating cash flow."
    with sources.screening_environment(COMPANY):
        result = sources.analyze_sentence(text, {"net_margin": {"2026-06-30": 3.3}}, date(2026, 6, 30))
    assert result["numbers"][1].metric == "other"
    assert result["reasons"]


def test_adjusted_margin_basis_continues_past_legacy_80_character_window():
    text = "Excluding non-GAAP adjustments, adjusted gross margin of 62.9% decreased 40 bps year over year and increased 130 bps sequentially."
    with sources.screening_environment(COMPANY):
        numbers = sources.find_numbers(text)
        sources.tag_numbers(text, numbers, False)
    assert all(n.basis == "non-GAAP" for n in numbers)


def test_fifo_operating_profit_is_not_eps():
    text = "Adjusted FIFO Operating Profit of $1,544 million and Adjusted EPS of $1.58."
    with sources.screening_environment(COMPANY):
        numbers = sources.find_numbers(text)
        sources.tag_numbers(text, numbers, False)
    assert numbers[0].metric == "operating_income"
    assert numbers[1].metric == "eps_diluted"


def test_eps_impacts_exclusions_and_money_deltas_are_not_eps_levels():
    texts = (
        "Both GAAP and non-GAAP diluted net EPS included an $0.11 favorable impact from tariff refunds.",
        "The charge reduced diluted earnings per share and adjusted diluted earnings per share by $0.14.",
        "Diluted EPS, as adjusted, excludes $98 million integration costs.",
    )
    with sources.screening_environment(COMPANY):
        for text in texts:
            result = sources.analyze_sentence(text, {}, date(2026, 6, 30))
            assert any("per-share level" in r for r in result["reasons"])


def test_per_share_units_and_nearest_basis_override_broader_metric_words():
    with sources.screening_environment(COMPANY):
        nums = sources.find_numbers("Adjusted net earnings were $7.23 per share.")
        sources.tag_numbers("Adjusted net earnings were $7.23 per share.", nums, False)
        assert nums[0].metric == "eps_diluted"
        assert nums[0].basis == "non-GAAP"
        text = "GAAP net income increased 166% while Adjusted net income grew 29%."
        nums = sources.find_numbers(text)
        sources.tag_numbers(text, nums, False)
        assert [n.basis for n in nums] == ["GAAP", "non-GAAP"]


def test_written_financial_quantities_are_not_silently_omitted():
    with sources.screening_environment(COMPANY):
        result = sources.analyze_sentence("Sales were $1 billion, with a five percent increase over last year.",
                                          {"revenue": {"2026-06-30": 1e9}}, date(2026, 6, 30))
    assert "written financial quantity is outside the numeric tagger" in result["reasons"]


def test_monthly_and_unbound_respectively_statements_require_their_heading():
    with sources.screening_environment(COMPANY):
        monthly = sources.analyze_sentence("The shift impacted February sales by approximately 4.0%.", {}, date(2026, 6, 30))
        unbound = sources.analyze_sentence("Organic revenue increased 3.1% and 2.7%, respectively.", {}, date(2026, 6, 30))
    assert "monthly statement is outside the paired quarter" in monthly["reasons"]
    assert "respectively figures lack distinct local periods or measures" in unbound["reasons"]


def test_noninterest_expense_does_not_absorb_interest_or_compensation():
    for text in ("Interest expense of ~$646 million at midpoint, vs. previous ~$640 million.",
                 "Non-GAAP operating expenses guidance excludes stock-based compensation expense, totaling approximately $43 million.",
                 "The earnings per share guidance assumes the repurchase of shares with a value equal to $1.0 billion."):
        with sources.screening_environment(COMPANY):
            result = sources.analyze_sentence(text, {}, date(2026, 6, 30))
        assert result["numbers"][0].metric == "other"
        assert result["reasons"]


def test_balance_sheet_deltas_and_net_debt_are_not_total_debt():
    text = "Decreased total debt by $6.6 billion to $20.6 billion; net debt reduced to $16.5 billion."
    with sources.screening_environment(COMPANY):
        result = sources.analyze_sentence(text, {"total_debt": {"2026-06-30": 20.6e9}}, date(2026, 6, 30))
    assert result["numbers"][-1].metric == "other"
    assert "balance-sheet monetary delta is not an instant amount" in result["reasons"]


def test_adjusted_basis_word_preserves_preceding_segment_qualifier():
    for text in ('Asset Management adjusted operating net revenues were $947 million.',
                 'MIS adjusted operating margin was 64.4%.'):
        with sources.screening_environment(COMPANY):
            numbers = sources.find_numbers(text)
            sources.tag_numbers(text, numbers, False)
        assert numbers[0].qualifier in ('Asset Management', 'MIS')
        assert numbers[0].basis == 'non-GAAP'


def test_per_diluted_common_share_and_earnings_causal_clause():
    with sources.screening_environment(COMPANY):
        text = 'Adjusted net income was $5.81 per diluted common share.'
        nums = sources.find_numbers(text)
        sources.tag_numbers(text, nums, False)
        assert nums[0].metric == 'eps_diluted'
        text = 'Earnings increased 12%, due to higher noninterest income and net interest income.'
        nums = sources.find_numbers(text)
        sources.tag_numbers(text, nums, False)
        assert nums[0].metric == 'net_income'
