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
  * Capital expenditures (2026-11-02) are derived as operating cash flow is, plus the never-negative tests revenue's
    quarters get (NON_NEGATIVE_METRICS). A concept in SOLE_LINE_CONCEPTS serves only at a company that tags none of the
    metric's other concepts in the window, and ISOLATED_METRICS never move another metric's rows: they label no fiscal
    period and change no filing's inferred precision.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from receipts import filing_xbrl
from receipts.edgar import Edgar, EdgarError, filing_url, parse_accepted
from receipts.fiscal_labels import FilingPeriod, label_periods
from receipts.release_gates import MAX_Q4_SHARE, MIN_Q4_SHARE, noncontrolling_interest
from receipts.store import (DB_PATH, FACT_UNIT, METRIC_CONCEPTS, company_aliases, connect_rw, iso_utc,
                            replace_company)
from receipts.types import Fact

DEFAULT_TICKERS = ("NVDA,AAPL,MSFT,META,AMZN,GOOGL,JPM,BAC,AMD,NFLX,TSLA,"
                   "AVGO,ORCL,CRM,ADBE,INTC,QCOM,CSCO,IBM,PLTR,UBER,WMT,COST,DIS,NKE,MCD,KO,LLY,UNH,JNJ,GS,WFC")
XBRL_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
ANCHOR_FORMS = {"10-K", "10-Q"}
STORED_FORMS = XBRL_FORMS | {"8-K", "8-K/A"}
YOY_QUARTERS = 4      # extra history so the oldest quarter still has its year-ago comparison
AUDIT_DIR = DB_PATH.parent / "ingest_audit"  # write_audit(): per-company calendar and revenue choices
INPUT_MARGIN_DAYS = 120
# ProfitLoss is net income including noncontrolling interests. It equals NetIncomeLoss only where there is no
# noncontrolling interest, so it stands in for net income only at a company whose filings show none, or in a period
# whose whole NCI is tagged as zero and no filing shows one (AVGO tags nothing else; review fix 15). Where a filing
# tags ProfitLoss and the whole NCI for one period but no NetIncomeLoss, net income is their difference, derived.
CONSOLIDATED_NET_INCOME = "ProfitLoss"
REVENUE_COMPONENT_CONCEPTS = frozenset({"RevenueFromContractWithCustomerExcludingAssessedTax",
                                        "RevenueFromContractWithCustomerIncludingAssessedTax"})
# Total-revenue concepts a contract-revenue figure must not undercut, and lessor income it never includes.
OTHER_REVENUE_CONCEPTS = frozenset({"Revenues", "SalesRevenueNet", "RevenuesNetOfInterestExpense"})
# Totals some utilities and REITs tag instead: compared with contract revenue, never served themselves.
COMPARISON_REVENUE_CONCEPTS = ("RegulatedAndUnregulatedOperatingRevenue", "RealEstateRevenueNet")
LEASE_INCOME_CONCEPTS = ("OperatingLeaseLeaseIncome", "OperatingLeasesIncomeStatementLeaseRevenue")
# A contract-revenue figure flagged for review (SIC sector or lease share) is served only once it is confirmed
# against the company's press-release totals (scripts/confirm_revenue.py writes this file).
CONFIRMATIONS = DB_PATH.parent / "revenue_confirmations.json"
# Derived quarters the company's own release outranks (scripts/gate_release_facts.py, review fix 7)
DERIVED_REJECTIONS = DB_PATH.parent / "derived_rejections.json"
# A company whose registrant changed keeps one row under its current CIK, holding each earlier registrant's filings and
# companyfacts as well (current CIK -> earlier CIKs). Exxon Mobil Corp (34088) filed every XOM report until the
# holding-company reorganization of 2026-07-01 (8-K12B 0001193125-26-291990) made ExxonMobil Holdings Corp (2115436)
# the registrant; the Q2 2026 10-Q is listed under both. Each filing's URL is in the folder of a registrant whose
# Submissions list it, the current one first.
PREDECESSOR_CIKS: dict[int, tuple[int, ...]] = {2115436: (34088,)}
# A company whose own releases report net income after preferred dividends (scripts/confirm_revenue.py decides it, from
# at least two periods where the figures differ) serves its available-to-common figure as net income, over a
# NetIncomeLoss the same filing tags, and ProfitLoss never stands in for it: Invesco's "Net income/(loss)
# attributable to Invesco Ltd." is -$12.5M for Q2 2025, its NetIncomeLossAvailableToCommonStockholdersBasic, where
# ProfitLoss less the noncontrolling interest is $203.4M.
AVAILABLE_TO_COMMON = "NetIncomeLossAvailableToCommonStockholdersBasic"
# A bank that tags no total revenue (Revenues, RevenuesNetOfInterestExpense) serves net interest income plus
# noninterest income, both tagged by one filing for one period, once its own releases show that sum is the total
# revenue they report (scripts/confirm_revenue.py): Regions' "Total revenue" of $1,905M for Q2 2025.
NET_INTEREST_INCOME, NONINTEREST_INCOME = "InterestIncomeExpenseNet", "NoninterestIncome"
BANK_REVENUE = "RevenuesNetOfInterestExpense"
AWAITING = "flagged for review: not served until confirmed against the press-release totals"
ROUNDING = 0.005  # two tags of one amount can differ by rounding; a larger total differs by far more
NCI_CONCEPTS = ("NetIncomeLossAttributableToNoncontrollingInterest",
                "NetIncomeLossAttributableToRedeemableNoncontrollingInterest",
                "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest")
# Metrics no quarter of which is ever negative: a derived quarter below zero or above its total proves its inputs
# were reported on different bases or concepts, and a reported quarter above the year's 9M YTD is a full year.
# Capital expenditures are payments, never negative: Honeywell's 9M 2026 of $369M, filed after a spin-off, less the
# H1 of $538M filed before it gave a Q3 of -$169M, and 17 more derived capex quarters (of 3,660) were negative or
# outside 10-75% of their year on 2026-11-02 (operating cash flow, which can be negative, has no such test).
NON_NEGATIVE_METRICS = frozenset({"revenue", "cost_of_revenue", "research_and_development", "operating_expenses",
                                  "capital_expenditures"})
# Metrics XBRL adds up but whose differences are not the quarter's figure. A per-share dividend tagged for a period is
# what the board declared during it: Eli Lilly declares two dividends in its second and fourth quarters and none in
# its first and third, so FY - 9M was $3.23 for Q4 2025 (its $1.50 and the next $1.73) and 9M - H1 was $0 for Q3; CME's
# fourth quarter carries its annual variable dividend, and a split inside the year leaves FY and 9M on two share counts.
# 137 of the 833 dividends ingest derived across 499 companies were not the company's quarterly rate (review fix 10),
# so a dividend is served only as filed.
NOT_DERIVED_METRICS = frozenset({"dividend_per_share"})
# Per-share values no S&P 500 company reports: a tag scaled a millionfold (Halliburton's Q3 2023 diluted EPS of
# 790,000, Mosaic's $220,000 dividend) is never served (review fix 13).
PER_SHARE_LIMITS = {"eps_basic": 1500.0, "eps_diluted": 1500.0, "dividend_per_share": 60.0}
DIVIDEND_ROUNDING = 0.0051  # dividends are filed to the cent or the hundredth of a cent
# Concepts a metric serves only at a company that tags none of the metric's other concepts in a 10-Q or 10-K of the
# window: there they are the company's sole line for it. Amazon's "Purchases of property and equipment" is
# PaymentsToAcquireProductiveAssets (it last tagged PaymentsToAcquirePropertyPlantAndEquipment in 2017). Of the 500
# companies on 2026-11-02, 314 tag only PaymentsToAcquirePropertyPlantAndEquipment in the window, 87 only
# PaymentsToAcquireProductiveAssets, 37 both and 62 neither. Where a company tags both, the two are often different lines
# (FIS tags $43M of the first beside $385M of the second for H1 2024, in one 10-Q), so the property-and-equipment line
# serves and the other never does; the two are never summed.
SOLE_LINE_CONCEPTS: dict[str, frozenset[str]] = {"capital_expenditures": frozenset({"PaymentsToAcquireProductiveAssets"})}
# Metrics added to a store whose other rows must not move (capital expenditures, 2026-11-02): their observations label
# no fiscal period (build_calendar), and each filing's precision for the other metrics is inferred without them
# (infer_decimals), as before they were added. Their own figures take the precision of every value the filing tags.
ISOLATED_METRICS = frozenset({"capital_expenditures"})


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
    formula: Optional[str] = None  # set for arithmetic on two facts of one filing and context: stored as derived
    note: Optional[str] = None     # the locator of a filed figure whose concept a receipt names in words: stays xbrl


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
    """Submissions rows -> Filing per accession; a row merge_filing_rows took from a predecessor registrant keeps
    that registrant's EDGAR folder in its URL."""
    out = {}
    for r in rows:
        if not r.get("acceptanceDateTime"):
            continue
        out[r["accessionNumber"]] = Filing(
            accession=r["accessionNumber"], form=r["form"], filed=date.fromisoformat(r["filingDate"]),
            accepted_at=parse_accepted(r["acceptanceDateTime"]),
            report_date=date.fromisoformat(r["reportDate"]) if r.get("reportDate") else None,
            items=r.get("items") or "", primary_doc=r.get("primaryDocument") or "",
            url=filing_url(r.get("filer_cik") or cik, r["accessionNumber"], r.get("primaryDocument")))
    return out


