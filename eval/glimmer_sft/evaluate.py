"""Score base vs tuned Muse Glimmer extraction with NoCap's own deterministic checks. No LLM judge, no model calls.

Inputs: samples.jsonl from train_and_sample.py (the visible to=user text of each answer) and the test passages
rebuilt by build_data.py. Every source (the Muse Spark 1.3 teacher, base Glimmer, tuned Glimmer) goes through the
product's code path for chat backends: json_from_message, validate_claims_lenient, build_claims (locate, re-read,
basis expansion) and receipts.stream.verdict_for against the local SEC evidence store.

Metrics, fixed before any sampling (denominators are reported with every number):
  schema     pure JSON that validates strictly as returned; strict after the product's JSON repair; claims the
             lenient chat path rejects; unterminated (no <|eot|> before max_tokens) and empty answers
  gold       the 10 gold passages: strict recall and precision against the 27 hand-labeled claims (bench_models)
  teacher    claims aligned to the teacher's by source number (code-located value offsets) and basis: exact
             match of metric, value_text, period_text and basis; missing and extra claims
  verdicts   for each teacher claim, the aligned claim's verdict status (and reason) under receipts.verify;
             teacher-flagged errors that came back verified; extra claims that came back verified
  items      seeded-error catch rate and false alarms on the test passages' dev items (eval.score, score_traps)
  tokens     completion, visible-answer and reasoning tokens per answer; sampling cost from the run's ledger
  paired     exact two-sided McNemar, base vs tuned: per gold claim (strict match), per passage (exact claim set
             vs teacher; verdict-equivalent to teacher), per seeded error (caught)

    .venv/bin/python eval/glimmer_sft/evaluate.py --samples <run>/samples.jsonl --out eval/glimmer_sft/eval_results.json
    .venv/bin/python eval/glimmer_sft/evaluate.py --self-test            # scorer check on teacher-derived answers
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import statistics
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))


def _no_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("evaluate is offline: no network or model calls")


socket.create_connection = _no_network  # type: ignore[assignment]
socket.socket.connect = _no_network  # type: ignore[assignment]

import build_data as bd  # noqa: E402
from eval import bench_models as bench  # noqa: E402
from eval import eval as harness  # noqa: E402
from receipts import extract as ex  # noqa: E402
from receipts import verify as _verify  # noqa: E402
from receipts.store import Store  # noqa: E402
from receipts.stream import verdict_for  # noqa: E402

SOURCES = ("teacher", "base", "tuned")
FIELDS = ("metric", "value_text", "period_text", "basis")
EXTRA_FIELDS = ("period_type", "comparison", "segment", "entity", "forward_looking", "unit", "scale", "value")
HAND_SEED = 20261031


def mcnemar(a: list[bool], b: list[bool]) -> dict[str, Any]:
    """Exact two-sided McNemar on paired binary outcomes (a = base, b = tuned), as in the cookbook starter."""
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    n, k = only_a + only_b, min(only_a, only_b)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    return {"n": len(a), "base_true": sum(a), "tuned_true": sum(b), "only_tuned": only_b, "only_base": only_a,
            "p_value": p}


def analyze(passage: str, text: str, start: int = 0) -> dict[str, Any]:
    """One answer through the chat-backend code path. text: the visible answer (or the teacher's output_text)."""
    out: dict[str, Any] = {"empty": not text.strip()}
    json_text, repair = ex.json_from_message(text)
    out["repair"] = repair
    out["pure_json_strict"] = ex.validate_output(text.strip())[0] == "ok" if text.strip() else False
    out["strict_after_repair"] = ex.validate_output(json_text)[0] == "ok" if json_text.strip() else False
    status, resp, problem, rejected = ex.validate_claims_lenient(json_text)
    out.update(lenient_status=status, claims_rejected=rejected, problem=problem)
    claims: list[Any] = []
    warnings: list[str] = []
    raw = 0
    if status == "ok" and resp is not None:
        raw = len(resp.claims)
        claims, _ = ex.build_claims([(start, passage, resp.claims)], warnings)
    out.update(raw_claims=raw, claims=claims, warnings=warnings,
               dropped=sum("dropped a claim" in w for w in warnings))
    return out


def value_range(c: Any) -> tuple[int, int]:
    k = c.span_text.find(c.value_text)
    return (c.span_start + k, c.span_start + k + len(c.value_text)) if k >= 0 else (c.span_start, c.span_end)


def align(teacher: list[Any], model: list[Any]) -> tuple[list[tuple[int, Optional[int]]], list[int]]:
    """Teacher claim index -> model claim index on the same source number (same basis first); unaligned model ones."""
    used: set[int] = set()
    pairs: list[tuple[int, Optional[int]]] = []
    for ti, t in enumerate(teacher):
        tr = value_range(t)
        cands = [mi for mi, m in enumerate(model) if mi not in used and value_range(m) == tr]
        same_basis = [mi for mi in cands if model[mi].basis == t.basis]
        pick = (same_basis or cands or [None])[0]
        if pick is not None:
            used.add(pick)
        pairs.append((ti, pick))
    return pairs, [mi for mi in range(len(model)) if mi not in used]


def gold_matches(entries: list[dict], gold: list[dict]) -> tuple[set[int], set[int], set[int]]:
    """bench.match_gold, also returning which gold claims were strictly matched."""
    strict_pairs, value_pairs = [], []
    for gi, g in enumerate(gold):
        target = g["value"] * bench.SCALE[g["scale"]]
        for ei, e in enumerate(entries):
            if abs(e["value"] * bench.SCALE.get(e["scale"], 1.0) - target) > max(1e-9, 1e-6 * abs(target)):
                continue
            value_pairs.append((gi, ei))
            if (e["metric"] in g["canonical"] and (g["basis"] is None or e["basis"] == g["basis"])
                    and (not g["period"] or bench._text_match(e["period_text"], g["period"]))):
                strict_pairs.append((gi, ei))
    strict = bench._assign(strict_pairs)
    return {e for _, e in strict}, {e for _, e in bench._assign(value_pairs)}, {g for g, _ in strict}


def load_samples(path: Path) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = {}
    for r in rows:
        if r.get("ok"):
            ok[(r["id"], r["model"])] = r
    return ok, rows


def self_test_samples(test: list[bd.Passage]) -> list[dict]:
    """tuned = the teacher's answer exactly; base = the same answer in a code fence with its last claim removed."""
    rows = []
    for r in test:
        obj = json.loads(r.teacher_text)
        cut = {"claims": obj["claims"][:-1]}
        for model, text in (("tuned", r.teacher_text), ("base", "```json\n" + json.dumps(cut) + "\n```")):
            rows.append({"id": r.key, "model": model, "ok": True, "visible": text, "terminated": True,
                         "completion_tokens": 10, "visible_tokens": 10, "reasoning_tokens_approx": 0,
                         "cost_usd": 0.0})
    return rows


def evaluate(test: list[bd.Passage], samples: dict[tuple[str, str], dict], all_rows: list[dict]) -> dict[str, Any]:
    store = Store(ROOT / "data/facts.sqlite", ROOT / "data/pr_facts")
    paired = [r for r in test if (r.key, "base") in samples and (r.key, "tuned") in samples]
    texts = {r.key: {"teacher": r.teacher_text, "base": samples[(r.key, "base")].get("visible") or "",
                     "tuned": samples[(r.key, "tuned")].get("visible") or ""} for r in paired}
    res: dict[str, Any] = {"test_passages": len(test), "paired_passages": len(paired),
                           "paired_by_corpus": dict(Counter(r.corpus for r in paired)),
                           "unpaired": [r.key for r in test if r not in paired]}
    an: dict[str, dict[str, dict]] = {r.key: {} for r in paired}
    for r in paired:
        as_of = _verify.to_utc(r.as_of) if r.as_of else None
        for s in SOURCES:
            a = analyze(r.passage, texts[r.key][s])
            a["verdicts"] = [verdict_for(c, store, as_of, r.context) for c in a["claims"]]
            an[r.key][s] = a

    # schema, termination, tokens
    schema: dict[str, Any] = {}
    for s in SOURCES:
        rows = [an[r.key][s] for r in paired]
        d = {"passages": len(rows), "empty": sum(a["empty"] for a in rows),
             "pure_json_strict": sum(a["pure_json_strict"] for a in rows),
             "strict_after_repair": sum(a["strict_after_repair"] for a in rows),
             "repairs": dict(Counter(a["repair"] for a in rows if a["repair"])),
             "lenient_unusable": sum(a["lenient_status"] != "ok" for a in rows),
             "lenient_status": dict(Counter(a["lenient_status"] for a in rows)),
             "claims_rejected": sum(a["claims_rejected"] for a in rows),
             "raw_claims": sum(a["raw_claims"] for a in rows), "built_claims": sum(len(a["claims"]) for a in rows),
             "claims_dropped_by_code": sum(a["dropped"] for a in rows)}
        if s != "teacher":
            recs = [samples[(r.key, s)] for r in paired]
            d["unterminated"] = sum(not x.get("terminated") for x in recs)
            d["other_recipients"] = sum(bool(x.get("other_recipients")) for x in recs)
            for k in ("completion_tokens", "visible_tokens", "reasoning_tokens_approx"):
                v = [int(x.get(k) or 0) for x in recs]
                d[k] = {"sum": sum(v), "mean": round(statistics.mean(v), 1), "median": statistics.median(v),
                        "p90": sorted(v)[int(0.9 * (len(v) - 1))], "max": max(v)}
        schema[s] = d
    res["schema"] = schema

    # gold
    gold = bench.load_gold()
    gold_rows = [r for r in paired if r.corpus == "gold"]
    gres: dict[str, Any] = {"gold_passages_paired": len(gold_rows), "gold_claims": len(gold)}
    gold_hits: dict[str, list[bool]] = {}
    for s in SOURCES:
        claims = [c for r in gold_rows for c in an[r.key][s]["claims"]]
        verdicts = [v for r in gold_rows for v in an[r.key][s]["verdicts"]]
        entries = [{"metric": c.metric, "value": c.value, "scale": c.scale, "basis": c.basis,
                    "period_text": c.period_text} for c in claims]
        strict, by_value, gold_idx = gold_matches(entries, gold)
        gold_hits[s] = [gi in gold_idx for gi in range(len(gold))]
        unmatched = [i for i in range(len(claims)) if i not in strict]
        gres[s] = {"shipped_claims": len(claims), "strict_matched": len(strict), "value_matched": len(by_value),
                   "label_errors": max(0, len(by_value) - len(strict)),
                   "recall": round(len(strict) / len(gold), 4),
                   "precision": round(len(strict) / len(claims), 4) if claims else None,
                   "unflagged_errors": sum(verdicts[i].status == "verified" for i in unmatched),
                   "verdicts": dict(Counter(v.status for v in verdicts))}
    res["gold"] = gres

    # teacher-relative fields and verdicts
    teach: dict[str, Any] = {}
    exact_set: dict[str, list[bool]] = {}
    verdict_eq: dict[str, list[bool]] = {}
    zero_keys = ("teacher_claims", "aligned", "missing", "teacher_wrong_missing", "eq_all4", "status_agree",
                 "status_reason_agree", "teacher_wrong_model_verified", "teacher_verified_model_wrong", "extra",
                 "extra_verified", "extra_wrong", *(f"eq_{f}" for f in FIELDS + EXTRA_FIELDS))
    for s in ("base", "tuned", "teacher"):
        c = Counter({k: 0 for k in zero_keys})
        flags_exact, flags_verdict = [], []
        for r in paired:
            T, M = an[r.key]["teacher"], an[r.key][s]
            pairs, extra = align(T["claims"], M["claims"])
            all_fields = all_status = True
            for ti, mi in pairs:
                c["teacher_claims"] += 1
                t = T["claims"][ti]
                if mi is None:
                    c["missing"] += 1
                    all_fields = all_status = False
                    if T["verdicts"][ti].status == "wrong":
                        c["teacher_wrong_missing"] += 1
                    continue
                m = M["claims"][mi]
                c["aligned"] += 1
                eq = {f: getattr(t, f) == getattr(m, f) for f in FIELDS}
                for f in FIELDS:
                    c[f"eq_{f}"] += eq[f]
                for f in EXTRA_FIELDS:
                    c[f"eq_{f}"] += getattr(t, f) == getattr(m, f)
                c["eq_all4"] += all(eq.values())
                all_fields &= all(eq.values())
                tv, mv = T["verdicts"][ti], M["verdicts"][mi]
                c["status_agree"] += tv.status == mv.status
                c["status_reason_agree"] += tv.status == mv.status and tv.reason == mv.reason
                all_status &= tv.status == mv.status
                c[f"teacher_{tv.status}"] += 1
                if tv.status == "wrong" and mv.status == "verified":
                    c["teacher_wrong_model_verified"] += 1
                if tv.status == "verified" and mv.status == "wrong":
                    c["teacher_verified_model_wrong"] += 1
            c["extra"] += len(extra)
            c["extra_verified"] += sum(M["verdicts"][mi].status == "verified" for mi in extra)
            c["extra_wrong"] += sum(M["verdicts"][mi].status == "wrong" for mi in extra)
            flags_exact.append(all_fields and not extra)
            flags_verdict.append(all_status and not extra)
        c["passages_exact_claim_set"] = sum(flags_exact)
        c["passages_verdict_equivalent"] = sum(flags_verdict)
        teach[s] = dict(c)
        exact_set[s], verdict_eq[s] = flags_exact, flags_verdict
    res["teacher_relative"] = teach

    # seeded-error items whose passages are all paired (matched by passage text and context, not request hash)
    by_text = {(r.passage, r.context): r.key for r in paired}

    def item_parts(it: dict) -> tuple[Optional[str], list[tuple[int, str, Optional[str]]]]:
        ctx = bd.resolve_context(it["text"], it["context"])
        return ctx, [(st, p, by_text.get((p, ctx))) for st, p in ex.split_passages(it["text"])]

    item_scores: dict[str, Any] = {}
    caught_flags: dict[str, list[bool]] = defaultdict(list)
    for corpus in ("v0", "trap_v1"):
        items = [it for it in harness.load_split("dev", corpus)
                 if (parts := item_parts(it)[1]) and all(k is not None for _, _, k in parts)]
        per_source = {}
        for s in SOURCES:
            results = {}
            for it in items:
                ctx, parts = item_parts(it)
                as_of = _verify.to_utc(it["as_of"]) if it.get("as_of") else None
                claims: list[Any] = []
                for st, p, k in parts:
                    claims += analyze(p, texts[k][s], start=st)["claims"]
                pairs = [[c.model_dump(mode="json"), verdict_for(c, store, as_of, ctx).model_dump(mode="json")]
                         for c in claims]
                results[it["item_id"]] = {"pairs": pairs, "error": None, "latency_s": 0.0}
            scored = harness.score(items, results) if corpus == "v0" else harness.score_traps(items, results)
            per_source[s] = {k: v for k, v in scored.items() if k != "rows"}
            if corpus == "v0":
                for row in scored["rows"]:
                    if "match" in row:
                        caught_flags[s].append(row.get("outcome") == "caught")
            else:
                for row in scored["rows"]:
                    caught_flags[s] += [e["outcome"] == "caught" for e in row.get("errors", [])]
        item_scores[corpus] = {"items": len(items), **per_source}
    res["items"] = item_scores

    # paired tests
    res["paired_tests"] = {
        "gold_claim_strict_match": mcnemar(gold_hits["base"], gold_hits["tuned"]),
        "passage_exact_claim_set_vs_teacher": mcnemar(exact_set["base"], exact_set["tuned"]),
        "passage_verdict_equivalent_vs_teacher": mcnemar(verdict_eq["base"], verdict_eq["tuned"]),
        "passage_pure_json_strict": mcnemar([an[r.key]["base"]["pure_json_strict"] for r in paired],
                                            [an[r.key]["tuned"]["pure_json_strict"] for r in paired]),
        "seeded_error_caught": mcnemar(caught_flags["base"], caught_flags["tuned"]),
    }
    res["cost"] = {"sampled_requests": len(all_rows), "failed_requests": sum(not r.get("ok") for r in all_rows),
                   "sampling_usd_estimate": round(sum(float(r.get("cost_usd") or 0) for r in all_rows), 6),
                   "cost_basis": dict(Counter(r.get("cost_basis") for r in all_rows))}
    res["_analysis"] = an  # stripped before saving
    res["_paired"] = paired
    return res


def hand(res: dict[str, Any], samples: dict[tuple[str, str], dict], n: int) -> None:
    paired = res["_paired"]
    pick = random.Random(HAND_SEED).sample(paired, min(n, len(paired)))
    for i, r in enumerate(pick, 1):
        print(f"\n=== {i}. {r.corpus} {r.key[:12]} ctx={r.context!r}\n{r.passage}")
        for s in SOURCES:
            a = res["_analysis"][r.key][s]
            claims = "; ".join(f"{c.value_text}|{c.metric}|{c.basis}|{c.period_text}|"
                               f"{v.status}" for c, v in zip(a["claims"], a["verdicts"]))
            extra = ""
            if s != "teacher":
                x = samples[(r.key, s)]
                extra = (f" [tokens {x.get('completion_tokens')}, reasoning~{x.get('reasoning_tokens_approx')}, "
                         f"terminated {x.get('terminated')}, repair {a['repair']}, rejected {a['claims_rejected']}]")
            print(f"--- {s}{extra}: {claims or '(no claims)'}")
            if s != "teacher" and a["lenient_status"] != "ok":
                print("    answer head:", repr((samples[(r.key, s)].get('visible') or '')[:300]))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=Path)
    ap.add_argument("--test-meta", type=Path, help="test_meta.jsonl written by build_data.py (default: rebuild)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--hand", type=int, default=0, help="print this many seeded-random passages for reading")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.test_meta:
        test = [bd.Passage(**json.loads(line), split="test")
                for line in args.test_meta.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        built = bd.build()
        rows = built["dev"] + built["gold"]
        test = sorted((r for r in rows if r.split == "test"),
                      key=lambda r: (r.corpus != "gold", bd.sha256(f"{bd.SEED}|{r.key}")))
    if args.self_test:
        all_rows = self_test_samples(test)
        samples = {(x["id"], x["model"]): x for x in all_rows}
    else:
        samples, all_rows = load_samples(args.samples)
        unknown = {k for k, _ in samples} - {r.key for r in test}
        if unknown:
            raise SystemExit(f"{len(unknown)} sampled ids are not test passages of this build")
    res = evaluate(test, samples, all_rows)
    if args.hand:
        hand(res, samples, args.hand)
    clean = {k: v for k, v in res.items() if not k.startswith("_")}
    clean["source_sha256"] = {rel: bd.file_sha256(rel) for rel in bd.HASHED_SOURCES + ("eval/glimmer_sft/evaluate.py",)}
    print(json.dumps({k: clean[k] for k in ("paired_passages", "schema", "gold", "teacher_relative", "paired_tests")},
                     indent=1, default=str))
    if args.out:
        args.out.write_text(json.dumps(clean, indent=1, default=str) + "\n", encoding="utf-8")
    if args.self_test:
        t, b = clean["teacher_relative"]["tuned"], clean["teacher_relative"]["base"]
        assert t["passages_exact_claim_set"] == clean["paired_passages"] == t["passages_verdict_equivalent"], t
        assert t["missing"] == 0 and t["extra"] == 0 and t["status_agree"] == t["teacher_claims"], t
        assert clean["gold"]["tuned"] == clean["gold"]["teacher"], clean["gold"]
        assert b["missing"] > 0 and clean["schema"]["base"]["repairs"].get("code_fence") == clean["paired_passages"], b
        assert clean["schema"]["base"]["pure_json_strict"] == 0, clean["schema"]["base"]
        print("self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
