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
- The actual authenticated submission form was inspected and its fields recorded in [completion plan](completion-plan.md#actual-form-observed).
  No submission or consent acceptance has been made.
- Submission copy, user-study kit, the 179.839-second demo, 29 subtitle cues and a 20-second GIF exist.
  The current feature UI passed 144 frontend checks, typecheck, lint and production build; the latest
  integrated Python suite passed 1,117 tests plus two independent follow-up regressions. Later release changes and live acceptance belong in the Hand-back log.

## Acceptance and external work still open

| Item | What remains | Dependency / current limit |
|---|---|---|
| Whole-filing live validation | Acquire eligible complete filings through `receipts.edgar`, validate snapshot inclusion and run a bounded quote smoke. | The configured SEC contact is a placeholder; a real declared contact is required. Do not claim a measured million-token request. |
| Modal / private live check | Bounded pipeline smoke and app-scoped usage audit complete; live HTTP/browser acceptance remains optional follow-up. | All four apps stopped, zero running tasks. $0.70780581 observed app-scoped usage; see [evidence](private-live-validation-2026-10-29.json). |
| Integrated feature release | Finish combined regressions/browser acceptance, update the snapshot, record the resulting deployment and public-access checks. | Specialist tests and frontend build passed; see the current Hand-back log for final release results. |
| Public repository | Complete final contents review and visibility change, then verify signed-out access. | Original development repo stays private; prepare a clean public main branch containing licensed replacement media, then verify signed-out access. |
| Demo video | Finish owner editorial review and upload/publication, then verify signed-out playback/captions. | Kokoro narration replacement and media validation are complete; signed-out public playback is pending. |
| Judge access to product | Complete the selected access method and test it without the owner's authenticated session. | Existing Preview requires Vercel Authentication. Availability and spending arrangements must match the eventual judge window. |
| Final submission | Fill the observed form with final links, team/profile details and reviewed copy; resolve the final consent and retain confirmation. | Form fields are known; no submission yet. No HTML maxlength was exposed, which does not prove no server-side limits. |
| Real user sessions | Obtain participant contacts, run 2–3 prepared sessions and record permissioned feedback. | **0 actual sessions**; no invented users, findings or time savings. |
| Human gold review | Complete the [offline 27-record review packet](gold-review.html) and export the named review with decisions/comments and human declaration. | A human reviewer is still needed; the review UI and agent/code reviews do not satisfy human adjudication. |
| Held-out evaluation | Run only following an explicit user instruction covering the test split. | Intentionally unrun; “complete remaining work” does not override the earlier test prohibition. |
| Judge-period operation | Confirm required availability window, actual provider balances and an operating budget. | Shared admission is implemented; it is not a provider invoice ceiling or evidence of remaining credits. |
| X / Most Viral | Confirm posting identity, official tags and final links, then record any actual publication. | Drafts only; account and final details are still needed. |

The owner confirmed SHOU-FENG CHANG (`Robert_Chang`) is the sole contributor. The final checkbox grants Meta and Cerebral Valley rights to use and
promote submitted materials, and has not been accepted. See the [submission kit](submission/README.md),
[evidence map](submission/evidence-map.md), [demo provenance](demo/README.md) and [user-study kit](user-study/README.md).

The original plan mentions service availability through November 16; confirm the event's actual requirement
before treating that planning date as an operating commitment. Successful Baseten benchmarks do not switch
the production extractor, and implemented private mode does not make the other workflows private.
