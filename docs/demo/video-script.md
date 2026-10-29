# Three-minute video script

**Format:** English narration, 1920 × 1080, captions, target 3:00 with a 180-second maximum. Author/presenter: Robert Chang, Team NoCap. The generated [review video](assets/receipts-demo.mp4) uses disclosed macOS Samantha synthetic narration. Its [SRT](assets/receipts-demo.en.srt) is the exact rendered narration and timing; this table also serves as a human recording guide.

Use the working app and actual receipts. The title card and metric card may be edited graphics. Keep `Cached example` or `Recorded response` visible where applicable, and use `Wait shortened` when cutting a model wait. The edited running time is not a latency measurement.

## Timed narration and actions

| Time | Narration | Screen and operator action |
|---|---|---|
| 0:00–0:15 | “A financial draft can sound convincing while comparing the wrong quarters or mixing accounting bases. I built NoCap after seeing unreliable numbers in an earlier portfolio-agent project. This is the verification layer I wanted before publishing.” | Title: **NoCap · Check the numbers. Keep the evidence.** Lower line: **Robert Chang · Team NoCap**. Cut to the populated draft editor. |
| 0:15–0:35 | “Here is an NVIDIA earnings story with two planted errors. Muse Spark extracts the numeric claims. Deterministic code checks them against stored SEC filing evidence. Most numbers pass, two need correction, and the outlook stays unverifiable.” | Select **NVIDIA Q2 FY2027 earnings story**, leave **Now**, click **Check**. Show `7 verified / 2 wrong / 2 unverifiable`. Keep **Cached example** visible. Hover the `18%` and `$2.22` flags. |
| 0:35–0:58 | “Eighteen percent is the quarter-over-quarter growth rate, but the draft says year over year. The receipt shows the comparison and the filing behind it. The EPS claim uses an adjusted number under a GAAP label: the filed GAAP figure is two dollars and forty-six cents.” | Open `18%`; hold on the explanation and growth calculation. Open `$2.22`; show claimed versus filed value, the source filing and accession. Do not crop away the reason or basis. |
| 0:58–1:25 | “Fix with receipts asks Muse Wonder to propose numeric substitutions. Code restricts those edits, checks each candidate, then extracts and verifies the whole revised draft again. Here both original errors are resolved. The two guidance figures still have question marks. I can review the changes and copy the draft with its correction receipts.” | Click **Fix with receipts**. Cut the wait only with a disclosure. Show original/revised draft and both correction receipts. Show `9 verified / 0 wrong / 2 unverifiable`, then **Copy with receipts**. Keep the original text visible. A recorded live response must be labeled **Recorded response**. |
| 1:25–1:49 | “The right number can still be unavailable at a historical cutoff. At sixteen hundred UTC on July twenty-ninth, Microsoft's filing had not reached EDGAR. NoCap separates those claims from numeric mistakes. Switching to the latest filings makes those two figures verifiable. That cutoff describes EDGAR availability, not every possible public channel.” | Choose **Edit draft**, then **June-quarter roundup, as of July 29** and **Check**. Open Microsoft's `$90.0 billion` claim: cutoff `2026-07-29 16:00 UTC`; first filing `2026-07-29 20:04:53 UTC`. Show `Not public yet`. Click **Now**, then **Check**; the two Microsoft claims become verified. Alphabet's misattributed EPS remains wrong. |
| 1:49–2:09 | “Same seeded figures, refreshed NoCap branding. This replays the original image response. SAM returned no region here, so Spark supplied the region and read the values. Code caught both planted errors against SEC evidence. The image itself is not proof.” | Upload the branding-refreshed [NVIDIA draft fixture](../../examples/slides/nvda-draft.png). Its numbers and layout are unchanged. Show the original input's recorded 2 verified / 2 wrong result, Spark fallback region and EPS receipt. The NoCap-branded image has not been sent to a model; its distinct hash and the original live-input hash are recorded in the manifest. This is a replay demonstration, not a fresh smoke result or general slide benchmark. |
| 2:09–2:31 | “The model proposes; code decides. Spark extracts claims and helps map earnings tables. Wonder proposes corrections and generated adversarial drafts. The evidence store covers ten companies across eight quarters. We also validated Muse Spark Open through Fireworks, using the same extraction and verification interface.” | Architecture card: **Spark → typed claims → code + SEC evidence → verdicts**; second row **Wonder → proposed edits → code guards → full recheck**. If the slide shot was used, add **SAM → regions → Spark vision**. Show **10 companies · 8 quarters** and **Fireworks: one small live benchmark**. |
| 2:31–2:51 | “On our seeded development set, NoCap caught one hundred and four of one hundred and nine planted errors, with no false alarms across one hundred and seven original items. Twenty percent of true claims remained unverifiable. These small development sets guided implementation; they are not a guarantee for arbitrary financial writing.” | Metric card: **Development only · 224 items**; **104/109 errors caught (95.4%)**; **0/107 original items with false alarms**; **51/250 true claims unverifiable (20.4%)**. Footer: **Held-out split not evaluated**. |
| 2:51–3:00 | “NoCap gives an editor numbers to review and evidence to inspect. Built during this hackathon by Team NoCap. Research and fact-checking, not investment advice.” | Return to a receipt, then end card. Add a preview URL only after it has been verified and its intended audience has access. Do not promise public access to a protected preview. |

The narration is approximately 390 words with the slide segment. Rehearse at a clear pace; preserve the numerical caveats and shorten transitions before accelerating speech. The generated video's captions match its synthetic narration; match any later human recording to a revised caption track.

## Slide-segment fallback: same 1:49–2:09 slot

Narration:

> “The boundaries matter too. NoCap currently covers ten companies and eight quarters. A question mark means the evidence or interpretation is insufficient. Guidance remains a forecast. The snapshot is not a live feed of every SEC filing, and a green number does not validate the story's wider conclusions.”

Screen: hold on the coverage list, then select NVIDIA's `$108.0 billion` guidance claim and its `forward_looking` explanation. Keep the rest of the timeline unchanged. If using this fallback, do not include SAM as a shipped product capability on the architecture card.

## Exact source material

Use the example picker to preserve the context and cutoff; do not retype or simplify the story during capture.

| Segment | Repository source | Context / cutoff |
|---|---|---|
| Check and Fix | [`examples/nvda_q2_fy2027.txt`](../../examples/nvda_q2_fy2027.txt) | `NVIDIA, second quarter of fiscal 2027`; latest filings |
| Time travel | [`examples/roundup_q2_2026.txt`](../../examples/roundup_q2_2026.txt) | `Microsoft, Alphabet and JPMorgan Chase, quarter ended June 30, 2026`; `2026-07-29T16:00:00Z` |
| Expected receipts | [`examples/README.md`](../../examples/README.md) | The API keeps `look_ahead` under its existing `wrong` status; the UI presents it separately. |
| Metrics | [`README.md`](../../README.md#results-development-sets-only) | Confirm against the latest completed dev run before exporting. |

For the recorded NVIDIA Fix smoke, growth was changed to `105.8511435%` and EPS to `$2.46`. The narration intentionally does not promise an exact output string from a future Wonder call. If the outcome differs, inspect it and update the shot; never replace an unsuccessful result with invented UI.
