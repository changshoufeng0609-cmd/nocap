"""CLI receipt lines: each filing document is listed once, however many of its facts support a verdict."""
from datetime import date, datetime, timezone

from receipts.cli import MAX_RECEIPTS, _receipt_lines
from receipts.types import Fact, Verdict


def fact(accession: str, metric: str, value: float) -> Fact:
    return Fact(cik=1045810, ticker="NVDA", metric=metric, value=value, unit="USD", basis="GAAP",
                period_start=date(2026, 4, 27), period_end=date(2026, 7, 26), source="press_release", form="8-K",
                accession=accession, accepted_at=datetime(2026, 8, 26, 20, 21, 19, tzinfo=timezone.utc),
                url=f"https://www.sec.gov/Archives/edgar/data/1045810/{accession.replace('-', '')}/pr.htm")


def verdict(*facts: Fact) -> Verdict:
    return Verdict(claim_id="c1", status="verified", reason="match", explanation="Matches.", evidence=list(facts))


def test_facts_from_one_document_print_one_receipt_line():
    same = [fact("0001045810-26-000073", m, v) for m, v in (("revenue", 96.2e9), ("segment_revenue", 89e9))]
    lines = _receipt_lines(verdict(*same))
    assert len(lines) == 1 and "0001045810-26-000073" in lines[0]


def test_more_documents_than_shown_are_counted_once_each():
    docs = [fact(f"0001045810-26-00007{i}", "revenue", 1.0) for i in range(MAX_RECEIPTS + 2)]
    lines = _receipt_lines(verdict(*docs, docs[0]))
    assert len(lines) == MAX_RECEIPTS + 1 and lines[-1].strip() == "(+2 more)"