def merge_filing_rows(rows: list[dict], predecessor: int, earlier: list[dict]) -> list[dict]:
    """The company's own Submissions rows plus each filing only a predecessor registrant (PREDECESSOR_CIKS) lists,
    tagged with that registrant's CIK for its URL; newest first, as Edgar.submissions returns them."""
    have = {r["accessionNumber"] for r in rows}
    merged = rows + [{**r, "filer_cik": predecessor} for r in earlier if r["accessionNumber"] not in have]
    return sorted(merged, key=lambda r: r["acceptanceDateTime"] or r["filingDate"], reverse=True)


def _entry_key(e: dict) -> tuple:
    return tuple(sorted((k, json.dumps(v)) for k, v in e.items() if k != "frame"))


def merge_companyfacts(current: dict, *earlier: dict) -> dict:
    """One companyfacts across registrants (PREDECESSOR_CIKS): the current registrant's entries as SEC lists them,
    then each earlier registrant's that the merged facts do not hold yet (one filing's fact listed under two CIKs,
    whatever frame SEC gave it in each, counts once)."""
    out = {**current, "facts": {ns: {c: {**body, "units": {u: list(es) for u, es in body.get("units", {}).items()}}
                                     for c, body in concepts.items()}
                                for ns, concepts in current.get("facts", {}).items()}}
    for cf in earlier:
        for ns, concepts in cf.get("facts", {}).items():
            for concept, body in concepts.items():
                into = out["facts"].setdefault(ns, {}).setdefault(concept, {**body, "units": {}})
                for unit, entries in body.get("units", {}).items():
                    kept = into["units"].setdefault(unit, [])
                    seen = {_entry_key(e) for e in kept}
                    for e in entries:
                        k = _entry_key(e)
                        if k not in seen:
                            seen.add(k)
                            kept.append(e)
    return out


def fill_holes(cik: int, companyfacts: dict) -> dict:
    """The companyfacts with the facts of each filing of the company (or a predecessor) that they lack, read from the
    filing's own archived XBRL instance (receipts.filing_xbrl); unchanged when there is none."""
    holes = filing_xbrl.hole_filings((int(cik), *PREDECESSOR_CIKS.get(int(cik), ())),
                                     filing_xbrl.accessions(companyfacts))
    return merge_companyfacts(companyfacts, *holes) if holes else companyfacts


def with_predecessors(cik: int, load: Callable[[int], dict]) -> dict:
    """The companyfacts `load` reads for a company's CIK, with each predecessor registrant's merged in, and the
    filings they lack from the archived instances (fill_holes)."""
    earlier = PREDECESSOR_CIKS.get(int(cik), ())
    current = load(int(cik))
    return fill_holes(cik, merge_companyfacts(current, *(load(p) for p in earlier)) if earlier else current)


def company_inputs(edgar: Edgar, cik: int, *, since: Optional[date] = None,
                   max_age: Optional[float] = None) -> tuple[dict, list[dict], dict, list[str]]:
    """(Submissions metadata, filing rows, companyfacts, registrant names) of a company, the current registrant's
    first, each predecessor registrant's (PREDECESSOR_CIKS) merged in, and the filings companyfacts lack from their
    archived instances (fill_holes)."""
    meta, rows = edgar.submissions(cik, since=since, max_age=max_age)
    companyfacts = edgar.companyfacts(cik, max_age=max_age)
    names = [meta.get("name") or ""]
    for p in PREDECESSOR_CIKS.get(int(cik), ()):
        earlier_meta, earlier_rows = edgar.submissions(p, since=since, max_age=max_age)
        rows = merge_filing_rows(rows, p, earlier_rows)
        companyfacts = merge_companyfacts(companyfacts, edgar.companyfacts(p, max_age=max_age))
        names.append(earlier_meta.get("name") or "")
    return meta, rows, fill_holes(cik, companyfacts), names


