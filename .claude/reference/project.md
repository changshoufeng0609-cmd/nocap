# Project: NoCap

Decided 2026-10-27. Indie Developer track, theme Build for Your World. This is the single source of truth for what we're building. Update it when scope changes.

**One-liner:** check financial numbers in a draft against SEC filings before publishing, with receipts and visible uncertainty.

**Current checkpoint — November 1:** all 500 S&P 500 companies and 243,839 stored facts plus 39,701 balance-sheet figures; the 2:52 v2.1 video (end card "Now covering all 500 S&P 500 companies") is exported locally, not yet deployed; the repository is private until the submission release. <!-- verify-at-release --> **October 29 checkpoint (historical):** the local 165-second continuous browser walkthrough (replaced locally on October 30–31 by the 2:52 v2.1), real local Private HTTP/Modal acceptance and four-backend closed-book Q&A run (200 calls) are complete. J2 core fixes are integrated at `df76e67`, with 1,246 full-suite Python passes, 144 frontend checks, preserved dev baselines/saved-example counts and independent closure of both findings. The held-out source was frozen at `61e1055`; its authorized one-time run completed with all 126 frozen files unchanged and no outcome-driven tuning. Seeded test caught 44/48 errors, with 1/47 original-item false alarms; trap test caught 20/20 errors, with 0/52 true-figure false alarms. See `eval/heldout_results.md` for denominators and limits. The filtered source-history branch was published on October 29 (public again with the release): 65 reconstructed commits and 104 final source/configuration files through private checkpoint `61e1055`; see `docs/source-history.md`. The updated public main/media/benchmark package passed anonymous desktop/mobile acceptance, with live model operations paused; see `docs/public-release-update-2026-10-29.json`. An October 30 review-fix round closed the independent review's sixteen findings (1,535 Python tests, 165 frontend checks, development replay equal to the baseline); held-out figures still describe `61e1055`. See `docs/reviews/opus-review-20261029/fix-closure.md`. Submission/consent, real participant sessions and independent human gold review remain open. See `docs/completion-round-2026-10-29.md` and the evidence-linked submission kit; historical spike results below retain their original scope.

## Users and job
- **Primary user:** financial journalists and equity analysts who publish numbers on deadline. One wrong figure means a correction, reputational damage, or a compliance problem.
- **Job:** before publishing, check a draft, whether a person or an AI wrote it, so that every number and statement traces back to the filing that supports it.
- **Secondary user:** retail investors, served by Ask mode on the same engine. At most 10 seconds of the video.
- **Developer integration (implemented 2026-10-29):** a local read-only MCP stdio server exposes evidence discovery, bounded search and deterministic typed-claim verification. See `docs/mcp.md`.
- **Origin story (the "your world" angle):** the user built an autonomous trading agent and saw LLMs invent numbers firsthand. NoCap is the verification layer they wished they'd had.

## Product
1. **Check mode (the hero).**
   - Paste a draft, such as an AI-written earnings story.
   - Muse Spark extracts typed claims: metric, value, unit, period, entity, source hint.
   - A deterministic verifier checks each claim against EDGAR and XBRL:
     - value within tolerance
     - unit and scale
     - fiscal period, including YoY vs QoQ
     - GAAP vs non-GAAP
     - entity
     - point-in-time availability
   - The draft comes back annotated inline:
     - ✓ verified, with its receipt
     - ✗ wrong, with the correct value and source
     - ? unverifiable

   Think "Grammarly for financial facts."
2. **Ask mode (implemented 2026-10-29).** Standard Spark proposes a bounded lookup; code binds every supported company, metric, period and qualifier to the question, renders sentences from stored facts, and verifies them. Only verified sentences enter the answer; withheld items remain visible with a reason. Footnotes export with EDGAR accessions. Unrecognized or ambiguous questions require clarification. See `docs/ask.md`.
3. **Time travel.** Run any check "as of" a date and time. Claims that depend on filings accepted after that moment are blocked as look-ahead.
4. **Check a slide (implemented 2026-10-28, user approved).** Single PNG/JPEG upload; SAM 3.1 chart/table candidate regions and Standard Spark vision transcription; the same deterministic SEC verifier checks the readings. Click numbered overlays for receipts. Axis estimates can only receive ≈ or ?; uncertain readings remain ?. The upload is not evidence, vision can omit or misread labels, and Spark grounding supplies regions when SAM fails or finds none. No image is saved by the application.
5. **Receipt panel.** Click a checked figure to see its filed value, provenance, source link and available cell or derivation. Highlighting occurs in the draft; the linked filing is not a guaranteed highlighted filing-page viewer. Slide overlays mark the uploaded input, not the source filing.
6. **Private check (live integration completed 2026-10-29).** Local browser → FastAPI HTTP → authenticated Muse Glimmer on Modal → deterministic verdicts passed a synthetic two-claim check with one verified and one wrong result. No application response cache or public-provider fallback is used. The NoCap host and Modal still process the draft. Task apps are stopped; this is not public or always-on Private availability. See `docs/private-mode.md`.

