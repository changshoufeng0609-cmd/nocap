# Examples

| File | `/api/examples` id | What it shows |
|---|---|---|
| `nvda_q2_fy2027.txt` | `nvda-ai-story` | QoQ/YoY swap, GAAP vs non-GAAP EPS, guidance stays `?` |
| `meta_q3_2026.txt` | `meta-ai-story` | wrong quarter, millions for billions, a transposed EPS |
| `roundup_q2_2026.txt` | `q2-roundup-look-ahead` | another company's EPS, and look-ahead under its own `as_of` |
| `draft_6claims.txt` | (CLI only) | the Day 2 six-claim smoke test |

`examples.json` holds each demo draft's title, description, `as_of` and context for `GET /api/examples`.
`scripts/build_snapshot.py` records all three through the real pipeline into `data/snapshot/api_cache/`, so the
demo replays them with `cached: true` and never waits on the model. Every figure in the drafts is a real value from
`data/facts.sqlite` and `data/pr_facts`; the planted errors are real figures from the wrong period, basis, scale or
company. Verdicts below were confirmed with the CLI on 2026-10-27; claim ids are the CLI's (document order), which
are also the ids in the pre-baked API streams.

## `nvda_q2_fy2027.txt`: NVIDIA Q2 FY2027 story

```
python -m receipts check examples/nvda_q2_fy2027.txt --context "NVIDIA, second quarter of fiscal 2027"
```

