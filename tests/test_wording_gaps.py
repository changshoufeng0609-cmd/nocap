"""Seven wordings that left a true, filed figure at "?" (a frozen one-shot evaluation, Nov 1).

Each rule is tested both ways over the committed SEC snapshot: the true figure verifies with its receipt, a wrong figure
in the same wording is wrong, and look-alike wordings stay "?". Typed claims are built the way extraction delivers
them; the extraction tests run the real post-processing on synthetic model proposals. No model, network or SEC call.
Basic and diluted EPS differ for Amgen ($4.40 / $4.37), Akamai ($0.55 / $0.52) and Disney ($1.52 / $1.51).
"""
from pathlib import Path

import pytest

import receipts.verify as verifier
from receipts import extract
from receipts.store import Store
from receipts.types import Claim
from receipts.verify import claimed_value, span_reading, verify

ROOT = Path(__file__).resolve().parents[1]
AMGN_10Q = "0000318154-26-000126"   # Amgen Q2 2026 10-Q: diluted EPS $4.37, basic $4.40, net income $2,375M
AKAM_PR = "0001086222-26-000083"    # Akamai Q2 2026 release: non-GAAP EPS $1.59
CNC_10Q = "0001071739-26-000192"    # Centene Q3 2026 10-Q: net loss $71M, EPS -$0.14
TGT_10Q = "0000027419-26-000042"    # Target Q2 FY2026 10-Q: revenue $26,539M, Q2 FY2025 $25,211M
DIS_10Q = "0001744489-26-000057"    # Disney Q3 FY2026 10-Q: net income $2,638M, with noncontrolling interests $2,844M
TFC_10Q = "0000092230-26-000099"    # Truist Q2 2026 10-Q: noninterest income $1,644M, Q2 2025 $1,400M
RF_10Q = "0001281761-26-000055"     # Regions Q2 2026 10-Q: noninterest income $630M, Q2 2025 $646M


@pytest.fixture(scope="module")
def snapshot():
    store = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    try:
        yield store
    finally:
        store.close()


def claim(span, value_text, value, *, metric, metric_text, unit="USD/share", scale="units", entity="Amgen",
          period_text="Q2 2026", basis=None, comparison="none"):
    return Claim(id="c1", metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                 scale=scale, period_text=period_text, period_type="quarter", comparison=comparison, basis=basis,
                 forward_looking=False, entity=entity, segment=None, span_text=span, span_start=0, span_end=len(span))


def per_share(span, value, **changes):
    return claim(span, f"${value:.2f}" if value >= 0 else f"$({-value:.2f})", value, metric="eps_diluted",
                 metric_text=changes.pop("metric_text", "per share"), **changes)


def unread(verdict):
    return verdict.status == "unverifiable" and verdict.expected_value is None


def proposal(span_text, value_text, value, *, metric, metric_text, unit, scale, entity, period_text, bases=(),
             comparison="none"):
    return extract.ModelClaim(metric=metric, metric_text=metric_text, value_text=value_text, value=value, unit=unit,
                              scale=scale, period_text=period_text, period_type="quarter", comparison=comparison,
                              bases=list(bases), forward_looking=False, entity=entity, segment=None,
                              span_text=span_text)


def located(text, *proposals):
    claims, _ = extract.build_claims([(0, text, list(proposals))], [])
    for c in claims:
        assert text[c.span_start:c.span_end] == c.span_text and c.span_text.count(c.value_text) == 1
    return claims


# --- 1. A bare per-share figure attached to net income is EPS ------------------------------------------------------

@pytest.mark.parametrize("span,metric_text", [
    ("Amgen's GAAP net income was $2.38 billion, or $4.37 per share.", "per share"),
    ("Amgen's net income was $2.38 billion ($4.37 per share).", "per share"),
    ("Amgen's net income was $2.38 billion, or $4.37 a share, in the second quarter of 2026.", "a share"),
    ("Amgen's net income attributable to Amgen was $2.38 billion, or $4.37 per share.", "per share"),
    ("Net income attributable to common stockholders was $2.38 billion, or $4.37 per common share.",
     "per common share"),
])
def test_per_share_figure_attached_to_net_income_is_diluted_eps(snapshot, span, metric_text):
    v = verify(per_share(span, 4.37, metric_text=metric_text), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.metric == "eps_diluted" and f.value == 4.37 and f.accession == AMGN_10Q for f in v.evidence)


def test_unqualified_per_share_figure_may_be_basic_eps(snapshot):
    v = verify(per_share("Amgen's GAAP net income was $2.38 billion, or $4.40 per share.", 4.40), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "doesn't say basic or diluted" in v.explanation
    assert any(f.metric == "eps_basic" and f.value == 4.40 and f.accession == AMGN_10Q for f in v.evidence)


def test_wrong_per_share_figure_attached_to_net_income_is_wrong(snapshot):
    v = verify(per_share("Amgen's GAAP net income was $2.38 billion, or $4.52 per share.", 4.52), snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(4.37)


def test_per_share_figure_of_a_net_loss_is_negative_eps(snapshot):
    right = per_share("Centene reported a net loss of $71 million, or $0.14 per share.", 0.14, entity="Centene",
                      period_text="Q3 2026")
    assert claimed_value(right) == pytest.approx(-0.14)
    v = verify(right, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.metric == "eps_diluted" and f.value == -0.14 and f.accession == CNC_10Q for f in v.evidence)
    wrong = per_share("Centene reported a net loss of $71 million, or $0.20 per share.", 0.20, entity="Centene",
                      period_text="Q3 2026")
    v = verify(wrong, snapshot)
    assert (v.status, v.expected_value) == ("wrong", pytest.approx(-0.14)), v.explanation


def test_per_share_figure_of_adjusted_net_income_is_non_gaap_eps(snapshot):
    span = "Akamai's adjusted net income was $235.8 million, or {} per share."
    v = verify(per_share(span.format("$1.59"), 1.59, entity="Akamai"), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert [(f.basis, f.source, f.accession) for f in v.evidence] == [("non-GAAP", "press_release", AKAM_PR)]
    v = verify(per_share(span.format("$1.65"), 1.65, entity="Akamai"), snapshot)
    assert (v.status, v.expected_value) == ("wrong", pytest.approx(1.59)), v.explanation
    # Adjusted wording read as GAAP is a basis conflict: never checked against GAAP EPS.
    assert unread(verify(per_share(span.format("$1.59"), 1.59, entity="Akamai", basis="GAAP"), snapshot))
    assert unread(verify(per_share(span.format("$0.52"), 0.52, entity="Akamai", basis="GAAP"), snapshot))


def test_gaap_per_share_figure_of_another_company_uses_its_own_basic_alternative(snapshot):
    for value in (0.52, 0.55):
        v = verify(per_share(f"Akamai's net income was $79.4 million, or ${value:.2f} per share.", value,
                             entity="Akamai"), snapshot)
        assert (v.status, v.reason) == ("verified", "match"), v.explanation


@pytest.mark.parametrize("span,value", [
    ("Amgen paid dividends of $1.28 billion, or $2.38 per share.", 2.38),
    ("Amgen's FFO was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's AFFO was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's normalized FFO was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's book value was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's tangible book value was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's operating cash flow was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's distributable earnings were $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's core earnings were $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's net income was $2.38 billion, and free cash flow was $2.0 billion ($4.37 per share).", 4.37),
    ("Amgen's net income was $2.38 billion, or $4.37 per ADS.", 4.37),
    ("Amgen's net income was $2.38 billion, or $4.37 per share from continuing operations.", 4.37),
    ("Excluding special items, Amgen's net income was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's consolidated net income was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's net income including noncontrolling interests was $2.38 billion, or $4.37 per share.", 4.37),
    ("Net income attributable to noncontrolling interests was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's pro forma net income was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's segment net income was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's trailing twelve-month net income was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's net income from continuing operations was $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's net income rose 66% to $2.38 billion, or $4.37 per share.", 4.37),
    ("Amgen's net income was approximately $2.38 billion, or about $4.37 per share.", 4.37),
])
def test_other_per_share_figures_are_never_read_as_eps(snapshot, span, value):
    assert unread(verify(per_share(span, value), snapshot))


@pytest.mark.parametrize("span,value,status", [
    ("Amgen's net income was $1.43 billion, or $2.65 per share, a year ago.", 2.65, "unverifiable"),
    ("Amgen's net income was $1.43 billion, or $2.65 per share, in the year-ago quarter.", 2.65, "unverifiable"),
    ("Amgen's net income was $2.38 billion, or $4.37 per share, up from $1.43 billion a year ago.", 4.37, "verified"),
    ("Amgen's net income was $2.38 billion, or $4.37 per share, on revenue of $10.1 billion.", 4.37, "verified"),
])
def test_a_comparison_period_after_the_per_share_words_leaves_the_figure_unread(snapshot, span, value, status):
    # The period check reads a label right after the amount only, so "$2.65 per share, a year ago" is never checked
    # against the current quarter; a label that belongs to the next amount does not count.
    assert verify(per_share(span, value), snapshot).status == status


@pytest.mark.parametrize("span", [
    "Amgen's net income was $2.38 billion, or $4.37 per share, including a $0.15 per share charge.",
    "Amgen's net income included an EPS impact of $0.15 per share.",
    "Amgen's net income was $2.38 billion, or $4.37 per share; a $0.15 per share adjustment lowered it.",
])
def test_a_per_share_impact_is_never_eps(snapshot, span):
    assert unread(verify(per_share(span, 0.15), snapshot))


def test_extraction_reaches_the_net_income_amount_a_bare_per_share_quote_is_attached_to(snapshot):
    text = "Amgen's GAAP net income was $2,375 million, or $4.37 per share, for the second quarter of 2026."
    eps = proposal("or $4.37 per share", "$4.37", 4.37, metric="eps_diluted", metric_text="per share",
                   unit="USD/share", scale="units", entity="Amgen", period_text="second quarter of 2026")
    (c,) = located(text, eps)
    assert c.span_text == "Amgen's GAAP net income was $2,375 million, or $4.37 per share"
    assert verify(c, snapshot).status == "verified"
    for subject in ("FFO", "dividend payout"):
        other = text.replace("GAAP net income", subject)
        (c,) = located(other, eps)
        assert c.span_text == "or $4.37 per share" and unread(verify(c, snapshot))


def test_span_reading_signs_a_net_loss_per_share_as_the_check_does(snapshot):
    c = per_share("Centene reported a net loss of $71 million, or $0.14 per share.", 0.14, entity="Centene",
                  period_text="Q3 2026")
    assert span_reading(c)[2].negative is True
    short = per_share("or $0.14 per share", 0.14, entity="Centene", period_text="Q3 2026")
    assert span_reading(short)[0] is None and span_reading(short) != span_reading(c)


# The newswire verb form: "<Company> earned $X, or $Y a share" (a production judge finding after release).

@pytest.mark.parametrize("span,value,metric", [
    ("Amgen earned $2.38 billion, or $4.37 a share.", 4.37, "eps_diluted"),
    ("Amgen earned $2.38 billion, or $4.40 a share.", 4.40, "eps_basic"),
    ("The company earned $2.38 billion, or $4.37 a share.", 4.37, "eps_diluted"),
    ("In the second quarter, it earned $2.38 billion ($4.37 a share).", 4.37, "eps_diluted"),
    ("We earned $2.38 billion, or $4.37 per share.", 4.37, "eps_diluted"),
    ("Amgen said it earned $2.38 billion, or $4.37 a share.", 4.37, "eps_diluted"),
])
def test_earned_amount_with_a_per_share_figure_is_eps(snapshot, span, value, metric):
    words = "per share" if "per share" in span else "a share"
    v = verify(per_share(span, value, metric_text=words), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.metric == metric and f.value == value and f.accession == AMGN_10Q for f in v.evidence)


def test_wrong_earned_per_share_figure_and_a_loss_are_checked(snapshot):
    v = verify(per_share("Amgen earned $2.38 billion, or $4.52 a share.", 4.52, metric_text="a share"), snapshot)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(4.37))
    for value, status in ((0.14, "verified"), (0.20, "wrong")):
        lost = per_share(f"Centene lost $71 million, or ${value:.2f} a share.", value, metric_text="a share",
                         entity="Centene", period_text="Q3 2026")
        assert claimed_value(lost) == pytest.approx(-value)
        v = verify(lost, snapshot)
        assert v.status == status and any(f.value == -0.14 and f.accession == CNC_10Q for f in v.evidence)


def test_adjusted_earned_amount_gives_non_gaap_eps_and_excluding_items_is_never_gaap(snapshot):
    for span in ("Akamai earned an adjusted $235.8 million, or $1.59 a share.",
                 "On an adjusted basis, Akamai earned $235.8 million, or $1.59 a share."):
        v = verify(per_share(span, 1.59, metric_text="a share", entity="Akamai"), snapshot)
        assert (v.status, v.reason) == ("verified", "match"), v.explanation
        assert [(f.basis, f.accession) for f in v.evidence] == [("non-GAAP", AKAM_PR)]
    adjusted_as_gaap = per_share("Akamai earned an adjusted $235.8 million, or $1.59 a share.", 1.59,
                                 metric_text="a share", entity="Akamai", basis="GAAP")
    assert unread(verify(adjusted_as_gaap, snapshot))
    for value in (1.59, 0.52):  # "excluding items" names no basis NoCap can check: never checked as GAAP
        c = per_share(f"Excluding items, Akamai earned $235.8 million, or ${value:.2f} a share.", value,
                      metric_text="a share", entity="Akamai")
        assert unread(verify(c, snapshot))


@pytest.mark.parametrize("span,value", [
    ("Amgen earned $10.1 billion in revenue, or $18.60 a share.", 18.60),
    ("Amgen earned $2.38 billion in fees, or $4.37 a share.", 4.37),
    ("Amgen earned $2.38 billion in sales, or $4.37 a share.", 4.37),
    ("Amgen earned $2.38 billion from operations, or $4.37 a share.", 4.37),
    ("Amgen earned $2.38 billion in adjusted earnings, or $4.37 a share.", 4.37),
    ("Amgen earned $2.38 billion in net interest income, or $4.37 a share.", 4.37),
    ("Amgen paid a dividend of $1.28 billion, or $2.38 a share.", 2.38),
    ("Amgen earned $2.38 billion and paid dividends of $1.28 billion, or $2.38 a share.", 2.38),
    ("Pfizer earned $2.38 billion, or $4.37 a share.", 4.37),
    ("Amgen's investors earned $2.38 billion, or $4.37 a share.", 4.37),
    ("Amgen's segment earned $2.38 billion, or $4.37 a share.", 4.37),
    ("Over the trailing twelve months, Amgen earned $2.38 billion, or $4.37 a share.", 4.37),
    ("Amgen earned $1.43 billion, or $2.65 a share, a year earlier.", 2.65),
    ("Amgen earned $2.38 billion, or $4.37 a share from continuing operations.", 4.37),
    ("Including noncontrolling interests, Amgen earned $2.38 billion, or $4.37 a share.", 4.37),
    ("Amgen earned a profit of $2.38 billion, or $4.37 a share.", 4.37),
    ("Earned $2.38 billion, or $4.37 a share.", 4.37),
])
def test_earned_look_alikes_stay_unread(snapshot, span, value):
    assert unread(verify(per_share(span, value, metric_text="a share"), snapshot))


def test_the_to_common_level_rule_is_unchanged_beside_its_per_share_figure(snapshot):
    # Tesla stores no available-to-common line for Q1 2026; by the owner's Oct 31 decision its to-common net income is
    # never checked against NetIncomeLoss. The per-share figure beside it is EPS, checked against EPS facts only.
    span = "Tesla's net income attributable to common stockholders was $477 million, or $0.13 per share."
    level = claim(span, "$477 million", 477, metric="net_income",
                  metric_text="net income attributable to common stockholders", unit="USD", scale="millions",
                  entity="Tesla", period_text="Q1 2026")
    v = verify(level, snapshot)
    assert unread(v) and "common stockholders" in v.explanation
    for text in (span, "Tesla earned $477 million, or $0.13 a share."):
        v = verify(per_share(text, 0.13, metric_text="a share" if "a share" in text else "per share",
                             entity="Tesla", period_text="Q1 2026"), snapshot)
        assert v.status == "verified" and all(f.metric in ("eps_diluted", "eps_basic") for f in v.evidence)


def test_extraction_reaches_the_amount_an_earned_per_share_quote_is_attached_to(snapshot):
    text = "Amgen earned $2,375 million, or $4.37 a share, in the second quarter of 2026."
    eps = proposal("or $4.37 a share", "$4.37", 4.37, metric="eps_diluted", metric_text="a share", unit="USD/share",
                   scale="units", entity="Amgen", period_text="second quarter of 2026")
    (c,) = located(text, eps)
    assert c.span_text == "Amgen earned $2,375 million, or $4.37 a share"
    assert verify(c, snapshot).status == "verified"
    (c,) = located(text.replace("earned $2,375 million", "earned $2,375 million in fees"), eps)
    assert c.span_text == "or $4.37 a share" and unread(verify(c, snapshot))


# --- 2. A parenthetical acronym restates its phrase ------------------------------------------------------------------

@pytest.mark.parametrize("acronym", ["(“EPS”)", '("EPS")', "('EPS')", "(‘EPS’)", "(EPS)"])
def test_defined_eps_acronym_is_the_same_phrase(snapshot, acronym):
    words = f"GAAP earnings per share {acronym}"
    v = verify(per_share(f"Amgen reported {words} of $4.37.", 4.37, metric_text=words), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.metric == "eps_diluted" and f.accession == AMGN_10Q for f in v.evidence)
    v = verify(per_share(f"Amgen reported {words} of $4.52.", 4.52, metric_text=words), snapshot)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(4.37))


def test_defined_eps_acronym_keeps_the_basic_alternative_and_other_metric_reading(snapshot):
    words = "GAAP earnings per share (“EPS”)"
    basic = per_share(f"Amgen reported {words} of $4.40.", 4.40, metric_text=words)
    assert verify(basic, snapshot).status == "verified"
    other = claim("Amgen reported earnings per share (EPS) of $4.37.", "$4.37", 4.37, metric="other",
                  metric_text="earnings per share (EPS)")
    assert verify(other, snapshot).status == "verified"
    diluted = "diluted earnings per share (“diluted EPS”)"
    assert verify(per_share(f"Amgen reported {diluted} of $4.37.", 4.37, metric_text=diluted),
                  snapshot).status == "verified"


@pytest.mark.parametrize("words,metric", [
    ("earnings per share (“EPS”) excluding special items", "eps_diluted"),
    ("net income (“EPS”)", "eps_diluted"),
    ("earnings per share for the quarter (“EPS”)", "eps_diluted"),
    ("dividends per share (“DPS”)", "dividend_per_share"),
])
def test_an_acronym_that_does_not_restate_its_phrase_stays_unread(snapshot, words, metric):
    c = claim(f"Amgen reported {words} of $4.37.", "$4.37", 4.37, metric=metric, metric_text=words)
    # The filed-facts check (_verify_filed) is what this pins; the company's 8-K release text can still confirm the
    # figure afterwards (receipts.release_text, tests/test_release_text.py).
    assert unread(verifier._verify_filed(c, snapshot))


# --- 3. Net income margin is net margin -------------------------------------------------------------------------------

@pytest.mark.parametrize("words,metric", [("net income margin", "other"), ("GAAP net income margin", "other"),
                                          ("net income margin", "net_income")])
def test_net_income_margin_is_net_margin(snapshot, words, metric):
    c = claim(f"Amgen's {words} was 23.6%.", "23.6%", 23.6, metric=metric, metric_text=words, unit="%")
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert {(f.metric, f.accession) for f in v.evidence} == {("net_income", AMGN_10Q), ("revenue", AMGN_10Q)}
    assert "net income / revenue" in v.explanation
    wrong = verify(c.model_copy(update={"span_text": f"Amgen's {words} was 25.0%.", "value_text": "25.0%",
                                        "value": 25.0, "span_end": len(f"Amgen's {words} was 25.0%.")}), snapshot)
    assert (wrong.status, wrong.reason) == ("wrong", "value_mismatch")
    assert wrong.expected_value == pytest.approx(2375 / 10054 * 100)


@pytest.mark.parametrize("words,entity", [("Biosimilars net income margin", "Amgen"),
                                          ("Entertainment net income margin", "Disney"),
                                          ("net interest margin", "Truist")])
def test_segment_net_income_margins_and_net_interest_margin_stay_unread(snapshot, words, entity):
    c = claim(f"{entity}'s {words} was 23.6%.", "23.6%", 23.6, metric="other", metric_text=words, unit="%",
              entity=entity, period_text="Q3 FY2026" if entity == "Disney" else "Q2 2026")
    assert unread(verify(c, snapshot))


# --- 4. Net income to common per share is EPS -------------------------------------------------------------------------

@pytest.mark.parametrize("words,value,metric,alternative", [
    ("net income attributable to common stockholders per diluted share", 4.37, "eps_diluted", False),
    ("net income available to common shareholders per diluted share", 4.37, "eps_diluted", False),
    ("net income attributable to common stockholders per share", 4.37, "eps_diluted", False),
    ("net income attributable to common stockholders per share", 4.40, "eps_diluted", True),
    ("net income attributable to common stockholders per basic share", 4.40, "eps_basic", False),
])
def test_to_common_per_share_wording_is_eps(snapshot, words, value, metric, alternative):
    c = claim(f"Amgen's {words} was ${value:.2f}.", f"${value:.2f}", value, metric=metric, metric_text=words)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert ("doesn't say basic or diluted" in v.explanation) is alternative
    assert any(f.metric in ("eps_diluted", "eps_basic") and f.value == value and f.accession == AMGN_10Q
               for f in v.evidence)


def test_to_common_per_diluted_share_is_diluted_only(snapshot):
    words = "net income attributable to common stockholders per diluted share"
    for value in (4.40, 4.52):  # basic EPS is not diluted EPS
        v = verify(claim(f"Amgen's {words} was ${value:.2f}.", f"${value:.2f}", value, metric="eps_diluted",
                         metric_text=words), snapshot)
        assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(4.37))


