"""Build LoRA SFT data for Muse Glimmer on NoCap's claim extraction (an experiment; the product is unchanged).

Prompt: the request the "modal" Glimmer backend sends (receipts.extract.build_request): system CHAT_INSTRUCTIONS
(instructions plus the JSON schema), user format_input(passage, context). The Muse Glimmer chat template adds
"Reasoning strength: low." because that backend passes chat_template_kwargs reasoning_strength=low.

Targets: Muse Spark 1.3's cached extraction of the identical passage and context (data/cache/spark, then
data/snapshot/spark_cache, the order receipts.check reads them), re-serialized compactly in schema key order
(json.loads-equal to the cached text). A target is kept only when code accepts it:
  schema   the strict ModelResponse validates and the lenient chat-backend path rejects no claim;
  locate   every span_text is verbatim in the passage and contains its value_text, and locate_claims places every
           claim without dropping it, re-anchoring it or widening its value_text;
  re-read  every value_text holds exactly one number, and its value, scale and unit read back unchanged.

Split. The held-out split is never used: only eval.load_split("dev", ...) rows are read.
  test      the 10 gold passages (spikes/inputs/gold_text.txt, 27 hand-labeled claims) plus a seeded 20% of each
            corpus's development passages (v0 and trap_v1), chosen by whole group (an original v0 sentence with
            its mutants; a trap sheet's drafts), so no test sentence has a twin in training;
  excluded  development groups taken from the gold passages' own press release (same SEC accession);
  train     every other development passage whose target passes the three checks.

    .venv/bin/python eval/glimmer_sft/build_data.py --out <scratch dir>     # offline; writes train/test JSONL there
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import socket
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _no_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("build_data is offline: no network or model calls")


socket.create_connection = _no_network  # type: ignore[assignment]
socket.socket.connect = _no_network  # type: ignore[assignment]

from eval import bench_models as bench  # noqa: E402
from eval import eval as harness  # noqa: E402
from receipts import extract as ex  # noqa: E402

SEED = "nocap-glimmer-sft-20261031"
TEST_FRACTION = 0.20
GOLD_ACCESSION = json.loads((ROOT / "spikes/inputs/gold_text.source.json").read_text(encoding="utf-8"))["accession"]
TEACHER_DIRS = (ROOT / "data/cache/spark", ROOT / "data/snapshot/spark_cache")
META = ex.BACKENDS["meta"]          # the backend whose cache holds the teacher responses
GLIMMER = ex.BACKENDS["modal"]      # the backend whose prompt the student is trained on
MANIFEST = Path(__file__).resolve().parent / "data_manifest.json"
HASHED_SOURCES = ("receipts/extract.py", "receipts/verify.py", "receipts/stream.py", "receipts/check.py",
                  "receipts/types.py", "receipts/periods.py", "receipts/store.py", "eval/bench_models.py",
                  "eval/eval.py", "eval/corpus/v0_dev.jsonl", "spikes/inputs/gold_text.txt",
                  "spikes/inputs/gold_claims.json", "eval/glimmer_sft/build_data.py")


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def file_sha256(rel: str) -> str:
    return sha256((ROOT / rel).read_bytes())


@dataclass
class Passage:
    key: str                     # Spark cache key = request hash of the Meta request for this passage and context
    corpus: str                  # v0 | trap_v1 | gold
    group: str                   # v0 group_id, trap sheet_id, or "gold"
    item_ids: list[str]
    passage: str
    context: Optional[str]       # the resolved document-context line, exactly as sent
    as_of: Optional[str]         # canonical item's as_of (ISO) for verification; None for gold
    accession: Optional[str]
    teacher_file: str = ""
    teacher_text: str = ""
    split: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def user(self) -> str:
        return ex.format_input(self.passage, self.context)


def resolve_context(text: str, context: Optional[str]) -> Optional[str]:
    """The context line receipts.check sends: inferred when None, stripped, '' meaning none."""
    ctx = ex.infer_context(text) if context is None else (context.strip() or None)
    return ctx


def _passages_of(text: str, context: Optional[str]) -> list[tuple[str, str]]:
    ctx = resolve_context(text, context)
    keys = ex.request_keys(text, ctx if ctx is not None else "", backend=META)
    parts = ex.split_passages(text)
    out = []
    for (_, p), key in zip(parts, keys):
        assert ex.request_key(ex.build_request(p, ctx, backend=META), META) == key
        out.append((p, key))
    return out


def teacher(key: str) -> tuple[str, str]:
    for d in TEACHER_DIRS:
        path = d / f"{key}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            status, _, _, _ = ex.check_output(rec.get("output_text") or "", META)
            if status == "ok":
                if rec.get("model") != ex.MODEL:
                    raise SystemExit(f"{path.name}: teacher model {rec.get('model')!r}, expected {ex.MODEL}")
                return str(path.relative_to(ROOT)), rec["output_text"]
    raise SystemExit(f"no schema-valid Muse Spark response cached for request {key[:12]}")


def dev_passages() -> list[Passage]:
    found: dict[str, Passage] = {}
    for corpus in ("v0", "trap_v1"):
        for item in sorted(harness.load_split("dev", corpus), key=lambda it: it["item_id"]):
            if item.get("split") != "dev":
                raise SystemExit("refusing a non-dev row")
            group = item["group_id"] if corpus == "v0" else item["sheet_id"]
            ctx = resolve_context(item["text"], item["context"])
            for p, key in _passages_of(item["text"], item["context"]):
                if key in found:
                    if found[key].group != group:
                        raise SystemExit(f"passage {key[:12]} is shared by two groups")
                    found[key].item_ids.append(item["item_id"])
                    continue
                found[key] = Passage(key, corpus, group, [item["item_id"]], p, ctx, item.get("as_of") or None,
                                     item.get("accession") or item.get("release_accession"))
    return list(found.values())


def gold_passages() -> list[Passage]:
    text = bench.GOLD_TEXT.read_text(encoding="utf-8")
    ctx = resolve_context(text, None)
    return [Passage(key, "gold", "gold", ["gold"], p, ctx, None, GOLD_ACCESSION) for p, key in _passages_of(text, None)]


def canonical_target(output_text: str) -> str:
    """The teacher's JSON, every claim in schema key order, compact separators; json.loads-equal to the input."""
    ex.ModelResponse.model_validate_json(output_text)  # strict schema first
    order = list(ex.ModelClaim.model_fields)
    claims = [{k: c[k] for k in order} for c in json.loads(output_text)["claims"]]  # the teacher's own values
    text = json.dumps({"claims": claims}, ensure_ascii=False, separators=(",", ":"))
    if json.loads(text) != json.loads(output_text):
        raise AssertionError("re-serialization changed the teacher's JSON")
    return text


