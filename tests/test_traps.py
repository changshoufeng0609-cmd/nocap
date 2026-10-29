"""Offline tests for the trap-draft corpus (eval/build_traps.py) and its scorer (eval/eval.py --corpus trap_v1).

The validator is the "code decides" half of "Muse Wonder proposes, code decides": these tests feed it mocked Wonder
output against a small synthetic fact sheet, so they need neither data/ nor the Model API.

    .venv/bin/python -m pytest tests/test_traps.py -q
"""
from __future__ import annotations

import copy
import importlib.util
import json
import random
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load("receipts_eval_build_traps", ROOT / "eval" / "build_traps.py")
ev = _load("receipts_eval_eval_for_traps", ROOT / "eval" / "eval.py")

AS_OF = "2026-08-26T22:21:19Z"
CAL = {
    "NVDA": [(2026, "Q2", date(2025, 4, 28), date(2025, 7, 27)), (2027, "Q1", date(2026, 1, 26), date(2026, 4, 26)),
             (2027, "Q2", date(2026, 4, 27), date(2026, 7, 26))],
    "AMD": [(2026, "Q2", date(2026, 3, 29), date(2026, 6, 27)), (2026, "Q3", date(2026, 6, 28), date(2026, 9, 26))],
}


def F(id_, section, metric, value, display, *, ticker="NVDA", basis="GAAP", segment=None, comparison=None,
      end="2026-07-26", public="2026-08-26T20:21:19Z", unit=None, note="", numbers=None):
    unit = unit or ("USD/share" if metric.startswith("eps") else "%" if metric.endswith(("growth", "margin"))
                    else "text" if metric == "guidance" else "USD")
    return bt.SheetFact(id=id_, section=section, ticker=ticker, metric=metric, unit=unit, value=value,
                        display=display, basis=basis, segment=segment, comparison=comparison, period_end=end,
                        first_public=public, note=note, numbers=numbers or [])


def make_sheet() -> "bt.Sheet":
    amd = "2026-08-04T20:16:24Z"
    facts = [
        F("F01", "current", "revenue", 96.221e9, "$96.2 billion"),
        F("F02", "current", "revenue_growth", 105.85, "106%", comparison="YoY"),
        F("F03", "current", "revenue_growth", 17.9, "18%", comparison="QoQ"),
        F("F04", "current", "net_income", 59.688e9, "$59.7 billion"),
        F("F05", "current", "eps_diluted", 2.46, "$2.46"),
        F("F06", "current", "eps_diluted", 2.22, "$2.22", basis="non-GAAP"),
        F("F07", "current", "gross_margin", 74.97, "75.0%"),
        F("F08", "current", "segment_revenue", 89.023e9, "$89.0 billion", segment="Data Center"),
        F("F09", "prior", "revenue", 81.615e9, "$81.6 billion", end="2026-04-26", note="previous quarter",
          public="2026-05-20T20:21:19Z"),
        F("F10", "prior", "revenue", 46.743e9, "$46.7 billion", end="2025-07-27", note="year-ago quarter",
          public="2025-08-27T20:00:00Z"),
        F("F11", "peer", "segment_revenue", 6.718e9, "$6.72 billion", ticker="AMD", segment="Data Center",
          end="2026-06-27", public=amd),
        F("F12", "peer", "revenue", 11.536e9, "$11.5 billion", ticker="AMD", end="2026-06-27", public=amd),
        F("F13", "later", "revenue", 13.0e9, "$13.0 billion", ticker="AMD", end="2026-09-26",
          public="2026-11-03T20:00:00Z"),
        F("F14", "guidance", "guidance", None, "Revenue is expected to be $108.0 billion, plus or minus 2%.",
          numbers=[{"text": "$108.0 billion", "kind": "money", "value": 108.0, "scale": "billions", "decimals": 1,
                    "is_range": False},
                   {"text": "2%", "kind": "pct", "value": 2.0, "scale": "units", "decimals": 0, "is_range": False}]),
    ]
    return bt.Sheet(sheet_id="NVDA-FY2027Q2", ticker="NVDA", company="NVIDIA", fiscal_year=2027, fiscal_period="Q2",
                    period_start="2026-04-27", period_end="2026-07-26", release_accession="0001045810-26-000073",
                    release_at="2026-08-26T20:21:19Z", as_of=AS_OF,
                    context=bt.context_line("NVDA", 2027, "Q2", date(2026, 7, 26)), facts=facts)