def test_to_common_level_amount_and_qualified_per_share_wording_keep_their_handling(snapshot):
    level = claim("Amgen's net income attributable to common stockholders was $2.38 billion.", "$2.38 billion", 2.38,
                  metric="net_income", metric_text="net income attributable to common stockholders", unit="USD",
                  scale="billions")
    v = verify(level, snapshot)
    assert unread(v) and "common stockholders" in v.explanation
    words = "net income attributable to common stockholders per diluted share excluding charges"
    assert unread(verify(claim(f"Amgen's {words} was $4.37.", "$4.37", 4.37, metric="eps_diluted",
                               metric_text=words), snapshot))


# --- 5. "The same quarter last year" names a period -------------------------------------------------------------------

@pytest.mark.parametrize("label", ["for the same quarter last year", "in the prior-year period",
                                   "for the same period a year ago", "in the same quarter of last year",
                                   "for the year-ago quarter", "for the same period of the prior year"])
def test_a_year_ago_amount_labelled_the_same_quarter_last_year_uses_that_quarter(snapshot, label):
    span = f"Total company sales were $26.5 billion, compared to $25.2 billion {label}."
    c = claim(span, "$25.2 billion", 25.2, metric="revenue", metric_text="total company sales", unit="USD",
              scale="billions", entity="Target", period_text="second quarter of fiscal 2026")
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "Q2 FY2025" in v.explanation and "comparison period" in v.explanation
    assert any(f.value == 25_211e6 and f.accession == TGT_10Q for f in v.evidence)
    wrong = c.model_copy(update={"span_text": span.replace("25.2", "24.0"), "value_text": "$24.0 billion",
                                 "value": 24.0})
    assert verify(wrong, snapshot).status == "wrong"


def test_a_comma_set_same_quarter_label_no_longer_draws_a_false_period_error(snapshot):
    # HEAD read this label as no comparison period and called the true year-ago figure a period mismatch (✗).
    span = "Revenue was $26.5 billion, compared to {} billion, the same quarter last year."
    for value, status in (("25.2", "verified"), ("24.0", "wrong")):
        c = target(span.format(f"${value}"), f"${value} billion", float(value), "revenue", "revenue")
        assert verify(c, snapshot).status == status


def test_the_same_quarter_last_year_as_the_period_text_corrects_with_that_quarter(snapshot):
    span = "Total company sales were $26.5 billion, compared to {} for the same quarter last year."
    for value, status in ((25.2, "verified"), (24.0, "wrong")):
        c = claim(span.format(f"${value} billion"), f"${value} billion", value, metric="revenue",
                  metric_text="total company sales", unit="USD", scale="billions", entity="Target",
                  period_text="the same quarter last year")
        v = verify(c, snapshot, context="Target, second quarter of fiscal 2026")
        assert v.status == status and "Q2 FY2025" in v.explanation, v.explanation
        assert v.expected_value == (None if status == "verified" else pytest.approx(25.211))


def test_a_change_from_the_same_quarter_last_year_is_year_over_year(snapshot):
    for value, status in (("5.3", "verified"), ("9", "wrong")):
        c = claim(f"Target's revenue rose {value}% from the same quarter last year.", f"{value}%", float(value),
                  metric="revenue_growth", metric_text="revenue", unit="%", entity="Target",
                  period_text="second quarter of fiscal 2026", comparison="QoQ")
        v = verify(c, snapshot)
        assert v.status == status and "year over year" in v.explanation, v.explanation


@pytest.mark.parametrize("words", ["same-store sales", "same store sales", "comparable sales",
                                   "comparable store sales", "identical sales", "comps"])
@pytest.mark.parametrize("metric_text", [None, "growth"])
def test_store_sales_measures_are_never_revenue(snapshot, words, metric_text):
    level = claim(f"{words.capitalize()} were $25.2 billion for the same quarter last year.", "$25.2 billion", 25.2,
                  metric="revenue", metric_text=words, unit="USD", scale="billions", entity="Target",
                  period_text="second quarter of fiscal 2026")
    assert unread(verify(level, snapshot))
    for label in ("from the same quarter last year", "year over year"):
        change = claim(f"{words.capitalize()} rose 5.3% {label}.", "5.3%", 5.3, metric="revenue_growth",
                       metric_text=metric_text or words, unit="%", entity="Target",
                       period_text="second quarter of fiscal 2026")
        assert unread(verify(change, snapshot)), (words, label)


@pytest.mark.parametrize("span,comparison", [
    ("Revenue was $26.5 billion, compared to $25.2 billion for the same quarter.", "none"),
    ("Revenue was $26.5 billion, compared to $25.2 billion in the same quarter of fiscal 2025.", "none"),
    # A level amount the model typed as YoY never reaches the year-ago check, so its label must not be dropped.
    ("Revenue was $26.5 billion, compared to $25.2 billion for the same quarter last year.", "YoY"),
])
def test_a_same_quarter_label_that_sets_no_checked_period_stays_unread(snapshot, span, comparison):
    c = claim(span, "$25.2 billion", 25.2, metric="revenue", metric_text="revenue", unit="USD", scale="billions",
              entity="Target", period_text="second quarter of fiscal 2026", comparison=comparison)
    assert unread(verify(c, snapshot))


def test_extraction_keeps_the_same_quarter_label_after_a_quoted_amount(snapshot):
    text = "Total company sales were $26.5 billion, compared to $25.2 billion for the same quarter last year."
    prior = proposal("compared to $25.2 billion", "$25.2 billion", 25.2, metric="revenue",
                     metric_text="total company sales", unit="USD", scale="billions", entity="Target",
                     period_text="second quarter of fiscal 2026")
    (c,) = located(text, prior)
    assert c.span_text.endswith("for the same quarter last year")
    assert verify(c, snapshot).status == "verified"


# A comps clause that follows a revenue figure describes something else: the revenue figure keeps its check (review of
# 7dfffde). Target Q2 FY2026: revenue $26,539M, up 5.27% from $25,211M.
COMPS_SENTENCES = [
    ("Total revenue was {} billion, driven by comparable sales growth of 2.1%.", "26.5", "27.5"),
    ("Total revenue increased {}%, driven by a comparable sales increase of 2.1%.", "5.3", "9.3"),
    ("Net sales rose {}% year over year on comparable sales growth of 2.1%.", "5.3", "9.3"),
    ("Revenue grew {}% from a year ago as same-store sales rose 2.1%.", "5.3", "9.3"),
    ("Revenue increased {}% year over year, including comps growth of 2.1%.", "5.3", "9.3"),
    ("Comparable sales rose 2.1% and total revenue increased {}% year over year.", "5.3", "9.3"),
]


def target(span, value_text, value, metric, metric_text, comparison="YoY"):
    unit, scale = ("USD", "billions") if value_text.startswith("$") else ("%", "units")
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale,
                 entity="Target", period_text="second quarter of fiscal 2026",
                 comparison="none" if unit == "USD" else comparison)


@pytest.mark.parametrize("template,true,false", COMPS_SENTENCES)
def test_a_following_comps_clause_leaves_the_revenue_figure_checked(snapshot, template, true, false):
    for value, status in ((true, "verified"), (false, "wrong")):
        level = template.startswith("Total revenue was")
        value_text = f"${value} billion" if level else f"{value}%"
        span = template.format(f"${value}" if level else value)
        c = target(span, value_text, float(value), "revenue" if level else "revenue_growth",
                   "total revenue" if level else "revenue")
        v = verify(c, snapshot)
        assert v.status == status, (span, v.explanation)
        if status == "wrong":
            assert v.expected_value == pytest.approx(26.539 if level else (26539 / 25211 - 1) * 100)


@pytest.mark.parametrize("template,true,false", COMPS_SENTENCES)
@pytest.mark.parametrize("metric,metric_text", [("revenue_growth", "revenue"), ("revenue_growth", "growth"),
                                                ("other", "comparable sales growth")])
def test_the_comps_figure_itself_stays_unread(snapshot, template, true, false, metric, metric_text):
    level = template.startswith("Total revenue was")
    span = template.format(f"${true}" if level else true)
    assert unread(verify(target(span, "2.1%", 2.1, metric, metric_text), snapshot))


@pytest.mark.parametrize("span", [
    "Target reported a 5.3% increase in comparable sales.",
    "Target posted 5.3% comparable sales growth.",
    "Target was up 5.3% in same-store sales.",
    "Target reported a 5.3% year-over-year increase in comps.",
    "Comps rose 5.3% year over year.",
])
def test_a_comps_figure_described_by_its_own_words_stays_unread(snapshot, span):
    assert unread(verify(target(span, "5.3%", 5.3, "revenue_growth", "growth"), snapshot))


@pytest.mark.parametrize("span,value_text,value,metric,metric_text", [
    ("Revenue was $26.5 billion (comparable sales rose 2.1%).", "$26.5 billion", 26.5, "revenue", "revenue"),
    ("Revenue was $26.5 billion — comparable sales rose 2.1%.", "$26.5 billion", 26.5, "revenue", "revenue"),
    ("Driven by comps, the company grew 5.3% year over year.", "5.3%", 5.3, "revenue_growth", "revenue growth"),
    ("Comps were strong, as total revenue rose 5.3% year over year.", "5.3%", 5.3, "revenue_growth", "total revenue"),
])
def test_comps_wording_in_another_clause_keeps_the_revenue_check(snapshot, span, value_text, value, metric,
                                                                metric_text):
    assert verify(target(span, value_text, value, metric, metric_text), snapshot).status == "verified"


@pytest.mark.parametrize("span,value_text,value,metric,metric_text,entity,period,segment,accession", [
    ("Extra Space's same-store revenues were $701.7 million.", "$701.7 million", 701.7, "segment_revenue",
     "same-store revenues", "Extra Space Storage", "third quarter of 2026", "same-store", "0001289490-26-000061"),
    ("Extra Space's Same-store revenues were $701.7 million.", "$701.7 million", 701.7, "revenue",
     "Same-store revenues", "Extra Space Storage", "third quarter of 2026", None, "0001289490-26-000061"),
    ("Extra Space's same-store revenues rose 2.7% year over year.", "2.7%", 2.7, "revenue_growth",
     "same-store revenues", "Extra Space Storage", "third quarter of 2026", "same-store", "0001289490-26-000061"),
    ("UDR's same-store revenue was $408.0 million.", "$408.0 million", 408.0, "segment_revenue", "same-store revenue",
     "UDR", "fourth quarter of 2025", "Same-Store Communities", "0000074208-26-000009"),
])
def test_a_store_sales_segment_the_release_reports_keeps_its_own_receipt(snapshot, span, value_text, value, metric,
                                                                        metric_text, entity, period, segment,
                                                                        accession):
    unit, scale = ("%", "units") if value_text.endswith("%") else ("USD", value_text.split()[-1] + "s")
    c = claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale, entity=entity,
              period_text=period).model_copy(update={"segment": segment})
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert v.evidence and all(f.metric == "segment_revenue" and "same" in f.segment.casefold() for f in v.evidence)
    assert accession in {f.accession for f in v.evidence}


# --- 6. Net income attributable to the company itself is plain net income -------------------------------------------

# Its shareholders or stockholders, unqualified, are the same parent line (loss wording, Nov 2): only "common" names the
# line after preferred dividends. These were "?"; GM's, Aon's and Nucor's true figures in both comprehensive sets read so.
@pytest.mark.parametrize("owner", ["Disney", "The Walt Disney Company", "DIS", "the Company", "Disney's",
                                   "Disney shareholders", "shareholders", "the Company's stockholders"])
def test_net_income_attributable_to_the_company_is_its_net_income(snapshot, owner):
    words = f"net income attributable to {owner}"
    for span, metric_text in ((f"{words[0].upper()}{words[1:]} was $2.64 billion.", words), ("$2.64 billion", words),
                              (f"{words[0].upper()}{words[1:]} was $2.64 billion.", "net income")):
        c = claim(span, "$2.64 billion", 2.64, metric="net_income", metric_text=metric_text, unit="USD",
                  scale="billions", entity="Disney", period_text="Q3 FY2026")
        v = verify(c, snapshot)
        assert (v.status, v.reason) == ("verified", "match"), (span, v.explanation)
        assert any(f.concept == "us-gaap:NetIncomeLoss" and f.value == 2_638e6 and f.accession == DIS_10Q
                   for f in v.evidence)


def test_wrong_net_income_attributable_to_the_company_is_wrong_and_the_consolidated_figure_as_plain(snapshot):
    def check(value, words="net income attributable to Disney"):
        return verify(claim(f"Disney's {words} was ${value} billion.", f"${value} billion", float(value),
                            metric="net_income", metric_text=words, unit="USD", scale="billions", entity="Disney",
                            period_text="Q3 FY2026"), snapshot)

    v = check("2.50")
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(2.638))
    # $2.84 billion is the consolidated figure. Attributed to Disney it is never right; plain "net income" is that
    # consolidated line since round 10a (it was "?" before, as the attributed figure still is).
    assert (check("2.84").status, check("2.84").reason) == ("unverifiable", "ambiguous")
    assert (check("2.84", "net income").status, check("2.84", "net income").reason) == ("verified", "match")


@pytest.mark.parametrize("words,value,status", [
    ("net income attributable to noncontrolling interests", "0.21", "unverifiable"),
    ("net income attributable to common shareholders", "2.64", "unverifiable"),
    ("net income attributable to Comcast", "2.64", "unverifiable"),
    ("net income attributable to Comcast shareholders", "2.64", "unverifiable"),
    ("net income attributable to preferred shareholders", "2.64", "unverifiable"),
    ("net income including noncontrolling interests", "2.84", "verified"),
    ("consolidated net income", "2.84", "verified"),
])
def test_other_attributions_keep_their_handling(snapshot, words, value, status):
    c = claim(f"Disney's {words} was ${value} billion.", f"${value} billion", float(value), metric="net_income",
              metric_text=words, unit="USD", scale="billions", entity="Disney", period_text="Q3 FY2026")
    v = verify(c, snapshot)
    assert v.status == status, v.explanation
    if status == "verified":
        assert all(f.metric == "net_income_including_nci" for f in v.evidence)
    else:
        assert v.expected_value is None


# --- 7. A bank's noninterest income change ---------------------------------------------------------------------------

