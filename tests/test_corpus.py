"""Tests for the seeded-error corpus (eval/build_corpus.py) and the eval harness (eval/eval.py).

Runs offline: only the committed eval/corpus/*.jsonl files are needed, never data/cache or the network.

    .venv/bin/python -m pytest tests/test_corpus.py -q
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load("receipts_eval_build_corpus", ROOT / "eval" / "build_corpus.py")
ev = _load("receipts_eval_eval", ROOT / "eval" / "eval.py")

REQUIRED = ("item_id", "ticker", "text", "as_of", "context", "original_item_id", "mutator", "target_value_text",
            "expected_status", "expected_reason")


@pytest.fixture(scope="module")
def sources() -> list[dict]:
    return bc.read_jsonl(bc.SOURCES_PATH)


@pytest.fixture(scope="module")
def committed() -> list[dict]:
    items = bc.read_jsonl(bc.DEV_PATH) + bc.read_jsonl(bc.TEST_PATH)
    return sorted(items, key=lambda r: r["item_id"])


@pytest.fixture(scope="module")
def by_id(committed) -> dict[str, dict]:
    return {it["item_id"]: it for it in committed}


def _digest(items: list[dict]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ------------------------------------------------------------------ determinism

def test_items_rebuild_exactly_from_sources(sources, committed):
    """Stage 2 is a pure function of v0_sources.jsonl: rebuilding reproduces the committed dev and test files."""
    assert _digest(bc.build_items(sources)) == _digest(committed)


def test_rebuild_is_independent_of_hash_seed(committed):
    """Same corpus under a different PYTHONHASHSEED, so no set or dict ordering leaks into the output."""
    code = ("import importlib.util,sys,json,hashlib;"
            f"spec=importlib.util.spec_from_file_location('bc',{str(ROOT / 'eval' / 'build_corpus.py')!r});"
            "bc=importlib.util.module_from_spec(spec);sys.modules['bc']=bc;spec.loader.exec_module(bc);"
            "items=bc.build_items(bc.read_jsonl(bc.SOURCES_PATH));"
            "print(hashlib.sha256(json.dumps(items,sort_keys=True,ensure_ascii=False).encode()).hexdigest())")
    for seed in ("1", "4242"):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             env={**os.environ, "PYTHONHASHSEED": seed}).stdout.strip()
        assert out == _digest(committed)


def test_mutators_are_deterministic(sources):
    company_rows = {r["ticker"]: r for r in sources if r["kind"] == "company"}
    docs = {r["doc_id"]: r for r in sources if r["kind"] == "document"}
    docs_by_label = defaultdict(dict)
    for d in docs.values():
        docs_by_label[d["ticker"]][(d["fy"], d["fp"])] = d
    pool = [bc._hydrate(copy.deepcopy(r)) for r in sources if r["kind"] == "sentence" and r["pool"] == "verifiable"]

    def run_all():
        out = []
        for s in pool:
            d = docs[s["doc_id"]]
            for fn in (bc.mutate_value, bc.mutate_scale, bc.mutate_period, bc.mutate_look_ahead):
                out.append(fn(s, d, company_rows))
            out += [bc.mutate_basis(s, d, company_rows, v) for v in (0, 1)]
            out.append(bc.mutate_entity(s, d, company_rows, docs_by_label))
        return [None if m is None else vars(m) for m in out]

    assert run_all() == run_all()


# ------------------------------------------------------------------ gold labels

def test_gold_fields_and_ids(committed):
    ids = [it["item_id"] for it in committed]
    assert len(ids) == len(set(ids))
    for it in committed:
        for k in REQUIRED:
            assert k in it, (it["item_id"], k)
        assert it["item_hash"] == bc.item_hash(it["text"], it["as_of"], it["context"])
        if it["original_item_id"] is None:
            assert it["mutator"] in ("none", "forward_looking_control")
            assert it["target_value_text"] is None
            if it["mutator"] == "none":
                assert (it["expected_status"], it["expected_reason"]) == ("verified", "match")
            else:
                assert (it["expected_status"], it["expected_reason"]) == ("unverifiable", "forward_looking")
        else:
            assert it["mutator"] in bc.MUTATORS
            assert it["expected_status"] == "wrong"
            assert it["expected_reason"] == bc.EXPECTED_REASON[it["mutator"]]
            assert it["target_value_text"]
            a, b = it["target_span"]
            assert it["text"][a:b] == it["target_value_text"], it["item_id"]


def test_corpus_size_and_coverage(committed):
    counts = Counter(it["mutator"] for it in committed)
    originals = counts["none"]
    assert 250 <= len(committed) <= 350
    assert 120 <= originals <= 170
    assert 8 <= counts["forward_looking_control"] <= 12
    for m in bc.MUTATORS:
        assert counts[m] >= (15 if m == "basis" else 20), m
    assert set(it["ticker"] for it in committed) == set(bc.TICKERS)


def test_originals_state_one_to_three_numbers(sources):
    """v0 pool sentences state 1-3 numbers; only the basis appendix allows 4-5 (sentences stating both bases)."""
    for r in sources:
        if r["kind"] == "sentence":
            fin = [n for n in r["numbers"] if n["kind"] in ("money", "pct", "pp", "bps")]
            if r["pool"] == bc.BASIS_EXTRA_POOL:
                assert bc.BASIS_EXTRA_NUMBERS[0] <= len(fin) <= bc.BASIS_EXTRA_NUMBERS[1], r["text"]
                assert bc._basis_spans(r["text"]) is not None, r["text"]
            else:
                assert 1 <= len(fin) <= 3, r["text"]


def test_basis_appendix_leaves_every_v0_item_unchanged(sources, committed):
    """Append-only: building without the appendix rows gives exactly the committed v0 items (ids, text, split)."""
    v0_sources = [r for r in sources if r.get("pool") != bc.BASIS_EXTRA_POOL]
    v0_items = [it for it in committed if "batch" not in it]
    assert _digest(bc.build_items(v0_sources)) == _digest(v0_items)
    extra = [it for it in committed if "batch" in it]
    assert extra and {it["batch"] for it in extra} == {bc.BASIS_EXTRA_BATCH}
    assert {it["mutator"] for it in extra} == {"none", "basis"}


def test_basis_mutator_on_four_and_five_number_sentences():
    msft = "Net income on a GAAP basis was $38.5 billion and increased 60%, and on a non-GAAP basis was $30.9 billion " \
           "and increased 23% (up 21% in constant currency)"
    series = {"net_income": {"2025-12-31": 38.458e9, "2024-12-31": 24.108e9}}
    a = bc.analyze_basis_sentence(msft, series, date(2025, 12, 31), (2026, "Q2"))
    s = bc._hydrate({"sentence_id": "T-FY2026Q2-s000001", "ticker": "MSFT", "text": msft, "pool": bc.BASIS_EXTRA_POOL,
                     "basis_pattern": a["basis_pattern"], "numbers": [vars(n) for n in a["numbers"]]})
    doc = {"doc_id": "MSFT-FY2026Q2", "ticker": "MSFT", "fy": 2026, "fp": "Q2", "period_end": "2025-12-31",
           "context": bc.context_line("MSFT", 2026, "Q2", "2025-12-31")}
    m0, m1 = (bc.mutate_basis_extra(s, doc, {"MSFT": {"series": series}}, v) for v in (0, 1))
    assert m0.text == msft.replace("on a non-GAAP basis", "on a GAAP basis")
    assert (m0.target_value_text, m0.expected_value, m0.affected_value_texts) == ("$30.9 billion", 38.5,
                                                                                  ["$30.9 billion", "23%"])
    assert m1.text == msft.replace("on a GAAP basis", "on a non-GAAP basis", 1)
    assert (m1.target_value_text, m1.expected_value) == ("$38.5 billion", 30.9)
    assert m0 == bc.mutate_basis_extra(s, doc, {"MSFT": {"series": series}}, 0)  # deterministic
    amd = "On a non-GAAP(*) basis, gross margin was 54%, operating income was $2.2 billion, net income was $2 billion " \
          "and diluted earnings per share was $1.20."
    series = {"gross_margin": {"2025-09-27": 51.7}, "operating_income": {"2025-09-27": 1.27e9},
              "net_income": {"2025-09-27": 1.243e9}, "eps_diluted": {"2025-09-27": 0.75}}
    a = bc.analyze_basis_sentence(amd, series, date(2025, 9, 27), (2025, "Q3"))
    s = bc._hydrate({"sentence_id": "T-FY2025Q3-s000002", "ticker": "AMD", "text": amd, "pool": bc.BASIS_EXTRA_POOL,
                     "basis_pattern": a["basis_pattern"], "numbers": [vars(n) for n in a["numbers"]]})
    doc = {"doc_id": "AMD-FY2025Q3", "ticker": "AMD", "fy": 2025, "fp": "Q3", "period_end": "2025-09-27",
           "context": bc.context_line("AMD", 2025, "Q3", "2025-09-27")}
    m = bc.mutate_basis_extra(s, doc, {"AMD": {"series": series}}, 0)
    assert m.text.startswith("On a GAAP(*) basis, gross margin was 54%")
    # "$2 billion" vs GAAP $1.243B is less than twice its written precision apart: never a target or affected
    assert "$2 billion" not in m.affected_value_texts and m.target_value_text != "$2 billion"
    assert bc.mutate_basis_extra(s, doc, {"AMD": {"series": series}}, 1) is None  # one variant for this pattern
    assert bc.analyze_basis_sentence("Revenue was $9.2 billion, up 36%.", series, date(2025, 9, 27), None) is None


def test_sentence_metadata_points_into_the_source(sources):
    docs = {r["doc_id"]: r for r in sources if r["kind"] == "document"}
    for r in sources:
        if r["kind"] != "sentence":
            continue
        d = docs[r["doc_id"]]
        for k in ("ticker", "accession", "accepted_at", "url", "char_start", "char_end"):
            assert r[k] not in (None, ""), (r["sentence_id"], k)
        assert r["char_end"] - r["char_start"] == len(r["text"])
        assert r["accession"] == d["accession"] and r["url"] == d["url"]
        assert datetime.fromisoformat(r["accepted_at"].replace("Z", "+00:00")).tzinfo is not None
        assert d["tenq_accepted_at"] > d["accepted_at"]  # ground truth (the 10-Q) exists and came later


# ------------------------------------------------------------------ exactly one change per mutant

def _numbers(text: str) -> list[str]:
    return [n.text for n in bc.find_numbers(text)]


def test_each_mutant_changes_exactly_one_claim(committed, by_id):
    for it in committed:
        if it["original_item_id"] is None:
            continue
        orig = by_id[it["original_item_id"]]
        m = it["mutator"]
        e = it["edit"]
        if m == "look_ahead":
            assert it["text"] == orig["text"] and it["context"] == orig["context"] and e is None
            as_of = datetime.fromisoformat(it["as_of"].replace("Z", "+00:00"))
            acc = datetime.fromisoformat(it["accepted_at"].replace("Z", "+00:00"))
            assert acc - as_of == timedelta(hours=1)
            continue
        assert it["as_of"] is None
        if m == "entity" and e is None:  # the company named by the context line was swapped
            assert it["text"] == orig["text"] and it["context"] != orig["context"]
            assert it["mutation"]["location"] == "context"
            assert bc.COMPANIES[it["mutation"]["to_ticker"]].name in it["context"]
            continue
        # one contiguous edit applied to the original text yields the mutant, so nothing outside it changed
        assert orig["text"][e["start"]:e["end"]] == e["before"], it["item_id"]
        assert orig["text"][:e["start"]] + e["after"] + orig["text"][e["end"]:] == it["text"], it["item_id"]
        if m in ("value", "scale"):  # the edit sits inside the one number it changes
            ts = it["target_span"][0]
            assert orig["text"][ts:ts + len(it["original_value_text"])] == it["original_value_text"]
            assert ts <= e["start"] and e["end"] <= ts + len(it["original_value_text"]), it["item_id"]
        elif m == "period":  # a comparison phrase or a quarter name, never a figure
            assert it["mutation"]["kind"] in ("comparison", "quarter")
            assert not re.search(r"\d", e["before"] + e["after"]) or re.fullmatch(r"[1-4]?Q[1-4]?\d*", e["after"])
        elif m == "basis":  # only basis words change: one is removed (v0) or GAAP and non-GAAP trade places
            assert ("GAAP" in e["before"] and len(e["after"]) <= 1) or {e["before"], e["after"]} == {"GAAP",
                                                                                                    "non-GAAP"}
        elif m == "entity":  # a company name (with its ticker) is replaced by another of the ten
            frm, to = bc.COMPANIES[it["mutation"]["from_ticker"]], bc.COMPANIES[it["mutation"]["to_ticker"]]
            assert any(a in e["before"] for a in frm.aliases) and to.name in e["after"]
        if m != "entity":
            assert it["context"] == orig["context"]
        before, after = _numbers(orig["text"]), _numbers(it["text"])
        assert len(before) == len(after), it["item_id"]
        changed = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
        if m in ("value", "scale"):
            assert len(changed) == 1, (it["item_id"], changed)
            assert after[changed[0]] == it["target_value_text"]
        else:  # period, basis, entity: the number stays, its period / basis / company changes
            assert changed == [], (it["item_id"], changed)


def test_value_mutations_are_clearly_wrong(committed, by_id):
    for it in committed:
        if it["mutator"] != "value":
            continue
        old = bc.find_numbers(it["original_value_text"])[0]
        new = bc.find_numbers(it["target_value_text"])[0]
        assert new.decimals == old.decimals and new.scale == old.scale
        step = 10 ** -old.decimals
        delta = abs(new.value - old.value)
        assert delta >= 2 * step - 1e-9, it["item_id"]
        assert delta >= 0.03 * abs(old.value) - 1e-9, it["item_id"]


def test_scale_and_basis_mutations(committed):
    for it in committed:
        if it["mutator"] == "scale":
            assert it["mutation"]["from_scale"] != it["mutation"]["to_scale"]
        if it["mutator"] == "basis":
            ev_ = it["expected_value"]
            stated = bc.find_numbers(it["target_value_text"])[0].value
            assert abs(ev_ - stated) >= 2 * 10 ** -bc.find_numbers(it["target_value_text"])[0].decimals - 1e-9


def test_period_and_entity_mutants_checked_against_xbrl_when_possible(committed):
    for it in committed:
        if it["mutator"] in ("period", "entity"):
            assert it["mutant_screen"] in ("xbrl", "assumed")
            if it["mutant_screen"] == "xbrl":
                assert it["expected_value"] is not None
            if it["mutator"] == "entity" and it["mutant_screen"] == "assumed":
                assert bc.find_numbers(it["target_value_text"])[0].kind == "money"  # %s are always XBRL-checked


# ------------------------------------------------------------------ split

def test_split_is_stable_grouped_and_stratified(committed):
    fresh = copy.deepcopy(committed)
    for it in fresh:
        it.pop("split")
    random.Random(7).shuffle(fresh)  # input order must not matter
    bc.assign_split(fresh)
    assert {it["item_id"]: it["split"] for it in fresh} == {it["item_id"]: it["split"] for it in committed}

    groups = defaultdict(set)
    for it in committed:
        groups[it["group_id"]].add(it["split"])
    assert all(len(s) == 1 for s in groups.values()), "an original and its mutants must share a split"

    dev = sum(it["split"] == "dev" for it in committed)
    assert 0.65 <= dev / len(committed) <= 0.75
    for key in ("ticker", "mutator"):
        per = defaultdict(set)
        n_groups = defaultdict(set)
        for it in committed:
            per[it[key]].add(it["split"])
            n_groups[it[key]].add(it["group_id"])
        for k, splits in per.items():
            if len(n_groups[k]) >= 4:
                assert splits == {"dev", "test"}, (key, k)


# ------------------------------------------------------------------ mutators on hand-made sentences

SERIES = {"revenue": {"2026-04-26": 81.6e9, "2026-07-26": 96.2e9, "2025-07-27": 46.7e9, "2026-01-25": 68.1e9,
                      "2025-04-27": 44.1e9}}


def _sentence(text: str, sid: str = "T-FY2027Q2-s000001", fy: int = 2027, fp: str = "Q2", end: str = "2026-07-26",
              series: dict = SERIES) -> tuple[dict, dict]:
    a = bc.analyze_sentence(text, series, date.fromisoformat(end), (fy, fp))
    s = bc._hydrate({"sentence_id": sid, "ticker": "NVDA", "text": text, "pool": "verifiable",
                     "numbers": [vars(n) for n in a["numbers"]]})
    doc = {"doc_id": "NVDA-FY2027Q2", "ticker": "NVDA", "fy": fy, "fp": fp, "period_end": end,
           "accepted_at": "2026-08-26T20:21:19Z", "numbers": [],
           "context": bc.context_line("NVDA", fy, fp, end)}
    return s, doc


def test_value_mutator_on_a_sentence():
    s, d = _sentence("Revenue of $96.2 billion, up 106% from a year ago")
    m = bc.mutate_value(s, d, {"NVDA": {"series": SERIES}})
    assert m is not None and m.text != s["text"]
    assert m == bc.mutate_value(s, d, {"NVDA": {"series": SERIES}})


def test_scale_mutator_flips_billion_and_million():
    s, d = _sentence("Revenue of $96.2 billion, up 106% from a year ago")
    m = bc.mutate_scale(s, d, {"NVDA": {"series": SERIES}})
    assert m.text == "Revenue of $96.2 million, up 106% from a year ago"
    assert m.target_value_text == "$96.2 million"


def test_period_mutator_swaps_comparison_and_refuses_accidental_truth():
    s, d = _sentence("Revenue of $96.2 billion, up 106% from a year ago")
    m = bc.mutate_period(s, d, {"NVDA": {"series": SERIES}})
    assert m.text == "Revenue of $96.2 billion, up 106% from the previous quarter"
    assert m.screen == "xbrl" and abs(m.expected_value - 17.89) < 0.1
    # if quarter-over-quarter growth were also 106%, the swapped sentence would still be true: no mutant
    same = {"revenue": dict(SERIES["revenue"], **{"2026-04-26": 96.2e9 / 2.06})}
    s2, d2 = _sentence("Revenue of $96.2 billion, up 106% from a year ago", series=same)
    assert bc.mutate_period(s2, d2, {"NVDA": {"series": same}}) is None


def test_basis_mutator_removes_one_basis_word():
    text = "GAAP and non-GAAP earnings per diluted share were $2.46 and $2.22, respectively."
    s, d = _sentence(text)
    m0 = bc.mutate_basis(s, d, {}, 0)
    m1 = bc.mutate_basis(s, d, {}, 1)
    assert m0.text == "GAAP earnings per diluted share were $2.46 and $2.22, respectively."
    assert (m0.target_value_text, m0.expected_value) == ("$2.22", 2.46)
    assert m1.text == "Non-GAAP earnings per diluted share were $2.46 and $2.22, respectively."
    assert (m1.target_value_text, m1.expected_value) == ("$2.46", 2.22)
    both, _ = _sentence("For the quarter, GAAP and non-GAAP gross margins were both 75.0%.")
    assert bc.mutate_basis(both, d, {}, 0) is None  # equal bases: nothing to get wrong


def test_look_ahead_mutator_sets_as_of_one_hour_before_acceptance():
    s, d = _sentence("Revenue of $96.2 billion, up 106% from a year ago")
    m = bc.mutate_look_ahead(s, d, {})
    assert (m.text, m.as_of) == (s["text"], "2026-08-26T19:21:19Z")


def test_entity_mentions_skip_products_and_include_tickers():
    nv = bc.COMPANIES["NVDA"]
    t = "NVIDIA (NASDAQ: NVDA) today reported revenue of $96.2 billion."
    assert [t[a:b] for a, b in bc._entity_mentions(t, nv)] == ["NVIDIA (NASDAQ: NVDA)"]
    assert bc._entity_mentions("Microsoft Cloud revenue was $54.5 billion.", bc.COMPANIES["MSFT"]) == []


def test_number_parsing_edge_cases():
    nums = bc.find_numbers("managed revenue of $47.1 billion2 and $12.9B, up 16 percent, (+11% F/X neutral)")
    assert [(n.text, n.scale) for n in nums] == [("$47.1 billion", "billions"), ("$12.9B", "billions"),
                                                  ("16 percent", "units"), ("+11%", "units")]


# ------------------------------------------------------------------ eval harness (offline)

def test_eval_oracle_stub_scores_perfectly(tmp_path):
    out = tmp_path / "run.json"
    ev.main(["--stub", "oracle", "--split", "dev", "--limit", "40", "--quiet", "--no-cache", "--json-out", str(out)])
    rec = json.loads(out.read_text())
    per = rec["summary"]["per_mutator"]
    assert sum(c.get("n", 0) for c in per.values()) > 0
    for m, c in per.items():
        assert c.get("caught", 0) == c.get("n", 0), m
    assert rec["summary"]["originals"].get("false_alarm_items", 0) == 0


def test_eval_random_stub_and_selection_are_deterministic(tmp_path):
    items = ev.load_split("dev")
    sel = ev.select_items(items, 20, None, None)
    assert [it["item_id"] for it in sel] == [it["item_id"] for it in ev.select_items(items, 20, None, None)]
    assert {ev._bucket(it) for it in sel} >= {"original", *bc.MUTATORS}
    out = tmp_path / "run.json"
    ev.main(["--stub", "--split", "dev", "--limit", "20", "--quiet", "--no-cache", "--json-out", str(out)])
    rec = json.loads(out.read_text())
    assert len(rec["rows"]) == 20 and rec["stats"]["errors"] == 0


def test_budget_guard_stops_starting_items(tmp_path):
    items = ev.select_items(ev.load_split("dev"), 12, None, None)
    check = ev.make_stub("random", items)
    cache = ev.ResultCache("budget-test", enabled=False)
    results, stats = ev.run_items(check, items, cache, concurrency=4, budget=0.0012, est_per_item=0.0004,
                                  progress=False)
    assert stats.skipped_budget > 0 and stats.fresh + stats.skipped_budget == len(items)
    assert stats.spent_usd <= 0.0012 + 1e-9


def test_fingerprint_sees_press_release_facts_in_ticker_folders(tmp_path, monkeypatch):
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts" / "verify.py").write_text("# code\n")
    folder = tmp_path / "data" / "pr_facts" / "EXMP"
    folder.mkdir(parents=True)
    facts = folder / "0000000001-26-000019.jsonl"
    facts.write_text("{}\n")
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    before = ev.verifier_fingerprint(None)
    facts.write_text("{}\n{}\n")  # evidence changed, code did not: cached verdicts must not be reused
    assert ev.verifier_fingerprint(None) != before


def test_batch_filter_selects_the_appendix_or_v0():
    items = ev.load_split("dev")
    appendix = ev.select_items(items, None, None, None, "basis5")
    v0 = ev.select_items(items, None, None, None, "v0")
    assert appendix and all(it.get("batch") == "basis5" for it in appendix)
    assert len(v0) + len(appendix) == len(items) and not any("batch" in it for it in v0)
