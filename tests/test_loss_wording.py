"""Loss-phrased figures are the negative filed figures they name (Boeing and Centene, committed SEC snapshot).

"Diluted loss per share improved from $7.14 to $0.26", "a net loss attributable to shareholders of $71 million" and
"a substantial improvement from the $5.34 billion loss recorded in Q3 2025" were "?" (no_evidence) although the filed
figures match: Boeing's diluted EPS -$0.26 (Q3 2026) and -$7.14 (Q3 2025) and net loss $5,337M (Q3 2025); Centene's
diluted EPS -$0.14 and -$13.50, net loss $71M and $6,631M, operating cash flow -$3,669M. Each now reads as the
"net loss of $116 million" path does. A loss's sign never flips: a loss where the filing shows income is "✗", and so
is income where it shows a loss. A bare "loss" is net income only as the company's own result; a division's, segment's,
operating, impairment or discontinued-operations loss, or a loss on a sale, stays "?". No model, network or SEC call.
"""
from pathlib import Path

import pytest

from receipts import extract
from receipts.store import Store
from receipts.types import Claim
from receipts.verify import claimed_value, interpret, named_in_draft, span_reading, verify

ROOT = Path(__file__).resolve().parents[1]
BA_10Q = "0001628280-26-067508"   # Boeing Q3 2026 10-Q: net loss $116M, diluted EPS -$0.26, operating income $354M
CNC_10Q = "0001071739-26-000192"  # Centene Q3 2026 10-Q: net loss $71M, diluted EPS -$0.14, operating cash flow -$3,669M
# Centene Q2 2026 (a profit): net income $1,091M, diluted EPS $2.19. Boeing Q3 2025: net loss $5,337M, operating loss
# $4,781M, diluted EPS -$7.14. Boeing Q3 2026 net loss available to common shareholders: $203M (preferred dividends).

BOEING = ("Boeing still reported a net loss of $116 million, a substantial improvement from the $5.34 billion loss "
          "recorded in Q3 2025. Diluted loss per share improved from $7.14 to $0.26.")
CENTENE = ("Centene reported a net loss attributable to shareholders of $71 million in Q3 2026, compared with a $6.63 "
           "billion loss in Q3 2025. GAAP diluted loss per share improved from $13.50 to $0.14. Operating cash flow was "
           "negative $3.67 billion.")


@pytest.fixture(scope="module")
def snapshot():
    store = Store(ROOT / "data/snapshot/facts.sqlite", ROOT / "data/snapshot/pr_facts")
    yield store
    store.close()


def claim(span, value_text, value, *, metric="net_income", metric_text="loss", entity="Boeing", period="Q3 2026",
          text=None, basis=None):
    """A claim at its place in text (the draft; the span itself when none is given)."""
    text = text or span
    per_share = metric.startswith("eps")
    start = text.index(span)
    return Claim(id="c1", metric=metric, metric_text=metric_text, value_text=value_text, value=value,
                 unit="USD/share" if per_share else "USD",
                 scale="units" if per_share else value_text.split()[-1] + "s", period_text=period,
                 period_type="quarter", comparison="none", basis=basis, forward_looking=False, entity=entity,
                 segment=None, span_text=span, span_start=start, span_end=start + len(span))


def check(store, c, text=None):
    text = text or c.span_text
    return verify(c, store, draft=text, named=named_in_draft(text, store))


BA_EPS = "Diluted loss per share improved from $7.14 to $0.26."
CNC_EPS = "GAAP diluted loss per share improved from $13.50 to $0.14."
OWNER = [  # (draft, span, value_text, value, metric, metric_text, entity, period, filed value)
    (BOEING, BA_EPS, "$0.26", 0.26, "eps_diluted", "Diluted loss per share", "Boeing", "Q3 2026", -0.26),
    (BOEING, BA_EPS, "$7.14", 7.14, "eps_diluted", "Diluted loss per share", "Boeing", "Q3 2025", -7.14),
    (BOEING, "the $5.34 billion loss recorded in Q3 2025", "$5.34 billion", 5.34, "net_income", "loss", "Boeing",
     "Q3 2025", -5_337e6),
    (CENTENE, CNC_EPS, "$0.14", 0.14, "eps_diluted", "GAAP diluted loss per share", "Centene", "Q3 2026", -0.14),
    (CENTENE, CNC_EPS, "$13.50", 13.50, "eps_diluted", "GAAP diluted loss per share", "Centene", "Q3 2025", -13.50),
    (CENTENE, "a net loss attributable to shareholders of $71 million", "$71 million", 71, "net_income",
     "net loss attributable to shareholders", "Centene", "Q3 2026", -71e6),
    (CENTENE, "a $6.63 billion loss in Q3 2025", "$6.63 billion", 6.63, "net_income", "loss", "Centene", "Q3 2025",
     -6_631e6),
    (CENTENE, "Operating cash flow was negative $3.67 billion", "$3.67 billion", 3.67, "operating_cash_flow",
     "Operating cash flow", "Centene", "Q3 2026", -3_669e6),
]