def collect_observations(usgaap: dict, filings: dict[str, Filing], stats: dict,
                         since: Optional[date] = None, sic: Optional[str] = None,
                         contract_revenue_total: bool = False,
                         broad_revenue_total: Optional[str] = None,
                         available_to_common: Optional[str] = None,
                         bank_revenue: bool = False) -> tuple[list[Obs], dict]:
    """companyfacts entries for every mapped concept, joined to their filing, plus DEI labels per filing.

    Entries filed before `since` (the start of the loaded filing window) are skipped. `sic` (Submissions) decides
    whether operating-lease income rules out contract revenue (_drop_component_revenue). `available_to_common`, the
    release's own label for net income when the company reports it after preferred dividends (AVAILABLE_TO_COMMON),
    serves that figure as net income and keeps ProfitLoss out. `bank_revenue` serves net interest income plus
    noninterest income as revenue (BANK_REVENUE) where a filing tags both for a period and no broader revenue.
    """
    obs: list[Obs] = []
    dei: dict[str, Counter] = defaultdict(Counter)
    excluded: Counter = Counter()
    unjoined: set[str] = set()
    floor = since.isoformat() if since else ""
    def contexts(concept: str, zero: bool) -> set[tuple]:
        return {(e.get("accn"), e.get("start"), e.get("end")) for e in usgaap.get(concept, {}).get("units", {}).get(
            "USD", []) if (e.get("val") == 0 if zero else e.get("val"))}
    with_nci = set().union(*(contexts(c, False) for c in NCI_CONCEPTS))
    # Ares tags no NCI for Q3 2023, but its FY2023 ProfitLoss of $885.8M against NetIncomeLoss of $474.3M shows one,
    # so its Q3 ProfitLoss ($197.0M) is not its net income (review fix 15); the release gates use the same test. At
    # such a company ProfitLoss stands for net income only in a filing's period whose whole NCI is tagged as zero, and
    # only if no filing shows an NCI for that period: APA tags its redeemable NCI as zero for Q3 2023, while its next
    # 10-Q reports $96M of nonredeemable NCI for that quarter.
    company_nci = noncontrolling_interest(usgaap, floor)
    total, redeemable, nonredeemable = NCI_CONCEPTS
    shown = {k[1:] for k in with_nci}
    nci_zero = {k for k in contexts(total, True) | (contexts(redeemable, True) & contexts(nonredeemable, True))
                if k[1:] not in shown}
    nci_by_context: dict[tuple, dict[str, float]] = defaultdict(dict)
    for c in NCI_CONCEPTS:
        for e in usgaap.get(c, {}).get("units", {}).get("USD", []):
            if e.get("val") is not None:
                nci_by_context[(e.get("accn"), e.get("start"), e.get("end"))][c] = float(e["val"])
    attributable_tagged = {(e.get("accn"), e.get("start"), e.get("end"))
                           for e in usgaap.get("NetIncomeLoss", {}).get("units", {}).get("USD", [])}
    skipped_nci = derived_nci = 0
    for metric, spec in METRIC_CONCEPTS.items():
        for priority, concept in enumerate(spec.concepts):
            if available_to_common and concept == CONSOLIDATED_NET_INCOME:
                continue  # net income before preferred dividends: not the figure this company reports
            for e in usgaap.get(concept, {}).get("units", {}).get(spec.xbrl_unit, []):
                if e.get("filed", "") < floor:
                    continue
                formula, out_concept, out_priority = None, concept, priority
                if concept == CONSOLIDATED_NET_INCOME and (
                        (e.get("accn"), e.get("start"), e.get("end")) in with_nci
                        or (company_nci and (e.get("accn"), e.get("start"), e.get("end")) not in nci_zero)):
                    # Net income is ProfitLoss less the whole NCI the same filing tags for the same period (review
                    # follow-up); otherwise "?". A zero that reaches here is one another filing contradicts.
                    k = (e.get("accn"), e.get("start"), e.get("end"))
                    whole = _whole_nci(nci_by_context.get(k, {}))
                    if whole is None or not whole[0] or k in attributable_tagged:
                        skipped_nci += 1  # may include a noncontrolling interest: not the company's own net income
                        continue
                    filing = filings.get(e["accn"])
                    form = filing.form if filing else e.get("form", "")
                    formula = (f"NetIncomeLoss = ProfitLoss {float(e['val']):,.0f} - {whole[1]} {whole[0]:,.0f} "
                               f"({form} {e['accn']})")
                    e = {**e, "val": float(e["val"]) - whole[0]}
                    out_concept, out_priority = "NetIncomeLoss", 0
                    derived_nci += 1
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
                if metric not in ISOLATED_METRICS:  # a metric added later labels no fiscal period
                    dei[filing.accession][(e.get("fy"), e.get("fp"))] += 1
                obs.append(Obs(metric, out_concept, out_priority, start, end, float(e["val"]), filing, span,
                               formula=formula))
    for metric, sole in SOLE_LINE_CONCEPTS.items():
        # Only a company's sole line: where it tags another of the metric's concepts in the window, that one serves
        if any(o.metric == metric and o.concept not in sole for o in obs):
            obs = [o for o in obs if not (o.metric == metric and o.concept in sole)]
    if available_to_common:
        for e in usgaap.get(AVAILABLE_TO_COMMON, {}).get("units", {}).get("USD", []):
            filing = filings.get(e.get("accn"))
            if (e.get("filed", "") < floor or filing is None or filing.form not in XBRL_FORMS or not e.get("start")
                    or e.get("val") is None):
                continue
            start, end = date.fromisoformat(e["start"]), date.fromisoformat(e["end"])
            dei[filing.accession][(e.get("fy"), e.get("fp"))] += 1
            obs.append(Obs("net_income", AVAILABLE_TO_COMMON, -1, start, end, float(e["val"]), filing,
                           _span(start, end), note=f"{available_to_common} (after preferred dividends), as the "
                                                   f"company reports it: {AVAILABLE_TO_COMMON} ({filing.form} "
                                                   f"{filing.accession})"))
    if bank_revenue:
        broader = {(o.filing.accession, o.start, o.end) for o in obs
                   if o.metric == "revenue" and o.concept in OTHER_REVENUE_CONCEPTS}
        parts: dict[tuple, dict[str, float]] = defaultdict(dict)
        for concept in (NET_INTEREST_INCOME, NONINTEREST_INCOME):
            for e in usgaap.get(concept, {}).get("units", {}).get("USD", []):
                if e.get("val") is not None and e.get("start") and e.get("filed", "") >= floor:
                    parts[(e["accn"], e["start"], e["end"])][concept] = float(e["val"])
        for (accn, s0, e0), both in parts.items():
            filing = filings.get(accn)
            if len(both) < 2 or filing is None or filing.form not in XBRL_FORMS:
                continue
            start, end = date.fromisoformat(s0), date.fromisoformat(e0)
            if (accn, start, end) in broader:
                continue
            nii, other = both[NET_INTEREST_INCOME], both[NONINTEREST_INCOME]
            obs.append(Obs("revenue", BANK_REVENUE, 0, start, end, nii + other, filing, _span(start, end),
                           formula=f"{BANK_REVENUE} = {NET_INTEREST_INCOME} {nii:,.0f} + {NONINTEREST_INCOME} "
                                   f"{other:,.0f} ({filing.form} {accn})"))
    if broad_revenue_total:
        # A total some utilities and REITs tag instead of Revenues (COMPARISON_REVENUE_CONCEPTS), served where the
        # company's own releases confirm it is the total (scripts/confirm_revenue.py) and the filing tags no broader
        # revenue for the period: Duke's "Total operating revenues" is its RegulatedAndUnregulatedOperatingRevenue.
        broader = {(o.filing.accession, o.start, o.end) for o in obs
                   if o.metric == "revenue" and o.concept in OTHER_REVENUE_CONCEPTS}
        for e in usgaap.get(broad_revenue_total, {}).get("units", {}).get("USD", []):
            filing = filings.get(e.get("accn"))
            if (e.get("filed", "") < floor or filing is None or filing.form not in XBRL_FORMS or not e.get("start")
                    or e.get("val") is None):
                continue
            start, end = date.fromisoformat(e["start"]), date.fromisoformat(e["end"])
            if (filing.accession, start, end) in broader:
                continue
            dei[filing.accession][(e.get("fy"), e.get("fp"))] += 1
            obs.append(Obs("revenue", broad_revenue_total, 0, start, end, float(e["val"]), filing, _span(start, end)))
    stats["excluded_forms"] = dict(excluded)
    stats["unjoined_accessions"] = sorted(unjoined)
    stats["consolidated_net_income_skipped"] = skipped_nci
    stats["attributable_net_income_derived"] = derived_nci
    stats["noncontrolling_interest"] = company_nci
    def by_period(concepts) -> dict[tuple, tuple[float, str]]:
        out: dict[tuple, tuple[float, str]] = {}
        for concept in concepts:
            for e in usgaap.get(concept, {}).get("units", {}).get("USD", []):
                if e.get("val") is not None and e.get("start") and e.get("filed", "") >= floor:
                    k = (e.get("accn"), date.fromisoformat(e["start"]), date.fromisoformat(e["end"]))
                    if float(e["val"]) > out.get(k, (float("-inf"), ""))[0]:
                        out[k] = (float(e["val"]), concept)
        return out
    lease = {k: v for k, (v, _) in by_period(LEASE_INCOME_CONCEPTS).items()}
    broad: list[dict] = []
    if contract_revenue_total:
        obs, broad = _drop_broad_revenue(obs)
    obs, stats["component_revenue_dropped"], stats["lease_income_notes"] = _drop_component_revenue(
        obs, lease, real_estate=_is_real_estate(sic), bank=_is_bank(sic),
        totals=None if contract_revenue_total else by_period(COMPARISON_REVENUE_CONCEPTS))
    stats["component_revenue_dropped"] = broad + stats["component_revenue_dropped"]
    stats["component_revenue_skipped"] = len(stats["component_revenue_dropped"])
    return obs, dei


CONTRACT_TOTAL = "the company's releases report its contract revenue as the total (scripts/confirm_revenue.py)"


def _whole_nci(parts: dict[str, float]) -> Optional[tuple[float, str]]:
    """The noncontrolling interest one filing tags for one period, when it is the whole of it: the total concept alone,
    both the redeemable and the nonredeemable parts, or all three when the total is their sum. None otherwise (one
    part only, or a total beside parts it does not add up to), so net income stays "?"."""
    total, redeemable, nonredeemable = NCI_CONCEPTS
    t, r, n = parts.get(total), parts.get(redeemable), parts.get(nonredeemable)
    if t is not None and r is None and n is None:
        return t, total
    if t is None and r is not None and n is not None:
        return r + n, f"{redeemable} + {nonredeemable}"
    if t is not None and r is not None and n is not None and abs(t - (r + n)) < 1:
        return t, total
    return None


def _drop_broad_revenue(obs: list[Obs]) -> tuple[list[Obs], list[dict]]:
    """At a company whose own releases show contract revenue is its total, a broad revenue figure (Revenues,
    SalesRevenueNet, RevenuesNetOfInterestExpense) that the same filing's contract revenue for the period
    contradicts is not served. Hasbro's filings from 2025 tag a Revenues line 13-15% above its net revenues ($1,006.1M
    against $887.1M for Q1 2025), Republic Services' a gross revenue before intercompany eliminations ($18,418M
    against $16,032M for 2024)."""
    contract: dict[tuple, list[float]] = defaultdict(list)
    for o in obs:
        if o.metric == "revenue" and o.concept in REVENUE_COMPONENT_CONCEPTS:
            contract[(o.filing.accession, o.start, o.end)].append(o.value)
    kept, dropped = [], []
    for o in obs:
        values = contract.get((o.filing.accession, o.start, o.end)) if o.metric == "revenue" else None
        if (values and o.concept in OTHER_REVENUE_CONCEPTS
                and all(abs(o.value - v) > ROUNDING * abs(v) for v in values)):
            dropped.append({"accession": o.filing.accession, "start": o.start, "end": o.end, "concept": o.concept,
                            "value": o.value, "conditions": [CONTRACT_TOTAL]})
            continue
        kept.append(o)
    return kept, dropped


