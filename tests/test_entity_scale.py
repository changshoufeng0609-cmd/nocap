"""Entity matching at S&P 500 scale (receipts.store): case-aware tickers, the word lists, legal-suffix names, phrases two
companies share, lookups by a stored ticker, constituent aliases and accent folding, display names and suggestions.

The universe checks read eval/evidence_expansion/sp500-aliases-20261031.json (499 constituents: ticker, CIK, SEC title,
Security name, GICS sub-industry) and the committed data/snapshot; no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from receipts import store as st
from receipts import verify
from receipts.store import Store, company_aliases, connect_rw, normalize_entity, replace_company
from test_store import word_tickers  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
ALIAS_RECORD = ROOT / "eval" / "evidence_expansion" / "sp500-aliases-20261031.json"
SNAPSHOT = ROOT / "data" / "snapshot" / "facts.sqlite"
DICTIONARY = Path("/usr/share/dict/words")


def make_store(tmp_path, companies, *, aliases=company_aliases):
    """companies: {ticker: SEC name}; aliases(ticker, name) gives the stored keys."""
    conn = connect_rw(tmp_path / "facts.sqlite")
    for i, (ticker, name) in enumerate(companies.items()):
        replace_company(conn, {"cik": 5000 + i, "ticker": ticker, "name": name, "aliases": aliases(ticker, name),
                               "fiscal_year_end": "1231"}, [], [], [])
    conn.close()
    return Store(tmp_path / "facts.sqlite", pr_dir=tmp_path / "pr")


def named(s, text):
    return [t for _, t, _ in s.names_in_text(text)]


# --------------------------------------------------------------------------- running text: tickers in capitals only
def test_a_bare_ticker_counts_in_running_text_only_in_capitals(word_tickers):
    """Case is the one signal that separates "NVDA" from "nvda": a ticker counts as the all-caps word, also in
    parentheses or before GAAP and EPS, but not in lowercase, in mixed case or inside an all-caps passage."""
    assert named(word_tickers, "Shares of NVDA rose.") == ["NVDA"]
    assert named(word_tickers, "Apple (AAPL) and NVDA's GAAP EPS") == ["AAPL", "NVDA"]
    assert named(word_tickers, "NVDA, MSFT and AAPL rose") == ["NVDA", "MSFT", "AAPL"]
    assert named(word_tickers, "nvda and Nvda rose; msft fell") == []
    assert named(word_tickers, "WHY NVDA AND MSFT ROSE TODAY") == []  # an all-caps headline: capitals say nothing there
    assert named(word_tickers, "UPS shipped. FICO, MSCI and RTX reported.") == []  # WORD_NAMES: never in running text
    assert named(word_tickers, "APP, USB, LIN, BRO, PSA and DOC rose.") == []      # word tickers: never by themselves


@pytest.mark.parametrize("text, ticker", [
    ("Chevron Corp. reported", "CVX"), ("Chevron Corporation reported", "CVX"), ("Merck & Co. reported", "MRK"),
    ("Flex Ltd. reported", "FLEX"), ("Target Corp said", "TGT"), ("Visa Inc. said", "V"), ("Moody's Corp. said", "MCO"),
])
def test_a_word_name_counts_in_running_text_before_a_legal_suffix(word_tickers, text, ticker):
    assert named(word_tickers, text) == [ticker]


@pytest.mark.parametrize("text", ["chevron", "Chevron reported", "the Chevron", "Chevron's revenue", "Chevron and Merck",
                                  "Flex", "flexible Ltd.", "Target and Visa", "Moody's said", "Lowe's said"])
def test_a_bare_word_name_never_counts_in_running_text(word_tickers, text):
    assert word_tickers.names_in_text(text) == []


def test_a_phrase_two_companies_share_names_neither_and_no_shorter_phrase_inside_it(tmp_path):
    """"Acme Holdings" is both companies' key, so it names neither; the shorter "Acme" (a third company's whole name)
    is not tried in its place."""
    s = make_store(tmp_path, {"ACH": "ACME HOLDINGS INC", "ACHC": "ACME HOLDINGS CORP", "ACM": "ACME INC"})
    assert s.resolve_entity("Acme Holdings") is None and s.resolve_entity("Acme")[1] == "ACM"
    assert named(s, "Acme Holdings reported record revenue.") == []
    assert named(s, "Acme reported record revenue.") == ["ACM"]
    s.close()


# --------------------------------------------------------------------------- lookups by a stored ticker
def test_by_ticker_is_an_exact_match_on_the_stored_ticker(word_tickers):
    """Code that already holds a ticker from the store (an Ask fact, a packaged filing) finds its company even when the
    ticker is a word or has one letter ("COST", "T"), which resolve_entity never matches by itself."""
    for ticker in ("GS", "FLEX", "KEY", "COST", "T", "C", "APP", "NVDA"):
        hit = word_tickers.by_ticker(ticker)
        assert hit is not None and hit[1] == ticker
        assert word_tickers.by_ticker(ticker.lower()) == hit
    assert word_tickers.resolve_entity("T") is None and word_tickers.resolve_entity("COST") is None
    for not_a_ticker in ("Goldman Sachs", "Flex Ltd.", "NVIDIA", "ZZZZ", ""):
        assert word_tickers.by_ticker(not_a_ticker) is None


# --------------------------------------------------------------------------- constituent aliases and accent folding
PILOT = {"MRSH": "MARSH & MCLENNAN COMPANIES, INC.", "DLR": "DIGITAL REALTY TRUST, INC.",
         "ORLY": "O REILLY AUTOMOTIVE INC", "EL": "ESTEE LAUDER COMPANIES INC", "MCO": "MOODYS CORP /DE/",
         "LOW": "LOWES COMPANIES INC", "SO": "SOUTHERN CO"}


@pytest.fixture
def pilot(tmp_path):
    """Stored before SECURITY_ALIASES existed (ticker and SEC name keys only): the index adds the aliases itself."""
    s = make_store(tmp_path, PILOT, aliases=lambda t, n: {normalize_entity(t), normalize_entity(n)})
    yield s
    s.close()


@pytest.mark.parametrize("name, ticker", [
    ("Marsh McLennan", "MRSH"), ("Digital Realty", "DLR"), ("O'Reilly Automotive", "ORLY"), ("Estée Lauder", "EL"),
    ("Estee Lauder", "EL"), ("ESTÉE LAUDER", "EL"), ("Moody's", "MCO"), ("Lowe's", "LOW"),
    ("Marsh & McLennan Companies", "MRSH"), ("O Reilly Automotive", "ORLY"),
])
def test_a_common_name_from_the_security_column_resolves(pilot, name, ticker):
    hit = pilot.resolve_entity(name)
    assert hit is not None and hit[1] == ticker


@pytest.mark.parametrize("name", ["Digital", "Realty", "Automotive", "Marsh", "Estée", "Lauder", "Southern Copper"])
def test_a_part_of_a_common_name_never_resolves(pilot, name):
    if name == "Marsh":  # issuer's own release name, now strictly bare-only
        assert pilot.resolve_entity(name)[1] == "MRSH"
        assert named(pilot, "The marsh flooded; Marsh reported results.") == []
    else:
        assert pilot.resolve_entity(name) is None


def test_constituent_aliases_count_in_running_text_unless_they_are_ordinary_words(pilot):
    assert named(pilot, "Marsh McLennan, Digital Realty and Estée Lauder rose; O'Reilly Automotive fell.") == [
        "MRSH", "DLR", "EL", "ORLY"]
    assert named(pilot, "A moody market at Lowe's; Moody's and Southern said") == []  # WORD_NAMES
    assert named(pilot, "Moody's Corp. and Southern Co. said") == ["MCO", "SO"]


def test_accent_folding_and_initials_preserve_legacy_snapshot_aliases():
    """Every stored key survives canonicalization; regenerated names also resolve
    without re-ingest. Ordinary ticker restrictions still apply."""
    assert normalize_entity("Estée Lauder Companies (The)") == "estee lauder companies"
    assert normalize_entity("ＮＶＩＤＩＡ Corp.") == "nvidia" and normalize_entity("Brown–Forman") == "brown forman"
    s = Store(SNAPSHOT)
    try:
        for cik, ticker, name, aliases in s._index().rows:
            legacy = {st.initials_key(a) for a in json.loads(aliases)}
            for key in st.lookup_keys(ticker, name, legacy):
                assert (cik, ticker) in s._index().lookup[key], (ticker, key)
            for key in st.lookup_keys(ticker, name, company_aliases(ticker, name)):
                assert (cik, ticker) in s._index().lookup[key], (ticker, key)
    finally:
        s.close()


def test_constituent_names_resolve_on_the_served_snapshot_stored_or_not():
    """SECURITY_ALIASES and SHORT_NAMES are applied when the index is built, so a snapshot resolves them whether or not
    its rows were stored before some of them existed. A re-ingest may store a lookup-only name ("abbott" for ABT); it
    still never counts in running text."""
    s = Store(SNAPSHOT, pr_dir=SNAPSHOT.parent / "pr_facts")
    try:
        stored = {t: set(json.loads(a)) for _, t, _, a in s._index().rows}
        assert s.resolve_entity("Abbott")[1] == "ABT"
        assert not s.names_in_text("Analyst Jane Abbott said revenue rose 5% in the quarter.")
        for table in (st.SECURITY_ALIASES, st.COMMON_NAMES, st.SHORT_NAMES):
            for ticker, names in table.items():
                for name in names if ticker in stored else ():
                    hit = s.resolve_entity(name)
                    assert hit is not None and hit[1] == ticker, (ticker, name)
    finally:
        s.close()


# --------------------------------------------------------------------------- the 499-company universe
def universe_index() -> st.EntityIndex:
    """Every constituent's keys as ingest would store them (company_aliases of its SEC title), with the snapshot's
    stored keys for the companies already covered."""
    record = json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))
    s = Store(SNAPSHOT)
    try:
        covered = list(s._index().rows)
    finally:
        s.close()
    have = {r[1] for r in covered}
    rows = [(u["cik"], u["ticker"], u["sec_title"], None) for u in record["universe"] if u["ticker"] not in have]
    return st.build_entity_index(rows + covered)


def test_no_lookup_key_names_two_companies_across_the_sp500():
    lookup = universe_index().lookup
    assert {k: sorted(t for _, t in v) for k, v in lookup.items() if len(v) > 1} == {}


def test_security_aliases_are_the_allowed_decisions_of_the_alias_record():
    """SECURITY_ALIASES and COMMON_NAMES hold exactly the record's ordinary aliases, SHORT_NAMES exactly its lookup-only
    names (all in WORD_NAMES), each normalizing to the recorded key; a blocked name never resolves to its company."""
    record = json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))
    assert len(record["universe"]) == 499 and record["summary"]["lookup_keys_naming_two_companies"] == {}
    for kind, tables in (("allow", (st.SECURITY_ALIASES, st.COMMON_NAMES)), ("lookup-only", (st.SHORT_NAMES,))):
        decided = {(d["ticker"], d["alias"]): d for d in record["decisions"] if d["decision"] == kind}
        assert set(decided) == {(t, a) for table in tables for t, names in table.items() for a in names}
        for (_ticker, alias), d in decided.items():
            assert normalize_entity(alias) == st.initials_key(d["key"]) and d["word_names"] == (d["key"] in st.WORD_NAMES)
            assert kind == "allow" or d["key"] in st.WORD_NAMES
    assert all(not set(st.SECURITY_ALIASES.get(t, ())) & set(names) for t, names in st.COMMON_NAMES.items())
    blocked = {(d["ticker"], d["key"]) for d in record["decisions"] if d["decision"] == "block"}
    assert {("BSX", "boston"), ("DOW", "dow"), ("HIG", "hartford"), ("ODFL", "old dominion")} <= blocked
    lookup = universe_index().lookup
    assert [(t, k) for t, k in sorted(blocked) if any(c[1] == t for c in lookup.get(k, ()))] == [
        ("MRSH", "marsh"), ("ODFL", "old dominion"), ("SBAC", "sba")]
    for ticker, key in (("MRSH", "marsh"), ("ODFL", "old dominion"), ("SBAC", "sba")):
        assert key in st.WORD_NAMES and key not in universe_index().scan


# Single-token free-text keys (a name matched in any case, or a bare ticker matched in capitals) that are dictionary
# words, kept on purpose. Any other dictionary word must go to WORD_NAMES or WORD_TICKERS.
DICTIONARY_KEYS_KEPT = {
    # covered companies: in a draft the word means the company
    "adobe", "alphabet", "amazon", "apple", "cisco", "meta", "oracle",
    "boa",  # Bank of America's "BoA" (ALIASES)
    # covered companies' bare tickers, matched only as the all-caps word
    "axon", "bac", "cor", "dell", "dis",
    # S&P 500 names (proposal of 2026-10-31): the company dominates usage, or the word is rare in news
    "assurant", "biogen", "caterpillar", "copart", "cummins", "intuit", "moodys", "starbucks", "steris", "synopsys",
    "xylem",
    # S&P 500 bare tickers, matched only in capitals: the dictionary words are obscure and not written in capitals
    "alb", "alle", "ame", "aos", "ares", "azo", "eme", "fis", "hubb", "trow", "wab",
}


def test_no_single_token_free_text_key_is_a_dictionary_word():
    if not DICTIONARY.exists():
        pytest.skip(f"{DICTIONARY} is missing: no dictionary to check free-text keys against")
    words = {w.strip().lower() for w in DICTIONARY.read_text().split()}

    def dictionary_word(key):  # also "starbucks" (starbuck) and "moodys" (moody): plural and possessive forms
        return key in words or any(key.endswith(end) and key[:-len(end)] in words for end in ("s", "es"))
    index = universe_index()
    free_text = {k for k in index.scan if " " not in k} | {t.lower() for t in index.tickers}
    flagged = sorted(k for k in free_text if dictionary_word(k) and k not in st.WORD_NAMES)
    assert [k for k in flagged if k not in DICTIONARY_KEYS_KEPT] == []
    assert sorted(DICTIONARY_KEYS_KEPT - set(flagged)) == []  # every kept key is still needed


def test_bank_tickers_are_the_gics_banks_plus_northern_trust():
    from receipts.verify import BANK_TICKERS
    record = json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))
    banks = {u["ticker"] for u in record["universe"] if u["sub_industry"] in ("Diversified Banks", "Regional Banks")}
    assert set(BANK_TICKERS) == banks | {"NTRS"} and len(BANK_TICKERS) == len(set(BANK_TICKERS))


# --------------------------------------------------------------------------- display names and suggestions
def test_display_names_drop_the_sec_tag_and_title_case_capitals(tmp_path):
    s = make_store(tmp_path, {"KEY": "KEYCORP /NEW/", "FLEX": "FLEX LTD.", "LIN": "LINDE PLC", "COST": "COSTCO WHOLESALE CORP /NEW",
                              "TJX": "TJX COMPANIES INC /DE/", "PM": "Philip Morris International Inc.",
                              "MRSH": "MARSH & MCLENNAN COMPANIES, INC."})
    assert {t: n for _, t, n in s.companies_list()} == {
        "KEY": "Keycorp", "FLEX": "Flex Ltd.", "LIN": "Linde plc", "COST": "Costco", "TJX": "TJX Companies Inc",
        "PM": "Philip Morris International Inc.", "MRSH": "Marsh McLennan"}
    s.close()


def test_a_suggestion_names_the_one_likely_company_and_never_resolves(tmp_path):
    s = make_store(tmp_path, {"ADI": "ANALOG DEVICES INC", "GS": "GOLDMAN SACHS GROUP INC",
                              "GE": "GENERAL ELECTRIC CO", "GEHC": "GE HealthCare Technologies Inc."})
    assert s.suggestion("Analog")[1] == "ADI" and s.resolve_entity("Analog") is None  # a generic first word: blocked
    assert s.suggestion("GS")[2] == "Goldman Sachs" and s.resolve_entity("GS") == s.by_ticker("GS")
    assert s.suggestion("GE")[1] == "GE"  # the bare ticker first: GE, not GE HealthCare
    assert s.suggestion("Ana") is None and s.suggestion("Goldman Sachs BDC") is None and s.suggestion("") is None
    s.close()


# --------------------------------------------------------------------------- short names
@pytest.mark.parametrize("stored_alias", [False, True])
def test_pepsi_is_lookup_only_in_existing_and_new_stores(tmp_path, stored_alias):
    aliases = company_aliases if stored_alias else lambda t, n: {normalize_entity(t), normalize_entity(n)}
    s = make_store(tmp_path, {"PEP": "PEPSICO INC"}, aliases=aliases)
    try:
        for name in ("Pepsi", "pepsi", "PEPSI"):
            assert s.resolve_entity(name) == s.by_ticker("PEP")
            assert named(s, f"{name}'s revenue rose in the quarter.") == []
        assert named(s, "PepsiCo's revenue rose in the quarter.") == ["PEP"]
    finally:
        s.close()


SHORT = ("F", "ABT", "TMUS", "TMO", "BALL", "FLEX", "CASY", "CTSH", "DPZ", "CRL", "PFG", "BKNG", "DOW", "BSX", "HIG",
         "ODFL", "COF", "JPM", "TROW", "USB", "MRK")


@pytest.fixture
def short(tmp_path):
    """Constituents under their SEC titles, stored without any constituent name: the index adds them itself."""
    titles = {u["ticker"]: u["sec_title"] for u in json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))["universe"]}
    s = make_store(tmp_path, {t: titles[t] for t in SHORT}, aliases=lambda t, n: {normalize_entity(t), normalize_entity(n)})
    yield s
    s.close()


@pytest.mark.parametrize("name, ticker", [
    ("Ford", "F"), ("Abbott", "ABT"), ("T-Mobile", "TMUS"), ("Thermo Fisher", "TMO"), ("Booking", "BKNG"),
    ("Ball", "BALL"), ("Flex", "FLEX"), ("Casey's", "CASY"), ("Cognizant", "CTSH"), ("Domino's", "DPZ"),
    ("Charles River", "CRL"), ("Principal Financial", "PFG"), ("Dow Inc.", "DOW"),
])
def test_a_short_common_name_resolves_as_a_claims_company(short, name, ticker):
    hit = short.resolve_entity(name)
    assert hit is not None and hit[1] == ticker


@pytest.mark.parametrize("name", ["Dow", "Boston", "Hartford", "Old Dominion", "Thermo", "Mobile", "Charles", "River"])
def test_an_index_place_or_partial_short_name_never_resolves(short, name):
    if name == "Old Dominion":  # release-confirmed bare alias; Virginia prose stays blocked
        assert short.resolve_entity(name)[1] == "ODFL"
        assert named(short, "Travel through Old Dominion.") == []
    else:
        assert short.resolve_entity(name) is None


def test_lookup_only_names_never_count_in_running_text_but_two_word_short_names_do(short):
    assert named(short, "Ford and Abbott rose; Ball, Flex, Cognizant, Casey's and Domino's fell.") == []
    assert named(short, "Along the Charles River, the principal financial officer booked a trip.") == []
    assert named(short, "T-Mobile and Thermo Fisher rose.") == ["TMUS", "TMO"]
    assert named(short, "Ford Motor Co. and Abbott Laboratories rose.") == ["F", "ABT"]  # the SEC names still count


# --------------------------------------------------------------------------- sentence ends
def test_no_name_runs_across_the_end_of_a_sentence(short):
    assert named(short, "It earned a high return on capital. One analyst called it cheap.") == []
    assert named(short, "Revenue fell 5%. Capital One said the rest; Capital One rose!") == ["COF"]
    assert named(short, "Shares of J.P. Morgan, T. Rowe Price and U.S. Bancorp rose; Merck & Co. fell.") == [
        "JPM", "TROW", "USB", "MRK"]
    assert named(short, "Return on capital! One analyst said") == [] and named(short, "capital;\n\nOne") == []


# --------------------------------------------------------------------------- the S&P 500 QA's names (2026-10-31)
QA_NAMES = {"TFC": "TRUIST FINANCIAL CORP", "MLM": "MARTIN MARIETTA MATERIALS INC", "LMT": "LOCKHEED MARTIN CORP",
            "USB": "US BANCORP \\DE\\", "LHX": "L3HARRIS TECHNOLOGIES, INC. /DE/"}  # SEC names as data/snapshot stores them


@pytest.fixture
def qa_names(tmp_path):
    """The QA's companies under their SEC names, stored without any constituent name."""
    s = make_store(tmp_path, QA_NAMES, aliases=lambda t, n: {normalize_entity(t), normalize_entity(n)})
    yield s
    s.close()


