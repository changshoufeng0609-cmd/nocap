"""Offline tests for receipts.press_release: no network, mocked model responses, SYNTHETIC tables.

Every table below is invented for "Example Corp", a fictional company; no number is real.
Run: .venv/bin/python -m pytest -q tests/test_press_release.py
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from receipts import press_release as pr
from receipts.types import Fact

ACCEPTED = datetime(2026, 7, 30, 20, 5, tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------ helpers
def _fact(concept: str, start: str, end: str, val: float, fy: int, fp: str, form: str, accn: str, filed: str,
          unit: str = "USD") -> tuple[str, str, dict]:
    return concept, unit, {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "form": form,
                           "accn": accn, "filed": filed}


def companyfacts(*facts: tuple[str, str, dict]) -> dict:
    gaap: dict = {}
    for concept, unit, f in facts:
        gaap.setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(f)
    return {"facts": {"us-gaap": gaap}}


# A calendar-year filer: FY2024 and FY2025 10-Ks, 10-Qs for Q2 2025, Q1 2026 and Q2 2026.
CALENDAR_FACTS = (
    _fact("NetIncomeLoss", "2024-01-01", "2024-12-31", 1.0, 2024, "FY", "10-K", "0000000001-25-000001", "2025-02-01"),
    _fact("NetIncomeLoss", "2025-04-01", "2025-06-30", 1.0, 2025, "Q2", "10-Q", "0000000001-25-000020", "2025-08-01"),
    _fact("NetIncomeLoss", "2025-01-01", "2025-12-31", 1.0, 2025, "FY", "10-K", "0000000001-26-000001", "2026-02-01"),
    _fact("NetIncomeLoss", "2026-01-01", "2026-03-31", 1.0, 2026, "Q1", "10-Q", "0000000001-26-000010", "2026-05-01"),
    _fact("NetIncomeLoss", "2026-04-01", "2026-06-30", 1.0, 2026, "Q2", "10-Q", "0000000001-26-000020", "2026-07-31"),
)


def ctx_for(html_tables: list[pr.PTable], facts: tuple = CALENDAR_FACTS) -> pr.FilingContext:
    doc_dates = {d for t in html_tables for c in t.columns for d in pr.parse_dates(" ".join(c))}
    return pr.FilingContext(ticker="EXMP", cik=1, company="Example Corp (SYNTHETIC)",
                            accession="0000000001-26-000019", accepted_at=ACCEPTED,
                            url="https://example.invalid/ex99-1.htm", exhibit="EX-99.1",
                            calendar=pr.FiscalCalendar(companyfacts(*facts)), doc_dates=doc_dates)


def td(text: str = "", colspan: int = 1) -> str:
    return f'<td colspan="{colspan}">{text}</td>' if colspan > 1 else f"<td>{text}</td>"


def table_html(title: list[str], header_rows: list[str], body_rows: list[str]) -> str:
    """Title paragraphs, then one <table> whose rows are pre-built <td> strings."""
    paras = "".join(f"<p>{t}</p>" for t in title)
    rows = "".join(f"<tr>{r}</tr>" for r in header_rows + body_rows)
    return f"<html><body><p>SYNTHETIC TEST DATA</p>{paras}<table>{rows}</table></body></html>"


# Income statement, 2 quarter columns. Each value column is [$][number][)] = 3 grid columns, as in
# SEC filings: a lone "$" cell before the number and a lone ")" cell after a negative number.
QUARTER_HEADERS = [
    td() + td("Three Months Ended", 6),
    td() + td("June 30, 2026", 3) + td("June 30, 2025", 3),
]


def money_row(label: str, *cells: tuple[str, str]) -> str:
    """cells: (number text, closing text) per column, e.g. ("(1,234", ")") or ("96,221", "")."""
    return td(label) + "".join(td("$") + td(num) + td(close) for num, close in cells)


INCOME_STATEMENT = table_html(
    ["Example Corp", "CONDENSED CONSOLIDATED STATEMENTS OF INCOME", "(In millions, except per share data)"],
    QUARTER_HEADERS,
    [
        money_row("Revenue", ("96,221", ""), ("46,743", "")),
        money_row("Operating income (loss)", ("(1,234", ")"), ("567", "")),
        td("Net income per share:"),
        money_row("Basic", ("2.47", ""), ("1.08", "")),
        money_row("Diluted", ("2.46", ""), ("1.08", "")),
        td("Weighted average shares used in per share computation:"),
        money_row("Basic", ("24,190", ""), ("24,366", "")),
        money_row("Diluted", ("24,532", ""), ("24,611", "")),
    ],
)


def column(col: int, end: str, duration: str = "quarter", use: bool = True, basis: str = "row") -> pr.ColumnMap:
    return pr.ColumnMap(col=col, use=use, period_end=end, duration=duration, basis=basis)


def row(rid: int, metric: str, values: list, basis: str = "GAAP", segment: str = "") -> pr.RowMap:
    return pr.RowMap(row=rid, metric=metric, basis=basis, segment=segment, values=values)


QUARTER_COLUMNS = [column(0, "2026-06-30"), column(1, "2025-06-30")]


def only_table(html: str) -> pr.PTable:
    tables = pr.parse_tables(html)
    assert len(tables) == 1
    return tables[0]


# ------------------------------------------------------------------------------- 1. scale detection
@pytest.mark.parametrize("text, scale", [
    ("(In millions, except per share data)", "millions"),
    ("(in thousands, except per share data)", "thousands"),  # Netflix
    ("(In millions, except number of shares, which are reflected in thousands, and per-share amounts)",
     "millions"),  # Apple: the first mention wins
    ("($ in billions)", "billions"),
    ("(Millions except per share amounts and percentages) (Unaudited)", "millions"),  # AMD
    ("($ millions, except per share data)", "millions"),  # JPMorgan
    ("Revenue ($M)", "millions"),
    ("(Unaudited)", None),
])
def test_detect_scale_from_unit_header(text, scale):
    assert pr.detect_scale(text) == scale


def test_thousands_vs_millions_scale_is_applied_from_the_unit_header():
    nflx_style = table_html(
        ["Example Corp", "Consolidated Statements of Operations", "(in thousands, except per share data)"],
        QUARTER_HEADERS, [money_row("Revenues", ("12,874,176", ""), ("11,510,307", ""))])
    t = only_table(nflx_style)
    assert (t.scale, t.unit_header) == ("thousands", "(in thousands, except per share data)")
    m = pr.TableMap(table=t.index, scale="thousands", columns=QUARTER_COLUMNS,
                    rows=[row(0, "revenue", [12874176, 11510307])])
    facts, rejected = pr.decide_table(t, m, ctx_for([t]))
    assert not rejected
    assert [f.value for f in facts] == [12_874_176_000.0, 11_510_307_000.0]
    assert {f.decimals for f in facts} == {-3} and {f.unit for f in facts} == {"USD"}

    t2 = only_table(INCOME_STATEMENT)
    assert t2.scale == "millions"
    m2 = pr.TableMap(table=t2.index, scale="millions", columns=QUARTER_COLUMNS,
                     rows=[row(0, "revenue", [96221, 46743])])
    facts2, _ = pr.decide_table(t2, m2, ctx_for([t2]))
    assert facts2[0].value == 96_221_000_000.0 and facts2[0].decimals == -6


def test_model_scale_must_agree_with_the_unit_header():
    t = only_table(INCOME_STATEMENT)
    m = pr.TableMap(table=t.index, scale="thousands", columns=QUARTER_COLUMNS,
                    rows=[row(0, "revenue", [96221, 46743]), row(3, "eps_diluted", [2.46, 1.08])])
    facts, rejected = pr.decide_table(t, m, ctx_for([t]))
    assert {r["reason"] for r in rejected} == {"scale_disagreement"}
    assert [f.metric for f in facts] == ["eps_diluted", "eps_diluted"]  # per-share amounts are never scaled


# ---------------------------------------------------------- 2. parenthesized negative split across cells
def test_parenthesized_negative_split_across_cells():
    merged = pr.merge_cells([("Operating income (loss)", 0, 0), ("$", 1, 1), ("(1,234", 2, 2), (")", 3, 3),
                             ("$", 4, 4), ("567", 5, 5), ("", 6, 6)])
    assert merged == [["Operating income (loss)", 0, 0], ["(1,234)", 2, 3], ["567", 5, 5]]
    assert pr.parse_number("(1,234)") == Decimal("-1234")
    assert pr.parse_number("(1,234") is None  # an unbalanced parenthesis is not a number

    t = only_table(INCOME_STATEMENT)
    op = next(r for r in t.rows if r.label == "Operating income (loss)")
    assert op.cells == {0: "(1,234)", 1: "567"}
    m = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS,
                    rows=[row(op.rid, "operating_income", [-1234, 567])])
    facts, rejected = pr.decide_table(t, m, ctx_for([t]))
    assert not rejected
    assert [f.value for f in facts] == [-1_234_000_000.0, 567_000_000.0]
    assert facts[0].locator == "Operating income (loss) | (1,234)"


@pytest.mark.parametrize("text, value", [
    ("96,221", "96221"), ("$96,221", "96221"), ("$(1,234)", "-1234"), ("($1,234)", "-1234"), ("75.0%", "75.0"),
    ("(4)%", "-4"), ("−12.5", "-12.5"), ("2.46*", "2.46"), ("—", None), ("N/A", None), ("Up 50%", None),
    ("0.1 pts", None), ("1Q26", None),
])
def test_parse_number(text, value):
    assert pr.parse_number(text) == (Decimal(value) if value is not None else None)


# -------------------------------------------------------- 3. the verbatim-cell rule rejects hallucinations
def test_verbatim_cell_rule_rejects_a_hallucinated_value():
    t = only_table(INCOME_STATEMENT)
    m = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS, rows=[
        row(0, "revenue", [96221, 46700]),        # 46,700 is invented: the cell reads 46,743
        row(1, "operating_income", [-1.234, 567]),  # scale applied by the model: not what the cell says
    ])
    facts, rejected = pr.decide_table(t, m, ctx_for([t]))
    assert [(f.metric, f.value) for f in facts] == [("revenue", 96_221_000_000.0),
                                                    ("operating_income", 567_000_000.0)]
    assert [(r["row"], r["col"], r["reason"]) for r in rejected] == [(0, 1, "value_mismatch"),
                                                                     (1, 0, "value_mismatch")]
    assert "46743" in rejected[0]["detail"] and "46700" in rejected[0]["detail"]
    assert not any(f.value in (46_700_000_000.0, -1_234_000.0) for f in facts)


# ---------------------------------------------------------------------- 4. EPS rows vs share-count rows
def test_eps_rows_vs_share_count_rows():
    t = only_table(INCOME_STATEMENT)
    by_label = [(r.rid, r.section, r.label) for r in t.rows]
    assert by_label[2:] == [(2, "Net income per share:", "Basic"), (3, "Net income per share:", "Diluted"),
                            (4, "Weighted average shares used in per share computation:", "Basic"),
                            (5, "Weighted average shares used in per share computation:", "Diluted")]
    m = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS, rows=[
        row(2, "eps_basic", [2.47, 1.08]),
        row(3, "eps_diluted", [2.46, 1.08]),
        row(5, "eps_diluted", [24532, 24611]),  # a share count mapped as EPS: code must refuse it
    ])
    facts, rejected = pr.decide_table(t, m, ctx_for([t]))
    eps = [(f.metric, f.value, f.unit, f.decimals) for f in facts]
    assert eps == [("eps_basic", 2.47, "USD/share", 2), ("eps_basic", 1.08, "USD/share", 2),
                   ("eps_diluted", 2.46, "USD/share", 2), ("eps_diluted", 1.08, "USD/share", 2)]
    assert [(r["row"], r["reason"]) for r in rejected] == [(5, "share_count_row")]
    assert "share counts: omit them" in pr.INSTRUCTIONS  # the model is told too; code enforces it anyway


# ------------------------------------------------------------------------- periods, columns, labels
def test_periods_starts_and_fiscal_labels():
    t = only_table(INCOME_STATEMENT)
    m = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS,
                    rows=[row(0, "revenue", [96221, 46743])])
    f26, f25 = pr.decide_table(t, m, ctx_for([t]))[0]
    assert (f26.period_start, f26.period_end, f26.fiscal_year, f26.fiscal_period) == (
        date(2026, 4, 1), date(2026, 6, 30), 2026, "Q2")
    assert (f25.period_start, f25.fiscal_year, f25.fiscal_period) == (date(2025, 4, 1), 2025, "Q2")
    assert (f26.source, f26.form, f26.accession, f26.accepted_at, f26.concept) == (
        "press_release", "8-K", "0000000001-26-000019", ACCEPTED, None)


def test_column_checks_reject_wrong_periods_and_forecasts():
    html = table_html(["(In millions)"],
                      [td() + td("Q2 FY26", 3) + td("Q1 FY26", 3) + td("Q3 FY26 Outlook", 3) + td("Y/Y", 3)],
                      [money_row("Revenue", ("96,221", ""), ("81,615", ""), ("99,000", ""), ("18%", ""))])
    t = only_table(html)
    m = pr.TableMap(table=t.index, scale="millions", rows=[row(0, "revenue", [96221, 81615, 99000, None])],
                    columns=[column(0, "2026-06-30"), column(1, "2025-12-31"),  # Q1 FY26 ended 2026-03-31
                             column(2, "2026-09-30"), column(3, "2026-06-30", use=False)])
    facts, rejected = pr.decide_table(t, m, ctx_for([t]))
    assert [(f.period_end, f.value) for f in facts] == [(date(2026, 6, 30), 96_221_000_000.0)]
    assert {(r["col"], r["reason"]) for r in rejected} == {
        (1, "period_label_mismatch"), (2, "forward_looking_column"), (3, "change_or_adjustment_column")}


def test_header_shift_aligns_offset_headers():
    """Some filers (BAC) print every column header a fixed number of grid columns left of its numbers."""
    header = td("") + td("Second Quarter 2026", 3) + td("", 3) + td("First Quarter 2026", 3)  # grid 1-3, 7-9
    body = td("Net income") + td("", 5) + td("9,074") + td("", 5) + td("8,584")              # numbers at 6, 12
    t = only_table(table_html(["(In millions)"], [header], [body, body.replace("Net income", "Net revenue")]))
    assert pr._header_shift([pr.merge_cells([("", 0, 0), ("Second Quarter 2026", 1, 3), ("", 4, 6),
                                             ("First Quarter 2026", 7, 9)])], [(6, 6), (12, 12)], 0) != 0
    assert [t.column_text(c) for c in range(2)] == ["Second Quarter 2026", "First Quarter 2026"]


def test_a_label_cell_spanning_two_header_rows_keeps_the_year_row_under_its_own_numbers():
    """Exxon-shaped: the unit label spans both header rows (rowspan=2), so the year row starts after it. Read row by
    row, the first "2025" fell in the label column and every column took its neighbour's year."""
    spanned = ('<td colspan="3" rowspan="2">Dollars in millions (unless otherwise noted)</td>'
               + td("Three Months Ended September 30,", 6) + td("Nine Months Ended September 30,", 6))
    years = td("2025", 3) + td("2024", 3) + td("2025", 3) + td("2024", 3)
    body = td("Total revenues and other income", 3) + "".join(td(n, 3) for n in ("85,294", "90,016", "249,930",
                                                                                   "266,159"))
    t = only_table(table_html(["CONDENSED CONSOLIDATED STATEMENT OF INCOME"], [spanned, years],
                              [body, body.replace("Total revenues and other income", "Sales and other revenue")]))
    assert [t.column_text(c) for c in range(len(t.columns))] == [
        "Three Months Ended September 30, / 2025", "Three Months Ended September 30, / 2024",
        "Nine Months Ended September 30, / 2025", "Nine Months Ended September 30, / 2024"]
    assert t.rows[0].cells == {0: "85,294", 1: "90,016", 2: "249,930", 3: "266,159"}
    assert t.scale == "millions" and "2025" not in t.title
    # the spanned cell holds its columns in the next row only: the row after the years starts at grid column 0
    grid = pr._grid_rows(BeautifulSoup(table_html([], [spanned, years], [body]), "lxml").find("table"))
    assert [(c[0], c[1]) for c in grid[1]] == [("2025", 3), ("2024", 6), ("2025", 9), ("2024", 12)]
    assert grid[2][0][:2] == ["Total revenues and other income", 0]


