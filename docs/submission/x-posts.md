# NoCap — X drafts

**Unpublished drafts, updated 2026-11-01 for the S&P 500 coverage.** The owner decides whether and from which account to post. At submission, live checks are open through 2026-11-17 16:00 UTC under a $5 daily cap, and saved examples plus three recorded samples (two real SEC documents and one Private check) always replay at $0. <!-- verify-at-release --> No official handle or event hashtag has been verified; leave them out until confirmed.

A comment above each post gives its length and the source of each figure, with line numbers as of November 1. "As typed" counts every character, with links at full length; "X count" follows X's rules, where each link counts as 23 and ✓ and ✗ count as 2.

## Main post

Keeps the opener and adds the coverage. It links the live product, because the attached X cut is the demo.

<!-- 279 as typed, X count 271. Hackathon: README.md:7. Checks the numbers in an earnings draft against SEC filings, and every ✓ or ✗ comes with a receipt: README.md:3. All 500 S&P 500 companies: README.md:20, :141. Muse Spark, Muse Wonder, SAM 3.1: README.md:30-36. Live product: README.md:17. -->

```text
Meet NoCap, built for the Meta Global AI Developer Hackathon: it checks the numbers in an earnings draft against SEC filings for all 500 S&P 500 companies, with a receipt for every ✓ or ✗. Powered by Muse Spark, Muse Wonder and SAM 3.1.

Try it: https://receipts-nocap.vercel.app
```

## Alternative openers for A/B choice

Alternatives to the main post, not extra posts. The form takes one post URL ([submission copy](writeup.md)), so pick one opener and put the thread under it.

### Alternative A: the stakes

Opens like the full walkthrough's stakes card.

<!-- 278 as typed, X count 270. "One wrong number. One public correction.": docs/demo/video-script.md:16. All 500 S&P 500 companies: README.md:20, :141. A receipt for every ✓ or ✗: README.md:3. Models: README.md:30-36. Hackathon: README.md:7. -->

```text
One wrong number. One public correction.

NoCap checks earnings drafts against SEC filings for all 500 S&P 500 companies, with a receipt for every ✓ or ✗. Built on Muse Spark, Muse Wonder and SAM 3.1 for the Meta Global AI Developer Hackathon.

https://receipts-nocap.vercel.app
```

### Alternative B: the planted errors

Spells out the two errors the X cut opens on.

<!-- 274 as typed, X count 264. "up 18% from a year ago" and "GAAP earnings per diluted share were $2.22": examples/nvda_q2_fy2027.txt:1, :5. Filed year-over-year growth of 105.85%: README.md:11. $2.22 is non-GAAP diluted EPS and the GAAP figure is $2.46: README.md:116; "adjusted EPS": docs/demo/video-script.md:56. "AI-drafted", "two planted errors", "NoCap flags both": docs/demo/video-script.md:49. All 500 S&P 500 companies: README.md:20; the X cut's end card says the same. -->

```text
An AI-drafted NVIDIA earnings story, two planted errors:

"up 18% from a year ago": the filings give 105.85%
GAAP EPS of $2.22: that's adjusted; GAAP was $2.46

NoCap flagged both, with SEC receipts. Now covering all 500 S&P 500 companies.

https://receipts-nocap.vercel.app
```

Alternative B leaves the hackathon and the Meta models to the video's title and end cards. If you post it, end reply 1/4 with "Built for the Meta Global AI Developer Hackathon." (1/4 then counts 270.)

## Optional four-post thread

Replies under whichever opener you post, in this order.

<!-- 220 as typed, X count 220. GAAP versus adjusted EPS, year-over-year versus quarter-over-quarter growth: README.md:5. Muse Spark 1.3 extracts the claims: README.md:30. Code checks values, periods and basis: README.md:127. Ambiguity returns ?: README.md:51. -->

```text
1/4 A plausible earnings draft can mix GAAP with adjusted EPS or confuse year-over-year and quarter-over-quarter growth. Muse Spark 1.3 extracts the claims; code checks filed values, periods and basis. Ambiguity stays ?.
```

<!-- 269 as typed, X count 269. All 500 S&P 500 companies: README.md:20. October 31, the latest four releases of 467 companies, one day, 1,969 calls, $42.97: README.md:20, :31; docs/evidence-expansion.md:176, :180-181. A cell kept only when code's own parse matched, behind an XBRL gate: README.md:20. Three QA rounds, spot checks rather than an accuracy estimate: docs/evidence-expansion.md:379-390. "Independent" is left out because the docs don't say who ran the rounds, and every review the evidence map lists is AI-assisted. -->

```text
2/4 Coverage: all 500 S&P 500 companies. On Oct 31, Muse Spark 1.3 mapped the latest four earnings releases of 467 of them in one day: 1,969 calls, $42.97. Code kept a cell only when its own parse matched, behind an XBRL gate. Three QA rounds spot-checked the new data.
```

<!-- 274 as typed, X count 274. 44/48, 1/47, 22/110, 20/20, 0/52: README.md:61-64. Frozen source, run once on October 29, small correlated sets: README.md:73. S&P 500 expansion on October 31: README.md:141. -->

```text
3/4 Frozen held-out test: NoCap caught 44/48 planted errors, with 1 false alarm in 47 unmodified sentences; 22/110 true figures stayed ?. Trap drafts: 20/20 caught, 0/52 true figures flagged. Small seeded tests, run Oct 29 before the S&P 500 expansion; not general accuracy.
```

