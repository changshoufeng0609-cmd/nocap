# NoCap 獨立審查（唯讀）

找到 8 個問題：兩個 P0 會把錯的數字判成 ✓，一個 P1 有條件下也會；另有前端草稿遺失、Fix 在暫停時必定失敗、benchmark 頁面呈現、`/tmp` 快取隱私等問題。我沒有執行任何測試，所有「預期結果」都是從程式碼推出來的，不是跑出來的。

**範圍**
- **讀過的文件：** `docs/HANDOFF.md`（State 區塊與 hand-back log 最後幾筆）、`AGENTS.md`、`.claude/reference/project.md`、`docs/remaining-work.md`、`docs/j2-review-2026-10-29.md`。
- **讀過的程式：** `receipts/` 下的 `verify`、`extract`（prompt、span 定位、bound 還原、快取）、`periods`、`check`、`stream`、`api`、`rewrite`、`spend`、`private`、`ask`、`ask_api`，以及 `slides` 的一部分。
- **讀過的前端：** 主要 web 元件、兩個 hook、benchmark 頁。
- **讀過的測試：** `test_verify.py`、`test_extract_j2_bounds.py`、`test_traps.py` 的一部分。
- **沒做的事：** 沒有執行測試、build、部署或任何外部呼叫；沒讀 `.env`、憑證或私人草稿；沒打開 held-out 檔案。
- **用詞：** 「靜態已證實」指我逐行追過程式路徑，不是實際執行的結果。

## 總覽

| # | 等級 | 類別 | 主要位置 | 證據 |
|---|---|---|---|---|
| 1 | P0 | false ✓，期間綁定 | `receipts/verify.py:1378-1392` | 靜態已證實；觸發條件就是 prompt 要求的最短 span |
| 2 | P0 | false ✓，bound 被忽略 | `receipts/verify.py:706-718`、`receipts/extract.py:1060` | 靜態已證實 |
| 3 | P1 | false ✓，YoY/QoQ 綁定 | `receipts/verify.py:1174-1175, 1312-1348` | 程式路徑已證實；要模型輸出 `comparison="none"` 才會觸發（假設） |
| 4 | P2 | false ✗，bound 誤讀 | `receipts/verify.py:88` | 靜態已證實；這種措辭多常見是假設 |
| 5 | P2 | 前端資料遺失 | `web/components/check-workspace.tsx:51`、`receipts-app.tsx` | 靜態已證實，沒在瀏覽器跑過 |
| 6 | P3 | demo 可信度 | `receipts-app.tsx:521-523`、`receipts/api.py:687-702` | 靜態已證實 |
| 7 | P3 | benchmark 可信度 | `web/app/benchmarks/page.tsx:60-106` | 靜態已證實 |
| 8 | P3 | 隱私 | `receipts/api.py:125-128, 663`、`receipts/stream.py:192-198` | 寫入路徑已證實；保留多久取決於平台（假設） |

---

## 1. [P0] 句子裡任何地方出現「a year earlier」或「previous quarter」，就會把錯期間的數字判成 ✓

**位置：** `receipts/verify.py:1187`（呼叫順序）、`receipts/verify.py:1378-1392`；regex 在 `receipts/verify.py:99-101`。

**路徑：**
- 一個金額型 claim，如果 `comparison == "none"` 而且當期值對不上，`_comparison_base` 會在**整段** `span_text` 裡找 `_YEAR_AGO_RE` 或 `_PRIOR_Q_RE`。
- 只要找到，就改去比對去年同季或上一季；對得上就回 `verified/match`。
- 它沒有檢查這個片語是不是在講這個數字。

