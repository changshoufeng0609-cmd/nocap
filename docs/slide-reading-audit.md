# Auditing overlapping slide readings

The vision model can report the same printed number in multiple overlapping chart/table regions.
Counting every returned reading independently inflates the result and can show both a verified and a wrong
interpretation over the same visual label.

The slide pipeline now reconciles the complete parsed result before publishing any verdicts:

- A visual location is considered coincident only when both label boxes cover at least 85% of each other.
  A large chart box containing a small label is not sufficient.
- The overlap graph includes every original reading before deduplication, so removing a middle copy cannot
  hide a conflicting overlap chain. Reconciliation applies to each connected group.
- Matching full transcriptions, printed value text, metric labels, normalized values, units, scale, metric,
  period, entity, accounting bases, comparison and reading type are deduplicated within the group.
  Uncertainty on either copy is retained.
- Conflicting interpretations of coincident labels remain separate claims, but every involved reading is
  marked uncertain and therefore receives `?`, never a conflicting pair of `✓` and `✗`.
- Equal numbers in distinct cells remain separate. A genuine shared GAAP/non-GAAP label still expands into
  its two accounting-basis claims. Final claim IDs are contiguous and counts match emitted verdicts.

The final SSE `done.reading_audit` reports `raw_readings`, `unique_readings`, `duplicates_removed`,
`conflicting_readings` and `image_completeness_measured: false`. Existing warning rendering exposes
deduplication/conflicts, the 32-reading limit and returned regions without any financial readings.

This audits the model's returned readings, **not all the numbers in the image**. It does not measure OCR
recall, establish correct visual grounding, or guarantee that a model-generated box contains the intended
number. Unmarked figures have not been checked. Printed reading validation and axis-estimate restrictions
remain in force; uploads and transcriptions never become filing evidence.