@pytest.mark.parametrize("name, ticker", [
    ("Truist", "TFC"), ("Truist Financial", "TFC"), ("Martin Marietta", "MLM"), ("Lockheed", "LMT"),
    ("Lockheed Martin", "LMT"), ("U.S. Bancorp", "USB"), ("US Bancorp", "USB"), ("L3Harris", "LHX"),
])
def test_the_qa_common_names_resolve_as_a_claims_company(qa_names, name, ticker):
    hit = qa_names.resolve_entity(name)
    assert hit is not None and hit[1] == ticker


@pytest.mark.parametrize("name", ["Martin", "Marietta", "U.S.", "US", "U.S. Bank", "Harris"])
def test_part_of_a_qa_name_never_resolves(qa_names, name):
    assert qa_names.resolve_entity(name) is None


def test_one_word_common_names_stay_out_of_running_text_and_us_is_never_a_company(qa_names):
    """Truist and Lockheed are lookup-only; Martin Marietta is an ordinary alias. "U.S." and "US" name a country, never
    U.S. Bancorp, which counts only under its name."""
    assert named(qa_names, "Truist and Lockheed rose.") == []
    assert named(qa_names, "Martin Marietta and Lockheed Martin rose.") == ["MLM", "LMT"]
    assert named(qa_names, "U.S. revenue rose as U.S. consumers spent; US exports and the U.S. economy slowed.") == []
    assert named(qa_names, "U.S. Bancorp and L3Harris rose.") == ["USB", "LHX"]


