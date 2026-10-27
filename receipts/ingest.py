"""Build data/facts.sqlite from SEC EDGAR: filings, XBRL facts, derived quarters, fiscal calendars.

    python -m receipts.ingest --tickers NVDA,AAPL,MSFT,META,AMZN,GOOGL,JPM,BAC,AMD,NFLX --quarters 8

Idempotent: each company is replaced in one transaction, and every EDGAR response comes from the
shared cache when present. Only missing pages are fetched, politely.

Window: the last N quarters plus one more fiscal year, so every quarter has its year-ago comparison.
A fact is stored when its period ends on or after `facts_start`. A period's original filing is always
filed after the period ends, so every stored fact keeps its original version and its true
first-public time, not just a later comparative. Filings are loaded from 120 days before
`facts_start` so Q4 = FY - 9M and YTD differences can use inputs reported just before the window.

Rules:
  * XBRL facts only from 10-K/10-Q (and amendments). DEF 14A pay-versus-performance tables, 8-K
    recasts and the like are excluded.
  * Metric -> concept: per filing and period, the first concept of METRIC_CONCEPTS present wins.
    Selecting per filing (not per company) keeps every version when a company switches concepts.
    GOOGL moved from RevenueFromContractWithCustomerExcludingAssessedTax to Revenues in 2025.
  * Fiscal calendar: each 10-Q/10-K's own current period, labelled by its DEI fiscal year and period.
    Q4 runs from the day after Q3 ends to the FY end.
  * Derived (source="derived"): Q4 = FY - 9M YTD (or FY - Q1 - Q2 - Q3); Q2/Q3 = YTD - prior YTD when
    no 3-month value was filed (cash flow). Never for EPS (not additive). accepted_at is the latest
    input's acceptance time.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from receipts.edgar import Edgar, EdgarError, filing_url, parse_accepted
from receipts.store import (DB_PATH, FACT_UNIT, METRIC_CONCEPTS, company_aliases, connect_rw, iso_utc,
                            replace_company)
from receipts.types import Fact

DEFAULT_TICKERS = "NVDA,AAPL,MSFT,META,AMZN,GOOGL,JPM,BAC,AMD,NFLX"
XBRL_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
ANCHOR_FORMS = {"10-K", "10-Q"}
STORED_FORMS = XBRL_FORMS | {"8-K", "8-K/A"}
YOY_QUARTERS = 4      # extra history so the oldest quarter still has its year-ago comparison
INPUT_MARGIN_DAYS = 120


# --------------------------------------------------------------------------- data shapes
@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filed: date
    accepted_at: datetime
    report_date: Optional[date]
    items: str
    primary_doc: str
    url: str


@dataclass
class Obs:
    """One companyfacts value mapped to a canonical metric."""
    metric: str
    concept: str
    priority: int
    start: Optional[date]
    end: date
    value: float
    filing: Filing
    span: str                    # 3m 6m 9m 12m instant other
    kind: str = "other"          # quarter annual ytd ttm instant other
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None


@dataclass(frozen=True)
class Period:
    fiscal_year: int
    fiscal_period: str
    start: date
    end: date
    source: str


@dataclass
class Derived:
    metric: str
    concept: str
    start: date
    end: date
    value: float
    fiscal_year: int
    fiscal_period: str
    inputs: list[Obs]
    formula: str


@dataclass
class CompanyBuild:
    company: dict
    filings: list[dict]
    periods: list[tuple[int, str, date, date, str]]
    facts: list[tuple[Fact, str]]
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers
def _span(start: Optional[date], end: date) -> str:
    if start is None:
        return "instant"
    days = (end - start).days
    if 80 <= days <= 100:
        return "3m"
    if 170 <= days <= 190:
        return "6m"
    if 260 <= days <= 285:
        return "9m"
    if 350 <= days <= 380:
        return "12m"
    return "other"


def _acc(o: Obs) -> datetime:
    return o.filing.accepted_at


def _ref(o: Obs) -> str:
    return f"{o.filing.form} {o.filing.accession}"


def _near(d: date, table: dict[date, Period], tol: int = 3) -> Optional[Period]:
    if d in table:
        return table[d]
    best = min(table, key=lambda k: abs((k - d).days), default=None)
    return table[best] if best is not None and abs((best - d).days) <= tol else None


def _is_fiscal_year_end(d: date, mmdd: Optional[str]) -> bool:
    if not mmdd or len(mmdd) != 4:
        return False
    for year in (d.year - 1, d.year, d.year + 1):
        try:
            if abs((date(year, int(mmdd[:2]), int(mmdd[2:])) - d).days) <= 10:
                return True
        except ValueError:
            continue
    return False


def _latest(cands: list[Obs], at: datetime, concept: str) -> Optional[Obs]:
    """Latest version accepted at or before `at`, preferring the same concept."""
    ok = [o for o in cands if _acc(o) <= at]
    same = [o for o in ok if o.concept == concept]
    pool = same or ok
    return max(pool, key=lambda o: (_acc(o), -o.priority)) if pool else None


def _trailing_zeros(v: float) -> int:
    n, z = abs(int(round(v))), 0
    while n and n % 10 == 0:
        n //= 10
        z += 1
    return z


def _places(v: float) -> int:
    s = f"{abs(v):.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


# --------------------------------------------------------------------------- build steps
def parse_filings(cik: int, rows: list[dict]) -> dict[str, Filing]:
    out = {}
    for r in rows:
        if not r.get("acceptanceDateTime"):
            continue
        out[r["accessionNumber"]] = Filing(
            accession=r["accessionNumber"], form=r["form"], filed=date.fromisoformat(r["filingDate"]),
            accepted_at=parse_accepted(r["acceptanceDateTime"]),
            report_date=date.fromisoformat(r["reportDate"]) if r.get("reportDate") else None,
            items=r.get("items") or "", primary_doc=r.get("primaryDocument") or "",
            url=filing_url(cik, r["accessionNumber"], r.get("primaryDocument")))
    return out


def collect_observations(usgaap: dict, filings: dict[str, Filing], stats: dict,
                         since: Optional[date] = None) -> tuple[list[Obs], dict]:
    """companyfacts entries for every mapped concept, joined to their filing, plus DEI labels per filing.

    Entries filed before `since` (the start of the loaded filing window) are skipped.
    """
    obs: list[Obs] = []
    dei: dict[str, Counter] = defaultdict(Counter)
    excluded: Counter = Counter()
    unjoined: set[str] = set()
    floor = since.isoformat() if since else ""
    for metric, spec in METRIC_CONCEPTS.items():
        for priority, concept in enumerate(spec.concepts):
            for e in usgaap.get(concept, {}).get("units", {}).get(spec.xbrl_unit, []):
                if e.get("filed", "") < floor:
                    continue
                filing = filings.get(e["accn"])
                form = filing.form if filing else e.get("form", "")
                if form not in XBRL_FORMS:
                    excluded[form] += 1
                    continue
                if filing is None:
                    unjoined.add(e["accn"])
                    continue
                start = date.fromisoformat(e["start"]) if e.get("start") else None
                end = date.fromisoformat(e["end"])
                span = _span(start, end)
                if (span == "instant") != spec.instant:
                    continue
                dei[filing.accession][(e.get("fy"), e.get("fp"))] += 1
                obs.append(Obs(metric, concept, priority, start, end, float(e["val"]), filing, span))
    stats["excluded_forms"] = dict(excluded)
    stats["unjoined_accessions"] = sorted(unjoined)
    return obs, dei


def build_calendar(obs: list[Obs], filings: dict[str, Filing], dei: dict[str, Counter],
                   warnings: list[str]) -> dict[tuple[int, str], Period]:
    """Fiscal periods from each 10-Q/10-K's own current period, labelled by its DEI fy/fp."""
    spans: dict[str, Counter] = defaultdict(Counter)
    for o in obs:
        f = o.filing
        if f.form not in ANCHOR_FORMS or o.start is None:
            continue
        if o.span != ("12m" if f.form == "10-K" else "3m"):
            continue
        if f.report_date and abs((o.end - f.report_date).days) > 3:
            continue
        spans[f.accession][(o.start, o.end)] += 1
    cal: dict[tuple[int, str], Period] = {}
    for accession, counter in sorted(spans.items(), key=lambda kv: filings[kv[0]].accepted_at):
        f = filings[accession]
        if f.report_date:
            (start, end), _ = counter.most_common(1)[0]
        else:
            start, end = max(counter, key=lambda se: (se[1], counter[se]))
        labels = dei.get(accession)
        if not labels:
            continue
        (fy, fp), _ = labels.most_common(1)[0]
        fp = "FY" if f.form == "10-K" else fp
        if not isinstance(fy, int) or fp not in ("Q1", "Q2", "Q3", "FY") or not end.year - 1 <= fy <= end.year + 1:
            warnings.append(f"calendar: ignored DEI label fy={fy} fp={fp} for {f.form} {accession} ending {end}")
            continue
        prev = cal.get((fy, fp))
        if prev is not None:
            if (prev.start, prev.end) != (start, end):
                warnings.append(f"calendar: {fp} FY{fy} is {prev.start}..{prev.end} in {prev.source} "
                                f"but {start}..{end} in {accession}; kept the earlier filing")
            continue  # filings are visited oldest first: keep the original
        cal[(fy, fp)] = Period(fy, fp, start, end, accession)
    for (fy, fp), p in list(cal.items()):
        if fp == "FY" and (fy, "Q3") in cal:
            q3 = cal[(fy, "Q3")]
            cal[(fy, "Q4")] = Period(fy, "Q4", q3.end + timedelta(days=1), p.end, "Q3+FY")
    for (fy, fp), p in cal.items():  # sanity: each quarter sits inside its fiscal year, in order
        year = cal.get((fy, "FY"))
        start = year.start if year else (cal[(fy, "Q1")].start if (fy, "Q1") in cal else None)
        if fp.startswith("Q") and start is not None:
            n = round((p.end - start).days / 91.3)
            if n != int(fp[1]) or (year and not (year.start <= p.start and p.end <= year.end)):
                warnings.append(f"calendar: {fp} FY{fy} {p.start}..{p.end} does not fit fiscal year start {start}")
    return cal


