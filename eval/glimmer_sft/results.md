# Experiment: Muse Glimmer 30B LoRA SFT for NoCap claim extraction

**Run 2026-10-30, 19:05–19:15 UTC, on the Fireworks Serverless Training API.** The owner launched it; the scripts
in this folder did the rest. The product is unchanged: nothing here touches `receipts/`, the web app or the
default extractor (Muse Spark 1.3), and Private mode still runs base Muse Glimmer on Modal.

The question: does a LoRA fine-tune on Muse Spark 1.3's code-accepted extractions make Glimmer a better NoCap
extractor under NoCap's own deterministic checks?

## Result in brief

- **The tuned model reproduced the teacher's exact claim sets more often.** 39/70 → 51/70 passages (McNemar,
  18 vs 6 discordant, p = 0.023).
  - Nearly all of that gain is `period_text` wording: 91 → 127 of 160 teacher claims.
  - Numbers, metrics and bases were already the same before tuning: every aligned claim had the teacher's
    `value_text`.
- **End-to-end NoCap verdicts barely changed.**
  - Verdict-equivalent passages: 66/70 → 68/70 (2 vs 0, p = 0.5, not significant).
  - Status agreement on teacher claims: 154/160 → 157/160.
  - Seeded errors caught: 32/34 → 33/34 (the teacher also catches 33/34; p = 1.0).
  - Neither model raised a false alarm, and neither turned a teacher-flagged error into ✓.
- **Both models were already perfect on gold.** 27/27 strict recall and 27/27 precision on the hand-labeled
  claims (p = 1.0). The teacher scores 27/28 precision.
- **Format was already fine.** Both models gave 70/70 pure, strictly valid JSON, with no unterminated and no
  empty answers. This was greedy decoding without guided decoding.
- **The tuned model answers without reasoning.**
  - Mean completion: 203 vs 725 tokens (−72%).
  - Median latency: 3.4 s vs 11.3 s (indicative only).
  - Sampling cost for the 70 passages: $0.146 vs $0.324.
- **Cost.**
  - Training: $0 under the promotion.
  - Sampling: $0.47, estimated from provider-reported token counts. Billing had not posted it when last read.
  - No deployment was created, and the training session was deleted.
- **Significance.** Five paired tests were run. With a Bonferroni correction (α = 0.01), none is significant,
  so read the claim-set result as suggestive, not conclusive.

## Design

| | |
|---|---|
| Base model | `accounts/fireworks/models/muse-glimmer-30b`, Fireworks Serverless Training API |
| Method | LoRA SFT, rank 8, alpha 32, MLP + attention + unembedding; cross-entropy on the last assistant turn; Adam lr 1e-4 (β 0.9/0.999, eps 1e-8, no weight decay, grad clip 1.0); batch 16; 2 epochs (26 steps); seed 0 |
| Prompt | Exactly what the `modal` Glimmer backend sends: system `receipts.extract.CHAT_INSTRUCTIONS` (instructions + JSON schema), user `format_input(passage, context)`, chat template with `Reasoning strength: low.` |
| Targets | Muse Spark 1.3's cached extraction of the same passage and context (effort `minimal`), re-serialized in schema key order with compact separators (`json.loads`-equal to the cached text). Answers only, no reasoning trace |
| Decoding, base = tuned | Temperature 0 (greedy), `max_tokens` 4,096, stop `<|eot|>`, same rendered prompt, no guided decoding. Base is the untrained LoRA snapshot of the same session, so both models use the same serving path |
| Scoring | `evaluate.py`: NoCap's chat-backend code path (`json_from_message`, `validate_claims_lenient`, `build_claims`) and `receipts.stream.verdict_for` on the local SEC evidence store. No LLM judge |

The steps follow the fw-ai/cookbook Muse Glimmer starter's serverless path
(`training/examples/sft/muse_glimmer_starter.py`, commit `57968a2`), as in the Fireworks workshop (Sinan Ozdemir,
`sinanuozdemir/ai-experiments`, `experiments/meta-hackathon-muse/muse_glimmer_train_router.ipynb`). There are
three deliberate differences:

