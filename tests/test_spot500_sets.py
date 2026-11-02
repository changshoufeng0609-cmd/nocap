"""Versioned spot500 sets: v1 and v2 stay reproducible; later sets change only seed, exclusions and exhausted quotas.

Source-only checks; no verifier, extraction or provider is involved. The full
source rebuild needs the gitignored SEC archive and runs only when
SPOT500_DATA_ROOT names it (about seven minutes).
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from eval import build_corpus as base
from eval import build_spot500 as spot
from eval import spot500

ROOT = Path(__file__).resolve().parents[1]
V1, V2, V3, FULL, FULL2 = (spot.SETS[name] for name in ("v1", "v2", "v3", "full", "full2"))
FREEZE_COMMITS = {"v1": "050be2e5fe97f9920308e9d63643b7ee8c9771c7", "v2": "7bdcb11ccb371e897e58ed710dd58ee68a267162",
                  "v3": "02d6e41f7f140c54db2e04fa03a5a0bbf9544576"}
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


@pytest.mark.parametrize("spot_set", [V1, V2, V3], ids=["v1", "v2", "v3"])
def test_pinned_builder_hash_is_the_freezing_commit_blob(spot_set):
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    blob = subprocess.run(["git", "cat-file", "blob", f"{FREEZE_COMMITS[spot_set.name]}:eval/build_spot500.py"],
                          cwd=ROOT, capture_output=True)
    if blob.returncode:
        pytest.skip(f"the {spot_set.name} freeze commit is not in this clone's history")
    pinned = dict(spot_set.frozen_builder_hashes)["eval/build_spot500.py"]
    assert sha(blob.stdout) == pinned == frozen_manifest(spot_set)["builder_hashes"]["eval/build_spot500.py"]


def test_v2_changes_only_the_seed_and_excludes_every_v1_company():
    manifest = frozen_manifest(V1)
    v1_companies = set(manifest["selection"]["selected_companies"])
    assert v1_companies == {row["ticker"] for row in manifest["company_list"]} == set(spot.V1_COMPANIES)
    assert len(v1_companies) == 30 and not v1_companies & spot.EXCLUDED
    assert V2.seed == 20261102 != V1.seed
    assert V2.excluded == spot.EXCLUDED | spot.V1_COMPANIES and len(V2.excluded) == 41
    assert V2.extra_excluded_from == "v1" and V2.variable_week_cap
    assert (V2.version, V2.corpus, V2.manifest) == ("spot500_v2", "corpus/spot500_v2.jsonl", "spot500_v2_manifest.json")
    # The builder changed after the v2 freeze, so v2 now pins its freezing builder too, exactly as v1 does.
    assert dict(V2.frozen_builder_hashes) == {
        "eval/build_spot500.py": frozen_manifest(V2)["builder_hashes"]["eval/build_spot500.py"]}
    assert spot.builder_hashes(V2) == frozen_manifest(V2)["builder_hashes"]


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
        spot.main(["--data-root", str(tmp_path), "--set", "v9"])


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
                    reason="set SPOT500_DATA_ROOT to the source data root for the full rebuild (about seven minutes)")
def test_full_source_rebuild_reproduces_every_set_byte_for_byte():
    from eval.spot500_sources import load_sources

    sources = load_sources(Path(os.environ["SPOT500_DATA_ROOT"]))
    for spot_set in (V1, V2, V3):
        raw, manifest = spot.build_from_sources(*deepcopy(sources), spot=spot_set)
        assert raw == (ROOT / "eval" / spot_set.corpus).read_bytes(), spot_set.name
        assert manifest == (ROOT / "eval" / spot_set.manifest).read_bytes(), spot_set.name
    # The full set's frame also screens the original development companies.
    raw, manifest = spot.build(Path(os.environ["SPOT500_DATA_ROOT"]), FULL)
    assert raw == (ROOT / "eval" / FULL.corpus).read_bytes()
    assert manifest == (ROOT / "eval" / FULL.manifest).read_bytes()
    # full2 also reads the releases labelled EX-99.01 or a single plain EX-99.
    raw, manifest = spot.build(Path(os.environ["SPOT500_DATA_ROOT"]), FULL2)
    assert raw == (ROOT / "eval" / FULL2.corpus).read_bytes()
    assert manifest == (ROOT / "eval" / FULL2.manifest).read_bytes()


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
        spot500.set_files("v9")
    seen = []

    def fake_load(spot_set="v1"):
        seen.append(("load", spot_set))
        return {}, []

    def fake_run(items, output, *, stub, evidence_dir, budget, spot_set="v1", concurrency=1):
        seen.append(("run", spot_set, concurrency))
        return {"summary": {"total": {"gold_claims": 0}}, "cost_usd": 0, "estimated_cost_usd": 0,
                "inputs_unchanged": True}
    monkeypatch.setattr(spot500, "load_items", fake_load)
    monkeypatch.setattr(spot500, "run", fake_run)
    assert spot500.main(["--stub", "oracle", "--output", str(tmp_path / "o.json")]) == 0
    assert spot500.main(["--set", "v2", "--stub", "oracle", "--output", str(tmp_path / "o.json")]) == 0
    assert spot500.main(["--set", "full", "--stub", "oracle", "--concurrency", "4",
                         "--output", str(tmp_path / "o.json")]) == 0
    assert seen == [("load", "v1"), ("run", "v1", 1), ("load", "v2"), ("run", "v2", 1),
                    ("load", "full"), ("run", "full", 4)]
    for bad in ("0", "5"):
        with pytest.raises(SystemExit):
            spot500.main(["--stub", "oracle", "--concurrency", bad, "--output", str(tmp_path / "o.json")])
    with pytest.raises(SystemExit):
        spot500.main(["--set", "v9", "--stub", "oracle", "--output", str(tmp_path / "o.json")])


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


# ---------------------------------------------------------------- v3: fresh companies for the fixed verifier

def derivation_record():
    return json.loads((ROOT / "eval" / V3.derivation[0]).read_bytes())


def test_v3_excludes_both_earlier_sets_and_every_derived_company():
    from eval import spot500_exclusions as exclusions

    groups = {key: companies for key, companies, _ in V3.exclusion_groups}
    record = derivation_record()
    assert V3.seed == 20261103 and V3.cap_quotas and V3.previous_sets == ("v1", "v2")
    assert groups["spot500_v1_companies"] == set(frozen_manifest(V1)["selection"]["selected_companies"])
    assert groups["spot500_v2_companies"] == set(frozen_manifest(V2)["selection"]["selected_companies"])
    assert groups["verifier_fix_test_companies"] == set(record["verifier_fix_tests"]["companies"])
    assert groups["verifier_fix_code_companies"] == set(record["verifier_fix_code"]["companies"])
    assert groups["reviewer_probe_companies"] == {"BAX", "CSGP", "TGT"} == set(exclusions.PROBES)
    assert V3.excluded == spot.EXCLUDED.union(*groups.values()) and len(V3.excluded) == 95
    assert sha((ROOT / "eval" / V3.derivation[0]).read_bytes()) == V3.derivation[1]
    # The test scan covers every test file added since v1's live run except the spot500 set tests.
    tests = record["verifier_fix_tests"]
    assert tests["listed"] == ["tests/test_spot500_sets.py", "tests/test_wording_gaps.py"]
    assert [row["path"] for row in tests["scanned"]] == ["tests/test_wording_gaps.py"]
    # The builder changed after the v3 freeze, so v3 pins its freezing builder too, as v1 and v2 do.
    assert spot.builder_hashes(V3) == frozen_manifest(V3)["builder_hashes"]


def test_exclusion_derivation_reproduces_from_git_history():
    from eval import spot500_exclusions as exclusions

    record = derivation_record()
    rows, table = exclusions.companies_table(ROOT / "data/snapshot/facts.sqlite")
    if table != record["companies_table"]:
        pytest.skip("the snapshot companies table changed after the v3 derivation")
    if shutil.which("git") is None or subprocess.run(
            ["git", "cat-file", "-e", f"{exclusions.UNTIL}^{{commit}}"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("the derivation range is not in this clone's history")
    assert exclusions.encode(exclusions.derive(ROOT / "data/snapshot/facts.sqlite")) == (
        ROOT / "eval" / V3.derivation[0]).read_bytes()


ROWS = [("A", "AGILENT TECHNOLOGIES, INC.", ["a", "agilent technologies"]),
        ("AXP", "AMERICAN EXPRESS CO", ["american express", "axp"]),
        ("AEP", "AMERICAN ELECTRIC POWER CO INC", ["american electric power", "aep"]),
        ("GE", "GENERAL ELECTRIC CO", ["ge", "ge aerospace", "general electric"]),
        ("GEHC", "GE HealthCare Technologies Inc.", ["ge healthcare", "gehc"]),
        ("MMM", "3M CO", ["3m", "mmm"]),
        ("TGT", "TARGET CORP", ["target", "tgt"]),
        ("VLO", "VALERO ENERGY CORP/TX", ["valero energy", "vlo"])]


@pytest.mark.parametrize("line,named,rejected", [
    ("Valero's net income rose.", {"VLO": {"leading word"}}, {}),
    ("valero energy as a lowercase phrase", {}, {"VLO": {"name without a capital letter"}}),
    ("American Express raised its outlook.", {"AXP": {"alias"}}, {}),
    ("American companies", {}, {}),  # a leading word shared by two companies never counts
    ("Revenue was $8,810.3M.", {}, {"MMM": {"name inside a number"}}),
    ("3M reported sales.", {"MMM": {"alias"}}, {}),
    ("A bank reported.", {}, {"A": {"one-letter ticker outside quotes or parentheses"}}),
    ('entity="A"', {"A": {"ticker"}}, {}),
    ("Agilent (A) reported.", {"A": {"leading word", "ticker"}}, {}),
    ("GE HealthCare reported.", {"GEHC": {"alias"}}, {"GE": {"ticker inside another company's name (GEHC)"}}),
    ('TGT_10Q = "0000027419-26-000042"', {"TGT": {"ticker"}}, {}),
    ("the mutation target and AXPX", {}, {"TGT": {"name without a capital letter"}}),
])
def test_scanner_rules(line, named, rejected):
    from eval import spot500_exclusions as exclusions

    found, missed = exclusions.Scanner(ROWS).scan([("x.py", 1, line)])
    assert {t: set(ev) for t, ev in found.items()} == named
    assert {t: set(ev) for t, ev in missed.items()} == rejected


def test_derivation_reads_added_lines_with_their_new_line_numbers():
    from eval import spot500_exclusions as exclusions

    diff = ("diff --git a/receipts/x.py b/receipts/x.py\n--- a/receipts/x.py\n+++ b/receipts/x.py\n"
            "@@ -3,0 +4,2 @@ def f():\n+# Valero's row\n++++ not a header\n"
            "diff --git a/receipts/gone.py b/receipts/gone.py\n--- a/receipts/gone.py\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-old\n")
    assert list(exclusions.added_lines(diff)) == [("receipts/x.py", 4, "# Valero's row"),
                                                  ("receipts/x.py", 5, "+++ not a header")]


def test_cap_quotas_lower_every_hard_quota_only_when_it_is_exhausted():
    companies, docs, sentences = pool()
    # Enough of every stratum: the cap changes nothing.
    assert spot.select_companies(companies, docs, sentences, cap_quotas=True) == spot.select_companies(
        companies, docs, sentences)
    banks = sorted(t for t in companies if spot.is_bank(companies[t]))
    reits = sorted(t for t in companies if spot.is_utility_reit(companies[t]))
    excluded = spot.EXCLUDED | set(banks[1:]) | set(reits[1:]) | spot.IRREGULAR
    for name in ("banks", "REITs or utilities"):
        with pytest.raises(ValueError, match=name):
            strict_excluded = excluded if name == "banks" else excluded - set(banks)
            spot.select_companies(companies, docs, sentences, excluded=strict_excluded, variable_week_cap=True)
    selected, metadata = spot.select_companies(companies, docs, sentences, excluded=excluded, cap_quotas=True)
    quotas = {r["constraint"]: (r["minimum"], r["selected"]) for r in metadata["reservations"]}
    assert quotas["banks"] == (1, banks[:1]) and quotas["REITs or utilities"] == (1, reits[:1])
    assert quotas["variable-week companies"] == (0, [])
    assert len(selected) == 30 and not set(selected) & excluded


def test_frozen_v3_files_are_consistent_and_disjoint_from_every_exclusion():
    raw = (ROOT / "eval" / V3.corpus).read_bytes()
    manifest, items = frozen_manifest(V3), frozen_items(V3)
    v1_manifest = frozen_manifest(V1)
    assert manifest["corpus_sha256"] == sha(raw)
    assert manifest["items"] == [{"item_id": it["item_id"], "sha256": sha(spot.canonical(it))} for it in items]
    assert manifest["version"] == "spot500_v3" and manifest["seed"] == V3.seed == 20261103
    assert {it["corpus_version"] for it in items} == {"spot500_v3"}
    assert all(it["item_id"].startswith("spot500-v3-") for it in items)
    companies = set(manifest["selection"]["selected_companies"])
    assert len(companies) == manifest["counts"]["companies"] == 30
    assert companies == {it["ticker"] for it in items} == {row["ticker"] for row in manifest["company_list"]}
    assert not companies & V3.excluded and manifest["excluded_tickers"] == sorted(V3.excluded)
    for earlier in (V1, V2):
        earlier_items = frozen_items(earlier)
        assert not {it["item_hash"] for it in items} & {it["item_hash"] for it in earlier_items}
        assert not {it["source"]["sentence_id"] for it in items} & {it["source"]["sentence_id"] for it in earlier_items}
    # The exclusions exhausted two strata; both deviations are recorded where they apply.
    mutators = manifest["counts"]["by_mutator"]
    assert {m: mutators[m] for m in spot.MUTATORS} == {**{m: 5 for m in spot.MUTATORS}, "basis": 2}
    assert mutators["forward_looking_control"] == 5 and 60 <= len(items) <= 80
    assert manifest["selection"]["mutant_shortfalls"] == {"basis": {"quota": 5, "taken": 2, "candidates": 2}}
    quotas = {r["constraint"]: r["minimum"] for r in manifest["selection"]["reservations"]}
    assert (quotas["banks"], quotas["REITs or utilities"], quotas["variable-week companies"]) == (3, 2, 0)
    changes = manifest["procedure"]["changes_from_spot500_v1"]
    assert changes[0] == "seed 20261101 -> 20261103" and "95 distinct companies in all" in changes[1]
    assert changes[2:] == [
        "variable-week quota lowered from 2 to 0, the number of eligible variable-week companies left after the exclusions",
        "basis mutants lowered from 5 to 2: the sampled companies have 2 basis candidates after the exclusions"]
    for item in items:
        for gold in item["gold_claims"]:
            assert item["text"][slice(*gold["span"])] == gold["value_text"]
    # Same frame and screening as v1; the earlier sets and the derivation are pinned by hash.
    assert manifest["source_pool_sha256"] == v1_manifest["source_pool_sha256"]
    assert manifest["sources"]["indexes"] == v1_manifest["sources"]["indexes"]
    assert [ref["version"] for ref in manifest["reference_sets"]] == ["spot500_v1", "spot500_v2"]
    for ref, earlier in zip(manifest["reference_sets"], (V1, V2)):
        assert ref["manifest_sha256"] == sha((ROOT / "eval" / earlier.manifest).read_bytes())
        assert ref["corpus_sha256"] == sha((ROOT / "eval" / earlier.corpus).read_bytes())
    assert manifest["exclusion_derivation"] == {"file": "eval/spot500_v3_exclusions.json", "sha256": V3.derivation[1],
                                                "record": derivation_record()}
    for key, companies_, _ in V3.exclusion_groups:
        assert manifest["exclusions"][key] == sorted(companies_)
    for key in ("quarters", "items", "outputs_used_for_selection", "human_review"):
        assert manifest["procedure"][key] == v1_manifest["procedure"][key]
    assert manifest["cost_estimate"]["total_usd"] == len(items) * manifest["cost_estimate"]["per_item_usd"] < 1
    assert manifest["builder_hashes"] == spot.builder_hashes(V3), "builder changed after the v3 freeze"


def test_committed_v3_passes_the_runner_freeze_and_the_oracle_scores_by_construction():
    manifest, items = spot500.load_items("v3")
    assert manifest["corpus_sha256"] == sha(spot500.V3_CORPUS.read_bytes())
    total = spot500.score(items, {it["item_id"]: {"pairs": spot500.oracle_pairs(it)} for it in items})["total"]
    assert total["status_and_reason_correct"] == total["gold_claims"] == manifest["counts"]["gold_claims"]
    assert total["caught_errors"] == total["expected_reasons"] == total["target_claims"] == 32
    assert total["false_alarm_original_items"] == total["verified_guidance"] == total["extra_claims"] == 0


@pytest.mark.parametrize("spot_set", ["v2", "v3", "full", "full2"])
def test_each_later_set_has_its_own_single_use_live_run(tmp_path, monkeypatch, spot_set):
    rows = [item("original", version=f"spot500_{spot_set}")]
    corpus, manifest = freeze(tmp_path / "frozen", rows, version=f"spot500_{spot_set}")
    runs = tmp_path / "spot500_runs"
    runs.mkdir()
    monkeypatch.setattr(spot500, f"{spot_set.upper()}_CORPUS", corpus)
    monkeypatch.setattr(spot500, f"{spot_set.upper()}_MANIFEST", manifest)
    monkeypatch.setattr(spot500, "RUNS", runs)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert spot500.load_items(spot_set)[1] == rows
    other = "v3" if spot_set == "v2" else "v2"
    (runs / f"{other}-live-20261102.json").write_text("{}")
    # Another set's live record does not use this set's live run; the key check is reached.
    with pytest.raises(ValueError, match="Export MODEL_API_KEY"):
        spot500.run(rows, runs / f"{spot_set}-live-20261102.json", stub=False, evidence_dir=runs, budget=.95,
                    spot_set=spot_set)
    (runs / f"{spot_set}-live-20261102.run").mkdir()
    with pytest.raises(FileExistsError, match="single use"):
        spot500.run(rows, runs / f"{spot_set}-live-20261103.json", stub=False, evidence_dir=runs, budget=.95,
                    spot_set=spot_set)
    with pytest.raises(ValueError, match=f"must be eval/spot500_runs/{spot_set}-live-"):
        spot500.run(rows, runs / f"{other}-live-20261104.json", stub=False, evidence_dir=runs, budget=.95,
                    spot_set=spot_set)


# ---------------------------------------------------------------- full: every eligible company, about 1,000 items

def full_derivation():
    return json.loads((ROOT / "eval" / FULL.derivation[0]).read_bytes())


def test_full_set_excludes_no_company_and_tags_the_development_ones():
    groups = {key: companies for key, companies, _ in FULL.development_groups}
    record, v3_record = full_derivation(), derivation_record()
    assert FULL.seed == 20261104 and FULL.full and FULL.exclude_sentences_of == ("v1", "v2", "v3")
    assert FULL.excluded == frozenset() and not FULL.exclude_original and not FULL.exclusion_groups
    assert groups["original_development_companies"] == spot.EXCLUDED
    assert groups["spot500_v3_fix_test_companies"] == set(v3_record["verifier_fix_tests"]["companies"])
    assert groups["spot500_v3_fix_code_companies"] == set(v3_record["verifier_fix_code"]["companies"])
    assert groups["verifier_fix_test_companies"] == set(record["verifier_fix_tests"]["companies"])
    assert groups["verifier_fix_code_companies"] == set(record["verifier_fix_code"]["companies"])
    assert groups["reviewer_probe_companies"] == {"ADI", "BAX", "CSGP", "TGT"} == set(record["reviewer_probes"]["companies"])
    assert groups["owner_named_probe_companies"] == {"LNT"}
    assert FULL.in_development == frozenset().union(*groups.values()) and len(FULL.in_development) == 52
    assert sha((ROOT / "eval" / FULL.derivation[0]).read_bytes()) == FULL.derivation[1]
    # The later scan re-reads everything v3's did, so nothing v3 found drops out.
    assert groups["spot500_v3_fix_test_companies"] <= groups["verifier_fix_test_companies"]
    assert groups["spot500_v3_fix_code_companies"] <= groups["verifier_fix_code_companies"]
    found = groups["verifier_fix_test_companies"] | groups["verifier_fix_code_companies"]
    later = {"VLO", "HPE", "MRK", "CARR", "PFE", "FDS", "CMCSA", "GEHC", "MSI", "INTC", "WMT", "WSM", "FRT", "ITW",
             "SNA", "DAL", "IVZ"}
    assert later <= found and "LNT" not in found  # the owner names Alliant; no scanned line does


def test_full_exclusion_derivation_reproduces_from_git_history():
    from eval import spot500_exclusions as exclusions

    record = full_derivation()
    _, table = exclusions.companies_table(ROOT / "data/snapshot/facts.sqlite")
    if table != record["companies_table"]:
        pytest.skip("the snapshot companies table changed after the full derivation")
    if shutil.which("git") is None or subprocess.run(
            ["git", "cat-file", "-e", f"{exclusions.DERIVATIONS['full'][1]}^{{commit}}"], cwd=ROOT,
            capture_output=True).returncode:
        pytest.skip("the derivation range is not in this clone's history")
    assert exclusions.encode(exclusions.derive(ROOT / "data/snapshot/facts.sqlite", "full")) == (
        ROOT / "eval" / FULL.derivation[0]).read_bytes()


def test_superseded_first_freeze_is_recorded_and_matches_git():
    manifest = frozen_manifest(FULL)
    assert manifest["superseded"] == spot.FULL_SUPERSEDED
    assert manifest["superseded"]["note"] == (
        "the first freeze was superseded before any run, at the owner's request, to include every company")
    assert manifest["superseded"]["corpus_sha256"] != manifest["corpus_sha256"]
    for record in (ROOT / "eval/spot500_runs").glob("full-live-*.json"):  # the first freeze never ran live
        assert json.loads(record.read_text()).get("corpus_sha256") != manifest["superseded"]["corpus_sha256"]
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    old = spot.FULL_SUPERSEDED
    blobs = [subprocess.run(["git", "cat-file", "blob", f"{old['commit']}:eval/{path}"], cwd=ROOT, capture_output=True)
             for path in (FULL.corpus, FULL.manifest)]
    if any(blob.returncode for blob in blobs):
        pytest.skip("the first full freeze is not in this clone's history")
    assert [sha(blob.stdout) for blob in blobs] == [old["corpus_sha256"], old["manifest_sha256"]]


def test_frozen_full_set_meets_its_design_and_reuses_nothing():
    raw = (ROOT / "eval" / FULL.corpus).read_bytes()
    manifest, items = frozen_manifest(FULL), frozen_items(FULL)
    scale = spot.FULL_SCALE
    assert manifest["corpus_sha256"] == sha(raw) and manifest["version"] == "spot500_full"
    assert manifest["items"] == [{"item_id": it["item_id"], "sha256": sha(spot.canonical(it))} for it in items]
    assert {it["corpus_version"] for it in items} == {"spot500_full"}
    assert all(it["item_id"].startswith("spot500-full-") for it in items) and len(items) == scale["total"]
    assert manifest["excluded_tickers"] == [] and manifest["exclusions"]["companies"] == []
    companies = set(manifest["selection"]["selected_companies"])
    assert companies == {it["ticker"] for it in items} and len(companies) == manifest["counts"]["companies"] == 391
    # The frame is v1's plus the original development companies (TSLA has no stored pair).
    frame = manifest["frame"]
    assert frame["source_pool_without_original_sha256"] == frozen_manifest(V1)["source_pool_sha256"]
    assert frame["original_companies_added"] == sorted(spot.EXCLUDED - {"TSLA"}) and frame["original_companies_absent"] == ["TSLA"]
    assert spot.EXCLUDED - {"TSLA"} <= companies
    # Company history: exactly one tag per item, from the development list; every absence has a reason.
    for item in items:
        tags = {"company_in_development", "company_fresh"} & set(item["features"])
        assert tags == {"company_in_development" if item["ticker"] in FULL.in_development else "company_fresh"}
    history = manifest["company_history"]
    assert set(history["in_development"]) == FULL.in_development
    assert set(manifest["selection"]["in_development_companies"]) == companies & FULL.in_development
    assert set(history["in_development_absent_from_set"]) == FULL.in_development - companies
    slices = manifest["counts"]["by_company_history"]
    for tag, entry in slices.items():
        tagged = [it for it in items if tag in it["features"]]
        assert entry["items"] == len(tagged) and entry["companies"] == len({it["ticker"] for it in tagged})
        assert entry["gold_claims"] == sum(len(it["gold_claims"]) for it in tagged)
    assert slices["company_in_development"]["items"] + slices["company_fresh"]["items"] == len(items)
    originals = [it for it in items if it["mutator"] == "none"]
    per_company = Counter(it["ticker"] for it in originals)
    assert set(per_company) == companies and max(per_company.values()) <= scale["per_company"]
    mutants = [it for it in items if it["mutator"] in spot.MUTATORS]
    candidates = manifest["selection"]["mutation_candidate_counts"]
    assert Counter(it["mutator"] for it in mutants) == {**{m: scale["mutants"] for m in spot.MUTATORS},
                                                        "basis": candidates["basis"]}
    original_ids = {it["item_id"] for it in originals}
    assert all(it["original_item_id"] in original_ids for it in mutants)
    assert max(Counter(it["original_item_id"] for it in mutants).values()) <= 2
    controls = [it for it in items if it["mutator"] == "forward_looking_control"]
    assert len(controls) == len({it["ticker"] for it in controls}) == scale["controls"]
    # No source sentence of v1, v2 or v3 returns, by record, by id or by identical text.
    earlier_records = {r["sha256"] for s in (V1, V2, V3) for r in frozen_manifest(s)["source_records"] if r["kind"] == "sentence"}
    full_records = {r["sha256"] for r in manifest["source_records"] if r["kind"] == "sentence"}
    assert not full_records & earlier_records
    earlier_items = [it for s in (V1, V2, V3) for it in frozen_items(s)]
    assert not {it["source"]["sentence_id"] for it in items} & {it["source"]["sentence_id"] for it in earlier_items}
    assert not {it["source"]["original_text"] for it in items} & {it["source"]["original_text"] for it in earlier_items}
    assert not {it["item_hash"] for it in items} & {it["item_hash"] for it in earlier_items}
    removed = manifest["sentence_exclusions"]
    assert {v: len(removed[v]) for v in ("spot500_v1", "spot500_v2", "spot500_v3")} == {
        "spot500_v1": 40, "spot500_v2": 39, "spot500_v3": 37}
    # Coverage: recorded counts equal the items', and every short feature says why.
    by_feature = Counter(f for it in items for f in it["features"])
    for feature, entry in manifest["selection"]["feature_coverage"].items():
        assert entry["items"] == by_feature[feature], feature
        assert (entry["items"] >= scale["feature_target"]) != ("short_because" in entry), feature
    short = {f for f, e in manifest["selection"]["feature_coverage"].items() if "short_because" in e}
    changes = manifest["procedure"]["changes_from_spot500_v1"]
    assert all(any(line.startswith(f"{f}: ") for line in changes) for f in short)
    assert any(line.startswith(f"basis mutants lowered from 60 to {candidates['basis']}: ") for line in changes)
    for item in items:
        for gold in item["gold_claims"]:
            assert item["text"][slice(*gold["span"])] == gold["value_text"]
    for ref, earlier in zip(manifest["reference_sets"], (V1, V2, V3)):
        assert ref["manifest_sha256"] == sha((ROOT / "eval" / earlier.manifest).read_bytes())
    assert manifest["company_history_derivation"] == {"file": "eval/spot500_full_exclusions.json",
                                                      "sha256": FULL.derivation[1], "record": full_derivation()}
    assert manifest["cost_estimate"]["total_usd"] == 5.0 <= manifest["cost_estimate"]["estimate_limit_usd"] == 5.5
    assert manifest["builder_hashes"] == spot.builder_hashes(FULL), "builder changed after the full freeze"


def test_committed_full_passes_the_runner_freeze_and_the_oracle_scores_by_construction():
    manifest, items = spot500.load_items("full")
    total = spot500.score(items, {it["item_id"]: {"pairs": spot500.oracle_pairs(it)} for it in items})["total"]
    assert total["status_and_reason_correct"] == total["gold_claims"] == manifest["counts"]["gold_claims"]
    assert total["caught_errors"] == total["expected_reasons"] == total["target_claims"] == sum(
        n for m, n in manifest["counts"]["by_mutator"].items() if m in spot.MUTATORS)
    assert total["false_alarm_original_items"] == total["verified_guidance"] == total["extra_claims"] == 0
    # The runner's by_feature summary yields both company-history slices from the same run.
    result = spot500.score(items, {it["item_id"]: {"pairs": spot500.oracle_pairs(it)} for it in items})
    slices = manifest["counts"]["by_company_history"]
    for tag in ("company_in_development", "company_fresh"):
        assert result["by_feature"][tag]["items"] == slices[tag]["items"]


@pytest.fixture
def full_frozen(tmp_path, monkeypatch):
    def frozen(count=3):
        rows = [item(f"original{i}", version="spot500_full") for i in range(count)]
        corpus, manifest = freeze(tmp_path / f"frozen{count}", rows, version="spot500_full")
        monkeypatch.setattr(spot500, "FULL_CORPUS", corpus)
        monkeypatch.setattr(spot500, "FULL_MANIFEST", manifest)
        return rows
    runs = tmp_path / "spot500_runs"
    runs.mkdir()
    monkeypatch.setattr(spot500, "RUNS", runs)
    return frozen, runs


@pytest.mark.parametrize("budget,allowed", [(6.0, True), (1.5, True), (6.01, False), (0, False), (float("nan"), False)])
def test_full_budget_allowance_is_explicit_and_bounded(full_frozen, budget, allowed):
    frozen, runs = full_frozen
    rows = frozen()
    output = runs / "full-oracle-20261102.json"
    if allowed:
        assert spot500.run(rows, output, stub=True, evidence_dir=runs, budget=budget, spot_set="full")["inputs_unchanged"]
    else:
        with pytest.raises(ValueError, match="at most \\$6"):
            spot500.run(rows, output, stub=True, evidence_dir=runs, budget=budget, spot_set="full")
        assert list(runs.iterdir()) == []


@pytest.mark.parametrize("spot_set", ["v1", "v2", "v3"])
def test_every_other_set_still_refuses_a_cap_of_one_dollar_or_more(tmp_path, monkeypatch, spot_set):
    rows = [item("original", version=f"spot500_{spot_set}")]
    with pytest.raises(ValueError, match="below \\$1"):
        spot500.run(rows, tmp_path / f"{spot_set}-oracle-x.json", stub=True, evidence_dir=tmp_path, budget=1.0,
                    spot_set=spot_set)
    assert not list(tmp_path.iterdir())


def test_full_estimate_limit_and_guard_cap_limit(full_frozen, tmp_path):
    from receipts import extract as ex

    frozen, runs = full_frozen
    rows = frozen(1101)  # 1,101 x $0.005 = $5.505, over the $5.50 estimate limit
    with pytest.raises(ValueError, match="estimate exceeds"):
        spot500.run(rows, runs / "full-oracle-20261102.json", stub=True, evidence_dir=runs, budget=6.0, spot_set="full")
    backend = ex.make_backend("meta")
    key = ex.request_key(ex.build_request("Example revenue was $2 billion.", "Example Q2 FY2026", backend=backend), backend)
    assert spot500.spark_guard(tmp_path / "a.json", backend, {key}, 5.5, cap_limit=6.0).cap_usd == 5.5
    for cap, limit in ((1.5, None), (6.5, 6.0), (0, 6.0)):
        with pytest.raises(ValueError, match="admission cap"):
            spot500.spark_guard(tmp_path / f"b{cap}.json", backend, {key}, cap, cap_limit=limit)


@pytest.mark.parametrize("concurrency", [0, 5, 1.5, True])
def test_concurrency_is_one_to_four_and_checked_before_any_file(full_frozen, concurrency):
    frozen, runs = full_frozen
    with pytest.raises(ValueError, match="Concurrency"):
        spot500.run(frozen(), runs / "full-oracle-20261102.json", stub=True, evidence_dir=runs, budget=.95,
                    spot_set="full", concurrency=concurrency)
    assert list(runs.iterdir()) == []


def test_live_concurrency_keeps_one_dispatch_per_request_and_an_exact_ledger(full_frozen, monkeypatch):
    """Four workers over items where originals and look-ahead mutants share a request; no provider call."""
    import threading
    import time
    from receipts import check as checker
    from receipts import extract as ex
    from receipts import store as stores

    frozen, runs = full_frozen
    rows = frozen(8)
    for i in range(4):  # look-ahead mutants: the same text and context as an original, another cutoff
        rows.append({**rows[i], "item_id": f"spot500-full-original{i}-look_ahead", "mutator": "look_ahead",
                     "as_of": "2026-01-01T00:00:00Z", "item_hash": spot500.digest(f"la{i}".encode())})
    corpus, manifest = freeze(runs.parent / "with-pairs", rows, version="spot500_full")
    monkeypatch.setattr(spot500, "FULL_CORPUS", corpus)
    monkeypatch.setattr(spot500, "FULL_MANIFEST", manifest)
    monkeypatch.setenv("MODEL_API_KEY", "SYNTHETIC_TEST_KEY")
    monkeypatch.setattr(ex, "_load_api_key", lambda *args: pytest.fail("Dotenv read"))
    monkeypatch.setattr(spot500, "evidence_hashes", lambda path: {"facts.sqlite": "synthetic"})
    monkeypatch.setattr(spot500.harness, "install_sdk_hooks", lambda: True)
    monkeypatch.setattr(stores, "Store", lambda db, pr: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ex, "make_client", lambda **kwargs: SimpleNamespace(close=lambda: None))
    by_text = {row["text"]: row for row in rows if row["mutator"] == "none"}
    cache, lock, busy, overlaps, peak = {}, threading.Lock(), Counter(), [], [0]

    def fake_check(text, *, context, backend, cache_dir, extra_cache_dirs, concurrency, **kwargs):
        assert concurrency == 1
        request = ex.build_request(text, context, backend=backend)
        key = ex.request_key(request, backend)
        with lock:
            busy[key] += 1
            overlaps.append(busy[key] > 1)
            peak[0] = max(peak[0], sum(busy.values()))
        try:
            time.sleep(0.05)
            if key not in cache:
                cache[key] = ex.RequestUsage(backend).call(
                    lambda: SimpleNamespace(usage={"input_tokens": 100, "output_tokens": 50}), request)
        finally:
            with lock:
                busy[key] -= 1
        return SimpleNamespace(results=spot500.oracle_pairs(by_text[text]), extraction=SimpleNamespace(calls=[]),
                               warnings=[], coverage={})
    monkeypatch.setattr(checker, "check_detailed", fake_check)
    result = spot500.run(rows, runs / "full-live-20261102.json", stub=False, evidence_dir=runs, budget=6.0,
                         spot_set="full", concurrency=4)
    assert peak[0] > 1 and not any(overlaps)   # concurrent, never the same request twice at once
    assert result["summary"]["total"]["errors"] == 0 and len(result["records"]) == 12
    assert result["metering"]["calls"] == len(cache) == 8 and result["concurrency"] == 4
    assert all(row["status"] == "completed" for row in result["metering"]["attempts"])
    assert result["cost_usd"] == pytest.approx(8 * .0003375) and not result["cost_is_estimate"]


# ---------------------------------------------------------------- full2: the round-8 version, on new sentences

def exhibit(text):
    return f"<html><body><p>{text}</p><p>Example Co reports results.</p></body></html>".encode()


@pytest.mark.parametrize("text,label", [
    ("EX-99.1 Press release", "99.1"), ("EX-99.01", "99.01"), ("Exhibit 99", "99"), ("EX-99 Release", "99"),
    ("Exhibit 99.2 Commentary", "99.2"), ("Quarterly results", None),
])
def test_exhibit_header_label_reads_the_opening_like_the_strict_rule(text, label):
    assert spot.exhibit_header_label(exhibit(text)) == label


@pytest.fixture
def exhibit_archive(tmp_path):
    """Synthetic 8-Ks: (accession, [(filename, opening text)])."""
    import gzip
    filings = {"0001-26-000001": [("ex9901.htm", "EX-99.01 Press release")],
               "0001-26-000002": [("ex99.htm", "EX-99 Press release")],
               "0001-26-000003": [("ex99a.htm", "EX-99 Press release"), ("ex99b.htm", "EX-99 Slides")],
               "0001-26-000004": [("ex991.htm", "EX-99.1 Press release"), ("ex9901.htm", "EX-99.01 Press release")],
               "0001-26-000005": [("release.htm", "Quarterly results")],
               "0001-26-000006": [("ex99.htm", "EX-99 Press release"), ("ex992.htm", "EX-99.2 Commentary")]}
    index, folder = {}, tmp_path / "archive/exhibits"
    folder.mkdir(parents=True)
    for accession, docs in filings.items():
        for name, text in docs:
            url = f"https://www.sec.gov/Archives/edgar/data/1/{accession.replace('-', '')}/{name}"
            key = sha(url.encode())
            (folder / f"{key}.gz").write_bytes(gzip.compress(exhibit(text)))
            index[url] = {"cik": 1, "accession": accession, "key": key, "sha256": sha(exhibit(text))}
    (folder / "index.json").write_text(json.dumps(index))
    return tmp_path, index


def test_fallback_exhibit_rule_reads_ex99_01_and_a_single_plain_ex99_only_without_ex99_1(exhibit_archive):
    from eval import spot500_sources as sources

    root, index = exhibit_archive
    rule = spot.fallback_exhibit_rule(root, sources.is_exhibit_991)
    accepted = {url.rsplit("/", 2)[1][-6:] + "/" + url.rsplit("/", 1)[1]: rule(url, exhibit_text)
                for url, exhibit_text in ((u, spot.gzip.decompress((root / "archive/exhibits" / (r["key"] + ".gz")).read_bytes()))
                                          for u, r in sorted(index.items()))}
    assert accepted == {
        "000001/ex9901.htm": True,    # EX-99.01 read as 99.1
        "000002/ex99.htm": True,      # the 8-K's single EX-99-type exhibit
        "000003/ex99a.htm": False, "000003/ex99b.htm": False,   # two plain EX-99: neither
        "000004/ex991.htm": True, "000004/ex9901.htm": False,   # an EX-99.1 exists: no fallback
        "000005/release.htm": False,  # no label: stays out, as TSLA does
        "000006/ex99.htm": False, "000006/ex992.htm": False}    # EX-99 beside EX-99.2 is not single


def test_history_tag_puts_development_first_then_new_or_seen():
    from dataclasses import replace

    probe = replace(FULL2, development_groups=(("dev", frozenset({"DEV"}), "test"),))
    assert [spot.history_tag(probe, t, frozenset({"DEV", "OLD"})) for t in ("DEV", "OLD", "NEW")] == [
        "company_in_development", "company_seen", "company_new"]
    assert [spot.history_tag(FULL, t) for t in ("AAPL", "ZZZZ")] == ["company_in_development", "company_fresh"]


def test_full2_definition_and_its_pending_derivation():
    seeds = {s.seed for s in (V1, V2, V3, FULL)}
    assert FULL2.seed == 20261105 and FULL2.seed not in seeds
    assert FULL2.full and FULL2.exhibit_fallback and FULL2.history_seen and not FULL2.exclude_original
    assert FULL2.exclude_sentences_of == ("v1", "v2", "v3", "full") and FULL2.excluded == frozenset()
    groups = {key: companies for key, companies, _ in FULL2.development_groups}
    assert groups["spot500_full_in_development"] == set(frozen_manifest(FULL)["company_history"]["in_development"])
    assert spot.SETS["full"].in_development == groups["spot500_full_in_development"]
    if not FULL2.derivation:
        with pytest.raises(ValueError, match="pending"):
            spot.full2_history_sources(FULL2)
    else:
        assert sha((ROOT / "eval" / FULL2.derivation[0]).read_bytes()) == FULL2.derivation[1]


def test_full2_derivation_scans_added_test_files_and_lines_added_to_existing_ones(monkeypatch):
    from eval import spot500_exclusions as exclusions

    monkeypatch.setattr(exclusions, "FULL2_UNTIL", None)
    with pytest.raises(ValueError, match="merge commit"):
        exclusions.derive_full2(ROOT / "data/snapshot/facts.sqlite")
    outputs = {
        "--name-only": "tests/test_new_fix.py\ntests/test_spot500_sets.py\n",
        "--diff-filter=M": ("diff --git a/tests/test_old.py b/tests/test_old.py\n--- a/tests/test_old.py\n"
                            "+++ b/tests/test_old.py\n@@ -9,0 +10,1 @@\n+    span = \"Valero's revenue\"\n"
                            "diff --git a/tests/test_spot500_sets.py b/tests/test_spot500_sets.py\n"
                            "--- a/tests/test_spot500_sets.py\n+++ b/tests/test_spot500_sets.py\n"
                            "@@ -1,0 +2,1 @@\n+# Walmart\n"),
        "receipts/": "",
    }

    def fake_git(*args):
        if args[1] == "show":
            return "def test_fix():\n    assert lookup('Intel')\n"
        key = next(k for k in outputs if k in args)
        return outputs[key]
    monkeypatch.setattr(exclusions, "git", fake_git)
    record = exclusions.derive_full2(ROOT / "data/snapshot/facts.sqlite", "f" * 40)
    tests = record["verifier_fix_tests"]
    assert tests["companies"] == ["INTC", "VLO"]   # the new file and an existing file's added line
    assert tests["modified_files"] == ["tests/test_old.py"] and "WMT" not in tests["companies"]
    assert [row["path"] for row in tests["scanned"]] == ["tests/test_new_fix.py"]


def test_full2_has_the_full_allowance(tmp_path, monkeypatch):
    rows = [item("original", version="spot500_full2")]
    corpus, manifest = freeze(tmp_path / "frozen", rows, version="spot500_full2")
    runs = tmp_path / "spot500_runs"
    runs.mkdir()
    monkeypatch.setattr(spot500, "FULL2_CORPUS", corpus)
    monkeypatch.setattr(spot500, "FULL2_MANIFEST", manifest)
    monkeypatch.setattr(spot500, "RUNS", runs)
    assert spot500.BUDGET_ALLOWANCES["full2"] == spot500.BUDGET_ALLOWANCES["full"] == (6.0, 5.5)
    assert spot500.load_items("full2")[1] == rows
    with pytest.raises(ValueError, match="at most \\$6"):
        spot500.run(rows, runs / "full2-oracle-x.json", stub=True, evidence_dir=runs, budget=6.5, spot_set="full2")
    assert spot500.run(rows, runs / "full2-oracle-x.json", stub=True, evidence_dir=runs, budget=6.0,
                       spot_set="full2")["set"] == "full2"


def test_full2_history_derivation_reproduces_from_git_history():
    from eval import spot500_exclusions as exclusions

    record = json.loads((ROOT / "eval" / FULL2.derivation[0]).read_bytes())
    assert record["range"] == {"since": exclusions.FULL2_SINCE, "until": exclusions.FULL2_UNTIL,
                               "since_role": "the integration tip full2 starts from",
                               "until_role": "the round-8 merge that spot500_full2 measures"}
    assert exclusions.FULL2_UNTIL == FULL2.commit
    _, table = exclusions.companies_table(ROOT / "data/snapshot/facts.sqlite")
    if table != record["companies_table"]:
        pytest.skip("the snapshot companies table changed after the full2 derivation")
    if shutil.which("git") is None or subprocess.run(
            ["git", "cat-file", "-e", f"{exclusions.FULL2_UNTIL}^{{commit}}"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("the derivation range is not in this clone's history")
    assert exclusions.encode(exclusions.derive_full2(ROOT / "data/snapshot/facts.sqlite")) == (
        ROOT / "eval" / FULL2.derivation[0]).read_bytes()


def test_frozen_full2_meets_its_design_and_reuses_nothing():
    raw = (ROOT / "eval" / FULL2.corpus).read_bytes()
    manifest, items = frozen_manifest(FULL2), frozen_items(FULL2)
    scale = spot.FULL_SCALE
    assert manifest["corpus_sha256"] == sha(raw) and manifest["version"] == "spot500_full2"
    assert manifest["commit"] == FULL2.commit == "8c2fd7815ccd0d1108a5df39428f6b0f091a0c65"
    assert manifest["items"] == [{"item_id": it["item_id"], "sha256": sha(spot.canonical(it))} for it in items]
    assert {it["corpus_version"] for it in items} == {"spot500_full2"}
    assert all(it["item_id"].startswith("spot500-full2-") for it in items) and len(items) == scale["total"]
    assert manifest["excluded_tickers"] == []
    companies = set(manifest["selection"]["selected_companies"])
    assert companies == {it["ticker"] for it in items} and len(companies) == manifest["counts"]["companies"]
    # Frame: full's, unchanged, plus the companies the exhibit fallback pairs.
    frame = manifest["frame"]
    assert frame["strict_frame_sha256"] == frozen_manifest(FULL)["source_pool_sha256"]
    added = frame["companies_added"]
    assert len(added) == 36 and "TSLA" not in added
    assert not set(added) & set(frozen_manifest(FULL)["selection"]["selected_companies"])
    assert {label for labels in added.values() for label in labels} == {"EX-99", "EX-99.01"}
    assert {t for t, labels in added.items() if labels == ["EX-99.01"]} == {"CDNS", "GEN", "INTU", "VLO", "XEL"}
    assert set(added) - companies == {"PGR"}   # its releases pass the rule; no sentence passes the screen
    # Company history: exactly one of three tags per item.
    seen = spot.earlier_companies(FULL2)
    tags = ("company_in_development", "company_new", "company_seen")
    for item in items:
        assert [t for t in tags if t in item["features"]] == [spot.history_tag(FULL2, item["ticker"], seen)]
    slices = manifest["counts"]["by_company_history"]
    for tag in tags:
        tagged = [it for it in items if tag in it["features"]]
        assert slices[tag]["items"] == len(tagged) and slices[tag]["companies"] == len({it["ticker"] for it in tagged})
    assert sum(slices[tag]["items"] for tag in tags) == len(items)
    assert set(manifest["company_history"]["in_development"]) == FULL2.in_development and len(FULL2.in_development) == 104
    assert not (set(manifest["selection"]["company_history"]["company_new"]) & seen)
    originals = [it for it in items if it["mutator"] == "none"]
    per_company = Counter(it["ticker"] for it in originals)
    assert set(per_company) == companies and max(per_company.values()) <= scale["per_company"]
    mutants = [it for it in items if it["mutator"] in spot.MUTATORS]
    shortfalls = manifest["selection"]["mutant_shortfalls"]
    assert Counter(it["mutator"] for it in mutants) == {
        m: shortfalls[m]["taken"] if m in shortfalls else scale["mutants"] for m in spot.MUTATORS}
    controls = [it for it in items if it["mutator"] == "forward_looking_control"]
    assert len(controls) == len({it["ticker"] for it in controls}) == scale["controls"]
    # No sentence of v1, v2, v3 or full returns, by record, id, identical text or item.
    earlier = (V1, V2, V3, FULL)
    earlier_records = {r["sha256"] for s in earlier for r in frozen_manifest(s)["source_records"] if r["kind"] == "sentence"}
    assert not {r["sha256"] for r in manifest["source_records"] if r["kind"] == "sentence"} & earlier_records
    earlier_items = [it for s in earlier for it in frozen_items(s)]
    assert not {it["source"]["sentence_id"] for it in items} & {it["source"]["sentence_id"] for it in earlier_items}
    assert not {it["source"]["original_text"] for it in items} & {it["source"]["original_text"] for it in earlier_items}
    assert not {it["item_hash"] for it in items} & {it["item_hash"] for it in earlier_items}
    by_feature = Counter(f for it in items for f in it["features"])
    for feature, entry in manifest["selection"]["feature_coverage"].items():
        assert entry["items"] == by_feature[feature], feature
        assert (entry["items"] >= scale["feature_target"]) != ("short_because" in entry), feature
    for ref, earlier_set in zip(manifest["reference_sets"], earlier):
        assert ref["manifest_sha256"] == sha((ROOT / "eval" / earlier_set.manifest).read_bytes())
    assert manifest["company_history_derivation"]["sha256"] == FULL2.derivation[1]
    assert manifest["cost_estimate"]["total_usd"] == 5.0 <= manifest["cost_estimate"]["estimate_limit_usd"] == 5.5
    assert manifest["builder_hashes"] == spot.builder_hashes(FULL2), "builder changed after the full2 freeze"


def test_committed_full2_passes_the_runner_freeze_and_the_oracle_scores_by_construction():
    manifest, items = spot500.load_items("full2")
    result = spot500.score(items, {it["item_id"]: {"pairs": spot500.oracle_pairs(it)} for it in items})
    total = result["total"]
    assert total["status_and_reason_correct"] == total["gold_claims"] == manifest["counts"]["gold_claims"]
    assert total["caught_errors"] == total["target_claims"] == sum(
        n for m, n in manifest["counts"]["by_mutator"].items() if m in spot.MUTATORS)
    assert total["false_alarm_original_items"] == total["verified_guidance"] == total["extra_claims"] == 0
    for tag in ("company_in_development", "company_new", "company_seen"):
        assert result["by_feature"][tag]["items"] == manifest["counts"]["by_company_history"][tag]["items"]