def _drop_component_revenue(obs: list[Obs], lease: dict[tuple, float], *, real_estate: bool = False,
                            bank: bool = False, totals: Optional[dict[tuple, tuple[float, str]]] = None
                            ) -> tuple[list[Obs], list[dict], list[dict]]:
    """Drop a RevenueFromContractWithCustomer figure that cannot be the filing's total revenue for its period.

    Banks, brokers and card issuers tag their in-scope fee income with it and REITs their non-lease income, while
    total revenue sits elsewhere or untagged; serving the slice would mark a correct total-revenue claim wrong. For
    one filing and period, the figure is dropped when any of these holds (review finding 2):
      * it is zero or negative: a REIT's $0 of non-lease income is not its revenue, and no ratio below is tested;
      * the filer is a bank (SIC 6020-6036), whose contract revenue is by definition its in-scope fee income only;
      * another revenue concept (Revenues, SalesRevenueNet, RevenuesNetOfInterestExpense, or for comparison only
        RegulatedAndUnregulatedOperatingRevenue and RealEstateRevenueNet, `totals`) is larger beyond rounding, which
        also lets a bank's RevenuesNetOfInterestExpense win over its fee income;
      * net interest income is at least 10% of it: a lender or broker, whose revenue is never its fee income;
      * for a real-estate filer (SIC 65xx or 6798, `real_estate`), operating-lease income is at least 1% of it: a
        REIT's contract revenue excludes its rents. Elsewhere lease income decides nothing, because a company such
        as Baxter tags its whole net sales, leases included, as contract revenue (Q3 2026: $2,976M, with $88M of
        lease income inside); a kept figure whose lease income is at least 10% of it gets a note for review;
      * it is under half the cost of revenue (Camden's 10-K: $13M of 2025 non-lease income, $567M of costs);
      * last, it is under a tenth of the operating expenses (Camden's quarters: about $1M against $140M), a bound a
        true total still clears in a quarter with a multi-billion IPR&D or impairment charge.
    Returns the kept observations, one record per dropped figure naming the conditions that fired, and one note
    per kept figure with that much lease income."""
    def key(o: Obs) -> tuple:
        return o.filing.accession, o.start, o.end
    biggest: dict[tuple[str, tuple], tuple[float, str]] = {}
    for o in obs:
        k = key(o)
        if o.metric == "revenue" and o.concept in OTHER_REVENUE_CONCEPTS:
            if o.value > biggest.get(("revenue", k), (float("-inf"), ""))[0]:
                biggest[("revenue", k)] = (o.value, o.concept)
        elif o.metric in ("net_interest_income", "cost_of_revenue", "operating_expenses"):
            if o.value > biggest.get((o.metric, k), (float("-inf"), ""))[0]:
                biggest[(o.metric, k)] = (o.value, o.concept)
    kept, dropped, notes = [], [], []
    for o in obs:
        if o.metric != "revenue" or o.concept not in REVENUE_COMPONENT_CONCEPTS:
            kept.append(o)
            continue
        k, v, fired = key(o), o.value, []
        rent = lease.get(k)  # operating-lease income tagged for the same filing and period; None when untagged
        if v <= 0:
            fired.append("not positive")
        if bank:
            fired.append("a bank's contract revenue is only its in-scope fee income")
        if v > 0:  # a ratio means nothing against a figure that is not positive (INVH's $0 for Q1 2025)
            for other in (biggest.get(("revenue", k)), (totals or {}).get(k)):
                if other and other[0] > v * (1 + ROUNDING) and other[0] > 0:
                    fired.append(f"{other[1]} {other[0]:,.0f} is larger")
            nii = biggest.get(("net_interest_income", k))
            if nii and nii[0] >= 0.10 * v:
                fired.append(f"net interest income {nii[0]:,.0f} is at least 10%")
            if real_estate and rent is not None and rent >= 0.01 * v:
                fired.append(f"operating-lease income {rent:,.0f} is at least 1%")
            cor = biggest.get(("cost_of_revenue", k))
            if cor and v < 0.5 * cor[0]:
                fired.append(f"under half of {cor[1]} {cor[0]:,.0f}")
            opex = biggest.get(("operating_expenses", k))
            if opex and v < 0.1 * opex[0]:
                fired.append(f"under a tenth of {opex[1]} {opex[0]:,.0f}")
        if fired:
            dropped.append({"accession": o.filing.accession, "start": o.start, "end": o.end, "concept": o.concept,
                            "value": v, "conditions": fired})
            continue
        kept.append(o)
        if not real_estate and rent is not None and rent >= 0.10 * v:
            notes.append({"accession": o.filing.accession, "start": o.start, "end": o.end, "concept": o.concept,
                          "value": v, "note": f"operating-lease income {rent:,.0f} is at least 10% of this "
                                              f"contract revenue: a lessor whose contract revenue may exclude leases"})
    return kept, dropped, notes


def _is_bank(sic: Optional[str]) -> bool:
    """SIC 6020-6036: commercial banks and savings institutions."""
    try:
        return 6020 <= int(str(sic).strip()) <= 6036
    except (TypeError, ValueError):
        return False


def _is_real_estate(sic: Optional[str]) -> bool:
    """SIC 6500-6599 (real estate) or 6798 (real estate investment trusts)."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return False
    return 6500 <= code <= 6599 or code == 6798


def _sic_sector(sic: Optional[str]) -> Optional[str]:
    """'financial or real estate' (SIC 6000-6799) or 'utility' (4900-4949, 4991), else None."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    if 6000 <= code <= 6799:
        return "financial or real estate"
    if 4900 <= code <= 4949 or code == 4991:
        return "utility"
    return None


def _component_revenue_flag(audit: list[dict], sic: Optional[str], facts_start: date) -> Optional[str]:
    """A warning when a financial, real-estate or utility filer is served contract revenue as its total revenue:
    the filers whose RevenueFromContractWithCustomer is most often only a slice (review finding 2)."""
    sector = _sic_sector(sic)
    served = [a for a in audit if a["served"] in REVENUE_COMPONENT_CONCEPTS and a["kind"] in ("quarter", "annual")
              and a["end"] >= facts_start.isoformat()]
    if not sector or not served:
        return None
    return (f"revenue: SIC {sic} ({sector}) filer served {len(served)} quarterly or annual revenue figures as "
            f"{served[-1]['served']}; check that it is total revenue, not a slice")


def revenue_audit(obs: list[Obs], selected: list[Obs], dropped: list[dict],
                  notes: tuple[dict, ...] | list[dict] = ()) -> list[dict]:
    """One line per filing and period that reports revenue: the concept served (None when every candidate was
    dropped), each dropped contract-revenue figure with the conditions that fired, and a review note when the
    served contract revenue sits beside lease income of 10% or more of it."""
    served = {(o.filing.accession, o.start, o.end): o for o in selected if o.metric == "revenue"}
    noted = {(n["accession"], n["start"], n["end"], n["concept"]): n["note"] for n in notes}
    gone: dict[tuple, list[dict]] = defaultdict(list)
    for d in dropped:
        gone[(d["accession"], d["start"], d["end"])].append(d)
    keys = {(o.filing.accession, o.start, o.end) for o in obs if o.metric == "revenue"} | set(gone)
    lines = []
    for k in sorted(keys, key=lambda k: (k[2], k[1] or date.min, k[0])):
        o = served.get(k)
        lines.append({"accession": k[0], "start": k[1].isoformat() if k[1] else None, "end": k[2].isoformat(),
                      "fiscal": f"{o.fiscal_period} FY{o.fiscal_year}" if o and o.fiscal_period else None,
                      "kind": o.kind if o else None, "served": o.concept if o else None,
                      "value": o.value if o else None,
                      "dropped": [{"concept": d["concept"], "value": d["value"], "conditions": d["conditions"]}
                                  for d in gone.get(k, [])],
                      **({"warning": noted[(*k, o.concept)]} if o and (*k, o.concept) in noted else {})})
    return lines


