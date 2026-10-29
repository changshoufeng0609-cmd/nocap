# Ask and private-mode interface

The workspace has four keyboard-accessible tabs: **Draft**, **Slide**, **Ask**, and **Private**.
Left/right arrows cycle tabs, Home/End select the first/last, and the active tab has the only tab stop.
The strip scrolls horizontally on narrow screens. Each mode is mounted separately; switching away aborts
its request and discards its input/result state. A private draft is never copied into a Meta-backed mode.

## Ask

Ask contains two explicitly different operations:

- **Numeric answer:** asks Standard Spark for a lookup, then shows only the server's verified sentences.
  The interpreted entity, fiscal period, basis, segment and metrics are visible so users can check whether
  the question was understood. Withheld sentences are separate, struck through and explained; they do not
  enter the accepted answer or export. Source footnotes link to SEC documents with accession and acceptance time.
- **Filing quotes:** selects one complete, server-listed filing. The list includes the filing's acceptance
  time and normalized character count. Exact excerpts are displayed without financial verification badges.
  Unicode code-point offsets are identified as normalized-text positions, not HTML/page coordinates.
  Provenance does not establish relevance, entailment or financial correctness.

Both operations expose the historical cutoff, progress, cancel, error accounting and final model usage.
Editing any request field clears the previous result and cancels its stream. Mode changes and unmounts
also cancel pending work. Cancellation stops the browser from displaying stale output; work already admitted
on the server may still finish and incur charges. Configuration lists load without making model calls.

**Copy with sources** and **Download text** export the exact accepted sentences and numbered sources,
or exact quotations with their filing, offsets and quotation-specific limitations. Numeric exports include
the interpreted lookup and cutoff. They do not silently promote withheld sentences into the answer.

The browser client validates complete terminal accounting, question/cutoff identity, sentence offsets,
claim/verdict IDs, citation-to-evidence linkage, SEC URLs, acceptance times and exact answer reconstruction.
Quote responses must match the selected filing identity/hash/length, whole-document flag and code-point
lengths. Exact quote-to-document matching is performed on the server; the browser does not hold the full filing.

## Private

See [private mode](private-mode.md). The separate Private tab routes only to `/api/private/check`, never
retries via ordinary Check and keeps no draft in browser storage. Its unavailable state is explicit when the
operator's Modal Glimmer endpoint or credentials are missing. It does not claim a successful live setup merely
because the route and UI exist.

## Validation

`npm run test:ask` runs 17 offline client checks, including unsafe/mismatched citations, wrong cutoff,
unverified answer injection, Unicode offsets, withheld export, failure usage and cancellation.
`npm run test:private` runs 8 offline privacy-client checks. Existing Fix/presentation/Slide/cost/coverage
checks remain separate. These are controlled fixtures, not a live user study or fresh model-quality results.