P1 = ("NVIDIA reported second-quarter revenue of $96.2 billion, up 106% from a year earlier, as cloud providers kept "
      "buying complete AI systems and the newest platform began shipping in volume to customers around the world. "
      "Executives pointed to sovereign projects and enterprise deployments as the next leg of demand, and investors "
      "spent the evening parsing how long the current buildout can last.")
P2 = ("Data Center revenue was $89.0 billion and carried the quarter. Net income rose to $59.7 billion, while GAAP "
      "diluted EPS was $2.46 and non-GAAP diluted EPS was $2.22. Gross margin held at 75.0%, in line with the prior "
      "period despite the cost of ramping a new architecture across several factories.")
P3 = ("Revenue rose 18% from the previous quarter, compared with $81.6 billion in the previous quarter. Rival AMD's "
      "data center revenue was $6.72 billion in the quarter ended June 27, 2026, a fraction of the market leader's.")
P4 = ("NVIDIA expects revenue of $108.0 billion, plus or minus 2%, for the third quarter, which would set another "
      "record as demand keeps running ahead of supply and customers lock in capacity for next year. Supply of advanced "
      "packaging and memory remains the main constraint the company is working through with its partners.")
HEADLINE = "NVIDIA extends its run as data-center demand keeps climbing"


def article(*paras: str) -> str:
    return "\n\n".join(paras)


def pe(quote: str, value_text: str, etype: str, fact_id: str, source: str = "") -> dict:
    return {"quote": quote, "value_text": value_text, "error_type": etype, "fact_id": fact_id,
            "source_fact_id": source}


def validate(art: str, planted: list[dict], **kw):
    out = {"headline": HEADLINE, "article": art, "planted_errors": planted}
    return bt.validate_draft(make_sheet(), out, calendars=CAL, **kw)


# a draft with three planted errors: scale, basis and value
P2_ERR = ("Data Center revenue was $89.0 billion and carried the quarter. Net income rose to $59.7 million, while "
          "GAAP diluted EPS was $2.22. Gross margin held at 79.0%, in line with the prior period despite the cost of "
          "ramping a new architecture across several factories.")
PLANTED_3 = [pe("Net income rose to $59.7 million, while GAAP diluted EPS was $2.22.", "$59.7 million", "scale", "F04"),
             pe("Net income rose to $59.7 million, while GAAP diluted EPS was $2.22.", "$2.22", "basis", "F05", "F06"),
             pe("Gross margin held at 79.0%, in line with the prior period despite the cost of ramping a new "
                "architecture across several factories.", "79.0%", "value", "F07")]


# ------------------------------------------------------------------ the validator: code decides

def test_clean_draft_reads_every_figure_as_true():
    v = validate(article(P1, P2, P3, P4), [], min_errors=0)
    assert v.accepted, v.reasons
    got = {c["value_text"]: c["fact_id"] for c in v.true_claims}
    assert got == {"$96.2 billion": "F01", "106%": "F02", "$89.0 billion": "F08", "$59.7 billion": "F04",
                   "$2.46": "F05", "$2.22": "F06", "75.0%": "F07", "18%": "F03", "$81.6 billion": "F09",
                   "$6.72 billion": "F11", "$108.0 billion": "F14", "2%": "F14"}
    assert [c["forward_looking"] for c in v.true_claims if c["fact_id"] == "F14"] == [True, True]


def test_planted_errors_are_confirmed_with_code_offsets_and_types():
    v = validate(article(P1, P2_ERR, P3, P4), PLANTED_3)
    assert v.accepted, v.reasons
    by_value = {e["value_text"]: e for e in v.planted}
    assert {k: e["type"] for k, e in by_value.items()} == {"$59.7 million": "scale", "$2.22": "basis",
                                                           "79.0%": "value"}
    for e in v.planted:
        a, b = e["span"]
        assert v.text[a:b] == e["value_text"]          # offsets come from code, not the model
        sa, sb = e["sentence_span"]
        assert sa <= a < b <= sb and v.text[sa:sb] == e["quote"]
        assert e["expected_status"] == "wrong" and e["expected_reason"] == bt.EXPECTED_REASON[e["type"]]
    assert by_value["$2.22"]["matched_evidence"]["basis"] == "non-GAAP"
    assert by_value["$59.7 million"]["true_display"] == "$59.7 billion"


def test_a_planted_error_that_is_true_is_demoted_and_the_draft_needs_two_errors():
    planted = [pe("Net income rose to $59.7 billion, while GAAP diluted EPS was $2.46 and non-GAAP diluted EPS "
                  "was $2.22.", "$59.7 billion", "value", "F04"),
               PLANTED_3[2]]
    art = article(P1, P2.replace("Gross margin held at 75.0%", "Gross margin held at 79.0%"), P3, P4)
    v = validate(art, planted)
    assert not v.accepted and any("only 1 confirmed error" in r for r in v.reasons)
    assert any("demoted" in line for line in v.log)
    assert any(c["value_text"] == "$59.7 billion" and c.get("demoted_plant") for c in v.true_claims)


