"""Balance-sheet figures: cash and cash equivalents, total assets, total liabilities, shareholders' equity, long-term
and total debt, and shares outstanding, each an instant at the balance-sheet date of a 10-Q or 10-K.

    .venv/bin/python -m receipts.balance_sheet                       # add them to data/facts.sqlite
    .venv/bin/python -m receipts.balance_sheet --db PATH --audit OUT.json

scripts/build_snapshot.py adds them to every snapshot it builds, from the same archive.

Build (company_facts, add_to_db). SEC companyfacts, read from the gzip archive (data/archive/companyfacts, checked
against the sha256 its index records), give the instants a company's 10-Qs and 10-Ks (and their amendments) tag. Only
filings the evidence store already holds count, so balance sheets and the other XBRL facts come from the same filings.
Predecessor registrants in receipts.ingest.PREDECESSOR_CIKS are merged before choosing versions, so the original
filing and its public timestamp survive a change of registrant.
  * Dates. A value counts only at a balance-sheet date of the company, on or after the store's facts_start: a fiscal
    period end in its stored calendar, or the report date of one of its stored 10-Qs and 10-Ks. The share count on a
    filing's cover page (dei:EntityCommonStockSharesOutstanding) keeps the date the cover gives it, a few weeks later.
  * Concepts (ITEMS). Per filing and date the first concept of an item that the filing tags wins. Cash and cash
    equivalents is CashAndCashEquivalentsAtCarryingValue; only a company that tags it nowhere in the window gets cash,
    cash equivalents and restricted cash instead. That total can verify cash alone only with same-filing tags proving
    all restricted cash is zero; those zero tags are retained as supporting evidence. Shareholders' equity is the
    parent's (StockholdersEquity), never the total including noncontrolling interests. Debt is only what a company tags
    as a total: long-term debt with its current portion (LongTermDebt), without it (LongTermDebtNoncurrent, or the
    lease-inclusive line some companies tag instead), and short- plus long-term debt (DebtLongtermAndShorttermCombined
    Amount, or the lease-inclusive DebtAndCapitalLeaseObligations). NoCap never adds debt components up itself.
  * Versions. As for the other XBRL facts, every version of a date counts: its own filing, later comparatives and
    restatements. A later filing that repeats a value unchanged adds nothing a verdict uses (the original is public
    earlier and has the same value), so per concept and date only the first filing of each distinct value is stored:
    the original and each restatement.
  * A filing that gives one concept two values for one date, a negative value of an item that is never negative, cash
    above the same balance sheet's total assets, or total assets that its own total of liabilities and equity
    contradicts is not stored; the audit says why.

Check (check). A draft's figure is read as a balance-sheet item only from its own words, in metric_text and in the
quote around its number, never from the extraction's metric: "cash and cash equivalents", "cash", "total assets", "total
liabilities", "shareholders' equity", "long-term debt", "total debt" and "shares outstanding". Anything said beyond
that ("free cash flow", "cash returned", "net debt", "total current assets", "assets under management", "diluted
shares") leaves the figure to receipts.verify, which answers "?". The date is the balance-sheet date the claim's period
names on the company's own fiscal calendar ("at the end of the quarter", "as of September 27, 2026", "at fiscal 2025
year-end"); a period that names no single balance sheet is "?". Precision, bounds, approximations and as_of work as for
every other figure (receipts.verify). Cash and cash equivalents, total assets, total liabilities and shareholders'
equity are single measures, so a mismatch is "✗"; debt, a bare "cash" and share counts are reported in several ways, so
a mismatch is "?" with the filed figures as receipts, never "✗".
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from receipts import periods
from receipts.periods import CalEntry, ResolvedPeriod
from receipts.store import _FACT_COLUMNS, DB_PATH, fact_row
from receipts.types import Claim, EvidenceStore, Fact, Verdict

ARCHIVE = DB_PATH.parent / "archive" / "companyfacts"
XBRL_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A")


# --------------------------------------------------------------------------- what is stored
@dataclass(frozen=True)
class Concept:
    name: str             # "us-gaap:Assets", "dei:EntityCommonStockSharesOutstanding"
    label: str            # how an explanation names the measure: "total assets"
    plural: bool = False  # "total assets were", "shareholders' equity was"


@dataclass(frozen=True)
class Item:
    metric: str                          # Fact.metric (receipts.types.BalanceSheetMetric)
    unit: str                            # companyfacts unit and Fact.unit: "USD" or "shares"
    concepts: tuple[Concept, ...]        # per filing and date, the first one the filing tags wins
    fallback: tuple[Concept, ...] = ()   # only at a company that tags none of `concepts` in the window
    signed: bool = False                 # may be negative (a shareholders' deficit)


CCE = Concept("us-gaap:CashAndCashEquivalentsAtCarryingValue", "cash and cash equivalents", True)
CCE_RESTRICTED = Concept("us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                         "cash, cash equivalents and restricted cash", True)
# Supporting evidence for the inclusive total, never candidate cash balances themselves. A current-only zero
# cannot establish that all restricted cash is zero: require the total, or both current and noncurrent tags.
RESTRICTED_TOTAL = Concept("us-gaap:RestrictedCashAndCashEquivalents", "restricted cash and cash equivalents", True)
RESTRICTED_CURRENT = Concept("us-gaap:RestrictedCashAndCashEquivalentsAtCarryingValue",
                             "current restricted cash and cash equivalents", True)
RESTRICTED_NONCURRENT = Concept("us-gaap:RestrictedCashAndCashEquivalentsNoncurrent",
                                "noncurrent restricted cash and cash equivalents", True)
RESTRICTED_SUPPORT = (RESTRICTED_TOTAL, RESTRICTED_CURRENT, RESTRICTED_NONCURRENT)
SHARES_BALANCE = Concept("us-gaap:CommonStockSharesOutstanding", "shares of common stock outstanding", True)
SHARES_COVER = Concept("dei:EntityCommonStockSharesOutstanding", "shares of common stock outstanding", True)
ITEMS: dict[str, Item] = {i.metric: i for i in (
    Item("cash_and_equivalents", "USD", (CCE,), (CCE_RESTRICTED,)),
    Item("total_assets", "USD", (Concept("us-gaap:Assets", "total assets", True),)),
    Item("total_liabilities", "USD", (Concept("us-gaap:Liabilities", "total liabilities", True),)),
    Item("stockholders_equity", "USD", (Concept("us-gaap:StockholdersEquity", "shareholders' equity"),), signed=True),
    Item("long_term_debt", "USD", (Concept("us-gaap:LongTermDebt", "long-term debt including its current portion"),)),
    Item("long_term_debt_noncurrent", "USD", (
        Concept("us-gaap:LongTermDebtNoncurrent", "long-term debt excluding its current portion"),
        Concept("us-gaap:LongTermDebtAndCapitalLeaseObligations",
                "long-term debt and finance lease obligations excluding their current portion", True))),
    Item("total_debt", "USD", (
        Concept("us-gaap:DebtLongtermAndShorttermCombinedAmount", "total short- and long-term debt"),
        Concept("us-gaap:DebtAndCapitalLeaseObligations", "total debt and finance lease obligations", True))),
    Item("shares_outstanding", "shares", (SHARES_BALANCE,)),
)}
BALANCE_SHEET_METRICS = tuple(ITEMS)
CONCEPTS = {c.name: c for item in ITEMS.values() for c in (*item.concepts, *item.fallback)} | {
    SHARES_COVER.name: SHARES_COVER, **{c.name: c for c in RESTRICTED_SUPPORT}}
# A filing's balance sheet totals liabilities and equity to its total assets; one that doesn't is a tagging slip.
LIABILITIES_AND_EQUITY = "us-gaap:LiabilitiesAndStockholdersEquity"


def _date(s: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _utc(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _trailing_zeros(v: float, cap: int = 6) -> int:
    n, z = abs(int(round(v))), 0
    while n and n % 10 == 0 and z < cap:
        n //= 10
        z += 1
    return z


def _rounds(later: float, kept: float) -> bool:
    """A later version that is the kept one written more coarsely: Apple's 10-Qs tag its FY2025 long-term debt as
    $90,700,000,000 ("$90.7 billion" in a note) beside the 10-K's $90,678,000,000. Only a coarser figure qualifies, so
    a restatement filed as precisely as the original is kept."""
    unit = 10.0 ** _trailing_zeros(later, 12)
    return unit > 10.0 ** _trailing_zeros(kept, 12) and abs(later - kept) <= 0.5 * unit


def company_facts(*, cik: int, ticker: str, companyfacts: Mapping[str, Any], filings: Mapping[str, Mapping[str, Any]],
                  calendar: Sequence[tuple[Any, Any, Any, Any]], facts_start: Optional[date] = None
                  ) -> tuple[list[Fact], dict[str, Any]]:
    """One company's balance-sheet facts, sorted by metric, date and acceptance, and an audit of what was left out.

    filings: accession -> the store's filings row (form, accepted_at, url, report_date); only 10-Qs and 10-Ks count.
    calendar: the store's fiscal periods, (fiscal_year, fiscal_period, start, end)."""
    filings = {a: f for a, f in filings.items() if f.get("form") in XBRL_FORMS}
    labels: dict[date, tuple[int, str]] = {}
    for fy, fp, _start, end in sorted(calendar, key=lambda r: r[1] == "FY"):  # a year end takes its FY label
        if (d := _date(end)) is not None:
            labels[d] = (int(fy), str(fp))
    dates = set(labels) | {d for f in filings.values() if (d := _date(f.get("report_date"))) is not None}
    if facts_start is not None:
        dates = {d for d in dates if d >= facts_start}
    tagged = companyfacts.get("facts", {}) if isinstance(companyfacts, Mapping) else {}
    audit: dict[str, Any] = {"dropped": [], "fallback": [], "versions_repeated": 0}

    def values(concept: Concept, unit: str, *, cover: bool = False) -> dict[tuple[str, date], float]:
        """(accession, date) -> the one value a concept has there, from stored 10-Qs and 10-Ks only."""
        ns, name = concept.name.split(":", 1)
        found: dict[tuple[str, date], set[float]] = defaultdict(set)
        for e in tagged.get(ns, {}).get(name, {}).get("units", {}).get(unit, []) or []:
            end, accn, val = _date(e.get("end")), e.get("accn"), e.get("val")
            if e.get("start") or end is None or accn not in filings or isinstance(val, bool) \
                    or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                continue
            if (facts_start is not None and end < facts_start) or (not cover and end not in dates):
                continue
            found[(accn, end)].add(float(val))
        out = {}
        for key, vals in found.items():
            if len(vals) == 1:
                out[key] = vals.pop()
            else:
                audit["dropped"].append({"concept": concept.name, "accession": key[0], "date": key[1].isoformat(),
                                         "values": sorted(vals), "why": "one filing gives it two values for one date"})
        return out

    chosen: dict[tuple[str, str, date], tuple[Concept, float]] = {}  # (metric, accession, date) -> concept, value
    for item in ITEMS.values():
        found = [(c, values(c, item.unit)) for c in item.concepts]
        if item.fallback and not any(v for _, v in found):
            found = [(c, values(c, item.unit)) for c in item.fallback]
            if any(v for _, v in found):
                audit["fallback"].append({"metric": item.metric, "concepts": [c.name for c, v in found if v]})
        for concept, vals in found:
            for (accn, end), value in sorted(vals.items()):
                if (item.metric, accn, end) in chosen:
                    continue  # the filing tags an earlier concept of the item there
                if value < 0 and not item.signed:
                    audit["dropped"].append({"concept": concept.name, "accession": accn, "date": end.isoformat(),
                                             "value": value, "why": "negative, and the item never is"})
                    continue
                chosen[(item.metric, accn, end)] = (concept, value)
    for (accn, end), value in values(SHARES_COVER, "shares", cover=True).items():
        if value > 0:
            chosen[("shares_outstanding", accn, end)] = (SHARES_COVER, value)
    totals = values(Concept(LIABILITIES_AND_EQUITY, ""), "USD")
    for (metric, accn, end), (concept, value) in sorted(chosen.items()):
        why = None
        assets = chosen.get(("total_assets", accn, end))
        if metric == "total_assets" and (accn, end) in totals and abs(totals[(accn, end)] - value) > 0.5e6:
            why = f"its balance sheet totals liabilities and equity to {totals[(accn, end)]:,.0f}"
        elif metric == "cash_and_equivalents" and assets is not None and value > assets[1]:
            why = f"above the same balance sheet's total assets of {assets[1]:,.0f}"
        if why:
            del chosen[(metric, accn, end)]
            audit["dropped"].append({"concept": concept.name, "accession": accn, "date": end.isoformat(),
                                     "value": value, "why": why})

    zeros: dict[tuple[str, str], int] = {}  # a filing's precision per unit: the fewest trailing zeros of its values
    versions: dict[tuple[str, str, date], list[tuple[datetime, str, float]]] = defaultdict(list)
    for (metric, accn, end), (concept, value) in chosen.items():
        unit = ITEMS[metric].unit
        if value:
            zeros[(accn, unit)] = min(zeros.get((accn, unit), 6), _trailing_zeros(value))
        versions[(metric, concept.name, end)].append((_utc(filings[accn]["accepted_at"]), accn, value))
    restricted = {c.name: values(c, "USD") for c in RESTRICTED_SUPPORT}
    conflicts = {(r["accession"], r["date"]) for r in audit["dropped"]
                 if r["concept"] in restricted}
    zero_support: dict[tuple[str, date], dict[str, float]] = {}
    for key in {key for vals in restricted.values() for key in vals}:
        support = {name: vals[key] for name, vals in restricted.items() if key in vals}
        complete = (RESTRICTED_TOTAL.name in support or
                    {RESTRICTED_CURRENT.name, RESTRICTED_NONCURRENT.name} <= support.keys())
        if complete and all(v == 0 for v in support.values()) and (key[0], key[1].isoformat()) not in conflicts:
            zero_support[key] = support
    out: list[Fact] = []
    for (metric, concept, end), rows in versions.items():
        last: Optional[float] = None
        last_proven_zero = False
        for accepted, accn, value in sorted(rows):
            proven_zero = concept == CCE_RESTRICTED.name and (accn, end) in zero_support
            if last is not None and (value == last or _rounds(value, last)) and (not proven_zero or last_proven_zero):
                audit["versions_repeated"] += 1
                continue  # a later comparative repeating the value: the filing first reporting it is held
            last, last_proven_zero = value, proven_zero
            unit, f = ITEMS[metric].unit, filings[accn]
            fy, fp = (None, None) if concept == SHARES_COVER.name else labels.get(end, (None, None))
            out.append(Fact(
                cik=int(cik), ticker=ticker, metric=metric, concept=concept, basis="GAAP", value=value, unit=unit,
                period_start=None, period_end=end, fiscal_year=fy, fiscal_period=fp, source="xbrl", form=f["form"],
                accession=accn, accepted_at=accepted, url=f["url"],
                decimals=-zeros[(accn, unit)] if (accn, unit) in zeros else None))
    # Keep the actual zero tags beside each inclusive total, with their original concept and filing. They are
    # support for equivalence, not balances to match, and bypass precision inference and version deduplication:
    # a repeated zero in a later filing is still needed to support that filing's restated total.
    for total in list(out):
        if total.concept != CCE_RESTRICTED.name:
            continue
        key = (total.accession, total.period_end)
        out.extend(total.model_copy(update={"concept": name, "value": 0.0, "decimals": None})
                   for name in zero_support.get(key, {}))
    out.sort(key=lambda f: (f.metric, f.period_end, f.accepted_at, f.accession, f.concept or ""))
    audit["facts"] = len(out)
    return out, audit


