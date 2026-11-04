# NoCap — submission copy

Copy for the Cerebral Valley submission form, in the form's field order. Each long field is at most 250 words; this is the copy as submitted on November 5. Every statement was checked against the deployed release and the public repository on November 4. Sources for every number: [evidence map](evidence-map.md).

## Short fields

| Field | Entry |
|---|---|
| Team name | NoCap |
| Team members | SHOU-FENG CHANG (`Robert_Chang`), known as Robert Chang |
| Track | Indie Developer |
| Theme | Build for Your World |
| Most Viral X post | https://x.com/showphone0609/status/2118034578598473894 |
| Public GitHub repository | https://github.com/changshoufeng0609-cmd/nocap |
| 3-minute demo video | https://receipts-nocap.vercel.app/demo |

## Project description

Financial journalists and equity analysts publish earnings stories on deadline, and one wrong figure means a public correction. AI now drafts more of that copy, which makes numbers cheap to write and costly to check. NoCap checks every financial number in a draft against SEC filings before it runs, and shows the filing behind each.

Paste a draft about any S&P 500 company and press Check. Each figure comes back ✓ verified, ✗ wrong, ◷ not public yet or ? unverifiable. Click a figure for its receipt: the filed value, the reason, and the 8-K, 10-Q or 10-K with its accession number and EDGAR acceptance time. Fix with receipts proposes corrected numbers and rechecks the whole draft. Check a slide and Ask apply the same verifier to earnings slides and questions.

Meta models read; code decides. Muse Spark turns prose, tables and charts into typed claims, and deterministic code verifies each one against filed data. A number code can't confirm gets ?, never a guess.

Proof, from frozen tests run once:
- One-shot test of the November 2 release (1,000 items, 400 S&P 500 companies): 306 of 360 planted errors caught, none marked verified; a ✓ was right 736 of 738 times.
- Held-out seeded errors: 44 of 48 caught; the 4 misses came back ?, never ✓.
- Trap drafts written by Muse Wonder: 20 of 20 planted errors caught.

Free, no sign-up, through November 17; built during the hackathon.

## How we used Meta products

NoCap uses five Meta models. Each one proposes; deterministic code checks the output before it reaches a verdict.

Muse Spark 1.3 (Meta Model API, strict JSON schemas)
- Extracts typed claims (metric, value, period, basis) from drafts. Check: code locates each quote and re-reads the number itself.
- Mapped 8-K earnings tables for 467 of the 500 companies in one day ($42.97). Check: a cell is kept only when code's own parse matches.
- Reads a whole 10-K in one request (1M-token context): Meta's 10-K, 101,135 tokens, 6.1 seconds, $0.129. Check: only exact, unique quotes are accepted.
- Ask: turns a question into a lookup plan with no answer field. Check: the question must name company, metric, period and basis.

SAM 3.1 + Muse Spark vision
- SAM finds chart regions on slides; Spark reads the printed figures. On a real Bank of America SEC slide: both bar charts found, all 5 GAAP bars verified. Check: axis estimates get only ≈ or ?.

Muse Wonder 1.0
- Fix with receipts proposes corrected numbers. Check: only digits may change; each fix must verify; then the whole draft is rechecked.
- Wrote trap drafts; code derived every gold label from the filings. NoCap caught 20 of 20 planted errors.

Muse Spark Open (Fireworks, Baseten) and Muse Glimmer (Modal)
- Same schema and verifier. Spark Open matched 27 of 27 gold claims on both providers. Glimmer, self-hosted on the operator's Modal, powers Private check.

All hosted calls use Standard-mode model IDs.

## Why Meta models matter

An earnings draft mixes prose, tables and charts. A fact-checker must turn all of it into data that code can check, and Meta's models supply each capability that step needs.

Strict structured output: Muse Spark returns claims, table mappings and lookup plans that match a schema, so code can verify every field. At minimal reasoning effort, a live check of our 11-claim NVIDIA example took 3.9 seconds and $0.013.

1M-token context: a whole 10-K fits in one request, so quotations come from the complete filing, not retrieved fragments.

Vision + SAM 3.1: earnings slides become regions and printed figures that go through the same verifier as prose.

Muse Wonder: writes complete stories with planted errors, a harder test than single sentences, and proposes the edits that turn a red flag into a corrected draft.

Open weights: an unpublished earnings story is sensitive. Muse Glimmer on an operator's own Modal deployment runs the same verifier without sending the draft to a hosted model API.

The models also show why the verifier must exist. Answering 50 financial questions each from memory, four Meta models gave 17 assessable wrong answers. NoCap's gate withheld all 17.

## Meta product feedback

Not a field on the form; kept here for the record.

Structured output at minimal reasoning effort made per-passage extraction practical, but a schema-valid answer is not a correct one, and most of our engineering went into that gap. In our first test, Spark's own character offsets were exact only 67% of the time, even though every quoted span appeared verbatim, so code now computes offsets; dropping model offsets also cut that run from 80–100 seconds to 27. Free-text metric names agreed across runs only about half the time, so we moved to a closed metric enum. Tried as an extractor, Muse Wonder returned 9 of 23 claims with the wrong unit; it works better for us as a writer and editor.

SAM 3.1 was sensitive to the concept phrase: generic "chart" and "graph" prompts missed a bank slide's bars, while "bar chart" worked. It found no usable region on our seeded table slide, and a zero-match result is an empty string that still bills one image, so an explicit no-match status would make fallbacks easier to report. Spark grounding coordinates occasionally came back as 1001 on the 0–1000 scale, so we clamp them, and large inline images needed `store: false` to avoid a 413.

Our main request is per-field grounding diagnostics: which input span supports each structured-output field, so developers can tell a well-formed answer from a supported one.


## Partner technology feedback

Fireworks and Baseten ran Muse Spark Open via OpenAI-compatible APIs (27/27 gold claims). Modal hosted Muse Glimmer privately; per-request cost reporting would help.