def test_quotes_must_be_verbatim_but_typography_is_repaired():
    curly = [dict(p, quote=p["quote"].replace("'", "’")) for p in PLANTED_3]
    v = validate(article(P1, P2_ERR, P3, P4), curly)
    assert v.accepted, v.reasons
    bad = [dict(PLANTED_3[0], quote="Net income was a record $59.7 million."), *PLANTED_3[1:]]
    v = validate(article(P1, P2_ERR, P3, P4), bad)   # the value is unique in the text: re-anchored, logged
    assert v.accepted and any("anchored" in line for line in v.log)
    missing = [dict(PLANTED_3[0], quote="A sentence that is not there.", value_text="$1.00 billion"), *PLANTED_3[1:]]
    v = validate(article(P1, P2_ERR, P3, P4), missing)
    assert not v.accepted and any("quote not found" in r for r in v.reasons)


def test_an_unflagged_false_figure_becomes_an_accidental_error():
    art = article(P1.replace("$96.2 billion", "$99.4 billion"), P2_ERR, P3, P4)
    v = validate(art, PLANTED_3)
    assert v.accepted, v.reasons
    assert [(e["value_text"], e["type"], e["wonder_type"]) for e in v.accidental] == [("$99.4 billion", "value", None)]


def test_a_figure_inside_the_rounding_margin_rejects_the_draft():
    art = article(P1.replace("$96.2 billion", "$96.3 billion"), P2_ERR, P3, P4)   # 1.6x the rounding: not clear
    v = validate(art, PLANTED_3)
    assert not v.accepted and any(r.startswith("borderline figure") for r in v.reasons)


def test_a_basis_error_needs_the_basis_written():
    # "diluted EPS was $2.22" with no basis word is true on the non-GAAP basis: not an error
    p2 = P2_ERR.replace("GAAP diluted EPS was $2.22", "diluted EPS was $2.22")
    planted = [PLANTED_3[0], dict(PLANTED_3[1], quote=PLANTED_3[1]["quote"].replace("GAAP diluted", "diluted")),
               PLANTED_3[2]]
    v = validate(article(P1, p2, P3, P4), planted)
    assert all(e["value_text"] != "$2.22" for e in v.planted)
    assert any(c["value_text"] == "$2.22" for c in v.true_claims)


def test_look_ahead_entity_and_period_errors():
    p3 = ("Revenue rose 106% from the previous quarter, compared with $81.6 billion in the previous quarter. Rival "
          "AMD's data center revenue was $89.0 billion in the quarter ended June 27, 2026, a fraction of the market "
          "leader's. AMD's revenue was $13.0 billion in the quarter ended September 26, 2026.")
    planted = [pe("Revenue rose 106% from the previous quarter, compared with $81.6 billion in the previous quarter.",
                  "106%", "period", "F02"),   # Wonder names the source fact: code reads the words (QoQ, F03)
               pe("Rival AMD's data center revenue was $89.0 billion in the quarter ended June 27, 2026, a fraction "
                  "of the market leader's.", "$89.0 billion", "entity", "F11", "F08"),
               pe("AMD's revenue was $13.0 billion in the quarter ended September 26, 2026.", "$13.0 billion",
                  "look_ahead", "F13", "F13")]
    p1 = P1.replace(", up 106% from a year earlier,", ",")
    v = validate(article(p1, P2, p3, P4), planted)
    assert v.accepted, v.reasons
    got = {e["value_text"]: (e["type"], e["fact_id"]) for e in v.planted}
    assert got == {"106%": ("period", "F03"), "$89.0 billion": ("entity", "F11"), "$13.0 billion": ("look_ahead", "F13")}
    assert any("is the figure's source" in line for line in v.log)


def test_a_self_contradiction_is_repaired_by_dropping_the_planted_sentence():
    p3 = P3.replace("Revenue rose 18% from the previous quarter, compared with $81.6 billion in the previous quarter.",
                    "Revenue rose 18% from a year earlier.")
    planted = [*PLANTED_3[:2], pe("Revenue rose 18% from a year earlier.", "18%", "period", "F02", "F03")]
    v = validate(article(P1, P2_ERR.replace("Gross margin held at 79.0%", "Gross margin held at 75.0%"), p3, P4),
                 planted)
    assert v.accepted, v.reasons
    assert "Revenue rose 18% from a year earlier." not in v.text
    assert any(line.startswith("repair: self-contradiction") for line in v.log)
    assert {e["type"] for e in v.planted} == {"scale", "basis"}