@pytest.mark.parametrize("span,value,entity,accession,terms", [
    ("Truist's noninterest income rose 17% from a year earlier.", 17, "Truist", TFC_10Q,
     ("NoninterestIncome 1,644,000,000", "NoninterestIncome 1,400,000,000")),
    ("Truist's noninterest income up 17.4% year over year.", 17.4, "Truist", TFC_10Q,
     ("NoninterestIncome 1,644,000,000", "NoninterestIncome 1,400,000,000")),
    ("Regions Financial's non-interest income fell 2.5% year over year.", 2.5, "Regions Financial", RF_10Q,
     ("NoninterestIncome 630,000,000", "NoninterestIncome 646,000,000")),
])
def test_bank_noninterest_income_change_is_read_from_its_filed_line(snapshot, span, value, entity, accession, terms):
    words = "non-interest income" if "non-interest" in span else "noninterest income"
    v = verify(claim(span, f"{value}%", value, metric="other", metric_text=words, unit="%", entity=entity), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "noninterest income" in v.explanation and "year over year" in v.explanation
    assert {f.accession for f in v.evidence} == {accession}
    assert all(any(t in (f.locator or "") for f in v.evidence) for t in terms)


@pytest.mark.parametrize("span,value,entity,expected", [
    ("Truist's noninterest income rose 12% year over year.", 12, "Truist", (1644 / 1400 - 1) * 100),
    ("Regions Financial's noninterest income rose 2.5% year over year.", 2.5, "Regions Financial",
     (630 / 646 - 1) * 100),
])
def test_wrong_bank_noninterest_income_change_is_wrong(snapshot, span, value, entity, expected):
    v = verify(claim(span, f"{value}%", value, metric="other", metric_text="noninterest income", unit="%",
                     entity=entity), snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(expected)


@pytest.mark.parametrize("span,words,value,unit,scale,entity", [
    ("Truist's noninterest income rose 5.9% from the prior quarter.", "noninterest income", 5.9, "%", "units",
     "Truist"),
    ("Truist's adjusted noninterest income rose 17% year over year.", "adjusted noninterest income", 17, "%", "units",
     "Truist"),
    ("Truist's noninterest income excluding securities losses rose 17% year over year.",
     "noninterest income excluding securities losses", 17, "%", "units", "Truist"),
    ("Truist's noninterest income rose to 31% of total revenue.", "noninterest income", 31, "%", "units", "Truist"),
    ("Truist's fee income rose 17% year over year.", "fee income", 17, "%", "units", "Truist"),
    ("Truist's noninterest income was $1.64 billion.", "noninterest income", 1.64, "USD", "billions", "Truist"),
    ("JPMorgan Chase's noninterest income rose 10% year over year.", "noninterest income", 10, "%", "units",
     "JPMorgan Chase"),
])
def test_noninterest_income_without_a_held_line_or_with_other_wording_stays_unread(snapshot, span, words, value, unit,
                                                                                  scale, entity):
    value_text = f"${value} billion" if unit == "USD" else f"{value}%"
    c = claim(span, value_text, value, metric="other", metric_text=words, unit=unit, scale=scale, entity=entity)
    # The filed-facts check (_verify_filed) is what this pins; the company's 8-K release text can still confirm the
    # figure afterwards (receipts.release_text, tests/test_release_text.py).
    assert unread(verifier._verify_filed(c, snapshot))


# --- Round 4. A direction word signs only its own figure's clause ---------------------------------------------------
# Target Q2 FY2026 revenue rose 5.27% (10-Q 0000027419-26-000042); Lennar Q3 FY2026 revenue fell 8.67%
# ($8,046.1M against $8,810.3M, 10-Q 0001628280-26-064557). Companies outside the frozen spot500 sets.

def change(span, value, entity="Target", period_text="second quarter of fiscal 2026"):
    return claim(span, f"{value}%", value, metric="revenue_growth", metric_text="revenue", unit="%", entity=entity,
                 period_text=period_text, comparison="YoY")


@pytest.mark.parametrize("span", [
    "Revenue rose 5.3% versus a decline of 1%.",
    "Revenue rose 5.3% year over year versus a comparable sales decline of 1%.",
    "Revenue grew 5.3%, while comparable sales declined 1%.",
    "Revenue increased 5.3%, or $1.33 billion, despite lower traffic.",
    "Revenue rose 5.3% despite lower traffic.",
    "Revenue rose 5.3% as costs fell.",
    "As costs fell 3%, revenue rose 5.3%.",
])
def test_a_direction_word_of_another_clause_never_flips_a_rise(snapshot, span):
    c = change(span, 5.3)
    assert claimed_value(c, change=True) == pytest.approx(5.3)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


@pytest.mark.parametrize("span", [
    "Lennar's revenue fell 8.7% year over year.",
    "Lennar's revenue was down 8.7%, versus growth of 2% a year ago.",
    "Lennar's revenue was 8.7% lower than a year earlier.",
    "Lennar's revenue was 8.7%, or $764 million, lower than a year earlier.",
    "Lennar's revenue decreased from $8.81 billion to $8.05 billion, or 8.7%.",
    "Lennar's revenue declined 8.7%, while home deliveries rose 3%.",
    "Lennar's deliveries rose 3%, and revenue fell 8.7%.",
    "After rising 3% last quarter, Lennar's revenue fell 8.7%.",
])
def test_a_fall_keeps_its_own_direction(snapshot, span):
    c = change(span, 8.7, entity="Lennar", period_text="third quarter of fiscal 2026")
    assert claimed_value(c, change=True) == pytest.approx(-8.7)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "fell 8.67%" in v.explanation


@pytest.mark.parametrize("span,value,entity,period,said", [
    ("Revenue fell 5.3%.", 5.3, "Target", "second quarter of fiscal 2026", "fell"),
    ("Revenue was down 5.3%, versus growth of 2% a year ago.", 5.3, "Target", "second quarter of fiscal 2026", "fell"),
    ("Lennar's revenue rose 8.7% year over year.", 8.7, "Lennar", "third quarter of fiscal 2026", "grew"),
    # HEAD read "fell" from the costs clause and verified this rise against the fall: a false ✓, now caught.
    ("Lennar's revenue grew 8.7% while costs fell.", 8.7, "Lennar", "third quarter of fiscal 2026", "grew"),
])
def test_a_truly_wrong_direction_is_still_wrong(snapshot, span, value, entity, period, said):
    v = verify(change(span, value, entity=entity, period_text=period), snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert f"the draft says it {said}" in v.explanation


# --- Round 4. "On a comparable-store basis" right after the number names the measure ------------------------------

@pytest.mark.parametrize("span,value_text,value,metric,head", [
    ("Revenue rose 2.1% on a comparable-store basis.", "2.1%", 2.1, "revenue_growth", "wrong"),
    ("Revenue rose 2.1% year over year, on a comparable sales basis.", "2.1%", 2.1, "revenue_growth", "wrong"),
    ("Revenue rose 2.1% (on a comparable store sales basis).", "2.1%", 2.1, "revenue_growth", "wrong"),
    ("Revenue rose 5.3% on a comparable store sales basis.", "5.3%", 5.3, "revenue_growth", "verified"),
    ("Revenue was $26.5 billion on a same-store basis.", "$26.5 billion", 26.5, "revenue", "verified"),
])
def test_a_store_sales_basis_after_the_number_is_never_total_revenue(snapshot, span, value_text, value, metric, head):
    # head: the verdict before this rule, a false ✗ or a ✓ against total revenue for a store-sales figure
    assert unread(verify(target(span, value_text, value, metric, "revenue"), snapshot))


@pytest.mark.parametrize("span,status", [
    ("Revenue rose 5.3% on comparable sales growth of 2.1%.", "verified"),  # a cause, not a basis
    ("Revenue rose 5.3% on a constant-currency basis.", "unverifiable"),     # already a distinct measure
    ("Revenue rose 5.3% on an organic basis.", "unverifiable"),              # already a distinct measure
])
def test_other_on_phrases_keep_their_handling(snapshot, span, status):
    assert verify(target(span, "5.3%", 5.3, "revenue_growth", "revenue"), snapshot).status == status


# --- Round 4. "Over the same quarter last year" labels a year-ago amount, as "for ..." does ---------------------

@pytest.mark.parametrize("label", ["over the same quarter last year", "over the prior-year period",
                                   "over the year-ago quarter", "over the same period a year ago"])
def test_an_over_label_makes_a_year_ago_amount_check_that_quarter(snapshot, label):
    span = "Revenue was $26.5 billion, compared to {} billion " + label + "."
    for value, status in (("25.2", "verified"), ("24.0", "wrong")):
        c = target(span.format(f"${value}"), f"${value} billion", float(value), "revenue", "revenue")
        v = verify(c, snapshot)
        assert v.status == status, v.explanation
        if status == "verified":
            assert "Q2 FY2025" in v.explanation and any(f.value == 25_211e6 for f in v.evidence)


def test_an_over_label_on_a_level_amount_typed_yoy_is_never_a_period_error(snapshot):
    # The year-ago check runs only for comparison "none"; typed YoY, the label stays a qualifier ("?"), where the
    # previous tip called the true year-ago figure a period mismatch.
    span = "Revenue was $26.5 billion, compared to $25.2 billion over the same quarter last year."
    c = target(span, "$25.2 billion", 25.2, "revenue", "revenue").model_copy(update={"comparison": "YoY"})
    assert unread(verify(c, snapshot))


def test_over_a_year_ago_is_not_read_as_a_label(snapshot):
    # "over a year ago" can mean "more than a year ago": it never moves the period.
    span = "Revenue was $26.5 billion, compared to $26.5 billion over a year ago."
    c = target(span.replace("$26.5 billion,", "$27.0 billion,"), "$26.5 billion", 26.5, "revenue", "revenue")
    v = verify(c, snapshot)
    assert v.status == "verified" and "Q2 FY2026" in v.explanation  # checked as the current quarter, as before


def test_extraction_keeps_the_whole_over_label(snapshot):
    text = "Total company sales were $26.5 billion, compared to $25.2 billion over the prior-year period."
    prior = proposal("compared to $25.2 billion", "$25.2 billion", 25.2, metric="revenue",
                     metric_text="total company sales", unit="USD", scale="billions", entity="Target",
                     period_text="second quarter of fiscal 2026")
    (c,) = located(text, prior)
    assert c.span_text.endswith("over the prior-year period")
    assert verify(c, snapshot).status == "verified"


# --- Round 4. A release row for comparable or constant-currency sales is never company revenue -----------------
# Sysco's "Comparable sales using a constant currency basis (Non-GAAP)" row was filed as non-GAAP revenue: $22.077B for
# Q4 FY2026 (8-K 0000096021-26-000026), where reported revenue was $22.124B.

@pytest.mark.parametrize("label,excluded", [
    ("Comparable sales using a constant currency basis (Non-GAAP) | 22,077", True),
    ("Adjusted Net Sales – FX Neutral | 2,489", True),
    ("Total Revenues Net of Interest Expense FX-adjusted 2 | 20,062", True),
    ("Net sales excluding divestitures and foreign currency exchange | 2,221", True),
    ("Revenue excluding foreign exchange effect | 64,900", True),
    ("Constant currency revenue (a) | 1,100", True),
    ("Same store revenues | 721,110", True),
    ("Revenue ($M) | 10,253", False),
    ("Total revenue, net of interest expense | 30,434", False),
    ("Net sales | 22,124", False),
    ("Organic revenues | 600", False),  # not a currency or store-sales row: outside this rule
])
def test_the_row_label_decides_what_is_not_revenue(label, excluded):
    from receipts.types import Fact
    from receipts.verify import _not_revenue_row
    from datetime import date, datetime, timezone
    f = Fact(cik=1, ticker="X", metric="revenue", basis="non-GAAP", value=1.0, unit="USD", period_end=date(2026, 6, 30),
             source="press_release", form="8-K", accession="a", accepted_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
             url="u", locator=label)
    assert _not_revenue_row(f) is excluded


def sysco(span, value_text, value, metric="revenue", metric_text="revenue", basis=None, comparison="none"):
    unit, scale = ("%", "units") if value_text.endswith("%") else ("USD", "billions")
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale,
                 entity="Sysco", period_text="Q4 FY2026", basis=basis, comparison=comparison)


def test_a_constant_currency_comparable_sales_row_is_not_non_gaap_revenue(snapshot):
    for c in (sysco("Sysco's non-GAAP revenue was $22.077 billion.", "$22.077 billion", 22.077,
                    metric_text="non-GAAP revenue", basis="non-GAAP"),
              sysco("Sysco's adjusted revenue was $22.077 billion.", "$22.077 billion", 22.077,
                    metric_text="adjusted revenue"),
              sysco("Sysco's non-GAAP revenue rose 4.4% year over year.", "4.4%", 4.4, metric="revenue_growth",
                    metric_text="non-GAAP revenue", basis="non-GAAP", comparison="YoY")):
        v = verify(c, snapshot)
        assert unread(v), v.explanation
        assert not any("Comparable sales" in (f.locator or "") for f in v.evidence)


def test_reported_revenue_keeps_its_receipt_and_the_row_value_is_never_wrong_unless_stated_gaap(snapshot):
    v = verify(sysco("Sysco's revenue was $22.124 billion.", "$22.124 billion", 22.124), snapshot)
    assert (v.status, v.reason) == ("verified", "match") and all(f.source != "press_release" or
                                                                 "Comparable" not in (f.locator or "") for f in v.evidence)
    v = verify(sysco("Sysco's revenue was $22.077 billion.", "$22.077 billion", 22.077), snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "Comparable sales using a constant currency basis" in v.explanation and "$22.124 billion" in v.explanation
    v = verify(sysco("Sysco's GAAP revenue was $22.077 billion.", "$22.077 billion", 22.077, metric_text="GAAP revenue",
                     basis="GAAP"), snapshot)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(22.124))


@pytest.mark.parametrize("span,value_text,value,entity,period,metric", [
    ("Monster Beverage's non-GAAP revenue was $2.489 billion.", "$2.489 billion", 2.489, "Monster Beverage", "Q2 2026",
     "revenue"),
    ("American Express's non-GAAP revenue was $20.062 billion.", "$20.062 billion", 20.062, "American Express",
     "Q3 2026", "revenue"),
])
def test_other_constant_currency_rows_are_not_non_gaap_revenue(snapshot, span, value_text, value, entity, period,
                                                               metric):
    c = claim(span, value_text, value, metric=metric, metric_text="non-GAAP revenue", unit="USD", scale="billions",
              entity=entity, period_text=period, basis="non-GAAP")
    assert unread(verify(c, snapshot))


def test_an_ordinary_non_gaap_revenue_row_still_verifies(snapshot):
    c = claim("AMD's non-GAAP revenue was $10.253 billion.", "$10.253 billion", 10.253, metric="revenue",
              metric_text="non-GAAP revenue", unit="USD", scale="billions", entity="AMD", period_text="Q1 2026",
              basis="non-GAAP")
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match")
    assert any(f.source == "press_release" and f.basis == "non-GAAP" for f in v.evidence)


@pytest.mark.parametrize("value,status", [("13.0", "verified"), ("13.2", "unverifiable"), ("11.0", "wrong")])
def test_an_adjusted_margin_is_computed_on_reported_revenue_never_on_an_ex_fx_row(snapshot, value, status):
    # Smucker Q2 FY2025: adjusted net income over reported net sales is 12.95% (HEAD divided by its "Net sales excluding
    # divestitures and foreign currency exchange" row, 13.25%, and called 13.0% wrong); 13.2% matches only that row.
    c = claim(f"J.M. Smucker's adjusted net margin was {value}%.", f"{value}%", float(value), metric="other",
              metric_text="adjusted net margin", unit="%", entity="J.M. Smucker", period_text="Q2 FY2025",
              basis="non-GAAP")
    v = verify(c, snapshot)
    assert v.status == status, v.explanation
    if status == "unverifiable":
        assert v.reason == "ambiguous" and "excluding divestitures and foreign currency exchange" in v.explanation


# --- Round 5. A loss word signs only its own figure ------------------------------------------------------------------
# Valero Q1 2026: net income $1,263M against a net loss of $595M a year earlier, operating income $1,731M against an
# operating loss of $900M (10-Q 0001628280-26-028690). Merck Q2 2026: net loss $1,335M against net income of $4,427M
# (10-Q 0000310158-26-000212). Carrier Q3 2026: operating loss $2,273M against operating income of $539M, net loss
# $2,593M (10-Q 0001783180-26-000037). Hewlett Packard Enterprise Q2 FY2026: net income $624M against a net loss of
# $1,050M. Companies outside the frozen spot500 sets.

def income(span, value_text, value, entity, period_text, metric="net_income", metric_text="net income"):
    scale = "billions" if "billion" in value_text else "millions"
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit="USD", scale=scale,
                 entity=entity, period_text=period_text)


VALERO = ("Valero", "first quarter of 2026")
MERCK = ("Merck", "second quarter of 2026")


@pytest.mark.parametrize("span,value_text,value,entity,period,metric,filed", [
    ("Valero's net income was $1.26 billion in the first quarter of 2026, compared with a net loss of $595 million a "
     "year earlier.", "$1.26 billion", 1.26, *VALERO, "net_income", 1.263e9),
    ("Net income was $624 million, compared with a net loss of $1.05 billion a year ago.", "$624 million", 624,
     "Hewlett Packard Enterprise", "second quarter of fiscal 2026", "net_income", 624e6),
    ("Valero's net income was $1.26 billion in the first quarter of 2026, up from a loss of $595 million.",
     "$1.26 billion", 1.26, *VALERO, "net_income", 1.263e9),
    ("Valero swung to a profit of $1.26 billion in the first quarter of 2026 against a loss of $595 million a year "
     "earlier.", "$1.26 billion", 1.26, *VALERO, "net_income", 1.263e9),
    ("After a net loss of $595 million a year earlier, Valero's net income was $1.26 billion in the first quarter of "
     "2026.", "$1.26 billion", 1.26, *VALERO, "net_income", 1.263e9),
    ("Valero's net income, which included a $0.2 billion loss on debt extinguishment, was $1.26 billion in the first "
     "quarter of 2026.", "$1.26 billion", 1.26, *VALERO, "net_income", 1.263e9),
    ("Valero's net income was $1.26 billion in the first quarter of 2026. A year earlier, it reported a net loss of "
     "$595 million.", "$1.26 billion", 1.26, *VALERO, "net_income", 1.263e9),
    ("Valero's operating income was $1.73 billion in the first quarter of 2026, versus an operating loss of $900 "
     "million a year earlier.", "$1.73 billion", 1.73, *VALERO, "operating_income", 1.731e9),
])
def test_another_figures_loss_never_signs_an_income_figure(snapshot, span, value_text, value, entity, period, metric,
                                                           filed):
    c = income(span, value_text, value, entity, period, metric=metric,
               metric_text=metric.replace("_", " "))
    assert claimed_value(c) > 0
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.value == pytest.approx(filed) for f in v.evidence)


