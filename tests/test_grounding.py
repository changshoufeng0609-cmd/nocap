"""Exact citation provenance, whole-document bounds and offline-only importer fixtures."""
import hashlib
import inspect
import json
import os

import pytest
from openai.resources.responses import Responses

from receipts import ask, extract, grounding
from test_ask import ProposalClient

TEXT = "Risk factors. Our business depends on continued demand for accelerated computing products. End of filing."
QUOTE = "Our business depends on continued demand for accelerated computing products."


def filing_fixture(directory, text=TEXT):
    sha = hashlib.sha256(text.encode()).hexdigest()
    row = {"id": "NVDA-0001045810-26-000075", "ticker": "NVDA", "cik": 1045810,
           "form": "10-Q", "accession": "0001045810-26-000075", "accepted_at": "2026-08-26T20:36:00Z",
           "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm",
           "sha256": sha, "source_sha256": sha, "characters": len(text)}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{sha}.txt").write_text(text)
    (directory / "index.json").write_text(json.dumps({"version": 1, "filings": [row]}))
    return row


def test_unique_exact_quotes_are_located_and_hallucinated_or_repeated_quotes_rejected():
    proposal = grounding.QuoteProposal(quotes=[QUOTE, "This quote was never present in the filing.", QUOTE])
    rows, rejected = grounding.locate_quotes(proposal, TEXT)
    assert len(rows) == 1 and rejected == 2
    assert TEXT[rows[0]["start"]:rows[0]["end"]] == QUOTE
    assert grounding.locate_quotes(grounding.QuoteProposal(quotes=[QUOTE]), TEXT + TEXT) == ([], 1)


@pytest.mark.parametrize("quote", ["", "short", "a" * 1601, QUOTE.lower()])
def test_quote_length_and_verbatim_guard(quote):
    assert grounding.locate_quotes(grounding.QuoteProposal(quotes=[quote]), TEXT)[0] == []


def test_unicode_offsets_are_python_codepoints():
    text = "🧾 " + TEXT
    rows, _ = grounding.locate_quotes(grounding.QuoteProposal(quotes=[QUOTE]), text)
    assert rows[0]["start"] == text.index(QUOTE)


def test_load_enforces_asof_hash_and_path(tmp_path):
    row = filing_fixture(tmp_path)
    repo = grounding.FilingTexts(tmp_path)
    assert repo.load(row["id"])[1] == TEXT
    with pytest.raises(ask.AskError, match="after"):
        repo.load(row["id"], as_of="2026-08-26T20:00:00Z")
    for ident in ("../.env", "https://attacker.example", row["id"] + "/.."):
        with pytest.raises(ask.AskError):
            repo.load(ident)
    (tmp_path / f"{row['sha256']}.txt").write_text("tampered")
    with pytest.raises(ask.AskError, match="hash"):
        repo.load(row["id"])


def test_symlink_text_is_not_read(tmp_path):
    directory = tmp_path / "texts"
    row = filing_fixture(directory)
    p = directory / f"{row['sha256']}.txt"
    p.unlink()
    target = tmp_path / "outside.txt"
    target.write_text(TEXT)
    p.symlink_to(target)
    with pytest.raises(ask.AskError):
        grounding.FilingTexts(directory).load(row["id"])


@pytest.mark.parametrize("url", ["https://attacker.example/file.htm",
    "https://www.sec.gov@attacker.example/file.htm", "http://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda.htm",
    "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/../../secret.htm",
    "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda.htm?redirect=evil"])
def test_noncanonical_sec_source_is_rejected(tmp_path, url):
    row = filing_fixture(tmp_path)
    row["url"] = url
    (tmp_path / "index.json").write_text(json.dumps({"version": 1, "filings": [row]}))
    with pytest.raises(ask.AskError):
        grounding.FilingTexts(tmp_path).entries()


def test_whole_document_is_sent_and_context_overflow_not_truncated(tmp_path):
    row = filing_fixture(tmp_path)
    request = grounding.build_request("What risks does the filing discuss?", row, TEXT)
    assert json.loads(request["input"])["complete_filing_text"] == TEXT
    assert not extract.strict_subset_problems(request["text"]["format"]["schema"])
    with pytest.raises(ask.AskError, match="not truncated or sent"):
        grounding.build_request("What risks?", row, "x" * grounding.CONTEXT_TOKEN_LIMIT)


def test_the_filing_prefix_comes_before_the_question_under_one_cache_key_per_filing(tmp_path):
    # Meta's prefix cache reuses a request up to its first differing token (docs: prompt-caching), so two
    # questions about one filing must share everything through its complete text and route under one key.
    row = filing_fixture(tmp_path)
    first = grounding.build_request("What risks does the filing discuss?", row, TEXT)
    second = grounding.build_request("How much did capital expenditures grow?", row, TEXT)
    assert list(json.loads(first["input"])) == ["filing", "complete_filing_text", "question"]
    shared = os.path.commonprefix([first["input"], second["input"]])
    assert json.dumps(TEXT, ensure_ascii=False) in shared and "capital" not in shared
    assert {k: v for k, v in first.items() if k != "input"} == {k: v for k, v in second.items() if k != "input"}
    assert first["prompt_cache_key"] == f"nocap-filing-{row['sha256'][:16]}"
    assert first["prompt_cache_retention"] == "24h"
    amended = TEXT + " Amended."
    other = grounding.build_request("What risks?", filing_fixture(tmp_path / "other", amended), amended)
    assert other["prompt_cache_key"] != first["prompt_cache_key"]
    # Every field is a named parameter of the pinned OpenAI SDK, so nothing is dropped or rejected client-side.
    assert set(first) <= set(inspect.signature(Responses.create).parameters)


