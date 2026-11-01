"""Regression tests for the query-side findings of the 2026-10-31 code review (.claude/judging/2026-10-31-code-review.md):
H1 (a lookup-only name with a legal suffix or "the"), H3 (the split-basis explanation), M2 ("is part of"), M4 (the cache
fingerprint), L4 (the growth fallback) and L6 (a sentence that ends inside quotes). The repro tests the review wrote
(each failed on f7a857b) come first in each section, run against the committed data/snapshot; no network.
"""
import ast
from pathlib import Path

import pytest

from receipts import ask, stream
from receipts import verify as V
from receipts.store import Store, legal_form
from receipts.types import Claim
from test_verify import META, META_10Q_Q2, META_10Q_Q3, PERIODS, FakeStore, accessions, fact

SNAP = Path(__file__).resolve().parents[1] / "data" / "snapshot"


@pytest.fixture(scope="module")
def store():
    s = Store(db_path=SNAP / "facts.sqlite", pr_dir=SNAP / "pr_facts")
    yield s
    s.close()


def claim(value_text, value, *, metric="revenue", unit="USD", scale="billions", period_text, entity, basis=None,
          comparison="none", period_type="quarter", span=None):
    span = span or f"{metric.replace('_', ' ')} of {value_text}"
    return Claim(id="c1", metric=metric, metric_text=metric.replace("_", " "), value_text=value_text, value=value,
                 unit=unit, scale=scale, period_text=period_text, period_type=period_type, comparison=comparison,
                 basis=basis, forward_looking=False, entity=entity, segment=None, span_text=span, span_start=0,
                 span_end=len(span))


# H1: a lookup-only name written with a legal suffix, or after "the" ------------------------------------------------
@pytest.mark.parametrize("name", ["Prudential plc", "Citizens, Inc.", "Citizens Inc."])
def test_a_lookup_only_short_name_with_another_legal_suffix_is_not_the_sp500_company(store, name):
    """Prudential plc (UK, files 20-F) is not Prudential Financial; Citizens, Inc. (NYSE: CIA) is not Citizens
    Financial Group. Both resolved on f7a857b, and a claim about them was marked wrong against the other's 10-K."""
    assert store.resolve_entity(name) is None


def test_a_prudential_plc_claim_is_never_wrong_against_prudential_financial(store):
    v = V.verify(claim("$2.9 billion", 2.9, metric="net_income", period_text="full year 2025", period_type="annual",
                       entity="Prudential plc", span="Prudential plc reported net income of $2.9 billion for 2025"),
                 store)
    assert v.status != "wrong", v.explanation
    assert "Prudential plc" in v.explanation and not v.evidence


@pytest.mark.parametrize("name", ["Delta Inc.", "Vulcan Inc.", "the carrier", "The Carrier", "the Travelers",
                                  "Prudential plc (PUK)", "Prudential Financial plc", "Abbott Inc.", "Ball plc",
                                  "Dow plc", "Visa plc", "Nvidia plc", "Linde Inc.", "Linde AG", "Medtronic Inc.",
                                  "Chubb Corp.", "Zoetis N.V.", "Southern Copper Corp."])
def test_a_name_whose_suffix_does_not_fit_the_company_never_resolves(store, name):
    """A lookup-only name counts with a suffix only as the company's own legal name ("Delta Inc." is not Delta Air
    Lines, Inc.), never after "the" ("the carrier" is a carrier, not Carrier Global), and no name counts with a suffix
    of another kind than the company's own: Linde Inc. (the former Praxair), Medtronic, Inc. and The Chubb Corporation
    are other registrants than Linde plc, Medtronic plc and Chubb Limited."""
    assert store.resolve_entity(name) is None


