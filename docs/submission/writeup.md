# NoCap — submission copy

**Working submission draft · earlier form copy filled, not submitted · updated 2026-10-29.** The earlier public workspace, source and licensed video passed anonymous access checks; live model requests remain paused. J2 core fixes, regression validation and the frozen held-out evaluation are complete. The new 165-second walkthrough and benchmark page await public acceptance. Refresh the form after final acceptance; editing this file does not change the form. These modules cover the project description, Meta usage/value and product/partner feedback fields. No input `maxlength` was exposed; final server-side limits remain unconfirmed. Product name: **NoCap**. Team: **NoCap**. Track: **Indie Developer**. Theme: **Build for Your World**. Sole contributor: **SHOU-FENG CHANG (`Robert_Chang`)**, known as **Robert Chang**. No submission or final consent acceptance has occurred.

## One-line description

NoCap checks financial claims in earnings drafts against SEC filings, with evidence receipts, constrained numeric corrections, and checks for uploaded slide images.

## Short introduction

NoCap helps financial journalists and equity analysts review numbers before publishing. Muse Spark extracts financial claims; deterministic code checks them against SEC filings. Each supported verdict links to its evidence. Muse Wonder proposes numeric corrections that must survive a complete recheck. SAM 3.1 and Spark bring the same workflow to slide images. Ambiguity stays visible, and an optional historical cutoff flags evidence filed too late. Built by Robert Chang, Team NoCap, for Build for Your World.

## Problem, users and motivation

An earnings draft can sound convincing while quoting adjusted EPS as GAAP, mixing fiscal quarters, or confusing millions with billions. Reviewing it means tracing each figure back to a filing while preserving its company, period, accounting basis and publication context.

NoCap is built for financial journalists and equity analysts working on deadline. The idea came from the author's earlier Taiwan portfolio agent, where unreliable AI-generated financial numbers exposed the need for a separate verification step. NoCap turns that need into a focused editorial workflow: inspect the claim, inspect its evidence, and decide what can be published. We have prepared a small formative study, but have not conducted participant sessions or measured time savings.

## What the product does

Paste a draft and optionally supply a company/period context and an as-of cutoff. NoCap annotates extracted figures as verified, wrong, unverifiable or approximate. The interface separates “Not public yet” from numerical errors. Click a figure to inspect the filed value, reason, source link, accession number, EDGAR acceptance time, and available source cell or derivation.

For eligible errors, **Fix with receipts** proposes numeric substitutions. Code constrains each edit and rechecks the entire revised draft before presenting the result alongside the original. Unresolved claims remain visible; unsupported corrections stay for manual review.

**Check a slide** accepts one PNG/JPEG and connects clickable numeric readings to the same SEC evidence. Printed labels can receive exact verdicts; readings estimated from axes can only be approximate or unverifiable. An uploaded image is an input to check, never filing evidence. Visual transcription can miss or misread figures, so users must confirm each reading against the image.

**Ask with receipts** supports a focused financial question about one company and explicit fiscal period. Spark proposes lookup parameters; a separate deterministic guard checks them against the question. Code retrieves the visible facts, writes each answer sentence and verifies it before inclusion. Advice, comparisons and ambiguous requests require clarification. A whole-filing quote workflow is also implemented: one complete, pre-ingested SEC filing is supplied to Spark, and code accepts only exact, uniquely located quotes with source hashes and acceptance cutoffs. A quote proves its location, not its relevance or financial truth. Real whole-filing acquisition/live acceptance is pending a valid declared SEC contact.

**Private check** is an explicit draft-only route to Muse Glimmer in the operator's Modal workspace, with no application response caching, redirects or fallback to Meta/partner APIs. It fails closed without valid setup. Modal and the NoCap host still process the draft; this is not offline computation. A real local browser → FastAPI HTTP → Modal → deterministic-verifier check returned 1 verified / 1 wrong from one model call, without a mocked response. Browser credential isolation, clear/mode-switch behavior and application-cache absence passed checks; all task apps were then confirmed stopped with zero tasks. This two-claim integration check does not establish public or always-on Private availability or general quality. A local **MCP stdio server** exposes read-only coverage, evidence search and typed-claim verification without making model or SEC calls itself.

