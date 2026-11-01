"""Low-severity query-side review regressions (2026-10-31): cross-filing copy, split timing, bounded release cache,
short company names and long dotted tokens. Snapshot reads and synthetic stores only; no network or model calls."""
import json
import time
from datetime import date, timedelta

import pytest

from receipts import store as S
from receipts import verify as V
from receipts.splits import SPLIT_EVENTS
from test_code_review_fixes import claim, store  # noqa: F401
from test_entity_scale import make_store, named
from test_store import pr_fact
from test_verify import FakeStore


# L5: MLM's release has Q2 but no Q1; the accepted QoQ pair uses Q1's 10-Q.
@pytest.mark.parametrize("text, value, status", [
    ("43.0%", 43.0, "verified"), ("55.0%", 55.0, "wrong"),
    ("more than 42.0%", 42.0, "verified"), ("more than 44.0%", 44.0, "wrong"),
    ("about 43.3%", 43.3, "approximate"),
])
def test_cross_filing_growth_names_both_sources(store, text, value, status):
    c = claim(text.split()[-1], value, metric="revenue_growth", unit="%", scale="units", entity="Martin Marietta",
              period_text="Q2 2026", comparison="QoQ", span=f"revenue grew {text} quarter over quarter")
    release = next(f for f in store.facts(store.by_ticker("MLM")[0], "revenue", period_end=date(2026, 6, 30))
                   if f.source == "press_release")
    v = V.verify(c, store, as_of=release.accepted_at)
    assert v.status == status, v.explanation
    assert "computed from" in v.explanation.lower() and "reports that" not in v.explanation
    assert "8-K press release ($1.947 billion for the period ended 2026-06-30)" in v.explanation
    assert "10-Q ($1.362 billion for the period ended 2026-03-31)" in v.explanation
    assert {f.value for f in v.evidence} == {1_947e6, 1_362e6}


def test_same_filing_growth_keeps_its_attribution(store):
    c = claim("21.0%", 21.0, metric="revenue_growth", unit="%", scale="units", entity="Martin Marietta",
              period_text="Q2 2026", comparison="YoY", span="revenue grew 21.0% year over year")
    v = V.verify(c, store)
    assert v.status == "verified" and "computed from" not in v.explanation
    assert v.explanation.startswith("Matches Martin Marietta's 10-Q:")


# L7: every generated event is absent one microsecond before acceptance, present at acceptance and afterwards.
@pytest.mark.parametrize("ticker, event", [(t, e) for t, events in SPLIT_EVENTS.items() for e in events])
def test_reported_split_waits_for_the_filings_acceptance(ticker, event):
    c = claim("$1.00", 1, metric="eps_diluted", unit="USD/share", scale="units", entity=ticker,
              period_text="Q1 2026")
    for at, visible in ((event.reported - timedelta(microseconds=1), False), (event.reported, True),
                        (event.reported + timedelta(microseconds=1), True), (None, True)):
        check = V._Check(c, FakeStore([]), (1, ticker), at, None)
        splits = check._company_splits()
        assert any(s.new_basis_from == event.reported for s in splits) == visible


def test_split_acceptances_match_the_reporting_filings(store):
    record = json.loads((S.ROOT / "eval/evidence_expansion/sp500-splits-20261031.json").read_text())
    for d in record["decisions"]:
        if d["decision"] == "split":
            filing = next(f for f in store.filings(d["cik"]) if f["accession"] == d["first_accn"])
            assert d["first_accepted_at"] == filing["accepted_at"]


# L8: recency, eviction, reloading and a changed file must leave the returned facts intact.
def test_release_cache_is_bounded_and_reloads_without_changing_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PR_CACHE_FILES", 2)
    s = make_store(tmp_path, {"AAA": "Alpha Inc.", "BBB": "Beta Inc.", "CCC": "Gamma Inc."})
    paths, loads = {}, []
    load = S._load_jsonl

    def counted(path):
        loads.append(path)
        return load(path)

    monkeypatch.setattr(S, "_load_jsonl", counted)
    try:
        for cik, ticker, _ in s.companies_list():
            path = s.pr_dir / ticker / "release.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(pr_fact(cik=cik, ticker=ticker).model_dump_json() + "\n")
            paths[ticker] = path

        def read(ticker):
            return s.facts(s.by_ticker(ticker)[0], "revenue")

        a, b = read("AAA"), read("BBB")
        assert read("AAA") == a and loads.count(paths["AAA"]) == 1
        read("CCC")
        assert len(s._pr_cache) == 2 and paths["AAA"] in s._pr_cache and paths["BBB"] not in s._pr_cache
        assert read("BBB") == b and loads.count(paths["BBB"]) == 2
        paths["BBB"].write_text(b[0].model_copy(update={"value": 12_345e6}).model_dump_json() + "\n")
        assert read("BBB")[0].value == 12_345e6 and loads.count(paths["BBB"]) == 3
        assert len(s._pr_cache) == 2
    finally:
        s.close()


# L9: explicit lookup aliases only; ordinary abbreviations and ambiguous GE stay out of running text.
@pytest.mark.parametrize("ticker", ["GS", "GM", "MS"])
def test_explicit_short_tickers_resolve_as_the_claims_company(store, ticker):
    assert store.resolve_entity(ticker) == store.by_ticker(ticker)
    assert store.names_in_text(f"{ticker} rose.") == []


@pytest.mark.parametrize("text, tickers", [("3M and HP reported revenue.", ["MMM", "HPQ"]),
                                         ("3m of cable and 100 hp.", []), ("GE rose.", []),
                                         ("HP Enterprise reported revenue.", ["HPE"])])
def test_short_company_names_count_without_units_or_namesakes(store, text, tickers):
    assert named(store, text) == tickers
    assert store.resolve_entity("GE") is None


# L15: CLI/MCP inputs can be much longer than the API's 12,000-character cap.
def test_sentence_joins_is_fast_on_a_long_dotted_token():
    s, tokens = S.scan_tokens("a." * 50_000 + "1. Next")
    start = time.monotonic()
    assert S.sentence_joins(s, tokens) == [False]
    assert time.monotonic() - start < 1.0


@pytest.mark.parametrize("text, joined", [("J.P. Morgan", True), ("T. Rowe", True), ("U.S. Bancorp", True),
                                         ("Inc. reported", True), ("capital. One", False), ("3M. Next", False),
                                         ('capital.\" One', False)])
def test_sentence_joins_keeps_initials_abbreviations_and_sentence_ends(text, joined):
    s, tokens = S.scan_tokens(text)
    assert S.sentence_joins(s, tokens) == [joined]