**觸發（用 `tests/test_verify.py` 裡標為真實的數值）：**
- 草稿句子：`NVIDIA's revenue rose 106% from a year earlier to $46.7 billion in the second quarter of fiscal 2027.`
- $46.7B 其實是 Q2 FY2026 的營收，Q2 FY2027 是 $96.221B。
- prompt 要求 span 是「同時包含指標用詞和數值的最短子字串」（`receipts/extract.py:274`），這句的最短 span 一定包含 "from a year earlier"。
- 金額型數字的 comparison 自然是 `none`，期間是當期。
- 106% 夾在中間，所以 `_local_level_description` 回 None（`verify.py:474-475`），解讀只看 metric_text "revenue"，仍然成立。
- 當期對不上，於是比對去年 46.743B，結果是 ✓。
- 同樣的事會發生在 `rose 18% from the previous quarter to $81.6 billion`，或句中含 `sequentially` 的寫法。

**影響：**
- seeded 錯誤類型「wrong period」在通訊社常見句型下被判成 verified，計入 verified 數。
- 因為它不是 wrong，Fix 不會修它，Fix 摘要卻可能顯示「Extracted red flags resolved.」
- 這正是專案自己 trap reader 測試用的句型（`tests/test_traps.py:253`）。

**證據：**
- committed 的 eval 紀錄裡沒有任何結果走過這條路徑；"the comparison period the sentence cites" 只出現在原始碼裡。所以 dev 和 held-out 沒抓到，不代表不存在。
- 現有測試只涵蓋片語確實在講該數字本身的情況（`tests/test_verify.py:459-462`）。

**最小修正：**
- 只在比較片語確實屬於這個數字時才走這條路。例如：片語出現在數值之後、同一子句內、下一個數字之前（像 "up from $X a year ago"），或數值前面緊接 `from`、`compared with`、`versus`、`vs.`。
- 其他情況交給 `_diag_period`（`verify.py:1418-1436`），它會判成 `period_mismatch`。

**驗證（建議，未執行）：**
- 在 `test_verify.py` 加入上面兩句，預期結果是 `("wrong", "period_mismatch")`。
- 保留 `test_verify.py:459-462` 和 `:851-855` 這兩個既有測試。
- 跑 guarded dev（v0 + basis5、trap dev），false alarm 不能增加。

## 2. [P0] 寫成「US$」或「USD」時 bound 失效，J1 那個 false ✓ 又回來了

**位置：**
- `receipts/verify.py:706-718`（`hedge_of`）：第 710 行的 `lead` 會留下 "US"，第 714 行只剝掉空白、`$` 和符號。
- `receipts/verify.py:87`：hedge regex 用 `\s*$` 錨定在結尾。
- `receipts/extract.py:1060`：`_source_bound_span` 用同一個剝除規則。

**對照：**
- `extract.py:540`、`:567-568`，`rewrite.py:81`，`coverage.py:105` 都把 `US$` 和 `USD` 當成幣別前綴。
- 只有 bound 這段不認得，幣別用詞前後不一致。

**觸發：**
- 草稿句子：`NVIDIA Q2 FY2027 revenue was under US$96.2 billion.`
- schema 要求 value_text「with any attached currency sign」（`extract.py:132-133`），所以 value_text 會是 `US$96.2 billion`，解析成 96.2 billions USD。
- `hedge_of` 看到的文字結尾是 `...was under us`，錨定的 regex 對不上，等於沒有 hedge。
- 接著用 ±$0.05B 做等值比較，對上 96.221，判成 ✓。但「under $96.2B」其實是錯的。
- `more than US$4.5 billion` 對 $4.459B（J1 原本的案例）、`USD 4.5 billion` 都一樣。
- 如果 value_text 不含 "US"，會因為多出來的字變成 "?"。這樣安全，但等於沒檢查。

**影響：**
- J1 和 J2 視為最嚴重的 bound false ✓，在亞洲英文財經媒體常用的寫法下重現。
- J2 的 bound 測試只用 `$`（`tests/test_extract_j2_bounds.py:18-84`）。