def test_give_away_wording_and_unreadable_figures_are_caught():
    p3 = "Sequential momentum stayed strong as revenue rose 106% year over year, the steepest climb in the sector."
    v = validate(article(P1, P2, p3, P4), [], min_errors=0)
    assert not v.accepted and any(r.startswith("ambiguous comparison") for r in v.reasons)
    v = validate(article(P1.replace("revenue of $96.2 billion", "revenue of about $96.2 billion"), P2, P3, P4), [],
                 min_errors=0)
    assert any(r.startswith("hedged figure") for r in v.reasons)
    v = validate(article(P1, P2, P3 + " The company employs 36,000 people.", P4), [], min_errors=0)
    assert any(r.startswith("non-financial number") for r in v.reasons)
    v = validate(article(P1, P2, P3, P4.replace("$108.0 billion", "$110.0 billion")), [], min_errors=0)
    assert any("not in the release's outlook" in r for r in v.reasons)


def test_an_implausible_scale_slip_is_dropped():
    p2 = P2_ERR.replace("Data Center revenue was $89.0 billion", "Data Center revenue was $89.0 trillion")
    planted = [*PLANTED_3, pe("Data Center revenue was $89.0 trillion and carried the quarter.", "$89.0 trillion",
                              "scale", "F08")]
    v = validate(article(P1, p2, P3, P4), planted)
    assert v.accepted, v.reasons
    assert "$89.0 trillion" not in v.text and len(v.planted) == 3
    assert any("implausible scale slip" in line for line in v.log)


# ------------------------------------------------------------------ the reader

def test_reader_cases():
    text = ("Revenue rose 18% from the previous quarter and 106% from a year earlier to $96.2 billion, up from $46.7 "
            "billion a year earlier, while net income was $59.7 billion, or $2.46 per diluted share. That compared "
            "with $81.6 billion in the previous quarter. Google Cloud remained the standout, producing revenue of "
            "$24.8 billion.")
    r = bt.read_draft(text, "NVDA", calendars=CAL, default_year=2027)
    got = {f.text: (f.family, f.growth, f.comparison, f.role, f.prior, f.per_share, f.segment, f.entity)
           for f in r.figures}
    assert got["18%"][:3] == ("revenue", True, "QoQ") and got["106%"][:3] == ("revenue", True, "YoY")
    assert got["$96.2 billion"][3] == "current" and got["$46.7 billion"][3:5] == ("prior", "year_ago")
    assert got["$59.7 billion"][0] == "net_income" and got["$2.46"][0] == "eps" and got["$2.46"][5]
    assert got["$81.6 billion"][:1] == ("net_income",) and got["$81.6 billion"][3:5] == ("prior", "prior_q")
    assert got["$24.8 billion"][6:] == ("Google Cloud", "GOOGL")


def test_diagnosis_ignores_coincidences_outside_the_story():
    sheet = make_sheet()
    stranger = bt.Ev("META", "net_income", None, "GAAP", None, "2026-06-30", 61.9e9, "USD", "2026-07-29T20:03:23Z")
    old = bt.Ev("NVDA", "net_income", None, "GAAP", None, "2023-07-30", 61.9e9, "USD", "2023-08-23T20:00:00Z")
    r = bt.read_draft("Net income rose to $61.9 billion.", "NVDA", calendars=CAL, default_year=2027)
    etype, _m, _all = bt.diagnose(r.figures[0], sheet.by_id()["F04"], sheet, [stranger, old], set())
    assert etype == "value"
    etype, m, _all = bt.diagnose(r.figures[0], sheet.by_id()["F04"], sheet, [stranger], {"META"})
    assert etype == "entity" and m.ticker == "META"


def test_store_fallback_does_not_make_future_restatements_visible():
    sheet = make_sheet()
    f = bt.read_draft("Operating expenses were $9.0 billion.", "NVDA", calendars=CAL,
                      default_year=2027).figures[0]
    public = bt.Ev("NVDA", "operating_expenses", None, "GAAP", None, sheet.period_end, 5e9, "USD",
                   "2026-08-26T20:00:00Z")
    later = bt.Ev("NVDA", "operating_expenses", None, "GAAP", None, sheet.period_end, 9e9, "USD",
                  "2026-11-26T20:00:00Z")
    claimed = bt.store_fact(f, sheet, [public, later], CAL)
    assert claimed.values() == [5e9]
    assert not bt.value_match(f, claimed.value, claimed.unit)
    # If every version is future, only the first release may supply the look-ahead fact.
    public = replace(public, first_public="2026-08-27T20:00:00Z")
    claimed = bt.store_fact(f, sheet, [public, later], CAL)
    assert claimed.values() == [5e9] and claimed.first_public > sheet.as_of