@pytest.mark.parametrize("span,metric_text", [
    ("Merck reported a net loss of $1.34 billion for the second quarter of 2026, compared with net income of $4.43 "
     "billion a year ago.", "net income"),
    ("Merck's net loss, which included $1.9 billion of charges, was $1.34 billion in the second quarter of 2026.",
     "net loss"),
    ("Merck's net loss, which included $1.9 billion of charges, was $1.34 billion in the second quarter of 2026.",
     "net income"),
    ("Merck's net loss, which included operating income of $0.3 billion, was $1.34 billion in the second quarter of "
     "2026.", "net income"),
    ("Merck posted a $1.34 billion net loss in the second quarter of 2026, after net income of $4.43 billion a year "
     "ago.", "net income"),
    ("Despite net income of $4.43 billion a year ago, Merck posted a loss of $1.34 billion in the second quarter of "
     "2026.", "net income"),
    ("Revenue and net income were $15.8 billion and $4.43 billion a year ago, but Merck's net loss was $1.34 billion "
     "in the second quarter of 2026.", "net income"),
    ("Merck's earnings swung to a $1.34 billion net loss in the second quarter of 2026.", "net income"),
    ("Merck's bottom line: $1.34 billion, a loss, in the second quarter of 2026.", "net income"),
])
def test_a_loss_keeps_its_own_figure_negative(snapshot, span, metric_text):
    c = income(span, "$1.34 billion", 1.34, *MERCK, metric_text=metric_text)
    assert claimed_value(c) < 0
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "-$1.335 billion" in v.explanation


@pytest.mark.parametrize("span,value_text,value,entity,metric,filed", [
    ("Merck reported a net loss of $1.34 billion for the second quarter of 2026, compared to net income of $4.43 "
     "billion a year ago.", "$4.43 billion", 4.43, "Merck", "net_income", 4.427e9),
    ("Valero's net income was $1.26 billion in the first quarter of 2026, compared to a net loss of $595 million a "
     "year earlier.", "$595 million", 595, "Valero", "net_income", -595e6),
    ("Carrier posted an operating loss of $2.27 billion in the third quarter of 2026, versus operating income of $539 "
     "million a year earlier.", "$539 million", 539, "Carrier", "operating_income", 539e6),
])
def test_a_year_ago_figure_takes_its_own_sign_and_label(snapshot, span, value_text, value, entity, metric, filed):
    period = {"Merck": "second quarter of 2026", "Valero": "first quarter of 2026"}.get(entity, "third quarter of 2026")
    c = income(span, value_text, value, entity, period, metric=metric, metric_text=metric.replace("_", " "))
    assert claimed_value(c) == pytest.approx(filed, rel=0.01)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "the comparison period the sentence cites" in v.explanation
    assert any(f.value == pytest.approx(filed) for f in v.evidence)


@pytest.mark.parametrize("span,value_text,value,entity,period,sign", [
    ("Valero's net income was $1.26 billion in the first quarter of 2026, compared with a net loss of $595 million a "
     "year earlier.", "$595 million", 595, *VALERO, -1),
    ("Merck reported a net loss of $1.34 billion for the second quarter of 2026, compared with net income of $4.43 "
     "billion a year ago.", "$4.43 billion", 4.43, *MERCK, 1),
])
def test_a_year_ago_figure_after_compared_with_is_signed_labelled_and_never_wrong(snapshot, span, value_text, value,
                                                                                  entity, period, sign):
    # "with" before the metric noun still leaves these unread ("compared to" verifies above); the reading itself has
    # the figure's own sign and the year-ago label.
    c = income(span, value_text, value, entity, period)
    assert claimed_value(c) * sign > 0
    assert verifier._comparison_period_after(c) == (True, False)
    assert verify(c, snapshot).status != "wrong"


def test_a_figure_the_sentence_carries_a_loss_subject_to_stays_negative(snapshot):
    c = income("Carrier's net loss was $2.59 billion in the third quarter of 2026, compared with $428 million of net "
               "income a year earlier.", "$2.59 billion", 2.59, "Carrier", "third quarter of 2026")
    v = verify(c, snapshot)
    assert claimed_value(c) < 0 and (v.status, v.reason) == ("verified", "match"), v.explanation
    elliptical = income("Pfizer's net loss was $248 million in the second quarter of 2026, compared with $2.91 billion "
                        "a year ago.", "$2.91 billion", 2.91, "Pfizer", "second quarter of 2025")
    assert claimed_value(elliptical) < 0  # "compared with $2.91 billion" has no subject of its own: still a loss


def test_another_sentences_loss_never_signs_a_year_ago_income_figure(snapshot):
    c = income("Merck lost money in the second quarter of 2026: its net loss was $1.34 billion. Net income a year "
               "earlier was $4.43 billion.", "$4.43 billion", 4.43, "Merck", "second quarter of 2025")
    v = verify(c, snapshot)
    assert claimed_value(c) > 0 and (v.status, v.reason) == ("verified", "match"), v.explanation


def test_income_wording_for_a_filed_loss_is_unread_when_the_text_also_names_a_loss(snapshot):
    # The draft calls Merck's $1.335 billion loss net income; a loss word of another figure used to sign it (a "✓"). Its
    # own clause now reads it as income, and the filed loss makes that "?", never a new "✗".
    c = income("Merck's net income was $1.34 billion in the second quarter of 2026, compared with a net loss of $4.43 "
               "billion a year ago.", "$1.34 billion", 1.34, *MERCK)
    assert claimed_value(c) > 0 > claimed_value(c, loss_scope=False)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "read as a loss" in v.explanation
    assert any(f.value == pytest.approx(-1.335e9) for f in v.evidence)


def test_a_wrong_income_figure_beside_a_loss_is_still_wrong(snapshot):
    c = income("Valero's net income was $1.42 billion in the first quarter of 2026, compared with a net loss of $595 "
               "million a year earlier.", "$1.42 billion", 1.42, *VALERO)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(1.263)


def test_extraction_keeps_an_income_quote_beside_another_figures_loss(snapshot):
    text = ("Valero's net income was $1.26 billion in the first quarter of 2026, compared with a net loss of $595 "
            "million a year earlier.")
    [c] = located(text, proposal("net income was $1.26 billion", "$1.26 billion", 1.26, metric="net_income",
                                 metric_text="net income", unit="USD", scale="billions", entity="Valero",
                                 period_text="first quarter of 2026"))
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), (c.span_text, v.explanation)


# --- Round 5. An organic or pro forma release row is never company revenue -----------------------------------------
# FactSet Q2 FY2026 release (0001628280-26-022203): "Revenues" $611.019M, "Organic revenues" $606.176M, "Adjusted net
# income" $165.271M. Comcast Q3 2025: revenue $31.198B, "Pro Forma Consolidated Revenue" $29.151B. GE HealthCare Q2
# 2026: "Total revenues" $5.295B, "Organic revenue*" $5.182B. Motorola Solutions Q1 2026: "Sales" $2.714B, "Organic
# revenue" $2.492B, non-GAAP net earnings $566M. The round-4 rule (_not_revenue_row) is unchanged; these rows are kept
# out by _kept_out_of_revenue.

@pytest.mark.parametrize("label,kept_out", [
    ("Organic revenues | 606,176", True),
    ("Organic Net Sales, Non-GAAP 1,2 | 4,155", True),
    ("Consolidated organic net sales | 2,463", True),
    ("Organic revenue* | 5,182", True),
    ("Pro Forma Consolidated Revenue | $29,298", True),
    ("Pro forma and macro adjusted | 1,271", True),
    ("Comparable sales using a constant currency basis (Non-GAAP) | 22,077", True),
    ("Revenues | 611,019", False),
    ("Total revenues | 5,295", False),
    ("Sales | $3,133", False),
])
def test_organic_and_pro_forma_rows_are_kept_out_of_revenue(label, kept_out):
    from datetime import date, datetime, timezone
    from receipts.types import Fact
    f = Fact(cik=1, ticker="X", metric="revenue", basis="non-GAAP", value=1.0, unit="USD", period_end=date(2026, 6, 30),
             source="press_release", form="8-K", accession="a", accepted_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
             url="u", locator=label)
    assert verifier._kept_out_of_revenue(f) is kept_out


def revenue(span, value_text, value, entity, period_text, *, metric="revenue", metric_text="revenue", basis=None,
            comparison="none"):
    unit, scale = (("%", "units") if value_text.endswith("%") else
                   ("USD", "billions" if "billion" in value_text else "millions"))
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale,
                 entity=entity, period_text=period_text, basis=basis, comparison=comparison)


@pytest.mark.parametrize("span,value_text,value,entity,period,metric_text,basis", [
    ("FactSet's non-GAAP revenue was $606.2 million.", "$606.2 million", 606.2, "FactSet", "Q2 FY2026",
     "non-GAAP revenue", "non-GAAP"),
    ("FactSet's adjusted revenue was $606.2 million.", "$606.2 million", 606.2, "FactSet", "Q2 FY2026",
     "adjusted revenue", None),
    ("FactSet's revenue was $606.2 million.", "$606.2 million", 606.2, "FactSet", "Q2 FY2026", "revenue", None),
    ("Comcast's non-GAAP revenue was $29.15 billion.", "$29.15 billion", 29.15, "Comcast", "Q3 2025",
     "non-GAAP revenue", "non-GAAP"),
    ("GE HealthCare's adjusted revenue was $5.18 billion.", "$5.18 billion", 5.18, "GE HealthCare", "Q2 2026",
     "adjusted revenue", None),
])
def test_an_organic_or_pro_forma_row_value_is_never_checked_as_revenue(snapshot, span, value_text, value, entity,
                                                                        period, metric_text, basis):
    v = verify(revenue(span, value_text, value, entity, period, metric_text=metric_text, basis=basis), snapshot)
    assert unread(v), v.explanation


@pytest.mark.parametrize("span,value_text,value,entity,period,filed", [
    ("FactSet's revenue was $611.0 million.", "$611.0 million", 611.0, "FactSet", "Q2 FY2026", 611.019e6),
    ("Comcast's revenue was $31.2 billion.", "$31.2 billion", 31.2, "Comcast", "Q3 2025", 31.198e9),
    ("GE HealthCare's revenue was $5.30 billion.", "$5.30 billion", 5.30, "GE HealthCare", "Q2 2026", 5.295e9),
])
def test_reported_revenue_beside_an_organic_or_pro_forma_row_still_verifies(snapshot, span, value_text, value, entity,
                                                                             period, filed):
    v = verify(revenue(span, value_text, value, entity, period), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.value == pytest.approx(filed) for f in v.evidence)


def test_a_gaap_figure_equal_to_the_organic_row_is_still_wrong(snapshot):
    v = verify(revenue("FactSet's GAAP revenue was $606.2 million.", "$606.2 million", 606.2, "FactSet", "Q2 FY2026",
                       metric_text="GAAP revenue", basis="GAAP"), snapshot)
    assert v.status == "wrong" and v.expected_value == pytest.approx(611.019), v.explanation


