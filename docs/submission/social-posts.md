# NoCap — Threads 與 LinkedIn 未發布草稿

## 發布前檢查（給作者，不貼出）

- [ ] 等 release 通過公開存取檢查後才發布；確認即時檢查的開放期間、預算設定與公開原始碼皆符合文案。所有 `<!-- verify-at-release -->` 都要在發布當下核對。
- [ ] 登出後，重新開啟本文的產品、示範影片與 GitHub 連結；本次只做離線核對，沒有驗證公開狀態。
- [ ] **GitHub repo 要到 11/4 最後一次補資料後才公開。** 網站 11/1 已上線，在那之前發文的話，刪掉 Threads 回覆四的「原始碼」那一行和 LinkedIn 的「Source:」那一行，否則讀者會看到 404；不然就等 repo 公開後再發。刪行後字數記得重算。
- [ ] Threads 建議附 `docs/demo/assets/nocap-x.mp4`（56 秒），SHA-256：`3b03ca6cb3e7d37936f0710139d1a68f72039be32381b0e48b1725de5dffa714`。LinkedIn 建議附 `docs/demo/assets/receipts-demo.mp4`（2:52），SHA-256：`9d5bb223148b3cc80d19ed5985433d8c501cd855212ff2bb8667825d91274897`。本機檔案雜湊已核對；來源：`docs/demo/README.md:3`、`:86`、`:89`。
- [ ] 先完整看過、聽過所選影片；兩版都是燒錄式英文字幕、英文合成旁白，保留字幕與重播揭露。下方中文影片說明不是中文字幕。來源：`docs/demo/README.md:73`、`:88`、`docs/demo/video-script.md:107-110`。
- [ ] **尚未發布、排程或送出任何貼文。** 由 Robert Chang 決定是否發布、使用哪個帳號、是否加回覆串及附哪支影片；有改字或加標註就重新計數。

以下僅複製 `text` 區塊；標題、註解與作者備註不貼出。來源行號以 2026-11-01 晚間在 README 結果表加入 500 家抽查列之後的版本為準（之前以 `c84fe01` 為準）。字數計入換行、空格及完整網址，不含區塊末尾的換行；Threads 按 Unicode code point 計算，每個 emoji 計 2。本文未用 emoji；✓、✗ 與 ? 是判定符號，各計 1。Threads 每篇控制在 450 字內，低於 Threads 規定的 500 字上限。

## Threads 主文

<!-- Length: 248 characters (Threads counting rule above). Stakes: docs/submission/writeup.md:19. Hackathon: README.md:7. Checks and receipts for every ✓ or ✗: README.md:3, :137. Unsupported evidence returns ?: README.md:134, :142. All 500 S&P 500 companies, mainly the latest eight quarters: README.md:20, :141; docs/submission/writeup.md:23. Muse Spark, Muse Wonder and SAM 3.1: README.md:30-36. Product link: README.md:17. -->
<!-- verify-at-release -->

```text
財報新聞寫錯一個數字，就得公開更正。

我做了 NoCap，參加 Meta Global AI Developer Hackathon：把財報新聞草稿裡的數字，逐一對照 SEC 申報文件。每個 ✓ 或 ✗ 都附原始文件憑據，可以點開看依據；證據不足就標 ?。

目前涵蓋全部 500 家 S&P 500 公司，資料以最近八季為主。用 Meta 的 Muse Spark、Muse Wonder 和 SAM 3.1 打造。

試用：https://receipts-nocap.vercel.app
```

作者備註：Threads 每篇最多選一個主題標籤，建議主文與下列回覆都選「AI」，由作者決定；不要把這則備註貼進內文。以下四則是可選的連續回覆，依序接在主文下方。

### 回覆一：誰負責判定

<!-- Length: 229 characters (Threads counting rule above). Model proposes, code decides: README.md:26. Muse Spark 1.3 extraction and slide readings: README.md:30, :32. GAAP/adjusted EPS and growth-period checks: README.md:5, :127. Muse Wonder 1.0 corrections, full recheck and trap drafts: README.md:36-37. SAM 3.1 regions are not evidence: README.md:35. Uncertainty: README.md:51. -->

