# AI-draft benchmark: batch `dev`

Drafts: 50 written of 50 planned, 50 checked (ok 50). Styles: analyst_note 13, bullet_recap 13, chatgpt_markdown 12, news_story 12.

> The drafts are written from the same release sentences the release-statement matcher reads, so a release-statement ✓ is partly circular; a filed-fact ✓ (10-Q/10-K XBRL, or the release's own table) is not.

## Totals

| | claims |
|---|---:|
| ✓ verified | 413 |
| &nbsp;&nbsp;filed fact (`match`) | 279 |
| &nbsp;&nbsp;&nbsp;&nbsp;of which 10-Q/10-K | 188 |
| &nbsp;&nbsp;&nbsp;&nbsp;of which the release's own table | 91 |
| &nbsp;&nbsp;release statement | 134 |
| ✗ wrong | 13 |
| ◷ not public | 0 |
| ? unverifiable | 749 |
| ≈ approximate | 0 |
| **all claims** | **1175** |

| style | claims | ✓ filed | ✓ release | ✗ | ? | ≈ |
|---|---:|---:|---:|---:|---:|---:|
| analyst_note | 299 | 79 | 40 | 7 | 173 | 0 |
| bullet_recap | 306 | 85 | 50 | 4 | 167 | 0 |
| chatgpt_markdown | 284 | 80 | 30 | 1 | 173 | 0 |
| news_story | 286 | 35 | 14 | 1 | 236 | 0 |

## Writer provenance (code-checked)

1174 figures: 1159 source_match, 2 rounded, 13 not_in_source (the writer's own errors, kept).

## Coverage

Of 1161 figures the release states (source_match or rounded), **411 got ✓ (35.4%)**; filed-fact ✓ alone: 277 (23.9%). ✗ on them: **13** (should be near 0).

Outcomes: ? 727, ✓ filed fact 277, ✓ release statement 134, ✗ 13, not extracted 10

## Each ✗, crossed with provenance

- writer error caught (figure not in source): **0**
- possible NoCap false alarm (figure is in the source): **13**
- ✗ on no scanned figure: 0

### Possible NoCap false alarms

1. **ACN** (analyst_note) `$2.03 billion` [net_income: net income] value_mismatch, provenance source_match
   - draft: GAAP net income climbed to $2.03 billion in the fourth quarter, with gross margin edging up to 32.0%.
   - NoCap: Accenture's 10-K reports that GAAP net income was $1.991 billion for Q4 FY2026 (quarter ended Aug 31, 2026), not $2.03 billion.
   - source: [s8] GAAP net income for the quarter was $2.03 billion, compared with $1.45 billion for the fourth quarter of fiscal 2025.
2. **ACN** (analyst_note) `$1.45 billion` [net_income: GAAP net income] value_mismatch, provenance source_match
   - draft: The GAAP effective tax rate slipped to 27.3%, compared with 30.1% in the prior-year quarter, as GAAP net income grew from $1.45 billion.
   - NoCap: Accenture plc's 10-K reports that GAAP net income was $1.684 billion for Q4 FY2024 (quarter ended Aug 31, 2024), the comparison period the sentence implies, not $1.45 billion.
   - source: [s8] GAAP net income for the quarter was $2.03 billion, compared with $1.45 billion for the fourth quarter of fiscal 2025.
3. **ACN** (analyst_note) `$8.52 billion` [net_income: GAAP net income] value_mismatch, provenance source_match
   - draft: For the full year, GAAP net income increased to $8.52 billion, compared with $7.83 billion in fiscal 2025.
   - NoCap: Accenture plc's 10-K reports that GAAP net income was $8.367 billion for fiscal 2026 (year ended Aug 31, 2026), not $8.52 billion.
   - source: [s16] GAAP net income for fiscal 2026 was $8.52 billion, compared with $7.83 billion in fiscal 2025.
4. **ACN** (analyst_note) `$7.83 billion` [net_income: GAAP net income] value_mismatch, provenance source_match
   - draft: For the full year, GAAP net income increased to $8.52 billion, compared with $7.83 billion in fiscal 2025.
   - NoCap: Accenture plc's 10-K reports that GAAP net income was $7.678 billion for fiscal 2025 (year ended Aug 31, 2025), not $7.83 billion.
   - source: [s16] GAAP net income for fiscal 2026 was $8.52 billion, compared with $7.83 billion in fiscal 2025.
5. **AME** (analyst_note) `22.7%` [other: operating margins] value_mismatch, provenance source_match
   - draft: Electromechanical Group sales grew to $628.9 million, up 15%, as operating income jumped to $142.5 million and operating margins reached 22.7%, supporting broad-based profitability across the portfolio.
   - NoCap: Ametek Inc's 10-K (derived) reports that operating margin was 25.29% for Q4 2025 (quarter ended Dec 31, 2025), not 22.7%.
   - source: [s20] EMG’s fourth quarter operating income increased 28% to $142.5 million, and operating income margins were 22.7% in the quarter, up 240 basis points versus the prior year’s results.
6. **BF-B** (chatgpt_markdown) `$1.0 billion` [operating_cash_flow: Cash flows from operations] value_mismatch, provenance source_match
   - draft: - Cash flows from operations grew to **$1.0 billion**, and free cash flow rose to **$893 million**.
   - NoCap: Brown Forman Corp's 10-K (derived) reports that operating cash flow was $291 million for Q4 FY2026 (quarter ended Apr 30, 2026), not $1.0 billion.
   - source: [s6] Reported operating income decreased 10% to $1.0 billion (-2% on an organic basis) and diluted earnings per share decreased 17% to $1.53.
   - source: [s10] Cash flows from operations grew by $402 million to $1.0 billion and free cash flow2 increased by $462 million to $893 million.
7. **COP** (news_story) `$1.56 per share` [other: earnings per share] period_mismatch, provenance source_match
   - draft: ConocoPhillips reported second-quarter 2026 earnings of $3.9 billion, or $3.23 per share, compared with $2.0 billion, or $1.56 per share in the same period a year earlier.
   - NoCap: $1.56 per share is ConocoPhillips' diluted EPS for Q2 2025 (quarter ended Jun 30, 2025); for Q2 2026 (quarter ended Jun 30, 2026) it was $3.23.
   - source: [s6] HOUSTON—Aug. 6, 2026—ConocoPhillips (NYSE: COP) today reported second-quarter 2026 earnings of $3.9 billion, or $3.23 per share, compared with second-quarter 2025 earnings of $2.0 billion, or $1.56 per share.
8. **DE** (bullet_recap) `$10.999 billion` [revenue: Net sales] value_mismatch, provenance source_match
   - draft: - Net sales: $10.999 billion, versus $10.357 billion a year earlier
   - NoCap: Deere & Company's 10-Q reports that revenue was $12.608 billion for Q3 FY2026 (quarter ended Aug 2, 2026), not $10.999 billion.
   - source: [s5] Net sales were $10.999 billion for the quarter and $30.779 billion for nine months, compared with $10.357 billion and $28.338 billion last year, respectively.
9. **DE** (bullet_recap) `$10.357 billion` [revenue: Net sales] value_mismatch, provenance source_match
   - draft: - Net sales: $10.999 billion, versus $10.357 billion a year earlier
   - NoCap: Deere & Company's 10-Q reports that revenue was $12.018 billion for Q3 FY2025 (quarter ended Jul 27, 2025), not $10.357 billion.
   - source: [s5] Net sales were $10.999 billion for the quarter and $30.779 billion for nine months, compared with $10.357 billion and $28.338 billion last year, respectively.
10. **GILD** (analyst_note) `$506 million` [segment_revenue: Trodelvy sales] value_mismatch, provenance source_match
   - draft: Trodelvy sales surged to $506 million on higher demand, partly offsetting weakness elsewhere.
   - NoCap: Gilead Sciences, Inc.'s 8-K press release reports that Trodelvy revenue was $336 million for Q3 2026 (quarter ended Sep 30, 2026), not $506 million.
   - source: [s23] Trodelvy® (sacituzumab govitecan-hziy) sales increased 42% to $506 million in the third quarter 2026 compared to the same period in 2025, primarily driven by higher demand.
11. **L** (bullet_recap) `$391 million` [net_income: Net income] period_mismatch, provenance source_match
   - draft: - Net income: $444 million, or $2.16 per share, up from $391 million, or $1.87 per share a year earlier
   - NoCap: $391 million is Loews Corporation's net income for Q2 2025 (quarter ended Jun 30, 2025); for Q2 2026 (quarter ended Jun 30, 2026) it was $444 million.
   - source: [s5] Loews Corporation reported net income of $444 million, or $2.16 per share, in the second quarter of 2026, compared to $391 million, or $1.87 per share, in the second quarter of 2025.
12. **SYF** (bullet_recap) `$2.86` [eps_diluted: Diluted earnings per share] period_mismatch, provenance source_match
   - draft: - Diluted earnings per share: $2.68, versus $2.86 a year earlier
   - NoCap: $2.86 is Synchrony Financial's diluted EPS for Q3 2025 (quarter ended Sep 30, 2025); for Q3 2026 (quarter ended Sep 30, 2026) it was $2.68.
   - source: [s2] STAMFORD, Conn - Synchrony Financial (NYSE: SYF) today announced third quarter 2026 net earnings of $892 million, or $2.68 per diluted share, compared to $1.1 billion, or $2.86 per diluted share in the third quarter 2025.
13. **WY** (analyst_note) `11 cents` [eps_diluted: per diluted share] value_mismatch, provenance source_match
   - draft: Net earnings came in at $61 million, or $0.08 per diluted share, compared with $80 million, or 11 cents per diluted share, a year earlier and $162 million in second quarter 2026.
   - NoCap: Weyerhaeuser's 10-Q reports that diluted EPS was $0.11 for Q3 2025 (quarter ended Sep 30, 2025), not 11 cents.
   - source: [s6] This compares with net earnings of $80 million, or 11 cents per diluted share, on net sales of $1.7 billion for the same period last year and net earnings of $162 million for second quarter 2026.

## Every ?, by cause

| cause | ? | share | drafts | top words | top metrics |
|---|---:|---:|---:|---|---|
| Guidance | 171 | 22.8% | 35 | revenue (6); billings (4); organic sales growth (4); effective tax rate (3); revenue growth (3) | other (74), eps_diluted (42), revenue_growth (21), revenue (18) |
| No company attached | 0 | 0.0% | 0 |  |  |
| Company name not matched | 11 | 1.5% | 1 | T. Rowe Price Group, Inc. (11) | other (4), eps_diluted (2), revenue_growth (2), operating_expenses (2) |
| Evidence ends before the quarter | 13 | 1.7% | 2 | Q2 2026 (10); Q1 FY2027 (3) | revenue_growth (5), revenue (3), eps_diluted (3), net_income (2) |
| Constant currency or organic (company-defined measure) | 19 | 2.5% | 7 | organic (14); constant-currency (5) | revenue_growth (15), other (3), eps_diluted (1) |
| Non-GAAP with no release fact | 27 | 3.6% | 14 | net income (5); free cash flow (5); operating income growth (4); operating income (4); diluted EPS (4) | operating_income (7), other (6), net_income (5), free_cash_flow (5) |
| Qualifier wording | 60 | 8.0% | 27 | core (5); taxes (4); U.S. (3); reaching (3); results (3) | revenue_growth (15), other (14), eps_diluted (8), revenue (5) |
| Untracked kind of figure | 312 | 41.7% | 48 | Adjusted EBITDA (5); share repurchases (4); Share repurchases (4); selling, general and administrative expenses (3); returned to shareholders (3) | other (244), capital_returned (26), operating_cash_flow (7), eps_diluted (7) |
| Period ambiguous or unresolved | 80 | 10.7% | 15 | net income (6); per diluted share (6); Net income (4); diluted earnings per share (3); net cash provided by operating activities (3) | net_income (16), eps_diluted (15), other (14), revenue_growth (11) |
| Other | 56 | 7.5% | 22 | per diluted share (2); Capital returned to shareholders (2); Loews Hotels net income (2); gross margin (1); net income (1) | revenue_growth (22), segment_revenue (15), net_income (6), operating_income (3) |

### Guidance (171)

- **AMAT** `$0.01` [other: known charges related to completed acquisitions]: This is guidance about a future period, so no filing can confirm it yet.
  - draft: The non-GAAP diluted EPS outlook excludes known charges related to completed acquisitions of $0.01 per share, includes the normalized tax benefit of share-based compensation of $0.01 per share and includes a net income tax benefit related to intra-entity inta…
- **AME** `$7.87` [eps_diluted: adjusted earnings per diluted share]: This is guidance about a future period, so no filing can confirm it yet.
  - draft: Outlook: For 2026, sales are expected to be up mid to high single digits compared with 2025, with adjusted earnings per diluted share guided to a range of $7.87 to $8.07.
- **AOS** `$500 million` [other: total value including the net present value of licensing economics]: This is guidance about a future period, so no filing can confirm it yet.
  - draft: The transaction values the China business at approximately $400 million on a cash-free, debt-free basis, with total value expected to exceed $500 million including the net present value of licensing economics, and the structure will enable the company to repa…

### Company name not matched (11)

- **TROW** `$2.79` [eps_diluted: diluted earnings per common share]: "T. Rowe Price Group, Inc." contains the name of T. Rowe Price, which NoCap covers, but isn't a name NoCap matches to it (it may be a unit, a subsidiary or another company), so NoCap doesn't check it against T. Rowe Price's filings.
  - draft: reported third-quarter diluted earnings per common share of $2.79, with adjusted diluted earnings per common share of $2.86, as net revenues reached $2.0 billion.

### Evidence ends before the quarter (13)

- **CMI** `$8.5 billion` [revenue: fourth-quarter revenue]: NoCap's evidence for Cummins Inc. ends with Q2 2026, so it can't check Q4 2026 yet; $8.5 billion matches its revenue for Q4 2025 (quarter ended Dec 31, 2025).
  - draft: reported fourth-quarter revenue of $8.5 billion, up 1% from a year earlier, while net income attributable to the company climbed to $593 million, or $4.27 per diluted share, from $418 million, or $3.02 per diluted share.
- **PG** `$23.3 billion` [revenue: net sales]: NoCap's evidence for Procter & Gamble ends with Q1 FY2027, so it can't check fiscal 2027 yet.
  - draft: Procter & Gamble opened fiscal 2027 with net sales climbing to **$23.3 billion**, up **4%** from a year earlier.

### Constant currency or organic (company-defined measure) (19)

- **ILMN** `4.7%` [revenue_growth: ROW organic revenue growth]: The wording “organic” describes a company-defined measure that filings don't report under that name, so NoCap can't check 4.7%.
  - draft: Revenue excluding tariff refunds was $1.17 billion, up 4.7% after also excluding the impacts of currency, acquisitions, and the Greater China region, a measure described as ROW organic revenue growth.
- **NKE** `5 percent` [revenue_growth: revenues]: 5 percent is a constant-currency (FX-neutral) figure; filings report results at actual exchange rates, so NoCap can't check it.
  - draft: First-quarter revenues were $11.2 billion, down 4 percent on a reported basis and down 5 percent on a currency-neutral basis.
- **PEP** `3.1%` [revenue_growth: organic revenue increased]: The wording “organic” describes a company-defined measure that filings don't report under that name, so NoCap can't check 3.1%.
  - draft: - Net revenue rose **5.6%** in the third quarter, while organic revenue increased **3.1%**.

### Non-GAAP with no release fact (27)

- **ACN** `$8.77 billion` [net_income: Adjusted net income]: No press-release fact for Accenture plc's non-GAAP net income for fiscal 2026 (year ended Aug 31, 2026) (non-GAAP figures come only from earnings releases).
  - draft: Adjusted net income rose to $8.77 billion, compared with $8.32 billion, while fiscal selling, general and administrative expenses were $12.06 billion, or 16.3% of revenues.
- **AFL** `6.2%` [operating_income: pretax adjusted earnings]: No press-release fact for Aflac Inc's non-GAAP operating income growth for Q2 2026 (quarter ended Jun 30, 2026) (non-GAAP figures come only from earnings releases).
  - draft: - In Japan, total adjusted revenues in dollar terms dropped **12.6%** to **$2.2 billion**, and pretax adjusted earnings slipped **6.2%** to **$741 million**.
- **AME** `$413.7 million` [operating_income: adjusted operating income]: No press-release fact for Ametek Inc's non-GAAP Electronic Instruments Group operating income for Q4 2025 (quarter ended Dec 31, 2025) (non-GAAP figures come only from earnings releases).
  - draft: Electronic Instruments Group sales increased to a record $1.37 billion, up 13%, with adjusted operating income of $413.7 million.

### Qualifier wording (60)

- **AFL** `$0.05` [other: impact on adjusted earnings per share]: The wording “impact” qualifies this figure (impact on adjusted earnings per share) in a way NoCap can't match to a filing fact, so there is nothing to check it against.
  - draft: - The weaker yen/dollar exchange rate had a negative **$0.05** impact on adjusted earnings per share.
- **AME** `$584.3 million` [operating_cash_flow: operating cash flow]: The wording “hitting” qualifies this figure (operating cash flow) in a way NoCap can't match to a filing fact, so there is nothing to check it against.
  - draft: Cash generation was a standout, with operating cash flow hitting a record $584.3 million and free cash flow reaching a record $527.3 million, representing free cash flow to net income conversion of 132%.
- **AOS** `$15 million` [segment_revenue: sales contribution from Leonard Valve]: The wording “contribution Leonard Valve” qualifies this figure (sales contribution from Leonard Valve) in a way NoCap can't match to a filing fact, so there is nothing to check it against.
  - draft: The segment also recorded a $15 million sales contribution from Leonard Valve, and segment earnings reached $179.9 million with a segment margin of 23.6%.

### Untracked kind of figure (312)

- **ACN** `$3.12 billion` [other: selling, general and administrative expenses]: NoCap doesn't track this kind of figure (selling, general and administrative expenses) yet, so there is no filing fact to check.
  - draft: Gross margin of 32.0% compared with 31.9% in the fourth quarter of fiscal 2025, while selling, general and administrative expenses rose to $3.12 billion, or 16.7% of revenues, from $2.95 billion a year earlier.
- **AFL** `159.45` [other: average yen/dollar exchange rate]: NoCap doesn't track this kind of figure (average yen/dollar exchange rate) yet, so there is no filing fact to check.
  - draft: - The average yen/dollar exchange rate was **159.45**, or **9.3%** weaker than a year earlier, creating a headwind for translated results.
- **AKAM** `$416 million` [other: Adjusted EBITDA]: NoCap doesn't track this kind of figure (Adjusted EBITDA) yet, so there is no filing fact to check.
  - draft: - Adjusted EBITDA totaled **$416 million**.

### Period ambiguous or unresolved (80)

- **AOS** `$396.2 million` [free_cash_flow: free cash flow]: Couldn't tell which A. O. Smith Corporation fiscal period "Year-to-date" refers to.
  - draft: Year-to-date cash provided by operations was $429.4 million, while free cash flow came to $396.2 million.
- **CASY** `$7.37` [eps_diluted: diluted earnings per share]: Couldn't tell which Casey's General Stores, Inc. fiscal period "first-quarter" refers to.
  - draft: reported stronger first-quarter earnings, with diluted earnings per share of $7.37, up 27.7% from the same period a year ago.
- **CINF** `$459 million` [net_income: net income]: Couldn't tell which Cincinnati Financial Corporation fiscal period "third-quarter" refers to.
  - draft: Cincinnati Financial Corporation reported third-quarter net income of $459 million, or $2.96 per share, compared with $1.122 billion, or $7.11 per share, a year earlier.

### Other (56)

- **ACN** `32.0%` [gross_margin: gross margin]: A margin change written with % could mean relative growth or percentage points. Specify which meaning is intended before comparing it with a filing.
  - draft: GAAP net income climbed to $2.03 billion in the fourth quarter, with gross margin edging up to 32.0%.
- **ALL** `$3.2 billion` [net_income: net income]: Allstate's 10-Q reports net income available to common stockholders of $3.241 billion for Q2 2026 (quarter ended Jun 30, 2026), alongside $3.271 billion, the net-income figure NoCap serves. $3.2 billion matches the available-to-common figure, so the draft's net-income meaning is ambiguous.
  - draft: Total revenues rose to **$18.6 billion**, net income reached **$3.2 billion**, and adjusted net income was **$2.3 billion**, or **$8.99 per diluted share**.
- **AME** `$1.37 billion` [segment_revenue: Electronic Instruments Group sales]: No filing fact for Ametek Inc's Electronic Instruments Group revenue for Q4 2025 (quarter ended Dec 31, 2025).
  - draft: Electronic Instruments Group sales increased to a record $1.37 billion, up 13%, with adjusted operating income of $413.7 million.
- template (2): A margin change written with % could mean relative growth or percentage points. Specify which meaning is intended before comparing it with a filing.
- template (2): No filing fact for <Company>'s diluted EPS for <period> (…).
- template (2): No filing fact for <Company>'s CNA Property and Casualty revenue growth for <period> (…).
- template (2): No filing fact for <Company>'s Loews Hotels net income for <period> (…).
- template (2): “…” makes N-percentage-point an item within a reported figure, not the figure itself, and NoCap holds no filing fact for that item, so it can't check it.
- template (1): <Company>'s N-Q reports net income available to common stockholders of N for <period> (…), alongside N, the net-income figure NoCap serves. N matches the availa
- template (1): No filing fact for <Company>'s Electronic Instruments Group revenue for <period> (…).
- template (1): No filing fact for <Company>'s Electronic Instruments Group revenue growth for <period> (…).

## Per draft

| draft | style | status | figures | not in source | claims | ✓ | ✗ | ? | ≈ |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| ACN-2026Q4 | analyst_note | ok | 24 | 0 | 24 | 6 | 4 | 14 | 0 |
| AFL-2026Q2 | chatgpt_markdown | ok | 33 | 0 | 33 | 15 | 0 | 18 | 0 |
| AKAM-2026Q2 | chatgpt_markdown | ok | 21 | 0 | 21 | 16 | 0 | 5 | 0 |
| ALL-2026Q2 | chatgpt_markdown | ok | 25 | 0 | 24 | 15 | 0 | 9 | 0 |
| AMAT-2026Q3 | analyst_note | ok | 21 | 0 | 22 | 14 | 0 | 8 | 0 |
| AME-2025Q4 | analyst_note | ok | 23 | 0 | 23 | 9 | 1 | 13 | 0 |
| AOS-2026Q3 | news_story | ok | 25 | 0 | 25 | 9 | 0 | 16 | 0 |
| APD-2026Q3 | bullet_recap | ok | 24 | 5 | 24 | 9 | 0 | 15 | 0 |
| BF-B-2026Q4 | chatgpt_markdown | ok | 24 | 0 | 24 | 11 | 1 | 12 | 0 |
| BIIB-2026Q3 | bullet_recap | ok | 22 | 0 | 23 | 10 | 0 | 13 | 0 |
| CASY-2027Q1 | news_story | ok | 22 | 0 | 22 | 0 | 0 | 22 | 0 |
| CINF-2026Q3 | news_story | ok | 25 | 0 | 25 | 0 | 0 | 25 | 0 |
| CMI-2025Q4 | news_story | ok | 25 | 0 | 25 | 0 | 0 | 25 | 0 |
| COP-2026Q2 | news_story | ok | 27 | 0 | 25 | 3 | 1 | 21 | 0 |
| CSGP-2026Q3 | news_story | ok | 24 | 0 | 24 | 6 | 0 | 18 | 0 |
| CSX-2026Q3 | bullet_recap | ok | 22 | 0 | 22 | 13 | 0 | 9 | 0 |
| DAL-2026Q3 | chatgpt_markdown | ok | 22 | 0 | 22 | 5 | 0 | 17 | 0 |
| DE-2026Q3 | bullet_recap | ok | 22 | 0 | 22 | 3 | 2 | 17 | 0 |
| DOC-2025Q4 | chatgpt_markdown | ok | 24 | 0 | 24 | 1 | 0 | 23 | 0 |
| DUK-2026Q2 | news_story | ok | 20 | 0 | 20 | 3 | 0 | 17 | 0 |
| EFX-2026Q3 | analyst_note | ok | 25 | 0 | 25 | 13 | 0 | 12 | 0 |
| ERIE-2026Q3 | news_story | ok | 24 | 0 | 24 | 9 | 0 | 15 | 0 |
| FIS-2026Q2 | bullet_recap | ok | 22 | 0 | 22 | 5 | 0 | 17 | 0 |
| FTNT-2026Q3 | chatgpt_markdown | ok | 24 | 2 | 24 | 9 | 0 | 15 | 0 |
| GILD-2026Q3 | analyst_note | ok | 21 | 0 | 22 | 12 | 1 | 9 | 0 |
| ILMN-2026Q3 | analyst_note | ok | 23 | 0 | 23 | 7 | 0 | 16 | 0 |
| ISRG-2026Q3 | bullet_recap | ok | 24 | 0 | 24 | 10 | 0 | 14 | 0 |
| L-2026Q2 | bullet_recap | ok | 24 | 0 | 24 | 8 | 1 | 15 | 0 |
| LOW-2026Q2 | bullet_recap | ok | 22 | 0 | 22 | 9 | 0 | 13 | 0 |
| LYB-2026Q3 | chatgpt_markdown | ok | 21 | 0 | 18 | 5 | 0 | 13 | 0 |
| MU-2026Q4 | analyst_note | ok | 20 | 0 | 20 | 16 | 0 | 4 | 0 |
| NKE-2027Q1 | analyst_note | ok | 20 | 0 | 21 | 0 | 0 | 21 | 0 |
| ORLY-2026Q2 | news_story | ok | 25 | 0 | 25 | 9 | 0 | 16 | 0 |
| P-2027Q2 | chatgpt_markdown | ok | 21 | 0 | 21 | 17 | 0 | 4 | 0 |
| PEP-2026Q3 | chatgpt_markdown | ok | 24 | 0 | 27 | 0 | 0 | 27 | 0 |
| PG-2027Q1 | chatgpt_markdown | ok | 24 | 5 | 24 | 5 | 0 | 19 | 0 |
| RDDT-2026Q3 | bullet_recap | ok | 23 | 0 | 23 | 17 | 0 | 6 | 0 |
| ROK-2026Q3 | analyst_note | ok | 23 | 0 | 26 | 10 | 0 | 16 | 0 |
| SHW-2026Q3 | news_story | ok | 23 | 0 | 23 | 10 | 0 | 13 | 0 |
| SYF-2026Q3 | bullet_recap | ok | 25 | 0 | 25 | 23 | 1 | 1 | 0 |
| TMUS-2026Q3 | news_story | ok | 24 | 0 | 24 | 0 | 0 | 24 | 0 |
| TROW-2026Q3 | news_story | ok | 23 | 0 | 24 | 0 | 0 | 24 | 0 |
| TSCO-2026Q2 | analyst_note | ok | 23 | 0 | 23 | 14 | 0 | 9 | 0 |
| UHS-2026Q1 | analyst_note | ok | 22 | 0 | 22 | 0 | 0 | 22 | 0 |
| VRT-2025Q4 | analyst_note | ok | 24 | 1 | 24 | 6 | 0 | 18 | 0 |
| WMB-2026Q2 | bullet_recap | ok | 24 | 0 | 24 | 8 | 0 | 16 | 0 |
| WST-2026Q3 | bullet_recap | ok | 25 | 0 | 25 | 6 | 0 | 19 | 0 |
| WY-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 12 | 1 | 11 | 0 |
| WYNN-2026Q2 | bullet_recap | ok | 26 | 0 | 26 | 14 | 0 | 12 | 0 |
| ZBRA-2026Q2 | chatgpt_markdown | ok | 26 | 0 | 22 | 11 | 0 | 11 | 0 |

## Spend

- write: {"stage": "write", "cap_usd": 3.4, "ledger_total_usd": 2.040482, "this_run_usd": 2.040482, "attempts_this_run": 50, "stub": false}
- check: {"stage": "check", "cap_usd": 4.0, "ledger_total_usd": 1.117326, "this_run_usd": 1.117326, "attempts_this_run": 184, "stub": false}

> Provenance compares values only: a source_match figure may still carry the wrong measure, period or basis, so each possible false alarm needs a human read before any fix.
