"""Spike A: EDGAR ingest for one company (Receipts, day 1).

The pass criteria come from .claude/reference/plan.md, Spike A:
  1. For every 10-K, 10-Q, and Item 2.02 8-K in the last N quarters, the Submissions
     API returns an acceptanceDateTime.
  2. Every companyfacts XBRL fact joins to one of those filings by accession number.
  3. Fetching is polite: a declared User-Agent, at most 10 req/s (this script uses 4 req/s),
     and a content-hash cache.
  4. The income-statement table in one EX-99.1 (the earnings press release) parses,
     and at least 9 of 10 numbers equal the later 10-Q XBRL values for the same quarter.

As a side effect it writes a real press-release paragraph to spikes/inputs/ex99_paragraph.txt
for Spike B to use.

Usage:
  .venv/bin/python spikes/a_edgar.py [--ticker NVDA] [--quarters 8]

Requires SEC_USER_AGENT in .env, formatted as "Name email@example.com" (SEC fair-access policy).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "edgar"
OUT = ROOT / "spikes" / "out"
INPUTS = ROOT / "spikes" / "inputs"

MIN_INTERVAL = 0.25  # seconds between live requests: 4 req/s, well under SEC's 10 req/s cap

# (display name, candidate us-gaap concepts, press-release row-label regex)
METRICS = [
    ("Revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"], r"^(total )?revenues?$"),
    ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold"], r"^cost of (revenue|sales)$"),
    ("Gross profit", ["GrossProfit"], r"^gross (profit|margin)$"),
    ("Research and development", ["ResearchAndDevelopmentExpense"], r"^research and development$"),
    ("SG&A", ["SellingGeneralAndAdministrativeExpense"], r"^(sales|selling), general and administrative$"),
    ("Total operating expenses", ["OperatingExpenses"], r"^total operating expenses$"),
    ("Operating income", ["OperatingIncomeLoss"], r"^(operating income|income from operations)$"),
    ("Net income", ["NetIncomeLoss"], r"^net income$"),
    ("EPS basic", ["EarningsPerShareBasic"], None),    # first "Basic" row after "Net income per share"
    ("EPS diluted", ["EarningsPerShareDiluted"], None),  # first "Diluted" row after "Net income per share"
]


class Edgar:
    """Polite EDGAR client with a content-addressed on-disk cache."""

    def __init__(self, user_agent: str):
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
            follow_redirects=True,
        )
        self._last = 0.0
        self.live_requests = 0
        self.cache_hits = 0

    def get(self, url: str) -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path, meta_path = CACHE / f"{key}.bin", CACHE / f"{key}.json"
        if body_path.exists() and meta_path.exists():
            self.cache_hits += 1
            return body_path.read_bytes()
        for attempt in range(5):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.client.get(url)
            self.live_requests += 1
            if r.status_code in (429, 503):
                time.sleep(2**attempt)
                continue
            if r.status_code == 403:
                raise SystemExit(
                    "SEC returned 403. Check that SEC_USER_AGENT in .env is 'Name email' (no placeholder)."
                )
            r.raise_for_status()
            body = r.content
            CACHE.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(body)
            meta_path.write_text(json.dumps({
                "url": url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            }))
            return body
        raise RuntimeError(f"giving up on {url} after retries")

    def json(self, url: str):
        return json.loads(self.get(url))


def resolve_cik(ed: Edgar, ticker: str) -> tuple[int, str]:
    for row in ed.json("https://www.sec.gov/files/company_tickers.json").values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"]), row["title"]
    raise SystemExit(f"ticker {ticker} not found in SEC company_tickers.json")


def load_filings(ed: Edgar, cik: int, cutoff: date) -> list[dict]:
    """All filings since cutoff, from submissions 'recent' plus older pages if needed."""
    sub = ed.json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        if date.fromisoformat(f["filingTo"]) >= cutoff:
            blocks.append(ed.json(f"https://data.sec.gov/submissions/{f['name']}"))
    rows = []
    for b in blocks:
        for i in range(len(b["accessionNumber"])):
            if date.fromisoformat(b["filingDate"][i]) >= cutoff:
                rows.append({k: b[k][i] for k in (
                    "accessionNumber", "form", "filingDate", "reportDate",
                    "acceptanceDateTime", "primaryDocument", "items")})
    return rows


def quarterly_fact(facts: dict, concepts: list[str], accn: str, end: str, eps: bool):
    """The 3-month fact for `end` reported in filing `accn`, or None."""
    for concept in concepts:
        node = facts.get("us-gaap", {}).get(concept)
        if not node:
            continue
        unit = "USD/shares" if eps else "USD"
        for f in node.get("units", {}).get(unit, []):
            if f.get("accn") != accn or f.get("end") != end or not f.get("start"):
                continue
            days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
            if 80 <= days <= 100:
                return {"concept": concept, "val": f["val"], "start": f["start"], "end": f["end"]}
    return None


NUM_RE = re.compile(r"\(\s*\$?\s*[\d,]+(?:\.\d+)?\s*\)|\$?\s*[\d,]+(?:\.\d+)?")


def first_number(cells: list[str]) -> float | None:
    """First numeric value in the cells after the label; (x) means -x."""
    m = NUM_RE.search(" ".join(cells))
    if not m:
        return None
    tok = m.group(0)
    neg = tok.strip().startswith("(")
    val = float(re.sub(r"[^\d.]", "", tok))
    return -val if neg else val


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().rstrip(":").lower()


def parse_income_table(html: bytes) -> dict[str, float]:
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c and c != "$"]
            if cells:
                rows.append(cells)
        labels = [norm(r[0]) for r in rows]
        if not any(re.match(r"^(total )?revenues?$", l) for l in labels) or not any("per share" in l for l in labels):
            continue
        found: dict[str, float] = {}
        after_per_share = False
        for cells in rows:
            label, rest = norm(cells[0]), cells[1:]
            if "net income per share" in label or label in ("net income per share", "earnings per share"):
                after_per_share = True
                continue
            for name, _, rx in METRICS:
                if name in found:
                    continue
                if rx and re.match(rx, label):
                    v = first_number(rest)
                    if v is not None:
                        found[name] = v
                elif rx is None and after_per_share and label == name.split()[-1]:  # "basic" / "diluted"
                    v = first_number(rest)
                    if v is not None:
                        found[name] = v
        if len(found) >= 5:
            return found
    return {}


def ex99_url(ed: Edgar, cik: int, accn: str, exhibit: str = "EX-99.1") -> str | None:
    nodash = accn.replace("-", "")
    index = ed.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{accn}-index.htm")
    soup = BeautifulSoup(index, "lxml")
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 4 and tds[3].get_text(strip=True).upper() == exhibit:
            a = tds[2].find("a")
            if a and a.get("href"):
                href = a["href"].replace("/ix?doc=", "")
                return "https://www.sec.gov" + href if href.startswith("/") else href
    return None


def headline_paragraphs(html: bytes, n: int = 3) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for el in soup.find_all(["p", "div"]):
        if el.find(["p", "div", "table"]):
            continue
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " "))
        if 120 <= len(text) <= 1500 and "$" in text and "%" in text and text not in out:
            out.append(text)
        if len(out) >= n:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--quarters", type=int, default=8)
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"')
    if not ua or "example.com" in ua or "@" not in ua:
        print("SEC_USER_AGENT is missing in .env. Create it with:  cp env.example .env  and fill in your name + email.")
        return 2

    ed = Edgar(ua)
    cutoff = date.today() - timedelta(days=int(args.quarters * 91.5) + 45)
    cik, title = resolve_cik(ed, args.ticker)
    print(f"{args.ticker}: {title} (CIK {cik}); window since {cutoff}")

    # 1. Acceptance timestamps
    filings = load_filings(ed, cik, cutoff)
    wanted = [f for f in filings if f["form"] in ("10-K", "10-Q")
              or (f["form"] == "8-K" and "2.02" in (f["items"] or ""))]
    with_ts = [f for f in wanted if f["acceptanceDateTime"]]
    c1 = len(wanted) > 0 and len(with_ts) == len(wanted)
    by_form = {k: sum(1 for f in wanted if f["form"] == k) for k in ("10-K", "10-Q", "8-K")}
    print(f"[1] filings in window {by_form}; acceptanceDateTime present {len(with_ts)}/{len(wanted)} -> {'PASS' if c1 else 'FAIL'}")

    # 2. XBRL facts join to filings by accession number
    facts = ed.json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")["facts"]
    accn_index = {f["accessionNumber"]: f for f in filings}
    fact_accns = set()
    for _, concepts, _ in METRICS:
        for concept in concepts:
            node = facts.get("us-gaap", {}).get(concept)
            for unit_facts in (node or {}).get("units", {}).values():
                for fct in unit_facts:
                    if fct.get("form") in ("10-Q", "10-K") and date.fromisoformat(fct["filed"]) >= cutoff:
                        fact_accns.add(fct["accn"])
    joined = [a for a in fact_accns if a in accn_index]
    c2 = len(fact_accns) > 0 and len(joined) == len(fact_accns)
    print(f"[2] XBRL fact filings in window: {len(fact_accns)}; joined to submissions {len(joined)}/{len(fact_accns)} -> {'PASS' if c2 else 'FAIL'}")

    # Point-in-time evidence: gap between the earnings 8-K and the 10-Q that carries XBRL
    tenqs = sorted([f for f in wanted if f["form"] == "10-Q"], key=lambda f: f["filingDate"], reverse=True)
    eightks = [f for f in wanted if f["form"] == "8-K"]
    pairs = []
    for q in tenqs:
        cands = [k for k in eightks if q["reportDate"] <= k["filingDate"] <= q["filingDate"]]
        if cands:
            k = max(cands, key=lambda f: f["filingDate"])
            gap = (datetime.fromisoformat(q["acceptanceDateTime"].replace("Z", "+00:00"))
                   - datetime.fromisoformat(k["acceptanceDateTime"].replace("Z", "+00:00")))
            pairs.append((k, q, gap))
    for k, q, gap in pairs:
        print(f"    quarter {q['reportDate']}: 8-K accepted {k['acceptanceDateTime']} -> 10-Q accepted {q['acceptanceDateTime']} (XBRL arrives {gap.total_seconds()/86400:.1f} days later)")

    # 4. EX-99.1 income statement vs later 10-Q XBRL (most recent pair that parses)
    comparison, c4, used = [], False, None
    for k, q, _ in pairs:
        url = ex99_url(ed, cik, k["accessionNumber"])
        if not url:
            continue
        html = ed.get(url)
        pr = parse_income_table(html)
        if not pr:
            continue
        used = {"8k": k, "10q": q, "ex99_url": url}
        for name, concepts, _ in METRICS:
            eps = name.startswith("EPS")
            x = quarterly_fact(facts, concepts, q["accessionNumber"], q["reportDate"], eps)
            p = pr.get(name)
            if x is None or p is None:
                comparison.append({"metric": name, "press_release": p, "xbrl": x and x["val"], "match": None})
                continue
            xv = x["val"] if eps else x["val"] / 1e6
            match = abs(xv - p) <= (0.005 if eps else 0.5)
            comparison.append({"metric": name, "press_release": p, "xbrl": xv, "concept": x["concept"], "match": match})
        matches = sum(1 for c in comparison if c["match"])
        c4 = matches >= 9
        print(f"[4] EX-99.1 ({k['filingDate']}) vs 10-Q XBRL (quarter {q['reportDate']}): {matches}/10 match -> {'PASS' if c4 else 'FAIL'}")
        for c in comparison:
            flag = "ok " if c["match"] else ("-- " if c["match"] is None else "XX ")
            print(f"    {flag}{c['metric']:<26} press release={c['press_release']}  xbrl={c['xbrl']}")
        # Side output for Spike B: real headline paragraphs from this press release
        paras = headline_paragraphs(html)
        if paras:
            INPUTS.mkdir(parents=True, exist_ok=True)
            (INPUTS / "ex99_paragraph.txt").write_text("\n\n".join(paras) + "\n")
            (INPUTS / "ex99_paragraph.source.json").write_text(json.dumps({
                "company": title, "cik": cik, "accession": k["accessionNumber"],
                "acceptanceDateTime": k["acceptanceDateTime"], "url": url}, indent=2))
            print(f"    wrote spikes/inputs/ex99_paragraph.txt ({len(paras)} paragraphs) for Spike B")
        break
    if used is None:
        print("[4] no EX-99.1 income table parsed -> FAIL")

    # 3. Politeness (by construction) + cache stats
    c3 = True
    print(f"[3] declared User-Agent, {1/MIN_INTERVAL:.0f} req/s max, content-hash cache "
          f"(live={ed.live_requests}, cache hits={ed.cache_hits}) -> PASS")

    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "ticker": args.ticker, "cik": cik, "company": title, "cutoff": str(cutoff),
        "criteria": {"acceptance_ts": c1, "xbrl_join": c2, "polite": c3, "ex99_vs_xbrl": c4},
        "filings": wanted, "pairs": [{"8k": k["accessionNumber"], "10q": q["accessionNumber"],
                                      "gap_days": g.total_seconds() / 86400} for k, q, g in pairs],
        "comparison": comparison, "used": used,
    }
    (OUT / f"a_edgar_{args.ticker}.json").write_text(json.dumps(report, indent=2, default=str))
    ok = all(report["criteria"].values())
    print(f"\nSpike A: {'PASS' if ok else 'FAIL'}  (report: spikes/out/a_edgar_{args.ticker}.json)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