def test_organic_growth_is_not_non_gaap_revenue_growth(snapshot):
    c = revenue("GE HealthCare's non-GAAP revenue grew 3.5% year over year.", "3.5%", 3.5, "GE HealthCare", "Q2 2026",
                metric="revenue_growth", metric_text="non-GAAP revenue", basis="non-GAAP", comparison="YoY")
    assert unread(verify(c, snapshot))
    plain = c.model_copy(update={"span_text": "GE HealthCare's revenue grew 3.5% year over year.",
                                 "metric_text": "revenue", "basis": None})
    v = verify(plain, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "Organic revenue" in v.explanation


@pytest.mark.parametrize("entity,period,true,row", [("FactSet", "Q2 FY2026", "27.0", "27.3"),
                                                    ("Motorola Solutions", "Q1 2026", "20.9", "22.7")])
def test_an_adjusted_margin_is_computed_on_reported_revenue_never_on_an_organic_row(snapshot, entity, period, true,
                                                                                    row):
    def margin(value):
        return revenue(f"{entity}'s adjusted net margin was {value}%.", f"{value}%", float(value), entity, period,
                       metric="other", metric_text="adjusted net margin", basis="non-GAAP")
    v = verify(margin(true), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation  # "✗" before: divided by the organic row
    v = verify(margin(row), snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "Organic revenue" in v.explanation


# --- Round 5. "Over a year ago" after an amount is never a period error -------------------------------------------
# Target Q2 FY2026 revenue $26,539M, Q2 FY2025 $25,211M (10-Q 0000027419-26-000042). "Over a year ago" may label the
# year-ago figure or mean more than a year earlier, so an amount it follows verifies only in its own period; otherwise
# it is "?", never "✗".

@pytest.mark.parametrize("span,value_text,value,comparison", [
    ("Revenue was $26.5 billion, compared to $25.2 billion over a year ago.", "$25.2 billion", 25.2, "none"),
    ("Revenue was $26.5 billion, compared to $25.2 billion over a year ago.", "$25.2 billion", 25.2, "YoY"),
    ("Revenue was $25.2 billion over a year ago.", "$25.2 billion", 25.2, "none"),
    ("Revenue was $24.0 billion over a year ago.", "$24.0 billion", 24.0, "none"),
    ("Revenue was $26.5 billion, compared to $25.2 billion over last year.", "$25.2 billion", 25.2, "none"),
    ("Revenue was $26.5 billion, compared to $25.2 billion over the prior year.", "$25.2 billion", 25.2, "none"),
])
def test_over_a_year_ago_after_an_amount_is_never_wrong(snapshot, span, value_text, value, comparison):
    c = claim(span, value_text, value, metric="revenue", metric_text="revenue", unit="USD", scale="billions",
              entity="Target", period_text="second quarter of fiscal 2026", comparison=comparison)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "can mean a year earlier or more than a year earlier" in v.explanation


@pytest.mark.parametrize("span,value_text,value,period,filed", [
    ("Revenue was $26.5 billion over a year ago.", "$26.5 billion", 26.5, "second quarter of fiscal 2026", 26.539e9),
    ("Revenue was $26.5 billion, compared to $25.2 billion over a year ago.", "$25.2 billion", 25.2,
     "second quarter of fiscal 2025", 25.211e9),
])
def test_an_amount_before_over_a_year_ago_still_verifies_in_its_own_period(snapshot, span, value_text, value, period,
                                                                          filed):
    c = claim(span, value_text, value, metric="revenue", metric_text="revenue", unit="USD", scale="billions",
              entity="Target", period_text=period)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.value == pytest.approx(filed) for f in v.evidence)


@pytest.mark.parametrize("span,value_text,value,metric,unit", [
    ("Revenue was $26.5 billion, compared to $24.0 billion a year ago.", "$24.0 billion", 24.0, "revenue", "USD"),
    ("Revenue was $27.0 billion, up over a year ago.", "$27.0 billion", 27.0, "revenue", "USD"),
    ("Revenue grew 6.3% over a year ago.", "6.3%", 6.3, "revenue_growth", "%"),
])
def test_other_wordings_keep_their_wrong_verdict(snapshot, span, value_text, value, metric, unit):
    c = claim(span, value_text, value, metric=metric, metric_text="revenue", unit=unit,
              scale="billions" if unit == "USD" else "units", entity="Target",
              period_text="second quarter of fiscal 2026", comparison="YoY" if unit == "%" else "none")
    assert verify(c, snapshot).status == "wrong"


def test_extraction_keeps_over_a_year_ago_after_a_quoted_amount(snapshot):
    text = "Target's revenue was $26.5 billion, compared to $25.2 billion over a year ago."
    [c] = located(text, proposal("compared to $25.2 billion", "$25.2 billion", 25.2, metric="revenue",
                                 metric_text="revenue", unit="USD", scale="billions", entity="Target",
                                 period_text="second quarter of fiscal 2026"))
    assert c.span_text.endswith("over a year ago")
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


# --- Round 5. FX-adjusted wording in a claim's own words is a constant-currency figure ---------------------------
# AMD Q1 2026 release: revenue $10.253B. An FX-adjusted figure is never checked against reported results, even when it
# equals them; the reported figure next to it still is.

def amd(span, value_text, value, metric_text="revenue"):
    return claim(span, value_text, value, metric="revenue", metric_text=metric_text, unit="USD", scale="billions",
                 entity="AMD", period_text="Q1 2026")


@pytest.mark.parametrize("span,value_text,value,metric_text", [
    ("AMD's revenue was $10.0 billion FX-adjusted.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's revenue was $10.0 billion (FX-adjusted).", "$10.0 billion", 10.0, "revenue"),
    ("AMD's revenue was $10.0 billion, adjusted for foreign exchange.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's revenue was $10.25 billion, or $10.0 billion FX-adjusted.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's revenue was $10.253 billion FX-adjusted.", "$10.253 billion", 10.253, "revenue"),
    ("AMD's FX-adjusted revenue was $10.0 billion.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's FX adjusted revenue was $10.0 billion.", "$10.0 billion", 10.0, "FX adjusted revenue"),
    ("AMD's F/X-adjusted revenue was $10.0 billion.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's currency-adjusted revenue was $10.0 billion.", "$10.0 billion", 10.0, "currency-adjusted revenue"),
    ("AMD's foreign exchange-adjusted revenue was $10.0 billion.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's revenue adjusted for currency was $10.0 billion.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's foreign exchange neutral revenue was $10.0 billion.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's FX neutral revenue was $10.0 billion.", "$10.0 billion", 10.0, "revenue"),
    ("AMD's FX-neutral revenue was $10.0 billion.", "$10.0 billion", 10.0, "FX-neutral revenue"),
    ("On an FX-adjusted basis, AMD's revenue was $10.0 billion and its gross margin was 54%.", "$10.0 billion", 10.0,
     "revenue"),
])
def test_fx_adjusted_wording_in_a_claims_own_words_is_constant_currency(snapshot, span, value_text, value,
                                                                        metric_text):
    c = amd(span, value_text, value, metric_text)
    assert verifier.currency_neutral(c)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "constant-currency" in v.explanation


@pytest.mark.parametrize("span", [
    "AMD's revenue was $10.25 billion, or $10.0 billion FX-adjusted.",
    "AMD's revenue was $10.25 billion; FX-adjusted gross margin was 54%.",
])
def test_the_reported_figure_beside_fx_adjusted_wording_is_still_checked(snapshot, span):
    c = amd(span, "$10.25 billion", 10.25)
    assert not verifier.currency_neutral(c)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


def test_an_fx_adjusted_growth_figure_is_constant_currency(snapshot):
    c = change("Revenue rose 5.3%, or 6% FX-adjusted.", 6)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "constant-currency" in v.explanation


@pytest.mark.parametrize("text", ["AMD's revenue was $10.0 billion FX-adjusted.",
                                  "AMD's revenue was $10.0 billion, adjusted for foreign exchange."])
def test_extraction_keeps_fx_adjusted_wording_after_a_quoted_amount(snapshot, text):
    [c] = located(text, proposal("revenue was $10.0 billion", "$10.0 billion", 10.0, metric="revenue",
                                 metric_text="revenue", unit="USD", scale="billions", entity="AMD",
                                 period_text="Q1 2026"))
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "constant-currency" in v.explanation, c.span_text


# --- Round 5. "On a comparable basis" right after a revenue figure names the company's own measure ----------------
# Target Q2 FY2026 revenue $26,539M, up 5.27% year over year (10-Q 0000027419-26-000042). Such a figure is never
# checked against total revenue (5.3% and $26.5 billion verified before, 2.1% and $27.0 billion were "✗").

@pytest.mark.parametrize("span,value_text,value,metric", [
    ("Revenue rose 5.3% on a comparable basis.", "5.3%", 5.3, "revenue_growth"),
    ("Revenue rose 2.1% on a comparable basis.", "2.1%", 2.1, "revenue_growth"),
    ("Revenue rose 5.3%, on a comparable basis.", "5.3%", 5.3, "revenue_growth"),
    ("Revenue rose 5.3% year over year on a comparable basis.", "5.3%", 5.3, "revenue_growth"),
    ("Revenue rose 5.3% (on a comparable basis).", "5.3%", 5.3, "revenue_growth"),
    ("Revenue was $26.5 billion on a comparable basis.", "$26.5 billion", 26.5, "revenue"),
    ("Revenue was $27.0 billion on a comparable basis.", "$27.0 billion", 27.0, "revenue"),
])
def test_a_comparable_basis_right_after_a_revenue_figure_is_never_total_revenue(snapshot, span, value_text, value,
                                                                                 metric):
    assert unread(verify(target(span, value_text, value, metric, "revenue"), snapshot))


@pytest.mark.parametrize("span,value_text,value,metric,status", [
    ("Revenue rose 5.3% on comparable sales growth of 2.1%.", "5.3%", 5.3, "revenue_growth", "verified"),
    ("Revenue rose 5.3%, and 4% on a comparable basis.", "5.3%", 5.3, "revenue_growth", "verified"),
    ("Revenue rose 5.3% on a reported basis and 4% on a comparable basis.", "5.3%", 5.3, "revenue_growth", "verified"),
    ("Revenue rose 5.3%, compared with 4% on a comparable basis.", "5.3%", 5.3, "revenue_growth", "verified"),
    ("Revenue was $26.5 billion, up 5.3% on a comparable basis.", "$26.5 billion", 26.5, "revenue", "verified"),
    ("Revenue was $27.0 billion, up 5.3% on a comparable basis.", "$27.0 billion", 27.0, "revenue", "wrong"),
])
def test_a_comparable_basis_of_another_figure_leaves_this_one_checked(snapshot, span, value_text, value, metric,
                                                                      status):
    v = verify(target(span, value_text, value, metric, "revenue"), snapshot)
    assert v.status == status, v.explanation


def test_extraction_keeps_a_comparable_basis_after_a_quoted_figure(snapshot):
    text = "Target's revenue rose 2.1% on a comparable basis."
    [c] = located(text, proposal("revenue rose 2.1%", "2.1%", 2.1, metric="revenue_growth", metric_text="revenue",
                                 unit="%", scale="units", entity="Target", period_text="second quarter of fiscal 2026",
                                 comparison="YoY"))
    assert c.span_text.endswith("on a comparable basis")
    assert unread(verify(c, snapshot))


# --- Round 6. A comparison base with no period of its own is checked in the base period -------------------------
# Intel Q2 2026 gross margin 40.40% against 27.50% in Q2 2025 (release 8-K); Target revenue Q2 FY2026 $26,539M, Q1
# FY2026 $25,443M, Q2 FY2025 $25,211M (10-Qs). Extraction may give such a base no period ("") or the context's; the
# figure is then checked in the period the sentence implies (a year earlier unless it says sequential), never as a
# current-period figure, and with no comparison words only a match there counts.

INTC_CONTEXT = "Intel, second quarter of 2026"
TGT_CONTEXT = "Target, second quarter of fiscal 2026"


def based(span, value_text, value, *, entity="Target", period_text="", metric="revenue", metric_text="revenue"):
    unit, scale = ("%", "units") if value_text.endswith("%") else ("USD", "billions")
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale,
                 entity=entity, period_text=period_text)


def margin_base(span, value_text, value, period_text=""):
    return based(span, value_text, value, entity="Intel", period_text=period_text, metric="gross_margin",
                 metric_text="gross margin")


@pytest.mark.parametrize("c,context,filed,period", [
    (margin_base("Gross margin of 40.4% increased 1,282 basis points compared to 27.5%.", "27.5%", 27.5),
     INTC_CONTEXT, 27.5, "Q2 2025"),
    (margin_base("Gross margin of 40.4% increased 1,282 basis points compared to 27.5%.", "27.5%", 27.5,
                 "second quarter of 2026"), INTC_CONTEXT, 27.5, "Q2 2025"),
    (margin_base("Gross margin of 40.4% compared to a gross margin of 27.5%.", "27.5%", 27.5), INTC_CONTEXT, 27.5,
     "Q2 2025"),
    (based("Revenue of $26.5 billion increased 5.3% compared to $25.2 billion.", "$25.2 billion", 25.2),
     TGT_CONTEXT, 25.211e9, "Q2 FY2025"),
    (based("Revenue of $26.5 billion increased 5.3% compared to $25.2 billion.", "$25.2 billion", 25.2,
           period_text="second quarter of fiscal 2026"), TGT_CONTEXT, 25.211e9, "Q2 FY2025"),
    (based("Revenue rose 5.3% to $26.5 billion from $25.2 billion.", "$25.2 billion", 25.2), TGT_CONTEXT, 25.211e9,
     "Q2 FY2025"),
    (based("Revenue increased from $25.2 billion to $26.5 billion.", "$25.2 billion", 25.2), TGT_CONTEXT, 25.211e9,
     "Q2 FY2025"),
    (based("Revenue was $26.5 billion, compared to $25.2 billion.", "$25.2 billion", 25.2), TGT_CONTEXT, 25.211e9,
     "Q2 FY2025"),
    (based("Revenue rose 4.3% sequentially to $26.5 billion from $25.4 billion.", "$25.4 billion", 25.4), TGT_CONTEXT,
     25.443e9, "Q1 FY2026"),
    (based("Revenue in the second quarter of fiscal 2026 was $26.5 billion, compared to $25.2 billion.",
           "$25.2 billion", 25.2, period_text="second quarter of fiscal 2026"), None, 25.211e9, "Q2 FY2025"),
])
def test_an_unlabelled_comparison_base_is_checked_in_the_period_it_implies(snapshot, c, context, filed, period):
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert period in v.explanation and "the comparison period the sentence implies" in v.explanation
    assert any(f.value == pytest.approx(filed) for f in v.evidence)


@pytest.mark.parametrize("c,context,expected,period", [
    (margin_base("Gross margin of 40.4% increased 1,282 basis points year over year compared to 29.0%.", "29.0%",
                 29.0), INTC_CONTEXT, 27.5, "Q2 2025"),
    (based("Revenue rose 5.3% year over year to $26.5 billion from $24.0 billion.", "$24.0 billion", 24.0),
     TGT_CONTEXT, 25.211, "Q2 FY2025"),
    (based("Revenue rose 4.3% sequentially to $26.5 billion from $25.2 billion.", "$25.2 billion", 25.2),
     TGT_CONTEXT, 25.443, "Q1 FY2026"),
])
def test_a_wrong_base_is_wrong_against_the_period_the_sentence_names(snapshot, c, context, expected, period):
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert period in v.explanation and v.expected_value == pytest.approx(expected)


@pytest.mark.parametrize("c,context", [
    (margin_base("Gross margin of 40.4% increased 1,282 basis points compared to 29.0%.", "29.0%", 29.0),
     INTC_CONTEXT),
    (based("Revenue of $26.5 billion increased 10% compared to $24.0 billion.", "$24.0 billion", 24.0), TGT_CONTEXT),
    (based("Revenue was $26.5 billion, compared to analysts' estimates of $26.1 billion.", "$26.1 billion", 26.1),
     TGT_CONTEXT),
])
def test_a_base_that_misses_the_implied_period_without_comparison_words_is_unread(snapshot, c, context):
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


@pytest.mark.parametrize("c,context,status", [
    (margin_base("Gross margin of 40.4% increased 1,282 basis points compared to 27.5%.", "40.4%", 40.4),
     INTC_CONTEXT, "verified"),
    (margin_base("Gross margin of 40.4% increased 1,282 basis points compared to 27.5% a year ago.", "27.5%", 27.5),
     INTC_CONTEXT, "verified"),
    (based("Revenue was $26.5 billion, compared to $25.2 billion in the second quarter of fiscal 2025.",
           "$25.2 billion", 25.2, period_text="second quarter of fiscal 2025"), TGT_CONTEXT, "verified"),
    (based("Revenue was $27.0 billion.", "$27.0 billion", 27.0), TGT_CONTEXT, "wrong"),
])
def test_other_figures_in_a_comparison_keep_their_check(snapshot, c, context, status):
    v = verify(c, snapshot, context=context)
    assert v.status == status, v.explanation


@pytest.mark.parametrize("span,value_text", [
    ("Revenue rose 5%, with revenue from continuing operations of $27.0 billion.", "$27.0 billion"),
    ("Intel's gross margin of 40.4% versus AMD's 53.8%.", "53.8%"),
    ("Revenue from $25.2 billion of orders was recognized.", "$25.2 billion"),
    ("Revenue was $26.5 billion, compared to $25.2 billion a year earlier.", "$25.2 billion"),
    ("Revenue was $26.5 billion, compared to $25.2 billion in 2025.", "$25.2 billion"),
])
def test_a_source_a_peer_or_a_dated_amount_is_not_an_unlabelled_base(span, value_text):
    assert verifier._unlabelled_base(based(span, value_text, 1.0)) is None


def test_a_figure_with_no_period_of_its_own_never_gets_a_period_error(snapshot):
    c = based("Revenue was $25.2 billion.", "$25.2 billion", 25.2)
    v = verify(c, snapshot, context=TGT_CONTEXT)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "names no period" in v.explanation
    dated = c.model_copy(update={"period_text": "second quarter of fiscal 2026"})
    assert verify(dated, snapshot, context=TGT_CONTEXT).reason == "period_mismatch"  # a period the draft gave


@pytest.mark.parametrize("text,quote,value_text,value,metric,metric_text,unit,scale,entity,context", [
    ("Intel's gross margin of 40.4% increased 1,282 basis points compared to 27.5%.", "27.5%", "27.5%", 27.5,
     "gross_margin", "gross margin", "%", "units", "Intel", INTC_CONTEXT),
    ("Target's revenue rose 5.3% to $26.5 billion from $25.2 billion.", "$25.2 billion", "$25.2 billion", 25.2,
     "revenue", "revenue", "USD", "billions", "Target", TGT_CONTEXT),
])
def test_extraction_keeps_the_comparison_a_quoted_base_belongs_to(snapshot, text, quote, value_text, value, metric,
                                                                   metric_text, unit, scale, entity, context):
    [c] = located(text, proposal(quote, value_text, value, metric=metric, metric_text=metric_text, unit=unit,
                                 scale=scale, entity=entity, period_text=""))
    assert c.span_text.startswith(entity)
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("verified", "match"), (c.span_text, v.explanation)


# --- Round 6. "The comparable period of 2025" is that year's quarter -----------------------------------------------
# Pfizer diluted EPS: Q1 2026 $0.47, Q1 2025 $0.52, full year 2025 $1.36. Carrier: Q2 2026 $0.60, Q2 2025 $0.68, full
# year 2025 $1.72. Target (fiscal year ending January): Q2 FY2025 revenue $25,211M.

PFE_CONTEXT = "Pfizer, first quarter of 2026"
PFE_SENTENCE = ("Pfizer reported $0.47 per diluted share for the first quarter of 2026, compared to $0.52 per diluted "
                "share in the comparable period of 2025.")


def comparable_eps(span, value, period_text, entity="Pfizer", period_type="quarter"):
    c = claim(span, f"${value:.2f}", value, metric="eps_diluted", metric_text="per diluted share", entity=entity,
              period_text=period_text)
    return c.model_copy(update={"period_type": period_type})


@pytest.mark.parametrize("c,context,filed,period", [
    (comparable_eps(PFE_SENTENCE, 0.52, "in the comparable period of 2025"), PFE_CONTEXT, 0.52, "Q1 2025"),
    (comparable_eps(PFE_SENTENCE, 0.52, "in the comparable period of 2025"), None, 0.52, "Q1 2025"),
    (comparable_eps("Pfizer earned $0.47 per diluted share, compared to $0.52 per diluted share in the same quarter "
                    "of 2025.", 0.52, "in the same quarter of 2025"), PFE_CONTEXT, 0.52, "Q1 2025"),
    (comparable_eps("Pfizer earned $0.47 per diluted share, compared to $0.52 per diluted share in the corresponding "
                    "period of 2025.", 0.52, "in the corresponding period of 2025"), PFE_CONTEXT, 0.52, "Q1 2025"),
    (comparable_eps("Carrier earned $0.60 per diluted share in the second quarter of 2026, compared to $0.68 in the "
                    "same period of 2025.", 0.68, "in the same period of 2025", entity="Carrier"), None, 0.68,
     "Q2 2025"),
    (based("Target's revenue was $26.5 billion, compared to $25.2 billion in the comparable period of 2025.",
           "$25.2 billion", 25.2, period_text="in the comparable period of 2025"), TGT_CONTEXT, 25.211e9,
     "Q2 FY2025"),
])
def test_the_comparable_period_of_a_year_is_that_years_quarter(snapshot, c, context, filed, period):
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert period in v.explanation and any(f.value == pytest.approx(filed) for f in v.evidence)


def test_a_wrong_figure_for_the_comparable_period_is_wrong_against_that_quarter(snapshot):
    v = verify(comparable_eps(PFE_SENTENCE.replace("$0.52", "$0.55"), 0.55, "in the comparable period of 2025"),
               snapshot, context=PFE_CONTEXT)
    assert (v.status, v.reason) == ("wrong", "value_mismatch") and "Q1 2025" in v.explanation, v.explanation
    assert v.expected_value == pytest.approx(0.52)


@pytest.mark.parametrize("c,context", [
    (based("Target's revenue was $26.5 billion, compared to $25.2 billion in the comparable period of 2025.",
           "$25.2 billion", 25.2, period_text="in the comparable period of 2025"), "Target, second quarter of 2026"),
    (based("Target's revenue for the first quarter of fiscal 2026 was $25.4 billion; in the second quarter it was $26.5 "
           "billion, compared to $25.2 billion in the comparable period of 2025.", "$25.2 billion", 25.2,
           period_text="in the comparable period of 2025"), TGT_CONTEXT),
])
def test_a_comparable_period_that_names_no_one_quarter_stays_unread(snapshot, c, context):
    # a calendar year on a January fiscal year, or two quarters in the sentence: no quarter to take
    assert verify(c, snapshot, context=context).status == "unverifiable"


@pytest.mark.parametrize("value,period_type,status", [(0.52, "quarter", "unverifiable"), (1.36, "quarter", "verified"),
                                                      (1.36, "annual", "verified"), (0.52, "annual", "wrong")])
def test_a_quarterly_figure_whose_period_reads_as_a_year_is_never_wrong(snapshot, value, period_type, status):
    c = comparable_eps(f"Pfizer earned ${value:.2f} per diluted share in 2025.", value, "in 2025",
                       period_type=period_type)
    v = verify(c, snapshot)
    assert v.status == status, v.explanation
    if status == "unverifiable":
        assert v.reason == "ambiguous" and "quarterly figure" in v.explanation


@pytest.mark.parametrize("period_text,span,context,expected", [
    ("in the comparable period of 2025", PFE_SENTENCE, None, "first quarter of 2025"),
    ("in the same quarter of 2025", "EPS was $0.52 in the same quarter of 2025.", PFE_CONTEXT, "first quarter of 2025"),
    ("the corresponding period of fiscal 2025", "EPS was $0.52 in the corresponding period of fiscal 2025.",
     "Target, second quarter of fiscal 2026", "second quarter of fiscal 2025"),
    ("in 2025", "EPS was $0.52 in 2025.", PFE_CONTEXT, None),
    ("in the comparable period of 2025", "EPS was $0.52 in the comparable period of 2025.", "Pfizer, 2026", None),
])
def test_the_comparable_period_reading(period_text, span, context, expected):
    c = comparable_eps(span, 0.52, period_text)
    assert verifier._comparable_period_text(c, context) == expected


# --- Round 6. A change measured against a stated base period is checked against that period ----------------------
# Carrier diluted EPS: Q2 2026 $0.60, Q1 2026 $0.28, Q2 2025 $0.68, Q1 2025 $0.47 (10-Qs): -11.8% year over year and
# +114.3% sequentially in Q2 2026, -40.4% year over year in Q1 2026. Target revenue grew 5.27% year over year in Q2
# FY2026. Before, a change with no comparison typed was checked year over year or sequentially even when its own words
# named another base period ("compared to first quarter of 2025" verified the -11.8%).

CARR_CONTEXT = "Carrier, second quarter of 2026"


def eps_change(span, value, comparison="none", period_text="second quarter of 2026"):
    return claim(span, f"{value} percent", value, metric="other", metric_text="diluted EPS", unit="%",
                 entity="Carrier", period_text=period_text, comparison=comparison)


@pytest.mark.parametrize("c,context", [
    (eps_change("Diluted EPS decreased 12 percent compared to first quarter of 2025.", 12), CARR_CONTEXT),
    (eps_change("Diluted EPS decreased 12 percent compared to first quarter of 2025.", 12, comparison="YoY"),
     CARR_CONTEXT),
    (eps_change("Diluted EPS decreased 12 percent from Q1 2025.", 12), CARR_CONTEXT),
    (claim("Revenue rose 5.3% compared to the first quarter of fiscal 2025.", "5.3%", 5.3, metric="revenue_growth",
           metric_text="revenue", unit="%", entity="Target", period_text="second quarter of fiscal 2026",
           comparison="none"), TGT_CONTEXT),
])
def test_a_change_against_another_stated_base_is_never_assumed_year_over_year(snapshot, c, context):
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "neither the year-earlier period nor the prior quarter" in v.explanation


@pytest.mark.parametrize("c,context,said", [
    (eps_change("Diluted EPS decreased 12 percent compared to the second quarter of 2025.", 12), CARR_CONTEXT,
     "fell 11.8% year over year"),
    (eps_change("Diluted EPS increased 114 percent compared to the first quarter of 2026.", 114, comparison="YoY"),
     CARR_CONTEXT, "114.3%"),
    (eps_change("In the first quarter of 2026, diluted EPS decreased 40 percent compared to the first quarter of "
                "2025.", 40, period_text="first quarter of 2026"), CARR_CONTEXT, "fell 40.4% year over year"),
    (claim("Revenue rose 5.3% compared to the second quarter of fiscal 2025.", "5.3%", 5.3, metric="revenue_growth",
           metric_text="revenue", unit="%", entity="Target", period_text="second quarter of fiscal 2026",
           comparison="none"), TGT_CONTEXT, "grew 5.27% year over year"),
])
def test_a_change_against_the_year_earlier_or_prior_quarter_base_is_checked_against_it(snapshot, c, context, said):
    v = verify(c, snapshot, context=context)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert said in v.explanation


def test_a_wrong_change_against_a_stated_base_is_wrong(snapshot):
    v = verify(eps_change("Diluted EPS decreased 20 percent compared to the second quarter of 2025.", 20), snapshot,
               context=CARR_CONTEXT)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert "fell 11.8% year over year" in v.explanation and "quarter over quarter" not in v.explanation


def test_a_change_its_own_words_date_to_another_quarter_is_unread(snapshot):
    c = eps_change("In the first quarter of 2026, diluted EPS decreased 40 percent compared to the first quarter of "
                   "2025.", 40)  # the period came from the context line, the sentence says Q1 2026
    v = verify(c, snapshot, context=CARR_CONTEXT)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "Q1 2026" in v.explanation, v.explanation


@pytest.mark.parametrize("span,value,expected", [
    ("Diluted EPS decreased 12 percent compared to first quarter of 2025.", 12, "first quarter of 2025"),
    ("Diluted EPS decreased 12 percent from Q1 2025.", 12, "Q1 2025"),
    ("Compared with the second quarter of 2025, diluted EPS decreased 12 percent.", 12, "second quarter of 2025"),
    ("Diluted EPS decreased 12 percent from a year ago.", 12, None),
    ("Diluted EPS decreased 12 percent over the prior-year period.", 12, None),
    ("Diluted EPS decreased 12 percent, from $0.68 in the second quarter of 2025.", 12, None),
])
def test_the_stated_base_period_reading(span, value, expected):
    assert verifier._stated_base_period(eps_change(span, value)) == expected


@pytest.mark.parametrize("text,status", [
    ("Carrier's diluted EPS decreased 12 percent compared to first quarter of 2025.", "unverifiable"),
    ("Carrier's diluted EPS decreased 12 percent compared to the second quarter of 2025.", "verified"),
])
def test_extraction_keeps_a_stated_base_period_after_a_quoted_change(snapshot, text, status):
    [c] = located(text, proposal("diluted EPS decreased 12 percent", "12 percent", 12, metric="other",
                                 metric_text="diluted EPS", unit="%", scale="units", entity="Carrier",
                                 period_text="second quarter of 2026"))
    assert "quarter of 2025" in c.span_text
    assert verify(c, snapshot, context=CARR_CONTEXT).status == status


# --- Round 7. A non-GAAP margin is checked only against non-GAAP evidence -----------------------------------------
# Releases often file the GAAP figure that opens a reconciliation under the table's non-GAAP basis: Walmart's
# "Operating income, as reported | 8,708" (Q4 FY2026), Williams-Sonoma's "Operating income | 328,059" (Q2 FY2025),
# Federal Realty's "Operating income | 180,654" (Q4 2025), Illinois Tool Works' "Net Income | 768" (Q1 2026), Snap-on's
# "Gross profit | 596.1" (Q4 2024), each equal to the same release's GAAP figure. An adjusted margin was checked against
# the GAAP margin they give. Genuine non-GAAP rows: AMD Q2 2026 non-GAAP operating income $3,094M on revenue $11,536M
# (26.82%; GAAP 17.25%), non-GAAP gross margin 56% (GAAP 54%); Delta 2025 "Operating income, adjusted" $5,804M on
# "Operating revenue, adjusted" $58,287M (9.96%; on reported revenue $63,364M, 9.16%); Invesco Q3 2025 adjusted
# operating income $406.1M on net revenues $1,186.3M (34.23%; on operating revenues $1,640.4M, 24.76%).

def margin_claim(span, value, entity, period_text, metric_text, metric="other", period_type="quarter", basis=None,
                 unit="%"):
    value_text = f"{value}%" if unit == "%" else value
    c = claim(span, value_text, value if unit == "%" else float(value.split()[0]), metric=metric,
              metric_text=metric_text, unit=unit, entity=entity, period_text=period_text, basis=basis)
    return c.model_copy(update={"period_type": period_type})


@pytest.mark.parametrize("c", [
    margin_claim("Walmart's adjusted operating margin was 4.6% in the quarter.", 4.6, "Walmart", "Q4 FY2026",
                 "adjusted operating margin"),
    margin_claim("Walmart's adjusted operating margin was 4.9% in the quarter.", 4.9, "Walmart", "Q4 FY2026",
                 "adjusted operating margin"),
    margin_claim("Williams-Sonoma's adjusted operating margin was 17.9%.", 17.9, "Williams-Sonoma", "Q2 FY2025",
                 "adjusted operating margin"),
    margin_claim("Williams-Sonoma's adjusted operating margin was 18.5%.", 18.5, "Williams-Sonoma", "Q2 FY2025",
                 "adjusted operating margin"),
    margin_claim("Federal Realty's adjusted operating margin was 53.8%.", 53.8, "Federal Realty", "Q4 2025",
                 "adjusted operating margin"),
    margin_claim("Illinois Tool Works' adjusted net margin was 19.1%.", 19.1, "Illinois Tool Works", "Q1 2026",
                 "adjusted net margin"),
    margin_claim("Illinois Tool Works' adjusted net margin was 20.0%.", 20.0, "Illinois Tool Works", "Q1 2026",
                 "adjusted net margin"),
    margin_claim("Snap-on's adjusted gross margin was 45.9%.", 45.9, "Snap-on", "Q4 2024", "adjusted gross margin",
                 metric="gross_margin"),
    margin_claim("Federal Realty's adjusted operating margin expanded 18.7 percentage points year over year.",
                 "18.7 percentage points", "Federal Realty", "Q4 2025", "adjusted operating margin", unit="other"),
])
def test_an_adjusted_margin_is_never_checked_against_a_gaap_row_filed_as_non_gaap(snapshot, c):
    if c.unit == "other":
        c = c.model_copy(update={"comparison": "YoY"})
    v = verify(c, snapshot)
    assert unread(v), v.explanation


@pytest.mark.parametrize("c,filed", [
    (margin_claim("Walmart's operating margin was 4.6% in the quarter.", 4.6, "Walmart", "Q4 FY2026",
                  "operating margin"), "4.57%"),
    (margin_claim("Williams-Sonoma's operating margin was 17.9%.", 17.9, "Williams-Sonoma", "Q2 FY2025",
                  "operating margin"), "17.86%"),
    (margin_claim("Illinois Tool Works' net margin was 19.1%.", 19.1, "Illinois Tool Works", "Q1 2026", "net margin"),
     "19.12%"),
    (margin_claim("Delta's operating margin was 9.2% in 2025.", 9.2, "Delta", "fiscal 2025", "operating margin",
                  period_type="annual"), "9.19%"),
])
def test_a_plain_margin_keeps_its_gaap_check(snapshot, c, filed):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match") and filed in v.explanation, v.explanation


@pytest.mark.parametrize("c,filed", [
    (margin_claim("Our second quarter adjusted operating margin was 26.8%.", 26.8, "AMD", "Q2 2026",
                  "adjusted operating margin"), "26.82%"),
    (margin_claim("Our second quarter adjusted gross margin was 56%.", 56, "AMD", "Q2 2026", "adjusted gross margin",
                  metric="gross_margin"), "56.0%"),
    (margin_claim("Delta's adjusted operating margin was 10.0% in 2025.", 10.0, "Delta", "fiscal 2025",
                  "adjusted operating margin", period_type="annual"), "9.96%"),
    (margin_claim("Invesco's adjusted operating margin was 34.2% in the third quarter of 2025.", 34.2, "Invesco",
                  "Q3 2025", "adjusted operating margin"), "34.23%"),
])
def test_a_true_adjusted_margin_verifies_against_non_gaap_evidence(snapshot, c, filed):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match") and filed in v.explanation, v.explanation
    assert "8-K press release" in v.explanation
    assert any(f.basis == "non-GAAP" for f in v.evidence) and not any(
        f.basis == "non-GAAP" and f.metric != "revenue" and "reported" in (f.locator or "") for f in v.evidence)


@pytest.mark.parametrize("c,expected", [
    (margin_claim("Our second quarter adjusted operating margin was 30.0%.", 30.0, "AMD", "Q2 2026",
                  "adjusted operating margin"), 26.82),
    (margin_claim("Delta's adjusted operating margin was 12.0% in 2025.", 12.0, "Delta", "fiscal 2025",
                  "adjusted operating margin", period_type="annual"), 9.96),
    (margin_claim("Invesco's adjusted operating margin was 33.0% in the third quarter of 2025.", 33.0, "Invesco",
                  "Q3 2025", "adjusted operating margin"), 34.23),
])
def test_a_wrong_adjusted_margin_is_wrong_against_non_gaap_evidence(snapshot, c, expected):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("c,words", [
    (margin_claim("Our second quarter adjusted operating margin was 17.3%.", 17.3, "AMD", "Q2 2026",
                  "adjusted operating margin"), "GAAP operating margin"),
    (margin_claim("Delta's adjusted operating margin was 9.2% in 2025.", 9.2, "Delta", "fiscal 2025",
                  "adjusted operating margin", period_type="annual"), "on reported revenue"),
    (margin_claim("Invesco's adjusted operating margin was 24.8% in the third quarter of 2025.", 24.8, "Invesco",
                  "Q3 2025", "adjusted operating margin"), "on reported revenue"),
])
def test_an_adjusted_margin_that_fits_only_a_gaap_or_reported_base_is_unread(snapshot, c, words):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and words in v.explanation, v.explanation


@pytest.mark.parametrize("c", [
    margin_claim("Our second quarter GAAP gross margin was 56%.", 56, "AMD", "Q2 2026", "GAAP gross margin",
                 metric="gross_margin"),
    margin_claim("Our second quarter GAAP gross margin was 56%.", 56, "AMD", "Q2 2026", "GAAP gross margin",
                 metric="gross_margin", basis="GAAP"),
    margin_claim("Our second quarter GAAP operating margin was 26.8%.", 26.8, "AMD", "Q2 2026", "GAAP operating margin"),
])
def test_a_gaap_margin_never_matches_a_non_gaap_one(snapshot, c):
    assert verify(c, snapshot).status != "verified"


def test_a_true_adjusted_margin_change_still_verifies(snapshot):
    c = margin_claim("AMD's adjusted operating margin expanded 15.1 percentage points year over year.",
                     "15.1 percentage points", "AMD", "Q2 2026", "adjusted operating margin", unit="other")
    v = verify(c.model_copy(update={"comparison": "YoY"}), snapshot)
    assert (v.status, v.reason) == ("verified", "match") and "15.15 percentage points" in v.explanation, v.explanation


@pytest.mark.parametrize("label,gaap,expected", [
    ("Operating income, as reported | 8,708", (), True),
    ("Operating income | 180,654", ("same",), True),
    ("Operating income | 63,956", (), False),
    ("Adjusted operating income | 7,135", ("same",), False),
    ("Non‐GAAP operating income | 709.9", (), False),
    ("Operating income, GAAP basis | 1,000", (), True),
])
def test_the_gaap_row_reading(label, gaap, expected):
    from datetime import date, datetime, timezone
    from receipts.types import Fact
    from receipts.verify import Obs

    def row(basis):
        return Fact(cik=1, ticker="X", metric="operating_income", basis=basis, value=1.0, unit="USD",
                    period_end=date(2026, 6, 30), source="press_release", form="8-K", accession="a",
                    accepted_at=datetime(2026, 7, 30, tzinfo=timezone.utc), url="u", locator=label)
    reported = [Obs(1.0, (row("GAAP"),))] if gaap else []
    assert verifier._gaap_row(row("non-GAAP"), reported) is expected


# --- Round 7. An operating margin's own words in the span qualify it, as for gross and net margins ---------------
# AMD Q2 2026 operating margin: GAAP 17.25%, non-GAAP 26.82%. With metric_text "operating margin" alone, a qualifier
# in the span ("core", "comparable", "excluding special items", a segment) was dropped and the figure checked against
# the GAAP margin; gross and net margins already read those words.

def amd_margin(span, value, metric_text="operating margin"):
    return margin_claim(span, value, "AMD", "Q2 2026", metric_text)


@pytest.mark.parametrize("span", [
    "Our second quarter core operating margin was 30.0%.",
    "Our second quarter comparable operating margin was 30.0%.",
    "Our second quarter operating margin, excluding special items, was 30.0%.",
    "AMD's Data Center operating margin was 17.3%.",
])
def test_a_qualifier_in_the_span_leaves_an_operating_margin_unread(snapshot, span):
    v = verify(amd_margin(span, 30.0 if "30.0" in span else 17.3), snapshot)
    assert unread(v), v.explanation


@pytest.mark.parametrize("span,value,filed", [
    ("Our second quarter operating margin was 17.3%.", 17.3, "17.25%"),
    ("AMD's GAAP operating margin was 17.3% in the quarter.", 17.3, "17.25%"),
    ("AMD reported an operating margin of 17.3%.", 17.3, "17.25%"),
    ("Operating margin was 17.3%, up from 11.7% a year ago.", 17.3, "17.25%"),
    ("Our operating margin, as adjusted, was 26.8%.", 26.8, "26.82%"),
])
def test_an_operating_margin_with_only_framing_words_keeps_its_check(snapshot, span, value, filed):
    v = verify(amd_margin(span, value), snapshot)
    assert (v.status, v.reason) == ("verified", "match") and filed in v.explanation, v.explanation


# --- Round 8. A change in dollars or per share is checked as a change, never against the level ---------------------
# Full set (Nov 2): Charter's "a decrease of $192 million" (free cash flow, Q1 2026 $1,372M, Q1 2025 $1,564M in one
# release), Norfolk Southern's "up $0.32" (adjusted diluted EPS $3.62, $3.30 in the Q3 2026 release) and Truist's
# taxable-equivalent net interest income "up $27 million" (Q3 2026 $3,707M, Q3 2025 $3.68B in its Q3 2026 release) were
# compared with the level. Truist's "up $32 million" for Q2 2026 has no Q2 2025 taxable-equivalent figure on file.
CHTR_PR = "0001091667-26-000027"   # Charter Q1 2026 release
NSC_PR = "0001193125-26-428384"    # Norfolk Southern Q3 2026 release
TFC_PR = "0000092230-26-000108"    # Truist Q3 2026 release
CHTR_FCF = ("Free cash flow in the first quarter of 2026 totaled $1.4 billion, a decrease of {} compared to the first "
            "quarter of 2025.")
NSC_EPS = "adjusted diluted earnings per share were $3.62, up {}"
TFC_NII = "Taxable-equivalent net interest income was up {}"


def chtr_change(value, words="a decrease of", comparison="YoY", span=CHTR_FCF):
    text = f"${value} million"
    return claim(span.replace("a decrease of {}", f"{words} {text}"), text, value, metric="free_cash_flow",
                 metric_text="Free cash flow", unit="USD", scale="millions", entity="Charter Communications (CHTR)",
                 period_text="first quarter of 2026", comparison=comparison)


def nsc_change(value):
    return claim(NSC_EPS.format(f"${value:.2f}"), f"${value:.2f}", value, metric="eps_diluted",
                 metric_text="adjusted diluted earnings per share", entity="Norfolk Southern (NSC)",
                 period_text="third quarter of fiscal 2026, the quarter ended September 30, 2026", basis="non-GAAP",
                 comparison="YoY")


def tfc_change(value, period_text="third quarter of fiscal 2026"):
    return claim(TFC_NII.format(f"${value} million"), f"${value} million", value, metric="net_interest_income",
                 metric_text="Taxable-equivalent net interest income", unit="USD", scale="millions",
                 entity="Truist Financial", period_text=period_text, comparison="YoY")


@pytest.mark.parametrize("c,said,accession", [
    (chtr_change(192), "free cash flow fell $192 million year over year in Q1 2026", CHTR_PR),
    (nsc_change(0.32), "non-GAAP diluted EPS grew $0.32 year over year in Q3 2026", NSC_PR),
    (tfc_change(27), "net interest income grew $27 million year over year in Q3 2026", TFC_PR),
])
def test_a_true_dollar_change_verifies_as_the_change(snapshot, c, said, accession):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match") and said in v.explanation, v.explanation
    assert len(v.evidence) == 2 and {f.accession for f in v.evidence} == {accession}
    assert "(from $" in v.explanation  # both levels are shown


@pytest.mark.parametrize("c,expected", [
    (chtr_change(150), -192), (nsc_change(0.40), 0.32), (tfc_change(45), 27),
    (chtr_change(192, words="an increase of"), -192),  # the right size, the wrong direction
])
def test_a_wrong_dollar_change_is_wrong_against_the_change(snapshot, c, expected):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(expected)
    assert "1.372 billion for" not in v.explanation and "$3.62 for" not in v.explanation  # never the level


def test_a_dollar_change_without_its_base_on_file_is_unread(snapshot):
    # The filed-facts check (_verify_filed) is what this pins; the company's 8-K release text can still confirm the
    # figure afterwards (receipts.release_text, tests/test_release_text.py).
    v = verifier._verify_filed(tfc_change(32, period_text="second quarter of fiscal 2026"), snapshot)
    assert unread(v), v.explanation
    assert "3.667 billion" not in v.explanation


def test_a_dollar_change_against_an_unnamed_base_is_never_wrong(snapshot):
    span = "Free cash flow totaled $1.4 billion, a decrease of {}."
    assert verify(chtr_change(192, comparison="none", span=span), snapshot).status == "verified"
    assert unread(verify(chtr_change(150, comparison="none", span=span), snapshot))


def test_a_level_the_change_reaches_is_still_a_level(snapshot):
    for span, value_text, value in [
        ("Free cash flow in the first quarter of 2026 totaled $1.4 billion, a decrease of $192 million.",
         "$1.4 billion", 1.4),
        ("Free cash flow fell $192 million to $1.372 billion in the first quarter of 2026.", "$1.372 billion", 1.372),
    ]:
        c = claim(span, value_text, value, metric="free_cash_flow", metric_text="Free cash flow", unit="USD",
                  scale="billions", entity="Charter Communications (CHTR)", period_text="first quarter of 2026")
        v = verify(c, snapshot)
        assert (v.status, v.reason) == ("verified", "match") and "free cash flow was $1.372 billion" in v.explanation


def test_an_unqualified_eps_change_may_be_the_basic_change(snapshot):
    # Amgen Q2 2026: diluted EPS $4.37 (Q2 2025 $2.65), basic $4.40 ($2.66).
    def eps_change(value):
        return per_share(f"Amgen's EPS was $4.40, up ${value:.2f} from a year earlier.", value, metric_text="EPS")
    v = verify(eps_change(1.74), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert "basic EPS grew $1.74 year over year" in v.explanation and "doesn't say basic or diluted" in v.explanation
    v = verify(eps_change(1.90), snapshot)
    assert (v.status, v.expected_value) == ("wrong", pytest.approx(1.72)), v.explanation


def test_a_change_described_as_a_loss_is_unread(snapshot):
    c = claim("Centene's net loss decreased $50 million from a year earlier.", "$50 million", 50.0,
              metric="net_income", metric_text="net loss", unit="USD", scale="millions", entity="Centene",
              period_text="Q3 2026", comparison="YoY")
    v = verify(c, snapshot)
    assert unread(v) and "is a change" in v.explanation, v.explanation


@pytest.mark.parametrize("span,value_text,expected", [
    ("adjusted diluted earnings per share were $3.62, up $0.32", "$0.32", True),
    ("Free cash flow totaled $1.4 billion, a decrease of $192 million", "$192 million", True),
    ("Taxable-equivalent net interest income increased by approximately $36 million", "$36 million", True),
    ("Revenue rose 5%, or $1.2 billion, from a year ago", "$1.2 billion", True),
    ("Revenue rose 5% (or $1.2 billion)", "$1.2 billion", True),
    ("an increase in revenue of $1.2 billion", "$1.2 billion", True),
    ("Adjusted EPS is $1.49 for the second quarter 2026, $0.24 lower than prior year", "$0.24", True),
    ("a $192 million decrease in free cash flow", "$192 million", True),
    ("Revenue rose 5% to $26.5 billion", "$26.5 billion", False),
    ("Revenue was up from $25.2 billion", "$25.2 billion", False),
    ("Revenue increased from $25.2 billion to $26.5 billion", "$26.5 billion", False),
    ("Data Center made up $35 billion of revenue", "$35 billion", False),
    ("net income of $1.2 billion, or $1.50 per share", "$1.50", False),
    ("Revenue was $26.5 billion", "$26.5 billion", False),
    ("Revenue grew 5% to a record $26.5 billion", "$26.5 billion", False),
])
def test_the_dollar_change_reading(span, value_text, expected):
    unit = "USD/share" if value_text in ("$0.32", "$0.24", "$1.50") else "USD"
    c = claim(span, value_text, float(value_text.strip("$").split()[0]), metric="other", metric_text="figure",
              unit=unit, scale="units" if unit != "USD" else "millions")
    assert verifier.absolute_change(c) is expected


# --- Round 8. A charge or other item within a reported figure is never checked as the figure ------------------------
# Martin Marietta Q1 2026: "gross profit, aggregates gross profit and aggregates gross profit per ton included a charge
# of $22 million, $22 million and $0.49 per ton, respectively" (aggregates gross profit was $288M). The second $22
# million was extracted with the quote ", $22 million" and compared with aggregates gross profit; its list's head is in
# the draft, which receipts.check now passes to the verifier.
MLM_TEXT = ("Quarter ended March 31, 2026, gross profit, aggregates gross profit and aggregates gross profit per ton "
            "included a charge of $22 million, $22 million and $0.49 per ton, respectively, for the impact of selling "
            "acquired inventory after markup to fair value as part of acquisition accounting.")


def mlm_claims():
    def at(span, start, value_text, value, metric, metric_text, unit="USD", scale="millions", segment=None):
        c = claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale,
                  entity="Martin Marietta Materials (MLM)", period_text="Quarter ended March 31, 2026")
        c = c.model_copy(update={"span_start": start, "span_end": start + len(span), "segment": segment})
        assert MLM_TEXT[c.span_start:c.span_end] == span
        return c
    return [
        at(MLM_TEXT[30:136], 30, "$22 million", 22.0, "gross_profit", "gross profit"),
        at(", $22 million", 136, "$22 million", 22.0, "gross_profit", "aggregates gross profit", segment="aggregates"),
        at(MLM_TEXT[72:167], 72, "$0.49", 0.49, "other", "aggregates gross profit per ton", scale="units",
           segment="aggregates"),
    ]


def test_a_charge_within_gross_profit_is_unread(snapshot):
    # The first and third figures were unread already (their wording names no tracked figure); the second is read as
    # aggregates gross profit unless its list's head, in the draft, makes it a charge.
    first, second, third = (verify(c, snapshot, draft=MLM_TEXT) for c in mlm_claims())
    assert all(unread(v) and "288" not in v.explanation for v in (first, second, third))
    assert "“a charge” makes $22 million an item" in second.explanation, second.explanation
    assert "doesn't track this kind of figure (gross profit)" in first.explanation
    assert "“per ton”" in third.explanation


def test_check_passes_the_draft_to_the_verifier(snapshot, monkeypatch):
    claims = mlm_claims()
    monkeypatch.setattr(extract, "extract_detailed",
                        lambda text, context=None, **kw: extract.ExtractResult(list(claims), context, []))
    from receipts.check import check_detailed
    result = check_detailed(MLM_TEXT, store=snapshot, context="Martin Marietta Materials (MLM), first quarter of "
                                                              "fiscal 2026")
    assert [v.status for _, v in result.results] == ["unverifiable"] * 3
    assert "“a charge” makes $22 million an item" in result.results[1][1].explanation


def test_a_span_not_at_its_offsets_reads_no_draft(snapshot):
    c = mlm_claims()[1]
    assert verifier.draft_line(c, MLM_TEXT).text == MLM_TEXT
    assert verifier.draft_line(c, MLM_TEXT.replace("$22 million, $22", "$22 million,  $22")) is None
    assert verifier.draft_line(c.model_copy(update={"span_start": 0, "span_end": 13}), MLM_TEXT) is None


@pytest.mark.parametrize("span,value_text,words", [
    ("gross profit included a charge of $22 million", "$22 million", "a charge"),
    ("an unfavorable impact of $0.11 per share", "$0.11", "an unfavorable impact"),
    ("a $300 million impairment", "$300 million", "impairment"),
    ("$22 million in restructuring charges", "$22 million", "in restructuring charges"),
    ("Net income was $1.2 billion, including $0.5 billion from a gain", "$0.5 billion", "including"),
    ("Adjusted EPS, excluding $0.15 of charges, was $2.50", "$0.15", "excluding"),
    ("Net income was $1.2 billion, including $0.5 billion from a gain", "$1.2 billion", None),
    ("Adjusted EPS, excluding $0.15 of charges, was $2.50", "$2.50", None),
    ("a net loss of $50 million", "$50 million", None),
    ("operating costs of $5 billion", "$5 billion", None),
    ("Revenue was $26.5 billion despite currency headwinds", "$26.5 billion", None),
    ("Revenue of $26.5 billion", "$26.5 billion", None),
])
def test_the_item_reading(span, value_text, words):
    c = claim(span, value_text, float(value_text.strip("$").split()[0]), metric="other", metric_text="figure",
              unit="USD", scale="millions")
    assert verifier.item_wording(c) == words


# --- Round 8. An adjusted, comparable or reported figure is judged only on its own basis ----------------------------
# Revvity Q2 2026: "adjusted operating income was $113 million, as compared to $89 million" against release rows labelled
# "Operating income from continuing operations" (93,696 and 84,651), filed with its non-GAAP figures but naming no
# adjusted measure. Constellation Brands Q2 FY2027: "Tax rate: reported ~12%; comparable ~20%" against the release's
# 20.2% (GAAP; comparable 20.4%), which fits the line's comparable ~20%. Estée Lauder Q3 FY2026: "Adjusted gross margin
# of approximately 75.0%" was checked against the adjusted rows (76.4%; Q3 FY2025 75.0%), and its "✗" was a period
# diagnosis on a line that names no period. Round 7's GAAP rows filed as non-GAAP (Walmart "Operating income, as
# reported | 8,708", Federal Realty "Operating income | 180,654") now never confirm an adjusted level either.
RVTY_TEXT = ("Second quarter 2026 adjusted operating income was $113 million, as compared to $89 million in the same "
             "period a year ago.")
STZ_TEXT = "Tax rate: reported ~12%; comparable ~20%"
STZ_PERIOD = "second quarter of fiscal 2027, the quarter ended August 31, 2026"


def rvty(value, span_end=None, period_text="Second quarter 2026"):
    span = RVTY_TEXT[20:span_end or RVTY_TEXT.index(", as")]
    return claim(span, f"${value} million", value, metric="operating_income", metric_text="adjusted operating income",
                 unit="USD", scale="millions", entity="Revvity", period_text=period_text
                 ).model_copy(update={"span_start": 20, "span_end": 20 + len(span)})


@pytest.mark.parametrize("c", [
    rvty(113), rvty(150),
    rvty(89, span_end=RVTY_TEXT.index("."), period_text="in the same period a year ago"),
])
def test_an_adjusted_figure_is_never_rejected_by_a_row_that_names_no_adjusted_measure(snapshot, c):
    v = verify(c, snapshot, draft=RVTY_TEXT, context="Revvity (RVTY), second quarter of fiscal 2026, the quarter ended "
                                                     "July 5, 2026")
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "names no adjusted measure" in v.explanation


def test_a_row_that_says_non_gaap_still_judges_an_adjusted_figure(snapshot):
    # Flex Q1 FY2027: "Non-GAAP operating income and margin % | 534"
    def flex(value):
        return claim(f"Adjusted Operating Income: ${value} million", f"${value} million", value,
                     metric="operating_income", metric_text="Adjusted Operating Income", unit="USD", scale="millions",
                     entity="Flex Ltd.", period_text="first quarter of fiscal 2027, the quarter ended June 26, 2026")
    assert verify(flex(534), snapshot).status == "verified"
    v = verify(flex(600), snapshot)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(534)), v.explanation


