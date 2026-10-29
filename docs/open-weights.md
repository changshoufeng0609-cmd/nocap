# Extraction backends and open weights

NoCap can extract claims with Meta Model API, partner-hosted Muse Spark Open, or Muse Glimmer in your own
Modal workspace. The deterministic verifier is the same for every backend. Backend adapters have local HTTP
contract tests. **Fireworks Muse Spark Open completed a live benchmark on 2026-10-28 and Baseten on 2026-10-29**.
Modal Glimmer completed one bounded live smoke on 2026-10-29 and was stopped afterward; this is not a quality
benchmark or an always-on service. Both partner runs used existing hosted endpoints and did not provision
dedicated deployments. See the retained [benchmark table](../eval/bench_results.md),
[Fireworks record](../eval/bench_runs/fireworks-20261028.json) and [Baseten record](../eval/bench_runs/baseten-20261029.json).

## Select a backend

Edit the existing, gitignored `.env` using [`env.example`](../env.example) as a reference. Do not replace an
existing `.env`, paste keys into chat, commit them, or put them in a URL. Shell variables override `.env`.
Restart the API after changing the backend; changing a running process is not supported.

| `RECEIPTS_EXTRACT_BACKEND` | Model | Credentials | Output format |
|---|---|---|---|
| `meta` (default) | `muse-spark-1.3` | `MODEL_API_KEY` | Responses API, strict JSON schema |
| `fireworks` | `accounts/fireworks/models/muse-spark-open` | `FIREWORKS_API_KEY` | Chat Completions, JSON object + schema in prompt |
| `baseten` | `meta-models/muse-spark-open-1026` | `BASETEN_API_KEY` | Chat Completions, schema in prompt |
| `modal` | `muse-glimmer` | Modal Proxy Token + `RECEIPTS_MODAL_BASE_URL` | Chat Completions, JSON schema |

The default Meta request body and hash are unchanged, so existing Spark caches remain valid. The model, request
and endpoint distinguish other response caches; a different backend cannot replay a Meta snapshot as its output.
Recorded checks and evaluation results also distinguish backends. No backend falls back to Meta when its setup
is missing. Contributor-mode model IDs are rejected everywhere.

Open-model output is validated claim by claim. Code checks quoted spans and re-reads numbers before verification.
A malformed claim is dropped with a warning; it never becomes a verified fact. Only an explicit server error
saying `response_format` is unsupported permits a retry with the schema in the prompt. Invalid schemas, context
overflow and unrelated HTTP 400s fail normally.

## Fireworks or Baseten: user setup