def test_display_names_of_the_qa_companies(qa_names):
    """"US BANCORP \\DE\\" loses its backslash tag; Martin Marietta is its constituent name; a one-word lookup-only name
    never becomes a display name."""
    assert {t: n for _, t, n in qa_names.companies_list()} == {
        "TFC": "Truist Financial Corp", "MLM": "Martin Marietta", "LMT": "Lockheed Martin Corp", "USB": "US Bancorp",
        "LHX": "L3Harris"}


# --------------------------------------------------------------------------- fourth round: every company's common name
def fourth_round(*decisions):
    record = json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))
    return [d for d in record["decisions"] if d["source"].startswith("common name (") and d["decision"] in decisions]


@pytest.fixture(scope="module")
def served():
    s = Store(SNAPSHOT, pr_dir=SNAPSHOT.parent / "pr_facts")
    yield s
    s.close()


def test_every_added_common_name_resolves_to_its_company_on_the_served_snapshot(served):
    added = fourth_round("allow", "lookup-only")
    assert len(added) == 152
    assert [(d["ticker"], d["alias"], served.resolve_entity(d["alias"])) for d in added
            if (served.resolve_entity(d["alias"]) or (None, None))[1] != d["ticker"]] == []
    # the S&P 500 QA's round-2 names, with apostrophes and hyphens as people type them
    for name, ticker in (("Valero", "VLO"), ("Ameriprise", "AMP"), ("Huntington Ingalls", "HII"),
                         ("Federal Realty", "FRT"), ("Charter", "CHTR"), ("Delta", "DAL"), ("Dominion", "D"),
                         ("Bristol Myers", "BMY"), ("Bristol-Myers", "BMY"), ("Extra Space", "EXR"),
                         ("O'Reilly", "ORLY"), ("OReilly", "ORLY"), ("O’Reilly", "ORLY"), ("Lowes", "LOW")):
        assert served.resolve_entity(name)[1] == ticker, name
    # ties, generic words and other blocks resolve to no company
    for name in ("Huntington", "American", "General", "United", "Texas", "Public", "Honeywell", "S&P", "Edison"):
        assert served.resolve_entity(name) is None, name
    assert [(d["ticker"], d["key"]) for d in fourth_round("block")
            if (served.resolve_entity(d["key"]) or (None, None))[1] == d["ticker"]] == [("MRSH", "marsh"), ("SBAC", "sba")]


