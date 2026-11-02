# AI-draft benchmark: batch `fresh`

Drafts: 50 written of 50 planned, 50 checked (ok 50). Styles: analyst_note 13, bullet_recap 12, chatgpt_markdown 13, news_story 12.

> The drafts are written from the same release sentences the release-statement matcher reads, so a release-statement ✓ is partly circular; a filed-fact ✓ (10-Q/10-K XBRL, or the release's own table) is not.

## Totals

| | claims |
|---|---:|
| ✓ verified | 388 |
| &nbsp;&nbsp;filed fact (`match`) | 216 |
| &nbsp;&nbsp;&nbsp;&nbsp;of which 10-Q/10-K | 147 |
| &nbsp;&nbsp;&nbsp;&nbsp;of which the release's own table | 69 |
| &nbsp;&nbsp;release statement | 172 |
| ✗ wrong | 7 |
| ◷ not public | 0 |
| ? unverifiable | 808 |
| ≈ approximate | 0 |
| **all claims** | **1203** |

| style | claims | ✓ filed | ✓ release | ✗ | ? | ≈ |
|---|---:|---:|---:|---:|---:|---:|
| analyst_note | 317 | 64 | 32 | 4 | 217 | 0 |
| bullet_recap | 292 | 66 | 78 | 0 | 148 | 0 |
| chatgpt_markdown | 305 | 69 | 52 | 3 | 181 | 0 |
| news_story | 289 | 17 | 10 | 0 | 262 | 0 |

## Writer provenance (code-checked)

1201 figures: 1194 source_match, 2 rounded, 5 not_in_source (the writer's own errors, kept).

## Coverage

Of 1196 figures the release states (source_match or rounded), **387 got ✓ (32.4%)**; filed-fact ✓ alone: 215 (18.0%). ✗ on them: **7** (should be near 0).

Outcomes: ? 794, ✓ filed fact 215, ✓ release statement 172, not extracted 8, ✗ 7

## Each ✗, crossed with provenance

- writer error caught (figure not in source): **0**
- possible NoCap false alarm (figure is in the source): **7**
- ✗ on no scanned figure: 0

### Possible NoCap false alarms

1. **CAT** (analyst_note) `$20 Billion` [revenue: Sales] value_mismatch, provenance source_match
   - draft: Caterpillar Q2 2026: First Quarter Above $20 Billion in Sales
   - NoCap: Caterpillar's 10-Q reports that revenue was $17.415 billion for Q1 2026 (quarter ended Mar 31, 2026), which is not more than $20 Billion.
   - source: [s3] “This is the first time in company history that we have generated over $20 billion in sales and revenues in a single quarter,” said Caterpillar Chairman and CEO Joe Creed.
2. **COF** (analyst_note) `6 percent` [other: operating expenses increase] value_mismatch, provenance source_match
   - draft: Total non-interest expense climbed 7 percent to $9.0 billion, reflecting an 11 percent jump in marketing and a 6 percent increase in operating expenses, leaving pre-provision earnings up 1 percent at $6.8 billion.
   - NoCap: Capital One's operating expenses growth for Q2 2026 (quarter ended Jun 30, 2026) was 29.4% year over year and 6.8% quarter over quarter, not 6 percent.
   - source: [s14] 6 percent increase in operating expenses.
3. **FAST** (analyst_note) `$401.6` [net_income: Net income] scale_mismatch, provenance source_match
   - draft: Net income jumped 19.7% to $401.6, with diluted net income per share advancing to $0.35 from $0.29.
   - NoCap: Fastenal Co's 10-Q reports that net income was $401.6 million for Q3 2026 (quarter ended Sep 30, 2026), so $401.6 is off by a factor of a million.
   - source: [s42] Net income was $401.6 in the third quarter of 2026, an increase of 19.7% compared to the third quarter of 2025.
4. **FAST** (analyst_note) `$421.6` [operating_cash_flow: net cash provided by operating activities] scale_mismatch, provenance source_match
   - draft: Cash generation remained solid, with net cash provided by operating activities growing to $421.6 and representing 105.0% of net income.
   - NoCap: Fastenal's 10-Q reports that operating cash flow was $421.6 million for Q3 2026 (quarter ended Sep 30, 2026), so $421.6 is off by a factor of a million.
   - source: [s44] Net cash provided by operating activities was $421.6 in the third quarter of 2026, an increase of 9.0% from the third quarter of 2025.
5. **SJM** (chatgpt_markdown) `13%` [revenue_growth: increase in net sales] value_mismatch, provenance source_match
   - draft: - One segment posted a **$90.6 million**, or **13%**, increase in net sales, while segment profit surged by **$165.8 million** on tariff refunds and higher net price realization, partly offset by higher marketing spend; volume/mix gains were led by the Dunkin…
   - NoCap: J.M. Smucker's revenue growth for Q1 FY2027 (quarter ended Jul 31, 2026) was 5.0% year over year and -2.2% quarter over quarter, not 13%.
   - source: [s31] Net sales increased $90.6 million, or 13 percent.
6. **SJM** (chatgpt_markdown) `7%` [revenue_growth: net sales slip] value_mismatch, provenance source_match
   - draft: - Another segment saw net sales slip by **$16.7 million**, or **7%**, as weaker volume/mix for snack cakes and breakfast weighed on results.
   - NoCap: J.M. Smucker's revenue growth for Q1 FY2027 (quarter ended Jul 31, 2026) was 5.0% year over year and -2.2% quarter over quarter, not 7%.
   - source: [s42] Net sales decreased $16.7 million, or 7 percent.
7. **VMC** (chatgpt_markdown) `9%` [gross_margin: Concrete gross profit margin] value_mismatch, provenance source_match
   - draft: - Concrete gross profit margin expanded to **9%**.
   - NoCap: Vulcan Materials Co's 10-Q reports that Concrete gross margin was 30.5% for Q3 2026 (quarter ended Sep 30, 2026), not 9%.
   - source: [s12] Concrete gross profit margin expanded to 9 percent.

## Every ?, by cause

| cause | ? | share | drafts | top words | top metrics |
|---|---:|---:|---:|---|---|
| Guidance | 196 | 24.3% | 37 | revenue (6); earnings per share (6); Full-year property revenue midpoint growth rates (5); operating earnings guidance (4); adjusted EPS guidance (4) | other (81), eps_diluted (52), revenue_growth (24), revenue (23) |
| No company attached | 9 | 1.1% | 1 | Adjusted EPS (2); Revenue (1); rising (1); Financial Services revenue grew (1); Operating margin (1) | other (3), revenue_growth (2), eps_diluted (2), revenue (1) |
| Company name not matched | 0 | 0.0% | 0 |  |  |
| Evidence ends before the quarter | 2 | 0.2% | 1 | Q1 FY2027 (2) | revenue (1), revenue_growth (1) |
| Constant currency or organic (company-defined measure) | 10 | 1.2% | 7 | organic (6); constant-currency (3); Organic (1) | revenue_growth (10) |
| Non-GAAP with no release fact | 14 | 1.7% | 9 | operating income growth (3); free cash flow (2); operating margin (2); diluted EPS (2); net income (2) | other (6), free_cash_flow (2), eps_diluted (2), net_income (2) |
| Qualifier wording | 68 | 8.4% | 32 | U.S. (6); cash (4); reaching (4); operating (3); comparable (3) | revenue_growth (16), other (14), eps_diluted (12), operating_income (6) |
| Untracked kind of figure | 353 | 43.7% | 50 | shares repurchased (4); returned to shareholders (4); dividends (4); Share repurchases (4); share repurchases (3) | other (290), capital_returned (22), eps_diluted (12), repurchase_authorization_remaining (7) |
| Period ambiguous or unresolved | 104 | 12.9% | 19 | net sales (4); adjusted EPS (3); free cash flow (3); revenues (3); net income (3) | other (22), revenue_growth (18), eps_diluted (17), net_income (11) |
| Other | 52 | 6.4% | 20 | net income (2); Revenue (2); segment operating income (2); total debt (2); operating income (2) | revenue_growth (22), segment_revenue (10), other (6), operating_income (5) |

### Guidance (196)

- **ABNB** `$4.69 billion` [revenue: revenue]: This is guidance about a future period, so no filing can confirm it yet.
  - draft: For the third quarter, the company expects revenue of $4.69 billion to $4.77 billion, representing year-over-year growth of 15% to 17%.
- **AEP** `$100 billion` [other: five-year capital plan]: This is guidance about a future period, so no filing can confirm it yet.
  - draft: The centrepiece alongside the results was an expanded $100 billion five-year capital plan through 2031, intended to strengthen reliability, modernise the grid and support growing customer demand.
- **AMT** `$55 million` [other: midpoint of its full-year outlook for property revenue]: This is guidance about a future period, so no filing can confirm it yet.
  - draft: - Citing Data Centers segment outperformance, the company raised the midpoint of its full-year outlook for property revenue by **$55 million**.

### No company attached (9)

- **CTSH** `$5.7 billion` [revenue: Revenue]: The draft doesn't say which company this figure belongs to.
  - draft: - Revenue climbed to **$5.7 billion**, rising **4.6%** year-over-year, or **4.8%** in constant currency.

### Evidence ends before the quarter (2)

- **SJM** `$2.2 billion` [revenue: net sales]: NoCap's evidence for The J.M. Smucker Company ends with Q1 FY2027, so it can't check fiscal 2027 yet.
  - draft: Smucker Company opened fiscal 2027 with net sales of **$2.2 billion**, up **5%** from the prior year, aided by higher coffee pricing and tariff refunds received during the quarter.

### Constant currency or organic (company-defined measure) (10)

- **ABNB** `13%` [revenue_growth: revenue growth on an ex-FX basis]: 13% is a constant-currency (FX-neutral) figure; filings report results at actual exchange rates, so NoCap can't check it.
  - draft: Airbnb said second-quarter revenue advanced 17% year-over-year to $3.6 billion, with growth of 13% on an ex-FX basis, as demand lifted bookings across its regions.
- **CTSH** `4.8%` [revenue_growth: rising in constant currency]: 4.8% is a constant-currency (FX-neutral) figure; filings report results at actual exchange rates, so NoCap can't check it.
  - draft: - Revenue climbed to **$5.7 billion**, rising **4.6%** year-over-year, or **4.8%** in constant currency.
- **HUBB** `11%` [revenue_growth: Organic sales grew]: The wording “Organic” describes a company-defined measure that filings don't report under that name, so NoCap can't check 11%.
  - draft: - Organic sales grew 11%; M&A contributed 13%

### Non-GAAP with no release fact (14)

- **ABNB** `$1.3 billion` [free_cash_flow: Free cash flow]: No press-release fact for Airbnb's non-GAAP free cash flow for Q2 2026 (quarter ended Jun 30, 2026) (non-GAAP figures come only from earnings releases).
  - draft: Free cash flow was $1.3 billion, up 30%, with a 35% margin, while trailing twelve month free cash flow reached $4.8 billion at a 37% margin.
- **CSCO** `$6.2 billion` [operating_income: Non-GAAP operating income]: No press-release fact for Cisco's non-GAAP operating income for Q4 FY2026 (quarter ended Jul 25, 2026) (non-GAAP figures come only from earnings releases).
  - draft: - Non-GAAP operating income advanced to **$6.2 billion**, with a non-GAAP operating margin of **35.9%**.
- **CVS** `$2.58` [eps_diluted: adjusted EPS]: No press-release fact for CVS Health's non-GAAP diluted EPS for Q2 2026 (quarter ended Jun 30, 2026) (non-GAAP figures come only from earnings releases).
  - draft: CVS Health reported second-quarter GAAP diluted EPS of $2.31, up from $0.80 in the prior year, while adjusted EPS climbed to $2.58 from $1.81.

### Qualifier wording (68)

- **ABNB** `$4.8 billion` [free_cash_flow: trailing twelve month free cash flow]: The wording “twelve month” qualifies this figure (trailing twelve month free cash flow) in a way NoCap can't match to a filing fact, so there is nothing to check it against.
  - draft: Free cash flow was $1.3 billion, up 30%, with a 35% margin, while trailing twelve month free cash flow reached $4.8 billion at a 37% margin.
- **AEP** `$2.23` [eps_diluted: operating earnings per share]: The wording “operating” qualifies this figure (operating earnings per share) in a way NoCap can't match to a filing fact, so there is nothing to check it against.
  - draft: Operating earnings rose to $1,216 million, or $2.23 per share, compared with $963 million, or $1.80 per share, a year earlier, extending the strong year-to-date performance cited by the company.
- **AIG** `$2.00` [eps_diluted: Adjusted after-tax income per diluted share]: The wording “after-tax income” qualifies this figure (Adjusted after-tax income per diluted share) in a way NoCap can't match to a filing fact, so there is nothing to check it against.
  - draft: - Adjusted after-tax income: $1.1 billion, or $2.00 per diluted share, up 10% year-over-year

### Untracked kind of figure (353)

- **ABNB** `$27.2 billion` [other: gross booking value]: NoCap doesn't track this kind of figure (gross booking value) yet, so there is no filing fact to check.
  - draft: The company reported gross booking value of $27.2 billion, up 16%, while nights and seats booked climbed 10% to 148.3 million.
- **AEP** `$972 million` [net_income: GAAP earnings]: NoCap doesn't track this kind of figure (GAAP earnings) yet, so there is no filing fact to check.
  - draft: American Electric Power reported third-quarter 2026 GAAP earnings of $1,187 million, or $2.18 per share, up from $972 million, or $1.82 per share, in third-quarter 2025.
- **AIG** `$1.1 billion` [other: Adjusted after-tax income]: NoCap doesn't track this kind of figure (Adjusted after-tax income) yet, so there is no filing fact to check.
  - draft: - Adjusted after-tax income: $1.1 billion, or $2.00 per diluted share, up 10% year-over-year

### Period ambiguous or unresolved (104)

- **AWK** `$946 million` [net_income: segment net income]: Couldn't tell which American Water Works Company, Inc. fiscal period "Over the first nine months" refers to.
  - draft: Over the first nine months, segment net income jumped to $946 million from $872 million.
- **CVS** `$0.80` [eps_diluted: GAAP diluted EPS]: Couldn't tell which CVS Health fiscal period "in the prior year" refers to.
  - draft: CVS Health reported second-quarter GAAP diluted EPS of $2.31, up from $0.80 in the prior year, while adjusted EPS climbed to $2.58 from $1.81.
- **DASH** `23%` [revenue_growth: revenue]: Couldn't tell which DoorDash, Inc. fiscal period "no period" refers to.
  - draft: Excluding Deliveroo, the company said total orders grew 17%, with Marketplace GOV up 23% and revenue also advancing 23%.

### Other (52)

- **AIG** `11%` [revenue_growth: Net premiums written excluding North America Property growth]: No filing fact for American International Group's General Insurance excluding North America Property revenue growth for Q2 2026 (quarter ended Jun 30, 2026).
  - draft: - Net premiums written excluding North America Property: up 11%
- **AWK** `$407 million` [net_income: net income]: No filing fact for American Water Works Company, Inc.'s Regulated Businesses net income for Q3 2026 (quarter ended Sep 30, 2026).
  - draft: The Regulated Businesses drove profitability, with net income climbing to $407 million in the third quarter from $383 million a year earlier.
- **BBY** `$3.00 billion` [segment_revenue: Domestic online revenue]: No filing fact for Best BUY Co Inc's Domestic online revenue for Q2 FY2027 (quarter ended Aug 1, 2026).
  - draft: - Domestic online revenue grew **5.1%** on a comparable basis to **$3.00 billion**.
- template (3): This claim is net income available to common stockholders, which can differ after preferred dividends; NoCap doesn't resolve that basis here, so it cannot compa
- template (2): No filing fact for American Water Works Company, <Company>'s Regulated Businesses net income for <period> (…).
- template (2): NoCap checks operating income in dollars, and this figure is written in percent.
- template (2): NoCap checks revenue growth in percent, and this figure is written in other.
- template (1): No filing fact for <Company>'s General Insurance excluding North America Property revenue growth for <period> (…).
- template (1): No filing fact for <Company>'s capital returned to shareholders for <period> (…).
- template (1): No filing fact for <Company>'s Domestic online revenue for <period> (…).
- template (1): No filing fact for <Company>'s Financial Products revenue for <period> (…).

## Per draft

| draft | style | status | figures | not in source | claims | ✓ | ✗ | ? | ≈ |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| ABNB-2026Q2 | news_story | ok | 32 | 0 | 31 | 8 | 0 | 23 | 0 |
| AEP-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 3 | 0 | 21 | 0 |
| AIG-2026Q2 | bullet_recap | ok | 27 | 0 | 27 | 13 | 0 | 14 | 0 |
| AMT-2026Q3 | chatgpt_markdown | ok | 22 | 0 | 22 | 1 | 0 | 21 | 0 |
| AWK-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 0 | 0 | 24 | 0 |
| BBY-2027Q2 | chatgpt_markdown | ok | 25 | 0 | 25 | 11 | 0 | 14 | 0 |
| BKR-2026Q3 | analyst_note | ok | 24 | 0 | 23 | 12 | 0 | 11 | 0 |
| BNY-2026Q3 | bullet_recap | ok | 23 | 0 | 22 | 16 | 0 | 6 | 0 |
| CAT-2026Q2 | analyst_note | ok | 26 | 0 | 26 | 13 | 1 | 12 | 0 |
| CB-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 6 | 0 | 18 | 0 |
| COF-2026Q2 | analyst_note | ok | 24 | 0 | 23 | 13 | 1 | 9 | 0 |
| CSCO-2026Q4 | chatgpt_markdown | ok | 23 | 0 | 23 | 11 | 0 | 12 | 0 |
| CTSH-2026Q3 | chatgpt_markdown | ok | 24 | 2 | 22 | 0 | 0 | 22 | 0 |
| CVS-2026Q2 | news_story | ok | 23 | 0 | 23 | 3 | 0 | 20 | 0 |
| DASH-2026Q3 | news_story | ok | 20 | 0 | 20 | 4 | 0 | 16 | 0 |
| DG-2026Q2 | bullet_recap | ok | 24 | 0 | 24 | 9 | 0 | 15 | 0 |
| DGX-2026Q3 | news_story | ok | 18 | 0 | 21 | 0 | 0 | 21 | 0 |
| DIS-2026Q1 | analyst_note | ok | 26 | 0 | 26 | 8 | 0 | 18 | 0 |
| EG-2026Q3 | news_story | ok | 23 | 0 | 23 | 0 | 0 | 23 | 0 |
| EW-2026Q2 | chatgpt_markdown | ok | 24 | 3 | 24 | 10 | 0 | 14 | 0 |
| EXE-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 0 | 0 | 24 | 0 |
| EXPD-2026Q2 | news_story | ok | 22 | 0 | 22 | 0 | 0 | 22 | 0 |
| FAST-2026Q3 | analyst_note | ok | 25 | 0 | 25 | 7 | 2 | 16 | 0 |
| HUBB-2026Q3 | bullet_recap | ok | 25 | 0 | 25 | 7 | 0 | 18 | 0 |
| IBKR-2026Q2 | bullet_recap | ok | 25 | 0 | 27 | 17 | 0 | 10 | 0 |
| J-2026Q3 | news_story | ok | 26 | 0 | 26 | 0 | 0 | 26 | 0 |
| JBHT-2026Q3 | news_story | ok | 28 | 0 | 27 | 12 | 0 | 15 | 0 |
| LITE-2026Q4 | analyst_note | ok | 25 | 0 | 25 | 13 | 0 | 12 | 0 |
| LLY-2025Q1 | news_story | ok | 24 | 0 | 24 | 0 | 0 | 24 | 0 |
| MAS-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 0 | 0 | 24 | 0 |
| MCHP-2027Q1 | bullet_recap | ok | 23 | 0 | 23 | 12 | 0 | 11 | 0 |
| MCO-2026Q3 | chatgpt_markdown | ok | 24 | 0 | 24 | 11 | 0 | 13 | 0 |
| META-2026Q3 | bullet_recap | ok | 24 | 0 | 24 | 11 | 0 | 13 | 0 |
| MLM-2026Q2 | chatgpt_markdown | ok | 24 | 0 | 24 | 11 | 0 | 13 | 0 |
| NXPI-2026Q3 | chatgpt_markdown | ok | 24 | 0 | 24 | 16 | 0 | 8 | 0 |
| PKG-2026Q2 | bullet_recap | ok | 19 | 0 | 23 | 5 | 0 | 18 | 0 |
| PM-2026Q3 | chatgpt_markdown | ok | 24 | 0 | 24 | 12 | 0 | 12 | 0 |
| ROL-2026Q3 | news_story | ok | 23 | 0 | 23 | 0 | 0 | 23 | 0 |
| ROST-2026Q2 | chatgpt_markdown | ok | 24 | 0 | 24 | 8 | 0 | 16 | 0 |
| SBUX-2026Q3 | analyst_note | ok | 25 | 0 | 25 | 14 | 0 | 11 | 0 |
| SCHW-2026Q2 | bullet_recap | ok | 26 | 0 | 25 | 15 | 0 | 10 | 0 |
| SJM-2027Q1 | chatgpt_markdown | ok | 23 | 0 | 23 | 7 | 2 | 14 | 0 |
| TECH-2026Q4 | news_story | ok | 25 | 0 | 25 | 0 | 0 | 25 | 0 |
| TYL-2026Q3 | bullet_recap | ok | 24 | 0 | 24 | 21 | 0 | 3 | 0 |
| UNH-2025Q3 | chatgpt_markdown | ok | 23 | 0 | 23 | 13 | 0 | 10 | 0 |
| VICI-2026Q3 | analyst_note | ok | 24 | 0 | 24 | 7 | 0 | 17 | 0 |
| VLTO-2026Q3 | news_story | ok | 23 | 0 | 24 | 0 | 0 | 24 | 0 |
| VMC-2026Q3 | chatgpt_markdown | ok | 23 | 0 | 23 | 10 | 1 | 12 | 0 |
| VTR-2026Q3 | bullet_recap | ok | 23 | 0 | 23 | 3 | 0 | 20 | 0 |
| WDAY-2027Q2 | bullet_recap | ok | 25 | 0 | 25 | 15 | 0 | 10 | 0 |

## Spend

- write: {"stage": "write", "cap_usd": 3.4, "ledger_total_usd": 2.293622, "this_run_usd": 2.293622, "attempts_this_run": 51, "stub": false}
- check: {"stage": "check", "cap_usd": 4.0, "ledger_total_usd": 1.217068, "this_run_usd": 1.217068, "attempts_this_run": 216, "stub": false}

> Provenance compares values only: a source_match figure may still carry the wrong measure, period or basis, so each possible false alarm needs a human read before any fix.