@pytest.mark.parametrize("span,value_text,value,entity,period_text", [
    ("Walmart's adjusted operating income was $8.7 billion.", "$8.7 billion", 8.7, "Walmart", "Q4 FY2026"),
    ("Federal Realty's adjusted operating income was $180.7 million.", "$180.7 million", 180.7, "Federal Realty",
     "Q4 2025"),
])
def test_an_adjusted_level_is_never_confirmed_by_a_gaap_row_filed_as_non_gaap(snapshot, span, value_text, value,
                                                                                entity, period_text):
    c = claim(span, value_text, value, metric="operating_income", metric_text="adjusted operating income", unit="USD",
              scale=value_text.split()[-1] + "s", entity=entity, period_text=period_text)
    v = verify(c, snapshot)
    assert unread(v) and "matches its GAAP operating income" in v.explanation, v.explanation


def stz(value_text, value, at):
    span = STZ_TEXT[:STZ_TEXT.index(";")] if at == 0 else STZ_TEXT[STZ_TEXT.index("comparable"):]
    return claim(span, value_text, value, metric="tax_rate", metric_text="Tax rate: reported" if at == 0 else
                 "comparable Tax rate", unit="%", entity="Constellation Brands (STZ)", period_text=STZ_PERIOD
                 ).model_copy(update={"span_start": STZ_TEXT.index(span), "span_end": STZ_TEXT.index(span) + len(span)})


