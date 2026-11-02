"""Offline release excerpts preserve the original SEC markup and provenance."""
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.build_release_text import build, exhibit_label, html_blocks, prose_units, reporting_period

FIXTURES = Path(__file__).parent / "fixtures/release_text"


@pytest.fixture
def inputs(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    database = tmp_path / "facts.sqlite"
    metadata = json.loads((FIXTURES / "metadata.json").read_text())
    index = {}
    with sqlite3.connect(database) as db:
        for table, sample in [("companies", metadata[0]["company"]), ("filings", metadata[0]["filing"]),
                              ("fiscal_periods", metadata[0]["periods"][0])]:
            db.execute(f"CREATE TABLE {table} (" + ",".join(f"{k} {'INTEGER' if isinstance(v, int) else 'TEXT'}" for k, v in sample.items()) + ")")
        for record in metadata:
            for table, rows in [("companies", [record["company"]]), ("filings", [record["filing"]]),
                                ("fiscal_periods", record["periods"])]:
                for row in rows:
                    db.execute(f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in row) + ")", list(row.values()))
            raw = (FIXTURES / (record["ticker"] + ".html")).read_bytes()
            key = hashlib.sha256(record["url"].encode()).hexdigest()
            (archive / (key + ".gz")).write_bytes(gzip.compress(raw, mtime=0))
            index[record["url"]] = dict(key=key, sha256=hashlib.sha256(raw).hexdigest(),
                                        cik=record["company"]["cik"], accession=record["filing"]["accession"])
    (archive / "index.json").write_text(json.dumps(index))
    return archive, database


def read_rows(output, ticker):
    return [json.loads(line) for line in gzip.decompress((output / f"{ticker}.jsonl.gz").read_bytes()).splitlines()]


def test_real_archived_releases(inputs, tmp_path):
    output = tmp_path / "out"
    manifest = build(*inputs, output)
    assert manifest["companies"] == manifest["documents"] == 3
    expected = {
        "MSFT": (2027, "Q1", "2026-09-30", "Microsoft Cloud revenue of $63.9 billion, with growth accelerating to 30% (up 31% in constant currency)"),
        "COST": (2026, "Q4", "2026-08-30", "Net sales for the quarter increased 11.2 percent, to $93.9 billion, from $84.4 billion last year."),
        "MCD": (2026, "Q2", "2026-06-30", "U.S. increased 0.8%"),
    }
    metadata = {r["ticker"]: r for r in json.loads((FIXTURES / "metadata.json").read_text())}
    for ticker, (year, quarter, end, sentence) in expected.items():
        rows = read_rows(output, ticker)
        row = next(r for r in rows if sentence in r["text"])
        assert (row["fiscal_year"], row["fiscal_period"], row["period_end"]) == (year, quarter, end)
        assert row["accepted_at"] == metadata[ticker]["filing"]["accepted_at"]
        assert row["url"] == metadata[ticker]["url"]
        assert row["exhibit"] == "EX-99.1"
        assert row["lead"] == ("Global comparable sales increased 1.3%:" if ticker == "MCD" else "")
        assert all(set(r) == {"ticker", "cik", "accession", "exhibit", "url", "accepted_at", "fiscal_year", "fiscal_period", "period_start", "period_end", "seq", "text", "lead"} for r in rows)
    assert any("currently operates 939 warehouses" in r["text"] for r in read_rows(output, "COST"))


