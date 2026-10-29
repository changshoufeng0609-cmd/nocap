# Review fix closure — October 29, 2026

This records how the [adjudicated October 29 findings](README.md) were fixed and checked.
The original [Claude report](claude-report.md), probe scripts and their adjacent `*_results.json`
files are unchanged; post-fix probe runs are in [`post-fix/`](post-fix/).

Fixes started as uncommitted Codex specialist patches (verifier, runtime and frontend) plus an
unfinished extraction patch. Actual Claude Code (Opus 5.5) then took over implementation,
integration and verification, as recorded in the [takeover checkpoint](../../claude-takeover-2026-10-29.md).
Baseline: private commit `b9bc045`, product baseline `d2296df`.

No provider, SEC or GPU call was made for these fixes or their checks: **0 model calls, $0**.
The held-out split was not rerun. Its one-time results describe frozen source `61e1055` and are
not measurements of this version.

## Outcome by finding

| ID | Status | Change | Evidence |
|---|---|---|---|
| R1 | Fixed | Comparison-period wording binds to the amount it follows, inside its clause | Post-fix correctness probe F2; verifier follow-up tests |
| R2 | Fixed | One shared currency-prefix rule (`$`, `US$`, `USD`) for bound recovery and reading | Post-fix bounds probe: 0/6 false ✓ (was 5/6) |
| R3 | Fixed | The complete source number decides value_text: digits, scale, sign, currency, enclosed parentheses | Post-fix F1 cases; extraction follow-up tests |
| R4 | Fixed | A change inherits its base metric's scope checks, including source-local qualifications | Post-fix F3 cases; verifier follow-up tests |
| R5 | Fixed, with residuals | Omitted source wording that changes the reading is restored into the quote | Post-fix F4 case; extraction follow-up tests |
| R6 | Fixed | Draft stays mounted across modes; Private still clears | Frontend lifecycle checks; browser acceptance below |
| R7 | Fixed | Callbacks and terminal states are guarded by run identity and abort | Post-fix product probe: 0 callbacks after abort (was 1) |
| R8 | Fixed | Check/Fix stop reading the body once its limit is crossed | Runtime follow-up tests |
| R9 | Fixed | Socket peer by default; forwarded identity only from configured trusted proxies | Runtime follow-up tests; post-fix runtime probe note |
| R10 | Fixed | Ledger admission runs off the event loop on all six paid routes | Runtime follow-up tests |
| R11 | Fixed | Demo hosting section now cites the deployed 165-second acceptance | [Demo kit](../../demo/README.md#hosting-boundary) |
| R12 | Fixed | Explicit local YoY/QoQ wording overrides omitted or conflicting metadata; conflicts return ? | Post-fix follow-up probe C3 |
| R13 | Fixed | "up to X from Y" returns ? instead of a strict upper bound | Post-fix follow-up probe C4 |
| R14 | Fixed | Pause precedes rate admission; paused Fix is disabled and explained in the UI | Runtime tests; browser acceptance below |
| R15 | Improved | Benchmark cards lead with accepted-subset numerator/denominator | Browser acceptance below |
| R16 | Fixed | Vercel defaults to snapshot-only caches; `/api/health` discloses the policy | Runtime tests; browser acceptance below |

## Validation

- **Python:** full suite **1,535 passed** (previous release baseline 1,246). New coverage:
  `tests/test_extract_review_followup.py`, `tests/test_verify_review_followup.py`,
  `tests/test_runtime_review_followup.py`, plus Fix grammar cases in `tests/test_rewrite.py`.
- **Frontend:** **165 client checks** (the 144 existing plus 21 request-lifecycle checks), typecheck,
  lint and a production build.
- **Local browser acceptance** (production build, real local API with live requests paused and model
  clients blocked, saved examples from cached extraction, labeled fixtures for in-flight, error, Fix-replay,
  Private and health-policy states): round 1 passed all 8 requested checks and found three defects
  (D1–D3 below); round 2, after their fixes, passed **29/29 scenarios** on desktop and 390px mobile.
  **0 provider calls, 0 non-local requests**, no page errors; every console/HTTP error came from a labeled
  503 fixture. Evidence: local `data/eval_runs/review_fix_browser{,_r2}/result.json` and screenshots.
- **Guarded development replay, dev split only:** cached extraction, model clients and sockets blocked,
  `--no-cache --budget 0`. **239 items, 292 cached responses, 0 model calls.**
  - v0: caught **104/109**, reasons **97/109**, false alarms **0/107**, original `?` **51/250**.
  - trap_v1: caught **40/40**, reasons **36/40**, false alarms **0/112 figures / 0/58 sentences**, `?` **1/112**,
    guidance **13/13** `?`.

  These equal the pre-fix development baseline exactly.
- **Per-claim comparison with the pre-fix code** on the same 239 development inputs: **693 claims,
  0 status or reason changes.** Four quotes gained source wording: the comparison period the source states
  (`... $2.8B in the prior year` twice, `... $44.1 billion a year earlier`) and `... on a F/X neutral basis`.
  Seven value texts now carry the source's own sign or unit (`+14%` three times, `+11%`, `2 percentage points`
  three times).