def test_a_reported_figure_whose_filed_rate_fits_the_lines_comparable_one_is_unread(snapshot):
    v = verify(stz("12%", 12.0, 0), snapshot, draft=STZ_TEXT)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "20.2%" in v.explanation and "“comparable” 20%" in v.explanation and "not 12%" not in v.explanation


@pytest.mark.parametrize("text", [
    "Tax rate: reported 25.0%; comparable 20.4%",   # the filed 20.2% fits neither figure
    "Tax rate: reported ~12%; prior year ~20%",     # the other figure names no basis
])
def test_a_reported_figure_is_still_wrong_when_no_other_basis_figure_explains_it(snapshot, text):
    value_text = text.split(": reported ")[1].split(";")[0].lstrip("~")
    span = text[:text.index(";")]
    c = claim(span, value_text, float(value_text.rstrip("%")), metric="tax_rate", metric_text="Tax rate: reported",
              unit="%", entity="Constellation Brands (STZ)", period_text=STZ_PERIOD
              ).model_copy(update={"span_end": len(span)})
    v = verify(c, snapshot, draft=text)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "value_mismatch", pytest.approx(20.2)), v.explanation


def el_margin(span):
    return claim(span, "75.0%", 75.0, metric="gross_margin", metric_text="Adjusted gross margin", unit="%",
                 entity="Estée Lauder Companies (The) (EL)", period_text="third quarter of fiscal 2026")


def test_a_line_that_names_no_period_never_gets_a_period_error(snapshot):
    text = "Adjusted gross margin of approximately 75.0%"
    v = verify(el_margin(text), snapshot, draft=text)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "names no period" in v.explanation, v.explanation
    # Its sentence's own period words make the same figure a period error, as before; so do period words outside the
    # span, which only the draft shows.
    for text, span in [("In the third quarter of fiscal 2026, adjusted gross margin was approximately 75.0%", None),
                       ("In the third quarter of fiscal 2026, adjusted gross margin was approximately 75.0%",
                        "adjusted gross margin was approximately 75.0%")]:
        c = el_margin(span or text)
        c = c.model_copy(update={"span_start": text.index(c.span_text), "span_end": len(text)})
        v = verify(c, snapshot, draft=text)
        assert (v.status, v.reason) == ("wrong", "period_mismatch"), v.explanation


# --- Round 8. Net sales are never checked against a larger total revenue --------------------------------------------
# A Costco summary the owner pasted: "Costco's quarterly net sales increased 11.2% year over year to $93.9 billion,
# compared with $84.4 billion in the same period last year." Costco's total revenue (95,723 for Q4 FY2026, 86,156 a
# year earlier) is net sales plus membership fees, and NoCap holds only that total. Costco, Walmart, Dollar Tree,
# Incyte, Snap-on and Viatris print net sales below a larger total revenue (verify.NET_SALES_BELOW_TOTAL_REVENUE).
COST_TEXT = ("Costco's quarterly net sales increased 11.2% year over year to $93.9 billion, compared with $84.4 billion "
             "in the same period last year.")
COST_CONTEXT = "Costco Wholesale, fourth quarter of fiscal year 2026"


def cost_sales(value_text, value, *, metric="revenue", metric_text="net sales", span=None, comparison="none"):
    span = span or COST_TEXT
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit="%" if "%" in value_text else
                 "USD", scale="units" if "%" in value_text else "billions", entity="Costco", period_text="",
                 comparison=comparison)


@pytest.mark.parametrize("c", [
    cost_sales("$93.9 billion", 93.9, span=COST_TEXT[:COST_TEXT.index(",")]),
    cost_sales("$84.4 billion", 84.4, span=COST_TEXT[COST_TEXT.index("compared"):]),
    cost_sales("11.2%", 11.2, metric="revenue_growth", span=COST_TEXT[:COST_TEXT.index(" to ")], comparison="YoY"),
    cost_sales("$90.0 billion", 90.0, span="Costco's net sales were $90.0 billion."),  # wrong as well: still "?"
    cost_sales("$93.9 billion", 93.9, metric_text="sales", span="Costco's sales were $93.9 billion."),
])
def test_net_sales_are_never_checked_against_total_revenue(snapshot, c):
    # The filed-facts check (_verify_filed) is what this pins; the company's 8-K release text can still confirm the
    # figure afterwards (receipts.release_text, tests/test_release_text.py).
    v = verifier._verify_filed(c, snapshot, context=COST_CONTEXT)
    assert unread(v) and "no net sales figure" in v.explanation, v.explanation
    assert "95.723" not in v.explanation and "86.156" not in v.explanation


@pytest.mark.parametrize("c,filed", [
    (cost_sales("$95.7 billion", 95.7, metric_text="Total revenue", span="Costco's total revenue was $95.7 billion."),
     "$95.723 billion"),
    (claim("Walmart's total revenues were $187.9 billion.", "$187.9 billion", 187.9, metric="revenue",
           metric_text="total revenues", unit="USD", scale="billions", entity="Walmart", period_text="Q2 FY2027"),
     "$187.937 billion"),
])
def test_total_revenue_of_those_companies_is_still_checked(snapshot, c, filed):
    v = verify(c, snapshot, context=COST_CONTEXT if c.entity == "Costco" else None)
    assert (v.status, v.reason) == ("verified", "match") and filed in v.explanation, v.explanation


def test_walmart_net_sales_are_unread(snapshot):
    c = claim("Walmart's net sales were $187.9 billion.", "$187.9 billion", 187.9, metric="revenue",
              metric_text="net sales", unit="USD", scale="billions", entity="Walmart", period_text="Q2 FY2027")
    assert unread(verify(c, snapshot))


