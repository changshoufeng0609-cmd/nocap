# Remaining work — November 1, 2026

The owner selected **1, 3, 4, 5, 6 and 8**. Items **1, 3, 4, 5, 6 and 8 are complete**;
item **3** (whole-filing live validation) was completed on 2026-10-30.
The sole contributor is **SHOU-FENG CHANG (`Robert_Chang`)**, known as Robert Chang.
The hackathon form is **not submitted** and its material-use consent is unchecked.

## Selected items

| Item | Status and evidence | Limits |
|---|---|---|
| 1 — Operation-led demo | **Complete; replaced locally on October 30–31 by the 2:52 v2.1** (male synthetic voice, original music, real SEC slide, whole-10-K and recorded Private segments, held-out results card, plus a 56-second X cut; deployed November 1, [demo kit](demo/README.md)). v1 (2:45, deployed October 29–30; [record](public-release-update-2026-10-29.json)), continuous browser actions, 18 English cues and a 20-second continuous GIF. Actual paste → Check → receipt → Fix/recheck → cutoff → Slide → short Ask. [Media provenance](demo/README.md). | Cached extraction and recorded real Fix/Slide/Ask responses are labeled; waits are shortened. Technical, transcript and sampled visual review is not human editorial approval. Native captions are optional to avoid duplicating burned-in captions. |
| 3 — Whole-filing live validation | **Complete (2026-10-30):** Meta's 2025 10-K (516,028 characters) imported with a real declared SEC contact; one live Muse Spark request read the whole filing (101,135 input tokens, 6.14 s, $0.128582) and code accepted 4 exact quotes, 0 rejected. A real Bank of America SEC slide ran through SAM 3.1 and Spark: 2 SAM chart regions, 5/5 GAAP bars verified. Both are recorded samples that replay at $0. [Quote run](ask.md#whole-filing-live-validation-2026-10-30) · [Slide run](check-a-slide.md#real-sec-slide-sample-2026-10-30). | About 10% of the 1M-token window, not a limit test. One filing and one slide are demonstrations, not accuracy measurements. Replays are not re-verified on later code. |
| 4 — Private HTTP/browser validation | **Complete:** actual browser → HTTP → Modal Glimmer → verifier, 1 verified / 1 wrong, receipt UI, clearing, mode switching and zero application-cache checks. [Evidence](private-http-live-validation-2026-10-29.json). | Hosted processing, not offline or infrastructure zero retention. Three new shared apps stopped/tasks zero; $1.25997788 observed usage includes the 50 Glimmer Q&A calls. Live Private mode stays unavailable on the public site; since the November 1 release, one recorded Private check replays there at $0. |
| 5 — Four-model answer comparison | **Complete:** [public benchmark explorer](https://receipts-nocap.vercel.app/benchmarks), 50 frozen questions × four backends, all 200 responses, receipts and download. [Results](../eval/qa_results.md) and [methods](../eval/qa_methods.md). | Pre-J2 frozen gate; original strict parser accepted none. Explicit post-run metadata sensitivity accepted 14/50 Spark, 0/50 Wonder, 17/50 Spark Open and 6/50 Glimmer. Precision is N/A at zero acceptance; low coverage and provisional supported-snapshot labels remain visible. |
| 6 — Reviews, history and release | **Complete:** [J2](j2-review-2026-10-29.md) closed both bound/qualifier findings; [J3](j3-review-2026-10-29.md) reviews the final package. [65 reconstructed source-history commits](source-history.md) were published on October 29; they become public again with the repository after the final top-up on November 4 (private since October 30). <!-- verify-at-release --> The [November 1 release acceptance](public-release-2026-11-01.json) passed. | J2/J3 are AI-assisted, unofficial, with disclosed conflicts; not human adjudication or actual judging. Original private history unchanged. Git dates are metadata, not independent timestamp attestations. |
| 8 — Held-out evaluation | **Complete:** [one-time frozen evaluation](../eval/heldout_results.md), all 126 files unchanged; no result-driven tuning or rerun. v0 caught 44/48, with 1/47 original-item false alarms; trap caught 20/20, with 0/52 true-figure false alarms. | 117 fresh calls / $0.49834625, 11 within-run extraction reuses, no pre-existing response cache, errors or skips. Four missed planted errors remained `?`. Small correlated, provisional-label tests do not establish general accuracy. |
| 500-company accuracy test | **Complete (November 2):** a [one-shot comprehensive test](../eval/spot500_methods.md#results-of-the-one-live-run-november-2-2026-after-round-8) of the deployed round-8 code and evidence (private `8c2fd78`): 1,000 frozen items from 400 companies, on sentences no earlier test used. 306/360 planted errors caught, none marked ✓; 17/1,298 true figures marked ✗; 736/1,298 verified; a ✓ was right 736/738 times. On the 312 companies not used in development: 235/274 caught, 14/1,018 marked ✗. | $3.80, 940 calls, run once and not to be repeated. The first comprehensive test (before round 8: 310/376, 14/1,271) and three earlier spot checks stay in the methods doc. 533 true figures returned `?`, mostly untracked figure types, qualified wording and missing non-GAAP release facts. Machine-made labels without human review. |

**November 1 release.** It passed:
- 49 anonymous desktop and mobile checks at $0;
- a consented live smoke, $0.202 in total: Check, Fix, Slide, Ask, a balance-sheet check, and a whole-filing question that reused 101,105 cached prompt tokens.

Live checks are open until 2026-11-17 16:00 UTC, at US$5 per UTC day. Visitors can also explore the saved examples, the demo and the benchmark data at $0. <!-- verify-at-release -->

On October 29, known batch model/GPU usage was **$3.30454604** against the authorized **$10** estimate; later live runs (October 30–31) are costed in their own records. Shared GPU work
is counted once; provider metering can lag. Public acceptance added zero provider calls/cost.
[Allocation and reconciliation](completion-round-2026-10-29.md).

## Newly identified review follow-up

The owner's subsequent Claude Code Opus 5.5/max review and three specialist reviews
are complete. [Cross-checked findings and reproduction artifacts](reviews/opus-review-20261029/README.md)
identify five new P1 correctness categories: comparison-period attachment, `US$` bounds,
source numeric-token normalization, growth qualifications and explicit YoY/QoQ binding.
A number-only quote limitation was already known. Additional scoped findings cover
draft preservation, cancellation, resource admission, availability messaging and retention.

**Fixed in private source on 2026-10-30.** All sixteen findings are closed or documented in the
[fix closure](reviews/opus-review-20261029/fix-closure.md), with dispositions for the follow-up
review findings and the residual limitations. The guarded development replay equals the prior
baseline, saved examples are unchanged and no provider call was made. The verifier changed after
the frozen held-out run, so the historical held-out results describe source `61e1055`, not this
version; they were not rerun. The fixes are public and deployed: the anonymous Production check passed with
live requests paused ([release record](public-release-fix-2026-10-30.json)).

## Outside this selected round

| Work | Remaining dependency |
|---|---|
| Formal submission | The website has been live since November 1. Remaining steps:<br>1. Make `changshoufeng0609-cmd/nocap` public again after the final top-up on November 4. It has been private since Oct 30 at the owner's request.<br>2. Refresh the previously filled form from the final submission kit.<br>3. Obtain the owner's material-use consent.<br>4. Submit only when authorized.<br>No entry has been submitted. <!-- verify-at-release --> |
| Real participant sessions | Prepared kit, **0 actual sessions**. Requires real participants; no invented adoption, findings or time savings. |
| Independent human gold review | [27-record review packet](gold-review.html) is ready; a human reviewer is still required. Agent/code review does not satisfy this. |
| Judge-period paid operation | **Approved 2026-10-30; running since 2026-11-01.**<br>Live requests are open through 2026-11-17 16:00 UTC, with:<br>• US$5 per UTC day;<br>• the US$1.50 Check/Fix reserve;<br>• 5 whole-filing questions a day.<br>See the [runbook](deploy.md#judging-window-approved-2026-10-30). The owner applied the Production settings, and Preview stays paused. The existing shared Neon allowance remains intact. |
| X/community publication | Drafts only; posting identity and official tags remain unconfirmed. Nothing posted or scheduled. |

Commit hashes on this page refer to the private development history, which is not in the public repository;
the [source-history mapping](source-history.md) gives the public counterpart of `61e1055`.

The codebase also includes [Ask](ask.md), [slide verification](check-a-slide.md),
number coverage, the [read-only MCP server](mcp.md),
[S&P 500 evidence](evidence-expansion.md) and shared spend admission. Their implementation
and historical live checks remain distinct from a general accuracy or always-on availability claim.
