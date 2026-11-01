"""scripts/topup_latest_quarter.py: planning from cached Submissions, the step order, a stop on any non-zero exit code,
no User-Agent in any output, and an offline dry run. SYNTHETIC fixture data in a temporary repo; the step commands run
through a fake, so no test touches SEC, the model API or the real data."""
import gzip
import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import scripts.topup_latest_quarter as topup
from receipts import edgar
from receipts.store import connect_rw

UA = "Tess Tester tess.tester@example.org"
CIKS = {"AAA": 101, "BBB": 102, "CCC": 103, "DDD": 104}
AAA_8K_NEW, AAA_10Q_NEW = "0000000101-26-000020", "0000000101-26-000021"
AAA_8K_OLD, AAA_10Q_OLD = "0000000101-26-000010", "0000000101-26-000011"
CCC_8K_NEW = "0000000103-26-000030"
WINDOW = ("2023-09-14", "2023-05-17")
HELD = "revenue: contract revenue blocked until confirmed (3 figures): scripts/confirm_revenue.py checks them"
USAGE = {"input_tokens": 10000, "cached_tokens": 0, "output_tokens": 2000, "reasoning_tokens": 0}  # $0.021


def filing(accession, form, filed, accepted, report=None, items=""):
    return {"accessionNumber": accession, "form": form, "filingDate": filed, "reportDate": report or "",
            "acceptanceDateTime": accepted, "primaryDocument": "doc.htm", "items": items}


SUBMISSIONS = {
    "AAA": [filing(AAA_10Q_NEW, "10-Q", "2026-11-02", "2026-11-02T12:00:00.000Z", "2026-09-30"),
            filing(AAA_8K_NEW, "8-K", "2026-10-28", "2026-10-28T20:00:00.000Z", "2026-10-28", "2.02,9.01"),
            filing(AAA_10Q_OLD, "10-Q", "2026-08-01", "2026-08-01T12:00:00.000Z", "2026-06-30"),
            filing(AAA_8K_OLD, "8-K", "2026-07-28", "2026-07-28T20:00:00.000Z", "2026-07-28", "2.02,9.01")],
    "BBB": [filing("0000000102-26-000050", "4", "2026-11-01", "2026-11-01T12:00:00.000Z"),
            filing("0000000102-26-000040", "8-K", "2026-11-01", "2026-11-01T13:00:00.000Z", "2026-11-01", "5.02"),
            filing("0000000102-26-000030", "10-Q", "2026-10-29", "2026-10-29T12:00:00.000Z", "2026-09-30")],
    "CCC": [filing(CCC_8K_NEW, "8-K", "2026-11-01", "2026-11-01T20:00:00.000Z", "2026-11-01", "2.02,9.01"),
            filing("0000000103-26-000020", "10-Q", "2026-08-01", "2026-08-01T12:00:00.000Z", "2026-06-30")],
    "DDD": [filing("0000000104-26-000020", "10-Q", "2026-08-02", "2026-08-02T12:00:00.000Z", "2026-06-30")],
}
# The newest filing each company has in the snapshot (AAA's earnings 8-K is in; its 10-Q came after).
NEWEST = {"AAA": (AAA_8K_NEW, "8-K", "2026-10-28T20:00:00Z"), "BBB": ("0000000102-26-000030", "10-Q",
          "2026-10-29T12:00:00Z"), "CCC": ("0000000103-26-000020", "10-Q", "2026-08-01T12:00:00Z"),
          "DDD": ("0000000104-26-000020", "10-Q", "2026-08-02T12:00:00Z")}


