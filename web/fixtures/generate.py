"""Build the mock-mode fixtures in web/fixtures/ from the real NoCap CLI.

    cd <repo root> && MODEL_API_KEY= .venv/bin/python web/fixtures/generate.py

MODEL_API_KEY= (empty) keeps this free and offline: every Muse Spark response must come from
data/cache/spark, and a cache miss fails with "Nothing was sent" instead of calling the API.

For each example draft the script runs `python -m receipts check - --json` once with no as-of
(latest filings), then once at every EDGAR acceptance time of the companies involved. Verdicts can
only change at those instants, so consecutive identical results are merged into one timeline
segment. The mock route replays the segment whose `valid_from` is the latest one at or before the
requested as-of, which makes mock time travel exact for these drafts.

Each run is stored as the SSE event sequence from docs/api-contract.md. Placeholders filled per
request by the mock route: {{DRAFT_HASH}}, {{AS_OF_ISO}} (2026-08-26T20:00:00Z) and {{AS_OF_TEXT}}
(2026-08-26 20:00:00 UTC, the verifier's own format inside look-ahead explanations).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PY = ROOT / ".venv" / "bin" / "python"
sys.path.insert(0, str(ROOT))

from receipts import extract as ex  # noqa: E402  (read-only: request hashes and cached usage)
from receipts import ask  # noqa: E402
from receipts.store import Store  # noqa: E402

NVDA_Q1_CONTEXT = "NVIDIA (NVDA), first quarter of fiscal 2027, the quarter ended April 26, 2026"
# Three sentences from NVIDIA's Q1 FY2027 earnings release (eval corpus v0, dev split). Two carry
# the corpus' seeded errors: "$81.6 million" (scale) and "GAAP ... $2.39 and $1.87" (basis).
NVDA_Q1_TEXT = "\n\n".join([
    "SANTA CLARA, Calif.—May 20, 2026―NVIDIA (NASDAQ: NVDA) today reported record revenue for the first quarter "
    "ended April 26, 2026, of $81.6 million, up 20% from the previous quarter and up 85% from a year ago.",
    "For the quarter, GAAP earnings per diluted share were $2.39 and $1.87, respectively.",
    "Data Center networking revenue was a record $14.8 billion, up 199% from a year ago and up 35% sequentially.",
]) + "\n"
DRAFT_6 = (ROOT / "examples" / "draft_6claims.txt").read_text(encoding="utf-8")

# Wall time of the original, uncached extraction (seconds): examples/README.md reports 3.8 s for the
# six-claim draft; for the NVIDIA draft it is the slowest of its three passages in the Day 2 eval run
# (data/eval_runs/dev_full_day2.json), since passages are extracted in parallel.
FIXTURES = [
    {"id": "draft-6claims", "text": DRAFT_6, "context": None, "latency_s": 3.8},
    {"id": "nvda-q1-fy2027", "text": NVDA_Q1_TEXT, "context": NVDA_Q1_CONTEXT, "latency_s": None},
]
NVDA_Q1_EVAL_ITEMS = ["v0-NVDA-FY2027Q1-s000347-scale", "v0-NVDA-FY2027Q1-s000640-basis", "v0-NVDA-FY2027Q1-s002776"]

EXAMPLES = [
    {
        "id": "earnings-roundup",
        "fixture": "draft-6claims",
        "title": "Earnings roundup: NVIDIA, Microsoft, Meta",
        "description": "Six figures from three earnings reports. Two of them are wrong.",
        "as_of": None,
    },
    {
        "id": "earnings-day-time-travel",
        "fixture": "draft-6claims",
        "title": "Time travel: 21 minutes before NVIDIA's 8-K",
        "description": "The same draft, checked as of 20:00 UTC on Aug 26, 2026, before the numbers were public.",
        "as_of": "2026-08-26T20:00:00Z",
    },
    {
        "id": "nvda-q1-recap",
        "fixture": "nvda-q1-fy2027",
        "title": "NVIDIA Q1 FY2027 recap",
        "description": "A scale slip, a GAAP vs non-GAAP swap, and segment figures no filing breaks out.",
        "as_of": None,
    },
]

EPOCH = "2000-01-01T00:00:00Z"


def fmt_text(iso: str) -> str:
    """receipts.verify.fmt_ts: '2026-08-26 20:00:00 UTC'."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return f"{dt:%Y-%m-%d %H:%M:%S} UTC"