@pytest.mark.parametrize("text,span,value_text,value,metric,metric_text,entity,period,filed", OWNER)
def test_owner_examples_verify_against_the_negative_filed_figure(snapshot, text, span, value_text, value, metric,
                                                                 metric_text, entity, period, filed):
    c = claim(span, value_text, value, metric=metric, metric_text=metric_text, entity=entity, period=period, text=text,
              basis="GAAP" if "GAAP" in metric_text else None)
    v = check(snapshot, c, text)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.value == pytest.approx(filed) for f in v.evidence), v.evidence
    assert claimed_value(c, metric=metric) < 0


@pytest.mark.parametrize("text,span,value_text,value,metric,metric_text,entity,period,filed", OWNER)
def test_a_wrong_amount_in_the_same_wording_is_wrong(snapshot, text, span, value_text, value, metric, metric_text,
                                                     entity, period, filed):
    wrong_value = round(value * 1.5, 2)
    wrong_text = value_text.replace(f"{value:g}" if value != 13.5 else "13.50", f"{wrong_value:.2f}")
    assert wrong_text != value_text
    draft = text.replace(value_text, wrong_text)
    c = claim(span.replace(value_text, wrong_text), wrong_text, wrong_value, metric=metric, metric_text=metric_text,
              entity=entity, period=period, text=draft, basis="GAAP" if "GAAP" in metric_text else None)
    v = check(snapshot, c, draft)
    assert v.status == "wrong", v.explanation


def test_without_its_sentence_a_bare_loss_names_no_subject(snapshot):
    # The quote alone doesn't show whose loss "a $6.63 billion loss" is; the draft's sentence does (above).
    c = claim("a $6.63 billion loss in Q3 2025", "$6.63 billion", 6.63, entity="Centene", period="Q3 2025")
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence"), v.explanation


# --- The loss forms of EPS ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("words,metric", [
    ("loss per share", "eps_diluted"), ("diluted loss per share", "eps_diluted"),
    ("net loss per diluted share", "eps_diluted"), ("loss per diluted share", "eps_diluted"),
    ("diluted net loss per share", "eps_diluted"), ("net loss per share", "eps_diluted"),
    ("net loss attributable to common shareholders per diluted share", "eps_diluted"),
    ("basic loss per share", "eps_basic"), ("net loss per basic share", "eps_basic"),
    ("loss per basic share", "eps_basic"), ("basic net loss per share", "eps_basic"),
])
def test_loss_per_share_wording_is_negative_eps_both_ways(snapshot, words, metric):
    span = f"Boeing's {words} was $0.26 in the third quarter."
    c = claim(span, "$0.26", 0.26, metric=metric, metric_text=words)
    assert interpret(c).metric == metric and claimed_value(c) == -0.26
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.metric == metric and f.value == -0.26 and f.accession == BA_10Q for f in v.evidence)
    wrong = claim(span.replace("0.26", "0.62"), "$0.62", 0.62, metric=metric, metric_text=words)
    assert verify(wrong, snapshot).status == "wrong"


