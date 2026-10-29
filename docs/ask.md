# Ask with receipts and whole-filing quote search

Ask converts a question into a limited financial lookup, then code writes and verifies each answer sentence.
Whole-filing quote search sends one complete, pre-ingested SEC document to Spark and accepts only exact,
uniquely located quotes. These are distinct operations: a matching quote proves where the words appear,
not the truth of the statement, its relevance to the question, or whether a quoted number matches XBRL.

## Numeric questions

`POST /api/ask` accepts JSON with `question` (1–1,500 characters) and optional ISO 8601 `as_of`.
It returns the same SSE framing as Check: `progress`, then `result` or `error`.

1. Standard `muse-spark-1.3` proposes only company, metrics, fiscal year/period, basis and segment.
   The strict schema has no answer, numeric value, URL, SQL or verdict field.
2. A separate deterministic guard matches recognizable English company aliases, metric phrases, explicit
   fiscal year/quarter and accounting basis back to the question. A model cannot invent missing years,
   swap metrics or silently reduce a multiple-company/period question to one. Unrecognized wording,
   advice, comparisons and mismatches require clarification. This narrow grammar favors withholding
   an answer; use explicit wording such as the example below. Parenthetical content and unknown words
   are retained: unbound qualifiers, an uncovered second company, arithmetic, or restrictions such as
   "net income attributable to common shareholders" cannot silently become an ordinary total. Code resolves that company through the
   evidence store and retrieves the latest version visible at
   the cutoff for the requested fiscal period. Missing periods, unknown companies, multiple accounting
   bases or conflicting equal-time values produce a clarification notice, not a guessed answer.
3. Code renders one sentence per fact and sends its typed claim through the existing deterministic
   verifier. Only `verified` sentences with evidence enter `answer`; other sentences remain visible
   in `items` with `withheld: true`. No second model writes prose around the accepted values.
4. The response includes `resolved_query`, every claim/verdict and deduplicated EDGAR footnotes. The
   interface should show the resolved company/period: the model can still misunderstand a question.

Supported questions cover one company and one fiscal quarter or full year, up to four listed metrics.
Comparisons, forecasts, investing recommendations, multiple companies/periods and unconstrained narrative
answers are outside this version. A fiscal period must be specified; there is no silent "latest" fallback.
`GET /api/ask/options` exposes the companies/periods present in the current store and example questions.

Example request:

```json
{"question":"What was NVIDIA's GAAP revenue in Q2 FY2027?","as_of":"2026-08-27T00:00:00Z"}
```

Successful result fields are `kind: "numbers"`, `question`, `as_of`, `resolved_query`, `items`, `answer`,
`footnotes`, `notice`, `model`, `latency_s`, `cost_usd`, `cost_is_estimate` and `model_calls`.
Each item contains `sentence`, `claim`, `verdict`, `withheld` and `footnote_ids`.
Each footnote has `id`, `accession`, `url`, `form` and `accepted_at`.

## Whole-filing quotes

`GET /api/grounding/filings` lists only pre-ingested documents. `POST /api/grounding` accepts `question`,
`filing_id` and optional `as_of`. There is no arbitrary URL, document upload or filesystem-path input.

The model receives the complete normalized filing text in one request, not a retrieval snippet. Code verifies
the local file's SHA-256, SEC URL/accession association and acceptance cutoff before any paid call. It reserves
the full request estimate, and rejects a request exceeding the conservative context bound before dispatch.
UTF-8 request bytes plus 4,096 framing tokens and the full 3,000-token output limit must fit below the documented
1,048,576-token context window. This bound is deliberately conservative; a token tokenizer or a character/4
guess is not treated as a safe upper bound. No silent document truncation occurs.

Spark can select up to five quotes, each 24–1,600 characters. Code rejects missing, repeated, duplicate and
oversized quotes. Accepted offsets are Unicode code-point positions in the normalized filing text, not raw HTML
bytes or JavaScript UTF-16 offsets. Quotes have no `verified` financial status. Readers must inspect context;
the model's selection can be irrelevant or incomplete, and quoted forward-looking statements remain forecasts.

Successful result fields are `kind: "filing_quotes"`, `question`, `as_of`, `filing`, `quotes`, `rejected_quotes`,
`notice`, `offset_unit`, `whole_document`, `model`, `latency_s` and the same billing fields.
Each quote contains only `text`, `start`, `end`; its source is the server-owned `filing` metadata.

## Ingesting complete documents

The operator imports a primary 10-Q or 10-K that is already registered in the evidence store. Import uses
`receipts.edgar.Edgar`: declared User-Agent, the existing cache, at most two live requests per invocation,
and the shared 2 requests/second limit. This step does not call a model.

```bash
.venv/bin/python -m receipts.grounding --ticker NVDA --form 10-Q --accession 0001045810-26-000075 --output-dir data/filing_texts
.venv/bin/python -m receipts.grounding --ticker META --form 10-K --accession 0001628280-26-003942 --output-dir data/filing_texts
```

Add `--offline` to require an existing EDGAR cache entry. The importer removes script/style/hidden iXBRL text,
collapses whitespace, and records hashes of both the original HTML and the normalized filing text. The API
reads `filing_texts/index.json` and hash-named `.txt` files beneath `Settings.data_dir`; include that directory
when rebuilding the deployable snapshot. Index entries and text must be deployed together. A missing index
produces an empty list; a damaged index or file fails closed.

## Accounting and integration

Install `receipts.ask_api.create_router(settings=settings, get_store=get_store, get_client=get_slide_client,
limiter=limiter, ledger=ledger)` in the existing FastAPI application. Reusing `get_slide_client` deliberately
selects Standard Meta Spark even when the draft extractor uses an open-weight backend. Ask/quote search does
not claim to run in private Glimmer mode; the question and selected public evidence go to Meta.

Both routes share the app's rate limiter and persistent spending ledger. Before starting the worker, the
route reserves `extract.estimate_request_cost` for the exact request. `RequestUsage` and `OperationBudget`
check each dispatch and settle known usage; unknown usage or failures after dispatch retain conservative
charges. A wall-time worker deadline seals the usage meter and withholds late responses, even if a provider
ignores the requested HTTP timeout. There is one attempt, no SDK retry, and `store: false`. The application does not cache question
text or generated quote responses. A disconnect does not bypass settlement.

The [structured-output documentation](https://dev.meta.ai/docs/structured-output.md) and
[model context limits](https://dev.meta.ai/docs/models.md) were checked on 2026-10-29: Responses schema
uses `text.format`, with reasoning in `reasoning.effort`. Official request
details are separate from local validation; a stub passing is not a successful live provider measurement.

## Validation

The dedicated offline tests cover factual rendering, fiscal/basis ambiguity, historical cutoffs, equal-time
conflicts, withheld verdicts, strict schema boundaries, per-attempt usage, missing usage/provider failures,
complete-question binding (including parenthetical restrictions and unknown entities),
budget and rate rejection, path/URL restrictions, file hashes, Unicode spans, duplicate/nonverbatim quotes,
context overflow and importer provenance. They do not run the held-out split or establish human relevance
judgments. Live smoke results, full-regression counts and deployment evidence belong in the current handoff.