@pytest.mark.parametrize("name, ticker", [
    ("Prudential", "PRU"), ("Citizens", "CFG"), ("Carrier", "CARR"), ("Delta", "DAL"),  # lookup-only, bare
    ("Prudential Financial, Inc.", "PRU"), ("Citizens Financial Group, Inc.", "CFG"), ("Delta Air Lines, Inc.", "DAL"),
    ("Ball Corp.", "BALL"), ("Ball Corporation", "BALL"), ("Dow Inc.", "DOW"), ("Visa Inc.", "V"),
    ("Target Corp.", "TGT"), ("Merck & Co.", "MRK"), ("Merck & Co., Inc.", "MRK"), ("Moody's Corp.", "MCO"),
    ("Moody's Corporation", "MCO"), ("The Southern Company", "SO"), ("Southern Co.", "SO"), ("Chevron Corp.", "CVX"),
    ("Flex Ltd.", "FLEX"), ("Eaton Corp.", "ETN"), ("Eaton Corporation plc", "ETN"), ("The Hershey Company", "HSY"),
    ("Linde plc", "LIN"), ("Medtronic plc", "MDT"), ("Chubb Limited", "CB"), ("NXP Semiconductors N.V.", "NXPI"),
    ("LyondellBasell N.V.", "LYB"), ("Bunge Global SA", "BG"), ("Nvidia Corp.", "NVDA"), ("Chipotle Inc.", "CMG"),
    ("Apple Inc. (AAPL)", "AAPL"), ("Ball Corp.'s", "BALL"), ("Abbott Laboratories Inc.", "ABT"),
])
def test_a_name_whose_suffix_fits_the_company_still_resolves(store, name, ticker):
    hit = store.resolve_entity(name)
    assert hit is not None and hit[1] == ticker, name


def test_legal_forms_keep_the_possessive_s_that_sec_names_write():
    assert legal_form("MOODYS CORP /DE/") == legal_form("Moody's Corp.") == (("moodys",), ("corp",))
    assert legal_form("Merck & Co., Inc.") == (("merck",), ("co", "inc"))
    assert legal_form("The Southern Company") == (("southern",), ("co",))
    assert legal_form("Nvidia") == (("nvidia",), ())


@pytest.mark.parametrize("text, tickers", [
    ("Prudential plc reported net income of $2.9 billion.", []),
    ("Citizens, Inc. said revenue rose.", []),
    ("Nvidia plc and Zoetis N.V. rose.", []),  # another company's legal name: no shorter name inside it counts either
    ("Ball Corp. and Merck & Co. reported; Moody's Corp.'s revenue rose.", ["BALL", "MRK", "MCO"]),
    ("Eaton Corp., Linde plc and NXP Semiconductors N.V. said.", ["ETN", "LIN", "NXPI"]),
    ("Norfolk Southern Corp. said.", ["NSC"]),
])
def test_running_text_counts_a_name_with_a_suffix_only_where_the_suffix_fits(store, text, tickers):
    assert [t for _, t, _ in store.names_in_text(text)] == tickers


def test_a_context_line_never_drops_a_legal_suffix(store):
    """"the company" takes its company from the context line, whose shorter prefixes may leave out lowercase words,
    but never "plc": "Prudential plc FY2025" is not Prudential."""
    assert V.context_entities("Prudential plc FY2025 results", store) == []
    c = claim("$2.9 billion", 2.9, metric="net_income", period_text="full year 2025", period_type="annual",
              entity="the company")
    assert V.resolve_claim_entity(c, store, "Prudential plc FY2025 results") is None
    assert V.resolve_claim_entity(c, store, "Prudential Financial FY2025 results")[1] == "PRU"


def plan(entity):
    return ask.LookupPlan(entity=entity, metrics=["net_income"], fiscal_year=2025, fiscal_period="FY", basis="GAAP",
                          segment=None, unsupported=False)


def test_ask_never_answers_a_prudential_plc_question_with_prudential_financials_figure(store):
    """On f7a857b the question with the plan entity "Prudential plc" passed bind_question and answer_plan answered
    "PRU GAAP net income for FY2025 was $3,576,000,000". Now the entity resolves to nothing, and a plan that drops the
    suffix ("Prudential") fails bind_question: the question names Prudential plc, not a covered company."""
    question = "What was Prudential plc GAAP net income for FY2025?"
    ask.bind_question(question, plan("Prudential plc"), store)  # an unresolved entity: answer_plan says so
    result = ask.answer_plan(plan("Prudential plc"), store=store)
    assert result["items"] == [] and result["answer"] == "" and "covered company" in result["notice"]
    with pytest.raises(ask.AskError):
        ask.bind_question(question, plan("Prudential"), store)
    ask.bind_question("What was Prudential Financial GAAP net income for FY2025?", plan("Prudential Financial"), store)


