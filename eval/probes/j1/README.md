# J1 regression drafts

The three original drafts from the 2026-10-27 J1 review, retained for repeatable checks. These are development probes, not held-out evaluation data. `results.json` records the corrected run using the original cached Spark extractions ($0).

| Draft | Verified | Wrong | Unverifiable |
|---|---:|---:|---:|
| AMD | 6 | 3 | 1 |
| Netflix | 5 | 2 | 0 |
| Amazon / Apple | 5 | 2 | 1 |
| Amazon / Apple, as of 2026-07-30 20:15 UTC | 3 | 4 | 1 |

Netflix's “more than $4.5 billion” and pre-split $5.87 both fail. Apple's June-quarter revenue and growth pass; $2.01 EPS is identified as the preceding quarter's figure. At the historical cutoff, all three Apple claims receive `look_ahead`. No correct claim in these probes receives a value-error verdict.

A snapshot-only temporary checkout also ran all three bundled examples without model calls, using the contexts and cutoffs in `data/snapshot/examples.json`. The NVIDIA CLI example works with inferred context as well.
