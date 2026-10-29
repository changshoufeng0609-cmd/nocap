# Remaining work (2026-10-29)

Current status is reconciled with the [Hand-back log](HANDOFF.md#hand-back-log) and the owner's
[completion scope and US$10 usage allocation](completion-plan.md). Historical unchecked plan items do not
mean the corresponding feature is still missing. Publication, live operation and submission are recorded
only after their actual access checks or confirmation; drafts and implemented interfaces are not those events.

**Current selected round:** the owner selected **1, 3, 4, 5, 6 and 8**. The replacement demo is
now a 165-second continuous recording of actual browser interaction. Private browser/HTTP/Modal
acceptance and all 200 four-model Q&A calls are complete. J2 identified two deterministic-boundary
bugs; fixes and independent rechecks are complete, and development results are preserved.
The authorized one-time held-out run is complete with unchanged frozen source/evidence. Filtered source history
is public; the new main release and production playback still require acceptance.
[Round evidence and usage](completion-round-2026-10-29.md). Submission remains unsubmitted.

## Completed implementation and measured work

- Check, Fix with receipts, Check a slide, historical cutoffs, numeric omission coverage, NoCap branding,
  the previously validated authenticated Preview and shared spend admission.
- [Ask with receipts](ask.md): typed lookup proposals, independent question binding, deterministic answer
  rendering, verified sentences and footnotes; API and UI implemented and specialist tests passed.
- [Whole-filing quote search](ask.md#whole-filing-quotes): bounded full-document requests, exact unique spans,
  source hashes and acceptance cutoffs; API and UI implemented. Real document acquisition/live validation
  is still pending, as listed below.
- [Private check](private-mode.md): Modal-only draft routing, no application response cache, no redirect or
  public-provider fallback, fail-closed setup and a separate UI. The real browser → HTTP → Modal → deterministic-verifier flow passed with 1 verified and 1 wrong claim. Three shared validation apps were stopped with zero tasks; see [live evidence](private-http-live-validation-2026-10-29.json).
- Slide duplicate reconciliation and conflict abstention, including unit/basis and confidence guards.
- [MCP stdio server](mcp.md): three read-only tools using the official SDK; 10/10 deterministic protocol
  scenarios passed with 21 actual tool calls and no model/SEC requests. This is integration testing, not
  autonomous agent accuracy.
- [Tesla evidence expansion](evidence-expansion.md): 379 additional facts and 57 filings, bringing coverage
  to 11 companies / 11,554 stored facts. Cached SEC ingestion and all original ten-company row hashes were
  checked. Tesla remains XBRL-only with incomplete EPS coverage.
- [Expanded 50-item dev benchmark](../eval/expanded_dev_results.md): both cached Meta Spark and Baseten
  Spark Open caught 24/24 errors, with 23/24 expected reasons and 0/24 original false alarms. Baseten used
  48 new calls plus two within-run reuses, independently reconciled at **$0.24020610**; Meta/replay cost $0.
  The original 13-item benchmark is unchanged. No test split was run.
- Official Vercel MCP team authorization completed; read-only team listing returned the intended team.
  Modal CLI authentication and a two-claim Glimmer pipeline smoke completed; this does not establish general model quality.
- The authenticated submission form is filled with project copy, final public links and the sole-contributor roster;
  the optional X URL is empty. No submission or consent acceptance has been made. See [completion plan](completion-plan.md#actual-form-observed).
- [Public source](https://github.com/changshoufeng0609-cmd/nocap) was verified without authentication: initial commit `7856fe65536597b13a96723f54b83b1bc8187df0` has no parent. Original development history stays private. The public MP4's bytes match the licensed Kokoro export.
- [Production workspace](https://receipts-nocap.vercel.app) and [demo player](https://receipts-nocap.vercel.app/demo) passed anonymous desktop/mobile acceptance: all three saved examples on each viewport, two uncached Check/Ask pause responses, expected unavailable quote/Private states, 29 video captions and matching MP4/VTT browser downloads. Live model requests remain paused; acceptance added zero model calls and zero provider cost. [Release evidence](public-release-2026-10-29.json).
- The [replacement demo](demo/README.md) is 165 seconds with 18 English subtitle cues and a 20-second continuous GIF. It discloses cached extraction, recorded real Fix/Slide/Ask responses and shortened waits. The previous public playback check above describes the old export.
  The current feature UI passed 144 frontend checks, typecheck, lint and production build; the latest
  integrated Python suite passed 1,117 tests plus two independent follow-up regressions. Later release changes and live acceptance belong in the Hand-back log.

## Acceptance and external work still open

| Item | What remains | Dependency / current limit |
|---|---|---|
| Whole-filing live validation | Acquire eligible complete filings through `receipts.edgar`, validate snapshot inclusion and run a bounded quote smoke. | The configured SEC contact is a placeholder; a real declared contact is required. Do not claim a measured million-token request. |
| Modal / private live check | **Complete:** real browser, HTTP, Glimmer, receipt UI, clearing and zero application-cache checks passed. | Three new apps stopped/tasks zero; observed $1.25997788 includes shared 50-question Glimmer Q&A. Historical $0.70780581 is separate. [Evidence](private-http-live-validation-2026-10-29.json). |
| Operation-led demo remake | **Local export complete:** continuous paste → Check → receipt → Fix/recheck → cutoff → Slide → short Ask, 165 seconds / 18 cues. Replacement production playback still pending. | Cached/recorded responses and shortened waits disclosed. Technical/transcript/sampled visual review does not represent human editorial approval. [Demo provenance](demo/README.md). |
| Final rubric reviews | [J2](j2-review-2026-10-29.md) closed both findings after 98 targeted tests and unchanged dev results; local package 80.5/100. [J3](j3-review-2026-10-29.md) awaits final release/held-out evidence. | AI-assisted, unofficial reviews with disclosed implementation conflicts; not actual judging or human adjudication. |
| Raw-versus-verified Q&A evaluation | **200 calls complete:** 50 frozen closed-book questions × Spark, Wonder, Spark Open/Baseten and Glimmer/Modal. Public benchmark page acceptance pending. | Original strict contract retained; post-run metadata sensitivity explicitly separate. Accepted-answer coverage 28% / 0% / 34% / 12%; precision 14/14, N/A, 17/17, 6/6. Provisional labels and supported-snapshot selection limit generalization. [Methods](../eval/qa_methods.md). |
| Development-history evidence | **Complete:** [65 reconstructed source-history commits](source-history.md), 104 final source/configuration files, public branch HEAD verified anonymously. | Original source blobs preserved; identity emails replaced; media/data/docs/env excluded. Original private history unchanged. Git dates are metadata, not independent timestamp attestations. |
| Final submission | Update the form after the demo remake and remaining acceptance work, obtain final material-use consent, submit when authorized and retain confirmation. | The draft form contains the old video link and SHOU-FENG CHANG (`Robert_Chang`) only; consent remains unchecked and no submission has occurred. Do not treat the earlier pending consent question as an instruction to submit. |
| Real user sessions | Obtain participant contacts, run 2–3 prepared sessions and record permissioned feedback. | **0 actual sessions**; no invented users, findings or time savings. |
| Human gold review | Complete the [offline 27-record review packet](gold-review.html) and export the named review with decisions/comments and human declaration. | A human reviewer is still needed; the review UI and agent/code reviews do not satisfy human adjudication. |
| Held-out evaluation | **Complete:** v0 44/48 caught, 1/47 original-item false alarms; trap 20/20 caught, 0/52 true-figure false alarms. | 126-file freeze unchanged; 117 fresh calls / $0.49834625, 11 extraction-cache hits, no errors/skips. All results retained; no tuning or rerun. [Records](../eval/heldout_runs/release-20261029/). |
| Judge-period operation | Confirm required availability window, actual provider balances and an operating budget. | Live requests are initially paused; saved examples and the video are the intended public experience. The fixed shared allowance remains intact. Current usage is reconciled in the [round record](completion-round-2026-10-29.md); public cached/pause checks add $0 provider usage. Admission is not a provider invoice ceiling or evidence of remaining credits. |
| X / Most Viral | Confirm posting identity, official tags and final links, then record any actual publication. | Drafts only; account and final details are still needed. |

The owner confirmed SHOU-FENG CHANG (`Robert_Chang`) is the sole contributor. The final checkbox grants Meta and Cerebral Valley rights to use and
promote submitted materials, and has not been accepted. See the [submission kit](submission/README.md),
[evidence map](submission/evidence-map.md), [demo provenance](demo/README.md) and [user-study kit](user-study/README.md).

The original plan mentions service availability through November 16; confirm the event's actual requirement
before treating that planning date as an operating commitment. Successful Baseten benchmarks do not switch
the production extractor, and implemented private mode does not make the other workflows private.