## How we use Meta models

| Model | Implemented role | What code controls |
|---|---|---|
| Muse Spark 1.3 | Extracts typed text claims, maps earnings-release rows, reads slide labels, proposes Ask lookups and selects whole-filing quotes. | Locates verbatim spans, parses values, checks question/lookup agreement, validates source cells and exact quotes, and computes numeric verdicts. |
| Muse Wonder 1.0 | Proposes numeric corrections for Fix with receipts and generated adversarial development drafts. | Rejects unsafe edits, rechecks the full revision, and independently confirms planted-error labels against stored evidence. |
| SAM 3.1 | Proposes chart/table regions for uploaded slides. | Bounds region handling and reports its source. Spark supplies grounding when SAM returns no usable region. |
| Muse Spark Open on Fireworks and Baseten | Live-tested alternative extractors; Baseten also completed a fixed 50-item dev comparison. | Uses the same typed contract and deterministic verifier; no unrestricted answer-factuality claim. |
| Muse Glimmer on Modal | Real local Private browser/HTTP check and 50 fixed closed-book Q&A requests completed; all task apps stopped. | Fixes the operator endpoint, disallows public-provider fallback and application response caching, then applies the same verifier. Local acceptance is separate from publicly hosted availability. |

The hosted Meta models use Standard-mode IDs: `muse-spark-1.3`, `muse-wonder-1.0`, and `sam-3.1`.

## Technical depth

The core boundary is simple: **models propose; code decides**. A fixed metric vocabulary connects typed claims to SEC XBRL and earnings-release tables attached to 8-Ks. Numeric parsing and span locations come from the quoted input. The verifier handles written precision, directional bounds, scale, fiscal periods, GAAP/non-GAAP basis, entity attribution, and split-consistent per-share evidence.

Every fact carries provenance and EDGAR acceptance time. Historical checks restrict the eligible evidence; they do not claim to establish when information first appeared outside EDGAR. Derived quarters and growth figures preserve their supporting facts. The interface shows traceable growth and margin arithmetic when the evidence is unambiguous.

A Python/FastAPI API streams results to a Next.js interface. The evidence store contains **11,554 facts across 11 companies**, targeting the latest eight fiscal quarters. Tesla adds 379 XBRL/derived facts and 57 filings from cached SEC sources, while all original ten-company rows remain unchanged. Tesla has no image-based earnings table/non-GAAP coverage and EPS is available in 6/8 quarters. These records are not independent benchmark examples. Cached examples replay recorded verification without new model calls.

The integrated October 29 core fixes passed 1,246 full-suite Python tests and retained 144 frontend checks. J2 independently closed both semantic extraction/verification findings with 98 targeted regressions and eight unchanged before/after probes. Development baselines and all three saved-example counts were preserved. Earlier 905/119 and 1,117-plus-two-targeted checkpoints remain historical. The MCP server passed ten deterministic protocol scenarios with 21 tool calls and no model/SEC requests. The shared PostgreSQL ledger passed eight actual database checks plus hosted admission checks across deployments. Engineering checks are separate from model-quality evaluation and final public-deployment acceptance.

## Held-out evaluation

After the J2 fixes and implementation/evidence freeze, the owner-authorized one-time test run completed without result-driven tuning. All 126 frozen files remained unchanged; errors and skipped items were retained in accounting, with **zero errors/skips observed**.

| Measure | Seeded-error held-out, 97 items | Wonder trap held-out, 7 drafts |
|---|---:|---:|
| Planted errors caught | **44/48 (91.7%)** | **20/20 (100%)** |
| Correct error reason | 42/48 (87.5%) | 18/20 (90.0%) |
| False alarms | **1/47 original items; 1/110 true claims** | **0/52 true figures; 0/25 true sentences** |
| True claims/figures left unverifiable | 22/110 (20.0%) | 3/52 (5.8%) |