def fill_store(path: Path, *, built_at: str = "2026-10-31T00:00:00Z") -> None:
    con = connect_rw(path)
    with con:
        con.executemany("INSERT INTO meta VALUES (?, ?)", [("built_at", built_at), ("facts_start", WINDOW[0]),
                                                           ("filings_start", WINDOW[1]), ("quarters", "8")])
        for t, cik in CIKS.items():
            acc, form, accepted = NEWEST[t]
            con.execute("INSERT INTO companies VALUES (?, ?, ?, ?, ?)", (cik, t, f"{t} Corp", json.dumps([t.lower()]),
                                                                         "1231"))
            con.execute("INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (acc, cik, form, accepted[:10], accepted, None, "", "doc.htm", f"https://sec.example/{acc}"))
            con.execute("INSERT INTO fiscal_periods VALUES (?, ?, ?, ?, ?, ?)",
                        (cik, 2026, "Q2", "2026-04-01", "2026-06-30", acc))
            con.execute("INSERT INTO facts (cik, ticker, metric, concept, basis, segment, value, unit, period_start, "
                        "period_end, fiscal_year, fiscal_period, source, derived_from, form, accession, accepted_at, "
                        "url, locator, decimals, period_kind) VALUES (?, ?, 'revenue', 'Revenues', 'GAAP', NULL, ?, "
                        "'USD', '2026-04-01', '2026-06-30', 2026, 'Q2', 'xbrl', '[]', ?, ?, ?, ?, NULL, -6, 'quarter')",
                        (cik, t, 1e9 + cik, form, acc, accepted, f"https://sec.example/{acc}"))
    con.close()


def make_repo(tmp_path: Path, *, cache=("AAA", "BBB", "CCC")) -> Path:
    root = tmp_path / "repo"
    (root / "data/snapshot").mkdir(parents=True)
    fill_store(root / "data/snapshot/facts.sqlite")
    (root / "data/snapshot/manifest.json").write_text(json.dumps(
        {"fingerprint": "f" * 24, "built_at": "2026-10-31T01:00:00Z", "source_built_at": "2026-10-31T00:00:00Z"}))
    fill_store(root / "data/facts.sqlite")
    cache_dir = root / "data/cache/edgar"
    cache_dir.mkdir(parents=True)
    for t in cache:
        rows = SUBMISSIONS[t]
        body = json.dumps({"cik": str(CIKS[t]), "name": f"{t} Corp", "filings": {
            "recent": {k: [r[k] for r in rows] for k in rows[0]}, "files": []}}).encode()
        url = topup.SUBMISSIONS_URL.format(cik=CIKS[t])
        key = hashlib.sha256(url.encode()).hexdigest()
        (cache_dir / f"{key}.bin").write_bytes(body)
        (cache_dir / f"{key}.json").write_text(json.dumps({
            "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}))
    pr = root / "data/pr_facts"
    for t, acc in (("AAA", AAA_8K_OLD), ("BBB", "0000000102-26-000020")):
        (pr / t).mkdir(parents=True)
        (pr / t / f"{acc}.jsonl").write_text('{"synthetic": true}\n')
        (pr / t / f"{acc}.report.json").write_text(json.dumps({
            "ticker": t, "cik": CIKS[t], "accession": acc, "accepted_at": "2026-07-28T20:00:00+00:00",
            "model_calls": [{"tables": [0], "status": "ok", "cached": False, "usage": USAGE}]}))
    spark = root / "data/cache/spark"
    spark.mkdir(parents=True)
    (spark / "press_release_spend.jsonl").write_text('{"cost_usd": 1.0}\n{"cost_usd": 0.5}\n')
    return root


def plan_args(out: Path, *extra: str):
    return topup.parse_args(["--auto", "--dry-run", "--out-dir", str(out), *extra])


@pytest.fixture(autouse=True)
def no_sec(monkeypatch):
    """Belt and braces: any live EDGAR fetch fails at once instead of retrying for half a minute."""
    def refuse(self, url, attempts=6):
        raise edgar.EdgarError(f"test: no live SEC request ({url})")
    monkeypatch.setattr(edgar.Edgar, "_fetch", refuse)


# --------------------------------------------------------------------------- planning
def test_plan_from_cached_submissions(tmp_path):
    root = make_repo(tmp_path)
    out = tmp_path / "out"
    plan = topup.build_plan(root, out, plan_args(out, "--max-ledger-usd", "10"), offline=True, ua=None)
    assert [c["ticker"] for c in plan["companies"]] == ["AAA", "CCC"]  # BBB filed only a Form 4 and an Item 5.02 8-K
    aaa, ccc = plan["companies"]
    assert [f["accession"] for f in aaa["new_filings"]] == [AAA_10Q_NEW]
    assert [(p["eightk"], p["report"]) for p in aaa["new_pairs"]] == [(AAA_8K_NEW, AAA_10Q_NEW)]
    assert aaa["map_quarters"] == 1 and aaa["estimate_usd"] == pytest.approx(0.021)
    assert aaa["estimate_basis"] == "mean of its last 1 releases"
    # CCC's earnings 8-K has no paired 10-Q yet: ingested, not mapped, no cost
    assert [f["accession"] for f in ccc["new_filings"]] == [CCC_8K_NEW]
    assert ccc["new_pairs"] == [] and ccc["map_quarters"] == 0 and ccc["estimate_usd"] == 0
    assert plan["submissions"]["not_cached"] == ["DDD"] and plan["submissions"]["live_requests"] == 0
    assert plan["batches"] == [{"batch": 1, "tickers": ["AAA", "CCC"], "map_tickers": ["AAA"], "map_quarters": 1,
                                "new_releases": 1, "estimate_usd": 0.021}]
    assert plan["ledger_usd"] == 1.5 and plan["guard"][0]["ok"] is True
    assert plan["window"]["pinned_today"] == "2026-10-31"  # 2023-09-14 + 1,143 days: the stored window's date

    # an 8-K that already has a release file is not mapped again; --tickers keeps a company with nothing new
    (root / "data/pr_facts/AAA" / f"{AAA_8K_NEW}.report.json").write_text("{}")
    plan = topup.build_plan(root, out, topup.parse_args(["--tickers", "AAA,BBB", "--dry-run", "--out-dir", str(out)]),
                            offline=True, ua=None)
    assert [(c["ticker"], c["map_quarters"]) for c in plan["companies"]] == [("AAA", 0), ("BBB", 0)]
    assert plan["estimate_usd"] == 0


def test_plan_guard_stops_a_batch_over_the_cap_and_resumes_after_completed_batches(tmp_path):
    root = make_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    plan = topup.build_plan(root, out, plan_args(out, "--max-ledger-usd", "1.51"), offline=True, ua=None)
    assert plan["guard"][0]["ok"] is False  # 1.5 + 1.15 x 0.021 > 1.51
    (out / "batch_1.json").write_text(json.dumps({"batch": 1, "tickers": ["AAA"], "ok": True}))
    plan = topup.build_plan(root, out, plan_args(out), offline=True, ua=None)
    assert plan["skipped_done_earlier"] == ["AAA"] and [b["batch"] for b in plan["batches"]] == [2]
    assert plan["batches"][0]["tickers"] == ["CCC"]


def test_dry_run_makes_no_network_call_and_starts_no_process(tmp_path, monkeypatch, capsys):
    root = make_repo(tmp_path)
    out = tmp_path / "out"

    def refuse(*a, **k):
        raise AssertionError("the dry run must not touch the network or start a process")

    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    before = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))
    assert topup.main(["--auto", "--dry-run", "--repo-root", str(root), "--out-dir", str(out),
                       "--max-ledger-usd", "10"]) == 0
    text = capsys.readouterr().out
    assert "mapping-cost estimate $0.0210" in text and "batch 1: 2 companies" in text and "ok" in text
    assert json.loads((out / "plan.json").read_text())["mode"] == "dry-run"
    assert sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*")) == before
    assert sorted(p.name for p in out.iterdir()) == ["plan.json"]


# --------------------------------------------------------------------------- a whole run through fake steps
PREFLIGHT = [("preflight", n) for n in ("split_check", "dev_replay", "scratch_snapshot", "snapshot_check")]
BATCH_STEPS = {  # each command of batch 1, in order, and the exit code its failure must give
    "ingest": 13, "archive_companyfacts_sync": 13, "map_dry_run": 14, "map": 14, "confirm_revenue": 15,
    "reingest_confirmed": 15, "relabel_dry": 16, "release_gates": 17, "reingest_rejected": 17,
    "release_gates_dry": 17, "relabel_dry3": 17, "xbrl_gate_rerun": 17, "dev_replay": 19, "scratch_snapshot": 20,
    "snapshot_check": 20, "split_check": 21, "archive_companyfacts": 22, "archive_exhibits": 22}
SEQUENCE = PREFLIGHT + [("batch_1", n) for n in BATCH_STEPS]
BASELINE = {"0123456789abcdef": [["c1", "$1.0 billion", 10, "verified", "match", "ok"]]}
SCORES = {"nvda-ai-story": {"claims": 11, "counts": {"verified": 7, "wrong": 2}}}


class FakeSteps:
    """Stands in for run_command: writes what each step's real command would leave behind and prints its key lines.
    `fail` names one (folder, command) that exits 1; `leak` puts the User-Agent into the output."""

    def __init__(self, root: Path, *, fail=None, leak=False, ingest_failure=False, touch_outside=False):
        self.root, self.fail, self.leak = root, fail, leak
        self.ingest_failure, self.touch_outside = ingest_failure, touch_outside
        self.calls, self.protected = [], set()

    def __call__(self, argv, *, cwd, log, scrub, protect=False):
        key = (log.parent.name, log.stem)
        self.calls.append(key)
        if protect:
            self.protected.add(key)
        assert cwd == self.root
        text = self.output(log.stem, [str(a) for a in argv])
        if self.leak:
            text = text.replace("cache hits.", f"cache hits ({UA}).") + f"User-Agent: {UA}\n"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(scrub(text))  # what run_command writes; the returned text stays raw on purpose
        return (1 if key == self.fail else 0), text

    def write(self, argv, obj):
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(obj))

    def output(self, name, argv):
        if name.startswith(("ingest", "reingest")):
            tickers = argv[argv.index("--tickers") + 1].split(",")
            assert "--pin-date" in argv and argv[argv.index("--pin-date") + 1] == "2026-10-31"
            assert ("--refresh" in argv) == (name == "ingest") and ("--offline" in argv) == (name != "ingest")
            audit = self.root / "data/ingest_audit"
            audit.mkdir(exist_ok=True)
            for t in tickers:
                if self.ingest_failure and t == "CCC":
                    continue
                warnings = [HELD] if name == "ingest" and t == "AAA" else []
                (audit / f"{t}.json").write_text(json.dumps({"cik": CIKS[t], "ticker": t, "warnings": warnings}))
            con = sqlite3.connect(self.root / "data/facts.sqlite")
            with con:
                con.execute("INSERT OR IGNORE INTO filings VALUES (?, ?, '10-Q', '2026-11-02', "
                            "'2026-11-02T12:00:00Z', '2026-09-30', '', 'doc.htm', 'https://sec.example/new')",
                            (AAA_10Q_NEW, CIKS["AAA"]))
                if self.touch_outside:
                    con.execute("UPDATE companies SET name = 'BBB Changed' WHERE ticker = 'BBB'")
            con.close()
            failed = "\nCCC  FAILED: boom" if self.ingest_failure else ""
            return (f"NoCap ingest: {len(tickers)} companies; facts ending since {WINDOW[0]}, filings since "
                    f"{WINDOW[1]} -> data/facts.sqlite{failed}\nEDGAR: 2 live requests, 0 cache hits. "
                    "All companies ingested.\n")
        if name == "map_dry_run":
            assert argv[argv.index("--tickers") + 1:argv.index("--quarters")] == ["AAA"]
            return "dry run: 2 model calls planned, 0 cached, ~$0.040 for the rest; ledger so far $1.5000\n"
        if name == "map":
            assert argv[argv.index("--budget-usd") + 1] == "1.56"  # 1.5 + 1.15 x 0.040 + 0.01
            folder = self.root / "data/pr_facts/AAA"
            (folder / f"{AAA_8K_NEW}.jsonl").write_text('{"synthetic": "new"}\n')
            (folder / f"{AAA_8K_NEW}.report.json").write_text(json.dumps(
                {"facts": 1, "model_calls": [{"status": "ok", "usage": USAGE}]}))
            return ("Spark: live calls 2, cached 0, spend this run $0.0420, ledger total $1.5420 (cap $1.56)\n"
                    "SEC: live requests 3, cache hits 1\nXBRL gate: 1 files; facts 1 -> 1\n")
        if name == "confirm_revenue":
            assert argv[-1] == "AAA"
            return "AAA    CONFIRMED: every compared period agrees with the releases (4 figures compared)\n"
        if name.startswith("relabel_dry"):
            self.write(argv, {"facts_read": 2, "changed": 0, "period_not_in_store": 0, "changes": []})
            return "would change 0 of 2 release facts\n"
        if name == "release_gates":
            self.write(argv, {"dropped": [], "files_changed": 0, "rejections": {"AAA": [{}]},
                              "new_rejections": {"AAA": [{}]}, "reingest": ["AAA"]})
            return "dropped 0 release facts\n"
        if name == "release_gates_dry":
            self.write(argv, {"dropped": [], "files_changed": 0, "rejections": {}, "new_rejections": {},
                              "reingest": []})
            return "would drop 0 release facts\n"
        if name == "xbrl_gate_rerun":
            self.write(argv, {"files": 3, "facts": 3, "dropped": [], "quarantined": [], "missing_report": []})
            return "XBRL gate re-run: 3 files\n"
        if name == "dev_replay":
            self.write(argv, BASELINE)
            return "drafts 1 claims 1\n"
        if name == "scratch_snapshot":
            return "data/snapshot: 1.00 MB · 4 companies\n"
        if name == "snapshot_check":
            self.write(argv, {"recordings": {"slide": True, "grounding": True, "private": True},
                              "qualifier_grid_identical": True, "examples": SCORES, "examples_expected": SCORES,
                              "examples_identical": True, "snapshot_bytes": 10, "facts_sqlite_bytes": 5})
            return "{}\n"
        if name == "split_check":
            return "3 splits; committed files are current\n"
        if name.startswith("archive"):
            return "archived\n"
        raise AssertionError(f"unexpected command {name}: {argv}")


def real_run(tmp_path, monkeypatch, fake_kwargs=None, extra=()):
    root = make_repo(tmp_path, cache=("AAA", "BBB", "CCC", "DDD"))
    out = tmp_path / "out"
    baseline = tmp_path / "dev-baseline.json"
    baseline.write_text(json.dumps(BASELINE))
    fake = FakeSteps(root, **(fake_kwargs or {}))
    monkeypatch.setattr(topup, "run_command", fake)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    code = topup.main(["--auto", "--repo-root", str(root), "--out-dir", str(out), "--dev-baseline", str(baseline),
                       "--max-ledger-usd", "10", "--disk-floor-gib", "0", *extra])
    return code, fake, root, out


def test_a_real_run_takes_every_step_in_order(tmp_path, monkeypatch):
    code, fake, root, out = real_run(tmp_path, monkeypatch)
    assert code == 0
    assert fake.calls == SEQUENCE
    assert fake.protected == {("batch_1", "archive_companyfacts"), ("batch_1", "archive_exhibits")}
    report = json.loads((out / "batch_1.json").read_text())
    assert report["ok"] is True and report["step_order"] == [name for name, _ in topup.STEPS]
    steps = report["steps"]
    assert steps["spend_guard"]["check"] == "1.5000 + 1.15 x 0.0210 = 1.5241 vs 10.00"
    assert steps["release_mapping"]["written"] == [{"ticker": "AAA", "accession": AAA_8K_NEW, "file": "live",
                                                    "facts": 1, "calls": 1}]
    assert steps["revenue_confirmation"]["confirmed"] == ["AAA"] and steps["release_gates"]["reingest"] == ["AAA"]
    proof = steps["old_rows_proof"]
    assert proof["ok"] is True and proof["tables"]["filings"]["batch_added"] == 1
    assert proof["tables"]["filings"]["outside_batch_changed"] == 0
    assert proof["pr_files"]["batch_added"] == [f"AAA/{AAA_8K_NEW}.jsonl", f"AAA/{AAA_8K_NEW}.report.json"]
    assert steps["dev_replay"]["identical"] is True
    assert json.loads((out / "summary.json").read_text())["ok"] is True
    assert json.loads((out / "preflight.json").read_text())["ok"] is True


@pytest.mark.parametrize("failing", SEQUENCE, ids=[f"{a}/{b}" for a, b in SEQUENCE])
def test_a_non_zero_exit_code_anywhere_stops_the_run(tmp_path, monkeypatch, failing):
    code, fake, root, out = real_run(tmp_path, monkeypatch, {"fail": failing}, extra=("--batch-size", "1"))
    assert fake.calls == SEQUENCE[:SEQUENCE.index(failing) + 1]  # nothing after the failing command, no batch 2
    if failing[0] == "preflight":
        assert code == topup.EXIT_PREFLIGHT and not (out / "batch_1.json").exists()
        assert json.loads((out / "preflight.json").read_text())["ok"] is False
        return
    assert code == BATCH_STEPS[failing[1]]
    report = json.loads((out / "batch_1.json").read_text())
    step = next(name for name, c in topup.STEPS if c == code)
    assert report["ok"] is False and report["failed"]["step"] == step and report["step_order"][-1] == step
    assert f"{failing[1]} exited 1" in report["failed"]["reason"]
    assert not (out / "batch_2.json").exists()
    assert json.loads((out / "summary.json").read_text())["ok"] is False


def test_ingest_exit_zero_with_a_failed_company_still_stops(tmp_path, monkeypatch):
    """Phase B's batch 3 lost 7 companies behind an accepted exit code: ingest's own failure list counts too."""
    code, fake, root, out = real_run(tmp_path, monkeypatch, {"ingest_failure": True})
    assert code == 13 and fake.calls[-1] == ("batch_1", "ingest")
    reason = json.loads((out / "batch_1.json").read_text())["failed"]["reason"]
    assert "ingest FAILED for CCC" in reason and "no audit file written for CCC" in reason


def test_a_change_outside_the_batch_fails_the_old_rows_proof(tmp_path, monkeypatch):
    code, fake, root, out = real_run(tmp_path, monkeypatch, {"touch_outside": True})
    assert code == 18 and fake.calls[-1] == ("batch_1", "xbrl_gate_rerun")
    report = json.loads((out / "batch_1.json").read_text())
    assert "companies: rows changed for 1 companies outside the batch: BBB" in report["failed"]["reason"]


def test_the_user_agent_never_lands_in_any_output(tmp_path, monkeypatch):
    code, fake, root, out = real_run(tmp_path, monkeypatch, {"leak": True})
    assert code == 0
    email = UA.split()[-1]
    for path in out.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            text = (gzip.decompress(data) if path.suffix == ".gz" else data).decode("utf-8", "replace")
            assert UA not in text and email not in text, path.name
    report = (out / "batch_1.json").read_text()
    assert "<redacted>" in report  # the leaked EDGAR line was kept, scrubbed
    for name in ("batch_1.json", "plan.json", "preflight.json", "summary.json"):
        text = (out / name).read_text()
        assert str(tmp_path) not in text and str(tmp_path.resolve()) not in text, name


def test_run_command_scrubs_a_child_s_output_before_it_reaches_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    scrub = topup.Scrubber(topup.secrets_from_env({"SEC_USER_AGENT": UA}), [(tmp_path, "<out>")])
    log = tmp_path / "child.log"
    code, text = topup.run_command(
        [sys.executable, "-c", "import os, sys; print(os.environ['SEC_USER_AGENT'], sys.argv[1]); sys.exit(3)",
         str(tmp_path / "x")], cwd=tmp_path, log=log, scrub=scrub)
    assert code == 3 and UA not in log.read_text() and UA not in text
    assert log.read_text() == text == "<redacted> <out>/x\n"


def test_a_ctrl_c_during_a_protected_step_waits_for_it_then_stops(tmp_path):
    """The archive scripts save their index only at the end, so Ctrl-C must not cut them short."""
    import os
    import signal
    import threading

    log = tmp_path / "archive.log"
    handler = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    with pytest.raises(KeyboardInterrupt):
        topup.run_command([sys.executable, "-c", "import time; time.sleep(1.5); print('index saved')"],
                          cwd=tmp_path, log=log, scrub=topup.Scrubber([], []), protect=True)
    timer.join()
    assert log.read_text() == "index saved\n"  # the child ran to the end before the run stopped
    assert signal.getsignal(signal.SIGINT) is handler


def test_a_real_run_refuses_without_the_user_agent_or_on_a_stale_snapshot(tmp_path, monkeypatch, capsys):
    root = make_repo(tmp_path, cache=("AAA", "BBB", "CCC", "DDD"))
    baseline = tmp_path / "dev-baseline.json"
    baseline.write_text(json.dumps(BASELINE))
    monkeypatch.setattr(topup, "run_command", FakeSteps(root))
    argv = ["--auto", "--repo-root", str(root), "--out-dir", str(tmp_path / "out"), "--dev-baseline", str(baseline),
            "--max-ledger-usd", "10"]
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert topup.main(argv) == topup.EXIT_PREFLIGHT
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    con = sqlite3.connect(root / "data/facts.sqlite")
    with con:
        con.execute("UPDATE meta SET value = '2026-11-01T00:00:00Z' WHERE key = 'built_at'")
    con.close()
    assert topup.main(argv) == topup.EXIT_PREFLIGHT
    assert "rebuild the snapshot" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        topup.main(["--auto", "--repo-root", str(root), "--out-dir", str(tmp_path / "out")])  # no baseline, no cap


def test_the_ingest_helper_pins_today(monkeypatch):
    import receipts.ingest as ing

    seen = {}
    monkeypatch.setattr(ing, "date", ing.date)  # restored after the test
    monkeypatch.setattr(ing, "main", lambda argv: seen.update(today=ing.date.today(), argv=argv) or 0)
    assert topup.helper_ingest(topup.REPO, date(2026, 10, 31), ["--tickers", "AAA", "--offline"]) == 0
    assert seen == {"today": date(2026, 10, 31), "argv": ["--tickers", "AAA", "--offline"]}


def test_old_rows_proof_counts_the_batch_and_guards_the_rest(tmp_path):
    root = make_repo(tmp_path)
    before = topup.row_state(root, [CIKS["AAA"]])
    con = sqlite3.connect(root / "data/facts.sqlite")
    with con:
        con.execute("UPDATE facts SET fiscal_period = 'Q3' WHERE ticker = 'AAA'")  # a relabelled AAA fact
        con.execute("DELETE FROM fiscal_periods WHERE cik = ?", (CIKS["AAA"],))
    con.close()
    after = topup.row_state(root, [CIKS["AAA"]])
    names = {str(c): t for t, c in CIKS.items()}
    proof = topup.old_rows_proof(before, after, {str(CIKS["AAA"])}, {"AAA"}, names)
    assert proof["ok"] is True
    assert proof["tables"]["facts"]["batch_removed_changed"] == 1 and proof["tables"]["facts"]["batch_added"] == 1
    assert proof["batch_companies_with_removals"] == {"AAA": {"facts": 1, "fiscal_periods": 1}}
    proof = topup.old_rows_proof(before, after, set(), set(), names)  # the same changes, AAA not in the batch
    assert proof["ok"] is False and len(proof["violations"]) == 2