1. Create a key in [Fireworks API keys](https://app.fireworks.ai/settings/users/api-keys) or
   [Baseten settings](https://app.baseten.co/). Check the account's credits and spending controls.
2. Put the key into `FIREWORKS_API_KEY` or `BASETEN_API_KEY` in `.env` yourself.
3. Set `RECEIPTS_EXTRACT_BACKEND=fireworks` or `baseten`, then restart the local API or run the CLI.

These presets use an existing hosted Model API; they do not create a dedicated deployment. Baseten's `-1026`
model ID is the hackathon Participant Guide's value. Its general documentation uses the unsuffixed name.
[Baseten](https://docs.baseten.co/inference/model-apis/overview) explicitly excludes Muse Spark Open from JSON
mode and structured-output support. [Meta's Fireworks guide](https://dev.meta.ai/docs/muse-spark-open/deploy#fireworks-ai)
documents JSON object mode. Both receive the schema in the system prompt.

## Modal: user setup before deployment

1. Visit the [Glimmer model page](https://huggingface.co/meta-models/Muse-Glimmer-30B), review its license and
   accept any access terms shown by Hugging Face. Glimmer is Apache-2.0 and currently ungated; its
   [download guide](https://dev.meta.ai/docs/muse-glimmer/get-the-model) does not require a token. If choosing
   Spark Open weights separately, accept that model's gated license first. The supplied server downloads
   Glimmer without a Hugging Face credential by default.
2. Install the optional Modal CLI and authenticate yourself in the intended workspace:

   ```bash
   .venv/bin/pip install -U modal
   .venv/bin/modal setup
   ```

3. Optional authenticated downloads: create **`receipts-hf`** in the [Modal Secrets dashboard](https://modal.com/secrets),
   containing only a read-only **`HF_TOKEN`**, and set `RECEIPTS_MODAL_USE_HF_SECRET=1` when deploying.
   Do not import the whole `.env`: the server does not need the Meta, SEC, Fireworks or Baseten credentials.
4. In [Modal Proxy Tokens](https://modal.com/settings/proxy-auth-tokens), create a dedicated token.
   Put its two values in `.env` as `MODAL_PROXY_TOKEN_ID` and `MODAL_PROXY_TOKEN_SECRET`, or put the combined
   `ID.SECRET` in `MODAL_PROXY_TOKEN`. These are different from the CLI's account credentials. Tokens are
   **workspace scoped** unless the workspace enables RBAC and assigns environment access. Modal does not
   offer endpoint-only Proxy Tokens; a dedicated token name does not narrow its permissions. With RBAC,
   place Glimmer alone in a restricted environment and allow that environment only. See the
   [official Proxy Token guide](https://modal.com/docs/guide/webhook-proxy-auth).

Obtain explicit approval before running the following command, which builds an image and creates the app
and cache volumes:

```bash
.venv/bin/modal deploy deploy/modal_glimmer.py
```

After an approved deployment, copy the printed URL into `.env`, append `/v1`, and set:

```dotenv
RECEIPTS_EXTRACT_BACKEND=modal
RECEIPTS_MODAL_BASE_URL=https://YOUR-DEPLOYMENT-URL/v1
```

[`deploy/modal_glimmer.py`](../deploy/modal_glimmer.py) runs BF16 Glimmer on one H100 with persistent Hugging Face
and vLLM caches, a 32,768-token context, at most four simultaneous model sequences, zero minimum containers,
one maximum container and a 60-second idle scale-down window. CPU and RAM have hard limits of four cores and
16 GiB; HTTP concurrency is capped at four. Modal proxy authentication is required. The image
uses `vllm/vllm-openai:v0.28.0`, the version in the [vLLM recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B)
checked on 2026-10-27, plus Modal's standalone Python 3.12 for lifecycle hooks. The Server URL uses
`NAME.REGION.modal.direct`; use the URL returned by `modal.Server.get_url()`. Both parsers are `muse_glimmer`;
`generation-config auto` retains the checkpoint's sampling
and stop tokens. Request logging is disabled. No credential is mounted by default; the opt-in HF secret
contains only the download token. The local `.env` is not uploaded.

For a bounded validation, start an independent wall-clock watchdog before deployment and stop the app
after the check with `.venv/bin/modal app stop receipts-glimmer --yes`. At the
[published rates](https://modal.com/pricing) checked on 2026-10-29, a full 30 minutes of one H100, four
CPU cores and 16 GiB RAM is approximately **$2.133**, before build, storage and network costs. A $3 smoke
budget therefore needs a shorter stop threshold or reserved overhead; it is an estimate, not a billing cap.
Keep minimum containers at zero. Public traffic must not be enabled on the assumption that NoCap's token
admission ledger caps GPU charges; cold starts and idle time are billed separately.

One H100 successfully loaded Glimmer and completed the bounded smoke below; future capacity and general
response quality are not established. First download and startup can take minutes;
requests may initially receive 503. The CLI/benchmark can wait longer than the interactive API's 100-second check
deadline. Warm the approved server before a demo and account for the GPU time. Do not assume the interactive API
can complete a cold start within its deadline.

## Modal live validation

The authorized 2026-10-29 smoke called the real authenticated Glimmer server through `private.private_events`
and the deterministic SEC verifier. One model call produced two claims: **1 verified / 1 wrong**, with a
complete stream. An unauthenticated request returned **401**. The private draft and response were not written
to application caches; the retained smoke contains aggregate event names and counts.

The deployment required image-runtime, Server-origin and vLLM flag compatibility fixes before the successful
attempt. All attempts shared one starting timestamp. The final **1,470.74 seconds** includes those fixes,
deployment, download/startup, inference and shutdown; it is not an inference-latency measurement. The cumulative
wall-time compute estimate was **$1.742710**, excluding build, storage and network. Earlier attempt records are
cumulative too and must not be added to that estimate.

An independent read-only check found all **four `receipts-glimmer` apps stopped, with zero tasks**. Modal's
hourly report attributed **$0.70780581** to those apps. The workspace's October summary separately reported
**$0.71237107 metered / $0 billed**, after credits and a network allowance; that summary includes other
workspace usage, is not a remaining-credit balance and may change with delayed metering. It is not a billing
cap. The [retained validation record](private-live-validation-2026-10-29.json) includes app IDs, stop times,
provider report rows and the historical smoke hash.

The private API/browser integration was checked separately with fake transports and controlled fixtures;
this smoke was not a live browser-to-API acceptance. The follow-up passed **38 private/deployment tests** and
made **zero new model calls**. One two-claim smoke does not establish model quality, broad financial coverage
or unattended availability. The server remains stopped.

## Overrides

| Variable | Meaning |
|---|---|
| `RECEIPTS_EXTRACT_MODEL` | Model ID; Meta requires a Standard-mode allowlisted ID |
| `RECEIPTS_EXTRACT_BASE_URL` | Absolute HTTP(S) API base URL; no credentials, query or fragment |
| `RECEIPTS_EXTRACT_API_KEY_ENV` | Name of the environment variable holding the key, never the key itself |
| `RECEIPTS_EXTRACT_STRUCTURED` | Chat format: `json_schema`, `json_object`, or `prompt` |
| `RECEIPTS_EXTRACT_EFFORT` | Meta: `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; open models: `low`, `medium`, `high`, `xhigh` |
| `RECEIPTS_EXTRACT_MAX_TOKENS` | Positive output token limit including reasoning |
| `RECEIPTS_EXTRACT_TIMEOUT_S` | Finite positive per-attempt timeout floor; does not extend the API's whole-check deadline |

Without an effort override, the extractor's `minimal` default becomes `low` for open models. Open-model output
limits default to 12,000 tokens. Timeout floors are 60 seconds for partner APIs and 180 seconds for Modal.
Clear backend-specific overrides when switching back to Meta. Custom model or endpoint pricing must be checked
separately; the preset prices do not automatically follow an override.

## Benchmark

[`eval/bench_models.py`](../eval/bench_models.py) compares 27 NVIDIA gold claims and 13 fixed **dev** items across
all backends. It never runs the held-out split. [`eval/bench_results.md`](../eval/bench_results.md) records measured
results; targets without credentials or real runs remain explicitly unmeasured.

```bash
.venv/bin/python eval/bench_models.py --list
.venv/bin/python eval/bench_models.py --offline
.venv/bin/python eval/bench_models.py --targets spark-open-fireworks --budget 0.60
```

The first command checks local configuration only. `--offline` reads cached responses and makes no model or
warmup calls, including for a zero-token-price self-hosted backend. A live self-hosted benchmark additionally
requires `--allow-self-hosted`, after deployment approval and setup:

```bash
.venv/bin/python eval/bench_models.py --targets glimmer --allow-self-hosted --budget 0.60
```

`--budget` controls estimated token API spend; it is not a hard GPU billing limit. A zero budget prevents new
calls. Set provider spending controls separately. As checked on 2026-10-27,
[Fireworks](https://fireworks.ai/models/fireworks/muse-spark-open) and
[Baseten](https://www.baseten.co/library/muse-spark-open/) list $1.25 input, $0.15 cached input and $4.25 output
per million tokens. [Modal](https://modal.com/pricing) lists H100 GPU time at $0.001097/second (about $3.95/hour),
plus CPU, memory and any applicable storage/network charges. Cold starts and idle time also consume resources.
A Modal `cost_usd=0` in extraction means **no per-token API charge**, not free compute.

## Fireworks live validation

On 2026-10-28, the configured Fireworks account successfully called
`accounts/fireworks/models/muse-spark-open` at `https://api.fireworks.ai/inference/v1`, using the existing
JSON-object preset, schema in the prompt, `reasoning_effort=low` and a 12,000 output-token limit. The model ID,
serverless availability and rates were rechecked against the [official model page](https://fireworks.ai/models/fireworks/muse-spark-open)
and [Meta's provider guide](https://dev.meta.ai/docs/muse-spark-open/deploy#fireworks-ai).

- **Gold:** 10/10 live passage calls; 23 raw claims, no raw numeric inconsistency, no schema rejection and no
  uncovered numbers. Basis expansion produced 28 claims, with strict recall **27/27** and precision **27/28
  (96.4%)**. The unmatched claim was a second accounting basis for the same quoted 50-basis-point guidance
  tolerance. The input explicitly says GAAP and non-GAAP, but gold has one tolerance record without a basis;
  this is a granularity mismatch, not an invented number. It remained unverifiable, and no unmatched claim
  was marked verified. Gold was not edited to improve this score.
- **Dev subset:** all 13 fixed items completed; **6/6** errors caught with correct reasons, **0/6** false
  alarms, no dangerous verified miss, one guidance control retained as unverifiable, no skips or errors.
  This is the fixed benchmark subset, not the full 224-item dev corpus.
- **Latency and usage:** gold wall time **71.74 s** (live), median call **21.81 s**, slowest call **71.73 s**;
  gold usage about **$0.054**. Independent SDK accounting recorded **$0.13005095** for all 23 benchmark calls;
  the separate one-passage smoke cost **$0.00944875**, giving **$0.13949970** in total at the published token
  prices. Provider invoices and remaining credit balances were not queried. The runner's summed rounded
  passage costs differ from the independent total by a few millionths of a dollar.
- **API integration:** the actual Fireworks responses were replayed through `POST /api/check`, with model
  client creation forbidden. The 28 claims returned **12 verified / 0 wrong / 15 unverifiable / 1 approximate**,
  correct Fireworks model attribution, and zero new spend. Product configuration was not switched persistently.

The gold annotations are a draft awaiting human review. In particular, guidance periods with empty gold
period labels are not evaluated, so 100% gold recall does not establish that every period field is correct.
This single small run does not establish general accuracy or production latency. The Meta Spark/Wonder rows
use earlier cached responses; their recorded maximum call times are not a simultaneous end-to-end comparison.
The default extractor remains Meta Spark.

The [committed record](../eval/bench_runs/fireworks-20261028.json) includes the original benchmark result,
independent usage totals, smoke metadata, input hashes and these audit notes. The held-out split was not run.

## Baseten live validation

On 2026-10-29, the configured Baseten account successfully called the exact hackathon model
`meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1`. The existing preset sends the schema
in the prompt, no JSON response mode, `reasoning_effort=low` and a 12,000 output-token limit. The user had
already completed a separate CLI smoke; its reported $0.0265 is excluded from the measured benchmark total.

- **Gold:** 10/10 fresh live passage calls, 23 raw claims, 23 verbatim spans, zero numeric inconsistencies,
  label errors, schema rejections or uncovered numbers. Deterministic basis expansion produced 28 claims:
  strict recall **27/27**, precision **27/28 (96.4%)**. The same extra non-GAAP 50-basis-point guidance
  tolerance as Fireworks remained `?`; no unmatched claim was verified. Labels were not changed.
- **Dev subset:** 13/13 completed, **6/6** planted errors caught with correct reasons, **0/6** false alarms,
  no planted error marked verified, and the guidance control remained unverifiable; no skips or errors.
- **Latency:** gold wall **5.05 s**, median passage **3.99 s**, maximum **5.05 s**; dev median **3.49 s**.
- **Usage:** all **23 SDK attempts** completed with valid usage. Total input **54,330** tokens, including
  **48,128** provider-cached tokens; output **22,180**, including **15,787** reasoning tokens. At the
  [official rates](https://www.baseten.co/library/muse-spark-open/) rechecked on 2026-10-29, benchmark usage
  cost **$0.10923670**, including **$0.04272550** for gold. Reasoning is included in output, not billed
  twice. This is usage-priced accounting, not an invoice or a remaining-credit query.
- **Run protection:** a process-local wrapper reserved conservative per-attempt estimates under a **$0.75**
  allowance and a **23-attempt** limit, retaining estimates on missing usage or failures after dispatch.
  Nine offline stub checks covered the wrapper before the run. This was an additional experiment guard,
  not a change to the production backend or a provider billing ceiling.

The fixed inputs and dev IDs match the retained Fireworks run. Both used ten concurrent gold passages and
up to four dev items. Dates, endpoints, model IDs, response-format settings, output lengths and provider cache
hits differ; local `--fresh` bypasses NoCap's response cache, not the provider's prompt cache. The result does
not establish that Baseten is generally faster or cheaper. Meta rows are earlier cached measurements.
Provisional gold labels still need independent human review, and empty guidance-period labels do not test
those period fields. The 13-item subset is not a full-corpus Baseten evaluation.

The [retained record](../eval/bench_runs/baseten-20261029.json) contains benchmark results, independent usage
accounting, input hashes and limitations. Full regression validation passed **905 Python tests**, guarded
**224-item dev** and **15-draft trap dev** replays with unchanged results and $0 additional model spend.
The held-out split was not run. Meta Spark remains the local default and the hosted extractor; no deployment
or persistent backend switch was made.

## Report definitions

- **Raw errors:** model JSON invents a quoted number, or its value, scale or unit disagrees with the passage.
- **Label errors:** the number matches a gold claim but metric, accounting basis or period does not.
- **Strict recall / precision:** matches against all 27 gold claims / all claims produced after deterministic repair.
- **Unflagged errors:** produced claims matching no gold claim that nevertheless receive ✓ from the verifier.
- **Catch rate / false alarms:** planted dev errors caught / correct original dev items flagged incorrectly.
- **Latency:** wall time for a live gold check; a cache replay reports recorded maximum call latency, not a new measurement.
- **Cost:** token usage at preset prices; the self-hosted GPU estimate excludes cold start, idle time and non-GPU charges.
