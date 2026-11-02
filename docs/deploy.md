# Deploy and run NoCap (updated 2026-11-01)

## Current deployment state

[NoCap](https://receipts-nocap.vercel.app), the [2:52 demo](https://receipts-nocap.vercel.app/demo)
and the [financial Q&A explorer](https://receipts-nocap.vercel.app/benchmarks) are public, and live checks are open for judging. <!-- verify-at-release -->
The [current acceptance record](public-release-2026-11-01.json) <!-- verify-at-release --> binds several things together:
- the source and the deployment;
- the real browser checks;
- the consented live smoke;
- the file hashes.

The [October 30 record](public-release-fix-2026-10-30.json) remains for the previous deployment.

| Setting | Current state |
|---|---|
| Project / team | `receipts-nocap` / `changshoufeng0609-cmds-projects`, Services / Node 24 |
| Production deployment | `dpl_2vPGGu27yA9Lx29ovmucC3BTwCvW`, READY, 56-second build from the GitHub commit; previous `dpl_DWzZqy43YFywgeQTrW5S9REN9jz3` kept for rollback <!-- verify-at-release --> |
| Deployed public source | `c99c9a0e68a65be47c91296107cdddbc0ed48515` (all 500 S&P 500 companies; private `42d48aa`) <!-- verify-at-release --> |
| Public entry points | `/`, `/demo`, `/benchmarks`, downloadable questions/model records |
| Protection | `prod_deployment_urls_and_all_previews`; the new generated deployment URL returns anonymous 302, while the Production domain is public |
| Live access | Check, Fix, Slide and Ask are open until 2026-11-17 16:00 UTC (Nov 18 00:00 Taipei). Saved examples, recordings and benchmark inspection stay at $0. <!-- verify-at-release --> |
| Shared admission | Existing free Neon primary, namespace `nocap`, US$5 per UTC admission day. Preview is paused with `RECEIPTS_LIVE_REQUESTS_ENABLED=false`, and the allowance was not reset <!-- verify-at-release --> |
| Model-quality evidence | [One-time held-out results](../eval/heldout_results.md), [separate four-model Q&A](../eval/qa_results.md) and the [one-shot comprehensive 500-company test](../eval/spot500_methods.md#results-of-the-one-live-run-november-2-2026-all-companies) |
| Development provenance | [65 reconstructed source-history commits](source-history.md); original private history unchanged |

**This deployment (November 1, all 500 companies).**
- **Health:** 291,112 facts for 500 companies, snapshot 2026-11-01. The first health response after READY took 1.36 s.
- **Anonymous browser acceptance:** passed 49 checks on desktop and 390px mobile with 0 provider calls. The three saved examples replayed 7/2/2, 5/3/0 and 6/1/2◷. The 2:52 video and its 26 captions matched their hashes.
- **Live smoke:** the consented run passed 50 checks. One live Check, Fix, Slide and Ask cost $0.0483 in 9 model calls.
- **Balance-sheet Check:** one live check, $0.0058, judged NVIDIA's total assets ✓, an inflated cash figure ✗ and stockholders' equity ✓.
- **Whole-filing question:** a repeated 10-K question reused 101,105 cached prompt tokens and cost $0.0186, against $0.1293 uncached.

**Previous deployment (October 29 media and benchmark update).** The base release `de534ca` / `dpl_CNhaupwwUJDBGcvsDxhpD6ML7UCe` passed six saved-example
checks, two zero-call Check/Ask pause responses, SEC receipt inspection, unavailable Private/whole-filing
states, media checks and the 200-record benchmark on desktop and 390px mobile. R4's five desktop POST
responses were preserved after a harness JSON-unwrapping error; R6 submitted only the three mobile
saved examples. R5 failed at sandboxed Chrome startup and made no new requests. No product HTTP,
console or page error occurred in the accepted runs.

The final deployment changes only the optional-caption default, footer wording and release provenance.
The verifier, snapshot, media and benchmark data are byte-identical to the base deployment. A separate
GET/HEAD-only follow-up passed eight checks across both viewports: initial native captions disabled,
muted playback starts, duration metadata 165 seconds, 18 optional English cues load, complete MP4/VTT
fetches match their exact hashes, 50 questions / four models / 200 records match local JSON, and no
horizontal page overflow. Four video GETs were cancelled during pause/navigation; complete subsequent
fetches passed. No POST was permitted in this follow-up.

These checks verify playback start, metadata, files and sampled frames, not full-video human listening
or editorial approval. Public acceptance added **0 model calls / $0 provider usage**. The local integrated
suite passed **1,246 Python tests**, **144 frontend checks**, typecheck and lint; the final Vercel production
build passed. Known batch model/GPU usage is **$3.30454604**, subject to delayed provider metering.
Whole-filing live validation was completed on 2026-10-30 ([record](ask.md#whole-filing-live-validation-2026-10-30)); formal submission remains unsubmitted.

## Runtime review follow-up (deployed October 30)

The runtime safeguards below shipped with the October 30 review-fix release. Production `/api/health`
reports `draft_cache_policy: snapshot_only` with live requests paused; anonymous acceptance replayed the
saved examples and both pause probes with 0 provider calls. Check/Fix now stop reading request chunks
at the body limit; all six paid routes offload synchronous ledger admission; paused requests consume
no per-client rate allowance. Vercel defaults to committed snapshot caches only, with the active
policy disclosed by `/api/health`. The source also defines an explicit forwarded-IP trust boundary.
See [draft retention](#draft-cache-policy-and-retention) and [proxy configuration](#trusted-proxy-boundary).
The release changed no environment variable or cloud configuration and deleted no historical cache.

## Historical first public release

The owner authorized the integrated public release. The [public source repository](https://github.com/changshoufeng0609-cmd/nocap)
and [licensed demo download](https://raw.githubusercontent.com/changshoufeng0609-cmd/nocap/main/docs/demo/assets/receipts-demo.mp4)
were verified through anonymous HTTP. Production URLs are [NoCap](https://receipts-nocap.vercel.app)
and [the demo player](https://receipts-nocap.vercel.app/demo). **Production anonymous desktop/mobile acceptance passed**
on deployment `dpl_7cezqJ72iLy18RW2GVTY8VMSCnNk`, source `30133131e48cfccb863c55e8bfd8562d49d73db5`.
Live model requests remain paused; visitors can inspect built-in saved examples and the captioned demo.
The [public release record](public-release-2026-10-29.json) summarizes the evidence and scope.

The repair explicitly includes `beautifulsoup4`, `lxml` and `httpx` in `pyproject.toml`. A fresh environment
containing only those declared API dependencies passed `/api/health`, `/api/examples`, `/api/ask/options`,
`/api/grounding/filings` and `/api/private/status` (all 200), an `lxml` HTML-parser check and `pip check`.
Model-client creation was forbidden during that validation; new provider usage was $0. The repaired
deployment subsequently passed anonymous browser/API acceptance.

| Setting | Current release state |
|---|---|
| Project / team | `receipts-nocap` / `changshoufeng0609-cmds-projects` |
| Production domain | `https://receipts-nocap.vercel.app`; `/demo` is the video player |
| Production deployment | `dpl_7cezqJ72iLy18RW2GVTY8VMSCnNk`, 47.1-second build, source `30133131e48cfccb863c55e8bfd8562d49d73db5`; anonymous acceptance passed |
| Deployment Protection | Standard Vercel Authentication: `prod_deployment_urls_and_all_previews`; generated deployment URL `ax7czigrj` and the older Preview `7mo0cmo4y` returned 302. The Production domain is anonymously accessible. |
| Live access | Initially paused. Saved examples and the video are the intended public experience pending the operating window; pausing does not reset the fixed shared ledger namespace or allowance. |
| Shared admission | Existing free Neon primary, namespace `nocap`, $1 per UTC admission day; conservative admission accounting, not a provider billing ceiling |
| Vercel MCP | Team authorization completed; read-only listing returned the intended team |
| Public source | Initial root commit `7856fe65536597b13a96723f54b83b1bc8187df0`, anonymously readable; original incremental history remains private |

Anonymous acceptance completed across R2 and its R3 continuation: R2 sent three desktop saved-example
checks and two uncached Check/Ask pause probes; R3 reused that evidence and sent only the three mobile
saved-example checks. All six replays returned expected verdicts and linked coverage **10/10, 8/8, 9/9**.
Both pause responses reported zero model calls and zero cost. Ask options loaded, the empty whole-filing
list kept quote search disabled, and unavailable Private mode stayed disabled and cleared input on unmount.
Desktop/mobile video playback started successfully; the player reported **179.937663 seconds** and loaded
**29 English caption cues**. Actual browser MP4/VTT downloads matched the committed hashes. Playback was
not observed for the full video duration, and this was not a human listening review. These are acceptance
observations, not independent model-quality tests. No new paid inference was requested.

Local pre-release validation passed 1,117 full-suite Python tests plus two later independent regressions,
144 frontend checks, typecheck, lint and a clean production build. Known batch model/GPU usage remains
**$0.96663691**; public-source and Production acceptance add **0 provider calls / $0 provider usage**.
See [completion status](completion-plan.md) for unresolved external work.

The first Production attempt, `dpl_C5sUWZbBUnHmgbNiPEfKJSyCT2mQ`, built in 37.6 seconds but returned 500
on anonymous health because the API runtime lacked `beautifulsoup4`. Its acceptance stopped before any
POST. Declaring `beautifulsoup4`, `lxml` and `httpx` explicitly fixed the thin API environment; that failure
is historical and does not describe the current Production deployment.

The release used Vercel MCP. The installed CLI is 65.5.0; the session reports 65.7.0 available. Before future CLI work, upgrading with `npm i -g vercel@latest` is strongly recommended for compatibility. No global upgrade was performed for this release.

## Historical protected Preview baseline

One Vercel project serves the Next.js app in `web/` and the FastAPI backend under `/api`, using
[Vercel Services](https://vercel.com/docs/services). The Next.js and FastAPI services have completed a real Vercel
Preview build; that build completed in about 33 seconds. At this historical checkpoint only an authenticated
private Preview was authorized. The broader public-release authorization and current status are recorded above.

**Shared spend protection is deployed and its hosted API checks passed.** The current authenticated Preview
runs `778d5f8`, with number-coverage reporting, conservative per-attempt accounting and the approved free Neon `nocap-spend` database.
It uses a fixed primary pooled Secret, namespace `nocap`, shared-required mode, and a **$1 allowance per UTC
admission day** across deployments using that database and namespace. The R1 shared-hosted remediation is
complete; this is estimated admission control, not an absolute provider billing ceiling or permission for public
access. The current release passed seven hosted browser checks and desktop/mobile visual acceptance.
Its operator test reservation was settled at zero; final primary readback was **$0 spent / 0 pending reservations**. See
[model-spend protection](spend-protection.md) for semantics and evidence.

The deployed number-coverage source passed **905 Python tests**, **119 frontend checks** (14 Fix, 18 presentation,
22 slide, 36 cost, 29 coverage), typecheck, lint and production build. Dev results stayed at **224 items, 104/109 caught,
97/109 correct reasons, 0/107 false alarms**; trap dev stayed at **15 drafts, 40/40 caught, 36/40 correct reasons,
0/112 true-figure and 0/58 true-sentence false alarms**. These rechecks made **0 model calls / $0 model spend**;
the held-out split was not run. The preceding shared-ledger release separately passed **8/8 actual PostgreSQL checks**; hosted budget guards
were rechecked on this release with **0 provider calls / $0 new model spend**. These results have distinct evidence records.

## Historical Preview configuration

| Setting | Current value |
|---|---|
| CLI | Vercel CLI **65.5.0**, installed globally and signed in as `changshoufeng0609-cmd` |
| Project / team | `receipts-nocap` / `changshoufeng0609-cmds-projects` |
| Project framework / Node | **Services** / **24.x**; repository root is the project root |
| Deployment Protection | Vercel Authentication, **All Deployments** (`ssoProtection.deploymentType: all`) |
| Hosted Preview guards | Shared PostgreSQL namespace `nocap`, `RECEIPTS_REQUIRE_SHARED_BUDGET=1`, `RECEIPTS_DAILY_BUDGET_USD=1`. Rate limiting remains per instance: `RECEIPTS_RATE_LIMIT_CHECKS=10`, default window 600 seconds. |
| Shared database | Dedicated free Neon `nocap-spend` (`free_v3`, `us-east-1` / `iad1`), fixed primary pooled connection stored as a Preview Secret; no automatic Preview database branching. |
| Hosted model key | The user added `MODEL_API_KEY` as a **Secret** for all Preview branches. Its value was not retrieved or displayed; the app was redeployed to receive it. |
| Agent setup | Followed the [official setup guide](https://vercel.com/get-started.md): global CLI installed, existing Vercel plugin enabled, shared `https://mcp.vercel.com` OAuth connected; documentation search works. |
| Vercel MCP | Team authorization is unfinished (`list_teams` returns no authorized teams). The authenticated CLI is available. |
| Private preview | **READY**, deployment `dpl_5gLPZiG8YfeyK8st19VZjTDKe6e3`, confirmed Preview target; build completed in about **33 seconds**, source commit `778d5f8` |
| Preview validation | Seven hosted browser checks passed: all three snapshot examples show coverage (10/10, 8/8, 9/9), mobile NVIDIA replay and edit/reset work. No browser errors. Check/Fix/Slide budget rejection passed with 0 model calls. The operator hold was released; final primary readback was $0 spent / 0 pending. |

Private preview: [Open NoCap with Vercel Authentication](https://receipts-nocap-7mo0cmo4y-changshoufeng0609-cmds-projects.vercel.app).

## Number-coverage hosted validation (historical source `778d5f8`)

The owner approved this protected Preview on 2026-10-29. Deployment metadata confirms source `778d5f8`,
`target: null` (Preview), **READY**, and **All Deployments** authentication. Both services built successfully
in 32.872 seconds. Anonymous `/api/health` HEAD returned **302** to Vercel sign-in.

Seven real-cloud browser checks passed with four exact cached requests: NVIDIA **10/10** candidates linked,
Meta **8/8**, roundup **9/9**, then NVIDIA on mobile **10/10**. Verdict counts remained **7/2/2**, **5/3/0**,
and **6/3/0** respectively (verified/wrong/unverifiable). Desktop/mobile layout and Edit draft clearing old
coverage passed, with **0 page errors / 0 console errors / 0 blocked requests**. These are real deployed
responses, with no fixture injection. Omission/zero-claim/malformed metadata and Fix coverage were separately
validated locally in the preceding release; no new paid Check/Fix/Slide was made here.

An operator-owned $1 reservation prevented accidental paid work during acceptance. Check, Fix and Slide
rejected uncached requests with `daily_budget`, **0 model calls / $0 cost**, while cached replay stayed
available. The same primary reservation persisted unchanged through the checks and was then settled at zero;
final readback was **$0 spent / 0 pending**. Authentication used a development OIDC header only for the exact
Preview origin; external assets received no token. Hosted model/database credentials, access settings and
database resources were not changed. The local development OIDC token was refreshed without displaying it.
No held-out test split was run.

Evidence: local `data/eval_runs/coverage_preview_metadata.json`, `coverage_preview_guard.json`,
`coverage_cloud_browser_R2/result.json` and screenshots, and `neon_operator_reservation.json`.

## Shared-ledger hosted validation (preceding source `885a46f`)

All three deployments below are Preview targets protected by **All Deployments** authentication.

| Stage | Deployment / build | Result |
|---|---|---|
| A: missing shared URL | `dpl_PBkkVi5A5Hy3tSPJ59s2pCTkQTQT` / 35 s | With shared-required mode and an empty database URL, valid Check/Fix/Slide requests returned `ledger_unavailable`, zero model calls and zero new cost. Cached NVIDIA remained available. |
| B: fixed shared primary | `dpl_2MLbC9x4PiC38Tq9bPUzZ8Fx2dKf` / 36 s | A draft estimated at $3 exceeded the $1 cap and was rejected before dispatch. The previously absent `nocap` day row appeared in the primary database with a $1 cap and zero charge, establishing the deployed API's database linkage. |
| C: redeploy, same configuration | `dpl_7vd7bnvFEVCGYapbALKWQcjtb4Cq` / 29 s | The operator's pending $1 reservation persisted from B to C. Both deployments rejected Check/Fix/Slide with `daily_budget`, zero calls and zero new cost, while cached NVIDIA returned 7 verified / 2 wrong / 2 unverifiable. |

The synthetic reservation is a deliberate admission hold, **not a model charge**. Its primary row and pending
reservation count stayed unchanged during B/C checks; no test reset the namespace or deleted ledger records.
HTTP validation used a short-lived development OIDC header restricted to the exact deployment origin, with
redirects disabled. Evidence is local in `data/eval_runs/neon_guard_missing.json`,
`neon_guard_primary_proof.json`, `neon_guard_primary_B.json` and `neon_guard_primary_C.json`.
All checks made **0 provider calls / $0 new model spend**; no held-out split was run.

The final browser run passed **all 4 functional groups**, with **0 page errors / 0 console errors**, two
cached NVIDIA replays at **7 verified / 2 wrong / 2 unverifiable**, and **0 provider calls / $0 new cost**.
Desktop visual review confirmed the expected two-column layout at **1440 × 1050**, a matching desktop media
query and **0 mobile sheet elements**; mobile review also passed. Earlier harness runs had unnecessarily
blocked the external `vercel.live` toolbar or captured a closing-sheet fade transition; neither required an
application change. Authentication headers stayed restricted to the deployment origin. Anonymous health HEAD returned **302**. Final evidence: `data/eval_runs/neon_shared_browser_C3/result.json`.
The operator settled only its own two synthetic reservations at zero, including the renewed hold for the visual
rerun. Final primary ledger readback was **$0 spent / 0 pending reservations**. Evidence:
`data/eval_runs/neon_operator_reservation.json`; the deployment, anonymous-access, C3 browser and final ledger
results are also summarized in `data/eval_runs/neon_shared_setup.json`.

## Historical Preview evidence (before shared-ledger deployment)

The historical NoCap branding refresh was deployed from clean source commit `6ee8aba` to
`dpl_GVdjYGRaBKiQjNBVDaaSnaQ4xr25` (32-second build). It did not have the current shared-ledger protection.
The browser verified the new heading
and page title, health **11,175 facts / 10 companies**, all **3 examples**, and cached NVIDIA checks on desktop
and 390 px mobile (**7 verified / 2 wrong / 2 unverifiable**). Filing receipts, mobile receipt sheet, Draft/Slide
switching and slide upload/preview passed, with **0 console/page errors**. Paid operations were blocked by the
acceptance harness; there were **0 provider calls / $0 new model spend**. Anonymous `/api/health` still returned
**302**, and the project readback retained **All Deployments** protection. Local evidence:
`data/eval_runs/nocap_preview_browser/result.json` and adjacent screenshots. No public production release occurred.

The initial keyless Preview's authenticated CLI checks returned a healthy snapshot with **10 companies / 11,175 facts** and **3 examples**.
The NVIDIA example replay sent `meta` with `cached: true`, **11 claims and 11 verdicts**, then `done` with
**7 verified / 2 wrong / 2 unverifiable**. These checks incurred **$0 new model spend**. An unauthenticated request
to the preview's `/api/health` returned **302** to authentication; `https://receipts-nocap.vercel.app` returned **404**.
A project readback confirmed Services, Node 24.x, and All Deployments protection remained unchanged.

The initial Preview's real browser checks, using an existing short-lived development OIDC header scoped to its exact origin,
passed on desktop and a 390 px mobile viewport: cached NVIDIA checks, receipt details/calculations, the mobile
receipt sheet, Draft/Slide switching, and local slide upload/preview. Responses came from the deployed services,
with no mocks, provider calls, console errors, or page errors. Tokens and browser authentication state were not
saved. Local evidence: `data/eval_runs/vercel_preview_browser/result.json` and adjacent screenshots.

After the user configured the Secret, the earlier keyed Preview `dpl_DLEVNMyDYNbWDua1bsgmMFLg6wag`
was deployed from application commit `ccbfb25` (27-second build, before the NoCap rename).
Its health endpoint again returned **11,175 facts / 10 companies**, and unauthenticated access still returned
**302**. Three real browser operations each made exactly one application request, with no test-runner retry:

| Live cloud operation | Result | API latency | API-reported new cost |
|---|---|---:|---:|
| New NVIDIA paragraph, Spark extraction | 1 verified / 1 wrong, `cached: false` | 3.63 s | $0.005590 |
| Wonder Fix with receipts, followed by full recheck | GAAP diluted EPS `$2.22` → `$2.46`; 2 verified / 0 wrong | 3.84 s | $0.009324 |
| Seeded NVIDIA image, SAM + Spark | 4 readings: 2 verified / 2 wrong; both basis and period errors caught | 8.06 s | $0.014560 |

Total API-reported new Meta cost was **$0.029474**, not an independently reconciled provider invoice.
The slide reported 3 model calls and `cost_is_estimate: false`; Check and Fix did not expose that flag in this
historical build. Current source now includes it on both successful and failed operations with accounting.
SAM found no matching regions in this seeded image, and the UI correctly identified the region as a Spark
fallback. Image-grounding boxes remain approximate. All three responses reached successful terminal events,
and browser console/page errors were **0**. Complete SSE responses, a request journal, the usage summary and
reviewed screenshots remain local in `data/eval_runs/vercel_live_browser/`. No credentials or browser auth
state were saved. The full **658-test** Python suite, **54** frontend checks, typecheck and lint passed; both
dev evaluations retained their prior results with **$0** evaluation spend. The held-out test split was not run.

The first real application deployment was unexpectedly assigned to Production despite an explicit Preview request
(deployment ID prefix `dpl_7Trq`). It built successfully, had no hosted model key, and was deleted. It is not a
current preview or a production release.

A separate bootstrap deployment, `dpl_DUZzueYAcSg5KABWfmv4NYFfhNH9`, is retained in a failed state. Its upload
contained only `vercel.json` and a private `package.json`, with no application, evidence data, or secrets. Its build
command was `exit 1`, the deploy requested `--skip-domain`, and All Deployments authentication was enabled.
Inspection still listed `receipts-nocap-changshoufeng0609-cmds-projects.vercel.app` as an alias, so the flag must not
be described as proof that no alias exists. The failed record does not serve NoCap and permits subsequent
deployments to use Preview.

## Layout

| Piece | File | What it does |
|---|---|---|
| Services and routing | `vercel.json` | `web` service: `web/` (Next.js). `api` service: the repo root `.`, with entrypoint `receipts.api:app`. `/api/*` goes to `api`; everything else goes to `web`. |
| Python dependencies | `pyproject.toml` | Runtime dependencies include fastapi, openai, pydantic, python-dotenv, Pillow for slide decoding, beautifulsoup4/lxml for filing text, httpx for HTTP requests, and `psycopg[binary]` for the optional shared ledger. In the repo root this takes precedence over `requirements.txt`, which remains the local dev and test environment. The production repair adds the filing parser packages and declares httpx explicitly. |
| Python version | `.python-version` | `3.12`, the same as `.venv`. |
| Function limits | `vercel.json` → `services.api.functions["receipts/api.py"]` | `maxDuration: 300`. The API's own overall deadline is 100 s for a check, fix, or slide request. `excludeFiles` keeps `web/`, tests and tooling out of the Python bundle. |
| Upload filter | `.vercelignore` | `vercel deploy` from this machine uploads no `.env`, no `.venv`, and nothing under `data/` except `data/snapshot/`. A Git deploy sees only committed files. |
| Data | `data/snapshot/` | The read-only evidence (`facts.sqlite`, `pr_facts/`), `manifest.json`, the pre-baked example streams (`api_cache/`), the examples' Muse Spark responses (`spark_cache/`), and `examples.json`. It is the only part of `data/` that is committed. |

How it fits together:

- **Path prefix.** The `api` service receives the original path (`/api/check`), and FastAPI declares its routes with
  the `/api` prefix, so no path rewriting is needed.
- **Routing is final.** Once a request is routed into a service, it stays there. Next.js must not define its own
  `/api/*` routes; its mock API lives at `/mock-api/*`.
- **Bundling.** A Python function bundles every uploaded file under its service root, so `data/snapshot/` ships
  automatically.
- **Data directory.** `RECEIPTS_DATA_DIR` defaults to `data/` when `data/facts.sqlite` exists, else `data/snapshot/`.
  On Vercel only the snapshot is uploaded, so the snapshot is picked without any setting.
- **Evidence is read-only at runtime.** The snapshot SQLite is opened read-only. The current source defaults to
  no ordinary draft response-cache reads or writes on Vercel; committed snapshot caches remain readable.
  Explicitly enabling draft persistence uses instance-local `/tmp/receipts` paths unless overridden.
  The separate local ledger uses transactional `_spend.sqlite3`
  with one-time `_spend.json` migration; a file under `/tmp` remains ephemeral and cannot share an allowance
  across instances. Current Vercel source requires `RECEIPTS_SPEND_DATABASE_URL` by default; unavailable shared
  storage disables paid operations while cached examples continue. Runtime never creates PostgreSQL tables.

## First-deployment exception

Vercel's [first-deployment rule](https://vercel.com/docs/deployments/environments#first-deployment) assigns the
first deployment of a new project to **Production**, even with `--environment preview` / `--target=preview` or a
non-production branch. Changing the flag does not bypass this rule.

- A retained failed, errored, or canceled deployment counts as the first deployment.
- Deleted deployments do not count. Deleting every deployment causes the next one to become Production again.
- A request rejected before a deployment is created does not count.
- The first deployment uses Production environment variables, not Preview-only values.

Keep the failed bootstrap record above. For a new or emptied project, stop before uploading the application and
obtain approval for an explicit initialization plan; do not treat a production-target bootstrap as a preview.
`--skip-domain` prevents automatic domain assignment but does not change the deployment target.

[All Deployments protection](https://vercel.com/docs/deployment-protection#all-deployments) is available on all
plans and protects generated URLs and production domains. Preserve `deploymentType: all`. In CLI 65.5.0,
`vercel project protection enable receipts-nocap --sso` sets `prod_deployment_urls_and_all_previews`, so it must not
be used to replace the stronger existing setting.

## Deploy an authorized private preview

Run commands from the repository root. The CLI is already installed and this checkout is linked to the existing
project. On another machine, sign in and link to **receipts-nocap** in the team above; do not create a replacement
project. Confirm Services, Node 24.x, the repository root, and All Deployments protection before continuing.

1. **Refresh the snapshot** after evidence or verifier changes, then commit `data/snapshot/`:
   ```bash
   .venv/bin/python scripts/build_snapshot.py --no-model
   ```
   Run the full Python tests and dev evaluations appropriate to the change. Never run the held-out test split
   without the user's explicit request.
2. **The user adds the Preview model key interactively.** Do not place its value in a command, chat, file, or log:
   ```bash
   vercel env add MODEL_API_KEY --environment preview
   ```
   The user completed this step on 2026-10-28; do not add the variable again. Retain the chosen $1 UTC admission-day
   budget and rate limit of 10 live operations per 600 seconds. Use the same `--environment preview` syntax
   for any additional Preview variables. Environment changes require a new deployment. See the
   [CLI environment guide](https://vercel.com/docs/cli/env).

   `RECEIPTS_DATA_DIR=data/snapshot` is optional because the uploaded snapshot is selected automatically. The
   default text backend remains Meta; slides and Fix with receipts also require the Meta key.

   Keep `RECEIPTS_PERSIST_DRAFT_CACHES` unset or `false` for the default Vercel snapshot-only policy.
   Supplying cache-directory overrides alone does not enable persistence. Review the health policy
   and [proxy boundary](#trusted-proxy-boundary) when validating an authorized new deployment.

   **The shared database and private Preview setup are approved and complete.** Preserve
   [the existing shared configuration](spend-protection.md#approved-cloud-setup): the private
   `RECEIPTS_SPEND_DATABASE_URL`, stable `RECEIPTS_SPEND_NAMESPACE=nocap`, and
   `RECEIPTS_REQUIRE_SHARED_BUDGET=1`. Use the same primary database across instances and relevant deployments;
   do not create a replacement or reset the allowance through automatic Preview database branches. Without it, the deployment
   intentionally serves cached examples but rejects paid work. Setting `RECEIPTS_REQUIRE_SHARED_BUDGET=0`
   permits local-only mode solely for an explicitly controlled private environment. A broken configured URL
   never falls back. Provider-side spending controls remain separate; notifications are not hard ceilings.
3. **Deploy explicitly to Preview** while the existing bootstrap record remains:
   ```bash
   vercel deploy --yes --environment preview --no-wait --json
   ```
   Inspect the returned deployment before treating it as a preview: Vercel's API represents Preview as
   `target: null`; `target: "production"` is not acceptable for the application. Wait for `READY` and review both
   service build results. A successful smoke test does not authorize promotion, `--prod`, or a Git production
   deployment.
4. **Smoke-test with Vercel Authentication.** In a signed-in browser, verify `/api/health` reports `ok: true`,
   the snapshot fingerprint and 500 companies; `/api/examples` returns the bundled examples; and the homepage can
   replay a cached example through its final `done` event. A signed-out browser must receive the Vercel login
   gate, including for `/api/health`. Plain unauthenticated `curl` returning login HTML is not an API failure.

   The authenticated CLI can also target the exact preview and handle Deployment Protection:

   ```bash
   PREVIEW_URL='https://receipts-nocap-7mo0cmo4y-changshoufeng0609-cmds-projects.vercel.app'
   vercel curl /api/health --deployment "$PREVIEW_URL"
   vercel curl /api/examples --deployment "$PREVIEW_URL"
   ```

   For an SSE replay, select the complete bundled NVIDIA example in the UI, or send that exact example text to
   `/api/check` using `vercel curl` and its curl arguments after `--`. A shortened or edited passage may require a
   live model call. Keep any automation bypass secret out of commands, chat, recordings, and logs; do not disable
   Deployment Protection to run a smoke test. See [vercel curl](https://vercel.com/docs/cli/curl).

   Without a hosted key or the required shared ledger, only health, examples, and cached replays can pass.
   The three bounded live checks above belong to the earlier keyed build. The `885a46f` shared-ledger release
   passed fail-closed and real database checks, including persistence across a redeploy. Current `778d5f8`
   additionally passed hosted coverage replay and budget rejection with no model calls. Any paid smoke needs a separately bounded, authorized run; record usage,
   terminal events and browser results. Read the specialized contracts in
   [Fix with receipts](fix-with-receipts.md) and [Check a slide](check-a-slide.md). Do not report local live results
   as hosted validation.

## Local development

Complete the [README's Python setup](../README.md#quickstart) first. Run the API
and frontend in separate terminals, both starting at the repository root.

Terminal 1 — API:

```bash
.venv/bin/uvicorn receipts.api:app --port 8000 --reload --no-proxy-headers  # API on http://localhost:8000 (docs: /api/docs)
```

Terminal 2 — frontend (Node.js 20.9+ and npm required):

```bash
npm --prefix web ci
npm --prefix web run dev -- --port 3100
```

Open `http://localhost:3100`. The bundled examples use the committed snapshot and cached model responses;
checking a new draft requires the model credentials described in the README or [backend guide](open-weights.md).

`web/next.config.ts` proxies `/api/*` to `http://localhost:8000` under `next dev`. Set `RECEIPTS_API_ORIGIN` to point
it at another port. `MODEL_API_KEY` comes from `.env`, which `receipts.extract` loads. Locally the API shares the
CLI's model-response cache in `data/cache/spark/`, so drafts already checked with the CLI cost nothing. Recorded checks
go to `data/api_cache/` (gitignored), alongside the local `_spend.sqlite3` ledger. A valid old `_spend.json`
total is imported once; malformed legacy data disables paid work rather than resetting the total. Outside
Vercel, local SQLite is the default unless shared storage is configured or explicitly required.
Ordinary local caches have no application TTL. Set `RECEIPTS_PERSIST_DRAFT_CACHES=false` to read only
the committed snapshot caches while retaining the existing local spend ledger. API settings are read
from the process environment at startup: export them before starting Uvicorn, or add `--env-file .env`
to load an existing local file. Merely adding an API setting to `.env` is insufficient; the extractor's
later credential loading does not rebuild the API settings. The tracked template is [`env.example`](../env.example).

As an alternative, `vercel dev -L`, run from the repo root, starts both services the way Vercel will.

To measure real, uncached latency, run `RECEIPTS_NO_CACHE=1 .venv/bin/uvicorn receipts.api:app --port 8000 --no-proxy-headers`. Every
check then calls the model.

## Draft cache policy and retention

`RECEIPTS_PERSIST_DRAFT_CACHES` defaults to `false` when the process runs on Vercel and `true` locally.
When explicitly set, only `true` (case-insensitive, surrounding whitespace ignored) enables persistence.
Restart the local API or use an authorized new deployment after changing settings.

| Configuration | Ordinary Check/Fix response caches | Saved examples |
|---|---|---|
| Persistence disabled, including the Vercel default | No reads from ordinary draft caches and no new extraction/event-cache writes. Directory overrides do not opt in. | Read only `data/snapshot/api_cache` and `data/snapshot/spark_cache`, regardless of a custom evidence data directory. |
| Persistence enabled, including the local default | Read/write the configured API and Spark cache paths; default local paths are `data/api_cache` and `data/cache/spark`. Vercel opt-in defaults to `/tmp/receipts/api_cache` and `/tmp/receipts/spark`. | Snapshot and configured data-directory caches remain available. |
| `RECEIPTS_NO_CACHE=1` | No response-cache reads or writes, even when persistence is enabled. | No cache replay; model admission is required. |

Persistent extraction and event caches can contain draft fragments and model output. They have no
application TTL and remain until the operator removes them or the host removes the storage. Disabling
persistence stops using ordinary caches; it does not erase historical files or establish their host
retention. This change does not inspect or delete historical ordinary draft caches.

The response-cache policy does not reset accounting. Local `_spend.sqlite3` and its one-time legacy
`_spend.json` migration keep the same directory selected by `RECEIPTS_API_CACHE_DIR` (or its previous
default), even when response-cache writes are disabled. Shared PostgreSQL keeps its existing database,
namespace and stored charges. Ledger records contain operation IDs and amounts, not draft content.

`GET /api/health` reports `draft_cache_policy`: `snapshot_only`, `persistent`, or `disabled`.
These describe application response-cache behavior, including when health returns HTTP 503 for unavailable
evidence. The browser shows the policy before submission. This does not promise offline processing or
control the API host's, inference provider's or operator-managed Modal service's retention. Private mode
keeps its separate [no-response-cache boundary](private-mode.md).

## Trusted proxy boundary

Direct requests use the ASGI socket peer for rate limiting. Ordinary `X-Real-IP` and
`X-Forwarded-For` headers do not establish a trusted caller by themselves.

On the Vercel runtime, the application accepts only a single valid IP in `x-vercel-forwarded-for`.
Vercel documents its platform forwarding headers and overwrite protection in the
[request-header reference](https://vercel.com/docs/headers/request-headers). Missing, malformed or
multi-value platform headers, including a repeated header line, fall back to the socket peer; there is
no fallback to client-supplied `X-Real-IP` or ordinary `X-Forwarded-For`. Vercel's reference does not
explicitly state that a client-supplied `x-vercel-forwarded-for` is removed; this was not probed on a
deployment. The `VERCEL` process environment selects this platform boundary;
do not set it on an independently exposed server to pretend that Vercel is authenticating requests.

For another reverse proxy, explicitly set `RECEIPTS_TRUSTED_PROXY_CIDRS` to comma-separated IPv4/IPv6
networks for the proxy socket peers, for example `127.0.0.1/32,::1/128` only when those local proxies are
trusted. They must append the connection they actually received to `X-Forwarded-For`. The application
joins repeated `X-Forwarded-For` lines in order (a proxy may add its own line), walks the chain from right
to left and stops at the first untrusted hop, ignoring spoofed prefixes. IPv4-mapped IPv6 peers such as
`::ffff:127.0.0.1` match their IPv4 networks. An invalid CIDR configuration disables this forwarding trust;
malformed addresses fall back to the peer. IPv6 clients are limited per address, not per /64 prefix.

The ASGI server must preserve the real socket peer for the application to apply that rule. The local
command uses Uvicorn `--no-proxy-headers`. If server-level proxy handling is used instead, restrict its
`--forwarded-allow-ips` to the same trusted boundary; never use unrestricted `*` for an exposed server.
Once an upstream server has replaced `scope.client` using untrusted input, the application cannot recover
the original connection. With the default local Next.js proxy and no explicit trust, local requests
share that proxy's allowance. Client IPs are not logged; rate limits remain per process/instance.

## Guards

| Guard | Setting | Behavior |
|---|---|---|
| Text request limits | `/api/check` and `/api/fix`: 12,000 characters, 64 KiB body | Read chunks only while the accumulated body fits; reject before retaining the crossing chunk or consuming the remainder. Invalid requests receive a terminal `error` event without a model call. |
| Image request limits | `/api/slide`: 3 MiB JSON body; 2 MiB PNG/JPEG | Images must be single-frame, 32–4,096 pixels per side, and at most 8 megapixels. Pillow checks dimensions before decoding, applies orientation, strips metadata, and reduces the longest side to 2,048 pixels. Images are not stored. |
| Per-instance per-IP rate limit | Preview: 10 live operations per 600 seconds | Check, Fix, Slide, Ask, whole-filing quotation and Private share one limiter. Paused requests consume no allowance. Rejection sends a retryable `error`; identity follows the trusted proxy boundary above. |
| Durable estimated spend admission | `RECEIPTS_DAILY_BUDGET_USD`: Preview $1 per UTC admission day; judging-window release $5; code default $5 | Precharge planned attempts before work; check every SDK dispatch against that reservation. Text estimates use request size plus the output ceiling, with a $0.05 floor ($0.15 for Wonder); Fix covers both extraction passes and Wonder. An attempt with unknown usage settles at its own estimate; only an attempt missing from the request's accounting retains the whole reservation. Actual costs above it increase the debit. See [full semantics](spend-protection.md). |
| Check and Fix reserve | `RECEIPTS_CHECK_FIX_RESERVE_USD`, default `1.5` | A Slide, Ask or whole-filing reservation must leave this much of the day's cap unused; Check and Fix may use the whole cap. A refusal is an `error` with `reason: "daily_budget"`, zero calls and zero cost. Not stored, so it can change at any time. |
| Whole-filing question limit | `RECEIPTS_FILING_QUESTIONS_PER_DAY`, default `5` (`0` turns new questions off) | New whole-filing questions per UTC day across all instances, counted in the same ledger storage under `<RECEIPTS_SPEND_NAMESPACE>.filing_questions` with no schema change. Recorded sample questions take no slot; a question refused before its model call returns its slot. A refusal has `reason: "filing_question_limit"`. Like the dollar cap, change it only before the first admission of a UTC day; a mid-day change fails closed until the next day. See [daily sub-caps](spend-protection.md#daily-sub-caps). |
| Ledger storage | SQLite locally; shared PostgreSQL required by default on Vercel | Processes sharing a SQLite file coordinate transactionally. PostgreSQL locks each namespace/day row across instances. Admission runs in a worker thread so database waits do not block the ASGI event loop. Missing driver/database, invalid configuration or failed transactions reject paid work without local fallback. Crashes retain pending precharges. |
| Check model calls | Default Meta: 30 s per HTTP attempt, at most one retry | Only eligible rate-limit/server/connection failures retry. A `Retry-After` longer than 4 s is not retried: the retryable `error` says how long Meta asked NoCap to wait and carries `retry_after_s`. The operation has a 100 s deadline. |
| Fix and slide model calls | No automatic Wonder/slide retry | Each SDK attempt is accounted, including extraction format fallback. Wonder/Spark use the remaining operation time and configured per-call timeout. SAM receives at most a 15 s per-call timeout; this is not a strict wall-clock bound. A slide makes at most two SAM calls and one Spark call. |
| Logs | Counts, timing, usage and safe error metadata | No draft text, image content, keys, raw provider errors, or client IPs. |

Only text checks served entirely from the recorded-check or model-response cache skip the rate and spend admission
guards. Fix and slide always use admission guards. A cache miss discovered after preflight cannot dispatch an
unreserved model request. Reservations are estimates, not a guarantee that provider charges cannot exceed them.
If a request is cancelled while database admission is running, its thread may still commit. No provider
work is dispatched by that cancelled route, and a committed or ambiguous precharge is conservatively
retained as pending; cancellation never assumes a rollback or automatically refunds it.
Shared PostgreSQL covers instances using the same database and namespace; local files do not. The 100 s deadline
and stream closure stop further dispatch, while an in-flight synchronous HTTP call still relies on its transport
timeout. Late text responses cannot refund frozen accounting or start a new retry. Unknown charges, UTC
rollover, idempotent settlement and configuration mismatch behavior are documented in [spend protection](spend-protection.md).

## API behavior details

The following ordering and cache details describe `/api/check`; see [the API contract](api-contract.md) for all
routes. Fix emits progress followed by a terminal `result` or `error`. Slide adds `progress` and `region` events to
its claim/verdict stream and ends with `done` or `error`.

- **Ordering.** The stream is `meta` first, then each passage as its extraction finishes: that passage's `claim`
  events, then their `verdict` events. `done` comes last.
- **Claim ids.** Ids are `c1`, `c2`, ... in the order they are sent. Live passages finish out of order, so sort by
  `span_start` to get document order. In a cached replay, the ids are the recorded ones.
- **Errors.**
  - `error` ends the stream; no `done` follows it.
  - A request rejected up front (bad input, length, rate limit, budget, no model key) gets a single `error` event and
    no `meta`.
  - Application-level responses from the six streaming POST routes use HTTP 200 with `text/event-stream`.
    `/api/health` returns JSON and can return 503; Vercel Authentication or platform limits can reject a request
    before it reaches the application and return a different status or content type.
  - Current application errors include available `cost_usd`, `cost_is_estimate`, and `model_calls`. An upfront
    rejection can explicitly report zero calls/cost. Browser/network failures or older records may omit these
    fields; the UI displays **Model cost unavailable** for missing or malformed tuples, not zero.
- **Cost reporting.** Check `done`, Fix `result` and Slide `done` expose the cost tuple. Estimated costs are
  labeled in the UI. Old Check/Fix successes remain readable without optional estimate/call-count fields.
  Observed usage is priced at configured rates, not reconciled against provider billing; a conservative ledger
  charge can exceed the displayed estimate when an entire uncertain reservation is retained.
- **Keep-alives.** During 15 s without an event, the server sends an SSE comment line, `: keep-alive`.
- **Cache replays.** A replay sends the recorded events with `meta.cached` set to true. `done` keeps the original run's
  `latency_s` and `cost_usd`.
- **Cache key.** `draft_hash` is the cache key: sha256 over the exact text, `as_of` normalized to
  `YYYY-MM-DDTHH:MM:SSZ`, and the trimmed context. A blank context means "infer it from the first passage", as in the
  CLI.
- **Stale recordings.** A recorded check is replayed only while the evidence (the snapshot fingerprint, or the file
  stats of `data/`) and the code that decides verdicts are unchanged. Otherwise the check reruns. For the examples the
  rerun is free, thanks to `spark_cache/`.

## Rebuilding the snapshot

```bash
.venv/bin/python scripts/build_snapshot.py             # re-runnable; prints size, fact counts and verdict counts
.venv/bin/python scripts/build_snapshot.py --no-model  # fail rather than call the model
```

- **What it copies.** `data/facts.sqlite`, using SQLite's backup API, so the copy is consistent even while ingest
  writes. It also copies every valid line of `data/pr_facts/*/*.jsonl`; half-written lines from a running writer are
  dropped.
- **What it records.** It records each entry of `examples/examples.json` through the real pipeline, then swaps the
  new snapshot in only if it is complete and under 112 MiB (raised from 10 MiB to 16 MiB, then to 112 MiB for the S&P 500, on 2026-10-31).
- **Re-runs.** If nothing changed, the files come out byte for byte the same. If the data or the verifier changed,
  the examples are re-recorded from the model-response cache at no cost. Model calls happen only for edited example
  passages, at about $0.004 each.
- **When to run it.** After the coverage agent finishes expanding `data/pr_facts`, and after any change to
  `receipts/{types,extract,periods,verify,store,stream}.py`. Then commit `data/snapshot/`.

## Measured on 2026-10-27 (local, M4)

| Run | First event | First verdict | Done | Cost |
|---|---|---|---|---|
| NVIDIA example, live (`RECEIPTS_NO_CACHE=1`, 5 model calls) | 0.04 s | 3.0 s | 3.9 s | $0.013 |
| Any example, cached replay | < 0.05 s | < 0.05 s | < 0.05 s | $0 |

## Known limits

- **Validation has a defined scope.** Actual PostgreSQL contention/settlement checks, hosted shared-ledger
  guards and browser functional/visual acceptance passed on `885a46f`; coverage replay and budget rejection
  passed on `778d5f8`. Operator reservations were settled at zero, with no pending reservation at final readback.
  Database failover and mid-commit network loss were not fault-injected. Public release is authorized, with
  current runtime/browser acceptance tracked above; provider-side hard limits have not been independently confirmed.
- **Estimates are not invoices or billing ceilings.** Configured token prices, request-size allowances and
  output limits inform admission; they are not measured upper bounds for every provider/tokenizer or self-hosted
  GPU charge. Unknown usage may consume more allowance than reported model cost. Keep the database/namespace
  stable, preserve pending charges and maintain separate provider spending controls where available.

- **Services is in Beta.** If the services build misbehaves, the documented alternative is two Vercel projects in
  one monorepo: `web/` on Next.js and the repo root on FastAPI. The frontend would then call the API's URL through
  `RECEIPTS_API_ORIGIN` or a rewrite.
- **Local scope remains local.** The rate limiter is per instance, and local SQLite only coordinates processes
  sharing its file. Vercel `/tmp` is ephemeral. PostgreSQL storage availability, access controls and durability
  still require operational validation; offline SQLite/mock tests do not establish those guarantees.


## Judging window (approved 2026-10-30)

The owner approved opening live requests from the submission release through the community vote:
`RECEIPTS_LIVE_UNTIL=2026-11-17T16:00:00Z` (2026-11-18 00:00 Taipei) and a **US$5 per UTC day** shared
allowance (raised from US$3 on 2026-10-30), paid from the Meta $150 hackathon credit. Two sub-caps inside that
allowance keep a burst of large requests from locking judges out of Check and Fix
([spend protection](spend-protection.md#daily-sub-caps)); their code defaults are the release values, so they
need no environment change:

- Slide, Ask and whole-filing questions may not use the last **US$1.50** of the day
  (`RECEIPTS_CHECK_FIX_RESERVE_USD=1.5`); that end of the allowance stays with Check and Fix.
- At most **5 new whole-filing questions** per UTC day across all instances
  (`RECEIPTS_FILING_QUESTIONS_PER_DAY=5`); the recorded Meta 10-K question still replays at $0.

Everything else stays as configured: the same PostgreSQL ledger and namespace `nocap`, the shared-budget
requirement and the per-instance rate limit. While the window is open the workspace shows an "open until" notice
instead of the pause notice. At the deadline, live work pauses on its own without a redeploy; saved examples,
the video and benchmarks keep working.

**Keep Preview paused for the whole window.** Production and the protected Preview share the ledger namespace
`nocap`, and a namespace/day rejects a different cap (Preview is configured at US$1). Whichever environment admits
a live request first on a UTC day would fail the other closed (`budget_unavailable`) until the next UTC day. Set
`RECEIPTS_LIVE_REQUESTS_ENABLED=false` for Preview, or give Preview its own `RECEIPTS_SPEND_NAMESPACE`, and redeploy Preview before the release deployment: an environment change does not reach an existing deployment. Until then, do not open the old Preview.
Change a cap or the filing-question limit only before the first admission of a UTC day.

Production environment changes take effect only in a new deployment, so the owner applies them right before the
release deployment:

```bash
SCOPE=changshoufeng0609-cmds-projects
vercel env rm RECEIPTS_LIVE_REQUESTS_ENABLED production --scope $SCOPE -y
printf 'true' | vercel env add RECEIPTS_LIVE_REQUESTS_ENABLED production --scope $SCOPE
vercel env rm RECEIPTS_LIVE_UNTIL production --scope $SCOPE -y 2>/dev/null || true
printf '2026-11-17T16:00:00Z' | vercel env add RECEIPTS_LIVE_UNTIL production --scope $SCOPE
vercel env rm RECEIPTS_DAILY_BUDGET_USD production --scope $SCOPE -y
printf '5' | vercel env add RECEIPTS_DAILY_BUDGET_USD production --scope $SCOPE
```

After the release deployment:

1. `GET /api/health` reports `"live_requests_paused": false` and `"live_until": "2026-11-17T16:00:00Z"`.
2. One live smoke each for Check (a built-in example with one number changed), Fix, Slide and Ask. At
   historical prices this is about $0.04–0.05 in total. Record the ledger's spend for the UTC day.
3. Rerun the anonymous acceptance harness. Saved examples and the three recorded samples (Slide → Try a real SEC slide, the recorded Meta 10-K question, Private → Replay a recorded Private check) must replay at $0 with `cached: true`.
4. The owner sets a spend notification on dev.meta.ai.

Three recorded samples stay available whatever the window: the real Bank of America slide, the Meta 10-K
question and the synthetic Private draft replay their recorded live runs at $0 from `data/snapshot/recordings/`,
and only for those exact requests (`receipts/recordings.py`). They skip the rate limit and the ledger because they
make no call; the Private replay also needs no Modal configuration ([Private mode](private-mode.md#recorded-sample)).

To close the window early, set `RECEIPTS_LIVE_REQUESTS_ENABLED` back to `false` and redeploy. Lowering the
daily allowance also needs a redeploy.

## Public release controls (2026-10-29)

The owner authorized public publication in the completion scope. The new Production configuration sets
`RECEIPTS_LIVE_REQUESTS_ENABLED=false` while the operating window is being confirmed. This preserves the
existing fixed PostgreSQL primary, namespace `nocap`, US$1 UTC admission-day allowance and stored charges.
It does not reset budgets or substitute a new ledger. Cached examples and the demo video remain available.

- `RECEIPTS_LIVE_REQUESTS_ENABLED`: omitted keeps the prior enabled behavior; when present, only `true`
  (case-insensitive, surrounding whitespace ignored) enables new model calls. Other values pause them.
- `RECEIPTS_LIVE_UNTIL`: optional UTC ISO deadline, `YYYY-MM-DDTHH:MM:SSZ` or `+00:00`, with optional
  fractional seconds. Missing timezone, non-UTC offsets and invalid values pause live work.
- Both admission and each queued model dispatch check the policy. Already-running calls still settle their
  observed/uncertain usage. In-flight provider requests cannot be cancelled merely by reaching the cutoff.
- `/api/health` exposes `live_requests_paused` and, while a window with a deadline is open, `live_until`
  (UTC ISO, otherwise `null`). A false `live_requests_paused` means the policy permits live work; it does not
  guarantee model credentials, the ledger or the private GPU endpoint are available.
- Check, Fix, Slide, Ask, whole-filing quotation and Private share this policy. Static examples and recorded
  Check or extraction-cache replays remain usable while paused. The current source rejects paused
  operations before per-client rate admission, as well as before ledger admission and provider dispatch.

The production frontend displays an explicit pause notice from health. No paid plan, extra database, new
namespace or billing-cap claim is introduced. Production access checks and the final public URL are recorded
only after deployment. Preview/generated URLs should remain protected when a production alias is opened,
using [Vercel Standard Protection](https://vercel.com/docs/deployment-protection).