def test_an_ordinary_header_is_placed_as_before():
    """No cell spans rows (or one spans a single row): every cell sits where its colspans put it."""
    t = only_table(INCOME_STATEMENT)
    assert [t.column_text(c) for c in range(2)] == ["Three Months Ended / June 30, 2026",
                                                    "Three Months Ended / June 30, 2025"]
    assert t.rows[0].cells == {0: "96,221", 1: "46,743"} and t.rows[1].cells == {0: "(1,234)", 1: "567"}
    one_row = '<td rowspan="1">(In millions)</td>' + td("Q2 FY26", 3) + td("Q1 FY26", 3)
    grid = pr._grid_rows(BeautifulSoup(table_html([], [one_row], [money_row("Revenue", ("96,221", ""),
                                                                             ("81,615", ""))]), "lxml").find("table"))
    assert [(c[0], c[1], c[2]) for c in grid[0]] == [("(In millions)", 0, 0), ("Q2 FY26", 1, 3), ("Q1 FY26", 4, 6)]
    assert [(c[0], c[1]) for c in grid[1]] == [("Revenue", 0), ("96,221", 2), ("81,615", 5)]


def test_dedupe_keeps_the_precise_repeat_and_drops_contradictions():
    t = only_table(INCOME_STATEMENT)
    base = pr.decide_table(t, pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS,
                                          rows=[row(0, "revenue", [96221, 46743])]), ctx_for([t]))[0][0]
    precise = base.model_copy(update={"value": 96_221_400_000.0, "decimals": -3, "locator": "Revenue | 96,221,400"})
    other = base.model_copy(update={"value": 95_000_000_000.0, "locator": "Revenue | 95,000"})
    kept, conflicts = pr.dedupe([base, precise])
    assert kept == [precise] and not conflicts
    kept, conflicts = pr.dedupe([base, other])
    assert kept == [] and conflicts[0]["values"] == [96_221_000_000.0, 95_000_000_000.0]