All four uncaught seeded errors remained unverifiable rather than verified; all ten trap-test guidance figures also remained unverifiable. The run made **117 model attempts** costing **$0.49834625** by recorded usage, with no estimated-usage attempts. These are small held-out portions of correlated, provisionally labeled corpora, not independent human adjudication or accuracy on arbitrary financial writing. The seeded test had **one original-item false alarm**; development zero-false-alarm results must not replace it. [Methods, complete results and records](../../eval/heldout_results.md).

## Development evaluation and limitations

The seeded-error and trap results below are development evaluations used during implementation. Their recorded Meta extraction was reused; separate open-weight live benchmarks ran on October 28 and 29. The closed-book Q&A comparison below is separate and did not use the held-out corpus.

| Measure | Seeded-error dev, 224 items | Wonder trap dev, 15 drafts |
|---|---:|---:|
| Planted errors caught | 104/109 (95.4%) | 40/40 (100%) |
| Correct error reason | 97/109 (89.0%) | 36/40 (90.0%) |
| False alarms | 0/107 unmodified items | 0/112 true figures; 0/58 true sentences |
| True claims/figures left unverifiable | 51/250 (20.4%) | 1/112 (0.9%) |

All five uncaught seeded errors remained unverifiable rather than verified. The trap set's 13 guidance figures also remained unverifiable. False-alarm denominators differ between the two sets and should not be combined.

These are small, implementation-facing development sets. Narrative sentences and table evidence come from the same releases and are correlated. Results do not establish general accuracy on arbitrary writing, correction success rates, or visual transcription accuracy. Coverage is limited to the snapshot's companies, periods and supported metrics. Extraction can miss claims, guidance cannot be verified as a realized result, and unclear claims may remain `?`. NoCap is a research and fact-checking tool, not investment advice.

## Open-weight validation

We ran Muse Spark Open live on Fireworks using `accounts/fireworks/models/muse-spark-open` and on Baseten using the hackathon model `meta-models/muse-spark-open-1026`. Each matched 27/27 provisional gold claims across ten passages, with strict precision of 27/28 (96.4%). The extra claim was a GAAP/non-GAAP expansion of a quoted guidance tolerance and remained unverifiable. The gold labels await independent human review; some guidance labels do not specify a period.

On the same separate fixed 13-item development subset, both completed 13/13 items and caught all six planted errors with correct reasons, with zero false alarms on six original items. These are small-set results, not full 224-item evaluations on the partner backends. Baseten's 23 benchmark calls cost $0.10923670 at published token rates, and its gold check took 5.05 seconds. These historical results are retained unchanged.

A subsequent fixed-seed 50-item dev sample compared cached Meta Spark with fresh Baseten Spark Open: both caught 24/24 seeded errors, assigned 23/24 expected reasons, produced 0/24 original false alarms and left both guidance controls unverifiable. Baseten used 48 successful new calls and two within-run reuses of identical extraction requests; separate as-of checks still ran for both items. Independent usage reconciliation gives **$0.24020610**, and a cache-only replay reproduced all 100 item outcomes. No gold/verifier changes were made to fit this sample. Different run dates, response modes and provider cache usage prevent a controlled speed/cost ranking. Meta Spark remains the default. The earlier [Modal pipeline smoke](../private-live-validation-2026-10-29.json) and subsequent [Private browser/HTTP acceptance](../private-http-live-validation-2026-10-29.json) are two-claim integration checks, separate from model-quality measurements. The latter shared its warm deployment with the Glimmer Q&A run below; all task apps were confirmed stopped with zero tasks.

## Closed-book Q&A comparison