| Claim | Draft says | Expected | Why (filed value) |
|---|---|---|---|
| c1 | revenue $96.2 billion | ✓ `match` | $96,221M (10-Q 0001045810-26-000075; 8-K 0001045810-26-000073) |
| c2 | revenue up 18% from a year ago | ✗ `period_mismatch` | 18% is the QoQ growth (17.9%); YoY was 105.9% (vs $46,743M in Q2 FY2026) |
| c3 | Data Center revenue $89.0 billion | ✓ `match` | $89,023M (8-K press release) |
| c4 | GAAP gross margin 75.0% | ✓ `match` | 74.98% (10-Q) |
| c5 | non-GAAP gross margin 75.0% | ✓ `match` | 75.0% (8-K press release) |
| c6 | operating income $63.7 billion | ✓ `match` | $63,734M (10-Q) |
| c7 | GAAP net income $59.7 billion | ✓ `match` | $59,688M (10-Q) |
| c8 | GAAP diluted EPS $2.22 | ✗ `basis_mismatch` | $2.22 is non-GAAP diluted EPS; GAAP was $2.46 |
| c9 | operating expenses $8.4 billion | ✓ `match` | $8,408M (10-Q) |
| c10 | Q3 revenue outlook $108.0 billion | ? `forward_looking` | guidance (the release's own outlook figure) |
| c11 | plus or minus 2% | ? `forward_looking` | guidance tolerance |

Summary: `7 ✓ · 2 ✗ · 2 ? · 0 ≈`.

## `meta_q3_2026.txt`: Meta Q3 2026 story

```
python -m receipts check examples/meta_q3_2026.txt --context "Meta Platforms, third quarter of 2026"
```

| Claim | Draft says | Expected | Why (filed value) |
|---|---|---|---|
| c1 | revenue $64.6 billion | ✓ `match` | $64,601M (10-Q 0001628280-26-066773; 8-K 0001628280-26-066700) |
| c2 | revenue up 26% year over year | ✓ `match` | 26.1% (vs $51,242M in Q3 2025) |
| c3 | total costs and expenses $42.0 billion | ✗ `period_mismatch` | $42.0B is Q2 2026 ($42,026M); Q3 2026 was $51,613M |
| c4 | research and development $20.1 billion | ✓ `match` | $20,054M |
| c5 | operating income $12.99 million | ✗ `scale_mismatch` | $12,988M, i.e. $12.99 billion; `expected_value` is 12,988 (the claim is in millions) |
| c6 | net income $9.86 billion | ✓ `match` | $9,860M |
| c7 | $3.38 per diluted share | ✗ `value_mismatch` | diluted EPS was $3.83 (digits transposed) |
| c8 | cash flow from operations $32.8 billion | ✓ `match` | $32,785M (10-Q, derived from year-to-date) |

Summary: `5 ✓ · 3 ✗ · 0 ? · 0 ≈`.

## `roundup_q2_2026.txt`: June-quarter roundup with time travel

This example's `as_of` is `2026-07-29T16:00:00Z`, noon ET on July 29: JPMorgan (8-K accepted 2026-07-14 10:30:38 UTC)
and Alphabet (2026-07-22 20:01:36 UTC) had reported, Microsoft had not (2026-07-29 20:04:53 UTC). The context line
names all three companies, which is what lets NoCap say whose figure a misattributed number is.

```
python -m receipts check examples/roundup_q2_2026.txt \
  --context "Microsoft, Alphabet and JPMorgan Chase, quarter ended June 30, 2026" --as-of 2026-07-29T16:00:00Z
```

| Claim | Draft says | Expected at `as_of` | Without `as_of` | Why (filed value) |
|---|---|---|---|---|
| c1 | JPMorgan net income $21.2 billion | ✓ `match` | ✓ | $21,155M (8-K 0001628280-26-048078) |
| c2 | JPMorgan $7.70 per diluted share | ✓ `match` | ✓ | $7.70 |
| c3 | JPMorgan net revenue $57.3 billion | ✓ `match` | ✓ | $57,347M (reported basis) |
| c4 | Alphabet revenue up 24% from a year earlier | ✓ `match` | ✓ | 24.2% (vs $96,428M in Q2 2025) |
| c5 | Alphabet revenue $119.8 billion | ✓ `match` | ✓ | $119,796M (8-K 0001652044-26-000066) |
| c6 | Alphabet operating income $40.8 billion | ✓ `match` | ✓ | $40,770M |
| c7 | Alphabet earned $7.70 per diluted share | ✗ `entity_mismatch` | ✗ `entity_mismatch` | $7.70 is JPMorgan's EPS; Alphabet's was $9.11 |
| c8 | Microsoft fiscal Q4 revenue $90.0 billion | ✗ `look_ahead` | ✓ `match` | not public until 8-K 0001193125-26-323632 at 2026-07-29 20:04:53 UTC; $90,007M |
| c9 | Microsoft diluted EPS $4.81 | ✗ `look_ahead` | ✓ `match` | same 8-K; $4.81 |

Summary at `as_of`: `6 ✓ · 3 ✗ · 0 ? · 0 ≈`. Without `as_of`: `8 ✓ · 1 ✗`. In the API, the as-of re-check of this
example costs nothing: its model responses ship in `data/snapshot/spark_cache/`, so only verification reruns.

## `draft_6claims.txt`

A hand-written six-claim draft covering NVIDIA, Microsoft and Meta. Four claims are true and two are wrong (one period swap, one scale error). Every figure is a real SEC value.

```
python -m receipts check examples/draft_6claims.txt
python -m receipts check examples/draft_6claims.txt --as-of 2026-08-26T20:00:00Z   # time travel
python -m receipts check examples/draft_6claims.txt --json
```

### Expected verdicts

| Claim | Draft says | Expected | Why (filed value) |
|---|---|---|---|
| c1 | NVIDIA revenue, Q2 FY2027: $96.2 billion | ✓ `match` | $96,221M (10-Q 0001045810-26-000075, accepted 2026-08-26 20:36:00 UTC) |
| c2 | NVIDIA diluted EPS, Q2 FY2027: $2.46 | ✓ `match` | $2.46 (same 10-Q, and the 8-K press release 0001045810-26-000073) |
| c3 | Microsoft revenue, quarter ended Dec 31, 2025: $82.9 billion | ✗ `period_mismatch` | $82.9B is the March 2026 quarter ($82,886M). The December quarter was $81,273M (10-Q 0001193125-26-027207) |
| c4 | Meta revenue, Q3 2026: $64.6 billion | ✓ `match` | $64,601M (10-Q 0001628280-26-066773) |
| c5 | Meta net income, Q3 2026: $9.86 million | ✗ `scale_mismatch` | $9,860M, which is $9.86 billion. `expected_value` is 9,860 because the claim is in millions |
| c6 | Meta diluted EPS, Q3 2026: $3.83 | ✓ `match` | $3.83 (same 10-Q) |

Expected summary: `4 ✓ verified · 2 ✗ wrong · 0 ? unverifiable · 0 ≈ approximate`.

### Time travel

The `--as-of` time decides which filings count as already public:

| `--as-of` | c1 and c2 (NVIDIA) | c3 (Microsoft) | c4 to c6 (Meta) |
|---|---|---|---|
| none (latest filings) | ✓ | ✗ `period_mismatch` | ✓ ✗ ✓ |
| `2026-08-26T20:00:00Z`, before NVIDIA's 8-K | ✗ `look_ahead`: the 8-K was accepted at 20:21:19 UTC | ✗ `period_mismatch` | ✗ `look_ahead`: Meta's 8-K was accepted 2026-10-21 |
| `2026-08-26T20:25:00Z`, between NVIDIA's 8-K and 10-Q | ✓, from the 8-K press release | ✗ `period_mismatch` | ✗ `look_ahead` |

### Output on 2026-10-27

This run used `data/facts.sqlite` and `data/pr_facts`. Receipt lines are trimmed to the first one per claim.

```
NoCap · examples/draft_6claims.txt
context: NVIDIA, second quarter of fiscal 2027 (inferred) · as of: latest filings
──────────────────────────────────────────────────────────────────────────────
NVIDIA reported revenue of $96.2 billion [✓c1] for the second quarter of fiscal 2027, with diluted earnings per share of $2.46 [✓c2].

Microsoft's revenue for the quarter ended December 31, 2025 was $82.9 billion [✗c3].

Meta Platforms reported third-quarter 2026 revenue of $64.6 billion [✓c4]. Net income was $9.86 million [✗c5], or $3.83 [✓c6] per diluted share.
──────────────────────────────────────────────────────────────────────────────
✓ c1   revenue · $96.2 billion · NVIDIA · second quarter of fiscal 2027  [match]
       Matches NVIDIA's 10-Q: revenue was $96.221 billion for Q2 FY2027 (quarter ended Jul 26, 2026).
       receipt: 8-K press release 0001045810-26-000073 · accepted 2026-08-26 20:21:19 UTC · https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm
✓ c2   eps_diluted · $2.46 · NVIDIA · second quarter of fiscal 2027  [match]
       Matches NVIDIA's 10-Q: diluted EPS was $2.46 for Q2 FY2027 (quarter ended Jul 26, 2026).
       receipt: 8-K press release 0001045810-26-000073 · accepted 2026-08-26 20:21:19 UTC · https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm
✗ c3   revenue · $82.9 billion · Microsoft · the quarter ended December 31, 2025  [period_mismatch]
       $82.9 billion is Microsoft's revenue for Q3 FY2026 (quarter ended Mar 31, 2026); for Q2 FY2026 (quarter ended Dec 31, 2025) it was $81.273 billion.
       expected: $81.273 billion
       receipt: 10-Q 0001193125-26-027207 · accepted 2026-01-28 21:07:34 UTC · https://www.sec.gov/Archives/edgar/data/789019/000119312526027207/msft-20251231.htm
✓ c4   revenue · $64.6 billion · Meta Platforms · third-quarter 2026  [match]
       Matches Meta Platforms' 10-Q: revenue was $64.601 billion for Q3 2026 (quarter ended Sep 30, 2026).
       receipt: 8-K press release 0001628280-26-066700 · accepted 2026-10-21 20:03:35 UTC · https://www.sec.gov/Archives/edgar/data/1326801/000162828026066700/meta-09302026xexhibit991.htm
✗ c5   net_income · $9.86 million · Meta Platforms · third-quarter 2026  [scale_mismatch]
       Meta Platforms' 10-Q reports that net income was $9.86 billion for Q3 2026 (quarter ended Sep 30, 2026), so $9.86 million is off by a factor of 1,000.
       expected: $9,860 million
       receipt: 10-Q 0001628280-26-066773 · accepted 2026-10-22 00:00:08 UTC · https://www.sec.gov/Archives/edgar/data/1326801/000162828026066773/meta-20260930.htm
✓ c6   eps_diluted · $3.83 · Meta Platforms · third-quarter 2026  [match]
       Matches Meta Platforms' 10-Q: diluted EPS was $3.83 for Q3 2026 (quarter ended Sep 30, 2026).
       receipt: 8-K press release 0001628280-26-066700 · accepted 2026-10-21 20:03:35 UTC · https://www.sec.gov/Archives/edgar/data/1326801/000162828026066700/meta-09302026xexhibit991.htm
──────────────────────────────────────────────────────────────────────────────
4 ✓ verified · 2 ✗ wrong · 0 ? unverifiable · 0 ≈ approximate
extraction: 3 passage call(s), 0 cached, $0.0129, 3.8 s
```

## Trap drafts (Muse Wonder, eval corpus `trap_v1`)

`trap_jpm_q1_2026.txt` and `trap_amd_q1_2026.txt` are two drafts from the trap-draft corpus (`eval/build_traps.py`, `eval/README.md`). Muse Wonder (`muse-wonder-1.0`) wrote each one from a fact sheet and planted three errors. Code then checked every figure against `data/facts.sqlite` and `data/pr_facts` and wrote the expected verdicts below. Both are dev drafts (`trap-JPM-FY2026Q1-b`, `trap-AMD-FY2026Q1-a`), so the held-out test split is never shown. Each file is the corpus `text` verbatim (headline plus story).

Each draft is dated two hours after its company's earnings 8-K, and its third error is a rival's figure that wasn't public yet. Run it with `--as-of` for the time-travel catch; without it, that figure is simply true today. All table entries were rechecked with the CLI after the J1 verifier and backend fixes on 2026-10-27, both with and without `--as-of`. The four checks used existing extraction responses and added **$0** model spend. These response caches are local; the two trap demos are not part of the three bundled snapshot examples, so their first check in a fresh clone requires a configured model backend.

### `trap_jpm_q1_2026.txt`: JPMorgan Q1 2026, the morning before Bank of America reported

```
python -m receipts check examples/trap_jpm_q1_2026.txt \
  --context "JPMorgan Chase (JPM), first quarter of fiscal 2026, the quarter ended March 31, 2026" \
  --as-of 2026-04-14T12:32:42Z
```

| Claim | Draft says | Expected at `as_of` | Without `as_of` | Why (filed value) |
|---|---|---|---|---|
| c1 | net income $16.5 billion | ✓ `match` | ✓ | $16,494M (8-K 0001628280-26-024990, accepted 2026-04-14 10:32:42 UTC) |
| c2 | net income up 13% year over year | ✓ `match` | ✓ | 12.6% (vs $14,643M in Q1 2025) |
| c3 | diluted EPS $5.94 | ✓ `match` | ✓ | $5.94 |
| c4 | GAAP revenue $50.5 billion | ✗ `basis_mismatch` | ✗ | **planted (basis):** $50.5B is managed (non-GAAP) revenue; GAAP total net revenue was $49,836M |
| c5 | revenue rose 8.8% year over year | ✗ `period_mismatch` | ✗ | **planted (period):** 8.8% is the quarter-over-quarter rate; year over year was 9.99% (vs $45,310M) |
| c6 | total expenses $26.9 billion | ✓ `match` | ✓ | $26,850M noninterest expense |
| c7 | CIB revenue $23.4 billion | ✓ `match` | ✓ | $23,379M |
| c8 | CIB revenue up 19% year over year | ✓ `match` | ✓ | 18.9% (vs $19,666M) |
| c9 | CCB revenue $19.6 billion | ✓ `match` | ✓ | $19,568M |
| c10 | net income $13.0 billion in the previous quarter | ✓ `match` | ✓ | $13,025M for Q4 2025 (8-K 0001628280-26-001902) |
| c11 | Bank of America revenue $30.3 billion, quarter ended March 31, 2026 | ✗ `look_ahead` | ✓ `match` | **planted (look-ahead):** not public until BofA's 8-K 0000070858-26-000222 at 2026-04-15 10:44:44 UTC, a day after the dateline; $30,272M |

Summary at `as_of`: `8 ✓ · 3 ✗ · 0 ? · 0 ≈`. Without `as_of`: `9 ✓ · 2 ✗`.

### `trap_amd_q1_2026.txt`: AMD Q1 2026, citing NVIDIA's quarter two weeks early

```
python -m receipts check examples/trap_amd_q1_2026.txt \
  --context "AMD (AMD), first quarter of fiscal 2026, the quarter ended March 28, 2026" \
  --as-of 2026-05-05T22:16:06Z
```

| Claim | Draft says | Expected at `as_of` | Without `as_of` | Why (filed value) |
|---|---|---|---|---|
| c1 | revenue $10.3 billion | ✓ `match` | ✓ | $10,253M (8-K 0000002488-26-000072, accepted 2026-05-05 20:16:06 UTC) |
| c2 | revenue up 38% year over year | ✓ `match` | ✓ | 37.8% (vs $7,438M in Q1 2025) |
| c3 | Data Center revenue $5.78 billion | ✓ `match` | ✓ | $5,775M |
| c4 | Data Center revenue up 57% year over year | ✓ `match` | ✓ | 57.2% (vs $3,674M) |
| c5 | Client revenue $3.03 billion | ✗ `value_mismatch` | ✗ | **planted (value):** Client revenue was $2,885M; the draft is 5% high |
| c6 | net income $1.38 billion | ✓ `match` | ✓ | $1,383M |
| c7 | non-GAAP gross margin 55.4% | ✓ `match` | ✓ | 55.45% (non-GAAP gross profit $5,685M) |
| c8 | GAAP diluted EPS $1.37 | ✗ `basis_mismatch` | ✗ | **planted (basis):** $1.37 is non-GAAP diluted EPS; GAAP was $0.84 |
| c9 | NVIDIA revenue $68.1 billion, quarter ended January 25, 2026 | ✓ `match` | ✓ | $68,127M (NVIDIA 8-K 0001045810-26-000019, public since 2026-02-25) |
| c10 | NVIDIA revenue $81.6 billion, quarter ended April 26, 2026 | ✗ `look_ahead` | ✓ `match` | **planted (look-ahead):** not public until NVIDIA's 8-K 0001045810-26-000051 at 2026-05-20 20:21:19 UTC, 15 days after the dateline; $81,615M |
| c11 | outlook: revenue $11.2 billion | ? `forward_looking` | ? | guidance (the release's own outlook) |
| c12 | plus or minus $300 million | ? `forward_looking` | ? | guidance range |

Summary at `as_of`: `7 ✓ · 3 ✗ · 2 ? · 0 ≈`. Without `as_of`: `8 ✓ · 2 ✗ · 2 ?`.
