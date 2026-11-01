"""Direct alternative net-income lines, kept as evidence outside the extraction vocabulary.

The snapshot builder reads hash-checked companyfacts (including predecessor registrants) and kept filing XBRL
instances. A direct USD tag is retained only if the same stored filing/quarter or annual span serves a different
GAAP net-income figure beyond rounding. No new filing, period or net-income observation is introduced. No Q4
subtraction is made: a Q4 alternative needs its own directly tagged quarter; otherwise existing verdict rules apply.
For unqualified net income these facts only explain ambiguity. The verifier may select a line only when the
claim's own wording names it, and still requires its same-filing served net-income observation.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from receipts import filing_xbrl
from receipts.balance_sheet import XBRL_FORMS, _layout, _trailing_zeros
from receipts.ingest import AVAILABLE_TO_COMMON, PREDECESSOR_CIKS, merge_companyfacts
from receipts.store import DB_PATH, _FACT_COLUMNS, _row_to_fact, fact_row
from receipts.types import Fact

ARCHIVE = DB_PATH.parent / "archive" / "companyfacts"
ALTERNATIVES = {
    "net_income_to_common": (AVAILABLE_TO_COMMON,
        "Net income available to common stockholders (direct XBRL tag; alternative meaning only)"),
    "net_income_including_nci": ("ProfitLoss",
        "Consolidated net income including noncontrolling interests (direct XBRL tag; alternative meaning only)"),
}


def company_facts(*, companyfacts: dict, net_income: Sequence[tuple[Fact, str]]) -> tuple[list[tuple[Fact, str]], dict]:
    """Retain each distinct direct alternative beside its same-filing served net-income observation."""
    out, by_metric = [], {}
    for metric, (tag, locator) in ALTERNATIVES.items():
        facts, audit = _tag_facts(companyfacts=companyfacts, net_income=net_income,
                                  metric=metric, tag=tag, locator=locator)
        out.extend(facts)
        by_metric[metric] = audit
    return out, {"facts": len(out), "by_metric": by_metric,
                 "conflicts": [{"metric": m, **c} for m, a in by_metric.items() for c in a["conflicts"]],
                 "equal_or_rounded": sum(a["equal_or_rounded"] for a in by_metric.values())}


def _tag_facts(*, companyfacts: dict, net_income: Sequence[tuple[Fact, str]], metric: str,
               tag: str, locator: str) -> tuple[list[tuple[Fact, str]], dict]:
    """Retain one direct tag only beside its same-filing served net-income observation.

    A repeated comparative is retained for each stored filing: the verifier must not join it to another filing's
    income. Conflicting tags for one context are omitted. companyfacts omits decimals, so use its filing's USD
    precision (fewest trailing zeros, capped at millions), as receipts.ingest does for the regular income facts.
    """
    tagged = companyfacts.get("facts", {}).get("us-gaap", {})
    entries = tagged.get(tag, {}).get("units", {}).get("USD", [])
    served: dict[tuple, list[tuple[Fact, str]]] = defaultdict(list)
    for f, kind in net_income:
        if (f.metric == "net_income" and f.basis == "GAAP" and f.unit == "USD" and f.segment is None
                and f.form in XBRL_FORMS and f.period_start is not None and kind in ("quarter", "annual")
                and f.source in ("xbrl", "derived")):
            served[(f.accession, f.period_start.isoformat(), f.period_end.isoformat())].append((f, kind))
    values: dict[tuple, set[float]] = defaultdict(set)
    for e in entries:
        key = (e.get("accn"), e.get("start"), e.get("end"))
        v = e.get("val")
        if key in served and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            values[key].add(float(v))
    zeros: dict[str, int] = {}
    accessions = {k[0] for k in values}
    for body in tagged.values():
        for e in body.get("units", {}).get("USD", []):
            v, acc = e.get("val"), e.get("accn")
            if (acc in accessions and isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and v):
                zeros[acc] = min(zeros.get(acc, 6), _trailing_zeros(v))
    audit: dict[str, Any] = {"conflicts": [], "equal_or_rounded": 0}
    out: list[tuple[Fact, str]] = []
    for key, vals in sorted(values.items()):
        if len(vals) != 1:
            audit["conflicts"].append({"accession": key[0], "start": key[1], "end": key[2], "values": sorted(vals)})
            continue
        value = next(iter(vals))
        own = served[key]
        decimals = -zeros[key[0]] if key[0] in zeros else None
        precision = 0.5 * 10.0 ** -(decimals if decimals is not None else 0)
        if any(abs(f.value - value) <= max(precision, 0.5 * 10.0 ** -(f.decimals or 0)) for f, _ in own):
            audit["equal_or_rounded"] += 1
            continue
        f, kind = own[0]
        out.append((f.model_copy(update={
            "metric": metric, "concept": f"us-gaap:{tag}", "value": value, "source": "xbrl", "derived_from": [],
            "decimals": decimals, "locator": locator,
        }), kind))
    audit["facts"] = len(out)
    return out, audit


def add_to_db(con: sqlite3.Connection, archive: Path = ARCHIVE, *, filing_archive: Path | None = None,
              log: Any = None) -> dict[str, Any]:
    """Rebuild evidence-only alternatives in the open database, with all archive validation before any mutation."""
    archive = Path(archive)
    if not archive.is_dir():
        raise SystemExit(f"net_income_common: no companyfacts archive at {archive}")
    try:
        index = json.loads((archive / "index.json").read_text())
    except (OSError, ValueError):
        raise SystemExit("net_income_common: companyfacts index with sha256 is required")

    def load(cik: int) -> tuple[dict, str]:
        path = archive / f"CIK{cik:010d}.json.gz"
        if not path.is_file():
            raise SystemExit(f"net_income_common: missing predecessor companyfacts archive {path.name}")
        raw = gzip.decompress(path.read_bytes())
        sha = hashlib.sha256(raw).hexdigest()
        if sha != (index.get(str(cik)) or {}).get("sha256"):
            raise SystemExit(f"net_income_common: {path.name} does not match its index sha256")
        return json.loads(raw), sha

    table, columns = _layout(con)
    names = [c for c in columns if c != "id"]
    cursor = con.cursor()
    cursor.row_factory = sqlite3.Row
    income: dict[int, list[tuple[Fact, str]]] = defaultdict(list)
    for r in cursor.execute("SELECT f.* FROM facts f JOIN filings g ON f.accession = g.accession AND f.cik = g.cik "
                            "WHERE f.metric = 'net_income' AND f.period_kind IN ('quarter', 'annual')"):
        income[int(r["cik"])].append((_row_to_fact(r), r["period_kind"]))
    held: dict[int, set[str]] = defaultdict(set)
    for cik, acc in con.execute("SELECT cik, accession FROM filings"):
        held[int(cik)].add(acc)
    filing_archive = Path(filing_archive) if filing_archive is not None else archive.parent / "filing_xbrl"
    filing_index = json.loads((filing_archive / "index.json").read_text()) if (filing_archive / "index.json").is_file() else {}
    rows_by_metric: dict[str, list[tuple]] = {metric: [] for metric in ALTERNATIVES}
    audits, missing = {}, []
    for cik, ticker in con.execute("SELECT cik, ticker FROM companies ORDER BY ticker").fetchall():
        cik = int(cik)
        path = archive / f"CIK{cik:010d}.json.gz"
        if path.is_file():
            current, sha = load(cik)
        else:
            current, sha = {"facts": {}}, None
            missing.append(ticker)
        earlier = {p: load(p) for p in PREDECESSOR_CIKS.get(cik, ())}
        combined = merge_companyfacts(current, *(cf for cf, _ in earlier.values())) if earlier else current
        # Skip even parsing instances whose accession the store does not hold. hole_filings checks each XML hash
        # and only fills entire missing filings, exactly as ingest does.
        have = filing_xbrl.accessions(combined) | (set(filing_index) - held[cik])
        holes = filing_xbrl.hole_filings((cik, *earlier), have, filing_archive) if filing_index else []
        if holes:
            combined = merge_companyfacts(combined, *holes)
        facts, audit = company_facts(companyfacts=combined, net_income=income[cik])
        audits[ticker] = {**audit, "companyfacts_sha256": sha,
                         "filing_xbrl_accessions": sorted(set().union(*(filing_xbrl.accessions(h) for h in holes)))}
        if earlier:
            audits[ticker]["predecessor_companyfacts_sha256"] = {str(p): d for p, (_, d) in earlier.items()}
        for f, kind in facts:
            full = dict(zip(_FACT_COLUMNS, fact_row(f, kind)))
            rows_by_metric[f.metric].append(tuple(full[c] for c in names))
    # Keep the existing common-stockholder block first, so adding consolidated income does not renumber it.
    rows = [row for metric_rows in rows_by_metric.values() for row in metric_rows]
    by_company = {t: a["facts"] for t, a in audits.items() if a["facts"]}
    by_metric = {}
    for metric, metric_rows in rows_by_metric.items():
        companies = {t: a["by_metric"][metric]["facts"] for t, a in audits.items() if a["by_metric"][metric]["facts"]}
        by_metric[metric] = {"facts": len(metric_rows), "companies": len(companies), "by_company": companies}
    summary = {"facts": len(rows), "companies": len(by_company), "by_company": by_company,
               "by_metric": by_metric, "without_companyfacts": missing, "audit": audits}
    with con:
        summary["replaced"] = con.execute(f"DELETE FROM {table} WHERE metric IN ({', '.join('?' * len(ALTERNATIVES))})",
                                           tuple(ALTERNATIVES)).rowcount
        con.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", rows)
    if log:
        for metric, counts in by_metric.items():
            log(f"{metric}: {counts['facts']:,} evidence-only facts at {counts['companies']} companies")
    return summary
