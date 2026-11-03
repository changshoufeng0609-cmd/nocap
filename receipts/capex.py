"""Capital expenditures: the cash a company paid for property and equipment in a period, checked from a draft's own words.

Evidence (receipts.ingest, metric capital_expenditures; evidence-only, outside extraction's vocabulary). Each 10-Q and
10-K's PaymentsToAcquirePropertyPlantAndEquipment ("Purchases of property and equipment" at Alphabet and Meta,
"Additions to property and equipment" at Microsoft), or PaymentsToAcquireProductiveAssets at a company that tags none in
the window, where it is the company's sole capex line (Amazon's "Purchases of property and equipment"). Never both,
never summed. 10-Qs report cash flows year to date, so quarters are derived as operating cash flow's are, with the same
restatement and period safeguards (Q2 = H1 - Q1, Q3 = 9M - H1, Q4 = FY - 9M) and the never-negative ones revenue's use.

Reading (read). Extraction proposes no capex metric (its vocabulary and cache keys are unchanged), so a figure is read
as capital expenditures only from its own words: "capital expenditures", "capex", "capital spending", "purchases of",
"additions to" or "payments for property (plant) and equipment", "property and equipment purchases", in its
metric_text; for a percent change whose metric_text names no measure, also as the subject right before its number. A
level is a dollar amount; a change is a percent ("capex rose 83%") or a dollar change its words make one ("up $27.4
billion"). A word that scopes the figure ("including finance leases", "AI", "maintenance", "growth", "net", "for data
centers") makes it "?": NoCap holds one capex figure per period, the cash paid, and can't tell what a qualified one is.

Verdicts (check). receipts.verify's own check, on the capex facts only: period and fiscal calendar, as-of and
look-ahead, written precision, hedges and bounds, and a change computed from one filing's two periods (or from filings
with no restatement between them). ✓ when the figure matches the filed cash figure at its written precision, or a change
matches the change computed from the filed figures. Never ✗: companies define capital expenditures in their own words
(Microsoft's "capital expenditures including finance leases", accrued purchases not yet paid), so a figure that differs
from the filed cash figure is "?" (no_evidence), naming that figure as its receipt; as a "?" for lack of evidence it lets
receipts.release_text confirm a figure the company's own earnings release states in a sentence. Look-ahead stays ◷.
Guidance stays guidance: receipts.verify answers a forward-looking figure before this check is reached.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from receipts import verify as _v
from receipts.periods import CalEntry, ResolvedPeriod
from receipts.types import Claim, EvidenceStore, Verdict

log = logging.getLogger(__name__)

METRIC = "capital_expenditures"
LABEL = "capital expenditures"
LINE = "cash paid for property and equipment"
DEFINITIONS = ("Companies define capital expenditures in their own ways (some include finance leases or purchases not "
               "yet paid), so NoCap doesn't call {figure} wrong.")

_PPE = r"(?:property|properties)(?:\s*,)?\s+(?:plant(?:\s*,)?\s+)?(?:and|&)\s+equipment|pp&e"
PHRASE_RE = re.compile(
    r"(?<![\w-])(?:capital[\s-]+expenditures?|cap[\s-]?ex(?:es)?|capital[\s-]+spend(?:ing)?"
    rf"|(?:purchases?|additions?|payments?)\s+(?:of|to|for)\s+(?:{_PPE})"
    rf"|(?:{_PPE})\s+(?:purchases|additions|spending|expenditures))(?![\w-])", re.I)
# Words right before the phrase that name a part or another measure of capex; framing word lists would pass them
_MODIFIER_RE = re.compile(r"\b(?:growth|maintenance|sustaining|expansion(?:ary)?|net|gross|incremental)[\s-]*$", re.I)
_NET_AFTER_RE = re.compile(r"^[\s,(]*net\b", re.I)  # "capital expenditures, net"
# The cash paid is what NoCap holds: "cash capital expenditures" names it
_OWN_WORDS = frozenset({"cash", "paid", "total", "company", "companywide", "company-wide", "overall", "its"})
# Reporting verbs and links between a capex subject and its number ("rose to", "came in at", "totaled", "hit")
_PREDICATE_RE = re.compile(
    r"\b(?:hit|hits|reach\w*|total\w*|came|come|comes|in\s+at|stood|amount\w*|ran|landed|ended|climb\w*|jump\w*"
    r"|surg\w*|soar\w*|rose|ris\w*|grew|grow\w*|increas\w*|decreas\w*|declin\w*|fell|fall\w*|dropp?\w*|eased"
    r"|was|were|is|are|been|being|be|of|at|to|by|up|down|nearly|almost|approximately|about|around|roughly|some"
    r"|more|less|than|over|under|just|spent|spend(?:s|ing)?|invest(?:s|ed|ing)?|record(?:s|ed)?|report(?:s|ed)?|posted"
    r"|clocked|accelerat\w*|expand\w*|doubl\w*|tripl\w*|higher|lower|year|over|quarter|sequentially|compared|with|from"
    r"|versus|vs|a|an|the|its|their|our|this|that|which|also|again|still|further)\b", re.I)
# Where the words after a number stop describing it: another clause, a comparison or a cause
_TAIL_BREAK_RE = re.compile(r"[;!?:]|\.(?!\d)|—|–|\b(?:and|but|while|whereas|compared|versus|vs|up|down|an?\s+increase"
                            r"|an?\s+decrease|from|as|driven|due|reflecting|led|amid|after|before|despite|year[\s-]+over"
                            r"|which|that)\b", re.I)
# A sentence that opens with an inclusion or exclusion clause, as receipts.verify's _LEADING_EXCLUSION_RE reads one
_LEADING_SCOPE_RE = re.compile(r"(?:^|[;:]|\.(?!\d))\s*[\"“(]?\s*(?P<words>(?:including|inclusive\s+of|net\s+of|excluding)\b"
                               r"[^,;:.]{1,60}?),[^,;:.]*$", re.I)
_TAIL_SCOPE_RE = re.compile(r"^[\s,(]*(?:including|excluding|inclusive|exclusive|net|for|on|in|toward|towards|related"
                            r"|tied|of|mostly|mainly|primarily|largely|plus|less)\b", re.I)


@dataclass(frozen=True)
class Read:
    """How a figure reads as capital expenditures: its kind ("level", "change" or "absolute") and the words beyond the
    capex phrase and framing that scope it (none for a plain figure)."""

    kind: str
    scope: tuple[str, ...]


def _words(text: str, company: set[str]) -> list[str]:
    """Words of a description beyond its capex phrases, periods, figures, framing and the company's own name."""
    text = re.sub(r"(?:'s|’s)\b", "  ", text or "")
    text = _v._without_period_phrases(text)
    text = PHRASE_RE.sub(lambda m: " " * len(m.group(0)), text)
    text = re.sub(_PPE, lambda m: " " * len(m.group(0)), text, flags=re.I)  # "spent $X on property and equipment"
    text = _v._FIGURE_RE.sub(lambda m: " " * len(m.group(0)) if re.search(r"\d", m.group(0)) else m.group(0), text)
    text = _PREDICATE_RE.sub(lambda m: " " * len(m.group(0)), text)
    return [w for w in _v._leftover_words(text, (0, 0), company, None) if w.casefold().strip(".") not in _OWN_WORDS]


