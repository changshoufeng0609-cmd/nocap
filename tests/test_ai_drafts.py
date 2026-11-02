"""eval/ai_drafts.py: plan, provenance, spend guard, "?" buckets and report, all offline with stub clients ($0)."""
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from eval import ai_drafts as ad
from receipts import extract as ex
from receipts.store import SCHEMA

ROOT = Path(__file__).resolve().parents[1]
FULL2 = ROOT / "eval/spot500_runs/full2-live-20261102.json"

SUB_INDUSTRIES = ("Application Software", "Regional Banks", "Electric Utilities")


def _sentences(ticker: str, k: int) -> list[str]:
    out = [f"{ticker} Announces Third Quarter 2026 Financial Results"]
    for i in range(1, 15):
        out.append(f"Revenue for segment {i} was ${k}.{i} billion, up {i + k}% from a year ago, with an operating "
                   f"margin of {20 + i}.{k}%.")
    return out


def make_snapshot(tmp_path: Path, n: int = 12) -> tuple[Path, Path]:
    """A tiny snapshot: n companies over three sectors, each with one Q3 2026 release and its filed 10-Q revenue."""
    snap = tmp_path / "snapshot"
    index = snap / "release_text"
    index.mkdir(parents=True)
    conn = sqlite3.connect(snap / "facts.sqlite")
    conn.executescript(SCHEMA)
    universe = []
    for k in range(n):
        ticker, cik = f"T{k:02d}", 1000 + k
        accession = f"0000000000-26-{k:06d}"
        conn.execute("INSERT INTO companies VALUES (?, ?, ?, '[]', '1231')", (cik, ticker, f"Test {k} Inc"))
        conn.execute("INSERT INTO facts (cik, ticker, metric, basis, value, unit, period_start, period_end, fiscal_year,"
                     " fiscal_period, source, derived_from, form, accession, accepted_at, url, period_kind) VALUES "
                     "(?, ?, 'revenue', 'GAAP', 1e9, 'USD', '2026-07-01', '2026-09-30', 2026, 'Q3', 'xbrl', '[]',"
                     " '10-Q', ?, '2026-10-30T20:00:00Z', 'https://www.sec.gov/x', 'quarter')",
                     (cik, ticker, f"10q-{k}"))
        rows = [{"ticker": ticker, "cik": cik, "accession": accession, "exhibit": "EX-99.1",
                 "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/r.htm", "accepted_at": "2026-10-20T20:00:00Z",
                 "fiscal_year": 2026, "fiscal_period": "Q3", "period_start": "2026-07-01", "period_end": "2026-09-30",
                 "seq": i, "text": text, "lead": ""} for i, text in enumerate(_sentences(ticker, k))]
        with gzip.open(index / f"{ticker}.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        universe.append({"ticker": ticker, "cik": cik, "security": f"Test {k} Inc. (Class A)",
                         "sub_industry": SUB_INDUSTRIES[k % 3]})
    conn.commit()
    conn.close()
    (snap / "manifest.json").write_text("{}")
    uni = tmp_path / "universe.json"
    uni.write_text(json.dumps({"universe": universe}))
    return snap, uni


# ============================================================ plan