<!-- 252 as typed, X count 214. Live through 2026-11-17 16:00 UTC: README.md:17. 2:52 walkthrough with recorded runs and a synthetic voice: README.md:13. Time travel: docs/demo/video-script.md:26. Meta's whole 10-K in one Muse Spark request: README.md:71. --> <!-- verify-at-release -->

```text
4/4 Live checks run through Nov 17. The full 2:52 walkthrough adds time travel and Meta's whole 10-K in one Muse Spark request (recorded runs, synthetic voice): https://receipts-nocap.vercel.app/demo
Code: https://github.com/changshoufeng0609-cmd/nocap
```

## Ready replies

Post one only when a reply calls for it.

When someone asks how accurate the 500-company version is:

<!-- 233 as typed, X count 234. Two spot checks, each run once on fresh items (v1 Nov 1, v2 Nov 2): 54/70 caught, 1 marked ✓, 4/147 true figures wrong: README.md:65-66, :73; eval/spot500_methods.md (both Results sections). Small seeded tests, not general accuracy: eval/spot500_methods.md (Limits). -->

```text
Two one-shot spot checks of the deployed 500-company versions on fresh items (Nov 1 and 2): 54/70 planted errors caught, only 1 marked ✓; only 4/147 true figures flagged wrong. Small seeded tests, each run once, not general accuracy.
```

When the $5 daily cap has been reached:

<!-- 201 as typed, X count 191. $5 per UTC day, and live work pauses until the next UTC day: README.md:18. Saved examples and recorded runs replay at $0: README.md:19, :22. -->

```text
Thanks for trying NoCap! Today's live checks hit the $5 daily cap, so live work pauses until the next UTC day. The saved examples and recorded runs still replay at $0: https://receipts-nocap.vercel.app
```

## Attachment and release notes — do not paste

- Submission status: the authenticated form contains earlier copy, links and the sole-member roster; refresh from the final submission kit before submission; the optional X URL is empty. Fields are recorded in [completion plan](../completion-plan.md#actual-form-observed). No final consent or submission has occurred; these posts remain unpublished drafts. Publish before the form is submitted, so its X field can carry the post's URL.
- After owner review, attach the 56-second X cut ([`nocap-x.mp4`](../demo/assets/nocap-x.mp4): it opens on the red ✗s, with 44 px burned-in captions for muted autoplay, and its end card carries the disclosure and "Now covering all 500 S&P 500 companies") or the full walkthrough; the audio-free 20-second GIF works as a shorter teaser. Assets, validation and [narration sources](../demo/narration-license.md) are in the demo kit; use the current export (X cut SHA-256 `3b03ca6c…`, end cards with the all-500 line), not an earlier-branded, earlier-voiced or pre-coverage file.
- Suggested media description: “NoCap checks an AI-drafted NVIDIA earnings story, opens SEC evidence receipts, rechecks a numeric correction, applies a historical cutoff, reads a real Bank of America SEC slide with SAM 3.1 and asks Meta's whole 10-K. The end card says NoCap now covers all 500 S&P 500 companies. Real interface with zooms and synthetic narration; cached extraction and recorded live runs are disclosed.”
- Suggested media description for the X cut: “NoCap flags two planted errors in an AI-drafted NVIDIA earnings story, shows the SEC receipt, rechecks a numeric correction and finds the two bar charts on a real Bank of America SEC slide with SAM 3.1. The end card says NoCap now covers all 500 S&P 500 companies. Real interface, synthetic narration; cached extraction and recorded live runs are disclosed.”
- The short GIF is a continuous 20-second excerpt (seconds 16.56–36.56 of the walkthrough) at original speed. Describe only visible actions; it does not contain every workflow or spoken narration.
- Keep the captions and replay disclosure with the video. The slide segment replays the [October 30 recorded live run](../check-a-slide.md#real-sec-slide-sample-2026-10-30) on a real Bank of America SEC slide (3 calls, $0.0302); the filing segment replays the recorded whole-10-K request ([run](../ask.md#whole-filing-live-validation-2026-10-30)). The video remains a replay.
- Every post above is within 280 characters both as typed and as X counts it. After any edit, recount: X counts each link as 23 characters and ✓, ✗ and emoji as 2 each. Recheck the final composed posts and any confirmed tags before publication. Do not add unsupported performance, customer, accuracy or time-savings claims.
- The coverage lines describe the November 1 snapshot, which covers all 500 companies. If a later latest-quarter top-up changes the company count, update every "500" (main post, both alternatives and 2/4) before posting. The October 31 mapping figures in 2/4 (467 companies, 1,969 calls, $42.97) describe that day and stay as they are.
- Public links are [the live product](https://receipts-nocap.vercel.app), [the demo player](https://receipts-nocap.vercel.app/demo) and [the repository](https://github.com/changshoufeng0609-cmd/nocap); recheck their anonymous access after the release, and post 4/4 only once the repository is public. <!-- verify-at-release --> Any official mention/hashtag and the posting account need owner confirmation. No post has been sent, scheduled or published.
- Before posting, confirm the release's anonymous acceptance and that live checks are open; if the daily cap is reached, visitors see saved examples and recorded samples only, and the second ready reply says so. Admission accounting is not a provider billing hard cap.

Evidence and limitations: [submission evidence map](evidence-map.md).