@pytest.mark.parametrize("span,metric_text", [
    ("Boeing reported a loss of $0.26 per diluted share.", "per diluted share"),
    ("Boeing reported a net loss of $0.26 per diluted share.", "per diluted share"),
    ("Boeing lost $0.26 per diluted share.", "per diluted share"),
])
def test_a_loss_written_around_the_per_share_number_signs_it(snapshot, span, metric_text):
    # These were "✗": the filed -$0.26 against a claimed +$0.26.
    v = verify(claim(span, "$0.26", 0.26, metric="eps_diluted", metric_text=metric_text), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


def test_span_reading_signs_a_loss_per_share_as_the_check_does():
    c = claim("Diluted loss per share was $0.26.", "$0.26", 0.26, metric="eps_diluted",
              metric_text="Diluted loss per share")
    assert span_reading(c)[2].negative is True


def test_extraction_keeps_a_loss_per_share_quote_that_reads_the_same(snapshot):
    proposal = extract.ModelClaim(metric="eps_diluted", metric_text="Diluted loss per share", value_text="$0.26",
                                  value=0.26, unit="USD/share", scale="units", period_text="Q3 2026",
                                  period_type="quarter", comparison="none", bases=[], forward_looking=False,
                                  entity="Boeing", segment=None, span_text="to $0.26")
    (c,) = extract.build_claims([(0, BA_EPS, [proposal])], [])[0]
    assert verify(c, snapshot, draft=BA_EPS).status == "verified"


@pytest.mark.parametrize("span,value_text,value,metric_text,period", [
    # the quote names EPS plainly while the description calls it a loss: the draft's sign is unclear
    ("Diluted EPS was $2.19.", "$2.19", 2.19, "loss per share", "Q2 2026"),
    # the per-share amount of another loss is no EPS
    ("Centene recorded an impairment loss of $0.14 per diluted share.", "$0.14", 0.14, "per diluted share", "Q3 2026"),
    # the change of a loss per share is never compared with a level, and its percent change has a negative base
    ("GAAP diluted loss per share improved by $13.36.", "$13.36", 13.36, "GAAP diluted loss per share", "Q3 2026"),
])
def test_unclear_or_other_per_share_losses_stay_unread(snapshot, span, value_text, value, metric_text, period):
    c = claim(span, value_text, value, metric="eps_diluted", metric_text=metric_text, entity="Centene", period=period)
    v = verify(c, snapshot)
    assert v.status == "unverifiable" and v.expected_value is None, v.explanation


def test_a_percent_change_of_a_loss_per_share_is_not_checked(snapshot):
    c = claim("Diluted loss per share improved 96%.", "96%", 96, metric="eps_diluted",
              metric_text="Diluted loss per share").model_copy(update={"unit": "%", "comparison": "YoY"})
    assert verify(c, snapshot).status == "unverifiable"


# --- Net loss attributable to shareholders, stockholders, common stockholders ---------------------------------------

@pytest.mark.parametrize("words", ["net loss attributable to shareholders", "net loss attributable to stockholders",
                                   "net loss attributable to Boeing shareholders",
                                   "net loss attributable to the Company's shareholders"])
def test_net_loss_attributable_to_shareholders_is_the_parent_net_loss(snapshot, words):
    c = claim(f"Boeing's {words} was $116 million.", "$116 million", 116, metric_text=words)
    v = verify(c, snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.concept == "us-gaap:NetIncomeLoss" and f.value == -116e6 and f.accession == BA_10Q
               for f in v.evidence)
    # Boeing's net loss available to common shareholders, after preferred dividends: never a match nor a "✗" here
    other = claim(f"Boeing's {words} was $203 million.", "$203 million", 203, metric_text=words)
    v = verify(other, snapshot)
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation


def test_net_loss_attributable_to_common_shareholders_keeps_the_to_common_rules(snapshot):
    words = "net loss attributable to common shareholders"
    v = verify(claim(f"Boeing's {words} was $203 million.", "$203 million", 203, metric_text=words), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    assert any(f.metric == "net_income_to_common" and f.value == -203e6 for f in v.evidence)
    # Centene stores no line after preferred dividends: "?", as for net income
    v = verify(claim(f"Centene's {words} was $71 million.", "$71 million", 71, metric_text=words, entity="Centene"),
               snapshot)
    assert v.status == "unverifiable" and v.expected_value is None, v.explanation


@pytest.mark.parametrize("words", ["net loss attributable to noncontrolling interests",
                                   "net loss attributable to preferred shareholders"])
def test_other_attributions_of_a_loss_stay_unread(snapshot, words):
    v = verify(claim(f"Centene's {words} was $71 million.", "$71 million", 71, metric_text=words, entity="Centene"),
               snapshot)
    assert v.status == "unverifiable" and v.expected_value is None, v.explanation


# --- A bare "loss" ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,span,value_text,value,entity,period", [
    ("In the third quarter, Boeing posted a loss of $116 million.", "Boeing posted a loss of $116 million",
     "$116 million", 116, "Boeing", "Q3 2026"),
    ("Boeing narrowed its loss to $116 million from $5.34 billion.", "its loss to $116 million", "$116 million", 116,
     "Boeing", "Q3 2026"),
    ("Centene swung from a $6.63 billion loss in Q3 2025 to a net loss of $71 million.",
     "a $6.63 billion loss in Q3 2025", "$6.63 billion", 6.63, "Centene", "Q3 2025"),
    ("Boeing reported a net loss of $116 million, or $0.26 per share, compared with a loss of $5.34 billion, or "
     "$7.14 per share, a year earlier.", "a loss of $5.34 billion", "$5.34 billion", 5.34, "Boeing", "Q3 2025"),
    ("Boeing posted a net loss of $116 million on revenue of $23.3 billion, versus a $5.34 billion loss a year "
     "earlier.", "a $5.34 billion loss a year earlier", "$5.34 billion", 5.34, "Boeing", "Q3 2025"),
    ("Revenue rose 30% to $23.3 billion, and Boeing posted a $116 million loss.", "a $116 million loss",
     "$116 million", 116, "Boeing", "Q3 2026"),
])
def test_a_bare_loss_of_the_company_itself_is_its_net_loss(snapshot, text, span, value_text, value, entity, period):
    for metric in ("net_income", "other"):
        v = check(snapshot, claim(span, value_text, value, metric=metric, entity=entity, period=period, text=text), text)
        assert (v.status, v.reason) == ("verified", "match"), (metric, v.explanation)
        assert all(f.metric == "net_income" and f.value < 0 for f in v.evidence)