def classify(obs: list[Obs], cal: dict[tuple[int, str], Period], fiscal_year_end: Optional[str]) -> None:
    """Set kind (quarter/annual/ytd/ttm/instant/other) and the fiscal label of every observation."""
    quarters = {p.end: p for p in cal.values() if p.fiscal_period.startswith("Q")}
    years = {p.end: p for p in cal.values() if p.fiscal_period == "FY"}

    def year_start(fy: int) -> Optional[date]:
        if (fy, "FY") in cal:
            return cal[(fy, "FY")].start
        return cal[(fy, "Q1")].start if (fy, "Q1") in cal else None

    for o in obs:
        if o.span == "3m":
            o.kind = "quarter"
            p = _near(o.end, quarters)
            if p:
                o.fiscal_year, o.fiscal_period = p.fiscal_year, p.fiscal_period
        elif o.span in ("6m", "9m"):
            p = _near(o.end, quarters)
            want = "Q2" if o.span == "6m" else "Q3"
            start = year_start(p.fiscal_year) if p else None
            if p and p.fiscal_period == want and start and abs((o.start - start).days) <= 3:
                o.kind, o.fiscal_year, o.fiscal_period = "ytd", p.fiscal_year, ("H1" if o.span == "6m" else "9M")
        elif o.span == "12m":
            p = _near(o.end, years)
            if p:
                o.kind, o.fiscal_year, o.fiscal_period = "annual", p.fiscal_year, "FY"
            elif _is_fiscal_year_end(o.end, fiscal_year_end):
                o.kind = "annual"  # a fiscal year older than the calendar
            else:
                o.kind = "ttm"     # AMZN tags trailing-twelve-month cash flow in its 10-Qs
                q = _near(o.end, quarters)
                if q:
                    o.fiscal_year, o.fiscal_period = q.fiscal_year, "TTM"
        elif o.span == "instant":
            o.kind = "instant"
            p = _near(o.end, years) or _near(o.end, quarters)
            if p:
                o.fiscal_year, o.fiscal_period = p.fiscal_year, p.fiscal_period


