"""Number coverage remains distinct from verdicts across public entry points; no live calls."""
import json
from types import SimpleNamespace

from receipts import check, cli, extract, stream
from test_api import FakeExtract, client, fake_verify
from test_rewrite_safety import scenario, result


def test_check_api_reports_omissions_and_replays_them(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "iter_extract", FakeExtract(cached=True))
    monkeypatch.setattr(stream._verify, "verify", fake_verify)
    text = "Example Corp revenue was $12.4 billion, with margin of 42%."
    with client(tmp_path) as api:
        replies = [api.post("/api/check", json={"text": text}) for _ in range(2)]
    events = []
    for reply in replies:
        assert reply.status_code == 200
        events.append([json.loads(line.removeprefix("data: "))
                       for line in reply.text.splitlines() if line.startswith("data: ")])
    for reply in events:
        done = reply[-1]
        assert done["counts"] == {"verified": 1, "wrong": 0, "not_public": 0, "unverifiable": 0, "approximate": 0}
        assert done["coverage"]["candidate_count"] == 2
        assert done["coverage"]["covered_count"] == 1
        assert done["coverage"]["uncovered"] == [{"start": text.index("42%"), "end": len(text) - 1, "text": "42%"}]
    assert events[1][0]["cached"] is True


def test_no_extracted_claims_still_reports_unchecked_candidates(monkeypatch):
    text = "Operating margin was 42%."
    monkeypatch.setattr(extract, "iter_extract", FakeExtract(cached=True))
    events = list(stream.check_events(text, store=object(), draft_hash="synthetic", as_of=None, context=None))
    assert events[-1][0] == "done"
    coverage = events[-1][1]["coverage"]
    assert coverage["candidate_count"] == 1 and coverage["covered_count"] == 0
    assert coverage["uncovered"][0]["text"] == "42%"


def test_incomplete_check_never_emits_complete_coverage(monkeypatch):
    monkeypatch.setattr(extract, "iter_extract", FakeExtract(fail={1}, cached=True))
    events = list(stream.check_events("Revenue was $12.", store=object(), draft_hash="synthetic", as_of=None, context=None))
    assert events[-1][0] == "error"
    assert all(name != "done" for name, _ in events)


def test_cli_and_detailed_check_expose_omissions(monkeypatch):
    text = "Operating margin was 42%."
    extraction = SimpleNamespace(claims=[], warnings=[], calls=[], cached_calls=0, cost_usd=0, wall_s=0)
    monkeypatch.setattr(extract, "extract_detailed", lambda *args, **kwargs: extraction)
    checked = check.check_detailed(text, store=object())
    output = cli.render("synthetic.txt", text, checked, inferred=True)
    assert "Not checked: '42%'" in output
    assert "0/1 digit-based candidates linked" in output
    assert json.loads(cli.to_json("synthetic.txt", checked))["coverage"] == checked.coverage
    assert checked.results == []  # no fabricated wrong or unverifiable verdict


def test_recording_validates_coverage_and_keeps_legacy_compatible():
    events = [{"event": "meta", "data": {}}, {"event": "done", "data": {}}]
    assert stream.valid_events(events) is not None
    events[-1]["data"]["coverage"] = {"version": 1, "candidate_count": 0, "covered_count": 1,
                                      "excluded_count": 0, "uncovered": []}
    assert stream.valid_events(events) is None
    events[-1]["data"]["coverage"]["covered_count"] = 0
    assert stream.valid_events(events) is not None


def test_fix_reports_coverage_for_original_and_final_text(monkeypatch):
    events, _, _, _ = scenario(monkeypatch)
    output = result(events)
    assert output["status"] == "corrected"
    for field in ("original_coverage", "coverage"):
        assert output[field]["candidate_count"] == output[field]["covered_count"] == 1
        assert output[field]["uncovered"] == []


def test_fix_cannot_skip_a_headcount_that_looks_like_a_year(monkeypatch):
    events, _, calls, model = scenario(monkeypatch,
        text="NVIDIA Q2 FY2027 revenue was $95.2 billion with 2000 employees.")
    assert events[-1][0] == "error"
    assert "original draft is unchanged" in events[-1][1]["message"].lower()
    assert len(calls) == 1
    assert model.calls == []  # no Wonder spend once coverage fails