```text
NoCap 的原則是「模型提案，程式判定」。

Muse Spark 1.3 擷取草稿裡的數字、期間與會計基礎，也讀取圖表標示；程式再對照申報資料，檢查有沒有把季增寫成年增、把調整後 EPS 當成 GAAP EPS。

Muse Wonder 1.0 提議修正數字，程式驗證後還要重查整篇草稿。它也負責寫測試用的陷阱稿，答案由程式核對。

SAM 3.1 找出簡報裡的圖表區域，交給 Muse Spark 1.3 讀數字。框出圖表不等於證據；有疑義就留 ?。
```

### 回覆二：資料怎麼來

<!-- Length: 271 characters (Threads counting rule above). Oct 31, one day, latest four releases of 467 companies, 1,969 calls/$42.97 and code validation: README.md:20, :31. Snapshot rather than live EDGAR feed: README.md:141. Whole Meta 10-K in one Muse Spark request/101,135 input tokens: README.md:71. SAM 3.1 found both real BAC charts: README.md:70. Single demonstrations, not accuracy measurements: README.md:74. -->

```text
查核使用預先匯入的申報文件快照，EDGAR 新公告不會即時同步。

10 月 31 日，Muse Spark 1.3 在一天內對應了 467 家公司各自最新四份財報新聞稿的表格欄位：1,969 次呼叫、42.97 美元。表格數值要通過程式自行解析與資料比對，才會納入查核依據。

另外，整份 Meta 10-K 曾用一次 Muse Spark 1.3 請求讀完，輸入 101,135 tokens；SAM 3.1 也在 Bank of America 的真實 SEC 簡報上找到兩張長條圖。

這些實際執行都有紀錄，但單次展示不是準確率測量。
```

### 回覆三：測到什麼，還不知道什麼

<!-- Length: 311 characters (Threads counting rule above). Two spot checks, each run once on fresh items (v1 Nov 1, v2 Nov 2): 54/70 caught, 1 marked ✓, 4/147 true figures wrong: README.md:65-66, :73; eval/spot500_methods.md (both Results sections); evidence-map.md (two-check row). 44/48 planted errors: README.md:61. 1/47 unmodified sentences: README.md:62. 20/20 trap-draft errors: README.md:64. 22/110 true figures (20%): README.md:63. Frozen held-out source, run once Oct 29, no rerun after later fixes, small correlated sets and provisional machine-assisted labels without independent human review: README.md:72. -->

```text
測試結果也把限制一起攤開。

凍結後的保留測試集，只在 10 月 29 日跑過一次：抓到 44/48 個刻意植入的錯誤；47 句未改動原文中，誤報 1 句。陷阱稿則抓到 20/20 個植入錯誤。

代價是保守：110 個真實數字中，22 個（20%）仍被留為 ?。

11 月 1、2 日另外用上線的 500 家版本做了兩次凍結抽查，每次都是全新的題目、只跑一次：70 個植入錯誤抓到 54 個，只有 1 個被判成 ✓；147 個真實數字只有 4 個被判錯。

這些都是小型、樣本彼此相關的植入錯誤測試，不是一般準確率，也不能代表全部公司的表現。標記由機器輔助產生，尚未經獨立人工審查；後續程式修正後，沒有重跑這兩組測試。
```

### 回覆四：怎麼試

<!-- Length: 311 characters (Threads counting rule above). Live through 2026-11-17 16:00 UTC = 2026-11-18 00:00 Taiwan (UTC+8): README.md:17. $5 per UTC day and pause until next UTC day: README.md:18. Saved examples and recorded samples replay at $0: README.md:19, :22. Suggested trial: README.md:19. Product/demo/source links: README.md:17, :13, :21. -->
<!-- verify-at-release -->

```text
可以先開儲存範例，點數字看憑據；再改一個數字，或貼上自己的財報新聞段落試試。

即時檢查開放到 2026 年 11 月 18 日 00:00（台灣時間），共用每日 5 美元額度，以 UTC 日計。額度用完會暫停到下一個 UTC 日；儲存範例與錄製樣本仍可隨時以 0 美元重播。

試用：https://receipts-nocap.vercel.app
影片：https://receipts-nocap.vercel.app/demo
原始碼：https://github.com/changshoufeng0609-cmd/nocap

如果你寫財報新聞、做分析或開發工具，想聽聽哪些數字最難核對、哪些憑據還不夠清楚。
```

