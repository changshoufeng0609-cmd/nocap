"""Offline checks that glimmer_format.py renders Muse Glimmer chats exactly like the official template.

1. template (needs jinja2): render_text() equals the model's own chat_template.jinja (meta-models/Muse-Glimmer-30B,
   pinned revision) for NoCap rows and the workshop example, with and without a generation prompt.
2. workshop (needs tokenizers): the workshop notebook's printed "prepare" report renders back byte for byte, with the
   same total token count and trained-token count the cookbook renderer reported.
3. tokens (needs tokenizers): for every row, encoding the cookbook's pieces separately equals encoding the whole
   rendered string, and the trained tokens decode to exactly " to=user<|message|>" + answer + "<|eot|>".

    python eval/glimmer_sft/selfcheck.py --template chat_template.jinja --tokenizer tokenizer.json \\
        --notebook muse_glimmer_train_router.ipynb --rows train.jsonl test.jsonl [--stats out.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glimmer_format as gf  # noqa: E402


def official(template: str, messages: list[dict], strength: str, gen: bool) -> str:
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    def raise_exception(msg: str) -> None:
        raise jinja2.TemplateError(msg)

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
    env.filters["tojson"] = lambda v, **kw: json.dumps(v, ensure_ascii=False)
    env.globals["raise_exception"] = raise_exception
    return env.from_string(template).render(messages=messages, bos_token=gf.BOS, add_generation_prompt=gen,
                                            reasoning_strength=strength, tools=None)


def workshop_example(notebook: Path) -> dict:
    nb = json.loads(notebook.read_text(encoding="utf-8"))
    for cell in nb["cells"]:
        for out in cell.get("outputs", []):
            text = "".join(out.get("text") or [])  # stream output: a log line, then the JSON report
            if '"example_rendered"' in text:
                return json.loads(text[text.index("{"):text.rindex("}") + 1])
    raise SystemExit("no prepare report in the notebook")


def split_rendered(rendered: str, strength: str) -> list[dict]:
    sys_head, rest = rendered.split(gf.START + "system" + gf.MESSAGE, 1)
    assert sys_head == gf.BOS
    system, rest = rest.split("\n\nReasoning strength: " + strength + ".\n\n" + gf.RECIPIENTS + gf.EOT, 1)
    user, rest = rest.removeprefix(gf.START + "user" + gf.MESSAGE).split(gf.EOT, 1)
    answer = rest.removeprefix(gf.START + "assistant to=user" + gf.MESSAGE).removesuffix(gf.EOT)
    return [{"role": "system", "content": system}, {"role": "user", "content": user},
            {"role": "assistant", "content": answer}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", type=Path)
    ap.add_argument("--tokenizer", type=Path)
    ap.add_argument("--notebook", type=Path)
    ap.add_argument("--rows", type=Path, nargs="*", default=[])
    ap.add_argument("--stats", type=Path, help="write token statistics as JSON")
    args = ap.parse_args(argv)
    rows = {p.name: [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
            for p in args.rows}
    report: dict = {}
    example = workshop_example(args.notebook) if args.notebook else None

    if args.template:
        template = args.template.read_text(encoding="utf-8")
        cases = [(r["messages"], "low") for part in rows.values() for r in part]
        if example:
            cases.append((split_rendered(example["example_rendered"], "high"), "high"))
        bad = 0
        for messages, strength in cases:
            for gen in (False, True):
                msgs = messages[:-1] if gen else messages
                bad += official(template, msgs, strength, gen) != gf.render_text(msgs, strength, gen)
        report["template"] = {"cases": 2 * len(cases), "mismatches": bad}
        print(f"template: {2 * len(cases)} renderings compared with chat_template.jinja, {bad} mismatches")

    if args.tokenizer:
        if example:
            fmt_high = gf.GlimmerFormat(str(args.tokenizer), reasoning_strength="high")
            msgs = split_rendered(example["example_rendered"], "high")
            tokens, weights = fmt_high.supervised(msgs)
            ok = (fmt_high.decode(tokens) == example["example_rendered"]
                  and len(tokens) == example["example_tokens"]
                  and int(sum(w > 0 for w in weights)) == example["example_trained_tokens"])
            report["workshop"] = {"tokens": len(tokens), "expected_tokens": example["example_tokens"],
                                  "trained": int(sum(w > 0 for w in weights)),
                                  "expected_trained": example["example_trained_tokens"], "match": ok}
            print(f"workshop example: {report['workshop']}")
        fmt = gf.GlimmerFormat(str(args.tokenizer), reasoning_strength="low")
        stats: dict = {}
        for name, part in rows.items():
            mismatched = 0
            prompt_lens, target_lens, totals = [], [], []
            for r in part:
                tokens, weights = fmt.supervised(r["messages"])
                whole = fmt.encode(gf.render_text(r["messages"], "low"))
                trained = [t for t, w in zip(tokens, weights) if w > 0]
                expect = " to=user" + gf.MESSAGE + r["messages"][-1]["content"] + gf.EOT
                mismatched += tokens != whole or fmt.decode(trained) != expect
                prompt = fmt.prompt(r["messages"])
                assert prompt == tokens[:len(prompt)], "the generation prompt must prefix the training tokens"
                prompt_lens.append(len(prompt))
                target_lens.append(len(trained))
                totals.append(len(tokens))
            stats[name] = {"rows": len(part), "mismatches": mismatched,
                           "prompt_tokens": {"min": min(prompt_lens), "median": statistics.median(prompt_lens),
                                             "max": max(prompt_lens), "sum": sum(prompt_lens)},
                           "target_tokens": {"min": min(target_lens), "median": statistics.median(target_lens),
                                             "max": max(target_lens), "sum": sum(target_lens)},
                           "total_tokens_max": max(totals)}
            print(f"{name}: {json.dumps(stats[name])}")
        report["tokens"] = stats
    if args.stats:
        args.stats.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    failed = (report.get("template", {}).get("mismatches") or 0) or not report.get("workshop", {"match": True})["match"] \
        or any(s["mismatches"] for s in report.get("tokens", {}).values())
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