def test_future_other_basis_cannot_demote_an_error_to_true():
    sheet = make_sheet()
    f = bt.read_draft("Net income was $61.9 billion.", "NVDA", calendars=CAL, default_year=2027).figures[0]
    later = bt.Ev("NVDA", "net_income", None, "non-GAAP", None, sheet.period_end, 61.9e9, "USD",
                  "2026-11-26T20:00:00Z")
    assert bt.unstated_basis_values(f, sheet.by_id()["F04"], [later], sheet.as_of) == []
    assert bt.diagnose(f, sheet.by_id()["F04"], sheet, [later])[0] == "value"
    later = replace(later, first_public="2026-08-26T20:00:00Z")
    assert bt.unstated_basis_values(f, sheet.by_id()["F04"], [later], sheet.as_of) == [61.9e9]


def test_derived_margin_waits_until_both_inputs_are_public():
    early, late = "2026-08-01T20:00:00Z", "2026-08-04T20:00:00Z"

    def fact(value, accepted):
        return SimpleNamespace(value=value, accepted_at=bt.parse_ts(accepted), period_start=date(2026, 4, 27),
                               period_end=date(2026, 7, 26), segment=None, basis="GAAP")

    facts = {"revenue": [fact(100, early)], "gross_profit": [fact(70, late)],
             "cost_of_revenue": [fact(30, late)], "operating_income": [fact(40, late)]}
    store = SimpleNamespace(resolve_entity=lambda _t: (1, "NVIDIA"), fiscal_calendar=lambda _c: CAL["NVDA"][-1:],
                            facts=lambda _c, metric, **_kw: facts.get(metric, []))
    ratios = [e for e in bt.StoreIndex(store, tickers=["NVDA"]).items if e.metric.endswith("_margin")]
    assert len(ratios) == 3
    assert {e.first_public for e in ratios} == {late}


def test_segment_fallback_keeps_companies_named_in_the_story():
    p1 = P1 + " Meta competes for the same enterprise customers."
    p2 = P2_ERR.replace("$59.7 million", "$61.9 billion")
    planted = [dict(PLANTED_3[0], quote=PLANTED_3[0]["quote"].replace("$59.7 million", "$61.9 billion"),
                    value_text="$61.9 billion", error_type="entity"),
               dict(PLANTED_3[1], quote=PLANTED_3[1]["quote"].replace("$59.7 million", "$61.9 billion")),
               PLANTED_3[2]]
    stranger = bt.Ev("META", "net_income", None, "GAAP", None, "2026-06-30", 61.9e9, "USD",
                     "2026-07-29T20:03:23Z")
    v = validate(article(p1, p2, P3, P4), planted, evidence=[stranger])
    assert v.accepted, v.reasons
    error = next(e for e in v.planted if e["value_text"] == "$61.9 billion")
    assert error["type"] == "entity" and error["matched_evidence"]["ticker"] == "META"


# ------------------------------------------------------------------ prompt, request, split

def test_request_is_wonder_standard_mode_low_effort_strict_schema():
    sheet = make_sheet()
    job = bt.Job("NVDA-FY2027Q2-a", sheet.sheet_id, "a", "wire", ["scale", "basis", "value"])
    req = bt.build_request([(sheet, job)])
    assert req["model"] == "muse-wonder-1.0" and "contributor" not in req["model"]
    assert req["reasoning"] == {"effort": "low"} and req["store"] is False
    fmt = req["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"]["required"] == ["stories"]
    assert req["input"][0]["role"] == "system"            # stable prefix first, for prompt caching
    user = req["input"][1]["content"]
    assert "PLANT 3 ERRORS: scale, basis, value" in user and "F13 AMD revenue, quarter ended September 26, 2026" in user
    assert bt.request_hash(req) == bt.request_hash(copy.deepcopy(req))
    assert bt.stories_of({"stories": [{"sheet_id": sheet.sheet_id, "headline": "h", "article": "a",
                                       "planted_errors": []}]}, [(sheet, job)]).keys() == {job.job_id}