# Every amount here equals Boeing's filed Q3 2026 net loss ($116M) or operating income ($354M), so reading any of them
# as the company's net loss would be a false "✓" or "✗": each must stay "?".
@pytest.mark.parametrize("text,metric_text", [
    ("The Commercial Airplanes division reported a loss of $116 million.", "loss"),
    ("Boeing's defense segment posted a $116 million loss.", "loss"),
    ("Despite relatively stable deliveries, the division reported an operating loss of $354 million due to higher "
     "production and supply chain costs.", "operating loss"),
    ("Despite relatively stable deliveries, the division reported an operating loss of $354 million due to higher "
     "production and supply chain costs.", "loss"),
    ("Boeing posted a $116 million loss in its defense division.", "loss"),
    ("Boeing recorded a loss of $116 million on the sale of its unit.", "loss"),
    ("Boeing recorded a $116 million loss on the sale of its unit.", "loss"),
    ("Boeing recorded an impairment loss of $116 million.", "loss"),
    ("Boeing recorded an impairment loss of $116 million.", "impairment loss"),
    ("Boeing reported a $116 million loss from discontinued operations.", "loss"),
    ("Boeing reported a loss from discontinued operations of $116 million.", "loss from discontinued operations"),
    ("Boeing reported a pre-tax loss of $116 million.", "loss"),
    ("Boeing recorded credit losses of $116 million.", "losses"),
    ("Boeing's losses were $116 million.", "losses"),  # an insurer's claims, or trading losses: plural stays unread
    ("Boeing posted losses of $116 million.", "loss"),
    ("Boeing reported a comprehensive loss of $116 million.", "loss"),
    ("Net income was $2.6 billion, including a $116 million loss on the sale.", "loss"),
    ("Airbus reported a net loss of $50 million, while Boeing's unit posted a $116 million loss.", "loss"),
    ("Boeing's revenue rose, but it posted a loss of $116 million.", "loss"),
    ("The loss was $116 million.", "loss"),
    ("The division's operating loss widened to $697 million, from a $116 million loss a year earlier.", "loss"),
])
def test_other_losses_stay_unread(snapshot, text, metric_text):
    value_text = "$354 million" if "$354 million" in text else "$116 million"
    c = claim(text, value_text, float(value_text[1:4]), metric_text=metric_text)
    v = check(snapshot, c, text)
    assert v.status == "unverifiable" and v.expected_value is None, v.explanation


