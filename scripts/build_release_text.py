#!/usr/bin/env python3
"""Build an offline, deterministic prose index of archived earnings exhibits.

Only companies, filings and fiscal_periods are read from SQLite (mode=ro).
No environment files, network clients, model clients or eval modules are used.
seq is zero-based over *all* prose units, including units without digits; gaps
are intentional. Table rows/cells (including layout tables) are never evidence.
The reporting period comes from the latest completed stored quarter, checked
against the release's opening quarter announcement. Later comparisons and
outlook sections are not document-period declarations. Consumers still need
to distinguish annual, comparison and forward-looking claims within a unit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from receipts import periods

# Normalization and abbreviation handling adapted from eval/build_corpus.py;
# deliberately kept local so the production index has no eval dependency.
ABBREVIATIONS = set("inc corp co ltd llc plc u.s u.k calif wash mr ms mrs dr no vs approx st jan feb mar apr jun jul aug sep sept oct nov dec e.g i.e etc n.a a.m p.m l.p est dept fig nos jr sr".split())
BOUNDARY = re.compile(r'[.!?][”"’\')\]]*\s+(?=[“"‘(\[]?[A-Z0-9$])')
BULLET = re.compile(r"[•◦▪●■]")
BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "section", "article", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}
ORDINAL = r"(?:first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:fiscal[\s-]+)?quarter"
YEAR = r"(?:\s+(?:of\s+)?(?:(?:fiscal\s+(?:year\s+)?|fy\s*)?['’]?\d{2,4}))?"
NAMED_QUARTER = re.compile(ORDINAL + YEAR, re.I)
NUM_QUARTERS = tuple(re.compile(rx.pattern, re.I) for rx in (periods._Q_NUM_RE, periods._NQ_RE))
END_QUARTER = re.compile(
    rf"\b(?:(?:{ORDINAL}|quarter|three[ -]months|3[ -]months)\s+end(?:ed|ing)\s+)"
    rf"(?:{periods._MON}\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})", re.I)
MONTH_QUARTER = re.compile(rf"\b{periods._MON}\.?[ -]+quarter(?:\s+(?:of\s+)?(?:fiscal\s+)?\d{{4}})?", re.I)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate({ord(c): None for c in "\u200b\ufeff\u00ad"})).strip()


def split_sentences(text: str) -> list[str]:
    result, start = [], 0
    for match in BOUNDARY.finditer(text):
        word = re.search(r"([A-Za-z.]+)$", text[start:match.start()])
        word = word.group(1).lower().rstrip(".") if word else ""
        if word in ABBREVIATIONS or re.fullmatch(r"[a-z]|(?:[a-z]\.)+[a-z]", word):
            continue
        result.append(text[start:match.end()].strip())
        start = match.end()
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def html_blocks(html: bytes) -> tuple[list[tuple[str, bool]], Counter]:
    """Ordered (text, in_list) blocks; preserve mixed inline/block text once.

    A table emits a boundary, so a colon before it cannot leak to a later list.
    SEC SGML wrappers are removed before parsing the HTML document.
    """
    raw = html.decode("utf-8", errors="replace")
    body = re.search(r"<TEXT\b[^>]*>(.*?)(?:</TEXT>|$)", raw, re.I | re.S)
    soup = BeautifulSoup(body.group(1) if body else raw, "lxml")
    counts = Counter(tables_skipped=len(soup.find_all("table")),
                     table_rows_skipped=len(soup.find_all("tr")),
                     table_cells_skipped=len(soup.find_all(["td", "th"])))
    for tag in soup.find_all(["script", "style", "head", "title"]):
        tag.decompose()
    blocks: list[tuple[str, bool]] = []
    buffer: list[str] = []

    def flush(in_list: bool) -> None:
        text = normalize_ws("".join(buffer))
        buffer.clear()
        if text:
            blocks.append((text, in_list))

    def walk(node, in_list=False):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            buffer.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name in {"table", "tr", "td", "th"}:
            flush(in_list)
            blocks.append(("", False))
            return
        if node.name == "br":
            if in_list:
                buffer.append(" ")
            else:
                flush(in_list)
            return
        block = node.name in BLOCK_TAGS and not re.search(r"display\s*:\s*inline(?:\s*;|\s*$)", node.get("style", ""))
        list_paragraph = block and in_list and node.name not in {"li", "ul", "ol"}
        if list_paragraph:
            buffer.append(" ")
            block = False
        if block:
            flush(in_list)
        child_list = in_list or node.name in {"li", "ul", "ol"}
        for child in node.children:
            walk(child, child_list)
        if block:
            flush(child_list)
        elif list_paragraph:
            buffer.append(" ")

    walk(soup)
    flush(False)
    return blocks, counts


def prose_units(blocks: list[tuple[str, bool]]) -> list[tuple[str, str]]:
    units, lead = [], ""
    for block, in_list in blocks:
        if not block:
            lead = ""
            continue
        parts = BULLET.split(block)
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            bullet = in_list or i > 0
            if bullet:
                sentences = [part]  # even a multi-sentence bullet stays one unit
            else:
                sentences = split_sentences(part)
            for text in sentences:
                units.append((text, lead if bullet else ""))
                if text.endswith(":"):
                    lead = text
                elif not bullet:
                    lead = ""
    return units


def exhibit_label(html: bytes, url: str) -> str | None:
    raw = html.decode("utf-8", errors="replace")
    header = re.search(r"<TYPE>\s*([^\s<]+)", raw, re.I)
    if header:
        label = header.group(1).upper()
        return label if re.fullmatch(r"EX-99(?:\.\d+)?", label) else None
    # Plain HTML archives sometimes retain the printed SEC exhibit label.
    printed = re.search(r"\b(?:EX-|Exhibit\s+)99(?:\.(\d+))?(?![\d.])", BeautifulSoup(raw[:12000], "lxml").get_text(" "), re.I)
    if printed:
        return "EX-99" + ("." + printed.group(1) if printed.group(1) else "")
    filename = Path(urlsplit(url).path).name
    match = re.search(r"(?:exhibit|ex)[-_]?99(?:[._-]?(\d{1,2}))?(?=[^\d]|$)", filename, re.I)
    if match:
        return "EX-99" + ("." + match.group(1) if match.group(1) else "")
    return None


def reporting_period(rows: list[dict], filed: str, blocks: list[tuple[str, bool]]) -> tuple[dict | None, str | None]:
    filing_date = date.fromisoformat(filed[:10])
    candidates = [r for r in rows if r["fiscal_period"] in periods.QUARTERS
                  and 0 < (filing_date - date.fromisoformat(r["period_end"])).days <= 120]
    if not candidates:
        return None, "no_recent_quarter"
    latest = max(r["period_end"] for r in candidates)
    candidates = [r for r in candidates if r["period_end"] == latest]
    if len(candidates) != 1:
        return None, "ambiguous_calendar"
    chosen = candidates[0]
    # Check the first named quarter and opening reporting statements. Do not
    # let historical comparisons, guidance or a dateline choose the quarter.
    declarations = []
    chars = 0
    for block, _ in blocks:
        chars += len(block)
        if chars > 6000:
            break
        if re.search(r"\b(?:outlook|guidance|forward-looking)\b", block, re.I):
            break
        announced = False
        for sentence in split_sentences(block):
            found = [(m.start(), m.group()) for rx in (NAMED_QUARTER, *NUM_QUARTERS, END_QUARTER, MONTH_QUARTER)
                     for m in rx.finditer(sentence)]
            if not found:
                continue
            announcement = re.search(r"\b(?:announc\w*|report\w*)\b.{0,180}\bresults\b|\bresults\b.{0,100}\b(?:quarter|q[1-4])\b", sentence, re.I)
            if not declarations or announcement:
                # A comparison clause isn't another reporting declaration.
                opening = re.split(r"\b(?:compar\w*|versus|vs\.|last year|prior year|sequential)\b", sentence, maxsplit=1, flags=re.I)[0]
                declarations.extend(phrase for pos, phrase in sorted(found) if pos < len(opening))
                announced = bool(END_QUARTER.search(opening) or re.search(
                    r"\b(?:announced|reported)\b.{0,180}\bresults\b", opening, re.I))
            if announced:
                break
        if announced:
            break
    if not declarations:
        return None, "quarter_not_named"
    for phrase in declarations:
        parsed = periods.parse(phrase)
        if parsed.end is not None and parsed.end.isoformat() != chosen["period_end"]:
            return None, "quarter_mismatch"
        if parsed.quarter is not None and f"Q{parsed.quarter}" != chosen["fiscal_period"]:
            return None, "quarter_mismatch"
        if parsed.year is not None and parsed.end is None:
            # A company's own numbered quarter is fiscal; a month-named quarter
            # without FY uses the calendar year of its end.
            year = date.fromisoformat(chosen["period_end"]).year if parsed.month and not parsed.fiscal else chosen["fiscal_year"]
            if parsed.year != year:
                return None, "quarter_mismatch"
        if parsed.month is not None and parsed.month != periods._nominal(date.fromisoformat(chosen["period_end"])).month:
            return None, "quarter_mismatch"
    return chosen, None


def _json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def build(archive: Path, database: Path, output: Path) -> dict:
    index_bytes = (archive / "index.json").read_bytes()
    index = json.loads(index_bytes)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        companies = {r["cik"]: dict(r) for r in db.execute("SELECT cik,ticker FROM companies")}
        filings = {r["accession"]: dict(r) for r in db.execute("SELECT * FROM filings")}
        calendars = defaultdict(list)
        calendar_rows = [dict(r) for r in db.execute("SELECT * FROM fiscal_periods ORDER BY cik,fiscal_year,fiscal_period")]
        for row in calendar_rows:
            calendars[row["cik"]].append(row)
    per_company = defaultdict(list)
    skipped, totals, company_docs = Counter(), Counter(), Counter()
    skip_details = []
    for url, entry in sorted(index.items()):
        reason = None
        filing = filings.get(entry["accession"])
        if filing is None:
            reason = "filing_not_in_snapshot"
        elif filing["cik"] != entry["cik"]:
            reason = "cik_mismatch"
        elif filing["form"] != "8-K" or not re.search(r"(?<![\d.])2\.02(?![\d.])", filing["items"] or ""):
            reason = "not_item_2_02_8k"
        elif entry["cik"] not in companies:
            reason = "company_not_in_snapshot"
        if reason is None:
            key = entry["key"]
            if not re.fullmatch(r"[a-f0-9]{64}", key):
                raise ValueError("Invalid archive key")
            html = gzip.decompress((archive / (key + ".gz")).read_bytes())
            if hashlib.sha256(html).hexdigest() != entry["sha256"]:
                raise ValueError(f"Archive content hash mismatch: {url}")
            exhibit = exhibit_label(html, url) if not html.startswith((b"\xff\xd8", b"\x89PNG", b"%PDF", b"GIF8")) else None
            if html.startswith((b"\xff\xd8", b"\x89PNG", b"%PDF", b"GIF8")):
                reason = "non_html"
            elif exhibit is None:
                reason = "not_ex99"
            else:
                blocks, counts = html_blocks(html)
                totals.update(counts)
                period, reason = reporting_period(calendars[entry["cik"]], filing["filed"], blocks)
                if reason is None:
                    accepted = filing.get("accepted_at") or entry.get("accepted_at")
                    if not accepted:
                        reason = "missing_acceptance"
                    else:
                        timestamp = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            reason = "ambiguous_acceptance"
        if reason is None:
            ticker = companies[entry["cik"]]["ticker"]
            metadata = dict(ticker=ticker, cik=entry["cik"], accession=entry["accession"],
                            exhibit=exhibit, url=url,
                            accepted_at=timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            **{k: period[k] for k in ("fiscal_year", "fiscal_period", "period_start", "period_end")})
            units = prose_units(blocks)
            records = [dict(metadata, seq=i, text=text, lead=lead) for i, (text, lead) in enumerate(units)
                       if re.search(r"\d", text)]
            if not records:
                reason = "no_numeric_prose"
            else:
                per_company[ticker].extend(records)
                company_docs[ticker] += 1
                totals["documents"] += 1
                totals["sentences"] += len(records)
                totals["units_without_digits"] += len(units) - len(records)
        if reason:
            skipped[reason] += 1
            skip_details.append(dict(url=url, accession=entry["accession"], reason=reason))
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for ticker, records in sorted(per_company.items()):
        records.sort(key=lambda r: (r["accession"], r["url"], r["seq"]))
        payload = b"".join(_json(record) for record in records)
        # GzipFile suppresses the path-dependent filename header and fixes OS.
        path = output / f"{ticker}.jsonl.gz"
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as stream:
                stream.write(payload)
        files[path.name] = dict(documents=company_docs[ticker], sentences=len(records),
                                bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    for stale in output.glob("*.jsonl.gz"):
        if stale.name not in files:
            stale.unlink()
    manifest = dict(schema_version=1, companies=len(files), **dict(totals),
                    archive_documents=len(index), documents_skipped=sum(skipped.values()),
                    skipped_by_reason=dict(sorted(skipped.items())), skipped_documents=skip_details,
                    source_index_sha256=hashlib.sha256(index_bytes).hexdigest(),
                    builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    period_helpers_sha256=hashlib.sha256(Path(periods.__file__).read_bytes()).hexdigest(),
                    source_metadata_sha256=hashlib.sha256(_json([companies, filings, calendar_rows])).hexdigest(),
                    gzip_bytes=sum(f["bytes"] for f in files.values()), files=files)
    (output / "manifest.json").write_bytes(_json(manifest))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Read-only exhibit archive containing index.json")
    parser.add_argument("--database", type=Path, default=ROOT / "data/snapshot/facts.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data/snapshot/release_text")
    args = parser.parse_args()
    manifest = build(args.archive, args.database, args.output)
    print(json.dumps({k: v for k, v in manifest.items() if k not in {"files", "skipped_documents"}}, indent=2))


if __name__ == "__main__":
    main()