def test_byte_identical_builds_and_read_only_inputs(inputs, tmp_path):
    archive, database = inputs
    before = {p: p.read_bytes() for p in [database, *archive.iterdir()]}
    first, second = tmp_path / "one", tmp_path / "two"
    build(*inputs, first)
    build(*inputs, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {p.name: p.read_bytes() for p in second.iterdir()}
    build(*inputs, first)
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert all(p.read_bytes() == data for p, data in before.items())
    assert all(p.read_bytes()[4:8] == b"\0\0\0\0" for p in first.glob("*.gz"))


def test_wrong_quarter_skipped(inputs, tmp_path):
    archive, database = inputs
    index = json.loads((archive / "index.json").read_text())
    entry = next(e for e in index.values() if e["cik"] == 789019)
    path = archive / (entry["key"] + ".gz")
    raw = gzip.decompress(path.read_bytes()).replace(b"First Quarter", b"Second Quarter")
    path.write_bytes(gzip.compress(raw, mtime=0))
    entry["sha256"] = hashlib.sha256(raw).hexdigest()
    (archive / "index.json").write_text(json.dumps(index))
    output = tmp_path / "out"
    manifest = build(archive, database, output)
    assert manifest["skipped_by_reason"] == {"quarter_mismatch": 1}
    assert not (output / "MSFT.jsonl.gz").exists()


def test_blocks_bullets_leads_tables_and_sentence_boundaries():
    blocks, counts = html_blocks(b'''<p>Sales increased 1.3%:</p><ul><li>U.S. increased 0.8%; Canada grew 2%. Still growing 3%.</li></ul>
    <div>Other 4.5 <b>billion</b>. <span>Next 6.</span><p>Nested 7.</p>Tail 8.</div>
    <p>Ignored table lead:</p><table><tr><td>Revenue 999</td><td><p>Hidden 888</p></td></tr></table>
    <div>&bull; Separate 9.</div><p>Dr. Smith said 10. "Up 11." Next 12.</p>''')
    units = prose_units(blocks)
    assert units == [('Sales increased 1.3%:', ''), ('U.S. increased 0.8%; Canada grew 2%. Still growing 3%.', 'Sales increased 1.3%:'),
                     ('Other 4.5 billion.', ''), ('Next 6.', ''), ('Nested 7.', ''), ('Tail 8.', ''),
                     ('Ignored table lead:', ''), ('Separate 9.', ''), ('Dr. Smith said 10.', ''), ('"Up 11."', ''), ('Next 12.', '')]
    assert counts == {"tables_skipped": 1, "table_rows_skipped": 1, "table_cells_skipped": 2}


@pytest.mark.parametrize("html,url,label", [
    (b"<TYPE>EX-99.01\n<TEXT><p>1</p>", "a.htm", "EX-99.01"),
    (b"<TYPE>EX-99\n", "a.htm", "EX-99"),
    (b"<TYPE>EX-10.1\n", "ex991.htm", None),
    (b"<p>1</p>", "https://example/ex99_2.htm", "EX-99.2"),
    (b"<p>1</p>", "https://example/d123dex9901.htm", "EX-99.01"),
    (b"<p>1</p>", "https://example/report.htm", None),
])
def test_exhibit_labels(html, url, label):
    assert exhibit_label(html, url) == label


@pytest.mark.parametrize("phrase,reason", [
    ("First quarter of fiscal year 2027", None), ("Q1 FY2027", None),
    ("quarter ended September 30, 2026", None), ("September quarter 2026", None),
    ("Q2 FY2027", "quarter_mismatch"), ("Q1 FY2026", "quarter_mismatch"),
    ("Q1FY2026", "quarter_mismatch"), ("1Q27", None), ("1Q26", "quarter_mismatch"),
    ("quarter ended June 30, 2026", "quarter_mismatch"),
    ("Q1 and Q2 results", "quarter_mismatch"), ("Annual results 2026", "quarter_not_named"),
])
def test_period_declarations(phrase, reason):
    rows = [dict(fiscal_year=2027, fiscal_period="Q1", period_start="2026-07-01", period_end="2026-09-30")]
    result, actual = reporting_period(rows, "2026-10-28", [(phrase, False)])
    assert actual == reason
    assert bool(result) == (reason is None)


@pytest.mark.parametrize("filed,reason", [("2026-09-30", "no_recent_quarter"), ("2027-01-29", "no_recent_quarter"), ("2027-01-28", None)])
def test_quarter_date_window(filed, reason):
    row = dict(fiscal_year=2027, fiscal_period="Q1", period_start="2026-07-01", period_end="2026-09-30")
    assert reporting_period([row], filed, [("Q1 FY2027", False)])[1] == reason
    assert reporting_period([row, dict(row, fiscal_year=2026)], "2026-10-28", [("Q1 FY2027", False)])[1] == "ambiguous_calendar"


def test_eligibility_and_hash_integrity(inputs, tmp_path):
    archive, database = inputs
    with sqlite3.connect(database) as db:
        db.execute("UPDATE filings SET items='12.02' WHERE cik=789019")
        db.execute("UPDATE filings SET form='10-Q' WHERE cik=63908")
    manifest = build(*inputs, tmp_path / "out")
    assert manifest["documents"] == 1
    assert manifest["skipped_by_reason"] == {"not_item_2_02_8k": 2}
    index = json.loads((archive / "index.json").read_text())
    entry = next(e for e in index.values() if e["cik"] == 909832)
    (archive / (entry["key"] + ".gz")).write_bytes(gzip.compress(b"altered"))
    with pytest.raises(ValueError, match="hash mismatch"):
        build(*inputs, tmp_path / "out")


def test_reporting_sentence_excludes_later_comparison_and_outlook():
    row = dict(fiscal_year=2025, fiscal_period="Q4", period_start="2025-10-01", period_end="2025-12-31")
    blocks = [("Company Reports Fourth Quarter Results", False),
              ("Company today announced fourth quarter and annual 2025 financial results. "
               "The company reported fourth quarter 2025 net sales of $4.4 billion. "
               "Comparatively, sequential third quarter 2025 income was $404 million.", False),
              ("First quarter 2026 outlook", False)]
    assert reporting_period([row], "2026-01-27", blocks) == (row, None)
    blocks[1] = ("Company today announced third quarter 2025 financial results.", False)
    assert reporting_period([row], "2026-01-27", blocks)[1] == "quarter_mismatch"


def test_bullet_paragraphs_are_one_unit_and_nested_list_is_separate():
    blocks, _ = html_blocks(b'<p>Sales:</p><ul><li><p>Revenue 1.</p><p>Growth 2%.<br>Margin 3%.</p></li>'
                          b'<li>Regions:<ul><li>East 4%.</li><li>West 5%.</li></ul></li></ul>')
    assert prose_units(blocks) == [("Sales:", ""), ("Revenue 1. Growth 2%. Margin 3%.", "Sales:"),
                                   ("Regions:", "Sales:"), ("East 4%.", "Regions:"), ("West 5%.", "Regions:")]
