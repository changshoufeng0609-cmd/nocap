"""Offline source-only screening for spot500; no verifier or extraction imports.

Only the snapshot's company/filing/calendar metadata is read. Numerical screening
uses archived SEC companyfacts, never NoCap's fact values or verdicts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, timedelta
import gzip
import hashlib
import html as html_module
import json
from pathlib import Path
import re
import sqlite3
from unittest.mock import patch

from eval import build_corpus as base

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = ROOT / "eval/evidence_expansion/sp500-aliases-20261031.json"
EXCLUDED = frozenset("AAPL AMD AMZN BAC GOOGL JPM META MSFT NFLX NVDA TSLA".split())
IRREGULAR = frozenset("COST KR DPZ PEP AZO".split())
BALANCE = frozenset(("cash_and_equivalents", "total_assets", "total_debt", "shares_outstanding"))
MARGINS = frozenset(("gross_margin", "operating_margin", "net_margin"))
ADDED_KEYWORDS = [
    ("other", r"(?:adjusted\s+)?EBITDA(?:\s+margin)?"),
    ("other", r"\binterest expense\b|stock[- ]based compensation expense|\bnet debt\b|"
              r"\bshares? repurchases?\b|\brepurchase of shares\b"),
    ("operating_income", r"operating profit"),
    ("net_income", r"\bearnings\b(?!\s+per\b)"),
    ("net_margin", r"net (?:profit |income )?margin"),
    ("cash_and_equivalents", r"cash and cash equivalents"),
    ("total_assets", r"total assets"),
    ("total_debt", r"total debt"),
    ("shares_outstanding", r"(?:common )?shares (?:were )?outstanding"),
]
INSTANT_CONCEPTS = {
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "total_assets": ("Assets",),
    "total_debt": ("DebtLongtermAndShorttermCombinedAmount",),
    "shares_outstanding": ("CommonStockSharesOutstanding",),
}
_FIND = base.find_numbers
_TAG = base.tag_numbers
_EXPECTED = base.expected_for
_WITHIN = base.within_tolerance
_OTHER = base.other_numeric
SHARE_NUMBER = re.compile(r"(?<![\w.$])(?P<num>" + base.NUM + r")\s*(?P<scale>billion|million|thousand)?\b", re.I)
WRITTEN_QUANTITY = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?\s+"
    r"(?:percent(?:age points?)?|per cent|basis points?|bps|dollars?|cents?|million|billion|trillion|thousand)\b", re.I)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def series_from_companyfacts(facts: dict, *, irregular: bool = False) -> dict:
    """Stage-1 quarter/YTD arithmetic, extended to fiscal 12/16/17-week quarters.

    Pick the latest filed observation for each exact duration. The longer window
    is only enabled for the five explicitly requested variable-week companies.
    """
    lower, upper = (75, 125) if irregular else (80, 100)
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = {}
    for metric, (concepts, unit, merge) in base.CONCEPTS.items():
        merged = {}
        for concept in concepts:
            durations = {}
            for row in gaap.get(concept, {}).get("units", {}).get(unit, []):
                if row.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A") or not row.get("start"):
                    continue
                if row["end"] < "2023-01-01":
                    continue
                key = row["start"], row["end"]
                candidate = (row.get("filed", ""), row.get("accn", ""), float(row["val"]))
                if key not in durations or candidate[:2] > durations[key][:2]:
                    durations[key] = candidate
            quarters = {end: value for (start, end), (_, _, value) in sorted(durations.items())
                        if lower <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= upper}
            if metric not in base.NON_ADDITIVE:
                for (start, end), (_, _, value) in sorted(durations.items()):
                    if end in quarters or (date.fromisoformat(end) - date.fromisoformat(start)).days <= upper:
                        continue
                    priors = [(prior_end, pv) for (prior_start, prior_end), (_, _, pv) in durations.items()
                              if prior_start == start and lower <= (date.fromisoformat(end) - date.fromisoformat(prior_end)).days <= upper]
                    if priors:
                        quarters[end] = value - sorted(priors)[-1][1]
            for end, value in quarters.items():
                if date.fromisoformat(end) < base.SERIES_FROM:
                    continue
                if end not in merged or (merge == "max" and value > merged[end]):
                    merged[end] = value
        if merged:
            out[metric] = dict(sorted(merged.items()))
    # Standard bank total revenue: net interest income + noninterest income.
    for end, nii in out.get("net_interest_income", {}).items():
        noninterest = out.get("noninterest_revenue", {}).get(end)
        if noninterest is not None:
            out.setdefault("revenue", {}).setdefault(end, nii + noninterest)
    for end, revenue in out.get("revenue", {}).items():
        if not revenue:
            continue
        gross = out.get("gross_profit", {}).get(end)
        if gross is None and end in out.get("cost_of_revenue", {}):
            gross = revenue - out["cost_of_revenue"][end]
        for metric, value in (("gross_margin", gross),
                              ("operating_margin", out.get("operating_income", {}).get(end)),
                              ("net_margin", out.get("net_income", {}).get(end))):
            if value is not None:
                out.setdefault(metric, {})[end] = 100 * value / revenue
    for end, rep in out.get("repurchases", {}).items():
        if end in out.get("dividends_paid", {}):
            out.setdefault("capital_returned", {})[end] = rep + out["dividends_paid"][end]
    for metric, concepts in INSTANT_CONCEPTS.items():
        latest = {}
        for concept in concepts:
            unit = "shares" if metric == "shares_outstanding" else "USD"
            for row in gaap.get(concept, {}).get("units", {}).get(unit, []):
                if row.get("start") or row.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                    continue
                key = row.get("filed", ""), row.get("accn", "")
                if row["end"] not in latest or key > latest[row["end"]][0]:
                    latest[row["end"]] = (key, float(row["val"]))
        out[metric] = {end: pair[1] for end, pair in sorted(latest.items()) if date.fromisoformat(end) >= base.SERIES_FROM}
    return out


def find_numbers(text):
    numbers = _FIND(text)
    metadata_spans = [(m.start(), m.end()) for rx in (base.DATE_RE, base.YEAR_RE, base.QLABEL_RE, base.FOOTNOTE_RE)
                      for m in rx.finditer(text)]
    if re.search(r"shares (?:were )?outstanding", text, re.I):
        for match in SHARE_NUMBER.finditer(text):
            if any(match.start() < n.end and match.end() > n.start for n in numbers):
                continue
            if any(match.start() < right and match.end() > left for left, right in metadata_spans):
                continue
            window = text[max(0, match.start() - 50):match.end() + 50]
            if not re.search(r"shares (?:were )?outstanding", window, re.I):
                continue
            value = match.group("num")
            if len(value) == 4 and 1900 <= float(value) <= 2100:
                continue
            word = match.group("scale")
            numbers.append(base.Num(start=match.start(), end=match.end(), text=match.group(),
                dstart=match.start("num"), dend=match.end("num"), kind="shares", value=float(value.replace(",", "")),
                decimals=base._decimals(value), scale=base.SCALE_OF.get(word.lower() if word else "", "units"),
                scale_start=match.start("scale") if word else -1, scale_end=match.end("scale") if word else -1,
                thousands_sep="," in value))
    return sorted(numbers, key=lambda n: n.start)


def tag_numbers(text, nums, forward, cur=None):
    _TAG(text, nums, forward, cur)
    for i, n in enumerate(nums):
        left = text[max(0, n.start - 50):n.start]
        right = text[n.end:nums[i + 1].start if i + 1 < len(nums) else len(text)]
        # A value followed by "per share" is EPS even when a broader net-income
        # keyword precedes it. Resolve the nearest explicit basis locally too.
        if n.kind == "money" and re.match(r"\s*per (?:diluted )?(?:common )?share\b", right, re.I):
            n.metric, n.qualifier = "eps_diluted", None
        if not re.search(r"GAAP and non-GAAP", text, re.I):
            basis_words = list(re.finditer(r"non-GAAP|(?<!non-)\bGAAP\b|\badjusted\b|\bmanaged\b", text[:n.start], re.I))
            if basis_words and n.start - basis_words[-1].end() <= 80:
                n.basis = "GAAP" if basis_words[-1].group().lower() == "gaap" else "non-GAAP"
        if n.kind == "shares":
            n.metric = "shares_outstanding"
        if n.metric in MARGINS and n.kind in ("pp", "bps"):
            n.growth = bool(re.search(r"\b(?:expanded?|contracted?|increased?|decreased?|rose|fell|improved?|declined?|up|down|higher|lower)\b", left, re.I))
            if re.search(r"\b(?:contracted?|decreased?|fell|declined?|down|lower)\b[^;]*$", left, re.I):
                n.sign = -1
        if n.growth and n.comparison is None:
            if re.search(r"year[- ]over[- ]year|\bYoY\b|prior.year|last year|year.ago", text, re.I):
                n.comparison = "YoY"
            elif re.search(r"quarter[- ]over[- ]quarter|\bQoQ\b|sequential", text, re.I):
                n.comparison = "QoQ"
        if i and n.basis is None:
            prior = nums[i - 1]
            connector = text[prior.end:n.start]
            if (n.metric == prior.metric and prior.basis == "non-GAAP" and ";" not in connector
                    and not re.search(r"(?<!non-)\bGAAP\b", connector, re.I)):
                n.basis = "non-GAAP"


def expected_for(n, series, end, *, growth_comparison=None, shift_days=0):
    if n.qualifier or n.basis == "non-GAAP" or n.fx_adjusted or n.is_range:
        return None
    at = end + timedelta(days=shift_days) - timedelta(days=364 if n.role == "prior" else 0)
    if n.metric in BALANCE:
        # Instant values must match the balance-sheet date exactly.
        if n.growth or n.kind in ("pct", "pp", "bps"):
            return None
        return series.get(n.metric, {}).get(at.isoformat())
    if n.metric in MARGINS:
        values = series.get(n.metric, {})
        current = base.series_at(values, at)
        if n.kind == "pct" and not n.growth:
            return current
        if n.kind == "pct" and n.growth:
            # "Margin rose 2%" does not distinguish points from relative growth.
            return None
        if n.kind in ("pp", "bps") and n.growth:
            comparison = growth_comparison or n.comparison or "YoY"
            prior = base.series_at(values, at, days=-364 if comparison == "YoY" else -91)
            return None if current is None or prior is None else (current - prior) * (100 if n.kind == "bps" else 1)
    return _EXPECTED(n, series, end, growth_comparison=growth_comparison, shift_days=shift_days)


def within_tolerance(n, claimed, actual):
    if n.kind == "shares":
        return abs(claimed - actual) <= 0.5 * 10 ** (-n.decimals) * base.SCALE_FACTOR[n.scale] * 1.0001 + 1e-9
    return _WITHIN(n, claimed, actual)


def screen_number(n, series, end):
    actual = expected_for(n, series, end)
    if actual is None:
        n.screen, n.screen_detail = "na", None
        return
    claimed = n.scaled() if n.kind in ("money", "shares") else n.sign * n.value
    n.screen = "ok" if within_tolerance(n, claimed, actual) else "mismatch"
    n.screen_detail = f"xbrl={actual:.9g}"


@contextmanager
def screening_environment(company):
    names = set(base.GENERIC_WORDS)
    # Basis adjectives do not end the search for an earlier segment name:
    # 'MIS adjusted operating margin' and 'Asset Management adjusted revenue'.
    names.add('adjusted')
    for name in (company["name"], company["sec_title"], company["ticker"]):
        names.update(w.lower().strip(",.") for w in name.split())
    with patch.multiple(base, find_numbers=find_numbers, tag_numbers=tag_numbers,
                        expected_for=expected_for, screen_number=screen_number,
                        _METRIC_RX=[(m, re.compile(rx, re.I)) for m, rx in ADDED_KEYWORDS] + base._METRIC_RX,
                        VOCAB_METRICS=base.VOCAB_METRICS | BALANCE | MARGINS,
                        QUALIFIABLE=base.QUALIFIABLE | BALANCE | MARGINS,
                        GENERIC_WORDS=names):
        yield


def analyze_sentence(text, series, end, cur=None):
    # Reuse every stage-1 gate; shares are additionally counted as financial numbers.
    analysis = base.analyze_sentence(text, series, end, cur)
    nums = analysis["numbers"]
    if WRITTEN_QUANTITY.search(text):
        analysis["reasons"].append("written financial quantity is outside the numeric tagger")
    without_dates = base.DATE_RE.sub(" ", text)
    without_quarter_months = re.sub(base.MONTHS + r"\s+quarter", " ", without_dates)
    if re.search(r"\b(?:monthly|for the month)\b", text, re.I) or re.search(r"\b" + base.MONTHS + r"\b", without_quarter_months):
        analysis["reasons"].append("monthly statement is outside the paired quarter")
    if (re.search(r"\brespectively\b", text, re.I) and len(nums) > 1
            and len({(n.metric, n.basis, n.role, n.qualifier) for n in nums}) == 1
            and not re.search(r"\bQ[1-4]\b|\b[1-4]Q\d{2}\b|\b(?:first|second|third|fourth)[ -]quarter\b|\b(?:19|20)\d{2}\b", text, re.I)):
        analysis["reasons"].append("respectively figures lack distinct local periods or measures")
    for i, n in enumerate(nums):
        if n.metric != "eps_diluted" or n.kind != "money":
            continue
        before = text[nums[i - 1].end if i else 0:n.start]
        after = text[n.end:nums[i + 1].start if i + 1 < len(nums) else len(text)]
        if n.scale != "units":
            analysis["reasons"].append("scaled currency amount is not a per-share level")
        if n.decimals > 2:
            analysis["reasons"].append("EPS precision may contain a glued numeric footnote")
        if (re.search(r"\b(?:includ(?:e[sd]?|ing)|exclud(?:e[sd]?|ing)|impact|benefit)\b[^;]*$", before, re.I)
                or re.search(r"\b(?:reduced?|increased?|decreased?|lowered?|raised?)\b[^;]*\bby\s*$", before, re.I)
                or re.match(r"\s+(?:favorable|unfavorable|positive|negative)?\s*(?:impact|benefit|charge|costs?)\b", after, re.I)):
            analysis["reasons"].append("EPS adjustment or impact is not a reported per-share level")
    shares = [n for n in nums if n.kind == "shares"]
    if shares:
        analysis["reasons"] = [r for r in analysis["reasons"] if not r.endswith("financial numbers")]
        if not 1 <= len(nums) <= 3:
            analysis["reasons"].append(f"{len(nums)} financial numbers")
        for n in shares:
            if n.screen != "ok" and n.role != "forward":
                analysis["reasons"].append("unmatched balance-sheet shares")
    if any(n.metric in BALANCE for n in nums):
        if any(n.metric in BALANCE and (n.growth or n.kind in ("pct", "pp", "bps")) for n in nums):
            analysis["reasons"].append("balance-sheet ratio or change is not an instant amount")
        if any(n.metric == "shares_outstanding" for n in nums) and re.search(r"fully diluted|weighted[- ]average|diluted shares", text, re.I):
            analysis["reasons"].append("diluted or weighted-average shares are not point-in-time common shares")
        for i, n in enumerate(nums):
            before = text[nums[i - 1].end if i else 0:n.start]
            if n.metric in BALANCE and re.search(r"\bby\s*$", before, re.I):
                analysis["reasons"].append("balance-sheet monetary delta is not an instant amount")
        dates = []
        for match in base.DATE_RE.finditer(text):
            try:
                written = match.group().replace(",", "").replace(".", "")
                if "/" in written:
                    month, day, year = map(int, written.split("/"))
                    year += 2000 if year < 100 else 0
                else:
                    month_text, day, year = written.split()
                    month = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec").index(month_text[:3].lower()) + 1
                dates.append(date(int(year), int(month), int(day)))
            except (ValueError, TypeError):
                analysis["reasons"].append("unparsed balance-sheet date")
        if dates and any(day != end for day in dates):
            analysis["reasons"].append("balance-sheet date differs from paired quarter")
    return analysis


def release_names_quarter(text: str, period: dict) -> bool:
    """Require the release opening to name the paired quarter or its end date."""
    opening = text[:8000]
    end = date.fromisoformat(period["period_end"])
    date_pattern = rf"\b{end.strftime('%B')}\s+0?{end.day},?\s+{end.year}\b"
    if re.search(date_pattern, opening, re.I):
        return True
    quarter = int(period["fiscal_period"][1])
    year = period["fiscal_year"]
    word = base.quarter_word(period["fiscal_period"])
    quarter_pattern = rf"(?:\b{word}[- ]quarter\b|\bQ{quarter}\b|\b{quarter}Q(?:{year}|{str(year)[2:]}))"
    return bool(re.search(quarter_pattern, opening, re.I) and re.search(rf"\b{year}\b", opening))


def is_exhibit_991(url: str, raw: bytes) -> bool:
    """SEC document header or an unambiguous exhibit filename establishes type."""
    header = base.normalize_ws(html_module.unescape(re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))))[:4000]
    marker = re.search(r"\b(?:EX[ -]?|Exhibit\s+)99[.-]([0-9]+)\b", header, re.I)
    if marker:
        return marker.group(1) == "1"
    return bool(re.search(r"99[._-]?1(?:\D|8-k|$)", url.split('/')[-1], re.I))


def load_sources(data_root: Path, *, tickers: set[str] | None = None, progress: bool = False):
    data_root = Path(data_root)
    universe = json.loads(UNIVERSE.read_text())["universe"]
    ex_path = data_root / "archive/exhibits/index.json"
    cf_path = data_root / "archive/companyfacts/index.json"
    xml_path = data_root / "archive/filing_xbrl/index.json"
    exhibits, cf_index, xml_index = (json.loads(p.read_text()) for p in (ex_path, cf_path, xml_path))
    provenance = {"indexes": {str(p.relative_to(data_root)): sha(p.read_bytes()) for p in (ex_path, cf_path, xml_path)},
                  "universe": {"path": str(UNIVERSE.relative_to(ROOT)), "sha256": sha(UNIVERSE.read_bytes()),
                               "stratum": "GICS sub_industry"}, "records": [], "screen_counts": {}}
    by_accession = defaultdict(list)
    for url, row in sorted(exhibits.items()):
        by_accession[row["accession"]].append((url, row))
    companies, docs, sentences = {}, {}, []
    connection = sqlite3.connect(f"file:{data_root / 'snapshot/facts.sqlite'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    meta_companies = {r["ticker"]: dict(r) for r in connection.execute("SELECT * FROM companies")}
    filings = defaultdict(list)
    for row in connection.execute("SELECT * FROM filings ORDER BY accepted_at, accession"):
        filings[row["cik"]].append(dict(row))
    periods = defaultdict(list)
    for row in connection.execute("SELECT * FROM fiscal_periods WHERE fiscal_period IN ('Q1','Q2','Q3') ORDER BY period_end DESC"):
        periods[row["cik"]].append(dict(row))
    connection.close()
    for index, row in enumerate(sorted(universe, key=lambda r: r["ticker"])):
        if progress and index % 50 == 0:
            print(f"Source scan {index}/{len(universe)} companies; {len(sentences)} accepted sentences", flush=True)
        ticker, cik = row["ticker"], int(row["cik"])
        if tickers is not None and ticker not in tickers:
            continue
        if ticker in EXCLUDED or ticker not in meta_companies or str(cik) not in cf_index:
            continue
        source = data_root / f"archive/companyfacts/CIK{cik:010d}.json.gz"
        if not source.exists():
            continue
        raw = gzip.decompress(source.read_bytes())
        if sha(raw) != cf_index[str(cik)]["sha256"]:
            raise ValueError(f"Companyfacts hash mismatch: {ticker}")
        facts = json.loads(raw)
        xbrl_accessions = {r["accn"] for namespace in facts.get("facts", {}).values() for concept in namespace.values()
                           for observations in concept.get("units", {}).values() for r in observations
                           if r.get("form") in ("10-Q", "10-Q/A")}
        company = {**row, "name": row["security"], "sector": row["sub_industry"],
                   "series": series_from_companyfacts(facts, irregular=ticker in IRREGULAR)}
        pairs = []
        for period in periods[cik]:
            tenqs = [f for f in filings[cik] if f["form"] == "10-Q" and f["report_date"] == period["period_end"]
                     and f["accession"] in xbrl_accessions]
            if not tenqs:
                continue
            q = min(tenqs, key=lambda f: (f["accepted_at"], f["accession"]))
            releases = [f for f in filings[cik] if f["form"] == "8-K" and "2.02" in (f["items"] or "")
                        and period["period_end"] < f["filed"] <= q["filed"] and f["accession"] in by_accession]
            if not releases:
                continue
            release = min(releases, key=lambda f: (f["accepted_at"], f["accession"]))
            # Index archives earnings EX-99 exhibits. Prefer a filename identifying 99.1.
            options = []
            for url, exhibit in by_accession[release["accession"]]:
                body = gzip.decompress((data_root / "archive/exhibits" / (exhibit["key"] + ".gz")).read_bytes())
                if sha(body) != exhibit["sha256"]:
                    raise ValueError(f"Exhibit hash mismatch: {url}")
                if is_exhibit_991(url, body):
                    options.append((url, exhibit))
            if not options:
                continue
            options = sorted(options)
            url, ex = options[0]
            pairs.append((period, q, release, url, ex))
            if len(pairs) == 2:
                break
        if not pairs:
            continue
        companies[ticker] = company
        provenance["records"].append({"kind": "companyfacts", "ticker": ticker, "path": str(source.relative_to(data_root)),
                                      "sha256": sha(raw), "record_sha256": sha(canonical(cf_index[str(cik)]))})
        with screening_environment(company):
            for period, q, release, url, ex in pairs:
                compressed = data_root / "archive/exhibits" / (ex["key"] + ".gz")
                html = gzip.decompress(compressed.read_bytes())
                if sha(html) != ex["sha256"]:
                    raise ValueError(f"Exhibit hash mismatch: {url}")
                fy, fp, end = period["fiscal_year"], period["fiscal_period"], date.fromisoformat(period["period_end"])
                doc_id = f"{ticker}-FY{fy}{fp}"
                blocks = base.html_blocks(html)
                text = "\n".join(blocks)
                if not release_names_quarter(text, period):
                    continue
                doc = {"doc_id": doc_id, "ticker": ticker, "cik": cik, "fy": fy, "fp": fp,
                       "period_end": end.isoformat(), "period_start": period["period_start"],
                       "quarter_days": (end - date.fromisoformat(period["period_start"])).days + 1,
                       "accession": release["accession"], "accepted_at": release["accepted_at"],
                       "pr_accepted": release["accepted_at"], "url": url, "exhibit": "EX-99.1", "form": "8-K",
                       "tenq_accession": q["accession"], "tenq_accepted_at": q["accepted_at"], "tenq_form": q["form"],
                       "context": f"{company['name']} ({ticker}), {base.quarter_word(fp)} quarter of fiscal {fy}, the quarter ended {end.strftime('%B')} {end.day}, {end.year}",
                       "numbers": sorted({base.number_key(n) for block in blocks for n in find_numbers(block)}),
                       "text_sha256": sha(text.encode()), "exhibit_sha256": sha(html),
                       "source_record_sha256": sha(canonical({"period": period, "tenq": q, "release": release, "exhibit": ex}))}
                docs[doc_id] = doc
                record = {"kind": "document", "ticker": ticker, "doc_id": doc_id,
                          "path": str(compressed.relative_to(data_root)), "sha256": sha(html),
                          "record_sha256": doc["source_record_sha256"], "tenq_accession": q["accession"],
                          "xbrl_source": "companyfacts", "metadata": {"period": period, "tenq": q, "release": release, "exhibit": ex}}
                if q["accession"] in xml_index:
                    xml_file = data_root / "archive/filing_xbrl" / (q["accession"] + ".xml.gz")
                    xml_raw = gzip.decompress(xml_file.read_bytes())
                    if sha(xml_raw) != xml_index[q["accession"]]["sha256"]:
                        raise ValueError("Filing XBRL hash mismatch")
                    record["filing_xbrl"] = {"path": str(xml_file.relative_to(data_root)), "sha256": sha(xml_raw)}
                provenance["records"].append(record)
                seen, offset, reasons = set(), 0, Counter()
                for bi, block in enumerate(blocks):
                    for off, sentence in base.split_sentences(block):
                        if sentence in seen or not re.search(r"\d", sentence):
                            continue
                        seen.add(sentence)
                        a = analyze_sentence(sentence, company["series"], end, (fy, fp))
                        if a["reasons"]:
                            reasons.update(a["reasons"])
                            continue
                        numbers = [{**asdict(n), "scaled": n.scaled()} for n in a["numbers"]]
                        start = offset + off
                        assert text[start:start + len(sentence)] == sentence
                        sentences.append({"sentence_id": f"{doc_id}-s{start:06d}", "doc_id": doc_id,
                                          "ticker": ticker, "text": sentence, "numbers": numbers,
                                          "kind": "forward_looking" if a["kind"] == "forward_looking" else "historical",
                                          "char_start": start, "block_index": bi})
                    offset += len(block) + 1
                provenance["screen_counts"][doc_id] = {"candidates": len(seen), "accepted": sum(s["doc_id"] == doc_id for s in sentences),
                                                        "rejection_reasons": dict(sorted(reasons.items()))}
    return companies, docs, sentences, provenance