- **Saved examples:** **7/2/2, 5/3/0, 6/3/0**, with identical claims, quotes, verdict statuses, reasons,
  explanations, expected values and receipt accessions. The recorded 165-second demo therefore
  still matches current output for these examples.
- **Snapshot rebuilt with `--no-model`:** `facts.sqlite`, `manifest.json`, `examples.json` and every
  cached model response are byte-identical. The three recordings changed only their code fingerprint,
  creation time and local replay `latency_s`.
- **Frozen Q&A reproduction:** **200/200 records** replayed; every record and metric matches for all
  four backends.
- **Post-fix probes** (redirected copies; originals untouched): correctness **0/9** issue cases
  reproduced (was 7); US-dollar bounds **0** false ✓ (was 5); Claude follow-up **0/12** (was 7); the
  client probe observes **0** callbacks after abort (was 1). The runtime probe stops at its first
  assertion, which expected a fresh rate-limit identity per `X-Real-IP` value. It now observes the
  socket peer `127.0.0.1` with the allowance exhausted. Its body-size and event-loop sections did
  not run; `tests/test_runtime_review_followup.py` covers them.

## Independent review and browser findings

A separate code review of the runtime and frontend diffs found no blocker. Fixed:

- **R9:** a repeated `X-Forwarded-For` line was ignored (only the first line was read), so a trusted proxy that
  appends its own line could let a client choose its rate-limit identity. Repeated lines now join in
  order; a repeated `x-vercel-forwarded-for` falls back to the socket peer; IPv4-mapped IPv6 peers match
  their IPv4 networks.
- **R10:** if no worker thread can start, the unused reservation is now settled inline instead of left
  pending; a Private settlement failure now returns the private error event instead of a bare 500.
- **D1 (R6 side effect):** with Draft kept mounted, Slide's as-of label and hint pointed at Draft's hidden
  input (duplicate ids). Each picker now has its own ids.
- **D2:** a receipt the reader closed reopened on returning to Draft; auto-open now happens once per run.
- **D3:** after an interrupted check, unchecked claims kept showing "Checking". An interruption now keeps
  only claims with verdicts and says how far the check got.
- Draft's error and no-claims toasts close when Draft is hidden, so their actions cannot silently do nothing.

A separate adversarial review of the extraction and verifier diffs then traced concrete inputs through
the code. Fixed, each with a regression test in `tests/test_extract_review_followup.py`:

- currencies written with a space or by name ("C$ 96B", "€ 96B", "96B Japanese yen", "Rs. 96B") had been
  read as US dollars; "US $96B" and "U.S. $96B" had lost their strict bounds;
- scale words in title case ("$96.221 Million"), hyphenated or as bln/mln were invisible to the tokenizer,
  so a model's wrong scale won;
- a period label introducing the next amount ("..., a year ago it was $30B", "(a year ago: $30B)") dated
  the preceding amount;
- "the fourth quarter of last year" overrode a correct QoQ proposal as YoY, and comparison wording past a
  comma did too;
