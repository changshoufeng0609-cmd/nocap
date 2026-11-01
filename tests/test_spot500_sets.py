"""Versioned spot500 sets: v1 stays reproducible, v2 changes only its seed and exclusions.

Source-only checks; no verifier, extraction or provider is involved. The full
source rebuild needs the gitignored SEC archive and runs only when
SPOT500_DATA_ROOT names it (about two minutes).
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from eval import build_corpus as base
from eval import build_spot500 as spot
from eval import spot500

ROOT = Path(__file__).resolve().parents[1]
V1, V2 = spot.SETS["v1"], spot.SETS["v2"]
V1_FREEZE_COMMIT = "050be2e5fe97f9920308e9d63643b7ee8c9771c7"
DOC = {"doc_id": "EXCO-FY2026Q3", "ticker": "EXCO", "fy": 2026, "fp": "Q3", "period_end": "2026-09-30",
       "context": "Example Co (EXCO), third quarter of fiscal 2026", "quarter_days": 91}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def frozen_manifest(spot_set):
    return json.loads((ROOT / "eval" / spot_set.manifest).read_bytes())


def frozen_items(spot_set):
    return [json.loads(line) for line in (ROOT / "eval" / spot_set.corpus).read_bytes().splitlines()]


def sentence(ticker, text="Revenue grew 5% year over year.", *, kind="historical"):
    numbers = []
    for number in base.find_numbers(text):
        number.metric, number.growth, number.screen = "revenue", True, "ok"
        number.screen_detail = f"xbrl={number.value:g}"
        numbers.append(asdict(number))
    doc_id = ticker + "-FY2026Q3"
    return {"sentence_id": doc_id + "-s000001", "doc_id": doc_id, "ticker": ticker, "kind": kind,
            "text": text, "numbers": numbers}


def pool(per_sector=8, without_sentences=()):
    """Eight companies per GICS sector; the five variable-week names sit in Consumer Staples."""
    companies, docs, sentences = {}, {}, []
    special = iter(sorted(spot.IRREGULAR))
    for index, (sector, industries) in enumerate(sorted(spot.SECTOR_GROUPS.items())):
        industry = "Diversified Banks" if sector == "Financials" else industries.split("|")[0]
        for offset in range(per_sector):
            ticker = (next(special, None) if sector == "Consumer Staples" else None) or f"S{index}C{offset}"
            companies[ticker] = {"ticker": ticker, "name": ticker, "sector": sector, "sub_industry": industry,
                                 "series": {}}
            docs[ticker + "-FY2026Q3"] = {**DOC, "doc_id": ticker + "-FY2026Q3", "ticker": ticker}
            if ticker not in without_sentences:
                sentences.append(sentence(ticker))
    return companies, docs, sentences


# ---------------------------------------------------------------- set definitions

def test_v1_set_matches_its_frozen_manifest_and_pins_only_its_own_builder():
    manifest = frozen_manifest(V1)
    assert (V1.version, V1.seed, V1.commit, V1.commit_role) == (
        manifest["version"], manifest["seed"], manifest["commit"], manifest["commit_role"])
    assert sorted(V1.excluded) == manifest["excluded_tickers"] == sorted(spot.EXCLUDED)
    assert not V1.extra_excluded and V1.extra_excluded_from is None and not V1.variable_week_cap
    assert f"Python Random({V1.seed})," in manifest["procedure"]["selection"]
    assert "exclusions" not in manifest and "reference_set" not in manifest
    # Only the parameterized builder itself is pinned; any edit to the source
    # screening or legacy mutators still changes a recomputed v1 manifest.
    assert dict(V1.frozen_builder_hashes) == {"eval/build_spot500.py": manifest["builder_hashes"]["eval/build_spot500.py"]}
    hashes = spot.builder_hashes(V1)
    assert hashes == manifest["builder_hashes"]
    for path in ("eval/spot500_sources.py", "eval/build_corpus.py"):
        assert hashes[path] == sha((ROOT / path).read_bytes()), f"{path} changed; v1 no longer rebuilds identically"


def test_pinned_v1_builder_hash_is_the_freezing_commit_blob():
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    blob = subprocess.run(["git", "cat-file", "blob", f"{V1_FREEZE_COMMIT}:eval/build_spot500.py"],
                          cwd=ROOT, capture_output=True)
    if blob.returncode:
        pytest.skip("the v1 freeze commit is not in this clone's history")
    assert sha(blob.stdout) == dict(V1.frozen_builder_hashes)["eval/build_spot500.py"]


def test_v2_changes_only_the_seed_and_excludes_every_v1_company():
    manifest = frozen_manifest(V1)
    v1_companies = set(manifest["selection"]["selected_companies"])
    assert v1_companies == {row["ticker"] for row in manifest["company_list"]} == set(spot.V1_COMPANIES)
    assert len(v1_companies) == 30 and not v1_companies & spot.EXCLUDED
    assert V2.seed == 20261102 != V1.seed
    assert V2.excluded == spot.EXCLUDED | spot.V1_COMPANIES and len(V2.excluded) == 41
    assert V2.extra_excluded_from == "v1" and V2.variable_week_cap
    assert (V2.version, V2.corpus, V2.manifest) == ("spot500_v2", "corpus/spot500_v2.jsonl", "spot500_v2_manifest.json")
    assert not V2.frozen_builder_hashes


def test_defaults_are_the_v1_set():
    companies, docs, sentences = pool()
    assert spot.select_companies(companies, docs, sentences) == spot.select_companies(
        companies, docs, sentences, seed=V1.seed, excluded=V1.excluded, variable_week_cap=False)
    source = sentence("EXCO")
    default = spot.make_item(source, DOC, {"name": "Example Co", "sub_industry": "Machinery"})
    assert default["item_id"] == "spot500-v1-EXCO-FY2026Q3-s000001" and default["corpus_version"] == "spot500_v1"


def test_v2_items_differ_from_v1_items_only_in_their_set_label():
    source, company = sentence("EXCO"), {"name": "Example Co", "sub_industry": "Machinery"}
    mutation = spot.mutate_direction(source, DOC, {})
    for kwargs in ({}, {"mutation": mutation}):
        first = spot.make_item(source, DOC, company, **kwargs)
        second = spot.make_item(source, DOC, company, version="spot500_v2", **kwargs)
        assert second["item_id"] == first["item_id"].replace("spot500-v1-", "spot500-v2-", 1)
        assert second["corpus_version"] == "spot500_v2"
        labels = ("item_id", "original_item_id", "corpus_version")
        assert {k: v for k, v in first.items() if k not in labels} == {k: v for k, v in second.items() if k not in labels}
        if kwargs:
            assert second["original_item_id"] == "spot500-v2-EXCO-FY2026Q3-s000001"


# ---------------------------------------------------------------- selection

def test_extra_exclusions_leave_the_pool_before_sampling():
    companies, docs, sentences = pool()
    first, _ = spot.select_companies(companies, docs, sentences)
    excluded = spot.EXCLUDED | set(first)
    second, metadata = spot.select_companies(companies, docs, sentences, seed=V2.seed, excluded=excluded,
                                             variable_week_cap=True)
    assert len(second) == 30 and not set(second) & set(first)
    assert metadata["eligible_company_count"] == len(companies) - 30
    assert all(not set(r["selected"]) & set(first) for r in metadata["reservations"])
    assert sum(spot.is_bank(companies[t]) for t in second) >= 3
    assert sum(spot.is_utility_reit(companies[t]) for t in second) >= 2


def test_seed_changes_the_sample_but_each_seed_is_repeatable():
    companies, docs, sentences = pool()
    v1 = spot.select_companies(companies, docs, sentences, seed=V1.seed)
    v2 = spot.select_companies(companies, docs, sentences, seed=V2.seed)
    assert v1 != v2
    assert v2 == spot.select_companies(dict(reversed(list(companies.items()))), docs, list(reversed(sentences)),
                                       seed=V2.seed)


def test_variable_week_quota_is_strict_unless_capped_and_capped_only_when_exhausted():
    companies, docs, sentences = pool()
    # Two or more eligible: the cap changes nothing.
    assert spot.select_companies(companies, docs, sentences, variable_week_cap=True) == spot.select_companies(
        companies, docs, sentences)
    # As in v2: AZO and KR are excluded, COST and DPZ have no eligible sentences.
    companies, docs, sentences = pool(without_sentences={"COST", "DPZ"})
    excluded = spot.EXCLUDED | {"AZO", "KR"}
    with pytest.raises(ValueError, match="variable-week"):
        spot.select_companies(companies, docs, sentences, excluded=excluded)
    selected, metadata = spot.select_companies(companies, docs, sentences, excluded=excluded, variable_week_cap=True)
    quota = next(r for r in metadata["reservations"] if r["constraint"] == "variable-week companies")
    assert quota == {"constraint": "variable-week companies", "minimum": 1, "selected": ["PEP"]}
    assert set(selected) & spot.IRREGULAR == {"PEP"}


# ---------------------------------------------------------------- reference set and CLI

def test_reference_set_checks_the_frozen_v1_files(tmp_path, monkeypatch):
    (tmp_path / "eval/corpus").mkdir(parents=True)
    corpus = b'{"item_id":"x"}\n'
    (tmp_path / "eval" / V1.corpus).write_bytes(corpus)
    manifest = {"version": "spot500_v1", "seed": V1.seed, "corpus_sha256": sha(corpus),
                "selection": {"selected_companies": sorted(spot.V1_COMPANIES)}}
    path = tmp_path / "eval" / V1.manifest
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(spot, "ROOT", tmp_path)
    assert spot.reference_set(V2) == {"version": "spot500_v1", "seed": V1.seed,
                                      "manifest": "eval/spot500_manifest.json", "manifest_sha256": sha(path.read_bytes()),
                                      "corpus": "eval/corpus/spot500_v1.jsonl", "corpus_sha256": sha(corpus)}
    path.write_text(json.dumps({**manifest, "selection": {"selected_companies": sorted(spot.V1_COMPANIES)[1:]}}))
    with pytest.raises(ValueError, match="Extra exclusions"):
        spot.reference_set(V2)
    path.write_text(json.dumps({**manifest, "corpus_sha256": sha(b"other")}))
    with pytest.raises(ValueError, match="files changed"):
        spot.reference_set(V2)


@pytest.mark.parametrize("argv,spot_set", [([], V1), (["--set", "v1"], V1), (["--set", "v2"], V2)])
def test_builder_cli_writes_each_set_to_its_own_frozen_paths(tmp_path, monkeypatch, argv, spot_set):
    calls = []

    def fake_build(data_root, chosen):
        calls.append(chosen)
        return b"corpus\n", b'{"counts": {}}\n'
    monkeypatch.setattr(spot, "build", fake_build)
    args = ["--data-root", str(tmp_path / "data"), "--output-dir", str(tmp_path / "out"), *argv]
    assert spot.main(args) == 0
    assert calls == [spot_set]
    assert sorted(str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").rglob("*") if p.is_file()) == sorted(
        [spot_set.corpus, spot_set.manifest])
    assert spot.main(args) == 0  # an identical rebuild is accepted
    monkeypatch.setattr(spot, "build", lambda data_root, chosen: (b"changed\n", b'{"counts": {}}\n'))
    with pytest.raises(ValueError, match="Refusing changed frozen output"):
        spot.main(args)


def test_builder_cli_rejects_an_unknown_set(tmp_path):
    with pytest.raises(SystemExit):
        spot.main(["--data-root", str(tmp_path), "--set", "v3"])


# ---------------------------------------------------------------- committed v2 freeze

def test_frozen_v2_files_are_consistent_and_disjoint_from_v1():
    raw = (ROOT / "eval" / V2.corpus).read_bytes()
    manifest, items = frozen_manifest(V2), frozen_items(V2)
    v1_manifest, v1_items = frozen_manifest(V1), frozen_items(V1)
    assert manifest["corpus_sha256"] == sha(raw)
    assert manifest["items"] == [{"item_id": it["item_id"], "sha256": sha(spot.canonical(it))} for it in items]
    assert manifest["version"] == "spot500_v2" and manifest["seed"] == V2.seed
    assert {it["corpus_version"] for it in items} == {"spot500_v2"}
    assert all(it["item_id"].startswith("spot500-v2-") for it in items)
    companies = set(manifest["selection"]["selected_companies"])
    assert len(companies) == manifest["counts"]["companies"] == 30
    assert companies == {it["ticker"] for it in items} == {row["ticker"] for row in manifest["company_list"]}
    assert not companies & (spot.EXCLUDED | spot.V1_COMPANIES)
    assert manifest["excluded_tickers"] == sorted(V2.excluded)
    assert manifest["exclusions"]["spot500_v1_companies"] == sorted(v1_manifest["selection"]["selected_companies"])
    assert not {it["item_hash"] for it in items} & {it["item_hash"] for it in v1_items}
    assert not {it["source"]["sentence_id"] for it in items} & {it["source"]["sentence_id"] for it in v1_items}
    mutators = manifest["counts"]["by_mutator"]
    assert {m: mutators[m] for m in spot.MUTATORS} == {m: 5 for m in spot.MUTATORS}
    assert mutators["forward_looking_control"] == 5 and 60 <= len(items) <= 80
    for item in items:
        for gold in item["gold_claims"]:
            assert item["text"][slice(*gold["span"])] == gold["value_text"]
    # Same frame and screening as v1; the earlier set is pinned by its hashes.
    assert manifest["source_pool_sha256"] == v1_manifest["source_pool_sha256"]
    assert manifest["sources"]["indexes"] == v1_manifest["sources"]["indexes"]
    assert manifest["sources"]["universe"] == v1_manifest["sources"]["universe"]
    assert manifest["reference_set"]["manifest_sha256"] == sha((ROOT / "eval" / V1.manifest).read_bytes())
    assert manifest["reference_set"]["corpus_sha256"] == sha((ROOT / "eval" / V1.corpus).read_bytes())
    for key in ("quarters", "items", "outputs_used_for_selection", "human_review"):
        assert manifest["procedure"][key] == v1_manifest["procedure"][key]
    assert manifest["procedure"]["selection"] == v1_manifest["procedure"]["selection"].replace(
        f"Random({V1.seed})", f"Random({V2.seed})")
    assert manifest["cost_estimate"]["total_usd"] == len(items) * manifest["cost_estimate"]["per_item_usd"] < 1
    # The committed builder froze v2. Editing it needs a pinned v2 builder hash, as v1 has.
    assert manifest["builder_hashes"] == spot.builder_hashes(V2), "builder changed after the v2 freeze"


def test_committed_v2_passes_the_runner_freeze_and_the_oracle_scores_by_construction():
    manifest, items = spot500.load_items("v2")
    assert manifest["corpus_sha256"] == sha(spot500.V2_CORPUS.read_bytes())
    total = spot500.score(items, {it["item_id"]: {"pairs": spot500.oracle_pairs(it)} for it in items})["total"]
    assert total["status_and_reason_correct"] == total["gold_claims"] == manifest["counts"]["gold_claims"]
    assert total["caught_errors"] == total["expected_reasons"] == total["target_claims"] == 35
    assert total["false_alarm_original_items"] == total["verified_guidance"] == total["extra_claims"] == 0


@pytest.mark.skipif(not os.environ.get("SPOT500_DATA_ROOT"),
                    reason="set SPOT500_DATA_ROOT to the source data root for the full rebuild (about two minutes)")
def test_full_source_rebuild_reproduces_v1_and_v2_byte_for_byte():
    from eval.spot500_sources import load_sources

    sources = load_sources(Path(os.environ["SPOT500_DATA_ROOT"]))
    for spot_set in (V1, V2):
        raw, manifest = spot.build_from_sources(*deepcopy(sources), spot=spot_set)
        assert raw == (ROOT / "eval" / spot_set.corpus).read_bytes(), spot_set.name
        assert manifest == (ROOT / "eval" / spot_set.manifest).read_bytes(), spot_set.name


# ---------------------------------------------------------------- runner

def item(name, *, version="spot500_v2", role="true", mutator="none", status="verified", reason="match"):
    text = f"{name} revenue was $2 billion."
    start = text.index("$2")
    return {"item_id": f"{version.replace('_', '-')}-{name}", "corpus_version": version,
            "item_hash": spot500.digest(name.encode()), "text": text, "context": "Example Q2 FY2026", "as_of": None,
            "mutator": mutator, "features": ["revenue"], "gold_claims": [
                {"value_text": "$2 billion", "span": [start, start + 10], "basis": "GAAP", "metric": "revenue",
                 "role": role, "expected_status": status, "expected_reason": reason}]}


def freeze(directory, rows, version="spot500_v2"):
    directory.mkdir(parents=True, exist_ok=True)
    corpus, manifest = directory / "corpus.jsonl", directory / "manifest.json"
    corpus.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest.write_text(json.dumps({"version": version, "corpus_sha256": spot500.file_digest(corpus), "items": [
        {"item_id": row["item_id"], "sha256": spot500.json_digest(row)} for row in rows]}))
    return corpus, manifest


@pytest.fixture
def v2_frozen(tmp_path, monkeypatch):
    rows = [item("original"), item("mutant", role="target", mutator="value", status="wrong", reason="value_mismatch"),
            item("guidance", role="guidance", mutator="forward_looking_control", status="unverifiable",
                 reason="forward_looking")]
    corpus, manifest = freeze(tmp_path / "frozen", rows)
    runs = tmp_path / "spot500_runs"
    runs.mkdir()
    monkeypatch.setattr(spot500, "V2_CORPUS", corpus)
    monkeypatch.setattr(spot500, "V2_MANIFEST", manifest)
    monkeypatch.setattr(spot500, "RUNS", runs)
    return rows, runs


def test_runner_defaults_to_v1_and_passes_the_set_through(monkeypatch, tmp_path):
    assert spot500.set_files() == (spot500.CORPUS, spot500.MANIFEST)
    assert spot500.set_files("v2") == (spot500.V2_CORPUS, spot500.V2_MANIFEST)
    with pytest.raises(ValueError, match="Unknown"):
        spot500.set_files("v3")
    seen = []

    def fake_load(spot_set="v1"):
        seen.append(("load", spot_set))
        return {}, []

    def fake_run(items, output, *, stub, evidence_dir, budget, spot_set="v1"):
        seen.append(("run", spot_set))
        return {"summary": {"total": {"gold_claims": 0}}, "cost_usd": 0, "estimated_cost_usd": 0,
                "inputs_unchanged": True}
    monkeypatch.setattr(spot500, "load_items", fake_load)
    monkeypatch.setattr(spot500, "run", fake_run)
    assert spot500.main(["--stub", "oracle", "--output", str(tmp_path / "o.json")]) == 0
    assert spot500.main(["--set", "v2", "--stub", "oracle", "--output", str(tmp_path / "o.json")]) == 0
    assert seen == [("load", "v1"), ("run", "v1"), ("load", "v2"), ("run", "v2")]
    with pytest.raises(SystemExit):
        spot500.main(["--set", "v3", "--stub", "oracle", "--output", str(tmp_path / "o.json")])


def test_v2_load_refuses_files_labelled_as_another_set(v2_frozen, tmp_path, monkeypatch):
    rows, _ = v2_frozen
    assert [row["item_id"] for row in spot500.load_items("v2")[1]] == [row["item_id"] for row in rows]
    v1_rows = [item("original", version="spot500_v1")]
    corpus, manifest = freeze(tmp_path / "v1-labelled", v1_rows, version="spot500_v1")
    monkeypatch.setattr(spot500, "V2_CORPUS", corpus)
    monkeypatch.setattr(spot500, "V2_MANIFEST", manifest)
    with pytest.raises(ValueError, match="not labelled spot500_v2"):
        spot500.load_items("v2")
    # The v1 freeze checks are unchanged: they never required a set label.
    monkeypatch.setattr(spot500, "CORPUS", corpus)
    monkeypatch.setattr(spot500, "MANIFEST", manifest)
    assert spot500.load_items()[1] == v1_rows


@pytest.mark.parametrize("name,stub", [
    ("oracle-20261101.json", True), ("v2-live-20261101.json", True), ("v1-oracle-20261101.json", True),
    ("v2-oracle-20261101.txt", True), ("v2-oracle-20261101.json", False), ("live-20261102.json", False),
])
def test_v2_output_must_be_a_set_named_record_under_spot500_runs(v2_frozen, name, stub, monkeypatch):
    rows, runs = v2_frozen
    monkeypatch.setenv("MODEL_API_KEY", "SYNTHETIC_TEST_KEY")
    with pytest.raises(ValueError, match="must be eval/spot500_runs/v2-"):
        spot500.run(rows, runs / name, stub=stub, evidence_dir=runs, budget=.95, spot_set="v2")
    assert list(runs.iterdir()) == []


def test_v2_output_outside_spot500_runs_is_refused_before_any_file(v2_frozen, tmp_path):
    rows, runs = v2_frozen
    elsewhere = tmp_path / "elsewhere" / "v2-oracle-20261101.json"
    with pytest.raises(ValueError, match="must be eval/spot500_runs/"):
        spot500.run(rows, elsewhere, stub=True, evidence_dir=runs, budget=.95, spot_set="v2")
    assert not elsewhere.parent.exists() and list(runs.iterdir()) == []


@pytest.mark.parametrize("earlier", ["v2-live-20261102.json", "v2-live-20261102.run"])
def test_v2_live_run_is_refused_after_any_earlier_live_record(v2_frozen, monkeypatch, earlier):
    from receipts import extract as ex

    rows, runs = v2_frozen
    if earlier.endswith(".run"):
        (runs / earlier).mkdir()
    else:
        (runs / earlier).write_text("{}")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(ex, "make_client", lambda **kwargs: pytest.fail("Client created"))
    before = sorted(p.name for p in runs.iterdir())
    with pytest.raises(FileExistsError, match="single use"):
        spot500.run(rows, runs / "v2-live-20261103.json", stub=False, evidence_dir=runs, budget=.95, spot_set="v2")
    assert sorted(p.name for p in runs.iterdir()) == before


def test_v2_oracle_runs_offline_records_its_set_and_refuses_reuse(v2_frozen, monkeypatch):
    from receipts import extract as ex

    rows, runs = v2_frozen
    for name in ("make_client", "settings_env", "_load_api_key"):
        monkeypatch.setattr(ex, name, lambda *args, **kwargs: pytest.fail("provider or dotenv access"))
    output = runs / "v2-oracle-20261101.json"
    result = spot500.run(rows, output, stub=True, evidence_dir=runs / "absent", budget=.95, spot_set="v2")
    total = result["summary"]["total"]
    assert total["status_and_reason_correct"] == total["gold_claims"] == 3
    assert result["set"] == "v2" and result["inputs_unchanged"] and result["cost_usd"] == 0
    assert result["corpus_sha256"] == spot500.file_digest(spot500.V2_CORPUS)
    assert result["manifest_sha256"] == spot500.file_digest(spot500.V2_MANIFEST)
    assert json.loads(output.with_suffix(".run").joinpath("started.json").read_text())["set"] == "v2"
    with pytest.raises(FileExistsError):
        spot500.run(rows, output, stub=True, evidence_dir=runs, budget=.95, spot_set="v2")
    # An oracle record never counts as the one live run.
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Export MODEL_API_KEY"):
        spot500.run(rows, runs / "v2-live-20261102.json", stub=False, evidence_dir=runs, budget=.95, spot_set="v2")


def test_v1_runs_keep_their_unrestricted_paths_and_record_format(tmp_path, monkeypatch):
    rows = [item("original", version="spot500_v1")]
    corpus, manifest = freeze(tmp_path / "v1", rows, version="spot500_v1")
    monkeypatch.setattr(spot500, "CORPUS", corpus)
    monkeypatch.setattr(spot500, "MANIFEST", manifest)
    result = spot500.run(rows, tmp_path / "anywhere.json", stub=True, evidence_dir=tmp_path, budget=.95)
    assert "set" not in result and result["corpus_sha256"] == spot500.file_digest(corpus)