def _modifiers(text: str) -> list[str]:
    """Words that name a part or another measure of capex, right before or (", net") after each capex phrase."""
    out = []
    for m in PHRASE_RE.finditer(text or ""):
        if hit := _MODIFIER_RE.search(text[:m.start()]):
            out.append(hit.group(0).strip(" -"))
        if _NET_AFTER_RE.match(text[m.end():]):
            out.append("net")
    return out


def _local_words(claim: Claim, line: Optional[_v.DraftLine], company: set[str]) -> list[str]:
    """Scoping words of the figure's own clause, read as receipts.verify reads a level's own words: from its quote (or,
    for a list item whose quote holds nothing before the number, its draft sentence), the subject attached to the
    nearest capex phrase before the number and the words between that phrase and the number when no other figure
    stands between them. From the draft sentence when given (a quote can stop short of them), else the quote: the words
    right after the number that scope it ("including finance leases", "for AI infrastructure"), and a leading inclusion
    clause ("Including finance leases, capital expenditures were ...")."""
    out: list[str] = []
    if (src := _v._lead_source(claim, line)) is not None:
        text, pos = src
        before = text[:pos]
        phrases = list(PHRASE_RE.finditer(before))
        if phrases:
            start, end = phrases[-1].span()
            breaks = [m.end() for m in re.finditer(r"[,;:()—–]|\.(?!\d)", before[:start])]
            breaks += [m.end() for m in _v._FIGURE_RE.finditer(_v._without_period_phrases(before[:start]))
                       if re.search(r"\d", m.group(0))]
            subject = before[max(breaks, default=0):end]
            out += _words(subject, company) + _modifiers(subject)
            between = before[end:]
            if not [m for m in _v._FIGURE_RE.finditer(_v._without_period_phrases(between))
                    if re.search(r"\d", m.group(0))]:  # else the phrase is another figure's: this one restates it
                out += _words(between, company) + (["net"] if _NET_AFTER_RE.match(between) else [])
    if line is not None:
        text, start, end = line.text, line.start, line.end
    elif (pos := _v._value_pos(claim)) is not None:
        text, start, end = claim.span_text, pos, pos + len(claim.value_text)
    else:
        return list(dict.fromkeys(out))
    after = text[end:]
    cut = _TAIL_BREAK_RE.search(after)
    tail = after[:cut.start() if cut else len(after)]
    figure = next((m for m in _v._FIGURE_RE.finditer(_v._without_period_phrases(tail)) if re.search(r"\d", m.group(0))),
                  None)
    tail = tail[:figure.start()] if figure else tail
    if scope := _TAIL_SCOPE_RE.match(tail):  # up to the phrase's own end: ", including finance leases, in the quarter"
        tail = tail[:scope.end()] + re.split(r"[,()]", tail[scope.end():], maxsplit=1)[0]
    else:
        tail = re.split(r"[,()]", tail, maxsplit=1)[0]
    if scope or PHRASE_RE.search(tail):
        out += _words(tail, company) + _modifiers(tail)
    if lead := _LEADING_SCOPE_RE.search(text[:start]):
        out += _words(lead.group("words"), company) or [lead.group("words").strip()]
    return list(dict.fromkeys(out))