**最小修正：**
- 抽出一個共用的幣別前綴 regex：`US\$|USD|US|\$`，不分大小寫，允許中間有空白。
- `hedge_of`、`_source_bound_span`、`verify.py:481` 先剝掉結尾的幣別，再找 hedge。
- `rewrite.py` 和 `coverage.py` 也改用同一個常數。

**驗證（建議，未執行）：**
- 把 `test_source_operators_are_authoritative_and_have_no_tolerance_slack` 參數化，加入 `US$96.2 billion`、`USD 96.2 billion`、`US$ 96.2 billion`。
- 每種寫法的判定結果都要和 `$` 版本一模一樣，而且 `hedge_of(...)` 不能是 None。

## 3. [P1] `comparison="none"` 會蓋掉句子裡明寫的 year over year 或 sequentially

**位置：**
- `receipts/verify.py:1174-1175` 進入 `_growth_unstated`（`1312-1348`），YoY 或 QoQ 任一對上就算 ✓。
- `_CHANGE_RE`（`115-118`）其實認得這些字，但只用來判斷「這是不是成長率」，沒拿來決定比較基準。

**觸發：**
- 一個 revenue_growth claim：數值 `18%`，`comparison="none"`，span 是 `Revenue rose 18% year over year`。
- QoQ 是 17.9%，對得上，所以判成 ✓。
- 但 YoY 實際是 105.9%，這正是 seeded 的「YoY 和 QoQ 對調」錯誤。

**證據：**
- 判定邏輯已證實。
- 要模型在句子寫明 year over year 時仍輸出 `none` 才會觸發，這是假設。prompt 規則 10 在「不清楚時」鼓勵用 `none`（`extract.py:278`）。
- 和 J2-2 是同一類問題：模型輸出的欄位值抹掉了原文的明確意思。

**最小修正：**
- 當 growth claim 的 comparison 是 `none` 時，在數值所在的子句裡找 YoY 或 QoQ 字詞。
- 只出現一種，就用那一種。
- 和模型給的欄位衝突時，回 "?"。

**驗證（建議，未執行）：**
- `pct("18%", 18, "revenue_growth", span="revenue rose 18% year over year")`，預期 `wrong/period_mismatch`。
- 把 span 換成 `revenue rose 106% sequentially`，不能判成 ✓。
- `test_verify.py:424-427` 仍要通過。

## 4. [P2] 「up to $X」一律當成上限，正確數字被判 ✗

**位置：**
- `receipts/verify.py:88` 把 `up\s+to` 列為含等號的上限，判定在 `bound_test`（`1094-1115`）和 `_bounded`（`1123-1153`）。
- `extract.py:1042-1066` 還會主動把 "up to" 補進只有數字的 span。
- J1 認定為具約束力的 bound 用詞清單（`docs/HANDOFF.md:66-71`）裡沒有 "up to"。

**觸發：**
- 草稿句子：`NVIDIA Q2 FY2027 revenue was up to $96.2 billion from $46.7 billion a year earlier.` 這裡的意思是「成長到」。
- 程式把它讀成「最多 $96.2B」，而 96.221 ≤ 96.2 不成立。
- 沒有可用的 non-GAAP 替代值，也不是銀行，所以判成 `wrong/value_mismatch`。

**影響：**
- 違反「ambiguity never yields ✗」，造成 false alarm。
- Fix 還會接受把它改成 `$96.221 billion`，等於「修正」了一個本來就合理四捨五入的數字。
- 這種措辭有多常見，是假設。

**最小修正：**
- 把 "up to" 從具約束力的 bound 清單拿掉，或在後面接 `from`、前面有成長類動詞時不當 bound。
- 至少讓沒滿足的 "up to" 回 "?"，而不是 ✗。

**驗證（建議，未執行）：** 把上面那句加進 `test_extract_j2_bounds.py`，結果不能是 `wrong`，Fix 也不能對它提出修改。

## 5. [P2] 切分頁或點範例會悄悄清掉使用者的草稿和花錢跑出來的 Fix 結果