def test_segment_names_are_normalized():
    assert [pr.normalize_segment(s) for s in ("AWS Segment", "Total Client and Gaming", "Google Services total",
                                               "Online stores (1)", "Other revenue", "Revenue")] == [
        "AWS", "Client and Gaming", "Google Services", "Online stores", "Other", None]


# ------------------------------------------------ model I/O: request shape, mocked HTTP, cache, budget
def _mock_response_body(output_text: str) -> dict:
    return {"id": "resp_mock_1", "object": "response", "created_at": 1761550000, "status": "completed",
            "model": pr.SPARK_MODEL, "output": [
                {"id": "msg_mock_1", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": output_text, "annotations": []}]}],
            "usage": {"input_tokens": 3000, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 800,
                      "output_tokens_details": {"reasoning_tokens": 300}, "total_tokens": 3800},
            "incomplete_details": None, "error": None, "instructions": None, "metadata": {},
            "parallel_tool_calls": True, "temperature": 1.0, "top_p": 1.0, "tool_choice": "auto", "tools": [],
            "store": False}


def mocked_client(output_text: str, seen: list):
    """The real OpenAI SDK with its HTTP transport mocked: nothing leaves the machine."""
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")

    def handler(request):
        seen.append(json.loads(request.content.decode()))
        return httpx2.Response(200, json=_mock_response_body(output_text))

    return openai.OpenAI(api_key="LLM_0_test_dummy_not_a_real_key", base_url=pr.BASE_URL, max_retries=0,
                         http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def test_pipeline_end_to_end_with_mocked_model(tmp_path: Path):
    t = only_table(INCOME_STATEMENT)
    ctx = ctx_for([t])
    job = pr.FilingJob(ticker="EXMP", cik=1, company=ctx.company,
                       eightk={"accessionNumber": ctx.accession, "acceptanceDateTime": "2026-07-30T20:05:00.000Z"},
                       tenq={"accessionNumber": "0000000001-26-000020"}, ctx=ctx, tables=[t], candidates=[t])
    pr.plan_requests(job, effort="minimal")
    assert len(job.requests) == 1
    request, tables = job.requests[0]
    assert tables == [t.index]
    assert request["model"] == "muse-spark-1.3" and not request["model"].endswith("-contributor")
    assert request["reasoning"] == {"effort": "minimal"} and request["store"] is False
    assert request["text"]["format"]["type"] == "json_schema" and request["text"]["format"]["strict"] is True
    assert "response_format" not in request and not pr.strict_subset_problems(request["text"]["format"]["schema"])
    assert "r0: Revenue | c0 96,221 | c1 46,743" in request["input"]
    assert "c0: Three Months Ended / June 30, 2026" in request["input"]

    answer = pr.ExhibitMap(tables=[pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS, rows=[
        row(0, "revenue", [96221, 46743]), row(3, "eps_diluted", [2.46, 1.08]),
        row(5, "eps_diluted", [24532, 24611])])]).model_dump_json()
    seen: list = []
    mapper = pr.SparkMapper(client=mocked_client(answer, seen), cache_dir=tmp_path / "spark", budget_usd=1.0)
    pr.run_model([job], mapper)
    assert len(seen) == 1 and seen[0]["text"]["format"]["name"] == pr.SCHEMA_NAME
    assert job.outcomes[0]["status"] == "ok" and job.outcomes[0]["cost_usd"] > 0
    pr.decide_job(job)
    path = pr.write_job(job, out_dir=tmp_path / "pr_facts")
    lines = path.read_text().splitlines()
    facts = [Fact.model_validate_json(x) for x in lines]
    assert path.name == "0000000001-26-000019.jsonl" and len(facts) == 4
    assert {(f.metric, f.value) for f in facts} == {("revenue", 96_221_000_000.0), ("revenue", 46_743_000_000.0),
                                                    ("eps_diluted", 2.46), ("eps_diluted", 1.08)}
    report = json.loads((path.parent / "0000000001-26-000019.report.json").read_text())
    assert report["rejection_counts"] == {"share_count_row": 1}

    # Same request again: served from the request-hash cache, no HTTP call, no cost.
    again = pr.SparkMapper(client=mocked_client(answer, seen), cache_dir=tmp_path / "spark", budget_usd=1.0)
    out = again.map(request)
    assert out.ok and out.cached and out.cost_usd == 0 and len(seen) == 1
    # The spend ledger persists across runs, so the cap holds across invocations.
    assert again.prior_spend == pytest.approx(job.outcomes[0]["cost_usd"])


def test_budget_cap_refuses_live_calls(tmp_path: Path):
    t = only_table(INCOME_STATEMENT)
    request = pr.build_request("context", [t], effort="minimal", max_output_tokens=4000)
    seen: list = []
    mapper = pr.SparkMapper(client=mocked_client("{}", seen), cache_dir=tmp_path, budget_usd=0.0)
    out = mapper.map(request)
    assert (out.ok, out.status, seen) == (False, "budget", [])
    with pytest.raises(ValueError):
        pr.SparkMapper(effort="high")  # this layer runs at reasoning effort minimal or low only


# ------------------------------------------------------------------------------------------ validation
def test_validation_compares_gaap_only_against_the_paired_10q():
    t = only_table(INCOME_STATEMENT)
    ctx = ctx_for([t])
    m = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS, rows=[
        row(0, "revenue", [96221, 46743]), row(3, "eps_diluted", [2.46, 1.08]),
        row(1, "operating_income", [-1234, 567], basis="non-GAAP")])
    facts, _ = pr.decide_table(t, m, ctx)
    tenq = "0000000001-26-000020"
    cf = companyfacts(
        *CALENDAR_FACTS,
        _fact("Revenues", "2026-04-01", "2026-06-30", 96_221_000_000, 2026, "Q2", "10-Q", tenq, "2026-07-31"),
        _fact("Revenues", "2025-04-01", "2025-06-30", 46_743_000_000, 2026, "Q2", "10-Q", tenq, "2026-07-31"),
        _fact("EarningsPerShareDiluted", "2026-04-01", "2026-06-30", 2.46, 2026, "Q2", "10-Q", tenq, "2026-07-31",
              unit="USD/shares"),
        # The prior-year EPS in the 10-Q is 10.80: pre-split, so the press release's 1.08 disagrees.
        _fact("EarningsPerShareDiluted", "2025-04-01", "2025-06-30", 10.80, 2026, "Q2", "10-Q", tenq, "2026-07-31",
              unit="USD/shares"),
        _fact("OperatingIncomeLoss", "2026-04-01", "2026-06-30", 1, 2026, "Q2", "10-Q", tenq, "2026-07-31"))
    job = pr.FilingJob(ticker="EXMP", cik=1, company=ctx.company, eightk={"accessionNumber": ctx.accession},
                       tenq={"accessionNumber": tenq}, ctx=ctx, facts=facts)
    report = pr.validate([job], {1: cf})
    rec = report["per_company"]["EXMP"]
    assert (rec["compared"], rec["matched"], rec["non_gaap"]) == (4, 3, 2)  # non-GAAP is never compared
    assert report["overall"]["agreement"] == pytest.approx(0.75)
    (miss,) = report["mismatches"]
    assert miss["metric"] == "eps_diluted" and miss["reason"].startswith("stock split")


# ------------------------------------------------------------------------------------------ Day 3
def _filing(form, accession, filing_date, report_date="", items="", accepted=None):
    return {"form": form, "accessionNumber": accession, "filingDate": filing_date, "reportDate": report_date,
            "items": items, "acceptanceDateTime": accepted or f"{filing_date}T20:05:00.000Z"}


def test_earnings_pairs_cover_every_quarter_including_q4_with_its_10k():
    filings = [
        _filing("10-Q", "q3", "2026-11-01", "2026-09-30"), _filing("8-K", "k3", "2026-10-20", items="2.02,9.01"),
        _filing("10-K", "fy", "2026-02-15", "2025-12-31"), _filing("8-K", "k4", "2026-01-28", items="2.02,9.01"),
        _filing("8-K", "other", "2026-02-01", items="5.02"),  # not an earnings release
        _filing("10-Q", "q2", "2025-08-01", "2025-06-30"), _filing("8-K", "k2", "2025-07-25", items="2.02"),
    ]
    pairs = pr.earnings_pairs(filings, 8)
    assert [(k["accessionNumber"], q["accessionNumber"]) for k, q in pairs] == [("k3", "q3"), ("k4", "fy"), ("k2", "q2")]
    assert [q["accessionNumber"] for _, q in pr.earnings_pairs(filings, 2)] == ["q3", "fy"]


def test_exhibit_labels_are_not_non_gaap_wording():
    assert not pr._NONGAAP_RE.search("EX-99.2 | CFO Commentary | Q1 Fiscal 2027 Summary | GAAP")
    assert pr._NONGAAP_RE.search("Revenue ex-FX") and pr._NONGAAP_RE.search("Non-GAAP operating income")


SUMMARY_GAAP = table_html(["EX-99.2", "CFO Commentary (SYNTHETIC)", "Summary", "GAAP", "($ in millions)"],
                          QUARTER_HEADERS, [money_row("Revenue", ("96,221", ""), ("46,743", "")),
                                            money_row("Operating income", ("5,000", ""), ("4,000", ""))])
SUMMARY_CONFLICT = table_html(["EX-99.2", "Summary", "($ in millions)"],
                              QUARTER_HEADERS, [money_row("Revenue", ("96,999", ""), ("46,743", "")),
                                                money_row("Operating income", ("5,000", ""), ("4,000", ""))])


class FakeEdgar:
    def __init__(self, docs):
        self.docs = docs  # url -> (type, size, html)

    def filing_index(self, cik, accession):
        return [{"seq": str(i + 1), "description": t, "document": u, "type": t, "size": str(size), "url": u}
                for i, (u, (t, size, _)) in enumerate(self.docs.items())]

    def get(self, url):
        return self.docs[url][2].encode()


def _job_for(docs):
    eightk = {"accessionNumber": "0000000001-26-000019", "acceptanceDateTime": "2026-07-30T20:05:00.000Z"}
    cal = pr.FiscalCalendar(companyfacts(*CALENDAR_FACTS))
    return pr.prepare_job(FakeEdgar(docs), "EXMP", 1, "Example Corp (SYNTHETIC)", eightk, {"accessionNumber": "q"},
                          cal, effort="minimal")


def test_secondary_exhibit_is_mapped_and_merged():
    job = _job_for({"https://example.invalid/ex99-1.htm": ("EX-99.1", 300_000, INCOME_STATEMENT + INCOME_STATEMENT),
                    "https://example.invalid/ex99-2.htm": ("EX-99.2", 200_000, SUMMARY_GAAP),
                    "https://example.invalid/tiny.htm": ("EX-99.3", 4_000, SUMMARY_GAAP)})  # too small to fetch
    assert job.ctx.exhibit == "EX-99.1" and [p.ctx.exhibit for p in job.secondary] == ["EX-99.2"]
    assert any(e.get("skipped", "").startswith("listed size") for e in job.exhibits_tried)
    part = job.secondary[0]
    assert len(part.requests) == 1 and "Exhibit EX-99.2" in part.requests[0][0]["input"]
    # the summary table is GAAP: its heading names the exhibit, which is not "ex-" wording
    t = part.candidates[0]
    part._maps[t.index] = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS,
                                      rows=[row(0, "revenue", [96221, 46743])])
    pr.decide_job(job)
    assert {(f.basis, f.value) for f in job.facts if f.metric == "revenue"} == {("GAAP", 96_221e6), ("GAAP", 46_743e6)}
    assert job.facts[0].url.endswith("ex99-2.htm")