def test_a_company_level_operating_loss_keeps_its_handling(snapshot):
    v = verify(claim("Boeing reported an operating loss of $4.78 billion in Q3 2025.", "$4.78 billion", 4.78,
                     metric="operating_income", metric_text="operating loss", period="Q3 2025"), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation
    v = verify(claim("Boeing reported an operating loss of $354 million.", "$354 million", 354,
                     metric="operating_income", metric_text="operating loss"), snapshot)
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation  # filed: operating income $354M
    assert "not a loss of $354 million" in v.explanation


# --- The sign never flips --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("span,value_text,value,metric,metric_text", [
    ("Centene reported a loss of $1.09 billion in Q2 2026.", "$1.09 billion", 1.09, "net_income", "loss"),
    ("Centene reported net income of $1.54 billion in Q1 2026, compared with a $1.09 billion loss in Q2 2026.",
     "$1.09 billion", 1.09, "net_income", "loss"),
    ("Centene's net loss attributable to shareholders was $1.09 billion.", "$1.09 billion", 1.09, "net_income",
     "net loss attributable to shareholders"),
    ("Centene's diluted loss per share was $2.19.", "$2.19", 2.19, "eps_diluted", "diluted loss per share"),
    ("Centene reported a loss of $2.19 per diluted share.", "$2.19", 2.19, "eps_diluted", "per diluted share"),
])
def test_a_loss_where_the_filing_shows_income_is_wrong(snapshot, span, value_text, value, metric, metric_text):
    v = check(snapshot, claim(span, value_text, value, metric=metric, metric_text=metric_text, entity="Centene",
                              period="Q2 2026"))
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    assert f"not a loss of {value_text}" in v.explanation


@pytest.mark.parametrize("span,value_text,value,metric,metric_text", [
    ("Boeing reported net income of $116 million.", "$116 million", 116, "net_income", "net income"),
    ("Boeing's net income attributable to shareholders was $116 million.", "$116 million", 116, "net_income",
     "net income attributable to shareholders"),
    ("Boeing's diluted EPS was $0.26.", "$0.26", 0.26, "eps_diluted", "diluted EPS"),
    ("Boeing earned $0.26 per diluted share.", "$0.26", 0.26, "eps_diluted", "per diluted share"),
])
def test_income_where_the_filing_shows_a_loss_is_wrong(snapshot, span, value_text, value, metric, metric_text):
    v = check(snapshot, claim(span, value_text, value, metric=metric, metric_text=metric_text))
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation


def test_an_untyped_net_income_figure_takes_its_loss_wording(snapshot):
    # metric "other" read as net income was never signed by its loss: the filed -$116M came back "✗"
    v = verify(claim("Boeing reported a net loss of $116 million.", "$116 million", 116, metric="other",
                     metric_text="net income"), snapshot)
    assert (v.status, v.reason) == ("verified", "match"), v.explanation


def test_a_narrowed_loss_is_a_change_never_its_level(snapshot):
    # $5.337 billion - $0.116 billion: was "✗" against the -$116 million level
    v = verify(claim("Boeing's net loss narrowed by $5.22 billion.", "$5.22 billion", 5.22, metric_text="net loss"),
               snapshot)
    assert (v.status, v.reason) == ("unverifiable", "no_evidence") and "is a change" in v.explanation, v.explanation


# --- "Negative" in words ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("span", ["Operating cash flow was negative $3.67 billion.",
                                  "Operating cash flow was a negative $3.67 billion.",
                                  "Operating cash flow was $3.67 billion negative."])
def test_negative_written_at_the_number_signs_it(snapshot, span):
    c = claim(span, "$3.67 billion", 3.67, metric="operating_cash_flow", metric_text="Operating cash flow",
              entity="Centene")
    assert (verify(c, snapshot).status, claimed_value(c)) == ("verified", -3.67e9)


def test_negative_elsewhere_never_signs_the_figure(snapshot):
    def ocf(span):
        return verify(claim(span, "$3.67 billion", 3.67, metric="operating_cash_flow",
                            metric_text="Operating cash flow", entity="Centene"), snapshot)

    # these were "✓": the draft's positive $3.67 billion matched the filed -$3.669 billion through another figure's word
    v = ocf("Operating cash flow was $3.67 billion, compared with negative $1.36 billion a year ago.")
    assert (v.status, v.reason) == ("wrong", "value_mismatch"), v.explanation
    v = ocf("Operating cash flow was $3.67 billion, despite a negative impact from timing.")
    assert (v.status, v.reason) == ("unverifiable", "ambiguous"), v.explanation
    assert ocf("Operating cash flow was $3.67 billion.").status == "wrong"