def test_cost_and_retry_policy():
    assert bt.cost_usd({"input_tokens": 1_000_000, "cached_tokens": 0, "output_tokens": 0}) == pytest.approx(4.0)
    assert bt.cost_usd({"input_tokens": 1_000_000, "cached_tokens": 1_000_000, "output_tokens": 1_000_000}) == \
        pytest.approx(20.2)
    assert 504 not in bt.RETRYABLE_STATUS and {429, 503} <= bt.RETRYABLE_STATUS


def _items(n_groups: int = 11) -> list[dict]:
    items = []
    for g in range(n_groups):
        for v in ("a", "b") if g % 3 == 0 else ("a",):
            items.append({"item_id": f"trap-T{g % 5}-G{g}-{v}", "group_id": f"T{g % 5}-G{g}", "ticker": f"T{g % 5}"})
    return items


def test_split_is_grouped_seeded_and_order_independent():
    items = _items()
    bt.assign_split(items)
    first = {it["item_id"]: it["split"] for it in items}
    shuffled = copy.deepcopy(items)
    random.Random(7).shuffle(shuffled)
    for it in shuffled:
        it.pop("split")
    bt.assign_split(shuffled)
    assert {it["item_id"]: it["split"] for it in shuffled} == first
    groups = {}
    for it in items:
        groups.setdefault(it["group_id"], set()).add(it["split"])
    assert all(len(s) == 1 for s in groups.values())
    n_test = sum(1 for s in first.values() if s == "test")
    assert n_test == round((1 - bt.DEV_FRACTION) * len(items)) and abs(n_test - 0.3 * len(items)) <= 1


# ------------------------------------------------------------------ the scorer (eval.py --corpus trap_v1)

def _gold_item() -> dict:
    v = validate(article(P1, P2_ERR, P3, P4), PLANTED_3)
    sheet = make_sheet()
    job = bt.Job("NVDA-FY2027Q2-a", sheet.sheet_id, "a", "wire", ["scale", "basis", "value"])
    it = bt.make_item(sheet, job, {"model": bt.MODEL}, v)
    it["split"] = "dev"
    return it


def _claims_for(it: dict, wrong: set[str], unverifiable: set[str] = frozenset()) -> list:
    pairs = []
    for k, (a, b) in enumerate(sorted([e["span"] for e in it["planted"] + it["accidental"]] +
                                      [c["span"] for c in it["true_claims"]])):
        vt = it["text"][a:b]
        status = "wrong" if vt in wrong else "unverifiable" if vt in unverifiable else "verified"
        reason = next((e["expected_reason"] for e in it["planted"] if e["span"] == [a, b]), "match")
        pairs.append([{"id": f"c{k + 1}", "value_text": vt, "span_text": vt, "span_start": a, "span_end": b},
                      {"status": status, "reason": reason if status == "wrong" else "match"}])
    return pairs


def test_scorer_counts_catches_false_alarms_and_unverifiable():
    it = _gold_item()
    errs = {e["value_text"] for e in it["planted"]}
    perfect = {it["item_id"]: {"pairs": _claims_for(it, errs, {"$108.0 billion", "2%"}), "error": None}}
    s = ev.score_traps([it], perfect)
    tot = sum(c.get("caught", 0) for c in s["per_type"].values())
    assert tot == 3 and s["true_claims"].get("true_wrong", 0) == 0 and s["true_claims"]["fl_unverifiable"] == 2
    assert s["sentences"].get("false_alarm_sentences", 0) == 0 and s["rows"][0]["all_caught"]
    noisy = {it["item_id"]: {"pairs": _claims_for(it, {"$59.7 million", "$96.2 billion"}), "error": None}}
    s = ev.score_traps([it], noisy)
    assert s["per_type"]["scale"]["caught"] == 1 and s["per_type"]["basis"].get("caught", 0) == 0
    assert s["per_type"]["basis"]["target_verified"] == 1
    assert s["true_claims"]["true_wrong"] == 1 and s["sentences"]["false_alarm_sentences"] == 1


# ------------------------------------------------------------------ the committed corpus

CORPUS = ROOT / "eval" / "corpus" / "trap_v1.jsonl"