1. **Renderer.** The cookbook renderer needs torch and transformers. `glimmer_format.py` ports it to the
   `tokenizers` package; the checks below show it matches.
2. **Holdout.** Evaluation uses a fixed test file, not a random 40-row holdout.
3. **Saved answers.** Full answers are scored with NoCap's checks. The starter truncates them and scores exact
   match.

## Data (`build_data.py`, `data_manifest.json`)

Only `eval.load_split("dev", ...)` rows are read: 224 v0 development items and 15 trap_v1 development drafts. The
held-out split was not used for training or evaluation. Passages are deduplicated by request (passage + context),
which gives 275 development passages (207 v0, 68 trap_v1) in 129 groups.

| Split | Passages | Groups | Rule |
|---|---|---|---|
| test, gold | 10 | 1 | `spikes/inputs/gold_text.txt`, 27 hand-labeled claims |
| test, development | 60 (42 v0, 18 trap_v1) | 26 | seeded 20% of each corpus, by whole group (a v0 sentence with all its mutants; a trap sheet's drafts) |
| excluded | 6 (v0) | 4 | taken from the gold passages' own press release (SEC accession 0001045810-26-000073) |
| train candidates | 209 | 101 | everything else |
| **train kept** | **203** (153 v0, 50 trap_v1; 481 teacher claims) | 96 | target accepted by code |
| train dropped | 6 | 5 | target rejected by code |

A target is kept only when code accepts it. Three checks apply:

- **Schema:** the strict schema validates, and the lenient chat path rejects no claim.
- **Locate:** every `span_text` is verbatim in the passage and contains its `value_text`, and `locate_claims`
  places every claim without dropping it, re-anchoring it or widening its `value_text`.
- **Re-read:** every `value_text` holds exactly one number, and its value, scale and unit read back unchanged.

Of the 6 dropped targets:

- 3 failed `reread:not_one_number`: ordinal "11th"/"12th" claims.
- 3 failed `locate:value_widened`: `value_text` "2" for "2 percentage points", and "14%" for "+14%".

13 kept passages and 4 test passages have no claims, such as headlines without figures. Teacher outputs for 2 test
passages would fail the same checks. They stay in the test set as references.

## Results

All 70 test passages were sampled as base + tuned pairs: 140 requests, 0 failures, 0 skipped.

### Format and tokens (70 passages each)

| | Base Glimmer | Tuned Glimmer | Teacher |
|---|---|---|---|
| Pure, strictly valid JSON as returned | 70/70 | 70/70 | 70/70 |
| Unterminated (no `<|eot|>` by 4,096 tokens) / empty | 0 / 0 | 0 / 0 | n/a |
| Claims rejected by the schema / dropped by code | 0 / 0 | 0 / 0 | 0 / 0 |
| Claims returned → after locating and basis expansion | 154 → 158 | 154 → 158 | 155 → 160 |
| Completion tokens: mean / median / p90 / max | 725 / 707 / 1,103 / 1,427 | 203 / 188 / 303 / 563 | n/a |
| of which reasoning (approx.) / answer, total | 35,606 / 14,429 | 0 / 13,936 | n/a |
| Wall time per request: median / p90 | 11.3 s / 17.3 s | 3.4 s / 5.7 s | n/a |

Wall times come from Fireworks' shared serverless training sampler, with four requests in flight. They are
indicative only, not a serving benchmark.

### Gold (10 passages, 27 hand-labeled claims; `bench_models.match_gold`)

| | Base | Tuned | Teacher |
|---|---|---|---|
| Strict recall (value, metric, basis and period all match) | 27/27 | 27/27 | 27/27 |
| Precision (strict matches / claims shipped) | 27/27 | 27/27 | 27/28 |
| Label errors / unflagged errors (unmatched claims marked ✓) | 0 / 0 | 0 / 0 | 0 / 0 |
| Verdicts | 12 ✓, 15 ? | 12 ✓, 15 ? | 12 ✓, 16 ? |

The teacher's 28th claim is the second accounting basis of the 50-basis-point guidance tolerance. The gold set has
a single tolerance without a basis. Both Glimmer models emit it once, also without a basis.

### Agreement with the teacher (160 teacher claims in 70 passages)

Claims are aligned on the code-located source number and basis. A missing claim counts as a mismatch.

| | Base | Tuned |
|---|---|---|
| Aligned / missing / extra | 156 / 4 / 2 | 157 / 3 / 1 |
| `value_text` equal | 156 | 157 |
| `metric` equal | 153 | 154 |
| `basis` equal | 155 | 156 |
| `period_text` equal | **91** | **127** |
| All four equal | 88 | 123 |
| `entity` equal (not one of the four) | 120 | 125 |
| Passages with the exact claim set (all four fields, nothing extra) / 70 | **39** | **51** |
| Verdict status equal / status and reason equal | 154 / 153 | 157 / 156 |
| Teacher ✗ → model ✓, teacher ✓ → model ✗ | 0, 0 | 0, 0 |
| Extra claims marked ✓ or ✗ | 0 | 0 |
| Passages verdict-equivalent to the teacher / 70 | **66** | **68** |

### Seeded errors (independent gold: dev items whose passages are all in the test set)

| | Base | Tuned | Teacher |
|---|---|---|---|
| v0 mutants caught (✗), 45 items | 22/23 (19 with the expected reason) | 22/23 (19) | 22/23 (19) |
| v0 unmodified originals with any ✗ | 0/20 | 0/20 | 0/20 |
| v0 forward-looking controls all ? | 2/2 | 2/2 | 2/2 |
| trap_v1 planted errors caught, 4 drafts | 10/11 | 11/11 | 11/11 |
| trap_v1 true figures ✓ / ✗ / ? (forward-looking figures ?) | 24 / 0 / 1 (6/6) | 25 / 0 / 0 (6/6) | 25 / 0 / 0 (6/6) |
| All seeded errors caught | **32/34** | **33/34** | 33/34 |

The one v0 mutant that all three models miss is a scale mutant whose target comes back unverifiable.

### Paired tests (exact two-sided McNemar, base vs tuned)

| Outcome | n | Base | Tuned | Only tuned / only base | p |
|---|---|---|---|---|---|
| Gold claim strictly matched | 27 | 27 | 27 | 0 / 0 | 1.0 |
| Passage: exact claim set vs teacher | 70 | 39 | 51 | 18 / 6 | 0.023 |
| Passage: verdict-equivalent to teacher | 70 | 66 | 68 | 2 / 0 | 0.5 |
| Passage: pure, strictly valid JSON | 70 | 70 | 70 | 0 / 0 | 1.0 |
| Seeded error caught | 34 | 32 | 33 | 1 / 0 | 1.0 |

### What changed: every discordant passage, read by hand

- **Tuned-only exact claim sets (18 passages).** Each is a `period_text` wording fix, 2 of them with entity
  naming as well. The base model did one of three things there:
  - copied the whole context line ("second quarter of fiscal 2026, the quarter ended June 30, 2026");
  - replaced a passage phrase such as "for the quarter" or "For the full year fiscal 2027" with the context
    period;
  - dropped or added a preposition ("the quarter ended …" vs "in the quarter ended …").
- **Base-only exact claim sets (6 passages).** Each is a tuned `period_text` wording difference, such as the
  short context form where the teacher used the long form, or "As of the end of the second quarter" vs "the end
  of the second quarter". The teacher itself is inconsistent about long vs short context forms, so part of this
  metric measures imitation of its habits.
- **Neither model exact (13 passages).** Mostly the same wording differences, plus four extraction differences:
  - the 50 bp tolerance emitted once without a basis (both models);
  - Meta's "13-16%" tax-rate range emitted as one claim, where the teacher emits two (both models);
  - Alphabet's "298%" and "294%" growth labeled `other`, where the teacher labels them `net_income` and
    `eps_diluted` (both models);
  - "over $500 billion" as `value_text`, where the teacher has "$500 billion" (base only).
- **Verdicts.** Only one passage changed in substance: a trap draft quoting Microsoft's figures in an Alphabet
  story.
  - Base wrote `metric_text` "Microsoft posted revenue" and "Microsoft has now reported revenue", with longer
    spans. The verifier treats that wording as a qualifier and returned **?** for both figures. So the true
    $82.9 billion lost its ✓, and the planted look-ahead $90.0 billion lost its ✗.
  - Tuned reproduced the teacher's `metric_text` "revenue" and got ✓ and ✗ (`look_ahead`).
  - The other verdict-equivalence misses are forward-looking figures (the tolerance, the tax-rate range, the
    $500 billion). They stay **?** however they are split.

### Hand reading: 10 seeded-random passages (`HAND_SEED` 20261031)

| # | Passage | Base | Tuned |
|---|---|---|---|
| 1 | JPM: "Noninterest expense was $24.3 billion, up 8%, …" | matches the teacher | matches; entity "JPMorgan Chase (JPM)" |
| 2 | BAC context: "Revenue grew 11% quarter-over-quarter." (period mutant) | `period_text` = the whole context line | matches |
| 3 | Netflix context: "Net income was $16.5 billion, up 13%." | short context period where the teacher used the long one; ticker dropped | short context period |
| 4 | Gold: GAAP / non-GAAP opex guidance $9.2B / $9.0B | matches | matches |
| 5 | Trap (AMD story): NVIDIA $68.1B and $81.6B for two quarters | "the quarter ended …" without "in" | matches |
| 6 | Gold: Data Center $89.0B, up 117% | matches | matches |
| 7 | JPM context: "Diluted EPS … $0.80 vs. $0.72 in Q2’25 (+11% …)" | context period instead of "for the quarter"; "Q2’25" without "in" | matches |
| 8 | AMD: Gaming revenue $720 million, up 11% QoQ | matches | matches |
| 9 | JPM: Home Lending $1.2 billion, up 2% | matches | matches; entity with ticker |
| 10 | Amazon: FX impact of about 190 basis points (guidance) | `period_text` = the whole context line | matches |

In all ten passages, `value_text`, `metric` and `basis` agreed across the three models. On all four fields, base
matched the teacher in 5/10 passages and tuned in 9/10. Base reasoning ran 243–757 tokens: it restated the
passage, then worked through the metric and period choices. No answer invented a number or put prose around the
JSON. Tuned answered directly, in 92–303 tokens.

## Training

- 26 steps (2 epochs of 13), 2 min 10 s (19:05:15–19:07:25 UTC).
- Loss per trained token: 0.116 at step 1, 0.018 at step 26. Epoch means were 0.034 and 0.017.
- The low starting loss fits Glimmer being distilled from Muse Spark: the base already predicts Spark's JSON
  closely.
- Pre-clip gradient norms ran 394–5,664 and were clipped to 1.0.
- The tuned sampler snapshot was promoted to `accounts/<account>/models/nocap-glimmer-sft-r20261030-190510` at
  19:07:53. It is stored, not deployed.

## Cost and run record

- **Training: $0, promotional.**
  - Fireworks' Models page and Glimmer cookbook make LoRA SFT on Muse Glimmer 30B free for jobs started before
    2026-12-01 00:00 PT; this one started 2026-10-30 19:05 UTC.
  - At the $5.86/1M train list rate, the 1,003,720 tokens processed would have cost about $5.88.
- **Sampling: $0.469983, estimated** at the published prefill $1.96, cached prefill $0.39 and sample $4.88 per
  1M tokens.
  - Per model: base $0.3241, tuned $0.1459.
  - Volume: 315,126 prompt tokens, of which the provider reported 295,936 (93.9%) as cached, plus 64,951
    completion tokens.
  - Run ledger: $0.469988 against a $0.95 guard.
  - All 140 requests finished in one attempt.
  - For every request, the provider-reported prompt-token count equals `glimmer_format.py`'s count.
- **Billing.**
  - Month to date read **$0.1395** both before (19:05:08 UTC) and after (19:15:06 UTC) the run. That is the
    2026-10-28 Muse Spark Open benchmark only.
  - Neither the sampling nor any training line had posted 10 minutes after the run. Re-read
    `billing/summary` later to confirm a $0 training line and about $0.47 of sampling.
- **Nothing left running.**
  - No deployment was created, and the code has no deployment path.
  - The training session was deleted at 19:15:06 UTC; no delete error was recorded.
  - Read-only re-check (needs `FIREWORKS_API_KEY` exported in that shell):
    `.venv/bin/python eval/glimmer_sft/fw_account.py --out /tmp/idle.json --idle-check ts-904120baaa334a9e9cb23f3e211d61a1 --model nocap-glimmer-sft-r20261030-190510`
- **Records.**
  - `run_record.json`: ids, config, plan, training curve, per-model usage and cost, billing snapshots, evidence
    store hashes. The account slug is redacted.
  - `run_requests.jsonl`: one row per request, with tokens, cost and Fireworks request id.
  - `eval_results.json`: every metric above.
  - The raw answers were not kept. Re-scoring on newer code needs a new sampling run, about $0.47.

## Offline verification

| Check | Result |
|---|---|
| `glimmer_format.render_text` vs the model's own `chat_template.jinja` (HF `meta-models/Muse-Glimmer-30B` @ `cec94fcf`), with and without a generation prompt | 548 / 548 identical |
| Workshop "prepare" example re-rendered with the cookbook defaults | identical bytes; 402 tokens, 34 trained (the cookbook reported 402 and 34) |
| Piecewise (cookbook order) vs whole-string tokenization, all 273 rows | 0 mismatches; trained tokens decode to exactly ` to=user<|message|>` + answer + `<|eot|>` |
| Server-reported vs client prompt tokens, 140 requests | equal on every request |
| Per-passage claim building vs document-level `extract_detailed` on the gold text | identical (28 claims) |
| `evaluate.py --self-test` (tuned = teacher; base = teacher in a code fence minus its last claim) | passes |
| `eval_results.json` recomputed with the evaluation-time `receipts/store.py` | every section identical |

## Limitations

- **Small, in-distribution test set.**
  - 70 passages (10 gold), one run, one seed, hyperparameters fixed before seeing results.
  - The test passages come from the same ten companies and press-release style as the training data. Groups are
    disjoint, but phrasing is shared.
  - The result says nothing about other filers or document styles.
- **The teacher is a reference, not ground truth.** Outside the 27 gold claims and the 34 seeded errors,
  "agreement" means agreement with Spark 1.3.
  - Exact claim-set agreement rewards imitating the teacher's `period_text` habits, which are themselves
    inconsistent.
  - The verifier resolves either form of a period phrase, so this gain does not reach the verdicts.
- **Not the product configuration.**
  - Production Glimmer on Modal uses vLLM `json_schema` guided decoding, the model's default sampling
    (temperature 1.0, top-p 0.95, top-k 64) and `max_tokens` 12,000.
  - Here sampling was greedy, unguided, capped at 4,096 tokens, and ran through Fireworks' training sampler.
    No answer reached the cap: the longest was 1,427 tokens.
- **Reasoning is suppressed.** The targets are answers only, so the tuned model stopped writing its `to=self`
  reasoning (0 reasoning tokens). That is where its token, latency and cost savings come from. Meta's
  post-training guide warns that answer-only SFT suppresses chain-of-thought.
- **Serving.** The adapter can't be used in NoCap today. Fireworks can't serve trained LoRAs serverless, so it
  would need an on-demand deployment billed per GPU-hour, or loading the adapter into the Modal vLLM server.
  Neither was done or tested.
- **Code drift.**
  - Scoring used the `receipts/` sources hashed in `eval_results.json`, which match those used to build the data,
    and the evidence store hashed in `run_record.json`.
  - `receipts/store.py` changed after scoring, as part of other work.
  - Re-run before quoting verdict-level numbers on newer code. The raw answers would have to be re-sampled.
- **Multiple comparisons.** Five paired tests, uncorrected. The claim-set p = 0.023 doesn't survive Bonferroni.

## Reproduce

```bash
# A small venv outside the repo (about 115 MB; no torch, no transformers)
python3 -m venv --without-pip /path/to/venv
PIP_NO_CACHE_DIR=1 python3 -m pip --python /path/to/venv/bin/python install \
  "fireworks-ai[training-sdk]==1.2.30" "tokenizers==0.22.2"
# Glimmer tokenizer files only (28 MB), pinned revision
REV=cec94fcfd6512f2cfe708138c32533b346ea4f69
for f in tokenizer.json chat_template.jinja; do
  curl -sSL -o /path/to/tok/$f https://huggingface.co/meta-models/Muse-Glimmer-30B/resolve/$REV/$f; done
# Data (offline; needs the local Spark cache) and checks; the file hashes must match the table below
.venv/bin/python eval/glimmer_sft/build_data.py --out /path/to/data
/path/to/venv/bin/python -I eval/glimmer_sft/selfcheck.py --tokenizer /path/to/tok/tokenizer.json \
  --rows /path/to/data/train.jsonl /path/to/data/test.jsonl
/path/to/venv/bin/python -I eval/glimmer_sft/train_and_sample.py --data /path/to/data \
  --tokenizer /path/to/tok/tokenizer.json --run-dir /path/to/run --dry-run
# Live (export FIREWORKS_API_KEY in this shell yourself): a new run, new session, new sampling cost
/path/to/venv/bin/python -I eval/glimmer_sft/fw_account.py --out /path/to/run/account_before.json
/path/to/venv/bin/python -I eval/glimmer_sft/train_and_sample.py --data /path/to/data \
  --tokenizer /path/to/tok/tokenizer.json --run-dir /path/to/run
/path/to/venv/bin/python -I eval/glimmer_sft/fw_account.py --out /path/to/run/account_after.json
# Score (offline)
.venv/bin/python eval/glimmer_sft/evaluate.py --samples /path/to/run/samples.jsonl \
  --test-meta /path/to/data/test_meta.jsonl --out eval/glimmer_sft/eval_results.json --hand 10
```

## Hashes

| Item | SHA-256 |
|---|---|
| `train.jsonl` (203 rows) | `711271418a18d3834d565a72bde91e1e35d8a5d2b279a2c5ff8135bafed1bdc5` |
| `test.jsonl` (70 rows) | `0c6d94f5b95c0d16c3a3c49a4e919affeaa9b10084fb9e436e64ba933dcd639f` |
| `test_meta.jsonl` | `8fc919ce908b0114ad2cf8f50e23a3816fb2d322095049dca5013807eb4c2f28` |
| `CHAT_INSTRUCTIONS` (system prompt) | `9205d18e3c527deb38bda893714ff838b26f1cb130fe5be055b70a63a5337b69` |
| JSON schema | `e221cbb47698437e09d625341e5613f4e07f9a9f19241e3e6ca0a7ea0770b187` |
| Development rows used | `840e899834f5fa602e2307a5da64518b810744f54dfda1ee93c54b148413ed71` |
| `tokenizer.json` (Muse-Glimmer-30B @ `cec94fcf`) | `c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d` |
| `chat_template.jinja` (same revision) | `cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678` |
| `samples.jsonl` of the run (not kept) | `run_record.json` → `run_files_sha256` |
| `data/facts.sqlite` at scoring | `30c102a5935c5e600c3b49aac036f6d583af33ad32edbdeb9d69653cddc920ae` |
| `receipts/store.py` at scoring | `3f621e7643eb501122efbe60ec0d31748127560a918cd761451119a172d6ae8a` |

The other source-file hashes are listed elsewhere:

- `data_manifest.json` (at build): `receipts/*.py`, `eval/eval.py`, `eval/bench_models.py`, the corpus and the gold
  files.
- `eval_results.json` (at scoring): the same set plus `evaluate.py`.

`data_manifest.json` also records each passage's split, filter outcome, target hash and teacher cache file.
