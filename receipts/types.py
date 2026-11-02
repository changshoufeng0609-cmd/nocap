"""Shared contracts for NoCap. Every module exchanges these types.

The LLM proposes and code decides. Claims come from the model, Facts come from SEC filings, and
Verdicts are computed only by deterministic code. Do not change this file casually: four modules
are built against it in parallel.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Literal, Optional, Protocol, Union

from pydantic import BaseModel, ConfigDict

# Canonical vocabulary. The first 15 classes are identical to spikes/b2_claims.py METRICS.
# The base metrics after them are needed for verification: gross_margin is gross_profit / revenue,
# and banks report net_interest_income.
Metric = Literal[
    "revenue", "segment_revenue", "revenue_growth", "gross_margin", "operating_expenses",
    "operating_income", "net_income", "eps_diluted", "eps_basic", "capital_returned",
    "repurchase_authorization_remaining", "dividend_per_share", "tax_rate", "guidance_tolerance", "other",
    # base metrics added on Day 2
    "gross_profit", "cost_of_revenue", "research_and_development", "operating_cash_flow",
    "free_cash_flow", "net_interest_income",
]
# Evidence-only metrics (receipts.balance_sheet): balance-sheet instants, and the cover page's share count. They are
# not in Metric, the extraction vocabulary, so the model never proposes one and extraction's schema and cache keys stay
# as they are: the verifier reaches them from a claim's own wording. Only Fact.metric takes them.
BalanceSheetMetric = Literal[
    "cash_and_equivalents", "total_assets", "total_liabilities", "stockholders_equity", "long_term_debt",
    "long_term_debt_noncurrent", "total_debt", "shares_outstanding",
]
# Alternative income lines stay outside extraction. They only explain ambiguity for unqualified net income;
# a claim's own words must name the alternative before the verifier can select it.
IncomeAlternativeMetric = Literal["net_income_to_common", "net_income_including_nci"]
Basis = Literal["GAAP", "non-GAAP"]
Unit = Literal["USD", "%", "USD/share", "shares", "other"]
Scale = Literal["units", "thousands", "millions", "billions"]
PeriodType = Literal["quarter", "year_to_date", "annual", "trailing_twelve_months", "instant", "unknown"]
Comparison = Literal["none", "QoQ", "YoY", "sequential"]
SCALE_FACTOR: dict[str, float] = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}


class Claim(BaseModel):
    """One numeric statement from a draft. The model proposed it; code expanded `bases` and located the span."""

    model_config = ConfigDict(extra="forbid")

    id: str                        # stable within one draft, e.g. "c7"
    metric: Metric
    metric_text: str               # the draft's own words, for display
    value_text: str                # exactly as written, e.g. "$96.2 billion"
    value: float                   # as written, scale not applied: 96.2
    unit: Unit
    scale: Scale
    period_text: str
    period_type: PeriodType
    comparison: Comparison
    basis: Optional[Basis]         # one basis per claim after expansion; None means not stated
    forward_looking: bool
    entity: str                    # as written, e.g. "NVIDIA"
    segment: Optional[str] = None
    span_text: str
    span_start: int                # computed by code from span_text, never by the model
    span_end: int


class Fact(BaseModel):
    """One number from an SEC filing, with full provenance. This is the receipt."""

    model_config = ConfigDict(extra="forbid")

    cik: int
    ticker: str
    metric: Union[Metric, BalanceSheetMetric, IncomeAlternativeMetric]
    concept: Optional[str] = None  # e.g. "us-gaap:Revenues"; None for press-release cells
    basis: Basis                   # XBRL facts are GAAP; press-release cells may be non-GAAP
    segment: Optional[str] = None
    value: float                   # full units: USD (not millions), USD/share, or percent as a number (75.0)
    unit: Unit
    period_start: Optional[date] = None  # None for instants
    period_end: date
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None  # "Q1".."Q4" or "FY"
    # release_text: a sentence of the company's filed 8-K earnings release that states a figure the other sources
    # don't hold (receipts.release_text); locator holds the quoted sentence
    source: Literal["xbrl", "press_release", "derived", "release_text"]
    derived_from: list[str] = []   # accessions used, e.g. Q4 = FY - 9M or a YTD difference
    form: str                      # "10-Q", "10-K", or "8-K"
    accession: str
    accepted_at: datetime          # EDGAR acceptanceDateTime (UTC), the point-in-time key
    url: str                       # filing document URL for the receipt panel
    locator: Optional[str] = None  # verbatim cell text, row label, or section, used for highlighting
    decimals: Optional[int] = None  # XBRL decimals, or the precision implied by a press-release table


ReasonCode = Literal[
    "match",            # verified: equals the evidence within rounding
    "value_mismatch",   # wrong number
    "period_mismatch",  # the number belongs to another period, e.g. YoY vs QoQ or the wrong quarter
    "scale_mismatch",   # thousands vs millions vs billions
    "basis_mismatch",   # stated as GAAP but the number is non-GAAP, or the reverse
    "entity_mismatch",  # the number belongs to another company
    "look_ahead",       # the evidence was not public at the as-of time
    "forward_looking",  # guidance; cannot be verified against filings
    "no_evidence",      # no filing fact for this metric and period
    "ambiguous",        # period, entity, or basis cannot be resolved; ambiguity never yields "wrong"
    "approximate",      # an estimate (for example read off a chart axis) that is within tolerance
    "release_statement",  # verified: the company's own filed 8-K earnings release states this figure in words
                          # (receipts.release_text); only for a figure no filing fact covers, never "wrong"
]
Status = Literal["verified", "wrong", "unverifiable", "approximate"]  # shown as ✓ ✗ ? ≈


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: Status
    reason: ReasonCode
    explanation: str               # one sentence for the UI, e.g. "The 10-Q reports $96.22B for Q2 FY2027."
    expected_value: Optional[float] = None  # the correct value in the claim's own unit and scale, when wrong
    evidence: list[Fact] = []      # the receipt(s)
    as_of: Optional[datetime] = None


# The buckets of Check, Fix and Slide `counts`, shown ✓ ✗ ◷ ? ≈. A look-ahead verdict keeps status "wrong" with
# reason "look_ahead" (the evidence was not public at the as-of time); it is counted as not_public, never as wrong.
COUNT_KEYS = ("verified", "wrong", "not_public", "unverifiable", "approximate")


def count_key(verdict: Verdict) -> str:
    """The `counts` bucket of one verdict."""
    return "not_public" if verdict.reason == "look_ahead" else verdict.status


def verdict_counts(verdicts: Iterable[Verdict]) -> dict[str, int]:
    counts = dict.fromkeys(COUNT_KEYS, 0)
    for verdict in verdicts:
        counts[count_key(verdict)] += 1
    return counts


class EvidenceStore(Protocol):
    """Implemented by receipts.store. The verifier depends only on this interface."""

    def resolve_entity(self, name: str) -> Optional[tuple[int, str]]:
        """Company name or ticker as written in a draft, mapped to (cik, ticker), or None."""
        ...

    def facts(self, cik: int, metric: Metric, *, basis: Optional[Basis] = None,
              segment: Optional[str] = None, period_end: Optional[date] = None,
              as_of: Optional[datetime] = None) -> list[Fact]:
        """Quarterly and annual facts, including derived ones, from both XBRL and press releases.

        With `as_of`, only facts accepted at or before that time are returned.
        """
        ...

    def fiscal_calendar(self, cik: int) -> list[tuple[int, str, date, date]]:
        """Known fiscal periods as (fiscal_year, fiscal_period, start, end), e.g. (2027, "Q2", ...)."""
        ...

    def first_public(self, cik: int, metric: Metric, period_end: date,
                     basis: Optional[Basis] = None) -> Optional[datetime]:
        """When a value for this metric and period first became public (earliest acceptance time)."""
        ...