@pytest.mark.skipif(not CORPUS.exists(), reason="eval/corpus/trap_v1.jsonl not built yet")
def test_committed_corpus_invariants():
    items = bt.read_jsonl(CORPUS)
    assert 20 <= len(items) <= 30
    assert {it["split"] for it in items} == {"dev", "test"}
    n_test = sum(it["split"] == "test" for it in items)
    assert abs(n_test - 0.3 * len(items)) <= 1.5
    by_group: dict[str, set] = {}
    for it in items:
        assert it["item_hash"] == bt.item_hash(it["text"], it["as_of"], it["context"])
        by_group.setdefault(it["group_id"], set()).add(it["split"])
        errors = it["planted"] + it["accidental"]
        assert len(errors) >= bt.MIN_ERRORS
        for e in errors:
            a, b = e["span"]
            assert it["text"][a:b] == e["value_text"]
            assert e["type"] in bt.ERROR_TYPES and e["expected_reason"] == bt.EXPECTED_REASON[e["type"]]
            if e["type"] == "look_ahead":
                assert e["claimed"]["first_public"] > it["as_of"]
            else:
                assert e["claimed"]["first_public"] <= it["as_of"]
        for c in it["true_claims"]:
            a, b = c["span"]
            assert it["text"][a:b] == c["value_text"]
        spans = sorted(tuple(x["span"]) for x in errors + it["true_claims"])
        assert all(p[1] <= q[0] for p, q in zip(spans, spans[1:]))   # every figure labeled once
        assert it["generation"]["model"] == "muse-wonder-1.0"
    assert all(len(s) == 1 for s in by_group.values())


@pytest.mark.skipif(not CORPUS.exists(), reason="eval/corpus/trap_v1.jsonl not built yet")
def test_eval_oracle_stub_scores_the_trap_corpus_perfectly(tmp_path):
    out = tmp_path / "run.json"
    ev.main(["--corpus", "trap_v1", "--split", "dev", "--stub", "oracle", "--quiet", "--no-cache",
             "--json-out", str(out)])
    rec = json.loads(out.read_text())
    per = rec["summary"]["per_type"]
    assert sum(c.get("n", 0) for c in per.values()) > 0
    assert all(c.get("caught", 0) == c.get("n", 0) for c in per.values())
    assert rec["summary"]["true_claims"].get("true_wrong", 0) == 0
    assert rec["summary"]["claims"].get("claims") == sum(
        c.get("n", 0) for c in per.values()) + rec["summary"]["true_claims"]["true_n"] + \
        rec["summary"]["true_claims"].get("fl_n", 0)


@pytest.mark.skipif(not (ROOT / "examples" / "trap_jpm_q1_2026.txt").exists(), reason="demo drafts not written yet")
def test_demo_drafts_are_dev_items_verbatim():
    items = {it["item_id"]: it for it in bt.read_jsonl(CORPUS)}
    for fn, iid in (("trap_jpm_q1_2026.txt", "trap-JPM-FY2026Q1-b"), ("trap_amd_q1_2026.txt", "trap-AMD-FY2026Q1-a")):
        it = items[iid]
        assert (ROOT / "examples" / fn).read_text(encoding="utf-8").strip() == it["text"]
        assert it["split"] == "dev"   # demos are never test items: the held-out split stays unseen


# ------------------------------------------------------------------ frozen-corpus validation and write guards

def _frozen_validation_fixture(tmp_path, monkeypatch):
    sheet = make_sheet()
    job = bt.Job("NVDA-FY2027Q2-a", sheet.sheet_id, "a", "wire", ["scale", "basis", "value"])
    story = {"sheet_id": sheet.sheet_id, "headline": HEADLINE, "article": article(P1, P2_ERR, P3, P4),
             "planted_errors": PLANTED_3}
    gen = {"model": bt.MODEL, "request_sha256": "a" * 64, "jobs": [job.to_json()],
           "output_text": json.dumps({"stories": [story]})}
    val = bt.validate_draft(sheet, story, calendars=CAL)
    item = bt.make_item(sheet, job, gen, val)
    item["split"] = "dev"
    path = tmp_path / "trap_v1.jsonl"
    # This deliberately cannot be parsed as a draft; touching it beyond split/hash metadata is a test failure.
    heldout = b'{ "split": "test", "text": "HELD-OUT SENTINEL" }\r\n'
    path.write_bytes((json.dumps(item) + "\n").encode() + heldout)
    cache = tmp_path / "wonder"
    cache.mkdir()
    (cache / f"{gen['request_sha256']}.json").write_text(json.dumps(gen))
    monkeypatch.setattr(bt, "WONDER_CACHE", cache)
    monkeypatch.setattr(bt, "TRAPS_PATH", path)
    monkeypatch.setattr(bt, "SheetBuilder", lambda _s: SimpleNamespace(
        quarters=lambda t: CAL[t], build=lambda _t, _q: sheet))
    monkeypatch.setattr(bt, "StoreIndex", lambda _s: SimpleNamespace(items=[]))
    monkeypatch.setattr(bt, "calendars_of", lambda _s: CAL)
    monkeypatch.setattr(bt, "make_client", lambda: pytest.fail("cache-only validation made a model client"))
    monkeypatch.setattr(bt, "cache_find", lambda *_a: pytest.fail("validation used an unpinned newer response"))
    return path, gen