def test_extractive_result_is_not_marked_financially_verified(tmp_path):
    row = filing_fixture(tmp_path)
    request = grounding.build_request("What risks?", row, TEXT)
    stats = ask.AskStats()
    model = ProposalClient({"quotes": [QUOTE, "An unsupported fabricated quotation is rejected."]})
    events = list(grounding.grounding_events("What risks?", request=request, filing=row, text=TEXT,
                 stats=stats, client_factory=lambda: model, before_model_call=lambda _: None))
    result = events[-1][1]
    assert events[-1][0] == "result" and result["rejected_quotes"] == 1
    assert result["quotes"][0]["text"] == QUOTE and "verdict" not in result["quotes"][0]
    assert "not independently verified" in result["notice"] and stats.model_calls == 1
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 0}  # provider-reported


def test_result_reports_no_token_usage_when_the_provider_omits_it(tmp_path):
    row = filing_fixture(tmp_path)
    request = grounding.build_request("What risks?", row, TEXT)
    events = list(grounding.grounding_events("What risks?", request=request, filing=row, text=TEXT,
                  stats=ask.AskStats(), client_factory=lambda: ProposalClient({"quotes": [QUOTE]}, usage=False),
                  before_model_call=lambda _: None))
    assert events[-1][0] == "result" and events[-1][1]["usage"] is None


def test_normalization_drops_scripts_and_preserves_visible_text():
    result = grounding.normalize_filing_html(b'<html><style>.hidden {}</style><body><p>Our  business</p><p>has risks.</p><script>EVIL</script><div hidden>HIDDEN</div><div style="display: none"><span>HIDDEN</span></div></body></html>')
    assert result == "Our business has risks." and "EVIL" not in result


def test_importer_uses_only_registered_sec_document(tmp_path):
    row = filing_fixture(tmp_path / "fixture")
    class Store:
        def by_ticker(self, _):
            return row["cik"], row["ticker"]
        def filings(self, *_):
            return [row]
    class Edgar:
        calls = []
        def get(self, url):
            self.calls.append(url)
            return f"<html><body>{TEXT}</body></html>".encode()
    edgar = Edgar()
    saved = grounding.import_filing(Store(), edgar, ticker="NVDA", form="10-Q", directory=tmp_path / "result")
    assert edgar.calls == [row["url"]]
    assert grounding.FilingTexts(tmp_path / "result").load(saved["id"])[1] == TEXT


def test_importer_finds_a_word_ticker_company_by_its_stored_ticker(tmp_path):
    """COST never resolves by itself as a word; the importer looks the operator's --ticker up as a ticker."""
    from test_snapshot_filings import word_ticker_filing
    _src, row, store, edgar = word_ticker_filing(tmp_path)
    try:
        saved = grounding.import_filing(store, edgar, ticker="COST", form="10-Q", directory=tmp_path / "result")
        assert (saved["id"], saved["cik"]) == (row["id"], 909832)
        with pytest.raises(ask.AskError, match="covered ticker"):
            grounding.import_filing(store, edgar, ticker="Costco", form="10-Q", directory=tmp_path / "result")
    finally:
        store.close()


def test_import_cli_loads_dotenv_and_reports_missing_contact_without_traceback(monkeypatch, tmp_path, capsys):
    loaded, closed = [], []
    monkeypatch.setattr(grounding, "load_dotenv", lambda path, **kw: loaded.append((path, kw)))
    class Store:
        def __init__(self, *_):
            pass
        def close(self):
            closed.append("store")
    class Edgar:
        def __init__(self, **_):
            pass
        def close(self):
            closed.append("edgar")
    monkeypatch.setattr(grounding, "Store", Store)
    monkeypatch.setattr(grounding, "Edgar", Edgar)
    def missing_contact(*_, **__):
        raise grounding.EdgarError("SEC_USER_AGENT missing PRIVATE_SECRET_SENTINEL")
    monkeypatch.setattr(grounding, "import_filing", missing_contact)
    assert grounding.main(["--ticker", "NVDA", "--form", "10-Q", "--data-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert loaded == [(grounding.ROOT / ".env", {"override": False})]
    assert closed == ["store", "edgar"]
    assert not captured.out and "real name and contact email" in captured.err
    assert "Traceback" not in captured.err and "PRIVATE_SECRET_SENTINEL" not in captured.err


def test_import_cli_missing_store_has_safe_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(grounding, "load_dotenv", lambda *_, **__: None)
    def missing_store(*_):
        raise FileNotFoundError("PRIVATE_SECRET_PATH")
    monkeypatch.setattr(grounding, "Store", missing_store)
    assert grounding.main(["--ticker", "NVDA", "--form", "10-Q", "--data-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "PRIVATE_SECRET_PATH" not in captured.err and "Traceback" not in captured.err
