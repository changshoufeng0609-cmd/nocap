# Remaining work (2026-10-29)

Current status is reconciled with the [Hand-back log](HANDOFF.md#hand-back-log) and the owner's
[completion scope and US$10 usage allocation](completion-plan.md). Historical unchecked plan items do not
mean the corresponding feature is still missing. Publication, live operation and submission are recorded
only after their actual access checks or confirmation; drafts and implemented interfaces are not those events.

## Completed implementation and measured work

- Check, Fix with receipts, Check a slide, historical cutoffs, numeric omission coverage, NoCap branding,
  the previously validated authenticated Preview and shared spend admission.
- [Ask with receipts](ask.md): typed lookup proposals, independent question binding, deterministic answer
  rendering, verified sentences and footnotes; API and UI implemented and specialist tests passed.
- [Whole-filing quote search](ask.md#whole-filing-quotes): bounded full-document requests, exact unique spans,
  source hashes and acceptance cutoffs; API and UI implemented. Real document acquisition/live validation
  is still pending, as listed below.
- [Private check](private-mode.md): Modal-only draft routing, no application response cache, no redirect or
  public-provider fallback, fail-closed setup and a separate UI. One bounded live pipeline check passed; all task apps were stopped afterward. Live HTTP/browser validation remains separate.
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
- Submission copy, user-study kit, the 179.937663-second demo, 29 subtitle cues and a 20-second GIF exist.
  The current feature UI passed 144 frontend checks, typecheck, lint and production build; the latest
  integrated Python suite passed 1,117 tests plus two independent follow-up regressions. Later release changes and live acceptance belong in the Hand-back log.

## Acceptance and external work still open

| Item | What remains | Dependency / current limit |
|---|---|---|
| Whole-filing live validation | Acquire eligible complete filings through `receipts.edgar`, validate snapshot inclusion and run a bounded quote smoke. | The configured SEC contact is a placeholder; a real declared contact is required. Do not claim a measured million-token request. |
| Modal / private live check | Bounded pipeline smoke and app-scoped usage audit complete; live HTTP/browser acceptance remains optional follow-up. | All four apps stopped, zero running tasks. $0.70780581 observed app-scoped usage; see [evidence](private-live-validation-2026-10-29.json). |
| Demo editorial review | Obtain the owner's final review of the video and captions. | Public desktop/mobile playback, caption behavior and MP4/VTT downloads passed. Technical checks do not imply human listening or editorial approval. |
| Final submission | Resolve the final material-use consent, submit the prepared form when authorized and retain confirmation. | The form is filled with final links, reviewed copy and SHOU-FENG CHANG (`Robert_Chang`) only; consent remains unchecked and no submission has occurred. |
| Real user sessions | Obtain participant contacts, run 2–3 prepared sessions and record permissioned feedback. | **0 actual sessions**; no invented users, findings or time savings. |
| Human gold review | Complete the [offline 27-record review packet](gold-review.html) and export the named review with decisions/comments and human declaration. | A human reviewer is still needed; the review UI and agent/code reviews do not satisfy human adjudication. |
| Held-out evaluation | Run only following an explicit user instruction covering the test split. | Intentionally unrun; “complete remaining work” does not override the earlier test prohibition. |
| Judge-period operation | Confirm required availability window, actual provider balances and an operating budget. | Live requests are initially paused; saved examples and the video are the intended public experience. The fixed shared allowance remains intact. Known batch usage is $0.96663691; public checks add $0 provider usage. Admission is not a provider invoice ceiling or evidence of remaining credits. |
| X / Most Viral | Confirm posting identity, official tags and final links, then record any actual publication. | Drafts only; account and final details are still needed. |

The owner confirmed SHOU-FENG CHANG (`Robert_Chang`) is the sole contributor. The final checkbox grants Meta and Cerebral Valley rights to use and
promote submitted materials, and has not been accepted. See the [submission kit](submission/README.md),
[evidence map](submission/evidence-map.md), [demo provenance](demo/README.md) and [user-study kit](user-study/README.md).

The original plan mentions service availability through November 16; confirm the event's actual requirement
before treating that planning date as an operating commitment. Successful Baseten benchmarks do not switch
the production extractor, and implemented private mode does not make the other workflows private.