def test_exhibits_that_disagree_drop_the_figure():
    job = _job_for({"https://example.invalid/ex99-1.htm": ("EX-99.1", 300_000, SUMMARY_GAAP + SUMMARY_GAAP),
                    "https://example.invalid/ex99-2.htm": ("EX-99.2", 200_000, SUMMARY_CONFLICT)})
    for part, value in ((job, 96221), (job.secondary[0], 96999)):
        t = part.candidates[0]
        part._maps[t.index] = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS,
                                          rows=[row(0, "revenue", [value, 46743])])
    pr.decide_job(job)
    assert {f.value for f in job.facts} == {46_743e6}  # 96,221 vs 96,999 for one period: ambiguous, dropped
    assert job.conflicts and job.conflicts[0]["values"] == [96_221e6, 96_999e6]


TRANSPOSED = table_html(
    ["Example Corp (SYNTHETIC)", "GAAP to Non-GAAP Reconciliation", "($ in millions, except per share amounts)"],
    [td() + td("Three Months Ended June 30,", 6), td() + td("Net Income", 3) + td("Diluted Earnings per Share", 3)],
    [money_row("2025 As Reported (GAAP)", ("24,108", ""), ("3.23", "")),
     money_row("Impact from investments", ("939", ""), ("0.12", "")),
     money_row("2025 As Adjusted (non-GAAP)", ("25,047", ""), ("3.35", "")),
     money_row("2026 As Reported (GAAP)", ("38,458", ""), ("5.16", "")),
     money_row("2026 As Adjusted (non-GAAP)", ("30,875", ""), ("4.14", "")),
     money_row("Percentage Change Y/Y (GAAP)", ("60%", ""), ("60%", ""))])


