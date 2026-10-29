"""Reproduce all public QA scores offline using the frozen source overlay.

No .env, ignored live records, admission database, API key, model or SEC request
is needed. Inputs remain unchanged; a temporary scratch tree isolates frozen code
from later production fixes. The only permanent write is the chosen report path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SHA256 = "4a294dfc9a88ac565d92243115e85795f732a402d3e1becf6cc4318c024a99ea"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_relative(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Invalid public artifact path")
    return path


def prepare_scratch(run_id: str, destination: Path, *, root: Path = ROOT) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid run ID")
    records = root / "eval/qa_runs" / run_id
    bundle = records / "frozen_sources"
    index_bytes = (bundle / "bundle.json").read_bytes()
    if digest(index_bytes) != BUNDLE_SHA256:
        raise ValueError("Frozen source bundle index differs")
    index = json.loads(index_bytes)
    manifest_bytes = (root / "eval/qa_manifest.json").read_bytes()
    if digest(manifest_bytes) != index["manifest_sha256"]:
        raise ValueError("Question manifest differs from the source bundle")
    manifest = json.loads(manifest_bytes)
    # Copy just publicly committed evidence bytes, verified before import/scoring.
    for name, expected in manifest["snapshot_hashes"].items():
        relative = checked_relative(name)
        source = bundle / "data/snapshot/manifest.json" if name == "manifest.json" else root / "data/snapshot" / relative
        raw = source.read_bytes()
        if digest(raw) != expected:
            raise ValueError(f"Public snapshot bytes differ: {name}")
        target = destination / "data/snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for name, expected in index["files"].items():
        relative = checked_relative(name)
        raw = (bundle / relative).read_bytes()
        if digest(raw) != expected:
            raise ValueError(f"Frozen source bytes differ: {name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (destination / "eval/__init__.py").write_text('"""Isolated frozen QA scoring package."""\n')
    (destination / "eval/qa_manifest.json").write_bytes(manifest_bytes)
    sensitivity_bytes = (records / "sensitivity.json").read_bytes()
    sensitivity = json.loads(sensitivity_bytes)
    if sensitivity["manifest_sha256"] != index["manifest_sha256"] or sensitivity["normalizer_sha256"] != index["files"]["eval/qa_rescore.py"]:
        raise ValueError("Sensitivity result is not bound to this manifest/normalizer")
    local_records = destination / "eval/qa_runs" / run_id
    local_records.mkdir(parents=True)
    (local_records / "sensitivity.json").write_bytes(sensitivity_bytes)
    for target, source in sensitivity["source_records"].items():
        if target not in {"spark", "wonder", "spark-open-baseten", "glimmer"}:
            raise ValueError("Unexpected target")
        path = records / f"{target}.json"
        raw = path.read_bytes()
        if digest(raw) != source["sha256"]:
            raise ValueError(f"Original response records differ: {target}")
        (local_records / path.name).write_bytes(raw)
    return {"manifest_sha256": index["manifest_sha256"], "bundle_sha256": BUNDLE_SHA256,
            "sensitivity_sha256": digest(sensitivity_bytes)}


def offline_worker(scratch: Path, run_id: str) -> dict:
    # Install the network barrier before importing any project or provider module.
    def audit(event, _args):
        if event.startswith("socket.") and event not in {"socket.gethostname"}:
            raise RuntimeError("Offline reproduction forbids all network/SEC/provider activity")
    sys.addaudithook(audit)
    sys.path = [str(scratch), *[path for path in sys.path if Path(path or ".").resolve() not in {ROOT, ROOT / "eval"}]]
    import openai
    def forbidden(*_args, **_kwargs):
        raise RuntimeError("Offline reproduction forbids model client construction")
    for name in ("OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI"):
        setattr(openai, name, forbidden)
    from eval import qa_core as core, qa_benchmark as runner, qa_rescore as rescore
    from receipts import extract
    from receipts.store import Store
    runner.make_client = extract.make_client = forbidden
    manifest = runner.load_manifest()
    by_id = {row["question"]["id"]: row for row in manifest["items"]}
    records_dir = scratch / "eval/qa_runs" / run_id
    saved = json.loads((records_dir / "sensitivity.json").read_text())
    expected = {run["target"]: run for run in saved["runs"]}
    assert set(expected) == set(core.TARGETS)
    store = Store(scratch / "data/snapshot/facts.sqlite", scratch / "data/snapshot/pr_facts")
    result = {"status": "passed", "scope": "Read-only public-input offline replay with frozen source overlay",
              "new_model_calls": 0, "new_sec_calls": 0, "model_clients_forbidden": True,
              "network_forbidden": True, "planned": 200, "records_replayed": 0, "runs": []}
    try:
        for target in core.TARGETS:
            run = json.loads((records_dir / f"{target}.json").read_text())
            assert run["target"] == target and run["planned"] == 50
            assert len({row["question_id"] for row in run["records"]}) == len(run["records"])
            assert set(row["question_id"] for row in run["records"]) <= set(by_id)
            primary, sensitivity = [], []
            for record in run["records"]:
                row = by_id[record["question_id"]]
                assert record["request_sha256"] == row["request_sha256"][target]
                if record.get("output_text") is not None:
                    assert digest(record["output_text"].encode()) == record["output_sha256"]
                strict = runner.evaluate_record(row, record.get("output_text", ""), complete=record.get("response_complete", False),
                                               store=store, call_error=record.get("error_type") is not None)
                assert all(record.get(key) == value for key, value in strict.items()), (target, record["question_id"], "primary record differs")
                primary.append(strict)
                sensitivity.append(rescore.assess(row, record, store))
            primary_score = core.aggregate(primary)
            sensitivity_score = rescore.summarize(sensitivity)
            usage = rescore.usage_audit(run, saved["ledger"]["attempts"])
            assert primary_score == run["score"] == expected[target]["primary_score"]
            assert sensitivity_score == expected[target]["sensitivity_score"]
            assert sensitivity == expected[target]["records"]
            assert usage == expected[target]["usage_audit"]
            result["records_replayed"] += len(primary)
            result["runs"].append({"target": target, "primary_metrics": primary_score,
                                   "sensitivity_metrics": sensitivity_score, "usage_audit": usage,
                                   "every_record_matches": True, "every_metric_matches": True})
    finally:
        store.close()
    assert result["records_replayed"] == sum(len(json.loads((records_dir / f"{target}.json").read_text())["records"]) for target in core.TARGETS)
    result["not_run"] = result["planned"] - result["records_replayed"]
    return result


def reproduce(run_id: str, *, root: Path = ROOT) -> dict:
    with tempfile.TemporaryDirectory(prefix="nocap-qa-offline-") as temporary:
        scratch = Path(temporary)
        hashes = prepare_scratch(run_id, scratch, root=root)
        # Do not inherit provider keys, private endpoints or dotenv settings.
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(scratch), "--run-id", run_id],
                                 cwd=scratch, env=environment, text=True, capture_output=True, check=True, timeout=120)
        result = json.loads(process.stdout)
        return {**result, **hashes, "source_note": "Minimal source overlay; not a complete historical commit"}


def run_frozen_contract_tests(case_name: str) -> str:
    """Run original synthetic cases in a separate frozen import namespace/process."""
    if case_name not in {"benchmark", "rescore"}:
        raise ValueError("Unknown frozen test case group")
    with tempfile.TemporaryDirectory(prefix="nocap-qa-tests-") as temporary:
        scratch = Path(temporary)
        prepare_scratch("qa-closed-book-20261029", scratch)
        test_file = scratch / f"test_{case_name}.py"
        test_file.write_bytes((ROOT / "tests/qa_frozen_cases" / f"{case_name}_cases.py").read_bytes())
        (scratch / "conftest.py").write_text('''import sys
def audit(event, args):
    if event.startswith("socket.") and event != "socket.gethostname":
        raise RuntimeError("Frozen QA tests forbid network and SEC requests")
sys.addaudithook(audit)
import openai
def forbidden(*args, **kwargs):
    raise RuntimeError("Frozen QA tests forbid provider client construction")
for name in ("OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI"):
    setattr(openai, name, forbidden)
''')
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                       "PYTHONPATH": str(scratch), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
        process = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                  "--confcutdir", str(scratch), str(test_file)], cwd=scratch, env=environment,
                                 text=True, capture_output=True, timeout=120)
        if process.returncode:
            raise AssertionError(process.stdout + process.stderr)
        return process.stdout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="qa-closed-book-20261029")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = offline_worker(args.worker, args.run_id) if args.worker else reproduce(args.run_id)
    if args.output:
        with args.output.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    print(json.dumps(report, indent=2))