def read(claim: Claim, line: Optional[_v.DraftLine] = None) -> Optional[Read]:
    """The claim as capital expenditures (module docstring), or None when its own words don't name capex."""
    mt = _v._without_markdown(claim.metric_text or "")
    company = _v._company_words(claim)
    if PHRASE_RE.search(mt):
        named = True
    elif claim.unit == "%" and _v._is_change(claim) and not _words(mt, company) and not _v._metric_phrases(mt):
        # a change whose metric_text names no measure: the subject right before its number, as receipts.verify reads one
        pos = _v._value_pos(claim)
        if pos is None:
            return None
        before = claim.span_text[:pos]
        hits = list(PHRASE_RE.finditer(before))
        tracked = _v._metric_phrases(before)
        if not hits or (tracked and tracked[-1][1] > hits[-1].start()):
            return None
        between = [m for m in _v._FIGURE_RE.finditer(before[hits[-1].end():]) if re.search(r"\d", m.group(0))]
        named = len(between) <= 1
    else:
        return None
    if not named:
        return None
    if claim.unit == "%":
        kind = "change" if _v._is_change(claim) and not _v._MARGIN_RE.search(mt) and not _v._SHARE_OF_RE.search(
            f"{mt} {claim.span_text}") else "share"
    elif claim.unit == "USD":
        kind = "absolute" if _v.absolute_change(claim) else "level"
    else:
        kind = "unit"
    others = [mt[a:b] for _, a, b in _v._metric_phrases(mt) if not any(a < p.end() and b > p.start() for p in
                                                                         PHRASE_RE.finditer(mt))]
    named_words = {w.casefold() for phrase in others for w in phrase.split()}
    scope = [*others, *(w for w in [*_words(mt, company), *_modifiers(mt), *_local_words(claim, line, company)]
                        if w.casefold() not in named_words)]
    return Read(kind, tuple(dict.fromkeys(w for w in scope if w)))