def test_plan_is_deterministic_and_batches_never_share_companies_or_releases(tmp_path):
    snap, uni = make_snapshot(tmp_path)
    runs = tmp_path / "runs"
    kw = dict(runs_dir=runs, snapshot=snap, universe=uni, exclude_tickers=())
    dev = ad.build_plan("dev", 6, 7, **kw)
    assert dev == ad.build_plan("dev", 6, 7, **kw)
    assert ad.save_plan(dev, runs) == (runs / "dev" / "plan.json", "written")
    assert ad.save_plan(dev, runs)[1] == "unchanged"
    assert len(dev["companies"]) == 6
    assert dev["selection"]["allocation"] == {"Financials": 2, "Information Technology": 2, "Utilities": 2}
    styles = dev["selection"]["styles"]
    assert set(styles) <= set(ad.STYLES) and max(styles.values()) - min(styles.values()) <= 1
    assert all(c["name"].endswith("Inc.") for c in dev["companies"])  # "(Class A)" is dropped
    fresh = ad.build_plan("fresh", 6, 8, exclude_batches=["dev"], **kw)
    assert {c["ticker"] for c in fresh["companies"]}.isdisjoint({c["ticker"] for c in dev["companies"]})
    assert {c["accession"] for c in fresh["companies"]}.isdisjoint({c["accession"] for c in dev["companies"]})
    assert fresh["selection"]["excluded_batches"] == {"dev": ad.json_sha(dev)}
    ad.save_plan(fresh, runs)
    # every company is taken: a third batch that excludes neither must refuse rather than share
    with pytest.raises(ValueError, match="would share"):
        ad.build_plan("again", 3, 9, **kw)
    with pytest.raises(ValueError, match="eligible companies are left"):
        ad.build_plan("again", 1, 9, exclude_batches=["dev", "fresh"], **kw)
    # a different plan never silently replaces a saved one
    with pytest.raises(FileExistsError):
        ad.save_plan(ad.build_plan("dev", 6, 99, **{**kw, "runs_dir": tmp_path / "other"}), runs)


def test_plan_source_ids_and_hashes_rebuild_the_exact_prompt(tmp_path):
    snap, uni = make_snapshot(tmp_path, n=3)
    plan = ad.build_plan("dev", 3, 1, runs_dir=tmp_path / "runs", snapshot=snap, universe=uni, exclude_tickers=())
    entry = plan["companies"][0]
    src = entry["source"]
    assert src["sentences"][0] == {"ref": "s1", "id": "EX-99.1:0", "sha256": ad.sha256_text(
        f"{entry['ticker']} Announces Third Quarter 2026 Financial Results"), "lead_sha256": None}
    block = ad.source_block(entry, snap / "release_text")
    assert ad.sha256_text(block) == src["sha256"] and src["figures"] == 14 * 4  # segment no., $, %, %
    entry["source"]["sentences"][1]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed since the plan"):
        ad.source_block(entry, snap / "release_text")


def test_plan_on_the_committed_snapshot_excludes_hand_tested_companies(tmp_path):
    runs = tmp_path / "runs"
    dev = ad.build_plan("dev", 50, 20261102, runs_dir=runs)
    assert dev == ad.build_plan("dev", 50, 20261102, runs_dir=runs)
    ad.save_plan(dev, runs)
    fresh = ad.build_plan("fresh", 50, 20261103, runs_dir=runs, exclude_batches=["dev"])
    tickers = [c["ticker"] for c in dev["companies"] + fresh["companies"]]
    assert len(set(tickers)) == 100 and not set(tickers) & set(ad.HAND_TESTED)
    assert len({c["accession"] for c in dev["companies"] + fresh["companies"]}) == 100
    for plan in (dev, fresh):
        assert sum(plan["selection"]["allocation"].values()) == 50
        assert len(plan["selection"]["allocation"]) == 11  # every GICS sector
        for c in plan["companies"]:
            assert c["source"]["figures"] >= ad.MIN_SOURCE_FIGURES and c["source"]["chars"] <= ad.MAX_SOURCE_CHARS
            assert c["exhibit"] in ad.PRESS_RELEASE_EXHIBITS and c["facts_covering_quarter"] > 0
    assert ad.sha256_text(ad.source_block(dev["companies"][0], ad.SNAPSHOT / "release_text")) == \
        dev["companies"][0]["source"]["sha256"]


# ============================================================ provenance