def test_lookup_only_common_names_never_count_in_running_text_and_ordinary_ones_do(served):
    for d in fourth_round("allow", "lookup-only"):
        found = [t for _, t, _ in served.names_in_text(f"Shares of {d['alias']} rose after the report.")]
        assert found == ([] if d["decision"] == "lookup-only" else [d["ticker"]]), d["alias"]


def test_no_mention_starts_inside_a_longer_one(served):
    """Inside "Norfolk Southern Corp." the words "Southern Corp." are not Southern Co., nor "Williams Co." inside
    "Sherwin-Williams Co." the Williams Companies; each still counts on its own."""
    def named(text):
        return [t for _, t, _ in served.names_in_text(text)]
    assert named("Norfolk Southern Corp. and Sherwin-Williams Co. rose.") == ["NSC", "SHW"]
    assert named("Southern Co. and the Williams Companies rose.") == ["SO", "WMB"]
    assert [t for _, t, _ in verify.named_in_draft("Norfolk Southern Corp. rose.", FallbackStore(served))] == ["NSC"]


class FallbackStore:
    """A store without names_in_text, so verify.named_in_draft uses its own n-gram scan."""

    def __init__(self, store):
        self.resolve_entity = store.resolve_entity


def test_no_company_name_is_read_as_another_company(served):
    """With the fourth round's names, each covered company's own names (S&P Security name, SEC name and aliases) name
    no other company, as a lookup or in running text: no added name makes another company's mention ambiguous."""
    universe = {u["ticker"]: u for u in json.loads(ALIAS_RECORD.read_text(encoding="utf-8"))["universe"]}
    problems = []
    for _cik, ticker, sec, _aliases in served._index().rows:
        # XOM joined after the record (its registrant changed): its SEC name and aliases are checked all the same
        security = universe[ticker]["security"] if ticker in universe else None
        for name in {n for n in (security, sec, *st.known_names(ticker)) if n}:
            hit = served.resolve_entity(name)
            found = {t for _, t, _ in served.names_in_text(f"Analysts said {name} reported results.")}
            if (hit is not None and hit[1] != ticker) or not found <= {ticker}:
                problems.append((ticker, name, hit, sorted(found)))
    assert problems == []