@pytest.mark.parametrize("span,value_text,value,entity,period_text,filed", [
    ("Apple's net sales were $109.4 billion.", "$109.4 billion", 109.4, "Apple", "Q3 FY2026", "$109.417 billion"),
    ("Amazon's net sales were $181.5 billion.", "$181.5 billion", 181.5, "Amazon", "Q1 2026", "$181.519 billion"),
    ("Home Depot's net sales were $47.9 billion.", "$47.9 billion", 47.9, "Home Depot", "Q2 FY2026",
     "$47.861 billion"),
])
def test_net_sales_of_a_company_whose_top_line_is_net_sales_are_checked(snapshot, span, value_text, value, entity,
                                                                       period_text, filed):
    c = claim(span, value_text, value, metric="revenue", metric_text="net sales", unit="USD", scale="billions",
              entity=entity, period_text=period_text)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match") and filed in v.explanation, v.explanation
    wrong = c.model_copy(update={"value": value + 5, "value_text": f"${value + 5:.1f} billion",
                                 "span_text": span.replace(value_text, f"${value + 5:.1f} billion")})
    assert verify(wrong, snapshot).status == "wrong"


# --- Round 8. A region, segment or same-store figure is never checked against the company-wide one ------------------
# Allegion Q3 2026: "Adjusted operating margin in the region decreased 40 basis points to 13.9%": the 13.9% lost the
# clause's subject because the 40 basis points stand between them, and was compared with the company's 24.71%. MGM Q2
# 2026: "Same-store revenue (adjusted for dispositions) of $904 million ... compared to $879 million ..., an increase of
# 3%", quoted as "an increase of 3%", was compared with total revenue growth (1.0%). Axon Q2 2026's "adjusted gross
# margin increased to 53.4% from 51.1% a year ago" names no segment, and no safe rule tells its figures from Axon's own:
# it is left as it was (see the round-8 report).
MGM_TEXT = ("Same-store revenue (adjusted for dispositions) of $904 million in the current quarter compared to $879 "
            "million in the prior year quarter, an increase of 3%")


def alle_margin(span, value):
    return claim(span, f"{value}%", value, metric="other", metric_text="Adjusted operating margin", unit="%",
                 entity="Allegion", period_text="third quarter of fiscal 2026, the quarter ended September 30, 2026",
                 basis="non-GAAP")


def test_a_regional_margin_after_its_change_is_unread(snapshot):
    # The filed-facts check (_verify_filed) is what this pins; the company's 8-K release text can still confirm the
    # figure afterwards (receipts.release_text, tests/test_release_text.py).
    v = verifier._verify_filed(alle_margin("Adjusted operating margin in the region decreased 40 basis points to 13.9%",
                                           13.9), snapshot)
    assert unread(v) and "“region”" in v.explanation, v.explanation


@pytest.mark.parametrize("c,filed", [
    (alle_margin("Adjusted operating margin decreased 40 basis points to 24.7%", 24.7), "24.71%"),
    (claim("AMD's revenue rose 24%, or 23% FX-neutral, to $10.25 billion", "$10.25 billion", 10.25, metric="revenue",
           metric_text="revenue", unit="USD", scale="billions", entity="AMD", period_text="Q1 2026"), "$10.253 billion"),
])
def test_a_figure_after_its_change_keeps_its_plain_subject(snapshot, c, filed):
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match") and filed in v.explanation, v.explanation


def test_a_change_whose_quote_names_no_base_takes_its_sentences_subject(snapshot):
    c = claim("an increase of 3%", "3%", 3.0, metric="revenue_growth", metric_text="increase", unit="%",
              entity="MGM Resorts", period_text="the current quarter", comparison="YoY")
    c = c.model_copy(update={"span_start": MGM_TEXT.index("an increase"), "span_end": len(MGM_TEXT)})
    v = verify(c, snapshot, draft=MGM_TEXT, context="MGM Resorts (MGM), second quarter of fiscal 2026, the quarter "
                                                     "ended June 30, 2026")
    assert unread(v) and "in its sentence" in v.explanation, v.explanation
    # A plain subject keeps the check: Target Q2 FY2026 revenue $26,539M, Q2 FY2025 $25,211M (+5.27%).
    text = "Target revenue was $26.5 billion, an increase of 5.3%."
    c = claim("an increase of 5.3%", "5.3%", 5.3, metric="revenue_growth", metric_text="increase", unit="%",
              entity="Target", period_text="Q2 FY2026", comparison="YoY")
    c = c.model_copy(update={"span_start": text.index("an increase"), "span_end": len(text) - 1})
    v = verify(c, snapshot, draft=text)
    assert (v.status, v.reason) == ("verified", "match") and "5.27%" in v.explanation, v.explanation


def test_a_fronted_clause_before_a_quote_that_names_its_base_changes_nothing(snapshot):
    text = "Compared with the prior year, total reported sales increased 7% to $947.1 million."
    span = text[text.index("total"):-1]
    c = claim(span, "$947.1 million", 947.1, metric="revenue", metric_text="total reported sales", unit="USD",
              scale="millions", entity="Mettler-Toledo", period_text="Q1 2026")
    c = c.model_copy(update={"span_start": text.index("total"), "span_end": len(text) - 1})
    v = verify(c, snapshot, draft=text)
    assert (v.status, v.reason) == ("verified", "match") and "$947.127 million" in v.explanation, v.explanation


def test_a_reporting_verb_in_the_sentences_subject_is_framing(snapshot):
    # Dover Q3 2026 (full set, verified before round 8): revenue $2.234 billion, up 7.5% year over year.
    text = ("For the quarter ended September 30, 2026, Dover generated revenue of $2.2 billion, an increase of 8% (+7% "
            "organic).")
    c = claim("an increase of 8%", "8%", 8.0, metric="revenue_growth", metric_text="increase", unit="%", entity="Dover",
              period_text="quarter ended September 30, 2026")
    c = c.model_copy(update={"span_start": text.index("an increase"), "span_end": text.index(" (+7%")})
    v = verify(c, snapshot, draft=text)
    assert (v.status, v.reason) == ("verified", "match") and "7.5%" in v.explanation, v.explanation


def test_a_quote_that_names_its_segment_keeps_its_segment_check(snapshot):
    # The dev set's AMD Q1 2026 line (Data Center revenue grew 57.2%): the quote names the segment, not "revenue", and
    # the sentence's earlier subject is another figure's.
    text = "AMD's revenue was $10.25 billion, and Data Center rose 57% year over year."
    span = "Data Center rose 57% year over year"
    c = claim(span, "57%", 57.0, metric="revenue_growth", metric_text="rose 57% year over year", unit="%", entity="AMD",
              period_text="Q1 2026", comparison="YoY")
    c = c.model_copy(update={"segment": "Data Center", "span_start": text.index(span),
                             "span_end": text.index(span) + len(span)})
    v = verify(c, snapshot, draft=text)
    assert (v.status, v.reason) == ("verified", "match") and "Data Center revenue grew 57.2%" in v.explanation, \
        v.explanation


# --- Round 8. An operating margin computed from rounded figures carries their rounding -------------------------------
# Illumina Q2 2026: "GAAP operating margin of 21.1% and non-GAAP operating margin of 22.5%". NoCap computes the non-GAAP
# margin from the release's non-GAAP operating profit (260, in millions) over revenue (1,159): 22.43%, or 22.38-22.49%
# with the rounding of both inputs, which the release's own 22.5% (computed before rounding) fits.
ILMN_TEXT = "GAAP operating margin of 21.1% and non-GAAP operating margin of 22.5%"


def ilmn_margin(value, words="non-GAAP operating margin", basis="non-GAAP"):
    span = f"{words} of {value}%"
    return claim(span, f"{value}%", value, metric="other", metric_text=words, unit="%", entity="Illumina, Inc.",
                 period_text="second quarter of fiscal 2026", basis=basis)


def test_an_operating_margin_from_rounded_figures_carries_their_rounding(snapshot):
    v = verify(ilmn_margin(22.5), snapshot)
    assert (v.status, v.reason) == ("verified", "match") and "22.43%" in v.explanation, v.explanation
    v = verify(ilmn_margin(21.1, "GAAP operating margin", "GAAP"), snapshot)
    assert (v.status, v.reason) == ("verified", "match") and "21.14%" in v.explanation, v.explanation


@pytest.mark.parametrize("value", [22.6, 23.0, 22.3])
def test_an_operating_margin_beyond_that_rounding_is_still_wrong(snapshot, value):
    v = verify(ilmn_margin(value), snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert v.expected_value == pytest.approx(22.43, abs=0.01)


# --- Round 8. A sentence whose exchange listing names another company can't say whose figure it gives --------------
# The full set's planted entity copy: "The Goldman Sachs Group, Inc. (NYSE: Vertex Pharmaceuticals) today reported net
# revenues of $17.23 billion", with the story's company set to Vertex, was verified against Goldman Sachs ($17.227B).
GS_TEXT = ("NEW YORK, April 13, 2026 – The Goldman Sachs Group, Inc. (NYSE: {}) today reported net revenues of $17.23 "
           "billion and")


def gs_revenue(listing):
    text = GS_TEXT.format(listing)
    span = "reported net revenues of $17.23 billion"
    c = claim(span, "$17.23 billion", 17.23, metric="revenue", metric_text="net revenues", unit="USD", scale="billions",
              entity="The Goldman Sachs Group, Inc.", period_text="first quarter of fiscal 2026")
    return text, c.model_copy(update={"span_start": text.index(span), "span_end": text.index(span) + len(span)})


def test_a_listing_that_names_another_company_leaves_the_figure_unread(snapshot):
    text, c = gs_revenue("Vertex Pharmaceuticals")
    v = verify(c, snapshot, draft=text, context="Vertex Pharmaceuticals (VRTX), first quarter of fiscal 2026")
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert "“(NYSE: Vertex Pharmaceuticals)” names another company" in v.explanation
    text, c = gs_revenue("VRTX")  # a ticker names it as well
    assert verify(c, snapshot, draft=text).status == "unverifiable"


@pytest.mark.parametrize("listing", ["GS", "GS) and Vertex Pharmaceuticals (Nasdaq: VRTX"])
def test_a_listing_of_the_claims_own_company_keeps_the_check(snapshot, listing):
    text, c = gs_revenue(listing)
    v = verify(c, snapshot, draft=text)
    assert (v.status, v.reason) == ("verified", "match") and "$17.227 billion" in v.explanation, v.explanation


def test_the_listing_rule_reads_tickers_that_are_words():
    from receipts.store import Store
    store = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    try:
        text = "KeyCorp (NYSE: KEY) today reported net income of $500 million."
        c = claim("net income of $500 million", "$500 million", 500.0, metric="net_income", metric_text="net income",
                  unit="USD", scale="millions", entity="KeyCorp", period_text="Q1 2026")
        c = c.model_copy(update={"span_start": text.index("net income"), "span_end": len(text) - 1})
        assert verifier.listing_conflict(c, store, None, verifier.draft_line(c, text)) is None
        other = c.model_copy(update={"entity": "Truist Financial"})
        assert verifier.listing_conflict(other, store, None, verifier.draft_line(other, text)) == "(NYSE: KEY)"
    finally:
        store.close()


# --- Round 9. Words that never change what a figure measures are not qualifiers ------------------------------------
# ChatGPT-style drafts that trial users paste returned "?" ("The wording “approximately” qualifies this figure"):
# McDonald's Q2 2026 "quarterly revenue increased approximately **4% year over year to $7.10 billion**" (filed 3.74%,
# $7.099B) and "Operating income also grew 3% to $3.34 billion" (3.3%); Costco's "Its gross margin declined slightly to
# 11.02%". Hedges away from their number, discourse and degree adverbs, evaluative adjectives and linking verbs are
# ignored (verify._NEUTRAL_WORDS); bounds and comparisons ("nearly", "more than", "below") and "representing" stay
# qualifiers, and markdown emphasis is no wording.
MCD_SUMMARY = (ROOT / "tests/fixtures/mcd_q2_2026_summary.txt").read_text()


def mcd(span, value_text, value, metric="revenue_growth", metric_text="revenue", unit="%", scale="units",
        comparison="YoY"):
    return claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale,
                 entity="McDonald's", period_text="Q2 2026", comparison=comparison)


def test_the_mcdonalds_summary_verifies_with_its_markdown_and_hedges(snapshot):
    passages = extract.split_passages(MCD_SUMMARY)
    start, passage = passages[3]
    proposals = [
        extract.ModelClaim(metric=m, metric_text=mt, value_text=vt, value=v, unit=u, scale=sc, period_text="",
                           period_type="quarter", comparison=cmp, bases=[], forward_looking=False, entity="McDonald's",
                           segment=None, span_text=span)
        for m, mt, vt, v, u, sc, cmp, span in [
            ("revenue_growth", "quarterly revenue increased approximately", "4%", 4.0, "%", "units", "YoY",
             "McDonald's quarterly revenue increased approximately **4% year over year"),
            ("revenue", "revenue", "$7.10 billion", 7.10, "USD", "billions", "none",
             "McDonald's quarterly revenue increased approximately **4% year over year to $7.10 billion"),
            ("operating_income", "Operating income also grew", "3%", 3.0, "%", "units", "none",
             "Operating income also grew 3%"),
        ]]
    claims, _ = extract.build_claims([(start, passage, proposals)], [])
    context = extract.infer_context(MCD_SUMMARY)
    verdicts = [verify(c, snapshot, context=context, draft=MCD_SUMMARY) for c in claims]
    assert [v.status for v in verdicts] == ["verified"] * 3, [v.explanation for v in verdicts]
    assert "revenue grew 3.7% year over year" in verdicts[0].explanation
    assert "$7.099 billion" in verdicts[1].explanation
    assert "operating income grew 3.3% year over year" in verdicts[2].explanation


@pytest.mark.parametrize("c,status", [
    # a hedge away from its number, and one before it read as a hedge (no wider than the written precision for ✓)
    (mcd("Revenue of approximately $7.1 billion grew 4% year over year", "4%", 4.0), "verified"),
    (mcd("McDonald's quarterly revenue increased approximately **4% year over year", "4%", 4.0), "verified"),
    (mcd("McDonald's quarterly revenue increased approximately 3.6% year over year", "3.6%", 3.6), "wrong"),
    (mcd("McDonald's quarterly revenue increased approximately 10% year over year", "10%", 10.0), "wrong"),
    # a discourse adverb
    (mcd("Operating income also grew 3%", "3%", 3.0, metric="operating_income", metric_text="Operating income also grew",
         comparison="none"), "verified"),
    (mcd("Operating income also grew 10%", "10%", 10.0, metric="operating_income", metric_text="Operating income also "
         "grew", comparison="none"), "wrong"),
])
def test_hedges_and_discourse_adverbs_are_not_qualifiers(snapshot, c, status):
    assert verify(c, snapshot).status == status


@pytest.mark.parametrize("span,value,status", [
    ("AMD's operating margin rose sharply to 17.3%", 17.3, "verified"),   # 17.25%
    ("AMD's operating margin rose sharply to 20.0%", 20.0, "wrong"),
    ("AMD's operating margin rose slightly to 17.3%", 17.3, "verified"),
])
def test_degree_adverbs_are_not_qualifiers(snapshot, span, value, status):
    c = claim(span, f"{value}%", value, metric="other", metric_text="operating margin", unit="%", entity="AMD",
              period_text="Q2 2026")
    assert verify(c, snapshot).status == status


def test_a_costco_margin_that_declined_slightly_is_read_as_a_margin(snapshot):
    # "slightly" no longer stops the reading; Costco's gross margin is on net sales, which NoCap doesn't hold.
    text = "Its gross margin declined slightly to 11.02%"
    c = claim(text, "11.02%", 11.02, metric="gross_margin", metric_text="gross margin", unit="%", entity="Costco",
              period_text="")
    v = verify(c, snapshot, context="Costco Wholesale, fourth quarter of fiscal year 2026", draft=text)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous") and "sales base" in v.explanation, v.explanation
    assert "slightly" not in v.explanation


@pytest.mark.parametrize("span,value_text,value,metric,metric_text,entity,period_text,unit,scale,status", [
    ("Sales were an all-time quarterly record of $818 million", "$818 million", 818.0, "revenue", "Sales", "Nordson",
     "Q3 FY2026", "USD", "millions", "verified"),
    ("Sales were an all-time quarterly record of $850 million", "$850 million", 850.0, "revenue", "Sales", "Nordson",
     "Q3 FY2026", "USD", "millions", "wrong"),
    ("Diluted earnings per share remained flat at $0.32", "$0.32", 0.32, "eps_diluted", "Diluted earnings per share",
     "Chipotle Mexican Grill", "Q2 2026", "USD/share", "units", "verified"),
    ("Diluted earnings per share remained flat at $0.40", "$0.40", 0.40, "eps_diluted", "Diluted earnings per share",
     "Chipotle Mexican Grill", "Q2 2026", "USD/share", "units", "wrong"),
])
def test_evaluative_adjectives_and_linking_verbs_are_not_qualifiers(snapshot, span, value_text, value, metric,
                                                                   metric_text, entity, period_text, unit, scale,
                                                                   status):
    c = claim(span, value_text, value, metric=metric, metric_text=metric_text, unit=unit, scale=scale, entity=entity,
              period_text=period_text)
    assert verify(c, snapshot).status == status


@pytest.mark.parametrize("span,words", [
    ("Revenue of nearly $7.1 billion grew 4% year over year", "nearly"),
    ("Revenue of more than $7 billion grew 4% year over year", "more than"),
    ("Revenue below $8 billion grew 4% year over year", "below"),
    ("Revenue of $7.10 billion, representing 4% year-over-year growth", "representing"),
])
def test_bounds_comparisons_and_shares_stay_qualifiers(snapshot, span, words):
    v = verify(mcd(span, "4%", 4.0), snapshot)
    assert unread(v) and f"“{words}”" in v.explanation, v.explanation


def test_markdown_emphasis_is_no_wording():
    assert verifier._without_markdown("increased **4% year over year** to __$7.10 billion__") == \
        "increased   4% year over year   to   $7.10 billion  "
    assert verifier._without_markdown("free_cash_flow") == "free_cash_flow"