- "up to X compared with Y" was a strict bound, and "(106%)" a negative figure;
- restoration missed a named metric's own modifiers ("European net income", "U.S. revenue"), fronted
  qualifications ("Excluding stock compensation, ..."), qualifications after a comparison, constant-currency
  wording and attached direction words ("down 106%");
- a later repeat of the value inside a quote disabled the local checks, and a second claim on one number
  could bind to digits inside another;
- context searches scanned the whole passage. They now stay within the sentence; a 43,000-character,
  300-claim passage locates in about one second.

Reviewed and left as documented behavior: IPv6 clients are limited per address rather than per /64 on
non-Vercel hosts; a 503 health response hides the cache policy in the browser (it shows the conservative
wording); a reservation committed just before a client disconnects stays pending without refund, as the
fail-closed accounting requires; no idle-in-transaction timeout is set on the shared ledger; validation
toasts without actions remain until they time out. Extraction residuals: "$US96B", a scale word after a
closing parenthesis ("$(96.2) million") and spaced abbreviations (" M", " B") are not parsed. A model-stated
GAAP basis still wins over fronted "On an adjusted basis," wording.

## Integration findings

Two specialist patches had not been run against the development set. Integrating them exposed:

1. **The first R4 patch rejected 14 legitimate change claims** (v0 catch fell to 101/109, trap `?` to 6/112).
   The causes were change wording without a repeated metric name ("an increase of 33%", "the segment
   grew"), reporting verbs ("posting AWS revenue growth"), relative clauses ("that rose"), a subject after
   ", as", segment wording ("Gaming business revenue") and digits blanked inside "Q1". Each now has a
   targeted fix and a regression test. Explicit qualifications still return `?`.
2. **The first R5 patch widened every quote to its whole clause.** That broke five contract tests and,
   once applied, reduced v0 catch to 87/109: narrative such as "Jamie Dimon ... commented" or "as we
   deepened relationships" became unsupported qualifiers. The final rule restores only the metric's own
   modifiers, a qualification or a comparison, and keeps the model's quote whenever restoring would not
   change any verifier reading (`verify.span_reading`). The five contract tests pass unchanged.
3. **Complete source values met Fix's narrower grammar.** Once `value_text` carries the source's own
   "+14%", "($2.22 billion)" or "–$2.22", Fix would have stopped offering those wrong figures. Fix now
   accepts them, still only when every non-digit character, including the sign, stays unchanged.
4. **One sentence-break rule.** The verifier had treated the periods in "U.S." as sentence ends, so
   "U.S. net income" could verify against consolidated net income when the model's description omitted
   "U.S.". Extraction and the verifier now share `verify.sentence_breaks`.

An intermediate replay, `data/eval_runs/review_fix_{v0,trap_v1}_dev.json` (local), records the regressed
state. `review_fix_r2_*` and `review_fix_final_*` recorded the restored baseline before the adversarial
review fixes; the accepted record for the committed code (`ccac9c8`) is
`review_fix_final2_{v0,trap_v1}_dev.json`, with the same numbers. The wrapper is
`data/eval_runs/review_fix_guarded_dev.py`; none of these local files is committed.

## Commits

| Commit | Scope |
|---|---|
| `ad22299` | Runtime: bounded bodies, proxy boundary, off-loop admission, pause ordering, cache policy |
| `772bcc6` | Frontend: Draft lifecycle, superseded requests, paused Fix, retention copy, benchmark emphasis |
| `8285f84` | Verifier: R1, R2, R4, R12, R13 and the integration fixes |
| `06ff213` | Extraction: R3, R5, occurrence binding; Fix accepts source-complete values |
| `a9bb0ae` | Snapshot recordings rebuilt with `--no-model` |
| `e7e4f04` | Extraction/verifier fixes from the adversarial review |
| `ccac9c8` | Snapshot recordings rebuilt again with `--no-model` |

Each code commit's tree passed the Python suite on its own before the next was added.

## Residual limitations

- A qualification separated from its number by more than one figure, or stated in another sentence,
  still depends on the model's metric description.
- Modifiers cut from in front of a metric that the quote does name ("European" in "European net income
  was ...", quoted from "net income") are not restored. Both the quote and the metric description must
  then omit them.
- Development results are not held-out results; labels remain provisional, with no independent
  human adjudication.