def build_calendar(obs: list[Obs], filings: dict[str, Filing], dei: dict[str, Counter],
                   warnings: list[str]) -> dict[tuple[int, str], Period]:
    """Fiscal periods from each 10-Q/10-K's own current period and its DEI fy/fp, checked against the company's
    labelling convention by receipts.fiscal_labels (shared with receipts.press_release)."""
    spans: dict[str, Counter] = defaultdict(Counter)
    for o in obs:
        f = o.filing
        if f.form not in ANCHOR_FORMS or o.start is None or o.metric in ISOLATED_METRICS:
            continue
        if o.span != ("12m" if f.form == "10-K" else "3m"):
            continue
        if f.report_date and abs((o.end - f.report_date).days) > 3:
            continue
        spans[f.accession][(o.start, o.end)] += 1
    periods: list[FilingPeriod] = []
    for accession, counter in spans.items():
        f = filings[accession]
        if f.report_date:
            (start, end), _ = counter.most_common(1)[0]
        else:
            start, end = max(counter, key=lambda se: (se[1], counter[se]))
        labels = dei.get(accession)
        if not labels:
            continue
        (fy, fp), _ = labels.most_common(1)[0]
        periods.append(FilingPeriod(accession, f.form, f.accepted_at, start, end, fy, fp))
    return {(lab.fiscal_year, lab.fiscal_period): Period(lab.fiscal_year, lab.fiscal_period, lab.start, lab.end,
                                                          lab.source)
            for lab in label_periods(periods, warnings)}


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


COST_TOTAL_CONCEPTS = ("CostOfRevenue", "CostOfGoodsAndServicesSold")


def select_concepts(obs: list[Obs]) -> list[Obs]:
    """Per metric, filing and period keep the highest-priority concept the filing used.

    A cost of revenue that the same filing reports beside a larger cost total for the period is a component, as
    contract revenue can be for revenue, so the larger figure stands (review fix 7). Illinois Tool Works tags
    CostOfRevenue at $1,914M for Q2 2025 beside the CostOfGoodsAndServicesSold of $2,271M its income statement and
    release call cost of revenue, and FY2025's $7,556M beside $8,969M; no other company tags the two that way."""
    groups: dict[tuple, list[Obs]] = defaultdict(list)
    for o in obs:
        if o.kind == "other":
            continue
        groups[(o.metric, o.filing.accession, o.start, o.end)].append(o)
    out: list[Obs] = []
    for key, cands in groups.items():
        best = min(cands, key=lambda o: o.priority)
        if key[0] == "cost_of_revenue":
            larger = [o for o in cands if o.concept in COST_TOTAL_CONCEPTS and o.value > best.value * (1 + ROUNDING)]
            if larger:
                best = max(larger, key=lambda o: o.value)
        out.append(best)
    return sorted(out, key=lambda o: (o.metric, o.end, o.start or date.min, _acc(o)))


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def _unit(o: Obs, decimals: dict[tuple[str, str], int]) -> float:
    """The rounding unit of o's filing: $1M for a filing in millions, $0.01 for cents per share."""
    return 10.0 ** -decimals.get((o.filing.accession, METRIC_CONCEPTS[o.metric].xbrl_unit), 0)


def _one_value(a: Obs, b: Obs, decimals: dict[tuple[str, str], int]) -> bool:
    """Two filed figures that are one number rounded differently: MLM's 9M 2023 cost of revenue is $3,629.8M in its
    own 10-Q and $3,630M as a comparative a year later."""
    return abs(a.value - b.value) <= 0.5 * max(_unit(a, decimals), _unit(b, decimals)) * (1 + 1e-9)


def _restated(versions: list[Obs], decimals: dict[tuple[str, str], int]) -> bool:
    """More than one filed value beyond rounding."""
    return any(not _one_value(a, versions[0], decimals) for a in versions[1:])


def drop_implausible_per_share(obs: list[Obs]) -> tuple[list[Obs], list[dict]]:
    """Per-share values beyond PER_SHARE_LIMITS are tagging slips, never served (review fix 13)."""
    keep, dropped = [], []
    for o in obs:
        limit = PER_SHARE_LIMITS.get(o.metric)
        if limit is not None and abs(o.value) > limit:
            dropped.append({"metric": o.metric, "accession": o.filing.accession, "concept": o.concept,
                            "start": o.start.isoformat() if o.start else None, "end": o.end.isoformat(),
                            "value": o.value, "why": f"a per-share value beyond {limit:,.0f}"})
        else:
            keep.append(o)
    return keep, dropped


def drop_irregular_dividends(selected: list[Obs]) -> tuple[list[Obs], list[dict]]:
    """A quarter's dividend per share is served only when it is the company's quarterly rate: positive, and equal to
    the filed dividend of the quarter before or after it (review fix 12, QA round 3 T2).

    A dividend tagged for a quarter is what the board declared during it. Pfizer declares its first-quarter dividend
    in December, so its first quarter files $0 and its second $0.86, two declarations of $0.43; Eli Lilly files $3.46
    for Q2 2026 and nothing for Q1 or Q3. Such a quarter matches neither neighbour, and a release, which states the
    rate, is the better evidence. A raise is served once the next quarter files the same rate."""
    quarters = [o for o in selected if o.metric == "dividend_per_share" and o.kind == "quarter" and o.start]
    keep, dropped = [], []
    for o in selected:
        if o.metric != "dividend_per_share" or o.kind != "quarter" or not o.start:
            keep.append(o)
            continue
        near = [n.value for n in quarters if 0 < (o.start - n.end).days <= 10 or 0 < (n.start - o.end).days <= 10]
        if o.value > 0 and any(abs(o.value - v) <= DIVIDEND_ROUNDING for v in near):
            keep.append(o)
            continue
        dropped.append({"metric": o.metric, "accession": o.filing.accession, "start": o.start.isoformat(),
                        "end": o.end.isoformat(), "value": o.value, "neighbours": sorted(set(near)),
                        "why": "zero" if o.value == 0 else ("no filed neighbouring quarter" if not near
                                                           else "not the rate of a neighbouring quarter")})
    return keep, dropped