## Design rules from the spikes (2026-10-27)
- **Character offsets come from code, not the model.** In Spike B, Muse Spark's own offsets were exact only 67% of the time. Every `span_text` did appear verbatim, so code computes the offsets from `span_text`. The model's offset is used only to tell repeated quotes apart.
- **Fixed metric vocabulary.** Free-text metric names agreed across runs only about 50% of the time. Use an enum of metrics that maps to XBRL concepts and press-release row labels.
- **Guidance is flagged as `forward_looking`, and the verdict is "?".** Spark flagged it correctly in 3 of 3 runs. Forward-looking statements can't be verified against filings.
- **Extraction config, measured in Spike B v2 on 2026-10-27 (`spikes/b2_claims.py`):**
  - **Setup:** one call per passage, split on blank lines, with concurrency 10; `muse-spark-1.3` at `reasoning.effort` minimal.
  - **Output fields:** `bases` is a list, so a claim that applies to both GAAP and non-GAAP is expanded into two claims by code. The metric enum has 15 canonical classes, and `metric_text` is kept for display.
  - **Offsets:** none come from the model.
  - **Results on the 27-claim gold set:** strict recall 100% and precision 100% in all 3 of 3 runs. Spans were exact (69/69). Wall time was 8.9–11 s, against a 20 s target, at about $0.035–0.05 per run.
  - **Next:** add a document-level context line to every per-passage call, giving the company and fiscal period, because a passage can't see the others.
  - **Biggest single win:** dropping model offsets. On the same text at low effort, time fell from 80–100 s to 27 s and reasoning tokens from 11–13K to 2.2K.
- **Values read from charts come in two kinds.** A printed data label can be verified exactly, so it gets ✓ or ✗. A value estimated from the axes is approximate (±1% in Spike C). It's marked "≈" and can never earn ✗ on its own.
- **Real-slide results, Spike C on 2026-10-27:**
  - **Printed labels:** on BAC's Q3 2026 presentation (EX-99.2), 12 of 12 values matched XBRL exactly.
  - **Axis estimates:** on TSLA's Q3 2026 Update, which has no printed labels, only 12 of 24 values were within ±1% relative, but 24 of 24 were within 1% of the axis range. So the tolerance for axis estimates is a percentage of the axis range, and the verdict is "≈", never ✓.
  - **Values that can be derived:** XBRL doesn't hold every quarterly figure directly, so some quarters must be computed:
    - **Q4:** fiscal year minus the first nine months, using only 10-K and 10-Q facts. companyfacts also contains DEF 14A annual figures, which must be excluded.
    - **Quarterly cash flow:** the year-to-date total minus the prior year-to-date total.
    - **Current quarter:** stays "?" until its 10-Q is filed.
  - **SAM concepts:**
    - Stacked bars come back as one box per bar, so merge each row of narrow boxes into one chart region.
    - "line chart" found nothing on BAC's slides, so fall back to Spark grounding.
  - **Prompt rules:** report the totals of stacked bars, and keep labels such as adjusted, non-GAAP, and FTE inside the label text. Never compare those values against GAAP XBRL.
- **Receipt regions use boxes, not masks.** SAM 3.1 boxes hug the data, so pad them, or use Spark grounding for the whole chart. Masks aren't needed.
- **Evidence timing varies by company.** NVIDIA files its 10-Q, with XBRL, 10–30 minutes after the earnings 8-K. Other companies can lag by days. Keep the EX-99.1 table layer because non-GAAP figures and guidance never appear in XBRL.