**位置：**
- `web/components/check-workspace.tsx:51` 用條件渲染，切到其他分頁就 unmount 草稿頁。
- 草稿只存在 local state（`receipts-app.tsx:71`）。
- 重新 mount 時 `touched` 重設為 false（`:86`），範例載入後執行 `loadExample(list[0])` 把草稿蓋掉（`:165-183`）。
- `loadExample` 無條件呼叫 `setDraft`（`:129-139`）。
- 以下三個入口都會直接覆蓋草稿，沒有確認也沒有 undo：
  - 範例選單（`:400-408`）
  - 錯誤 toast 的「Run an example」（`:96-108`）
  - 錯誤 alert 裡的範例按鈕（`:420-441`）
- 整個前端沒有用到 sessionStorage 或 localStorage。

**觸發：**
- production 目前暫停 live 請求，使用者貼自己的草稿按 Check 一定會收到 paused 錯誤。
- UI 給的下一步正好是「Run an example」，一點原文就被換掉。
- 切到 Ask 再切回來，草稿也會變成 NVIDIA 範例。
- Fix 結果在以下三種情況會消失：按「Edit draft」（`:256-261`）、改 as-of（`:409-414`）、切分頁。但 Fix 面板寫著「Your original draft is unchanged」（`fix-with-receipts.tsx:136`），容易讓人以為修訂稿還在。
- Private 分頁在 unmount 時清掉內容是刻意設計，應該保留。

**最小修正：**
- 把 draft 和 asOf 提升到 `CheckWorkspace`，或四個分頁都保持 mount、用 `hidden` 切換。
- 草稿存到 sessionStorage。
- 草稿被使用者改過時，覆蓋前提供 Undo toast。

**驗證（建議，未執行）：** 用 Playwright 測三個流程：
- 打字、切到 Ask、再切回來，內容不變。
- 模擬 paused 錯誤後點「Run an example」，再按 Undo，草稿能還原。
- Fix 完成後按「Edit draft」，修訂稿有保留或有提示。

## 6. [P3] live 暫停時，內建範例仍然顯示一定會失敗的「Fix with receipts」

**位置：**
- `receipts-app.tsx:521-523`：只要有 wrong 就顯示 Fix 區塊。
- `check-workspace.tsx:19-26` 已經拿到 `livePaused`，但沒有往下傳。
- `/api/fix` 沒有重播路徑：先在 `api.py:687` 扣掉 rate limit 名額，到 `:699-702` 才因為 `live_paused` 拒絕。
- 回給使用者的訊息是「...The cached examples remain available.」（`api.py:211`）。

**影響：**
- 評審在 NVIDIA 範例（7 verified / 2 wrong / 2 unverifiable）上看到 Wonder 主功能的按鈕，點了必定失敗。
- 錯誤訊息還說範例可用，和 demo 影片裡成功的 Fix 對不上，會影響 Demo 和 Meta tooling 兩項評分。
- 預設每 600 秒 10 次的 rate limit 被扣完之後，錯誤訊息還會變成「Too many live checks」。

**最小修正：**
- 把 `livePaused` 傳給 Fix 元件，停用按鈕並連到 demo 或 `docs/demo/fix-live-2026-10-29.json`。
- 或者對完全相同的範例，重播已錄好的 Fix 結果，並明確標示是錄製的。
- 後端把 `require_live()` 移到 rate limit 之前；Check 的 `api.py:626` 和 `:633-634` 也一樣。

**驗證（建議，未執行）：**
- health 回報暫停時，前端不送出 POST /api/fix。
- API 測試：暫停時不消耗 rate limit 名額。

## 7. [P3] benchmark 頁最顯眼的數字是 post-hoc 分析的「100.0%」