def drop_misdated(selected: list[Obs], decimals: dict[tuple[str, str], int]) -> tuple[list[Obs], list[dict]]:
    """Quarterly and full-year figures whose value contradicts their period (review fix 3, QA T1).

    L3Harris' FY2025 10-K tags full-year revenue, cost of revenue and operating income under 3-month "Q4" dates, so
    Q4 2025 revenue read $21,865M (FY - 9M is $5,648M); Comfort Systems' Q1 2026 10-Q tags its Q1 2025 figures under
    a full-year 2025 date. For flow metrics (additive, in dollars), a reported quarter is dropped when
      (a) it equals the same concept's fiscal-year figure from a 10-K, within precision, while another quarter of
          that year is non-zero;
      (b) the reported Q1+Q2+Q3+Q4 differs from the 10-K's fiscal year by more than rounding in a year none of whose
          figures was ever restated; the Q4 goes when Q1-Q3 agree with the 9M YTD (in a restated year the sum fails
          for honest figures on two bases, so the test would drop true Q4s);
      (c) it is a Q4 of a metric that is never negative and exceeds every filed version of the year's 9M YTD: one
          quarter larger than the three before it combined, as when no 12-month figure was tagged at all (L3Harris'
          FY2025 revenue).
    For every metric, (d) a full-year figure from a 10-Q that no 10-K figure for that year matches is dropped: a 10-Q
    reports quarters and year-to-date periods, and these are quarters under a full-year date (Comfort Systems, AXON,
    and quarterly dividends at Ross and Williams-Sonoma). Nothing is put in their place: Q4 = FY - 9M is derived from
    what remains, and a period with no consistent figure answers "?". Returns the kept figures and one record per
    dropped figure."""
    def unit(o: Obs) -> float:
        return _unit(o, decimals)

    def record(o: Obs, rule: str, why: str) -> dict:
        return {"rule": rule, "metric": o.metric, "concept": o.concept, "accession": o.filing.accession,
                "form": o.filing.form, "start": o.start, "end": o.end, "fiscal": f"{o.fiscal_period} FY{o.fiscal_year}",
                "value": o.value, "why": why}

    by_year: dict[tuple[str, int], list[Obs]] = defaultdict(list)
    for o in selected:
        if o.fiscal_year is not None and not METRIC_CONCEPTS[o.metric].instant:
            by_year[(o.metric, o.fiscal_year)].append(o)
    dropped: dict[int, dict] = {}
    for (metric, fy), rows in sorted(by_year.items()):
        spec = METRIC_CONCEPTS[metric]
        tenk = [o for o in rows if o.kind == "annual" and o.filing.form in ("10-K", "10-K/A")]
        for o in rows:
            if (o.kind == "annual" and o.filing.form in ("10-Q", "10-Q/A") and tenk
                    and not any(_one_value(o, k, decimals) for k in tenk)):
                dropped[id(o)] = record(o, "d", f"a 10-Q's full-year figure that no 10-K figure for FY{fy} matches "
                                                f"({', '.join(f'{k.value:,.0f}' for k in tenk[:3])})")
        if not (spec.additive and spec.xbrl_unit == "USD"):
            continue
        quarters = {fp: [o for o in rows if o.kind == "quarter" and o.fiscal_period == fp]
                    for fp in ("Q1", "Q2", "Q3", "Q4")}
        nine = [o for o in rows if o.kind == "ytd" and o.fiscal_period == "9M"]
        for fp, qs in quarters.items():
            others = [o for g, os_ in quarters.items() if g != fp for o in os_ if o.value]
            for q in qs:
                same_year = [k for k in tenk if k.concept == q.concept and _one_value(q, k, decimals)]
                if others and same_year:
                    dropped.setdefault(id(q), record(q, "a", f"equals the FY{fy} figure of the 10-K "
                                                             f"{same_year[0].filing.accession}"))
                elif (fp == "Q4" and metric in NON_NEGATIVE_METRICS and nine
                        and all(q.value > n.value + 0.5 * unit(n) for n in nine)):
                    dropped.setdefault(id(q), record(q, "c", f"exceeds every 9M YTD figure of FY{fy} "
                                                             f"(at most {max(n.value for n in nine):,.0f})"))
        never_restated = (not _restated(tenk, decimals) and not (nine and _restated(nine, decimals))
                          and not any(_restated(qs, decimals) for qs in quarters.values()))
        if tenk and all(quarters.values()) and never_restated:
            q1_q3 = sum(quarters[fp][0].value for fp in ("Q1", "Q2", "Q3"))
            parts = [quarters[fp][0] for fp in quarters] + [tenk[0]]
            tol = 2.5 * max(unit(o) for o in parts)
            if (abs(q1_q3 + quarters["Q4"][0].value - tenk[0].value) > tol
                    and (not nine or abs(q1_q3 - nine[0].value) <= tol)):
                for q in quarters["Q4"]:
                    dropped.setdefault(id(q), record(q, "b", f"Q1+Q2+Q3+Q4 = {q1_q3 + q.value:,.0f} against "
                                                             f"FY{fy} {tenk[0].value:,.0f}"))
    kept = [o for o in selected if id(o) not in dropped]
    return kept, sorted(dropped.values(), key=lambda r: (r["metric"], r["end"], r["rule"], r["accession"]))


def _same_measure(total: Obs, concept: str, observations: list[Obs], decimals: dict[tuple[str, str], int]) -> bool:
    """The total's own filing reports its period under `concept` too, at the same value: for that company and period
    the two concepts are one measure (review fix 14)."""
    return any(o.concept == concept and o.metric == total.metric and o.filing.accession == total.filing.accession
               and o.start == total.start and o.end == total.end and _one_value(o, total, decimals)
               for o in observations)


def _first_filed(total: Obs, observations: Optional[list[Obs]]) -> Optional[Obs]:
    """The earliest periodic report of the total's metric, concept and dates in a filing of its own form, if filed
    before it."""
    if observations is None:
        return None
    annual = total.filing.form.startswith("10-K")
    same = [o for o in observations if o.metric == total.metric and o.concept == total.concept
            and o.start == total.start and o.end == total.end and o.filing.form.startswith("10-K") == annual
            and _acc(o) < _acc(total)]
    return min(same, key=_acc) if same else None


