# AI-draft benchmark: Muse Wonder writes, NoCap checks

The comprehensive tests ([methods](spot500_methods.md)) check one sentence at a time. People paste whole drafts:
AI-written recaps, news stories, analyst notes and ChatGPT-style markdown. This benchmark measures NoCap on such
drafts end to end. Muse Wonder writes each draft from one company's own earnings-release sentences, and NoCap checks
it exactly as the website does. Code then tallies every ✓, ✗ and `?` by cause. Tool: [`ai_drafts.py`](ai_drafts.py)
(plan, write, check, report). Records: [`ai_drafts_runs/`](ai_drafts_runs/).

## Procedure

1. **Plan** (offline, deterministic). 50 companies per batch, stratified by GICS sector over the 399 eligible
   S&P 500 companies.
   - Each company contributes its latest quarter whose earnings release NoCap has indexed and whose 10-Q/10-K
     revenue, net income or EPS the snapshot holds.
   - Its release sentences must hold at least 30 figures.
   - Batches never share a company or a release.
   - The seven companies the owner tested by hand are left out.
   - Styles rotate through four: news story, bullet recap, analyst note and ChatGPT-style markdown.
2. **Write.** One `muse-wonder-1.0` call per company (Standard mode, low effort) writes a 15–25-figure draft from
   that release's indexed sentences only. Code classifies every figure's provenance by value:
   - `source_match`: in the sentences;
   - `rounded`: after normal rounding;
   - `not_in_source`: the writer's own error, kept.
3. **Check.** `receipts.check` on each draft, as the website runs it:
   - no context, no as-of date, live `muse-spark-1.3` extraction, against the deployed snapshot;
   - extraction is cached per batch, so re-checking after a verifier change costs $0;
   - `--cached-only` never calls the model, and it skips any draft whose extraction context changed. No draft was
     skipped in any re-check below.
4. **Report.**
   - totals, with ✓ split into filed facts (10-Q/10-K XBRL or the release's own table) and release statements;
   - each ✗ crossed with provenance. A ✗ on a figure the release states is listed as a possible false alarm;
   - every `?` bucketed by cause.

**Two batches:**
- **`dev`** (seed 20261102) is the design set. Its possible false alarms and `?` buckets drove the fixes.
- **`fresh`** (seed 20261103) is held out. It was written and checked once on November 2, on private `98b8de9`,
  before any round-10 fix was written. The fixes were specified from `dev` alone.

## Results

Coverage is the share of the figures the release states (`source_match` or `rounded`) that got ✓. A ✗ on such a
figure is a possible false alarm.

| batch | code | claims | ✓ (filed / release statement) | ✗ | `?` | coverage | filed-fact coverage |
|---|---|---:|---:|---:|---:|---:|---:|
| dev | before (`98b8de9`) | 1,175 | 413 (279 / 134) | 13 | 749 | 35.4% | 23.9% |
| dev | after rounds 10a and 10b (`e9aaa51`) | 1,175 | 481 (333 / 148) | **0** | 694 | **41.3%** | 28.5% |
| **fresh (held out)** | before (`98b8de9`) | 1,203 | 388 (216 / 172) | 7 | 808 | 32.4% | 18.0% |
| **fresh (held out)** | after rounds 10a and 10b (`e9aaa51`) | 1,203 | 457 (269 / 188) | **9** | 737 | **38.1%** | 22.4% |

**What changed between before and after:**
- Rounds 10a and 10b (design set only):
  - comparison bases whose label follows an intervening figure;
  - written cents;
  - quarters with no year, which anchor to the latest ended one as month-named quarters already did;
  - a draft title's lone quarter;
  - consolidated net income;
  - release-table conflicts;
  - segment subjects;
  - "net sales and revenues" totals;
  - a release's year never confirming its quarter.
- The loss-wording fix (`5196d59`), which landed after the fresh batch was checked.

On the held-out batch, ✓ rose by 69, and every ✓ that was added landed on a figure the release states. News stories
gained the most (✓ 27 → 78), because they write "third-quarter" and often name no year.

**The held-out ✗ moved from 7 to 9:**
- One false alarm went away.
- Three appeared. Each claim used to be `?` for an unrelated reason (no resolvable period, or "reaching" read as a
  qualifier). Once that reason was removed, a scope word earlier in the sentence was not applied:
  - an adjusted figure's ", or $1.46 per diluted share" restatement;
  - a sentence-initial "Excluding Deliveroo,";
  - a segment subject before a ", with operating margin …" clause.

Round 10c (merged `3e2e4f4`) fixes those three classes. Its rules were designed after the held-out results were
seen, so the held-out figures above are the measurement. Re-checked at $0 after round 10c:
- `dev` is unchanged.
- `fresh` shows ✓ 457, ✗ 6 and `?` 740. This `fresh` result is not held out.
- Re-verifying both comprehensive runs gives no changed verdict.

**Disclosure.** After the round-10 fixes had been specified from `dev`, the fresh batch's possible-false-alarm list was
shown once in a working session. No round-10a or round-10b rule was designed or changed from it.

**Circularity.** The drafts are written from the same release sentences that the release-statement matcher reads, so
a release-statement ✓ here is partly circular. Filed-fact ✓ (10-Q/10-K XBRL, or the release's own table) is not.

**What the remaining `?` are.** On both batches, most remaining `?` fall into these causes:
- figure kinds NoCap does not track (adjusted EBITDA, Marketplace GOV, cash and marketable securities, share
  repurchases);
- guidance, which no filing can confirm yet;
- company-defined measures (organic, constant currency, non-GAAP with no release fact);
- periods the draft leaves unresolvable.

Each report lists every `?` by cause: [dev](ai_drafts_runs/dev/report.md), [fresh](ai_drafts_runs/fresh/report.md)
(the before runs).

**Writer errors.** Muse Wonder's own provenance errors were rare: 13 of 1,174 figures in `dev` and 5 of 1,201 in `fresh`
were not in the source. In `dev`, two of them received ✓, and both are right. The writer rounded or derived
figures the filings confirm: Air Products' revenue grew 4.6%, and the draft said 5%. No ✗ landed on a writer error, so
this benchmark does not measure catching: the planted-error tests do.

## Spend

| stage | dev | fresh |
|---|---:|---:|
| write (Muse Wonder) | $2.04 | $2.29 |
| check (Muse Spark) | $1.12 | $1.22 |

All re-checks after the fixes cost $0 (cached extraction, `--cached-only`).

## Limits

- One writer model and one prompt per style, writing from the release itself; human-written drafts and drafts from
  other sources may differ.
- Provenance compares values only. A `source_match` figure can still carry the wrong measure, period or basis, so
  every possible false alarm needs a human read; the round-10 analysis read each one in `dev`.
- Verdicts use the deployed evidence snapshot and code at the commits named above. The website's live results can
  differ after later fixes or data updates.