def test_transposed_reconciliation_is_read_by_code():
    t = only_table(TRANSPOSED)
    facts, rej = pr.transposed_facts(t, ctx_for([t]))
    got = {(f.metric, f.basis, f.period_end.isoformat(), f.value) for f in facts}
    assert got == {("net_income", "GAAP", "2025-06-30", 24_108e6), ("net_income", "non-GAAP", "2025-06-30", 25_047e6),
                   ("net_income", "GAAP", "2026-06-30", 38_458e6), ("net_income", "non-GAAP", "2026-06-30", 30_875e6),
                   ("eps_diluted", "GAAP", "2025-06-30", 3.23), ("eps_diluted", "non-GAAP", "2025-06-30", 3.35),
                   ("eps_diluted", "GAAP", "2026-06-30", 5.16), ("eps_diluted", "non-GAAP", "2026-06-30", 4.14)}
    assert all(f.source == "press_release" and f.period_start is not None for f in facts) and not rej
    assert pr.transposed_facts(only_table(INCOME_STATEMENT), ctx_for([t])) == ([], [])  # ordinary layout: nothing


def test_validation_explains_restatements_and_split_recasts():
    t = only_table(INCOME_STATEMENT)
    ctx = ctx_for([t])
    m = pr.TableMap(table=t.index, scale="millions", columns=QUARTER_COLUMNS, rows=[
        row(0, "revenue", [96221, 46743]), row(3, "eps_diluted", [2.46, 1.08])])
    facts, _ = pr.decide_table(t, m, ctx)
    tenq = "0000000001-26-000020"
    cf = companyfacts(
        *CALENDAR_FACTS,
        _fact("Revenues", "2026-04-01", "2026-06-30", 96_221_000_000, 2026, "Q2", "10-Q", tenq, "2026-07-31"),
        # prior-year revenue: first reported as 46,743, restated to 47,000 by a later 10-Q
        _fact("Revenues", "2025-04-01", "2025-06-30", 46_743_000_000, 2025, "Q2", "10-Q", "0000000001-25-000020",
              "2025-08-01"),
        _fact("Revenues", "2025-04-01", "2025-06-30", 47_000_000_000, 2026, "Q2", "10-Q", "0000000001-26-000099",
              "2026-09-01"),
        _fact("EarningsPerShareDiluted", "2026-04-01", "2026-06-30", 2.46, 2026, "Q2", "10-Q", tenq, "2026-07-31",
              unit="USD/shares"),
        # the release predates a 10-for-1 split; the later XBRL is split-adjusted
        _fact("EarningsPerShareDiluted", "2025-04-01", "2025-06-30", 0.108, 2026, "Q2", "10-Q", "0000000001-26-000099",
              "2026-09-01", unit="USD/shares"))
    job = pr.FilingJob(ticker="EXMP", cik=1, company=ctx.company, eightk={"accessionNumber": ctx.accession},
                       tenq={"accessionNumber": "0000000001-26-000098"}, ctx=ctx, facts=facts)
    report = pr.validate([job], {1: cf})
    reasons = {m["metric"]: m["reason"] for m in report["mismatches"]}
    assert reasons["revenue"].startswith("restated") and reasons["eps_diluted"].startswith("stock split")
    assert report["overall"]["mismatches_unexplained"] == 0


