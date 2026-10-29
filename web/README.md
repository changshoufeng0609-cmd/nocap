# NoCap web app

The NoCap UI: paste a draft, stream claims and verdicts from `POST /api/check`, and open the receipt (filing, accession, EDGAR acceptance time, verbatim cell) for every number. Next.js 16 App Router, TypeScript, Tailwind v4, shadcn/ui. The contract is `../docs/api-contract.md`.

## Run

```bash
npm install
npm run dev:mock     # mock mode: replays web/fixtures, no backend needed (http://localhost:3000)
npm run dev          # real backend: /api/* is proxied to http://localhost:8000/api/*
npm run build        # production build (add :mock to bake mock mode in: npm run build:mock && npm start)
```

- `NEXT_PUBLIC_RECEIPTS_MOCK=1` switches the browser to `/mock-api/*` (route handlers in `app/mock-api`). They return 404 when mock mode is off.
- `RECEIPTS_API_ORIGIN` changes the dev proxy target (default `http://localhost:8000`). The rewrite exists only under `next dev`; in production `/api/*` must be served by the deployed Python API.
- Only one `next dev` can run per directory.

## Mock fixtures

`fixtures/` holds the event sequences the mock replays, generated from the real CLI (`python -m receipts check - --json`) with cached Muse Spark responses only:

```bash
cd .. && MODEL_API_KEY= .venv/bin/python web/fixtures/generate.py
```

The empty `MODEL_API_KEY` makes a cache miss fail instead of spending. The script runs the CLI once per EDGAR acceptance time of the companies involved, so mock time travel is exact for any as-of. Mock mode only answers the bundled examples; any other draft gets an `error` event, which exercises the fallback.

## Layout

- `components/receipts-app.tsx`: state, submit (button or ⌘/Ctrl+Enter), examples, error fallback, desktop panel or mobile sheet.
- `components/highlighted-draft.tsx`: the checked draft. Claims can overlap or share a span, so the text is cut into flat segments. Each claim gets a focusable mark after its number: Tab walks the claims, Enter opens the receipt, ← → step and Esc closes.
- `components/receipt-detail.tsx`: claimed vs filed, reason, verbatim explanation, one receipt slip per evidence Fact.
- `lib/sse.ts`: the SSE reader (fetch + ReadableStream + TextDecoder; chunks may split anywhere).
- `lib/api.ts`, `hooks/use-check.ts`: request and stream state.

## What the API must match

- `POST /api/check` returns `text/event-stream` with `meta`, `claim`, `verdict`, `done` and `error` events, each `data:` one JSON line. Send `Cache-Control: no-cache, no-transform` so proxies don't buffer the stream.
- `span_start` and `span_end` index the request `text` exactly as sent, including its newlines.
- `verdict.expected_value` is in the claim's own unit and scale. It drives the inline correction ("$82.9 billion ✗ $81.273B").
- Errors before the stream starts may be non-200 JSON with `message` or FastAPI's `detail`. A 429 counts as retryable.
- The UI sends `as_of: null` while the picker is on "Now", so example requests hit the cache key of a plain check.
