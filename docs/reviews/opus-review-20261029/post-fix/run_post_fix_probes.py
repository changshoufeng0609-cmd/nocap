"""Rerun the October 29 review probes against the current working tree without touching their evidence.

Each original probe writes its adjacent *_results.json. This runner leaves those files alone: it reads
each probe's source unchanged, redirects only its repository root and output path into this directory,
and records the original probe's SHA-256 and every substitution made. No provider, SEC or network call
is made; the probes block sockets themselves and use the committed snapshot.

    .venv/bin/python -B docs/reviews/opus-review-20261029/post-fix/run_post_fix_probes.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback

HERE = Path(__file__).resolve().parent
REVIEW = HERE.parent
ROOT = REVIEW.parents[2]
PYTHON_PROBES = {
    "correctness_probe.py": "correctness_results.json",
    "claude_bounds_probe.py": "claude_bounds_results.json",
    "claude_followup_probe.py": "claude_followup_results.json",
    "runtime_probe.py": "runtime_results.json",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redirect(source: str, replacements: list[tuple[str, str]]) -> str:
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError(f"expected exactly one occurrence of {old!r}")
        source = source.replace(old, new)
    return source


def run_python(name: str, output: str) -> dict:
    path = REVIEW / name
    raw = path.read_bytes()
    replacements = [
        ("Path(__file__).resolve().parents[3]", repr(str(ROOT)).join(("Path(", ")"))),
        (f'Path(__file__).with_name("{output}")', repr(str(HERE / output)).join(("Path(", ")"))),
    ]
    source = redirect(raw.decode("utf-8"), replacements)
    record = {"probe": name, "original_sha256": sha256(raw), "substitutions": replacements,
              "output": output}
    namespace = {"__name__": "__main__", "__file__": str(path)}
    try:
        exec(compile(source, str(path), "exec"), namespace)
        record["status"] = "completed"
    except Exception as e:  # a probe built for the old code may no longer apply; record it honestly
        record["status"] = "failed"
        record["error"] = "".join(traceback.format_exception_only(type(e), e)).strip()
    return record


def run_node() -> dict:
    path = REVIEW / "product_probe.mjs"
    raw = path.read_bytes()
    replacements = [('const root = path.resolve(evidenceDir, "../../..");',
                     'const root = path.resolve(evidenceDir, "../../../..");')]
    copy = HERE / "product_probe_redirected.mjs"
    copy.write_text(redirect(raw.decode("utf-8"), replacements), encoding="utf-8")
    process = subprocess.run(["node", str(copy)], capture_output=True, text=True, timeout=120)
    return {"probe": "product_probe.mjs", "original_sha256": sha256(raw), "substitutions": replacements,
            "redirected_copy": copy.name, "output": "product_results.json",
            "status": "completed" if process.returncode == 0 else "failed",
            "stderr_tail": process.stderr[-2000:] if process.returncode else ""}


def main() -> None:
    runs = [run_python(name, output) for name, output in PYTHON_PROBES.items()]
    runs.append(run_node())
    current = {name: sha256((ROOT / name).read_bytes()) for name in (
        "receipts/extract.py", "receipts/verify.py", "receipts/api.py", "web/lib/api.ts", "web/hooks/use-check.ts")}
    record = {"purpose": "Post-fix rerun of the October 29 review probes; originals and their results untouched.",
              "provider_calls": 0, "sec_calls": 0, "cost_usd": 0, "current_source_sha256": current, "runs": runs}
    (HERE / "post_fix_runs.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: r[k] for k in ("probe", "status")} for r in runs]))


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