# H3: the split-basis explanation ---------------------------------------------------------------------------------
@pytest.mark.parametrize("value_text, value", [("$11.40", 11.40), ("$11.60", 11.60), ("$11.75", 11.75)])
def test_split_basis_explanation_never_names_a_pre_split_figure_that_was_not_filed(store, value_text, value):
    """Chipotle's Q3 2023 diluted EPS was $11.32 before the 50-for-1 split (held: 10-Q 0001058090-23-000041). NoCap
    must not say $11.40, $11.60 or $11.75 "is" that pre-split figure, nor cite another period's receipt."""
    v = V.verify(claim(value_text, value, metric="eps_diluted", unit="USD/share", scale="units", period_text="Q3 2023",
                       entity="Chipotle", basis="GAAP"), store)
    assert v.status == "wrong"
    assert "on the share basis before" not in v.explanation, v.explanation
    assert {(f.fiscal_year, f.fiscal_period, f.metric) for f in v.evidence} == {(2023, "Q3", "eps_diluted")}


def test_the_held_pre_split_figure_is_still_explained_as_one(store):
    """$11.32 is the figure as filed before the split: _diag_split says so, citing that 10-Q."""
    v = V.verify(claim("$11.32", 11.32, metric="eps_diluted", unit="USD/share", scale="units", period_text="Q3 2023",
                       entity="Chipotle", basis="GAAP"), store)
    assert v.status == "wrong" and "as reported before a 50-for-1 stock split" in v.explanation, v.explanation
    assert "0001058090-23-000041" in accessions(v)


def test_split_basis_explanation_for_crowdstrike_uses_the_filed_pre_split_figure(store):
    """CrowdStrike's Q2 FY2026 GAAP diluted EPS was -$0.31 before its 4-for-1 split; -$0.28 (= 4 x -$0.07) was never
    filed, so "-$0.28 is ... on the share basis before a 4-for-1 stock split" is false."""
    v = V.verify(claim("-$0.28", -0.28, metric="eps_diluted", unit="USD/share", scale="units",
                       period_text="Q2 FY2026", entity="CrowdStrike", basis="GAAP"), store)
    assert "on the share basis before" not in v.explanation, v.explanation


# M2: "is part of" ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["Coca-Cola Consolidated", "Apple Hospitality REIT", "Blackstone Mortgage Trust",
                                  "Coca-Cola Europacific Partners"])
def test_an_unrelated_namesake_is_never_called_part_of_a_covered_company(store, name):
    v = V.verify(claim("$1.8 billion", 1.8, period_text="Q3 2026", entity=name, span=f"{name} revenue of $1.8 billion"),
                 store)
    assert v.status == "unverifiable"
    assert " is part of " not in v.explanation, v.explanation
    assert "it may be a unit, a subsidiary or another company" in v.explanation and not v.evidence


# M4: the cache fingerprint -------------------------------------------------------------------------------------------
def test_every_module_verify_reads_at_query_time_is_in_the_cache_fingerprint():
    """receipts/splits.py decides split verdicts, but a change to it alone left cache_fingerprint unchanged."""
    assert "splits.py" in stream.RESULT_MODULES


