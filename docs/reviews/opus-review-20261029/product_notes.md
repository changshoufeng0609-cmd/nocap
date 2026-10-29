# Product review evidence — 2026-10-29

Independent AI code/document review; not a human audit or new browser acceptance.
Only the local source, existing release evidence and an offline synthetic client
probe were used. No product/test source was edited, no credential file was read,
and no provider, SEC, browser, API or held-out run was made. New provider spend: $0.

## P2: General Draft state is discarded when changing workflow tabs

- Primary location: `web/components/check-workspace.tsx:51`.
- The active workflow is rendered conditionally:
  `mode === "draft" ? <ReceiptsApp /> : mode === "slide" ? <SlideApp /> : ...`.
- `web/components/receipts-app.tsx:71-76` initializes draft, selected example,
  cutoff, result view and selection in component-local state. The Check hook is
  local at line 96; Fix is a child at lines 521-523. Returning to Draft remounts
  this subtree, and lines 165-175 load the default example again.
- Trigger: type or modify a draft (or finish Check/Fix), switch to Slide or Ask,
  and return to Draft.
- Impact: the in-progress general draft, custom cutoff and review results are
  lost without a save or warning. This is a deterministic consequence of the
  component structure, not a newly observed browser recording.
- Suggested repair: retain ordinary Draft state at workspace scope or preserve
  its mounted subtree with inactive controls. Private must remain separately
  cleared on exit; its documented privacy behavior is not the defect.

## P3: Current demo-kit hosting notes retain a pre-release checkpoint

- Primary location: `docs/demo/README.md:50-57`.
- The current section says the 165-second export's deployment and anonymous
  acceptance are separate future steps, then links only the historical
  179.937663-second release acceptance.
- Existing contrary evidence: `docs/public-release-update-2026-10-29.json:3`
  records `status: passed`; lines 8-9 bind final deployment
  `dpl_34X9vCXnCGf3SHSYTvs1uQezGKji` to public source
  `04ecc91bf9ee2362471359ba44d026775c82f8b7`. The current root README also states
  that the 165-second release passed anonymous playback/download checks.
- Impact: a reader following the main README's recording-notes link encounters
  an unresolved release-status contradiction. This finding is documentary; it
  does not dispute the accepted public release or the repaired caption default.
- Suggested repair: link and summarize current accepted release evidence in
  Hosting boundary, keeping the previous version explicitly historical.

## P2: Cancellation callback race — dynamic evidence

Run `node docs/reviews/opus-review-20261029/product_probe.mjs` from the repository
root. It compiles the existing TypeScript client in memory, replaces fetch with
a synthetic delayed HTTP 503 body, aborts while the error body is being parsed,
and writes `product_results.json`. Source hashes bind the exact reviewed files.

Primary location: `web/lib/api.ts:135-136`. The abort check precedes the awaited
error-body parser; no second check gates `onError` after parsing fails. Related
static evidence: `web/hooks/use-check.ts:102-105` dispatches and calls the error
callback without checking current run/abort; `cancel` at lines 111-114 does not
invalidate the run. In `receipts-app.tsx`, Edit and example changes call cancel,
but a late error can still render the prior error and toast; a superseded run can
still produce an old toast because callbacks are outside reducer run filtering.

Suggested repair: recheck cancellation after awaited parsing, guard all hook
callbacks with active run and signal, and invalidate the run on cancel. The probe
establishes the callback behavior only; the described UI consequence is static
code analysis and was not re-run in a browser.