@pytest.mark.parametrize("draft, source, expected", [
    ("Revenue was $63.9 billion.", "Revenue was $63.9 billion.", "source_match"),
    ("Revenue was $63.9 billion.", "Revenue was $63.94 billion.", "rounded"),
    ("Revenue was $64 billion.", "Revenue was $63.9 billion.", "rounded"),
    ("Margin was 12.5%.", "Margin was 12.46 percent.", "rounded"),
    ("Sales rose 12 percent.", "Sales rose 12%.", "source_match"),
    ("Sales were $1.2 billion.", "Sales were $1,200 million.", "source_match"),
    ("Sales were $1.2 billion.", "Sales were $1,234 million.", "rounded"),
    ("Margin widened 0.6 percentage points.", "Margin widened 60 basis points.", "source_match"),
    ("EPS was $1.23.", "EPS was $1.23 per diluted share.", "source_match"),
    ("Margin was 63.94%.", "Margin was 63.9%.", "not_in_source"),        # more precise than the source
    ("That is 92.5% of total revenue.", "Revenue was $5.0 billion.", "not_in_source"),  # a derived share
    ("Revenue grew 12%.", "Revenue was $12 million.", "not_in_source"),  # a percent never matches an amount
    ("Revenue was $5.2 billion.", "Revenue was $5.3 billion.", "not_in_source"),
])
def test_provenance_classifier(draft, source, expected):
    rows = ad.provenance(draft, f"[s1] {source}")
    assert len(rows) == 1 and rows[0]["provenance"] == expected
    assert rows[0]["source_refs"] == ([] if expected == "not_in_source" else ["s1"])


def test_dates_years_and_quarter_labels_are_not_figures():
    draft = ("# Acme Q3 2026 Earnings Summary\n\nFor the quarter ended September 30, 2026, revenue was "
             "**$5.2 billion**, up 8% from fiscal 2025, as filed on Form 10-Q.")
    assert [f.text for f in ad.figures(draft)] == ["$5.2 billion", "8%"]
    rows = ad.provenance(draft, "[s1] Revenue was $5.2 billion.\n(lead-in) Growth: 8%")
    assert [(r["provenance"], r["source_refs"]) for r in rows] == [("source_match", ["s1"]),
                                                                  ("source_match", ["lead-in"])]


# ============================================================ spend guard

def test_spend_guard_stops_before_the_cap_and_carries_over_runs(tmp_path):
    path = tmp_path / "ledger.jsonl"
    guard = ad.SpendGuard(path, 0.30, stage="write")
    first = guard.reserve(0.14, "A")
    att = first.attempt(0.14, request_sha256="r1", model="m")
    att.settle({"input_tokens": 1}, 0.10)
    first.close()
    assert guard.spent == pytest.approx(0.10) and guard.held == pytest.approx(0)
    second = guard.reserve(0.14, "B")
    with pytest.raises(ad.BudgetStop):            # 0.10 spent + 0.14 held + 0.14 next = 0.38 > 0.30
        guard.reserve(0.14, "C")
    second.attempt(0.14, request_sha256="r2", model="m").failed("http_500")  # sent: the worst case stays charged
    with pytest.raises(ad.BudgetStop):            # a retry would need 0.14 more: 0.24 + 0.14 > 0.30
        second.attempt(0.14, request_sha256="r2", model="m")
    second.close()
    assert ad.ledger_total(path) == pytest.approx(0.24)
    again = ad.SpendGuard(path, 0.30, stage="write")  # the next run starts from the ledger
    assert again.spent == pytest.approx(0.24)
    with pytest.raises(ad.BudgetStop):
        again.reserve(0.07, "D")
    events = [r["event"] for r in ad.read_jsonl(path)]
    assert events == ["dispatch", "settle", "dispatch", "error"]
    with pytest.raises(ValueError):
        ad.SpendGuard(path, 0, stage="write")


class FixedCost(ad.StubWriterClient):
    """The stub writer, but every call costs exactly $0.10 at Wonder prices."""

    def create(self, **req):
        resp = super().create(**req)
        resp.usage = {"input_tokens": 10000, "output_tokens": 3000}
        return resp