### 影片說明：56 秒短版

作者備註：附 `docs/demo/assets/nocap-x.mp4` 時使用。以下忠實翻譯 X-cut 的英文媒體說明，並補充字幕與旁白語言；影片描述中的「兩個植入錯誤」是畫面內容，不是另一組測試結果。

<!-- Length: 202 characters (same code-point rule). Faithful source including two planted errors and both charts: docs/submission/x-posts.md:102. SAM 3.1/both charts: README.md:70. All 500 companies: README.md:20, :141. 56-second asset: docs/demo/README.md:3, :89. English synthetic narration/burned-in English captions: docs/demo/README.md:73, :88; docs/demo/video-script.md:107-110. -->

```text
NoCap 在 AI 撰寫的 NVIDIA 財報新聞草稿中，標出兩個刻意植入的錯誤，展示 SEC 文件憑據，重新驗證數字修正，並用 SAM 3.1 找出 Bank of America 真實 SEC 簡報上的兩張長條圖。片尾說明 NoCap 現已涵蓋全部 500 家 S&P 500 公司。畫面為真實介面，旁白為合成語音；影片已揭露使用快取的模型擷取結果與先前錄下的即時執行。附燒錄式英文字幕及英文旁白。
```

### 影片說明：2:52 完整版

作者備註：若改附 `docs/demo/assets/receipts-demo.mp4`，使用這段說明。

<!-- Length: 219 characters (same code-point rule). Faithful full-video source: docs/submission/x-posts.md:101. SAM 3.1 and whole Meta 10-K: README.md:70-71. All 500 companies: README.md:20, :141. 2:52 duration: README.md:13; docs/demo/README.md:3. English synthetic narration/burned-in English captions: docs/demo/README.md:73, :88. -->

```text
NoCap 檢查 AI 撰寫的 NVIDIA 財報新聞草稿，開啟 SEC 文件憑據，重新驗證數字修正，套用歷史時間截點，搭配 SAM 3.1 讀取 Bank of America 的真實 SEC 簡報，並針對 Meta 整份 10-K 提問。片尾說明 NoCap 現已涵蓋全部 500 家 S&P 500 公司。畫面為真實介面，搭配拉近鏡頭與合成語音旁白；影片已揭露使用快取的模型擷取結果與先前錄下的即時執行。附燒錄式英文字幕及英文旁白。
```

## LinkedIn

作者備註：以獨立開發者 Robert Chang 的第一人稱發文。開頭兩行共 139 字；全文低於 3,000 字。若要標註頁面，可考慮 Meta、Cerebral Valley，但作者必須先確認正確頁面與標註對象。本次未查證任何官方帳號或活動標籤，因此草稿不含活動 hashtag 或 handle。

<!-- Length: 2535 characters as typed (recounted Nov 2 for the two spot checks), first two lines unchanged. Two spot checks, 54/70 caught, 1 marked ✓, 4/147 true figures wrong: README.md:65-66, :73; eval/spot500_methods.md (both Results sections). Earlier note, characters as typed, including line breaks and full URLs; first two lines: 139. Solo builder Robert Chang: docs/submission/README.md:3. Stakes: docs/submission/writeup.md:19. Hackathon/from Oct 27: README.md:7, :152. Receipts/uncertainty: README.md:3, :133, :136. Model rule and versions/roles: README.md:26, :30-37. All 500/latest eight quarters/snapshot: README.md:20, :140. Oct 31/one day/467/latest four/1,969/$42.97: README.md:31. One whole-Meta-10-K request/101,135 input tokens: README.md:70. SAM both real BAC charts: README.md:69; demonstration limit: README.md:73. 44/48, 1/47, 22/110 (20%), 20/20: README.md:61-64; frozen/run once Oct 29/limitations: README.md:72. Window, $5 per UTC day, $0 replays: README.md:17-19, :22. Product/demo/public source links: README.md:17, :13, :21. -->
<!-- verify-at-release -->