def derive(selected: list[Obs], cal: dict[tuple[int, str], Period],
           decimals: Optional[dict[tuple[str, str], int]] = None,
           skipped: Optional[list[dict]] = None, observations: Optional[list[Obs]] = None) -> list[Derived]:
    """Q4 = FY - 9M YTD (or FY - Q1 - Q2 - Q3) and Q2/Q3 = YTD - prior YTD, one row per distinct value, for every
    additive metric but NOT_DERIVED_METRICS (per-share dividends: what was declared in a period, not the quarter's).

    Each version of the total (the FY or the current YTD, original then comparatives) is paired with the
    latest component accepted at or before it. A later version is derived only if the total and its
    components were restated together. BAC restated FY2024 revenue in its FY2025 10-K, but no filing
    restated 9M 2024, so "restated FY - original 9M" would invent a Q4. NVDA's split-adjusted dividends
    restate both, so they stay. Component versions that differ only by rounding are one value (MLM's 9M
    2023 is $3,629.8M, then $3,630M), so a recast FY paired with them is a restated total with stale
    components, not a consistent restatement (review fix 4).

    When the component (the 9M for a Q4, the Q1 or H1 for a Q2 or Q3) has more than one filed value while
    the total, filed again after that change, kept one, the quarter is derived once, from the latest total
    and the latest component, and is public from the later acceptance. Dollar Tree's FY2024 10-K (Family
    Dollar discontinued) minus its original 9M (Family Dollar included) gave cost of revenue of -$4,377.1M;
    its FY2025 10-K repeats the FY2024 total, so only that pair counts. A derived quarter of a metric that is
    never negative must lie between zero and its total, and a derived Q4 between MIN_Q4_SHARE and MAX_Q4_SHARE
    of its fiscal year. Skips are recorded in `skipped`.

    A total pairs only with components of its own concept, or of a concept its own filing reports for the same period
    at the same value (`observations`, every concept the filings tag; review fix 14). Ares' FY2023 NetIncomeLoss
    minus its nine-month ProfitLoss, which includes noncontrolling interests, gave Q4 2023 net income of -$87.6M, and
    Hasbro's FY Revenues minus its nine-month contract revenue a Q4 2025 revenue of $2,110.5M.
    """
    decimals = decimals or {}
    by_metric: dict[str, list[Obs]] = defaultdict(list)
    for o in selected:
        by_metric[o.metric].append(o)
    out: list[Derived] = []
    for metric, spec in METRIC_CONCEPTS.items():
        if not spec.additive or metric not in by_metric or metric in NOT_DERIVED_METRICS:
            continue
        rows = by_metric[metric]

        def pick(kind: str, fy: int, fp: str) -> list[Obs]:
            return [o for o in rows if o.kind == kind and o.fiscal_year == fy and o.fiscal_period == fp]

        def skip(fy: int, fp: str, why: str, inputs: list[Obs], value: Optional[float] = None) -> None:
            if skipped is not None:
                skipped.append({"metric": metric, "fiscal": f"{fp} FY{fy}", "why": why, "value": value,
                                "inputs": [f"{o.filing.form} {o.filing.accession} {o.value:,.0f}" for o in inputs]})

        for (fy, fp), period in sorted(cal.items(), key=lambda kv: (kv[1].end, kv[0][1])):
            if fp not in ("Q2", "Q3", "Q4"):
                continue
            direct = pick("quarter", fy, fp)
            if fp == "Q4":
                if (fy, "FY") not in cal:
                    continue
                current = pick("annual", fy, "FY")
                component = pick("ytd", fy, "9M")
            else:
                current = pick("ytd", fy, "H1" if fp == "Q2" else "9M")
                component = pick("quarter", fy, "Q1") if fp == "Q2" else pick("ytd", fy, "H1")
            totals = sorted(current, key=_acc)

            def pairable(cands: list[Obs], total: Obs) -> list[Obs]:
                same = [o for o in cands if o.concept == total.concept]
                if same or observations is None:
                    return same
                return [o for o in cands if _same_measure(total, o.concept, observations, decimals)]

            def elsewhere(cands: list[Obs], total: Obs) -> list[Obs]:
                """Inputs on file by the total's acceptance under a concept it does not pair with."""
                fit = pairable(cands, total)
                return [o for o in cands if _acc(o) <= _acc(total) and o not in fit]

            every_component = component
            component = [o for o in component if any(o in pairable([o], t) for t in totals)]
            last_part = max(component, key=_acc) if component else None
            if (last_part is not None and _restated(component, decimals) and not _restated(totals, decimals)
                    and any(_acc(t) >= _acc(last_part) for t in totals)):
                # The component changed, and a total filed after that change kept the earlier value: every
                # version of the total stood on the component's new basis, so pairing one with the old component
                # mixes bases (DLTR FY2024). Only the latest pair is consistent (review fix 4). When the total
                # changed too (NVDA's split), each pair is consistent and every version stays; when no total came
                # after the change, each total still pairs with the component of its own time.
                latest = totals[-1]
                if any(_acc(d) <= _acc(latest) for d in direct):
                    continue  # a 3-month value was already filed: nothing to derive
                totals = [latest]

            def pair(v: Obs, record: bool = True) -> Optional[tuple[list[Obs], str]]:
                """The components the total v pairs with as of its acceptance, and the formula; None if it pairs with
                none (recorded when an input of its time is filed under another concept)."""
                if fp == "Q4":
                    nine = _latest(pairable(component, v), _acc(v), v.concept)
                    if nine is not None:
                        return [nine], f"Q4 = FY ({_ref(v)}) - 9M YTD ({_ref(nine)})"
                    quarters = [pick("quarter", fy, f"Q{n}") for n in (1, 2, 3)]
                    qs = [_latest(pairable(q, v), _acc(v), v.concept) for q in quarters]
                    if all(q is not None for q in qs):
                        return qs, f"Q4 = FY ({_ref(v)}) - " + " - ".join(
                            f"Q{n} ({_ref(q)})" for n, q in zip((1, 2, 3), qs))
                    other = elsewhere(every_component, v) + [
                        o for q, got in zip(quarters, qs) if got is None for o in elsewhere(q, v)]
                else:
                    c = _latest(pairable(component, v), _acc(v), v.concept)
                    if c is not None:
                        return [c], (f"{fp} = {'H1' if fp == 'Q2' else '9M'} YTD ({_ref(v)}) - "
                                     f"{'Q1' if fp == 'Q2' else 'H1 YTD'} ({_ref(c)})")
                    other = elsewhere(every_component, v)
                if other and record:
                    skip(fy, fp, "its components are filed under another concept", [v, *other])
                return None

            emitted: list[float] = []
            baseline: Optional[tuple[Obs, list[Obs]]] = None  # (total, components) of the first derivation
            for v in totals:
                if any(_acc(d) <= _acc(v) for d in direct):
                    continue  # a 3-month value was already filed: nothing to derive
                got = pair(v)
                if got is None:
                    continue
                parts, formula = got
                inputs, value = [v, *parts], v.value - sum(o.value for o in parts)
                if baseline is None:
                    # The total as first filed under its concept may be an observation another concept outranked in
                    # that filing: Crown Castle's FY2023 cost of goods sold is $316M in its FY2023 10-K (beside the
                    # CostOfRevenue served) and $304M, restated, in its FY2025 10-K, while its 9M stays $268M. A
                    # total restated since then, against the components of that time, mixes bases (review fix 14).
                    first = _first_filed(v, observations)
                    then = pair(first, record=False) if first is not None and not _one_value(first, v, decimals) \
                        else None
                    if then is not None and len(then[0]) == len(parts) and all(
                            _one_value(a, b, decimals) for a, b in zip(then[0], parts)):
                        skip(fy, fp, "restated since first filed, against the components of that filing", inputs,
                             value)
                        continue
                    baseline = (v, parts)
                else:
                    total_restated = not _same(v.value, baseline[0].value)
                    parts_restated = len(parts) != len(baseline[1]) or any(
                        not _one_value(a, b, decimals) for a, b in zip(parts, baseline[1]))
                    if total_restated and not parts_restated:
                        continue  # restated total with stale components: no consistent derivation
                if metric in NON_NEGATIVE_METRICS and (value < 0 or value > v.value + 0.5 * _unit(v, decimals)):
                    skip(fy, fp, "negative" if value < 0 else "larger than its total", inputs, value)
                    continue
                if metric in NON_NEGATIVE_METRICS and fp == "Q4" and not (
                        MIN_Q4_SHARE * v.value <= value <= MAX_Q4_SHARE * v.value):
                    skip(fy, fp, f"outside {MIN_Q4_SHARE:.0%}-{MAX_Q4_SHARE:.0%} of its fiscal year", inputs, value)
                    continue
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
                  last_n: int = 8, sic: Optional[str] = None, revenue_confirmed: bool = False,
                  rejected_derived: Iterable[dict] = (), contract_revenue_total: bool = False,
                  broad_revenue_total: Optional[str] = None, available_to_common: Optional[str] = None,
                  bank_revenue: bool = False) -> CompanyBuild:
    """Pure transformation from Submissions rows and companyfacts to rows for the store.

    A company whose served contract revenue is flagged (a financial, real-estate or utility SIC, or lease income of
    10% or more beside it) keeps that revenue only when `revenue_confirmed` (CONFIRMATIONS); otherwise it is not
    served and its audit lines say so. A derived quarter listed in `rejected_derived` (DERIVED_REJECTIONS: the
    company's own release outranks it) is not served either, and the audit's derivations_skipped says why. When the
    company's releases show contract revenue is its total (`contract_revenue_total`, a CONFIRMATIONS decision), a
    broad revenue figure its filing's contract revenue contradicts is not served (_drop_broad_revenue); when they show
    a comparison total is (`broad_revenue_total`, a concept), that concept is served as revenue; and when they report
    net income after preferred dividends (`available_to_common`, their label for it), AVAILABLE_TO_COMMON is served
    as net income; and when they show a bank's total revenue is net interest income plus noninterest income
    (`bank_revenue`), that sum is served as revenue."""
    today = today or date.today()
    ticker = ticker.upper()
    warnings: list[str] = []
    stats: dict = {}
    filings = parse_filings(cik, filing_rows)
    if filings_start is None and filings:
        filings_start = min(f.filed for f in filings.values())
    usgaap = companyfacts.get("facts", {}).get("us-gaap", {})
    obs, dei = collect_observations(usgaap, filings, stats, since=filings_start, sic=sic,
                                    contract_revenue_total=contract_revenue_total,
                                    broad_revenue_total=broad_revenue_total, available_to_common=available_to_common,
                                    bank_revenue=bank_revenue)
    cal = build_calendar(obs, filings, dei, warnings)
    classify(obs, cal, fiscal_year_end)
    selected = select_concepts(obs)
    stats["revenue_audit"] = revenue_audit(obs, selected, stats["component_revenue_dropped"],
                                           stats["lease_income_notes"])
    flag = _component_revenue_flag(stats["revenue_audit"], sic, facts_start)
    if flag:
        warnings.append(flag)
    leased = [a for a in stats["revenue_audit"] if a.get("warning") and a["kind"] in ("quarter", "annual")
              and a["end"] >= facts_start.isoformat()]
    if leased:
        warnings.append(f"revenue: operating-lease income is at least 10% of the served contract revenue in "
                        f"{len(leased)} quarterly or annual figures; review whether that revenue excludes leases")
    stats["revenue_flagged"] = bool(flag or leased)
    if stats["revenue_flagged"] and not revenue_confirmed:
        blocked = [o for o in obs if o.metric == "revenue" and o.concept in REVENUE_COMPONENT_CONCEPTS]
        obs = [o for o in obs if not (o.metric == "revenue" and o.concept in REVENUE_COMPONENT_CONCEPTS)]
        stats["component_revenue_dropped"] += [
            {"accession": o.filing.accession, "start": o.start, "end": o.end, "concept": o.concept,
             "value": o.value, "conditions": [AWAITING]} for o in blocked]
        stats["component_revenue_skipped"] = len(stats["component_revenue_dropped"])
        selected = select_concepts(obs)
        stats["revenue_audit"] = revenue_audit(obs, selected, stats["component_revenue_dropped"],
                                               stats["lease_income_notes"])
        warnings.append(f"revenue: contract revenue blocked until confirmed ({len(blocked)} figures): "
                        f"scripts/confirm_revenue.py checks them against the press-release totals")
    # ISOLATED_METRICS never move another metric's rows: the other metrics keep the precision their own values give each
    # filing, and the isolated ones take the precision of every value the filing tags. They are dropped and derived
    # by the same rules, separately, and their rows come after the others'.
    isolated = [o for o in selected if o.metric in ISOLATED_METRICS]
    selected = [o for o in selected if o.metric not in ISOLATED_METRICS]
    decimals = infer_decimals(selected)  # before any drop, so no filing's precision moves
    isolated_decimals = infer_decimals(selected + isolated)
    selected, stats["misdated_dropped"] = drop_misdated(selected, decimals)
    isolated, isolated_misdated = drop_misdated(isolated, isolated_decimals)
    stats["misdated_dropped"] = sorted(stats["misdated_dropped"] + isolated_misdated,
                                       key=lambda r: (r["metric"], r["end"], r["rule"], r["accession"]))
    selected, stats["per_share_implausible"] = drop_implausible_per_share(selected)
    selected, stats["dividend_quarters_not_served"] = drop_irregular_dividends(selected)
    if stats["per_share_implausible"]:
        warnings.append(f"per share: {len(stats['per_share_implausible'])} values beyond a per-share limit are not served")
    if stats["misdated_dropped"]:
        warnings.append(f"periods: {len(stats['misdated_dropped'])} quarterly or full-year figures contradict their "
                        f"period and are not served (see the audit's misdated_dropped)")
    stats["derivations_skipped"] = []
    derived = derive(selected, cal, decimals, stats["derivations_skipped"], obs)
    derived += derive(isolated, cal, isolated_decimals, stats["derivations_skipped"], obs)
    selected += isolated

    def precision(metric: str) -> dict[tuple[str, str], int]:
        return isolated_decimals if metric in ISOLATED_METRICS else decimals

    rejected = list(rejected_derived)
    if rejected:
        def outranked(d: Derived) -> Optional[dict]:
            tol = 0.5 if METRIC_CONCEPTS[d.metric].xbrl_unit == "USD" else 1e-6  # whole dollars, or per share
            return next((r for r in rejected if r["metric"] == d.metric and r["period_start"] == d.start.isoformat()
                         and r["period_end"] == d.end.isoformat() and abs(r["value"] - d.value) <= tol), None)
        kept = []
        for d in derived:
            r = outranked(d)
            if r is None:
                kept.append(d)
                continue
            stats["derivations_skipped"].append({
                "metric": d.metric, "fiscal": f"{d.fiscal_period} FY{d.fiscal_year}", "value": d.value,
                "why": f"outranked by the company's release {r['release']['accession']}: {r['why']}",
                "inputs": [f"{o.filing.form} {o.filing.accession} {o.value:,.0f}" for o in d.inputs]})
        if len(kept) < len(derived):
            warnings.append(f"derived: {len(derived) - len(kept)} derived quarters are outranked by the company's own "
                            f"releases and not served (data/derived_rejections.json)")
        derived = kept

    facts: list[tuple[Fact, str]] = []
    for o in selected:
        if o.end < facts_start:
            continue
        unit = METRIC_CONCEPTS[o.metric].xbrl_unit
        facts.append((Fact(
            cik=cik, ticker=ticker, metric=o.metric, concept=f"us-gaap:{o.concept}", basis="GAAP",
            value=o.value, unit=FACT_UNIT[unit], period_start=o.start, period_end=o.end,
            fiscal_year=o.fiscal_year, fiscal_period=o.fiscal_period, source="derived" if o.formula else "xbrl",
            derived_from=[o.filing.accession] if o.formula else [], form=o.filing.form,
            accession=o.filing.accession, accepted_at=o.filing.accepted_at, url=o.filing.url,
            locator=o.formula or o.note,
            decimals=precision(o.metric).get((o.filing.accession, unit))), o.kind))
    for d in derived:
        if d.end < facts_start:
            continue
        unit = METRIC_CONCEPTS[d.metric].xbrl_unit
        last = max(d.inputs, key=_acc)
        accessions = list(dict.fromkeys(o.filing.accession for o in d.inputs))
        precisions = [precision(d.metric)[(a, unit)] for a in accessions if (a, unit) in precision(d.metric)]
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
         "dividend_per_share": "dps", "repurchase_authorization_remaining": "buyb", "capital_expenditures": "capex"}


