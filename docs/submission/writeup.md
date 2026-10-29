# NoCap — submission copy

**Working draft · unpublished · updated 2026-10-29.** The authenticated submission form has been inspected; these modules cover its project description, Meta usage/value and product/partner feedback fields. No input `maxlength` was exposed; final server-side limits remain unconfirmed. Product name: **NoCap**. Team: **NoCap**. Track: **Indie Developer**. Theme: **Build for Your World**. Sole contributor, confirmed by the owner: **SHOU-FENG CHANG (`Robert_Chang`)**, known as **Robert Chang**. No submission or final consent acceptance has occurred.

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

**Private check** is an explicit draft-only route to Muse Glimmer in the operator's Modal workspace, with no application response caching, redirects or fallback to Meta/partner APIs. It fails closed without valid setup. Modal and the NoCap host still process the draft; this is not offline computation. A bounded live pipeline smoke returned 1 verified / 1 wrong from one model call and rejected unauthenticated access. The deployment was stopped afterward; live HTTP/browser acceptance remains separate. A local **MCP stdio server** exposes read-only coverage, evidence search and typed-claim verification without making model or SEC calls itself.

## How we use Meta models

| Model | Implemented role | What code controls |
|---|---|---|
| Muse Spark 1.3 | Extracts typed text claims, maps earnings-release rows, reads slide labels, proposes Ask lookups and selects whole-filing quotes. | Locates verbatim spans, parses values, checks question/lookup agreement, validates source cells and exact quotes, and computes numeric verdicts. |
| Muse Wonder 1.0 | Proposes numeric corrections for Fix with receipts and generated adversarial development drafts. | Rejects unsafe edits, rechecks the full revision, and independently confirms planted-error labels against stored evidence. |
| SAM 3.1 | Proposes chart/table regions for uploaded slides. | Bounds region handling and reports its source. Spark supplies grounding when SAM returns no usable region. |
| Muse Spark Open on Fireworks and Baseten | Live-tested alternative extractors; Baseten also completed a fixed 50-item dev comparison. | Uses the same typed contract and deterministic verifier; no unrestricted answer-factuality claim. |
| Muse Glimmer on Modal | Private extraction pipeline passed one bounded live smoke; the deployment was stopped afterward. | Fixes the operator endpoint, disallows public-provider fallback and application response caching, then applies the same verifier. Live HTTP/browser acceptance is separate. |

The hosted Meta models use Standard-mode IDs: `muse-spark-1.3`, `muse-wonder-1.0`, and `sam-3.1`.

## Technical depth

The core boundary is simple: **models propose; code decides**. A fixed metric vocabulary connects typed claims to SEC XBRL and earnings-release tables attached to 8-Ks. Numeric parsing and span locations come from the quoted input. The verifier handles written precision, directional bounds, scale, fiscal periods, GAAP/non-GAAP basis, entity attribution, and split-consistent per-share evidence.

Every fact carries provenance and EDGAR acceptance time. Historical checks restrict the eligible evidence; they do not claim to establish when information first appeared outside EDGAR. Derived quarters and growth figures preserve their supporting facts. The interface shows traceable growth and margin arithmetic when the evidence is unambiguous.

A Python/FastAPI API streams results to a Next.js interface. The evidence store contains **11,554 facts across 11 companies**, targeting the latest eight fiscal quarters. Tesla adds 379 XBRL/derived facts and 57 filings from cached SEC sources, while all original ten-company rows remain unchanged. Tesla has no image-based earnings table/non-GAAP coverage and EPS is available in 6/8 quarters. These records are not independent benchmark examples. Cached examples replay recorded verification without new model calls.

The earlier numeric-coverage release passed 905 Python tests, 119 frontend checks, typecheck, lint and production build. The latest full Python run passed 1,117 tests, followed by two additional independent slide-expiry checks. The frontend passed 144 checks, typecheck and lint; final build and release acceptance are recorded separately in the Hand-back log. The MCP server passed ten deterministic protocol scenarios with 21 tool calls and no model/SEC requests. The shared PostgreSQL ledger passed eight actual database checks plus hosted admission checks across deployments. Engineering checks are separate from model-quality evaluation and do not establish live acceptance of every new workflow.

## Development evaluation and limitations

We evaluated development sets only. The held-out test split has never been evaluated. Full-corpus results below reuse recorded Meta extraction; the separate open-weight live benchmarks ran on October 28 and 29.

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

A subsequent fixed-seed 50-item dev sample compared cached Meta Spark with fresh Baseten Spark Open: both caught 24/24 seeded errors, assigned 23/24 expected reasons, produced 0/24 original false alarms and left both guidance controls unverifiable. Baseten used 48 successful new calls and two within-run reuses of identical extraction requests; separate as-of checks still ran for both items. Independent usage reconciliation gives **$0.24020610**, and a cache-only replay reproduced all 100 item outcomes. No gold/verifier changes were made to fit this sample. Different run dates, response modes and provider cache usage prevent a controlled speed/cost ranking. Meta Spark remains the default. The separate [Modal smoke](../private-live-validation-2026-10-29.json) is a two-claim integration check, not another quality benchmark. The provider's observed costs across four deployment attempts total **$0.70780581**, subject to delayed metering; all four apps were stopped with zero tasks at the follow-up inspection.

