# Meta AI platform reference

> **Chosen project (2026-10-27):** see `.claude/reference/project.md` (Receipts, a financial fact-checker). It describes which model does which job.

Checked against https://dev.meta.ai on 2026-10-27. Every docs page also comes as Markdown: append `.md` to the URL (for example https://dev.meta.ai/docs/tool-calling.md). https://dev.meta.ai/llms.txt indexes every page. Muse models post-date most LLM training data, so fetch the relevant page before writing code, and update this file when the docs change.

## Connection
- **Base URL:** `https://api.meta.ai/v1` for OpenAI-compatible clients. Anthropic-compatible clients (Anthropic SDK, Claude Code) use `https://api.meta.ai` and append `/v1/messages` themselves.
- **Auth:** send `Authorization: Bearer $MODEL_API_KEY`. Create the key at https://dev.meta.ai under API keys → Create API key. The OpenAI SDK doesn't read `MODEL_API_KEY` on its own, so pass `api_key=` explicitly.
- **Protocols:**
  - **Responses** (`/v1/responses`): recommended for agents. It keeps state on the server through `previous_response_id`, has built-in tools such as `web_search`, and supports `background: true`.
  - **Chat Completions:** stateless.
  - **Messages:** Anthropic-compatible; the client manages state.
- **Discovery:** `GET /v1/models` lists the API-hosted models, newest first. The unauthenticated status endpoint is documented on the `api-reference/status` page.

## Hosted models
| Model | ID | Input → output | Context (tokens) | Notes |
|---|---|---|---|---|
| Muse Wonder | `muse-wonder-1.0` | text, image, video, audio, PDF → text | 1,048,576 | Most capable; long-running agents; computer use. Standard mode only; preview |
| Muse Spark 1.3 | `muse-spark-1.3`, `muse-spark-1.3-contributor` | same → text | 1,048,576 | Default fast model; weak audio understanding |
| Muse Spark 1.2 | `muse-spark-1.2`, `muse-spark-1.2-contributor` | same → text | 1,048,576 | Muse Code's default; better for audio |
| Muse Spark 1.1 | `muse-spark-1.1` | same → text | 1,048,576 | Standard mode only |
| Muse Image | `muse-image-1.0` | text, image → image | n/a | Generation and editing |
| Muse Voice Transcribe | `muse-voice-transcribe-1.0` | audio → transcript | n/a | File and realtime transcription |
| SAM 3.1 | `sam-3.1` | noun phrase, boxes, or points, plus image or video → boxes, masks, tracks | n/a | Called through `/v1/responses` |

