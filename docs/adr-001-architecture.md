# ADR-001: NoCap architecture (Day 2, 2026-10-27)

## Principle
**The LLM proposes, code decides.** Muse Spark extracts claims and maps table rows. Deterministic code locates spans, checks cells verbatim, and computes every verdict.

## Data flow
```
draft text ─▶ extract (Muse Spark 1.3, per passage, parallel, effort minimal) ─▶ Claim[]
                                                                                   │
EDGAR ─▶ ingest ─▶ data/facts.sqlite (XBRL facts + derived quarters + filings)     ▼
EDGAR ─▶ press_release (Spark maps rows; code checks cells verbatim) ─▶ data/pr_facts/*.jsonl ─▶ EvidenceStore
                                                                                   │
                                     verify (rules, reason codes, as_of) ◀────────┘
                                                                                   ▼
                                                                Verdict[] with receipts (Fact[])
```

## Modules and owners (Day 2)
| Module | Owner |
|---|---|
| `receipts/types.py`: contracts (Claim, Fact, Verdict, ReasonCode, EvidenceStore) | main session |
| `receipts/edgar.py`, `store.py`, `ingest.py`: EDGAR client, SQLite store, point-in-time queries, derived quarters | evidence agent |
| `receipts/press_release.py`: EX-99.1 tables → press-release Facts (non-GAAP, segments, earnings-day numbers) | press-release agent |
| `receipts/extract.py`, `periods.py`, `verify.py`, `check.py`, `cli.py`: extraction, period resolution, rules, CLI | verifier agent |
| `eval/`: seeded-error corpus and `eval.py` | corpus agent |

## Decisions
- **Statuses:** verified ✓, wrong ✗, unverifiable ?, approximate ≈. Ambiguity returns ?, never ✗.
- **Tolerance:** derived from the precision written in the draft. "$96.2 billion" allows ±$0.05B, "18%" allows ±0.5 pp, "$2.46" allows ±$0.005.
- **Point in time:** every Fact carries EDGAR `accepted_at`. If a claim's only supporting evidence was accepted after `as_of`, the reason is `look_ahead`.
- **Non-GAAP figures** come only from press-release facts, since XBRL is GAAP. Guidance is `forward_looking`, so ?.
- **Storage:** SQLite plus JSONL under `data/` (gitignored and rebuildable), with a cache budget of 2 GB or less. No live EDGAR calls in production; the data is pre-ingested.
- **Hosting (Day 3):**
  - Next.js UI and Python API on Vercel, with SQLite bundled read-only.
  - Modal is used only to serve Muse Glimmer.
  - SSE streaming for verdicts.
- **SEC access:** polite client with a declared User-Agent and a shared cache in `data/cache/edgar`. Each process makes at most 2 requests per second, so parallel agents stay under SEC's 10/s limit.
- **Models:** Standard-mode IDs only; never `-contributor`.
