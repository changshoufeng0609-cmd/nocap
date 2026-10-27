"""Deterministic verification: one Claim plus the EvidenceStore gives one Verdict. No model is called here.

Rules (docs/adr-001-architecture.md, .claude/reference/project.md):
  * Forward-looking claims (guidance) are unverifiable / forward_looking.
  * The entity maps to a CIK through store.resolve_entity; failing that, unverifiable / ambiguous.
  * The period resolves on the company's own fiscal calendar (receipts.periods); failing that, ambiguous.
  * Non-GAAP claims verify only against press-release facts. GAAP or unstated claims verify against XBRL,
    derived and press-release GAAP facts. With no basis stated, non-GAAP wording ("adjusted", "managed",
    "FTE") also means press-release facts only, and a match on the other basis is ambiguous, never wrong.
    Bank headline revenue and net interest income with no basis stated are never wrong on GAAP alone.
  * Tolerance is the precision written in value_text: "$96.2 billion" allows +-$0.05B, "18%" +-0.5 pp,
    "$2.46" +-$0.005, "75.0%" +-0.05 pp. Hedges widen it: "approximately" gives "≈", "more than" is a bound.
  * Derived metrics: gross_margin = gross_profit / revenue (or (revenue - cost of revenue) / revenue) for the
    same period and basis; revenue_growth QoQ against the previous fiscal quarter and YoY against the same
    quarter of the prior fiscal year; segment revenue from segment facts only. A segment the wording names
    ("Data Center revenue") is never checked against the consolidated total.
  * Restatements: the store returns every version; a claim matching any version visible at as_of is
    verified, and a wrong claim is corrected with the latest visible version.
  * A wrong value is diagnosed in this order: period_mismatch (the same metric in another period),
    scale_mismatch (a x1000 shift), basis_mismatch (the other basis), entity_mismatch (another company the
    context names), else value_mismatch. expected_value is in the claim's own unit and scale.
  * With as_of, evidence accepted after as_of does not count. If none of it was public then, the claim is
    wrong / look_ahead, citing the acceptance time, only when as_of also precedes the period's Item 2.02
    earnings 8-K (or a press-release fact dates the release); otherwise it is unverifiable, because the
    release may already have been out with no press-release fact on file.
  * No facts: unverifiable / no_evidence. Ambiguity never produces "wrong".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from receipts import periods
from receipts.periods import CalEntry, ResolvedPeriod
from receipts.types import SCALE_FACTOR, Claim, EvidenceStore, Fact, Verdict

log = logging.getLogger(__name__)

USD_METRICS = frozenset({
    "revenue", "segment_revenue", "operating_expenses", "operating_income", "net_income", "capital_returned",
    "repurchase_authorization_remaining", "gross_profit", "cost_of_revenue", "research_and_development",
    "operating_cash_flow", "free_cash_flow", "net_interest_income"})
PER_SHARE_METRICS = frozenset({"eps_diluted", "eps_basic", "dividend_per_share"})
PERCENT_METRICS = frozenset({"revenue_growth", "gross_margin", "tax_rate"})
INSTANT_METRICS = frozenset({"repurchase_authorization_remaining"})
NON_GAAP_BY_NATURE = frozenset({"free_cash_flow"})
# Bank headline revenue and net interest income are often managed / fully taxable-equivalent (non-GAAP):
# with no basis stated, a mismatch against GAAP alone is "?", never "✗".
BANK_TICKERS = frozenset({"JPM", "BAC", "C", "WFC", "GS", "MS", "USB", "PNC", "TFC", "COF", "SCHW", "BK", "STT"})
BASIS_SENSITIVE_AT_BANKS = frozenset({"revenue", "net_interest_income"})
SCALE_SHIFTS = ((1e3, "1,000"), (1e-3, "1,000"), (1e6, "a million"), (1e-6, "a million"))
SOURCE_RANK = {"xbrl": 0, "derived": 1, "press_release": 2}
EARNINGS_RELEASE_WINDOW_DAYS = 100  # an Item 2.02 8-K this many days after a period end reports that period

METRIC_LABELS = {
    "revenue": "revenue", "segment_revenue": "revenue", "revenue_growth": "revenue growth",
    "gross_margin": "gross margin", "operating_expenses": "operating expenses",
    "operating_income": "operating income", "net_income": "net income", "eps_diluted": "diluted EPS",
    "eps_basic": "basic EPS", "capital_returned": "capital returned to shareholders",
    "repurchase_authorization_remaining": "remaining buyback authorization",
    "dividend_per_share": "dividend per share", "tax_rate": "effective tax rate",
    "guidance_tolerance": "guidance range", "other": "figure", "gross_profit": "gross profit",
    "cost_of_revenue": "cost of revenue", "research_and_development": "R&D expense",
    "operating_cash_flow": "operating cash flow", "free_cash_flow": "free cash flow",
    "net_interest_income": "net interest income",
}
GENERIC_ENTITIES = frozenset({"", "the company", "company", "we", "our", "us", "it", "the firm", "the group",
                              "management", "the business", "n/a", "none", "unknown"})

_APPROX_RE = re.compile(r"(?:\b(?:approximately|approx\.?|about|around|roughly|nearly|almost|close\s+to|some|circa)"
                        r"|~)\s*$")
_LOWER_RE = re.compile(r"\b(?:more\s+than|over|above|exceeding|in\s+excess\s+of|at\s+least|upwards?\s+of"
                       r"|north\s+of)\s*$")
_UPPER_RE = re.compile(r"\b(?:less\s+than|under|below|at\s+most|up\s+to|no\s+more\s+than)\s*$")
_DOWN_RE = re.compile(r"\b(?:down|declin\w*|decreas\w*|fell|fall\w*|dropp?\w*|lower|shr[ai]nk\w*|shrunk"
                      r"|contract\w*)\b")
_LOSS_RE = re.compile(r"\bloss(?:es)?\b")
_YEAR_AGO_RE = re.compile(r"\ba\s+year\s+(?:ago|earlier|before)\b|\byear[\s-]+(?:ago|earlier)\b|\bprior[\s-]+year\b"
                          r"|\blast\s+year\b|\bprevious\s+year\b")
_PRIOR_Q_RE = re.compile(r"\b(?:previous|prior|preceding|last)\s+quarter\b|\bsequential(?:ly)?\b")
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_NON_GAAP_WORDS_RE = re.compile(r"\b(?:non[\s-]?gaap|adjusted|managed|fte|fully[\s-]taxable[\s-]equivalent|underlying"
                                r"|organic|pro[\s-]forma"
                                # constant-currency / FX-neutral growth is not the reported figure (Day 2 eval:
                                # 10 of 11 false alarms were these, compared against reported growth)
                                r"|constant[\s-]currenc(?:y|ies)|currency[\s-]neutral|ex[\s-]?fx"
                                r"|(?:fx|f/x)[\s-]?neutral|on\s+an?\s+(?:fx|f/x|foreign[\s-]exchange)\b)", re.I)
_NEGATIVE_RE = re.compile(r"\b(?:negative|minus)\b")
# Words before "revenue" that don't name a segment: totals, bases, periods, and possessives.
_GENERIC_QUALIFIERS = frozenset(
    "total net gross reported record quarterly annual consolidated overall company group companywide company-wide "
    "fiscal full-year full year quarter quarters first second third fourth first-quarter second-quarter "
    "third-quarter fourth-quarter q1 q2 q3 q4 its the our their his her a an of for in and gaap non-gaap "
    "managed adjusted headline top-line topline revenue revenues sales fy ttm trailing twelve-month "
    "year-over-year sequential latest current inc corp corporation co ltd plc llc".split())
_REVENUE_SUBJECT_RE = re.compile(r"((?:[A-Z][\w&.\-]*\s+){1,4})(?:[Ss]egment\s+)?[Rr]evenues?\b")
_CONTEXT_NOISE_RE = re.compile(  # period words in a context line; what is left is the company name
    r"\b(?:f?q[1-4](?:'\d{2})?|[1-4]q\d{0,4}|fy\s*'?\d{2,4}|fiscal|years?|quarters?|full|ended|ending|end|of|for|"
    r"the|first|second|third|fourth|three|twelve|months|period|as|\d{1,4}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
    r"apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    r"|[,.;:()]|(?:'|’)s\b", re.I)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def to_utc(x: Any) -> Optional[datetime]:
    """datetime, date or ISO string as an aware UTC datetime. Naive values are taken as UTC."""
    if x is None:
        return None
    if isinstance(x, str):
        s = x.strip()
        x = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
    if isinstance(x, date) and not isinstance(x, datetime):
        x = datetime(x.year, x.month, x.day)
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)


def possessive(name: str) -> str:
    """The possessive form: NVIDIA's, but Meta Platforms'."""
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def fmt_ts(x: datetime) -> str:
    return f"{to_utc(x):%Y-%m-%d %H:%M:%S} UTC"


def decimals_of(value_text: str, value: float) -> int:
    """Decimal places of the number written in value_text ('$96.2 billion' -> 1, '18%' -> 0)."""
    nums = _NUM_RE.findall(value_text or "")
    if nums:
        pick = min(nums, key=lambda n: abs(float(n.replace(",", "")) - abs(value)))
        return len(pick.split(".", 1)[1]) if "." in pick else 0
    r = repr(float(value))
    return 0 if r.endswith(".0") or "e" in r else len(r.split(".", 1)[1])


def tolerance(claim: Claim) -> float:
    """Half a unit of the last written digit, in full units: '$96.2 billion' -> 0.05e9, '18%' -> 0.5."""
    step = 10.0 ** -decimals_of(claim.value_text, claim.value)
    if claim.unit in ("USD", "shares"):
        step *= SCALE_FACTOR.get(claim.scale, 1.0)
    return 0.5 * step


def claimed_value(claim: Claim) -> float:
    """The claim's value in full units (USD, not billions), signed by the draft's own words."""
    v = float(claim.value)
    if claim.unit in ("USD", "shares"):
        v *= SCALE_FACTOR.get(claim.scale, 1.0)
    words = f"{claim.span_text} {claim.metric_text}".lower()
    if v > 0 and _NEGATIVE_RE.search(words):
        v = -v  # "free cash flow was negative $2.12 billion"
    elif v > 0 and claim.metric == "revenue_growth" and _DOWN_RE.search(words):
        v = -v  # "revenue fell 5%"
    elif v > 0 and claim.metric in ("net_income", "operating_income") and _LOSS_RE.search(words):
        v = -v  # "a net loss of $1.2 billion"
    return v


def within(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-9 * max(1.0, abs(a), abs(b))


def to_claim_units(value: float, claim: Claim) -> float:
    """A full-unit value in the claim's own unit and scale: 9.86e9 for a '$9.86 million' claim is 9860.0."""
    if claim.unit in ("USD", "shares"):
        value /= SCALE_FACTOR.get(claim.scale, 1.0)
    return float(f"{value:.10g}")