def sequential_admissions(plan, snap, cap):
    """(spent, drafts): admit in plan order while spent + the next call's worst case stays within the cap."""
    spent, admitted = 0.0, 0
    for c in plan["companies"]:
        worst = ex.estimate_request_cost(ad.writer_request(c, ad.source_block(c, snap / "release_text")),
                                         ad.WONDER_BACKEND)
        if spent + worst > cap:
            break
        spent, admitted = spent + 0.10, admitted + 1
    return spent, admitted


def test_write_stops_before_the_cap_then_finishes_and_a_rerun_costs_nothing(tmp_path):
    snap, uni = make_snapshot(tmp_path, n=6)
    runs = tmp_path / "runs"
    ad.save_plan(ad.build_plan("b", 6, 3, runs_dir=runs, snapshot=snap, universe=uni, exclude_tickers=()), runs)
    with pytest.raises(ValueError, match="scratch"):
        ad.write_batch("b", 1.0, runs_dir=ad.RUNS_DIR, stub=True, snapshot=snap)
    spent, admitted = sequential_admissions(ad.load_plan(runs, "b"), snap, 0.40)
    assert 1 <= admitted < 6
    client = FixedCost()
    rec = ad.write_batch("b", 0.40, runs_dir=runs, client=client, stub=True, concurrency=1, snapshot=snap,
                         progress=False)
    ledger = runs / "b" / "write_ledger.jsonl"
    assert rec["drafts"] == client.calls == admitted and len(rec["skipped_budget"]) == 6 - admitted
    assert rec["stopped"] and ad.ledger_total(ledger) == pytest.approx(spent) and spent <= 0.40
    rec = ad.write_batch("b", 5.0, runs_dir=runs, client=client, stub=True, snapshot=snap, progress=False)
    assert rec["drafts"] == 6 and not rec["skipped_budget"] and client.calls == 6 and rec["cached"] == admitted
    spent = ad.ledger_total(ledger)
    rec = ad.write_batch("b", 5.0, runs_dir=runs, client=client, stub=True, snapshot=snap, progress=False)
    assert rec["cached"] == 6 and rec["fresh_calls"] == 0 and client.calls == 6 and ad.ledger_total(ledger) == spent
    drafts = ad.read_jsonl(runs / "b" / "drafts.jsonl")
    for d in drafts:
        counts = d["figure_counts"]
        assert counts["not_in_source"] >= 1 and counts["source_match"] >= 10  # the stub plants one invented figure
        assert d["writer"]["stub"] and d["source_block"].startswith("[s1] ")


def test_concurrent_writes_wait_for_held_reservations_instead_of_stopping(tmp_path):
    snap, uni = make_snapshot(tmp_path, n=6)
    runs = tmp_path / "runs"
    ad.save_plan(ad.build_plan("b", 6, 3, runs_dir=runs, snapshot=snap, universe=uni, exclude_tickers=()), runs)
    spent, admitted = sequential_admissions(ad.load_plan(runs, "b"), snap, 0.40)
    client = FixedCost()
    rec = ad.write_batch("b", 0.40, runs_dir=runs, client=client, stub=True, concurrency=4, snapshot=snap,
                         progress=False)
    # four reservations at once would pass the cap, but finished drafts give theirs back: the same drafts get written
    assert rec["drafts"] == client.calls == admitted and rec["stopped"]
    assert ad.ledger_total(runs / "b" / "write_ledger.jsonl") == pytest.approx(spent)


