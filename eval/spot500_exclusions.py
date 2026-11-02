"""Derive the companies that later fixes were tested or written on, for a fresh spot500 set.

python eval/spot500_exclusions.py   # writes eval/spot500_v3_exclusions.json; an existing file must match exactly

Source-only: the scan reads git history, test and verifier source text, and the
snapshot's companies table (ticker, name, aliases). It never runs NoCap.

A source line names a company when, against that table:
- alias: one of its aliases (except the bare ticker key) appears as whole words,
  case-insensitively, with at least one capital letter in the matched text and
  not glued to a number (the "3M" in "$8,810.3M" is not 3M);
- leading word: the first word of its name or of an alias appears the same way,
  if that word has four or more characters and begins no other company's name
  or alias ("Valero", "FactSet", "Akamai");
- ticker: the ticker appears case-sensitively, not touching a letter or digit.
  A one-letter ticker counts only when quoted or in parentheses ("A" as an
  article does not), and a ticker inside another company's matched name does
  not count ("GE" in "GE HealthCare").
Over-matching only removes companies from the sample, so the rules favour recall.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval/spot500_v3_exclusions.json"
SINCE = "50a4eac488691bc820b83ba29c8630d4eed39d80"   # the commit recording v1's one live run
UNTIL = "84b8fd48a94de10027e041be69595362a0d4ca40"   # the deployed code that v3 measures
TEST_DIFF = ("git", "diff", "--name-only", "--diff-filter=A", f"{SINCE}..{UNTIL}", "--", "tests/")
CODE_DIFF = ("git", "diff", "-U0", "--no-color", f"{SINCE}..{UNTIL}", "--", "receipts/")
DROPPED = {"tests/test_spot500_sets.py": "the spot500 set tests themselves, not a verifier fix"}
PROBES = ("BAX", "CSGP", "TGT")
TOKEN = re.compile(r"[^\W_]+|&")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def name_words(name: str) -> list[str]:
    """The words receipts.store.normalize_entity keeps, without dropping suffixes."""
    s = fold(name).replace("’", "'").replace("&", " and ").replace(".", "").replace("'", "")
    words = re.sub(r"[^0-9a-z]+", " ", s).split()
    while words and words[0] == "the":
        words.pop(0)
    return words


def companies_table(db: Path) -> tuple[list[tuple[str, str, list[str]]], dict]:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = [(t, n, json.loads(a)) for t, n, a in
                connection.execute("SELECT ticker, name, aliases FROM companies ORDER BY ticker")]
    finally:
        connection.close()
    return rows, {"table": "companies (ticker, name, aliases)", "rows": len(rows),
                  "sha256": sha(canonical([list(row) for row in rows]))}


class Scanner:
    def __init__(self, rows):
        self.tickers = [t for t, _, _ in rows]
        firsts, forms = defaultdict(set), []
        for ticker, name, keys in rows:
            bare = ticker.lower().replace(".", "")
            for form in [name, *keys]:
                words = name_words(form)
                if not words or words == [bare]:
                    continue
                if form in keys:
                    forms.append(("alias", ticker, tuple(words)))
                firsts[words[0]].add(ticker)
        forms += [("leading word", next(iter(t)), (w,)) for w, t in firsts.items() if len(t) == 1 and len(w) >= 4]
        self.by_first = defaultdict(set)
        for rule, ticker, words in forms:
            self.by_first[words[0]].add((rule, ticker, words))
        alternatives = "|".join(re.escape(t) for t in sorted(self.tickers, key=lambda t: (-len(t), t)))
        self.ticker_rx = re.compile(r"(?<![0-9A-Za-z])(?:" + alternatives + r")(?![0-9A-Za-z])")

    def line(self, text):
        """({ticker: [(rule, match)]}, {ticker: [(reason, match)]}) for one source line."""
        tokens = [(m.start(), m.end(), "and" if m.group() == "&" else fold(m.group())) for m in TOKEN.finditer(text)]
        words = [t[2] for t in tokens]
        named, rejected, spans = defaultdict(list), defaultdict(list), []
        for i, word in enumerate(words):
            for rule, ticker, form in sorted(self.by_first.get(word, ())):
                if tuple(words[i:i + len(form)]) != form:
                    continue
                start, end = tokens[i][0], tokens[i + len(form) - 1][1]
                match = text[start:end]
                if re.search(r"[0-9][.,]$", text[:start]):
                    rejected[ticker].append(("name inside a number", match))
                elif not any(c.isupper() for c in match):
                    rejected[ticker].append(("name without a capital letter", match))
                else:
                    named[ticker].append((rule, match))
                    spans.append((start, end, ticker))
        for m in self.ticker_rx.finditer(text):
            ticker = m.group()
            inside = sorted({t for s, e, t in spans if s <= m.start() and m.end() <= e and t != ticker})
            wrapped = text[max(0, m.start() - 1):m.start()] + text[m.end():m.end() + 1]
            if inside:
                rejected[ticker].append((f"ticker inside another company's name ({', '.join(inside)})", ticker))
            elif len(ticker) == 1 and wrapped not in ('""', "''", "()"):
                rejected[ticker].append(("one-letter ticker outside quotes or parentheses", ticker))
            else:
                named[ticker].append(("ticker", ticker))
        return named, rejected

    def scan(self, lines):
        """lines: (path, line number, text). Evidence keeps each rule's first match per company."""
        named, rejected = defaultdict(dict), defaultdict(dict)
        for path, number, text in lines:
            hits, misses = self.line(text)
            for ticker, found in hits.items():
                for rule, match in found:
                    named[ticker].setdefault(rule, {"match": match, "path": path, "line": number})
            for ticker, found in misses.items():
                for reason, match in found:
                    entry = rejected[ticker].setdefault(reason, {"match": match, "path": path, "line": number,
                                                                 "count": 0})
                    entry["count"] += 1
        return ({t: dict(sorted(v.items())) for t, v in sorted(named.items())},
                {t: dict(sorted(v.items())) for t, v in sorted(rejected.items()) if t not in named})