class _CapexCheck(_v._Check):
    """receipts.verify's check with capital expenditures' own labels."""

    def subject(self) -> str:
        return LABEL

    def mlabel(self, basis_word: bool = True) -> str:
        if self.growth_base:
            return f"{LABEL} {'change' if self.absolute else 'growth'}"
        return LABEL

    def statement(self, o: _v.Obs, rp: ResolvedPeriod, comparison: Optional[str] = None,
                  cal: Optional[list[CalEntry]] = None, *, filed_precision: bool = False) -> str:
        if self.growth_base:
            return super().statement(o, rp, comparison, cal, filed_precision=filed_precision)
        value = self.fmt_observation(o) if filed_precision else self.fmt(o.value)
        return f"{LABEL} ({LINE}) were {value} for {self.plabel(rp, cal)}"


def check(claim: Claim, store: EvidenceStore, *, as_of: Optional[datetime] = None, context: Optional[str] = None,
          named: Optional[Sequence[tuple[int, str, str]]] = None, entity: Optional[tuple[int, str]] = None,
          line: Optional[_v.DraftLine] = None, draft_period: bool = False) -> Optional[Verdict]:
    """The verdict for a figure its own words make capital expenditures (read), or None for any other figure. A failure
    here returns None, which leaves the figure to the rules it met before capex was checked."""
    try:
        return _check(claim, store, as_of=as_of, context=context, named=named, entity=entity, line=line,
                      draft_period=draft_period)
    except Exception:  # never let a capex rule break the check of a draft
        log.exception("capital-expenditures check failed for claim %s", claim.id)
        return None


def _check(claim: Claim, store: EvidenceStore, *, as_of: Optional[datetime], context: Optional[str],
           named: Optional[Sequence[tuple[int, str, str]]], entity: Optional[tuple[int, str]],
           line: Optional[_v.DraftLine], draft_period: bool) -> Optional[Verdict]:
    r = read(claim, line)
    if r is None:
        return None
    what = f" ({claim.metric_text})" if claim.metric_text else ""

    def unverifiable(reason: str, explanation: str) -> Verdict:
        return Verdict(claim_id=claim.id, status="unverifiable", reason=reason, explanation=explanation, as_of=as_of)

    if r.scope:
        return unverifiable("no_evidence",
                            f"The wording “{' '.join(r.scope)}” qualifies this capital expenditures figure{what}, and "
                            f"NoCap holds only the {LINE} a filing reports, so there is nothing to check "
                            f"{claim.value_text} against.")
    if r.kind in ("share", "unit"):
        return unverifiable("no_evidence",
                            f"NoCap checks capital expenditures in dollars, or their change, and {claim.value_text} is "
                            f"neither, so there is no filing fact to check it against.")
    own = entity or _v.resolve_claim_entity(claim, store, context)
    if own is None:
        return _v.unresolved_entity(claim, store, as_of)
    growth = METRIC if r.kind in ("change", "absolute") else None
    reading = _v.Reading(METRIC, growth, None, absolute=r.kind == "absolute")
    verdict = _CapexCheck(claim, store, (int(own[0]), str(own[1])), as_of, context, reading, named, line=line,
                          draft_period=draft_period).run()
    if verdict.status != "wrong" or verdict.reason == "look_ahead":
        return verdict
    # Never ✗: the filed figure is the cash paid, and the draft's may be defined otherwise
    return verdict.model_copy(update={
        "status": "unverifiable", "reason": "no_evidence", "expected_value": None,
        "explanation": f"{verdict.explanation.rstrip()} {DEFINITIONS.format(figure=claim.value_text)}"})