def select_concepts(obs: list[Obs]) -> list[Obs]:
    """Per metric, filing and period keep the highest-priority concept the filing used."""
    best: dict[tuple, Obs] = {}
    for o in obs:
        if o.kind == "other":
            continue
        key = (o.metric, o.filing.accession, o.start, o.end)
        if key not in best or o.priority < best[key].priority:
            best[key] = o
    return sorted(best.values(), key=lambda o: (o.metric, o.end, o.start or date.min, _acc(o)))


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def derive(selected: list[Obs], cal: dict[tuple[int, str], Period]) -> list[Derived]:
    """Q4 = FY - 9M YTD (or FY - Q1 - Q2 - Q3) and Q2/Q3 = YTD - prior YTD, one row per distinct value.

    Each version of the total (the FY or the current YTD, original then comparatives) is paired with the
    latest component accepted at or before it. A later version is derived only if the total and its
    components were restated together. BAC restated FY2024 revenue in its FY2025 10-K, but no filing
    restated 9M 2024, so "restated FY - original 9M" would invent a Q4. NVDA's split-adjusted dividends
    restate both, so they stay.
    """
    by_metric: dict[str, list[Obs]] = defaultdict(list)
    for o in selected:
        by_metric[o.metric].append(o)
    out: list[Derived] = []
    for metric, spec in METRIC_CONCEPTS.items():
        if not spec.additive or metric not in by_metric:
            continue
        rows = by_metric[metric]

        def pick(kind: str, fy: int, fp: str) -> list[Obs]:
            return [o for o in rows if o.kind == kind and o.fiscal_year == fy and o.fiscal_period == fp]

        for (fy, fp), period in sorted(cal.items(), key=lambda kv: (kv[1].end, kv[0][1])):
            if fp not in ("Q2", "Q3", "Q4"):
                continue
            direct = pick("quarter", fy, fp)
            if fp == "Q4":
                if (fy, "FY") not in cal:
                    continue
                current = pick("annual", fy, "FY")
            else:
                current = pick("ytd", fy, "H1" if fp == "Q2" else "9M")
            emitted: list[float] = []
            baseline: Optional[tuple[float, list[float]]] = None  # (total, components) of the first derivation
            for v in sorted(current, key=_acc):
                if any(_acc(d) <= _acc(v) for d in direct):
                    continue  # a 3-month value was already filed: nothing to derive
                if fp == "Q4":
                    nine = _latest(pick("ytd", fy, "9M"), _acc(v), v.concept)
                    if nine is not None:
                        inputs, value = [v, nine], v.value - nine.value
                        formula = f"Q4 = FY ({_ref(v)}) - 9M YTD ({_ref(nine)})"
                    else:
                        qs = [_latest(pick("quarter", fy, f"Q{n}"), _acc(v), v.concept) for n in (1, 2, 3)]
                        if any(q is None for q in qs):
                            continue
                        inputs, value = [v, *qs], v.value - sum(q.value for q in qs)
                        formula = f"Q4 = FY ({_ref(v)}) - " + " - ".join(
                            f"Q{n} ({_ref(q)})" for n, q in zip((1, 2, 3), qs))
                else:
                    prior = (pick("quarter", fy, "Q1") if fp == "Q2" else pick("ytd", fy, "H1"))
                    p = _latest(prior, _acc(v), v.concept)
                    if p is None:
                        continue
                    inputs, value = [v, p], v.value - p.value
                    formula = (f"{fp} = {'H1' if fp == 'Q2' else '9M'} YTD ({_ref(v)}) - "
                               f"{'Q1' if fp == 'Q2' else 'H1 YTD'} ({_ref(p)})")
                parts = [o.value for o in inputs[1:]]
                if baseline is None:
                    baseline = (v.value, parts)
                else:
                    total_restated = not _same(v.value, baseline[0])
                    parts_restated = len(parts) != len(baseline[1]) or any(
                        not _same(a, b) for a, b in zip(parts, baseline[1]))
                    if total_restated and not parts_restated:
                        continue  # restated total with stale components: no consistent derivation
                value = round(value) if spec.xbrl_unit == "USD" else round(value, 6)
                if any(_same(value, e) for e in emitted):
                    continue  # same number as an earlier version: not a restatement
                emitted.append(value)
                out.append(Derived(metric, v.concept, period.start, period.end, value, fy, fp, inputs, formula))
    return out