# ------------------------------------------------------------------------------------------ XBRL gate
TENQ = "0000000001-26-000020"
Q2_26, Q2_25, H1_26 = ("2026-04-01", "2026-06-30"), ("2025-04-01", "2025-06-30"), ("2026-01-01", "2026-06-30")


def _pr(metric: str, period: tuple[str, str], value: float, *, basis: str = "GAAP", segment=None,
        unit: str = "USD") -> Fact:
    return Fact(cik=1, ticker="EXMP", metric=metric, basis=basis, segment=segment, value=value, unit=unit,
                period_start=date.fromisoformat(period[0]), period_end=date.fromisoformat(period[1]),
                source="press_release", form="8-K", accession="0000000001-26-000019", accepted_at=ACCEPTED,
                url="https://example.invalid/ex99-1.htm", locator=f"{metric} | {value}", decimals=-6)


def _xbrl(concept: str, period: tuple[str, str], value: float, accn: str = TENQ, unit: str = "USD"):
    return _fact(concept, period[0], period[1], value, 2026, "Q2", "10-Q", accn, "2026-07-31", unit=unit)


def _gate_job(facts: list[Fact]) -> pr.FilingJob:
    ctx = ctx_for([only_table(INCOME_STATEMENT)])
    return pr.FilingJob(ticker="EXMP", cik=1, company=ctx.company, eightk={"accessionNumber": ctx.accession},
                        tenq={"accessionNumber": TENQ}, ctx=ctx, facts=list(facts))