def test_validate_dev_uses_pinned_cache_and_leaves_heldout_bytes_alone(tmp_path, monkeypatch):
    path, _gen = _frozen_validation_fixture(tmp_path, monkeypatch)
    before = path.read_bytes()
    report = bt.validate_committed_dev(object(), path)
    assert report["ok"] and report["validated_dev_rows"] == report["dev_rows"] == 1
    assert report["gold"]["errors"] == 3 and report["gold"]["guidance_figures"] == 2
    assert report["held_out_rows_validated"] == 0 and report["model_calls"] == 0
    assert report["before"] == report["after"] and path.read_bytes() == before


def test_validate_dev_reports_missing_original_cache_without_regenerating(tmp_path, monkeypatch):
    path, _gen = _frozen_validation_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(bt, "WONDER_CACHE", tmp_path / "empty")
    before = path.read_bytes()
    report = bt.validate_committed_dev(object(), path)
    assert not report["ok"] and report["validated_dev_rows"] == 0
    assert "original Wonder response missing" in report["errors"][0]["reason"]
    assert path.read_bytes() == before and report["model_calls"] == 0


def test_validate_dev_detects_gold_drift_without_rewriting_it(tmp_path, monkeypatch):
    path, _gen = _frozen_validation_fixture(tmp_path, monkeypatch)
    lines = path.read_bytes().splitlines(keepends=True)
    item = json.loads(lines[0])
    item["planted"][0]["expected_reason"] = "entity_mismatch"
    path.write_bytes((json.dumps(item) + "\n").encode() + lines[1])
    before = path.read_bytes()
    report = bt.validate_committed_dev(object(), path)
    assert not report["ok"] and report["errors"][0]["fields"] == ["planted"]
    assert path.read_bytes() == before


def test_frozen_generation_is_refused_before_loading_store(tmp_path, monkeypatch):
    path, _gen = _frozen_validation_fixture(tmp_path, monkeypatch)
    import receipts.store
    monkeypatch.setattr(receipts.store, "Store", lambda: pytest.fail("frozen generation loaded the store"))
    before = path.read_bytes()
    with pytest.raises(SystemExit, match="frozen held-out split"):
        bt.main([])
    assert path.read_bytes() == before


def test_dry_run_on_frozen_corpus_only_validates_dev_and_writes_optional_summary(tmp_path, monkeypatch):
    path, _gen = _frozen_validation_fixture(tmp_path, monkeypatch)
    import receipts.store
    monkeypatch.setattr(receipts.store, "Store", lambda: SimpleNamespace(close=lambda: None))
    out = tmp_path / "summary.json"
    before = path.read_bytes()
    assert bt.main(["--dry-run", "--json-out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["held_out_rows_validated"] == 0 and report["validated_dev_rows"] == 1
    assert path.read_bytes() == before
    with pytest.raises(SystemExit):
        bt.main(["--validate-dev", "--json-out", str(path)])
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["cache_missing", "budget"])
def test_incomplete_initial_generation_preserves_corpus_and_rejects(tmp_path, monkeypatch, failure):
    sheet = make_sheet()
    job = bt.Job("NVDA-FY2027Q2-a", sheet.sheet_id, "a", "wire", ["scale", "basis", "value"])
    path, rejected = tmp_path / "trap_v1.jsonl", tmp_path / "rejects.jsonl"
    path.write_text('{"split":"dev","draft":"prior corpus"}\n')
    rejected.write_text('{"reason":"prior reject"}\n')
    before = (path.read_bytes(), rejected.read_bytes())
    monkeypatch.setattr(bt, "TRAPS_PATH", path)
    monkeypatch.setattr(bt, "REJECTS_PATH", rejected)
    monkeypatch.setattr(bt, "build_sheets", lambda _s: [sheet])
    monkeypatch.setattr(bt, "plan_calls", lambda _s, **_kw: [[job]])
    monkeypatch.setattr(bt, "StoreIndex", lambda _s: SimpleNamespace(items=[]))
    monkeypatch.setattr(bt, "calendars_of", lambda _s: CAL)
    import receipts.store
    monkeypatch.setattr(receipts.store, "Store", lambda: SimpleNamespace(close=lambda: None))

    def generate(*_a, **_kw):
        if failure == "budget":
            raise bt.BudgetExceeded("synthetic budget stop")
        return None

    monkeypatch.setattr(bt, "generate", generate)
    assert bt.main(["--dry-run"]) == 1
    assert (path.read_bytes(), rejected.read_bytes()) == before