def test_check_admits_drafts_only_within_the_cap(tmp_path):
    snap, uni = make_snapshot(tmp_path, n=6)
    runs = tmp_path / "runs"
    ad.save_plan(ad.build_plan("b", 6, 3, runs_dir=runs, snapshot=snap, universe=uni, exclude_tickers=()), runs)
    ad.write_batch("b", 5.0, runs_dir=runs, stub=True, snapshot=snap, progress=False)
    client = ad.StubSparkClient()
    rec = ad.check_batch("b", 0.40, runs_dir=runs, client=client, stub=True, concurrency=1, snapshot=snap,
                         progress=False)
    ledger = runs / "b" / "check_ledger.jsonl"
    assert rec["statuses"].get("skipped") and rec["stopped"] and ad.ledger_total(ledger) <= 0.40
    assert client.calls == sum(r["event"] == "dispatch" for r in ad.read_jsonl(ledger)) > 0
    rows = ad.read_jsonl(runs / "b" / "checks.jsonl")
    assert {r["status"] for r in rows} <= {"ok", "skipped"}
    assert all(r["reason"] == "budget" for r in rows if r["status"] == "skipped")
    checked = [r for r in rows if r["status"] == "ok"]
    assert checked and all(r["context"] is not None and r["claims"] for r in checked)
    pair = checked[0]["claims"][0]
    assert set(pair["verdict"]) >= {"status", "reason", "explanation", "evidence", "evidence_sources"}
    # --cached-only never calls the model: drafts already extracted re-verify for $0, the rest are skipped
    calls = client.calls
    rec = ad.check_batch("b", 0.40, runs_dir=runs, stub=True, cached_only=True, snapshot=snap, progress=False)
    assert rec["statuses"]["ok"] == len(checked) and client.calls == calls


def test_extraction_guard_admits_only_a_bound_draft_on_the_standard_model(tmp_path):
    backend = ex.make_backend("meta")
    request = ex.build_request("Revenue was $5 billion.", None, backend=backend)
    guard = ad.SpendGuard(tmp_path / "l.jsonl", 1.0, stage="check")
    response = type("R", (), {"usage": {"input_tokens": 10, "output_tokens": 10}})()
    with ad.guarded_extraction(backend):
        with pytest.raises(ex.ModelCallRejected):  # no admitted draft on this thread
            ex.RequestUsage(backend).call(lambda: response, request)
        reservation = guard.reserve(0.5, "X")
        with ad.bound(reservation):
            with pytest.raises(ex.ModelCallRejected):
                ex.RequestUsage(backend).call(lambda: response, {**request, "model": "muse-spark-1.3-contributor"})
            assert ex.RequestUsage(backend).call(lambda: response, request) is response
        reservation.close()
    assert ex.RequestUsage.call is ad.ORIGINAL_REQUEST_CALL
    assert 0 < ad.ledger_total(tmp_path / "l.jsonl") < 0.01


def test_writer_request_is_standard_mode_strict_json_with_the_source(tmp_path):
    snap, uni = make_snapshot(tmp_path, n=3)
    plan = ad.build_plan("dev", 3, 2, runs_dir=tmp_path / "runs", snapshot=snap, universe=uni, exclude_tickers=())
    entry = plan["companies"][0]
    block = ad.source_block(entry, snap / "release_text")
    req = ad.writer_request(entry, block)
    assert req["model"] == "muse-wonder-1.0" and "-contributor" not in req["model"] and req["store"] is False
    assert req["text"]["format"]["strict"] is True and req["reasoning"] == {"effort": "low"}
    user = req["input"][1]["content"]
    assert block in user and f"STYLE ({entry['style']})" in user and "QUARTER: Q3 2026" in user
    assert ad.request_sha(req) == ad.request_sha(ad.writer_request(entry, block))
    assert ad.parse_draft(json.dumps({"draft": "x" * 300}), "completed") == ("x" * 300, None)
    assert ad.parse_draft(json.dumps({"draft": "x" * 300}), "incomplete")[0] is None
    assert ad.parse_draft("not json", "completed")[0] is None


# ============================================================ "?" buckets

def _real(pattern):
    """The first explanation in the frozen full2 live run that contains `pattern` (a real NoCap string)."""
    data = json.loads(FULL2.read_text())
    for rec in data["records"].values():
        for _, verdict in rec["pairs"]:
            if verdict["status"] == "unverifiable" and pattern in verdict["explanation"]:
                return verdict
    raise AssertionError(pattern)