def git(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, encoding="utf-8").stdout


def added_lines(diff: str):
    """Added lines of a -U0 diff, keyed by their line numbers in the new file."""
    path, number, header = None, 0, False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            header, number = False, int(re.match(r"@@ -\S+ \+(\d+)", line).group(1))
        elif not header and line.startswith("+") and path:
            yield path, number, line[1:]
            number += 1


def derive(db: Path) -> dict:
    rows, table = companies_table(db)
    if not set(PROBES) <= {t for t, _, _ in rows}:
        raise ValueError("A probe ticker is missing from the companies table")
    scanner = Scanner(rows)
    listed = git(*TEST_DIFF).split()
    files = [path for path in listed if path not in DROPPED]
    texts = {path: git("git", "show", f"{UNTIL}:{path}") for path in files}
    tests_named, tests_rejected = scanner.scan(
        [(path, i, line) for path in files for i, line in enumerate(texts[path].splitlines(), 1)])
    diff = git(*CODE_DIFF)
    code_named, code_rejected = scanner.scan(list(added_lines(diff)))
    return {
        "version": "spot500_v3",
        "range": {"since": SINCE, "until": UNTIL, "since_role": "the commit recording spot500_v1's one live run",
                  "until_role": "the deployed code that spot500_v3 measures"},
        "companies_table": table,
        "method": __doc__.split("Source-only:")[1].strip(),
        "verifier_fix_tests": {
            "command": " ".join(TEST_DIFF), "listed": listed, "dropped": DROPPED,
            "scanned": [{"path": path, "sha256": sha(texts[path].encode())} for path in files],
            "companies": sorted(tests_named), "evidence": tests_named, "rejected": tests_rejected},
        "verifier_fix_code": {
            "command": " ".join(CODE_DIFF), "scope": "added lines only", "diff_sha256": sha(diff.encode()),
            "note": "beyond the requested test scan: the companies the fixed verifier's own added lines name",
            "companies": sorted(code_named), "evidence": code_named, "rejected": code_rejected},
        "reviewer_probes": {"companies": sorted(PROBES), "source": "used by the owner's reviewer in probes"},
    }


def encode(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies-db", type=Path, default=ROOT / "data/snapshot/facts.sqlite")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    raw = encode(derive(args.companies_db))
    if args.output.exists():
        if args.output.read_bytes() != raw:
            raise ValueError(f"Refusing changed frozen output: {args.output}")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    record = json.loads(raw)
    print(json.dumps({"output": str(args.output), "sha256": sha(raw),
                      "tests": record["verifier_fix_tests"]["companies"],
                      "code": record["verifier_fix_code"]["companies"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