def fmt_money(v: float) -> str:
    sign, a = ("-" if v < 0 else ""), abs(v)
    for factor, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if a >= factor:
            x = f"{a / factor:,.3f}".rstrip("0").rstrip(".")
            return f"{sign}${x} {word}"
    return f"{sign}${a:,.2f}"


def fmt_value(v: float, metric: str, claim: Claim) -> str:
    if metric in PERCENT_METRICS or claim.unit == "%":
        d = max(1, decimals_of(claim.value_text, claim.value) + 1)
        return f"{v:.{d}f}%"
    if metric in PER_SHARE_METRICS or claim.unit == "USD/share":
        return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"
    if claim.unit == "shares":
        return f"{v:,.0f} shares"
    return fmt_money(v)


def _seg_norm(s: Optional[str]) -> str:
    s = (s or "").casefold().replace("&", " and ")
    s = re.sub(r"\b(?:segment|segments|revenue|revenues|business|market|platform|the)\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def implied_segment(claim: Claim) -> Optional[str]:
    """A segment the wording names although the claim has none: 'Data Center revenue' -> 'Data Center'.

    A segment figure must never be checked against the consolidated total (a false ✗), so a revenue claim
    whose metric_text or quote puts a non-generic qualifier before "revenue" is treated as a segment claim.
    The company's own name, periods and bases ("NVIDIA Q2 revenue", "total GAAP revenue") don't count."""
    company = {w for w in re.sub(r"(?:'s|’s)\b", "", claim.entity or "").casefold().split() if w}
    candidates = []
    m = re.match(r"^\s*(.*?)\s*\brevenues?\b", claim.metric_text or "", re.I)
    if m:
        candidates.append(m.group(1))
    i = claim.span_text.find(claim.value_text) if claim.value_text else -1
    for m in _REVENUE_SUBJECT_RE.finditer(claim.span_text[:i] if i > 0 else claim.span_text):
        candidates.append(m.group(1))
    for raw in candidates:
        words = [w for w in re.sub(r"(?:'s|’s)\b", "", raw).replace(",", " ").split()]
        keep = [w for w in words if w.casefold().strip(".") not in _GENERIC_QUALIFIERS
                and w.casefold() not in company and not re.fullmatch(r"(?:fy|q[1-4])?'?\d{2,4}", w.casefold())]
        if keep and len(keep) <= 4:
            return " ".join(keep)
    return None


def _fact_kind(f: Fact) -> str:
    if f.period_start is not None:
        days = (f.period_end - f.period_start).days + 1
        return "quarter" if 80 <= days <= 100 else "annual" if 350 <= days <= 380 else "other"
    fp = (f.fiscal_period or "").upper()
    return "quarter" if fp in periods.QUARTERS else "annual" if fp == "FY" else "instant"


def _source_rank(f: Fact) -> int:
    return SOURCE_RANK.get(f.source, 3)


def form_phrase(f: Fact) -> str:
    if f.source == "press_release":
        return "8-K press release"
    return f"{f.form} (derived)" if f.source == "derived" else f.form


def _dedupe_facts(facts: Iterable[Fact]) -> list[Fact]:
    out: dict[tuple[str, str, Optional[str], float, date], Fact] = {}
    for f in facts:
        out.setdefault((f.accession, f.metric, f.segment, f.value, f.period_end), f)
    return sorted(out.values(), key=lambda f: (to_utc(f.accepted_at), _source_rank(f)))


@dataclass(frozen=True)
class Obs:
    """One observed value for a (company, metric, period, basis): a fact, or a value derived from facts."""

    value: float
    facts: tuple[Fact, ...]

    @property
    def public_at(self) -> datetime:  # a derived value is public once its last input is
        return max(to_utc(f.accepted_at) for f in self.facts)

    @property
    def primary(self) -> Fact:
        """The fact the value is about: the fact itself, a margin's gross profit, a growth rate's current
        quarter. A non-GAAP margin is cited from its press release, never from the 10-Q revenue beneath it."""
        return self.facts[0]


def _best_obs(obs: Iterable[Obs]) -> Obs:
    """The receipt to cite for a match: a filing over a press release, the original version first."""
    return min(obs, key=lambda o: (_source_rank(o.primary), o.public_at))


def _current_obs(obs: Iterable[Obs]) -> Obs:
    """The figure to correct a wrong claim with: a filing over a press release, the latest version visible
    (the store keeps restated versions; a claim matching any of them was already accepted as verified)."""
    return min(obs, key=lambda o: (_source_rank(o.primary), -o.public_at.timestamp()))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
def _name_variants(name: str) -> list[str]:
    out = [name.strip()]
    s = re.sub(r"(?:'s|’s)$", "", out[0]).strip()
    out.append(s)
    out.append(re.sub(r",?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.)$",
                      "", s).strip())
    return [v for v in dict.fromkeys(out) if v]


def _resolve(store: EvidenceStore, name: str) -> Optional[tuple[int, str]]:
    for cand in _name_variants(name):
        r = store.resolve_entity(cand)
        if r:
            return int(r[0]), str(r[1])
    return None


def context_entities(context: Optional[str], store: EvidenceStore) -> list[tuple[int, str, str]]:
    """Companies a context line names, as (cik, ticker, name as written): 'NVIDIA Q2 FY2027' -> NVIDIA."""
    if not context:
        return []
    out: dict[int, tuple[int, str, str]] = {}
    for chunk in re.split(r"[;,/|]|\s+(?:and|vs\.?|versus)\s+", context):
        name = " ".join(_CONTEXT_NOISE_RE.sub(" ", chunk).split())
        if not re.search(r"[A-Za-z]", name):
            continue
        hit = _resolve(store, name)
        words = name.split()
        for k in range(len(words) - 1, 0, -1):  # "NVIDIA Corp" or a leftover word: try shorter prefixes
            if hit:
                break
            hit = _resolve(store, " ".join(words[:k]))
        if hit and hit[0] not in out:
            out[hit[0]] = (hit[0], hit[1], name)
    return list(out.values())


def resolve_claim_entity(claim: Claim, store: EvidenceStore, context: Optional[str] = None) -> Optional[tuple[int, str]]:
    """(cik, ticker) for the claim's company. A generic subject ('the company') falls back to a context that
    names exactly one company; a named company the store doesn't know is never swapped for the context's."""
    name = (claim.entity or "").strip()
    if name.casefold() not in GENERIC_ENTITIES:
        return _resolve(store, name)
    ents = context_entities(context, store)
    return ents[0][:2] if len(ents) == 1 else None


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
def verify(claim: Claim, store: EvidenceStore, *, as_of: Any = None, context: Optional[str] = None) -> Verdict:
    """The verdict for one claim. as_of: datetime or ISO string; evidence accepted later is look-ahead.
    context: the document-context line (company and fiscal period) used for extraction."""
    as_of_dt = to_utc(as_of)

    def plain(status: str, reason: str, explanation: str) -> Verdict:
        return Verdict(claim_id=claim.id, status=status, reason=reason, explanation=explanation, as_of=as_of_dt)

    if claim.forward_looking or claim.metric == "guidance_tolerance":
        return plain("unverifiable", "forward_looking",
                     "This is guidance about a future period, so no filing can confirm it yet.")
    if claim.metric == "other":
        what = f" ({claim.metric_text})" if claim.metric_text else ""
        return plain("unverifiable", "no_evidence",
                     f"Receipts doesn't track this kind of figure{what} yet, so there is no filing fact to check.")
    entity = resolve_claim_entity(claim, store, context)
    if entity is None:
        who = claim.entity.strip() or "the company"
        return plain("unverifiable", "ambiguous", f"Couldn't match \"{who}\" to a company in the SEC filings database.")
    return _Check(claim, store, entity, as_of_dt, context).run()


class _Check:
    def __init__(self, claim: Claim, store: EvidenceStore, entity: tuple[int, str], as_of: Optional[datetime],
                 context: Optional[str]):
        self.c, self.store, self.as_of, self.context = claim, store, as_of, context
        self.cik, self.ticker = entity
        name = " ".join(re.sub(r"\([^)]*\)", " ", claim.entity).split())  # "Amazon (AMZN)" -> "Amazon"
        self.company = name if name.casefold() not in GENERIC_ENTITIES else self.ticker
        self.co_s = possessive(self.company)
        self.cal = periods.calendar(store, self.cik)
        self.segment = (claim.segment or "").strip() or None
        if self.segment is None and claim.metric in ("revenue", "revenue_growth"):
            self.segment = implied_segment(claim)  # never check a segment figure against the consolidated total
        self.metric = "segment_revenue" if claim.metric == "revenue" and self.segment else claim.metric
        # The basis the draft states. With none stated, non-GAAP wording ("adjusted", "managed", "FTE") or a
        # non-GAAP-only measure still means press-release evidence only, and a GAAP mismatch is never "✗".
        self.basis_stated = claim.basis is not None
        non_gaap_words = _NON_GAAP_WORDS_RE.search(f"{claim.metric_text} {claim.span_text}")
        self.basis = claim.basis or ("non-GAAP" if self.metric in NON_GAAP_BY_NATURE or non_gaap_words else None)
        self.claimed = claimed_value(claim)
        self.tol = tolerance(claim)
        self._facts: dict[tuple[Any, ...], list[Fact]] = {}

    # -- verdicts ------------------------------------------------------------
    def verdict(self, status: str, reason: str, explanation: str, *, evidence: Iterable[Fact] = (),
                expected: Optional[float] = None) -> Verdict:
        return Verdict(claim_id=self.c.id, status=status, reason=reason, explanation=explanation,
                       expected_value=None if expected is None else to_claim_units(expected, self.c),
                       evidence=_dedupe_facts(evidence), as_of=self.as_of)

    def wrong(self, reason: str, actual: Obs, explanation: str, extra: Iterable[Fact] = ()) -> Verdict:
        return self.verdict("wrong", reason, explanation, evidence=[*actual.facts, *extra], expected=actual.value)

    # -- labels ----------------------------------------------------------------
    def plabel(self, rp: ResolvedPeriod, cal: Optional[list[CalEntry]] = None) -> str:
        return periods.label(rp, self.cal if cal is None else cal)

    def mlabel(self, basis_word: bool = True) -> str:
        base = METRIC_LABELS.get(self.metric, self.metric)
        if self.segment and self.metric in ("segment_revenue", "revenue_growth"):
            base = f"{self.segment} {base}"
        if basis_word and self.c.basis and self.metric not in ("revenue", "segment_revenue", "revenue_growth"):
            base = f"{self.c.basis} {base}"
        return base

    def fmt(self, v: float) -> str:
        return fmt_value(v, self.metric, self.c)

    def statement(self, o: Obs, rp: ResolvedPeriod, comparison: Optional[str] = None,
                  cal: Optional[list[CalEntry]] = None) -> str:
        if self.metric == "revenue_growth":
            subject = f"{self.segment} revenue" if self.segment else "revenue"
            verb = "grew" if o.value >= 0 else "fell"
            return f"{subject} {verb} {self.fmt(abs(o.value))} {_cmp_words(comparison or self.c.comparison)} in " \
                   f"{self.plabel(rp, cal)}"
        return f"{self.mlabel()} was {self.fmt(o.value)} for {self.plabel(rp, cal)}"

    # -- evidence --------------------------------------------------------------
    def public(self, o: Obs) -> bool:
        return self.as_of is None or o.public_at <= self.as_of

    def _store_facts(self, cik: int, metric: str, period_end: date, segment: Optional[str]) -> list[Fact]:
        """Store query (cached per check). segment=None is consolidated only; a named segment falls back to
        every segment ("*") so 'data center' still finds 'Data Center segment' through _seg_norm."""
        key = (cik, metric, period_end, segment)
        if key not in self._facts:
            facts = list(self.store.facts(cik, metric, segment=segment, period_end=period_end) or [])
            if segment and not facts:
                facts = list(self.store.facts(cik, metric, segment="*", period_end=period_end) or [])
            self._facts[key] = facts
        return self._facts[key]

    def level(self, cik: int, metric: str, rp: Optional[ResolvedPeriod], basis: Optional[str],
              segment: Optional[str] = None) -> list[Obs]:
        if rp is None or rp.period_end is None:
            return []
        want = "instant" if metric in INSTANT_METRICS else "annual" if rp.fiscal_period == "FY" else "quarter"
        out = []
        for f in self._store_facts(cik, metric, rp.period_end, segment):
            if f.cik != cik or f.metric != metric or f.period_end != rp.period_end:
                continue
            if basis == "non-GAAP":
                if f.basis != "non-GAAP" or f.source != "press_release":
                    continue  # non-GAAP figures come only from press releases
            elif f.basis != "GAAP":
                continue
            if segment is None:
                if f.segment:
                    continue
            elif _seg_norm(f.segment) != _seg_norm(segment):
                continue
            if want == "instant":
                if f.period_start is not None:
                    continue
            elif _fact_kind(f) != want:
                continue
            out.append(Obs(float(f.value), (f,)))
        return out

    def growth(self, cik: int, cal: list[CalEntry], rp: ResolvedPeriod, comparison: str, basis: Optional[str],
               segment: Optional[str]) -> list[Obs]:
        base = (periods.shift(rp, cal, quarters=-1) if comparison in ("QoQ", "sequential")
                else periods.shift(rp, cal, years=-1))
        metric = "segment_revenue" if segment else "revenue"
        cur, prev = self.level(cik, metric, rp, basis, segment), self.level(cik, metric, base, basis, segment)
        return [Obs((c.value / p.value - 1.0) * 100.0, c.facts + p.facts) for c in cur for p in prev if p.value > 0]

    def margin(self, cik: int, rp: ResolvedPeriod, basis: Optional[str]) -> list[Obs]:
        out = self.level(cik, "gross_margin", rp, basis)  # a press-release "Gross margin 75.0%" cell
        revenue = self.level(cik, "revenue", rp, None)
        gross = self.level(cik, "gross_profit", rp, basis)
        out += [Obs(g.value / r.value * 100.0, g.facts + r.facts) for g in gross for r in revenue if r.value]
        if not gross and basis != "non-GAAP":  # Meta reports no gross profit line
            cost = self.level(cik, "cost_of_revenue", rp, None)
            out += [Obs((r.value - k.value) / r.value * 100.0, r.facts + k.facts) for r in revenue for k in cost
                    if r.value]
        return out

    def observe(self, rp: Optional[ResolvedPeriod], *, cik: Optional[int] = None,
                cal: Optional[list[CalEntry]] = None, basis: Any = "same", comparison: Optional[str] = None) -> list[Obs]:
        cik = self.cik if cik is None else cik
        cal = self.cal if cal is None else cal
        basis = self.basis if basis == "same" else basis
        if rp is None or rp.period_end is None:
            return []
        if self.metric == "revenue_growth":
            return self.growth(cik, cal, rp, comparison or self.c.comparison, basis, self.segment)
        if self.metric == "gross_margin":
            return self.margin(cik, rp, basis)
        return self.level(cik, self.metric, rp, basis, self.segment)

    def matches(self, obs: Iterable[Obs], claimed: Optional[float] = None, tol: Optional[float] = None) -> list[Obs]:
        claimed = self.claimed if claimed is None else claimed
        tol = self.tol if tol is None else tol
        return [o for o in obs if within(claimed, o.value, tol)]

    # -- the rules -------------------------------------------------------------
    def run(self) -> Verdict:
        c = self.c
        if not self._unit_fits():
            return self.verdict("unverifiable", "no_evidence",
                                f"Receipts checks {self.mlabel(False)} in {_unit_words(self.metric)}, and this figure "
                                f"is written in {_unit_words_of(c.unit)}.")
        if self.metric == "segment_revenue" and not self.segment:
            return self.verdict("unverifiable", "ambiguous", "The draft doesn't say which segment this revenue is for.")
        level_claim = self.metric != "revenue_growth"
        rp = periods.resolve_in_calendar(c.period_text, self.cal, context=self.context, period_type=c.period_type,
                                         comparison=c.comparison if level_claim else "YoY")
        if rp is None:
            said = c.period_text.strip() or "no period"
            return self.verdict("unverifiable", "ambiguous",
                                f"Couldn't tell which {self.company} fiscal period \"{said}\" refers to.")
        if self.metric == "revenue_growth" and (c.comparison == "none" or rp.fiscal_period == "FY"):
            return self._growth_unstated(rp)
        obs_all = self.observe(rp)
        if not obs_all:
            return self._no_evidence(rp)
        pub = [o for o in obs_all if self.public(o)]
        if not pub:
            return self._look_ahead(rp, obs_all)
        hits = self.matches(pub)
        if hits:
            return self._verified(rp, hits)
        for check in (self._hedged, self._comparison_base):
            v = check(rp, pub)
            if v is not None:
                return v
        actual = _current_obs(pub)
        for diagnose in (self._diag_period, self._diag_scale, self._diag_basis, self._diag_entity,
                         self._bank_headline):
            v = diagnose(rp, actual)
            if v is not None:
                return v
        return self.wrong("value_mismatch", actual,
                          f"{self.co_s} {form_phrase(actual.primary)} reports that "
                          f"{self.statement(actual, rp)}, not {c.value_text}.")

    def _unit_fits(self) -> bool:
        u = self.c.unit
        if self.metric in USD_METRICS:
            return u == "USD"
        if self.metric in PER_SHARE_METRICS:
            return u in ("USD/share", "USD")
        if self.metric in PERCENT_METRICS:
            return u == "%"
        return True

    def _verified(self, rp: ResolvedPeriod, hits: list[Obs], *, note: str = "") -> Verdict:
        best = _best_obs(hits)
        return self.verdict("verified", "match",
                            f"Matches {self.co_s} {form_phrase(best.primary)}: {self.statement(best, rp)}{note}.",
                            evidence=[f for o in hits for f in o.facts])

    def _no_evidence(self, rp: ResolvedPeriod) -> Verdict:
        if rp.period_end is None:
            text = f"No {self.company} filing covers {self.plabel(rp)} yet"
        elif self.basis == "non-GAAP":
            text = (f"No press-release fact for {self.co_s} non-GAAP {self.mlabel(False)} for {self.plabel(rp)} "
                    f"(non-GAAP figures come only from earnings releases)")
        else:
            text = f"No filing fact for {self.co_s} {self.mlabel()} for {self.plabel(rp)}"
        hint = self._other_period_match(rp)
        if hint is not None:
            orp, o = hint
            text += f"; {self.c.value_text} matches its {self.mlabel()} for {self.plabel(orp)}"
            return self.verdict("unverifiable", "no_evidence", text + ".", evidence=o.facts)
        if self.basis == "non-GAAP" and rp.period_end is not None:  # GAAP and non-GAAP often coincide: a hint only
            gaap = [o for o in self.matches(self.observe(rp, basis="GAAP")) if self.public(o)]
            if gaap:
                text += f"; {self.c.value_text} matches its GAAP {self.mlabel(False)}"
                return self.verdict("unverifiable", "no_evidence", text + ".", evidence=_best_obs(gaap).facts)
        return self.verdict("unverifiable", "no_evidence", text + ".")

    def earnings_release(self, rp: ResolvedPeriod) -> Optional[dict[str, Any]]:
        """The earliest Item 2.02 8-K accepted within EARNINGS_RELEASE_WINDOW_DAYS after the period end, from
        the store's filings table (receipts.store.Store.filings; not part of the EvidenceStore protocol)."""
        filings = getattr(self.store, "filings", None)
        if filings is None or rp.period_end is None:
            return None
        try:
            rows = filings(self.cik, forms=["8-K", "8-K/A"])
        except Exception:  # an optional extra: never let it break a verdict
            log.debug("store.filings failed", exc_info=True)
            return None
        best = None
        for r in rows or []:
            if "2.02" not in str(r.get("items") or ""):
                continue
            at = to_utc(r["accepted_at"])
            if 0 < (at.date() - rp.period_end).days <= EARNINGS_RELEASE_WINDOW_DAYS and (best is None or at < best[0]):
                best = (at, r)
        return best[1] if best else None

    def _look_ahead(self, rp: ResolvedPeriod, obs_all: list[Obs]) -> Verdict:
        """Nothing for this figure was public at as_of. It is look-ahead only if the figure's first public moment
        (its earliest fact, or the earnings 8-K for the period if that came first) is after as_of. Without a
        press-release fact the earliest fact is the 10-Q, which banks file weeks after the 8-K: if the 8-K was
        already out, or it can't be dated, the claim is unverifiable rather than wrong."""
        assert self.as_of is not None
        first = min(obs_all, key=lambda o: o.public_at)
        binding = max(first.facts, key=lambda f: to_utc(f.accepted_at))
        release = self.earnings_release(rp)
        release_at = to_utc(release["accepted_at"]) if release else None
        has_release_fact = any(f.source == "press_release" for o in obs_all for f in o.facts)
        value_ok = bool(self.matches(obs_all))
        filed = _current_obs(obs_all)
        what = f"{self.co_s} {self.mlabel()} for {self.plabel(rp)}"
        if release_at is not None and release_at <= first.public_at:
            if self.as_of < release_at:
                text = (f"{what} wasn't public until its 8-K earnings release ({release['accession']}) was accepted "
                        f"at {fmt_ts(release_at)}, after the as-of time {fmt_ts(self.as_of)}")
            else:
                return self.verdict(
                    "unverifiable", "no_evidence",
                    f"{self.co_s} earnings release for {self.plabel(rp)} (8-K accepted {fmt_ts(release_at)}) was "
                    f"out by the as-of time {fmt_ts(self.as_of)}, but Receipts has no press-release figure for its "
                    f"{self.mlabel()}, and its {form_phrase(binding)} came later, at {fmt_ts(binding.accepted_at)}.")
        elif has_release_fact or release_at is not None:
            text = (f"{what} wasn't public until its {form_phrase(binding)} was accepted at "
                    f"{fmt_ts(binding.accepted_at)}, after the as-of time {fmt_ts(self.as_of)}")
        else:
            return self.verdict(
                "unverifiable", "no_evidence",
                f"The first filing with {what} that Receipts holds is its {form_phrase(binding)}, accepted at "
                f"{fmt_ts(binding.accepted_at)}, after the as-of time {fmt_ts(self.as_of)}, and the earnings release "
                f"that may have come earlier can't be dated.")
        if not value_ok:
            text += f", and the filed figure is {self.fmt(filed.value)}"
        return self.verdict("wrong", "look_ahead", text + ".", evidence=first.facts,
                            expected=None if value_ok else filed.value)

    def _growth_unstated(self, rp: ResolvedPeriod) -> Verdict:
        """A percent change whose base the draft doesn't name: right if either reading matches, wrong only if
        both readings have public evidence and neither matches, otherwise ambiguous."""
        readings = [("YoY", self.observe(rp, comparison="YoY"))]
        if rp.fiscal_period != "FY":
            readings.append(("QoQ", self.observe(rp, comparison="QoQ")))
        if not any(obs for _, obs in readings):
            return self._no_evidence(rp)
        public = [(cmp, [o for o in obs if self.public(o)]) for cmp, obs in readings]
        if not any(obs for _, obs in public):
            return self._look_ahead(rp, [o for _, obs in readings for o in obs])
        for cmp, obs in public:
            hits = self.matches(obs)
            if hits:
                best = _best_obs(hits)
                return self.verdict("verified", "match",
                                    f"Matches {self.co_s} {form_phrase(best.primary)}: "
                                    f"{self.statement(best, rp, cmp)}.", evidence=[f for o in hits for f in o.facts])
        parts = [f"{self.fmt(_current_obs(obs).value)} {_cmp_words(cmp)}" for cmp, obs in public if obs]
        if all(obs for _, obs in public):
            actual = _current_obs(public[0][1])
            return self.wrong("value_mismatch", actual,
                              f"{self.co_s} {self.mlabel()} for {self.plabel(rp)} was {' and '.join(parts)}, "
                              f"not {self.c.value_text}.", extra=[f for _, obs in public for o in obs for f in o.facts])
        return self.verdict("unverifiable", "ambiguous",
                            f"The draft doesn't say what the {self.c.value_text} change is measured against, and "
                            f"{self.co_s} {self.mlabel()} for {self.plabel(rp)} was {' and '.join(parts)}.",
                            evidence=[f for _, obs in public for o in obs for f in o.facts])

    def _hedged(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        i = self.c.span_text.find(self.c.value_text)
        before = self.c.span_text[:i].lower()[-30:] if i >= 0 else ""
        if _APPROX_RE.search(before):
            band = max(2 * self.tol, 0.01 * abs(self.claimed))
            near = self.matches(pub, tol=band)
            if near:
                best = _best_obs(near)
                return self.verdict("approximate", "approximate",
                                    f"Close enough for an approximation: {self.co_s} {form_phrase(best.primary)} "
                                    f"reports that {self.statement(best, rp)}.", evidence=best.facts)
        bound = "lower" if _LOWER_RE.search(before) else "upper" if _UPPER_RE.search(before) else None
        if bound:
            ok = [o for o in pub if (o.value >= self.claimed - self.tol if bound == "lower"
                                     else o.value <= self.claimed + self.tol)]
            if ok:
                best = _best_obs(ok)
                word = "more" if bound == "lower" else "less"
                return self.verdict("verified", "match",
                                    f"Matches {self.co_s} {form_phrase(best.primary)}: "
                                    f"{self.statement(best, rp)}, which is {word} than {self.c.value_text}.",
                                    evidence=best.facts)
        return None

    def _comparison_base(self, rp: ResolvedPeriod, pub: list[Obs]) -> Optional[Verdict]:
        """'up from $46.7 billion a year ago': a level figure for the comparison period the sentence names."""
        if self.c.comparison != "none" or self.metric == "revenue_growth":
            return None
        words = self.c.span_text.lower()
        shifted = []
        if _YEAR_AGO_RE.search(words):
            shifted.append(periods.shift(rp, self.cal, years=-1))
        if _PRIOR_Q_RE.search(words):
            shifted.append(periods.shift(rp, self.cal, quarters=-1))
        for srp in shifted:
            hits = [o for o in self.matches(self.observe(srp)) if self.public(o)]
            if hits and srp is not None:
                return self._verified(srp, hits, note=", the comparison period the sentence cites")
        return None

    def _other_period_match(self, rp: ResolvedPeriod) -> Optional[tuple[ResolvedPeriod, Obs]]:
        want_fy = rp.fiscal_period == "FY"
        for e in sorted(self.cal, key=lambda e: e.end, reverse=True):
            if (e.fiscal_period == "FY") != want_fy or (e.fiscal_year, e.fiscal_period) == rp[:2]:
                continue
            orp = ResolvedPeriod(e.fiscal_year, e.fiscal_period, e.end)
            hits = [o for o in self.matches(self.observe(orp)) if self.public(o)]
            if hits:
                return orp, _best_obs(hits)
        return None

    # -- diagnosis of a wrong value -----------------------------------------
    def _diag_period(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        c = self.c
        if self.metric == "revenue_growth":
            other = "YoY" if c.comparison in ("QoQ", "sequential") else "QoQ"
            hits = [o for o in self.matches(self.observe(rp, comparison=other)) if self.public(o)]
            if hits:
                return self.wrong("period_mismatch", actual,
                                  f"{c.value_text} is {self.co_s} {_cmp_words(other)} {self.mlabel()}; "
                                  f"{_cmp_words(c.comparison)} it was {self.fmt(actual.value)} in {self.plabel(rp)}.",
                                  extra=_best_obs(hits).facts)
        hint = self._other_period_match(rp)
        if hint is None:
            return None
        orp, o = hint
        return self.wrong("period_mismatch", actual,
                          f"{c.value_text} is {self.co_s} {self.mlabel()} for {self.plabel(orp)}; for "
                          f"{self.plabel(rp)} it was {self.fmt(actual.value)}.", extra=o.facts)

    def _diag_scale(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        if self.c.unit not in ("USD", "shares"):
            return None
        pub = [o for o in self.observe(rp) if self.public(o)]
        for factor, words in SCALE_SHIFTS:
            hits = self.matches(pub, claimed=self.claimed * factor, tol=self.tol * factor)
            if hits:
                best = _best_obs(hits)
                return self.wrong("scale_mismatch", best,
                                  f"{self.co_s} {form_phrase(best.primary)} reports that "
                                  f"{self.statement(best, rp)}, so {self.c.value_text} is off by a factor of "
                                  f"{words}.")
        return None

    def _diag_basis(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        other = "GAAP" if self.basis == "non-GAAP" else "non-GAAP"
        hits = [o for o in self.matches(self.observe(rp, basis=other)) if self.public(o)]
        if not hits:
            return None
        best = _best_obs(hits)
        label = self.mlabel(False)
        mine = self.basis or "GAAP"
        if not self.basis_stated:  # the draft never named a basis: reading it one way is a guess, not an error
            return self.verdict("unverifiable", "ambiguous",
                                f"{self.c.value_text} matches {self.co_s} {other} {label} for {self.plabel(rp)}, "
                                f"but the draft doesn't say {other}, and the {mine} figure is "
                                f"{self.fmt(actual.value)}.", evidence=[*best.facts, *actual.facts])
        return self.wrong("basis_mismatch", actual,
                          f"{self.c.value_text} is {self.co_s} {other} {label} for {self.plabel(rp)}; the "
                          f"{mine} figure is {self.fmt(actual.value)}.", extra=best.facts)

    def _bank_headline(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        """Bank revenue or net interest income with no basis stated may be the managed (FTE) figure, which only
        the earnings release carries: a GAAP mismatch is "?" until a press-release figure can rule that out."""
        if self.basis_stated or self.ticker not in BANK_TICKERS or self.metric not in BASIS_SENSITIVE_AT_BANKS:
            return None
        if any(self.public(o) for o in self.observe(rp, basis="non-GAAP")):
            return None  # both readings are on file and neither matched: a real mismatch
        return self.verdict("unverifiable", "ambiguous",
                            f"{self.co_s} {form_phrase(actual.primary)} reports that {self.statement(actual, rp)}, "
                            f"but bank headline {self.mlabel(False)} is often on a managed (fully taxable-equivalent) "
                            f"basis that only the earnings release can confirm.", evidence=actual.facts)

    def _diag_entity(self, rp: ResolvedPeriod, actual: Obs) -> Optional[Verdict]:
        c = self.c
        for ocik, _oticker, oname in context_entities(self.context, self.store):
            if ocik == self.cik:
                continue
            ocal = periods.calendar(self.store, ocik)
            orp = periods.resolve_in_calendar(c.period_text, ocal, context=self.context, period_type=c.period_type,
                                              comparison=c.comparison)
            if orp is None or orp.period_end is None:
                orp = periods.nearest(ocal, rp)
            if orp is None:
                continue
            hits = [o for o in self.matches(self.observe(orp, cik=ocik, cal=ocal)) if self.public(o)]
            if hits:
                return self.wrong("entity_mismatch", actual,
                                  f"{c.value_text} is {possessive(oname)} {self.mlabel()} for {self.plabel(orp, ocal)}; "
                                  f"{self.co_s} was {self.fmt(actual.value)}.", extra=_best_obs(hits).facts)
        return None


def _cmp_words(comparison: Optional[str]) -> str:
    return {"YoY": "year over year", "QoQ": "quarter over quarter", "sequential": "quarter over quarter"}.get(
        comparison or "", "")


def _unit_words(metric: str) -> str:
    return "percent" if metric in PERCENT_METRICS else "dollars per share" if metric in PER_SHARE_METRICS else "dollars"


def _unit_words_of(unit: str) -> str:
    return {"USD": "dollars", "%": "percent", "USD/share": "dollars per share", "shares": "shares"}.get(unit, unit)