@pytest.mark.parametrize("pattern, bucket, words", [
    ("guidance about a future period", "guidance", None),
    ("constant-currency (FX-neutral)", "constant_currency_or_organic", "constant-currency"),
    ("The wording “organic” describes", "constant_currency_or_organic", "organic"),
    ("The wording “effective income” qualifies", "qualifier_wording", "effective income"),
    ("doesn't track this kind of figure (per share)", "untracked_kind", "per share"),
    ("is a change (decreased), not its level", "untracked_kind", "decreased"),
    ("No press-release fact for Otis Worldwide's non-GAAP free cash flow", "non_gaap_no_release_fact",
     "free cash flow"),
    ("Couldn't tell which Cintas fiscal period", "period", None),
    ("the period the draft measures 60% against", "period", None),
    ("No filing fact for CBRE Group's Resilient Businesses", "other", None),
    ("A margin change written with % could mean", "other", None),
])
def test_bucket_classifier_on_real_explanations(pattern, bucket, words):
    assert ad.bucket_of(_real(pattern)) == (bucket, words)


@pytest.mark.parametrize("explanation, bucket, words", [  # verify.py templates the full2 run (context given) lacks
    ("The draft doesn't say which company this figure belongs to.", "no_company", None),
    ("NoCap couldn't match \"Globex\" to a company it covers, so there is no filing data to check it against.",
     "company_not_matched", "Globex"),
    ("\"Flex\" is an everyday word as well as a company name, so NoCap doesn't match it by itself; write the "
     "company's full name, for example \"Flex Ltd.\".", "company_not_matched", "Flex"),
    ("NoCap's evidence for Microsoft ends with Q1 FY2027, so it can't check Q2 FY2027 yet.", "evidence_ends",
     "Q1 FY2027"),
    ("NoCap couldn't match Q4 FY2027 to a fiscal period labelled in Microsoft's filings.", "period", None),
])
def test_bucket_classifier_on_verifier_templates(explanation, bucket, words):
    assert ad.bucket_of({"status": "unverifiable", "reason": "no_evidence", "explanation": explanation}) == (
        bucket, words)


# ============================================================ report