def run_cli(text: str, context: str | None, as_of: str | None) -> dict:
    cmd = [str(PY), "-m", "receipts", "check", "-", "--json"]
    if as_of:
        cmd += ["--as-of", as_of]
    if context is not None:
        cmd += ["--context", context]
    env = dict(os.environ, MODEL_API_KEY="")  # cache-only: a miss raises instead of calling the API
    out = subprocess.run(cmd, input=text, capture_output=True, text=True, cwd=ROOT, env=env, check=False)
    if out.returncode != 0:
        raise SystemExit(f"receipts check failed ({out.returncode}): {out.stderr.strip()}")
    return json.loads(out.stdout)


def original_cost(text: str, context: str | None) -> float:
    """Model API spend of the original (uncached) extraction, from the usage stored with each cached response."""
    passages = ex.split_passages(text)
    total = 0.0
    for _, p in passages:
        rec = json.loads((ex.CACHE_DIR / f"{ex.request_hash(ex.build_request(p, context))}.json").read_text())
        total += ex.cost_usd(rec["usage"])
    return round(total, 4)


def eval_latency(item_ids: list[str]) -> float:
    rows = json.loads((ROOT / "data" / "eval_runs" / "dev_full_day2.json").read_text())["rows"]
    lat = [r["latency_s"] for r in rows if r["item_id"] in item_ids]
    if len(lat) != len(item_ids):
        raise SystemExit(f"eval latency missing for {item_ids}")
    return round(max(lat), 1)


def boundaries(tickers: set[str]) -> list[str]:
    """Every EDGAR acceptance time of these companies' filings and press-release facts, ascending."""
    con = sqlite3.connect(ROOT / "data" / "facts.sqlite")
    q = ",".join("?" * len(tickers))
    times = {r[0] for r in con.execute(
        f"select f.accepted_at from filings f join companies c on c.cik = f.cik where c.ticker in ({q})",
        sorted(tickers))}
    for t in tickers:
        for path in (ROOT / "data" / "pr_facts" / t).glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    times.add(json.loads(line)["accepted_at"])
    return sorted(times)


def normalize(results: list[dict], as_of: str | None) -> list[dict]:
    """Verdicts with the as-of time replaced by placeholders, so runs in one segment compare equal."""
    out = []
    for r in results:
        v = json.loads(json.dumps(r["verdict"]))
        if as_of is not None:
            v["explanation"] = v["explanation"].replace(f"as-of time {fmt_text(as_of)}", "as-of time {{AS_OF_TEXT}}")
            v["as_of"] = "{{AS_OF_ISO}}"
        out.append(v)
    return out


def events(claims: list[dict], verdicts: list[dict], *, passages: int, as_of_placeholder: bool,
           latency_s: float, cost_usd: float) -> list[dict]:
    counts = {s: sum(v["status"] == s for v in verdicts) for s in ("verified", "wrong", "unverifiable", "approximate")}
    seq = [{"event": "meta", "data": {"draft_hash": "{{DRAFT_HASH}}", "cached": True, "passages": passages,
                                      "as_of": "{{AS_OF_ISO}}" if as_of_placeholder else None}}]
    seq += [{"event": "claim", "data": {"claim": c}} for c in claims]
    seq += [{"event": "verdict", "data": {"claim_id": v["claim_id"], "verdict": v}} for v in verdicts]
    seq.append({"event": "done", "data": {"counts": counts, "latency_s": latency_s, "cost_usd": cost_usd,
                                          "model": ex.MODEL}})
    return seq


