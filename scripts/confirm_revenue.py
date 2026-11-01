"""Confirm flagged contract revenue against press-release totals, so that ingest may serve it.

receipts.ingest holds back a company's RevenueFromContractWithCustomer figures when its SIC is financial,
real-estate or utility, or when operating-lease income is 10% or more of them (receipts.ingest.AWAITING). This
script compares each such figure in data/ingest_audit/<TICKER>.json with the GAAP, company-level revenue for the same
period in data/pr_facts/<TICKER>/*.jsonl. A company is confirmed when at least MIN_PERIODS quarterly or annual periods
compare and in every one a filed version agrees with a release total within the release figure's precision. It is then written to
data/revenue_confirmations.json with the compared periods as evidence; re-run
`python -m receipts.ingest --offline --tickers ...` for it afterwards. A company with no comparable release figure,
or with any disagreement, stays held back and is listed; a confirmed company whose re-check disagrees loses its
confirmation (review L11: --recheck re-checks every confirmed company).

It also decides, for any company, whether contract revenue is the total where a filing reports a broad revenue
concept (Revenues, SalesRevenueNet, RevenuesNetOfInterestExpense) larger than its contract revenue for one period:
when the company's own releases equal the contract figure in at least MIN_PERIODS such periods (a fourth quarter
derived as FY - 9M for each concept) and the broad figure in none, ingest serves contract revenue there. Hasbro's
filings from 2025 tag a Revenues line 13-15% above the net revenues its income statement and releases report.

It decides whether a bank's revenue is net interest income plus noninterest income, both filed by one filing for one
period: when its release totals, quarantined files included, equal that sum in at least MIN_PERIODS quarters or
years and differ in none, ingest serves the sum where the filing tags no broader revenue (Regions: "Total revenue"
$1,905M for Q2 2025). A later release total that differs revokes it.

And it decides, for any company, whether net income is the figure after preferred dividends: when the company's own
releases report GAAP net income equal to NetIncomeLossAvailableToCommonStockholdersBasic in at least MIN_PERIODS
periods where that figure differs from NetIncomeLoss and from ProfitLoss less the noncontrolling interest, and equal
to one of those in none, ingest serves the available-to-common figure as net income. Invesco's releases report
"Net income/(loss) attributable to Invesco Ltd." after its preferred dividends: -$12.5M in Q2 2025, where ProfitLoss
less the noncontrolling interest is $203.4M. A later release equal to the other figure revokes the decision.

    .venv/bin/python scripts/confirm_revenue.py TICKER [TICKER ...] [--all] [--recheck] [--dry-run] [--run-date D]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from receipts.ingest import (AUDIT_DIR, AVAILABLE_TO_COMMON, AWAITING, COMPARISON_REVENUE_CONCEPTS,  # noqa: E402
                             CONFIRMATIONS, DB_PATH, NCI_CONCEPTS, NET_INTEREST_INCOME, NONINTEREST_INCOME,
                             OTHER_REVENUE_CONCEPTS, REVENUE_COMPONENT_CONCEPTS, _whole_nci, store_run_date,
                             with_predecessors)
from receipts.press_release import PR_FACTS_DIR, _tolerance, frac_digits, parse_number  # noqa: E402
from receipts.types import Fact  # noqa: E402

MIN_PERIODS = 2
NET_INCOME_KEY = "available_to_common_is_net_income"
BANK_KEY = "nii_plus_noninterest_is_revenue"
COMPANYFACTS_DIR = REPO / "data/archive/companyfacts"
PERIODIC = ("10-Q", "10-K", "10-Q/A", "10-K/A")


def _meta(db: Path) -> dict:
    import sqlite3
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT key, value FROM meta"))
    finally:
        con.close()


def kind(start: str | None, end: str) -> str | None:
    if not start:
        return None
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return "quarter" if 80 <= days <= 125 else "annual" if 350 <= days <= 380 else None


def held(audit: dict) -> dict[tuple[str, str], set[float]]:
    """(start, end) -> contract-revenue values ingest served or held back for review, quarters and years only."""
    out: dict[tuple[str, str], set[float]] = {}
    for line in audit.get("revenue", []):
        if not kind(line["start"], line["end"]):
            continue
        values = {d["value"] for d in line["dropped"] if AWAITING in d["conditions"]}
        if line["served"] in REVENUE_COMPONENT_CONCEPTS:
            values.add(line["value"])
        if values:
            out.setdefault((line["start"], line["end"]), set()).update(values)
    return out


def release_totals(ticker: str, pr_dir: Path, quarantined: bool = False) -> dict[tuple[str, str], list[Fact]]:
    """(start, end) -> the GAAP, company-level revenue figures of the company's release files; with `quarantined`,
    also those of files the XBRL gate held back (their figures are the company's own; only serving them was refused)."""
    out: dict[tuple[str, str], list[Fact]] = {}
    paths = sorted((pr_dir / ticker).glob("*.jsonl")) + (sorted((pr_dir / ticker).glob("*.jsonl.quarantined"))
                                                         if quarantined else [])
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                f = Fact.model_validate_json(line)
                if f.metric == "revenue" and f.basis == "GAAP" and not f.segment and f.period_start:
                    out.setdefault((f.period_start.isoformat(), f.period_end.isoformat()), []).append(f)
    return out


def check(ticker: str, audit_dir: Path = AUDIT_DIR, pr_dir: Path = PR_FACTS_DIR) -> dict:
    path = audit_dir / f"{ticker}.json"
    if not path.exists():
        return {"ticker": ticker, "confirmed": False, "reason": "no ingest audit file", "periods": []}
    xbrl, releases = held(json.loads(path.read_text())), release_totals(ticker, pr_dir)
    periods, bad = [], []
    for period, values in sorted(xbrl.items()):
        facts = releases.get(period)
        if not facts:
            continue
        # A period agrees when one of its filed versions equals a release total within the release's precision. A
        # slice never does, while a later restatement may leave an earlier version a hair off (AJG, Q2 2025:
        # $3,220.8M first, $3,222.0M restated, as released).
        rows = [{"period": f"{period[0]}..{period[1]}", "xbrl": v, "release": sorted({f.value for f in facts}),
                 "release_accessions": sorted({f.accession for f in facts}),
                 "agree": any(abs(f.value - v) <= _tolerance(f) for f in facts)} for v in sorted(values)]
        periods.extend(rows)
        if not any(r["agree"] for r in rows):
            bad.extend(rows)
    if bad:
        return {"ticker": ticker, "confirmed": False, "reason": f"{len(bad)} periods disagree with the releases",
                "periods": periods}
    if len({p["period"] for p in periods if p["agree"]}) < MIN_PERIODS:
        return {"ticker": ticker, "confirmed": False,
                "reason": f"{len({p['period'] for p in periods})} comparable periods; {MIN_PERIODS} needed",
                "periods": periods}
    return {"ticker": ticker, "confirmed": True, "reason": "every compared period agrees with the releases",
            "periods": periods}


def revenue_sides(audit: dict) -> dict[tuple[str, str], dict]:
    """(start, end) -> the contract-revenue values filed for the period, the broad values served where the same
    filing's contract revenue is smaller (the "is larger" rule of receipts.ingest), and its fiscal label."""
    out: dict[tuple[str, str], dict] = {}
    for line in audit.get("revenue", []):
        side = out.setdefault((line["start"], line["end"]), {"contract": set(), "broad": set(), "fiscal": None})
        side["fiscal"] = side["fiscal"] or line.get("fiscal")
        contract = [d for d in line["dropped"] if d["concept"] in REVENUE_COMPONENT_CONCEPTS]
        side["contract"].update(d["value"] for d in contract)
        if line["served"] in REVENUE_COMPONENT_CONCEPTS:
            side["contract"].add(line["value"])
        if line["served"] in OTHER_REVENUE_CONCEPTS and any(
                c.endswith(" is larger") for d in contract for c in d["conditions"]):
            side["broad"].add(line["value"])
    return out


def _fourth_quarter(sides: dict, end: str, kind_: str) -> set[float]:
    """kind_ ("contract" or "broad") values of the fourth quarter ending `end`: FY minus 9M, or minus Q1-Q3."""
    fy = next((s for (start, e), s in sides.items() if e == end and (s["fiscal"] or "").startswith("FY ")), None)
    if fy is None:
        return set()
    year = fy["fiscal"].split()[-1]
    part = lambda label: next((s[kind_] for s in sides.values() if s["fiscal"] == f"{label} {year}"), set())
    nine = set(part("9M")) or {a + b + c for a in part("Q1") for b in part("Q2") for c in part("Q3")}
    return {t - n for t in fy[kind_] for n in nine}


def contract_total(ticker: str, audit_dir: Path = AUDIT_DIR, pr_dir: Path = PR_FACTS_DIR) -> dict:
    """Whether the company's releases show its contract revenue, not a larger broad revenue concept, is its total."""
    path = audit_dir / f"{ticker}.json"
    if not path.exists():
        return {"ticker": ticker, "decided": False, "reason": "no ingest audit file", "periods": []}
    sides, releases = revenue_sides(json.loads(path.read_text())), release_totals(ticker, pr_dir)
    if not any(s["broad"] for s in sides.values()):
        return {"ticker": ticker, "decided": False, "reason": "no filing tags a larger broad revenue", "periods": []}
    periods = []
    for (start, end), facts in sorted(releases.items()):
        side = sides.get((start, end))
        if side and side["broad"]:
            contract, broad, slack = side["contract"], side["broad"], 1
        elif kind(start, end) == "quarter" and not side:
            contract, broad, slack = _fourth_quarter(sides, end, "contract"), _fourth_quarter(sides, end, "broad"), 3
        else:
            continue
        if not contract or not broad:
            continue
        near = lambda vs: any(abs(f.value - v) <= slack * _tolerance(f) for f in facts for v in vs)
        if near(contract) != near(broad):
            periods.append({"period": f"{start}..{end}", "release": sorted({f.value for f in facts}),
                            "contract": sorted(contract), "broad": sorted(broad),
                            "agrees_with": "contract" if near(contract) else "broad"})
    if any(p["agrees_with"] == "broad" for p in periods):
        return {"ticker": ticker, "decided": False, "reason": "a release agrees with the broad figure",
                "periods": periods}
    if len(periods) < MIN_PERIODS:
        return {"ticker": ticker, "decided": False,
                "reason": f"{len(periods)} periods where the releases tell the two apart; {MIN_PERIODS} needed",
                "periods": periods}
    return {"ticker": ticker, "decided": True, "periods": periods,
            "reason": f"in {len(periods)} periods the releases equal contract revenue and not the broad figure"}


def broad_total(ticker: str, audit_dir: Path = AUDIT_DIR, pr_dir: Path = PR_FACTS_DIR,
                cf_dir: Path = COMPANYFACTS_DIR) -> dict:
    """Whether the company's releases show a comparison total (COMPARISON_REVENUE_CONCEPTS, which ingest otherwise
    only compares with) is its revenue: its release totals equal the filed value in at least MIN_PERIODS quarters or
    years and differ in none. Duke's "Total operating revenues" equals its RegulatedAndUnregulatedOperatingRevenue."""
    import gzip
    path = audit_dir / f"{ticker}.json"
    if not path.exists():
        return {"ticker": ticker, "decided": False, "reason": "no ingest audit file", "periods": []}
    cik = json.loads(path.read_text()).get("cik")
    cf = cf_dir / f"CIK{int(cik):010d}.json.gz" if cik else None
    if cf is None or not cf.exists():
        return {"ticker": ticker, "decided": False, "reason": "no archived companyfacts", "periods": []}
    gaap = json.loads(gzip.decompress(cf.read_bytes())).get("facts", {}).get("us-gaap", {})
    releases = release_totals(ticker, pr_dir, quarantined=True)
    best = {"ticker": ticker, "decided": False, "reason": "no comparison total matches a release", "periods": []}
    for concept in COMPARISON_REVENUE_CONCEPTS:
        filed: dict[tuple[str, str], set[float]] = {}
        for x in gaap.get(concept, {}).get("units", {}).get("USD", []):
            if x.get("form") in PERIODIC and x.get("start") and kind(x["start"], x["end"]) and x.get("val") is not None:
                filed.setdefault((x["start"], x["end"]), set()).add(float(x["val"]))
        periods = [{"period": f"{s}..{e}", "release": sorted({f.value for f in facts}), "filed": sorted(filed[(s, e)]),
                    "agree": any(abs(f.value - v) <= _tolerance(f) for f in facts for v in filed[(s, e)])}
                   for (s, e), facts in sorted(releases.items()) if (s, e) in filed]
        if not periods:
            continue
        if not all(p["agree"] for p in periods):
            return {"ticker": ticker, "decided": False, "concept": concept, "periods": periods,
                    "reason": f"a release total differs from {concept}"}
        if len(periods) >= MIN_PERIODS:
            return {"ticker": ticker, "decided": True, "concept": concept, "periods": periods,
                    "reason": f"in {len(periods)} periods the releases equal {concept}"}
        best = {"ticker": ticker, "decided": False, "concept": concept, "periods": periods,
                "reason": f"{len(periods)} periods compare with {concept}; {MIN_PERIODS} needed"}
    return best


def bank_total(ticker: str, audit_dir: Path = AUDIT_DIR, pr_dir: Path = PR_FACTS_DIR,
               cf_dir: Path = COMPANYFACTS_DIR) -> dict:
    """Whether the company's release totals show its revenue is net interest income plus noninterest income, both
    filed by one filing for one quarter or year: equal in at least MIN_PERIODS periods and different in none."""
    import gzip
    path = audit_dir / f"{ticker}.json"
    if not path.exists():
        return {"ticker": ticker, "decided": False, "reason": "no ingest audit file", "periods": []}
    cik = json.loads(path.read_text()).get("cik")
    if not cik or not (cf_dir / f"CIK{int(cik):010d}.json.gz").exists():
        return {"ticker": ticker, "decided": False, "reason": "no archived companyfacts", "periods": []}
    load = lambda c: json.loads(gzip.decompress((cf_dir / f"CIK{int(c):010d}.json.gz").read_bytes()))
    gaap = with_predecessors(int(cik), load).get("facts", {}).get("us-gaap", {})
    parts: dict[tuple, dict[str, float]] = {}
    for concept in (NET_INTEREST_INCOME, NONINTEREST_INCOME):
        for x in gaap.get(concept, {}).get("units", {}).get("USD", []):
            if x.get("form") in PERIODIC and x.get("start") and kind(x["start"], x["end"]) and x.get("val") is not None:
                parts.setdefault((x["accn"], x["start"], x["end"]), {})[concept] = float(x["val"])
    filed: dict[tuple[str, str], set[float]] = {}
    for (_, start, end), both in parts.items():
        if len(both) == 2:
            filed.setdefault((start, end), set()).add(sum(both.values()))
    if not filed:
        return {"ticker": ticker, "decided": False, "periods": [],
                "reason": f"no filing tags {NET_INTEREST_INCOME} and {NONINTEREST_INCOME} for one period"}
    periods = [{"period": f"{s}..{e}", "release": sorted({f.value for f in facts}), "filed": sorted(filed[(s, e)]),
                "agree": any(abs(f.value - v) <= _tolerance(f) for f in facts for v in filed[(s, e)])}
               for (s, e), facts in sorted(release_totals(ticker, pr_dir, quarantined=True).items()) if (s, e) in filed]
    if not all(p["agree"] for p in periods):
        return {"ticker": ticker, "decided": False, "periods": periods,
                "reason": "a release total differs from net interest income plus noninterest income"}
    if len(periods) < MIN_PERIODS:
        return {"ticker": ticker, "decided": False, "periods": periods,
                "reason": f"{len(periods)} periods compare; {MIN_PERIODS} needed"}
    return {"ticker": ticker, "decided": True, "periods": periods,
            "reason": f"in {len(periods)} periods the releases equal net interest income plus noninterest income"}


def _record_tolerance(value: float, locator: str | None) -> float | None:
    """Half a unit of the release cell a gate's record quotes ("Net income | (12.5)" for -$12.5M: $0.05M)."""
    text = (locator or "").rsplit(" | ", 1)[-1]
    n = parse_number(text)
    if not n or not value:
        return None
    scale = 10.0 ** round(math.log10(abs(value / float(n))))
    return 0.5 * scale * 10.0 ** -frac_digits(text) + 1e-9


def release_net_income(ticker: str, pr_dir: Path) -> dict[tuple[str, str], list[tuple[float, float, str]]]:
    """(start, end) -> (value, tolerance, row label) of each GAAP, company-level net income figure the company's
    releases report, as the mapper read them: the live facts, and those a gate dropped from a live file (the paired
    10-Q/10-K XBRL gate, scripts/gate_release_facts.py), whose value and cell its report keeps. Quarantined files are
    left out: their columns may be misread."""
    out: dict[tuple[str, str], list[tuple[float, float, str]]] = {}
    for path in sorted((pr_dir / ticker).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                f = Fact.model_validate_json(line)
                if f.metric == "net_income" and f.basis == "GAAP" and not f.segment and f.period_start:
                    out.setdefault((f.period_start.isoformat(), f.period_end.isoformat()), []).append(
                        (f.value, _tolerance(f), (f.locator or "").split(" | ")[0]))
        report_path = path.with_name(path.name.replace(".jsonl", ".report.json"))
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        records = [(r["period"], r["press_release"], r.get("locator")) for r in
                   (report.get("xbrl_gate") or {}).get("dropped", []) if r.get("metric") == "net_income"]
        records += [(r["period"], r["value"], r.get("locator")) for r in report.get("release_gates", [])
                    if r.get("metric") == "net_income" and r.get("basis") == "GAAP" and not r.get("segment")
                    and r.get("rule") != "rowspan_header"]
        for period, value, locator in records:
            start, _, end = period.partition("..")
            tol = _record_tolerance(value, locator)
            if tol is not None and start and start != "None":
                out.setdefault((start, end), []).append((value, tol, (locator or "").split(" | ")[0]))
    return out


def net_income_sides(gaap: dict) -> dict[tuple[str, str], dict[str, set[float]]]:
    """(start, end) -> the available-to-common values filed for a quarter or year, and the other net income
    definitions filed for it: NetIncomeLoss, and ProfitLoss less the whole noncontrolling interest of one filing."""
    out: dict[tuple[str, str], dict[str, set[float]]] = {}
    def entries(concept: str):
        for x in gaap.get(concept, {}).get("units", {}).get("USD", []):
            if x.get("form") in PERIODIC and x.get("start") and kind(x["start"], x["end"]) and x.get("val") is not None:
                yield x
    side = lambda x: out.setdefault((x["start"], x["end"]), {"available": set(), "other": set()})
    for x in entries(AVAILABLE_TO_COMMON):
        side(x)["available"].add(float(x["val"]))
    for x in entries("NetIncomeLoss"):
        side(x)["other"].add(float(x["val"]))
    nci: dict[tuple, dict[str, float]] = {}
    for c in NCI_CONCEPTS:
        for x in gaap.get(c, {}).get("units", {}).get("USD", []):
            if x.get("val") is not None:
                nci.setdefault((x.get("accn"), x.get("start"), x.get("end")), {})[c] = float(x["val"])
    for x in entries("ProfitLoss"):
        whole = _whole_nci(nci.get((x.get("accn"), x.get("start"), x.get("end")), {}))
        if whole is not None:
            side(x)["other"].add(float(x["val"]) - whole[0])
    return out


def available_to_common(ticker: str, audit_dir: Path = AUDIT_DIR, pr_dir: Path = PR_FACTS_DIR,
                        cf_dir: Path = COMPANYFACTS_DIR) -> dict:
    """Whether the company's releases report net income after preferred dividends (AVAILABLE_TO_COMMON): equal to
    it in at least MIN_PERIODS quarters or years where it differs from the other filed net income, and equal to the
    other in none."""
    import gzip
    path = audit_dir / f"{ticker}.json"
    if not path.exists():
        return {"ticker": ticker, "decided": False, "reason": "no ingest audit file", "periods": []}
    cik = json.loads(path.read_text()).get("cik")
    load = lambda c: json.loads(gzip.decompress((cf_dir / f"CIK{int(c):010d}.json.gz").read_bytes()))
    if not cik or not (cf_dir / f"CIK{int(cik):010d}.json.gz").exists():
        return {"ticker": ticker, "decided": False, "reason": "no archived companyfacts", "periods": []}
    gaap = with_predecessors(int(cik), load).get("facts", {}).get("us-gaap", {})
    sides = net_income_sides(gaap)
    if not any(s["available"] for s in sides.values()):
        return {"ticker": ticker, "decided": False, "reason": f"no filing tags {AVAILABLE_TO_COMMON}", "periods": []}
    periods = []
    for (start, end), figures in sorted(release_net_income(ticker, pr_dir).items()):
        side = sides.get((start, end))
        if not side or not side["available"] or not side["other"]:
            continue
        near = lambda vs: any(abs(v - x) <= tol for v, tol, _ in figures for x in vs)
        on_available, on_other = near(side["available"]), near(side["other"])
        if on_available != on_other:
            periods.append({"period": f"{start}..{end}", "release": sorted({v for v, _, _ in figures}),
                            "available": sorted(side["available"]), "other": sorted(side["other"]),
                            "agrees_with": "available" if on_available else "other",
                            "label": sorted({label for _, _, label in figures if label})})
    if any(p["agrees_with"] == "other" for p in periods):
        return {"ticker": ticker, "decided": False, "periods": periods,
                "reason": "a release agrees with the net income before preferred dividends"}
    if len(periods) < MIN_PERIODS:
        return {"ticker": ticker, "decided": False, "periods": periods,
                "reason": f"{len(periods)} periods where the releases tell the two apart; {MIN_PERIODS} needed"}
    labels = Counter(label for p in periods for label in p["label"])
    return {"ticker": ticker, "decided": True, "periods": periods, "label": labels.most_common(1)[0][0],
            "reason": f"in {len(periods)} periods the releases equal {AVAILABLE_TO_COMMON}, not the other figure"}


def main(argv=None, *, audit_dir: Path | None = None, pr_dir: Path | None = None,
         confirmations_path: Path | None = None, db: Path | None = None, cf_dir: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="decide contract revenue against broad revenue for every company with an ingest audit file")
    ap.add_argument("--recheck", action="store_true", help="also re-check every company already confirmed")
    ap.add_argument("--dry-run", action="store_true", help="report only; do not write the confirmations file")
    ap.add_argument("--run-date", type=date.fromisoformat,
                    help="date recorded with new decisions (default: the store's own run date, from its window)")
    args = ap.parse_args(argv)
    audit_dir, pr_dir = audit_dir or AUDIT_DIR, pr_dir or PR_FACTS_DIR
    confirmations_path, db, cf_dir = confirmations_path or CONFIRMATIONS, db or DB_PATH, cf_dir or COMPANYFACTS_DIR
    run_date = args.run_date or (store_run_date(_meta(db)) if db.exists() else None) or date.today()
    confirmations = json.loads(confirmations_path.read_text()) if confirmations_path.exists() else {}
    tickers = {t.upper() for t in args.tickers}
    if args.recheck:
        tickers |= {t for t, rec in confirmations.items() if "confirmed_on" in rec}
        rechecked = {t for t, rec in confirmations.items() if NET_INCOME_KEY in rec}
    results = [check(t, audit_dir, pr_dir) for t in sorted(tickers)]   # the companies named or already confirmed
    if args.all:                                     # the contract-revenue decision: every company
        tickers |= {p.stem for p in audit_dir.glob("*.json")}
    for r in results:
        rec = confirmations.get(r["ticker"], {})
        if r["confirmed"] or "confirmed_on" in rec or args.tickers:
            print(f"{r['ticker']:6} {'CONFIRMED' if r['confirmed'] else 'held back'}: {r['reason']} "
                  f"({len(r['periods'])} figures compared)")
        if r["confirmed"]:
            confirmations[r["ticker"]] = {**rec, "confirmed_on": rec.get("confirmed_on", run_date.isoformat()),
                                          "method": "press-release totals", "periods": r["periods"]}
        elif "confirmed_on" in rec and r["reason"].endswith("disagree with the releases"):
            for k in ("confirmed_on", "method", "periods"):
                rec.pop(k, None)
            rec["revoked"] = {"on": run_date.isoformat(), "reason": r["reason"], "periods": r["periods"]}
            print(f"{r['ticker']:6} confirmation REVOKED: {r['reason']}")
    for t in sorted(tickers):
        d = contract_total(t, audit_dir, pr_dir)
        rec = confirmations.get(t, {})
        if d["decided"]:
            print(f"{t:6} contract revenue is the total: {d['reason']}")
            confirmations[t] = {**rec, "contract_revenue_is_total": {
                "decided_on": rec.get("contract_revenue_is_total", {}).get("decided_on", run_date.isoformat()),
                "periods": d["periods"]}}
        elif "contract_revenue_is_total" in rec and d["reason"] == "a release agrees with the broad figure":
            rec.pop("contract_revenue_is_total")
            print(f"{t:6} contract-revenue decision REVOKED: {d['reason']}")
        b = broad_total(t, audit_dir, pr_dir, cf_dir)
        rec = confirmations.get(t, {})
        if b["decided"]:
            print(f"{t:6} {b['concept']} is the total: {b['reason']}")
            confirmations[t] = {**rec, "broad_revenue_is_total": {
                "concept": b["concept"],
                "decided_on": rec.get("broad_revenue_is_total", {}).get("decided_on", run_date.isoformat()),
                "periods": b["periods"]}}
        elif "broad_revenue_is_total" in rec and b["reason"].startswith("a release total differs"):
            rec.pop("broad_revenue_is_total")
            print(f"{t:6} broad-revenue decision REVOKED: {b['reason']}")
        k = bank_total(t, audit_dir, pr_dir, cf_dir)
        rec = confirmations.get(t, {})
        if k["decided"]:
            print(f"{t:6} revenue is net interest income plus noninterest income: {k['reason']}")
            confirmations[t] = {**rec, BANK_KEY: {
                "decided_on": rec.get(BANK_KEY, {}).get("decided_on", run_date.isoformat()), "periods": k["periods"]}}
        elif BANK_KEY in rec and k["reason"].startswith("a release total differs"):
            rec.pop(BANK_KEY)
            print(f"{t:6} bank-revenue decision REVOKED: {k['reason']}")
    for t in sorted(tickers | (rechecked if args.recheck else set())):
        n = available_to_common(t, audit_dir, pr_dir, cf_dir)
        rec = confirmations.get(t, {})
        if n["decided"]:
            print(f"{t:6} net income is the available-to-common figure: {n['reason']}")
            confirmations[t] = {**rec, NET_INCOME_KEY: {
                "concept": AVAILABLE_TO_COMMON, "label": n["label"],
                "decided_on": rec.get(NET_INCOME_KEY, {}).get("decided_on", run_date.isoformat()),
                "periods": n["periods"]}}
        elif NET_INCOME_KEY in rec and n["reason"].startswith("a release agrees with"):
            rec.pop(NET_INCOME_KEY)
            print(f"{t:6} net-income decision REVOKED: {n['reason']}")
    confirmations = {t: rec for t, rec in confirmations.items() if rec}
    if not args.dry_run:
        confirmations_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = confirmations_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(sorted(confirmations.items())), indent=1) + "\n")
        tmp.replace(confirmations_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