def test_report_on_a_tiny_fake_batch(tmp_path):
    runs = tmp_path / "runs"
    folder = runs / "tiny"
    folder.mkdir(parents=True)
    ad.write_json(folder / "plan.json", {"batch": "tiny", "companies": [{"ticker": "ACME"}]})
    text = ("Acme revenue was $5.2 billion, up 12%. Net income was $1.1 billion. Adjusted EPS was $1.23. "
            "It expects $6 billion next year. That is 92.5% of sales.")
    block = ("[s1] Revenue was $5.2 billion, up 12.3%.\n[s2] Net income was $1.1 billion.\n"
             "[s3] Adjusted EPS was $1.23.\n[s4] We expect revenue of $6 billion next year.")
    figs = ad.provenance(text, block)
    assert [f["provenance"] for f in figs] == ["source_match", "rounded", "source_match", "source_match",
                                               "source_match", "not_in_source"]
    ad.write_jsonl(folder / "drafts.jsonl", [{"draft_id": "ACME-2026Q3", "ticker": "ACME", "style": "news_story",
                                              "draft": text, "figures": figs, "source_block": block,
                                              "writer": {"stub": False}}])

    def pair(value_text, metric, status, reason, explanation, sources=()):
        start = text.index(value_text)
        return {"claim": {"value_text": value_text, "metric": metric, "metric_text": metric, "span_text": value_text,
                          "span_start": start, "span_end": start + len(value_text)},
                "verdict": {"status": status, "reason": reason, "explanation": explanation,
                            "evidence_sources": list(sources)}}

    claims = [
        pair("$5.2 billion", "revenue", "verified", "match", "The 10-Q reports $5.2 billion.", ["xbrl"]),
        pair("12%", "revenue_growth", "verified", "release_statement", "The release says 12.3%.", ["release_text"]),
        pair("$1.1 billion", "net_income", "wrong", "value_mismatch", "The 10-Q reports $1.0 billion.", ["xbrl"]),
        pair("$1.23", "eps_diluted", "unverifiable", "no_evidence",
             "The wording “Adjusted” qualifies this figure (Adjusted EPS) in a way NoCap can't match to a filing "
             "fact, so there is nothing to check it against."),
        pair("$6 billion", "revenue", "unverifiable", "forward_looking",
             "This is guidance about a future period, so no filing can confirm it yet."),
        pair("92.5%", "other", "wrong", "value_mismatch", "Net income divided by revenue is 21.2%."),
    ]
    ad.write_jsonl(folder / "checks.jsonl", [{"draft_id": "ACME-2026Q3", "ticker": "ACME", "style": "news_story",
                                              "status": "ok", "claims": claims}])
    rep = ad.report_batch("tiny", runs_dir=runs)
    t = rep["totals"]
    assert (t["claims"], t["verified"], t["verified_filed_fact"], t["verified_release_statement"], t["wrong"],
            t["unverifiable"]) == (6, 2, 1, 1, 2, 2)
    assert t["verified_filed_fact_10q_10k"] == 1
    w = rep["wrong"]
    assert [x["value_text"] for x in w["writer_error_caught"]] == ["92.5%"]
    assert [x["value_text"] for x in w["possible_false_alarm"]] == ["$1.1 billion"]
    assert w["possible_false_alarm"][0]["source_lines"] == ["[s2] Net income was $1.1 billion."]
    buckets = {b["bucket"]: b for b in rep["unverifiable"]["buckets"]}
    assert buckets["guidance"]["count"] == 1 and buckets["qualifier_wording"]["top_words"] == [["Adjusted", 1]]
    assert buckets["qualifier_wording"]["examples"][0]["draft_line"] == "Adjusted EPS was $1.23."
    cov = rep["coverage"]
    assert (cov["figures_in_source"], cov["verified_any"], cov["verified_filed_fact"],
            cov["wrong_on_source_figures"]) == (5, 2, 1, 1)
    assert cov["rate_any"] == 0.4 and cov["rate_filed_fact"] == 0.2
    md = (folder / "report.md").read_text()
    assert "possible NoCap false alarm (figure is in the source): **1**" in md
    assert "| Qualifier wording | 1 |" in md and "partly circular" in md
    assert json.loads((folder / "report.json").read_text())["coverage"]["rate_any"] == 0.4
    # a check of an earlier text of the draft is stale: its offsets no longer point at the draft's figures
    ad.write_jsonl(folder / "drafts.jsonl", [{**ad.read_jsonl(folder / "drafts.jsonl")[0], "draft_sha256": "new"}])
    ad.write_jsonl(folder / "checks.jsonl", [{**ad.read_jsonl(folder / "checks.jsonl")[0], "draft_sha256": "old"}])
    rep = ad.build_report("tiny", runs_dir=runs)
    assert rep["per_draft"][0]["status"] == "stale" and not rep["totals"] and not rep["coverage"]["figures_in_source"]


def test_stub_flow_end_to_end_on_the_committed_snapshot(tmp_path):
    runs = tmp_path / "runs"
    assert ad.main(["--runs-dir", str(runs), "plan", "--batch", "dry", "--companies", "3", "--seed", "5"]) == 0
    assert ad.main(["--runs-dir", str(runs), "write", "--batch", "dry", "--budget", "1", "--stub"]) == 0
    assert ad.main(["--runs-dir", str(runs), "check", "--batch", "dry", "--budget", "2", "--stub"]) == 0
    assert ad.main(["--runs-dir", str(runs), "report", "--batch", "dry"]) == 0
    rep = json.loads((runs / "dry" / "report.json").read_text())
    assert rep["stub"] and rep["drafts"]["statuses"] == {"ok": 3} and rep["totals"]["claims"] > 30
    assert "STUB RUN" in (runs / "dry" / "report.md").read_text()
    with pytest.raises(ValueError, match="scratch"):
        ad.main(["check", "--batch", "dry", "--budget", "1", "--stub"])
