"""F5: a future filed split-adjusted value is look-ahead, not a numeric correction.

Real snapshot cases run through extraction's deterministic post-processing. Synthetic cases cover
every table event and per-share metric without changing the table or invoking a model.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from receipts import extract as X
from receipts import verify as V
from receipts.splits import SPLIT_EVENTS
from receipts.store import Store
from receipts.types import Fact
from test_verify import FakeStore, claim


SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "snapshot"


@pytest.fixture(scope="module")
def snapshot():
    store = Store(SNAPSHOT / "facts.sqlite", SNAPSHOT / "pr_facts")
    try:
        yield store
    finally:
        store.close()


def extracted_eps(entity, value, period="first quarter of 2025"):
    value_text = f"${value:.2f}"
    sentence = f"{entity}'s first-quarter diluted EPS was {value_text}."
    model = X.ModelClaim(metric="eps_diluted", metric_text="diluted EPS", value_text=value_text, value=value,
                         unit="USD/share", scale="units", period_text=period, period_type="quarter",
                         comparison="none", bases=["GAAP"], forward_looking=False, entity=entity,
                         segment=None, span_text=sentence)
    claims, _ = X.build_claims([(0, sentence, [model])], [])
    assert len(claims) == 1
    return claims[0]


@pytest.mark.parametrize("entity,ticker,old_value,new_value,as_of,first_accession,first_time,ratio", [
    ("O'Reilly", "ORLY", 9.35, 0.62, "2025-05-20T00:00:00Z", "0000898173-26-000024",
     "2026-04-29T20:35:09Z", 15),
    ("Netflix", "NFLX", 6.61, 0.66, "2025-05-20T00:00:00Z", "0001065280-26-000033",
     "2026-01-20T21:07:54Z", 10),
])
def test_real_split_adjusted_claim_waits_for_its_first_matching_receipt(
        snapshot, entity, ticker, old_value, new_value, as_of, first_accession, first_time, ratio):
    # The snapshot's ORLY earnings release precedes the May 8 10-Q; use the earlier actual receipt.
    c = extracted_eps(entity, new_value)
    v = V.verify(c, snapshot, as_of=as_of)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "look_ahead", None), v.explanation
    assert "split-adjusted" in v.explanation and f"{ratio}-for-1" in v.explanation
    assert V.fmt_ts(V.to_utc(first_time)) in v.explanation
    assert f"${old_value:.2f}" in v.explanation and "as-of" in v.explanation
    assert first_accession in {f.accession for f in v.evidence}
    assert {old_value, new_value} <= {f.value for f in v.evidence}
    assert all(f.ticker == ticker and f.period_end == date(2025, 3, 31) for f in v.evidence)


@pytest.mark.parametrize("entity,value,as_of,status", [
    ("O'Reilly", 0.62, None, "verified"),
    ("O'Reilly", 0.62, "2026-05-08T20:37:22Z", "verified"),
    ("O'Reilly", 9.35, "2025-05-20T00:00:00Z", "verified"),
    ("O'Reilly", 4.11, "2025-05-20T00:00:00Z", "wrong"),
    ("Netflix", 0.66, None, "verified"),
    ("Netflix", 0.66, "2026-01-20T21:07:54Z", "verified"),
    ("Netflix", 6.61, "2025-05-20T00:00:00Z", "verified"),
    ("Netflix", 4.11, "2025-05-20T00:00:00Z", "wrong"),
])
def test_real_split_controls_keep_their_verdict(snapshot, entity, value, as_of, status):
    v = V.verify(extracted_eps(entity, value), snapshot, as_of=as_of)
    assert v.status == status and v.reason != "look_ahead", v.explanation


def test_orly_xbrl_only_waits_until_the_restating_10q_acceptance(snapshot):
    cik = snapshot.by_ticker("ORLY")[0]
    facts = [f for f in snapshot.facts(cik, "eps_diluted") if f.source == "xbrl"]
    store = FakeStore(facts, aliases={"o'reilly": (cik, "ORLY")},
                      periods={(cik, 2025, "Q1"): (date(2025, 1, 1), date(2025, 3, 31))})
    c = extracted_eps("O'Reilly", 0.62)
    accepted = V.to_utc("2026-05-08T20:37:22Z")
    for as_of in (V.to_utc("2025-05-20T00:00:00Z"), accepted - timedelta(microseconds=1)):
        v = V.verify(c, store, as_of=as_of)
        assert (v.status, v.reason, v.expected_value) == ("wrong", "look_ahead", None), v.explanation
        assert "2026-05-08 20:37:22 UTC" in v.explanation and "$9.35" in v.explanation
        assert "0000898173-26-000027" in {f.accession for f in v.evidence}
    assert V.verify(c, store, as_of=accepted).status == "verified"


def test_orly_first_matching_release_acceptance_is_inclusive(snapshot):
    c = extracted_eps("O'Reilly", 0.62)
    accepted = V.to_utc("2026-04-29T20:35:09Z")
    v = V.verify(c, snapshot, as_of=accepted - timedelta(microseconds=1))
    assert (v.status, v.reason) == ("wrong", "look_ahead"), v.explanation
    assert "2026-04-29 20:35:09 UTC" in v.explanation
    assert "0000898173-26-000024" in {f.accession for f in v.evidence}
    assert V.verify(c, snapshot, as_of=accepted).status == "verified"


def test_crowdstrike_negative_eps_uses_the_reported_split_restatement_slack(snapshot):
    # The restated loss is recomputed on the new share count: -0.31 / 4 = -0.0775,
    # which does not round to -0.07. The existing reported-split slack covers this pair.
    c = claim("-$0.07", -0.07, metric="eps_diluted", metric_text="diluted EPS", unit="USD/share",
              scale="units", entity="CrowdStrike", basis="GAAP", period_text="second quarter of fiscal 2026",
              span="CrowdStrike's diluted EPS was -$0.07 for the second quarter of fiscal 2026.")
    v = V.verify(c, snapshot, as_of="2026-05-20T00:00:00Z")
    assert (v.status, v.reason, v.expected_value) == ("wrong", "look_ahead", None), v.explanation
    assert "4-for-1" in v.explanation and "split-adjusted" in v.explanation
    assert "2026-08-26 20:07:02 UTC" in v.explanation and "-$0.31" in v.explanation
    assert {f.accession for f in v.evidence} == {"0001535527-25-000025", "0001535527-26-000029"}
    old = next(f for f in v.evidence if f.value == -0.31)
    new = next(f for f in v.evidence if f.value == -0.07)
    assert V.split_ratio(old, new) is None
    assert V.verify(c, snapshot, as_of="2026-08-26T20:07:02Z").status == "verified"


EVENTS = [(ticker, event) for ticker, events in SPLIT_EVENTS.items() for event in events]
LABELS = {"eps_diluted": "diluted EPS", "eps_basic": "basic EPS", "dividend_per_share": "dividend per share"}


def synthetic_pair(ticker, event, metric="eps_diluted"):
    # Hand-made figures on either side of the real table event, for one prior-year quarter.
    year = event.starts.year - 1
    old_at = datetime.combine(event.starts, datetime.min.time(), timezone.utc) - timedelta(days=1)
    old_value, new_value = (12.0, 12.0 / event.ratio) if event.forward else (1.0, float(event.ratio))
    old = Fact(cik=1, ticker=ticker, metric=metric, basis="GAAP", value=old_value, unit="USD/share",
               period_start=date(year, 1, 1), period_end=date(year, 3, 31), fiscal_year=year, fiscal_period="Q1",
               source="xbrl", form="10-Q", accession="old", accepted_at=old_at,
               url="https://example.invalid/old", decimals=4)
    new = old.model_copy(update={"value": new_value, "accession": "new", "accepted_at": event.reported,
                                 "url": "https://example.invalid/new"})
    return old, new


def synthetic_store(facts, ticker):
    first = facts[0]
    return FakeStore(facts, aliases={ticker.casefold(): (first.cik, ticker)},
                     periods={(first.cik, first.fiscal_year, "Q1"): (first.period_start, first.period_end)})


def per_share_claim(fact, value=None):
    value = fact.value if value is None else value
    text = f"${value:.4f}"
    label = LABELS[fact.metric]
    return claim(text, value, metric=fact.metric, unit="USD/share", scale="units", basis="GAAP",
                 entity=fact.ticker, period_text=f"Q1 {fact.fiscal_year}", metric_text=label,
                 span=f"{label} was {text}.")


@pytest.mark.parametrize("ticker,event", EVENTS, ids=[f"{t}-{e.starts}" for t, e in EVENTS])
@pytest.mark.parametrize("metric", LABELS)
def test_every_table_event_and_per_share_metric_uses_the_matching_future_filing(ticker, event, metric):
    old, new = synthetic_pair(ticker, event, metric)
    store = synthetic_store([old, new], ticker)
    c = per_share_claim(new)
    v = V.verify(c, store, as_of=old.accepted_at)
    assert (v.status, v.reason, v.expected_value) == ("wrong", "look_ahead", None), v.explanation
    assert V.fmt_ts(new.accepted_at) in v.explanation
    assert {f.accession for f in v.evidence} == {"old", "new"}
    assert V.verify(per_share_claim(old), store, as_of=old.accepted_at).status == "verified"
    assert V.verify(c, store, as_of=new.accepted_at).status == "verified"
    assert V.verify(c, store).status == "verified"


def test_an_unrelated_future_restatement_is_not_split_look_ahead():
    old, new = synthetic_pair("ORLY", SPLIT_EVENTS["ORLY"][0])
    new = new.model_copy(update={"value": 11.35})
    store = synthetic_store([old, new], "ORLY")
    v = V.verify(per_share_claim(new), store, as_of=old.accepted_at)
    assert v.status == "wrong" and v.reason != "look_ahead", v.explanation


def test_a_calculated_adjustment_without_a_matching_future_fact_is_not_look_ahead():
    old, new = synthetic_pair("ORLY", SPLIT_EVENTS["ORLY"][0])
    store = synthetic_store([old], "ORLY")
    v = V.verify(per_share_claim(new), store, as_of=old.accepted_at)
    assert v.status == "wrong" and v.reason != "look_ahead", v.explanation


@pytest.mark.parametrize("field,value", [("metric", "eps_basic"), ("basis", "non-GAAP"),
                                         ("period_start", date(2024, 1, 2))])
def test_a_future_match_on_another_measure_or_span_is_not_split_look_ahead(field, value):
    old, new = synthetic_pair("ORLY", SPLIT_EVENTS["ORLY"][0])
    store = synthetic_store([old, new.model_copy(update={field: value})], "ORLY")
    v = V.verify(per_share_claim(new), store, as_of=old.accepted_at)
    assert v.status == "wrong" and v.reason != "look_ahead", v.explanation