Four backends answered the same **50 fixed questions each (200 completed calls)** without filings, retrieval or tools: Spark 1.3, Wonder 1.0, Baseten Spark Open and Modal Glimmer. Questions and provisional reference labels come from supported SEC snapshot facts. This tests direct model answers followed by a verification gate; it is not an evaluation of Ask's retrieval planner or a general hallucination-rate estimate. The frozen gate predates the J2 runtime fixes and remains unchanged; the Q&A results do not measure those later production changes.

The pre-registered exact metadata contract accepted zero answers. Those rejections mix unsupported unit/period spellings, format failures and binding/numeric problems and cannot be called financial errors. An explicitly **post-run metadata sensitivity analysis** normalized supported aliases without changing numeric text, companies, metrics or stated years. It accepted **14/50 Spark, 0/50 Wonder, 17/50 Baseten and 6/50 Glimmer** answers, with no incorrect answer accepted within this small supported set. Coverage was **28% / 0% / 34% / 12%**; Wonder's precision is **N/A**, not 100%. Its unsupported metadata and Glimmer's 37 format failures are not evidence of wrong financial knowledge. All 17 assessable wrong numbers/bindings were withheld, with substantial lost coverage.

All 200 original responses, failures and denominators remain available. Offline reproduction from public inputs matched every record and aggregate. The scorer and verifier share evidence sources; labels await independent human adjudication. Different formats and output/reasoning settings prevent a controlled provider ranking. Token-priced usage totaled **$0.568505**. Observed GPU metering was **$1.25997788 across three shared Private/Q&A attempts**, including **$0.82043920** for the successful warmup + Private + 50-question session. That amount is shared, may lag, and must not be counted again as an independent Q&A charge. [Results, methods and reproduction](../../eval/qa_results.md). Public acceptance of the benchmark page remains pending.

## Why Meta models matter

Spark turns prose, earnings tables and visual labels into one typed financial-claim contract. Wonder proposes focused corrections and adversarial drafts, while SAM contributes candidate chart/table regions. These capabilities let NoCap share one deterministic verification engine across different inputs. The models propose interpretations; stored filings, acceptance times, exact spans and code determine which outputs may be shown as verified. Spark Open lets us test the same contract across partner inference providers without changing the evidence boundary.

## Meta product feedback

Structured output and low reasoning effort made constrained extraction practical, but schema validity alone does not establish correct company, period, basis or question interpretation. We added independent checks for those dimensions. SAM sometimes returns no usable chart region, so the interface discloses Spark grounding fallback rather than attributing every region to SAM. Clearer comparable latency/usage records and document-grounding diagnostics would help developers distinguish a valid response from a supported result. The whole-document path is implemented but has not yet established a live million-token measurement.

## Partner technology feedback

The OpenAI-compatible Fireworks and Baseten interfaces fit the same extractor after explicit endpoint/model configuration. Baseten's hackathon key requires `meta-models/muse-spark-open-1026`; provider prompt caching and different response modes matter when interpreting cost and latency. We keep request hashes, usage records and failed-attempt reservations so replay and budget checks are auditable. Modal offers an operator-controlled private route, but cold starts and GPU-time billing need separate availability and cost validation from token-priced APIs. No controlled partner ranking is claimed.

## What was built during the hackathon