def build(fx: dict) -> dict:
    text, context = fx["text"], fx["context"]
    latest = run_cli(text, context, None)
    used_context = latest["context"]
    claims = [r["claim"] for r in latest["results"]]
    passages = len(ex.split_passages(text))
    cost = original_cost(text, used_context)
    latency = fx["latency_s"] if fx["latency_s"] is not None else eval_latency(NVDA_Q1_EVAL_ITEMS)
    tickers = {e["ticker"] for r in latest["results"] for e in r["verdict"]["evidence"]}

    segments: list[dict] = []
    prev = None
    for t in [EPOCH, *boundaries(tickers)]:
        res = run_cli(text, context, t)
        if [r["claim"] for r in res["results"]] != claims:
            raise SystemExit(f"{fx['id']}: claims changed at as-of {t}; extraction must not depend on as-of")
        norm = normalize(res["results"], t)
        if norm != prev:
            segments.append({"valid_from": None if t == EPOCH else t,
                             "events": events(claims, norm, passages=passages, as_of_placeholder=True,
                                              latency_s=latency, cost_usd=cost)})
            prev = norm
    print(f"{fx['id']}: {len(claims)} claims, {len(segments)} timeline segments, ${cost}, {latency} s")
    for s in segments:
        marks = "".join({"verified": "✓", "wrong": "✗", "unverifiable": "?", "approximate": "≈"}[e["data"]["verdict"]["status"]]
                        for e in s["events"] if e["event"] == "verdict")
        print(f"   from {s['valid_from'] or '-∞':22s} {marks}")
    return {
        "id": fx["id"],
        "text": text,
        "context": used_context,
        "source": "python -m receipts check - --json" + (f' --context "{context}"' if context else "")
                  + " (cached Muse Spark responses), generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "latest": {"valid_from": None,
                   "events": events(claims, [r["verdict"] for r in latest["results"]], passages=passages,
                                    as_of_placeholder=False, latency_s=latency, cost_usd=cost)},
        "timeline": segments,
    }


def health() -> dict:
    con = sqlite3.connect(ROOT / "data" / "facts.sqlite")
    built = dict(con.execute("select key, value from meta"))["built_at"][:10]
    tickers = [r[0] for r in con.execute("select ticker from companies order by ticker")]
    n = con.execute("select count(*) from facts").fetchone()[0]
    for path in (ROOT / "data" / "pr_facts").glob("*/*.jsonl"):
        n += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {"ok": True, "data_snapshot": built, "companies": tickers, "facts": n}


def ask_options() -> dict:
    """The same bounded company periods as GET /api/ask/options; no model call."""
    store = Store()
    try:
        return {"companies": ask.companies(store), "metrics": list(ask.ASK_METRICS), "examples": [
            "What was NVIDIA's GAAP revenue in Q2 FY2027?",
            "What was Meta's GAAP net income in Q3 FY2026?",
            "What was Apple's GAAP diluted EPS in Q3 FY2026?",
        ]}
    finally:
        store.close()


def main() -> None:
    (OUT / "checks").mkdir(exist_ok=True)
    by_id = {}
    for fx in FIXTURES:
        data = build(fx)
        by_id[fx["id"]] = data
        (OUT / "checks" / f"{fx['id']}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                                                          encoding="utf-8")
    examples = [{"id": e["id"], "title": e["title"], "description": e["description"],
                 "text": by_id[e["fixture"]]["text"], "as_of": e["as_of"],
                 "context": next(f["context"] for f in FIXTURES if f["id"] == e["fixture"])} for e in EXAMPLES]
    (OUT / "examples.json").write_text(json.dumps(examples, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "health.json").write_text(json.dumps(health(), indent=1) + "\n", encoding="utf-8")
    (OUT / "ask-options.json").write_text(json.dumps(ask_options(), indent=1) + "\n", encoding="utf-8")
    print("wrote", ", ".join(str(p.relative_to(OUT)) for p in sorted(OUT.rglob("*.json"))))


if __name__ == "__main__":
    main()