def acceptance(passage: str, output_text: str) -> list[str]:
    """Why code rejects this teacher output as a training target; [] when it accepts it."""
    status, resp, _ = ex.validate_output(output_text)
    if status != "ok" or resp is None:
        return [f"schema:{status}"]
    reasons: list[str] = []
    _, _, _, rejected = ex.validate_claims_lenient(output_text)
    if rejected:
        reasons.append("schema:lenient_rejected")
    for c in resp.claims:
        if not c.value_text or not c.span_text:
            reasons.append("locate:empty_text")
            continue
        if c.span_text not in passage:
            reasons.append("locate:span_not_verbatim")
        elif c.value_text not in c.span_text:
            reasons.append("locate:value_not_in_span")
        p = ex.parse_value_text(c.value_text)
        if p["n_numbers"] != 1 or p["number"] is None:
            reasons.append("reread:not_one_number")
        reasons.extend(f"reread:{e}" for e in bench.raw_errors(c, passage))
        corrections: list[str] = []
        ex.normalize_value(c, corrections)
        if corrections:
            reasons.append("reread:normalize_corrected")
    warnings: list[str] = []
    located = ex.locate_claims(list(resp.claims), passage, 0, warnings)
    if len(located) != len(resp.claims):
        reasons.append("locate:dropped")
    if any("was not verbatim" in w for w in warnings):
        reasons.append("locate:reanchored")
    if any("restored the complete source number" in w for w in warnings):
        reasons.append("locate:value_widened")
    return sorted(set(reasons))


def assign(dev: list[Passage]) -> None:
    pool: dict[tuple[str, str], list[Passage]] = defaultdict(list)
    for r in dev:
        if r.accession == GOLD_ACCESSION:
            r.split, r.reasons = "excluded", ["gold_source_document"]
        else:
            pool[(r.corpus, r.group)].append(r)
    for corpus in sorted({g[0] for g in pool}):  # stratified: 20% of each corpus's passages, by whole groups
        groups = sorted((g for g in pool if g[0] == corpus), key=lambda g: sha256(f"{SEED}|{g[0]}|{g[1]}"))
        target = math.ceil(TEST_FRACTION * sum(len(pool[g]) for g in groups))
        chosen = 0
        for g in groups:
            split = "test" if chosen < target else "train"
            chosen += len(pool[g]) if split == "test" else 0
            for r in pool[g]:
                r.split = split