**位置：**
- `web/app/benchmarks/page.tsx:60-91` 先放 post-run sensitivity 卡片，`:74` 用大字顯示 precision。
- `results.json` 裡 Spark 是 14/14、Spark Open 17/17、Glimmer 6/6，所以三張卡片都顯示 100.0%（`page.tsx:35`）；Wonder 是 N/A。
- 事先登記的結果是四個 backend 都 0/50 accepted，卻放在後面 `:94-106` 的普通表格裡。

**影響：**
- 頁面在 `:63` 有揭露，但快速瀏覽的評審會先看到三個 100.0%。
- `docs/submission/writeup.md:93` 寫得比頁面謹慎。
- 這是可信度風險，數據本身沒有錯。

**最小修正：**
- 把事先登記的表格移到前面，或在每張卡片的大數字旁加上「post-hoc」標記和「Pre-registered: 0/50 accepted」。
- precision 改顯示「14/14」，而不是百分比。

**驗證（建議，未執行）：** 在 1440 和 390 寬度檢查版面；確認兩個下載檔和 200 筆紀錄不變。

## 8. [P3] 公開 Check 會把草稿片段寫進 instance 的 `/tmp`，沒有過期清除，也沒告訴使用者

**位置：**
- Vercel 上的寫入目錄是 `/tmp/receipts`，見 `api.py:125-128`。
- `api.py:663` 把 `stats.events` 寫入快取，內容包含每個 claim 的 `span_text`（`stream.py:192-198`、`:309`）。
- Spark 回應快取存的是 `output_text`，裡面有草稿原文的引用（`extract.py:1211-1213, 1272, 1560`）。
- Fix 也會寫入同一個快取（`api.py:711`）。
- 兩種快取都沒有清除機制。
- UI 只有 Private 模式寫了「not saved in response caches」（`private-check.tsx:51`），公開模式沒有任何說明；只有工程文件 `docs/deploy.md:262-263` 提到。

**證據：**
- 寫入路徑已證實；資料會留多久取決於 Fluid Compute 怎麼重用 instance，這是假設。
- 目前 live 暫停，暴露為零；評審期間如果開放就會開始累積。
- 要讀到快取，必須送出完全相同的 text、as_of、context，所以跨使用者外洩的風險低。主要問題是資料保留和沒有揭露。
- 所有送給模型供應商的請求都設了 `store: False`。

**最小修正：**
- Vercel 上預設只讀 snapshot 快取、不寫入；或加上過期清除。
- 在 Draft UI 加一行說明資料怎麼處理。
- Fix 的預算預留已經涵蓋兩次 extraction，關掉寫入快取不會超出預估。

**驗證（建議，未執行）：**
- `Settings.from_env({"VERCEL": "1"})` 的寫入目錄應為 None。
- 用 stub client 跑一次 live check，`/tmp` 不應出現新檔。
- 內建範例仍能從 snapshot 重播。

---

## 補充

**看過、目前沒發現問題的部分（方便交叉比對）：**
- spend ledger 的交易處理和出錯時一律拒絕（`spend.py:150-211`）
- 每次模型呼叫前的預算檢查（`api.py:262-273`）
- Private 模式的 Modal 網域白名單，以及停用 redirect（`private.py:35-75`）
- Ask 的問題綁定偏保守（`ask.py:156-257`）
- ingest 用實際期間日期重算 fiscal 標籤（`ingest.py:275-312`）
- Fix 的完整重驗（`rewrite.py:261-282`）

**第 1 到 4 點修完後要注意：**
- 這幾點都會改 `verify.py`，它屬於 `RESULT_MODULES`，已錄製的 Check 結果會失效。
- 依 AGENTS.md 用 `--no-model` 重建 snapshot，確認三個範例仍是 7/2/2、5/3/0、6/3/0，而且 165 秒 demo 草稿的判定仍和影片一致。
- held-out 已凍結，不重跑，也不依它調整。投稿文字應註明 held-out 數字對應的是修正前的 `61e1055`。
