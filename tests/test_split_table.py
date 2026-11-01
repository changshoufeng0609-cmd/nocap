"""receipts/splits.py and its decision record (eval/evidence_expansion/sp500-splits-20261031.json) are what
scripts/build_split_table.py makes of the archived companyfacts and data/snapshot, so a data top-up can regenerate them.

The full check reads data/archive/companyfacts, which the public clone doesn't carry: there it skips, and only the
check that the table and the record agree with each other runs."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data/archive/companyfacts"
SCRIPT = ROOT / "scripts/build_split_table.py"


def generator():
    spec = importlib.util.spec_from_file_location("build_split_table", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_table_is_rendered_from_the_committed_record():
    gen = generator()
    record = json.loads(gen.RECORD.read_text(encoding="utf-8"))
    assert gen.render_module(record["decisions"]) == gen.MODULE.read_text(encoding="utf-8")
    assert gen.render_record(record["decisions"], gen.DB) == gen.RECORD.read_text(encoding="utf-8")


@pytest.mark.skipif(not ARCHIVE.is_dir(), reason="no data/archive/companyfacts: the public clone doesn't carry it")
def test_the_committed_table_is_the_generators_output_on_the_archive_and_the_snapshot():
    result = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, cwd=ROOT,
                            timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