def test_every_receipts_module_the_fingerprinted_modules_import_is_fingerprinted():
    """The fingerprint must cover every receipts module a fingerprinted one imports, at any depth and also inside a
    function: a module left out can change a verdict while a recorded stream still replays."""
    root = Path(stream.__file__).resolve().parent
    seen, todo = set(), list(stream.RESULT_MODULES)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((root / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "receipts":
                todo += [f"{a.name}.py" for a in node.names if (root / f"{a.name}.py").exists()]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("receipts."):
                todo.append(f"{node.module.split('.')[1]}.py")
            elif isinstance(node, ast.Import):
                todo += [f"{a.name.split('.')[1]}.py" for a in node.names if a.name.startswith("receipts.")]
    assert sorted(seen - set(stream.RESULT_MODULES)) == []


# L4: the growth fallback -----------------------------------------------------------------------------------------
def test_yoy_change_of_a_derived_q4_pairs_when_nothing_was_restated(store):
    """Dollar Tree Q4 FY2025 revenue $5,450.7M (FY2025 10-K, derived) against Q4 FY2024 $4,996.7M (FY2024 10-K, filed
    after the Family Dollar recast; FY2024 is unchanged in the FY2025 10-K): +9.1%. The release filed three minutes
    before the 10-K made the fallback refuse the pair."""
    v = V.verify(claim("9.1%", 9.1, metric="revenue_growth", unit="%", scale="units", period_text="Q4 FY2025",
                       entity="Dollar Tree", comparison="YoY", span="net sales rose 9.1% year over year"), store)
    assert v.status == "verified", v.explanation


META_8K_MINUTES_EARLIER = (META, "META", "8-K", "0001628280-26-066770", "2026-10-21T23:57:00Z", "meta-ex991.htm")
SEQ_PERIODS = {**PERIODS, (META, 2025, "Q3"): (PERIODS[(META, 2026, "Q3")][0].replace(year=2025),
                                               PERIODS[(META, 2026, "Q3")][1].replace(year=2025))}


@pytest.mark.parametrize("release_comparative, status", [(2_709e6, "verified"), (3_100e6, "unverifiable")])
def test_a_release_minutes_before_the_10q_is_one_more_version_that_must_agree(release_comparative, status):
    """A QoQ change from two 10-Qs (Meta's Q3 2026 $9,860M against Q2's $15,848M, -37.8%) counts when nothing was
    restated in between, as Q3 2025 shows: $2,709M in its own 10-Q and in the Q3 2026 10-Q. A release three minutes
    before that 10-Q (hand-made, with only the year-ago figure) used to make Q2's 10-Q too old to pair. Now it is one more
    version that must agree: at $2,709M the change is checked, at a hand-made $3,100M it stays "?"."""
    q3_25 = (META, "META", "10-Q", "0001628280-25-047240", "2025-10-30T20:00:00Z", "meta-20250930.htm")

    def year_ago(filing, value):
        f = fact(filing, "net_income", value, 2026, "Q3")
        start, end = SEQ_PERIODS[(META, 2025, "Q3")]
        return f.model_copy(update={"period_start": start, "period_end": end, "fiscal_year": 2025})
    facts = [fact(META_10Q_Q3, "net_income", 9_860e6, 2026, "Q3"), fact(META_10Q_Q2, "net_income", 15_848e6, 2026, "Q2"),
             year_ago(q3_25, 2_709e6), year_ago(META_10Q_Q3, 2_709e6),
             year_ago(META_8K_MINUTES_EARLIER, release_comparative)]
    c = claim("38%", 38, metric="net_income", unit="%", scale="units", entity="Meta Platforms", period_text="Q3 2026",
              comparison="QoQ", span="net income fell 38% from the previous quarter")
    v = V.verify(c, FakeStore(facts, periods=SEQ_PERIODS))
    assert v.status == status, v.explanation
    if status == "verified":
        assert "fell 37.8% quarter over quarter" in v.explanation
        assert set(accessions(v)) == {META_10Q_Q3[3], META_10Q_Q2[3]}


# L6: a sentence that ends inside quotes ----------------------------------------------------------------------------
def test_a_sentence_ending_inside_quotes_is_still_a_sentence_end(store):
    assert store.names_in_text('He stressed "return on capital." One analyst disagreed.') == []
    assert store.names_in_text("He stressed (return on capital.) One analyst disagreed.") == []
    assert [t for _, t, _ in store.names_in_text('"We like Capital One." Analysts agreed.')] == ["COF"]