def infer_decimals(selected: list[Obs]) -> dict[tuple[str, str], int]:
    """companyfacts omits XBRL `decimals`; infer each filing's precision from its values.

    USD: -(fewest trailing zeros among the filing's values, capped at 6), e.g. -6 for $ millions, -3 for
    NFLX's $ thousands. Per-share: the most decimal places used, usually 2.
    """
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for o in selected:
        if o.value:
            values[(o.filing.accession, METRIC_CONCEPTS[o.metric].xbrl_unit)].append(o.value)
    out = {}
    for (accession, unit), vals in values.items():
        out[(accession, unit)] = (-min(6, min(_trailing_zeros(v) for v in vals)) if unit == "USD"
                                  else max(_places(v) for v in vals))
    return out


def build_company(*, cik: int, ticker: str, names: list[str], fiscal_year_end: Optional[str],
                  filing_rows: list[dict], companyfacts: dict, facts_start: date,
                  filings_start: Optional[date] = None, today: Optional[date] = None,
                  last_n: int = 8) -> CompanyBuild:
    """Pure transformation from Submissions rows and companyfacts to rows for the store."""
    today = today or date.today()
    ticker = ticker.upper()
    warnings: list[str] = []
    stats: dict = {}
    filings = parse_filings(cik, filing_rows)
    if filings_start is None and filings:
        filings_start = min(f.filed for f in filings.values())
    usgaap = companyfacts.get("facts", {}).get("us-gaap", {})
    obs, dei = collect_observations(usgaap, filings, stats, since=filings_start)
    cal = build_calendar(obs, filings, dei, warnings)
    classify(obs, cal, fiscal_year_end)
    selected = select_concepts(obs)
    derived = derive(selected, cal)
    decimals = infer_decimals(selected)

    facts: list[tuple[Fact, str]] = []
    for o in selected:
        if o.end < facts_start:
            continue
        unit = METRIC_CONCEPTS[o.metric].xbrl_unit
        facts.append((Fact(
            cik=cik, ticker=ticker, metric=o.metric, concept=f"us-gaap:{o.concept}", basis="GAAP",
            value=o.value, unit=FACT_UNIT[unit], period_start=o.start, period_end=o.end,
            fiscal_year=o.fiscal_year, fiscal_period=o.fiscal_period, source="xbrl", form=o.filing.form,
            accession=o.filing.accession, accepted_at=o.filing.accepted_at, url=o.filing.url,
            decimals=decimals.get((o.filing.accession, unit))), o.kind))
    for d in derived:
        if d.end < facts_start:
            continue
        unit = METRIC_CONCEPTS[d.metric].xbrl_unit
        last = max(d.inputs, key=_acc)
        accessions = list(dict.fromkeys(o.filing.accession for o in d.inputs))
        precisions = [decimals[(a, unit)] for a in accessions if (a, unit) in decimals]
        facts.append((Fact(
            cik=cik, ticker=ticker, metric=d.metric, concept=f"us-gaap:{d.concept}", basis="GAAP",
            value=d.value, unit=FACT_UNIT[unit], period_start=d.start, period_end=d.end,
            fiscal_year=d.fiscal_year, fiscal_period=d.fiscal_period, source="derived", derived_from=accessions,
            form=last.filing.form, accession=last.filing.accession, accepted_at=last.filing.accepted_at,
            url=last.filing.url, locator=d.formula, decimals=min(precisions) if precisions else None), "quarter"))

    stored = [f for f in filings.values() if f.form in STORED_FORMS]
    company = {"cik": cik, "ticker": ticker, "name": next((n for n in names if n), ticker),
               "aliases": company_aliases(ticker, *names), "fiscal_year_end": fiscal_year_end}
    filing_rows_out = [{"accession": f.accession, "form": f.form, "filed": f.filed.isoformat(),
                        "accepted_at": iso_utc(f.accepted_at),
                        "report_date": f.report_date.isoformat() if f.report_date else None,
                        "items": f.items, "primary_doc": f.primary_doc, "url": f.url} for f in stored]
    periods = [(p.fiscal_year, p.fiscal_period, p.start, p.end, p.source)
               for p in sorted(cal.values(), key=lambda p: (p.end, p.fiscal_period == "FY"))]
    build = CompanyBuild(company, filing_rows_out, periods, facts, warnings, stats)
    build.stats.update(_stats(build, cal, stored, today, last_n, facts_start))
    return build