Development began on October 27, 2026. The NoCap implementation—including ingestion, claim extraction, deterministic verification, the CLI/API/web interface, constrained corrections, slide checking, evaluation tooling and demo assets—was built during the hackathon. The October 29 public-release export starts a clean history after media licensing cleanup; the original incremental development history remains in the private development repository. Its initial release date is not the date all features were written. The [public filtered source-history branch](https://github.com/changshoufeng0609-cmd/nocap/tree/source-history) contains 65 reconstructed commits and 104 final source/configuration files through the post-J2 checkpoint. Its [scope and mapping](../source-history.md) distinguish reconstructed commits from original objects and independent timestamp attestation. The earlier Taiwan portfolio agent contributed the motivating concept only; no code from it was copied into this project. Third-party libraries, source filings and model weights retain their own provenance and licenses.

## Demo and reviewer instructions

The new local **165-second NoCap walkthrough** records continuous actual-browser interactions: draft → receipts → Fix/full recheck/copy → historical cutoff → seeded slide → brief Ask. It has 18 English caption cues and local Kokoro synthetic narration. Check uses cached extraction; Fix/Slide/Ask replay disclosed successful recorded responses, with original model waiting time omitted. A fresh Fix source call cost $0.01108, and the slide replay uses the same branded image bytes checked in the [October 29 smoke](../demo/live-smoke-2026-10-29.json). This is a continuous UI recording, not live inference or latency evidence. The seeded slide is original demo material, explicitly not an SEC filing; Spark fallback after SAM found no usable region is visible. [Recording provenance and local validation](../demo/README.md) and [narration permissions](../demo/narration-license.md) are separate from owner listening/editorial approval. Public acceptance of this replacement remains pending.

Open [NoCap](https://receipts-nocap.vercel.app), choose a built-in Draft example, and inspect its saved results and SEC receipts; or [watch the hosted demo](https://receipts-nocap.vercel.app/demo). Anonymous desktop/mobile checks passed for all three saved examples and the earlier 179.937663-second, 29-cue player export. New live model requests are paused, so the public experience uses saved examples and recorded demonstrations. The [earlier release evidence](../public-release-2026-10-29.json) does not yet validate the replacement video or benchmark page.

The [public repository](https://github.com/changshoufeng0609-cmd/nocap) also supports the README's five-command quickstart. The bundled NVIDIA example needs no model key and should produce **7 verified / 2 wrong / 2 unverifiable**. The [video download](https://raw.githubusercontent.com/changshoufeng0609-cmd/nocap/main/docs/demo/assets/receipts-demo.mp4) passed anonymous checks for the earlier licensed export; recheck its hash when publishing the replacement.

Earlier authenticated Preview checks separately established live Check/Fix/Slide behavior and shared Neon admission across redeployments. Those historical results are preserved in [deployment status](../deploy.md#current-deployment-state); current anonymous acceptance made zero new model calls. New live checks incur model charges, and admission estimates are not provider billing hard caps.

## Next steps

Accept the new public main/media/benchmark package, then synchronize the actual submission form. Filtered source-history publication, J2 fixes and regressions, the one-time frozen held-out run, Private browser/HTTP acceptance and the 200-call Q&A comparison are complete. Preserve all test outcomes without tuning on them. Live whole-filing acquisition still needs a valid SEC contact identity. Decide the operating window and final submission/consent; conduct the prepared formative sessions and independent human gold review. No user study or customer adoption has been established.

## Submission fields still requiring owner confirmation

- **Actual form fields:** filled with earlier reviewed copy and links; refresh after final package acceptance. Observed in [completion plan](../completion-plan.md#actual-form-observed). No HTML input maximum was exposed; server-side length limits remain unconfirmed. Material-use consent is unchecked and the entry has not been submitted.
- **Contributor roster:** filled with SHOU-FENG CHANG (`Robert_Chang`) only.
- **Public repository URL:** [github.com/changshoufeng0609-cmd/nocap](https://github.com/changshoufeng0609-cmd/nocap) — anonymous HTTP and clean initial history verified.
- **Public demo-video URL:** [receipts-nocap.vercel.app/demo](https://receipts-nocap.vercel.app/demo) — earlier 179.937663-second export passed playback, 29 captions and download checks; replacement 165-second/18-cue acceptance and owner editorial review remain pending.
- **Public live-product URL, if required:** [receipts-nocap.vercel.app](https://receipts-nocap.vercel.app) — anonymous acceptance passed; built-in examples work while live requests remain paused.
- **Optional X post URL:** `[PUBLISHED_X_POST_URL]` — drafts only; no post has been published.

See [the evidence map](evidence-map.md) for the sources and limits behind these statements. Remove this readiness checklist and the working-draft note from any field that asks only for project copy.
