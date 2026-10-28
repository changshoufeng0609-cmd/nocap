"""Independent, deterministic omission hints for text drafts.

This is a lexical scan of Arabic-number candidates, not a claim that every
financial assertion was found. A candidate is covered only by a uniquely located
claim value, regardless of its verifier verdict. Counts refer to occurrences,
so expanding one value into GAAP and non-GAAP claims does not double-count it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, TypedDict

from receipts.extract import scan_numbers
from receipts.types import Claim


class CoverageNumber(TypedDict):
    start: int
    end: int
    text: str


class CoverageSummary(TypedDict):
    version: int
    candidate_count: int
    covered_count: int
    excluded_count: int
    uncovered: list[CoverageNumber]


_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
          r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_YEAR = r"(?:19|20|21)\d{2}"
# These masks supplement the extraction scanner, without altering extraction or
# its model requests. A bare 1900..2100 is deliberately NOT a year mask: it could
# be employee count, revenue, etc. Ambiguous tokens remain candidates.
_DATE_MASKS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+{_YEAR}\b)?",
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?(?:,?\s+{_YEAR}\b)?",
    rf"\b{_MONTH}\.?\s+{_YEAR}\b",
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b",
    rf"\b(?:FY|CY)\s*{_YEAR}\b",
    rf"\b(?:fiscal|calendar)(?:\s+year)?(?:\s+ended)?\s+{_YEAR}\b",
    rf"\byear(?:\s+ended)?\s+{_YEAR}\b",
    rf"\b(?:in|during|since|through|until)\s+{_YEAR}\b",
    rf"\b{_YEAR}\s+(?:fiscal|calendar)\s+year\b",
    rf"\bQ\s*[1-4](?:\s+(?:of\s+)?(?:FY\s*)?{_YEAR})?\b",
    rf"\bquarter\s+[1-4](?:\s+(?:of\s+)?(?:FY\s*)?{_YEAR})?\b",
    rf"\b(?:first|second|third|fourth|[1-4](?:st|nd|rd|th))(?:\s+|-)quarter(?:\s+(?:of\s+)?{_YEAR})?\b",
))
_REFERENCE_MASKS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:Forms?\s+)?(?:10-[KQ]|8-K|20-F|6-K|S-[1348])(?:/A)?\b",
    r"\b(?:Items?|Exhibits?|Sections?|Rules?|Schedules?)\s+\d+(?:[.-]\d+)*(?:\([a-z0-9]+\))*\b",
    r"\b(?:CIK\s*[:#]?\s*\d{1,10}|\d{10}-\d{2}-\d{6})\b",
))


def _scan(text: str) -> list[dict[str, Any]]:
    # Equal-length lexical normalization lets the existing scanner recognize
    # leading decimals and lower-case short scales while preserving all offsets.
    # This is coverage-only: extraction requests and numeric parsing are untouched.
    normalized = re.sub(r"(?<![\w.,/:])\.(?=\d)", "0", text)
    normalized = re.sub(r"(?<=\d)[bmk]\b", lambda match: match.group().upper(), normalized)
    tokens = scan_numbers(normalized)
    for token in tokens:
        token["text"] = text[token["start"]:token["end"]]
        token["num"] = text[token["num_start"]:token["num_end"]]
    return tokens


def _plain_reference(text: str, token: dict[str, Any]) -> bool:
    """Explicit financial markers take precedence over a date-like context."""
    if token["kind"] != "number" or token["scale"]:
        return False
    if re.search(r"[€£¥]\s*$", text[:token["start"]]) or token["text"].endswith("x"):
        return False
    return not re.match(r"\s*(?:dollars?|euros?|pounds?|USD|EUR|GBP|shares?|employees?|people|"
                        r"customers?|stores?|units?)\b", text[token["end"]:], re.IGNORECASE)


def _value_cores(text: str, claims: Iterable[Claim]) -> set[tuple[int, int]]:
    """Numeric cores actually represented by unambiguous exact claim values."""
    covered: set[tuple[int, int]] = set()
    for claim in claims:
        start, end = claim.span_start, claim.span_end
        if not (0 <= start < end <= len(text)) or text[start:end] != claim.span_text:
            continue
        value = claim.value_text
        if not value or claim.span_text.count(value) != 1:
            continue
        numbers = _scan(value)
        if len(numbers) != 1:
            continue
        offset = start + claim.span_text.index(value)
        number = numbers[0]
        covered.add((offset + number["num_start"], offset + number["num_end"]))
    return covered


def _display_span(text: str, token: dict[str, Any]) -> CoverageNumber:
    start, end = token["start"], token["end"]
    # Preserve signs and accounting parentheses when showing the missed value.
    prefix = re.search(r"(?:(?:\(\s*)?[-−–]\s*|\(\s*|(?:US\$|\$|USD)\s*[-−–]\s*)$", text[:start])
    if prefix:
        start = prefix.start()
    if "(" in text[start:token["num_start"]] and text[end:end + 1] == ")":
        end += 1
    return {"start": start, "end": end, "text": text[start:end]}


def numeric_coverage(text: str, claims: Iterable[Claim]) -> CoverageSummary:
    """Compare text-number occurrences with extracted values; never emit verdicts.

    Positions use Python Unicode code points. The existing extraction scanner
    supplies numeric tokens; explicit date/identifier contexts are excluded.
    Number words, image content and numeric notation the scanner cannot parse
    (for example scientific notation) are outside this heuristic's scope.
    """
    cores = _value_cores(text, claims)
    dates = [(m.start(), m.end()) for pattern in _DATE_MASKS for m in pattern.finditer(text)]
    references = [(m.start(), m.end()) for pattern in _REFERENCE_MASKS for m in pattern.finditer(text)]
    candidates, covered, excluded = 0, 0, 0
    uncovered: list[CoverageNumber] = []
    for token in _scan(text):
        ns, ne = token["num_start"], token["num_end"]
        # Restore the old scanner's unconditional year/footnote exclusions.
        # Parenthesized numbers can be financial losses; unresolved cases should
        # remain visible. Context masks below handle actual dates and references.
        plain = _plain_reference(text, token)
        ignored = token["ignored"] not in (None, "year", "date", "footnote_marker", "doc_ref")
        ignored |= plain and any(start <= ns and ne <= end for start, end in references)
        ignored |= plain and "." not in token["num"] and (
            token["ignored"] == "date" or any(start <= ns and ne <= end for start, end in dates))
        if ignored:
            excluded += 1
            continue
        candidates += 1
        if (ns, ne) in cores:
            covered += 1
        else:
            uncovered.append(_display_span(text, token))
    return {"version": 1, "candidate_count": candidates, "covered_count": covered,
            "excluded_count": excluded, "uncovered": uncovered}


def validate_coverage(value: Any, text: str | None = None) -> bool:
    """Validate serialized coverage, optionally including exact source offsets.

    Legacy responses may omit coverage; callers must represent that as unavailable
    rather than silently treating it as a successful scan.
    """
    if not isinstance(value, dict) or set(value) != {
        "version", "candidate_count", "covered_count", "excluded_count", "uncovered"
    }:
        return False
    if type(value["version"]) is not int or value["version"] != 1:
        return False
    if any(type(value[key]) is not int or value[key] < 0
           for key in ("candidate_count", "covered_count", "excluded_count")):
        return False
    numbers = value["uncovered"]
    if not isinstance(numbers, list) or value["candidate_count"] != value["covered_count"] + len(numbers):
        return False
    last_end = 0
    for number in numbers:
        if not isinstance(number, dict) or set(number) != {"start", "end", "text"}:
            return False
        start, end, token_text = number["start"], number["end"], number["text"]
        if (type(start) is not int or type(end) is not int or not isinstance(token_text, str)
                or start < last_end or end <= start or len(token_text) != end - start):
            return False
        if text is not None and (end > len(text) or text[start:end] != token_text):
            return False
        last_end = end
    return True
