# NoCap — submission copy

Copy for the Cerebral Valley submission form, in the form's field order. Each long field is 150–250 words. A `<!-- verify-at-release -->` comment follows each statement that becomes true at release; check it, then delete the comments before pasting. Sources for every number: [evidence map](evidence-map.md).

## Short fields

| Field | Entry |
|---|---|
| Team name | NoCap |
| Team members | SHOU-FENG CHANG (`Robert_Chang`), known as Robert Chang |
| Track | Indie Developer |
| Theme | Build for Your World |
| Most Viral X post (optional) | The published post's URL |
| Public GitHub repository | https://github.com/changshoufeng0609-cmd/nocap <!-- verify-at-release --> |
| 3-minute demo video | https://receipts-nocap.vercel.app/demo <!-- verify-at-release --> |

## Project description

NoCap checks an earnings draft's financial numbers against SEC filings before publication. It is built for financial journalists and equity analysts on deadline, for whom one wrong figure means a public correction. The idea came from the author's earlier portfolio agent, whose AI-generated numbers could not be trusted.

Paste a draft and NoCap marks each figure ✓ verified, ✗ wrong, ◷ not public yet or ? unverifiable. Click a figure for its receipt: the filed value, the reason, the arithmetic for derived figures, and the 8-K, 10-Q or 10-K with its accession number and EDGAR acceptance time. Fix with receipts proposes corrected numbers and rechecks the whole draft; Check a slide and Ask apply the same verifier to chart images and questions.

In a frozen held-out test, NoCap caught 44 of 48 planted errors (91.7%), with 1 false alarm on 47 unmodified sentences, and all 20 errors in seven AI-written trap drafts. It answers ? rather than guessing: 20% of true figures in the seeded held-out test stayed ?. It covers every S&P 500 company, mainly the latest eight quarters.

Try it at https://receipts-nocap.vercel.app: live checks run through November 17, 16:00 UTC, under a $5 daily cap, and saved examples always work. <!-- verify-at-release --> All code was built during the hackathon, from October 27. The full history stays private because it contains narration audio that could not be shared publicly; a public branch reconstructs it. <!-- verify-at-release --> The video records the real interface with cached and recorded model responses and a synthetic voice.

## How we used Meta products

NoCap uses five Meta models, and deterministic code checks every output before it can affect a verdict.

Muse Spark 1.3 (Meta Model API, strict JSON schemas) extracts typed claims from drafts, maps 8-K earnings tables (for 467 S&P 500 companies in one day, $42.97), reads printed figures on slides, plans Ask lookups, and reads a complete 10-K in one request to find exact quotations: Meta's 10-K took 101,135 tokens and six seconds. Code re-reads every quoted number itself, keeps a table cell only when its own parse matches, limits chart-axis readings to ≈ or ?, requires a lookup's company, metric, period and basis to appear in the question, and accepts only exact, unique quotes.

Muse Wonder 1.0 proposes corrected numbers in Fix with receipts; code allows digit-only edits, verifies each one, then rechecks the whole draft. Wonder also wrote our trap drafts, whose gold labels code re-derived from filings; NoCap caught 20 of 20 planted errors.

SAM 3.1 proposes chart regions. On a real Bank of America earnings slide filed with the SEC, it found both bar charts and all five GAAP net-interest-income bars verified; when SAM finds nothing, Spark grounding supplies the region and the interface says so.

Open weights: Muse Spark Open on Fireworks and Baseten matched 27 of 27 gold claims with the same contract. Muse Glimmer, served with vLLM on the operator's own Modal workspace, powers Private check; a browser-to-Modal test returned 1 ✓ and 1 ✗. All hosted calls use Standard-mode IDs.

## Why Meta models matter

An earnings draft mixes prose, tables and charts, and a fact-checker must first turn each into typed data. Muse Spark's strict structured output gives every input a typed form that code can check: claims from prose and slide images, row mappings from 8-K tables, lookup plans from questions. At minimal reasoning effort, a live check of our 11-claim NVIDIA example took 3.9 seconds and $0.013. Spark's 1M-token context takes a whole 10-K in one request, so quotations come from the complete filing, not retrieved fragments. Muse Wonder writes complete stories with planted errors, a harder test than single sentences, and its edit proposals turn a red flag into a corrected draft.

The models also show why verification has to exist. In a closed-book test, Spark, Wonder, Spark Open and Glimmer each answered 50 financial questions from memory and produced 17 assessable wrong answers among the 200. NoCap's gate withheld all 17 and accepted 37 correct answers, with only 0–34% coverage per model. (Post-run analysis that only normalizes unit and period labels; the pre-registered parser accepted none.) Every answer is public at https://receipts-nocap.vercel.app/benchmarks. <!-- verify-at-release --> NoCap follows the Agents 102 workshop's verifiable-agent practices: verification is the submit step, deterministic checks come first, Wonder's edits are allow-listed, and whatever code can't confirm goes to the editor.

Open weights matter for newsrooms. An unpublished earnings story is sensitive, and Muse Glimmer on the operator's own Modal deployment lets the same verifier run without sending the draft to a hosted model API.

## Meta product feedback

Structured output at minimal reasoning effort made per-passage extraction practical, but a schema-valid answer is not a correct one, and most of our engineering went into that gap. In our first test, Spark's own character offsets were exact only 67% of the time, even though every quoted span appeared verbatim, so code now computes offsets; dropping model offsets also cut that run from 80–100 seconds to 27. Free-text metric names agreed across runs only about half the time, so we moved to a closed metric enum. Tried as an extractor, Muse Wonder returned 9 of 23 claims with the wrong unit; it works better for us as a writer and editor.

SAM 3.1 was sensitive to the concept phrase: generic "chart" and "graph" prompts missed a bank slide's bars, while "bar chart" worked. It found no usable region on our seeded table slide, and a zero-match result is an empty string that still bills one image, so an explicit no-match status would make fallbacks easier to report. Spark grounding coordinates occasionally came back as 1001 on the 0–1000 scale, so we clamp them, and large inline images needed `store: false` to avoid a 413.

Our main request is per-field grounding diagnostics: which input span supports each structured-output field, so developers can tell a well-formed answer from a supported one.

## Partner technology feedback

Fireworks and Baseten both serve Muse Spark Open through OpenAI-compatible APIs, so a single extractor serves Meta's Responses API and both partners; each backend is a base URL, a model ID and an output mode. The differences were in structured output. Meta's Fireworks guide documents JSON mode, and Fireworks' `json_schema` mode turns reasoning output off, so we use JSON mode with the schema in the prompt. Baseten's Model APIs exclude Muse Spark Open from JSON mode and structured output, so we send the schema in the prompt and validate every claim in code. Baseten's hackathon key also needs the model ID `meta-models/muse-spark-open-1026` rather than the documented name. Both providers matched 27 of 27 gold claims. The 10-passage gold check took 71.7 seconds on Fireworks and 5.05 seconds on Baseten, on different days and with different provider prompt caching, so this is not a controlled ranking.

Modal gave us a private deployment for Muse Glimmer: vLLM on one H100, scaling to zero after 60 idle seconds. The first deployment needed image, server-origin and vLLM flag fixes, early requests can return 503, and a warm-up took 125.5 seconds before a 29.9-second browser check. Proxy tokens are workspace-scoped unless the workspace uses RBAC, which matters for a credential that guards private drafts. GPU usage arrives in hourly reports and can lag, so per-request cost reporting would help apps that budget every request.