## Why Meta models matter

Spark turns prose, earnings tables and visual labels into one typed financial-claim contract. Wonder proposes focused corrections and adversarial drafts, while SAM contributes candidate chart/table regions. These capabilities let NoCap share one deterministic verification engine across different inputs. The models propose interpretations; stored filings, acceptance times, exact spans and code determine which outputs may be shown as verified. Spark Open lets us test the same contract across partner inference providers without changing the evidence boundary.

## Meta product feedback

Structured output and low reasoning effort made constrained extraction practical, but schema validity alone does not establish correct company, period, basis or question interpretation. We added independent checks for those dimensions. SAM sometimes returns no usable chart region, so the interface discloses Spark grounding fallback rather than attributing every region to SAM. Clearer comparable latency/usage records and document-grounding diagnostics would help developers distinguish a valid response from a supported result. The whole-document path is implemented but has not yet established a live million-token measurement.

## Partner technology feedback

The OpenAI-compatible Fireworks and Baseten interfaces fit the same extractor after explicit endpoint/model configuration. Baseten's hackathon key requires `meta-models/muse-spark-open-1026`; provider prompt caching and different response modes matter when interpreting cost and latency. We keep request hashes, usage records and failed-attempt reservations so replay and budget checks are auditable. Modal offers an operator-controlled private route, but cold starts and GPU-time billing need separate availability and cost validation from token-priced APIs. No controlled partner ranking is claimed.

## What was built during the hackathon

Development began on October 27, 2026. The NoCap implementation—including ingestion, claim extraction, deterministic verification, the CLI/API/web interface, constrained corrections, slide checking, evaluation tooling and demo assets—was built during the hackathon. The October 29 public-release export starts a clean history after media licensing cleanup; the original incremental development history remains in the private development repository. Its initial release date is not the date all features were written. The earlier Taiwan portfolio agent contributed the motivating concept only; no code from it was copied into this project. Third-party libraries, source filings and model weights retain their own provenance and licenses.

## Demo and reviewer instructions

The 179.937663-second NoCap video demonstrates the draft → receipt → correction → historical-cutoff workflow and a seeded slide example. It is an edited sequence of actual browser captures with disclosed local Kokoro synthetic narration. Draft checks use cached extraction; Fix and slide scenes replay recorded successful live responses. The branded slide display retains the original image's response; a separate [October 29 smoke](../demo/live-smoke-2026-10-29.json) later checked the branded bytes. It is not a continuous live session or a latency measurement. The seeded slide is original demo material, explicitly labeled as containing errors and not an SEC filing. The current narration's [sources and permissions](../demo/narration-license.md) are recorded separately from human editorial review and public-access checks.

With repository access, follow the README's five-command quickstart. The bundled NVIDIA example needs no model key and should produce **7 verified / 2 wrong / 2 unverifiable**. Before the branding refresh, the authenticated private Preview passed separate live Check, Fix and Slide smoke checks. The shared-ledger Preview release (`885a46f`; see deployment status for the latest build) adds conservative per-attempt accounting and a validated fixed shared Neon primary ledger. Hosted missing-ledger and exhausted-budget checks passed with no model calls; paid live flows were not repeated on that build. Its URL is not a public demo link. New live checks incur model charges, and admission estimates are not provider billing hard caps. Public release is authorized within the completion scope, with final content and signed-out access checks still pending.

## Next steps

Complete live whole-filing and private HTTP/browser acceptance, public judge access and the actual submission; run the prepared formative sessions and independent human gold review. Ask, MCP, private-mode routing, Tesla evidence and slide conflict handling are now implemented, with their validation boundaries documented. Narration replacement and media validation are complete. We have not conducted a user study, established customer adoption, or released a public production service at this checkpoint.

## Submission fields still requiring owner confirmation

- **Actual form fields:** observed and recorded in [completion plan](../completion-plan.md#actual-form-observed). No HTML input maximum was exposed; server-side length limits remain unconfirmed. The final material-use consent checkbox has not been accepted.
- **Contributor roster:** confirmed as SHOU-FENG CHANG (`Robert_Chang`) only. Remaining contact and any required eligibility/profile values must be supplied accurately.
- **Public repository URL:** `[APPROVED_PUBLIC_REPOSITORY_URL]` — repository is currently private; final release/history and signed-out access checks are pending.
- **Public demo-video URL:** `[APPROVED_PUBLIC_DEMO_VIDEO_URL]` — the narration-refreshed assets are validated; final owner review and publication are pending.
- **Public live-product URL, if required:** `[APPROVED_PUBLIC_LIVE_URL]` — current Preview requires authentication; final public-release acceptance remains pending.
- **Optional X post URL:** `[PUBLISHED_X_POST_URL]` — drafts only; no post has been published.

See [the evidence map](evidence-map.md) for the sources and limits behind these statements. Remove this readiness checklist and the working-draft note from any field that asks only for project copy.