def _stats(build: CompanyBuild, cal: dict, stored: list[Filing], today: date, last_n: int,
           facts_start: date) -> dict:
    forms = Counter(f.form for f in stored)
    quarters = sorted((p for p in cal.values() if p.fiscal_period.startswith("Q") and p.end >= facts_start
                       and p.end <= today), key=lambda p: p.end)
    lastn = quarters[-last_n:]
    served = [(f, k) for f, k in build.facts if k in ("quarter", "annual", "instant")]
    per_metric = {}
    for metric in METRIC_CONCEPTS:
        rows = [f for f, _ in served if f.metric == metric]
        have = {(f.fiscal_year, f.fiscal_period) for f, k in served
                if f.metric == metric and k in ("quarter", "instant") and f.fiscal_period}
        have |= {(p.fiscal_year, p.fiscal_period) for p in quarters
                 if any(f.metric == metric and f.period_start is None and f.period_end == p.end for f, _ in served)}
        per_metric[metric] = {"rows": len(rows), "quarters": len({q for q in have if q[1].startswith("Q")}),
                              "lastn": sum(1 for p in lastn if (p.fiscal_year, p.fiscal_period) in have)}
    derived = [f for f, _ in build.facts if f.source == "derived"]
    return {
        "forms": dict(forms), "earnings_8k": sum(1 for f in stored if f.form == "8-K" and "2.02" in f.items),
        "quarters": quarters, "lastn": lastn, "per_metric": per_metric,
        "xbrl_rows": sum(1 for f, _ in build.facts if f.source == "xbrl"),
        "derived_q4": sorted({f.fiscal_year for f in derived if f.fiscal_period == "Q4"}),
        "derived_q4_rows": sum(1 for f in derived if f.fiscal_period == "Q4"),
        "derived_ytd_rows": sum(1 for f in derived if f.fiscal_period in ("Q2", "Q3")),
    }


