# Meta Global AI Developer Hackathon

This folder is the user's entry for the Meta Global AI Developer Hackathon (Meta × Cerebral Valley, virtual, 10 days).

- **Project (decided 2026-10-27):** **NoCap** (renamed from Receipts by the user on 2026-10-28), a fact-checker that verifies every financial number in a draft against SEC filings, built for financial journalists and analysts. Indie track, Build for Your World. Spec: `.claude/reference/project.md`.

- **Submission deadline: Thu 11/05 10:00 Taipei** (Wed 11/04 9:00 PM ET). Treat 11/05 09:00 Taipei as the hard stop. Credit claims close at the same time.
- **Official rubric:** Impact 30%, Use of Meta AI Tooling 25%, Technical Depth & Execution 20%, Demo 25%. Stage One is a pass/fail check that the project fits a theme and genuinely uses Meta models.
- **Hard rules:**
  - The repo must be public.
  - Teams can have up to 4 people.
  - Only work built during the event can be demoed.
  - A 3-minute demo video is required.
  - Anti-projects are excluded, including medical advice, basic RAG, simple image analyzers, and anything political or involving war.
- Rules, links, schedule, prizes, and credits, taken from the logged-in Participant Guide: `.claude/reference/hackathon-brief.md`
- Meta models, Model API, open weights, Muse Code: `.claude/reference/meta-platform.md`

## Working rules
- **Language:** talk to the user in 繁體中文. Everything judges see (README, UI copy, demo script, submission text, slides) is in English unless the user says otherwise.
- **Meta docs first:** Muse Spark, Muse Wonder, Muse Image, and SAM 3.1 post-date most training data. Before writing Meta API code, fetch the matching `https://dev.meta.ai/docs/<page>.md` (index: https://dev.meta.ai/llms.txt). When the docs disagree with a reference file, fix the reference file.
- **Secrets and data:** API keys go in env vars (`MODEL_API_KEY`) and a gitignored `.env`, never in code, logs, or screenshots. Use Standard-mode model IDs for any private data, because Meta can train on anything sent to `-contributor` IDs.
- **Scope:** optimize for the rubric. One working, demo-able core flow beats breadth. Cut scope before you cut reliability.
- **Local machine:** Apple M4, 16 GB RAM, about 9 GB free disk. That's too small for local open-weight inference, so host open-weight models on Modal, Baseten, or Fireworks.

## Expert team
Hackathon experts in this repo (`.claude/agents/`):

| Expert | When to use |
|---|---|
| `meta-hackathon-strategist` | **Start here.** Ideas and scoring, track and theme, scope, daily plan, re-planning, rules and submission checklist |
| `meta-model-api-engineer` | Muse Spark and Muse Wonder: Responses API, tool calling, agents, structured output, search grounding, cost and rate limits |
| `meta-multimodal-engineer` | SAM 3.1 segmentation and tracking, Muse Image, image, video, and audio understanding, Voice Transcribe |
| `meta-open-weights-engineer` | Muse Spark Open and Muse Glimmer on Modal, Baseten, or Fireworks; fine-tuning; sponsor credits |
| `hackathon-demo-producer` | Demo video, voiceover script, recording, README, submission write-up, pitch |
| `hackathon-judge` | Independent rubric scoring and a hands-on check that the product works, at each milestone and before submitting |

The general Agency Agents are installed user-wide (`~/.claude/agents/`) and already work here. Call them by exact name:

| Area | Agents |
|---|---|
| Product | `Product Manager`, `Trend Researcher`, `UX Researcher`, `Business Strategist` (startup track) |
| Build | `AI Engineer`, `Rapid Prototyper`, `Software Architect`, `Backend Architect`, `Frontend Developer`, `Mobile App Builder`, `Multi-Agent Systems Architect`, `Prompt Engineer`, `RAG Pipeline Engineer`, `MCP Builder`, `Voice AI Integration Engineer`, `LLM Post-Training Engineer`, `XR Immersive Developer`, `Embedded Firmware Engineer` |
| Quality | `Code Reviewer`, `AI-Generated Code Security Auditor`, `API Tester`, `Test Automation Engineer`, `Performance Benchmarker`, `Reality Checker` |
| Design and story | `UI Designer`, `UX Architect`, `UI Finish-Gate Reviewer`, `Whimsy Injector`, `Visual Storyteller`, `Short-Video Editing Coach`, `Technical Writer` |