def _q(p: Period) -> str:
    return f"{p.fiscal_period} FY{p.fiscal_year}"


def store_run_date(meta: dict) -> Optional[date]:
    """The run date a store's window counts back from (main(): facts_start is the run date less the window), so
    records written about a store carry its date rather than the clock's (review L12)."""
    try:
        quarters = int(meta["quarters"])
        return date.fromisoformat(meta["facts_start"]) + timedelta(days=int((quarters + YOY_QUARTERS) * 91.5) + 45)
    except (KeyError, TypeError, ValueError):
        return None


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
    served = Counter(a["served"] for a in s["revenue_audit"] if a["kind"] in ("quarter", "annual"))
    fired = Counter(c.split(" is ")[-1] if " is " in c else c.split(" of ")[0] for a in s["revenue_audit"]
                    for d in a["dropped"] for c in d["conditions"])
    if served or fired:
        print(f"  revenue   served {', '.join(f'{k or chr(45)} x{v}' for k, v in served.most_common())}"
              + (f"; contract revenue dropped x{s['component_revenue_skipped']} "
                 f"({', '.join(f'{k} x{v}' for k, v in fired.most_common())})" if fired else ""))
    for w in build.warnings:
        print(f"  warning   {w}")


def _available_to_common(decisions: dict) -> Optional[str]:
    """The release label of net income for a company whose releases report it after preferred dividends, from its
    CONFIRMATIONS decision (scripts/confirm_revenue.py); None when there is no such decision."""
    decision = decisions.get("available_to_common_is_net_income")
    if not decision:
        return None
    return decision.get("label") or "Net income attributable to the company"


def write_audit(build: CompanyBuild, folder: Path) -> Path:
    """data/ingest_audit/<TICKER>.json: the calendar warnings and one revenue line per filing and period, so the
    choices ingest made can be audited after the raw companyfacts are gone."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{build.company['ticker']}.json"
    body = {"cik": build.company["cik"], "ticker": build.company["ticker"], "warnings": build.warnings,
            "fiscal_periods": [[fy, fp, s.isoformat(), e.isoformat(), src] for fy, fp, s, e, src in build.periods],
            "revenue": build.stats.get("revenue_audit", []),
            "misdated_dropped": build.stats.get("misdated_dropped", []),
            "derivations_skipped": build.stats.get("derivations_skipped", [])}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


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
    print(f"NoCap ingest: {len(tickers)} companies, last {args.quarters} quarters + {YOY_QUARTERS} for YoY; "
          f"facts ending since {facts_start}, filings since {filings_start} -> {args.db}")

    edgar = Edgar(offline=args.offline, max_live_requests=args.max_requests)
    confirmations = json.loads(CONFIRMATIONS.read_text()) if CONFIRMATIONS.exists() else {}
    rejections = json.loads(DERIVED_REJECTIONS.read_text()) if DERIVED_REJECTIONS.exists() else {}
    conn = connect_rw(args.db)
    builds, failed = [], []
    for ticker in tickers:
        try:
            cik, title = edgar.ticker_to_cik(ticker)
            meta, rows, cf, names = company_inputs(edgar, cik, since=filings_start, max_age=max_age)
            build = build_company(cik=cik, ticker=ticker, names=[names[0] or title, title, *names[1:]],
                                  fiscal_year_end=meta.get("fiscalYearEnd"), filing_rows=rows, companyfacts=cf,
                                  facts_start=facts_start, filings_start=filings_start, today=today,
                                  last_n=args.quarters, sic=meta.get("sic"),
                                  revenue_confirmed="confirmed_on" in confirmations.get(ticker, {}),
                                  contract_revenue_total="contract_revenue_is_total" in confirmations.get(ticker, {}),
                                  broad_revenue_total=confirmations.get(ticker, {}).get(
                                      "broad_revenue_is_total", {}).get("concept"),
                                  available_to_common=_available_to_common(confirmations.get(ticker, {})),
                                  bank_revenue="nii_plus_noninterest_is_revenue" in confirmations.get(ticker, {}),
                                  rejected_derived=rejections.get(ticker, ()))
        except EdgarError as e:
            print(f"\n{ticker}  FAILED: {e}")
            failed.append(ticker)
            continue
        replace_company(conn, build.company, build.filings, build.periods, build.facts)
        builds.append(build)
        write_audit(build, AUDIT_DIR if args.db == DB_PATH else args.db.parent / "ingest_audit")
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