def test_gate_drops_a_served_fact_the_paired_10q_contradicts_and_keeps_the_rest():
    facts = [_pr("revenue", Q2_26, 96_221e6), _pr("revenue", Q2_25, 46_743e6),
             _pr("operating_income", Q2_26, -1_234e6, basis="non-GAAP"),
             _pr("segment_revenue", Q2_26, 10_000e6, segment="Cloud")]
    cf = companyfacts(*CALENDAR_FACTS, _xbrl("Revenues", Q2_26, 96_221e6), _xbrl("Revenues", Q2_25, 46_000e6))
    job = _gate_job(facts)
    gate = pr.apply_xbrl_gate([job], {1: cf})
    assert job.gate.quarantine is None and gate["quarantined"] == []
    (drop,) = gate["dropped"]
    assert (drop["metric"], drop["period"], drop["press_release"], drop["xbrl"]) == (
        "revenue", "2025-04-01..2025-06-30", 46_743e6, 46_000e6)
    assert drop["scope"] == "paired" and drop["cause"] == "value or definition differs"
    assert facts[1] not in job.facts and [f for f in facts if f is not facts[1]] == job.facts  # non-GAAP, segment stay
    assert (gate["served_before"], gate["served_after"]) == (4, 3)


def test_gate_keeps_a_documented_restatement():
    facts = [_pr("revenue", Q2_25, 46_743e6)]
    cf = companyfacts(*CALENDAR_FACTS,
                      _xbrl("Revenues", Q2_25, 46_743e6, accn="0000000001-25-000020"),  # as first reported
                      _xbrl("Revenues", Q2_25, 47_000e6))                               # restated by the 10-Q
    job = _gate_job(facts)
    gate = pr.apply_xbrl_gate([job], {1: cf})
    assert gate["dropped"] == [] and gate["quarantined"] == [] and job.facts == facts


