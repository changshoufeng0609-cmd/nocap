# Qualifier robustness

**A qualified figure is never confirmed by evidence reported on another basis.** On a deterministic grid of
2,048 qualified variants of eight real figures, none verifies against GAAP evidence; the 16 unqualified controls
all verify. [Script](qualifier_robustness.py) · [result](qualifier_robustness.json) ·
[tests](../tests/test_qualifier_robustness.py)

## The grid

Eight real figures from the committed snapshot: NVIDIA revenue and diluted EPS, Meta revenue and net income,
Microsoft, Alphabet and AMD revenue, JPMorgan net income. Each value equals the filed GAAP figure at the draft's
precision, so a check that ignores the wording would confirm it. Each figure is wrapped in:

- 16 qualifying phrases: excluding acquisitions, foreign exchange effects, one-time items, special items,
  stock-based compensation, litigation charges or the impact of divestitures; before restructuring charges; in
  constant currency; on an FX-neutral, adjusted, non-GAAP, managed, pro forma, organic or underlying basis;
- 4 positions: leading ("On an adjusted basis, ..."), trailing ("..., on an adjusted basis."), inside the metric
  description, and parenthetical;
- the phrase in the model's metric description or only in the quoted sentence;
- the model's basis left empty or set to GAAP, the hard case.

Claims are typed directly, as extraction delivers them; there is no model, network or SEC call.

## Result (October 30)

| Outcome | Variants |
|---|---:|
| `?` unverifiable | 2,000 |
| ✓ against the company's own reported non-GAAP figure | 48 |
| ✓ against GAAP evidence | **0** |
| Unqualified controls verified | 16 / 16 |

The 48 are "on an adjusted / non-GAAP / managed basis" variants of NVIDIA's and AMD's revenue with no basis from
the model. Both companies list revenue in their 8-K non-GAAP tables (for example NVIDIA's "Revenue | $96,221"),
and that row is the receipt, so the claim as written is supported.

**Before this change** the same grid verified 300 variants: 240 against GAAP evidence, where the model had read a
figure as GAAP despite a basis phrase bound to it, and 12 organic, pro forma or "underlying basis" figures against a
non-GAAP figure that is a different measure. Two rules close both gaps without changing any of the 693
development-set verdicts:

- **A basis phrase bound to a GAAP-labelled number** ("on an adjusted basis" leading its sentence or following the
  number, or "(non-GAAP)") makes the basis unclear: `?`, reason `ambiguous`. A sentence that states both bases
  ("GAAP and non-GAAP gross margins were both 75.0%") and another figure's modifiers ("Adjusted EPS was $2.62, and
  revenue was ...") do not count.
- **Organic, pro forma and "underlying basis" figures** are company-defined measures that filings don't report
  under that name: `?`, reason `no_evidence`, whatever basis the model gave.

This closes the limitation recorded in the
[October 29 review closure](../docs/reviews/opus-review-20261029/fix-closure.md). The grid tests the verifier's
reading of wording around a number. It does not measure extraction, and it is not a sample of real drafts.