# --------------------------------------------------------------------------- CLI
SHORT = {"revenue": "rev", "cost_of_revenue": "cor", "gross_profit": "gp", "research_and_development": "r&d",
         "operating_expenses": "opex", "operating_income": "opinc", "net_income": "ni", "eps_basic": "epsB",
         "eps_diluted": "epsD", "operating_cash_flow": "ocf", "net_interest_income": "nii",
         "dividend_per_share": "dps", "repurchase_authorization_remaining": "buyb"}


def _q(p: Period) -> str:
    return f"{p.fiscal_period} FY{p.fiscal_year}"


def print_company(build: CompanyBuild) -> None:
    c, s = build.company, build.stats
    forms = s["forms"]
    print(f"\n{c['ticker']}  {c['name']}  (CIK {c['cik']}, fiscal year ends {c['fiscal_year_end']})")
    amend = sum(v for k, v in forms.items() if k.endswith("/A"))
    print(f"  filings   10-K {forms.get('10-K', 0)}, 10-Q {forms.get('10-Q', 0)}, 8-K {forms.get('8-K', 0)} "
          f"(Item 2.02: {s['earnings_8k']}), amendments {amend}")
    q, ln = s["quarters"], s["lastn"]
    if q:
        print(f"  quarters  {len(q)} covered: {_q(q[0])} .. {_q(q[-1])}; last {len(ln)}: {_q(ln[0])} .. {_q(ln[-1])}")
    fy = ", ".join(f"FY{y}" for y in s["derived_q4"]) or "none"
    print(f"  derived   Q4 = FY - 9M: {s['derived_q4_rows']} facts ({fy}); Q2/Q3 from YTD: {s['derived_ytd_rows']}")
    print(f"  facts     {s['xbrl_rows']} xbrl + {s['derived_q4_rows'] + s['derived_ytd_rows']} derived rows; "
          "per metric: rows / quarters / last-N coverage")
    cells = [f"{m} {v['rows']}/{v['quarters']}/{v['lastn']}" for m, v in s["per_metric"].items() if v["rows"]]
    missing = [m for m, v in s["per_metric"].items() if not v["rows"]]
    for i in range(0, len(cells), 4):
        print("            " + "   ".join(cells[i:i + 4]))
    if missing:
        print(f"  no XBRL   {', '.join(missing)}")
    if s["excluded_forms"]:
        parts = [f"{form or '?'} x{n}" for form, n in sorted(s["excluded_forms"].items())]
        print(f"  excluded  XBRL facts from other forms in the window: {', '.join(parts)}")
    if s["unjoined_accessions"]:
        print(f"  UNJOINED  {len(s['unjoined_accessions'])} 10-K/10-Q accessions in the window have no Submissions "
              f"row (their facts are dropped): {', '.join(s['unjoined_accessions'][:5])}")
    for w in build.warnings:
        print(f"  warning   {w}")