```text
One wrong number in an earnings story means a public correction.
I built NoCap to check the numbers against SEC filings before publication.

I'm Robert Chang, the solo builder. I started on October 27 during the Meta Global AI Developer Hackathon.

Paste an earnings draft: every ✓ or ✗ comes with a receipt from the filing. Open it to inspect the filed value, source and arithmetic. Unsupported or ambiguous claims stay ?.

The rule: the model proposes, code decides.

Muse Spark 1.3 extracts claims, maps earnings tables and reads chart labels. Code checks values, periods and accounting basis. Muse Wonder 1.0 proposes numeric corrections; code verifies each edit and rechecks the draft. Wonder also writes trap drafts; code derives their labels. SAM 3.1 locates chart regions for Spark to read; regions are never evidence.

Coverage is all 500 S&P 500 companies, mainly the latest eight quarters, from a stored snapshot. On October 31, Spark mapped the latest four earnings releases of 467 companies in one day: 1,969 calls, $42.97. Code kept cells only when its own parse matched, subject to further evidence checks.

In recorded demonstrations, Spark read Meta's whole 10-K in one request (101,135 input tokens), and SAM found both bar charts on a real Bank of America SEC slide. These are demonstrations, not accuracy measurements.

The held-out results:
• 44/48 planted errors caught.
• 1/47 unmodified sentences falsely flagged.
• 20/20 trap-draft errors caught.
• 22/110 true figures (20%) left ?.

Two one-shot spot checks of the deployed 500-company versions on fresh items (November 1 and 2):
• 54/70 planted errors caught; only 1 marked ✓.
• Only 4/147 true figures flagged wrong.

These are small, correlated seeded tests, each frozen and run once: the held-out set on October 29, before the coverage expansion, and the spot checks on November 1 and 2. Labels are machine-assisted and await independent human review. They are not general accuracy estimates; later verifier fixes have not been tested on either set.

Live checks are open through 2026-11-17 16:00 UTC under a shared $5 per UTC-day cap. Saved examples and recorded samples always replay at $0.

Try it: https://receipts-nocap.vercel.app
Walkthrough: https://receipts-nocap.vercel.app/demo
Source: https://github.com/changshoufeng0609-cmd/nocap

If you write or edit earnings coverage, I'd welcome feedback: which figures are hardest to check, and what would a receipt need to show before you'd rely on it in an edit?

#AI #FinancialJournalism #FactChecking
```

### LinkedIn video description

作者備註：建議附 **2:52 完整版** `docs/demo/assets/receipts-demo.mp4`。它能讓寫稿、編稿的人看清楚憑據、修正後重查、歷史截點與整份申報文件提問的流程；56 秒版適合快速預覽，但沒有完整展示這些步驟。選片後使用相符的說明，保留英文字幕與重播揭露。內容依據：`docs/submission/x-posts.md:101-102`。

<!-- Length: 469 characters as typed. Main-video actions/disclosures: docs/submission/x-posts.md:101. 2:52: README.md:13. SAM 3.1/whole Meta 10-K: README.md:70-71. All 500 companies: README.md:20, :141. English synthetic narration and burned-in English captions: docs/demo/README.md:73, :88. -->

```text
A 2:52 walkthrough of NoCap checking an AI-drafted NVIDIA earnings story: opening SEC evidence receipts, rechecking a numeric correction, applying a historical cutoff, reading a real Bank of America SEC slide with SAM 3.1 and asking questions of Meta's whole 10-K. The end card says NoCap now covers all 500 S&P 500 companies. Real interface with zooms, burned-in English captions and synthetic English narration. Cached extraction and recorded live runs are disclosed.
```

作者備註：若選 56 秒短版，改用下段，避免描述短版未出現的流程。

<!-- Length: 421 characters as typed. 56 seconds: docs/demo/README.md:3, :89. Two planted errors, both charts, SAM 3.1, all 500 companies and disclosures: docs/submission/x-posts.md:102; README.md:20, :70, :141. English audio/captions: docs/demo/README.md:73, :88; docs/demo/video-script.md:107-110. -->

```text
A 56-second cut of NoCap flagging two planted errors in an AI-drafted NVIDIA earnings story, showing the SEC receipt, rechecking a numeric correction and finding both bar charts on a real Bank of America SEC slide with SAM 3.1. The end card says NoCap now covers all 500 S&P 500 companies. Real interface, burned-in English captions and synthetic English narration. Cached extraction and recorded live runs are disclosed.
```
