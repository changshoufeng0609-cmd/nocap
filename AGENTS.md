# AGENTS.md: NoCap

Project instructions for coding agents such as Codex and Muse Code. Claude Code reads `CLAUDE.md`; both files point to the same sources of truth.

- **What this is:** NoCap verifies every financial number in a draft against SEC filings. It's our entry in the Meta Global AI Developer Hackathon (Indie track, team **NoCap**).
- **Spec and design rules:** `.claude/reference/project.md`
- **Current handoff and task list:** `docs/HANDOFF.md`. Read it before doing anything.

## Commands
| Task | Command |
|---|---|
| Tests | `.venv/bin/python -m pytest -q tests/` (Python 3.12 venv in `.venv`) |
| CLI | `.venv/bin/python -m receipts check examples/nvda_q2_fy2027.txt [--as-of 2026-08-26T20:00:00Z] [--json]` |
| API | `.venv/bin/uvicorn receipts.api:app --port 8000 --no-proxy-headers` (the app reads the socket peer; see `docs/deploy.md` for trusted proxies) |
| Web | `cd web && npm run dev -- --port 3100`, which proxies `/api` to port 8000. Mock mode, no backend needed: `npm run dev:mock` |
| Eval | `.venv/bin/python eval/eval.py --split dev`. **Never run `--split test`**: the held-out split is for headline numbers, and only runs if the user asks. |
| Snapshot | `.venv/bin/python scripts/build_snapshot.py`. Run it after changing evidence data or verifier code, then commit `data/snapshot/`. |

## Rules
- **Secrets.** `.env` holds `MODEL_API_KEY` and `SEC_USER_AGENT`. Never print, log, commit, or paste them anywhere. Scripts load `.env` through python-dotenv.
- **Meta models.**
  - Use Standard-mode IDs only: `muse-spark-1.3`, `muse-wonder-1.0`, `sam-3.1`. Never use a `-contributor` ID, because Meta may train on that data.
  - On the Responses API, structured output goes in `text={"format": {"type": "json_schema", ...}}` and reasoning goes in `reasoning={"effort": ...}`.
  - Verified API details are in `.claude/reference/meta-platform.md`. Check docs at `https://dev.meta.ai/docs/<page>.md`; don't work from memory.
- **The LLM proposes, code decides.** Models extract or propose. Deterministic code computes every verdict. Ambiguity returns "?", never "✗".
- **SEC EDGAR.** Go through `receipts/edgar.py`, which sends a declared User-Agent, stays at or below 2 req/s, and caches in `data/cache/edgar`.
- **Spend.** Keep Meta API spend per task small, and report it. It comes out of the user's $150 credit and card.
- **New-work rule.** All code is new and written during the hackathon, starting 2026-10-27. Never copy code from the user's other repositories.
- **Git.**
  - Commit small, focused changes with clear messages, and push to the private `origin`.
  - Don't make the repo public, deploy, or create cloud resources without the user's explicit OK.
- **Language.** Talk to the user in 繁體中文. Judge-facing text, such as the README and UI copy, is in English.
