# Model-spend protection

The application now records each attempted model request and keeps a conservative charge when usage is unknown. It reserves budget durably before dispatch and stops new paid work if the required ledger is unavailable. **This is estimated admission control, not a provider invoice or an absolute provider billing ceiling.**

## Deployment status

The owner approved a free Neon shared database and a new authenticated Preview deployment on 2026-10-28. The `nocap-spend` database is provisioned in `iad1`, its schema is initialized, and actual PostgreSQL validation passed **8/8 checks** with **0 model calls / $0 model spend**. The private Preview environment now selects the fixed primary pooled connection, namespace `nocap`, shared-required mode and a **$1/day shared admission allowance**. The Marketplace resource-to-project connection was disconnected after private retrieval of the connection details; the database remains, and the fixed URL is set manually as a Preview Secret to avoid automatic Preview database branching.

**Shared-hosted remediation is complete.** The current authenticated Preview runs source `778d5f8` at [this protected deployment](https://receipts-nocap-7mo0cmo4y-changshoufeng0609-cmds-projects.vercel.app), deployment `dpl_5gLPZiG8YfeyK8st19VZjTDKe6e3` (33-second build). The preceding `885a46f` release proved fail-closed behavior when the shared URL is missing, the deployed API's connection to the fixed primary database, and budget retention across a redeploy. All three model routes rejected work at the exhausted allowance with zero provider calls, while cached NVIDIA remained available. The current `778d5f8` release rechecked all three budget guards and passed seven hosted coverage/browser checks with no model calls. Desktop/mobile visual review passed. All operator test reservations were settled at zero; final primary readback was $0 spent and no pending reservation. The earlier `6ee8aba` Preview is historical and does not provide evidence of this protection. Public access is not authorized, and the shared allowance is not a provider billing hard cap.

## What changed

- Each SDK attempt has separate accounting, including text retries and Chat Completions format fallback. A failed attempt does not disappear when a later one succeeds.
- Missing, incomplete, negative, fractional, boolean or otherwise invalid token counts retain an estimate. A zero token-price self-hosted backend retains an estimate because token counts cannot establish its GPU bill.
- Each text estimate uses the encoded request size with framing allowance and the full output limit, with a $0.05 floor ($0.15 for Wonder). The estimate is based on configured prices, not a measured upper bound for every provider or tokenizer. Slide estimates remain $0.0025 per SAM call and $0.10 for Spark vision.
- The request-level reservation covers the planned attempts. A synchronized gate runs before every SDK call and rejects attempts beyond the reservation. A cache preflight hit cannot silently become an unreserved paid call if the cache disappears.
- A deadline freezes started-call accounting. Calls not started cost zero; started calls without known usage retain estimates. Late responses cannot refund a completed request or start another retry. Closing a stream also closes its dispatch gate before settlement.
- Check, Fix and Slide terminal events expose `cost_usd`, `cost_is_estimate` and `model_calls`. Errors include available accounting; browser errors with no accounting display **Model cost unavailable**, never an invented zero. Old recorded successes remain readable without the new optional fields.

## Ledger semantics

| State | Budget effect |
|---|---|
| Reservation accepted | Atomically debit the estimate and persist a unique token before model work. |
| All started usage known | Replace the reservation with observed usage priced at configured rates. |
| Every admitted attempt accounted, some usage unknown (a failed, timed-out or abandoned attempt, or missing usage) | Settle at the accounted cost: observed usage plus each unknown attempt's own conservative estimate. Planned attempts that never started are refunded. |
| An admitted attempt missing from final statistics (a worker died around the usage callback) | Retain at least the entire reservation; observed costs above it increase the debit. |
| Worker crashes or settlement cannot commit | The persisted reservation remains charged. |
| Worker cannot start | Settle the unused reservation at zero. |
| Duplicate identical settlement | No additional debit or refund. Conflicting settlement is rejected. |
| UTC midnight | New admissions use the new day's bucket; older tokens settle their original admission day. |

Amounts are nonnegative integer micro-USD, rounded upward for admission and settlement. Displayed model costs round to six decimals, so a ledger debit can differ from the displayed cost by about one micro-USD. When every attempt was accounted, the debit therefore matches the displayed cost, including each unknown attempt's estimate. Only when an admitted attempt went unaccounted is the complete reservation retained, and then the charge can be higher than the displayed estimate. Do not describe either value as a reconciled invoice.

Each unknown attempt's estimate is an upper bound computed from the request itself (UTF-8 bytes plus framing as input tokens, the full output limit, no cached-input discount), never less than $0.05 ($0.15 for Wonder; Slide uses $0.10 per Spark vision call and $0.0025 per SAM call). A Check whose one passage hits a 429, a 503 or the deadline therefore costs about $0.05 per started attempt, not its whole $0.30–$0.50 reservation. This settlement rule was adopted on 2026-10-30 (Meta API audit P1-2, option a); earlier releases retained the whole reservation whenever any usage was unknown.

There is no automatic timeout refund. Pending crash reservations remain in their original day; they are not moved to the next day or silently deleted. Changing the configured cap for an existing namespace/day is rejected, rather than allowing instances with inconsistent settings to race. The namespace and database must stay stable across deployments that use the same budget. Do not reset the namespace, delete budget rows or use automatically branched databases to reset spending allowance.

## Daily sub-caps

Two sub-caps share the same ledger tables and need no schema change. Both apply to every instance that uses the same ledger storage and namespace.

| Sub-cap | Setting (default) | Rule |
|---|---|---|
| Check and Fix reserve | `RECEIPTS_CHECK_FIX_RESERVE_USD` (`1.5`) | A Slide, Ask or whole-filing reservation is admitted only if it leaves this much of the day's cap unused. Check and Fix may use the whole cap. It only narrows admission (`reserve(..., keep_usd=...)`); the stored cap and every settlement are unchanged. With the release cap of $5, Slide, Ask and quote search can use at most $3.50 a day, and the last $1.50 stays with Check and Fix. |
| New whole-filing questions | `RECEIPTS_FILING_QUESTIONS_PER_DAY` (`5`) | At most this many new questions per UTC day. Each one is a one-micro-unit reservation in the namespace `<RECEIPTS_SPEND_NAMESPACE>.filing_questions` (for example `nocap.filing_questions`), whose day cap is the limit in micro-units; amounts in that namespace are counts, not dollars. A recorded sample replays at $0 and takes no slot. A question refused before its model call returns its slot; a dispatched question keeps it even if the provider fails, because it may still be billed. `0` turns new questions off. |

A refused request gets a one-event error stream with `model_calls: 0`, `cost_usd: 0` and a `reason`: `daily_budget` (including the Check and Fix reserve), `filing_question_limit`, `rate_limited`, `live_paused` or `budget_unavailable` ([api-contract.md](api-contract.md#errors)). Like the dollar cap, a different count limit for a namespace/day that already exists is rejected, so whole-filing questions fail closed (`budget_unavailable`) for the rest of that UTC day. Change the count limit, like the dollar cap, only before the first admission of a UTC day; the Check and Fix reserve is not stored and can change at any time. Offline tests (`tests/test_spend_subcaps.py`) cover the reserve boundary, the shared count across two app instances, slot return and keeping, UTC rollover, a changed limit and recorded-sample replay after the limit.

## Storage modes

| Mode | Scope and failure behavior |
|---|---|
| Local SQLite | Processes sharing the same durable file coordinate with transactions. The old `_spend.json` total is imported once, atomically, into `_spend.sqlite3`. Invalid legacy data fails closed. A file under Vercel `/tmp` is still instance-local and ephemeral. |
| Shared PostgreSQL | Every reservation/settlement locks the namespace/day row in a transaction. All instances using the same primary database and namespace share the allowance. Database/driver/configuration errors reject paid work; no local fallback. Runtime never creates tables. |
| Shared required but unconfigured | Paid Check, Fix and Slide are unavailable; recorded cached examples still work. This is the default when `VERCEL` is set. |

`RECEIPTS_REQUIRE_SHARED_BUDGET=0` permits local-only mode without a database URL. It is intended for local development or an explicitly controlled private environment, not unrestricted public paid access. Supplying a database URL always selects PostgreSQL; a broken URL does not fall back to SQLite.

<a id="prepared-cloud-setup--owner-approval-required"></a>

## Approved cloud setup

The approved NoCap-specific `nocap-spend` database uses the free `free_v3` plan in `us-east-1` / `iad1`, provisioned through the Vercel Marketplace [Neon integration](https://vercel.com/marketplace/neon/neon); Neon Auth and Realtime are disabled. No paid-plan upgrade or additional resource is authorized. The database stores only namespace, UTC day, random operation IDs and budget amounts/status, not drafts, images or model credentials. The following records the completed setup and validation; it is not an instruction to create a second database or request the same authorization again.

1. **Complete:** provision the approved database and use **one stable pooled primary connection** for all instances/deployments sharing this budget. The current setup manually stores the fixed URL and has no active Marketplace project connection that could automatically branch it. Keep the separate provider-side total-spend controls, if available.
2. **Complete for Preview:** configure `RECEIPTS_SPEND_DATABASE_URL` privately, `RECEIPTS_SPEND_NAMESPACE=nocap`, `RECEIPTS_REQUIRE_SHARED_BUDGET=1`, and `RECEIPTS_DAILY_BUDGET_USD=1`. Never print or commit the connection string.
3. Review the schema without connecting:
   ```bash
   .venv/bin/python scripts/init_spend_db.py
   ```
   Initialization is complete. For an approved future initialization, privately supply the same primary's **direct, non-pooled URL** to this process, then run:
   ```bash
   .venv/bin/python scripts/init_spend_db.py --apply
   ```
4. **Complete: 8/8 actual PostgreSQL checks**, detailed below. Validation uses dedicated random namespaces and no model calls. It does not test mid-commit connection loss or database failover.
5. **Complete: hosted API acceptance on protected Preview**, detailed below. Missing-ledger and exhausted-budget conditions block Check/Fix/Slide, cached NVIDIA still gives 7 verified / 2 wrong / 2 unverifiable, and the same primary reservation persisted across redeploys. The independent real PostgreSQL process checks cover concurrent admission. Public deployment remains a separate decision.

The application uses [Psycopg's binary package](https://www.psycopg.org/psycopg3/docs/basic/install.html) and PostgreSQL [row-level locking](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS). SQL errors and connection credentials never become public exception messages. TLS is required by the PostgreSQL adapter. Database access controls, availability, retention and the selected host's durability still need operational verification.

## Validation

Offline regressions cover per-attempt accounting, malformed usage, retries/fallback, deadlines, queued and late work, crash/restart retention, real local process contention, UTC rollover, duplicate settlement, damaged stores, migration, dispatch-gate closure and cached-only fallback. Frontend checks cover success/error cost propagation and reject malformed cost information. The latest full Python suite passed **809 tests**; guarded dev retained **104/109** caught errors, **97/109** reasons and **0/107** false alarms. Trap dev retained **40/40** caught errors, **36/40** reasons and **0/112 figures / 0/58 sentences** false alarms. New model calls/spend were **0 / $0**, with no held-out test run. See [the hand-back log](HANDOFF.md#hand-back-log) for the complete integration record.

### Actual PostgreSQL validation

The approved hosted database passed **8/8 checks** on 2026-10-28, finishing at **14:44:38 UTC**. Evidence: local `data/eval_runs/postgres_spend_validation.json`. Four independent processes competing for the final allowance produced **1 accepted / 3 denied**; both row-lock cases observed the intended blocker via `pg_blocking_pids()`, and timeout rollback produced SQLSTATE **`55P03`**. Concurrent duplicate settlement, unknown-usage retention, injected UTC rollover, crash retention and a nonexistent-database failure also passed. All cases verified integer conservation. These were synthetic ledger entries, **not model charges**, with **0 model calls / $0 model spend**. This validates the database adapter; the separate deployed API checks are recorded below.

After the approved database has been initialized, privately configure `RECEIPTS_SPEND_DATABASE_URL` in the environment or the repository's `.env`, then run:

```bash
.venv/bin/python scripts/validate_spend_db.py --run --output data/eval_runs/postgres_spend_validation.json
```

Without `--run`, the script only prints its plan; it does not read credentials or connect. The live run uses new random `nocap_validation_…` namespaces, never the application's budget namespace. It retains its small synthetic ledger records for audit and does not create databases, change the schema, delete rows, or call models. The JSON report contains safe status codes, random namespaces and integer amounts, never connection strings or raw exception messages. All amounts are synthetic budget entries, not provider charges.

Eight checks cover four independent processes competing for the final allowance, concurrent duplicate settlement, real row-lock blocking and release, the adapter's five-second lock timeout and transaction rollback, unknown usage, injected UTC date rollover, abrupt process exit after a committed reservation, and a connection attempt to a random nonexistent database on the approved host. The lock checks identify both backend sessions inside their transactions and verify the holder appears in [`pg_blocking_pids()`](https://www.postgresql.org/docs/current/functions-info.html); they do not assume a pooled connection keeps the same backend outside a transaction. The timeout check additionally requires SQLSTATE `55P03`, rather than treating every connection error as proof of a lock timeout. Every case reads its records through a fresh transaction and checks exact integer conservation between day charges and pending/settled reservations.

Exit code `0` means all checks passed; `1` means a validation case failed; `2` means configuration, access or report output blocked completion. Keep the JSON report as the evidence of an actual run. The script does **not** test model/API dispatch, database failover, or connection loss during commit. The rollover uses injected dates rather than waiting for midnight; the connection-failure check establishes adapter failure behavior. API fail-closed behavior is covered separately by regressions and the Preview checks below.

### Hosted shared-ledger validation

Three deployments of source `885a46f` used Preview targets with **All Deployments** authentication. Requests used a short-lived development OIDC header restricted to the exact deployment origin, with redirects disabled. No model was called.

| Stage | Configuration and evidence | Result |
|---|---|---|
| A — missing database URL | `dpl_PBkkVi5A5Hy3tSPJ59s2pCTkQTQT`, 35-second build; `neon_guard_missing.json` | Empty shared URL with shared-required mode: valid Check, Fix and Slide return `ledger_unavailable`, zero calls and zero cost. Cached NVIDIA returns 7 verified / 2 wrong / 2 unverifiable. |
| B — primary linkage | `dpl_2MLbC9x4PiC38Tq9bPUzZ8Fx2dKf`, 36-second build; `neon_guard_primary_proof.json` | A $3 estimated Check is rejected by the $1 cap before provider dispatch. The previously absent `nocap` / `2026-10-28` row appears in the primary with a 1,000,000 micro-USD cap, zero charge and no reservation. |
| B — exhausted allowance | Same deployment; `neon_guard_primary_B.json` | The operator reserves its own $1 synthetic hold. Check, Fix and Slide return `daily_budget`, zero calls and zero cost; cached NVIDIA remains available. The pending row and day charge are unchanged. |
| C — redeploy, same primary | `dpl_7vd7bnvFEVCGYapbALKWQcjtb4Cq`, 29-second build; `neon_guard_primary_C.json` | The same pending reservation and 1,000,000 micro-USD charge survive redeployment. All three routes still reject work; cached NVIDIA remains available. |

The four JSON files are local under `data/eval_runs/`. These checks incurred **0 provider calls / $0 new model spend**; the $1 hold is a synthetic budget reservation, not a model charge. The operator subsequently settled only its own two tokens at zero, including the renewed hold for the desktop visual rerun. Final primary readback showed **$0 spent / 0 pending reservations** (`neon_operator_reservation.json`). No other record was deleted or reset. The combined deployment, anonymous-access, C3 browser and final ledger results are summarized in `neon_shared_setup.json`.

The final cloud browser harness passed all **4 functional groups**, with **0 page/console errors**, two cached NVIDIA replays returning **7 verified / 2 wrong / 2 unverifiable**, and **0 provider calls / $0 new cost** (`neon_shared_browser_C3/result.json`). Desktop visual review confirmed the expected two-column layout at **1440 × 1050**, a matching desktop media query and **0 mobile sheet elements**; mobile review also passed. Anonymous health HEAD returned **302**. Earlier harness runs had blocked the external `vercel.live` toolbar or captured a closing-sheet transition; neither required an application change, and no deployment OIDC header was sent to the external origin. These validations did not rerun paid Check/Fix/Slide; earlier paid smoke results remain historical. No held-out split was run, and no public release was authorized.