def print_summary(builds: list[CompanyBuild], last_n: int) -> None:
    metrics = list(METRIC_CONCEPTS)
    head = f"{'ticker':<6} {'filings':>7} {'facts':>6} {'Q4s':>4} {'qtrs':>4}  " + " ".join(f"{SHORT[m]:>5}" for m in metrics)
    print(f"\nSummary: last-{last_n}-quarter coverage per metric (quarters with a value; '-' = no XBRL concept)")
    print(head)
    for b in builds:
        s = b.stats
        cells = []
        for m in metrics:
            v = s["per_metric"][m]
            cells.append(f"{v['lastn']}/{len(s['lastn'])}" if v["rows"] else "-")
        print(f"{b.company['ticker']:<6} {len(b.filings):>7} {len(b.facts):>6} {s['derived_q4_rows']:>4} "
              f"{len(s['quarters']):>4}  " + " ".join(f"{c:>5}" for c in cells))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build data/facts.sqlite from SEC EDGAR (cache-backed, idempotent).")
    ap.add_argument("--tickers", default=DEFAULT_TICKERS, help="comma-separated tickers")
    ap.add_argument("--quarters", type=int, default=8, help="recent quarters to cover (plus one year for YoY)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--refresh", action="store_true",
                    help="refetch Submissions and companyfacts cached more than an hour ago")
    ap.add_argument("--offline", action="store_true", help="never call SEC; fail on a cache miss")
    ap.add_argument("--max-requests", type=int, default=250, help="live SEC request budget for this run")
    args = ap.parse_args(argv)

    today = date.today()
    facts_start = today - timedelta(days=int((args.quarters + YOY_QUARTERS) * 91.5) + 45)
    filings_start = facts_start - timedelta(days=INPUT_MARGIN_DAYS)
    max_age = 3600.0 if args.refresh else None
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    print(f"Receipts ingest: {len(tickers)} companies, last {args.quarters} quarters + {YOY_QUARTERS} for YoY; "
          f"facts ending since {facts_start}, filings since {filings_start} -> {args.db}")

    edgar = Edgar(offline=args.offline, max_live_requests=args.max_requests)
    conn = connect_rw(args.db)
    builds, failed = [], []
    for ticker in tickers:
        try:
            cik, title = edgar.ticker_to_cik(ticker)
            meta, rows = edgar.submissions(cik, since=filings_start, max_age=max_age)
            cf = edgar.companyfacts(cik, max_age=max_age)
            build = build_company(cik=cik, ticker=ticker, names=[meta.get("name") or title, title],
                                  fiscal_year_end=meta.get("fiscalYearEnd"), filing_rows=rows, companyfacts=cf,
                                  facts_start=facts_start, filings_start=filings_start, today=today,
                                  last_n=args.quarters)
        except EdgarError as e:
            print(f"\n{ticker}  FAILED: {e}")
            failed.append(ticker)
            continue
        replace_company(conn, build.company, build.filings, build.periods, build.facts)
        builds.append(build)
        print_company(build)
    with conn:
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [
            ("built_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("facts_start", facts_start.isoformat()), ("filings_start", filings_start.isoformat()),
            ("quarters", str(args.quarters))])
    conn.execute("VACUUM")
    conn.close()
    edgar.close()
    print_summary(builds, args.quarters)
    print(f"\nEDGAR: {edgar.live_requests} live requests, {edgar.cache_hits} cache hits. "
          f"{'Failed: ' + ', '.join(failed) if failed else 'All companies ingested.'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