def build() -> dict[str, Any]:
    dev, gold = dev_passages(), gold_passages()
    assign(dev)
    for r in gold:
        r.split = "test"
    for r in dev + gold:
        r.teacher_file, r.teacher_text = teacher(r.key)
        reasons = acceptance(r.passage, r.teacher_text)
        if r.split == "train" and reasons:
            r.split, r.reasons = "dropped", reasons
        elif r.split == "test":
            r.reasons = reasons  # reported only: test targets are references, not training data
    return {"dev": dev, "gold": gold}


def chat_row(r: Passage, assistant: str) -> dict[str, Any]:
    req = ex.build_request(r.passage, r.context, backend=GLIMMER)
    system, user = req["messages"]
    assert system["content"] == ex.CHAT_INSTRUCTIONS and user["content"] == r.user
    return {"id": r.key, "corpus": r.corpus, "messages": [system, user, {"role": "assistant", "content": assistant}]}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path, help="scratch directory for train.jsonl and test.jsonl")
    ap.add_argument("--no-manifest", action="store_true", help="do not rewrite eval/glimmer_sft/data_manifest.json")
    args = ap.parse_args(argv)
    built = build()
    rows = built["dev"] + built["gold"]
    # Sampling order: the gold passages first, then development passages in a seeded order across both corpora,
    # so a budget stop never drops one corpus wholesale.
    test = sorted((r for r in rows if r.split == "test"), key=lambda r: (r.corpus != "gold", sha256(f"{SEED}|{r.key}")))
    train = sorted((r for r in rows if r.split == "train"), key=lambda r: r.key)
    args.out.mkdir(parents=True, exist_ok=True)
    files = {}
    meta = [json.dumps({k: v for k, v in asdict(r).items() if k not in ("split", "reasons")}, ensure_ascii=False)
            for r in test]  # what evaluate.py needs even if receipts/ changes after sampling
    for name, part, lines in (("train.jsonl", train, None), ("test.jsonl", test, None), ("test_meta.jsonl", test, meta)):
        lines = lines or [json.dumps(chat_row(r, canonical_target(r.teacher_text)), ensure_ascii=False) for r in part]
        blob = ("\n".join(lines) + "\n").encode("utf-8")
        (args.out / name).write_bytes(blob)
        files[name] = {"rows": len(part), "sha256": sha256(blob), "bytes": len(blob)}
    dev = built["dev"]
    claims = Counter()
    for r in rows:
        claims[r.split] += len(json.loads(r.teacher_text)["claims"])
    summary = {
        "dev_passages": len(dev), "dev_groups": len({(r.corpus, r.group) for r in dev}),
        "dev_by_corpus": dict(Counter(r.corpus for r in dev)),
        "excluded_gold_source": sum(r.split == "excluded" for r in dev),
        "test_dev_passages": sum(r.split == "test" for r in dev),
        "test_dev_groups": len({(r.corpus, r.group) for r in dev if r.split == "test"}),
        "test_gold_passages": len(built["gold"]),
        "train_candidates": sum(r.split in ("train", "dropped") for r in dev),
        "train_kept": len(train), "train_dropped": sum(r.split == "dropped" for r in dev),
        "drop_reasons": dict(Counter(x for r in dev if r.split == "dropped" for x in r.reasons)),
        "test_teacher_rejections": dict(Counter(x for r in test for x in r.reasons)),
        "teacher_claims_by_split": dict(claims),
    }
    manifest = {
        "experiment": "Muse Glimmer 30B LoRA SFT on NoCap claim extraction",
        "seed": SEED, "test_fraction": TEST_FRACTION, "gold_accession": GOLD_ACCESSION,
        "teacher_model": ex.MODEL, "teacher_effort": ex.EFFORT,
        "prompt": {"system": "receipts.extract.CHAT_INSTRUCTIONS", "system_sha256": sha256(ex.CHAT_INSTRUCTIONS),
                   "schema_sha256": sha256(json.dumps(ex.SCHEMA, sort_keys=True)),
                   "user": "receipts.extract.format_input(passage, context)", "reasoning_strength": "low"},
        "source_sha256": {rel: file_sha256(rel) for rel in HASHED_SOURCES},
        "dev_rows_sha256": sha256("\n".join(sorted(f"{c}:{i}" for r in dev for i in r.item_ids for c in [r.corpus]))),
        "files": files, "summary": summary,
        "passages": [{"key": r.key, "corpus": r.corpus, "group": r.group, "items": r.item_ids, "split": r.split,
                      "reasons": r.reasons, "teacher_file": r.teacher_file,
                      "teacher_claims": len(json.loads(r.teacher_text)["claims"]),
                      "target_sha256": sha256(canonical_target(r.teacher_text)),
                      "passage_sha256": sha256(r.passage)} for r in sorted(rows, key=lambda r: (r.split, r.key))],
    }
    if not args.no_manifest:
        MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"files": files, "summary": summary}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