## Meta models (each one does real work)
- **Muse Spark 1.3:**
  - claim extraction (structured output)
  - the typed lookup planner in Ask mode; code retrieves facts and renders verified answers
  - reading charts on slide exhibits, with those numbers verified like any other claim
- **Whole-filing quote search (implemented; validated live on 2026-10-30):** bounded complete-document input and exact unique quote spans with source hashes and acceptance cutoffs. Meta's 2025 10-K (516,028 characters) took 101,135 input tokens in one Muse Spark request; code accepted 4 exact quotes and rejected none. Quote provenance is not a truth or relevance judgment. No million-token measurement is claimed (the filing used about 10% of the window).
- **Muse Wonder:** Fix with receipts proposes numeric corrections to wrong claims; code constrains each edit and verifies it, then the complete draft is extracted and checked again. The UI compares the original and revised draft with correction receipts and remaining flags. Signs, units, prose, company, period and basis stay unchanged; look-ahead and unsafe corrections remain for manual review. Wonder also generates adversarial "trap drafts" (evaluated on the dev split and once on the held-out split: 20/20 planted errors caught). Hard multi-step questions remain planned.
- **SAM 3.1:** proposes chart and table regions. Spark grounding supplies regions when SAM finds none; the seeded NVIDIA slide shows this fallback; on the real Bank of America slide in the v2.1 demo, SAM found both bar charts.
- **Muse Glimmer and Muse Spark Open:** live-tested alternatives on Modal, Baseten and Fireworks. Four backends completed a fixed 50-question closed-book Q&A experiment, with exact-contract results and a separately labeled post-run metadata sensitivity analysis. Accepted-answer coverage was limited; no claim that any model becomes trustworthy or that bonus-prize eligibility is certified. See `eval/qa_results.md` and `docs/open-weights.md`.

## Data
SEC EDGAR only:
- **Submissions API:** acceptance timestamps
- **XBRL:** companyfacts, companyconcept, frames
- **Filing documents:** 10-K, 10-Q, and 8-K, including EX-99 press releases and slides

Rules and scope:
- **Fair access:** use `receipts/edgar.py`, a declared contact User-Agent, caching and at most two requests per second per process, shared across clients.
- **Caching:** cache everything locally with content hashes.
- **Current scope:** the original 10-company collection over eight fiscal quarters, plus TSLA XBRL (379 facts / 57 filings), the October 30 top-up of the September 2026 quarter for MSFT, GOOGL and AMZN (98 XBRL + 263 earnings-release facts, 7 filings), and the October 31 expansion to 21 more large caps (6,473 XBRL + 9,229 earnings-release facts, 1,163 filings, release figures behind the XBRL gate): 32 companies and 27,617 stored facts. The same day the S&P 500 step added 467 more (499 companies, 240,362 stored facts, 28,535 filings), and on November 1 Exxon Mobil joined across its two registrants (500 companies, 243,839 stored facts, 28,607 filings; see `docs/evidence-expansion.md`). Tesla EPS covers 6/8 quarters; no Tesla press-release or image-table coverage is claimed. On 2026-10-31 the owner approved AVGO, ORCL, CRM, ADBE, INTC, QCOM, CSCO, IBM, PLTR, UBER, WMT, COST, DIS, NKE, MCD, KO, LLY, UNH, JNJ, GS and WFC (V, MA and XOM were evaluated and dropped); an S&P 500 step follows.
- **Companies, approved by the user on 2026-10-27:** NVDA, AAPL, MSFT, META, AMZN, GOOGL, JPM, BAC, AMD, NFLX. TSLA was initially excluded because its press release uses images; the 10/29 expansion adds XBRL only. Swap out any company whose tables still can't be mapped on 10/29.
- **10-company check on 2026-10-27 (`a_edgar.py`):**
  - **Filings and XBRL:** all 10 pass the acceptance-timestamp and XBRL-join checks.
  - **Table parsing:** the generic label-regex parser, tuned on NVIDIA, gets NVDA 10/10. Every number it parsed for MSFT and META matched, but both scored 7/10 because their row labels differ ("Total cost of revenue", "Marketing and sales").
  - **Units:** NFLX reports in thousands, so the parser must read the unit header ("in thousands" vs "in millions").
  - **No table found:** AAPL and AMZN ("Net sales"), AMD ("Net revenue"), and GOOGL, JPM, and BAC (bank statements).
  - **Day 2 consequence:** label regexes don't scale. Spark maps the table rows to the canonical metrics and reads the unit header, and code checks every cell value verbatim. Plan Risk #1 has fired, as expected.