- **Wonder and Spark capabilities:**
  - Parallel tool calling, structured output (JSON schema), and search grounding with citations
  - Reasoning effort from minimal to max (`max` isn't available on contributor IDs). The parameter differs by API. **Responses API:** `reasoning={"effort": "low"}`. **Chat Completions:** `reasoning_effort`. Verified live on 2026-10-27.
  - **Structured output on Responses:** `text={"format": {"type": "json_schema", "name": ..., "schema": ..., "strict": True}}`, which is flat. `response_format` is the Chat Completions form; on Responses the docs conflict on whether it's silently ignored or returns a 400. Verified live on 2026-10-27.
  - **Retries:** the docs say don't retry 504, but openai 3.x retries it by default. Disable SDK retries and handle 429 and 503 yourself.
  - **Measured latency and cost on 2026-10-27** (muse-spark-1.3 at effort low, typed-claim extraction over 2 paragraphs, 9 claims): 27–56 s and about $0.02–0.04 per call, with 4–7K reasoning tokens. Prompt caching hits on repeat calls. A trivial call at default effort used about 500 output tokens and took 6.8 s.
  - Automatic prefix prompt caching, token counting, and tool search
  - Files API, plus image, video, and audio understanding
  - Computer use (Wonder only)
- **Regional limits:** availability varies by country. Muse Wonder isn't available in South Korea, and Muse Image isn't available in India.

## Pricing (pay-as-you-go)
Per 1M tokens:

| Model | Mode | Cached input | Input | Output |
|---|---|---|---|---|
| `muse-wonder-1.0` | Standard | $0.20 | $4.00 | $20.00 |
| `muse-spark-1.1`, `1.2`, `1.3` | Standard | $0.15 | $1.25 | $4.25 |
| `muse-spark-1.2-contributor`, `1.3-contributor` | Contributor | $0.002 | $0.10 | $0.20 |

Other prices:

| Service | Price |
|---|---|
| Muse Image | $0.01 per image (failed or safety-blocked images aren't billed) |
| Voice Transcribe | $0.18 per hour of audio |
| SAM 3.1, images | $2.50 per 1k images |
| SAM 3.1, video | $0.20 per 1k frames, minimum 50 frames per request |
| Web search grounding | $2.50 per 1k queries, on top of token cost |

Reasoning tokens bill as output.

**In Contributor mode, Meta may train on the prompts, code, files, context, and outputs you send.** Never send private, user, or NDA data to `-contributor` IDs. Muse Code subscriptions default to Contributor mode; change that in its settings.

## Rate limits (per team, per model)
| Model | Limit |
|---|---|
| Wonder, Spark (Standard) | 3,000 RPM, 4M TPM |
| Spark contributor IDs | 150 RPM, 3M TPM |
| SAM 3.1 | 3,000 RPM |
| Muse Image | 150 RPM |
| Voice Transcribe | 256 concurrent streams, 16,000 streams per hour |
| Background responses | 600 submissions per minute |

## Minimal calls
```python
import os
from openai import OpenAI

client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

r1 = client.responses.create(model="muse-spark-1.3", input="Summarize SAM 3.1 in one line.")
print(r1.output_text)
r2 = client.responses.create(model="muse-spark-1.3", previous_response_id=r1.id, input="Shorter.")

img = client.images.generate(model="muse-image-1.0", prompt="...", n=1, size="1536x1024")
# img.data[0].b64_json by default; response_format="url" returns a signed URL; output_format: webp | png | jpeg
```

### Muse Image
- **Rejected parameters (400):** `seed` and any other unknown field, `moderation`, `mask`, `input_fidelity`, `partial_images`, and aspect ratios wider than 16:1.
- **Tools:** only the `image_generation` tool is accepted.
- **Errors:** retry 503 with backoff. A safety block returns 400 with `content_policy_violation`.

### SAM 3.1
- **Request:** one user message containing an `input_text` noun phrase (one concept per request) plus an `input_image` or `input_video` part.
- **Video:** MP4 or MOV, encoded as H.264, H.265, or MPEG-4.
- **Output:** results stream in `output_text`, one line per image or frame. Boxes use source pixels, and track IDs stay stable across frames.
- **Masks:** decode them with `@meta-sam/parser` (TypeScript: `parseImageStream`, `recordsOfKind`, `decodeMaskToRaster`).
- Read `sam/segmenting` and `sam/reading-segmentation` before writing SAM code.
- **Verified live on 2026-10-27**, spike `spikes/c_slides.py`:
  - **Request:** send one streaming request per concept with `stream: true`. `metadata` values must be strings, for example `mask_encoding: "one_bit"` and `include_confidence: "true"`.
  - **Response:** each frame arrives as a `response.output_text.delta`, shaped like `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=0.64|><|mask;x=..;y=..;c=..;data=H,W,!...|>`. Box corners are inclusive. A zero-match result is an empty string, and it still bills one image (`usage.images_processed`).
  - **Mask codec:** not documented. Only the official Python package `meta-sam-parser` (SAM License) decodes masks. It isn't installed or verified, so Receipts uses boxes only.
  - **Box fit:** SAM boxes hug the plotted data and leave out titles and axes. For a full-chart receipt region, pad the box or use Muse Spark grounding boxes, which scored IoU 0.97 on a synthetic chart.
  - **Cost and latency:** about 0.7–3.1 s and $0.0025 per call.
- **Muse Spark chart reading, verified live 2026-10-27:**
  - A synthetic bar chart with printed data labels scored 8/8 exact at effort minimal, in 4.5 s for $0.0055.
  - Without printed labels, it reads values from the axes and lands within ±1%, but none are exact. Treat those numbers as estimates, not facts.
- **Gotchas, verified live 2026-10-27:**
  - Use `store: false` with large inline images; `store: true` can return 413.
  - SDK 3.39's `responses.stream()` helper fails on SAM's events. Use `create(stream=True)` and handle the events yourself.
  - A strict JSON schema needs an object at its root.
  - Spark rejects `effort: "none"`.
  - Grounding coordinates can come back as 1001, so clamp them.

### Voice Transcribe
- **Endpoints:** `POST /v1/asr/transcribe` for files; `wss://api.meta.ai/v1/asr/realtime` for live audio.
- **Supports:** diarization, VAD, keyword biasing, 25 evaluated languages, and turn-level timestamps.
- **Doesn't support:** text-to-speech or word-level timestamps.

## Open-weight models
- **Muse Spark Open:**
  - Mixture-of-experts with 816B total and 42B active parameters. Text, image, audio, and video in; text out.
  - Hugging Face, BF16: `meta-models/Muse-Spark-Open-816B-A42B` (about 1.65 TB, 8×B300).
  - Hugging Face, NVFP4: `meta-models/Muse-Spark-Open-816B-A42B-NVFP4` (about 537 GB, 4×B300).
  - Runs on SGLang or vLLM. For the hackathon, use a hosted provider; the `muse-spark-open/deploy#hosted-providers` page lists them.
  - On Baseten, use model `meta-models/muse-spark-open-1026` for the hackathon, not `meta-models/muse-spark-open` as the docs say (per the Participant Guide).
- **Muse Glimmer:**
  - 30B parameters, distilled from Muse Spark. Text and image in; text out.
  - 128K context by default; Apache-2.0 license.
  - Runs on vLLM, SGLang, llama.cpp (GGUF), Ollama, and LM Studio. Built for local and edge agents.
- **Fine-tuning docs:** `muse-spark-open/post-training`, `muse-glimmer/post-training`, and the cookbook pages `muse-spark-open-torchtitan-sft` and `muse-spark-open-torchtitan-rl`.
- **This Mac** (M4, 16 GB RAM, about 9 GB free disk) can't hold Glimmer, which needs roughly 17–20 GB even at 4-bit (an estimate). Use Modal, Baseten, or Fireworks.

## Muse Code (Meta's coding agent)
- **Install:** `curl -fsSL https://dev.meta.ai/install.sh | sh`. It isn't installed yet; the `muse` on this Mac's PATH is cmux's wrapper, which looks for the real binary.
- **Commands:** `muse` (interactive), `muse exec "<prompt>"` (scripts and CI), `muse serve`.
- **Defaults:** model `muse-spark-1.2`; CI authenticates with `META_API_KEY`.
- **Instruction files:** at each directory level the first match wins: `AGENTS.md`, `CLAUDE.md`, `.agents/AGENTS.md`, `.claude/CLAUDE.md`. Project files load only after you trust the workspace, so this repo's CLAUDE.md also applies to Muse Code.
- **Skills:** `.agents/skills/<id>/SKILL.md`. Muse Code also scans `.claude/skills` and `~/.claude/skills`.
- **Other locations:** memory in `.agents/memory/`, hooks in `.muse/hooks.json`, and MCP servers in the `mcp_servers` block of `~/.config/muse/settings.json` (which must contain `"schema_version": 1`).
- **Agents:** Muse Code doesn't read `.claude/agents/`, so this repo's experts only run in Claude Code.

## Running other coding agents on Muse models (optional)
- **Claude Code:**
  - `ANTHROPIC_BASE_URL=https://api.meta.ai`
  - `ANTHROPIC_AUTH_TOKEN=$MODEL_API_KEY` (not `ANTHROPIC_API_KEY`)
  - Point `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, and `CLAUDE_CODE_SUBAGENT_MODEL` at Muse IDs
  - `ENABLE_TOOL_SEARCH=true`
- **Codex:** add a `meta` provider with `wire_api = "responses"`.
- Full setup is on the `coding-agents` page.

## Doc pages worth knowing
- **Guides:** `quickstart`, `sdks`, `protocols/responses`, `tool-calling`, `structured-output`, `search-grounding`, `reasoning`, `prompt-caching`, `file-handling`, `image-understanding`, `video-understanding`, `computer-use`, `error-handling`, `token-counting`, `tool-search`, `agent-frameworks`
- **Media:** `image-generation`, `media-segmentation`, `sam/overview`, `sam/segmenting`, `sam/reading-segmentation`, `sam/client-libraries`, `speech-to-text`
- **Cookbook** (`cookbook/<name>`):
  - `use-cases`, `agent-patterns`, `multi-agent-orchestration`, `long-context`, `run-locally`
  - `rayban-openclaw` (vision Q&A on smart glasses)
  - `perception-grounding`, `perception-chart-analysis`, `sam-api-basics`, `video-to-sticker-pack`, `headlocked-speech-bubble-captions`
  - `voice-chess-cua`, `computer-use-macos`
  - `search-grounded-image-generation`, `anchored-image-series`
