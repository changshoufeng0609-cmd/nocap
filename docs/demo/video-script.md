# NoCap video script — v2.1, 2:51.9

The [current MP4](assets/receipts-demo.mp4) is **171.88 seconds**: a re-edit of the October 30 v2 recording of
the real interface (commit `4bd6f4c`) plus one Private take recorded on October 31 (commit `d6f7ebc`). It uses
cuts and camera moves, five text cards, one edit-layer highlight on the SEC slide and an original music bed
([provenance](narration-license.md#music-bed-v21)).
Narration is Kokoro-82M `am_fenrir`, a synthetic American English male voice generated on the local CPU
([provenance](narration-license.md)). Captions are burned in and match the
[SRT](assets/receipts-demo.en.srt)/[VTT](assets/receipts-demo.en.vtt) sidecars. Author: Robert Chang, Team
NoCap. Waits are not shown; nothing is sped up.

## Shots

| Time | Shot | Picture |
|---|---|---|
| 0:00.00–0:04.45 | Stakes card | "One wrong number. / One public correction." Subtitle: financial journalists and analysts check every figure before it runs |
| 0:04.00–0:11.36 | Cold open footage | The checked NVIDIA draft: "18%" struck with "✗ 105.85%"; at 0:06.15 the camera moves down to "$2.22" with "✗ $2.46" |
| 0:10.96–0:15.96 | Title and disclosure card (5.0 s) | NoCap logo and one-line pitch; "How this video was made", six lines: real interface recorded locally on Oct 30–31, 2026; Check uses cached model output for saved examples; Fix, Slide, Ask and Private replay recorded live runs; the "Live checks are open" notice shows the judging-window setting; synthetic voice (Kokoro-82M); labels, highlights and original music added in editing |
| 0:15.56–0:22.86 | Check, wide | Draft tab with the green "Live checks are open until 2026-11-17 16:00 UTC" notice; scroll, click Check. Label, top right and clear of the Check button: "Muse Spark 1.3 extracts the claims · code decides each verdict" |
| 0:22.86–0:28.78 | Verdict tiles (1.6×) | 7 Verified, 2 Wrong, 2 Unverifiable, 0 Approximate; "11 claims checked", "muse-spark-1.3", "Cached"; number coverage 10 of 10 |
| 0:28.78–0:40.57 | Growth receipt | The paragraph with "18% ✗ 105.85%", then the receipt: Claimed 18%, Filed 105.85%, ($96,221,000,000 ÷ $46,743,000,000 − 1) × 100 ≈ 105.85% |
| 0:40.57–0:45.20 | Sources | Why, then Receipts · 3 filings: 8-K press release, $46.743B, accession 0001045810-25-000207, accepted 2025-08-27 20:22:14 UTC |
| 0:45.20–0:52.06 | EPS basis mismatch | Click "$2.22"; receipt (2.4×): Claimed $2.22, Filed $2.46; "$2.22 is NVIDIA's non-GAAP diluted EPS … the GAAP figure is $2.46" |
| 0:51.71–1:04.01 | Fix with receipts | Click Fix; original and revised drafts (18% → 105.9%); then the recheck counts close up: "2 corrections verified", 9 verified, 0 wrong, 2 unverifiable, 0 approximate; 3.4 s, $0.0111. Label: "Muse Wonder 1.0 proposes · code constrains and rechecks" |
| 1:04.01–1:07.15 | Correction receipts | "[1] 18% → 105.9%" with its filing cards |
| 1:06.80–1:13.09 | Time travel | Pick "June-quarter roundup, as of July 29"; the cutoff reads 2026/07/29 04:00 PM UTC. Label: "Time travel: availability follows EDGAR acceptance time" |
| 1:13.09–1:15.29 | Cutoff tiles | 6 Verified, 1 Wrong, 2 Not public yet, 0 Unverifiable; "As of 2026-07-29 16:00 UTC" |
| 1:15.29–1:21.56 | Not-public-yet receipt | Microsoft "$90.0 billion ◷ not public yet"; receipt (1.8×): "Not public until 2026-07-29 20:04:53 UTC · 4 h 5 min before it was filed"; Claimed $90B, Filed later $90.007B |
| 1:21.22–1:47.47 | Real SEC slide, one continuous take | Slide tab, "Try a real SEC slide". From 1:25.08 the run summary: 32 readings, 5 verified, 0 wrong, 27 unverifiable; recorded live run 2026-10-30 07:20 UTC; original run 21 s / $0.0302, 3 model calls; 8-K Exhibit 99.2. The real click on "Show chart/table regions (2 SAM, 2 Spark)" scrolls the page to the slide (1:32.39). **1:33.86–1:39.49, edit layer:** amber outlines and a 45% dim around the two regions the product itself labels "Bar Chart · SAM", with the corner label "SAM 3.1 · two bar-chart regions". From 1:38.99 a 2.0× push to the net-interest-income chart: readings 6–10 (GAAP) outlined green, 1–5 (FTE totals) dashed. Label: "SAM 3.1 finds the chart regions · Muse Spark 1.3 reads the labels" |
| 1:47.47–1:51.24 | Slide receipt | Reading 10: Claimed $16B, Filed $15.997B; "Matches Bank of America's 10-Q: GAAP net interest income was $15.997 billion for Q2 2026"; 8-K press release |
| 1:50.89–1:56.52 | Ask, filing quotes | Ask tab, "Filing quotes": META · 10-K · 0001628280-26-003942, accepted 2026-01-29 00:13:46 UTC, 516,028 normalized characters. Label: "Muse Spark 1.3 · one request reads the whole 10-K" |
| 1:56.52–2:06.27 | One request | Result header (recorded live run 2026-10-30 07:23:11 UTC; original run 6.1 s / $0.13; 1 model call), then a 2.1× pan along "Muse Spark read the complete filing in one request: 101,135 input tokens for 516,028 characters, with no retrieval step or truncation." |
| 2:06.27–2:11.05 | Quotes | Three of the four exact quotes with their character offsets (capital expenditures of $115–135 billion in 2026; Reality Labs losses) |
| 2:10.65–2:21.19 | Backends card (10.5 s) | "Same verifier, four backends": Muse Spark 1.3 on the Meta Model API (default extractor); Muse Spark Open on Fireworks and on Baseten (27/27 gold claims found on each); Muse Glimmer on our own Modal H100 (Private check: unpublished drafts), all feeding the same code verifier. "200 closed-book answers from four Meta models: the gate let 0 wrong numbers through." Notes: gold set of 27 claims in 10 NVIDIA passages, one extra claim stayed ? on each host; the Q&A is a development benchmark (held-out split not used), post-run analysis; the gate accepted 0–34% of each model's answers |
| 2:20.84–2:30.63 | Private check (take of Oct 31) | Private tab, framed on the form card (1.25×); the cursor clicks "Replay a recorded Private check", the form fills with the public sample ("NVIDIA reported revenue of $96.2 billion and GAAP diluted EPS of $2.22 in Q2 FY2027."; NVIDIA Q2 FY2027; as of 2026-10-29T00:00:00Z) and the page scrolls to the result; from 2:26.31 the camera frames it (1.28×): "Recorded Private check · Muse Glimmer · 2 claims"; "Recorded live run on the operator's Modal (2026-10-30 18:17 UTC) · $0 this replay · original run: 26 s, 1 model call"; ✓ Verified $96.2 billion, "Matches NVIDIA's 10-Q: revenue was $96.221 billion"; ✗ Wrong $2.22 GAAP diluted EPS, "$2.22 is NVIDIA's non-GAAP diluted EPS … the GAAP figure is $2.46". Label (top right): "Private check · Muse Glimmer on our own Modal GPU" |
| 2:30.23–2:40.65 | Results card (10.4 s) | Since November 2, the comprehensive test: "1,000 frozen test items from 400 S&P 500 companies"; 306/360 planted errors caught (none marked ✓; the other 54 came back ?); 736/738 times a ✓ was right (99.7%; the other 2 landed on figures labeled guidance); 17/1,298 true figures flagged ✗ (1.3%; 736 verified and 533 left ? instead of guessed); a secondary line, "Earlier held-out test, run once on Oct 29: 44/48 planted errors caught, 1/47 false alarm, 20/20 in trap drafts"; caveat: one frozen run of the November 2 release, not rerun; labels machine-made, with no human review yet; details in eval/spot500_methods.md. Until November 2 the card showed only the held-out test ("Measured on held-out tests") |
| 2:40.25–2:51.88 | End card | "Now covering all 500 S&P 500 companies" in green under the tagline (added October 31 as "Now covering 499 S&P 500 companies" and updated November 1, when Exxon Mobil joined: the footage predates that coverage); receipts-nocap.vercel.app; "Live checks open through Nov 17"; github.com/changshoufeng0609-cmd/nocap; the five Meta models; built during the Meta Global AI Developer Hackathon · Robert Chang · Team NoCap |

Shots with overlapping times cross-fade (0.35–0.45 s). Zoom factors are relative to the full 1280 × 640
viewport; the master is recorded at 2×, so a 2.4× crop is still upscaled only 1.2×. The SAM highlight boxes are
the DOM boxes logged at the `slide-regions` mark, after the page stopped scrolling; the manifest lists them under
`edit_overlays`.

## Narration and captions

| Time | Caption (narration) |
|---|---|
| 0:00.35–0:04.50 | One wrong number in an earnings story becomes a public correction. |
| 0:04.50–0:11.16 | This AI-drafted story has two planted errors. NoCap flags both, with the filed figures. |
| 0:11.16–0:16.16 | NoCap checks every financial number in a draft against SEC filings. |
| 0:16.16–0:21.32 | Muse Spark pulls out each claim. Code checks every one against the filings. |
| 0:23.01–0:28.48 | Seven verified. Two wrong. Two unverifiable, because NoCap won't guess. |
| 0:28.93–0:34.01 | The draft says revenue grew 18% from a year ago. That's the quarterly rate. |
| 0:34.01–0:39.57 | The receipt does the math from the filings: 105.85%. |
| 0:40.72–0:44.90 | Each receipt cites the filing, down to when EDGAR accepted it. |
| 0:45.40–0:51.26 | And $2.22 is adjusted EPS. The GAAP figure was $2.46. |
| 0:52.21–0:58.77 | Fix with receipts: Muse Wonder proposes corrected numbers, and code lets only the digits change. |
| 0:58.77–1:03.11 | Then the whole draft is checked again: nine verified, zero wrong. |
| 1:04.16–1:06.45 | Each correction keeps its receipt. |
| 1:07.30–1:13.24 | Time travel: check this roundup as of July 29, 4 p.m. UTC. |
| 1:13.24–1:20.36 | Microsoft's revenue figure is right, but its filing reached EDGAR hours later, so it's marked not public yet. |
| 1:21.61–1:26.30 | Now a real SEC slide: Bank of America's earnings presentation. |
| 1:26.30–1:31.72 | Its recorded live run: three model calls, 21 seconds, about three cents. |
| 1:32.91–1:39.24 | SAM 3.1 finds both bar charts, and Muse Spark reads 32 printed labels. |
| 1:39.24–1:47.08 | Only the five GAAP bars verify. The rest are non-GAAP, untracked or ambiguous, so they stay unverifiable. |
| 1:47.67–1:49.84 | Each reading opens its receipt. |
| 1:51.39–1:56.67 | Ask a question of Meta's entire 10-K: more than half a million characters. |
| 1:56.67–2:05.77 | One Muse Spark request reads all of it in six seconds: 101,135 input tokens, no retrieval, no truncation. |
| 2:06.42–2:10.15 | Code keeps only exact quotes it can find in the filing. |
| 2:11.20–2:21.09 | Same verifier, four backends: Spark Open found all 27 gold claims, and Glimmer reads unpublished drafts on our own GPU. |
| 2:21.34–2:30.43 | Private check runs on Glimmer: one real run on our own Modal GPU, one figure verified, one planted error caught. |
| 2:30.68–2:39.57 | Across 1,000 test items, NoCap caught 306 of 360 planted errors and never marked one verified. (Since November 2; until then: "On held-out tests, NoCap caught 44 of 48 planted errors, with one false alarm in 47 clean sentences.") |
| 2:40.75–2:50.08 | NoCap, for financial journalists and analysts. Built during this hackathon by Robert Chang. Try it live through November 17. |

26 cues, 338 words, 140.2 s of speech in 171.88 s. The narration text is in
[`narration_v2.py`](narration_v2.py). Spoken text differs from captions only where pronunciation needs help:
figures are spelled out, and NoCap, GAAP, SEC, UTC, GPU, API, 10-K, SAM, Baseten and "backends" are respelled
(for example "gap", "S E C", "Sam three point one", "Base ten", "back ends"). The respellings were checked
against Kokoro's phonemes; c23, c24 and the November 2 c25 were also transcribed by local Whisper small and
medium. Six lines are new in v2.1 (c02, c17, c18, c23, c24, c25); c25 got new text and new audio on November 2
(8.24 s, in the slot of the 8.52 s line it replaced). The other twenty reuse the v2 audio files unchanged.
Per-cue start times, durations and audio hashes are in the [manifest](assets/manifest.json).

## Every number on screen, with its source

| Number | Where | Source |
|---|---|---|
| 7 verified, 2 wrong, 2 unverifiable, 0 approximate; 11 claims; 10 of 10 numbers linked | Tiles | Saved NVIDIA example, replayed by the real API (cached `muse-spark-1.3` extraction, deterministic verdicts) |
| 18% claimed; 105.85% = ($96,221,000,000 ÷ $46,743,000,000 − 1) × 100 | Growth receipt | Same response; NVIDIA 8-K/10-Q values for Q2 FY2027 and Q2 FY2026 |
| $46.743B, accession 0001045810-25-000207, accepted 2025-08-27 20:22:14 UTC | Sources | Same response (NVIDIA 8-K press release) |
| $2.22 claimed; $2.46 filed GAAP diluted EPS | EPS receipt | Same response; NVIDIA 8-K 0001045810-26-000073 and 10-Q 0001045810-26-000075 |
| 2 corrections; 9 / 0 / 2 / 0; 18% → 105.9%; 3.4 s, $0.0111 | Fix | [Recorded October 29 Fix response](fix-live-2026-10-29.json) (one Muse Wonder call, $0.01108) |
| 2026-07-29 16:00 UTC cutoff; 6 / 1 / 2 not public yet / 0; $90.0 billion; not public until 2026-07-29 20:04:53 UTC, 4 h 5 min later; claimed $90B, filed later $90.007B | Time travel | Saved roundup example at its saved cutoff, replayed by the real API |
| 32 readings: 5 / 0 / 27 / 0; 2 SAM and 2 Spark regions; recorded 2026-10-30 07:20 UTC; 21 s, $0.0302, 3 model calls; accepted 2026-07-14 10:45 UTC | Slide | Recorded October 30 live run on Bank of America's 2Q26 presentation, page 9 (8-K Exhibit 99.2), replayed by the real API; [slide doc](../check-a-slide.md#real-sec-slide-sample-2026-10-30). Of the 27 unverifiable readings, 11 are FTE-qualified (non-GAAP), 15 are metrics NoCap does not track (yields, NII excluding Global Markets) and 1 is ambiguous ($16.0B with no stated basis) |
| Reading 10: claimed $16B, filed $15.997B; 8-K 0000070858-26-000353, accepted 2026-07-14 10:45:08 UTC | Slide receipt | Same recorded run |
| 516,028 characters; 101,135 input tokens; 6.1 s, $0.13, 1 model call; recorded 2026-10-30 07:23:11 UTC; 10-K 0001628280-26-003942 accepted 2026-01-29 00:13:46 UTC; $115–135 billion capex quote; $19.19 billion Reality Labs quote | Ask | Recorded October 30 live run, replayed by the real API; [Ask doc](../ask.md#whole-filing-live-validation-2026-10-30) |
| 27/27 gold claims on Fireworks and on Baseten, one extra claim left ? on each; Glimmer on one Modal H100 | Backends card | [Open-weights record](../open-weights.md) (Fireworks run 2026-10-28, Baseten run 2026-10-29, Modal deployment) |
| 200 closed-book answers from four Meta models; 0 wrong numbers passed; 0–34% accepted per model | Backends card | [Closed-book Q&A results](../../eval/qa_results.md): a development benchmark (held-out split not used), post-run analysis; the pre-registered strict parser accepted none of the 200 |
| 1,000 frozen items from 400 S&P 500 companies, run once on November 2 (the round-8 release); 306/360 planted errors caught, 0 marked ✓, 54 returned ?; a ✓ right 736 of 738 times (99.7%), the other 2 on figures labeled guidance; 17/1,298 true figures marked ✗ (1.3%), 736 verified, 533 returned ? | Results card and line | [README Results table](../../README.md#results) and the [comprehensive test](../../eval/spot500_methods.md#results-of-the-one-live-run-november-2-2026-after-round-8) |
| Secondary line: 44/48 planted errors caught; 1/47 false alarm; 20/20 in trap drafts; run once on October 29 (frozen source 61e1055) | Results card | [README Results table](../../README.md#results) and [held-out results](../../eval/heldout_results.md) |
| 2026-11-17 16:00 UTC; "through Nov 17" | Notice, end card | Owner-approved judging window, [deploy.md](../deploy.md#judging-window-approved-2026-10-30); shown through the disclosed health substitution |
| All 500 S&P 500 companies | End card (both cuts) | Coverage on November 1, 2026, after the footage was recorded: all 500 index members of October 31; Exxon Mobil, the one missing on October 31, joined on November 1; [README coverage](../../README.md#coverage-and-limitations), [snapshot manifest](../../data/snapshot/manifest.json) (500 companies, commit `e61df17`) |

The cutoff, Fix, Slide and Ask results are single recorded or seeded demonstrations, not accuracy
measurements. No held-out item appears in the footage; the results card quotes the README.

## X cut, 0:56.3

[`nocap-x.mp4`](assets/nocap-x.mp4) is a 56.28-second cut for posting on X, edited from the same capture with
nine of the walkthrough's lines, unchanged ([SRT](assets/nocap-x.en.srt), [VTT](assets/nocap-x.en.vtt)). X
autoplays muted, so it opens on the red ✗s instead of the stakes card and its captions are 44 px instead of 34 px.
A one-line disclosure sits on its title card, and a fuller one on the end card.

| Time | Shot | Picture |
|---|---|---|
| 0:00.00–0:07.43 | Cold open footage | The checked NVIDIA draft from the first frame: "18%" struck with "✗ 105.85%"; at 0:05.00 the camera moves down to "$2.22" with "✗ $2.46" |
| 0:07.43–0:10.61 | Verdict tiles (1.6×) | 7 Verified, 2 Wrong, 2 Unverifiable, 0 Approximate |
| 0:10.26–0:15.91 | Title card | NoCap logo; "Checks every financial number in a draft against SEC filings."; "Built with Meta's Muse Spark, Muse Wonder and SAM 3.1"; "The real interface · recorded runs replayed · synthetic voice" |
| 0:15.56–0:26.94 | Growth receipt | As in the walkthrough: claimed 18%, filed 105.85%, with the calculation |
| 0:26.59–0:38.29 | Fix with receipts | As in the walkthrough, ending on 9 verified, 0 wrong. Label: "Muse Wonder 1.0 proposes · code constrains and rechecks" |
| 0:37.94–0:45.47 | SAM regions | The real Bank of America slide; from 0:38.39 the edit-layer highlight on the two "Bar Chart · SAM" regions, with the label "SAM 3.1 · two bar-chart regions" |
| 0:45.07–0:56.28 | End card | As in the walkthrough, plus two lines: "The real interface, recorded locally: saved examples and recorded live runs replayed." and "Synthetic voice (Kokoro-82M); labels, highlights and original music added in editing." |

| Time | Caption (narration) |
|---|---|
| 0:00.10–0:03.95 | One wrong number in an earnings story becomes a public correction. |
| 0:03.95–0:10.66 | This AI-drafted story has two planted errors. NoCap flags both, with the filed figures. |
| 0:10.66–0:15.96 | NoCap checks every financial number in a draft against SEC filings. |
| 0:15.96–0:20.99 | The draft says revenue grew 18% from a year ago. That's the quarterly rate. |
| 0:20.99–0:26.54 | The receipt does the math from the filings: 105.85%. |
| 0:27.04–0:33.55 | Fix with receipts: Muse Wonder proposes corrected numbers, and code lets only the digits change. |
| 0:33.55–0:37.89 | Then the whole draft is checked again: nine verified, zero wrong. |
| 0:38.74–0:45.27 | SAM 3.1 finds both bar charts, and Muse Spark reads 32 printed labels. |
| 0:45.57–0:54.89 | NoCap, for financial journalists and analysts. Built during this hackathon by Robert Chang. Try it live through November 17. |