## Evaluation (headline numbers for the video and README)
- **Seeded-error corpus:** real sentences from 8-K press releases and filings with injected errors:
  - wrong value
  - wrong period (YoY vs QoQ)
  - unit or scale (thousands vs millions)
  - GAAP vs non-GAAP swap
  - look-ahead
  - wrong entity

  Report planted-error catch rate and false alarms with their distinct denominators. A false-alarm rate is not precision. Provisional labels still await human review. The frozen held-out run completed: 97 seeded test items, 44/48 errors caught (91.7%), 42/48 reasons, 1/47 original-item false alarms (1/110 true claims), 22/110 true claims left `?`; all four uncaught errors were `?`, not ✓. Seven trap-test drafts: 20/20 caught, 18/20 reasons, 0/52 figure and 0/25 sentence false alarms, 3/52 true figures left `?`, all ten guidance figures `?`. The 117 model attempts cost $0.49834625 with no estimated usage, errors or skips. Preserve every outcome and do not tune on it. Development zero-false-alarm results must remain labeled development.
- **Raw vs verified Q&A per model (completed 2026-10-29):** Spark 1.3, Wonder 1.0, Baseten Spark Open and Modal Glimmer answered 50 supported snapshot questions each, without retrieval/tools. Report numeric/binding errors separately from abstentions, unsupported metadata and format failures. The pre-registered exact contract accepted zero answers. Post-run metadata sensitivity accepted 14/0/17/6 answers, all correct within this small set, with coverage 28%/0%/34%/12%; zero accepted means N/A precision. This is not a general hallucination-rate estimate or an evaluation of Ask's retrieval planner. The scorer and verifier share evidence sources; no independent human adjudication is claimed. See `eval/qa_methods.md` and `eval/qa_results.md`.

## Current demo (v2.1, 2:52, local; publishes with the next release)
1. Cold open: an AI-drafted NVIDIA earnings story with two wrong figures; one disclosure card.
2. Check the saved draft (7 ✓ / 2 ✗ / 2 ?); open the growth receipt (18% → 105.85%) and the EPS basis receipt ($2.22 → $2.46).
3. Fix with receipts (Muse Wonder) and the full recheck (9 ✓ / 0 ✗ / 2 ?).
4. Time travel: the June-quarter roundup as of July 29, with two Microsoft figures "not public yet".
5. A real Bank of America SEC slide: SAM 3.1 finds both charts, Spark reads the labels, 5/5 GAAP bars verify.
6. Meta's whole 10-K in one Spark request (101,135 input tokens); code keeps only exact quotes.
7. A "same verifier, four backends" card, a real recorded Private check (Glimmer on Modal, 1 ✓ / 1 ✗), a held-out results card and an end card.

The real interface recorded locally at `4bd6f4c` and edited with zooms (no speed changes): cached extraction, disclosed recorded Fix/Slide/Ask/Private runs, judging-window health values shown, local Kokoro `am_fenrir` synthetic narration, original music and 26 English cues. It is not live latency evidence. Owner listening/editorial approval and deployment remain open. <!-- verify-at-release --> The 165-second v1 (deployed October 29–30) is history. See `docs/demo/README.md`.

## Guardrails
- **Research and fact-checking only:** no buy or sell opinions, and a disclaimer in the UI.
- **New work only:** a new public repo with new code. The earlier Taiwan portfolio agent is prior work. Don't copy it, and call out any reuse in the submission.
- **Model IDs:** use Standard-mode IDs, never `-contributor`.
- **Name:** **NoCap**, matching Team NoCap; the user confirmed this product rename on 2026-10-28. Previously named Receipts. Internal package and repository identifiers remain `receipts`.
- **Team name:** **NoCap** (decided 2026-10-27). "No cap" is slang for "no lie", and it's also a pun on market cap. Use it on every form and on the submission page. The sole contributor is SHOU-FENG CHANG (`Robert_Chang`, Robert Chang), confirmed by the owner on 2026-10-29.