# --------------------------------------------------------------------------- the database
def _layout(con: sqlite3.Connection) -> tuple[str, list[str]]:
    """(table to write, its columns): facts, or a compacted snapshot's facts_compact behind a view named facts
    (scripts/build_snapshot.compact_facts), which takes ticker, form, accepted_at and url from companies and filings."""
    kinds = dict(con.execute("SELECT name, type FROM sqlite_master WHERE name IN ('facts', 'facts_compact')"))
    table = "facts_compact" if kinds.get("facts") == "view" and kinds.get("facts_compact") == "table" else "facts"
    if kinds.get(table) != "table":
        raise SystemExit("balance_sheet: the database has no facts table")
    return table, [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def add_to_db(con: sqlite3.Connection, archive: Path = ARCHIVE, *, log: Any = None) -> dict[str, Any]:
    """Replace every balance-sheet fact in an open evidence database (data/facts.sqlite, a snapshot, or the copy
    scripts/build_snapshot.py builds) with those the archived companyfacts give for its companies, in one transaction,
    so a re-run changes nothing. A company without an archive keeps none. Returns counts and the per-company audit."""
    # Reuse ingest's registrant map and deduplication without its filing-instance hole filling: this builder reads
    # only the supplied, hash-checked companyfacts archive and retains the same scope for all other companies.
    from receipts.ingest import PREDECESSOR_CIKS, merge_companyfacts

    archive = Path(archive)
    if not archive.is_dir():
        raise SystemExit(f"balance_sheet: no companyfacts archive at {archive}")
    try:
        index = json.loads((archive / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}

    def load(cik: int) -> tuple[dict, str]:
        path = archive / f"CIK{int(cik):010d}.json.gz"
        if not path.is_file():
            raise SystemExit(f"balance_sheet: missing predecessor companyfacts archive {path.name}")
        raw = gzip.decompress(path.read_bytes())
        digest = hashlib.sha256(raw).hexdigest()
        want = (index.get(str(int(cik))) or {}).get("sha256")
        if want and digest != want:
            raise SystemExit(f"balance_sheet: {path.name} does not decompress to the sha256 in its index.json")
        return json.loads(raw), digest

    table, columns = _layout(con)
    names = [c for c in columns if c != "id"]
    facts_start = _date(dict(con.execute("SELECT key, value FROM meta").fetchall()).get("facts_start"))
    filings: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    marks = ", ".join("?" * len(XBRL_FORMS))
    for accn, cik, form, accepted, url, report in con.execute(
            f"SELECT accession, cik, form, accepted_at, url, report_date FROM filings WHERE form IN ({marks})",
            XBRL_FORMS):
        filings[int(cik)][accn] = {"form": form, "accepted_at": accepted, "url": url, "report_date": report}
    calendars: dict[int, list[tuple]] = defaultdict(list)
    for cik, fy, fp, start, end in con.execute(
            "SELECT cik, fiscal_year, fiscal_period, period_start, period_end FROM fiscal_periods"):
        calendars[int(cik)].append((fy, fp, start, end))
    rows, missing, audits = [], [], {}
    for cik, ticker in con.execute("SELECT cik, ticker FROM companies ORDER BY ticker").fetchall():
        path = archive / f"CIK{int(cik):010d}.json.gz"
        if not path.is_file():
            missing.append(ticker)
            continue
        current, digest = load(int(cik))
        earlier = {p: load(p) for p in PREDECESSOR_CIKS.get(int(cik), ())}
        combined = merge_companyfacts(current, *(cf for cf, _ in earlier.values())) if earlier else current
        facts, audit = company_facts(cik=int(cik), ticker=ticker, companyfacts=combined,
                                     filings=filings.get(int(cik), {}), calendar=calendars.get(int(cik), []),
                                     facts_start=facts_start)
        audits[ticker] = {**audit, "companyfacts_sha256": digest}
        if earlier:
            audits[ticker]["predecessor_companyfacts_sha256"] = {str(p): sha for p, (_, sha) in earlier.items()}
        for f in facts:
            full = dict(zip(_FACT_COLUMNS, fact_row(f, "instant")))
            rows.append(tuple(full[c] for c in names))
    metric_at = names.index("metric")
    summary = {"facts": len(rows), "companies": sum(1 for a in audits.values() if a["facts"]),
               "without_companyfacts": missing,
               "by_metric": {m: sum(1 for r in rows if r[metric_at] == m) for m in BALANCE_SHEET_METRICS}}
    with con:
        marks = ", ".join("?" * len(BALANCE_SHEET_METRICS))
        summary["replaced"] = con.execute(f"DELETE FROM {table} WHERE metric IN ({marks})",
                                          BALANCE_SHEET_METRICS).rowcount
        con.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", rows)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('balance_sheet', ?)", (json.dumps(
            {k: summary[k] for k in ("facts", "companies", "without_companyfacts")}, sort_keys=True),))
    if log:
        log(f"balance sheet: {summary['facts']:,} facts at {summary['companies']} companies"
            + (f", {len(missing)} without archived companyfacts" if missing else ""))
    summary["audit"] = audits
    return summary


# --------------------------------------------------------------------------- reading a claim
@dataclass(frozen=True)
class Reading:
    """What a draft's wording says a figure is."""
    key: str
    metrics: tuple[str, ...]  # balance-sheet metrics whose figures can verify it, in order of preference
    label: str                # the draft's measure, as explanations name it
    strict: bool              # one well-defined measure: a mismatch is "✗"; otherwise "?" with the receipts
    unit: str                 # the claim unit it needs
    note: str = ""            # why a mismatch of a loose reading is not "✗"


READINGS: dict[str, Reading] = {r.key: r for r in (
    Reading("cash_and_equivalents", ("cash_and_equivalents",), "cash and cash equivalents", True, "USD"),
    Reading("cash", ("cash_and_equivalents",), "cash and cash equivalents", False, "USD",
            "“cash” can also mean cash with marketable securities or restricted cash"),
    Reading("total_assets", ("total_assets",), "total assets", True, "USD"),
    Reading("total_liabilities", ("total_liabilities",), "total liabilities", True, "USD"),
    Reading("stockholders_equity", ("stockholders_equity",), "shareholders' equity", True, "USD"),
    Reading("long_term_debt", ("long_term_debt_noncurrent", "long_term_debt"), "long-term debt", False, "USD",
            "long-term debt is reported with and without its current portion, and sometimes with finance leases"),
    Reading("total_debt", ("total_debt",), "total debt", False, "USD",
            "total debt can include or leave out short-term borrowings and finance leases"),
    Reading("shares_outstanding", ("shares_outstanding",), "shares outstanding", False, "shares",
            "the share count changes from day to day, and drafts cite it as of different dates"),
)}

# The wording of each reading, matched in lowercase text with "&" read as "and" and "long term" as "long-term". Where
# two overlap, the longer wins: "long-term debt" is not also "debt", "cash and cash equivalents" not also "cash".
_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = tuple((k, re.compile(rx)) for k, rx in (
    ("cash_and_equivalents", r"\bcash\s+and\s+(?:cash\s+)?equivalents\b"),
    ("cash", r"\bcash(?:\s+(?:on\s+hand|balances?|position|holdings|pile|reserves?))?\b"),
    ("total_assets", r"\btotal\s+assets\b"),
    ("total_liabilities", r"\btotal\s+liabilities\b"),
    ("stockholders_equity", r"\b(?:total\s+)?(?:share|stock)(?:holder|owner)s?'?\s+equity\b"),
    ("long_term_debt", r"\b(?:total\s+)?long-term\s+debt\b"),
    ("total_debt", r"\btotal\s+debt\b|\bdebt\b"),
    ("shares_outstanding", r"\b(?:common\s+)?shares\s+(?:of\s+(?:common\s+)?stock\s+)?outstanding\b"
                           r"|\boutstanding\s+(?:common\s+)?shares(?:\s+of\s+(?:common\s+)?stock)?\b"),
))
# Words a description may hold besides the measure: articles, prepositions, the balance-sheet date and holding verbs.
# None of them changes what is measured. "net", "and", "restricted", "current", "average", "under", "per", "free",
# "generated" and "returned" are deliberately absent, so "net cash", "cash and marketable securities", "total current
# assets", "average total assets", "assets under management", "cash per share", "free cash flow", "generated $5 billion
# in cash" and "cash returned" are never read as balance-sheet items.
_FRAME = frozenset("""
a an the its their our his her of in at on as by for to with this that which
total consolidated reported balance sheet sheets gaap
end ended ending close closed closing finish finished finishing
had has have having held holds hold holding hand sat sits sitting
was were is are be been stood stands stand totaled totalled totaling totalling amounted amounting reached came come
quarter quarters quarterly period periods year years fiscal date day month
quarter-end year-end period-end fiscal-year
company firm group business
""".split())
# Change words. A level reached ("rose to $34.5 billion") is a balance; a change ("rose $4 billion", "an increase of
# $4 billion") is not.
_CHANGE_WORDS = frozenset("""
increase increases increased increasing rose rise rises risen rising grew grow grows grown growing growth jumped
jump jumps climbed climb surged surge soared gained gain added fell fall falls fallen falling declined decline
declines declining decreased decrease decreases decreasing dropped drop drops shrank shrunk slipped slid up down
higher lower change changed changes
""".split())
_MONTH_WORDS = frozenset("january february march april may june july august september october november december "
                         "jan feb mar apr jun jul aug sep sept oct nov dec".split())
# Period words. They frame the measure too, but they also date it, so a quote that holds them must date the figure
# the way its period does (_BalanceCheck.date).
_PERIOD_WORDS = frozenset("""
first second third fourth 1st 2nd 3rd 4th prior previous preceding last earlier ago before next following beginning
begin start opening same calendar
""".split())
_PERIOD_SIGNAL_RE = re.compile(r"\b(?:quarters?|years?|fiscal|fy|q[1-4]|[1-4]q|(?:19|20)\d{2}|first|second|third|fourth"
                               r"|prior|previous|preceding|last|earlier|ago|beginning|start|opening)\b|year[\s-]*end")
_COMPARE_TAIL_RE = re.compile(r"\b(?:from|versus|vs\.?|compared\s+(?:with|to)|against|by|than)\s*$")
_SIGN_TAIL_RE = re.compile(r"\b(?:negative|minus)\s*$")
# A clause after the number's comma that says what the figure holds: "$13.0 billion, including restricted cash".
_QUALIFIER_AFTER_COMMA_RE = re.compile(
    r"\s*\(?\s*(?:excluding|excl\b|including|incl\b|inclusive|exclusive|net\s+of|less|plus|before|after|not\s+including"
    r"|together\s+with|combined\s+with|along\s+with|of\s+which|which|primarily|mostly|mainly|largely|substantially"
    r"|held|most|all)\b")


def _norm(text: str) -> str:
    """Lowercase with quotes and dashes unified (receipts.periods), "&" read as "and", "long term" as "long-term".
    For matching words only: offsets into the result are not offsets into the claim."""
    s = re.sub(r"\s*&\s*", " and ", periods._norm(text or ""))
    return re.sub(r"\blong[\s-]+term\b", "long-term", s)


def _phrases(text: str) -> list[tuple[str, int, int]]:
    """(reading key, start, end) of every balance-sheet phrase in normalized text, the longer winning overlaps."""
    found = [(k, m.start(), m.end()) for k, rx in _PHRASES for m in rx.finditer(text)]
    chosen: list[tuple[str, int, int]] = []
    for k, a, b in sorted(found, key=lambda x: (-(x[2] - x[1]), x[1])):
        if not any(a < y and b > x for _, x, y in chosen):
            chosen.append((k, a, b))
    return sorted(chosen, key=lambda x: x[1])


def _words(text: str) -> list[str]:
    return [w for w in (t.strip("'.-") for t in re.findall(r"[a-z0-9][a-z0-9'.\-]*", text)) if w]


def _framing(text: str, company: set[str], *, level: bool = False) -> bool:
    """Every word of text frames the measure: _FRAME, period words, the company's name, a date or period label, and
    with `level` (the number is a level reached: "rose to $X") change words too. A hyphenated word counts when each of
    its parts does ("third-quarter", "year-end"; never "short-term" or "non-gaap")."""
    company = company | {part for w in company for part in w.split("-")}  # "Bio-Techne", "Coca-Cola"
    for w in _words(text):
        for k in re.sub(r"'s?$", "", w).split("-"):
            if k and not (k in _FRAME or k in _PERIOD_WORDS or k in company or k in _MONTH_WORDS
                          or (level and k in _CHANGE_WORDS)
                          or re.fullmatch(r"(?:fy|q[1-4]|[1-4]q|h[12])?'?\d{0,4}(?:st|nd|rd|th)?", k)):
                return False
    return True


def _blank_periods(text: str) -> tuple[str, list[tuple[int, int, date]]]:
    """The text, normalized and of the same length, with dates, period phrases and years blanked, plus the dates found
    as (start, end, date): their digits are not figures, and their words not a description."""
    s = periods._norm(text or "")
    found: list[tuple[int, int, date]] = []

    def blank(a: int, b: int) -> None:
        nonlocal s
        s = s[:a] + " " * (b - a) + s[b:]

    for m in list(periods._DATE_RE.finditer(s)):
        if (d := periods._date_of(m)) is not None:
            found.append((m.start(), m.end(), d))
        blank(m.start(), m.end())
    while True:
        hits = [m for rx in periods._PHRASE_RES if (m := rx.search(s))]
        if not hits:
            break
        m = min(hits, key=lambda m: m.start())
        blank(m.start(), m.end())
    for m in list(re.finditer(r"(?<![\w$.,])(?:(?:fy|q[1-4]|[1-4]q)\s*)?'?(?:19|20)\d{2}\b(?![.,]?\d)", s)):
        blank(m.start(), m.end())
    return s, found


@dataclass(frozen=True)
class Wording:
    reading: Reading
    phrase: str                 # the draft's own phrase, normalized: "stockholders' equity"
    negative: bool              # "negative $2.1 billion" (shareholders' equity only)
    period_words: str           # the quote's own words around the number when they date it ("a year ago", "at
                                # the end of the prior fiscal year", "at fiscal 2025 year-end"), else ""
    respectively: bool          # "... were $X and $Y at A and B, respectively"
    dates: tuple[date, ...]     # dates written next to the number ("at September 27, 2026")


def read_wording(claim: Claim, names: Iterable[str] = ()) -> Optional[Wording]:
    """The balance-sheet item a claim's own words state, or None. The quote must name the item right next to the
    number (no other figure between them, and nothing else in that stretch of the clause but framing words), and
    metric_text must name the same item and nothing more. "Apple ended the quarter with $34.5 billion in cash and
    cash equivalents" and "Total assets were $360 billion" read; "cash and marketable securities", "free cash flow",
    "cash returned", "net debt", "current portion of long-term debt", "total assets under management", "diluted shares
    outstanding", "cash rose $4 billion" and a comparison base ("up from $29.9 billion") don't. names: words of the
    company's own names, which frame the quote like the claim's entity does."""
    from receipts import verify as V  # receipts.verify imports this module lazily, from verify(), so this is safe
    span, vt = claim.span_text or "", claim.value_text or ""
    pos = V._value_pos(claim)
    digit = re.search(r"\d", vt)
    if pos is None or digit is None:
        return None
    end = pos + len(vt)
    blank, dates_found = _blank_periods(span)
    blank = blank[:pos] + periods._norm(span[pos:end]) + blank[end:]  # the number itself is never blanked
    figures = [(m.start(), m.end()) for m in V._FIGURE_RE.finditer(blank)
               if re.search(r"\d", m.group(0)) and not (m.start() < end and m.end() > pos)]
    start = max([b for _, b in figures if b <= pos] + V.sentence_breaks(span[:pos]) + [0])
    stop = min([a for a, _ in figures if a >= end] + [len(span)])
    if cut := re.search(r"[;!?\n,]|\.(?=\s|$)", blank[end:stop]):
        if cut.group() == "," and _QUALIFIER_AFTER_COMMA_RE.match(_norm(blank[end + cut.end():])):
            return None  # "$13.0 billion, including restricted cash": the figure is qualified
        stop = end + cut.start()
    before = V.strip_bound_currency_prefix(blank[start:pos] + vt[:digit.start()].lower())
    for _kind, _inclusive, rx in V._HEDGES:  # "more than", "about": hedge_of reads the bound itself
        if hit := rx.search(before):
            before = before[:hit.start()]
            break
    negative = bool(_SIGN_TAIL_RE.search(before))
    before = _SIGN_TAIL_RE.sub("", before)
    if _COMPARE_TAIL_RE.search(before):
        return None  # "up from $29.9 billion", "increased by $4 billion": not this balance
    before = re.sub(r"^[\s,;:]*(?:(?:and|but|while|whereas|with|as|plus)\b)?", " ", _norm(before))
    after = _norm(blank[end:stop])
    if stop < len(span) and re.match(r"\s*\S", blank[stop:]) and stop in [a for a, _ in figures]:
        # the words that lead into the next figure belong to it: "rose to $383.3 billion from $331.5 billion"
        after = re.sub(r"\b(?:(?:up|down)\s+)?(?:from|versus|vs\.?|compared\s+(?:with|to)|against|than)\s*$", " ",
                       after)
    after = re.sub(r"[\s,;:]*(?:\b(?:and|or|but|while|whereas|with|as|plus))?[\s,;:]*$", " ", after)
    company = (V._company_words(claim) | set(_words(_norm(re.sub(r"\([^)]*\)", " ", claim.entity or ""))))
               | set(names))
    level = bool(re.search(r"\b(?:to|at)\s*$", before))
    pb, pa = _phrases(before), _phrases(after)
    if len(pb) + len(pa) != 1:
        return None  # no item named next to the number, or two ("total liabilities and shareholders' equity")
    key, a, b = (pb or pa)[0]
    phrase = (before if pb else after)[a:b]
    rest_before = before[:a] + " " + before[b:] if pb else before
    rest_after = after[:a] + " " + after[b:] if pa else after
    if not _framing(rest_before, company, level=level) or not _framing(rest_after, company):
        return None
    reading = READINGS[key]
    # metric_text names the same item and nothing more; "cash" there and "cash and cash equivalents" in the quote (or
    # the reverse) is one item, read as the quote has it
    mt = _norm(_blank_periods(claim.metric_text or "")[0])
    named = _phrases(mt)
    family = {"cash", "cash_and_equivalents"} if key in ("cash", "cash_and_equivalents") else {key}
    if len(named) > 1 or (named and named[0][0] not in family):
        return None
    if not _framing(mt[:named[0][1]] + " " + mt[named[0][2]:] if named else mt, company):
        return None
    if negative and not ITEMS[reading.metrics[0]].signed:
        return None
    own = " ".join((periods._norm(span[start:pos]), periods._norm(span[end:stop])))
    return Wording(reading, phrase, negative, own if _PERIOD_SIGNAL_RE.search(own) else "",
                   bool(re.search(r"\brespectively\b", _norm(span))),
                   tuple(sorted({d for a0, b0, d in dates_found if start <= a0 and b0 <= stop})))


# --------------------------------------------------------------------------- the balance-sheet date
_YEAR_END_RE = re.compile(r"\byear[\s-]*end\b|\bend\s+of\s+(?:the\s+)?(?:(?P<rel>prior|previous|preceding|last)\s+)?"
                          r"(?:fiscal\s+|calendar\s+)?year\b|\bfiscal[\s-]+year[\s-]*end\b")
_PRIOR_RE = re.compile(r"\b(?:prior|previous|preceding|last)\b")
_NOT_A_BALANCE_RE = re.compile(r"\b(?:beginning|begin|start|opening|since|average|during|throughout|ytd)\b"
                               r"|\byear[\s-]to[\s-]date\b")
_RELATIVE_RE = re.compile(r"\b(?:prior|previous|preceding|last|earlier|ago|before|after|next|following)\b")


@dataclass(frozen=True)
class BalanceDate:
    date: Optional[date]           # None: the period is well defined but no stored filing reports it yet
    rp: Optional[ResolvedPeriod]   # the fiscal period the date closes, when the calendar has one
    contextual: bool               # the period named no date, year, quarter, month or shift: it is the context's


def _closes(cal: list[CalEntry], d: date) -> Optional[ResolvedPeriod]:
    """The fiscal period ending on d (its year when a year ends there)."""
    e = min((e for e in cal if e.end == d), key=lambda e: e.fiscal_period != "FY", default=None)
    return ResolvedPeriod(e.fiscal_year, e.fiscal_period, e.end) if e else None


def balance_date(claim: Claim, cal: list[CalEntry], *, context: Optional[str], as_of: Any,
                 dates: Sequence[date] = (), balance_dates: Sequence[date] = ()) -> Optional[BalanceDate]:
    """The balance-sheet date a claim's period names, or None when no single date is meant:
      * a date written in the period or next to the number ("as of September 27, 2026") is that date;
      * a year end ("at fiscal 2025 year-end", "at the end of the prior fiscal year"; "at year-end" only when the
        context period is a fiscal year or its fourth quarter) is that fiscal year's last day;
      * an explicitly calendar year end is December 31 of the stated or contextual year, allowing a filed balance
        date within seven days for companies whose quarter ends fall on a fixed weekday;
      * any other period ("at the end of the quarter", "third quarter of 2026", "a year ago", the context's own
        period) is the end of the fiscal period receipts.periods resolves on the company's calendar.
    The start of a period, an average, "last quarter", "the prior year" (its end, or the same quarter a year earlier?)
    and relative words the resolver doesn't apply are None: a balance at another date than the one meant would read
    as wrong."""
    text = claim.period_text or ""
    s = periods._norm(text)
    p = periods.parse(text)
    if p.kind in ("ytd", "conflict") or _NOT_A_BALANCE_RE.search(s):
        return None
    written = {d for _, _, d in _blank_periods(text)[1]} | set(dates)
    if len(written) > 1:
        return None  # "at September 27, 2026 and September 28, 2025"
    if written:
        d = written.pop()
        return BalanceDate(d, _closes(cal, d), False)
    if (m := _YEAR_END_RE.search(s)) is not None:
        if re.search(r"\bcalendar\b", s):
            year = p.year if p.year is not None else periods.parse(context or "").year
            if year is None:
                return None
            if p.year is None and (m.group("rel") or _PRIOR_RE.search(s)):
                year -= 1
            target = date(year, 12, 31)
            ends = {d for d in ({e.end for e in cal} | set(balance_dates)) if abs((d - target).days) <= 7}
            nearest = sorted(ends, key=lambda d: abs((d - target).days))
            if len(nearest) > 1 and abs((nearest[0] - target).days) == abs((nearest[1] - target).days):
                return None
            d = nearest[0] if nearest else target
            return BalanceDate(d, _closes(cal, d), False)
        if p.year is not None:
            if not p.fiscal and periods.is_calendar_fiscal_year(cal) is not True:
                return None  # "year-end 2025" on a fiscal year that isn't the calendar year reads two ways
            fy = p.year
        else:
            base = periods.resolve_in_calendar("", cal, context=context, as_of=as_of)
            if base is None:
                return None
            if m.group("rel") or _PRIOR_RE.search(s):
                fy = base.fiscal_year - 1
            elif base.fiscal_period in ("FY", "Q4"):
                fy = base.fiscal_year
            else:
                return None  # "at year-end" in a story about a first, second or third quarter
        e = periods.lookup(cal, fy, "FY") or periods.lookup(cal, fy, "Q4")
        return BalanceDate(e.end if e else None, ResolvedPeriod(fy, "FY", e.end if e else None), False)
    shifted = p.shift in ("year", "quarter")
    if _RELATIVE_RE.search(s) and not shifted:
        return None  # "the prior fiscal year" (its end?), "the following quarter": nothing here reads them
    if p.shift == "year" and not re.search(r"\bago\b|\bearlier\b|\bsame\b", s):
        return None  # "the prior year": its end, or the same quarter a year earlier?
    hint = {"quarter": "quarter", "annual": "annual"}.get(claim.period_type or "")
    rp = periods.resolve_in_calendar(text, cal, context=context, period_type=hint, comparison="none", as_of=as_of)
    if rp is None:
        return None
    contextual = not shifted and p.year is None and p.quarter is None and p.month is None
    return BalanceDate(rp.period_end, rp, contextual)


def fmt_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


# --------------------------------------------------------------------------- the check
def check(claim: Claim, store: EvidenceStore, *, as_of: Optional[datetime] = None, context: Optional[str] = None,
          entity: Optional[tuple[int, str]] = None) -> Optional[Verdict]:
    """The verdict for a balance-sheet claim (read_wording), or None when the claim's own words name no balance-sheet
    item, so receipts.verify answers as before. as_of: an aware UTC datetime or None."""
    if claim.forward_looking or not math.isfinite(claim.value) or claim.basis == "non-GAAP":
        return None
    from receipts import verify as V
    entity = entity or V.resolve_claim_entity(claim, store, context)
    wording = read_wording(claim, _company_names(store, entity))
    if wording is None or claim.unit != wording.reading.unit:
        return None
    if claim.value < 0 and not ITEMS[wording.reading.metrics[0]].signed:
        return None  # "($13.0 billion)" of cash: no balance of the item is ever negative, so the figure stays unread
    if entity is None:
        return V.unresolved_entity(claim, store, as_of)
    return _BalanceCheck(claim, store, (int(entity[0]), str(entity[1])), wording, as_of, context).run()


def _company_names(store: EvidenceStore, entity: Optional[tuple[int, str]]) -> set[str]:
    """The words of a resolved company's names (its SEC and display names and the names receipts.store knows), which
    frame a quote as the claim's entity does: "Alphabet ended the quarter with ..." for a claim whose entity reads
    "Google"."""
    if entity is None:
        return set()
    from receipts.store import display_name, known_names
    names = list(known_names(str(entity[1])))
    lookup = getattr(store, "company", None)
    try:
        info = lookup(int(entity[0])) if callable(lookup) else None
    except Exception:  # an optional extra of receipts.store.Store
        info = None
    if info and info.get("name"):
        names += [info["name"], display_name(str(entity[1]), info["name"])]
    return {w for n in names for w in _words(_norm(re.sub(r"[/\\][A-Za-z]{2,3}[/\\]?", " ", n)))}


class _BalanceCheck:
    def __init__(self, claim: Claim, store: EvidenceStore, entity: tuple[int, str], wording: Wording,
                 as_of: Optional[datetime], context: Optional[str]):
        from receipts import verify as V
        self.V, self.c, self.store, self.as_of = V, claim, store, as_of
        self.cik, self.ticker = entity
        self.w, self.r = wording, wording.reading
        others = V.context_entities(context, store)
        if others and all(cik != self.cik for cik, _, _ in others):
            if periods.parse(claim.period_text).month is None or periods.parse(context).fiscal:
                context = None  # the context line names another company: its fiscal period isn't this one's
        self.context = context
        name = " ".join(re.sub(r"\([^)]*\)", " ", claim.entity or "").split())
        self.company = name if name.casefold() not in V.GENERIC_ENTITIES else self.ticker
        self.co_s = V.possessive(self.company)
        self.cal = periods.calendar(store, self.cik)
        self.claimed = V.claimed_value(claim.model_copy(update={"span_text": "", "metric_text": ""}), change=False)
        if wording.negative and self.claimed > 0:
            self.claimed = -self.claimed  # "negative $2.1 billion" of shareholders' equity
        self.tol = V.tolerance(claim)
        self.hedge = V.hedge_of(claim)
        self._facts: dict[str, list[Fact]] = {}
        # the claim names its balance-sheet date itself: a written date, or the end or close of a period
        said = periods._norm(f"{claim.period_text} {wording.period_words}")
        self.pinned = bool(wording.dates or periods._DATE_RE.search(said)
                           or re.search(r"\b(?:end|ended|ending|close|closed|closing)\b|[a-z]-end\b", said))

    # -- evidence ------------------------------------------------------------
    def facts(self, metric: str) -> list[Fact]:
        if metric not in self._facts:
            self._facts[metric] = [f for f in (self.store.facts(self.cik, metric) or [])
                                   if f.metric == metric and f.period_start is None and f.segment is None
                                   and f.basis == "GAAP" and math.isfinite(f.value)]
        return self._facts[metric]

    def observed(self, d: date, *, include_restricted: bool = False) -> list[Fact]:
        """Every version of the reading's figures at d. For a share count at a balance-sheet date the claim doesn't pin
        ("Testco had 1.23 billion shares outstanding", its period the quarter), also the count on the cover page of the
        filing whose balance sheet that is, dated as the cover dates it; never when it says the date ("at the end of
        the quarter", "as of September 30, 2025")."""
        out = [f for m in self.r.metrics for f in self.facts(m) if f.period_end == d
               and (m != "cash_and_equivalents" or f.concept in (CCE.name, CCE_RESTRICTED.name))]
        if self.r.key == "cash_and_equivalents" and not include_restricted:
            out = [f for f in out if f.concept == CCE.name or self._cash_support(f)]
        if self.r.key == "shares_outstanding" and d in {e.end for e in self.cal} and not self.pinned:
            reports = {f.accession for f in out if f.concept == SHARES_BALANCE.name}
            reports |= {r["accession"] for r in self._filings() if _date(r.get("report_date")) == d}
            out += [f for f in self.facts("shares_outstanding")
                    if f.concept == SHARES_COVER.name and f.accession in reports and f not in out]
        return out

    def _cash_support(self, total: Fact) -> list[Fact]:
        """Zero restricted-cash tags from this exact filing/date make its inclusive total cash-only as well."""
        if total.concept != CCE_RESTRICTED.name or total.source != "xbrl" or total.unit != "USD":
            return []
        rows = [f for f in self.facts("cash_and_equivalents")
                if f.concept in {c.name for c in RESTRICTED_SUPPORT} and f.value == 0
                and f.period_end == total.period_end and f.accession == total.accession
                and f.accepted_at == total.accepted_at and f.source == "xbrl" and f.unit == "USD"
                and f.form in XBRL_FORMS]
        # Be conservative even with stores not built by company_facts: conflicting tags invalidate the zero proof.
        if any(f.value != 0 for f in self.facts("cash_and_equivalents")
               if f.concept in {c.name for c in RESTRICTED_SUPPORT}
               and f.period_end == total.period_end and f.accession == total.accession):
            return []
        names = {f.concept for f in rows}
        complete = (RESTRICTED_TOTAL.name in names or
                    {RESTRICTED_CURRENT.name, RESTRICTED_NONCURRENT.name} <= names)
        return rows if complete else []

    def _filings(self) -> list[dict]:
        listing = getattr(self.store, "filings", None)
        try:
            return list(listing(self.cik, forms=list(XBRL_FORMS)) or []) if callable(listing) else []
        except Exception:  # an optional extra of receipts.store.Store
            return []

    def public(self, f: Fact) -> bool:
        return self.as_of is None or self.V.to_utc(f.accepted_at) <= self.as_of

    def accepts(self, f: Fact) -> Optional[bool]:
        """Whether the filed value bears the claim out: its bound when it states one ("more than $30 billion"), else
        equality within the written precision. None: an approximation within its band but not exact ("≈")."""
        V, h, x, v = self.V, self.hedge, self.claimed, f.value
        if h is None or h.kind == "approx":
            if V.within(x, v, self.tol):
                return True
            return None if h is not None and V.within(x, v, max(2 * self.tol, 0.01 * abs(x))) else False
        if h.kind == "nearly":
            return x - max(2 * self.tol, 0.01 * abs(x)) <= v < x
        if h.kind == "lower":
            return v >= x if h.inclusive else v > x
        return v <= x if h.inclusive else v < x

    # -- wording ---------------------------------------------------------------
    def amount(self, v: float) -> str:
        return f"{v:,.0f} shares" if self.r.unit == "shares" else self.V.fmt_money(v)

    def measure(self, f: Fact) -> str:
        """The filed measure's name ("long-term debt excluding its current portion"), in the draft's own word for
        shareholders' or stockholders' equity."""
        if f.concept == "us-gaap:StockholdersEquity" and "stock" in self.w.phrase:
            return "stockholders' equity"
        return CONCEPTS[f.concept].label if f.concept in CONCEPTS else self.r.label

    def statement(self, f: Fact) -> str:
        """"cash and cash equivalents were $39.544 billion at June 27, 2026"; a share count says which date it has."""
        if f.concept == SHARES_COVER.name:
            return (f"its cover page reports {f.value:,.0f} shares of common stock outstanding as of "
                    f"{fmt_date(f.period_end)}")
        if f.concept == SHARES_BALANCE.name:
            return f"{f.value:,.0f} shares of common stock were outstanding at {fmt_date(f.period_end)}"
        concept = CONCEPTS.get(f.concept or "")
        verb = "were" if concept and concept.plural else "was"
        return f"{self.measure(f)} {verb} {self.amount(f.value)} at {fmt_date(f.period_end)}"

    def verdict(self, status: str, reason: str, explanation: str, evidence: Iterable[Fact] = (),
                expected: Optional[float] = None) -> Verdict:
        V = self.V
        evidence = V._dedupe_facts(evidence)
        if self.r.key == "cash_and_equivalents":
            # Generic fact deduplication omits concept from its key; preserve distinct current/noncurrent zeros.
            for support in [s for f in list(evidence) for s in self._cash_support(f)]:
                if support not in evidence:
                    evidence.append(support)
        return Verdict(claim_id=self.c.id, status=status, reason=reason, explanation=explanation,
                       expected_value=None if expected is None else V.to_claim_units(expected, self.c),
                       evidence=evidence, as_of=self.as_of)

    def _order(self, f: Fact) -> tuple[int, bool]:
        """The preferred measure first; a balance-sheet share count before the cover page's."""
        return (self.r.metrics.index(f.metric) if f.metric in self.r.metrics else 9, f.concept == SHARES_COVER.name)

    def best(self, facts: Sequence[Fact]) -> Fact:
        """The receipt to cite for a match: the preferred measure, its first filing."""
        return min(facts, key=lambda f: (self._order(f), self.V.to_utc(f.accepted_at)))

    def current(self, facts: Sequence[Fact]) -> Fact:
        """The figure to correct a wrong claim with: the preferred measure, its latest version visible."""
        return min(facts, key=lambda f: (self._order(f), -self.V.to_utc(f.accepted_at).timestamp()))

    # -- the rules -------------------------------------------------------------
    def date(self) -> tuple[Optional[BalanceDate], str]:
        """The balance-sheet date, and the period words it was read from. The claim's period gives it (balance_date).
        When the quote's own words around the number date the figure too ("a year ago", "at the end of the prior
        fiscal year", "ended the quarter with"), they must name one date as well, and the same one, unless the claim's
        period only repeats the context line: the draft's own words outrank a period the extraction filled in."""
        c = self.c
        said = " ".join((c.period_text or "").split()) or "no period"
        unclear = f"Couldn't tell which {self.company} balance-sheet date \"{said}\" refers to."
        filed_dates = tuple(d for r in self._filings() if (d := _date(r.get("report_date"))) is not None)
        bd = balance_date(c, self.cal, context=self.context, as_of=self.as_of, dates=self.w.dates,
                          balance_dates=filed_dates)
        if not self.w.period_words:
            return bd, unclear
        # "the quarter", "a year ago" in the quote count from the story's period: the context line's, else the claim's
        own_calendar_end = bool(_YEAR_END_RE.search(self.w.period_words)
                                and re.search(r"\bcalendar\b", self.w.period_words))
        own_context = (c.period_text if own_calendar_end and periods.parse(c.period_text).year is not None
                       else self.context or c.period_text or None)
        own = balance_date(c.model_copy(update={"period_text": self.w.period_words}), self.cal,
                           context=own_context, as_of=self.as_of, dates=self.w.dates, balance_dates=filed_dates)
        if own is None:
            return None, (f"Couldn't tell which {self.company} balance-sheet date the sentence's own words for this "
                          f"figure refer to.")
        if own_calendar_end and not self.w.dates and not _blank_periods(c.period_text)[1]:
            return own, unclear  # the claim's year dates the calendar qualifier, not its fiscal quarter end
        if bd is None or bd.contextual or own.date == bd.date:
            return own, unclear
        return None, (f"The sentence's own words date this figure differently from its period (\"{said}\"), so NoCap "
                      f"doesn't guess which {self.company} balance sheet it is.")

    def run(self) -> Verdict:
        c = self.c
        if not math.isfinite(self.claimed) or not math.isfinite(self.tol):
            return self.verdict("unverifiable", "ambiguous", "This figure exceeds the supported numeric range after "
                                                             "applying its units or precision.")
        bd, unclear = self.date()
        if bd is None:
            return self.verdict("unverifiable", "ambiguous", unclear)
        if bd.date is None:
            label = periods.label(bd.rp, self.cal) if bd.rp else "that period"
            return self.verdict("unverifiable", "no_evidence",
                                f"No filing NoCap holds reports {self.co_s} balance sheet at the end of {label}.")
        d = bd.date
        every = self.observed(d)
        pub = [f for f in every if self.public(f)]
        if not pub and self.r.key == "cash_and_equivalents":
            combined = [f for f in self.observed(d, include_restricted=True)
                        if f.concept == CCE_RESTRICTED.name and self.public(f)]
            if combined:
                best = self.current(combined)
                return self.verdict(
                    "unverifiable", "no_evidence",
                    f"{self.co_s} {self.V.form_phrase(best)} reports that {self.statement(best)}. "
                    "That total includes restricted cash; no same-filing evidence shows that all restricted cash "
                    "is zero, so NoCap can't check cash and cash equivalents alone.", combined)
        if not every:
            return self._no_evidence(d, bd)
        if not pub:
            return self._look_ahead(d, every)
        tests = [(f, self.accepts(f)) for f in pub]
        if hits := [f for f, t in tests if t is True]:
            bound = self.hedge.words if self.hedge is not None and self.hedge.kind != "approx" else ""
            return self._verified(hits, note=f", which is {bound} {c.value_text}" if bound else "")
        if near := [f for f, t in tests if t is None]:
            best = self.best(near)
            return self.verdict("approximate", "approximate",
                                f"Close enough for an approximation: {self.co_s} {self.V.form_phrase(best)} reports "
                                f"that {self.statement(best)}.", [best])
        actual = self.current(pub)
        restricted = actual.concept == CCE_RESTRICTED.name and not self._cash_support(actual)
        if restricted or self.w.respectively or not self.r.strict:
            return self._not_wrong(d, pub, actual, restricted=restricted)
        for diagnose in (() if self.bounded else (self._diag_scale, self._diag_period)):  # a bound is judged alone
            if (v := diagnose(d, actual)) is not None:
                return v
        bound = self.hedge.words if self.hedge is not None and self.hedge.kind != "approx" else ""
        return self.verdict("wrong", "value_mismatch",
                            f"{self.co_s} {self.V.form_phrase(actual)} reports that {self.statement(actual)}, "
                            f"{f'which is not {bound}' if bound else 'not'} {c.value_text}.",
                            [actual], expected=actual.value)

    def _verified(self, hits: Sequence[Fact], note: str = "") -> Verdict:
        best = self.best(hits)
        if self.r.key == "cash_and_equivalents" and self._cash_support(best):
            note += "; the same filing tags all restricted cash and cash equivalents as zero"
        return self.verdict("verified", "match",
                            f"Matches {self.co_s} {self.V.form_phrase(best)}: {self.statement(best)}{note}.", hits)

    def statements(self, facts: Sequence[Fact]) -> str:
        """Several filed figures in one clause: "long-term debt excluding its current portion of $78.328 billion and
        long-term debt including its current portion of $90.678 billion at September 27, 2025"."""
        days = {f.period_end for f in facts}
        shares = any(f.concept in (SHARES_BALANCE.name, SHARES_COVER.name) for f in facts)
        if len(facts) == 1 or len(days) > 1 or shares:
            return "; ".join(self.statement(f) for f in facts)
        parts = [f"{self.measure(f)} of {self.amount(f.value)}" for f in facts]
        return f"{', '.join(parts[:-1])} and {parts[-1]} at {fmt_date(days.pop())}"

    def _not_wrong(self, d: date, pub: Sequence[Fact], actual: Fact, *, restricted: bool) -> Verdict:
        """A figure that matches none of the filed figures, where a mismatch proves nothing: "?" with them as receipts.
        The latest version of each measure filed for d is shown."""
        shown: dict[str, Fact] = {}
        for f in sorted(pub, key=lambda f: (self._order(f), -self.V.to_utc(f.accepted_at).timestamp())):
            shown.setdefault(f.concept or "", f)
        facts = list(shown.values())
        if restricted:
            why = (f"{self.company} tags its cash only together with restricted cash, so NoCap can't check a figure "
                   f"for cash and cash equivalents alone")
        elif self.w.respectively and self.r.strict:
            why = ("the sentence pairs several figures with several dates (“respectively”), so NoCap doesn't treat a "
                   "different figure as wrong")
        else:
            why = f"{self.r.note}, so NoCap doesn't treat a different figure as wrong"
        one_clause = len(facts) > 1 and len({f.period_end for f in facts}) == 1 and not any(
            f.concept in (SHARES_BALANCE.name, SHARES_COVER.name) for f in facts)
        who = (f"{self.co_s} {self.V.form_phrase(actual)}" if len({f.accession for f in facts}) == 1
               else f"{self.co_s} filings")
        verb = ("report" if who.endswith("filings") else "reports") + ("" if one_clause else " that")
        text = f"{who} {verb} {self.statements(facts)}, not {self.c.value_text}; {why}."
        hint = self._other_date(d)
        if hint is not None:
            text += f" {self.c.value_text} matches its {self.measure(hint)} at {fmt_date(hint.period_end)}."
            facts.append(hint)
        return self.verdict("unverifiable", "no_evidence", text, facts)

    def _other_date(self, d: date) -> Optional[Fact]:
        """The same figure at a balance-sheet date a writer plausibly confuses with d: the quarter end before or after
        it, the same date a year off, or the last fiscal year end before it. Only a precise figure (half a unit at most
        0.5% of it), equal within its precision, qualifies: a coarse one or a bound matches some date by chance."""
        if self.bounded or not self.claimed or self.tol / abs(self.claimed) > self.V.PEER_MATCH_MAX_REL_TOL:
            return None
        ends = sorted({e.end for e in self.cal} - {d})
        year_ends = sorted(e.end for e in self.cal if e.fiscal_period == "FY" and e.end < d)
        cands = [e for e in ends if abs((e - d).days) <= 100 or 350 <= abs((e - d).days) <= 380] + year_ends[-1:]
        for e in dict.fromkeys(sorted(cands, key=lambda x: abs((x - d).days))):
            hits = [f for f in self.observed(e) if self.public(f) and self.V.within(self.claimed, f.value, self.tol)]
            if hits:
                return self.best(hits)
        return None

    @property
    def bounded(self) -> bool:
        return self.hedge is not None and self.hedge.kind in ("lower", "upper", "nearly")

    def _diag_period(self, d: date, actual: Fact) -> Optional[Verdict]:
        hint = self._other_date(d)
        if hint is None:
            return None
        return self.verdict("wrong", "period_mismatch",
                            f"{self.c.value_text} is {self.co_s} {self.measure(hint)} at {fmt_date(hint.period_end)}; "
                            f"at {fmt_date(d)} it was {self.amount(actual.value)}.", [actual, hint],
                            expected=actual.value)

    def _diag_scale(self, d: date, actual: Fact) -> Optional[Verdict]:
        V = self.V
        for factor, words in V.SCALE_SHIFTS:
            hits = [f for f in self.observed(d) if self.public(f)
                    and V.within(self.claimed * factor, f.value, self.tol * factor)]
            if hits:
                best = self.best(hits)
                return self.verdict("wrong", "scale_mismatch",
                                    f"{self.co_s} {V.form_phrase(best)} reports that {self.statement(best)}, so "
                                    f"{self.c.value_text} is off by a factor of {words}.", [best], expected=best.value)
        return None

    def _no_evidence(self, d: date, bd: BalanceDate) -> Verdict:
        held = {f.period_end for m in BALANCE_SHEET_METRICS for f in self.facts(m)}
        if bd.rp is None and d not in held:
            return self.verdict("unverifiable", "no_evidence",
                                f"No {self.company} balance sheet NoCap holds is dated {fmt_date(d)}, so it can't "
                                f"check {self.c.value_text} at that date.")
        if self.r.key == "total_debt":
            return self.verdict("unverifiable", "no_evidence",
                                f"No filing NoCap holds tags a total of {self.co_s} debt at {fmt_date(d)}, and NoCap "
                                f"doesn't add up debt components itself.")
        return self.verdict("unverifiable", "no_evidence",
                            f"No filing fact for {self.co_s} {self.r.label} at {fmt_date(d)}.")

    def _earnings_release(self, d: date) -> Optional[dict]:
        """The earliest Item 2.02 8-K within receipts.verify's release window after d."""
        V, best = self.V, None
        listing = getattr(self.store, "filings", None)
        try:
            rows = list(listing(self.cik, forms=["8-K", "8-K/A"]) or []) if callable(listing) else []
        except Exception:
            return None
        for r in rows:
            if "2.02" not in str(r.get("items") or ""):
                continue
            at = V.to_utc(r["accepted_at"])
            if 0 < (at.date() - d).days <= V.EARNINGS_RELEASE_WINDOW_DAYS and (best is None or at < best[0]):
                best = (at, r)
        return best[1] if best else None

    def _look_ahead(self, d: date, every: Sequence[Fact]) -> Verdict:
        """Nothing at d was public at as_of: look-ahead as receipts.verify decides it for every figure. It is wrong /
        look_ahead only when as_of also precedes the period's earnings release, else "?": the release may have
        carried the balance sheet already."""
        V = self.V
        assert self.as_of is not None
        first = min(every, key=lambda f: V.to_utc(f.accepted_at))
        first_at = V.to_utc(first.accepted_at)
        release = self._earnings_release(d)
        release_at = V.to_utc(release["accepted_at"]) if release else None
        what = f"{self.co_s} {self.r.label} at {fmt_date(d)}"
        if release_at is not None and release_at <= first_at:
            if self.as_of >= release_at:
                return self.verdict(
                    "unverifiable", "no_evidence",
                    f"{self.co_s} earnings release for the period ended {fmt_date(d)} (8-K accepted "
                    f"{V.fmt_ts(release_at)}) was out by the as-of time {V.fmt_ts(self.as_of)}, but NoCap has no "
                    f"press-release figure for its {self.r.label}, and its {V.form_phrase(first)} came later, at "
                    f"{V.fmt_ts(first_at)}.")
            text = (f"{what} wasn't public until its 8-K earnings release ({release['accession']}) was accepted at "
                    f"{V.fmt_ts(release_at)}, after the as-of time {V.fmt_ts(self.as_of)}")
        elif release_at is not None:
            text = (f"{what} wasn't public until its {V.form_phrase(first)} was accepted at {V.fmt_ts(first_at)}, "
                    f"after the as-of time {V.fmt_ts(self.as_of)}")
        else:
            return self.verdict(
                "unverifiable", "no_evidence",
                f"The first filing with {what} that NoCap holds is its {V.form_phrase(first)}, accepted at "
                f"{V.fmt_ts(first_at)}, after the as-of time {V.fmt_ts(self.as_of)}, and the earnings release that may "
                f"have come earlier can't be dated.")
        value_ok = any(self.accepts(f) is True for f in every)
        filed = self.current(every)
        if not value_ok:
            text += f", and the filed figure is {self.amount(filed.value)}"
        return self.verdict("wrong", "look_ahead", text + ".", [first], expected=None if value_ok else filed.value)


# --------------------------------------------------------------------------- command line
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=DB_PATH, help="evidence database to update (data/facts.sqlite)")
    parser.add_argument("--archive", type=Path, default=ARCHIVE,
                        help="companyfacts archive (data/archive/companyfacts)")
    parser.add_argument("--audit", type=Path, help="write the per-company audit here as JSON")
    args = parser.parse_args(argv)
    if not args.db.exists():
        raise SystemExit(f"balance_sheet: {args.db} does not exist")
    con = sqlite3.connect(args.db)
    try:
        summary = add_to_db(con, args.archive, log=print)
    finally:
        con.close()
    if args.audit:
        args.audit.write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    for metric, n in summary["by_metric"].items():
        print(f"  {metric:<26} {n:>7,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