def test_gate_quarantines_a_file_with_two_unexplained_mismatches_in_one_column():
    facts = [_pr("revenue", Q2_25, 46_743e6), _pr("net_income", Q2_25, 9_000e6),
             _pr("segment_revenue", Q2_25, 5_000e6, segment="Cloud")]
    cf = companyfacts(*CALENDAR_FACTS, _xbrl("Revenues", Q2_25, 46_000e6), _xbrl("NetIncomeLoss", Q2_25, 8_500e6))
    job = _gate_job(facts)
    gate = pr.apply_xbrl_gate([job], {1: cf})
    (q,) = gate["quarantined"]
    assert q["reason"].startswith("2 unexplained GAAP mismatches in the 2025-04-01..2025-06-30 column")
    assert job.facts == [] and job.held == facts and gate["dropped"] == []  # the segment row goes with its table


def test_gate_quarantines_a_file_whose_columns_were_read_with_swapped_durations():
    facts = [_pr("operating_income", Q2_26, 5_676e6)]  # the six-month figure, filed as the quarter
    cf = companyfacts(*CALENDAR_FACTS, _xbrl("OperatingIncomeLoss", Q2_26, 2_309e6),
                      _xbrl("OperatingIncomeLoss", H1_26, 5_676e6))
    job = _gate_job(facts)
    gate = pr.apply_xbrl_gate([job], {1: cf})
    (q,) = gate["quarantined"]
    assert q["reason"].startswith("column swap: operating_income 2026-04-01..2026-06-30 equals OperatingIncomeLoss "
                                  "for 2026-01-01..2026-06-30")
    assert job.facts == []


def test_gate_records_but_keeps_an_unexplained_mismatch_the_store_never_serves():
    facts = [_pr("net_income", H1_26, 19_000e6)]  # a year-to-date column: stored, never evidence
    cf = companyfacts(*CALENDAR_FACTS, _xbrl("NetIncomeLoss", H1_26, 18_500e6))
    job = _gate_job(facts)
    gate = pr.apply_xbrl_gate([job], {1: cf})
    assert gate["dropped"] == [] and job.facts == facts
    (finding,) = job.gate.findings
    assert finding["served"] is False and not pr.is_served(facts[0])


def test_quarantined_file_is_written_where_the_store_never_reads(tmp_path: Path):
    facts = [_pr("revenue", Q2_25, 46_743e6), _pr("net_income", Q2_25, 9_000e6)]
    cf = companyfacts(*CALENDAR_FACTS, _xbrl("Revenues", Q2_25, 46_000e6), _xbrl("NetIncomeLoss", Q2_25, 8_500e6))
    job = _gate_job(facts)
    active = tmp_path / "EXMP" / f"{job.ctx.accession}.jsonl"
    active.parent.mkdir(parents=True)
    active.write_text("stale facts from an ungated run\n")
    pr.apply_xbrl_gate([job], {1: cf})
    path = pr.write_job(job, tmp_path)
    assert path.name == f"{job.ctx.accession}.jsonl.quarantined" and not active.exists()
    assert pr.load_facts(path) == facts and sorted(p.name for p in (tmp_path / "EXMP").glob("*.jsonl")) == []
    report = json.loads((tmp_path / "EXMP" / f"{job.ctx.accession}.report.json").read_text())
    assert report["xbrl_gate"]["quarantine"].startswith("2 unexplained GAAP mismatches")
    job.gate = pr.xbrl_gate(job, {}, None)  # nothing to check against: the file is released again
    job.facts, job.held = facts, []
    pr.write_job(job, tmp_path)
    assert active.exists() and not path.exists()
