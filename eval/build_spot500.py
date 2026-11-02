"""Freeze an independent, source-only 500-company spot check. Never calls NoCap.

python eval/build_spot500.py --data-root /path/to/Meta/data            # v1 (frozen; rebuilds identically)
python eval/build_spot500.py --data-root /path/to/Meta/data --set v2   # v2: new seed, v1 companies excluded
python eval/build_spot500.py --data-root /path/to/Meta/data --set v3   # v3: new seed; v1, v2 and fix-tested companies excluded
python eval/build_spot500.py --data-root /path/to/Meta/data --set full # every eligible company, about 1,000 items
python eval/build_spot500.py --data-root /path/to/Meta/data --set full2 # full's frame plus EX-99/EX-99.01 releases
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
import gzip
import hashlib
import html as html_module
import json
from pathlib import Path
import random
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).parent]
sys.path.insert(0, str(ROOT))
from eval import build_corpus as base

SEED = 20261101
COMMIT = "30336129d119addf94440be6e082cad2c4fe315f"
EXCLUDED = frozenset("AAPL AMD AMZN BAC GOOGL JPM META MSFT NFLX NVDA TSLA".split())
IRREGULAR = frozenset("COST KR DPZ PEP AZO".split())
MUTATORS = (*base.MUTATORS, "direction")
REASONS = {**base.EXPECTED_REASON, "direction": "value_mismatch"}
DIRECTION_PAIRS = {
    "rose": "fell", "fell": "rose", "increased": "decreased", "decreased": "increased",
    "increase": "decrease", "decrease": "increase", "grew": "declined", "declined": "grew",
    "expanded": "contracted", "contracted": "expanded", "expansion": "contraction",
    "contraction": "expansion", "up": "down", "down": "up", "higher": "lower", "lower": "higher",
}
DIRECTION_RE = re.compile(r"\b(" + "|".join(DIRECTION_PAIRS) + r")\b", re.I)
# The 30 companies sampled by the frozen v1 set (eval/spot500_manifest.json).
V1_COMPANIES = frozenset("A ABNB AMT APTV AZO BBY CFG CHD CIEN DIS FE FLEX FOXA HAL HCA IP IVZ JBHT KR "
                         "MLM MPC MTD NRG PLTR PNC PWR TKO TWLO WELL WFC".split())
# The 30 companies sampled by the frozen v2 set (eval/spot500_v2_manifest.json).
V2_COMPANIES = frozenset("AMGN AMP BE CF CHTR CNP COF DE DOC EXE FITB ICE KEY KIM MCHP MSCI PEP PSX RF RL "
                         "SWKS TFC TROW TSN TTWO UBER ULTA UNH VMC WTW".split())
# Companies the verifier fixes made after v1's live run were tested or written on,
# copied from the frozen derivation eval/spot500_v3_exclusions.json, which the
# build checks. ED is matched only by the row label "Pro Forma Consolidated Revenue".
V3_FIX_TEST_COMPANIES = frozenset("AKAM AMD AMGN AXP CARR CMCSA CNC DIS ED EXR FDS GEHC HPE INTC JPM LEN MNST "
                                  "MRK MSI PFE RF SJM SYY TFC TGT TSLA UDR VLO".split())
V3_FIX_CODE_COMPANIES = frozenset("CMCSA DIS ED EL FDS MPC SYY".split())
V3_PROBE_COMPANIES = frozenset("BAX CSGP TGT".split())
# The full set re-derives both lists over 50a4eac..8d69d3c (eval/spot500_full_exclusions.json,
# checked by the build). It no longer excludes them: they tag company_in_development. Probable
# false positives: ED ("Pro Forma Consolidated Revenue"), GPC ("Genuine non-GAAP rows"), O
# ("Federal Realty") and WMB ("Williams-Sonoma").
FULL_FIX_TEST_COMPANIES = frozenset("AKAM AMD AMGN AXP CARR CMCSA CNC DAL DIS ED EXR FDS FRT GEHC GPC HPE INTC ITW "
                                    "IVZ JPM LEN MNST MRK MSI O PFE RF SJM SNA SYY TFC TGT TSLA UDR VLO WMB WMT "
                                    "WSM".split())
FULL_FIX_CODE_COMPANIES = frozenset("CMCSA DAL DIS ED EL FDS MPC NVDA SYY".split())
FULL_PROBE_COMPANIES = frozenset("ADI BAX CSGP TGT".split())
# The owner names Alliant among the probe companies; no scanned test or fix line names it.
FULL_TASK_NAMED_COMPANIES = frozenset({"LNT"})
# The first full freeze (7a68626, merged as 370d943) excluded the development companies. The owner
# superseded it before any run so that the comprehensive set includes every company.
FULL_SUPERSEDED = {'commit': '7a68626363949935aecf49ff636137b98f0c8a7a', 'merged_as': '370d943b95566d39845921afd5e86baada4096ec',
                   'corpus_sha256': '2494d90b887fd183e3a7e59e349c35a383e68fa999d0057cc7f72f5e7dd2bb62',
                   'manifest_sha256': 'fd6c45e8187476f826002368c834d093f3505086588a2dae2a06c8d1d615ac39',
                   'note': "the first freeze was superseded before any run, at the owner's request, to include every "
                           "company"}
# Full-set scale: total items, mutants per mutator, guidance controls, originals per company,
# and the item count every source feature should reach where the frame allows.
FULL_SCALE = {'total': 1000, 'mutants': 60, 'controls': 50, 'per_company': 3, 'feature_target': 20}
# The full set's company_in_development list (52), carried into full2 unchanged.
FULL_IN_DEVELOPMENT = (EXCLUDED | V3_FIX_TEST_COMPANIES | V3_FIX_CODE_COMPANIES | FULL_FIX_TEST_COMPANIES
                       | FULL_FIX_CODE_COMPANIES | FULL_PROBE_COMPANIES | FULL_TASK_NAMED_COMPANIES)
# full2 measures the round-8 version once, on sentences no earlier set used. Its frame adds companies whose
# earnings 8-K labels the release EX-99.01, or plain EX-99 as its single EX-99-type exhibit, read only when no
# exhibit of that 8-K is EX-99.1, so no earlier company's pairs change (eval/spot500_frame_gaps.py).
EXHIBIT_LABEL = re.compile(r"\b(?:EX[ -]?|Exhibit\s+)99(?:[.-]([0-9]+))?\b", re.I)
# Companies the round-8 and name-resolution fixes were tested or written on, derived over 22cd636..8c2fd78 into
# eval/spot500_full2_history.json (checked by the build). Probable false positives, kept as tags: FSLR ("First
# Quarter" in a fixture), SO ("Norfolk Southern"), and GM, GPN and COF (Nasdaq tiers in a listing pattern).
FULL2_FIX_TEST_COMPANIES = frozenset((
    "AAPL ALLE AMD AMGN AMZN AOS AXON BAC BDX CASY CHRW CHTR CNC COO COST D DHI DLTR DOV DPZ EL FCX "
    "FLEX FRT FSLR GOOGL GS GWW HD HIG ILMN INCY JBHT KEY LOW MCD MCO META MGM MLM MPWR MRSH MSFT MTD "
    "NDAQ NSC NVDA O ODFL ON ORLY PRU RVTY SBAC SJM SNA SO STZ TFC TGT VRTX VTRS WM WMT WRB").split())
FULL2_FIX_CODE_COMPANIES = frozenset((
    "AAPL ALLE AMD AMZN BDX CBOE CHTR COF COO COST D DLTR DPZ FCX FSLR GM GPN GS GWW HD ILMN INCY "
    "JBHT KEY KEYS LOW MCD META MLM MPWR MRK MRSH MSFT NDAQ NSC NVDA ODFL ON RVTY SBAC SNA SO TFC TGT "
    "VRTX VTRS WM WMT").split())
BUILDER_FILES = ('eval/build_spot500.py', 'eval/spot500_sources.py', 'eval/build_corpus.py')
# A hard quota with its nominal size, as the changes from v1 name it.
HARD_QUOTAS = (('banks', 3, 'bank', 'banks'), ('REITs or utilities', 2, 'REIT/utility', 'REITs or utilities'),
               ('variable-week companies', 2, 'variable-week', 'variable-week companies'))


@dataclass(frozen=True)
class SpotSet:
    """One frozen spot-check version. Every set runs the same procedure; only
    the seed, the companies excluded before sampling, and a quota that those
    exclusions leave unfillable may differ."""
    name: str
    seed: int
    commit: str
    commit_role: str
    corpus: str
    manifest: str
    extra_excluded: frozenset = frozenset()
    extra_excluded_from: str | None = None
    # Five named companies form the variable-week stratum. Excluding an earlier
    # set's picks can exhaust it; such a set reserves all eligible ones left.
    variable_week_cap: bool = False
    # From v3: named exclusion groups (key, companies, description), the earlier
    # sets among them, the frozen derivation file with its SHA-256, and a cap
    # that lowers any hard quota to the eligible companies left.
    exclusion_groups: tuple = ()
    previous_sets: tuple = ()
    derivation: tuple = ()
    cap_quotas: bool = False
    # The full set: every eligible company at FULL_SCALE, without the sentences
    # these earlier sets used (their companies may stay).
    full: bool = False
    exclude_sentences_of: tuple = ()
    # A full set may keep the original development companies and tag, not exclude, the companies
    # named in development: (key, companies, description) groups whose union is in_development.
    exclude_original: bool = True
    development_groups: tuple = ()
    # full2: the exhibit-label fallback frame, and three company-history tags (in_development, new, seen).
    exhibit_fallback: bool = False
    history_seen: bool = False
    # The builder file that froze a set is provenance, not something a later
    # builder edit can recompute. Each set keeps the hash of the builder that
    # froze it, the git blob eval/build_spot500.py at its freezing commit: v1 050be2e5fe97f9920308e9d63643b7ee8c9771c7,
    # v2 7bdcb11ccb371e897e58ed710dd58ee68a267162, v3 02d6e41f7f140c54db2e04fa03a5a0bbf9544576,
    # full 9bff2eb62f9fa6c55153687d3355cb9b7216e96e.
    frozen_builder_hashes: tuple = ()

    @property
    def version(self):
        return f"spot500_{self.name}"

    @property
    def excluded(self):
        return (EXCLUDED if self.exclude_original else frozenset()).union(
            self.extra_excluded, *(companies for _, companies, _ in self.exclusion_groups))

    @property
    def in_development(self):
        return frozenset().union(*(companies for _, companies, _ in self.development_groups))


SETS = {
    'v1': SpotSet('v1', SEED, COMMIT, 'worktree source baseline; uncommitted new evaluation files',
                  'corpus/spot500_v1.jsonl', 'spot500_manifest.json',
                  frozen_builder_hashes=(('eval/build_spot500.py',
                                          '9b4c79e938ac9cb061a6b06876dc0a09ea31de920b0d79c54b6647d50e64c033'),)),
    'v2': SpotSet('v2', 20261102, '6099dc974adf4c934d4732c4bb01d18ee1b622d6',
                  'worktree source baseline; uncommitted builder parameterization and new evaluation files',
                  'corpus/spot500_v2.jsonl', 'spot500_v2_manifest.json',
                  extra_excluded=V1_COMPANIES, extra_excluded_from='v1', variable_week_cap=True,
                  frozen_builder_hashes=(('eval/build_spot500.py',
                                          '0aeacfcb74fb48003a17760516b4cb807c0ec2eab40a55fe26ca7025082c49f9'),)),
    'v3': SpotSet('v3', 20261103, '84b8fd48a94de10027e041be69595362a0d4ca40',
                  'the deployed code that v3 measures; uncommitted builder change and new evaluation files',
                  'corpus/spot500_v3.jsonl', 'spot500_v3_manifest.json',
                  exclusion_groups=(
                      ('spot500_v1_companies', V1_COMPANIES, 'companies sampled by spot500_v1'),
                      ('spot500_v2_companies', V2_COMPANIES, 'companies sampled by spot500_v2'),
                      ('verifier_fix_test_companies', V3_FIX_TEST_COMPANIES,
                       'companies named in the verifier-fix tests added since spot500_v1'),
                      ('verifier_fix_code_companies', V3_FIX_CODE_COMPANIES,
                       "companies named in the fixed verifier's added lines"),
                      ('reviewer_probe_companies', V3_PROBE_COMPANIES, "companies the reviewer's probes used")),
                  previous_sets=('v1', 'v2'),
                  derivation=('spot500_v3_exclusions.json',
                              '2e5ecef757b5f553e531b7e11871aa0efafd8c945476e54a1671e89d91af786e'),
                  cap_quotas=True,
                  frozen_builder_hashes=(('eval/build_spot500.py',
                                          '84cd45a9f1f7031360c309c005f45869b186b840d968bf5cce3c946e729c054b'),)),
    'full': SpotSet('full', 20261104, '8d69d3c19c3bdfbc3825b48df3e1cb8e7b0edfa0',
                    'the deployed code that the full set measures; uncommitted builder change and new evaluation files',
                    'corpus/spot500_full.jsonl', 'spot500_full_manifest.json',
                    derivation=('spot500_full_exclusions.json',
                                'ff3886abce011286d41388919966fef2e75c8853b3072751d70143ab93579db8'),
                    full=True, exclude_sentences_of=('v1', 'v2', 'v3'), exclude_original=False,
                    development_groups=(
                        ('original_development_companies', EXCLUDED, 'the original development companies'),
                        ('spot500_v3_fix_test_companies', V3_FIX_TEST_COMPANIES,
                         'companies spot500_v3 found named in verifier-fix tests'),
                        ('spot500_v3_fix_code_companies', V3_FIX_CODE_COMPANIES,
                         "companies spot500_v3 found named in the fixed verifier's added lines"),
                        ('verifier_fix_test_companies', FULL_FIX_TEST_COMPANIES,
                         'companies named in the verifier-fix tests added since spot500_v1'),
                        ('verifier_fix_code_companies', FULL_FIX_CODE_COMPANIES,
                         "companies named in the fixed verifier's added lines"),
                        ('reviewer_probe_companies', FULL_PROBE_COMPANIES, "companies the reviewer's probes used"),
                        ('owner_named_probe_companies', FULL_TASK_NAMED_COMPANIES,
                         'probe company the owner names, which no scanned line names')),
                    frozen_builder_hashes=(('eval/build_spot500.py',
                                            '2e9a365c9e56a72febb58de9a77b3bd3fd0d5b8591e28860ea491ca455208511'),)),
    'full2': SpotSet('full2', 20261105, '8c2fd7815ccd0d1108a5df39428f6b0f091a0c65',
                     'the fixed code full2 measures (wip/day3-handoff, round 8 and the names fix); uncommitted '
                     'evaluation files',
                     'corpus/spot500_full2.jsonl', 'spot500_full2_manifest.json',
                     full=True, exclude_sentences_of=('v1', 'v2', 'v3', 'full'), exclude_original=False,
                     exhibit_fallback=True, history_seen=True,
                     development_groups=(
                         ('spot500_full_in_development', FULL_IN_DEVELOPMENT,
                          'companies the full set tags company_in_development'),
                         ('round8_fix_test_companies', FULL2_FIX_TEST_COMPANIES,
                          'companies named in the round-8 and name-resolution fix tests'),
                         ('round8_fix_code_companies', FULL2_FIX_CODE_COMPANIES,
                          "companies named in the lines the round-8 fixes add to the verifier")),
                     derivation=('spot500_full2_history.json',
                                 '3874b07e871278317b8f390106b2fb6c7eaf0dcae81b49579f01dddeb3785f65')),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def targetable(n):
    return (n["role"] == "current" and n["screen"] == "ok" and not n["approx"]
            and not n["fx_adjusted"] and not n["is_range"] and not n["qualifier"])


def mutate_direction(sentence, doc, company_rows):
    """Flip exactly one local change word, preserving every numeric character.

    Only an XBRL-screened, nonzero change is eligible. A shared change verb for
    multiple figures is rejected, as are level statements such as 'rose to 25%'.
    """
    text, nums = sentence["text"], sentence["numbers"]
    for i, n in enumerate(nums):
        if not targetable(n) or not n["growth"] or n["kind"] not in ("pct", "pp", "bps") or not n["value"]:
            continue
        exact = re.search(r"xbrl=([-+\d.eE]+)", n.get("screen_detail") or "")
        if exact is None or float(exact.group(1)) * n["sign"] <= 0:
            continue
        left = nums[i - 1]["end"] if i else 0
        matches = list(DIRECTION_RE.finditer(text, left, n["start"]))
        if not matches:
            continue
        match = matches[-1]
        between = text[match.end():n["start"]]
        if len(between) > 45 or re.search(r"\b(?:to|from)\b|[;.!?]", between, re.I):
            continue
        # A following number that inherits this verb would also change meaning.
        if i + 1 < len(nums) and nums[i + 1]["growth"]:
            clause = text[n["end"]:nums[i + 1]["start"]]
            if not DIRECTION_RE.search(clause) and not re.search(r"[;.!?]", clause):
                continue
        word = DIRECTION_PAIRS[match.group().lower()]
        if match.group().isupper():
            word = word.upper()
        elif match.group()[0].isupper():
            word = word.capitalize()
        shift = len(word) - len(match.group())
        return base.Mutation(
            mutator="direction", text=base._replace(text, match.start(), match.end(), word),
            context=doc["context"], as_of=None, target_value_text=n["text"],
            target_span=(n["start"] + shift, n["end"] + shift), original_value_text=n["text"],
            expected_value=n["sign"] * n["value"], affected_value_texts=[n["text"]],
            edit={"start": match.start(), "end": match.end(), "before": match.group(), "after": word},
            screen="xbrl", detail={"metric": n["metric"], "from": match.group(), "to": word})
    return None


def mutate_single_basis(sentence, doc, companies):
    """Stage-2 basis swap for a single explicitly adjusted figure.

    The existing pair mutator is attempted first. This extension changes one
    basis token and requires a different, available companyfacts GAAP value.
    """
    old = base.mutate_basis(sentence, doc, companies)
    if old:
        return old
    nums = sentence["numbers"]
    if len(nums) != 1:
        return None
    n = nums[0]
    if n["basis"] != "non-GAAP" or n["qualifier"] or n["growth"] or n["role"] != "current" or n["is_range"]:
        return None
    # An adjustment amount, excluded cost, or EPS impact is not the level of
    # the adjusted measure. Replacing one of two basis labels is not a swap.
    if re.search(r"\b(?:both|includ\w*|exclud\w*|impact|reduc\w*|benefit|costs?|charges?|tax|"
                 r"chang\w*|increas\w*|decreas\w*|grew|growth|result\w*|retrospective|"
                 r"compar\w*|prior|previous|favorable|unfavorable)\b|reported and",
                 sentence["text"], re.I):
        return None
    if n["metric"] == "eps_diluted" and n["scale"] != "units":
        return None
    if len(re.findall(r"\b(?:non-GAAP|adjusted)\b", sentence["text"], re.I)) != 1:
        return None
    years = {int(m.group()) for m in base.YEAR_RE.finditer(sentence['text'])}
    if years - {doc['fy'], date.fromisoformat(doc['period_end']).year}:
        return None
    if not re.search(r"\b(?:was|were|of|at|is)\s*$|:\s*$", sentence['text'][:n['start']], re.I):
        return None
    token = re.search(r"\b(?:non-GAAP|adjusted)\b", sentence["text"][:n["start"]], re.I)
    if not token:
        return None
    series = companies[sentence["ticker"]]["series"].get(n["metric"], {})
    actual = base.series_at(series, date.fromisoformat(doc["period_end"]))
    if actual is None:
        return None
    actual /= base.SCALE_FACTOR[n["scale"]] if n["kind"] == "money" else 1
    if not base._clearly_apart(actual, n["sign"] * n["value"], n["decimals"]):
        return None
    shift = 4 - len(token.group())
    return base.Mutation(
        mutator="basis", text=base._replace(sentence["text"], token.start(), token.end(), "GAAP"),
        context=doc["context"], as_of=None, target_value_text=n["text"],
        target_span=(n["start"] + shift, n["end"] + shift), original_value_text=n["text"],
        expected_value=actual, affected_value_texts=[n["text"]],
        edit={"start": token.start(), "end": token.end(), "before": token.group(), "after": "GAAP"},
        screen="xbrl", detail={"metric": n["metric"], "target_basis": "GAAP",
                               "true_basis_of_number": "non-GAAP", "stated_basis": "GAAP"})


@contextmanager
def mutation_environment(companies, seed=SEED):
    """Reuse legacy pure mutators without changing their module or frozen corpora."""
    adapted = {}
    for ticker, c in companies.items():
        names = {c["name"], c.get("sec_title", c["name"]), ticker}
        adapted[ticker] = base.Company(ticker, c["name"], tuple(sorted(names, key=lambda s: (-len(s), s))),
                                       "NYSE", "bank" if is_bank(c) else "tech")
    from eval.spot500_sources import expected_for, within_tolerance
    with patch.multiple(base, SEED=seed, COMPANIES=adapted, TICKERS=tuple(sorted(companies)), targetable=targetable,
                        expected_for=expected_for, within_tolerance=within_tolerance):
        yield


def is_bank(company):
    # The GICS "Asset Management & Custody Banks" bucket also holds asset
    # managers. Only these two explicit bank groups satisfy the bank quota.
    return company.get("sub_industry") in ("Diversified Banks", "Regional Banks")


def is_utility_reit(company):
    return bool(re.search(r"REIT|utilities|utility", company.get("sub_industry", ""), re.I))


def mutation_sentence_safe(s):
    if re.search(r"\b(?:surpass\w*|exceed\w*|topp\w*|at (?:least|most)|up to|"
                 r"more than|less than|nearly|almost|approximately|about|"
                 r"exclud\w*|includ\w*|impact|adjustments?|retrospective)\b|"
                 r"\b(?:over|under)\s+\$?\d", s['text'], re.I):
        return False
    return not (any(n['metric'] == 'capital_returned' for n in s['numbers']) and re.search(
        r"\b(?:through|via|in)\s+(?:cash\s+)?(?:dividends|repurchases|share buybacks)\b", s['text'], re.I))


def mutation_candidates(sentences, docs, companies, *, seed=SEED):
    by_label = defaultdict(dict)
    for d in docs.values():
        by_label[d["ticker"]][(d["fy"], d["fp"])] = d
    candidates = defaultdict(list)
    with mutation_environment(companies, seed):
        for s in sorted(sentences, key=lambda s: s["sentence_id"]):
            if s["kind"] == "forward_looking":
                continue
            d = docs[s["doc_id"]]
            # Bounds must not become supposedly false by changing a threshold
            # that is still below/above the actual value.
            if not mutation_sentence_safe(s):
                continue
            for mut in MUTATORS:
                # A context/time edit must affect only one numeric claim.
                if mut in ("entity", "look_ahead") and len(s["numbers"]) != 1:
                    continue
                if mut == "direction":
                    m = mutate_direction(s, d, companies)
                elif mut == "basis":
                    m = mutate_single_basis(s, d, companies)
                elif mut == "entity":
                    # A context swap is safe only for company-free prose.
                    # Explicit names not in the S&P aliases can otherwise
                    # survive it (e.g. 'Deckers' vs 'Deckers Outdoor').
                    name = companies[s['ticker']]['name']
                    first = re.split(r"[\s,.]+", name)[0]
                    if re.search(r"\b" + re.escape(first) + r"\b", s['text'], re.I) and not base._entity_mentions(s['text'], base.COMPANIES[s['ticker']]):
                        continue
                    if not base._entity_mentions(s['text'], base.COMPANIES[s['ticker']]):
                        if len(s['text']) > 160 or re.search(r"\b(?!company[’']s)[A-Za-z]+[’']s\b", s['text'], re.I):
                            continue
                        if not re.match(r"(?:Net|Total|Record|First|Second|Third|Fourth|GAAP|Revenues?|Sales|Operating|Gross|Diluted|Earnings|Cash|The Company)\b", s['text'], re.I):
                            continue
                    m = base.mutate_entity(s, d, companies, by_label)
                elif mut == "look_ahead":
                    first = min(d["accepted_at"], d.get("tenq_accepted_at", d["accepted_at"]))
                    m = base.mutate_look_ahead(s, {**d, "accepted_at": first}, companies)
                else:
                    m = getattr(base, f"mutate_{mut}")(s, d, companies)
                if m and m.screen != "assumed" and len(m.affected_value_texts) == 1:
                    candidates[mut].append((s, m))
    return candidates


def features(sentence, company, doc):
    out = set()
    for n in sentence["numbers"]:
        metric = n["metric"]
        if metric in ("gross_margin", "operating_margin", "net_margin"):
            out.add(metric)
            if n["kind"] in ("pp", "bps") and n["growth"]:
                out.add("margin_change")
        elif metric in ("cash_and_equivalents", "total_assets", "total_debt", "shares_outstanding"):
            out.update(("balance_sheet", metric))
        else:
            out.add(metric or "other")
        if n["growth"] and n["kind"] == "pct":
            out.add("growth")
        if n["basis"] == "non-GAAP":
            out.add("non_gaap")
    if is_bank(company):
        out.add("bank")
        if any(n["metric"] == "revenue" for n in sentence["numbers"]):
            out.add("bank_revenue")
    if is_utility_reit(company):
        out.add("reit_or_utility")
    if sentence["ticker"] in IRREGULAR:
        out.add("variable_week_company")
    if doc.get("quarter_days", 0) >= 110:
        out.add("16_or_17_week_quarter")
    if sentence["kind"] == "forward_looking":
        out.add("guidance")
    return sorted(out)


def make_item(s, d, company, mutation=None, *, version="spot500_v1"):
    oid = version.replace("_", "-") + "-" + s["sentence_id"]
    control = s["kind"] == "forward_looking"
    mut = mutation.mutator if mutation else ("forward_looking_control" if control else "none")
    text = mutation.text if mutation else s["text"]
    context = mutation.context if mutation else d["context"]
    as_of = mutation.as_of if mutation else None
    gold = []
    target_found = False
    for n in s["numbers"]:
        span, vt = [n["start"], n["end"]], n["text"]
        if mutation and mutation.edit:
            e = mutation.edit
            shift = len(e["after"]) - (e["end"] - e["start"])
            if span[0] >= e["end"]:
                span = [x + shift for x in span]
            elif span[0] <= e["start"] and e["end"] <= span[1]:
                span[1] += shift
        is_target = bool(mutation and vt == mutation.original_value_text
                         and span == list(mutation.target_span) and not target_found)
        if is_target:
            span, vt = list(mutation.target_span), mutation.target_value_text
            target_found = True
        gold.append({"value_text": vt, "span": span, "metric": n["metric"],
                     "basis": mutation.detail.get("target_basis", n["basis"]) if is_target else n["basis"],
                     "role": "target" if is_target else "guidance" if control else "true",
                     "expected_status": "wrong" if is_target else "unverifiable" if control else "verified",
                     "expected_reason": REASONS[mut] if is_target else "forward_looking" if control else "match",
                     "source_screen": n["screen"], "source_screen_detail": n["screen_detail"]})
    if mutation and not target_found:
        raise ValueError("Mutation target absent")
    for g in gold:
        assert text[slice(*g["span"])] == g["value_text"], (s["sentence_id"], g)
    return {"item_id": oid + ("-" + mut if mutation else ""), "corpus_version": version,
            "ticker": s["ticker"], "text": text, "context": context, "as_of": as_of,
            "item_hash": base.item_hash(text, as_of, context), "original_item_id": oid if mutation else None,
            "mutator": mut, "features": features(s, company, d), "gold_claims": gold,
            "expected_status": "wrong" if mutation else "unverifiable" if control else "verified",
            "expected_reason": REASONS[mut] if mutation else "forward_looking" if control else "match",
            "target_span": list(mutation.target_span) if mutation else None,
            "target_value_text": mutation.target_value_text if mutation else None,
            "mutation": asdict(mutation) if mutation else None,
            "source": {"doc_id": s["doc_id"], "sentence_id": s["sentence_id"],
                       "original_text": s["text"], "sentence_sha256": digest(s["text"].encode()),
                       "period_end": d["period_end"], "fiscal_year": d["fy"], "fiscal_period": d["fp"]}}
# Explicit grouping of the archived S&P record's GICS sub-industry strings.
# This is selection metadata only, not an inference from financial outcomes.
SECTOR_GROUPS = {
    'Communication Services': '''Advertising|Broadcasting|Cable & Satellite|Integrated Telecommunication Services|Interactive Home Entertainment|Interactive Media & Services|Movies & Entertainment|Publishing|Wireless Telecommunication Services''',
    'Consumer Discretionary': '''Apparel Retail|Apparel, Accessories & Luxury Goods|Automobile Manufacturers|Automotive Parts & Equipment|Automotive Retail|Broadline Retail|Casinos & Gaming|Computer & Electronics Retail|Distributors|Footwear|Home Improvement Retail|Homebuilding|Homefurnishing Retail|Hotels, Resorts & Cruise Lines|Leisure Products|Other Specialty Retail|Restaurants|Specialized Consumer Services''',
    'Consumer Staples': '''Agricultural Products & Services|Consumer Staples Merchandise Retail|Distillers & Vintners|Food Distributors|Food Retail|Household Products|Packaged Foods & Meats|Personal Care Products|Soft Drinks & Non-alcoholic Beverages|Tobacco''',
    'Energy': '''Integrated Oil & Gas|Oil & Gas Equipment & Services|Oil & Gas Exploration & Production|Oil & Gas Refining & Marketing|Oil & Gas Storage & Transportation''',
    'Financials': '''Asset Management & Custody Banks|Consumer Finance|Diversified Banks|Financial Exchanges & Data|Insurance Brokers|Investment Banking & Brokerage|Life & Health Insurance|Multi-Sector Holdings|Multi-line Insurance|Property & Casualty Insurance|Regional Banks|Reinsurance|Transaction & Payment Processing Services''',
    'Health Care': '''Biotechnology|Health Care Distributors|Health Care Equipment|Health Care Facilities|Health Care Services|Health Care Supplies|Health Care Technology|Life Sciences Tools & Services|Managed Health Care|Pharmaceuticals''',
    'Industrials': '''Aerospace & Defense|Agricultural & Farm Machinery|Air Freight & Logistics|Building Products|Cargo Ground Transportation|Construction & Engineering|Construction Machinery & Heavy Transportation Equipment|Data Processing & Outsourced Services|Diversified Support Services|Electrical Components & Equipment|Environmental & Facilities Services|Heavy Electrical Equipment|Human Resource & Employment Services|Industrial Conglomerates|Industrial Machinery & Supplies & Components|Passenger Airlines|Passenger Ground Transportation|Rail Transportation|Research & Consulting Services|Trading Companies & Distributors''',
    'Information Technology': '''Application Software|Communications Equipment|Consumer Electronics|Electronic Components|Electronic Equipment & Instruments|Electronic Manufacturing Services|IT Consulting & Other Services|Internet Services & Infrastructure|Semiconductor Materials & Equipment|Semiconductors|Systems Software|Technology Distributors|Technology Hardware, Storage & Peripherals''',
    'Materials': '''Commodity Chemicals|Construction Materials|Copper|Fertilizers & Agricultural Chemicals|Gold|Industrial Gases|Metal, Glass & Plastic Containers|Paper & Plastic Packaging Products & Materials|Specialty Chemicals|Steel''',
    'Real Estate': '''Data Center REITs|Health Care REITs|Hotel & Resort REITs|Industrial REITs|Multi-Family Residential REITs|Office REITs|Other Specialized REITs|Real Estate Services|Retail REITs|Self-Storage REITs|Single-Family Residential REITs|Telecom Tower REITs|Timber REITs''',
    'Utilities': '''Electric Utilities|Gas Utilities|Independent Power Producers & Energy Traders|Multi-Utilities|Water Utilities''',
}
SECTOR_BY_INDUSTRY = {industry: sector for sector, values in SECTOR_GROUPS.items() for industry in values.split('|')}
FEATURE_PRIORITIES = ('net_margin', 'margin_change', 'bank_revenue', 'cash_and_equivalents',
                      'total_assets', 'total_debt', 'shares_outstanding',
                      'gross_margin', 'operating_margin', '16_or_17_week_quarter')


def select_companies(companies, docs, sentences, *, count=30, seed=SEED, excluded=EXCLUDED,
                     variable_week_cap=False, cap_quotas=False):
    """Seeded, coverage-constrained sector sample; consult source tags only.

    Excluded companies leave the eligible pool before any reservation, so
    every quota and the sector fill draw only from the remaining companies.
    With variable_week_cap, the variable-week quota of two is lowered to the
    number of eligible variable-week companies left; cap_quotas does the same
    for every hard quota (banks, REITs or utilities, variable-week). Otherwise
    the quotas are strict.
    """
    rng = random.Random(seed)
    historical = defaultdict(list)
    for sentence in sorted(sentences, key=lambda s: s['sentence_id']):
        if sentence['kind'] != 'forward_looking':
            historical[sentence['ticker']].append(sentence)
    eligible = sorted(set(historical) - excluded)
    pools = {ticker: set(f for s in historical[ticker]
                         for f in features(s, companies[ticker], docs[s['doc_id']])) for ticker in eligible}
    chosen, reservations = [], []

    def reserve(name, candidates, minimum):
        pool = sorted(candidates)
        needed = max(0, minimum - len(set(pool) & set(chosen)))
        available = [t for t in pool if t not in chosen]
        if len(available) < needed:
            raise ValueError(f'Insufficient source companies for {name}: {len(pool)}')
        picks = rng.sample(available, needed)
        chosen.extend(picks)
        reservations.append({'constraint': name, 'minimum': minimum, 'selected': picks})

    banks = [t for t in eligible if is_bank(companies[t])]
    reserve('banks', banks, min(3, len(banks)) if cap_quotas else 3)
    reits_or_utilities = [t for t in eligible if is_utility_reit(companies[t])]
    reserve('REITs or utilities', reits_or_utilities, min(2, len(reits_or_utilities)) if cap_quotas else 2)
    variable_week = [t for t in eligible if t in IRREGULAR]
    reserve('variable-week companies', variable_week,
            min(2, len(variable_week)) if variable_week_cap or cap_quotas else 2)
    basis_companies = [t for t in eligible if any(mutate_single_basis(s, docs[s['doc_id']], companies)
                                                for s in historical[t])]
    # Basis needs explicit printed labels, so reserve its feasible source stratum.
    if basis_companies:
        reserve('basis-supporting companies', basis_companies, min(5, len(basis_companies)))
    with mutation_environment(companies, seed):
        period_companies = [t for t in eligible if any(
            mutation_sentence_safe(s) and (m := base.mutate_period(s, docs[s['doc_id']], companies))
            and m.screen != 'assumed' for s in historical[t])]
    if period_companies:
        reserve('period-supporting companies', period_companies, min(5, len(period_companies)))
    guidance_tickers = {s['ticker'] for s in sentences if s['kind'] == 'forward_looking'}
    guidance_companies = [t for t in eligible if t in guidance_tickers]
    if guidance_companies:
        reserve('guidance-supporting companies', guidance_companies, min(5, len(guidance_companies)))
    margin_directions = [t for t in eligible if any(
        (m := mutate_direction(s, docs[s['doc_id']], companies)) and m.detail['metric'].endswith('margin')
        for s in historical[t])]
    if margin_directions:
        reserve('screened margin direction', margin_directions, 1)
    for feature in FEATURE_PRIORITIES:
        candidates = [t for t in eligible if feature in pools[t]]
        if candidates:
            reserve(feature, candidates, 1)
    strata = defaultdict(list)
    for ticker in eligible:
        sector = SECTOR_BY_INDUSTRY[companies[ticker]['sub_industry']]
        strata[sector].append(ticker)
    for sector in sorted(strata):
        rng.shuffle(strata[sector])
    # Fill the least-represented broad sector first; ties use sorted GICS names.
    while len(chosen) < count:
        counts = Counter(SECTOR_BY_INDUSTRY[companies[t]['sub_industry']] for t in chosen)
        available = [sector for sector in strata if any(t not in chosen for t in strata[sector])]
        if not available:
            raise ValueError('Not enough eligible companies')
        sector = min(available, key=lambda k: (counts[k], k))
        chosen.append(next(t for t in strata[sector] if t not in chosen))
    if len(chosen) > count:
        raise ValueError('Coverage reservations exceed company target')
    return sorted(chosen), {'reservations': reservations, 'eligible_company_count': len(eligible),
                            'eligible_by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[t]['sub_industry']]
                                                                     for t in eligible).items()))}


def build_items(companies, docs, sentences, spot=None):
    """Pure source-to-items transform. No extraction, store facts, or verifier calls."""
    spot = spot or SETS['v1']
    selected, selection = select_companies(companies, docs, sentences, seed=spot.seed, excluded=spot.excluded,
                                           variable_week_cap=spot.variable_week_cap, cap_quotas=spot.cap_quotas)
    companies = {t: companies[t] for t in selected}
    docs = {k: d for k, d in docs.items() if d['ticker'] in companies}
    sentences = sorted((s for s in sentences if s['ticker'] in companies), key=lambda s: s['sentence_id'])
    candidates = mutation_candidates(sentences, docs, companies, seed=spot.seed)
    rng = random.Random(spot.seed)
    for mutator in MUTATORS:
        rng.shuffle(candidates[mutator])
    selected_sentences, mutants, used = {}, [], Counter()
    # Scarce mutation types get first choice. A source can produce at most two mutants.
    # A capped set takes fewer than five only when its candidates run out, and records it.
    shortfalls = {}
    for mutator in sorted(MUTATORS, key=lambda m: (len(candidates[m]), m)):
        taken_companies = set()
        for _ in range(5):
            options = [(s, m) for s, m in candidates[mutator] if used[s['sentence_id']] < 2
                       and not any(old_s['sentence_id'] == s['sentence_id'] and old_m.mutator == mutator
                                   for old_s, old_m in mutants)]
            if not options and spot.cap_quotas:
                shortfalls[mutator] = {'quota': 5, 'taken': _, 'candidates': len(candidates[mutator])}
                break
            if not options:
                raise ValueError(f'Need five source-screened {mutator} mutations; found {len(candidates[mutator])}')
            represented = {s['ticker'] for s in selected_sentences.values()}
            s, m = min(options, key=lambda pair: (
                mutator == 'direction' and _ == 0 and not pair[1].detail['metric'].endswith('margin'),
                pair[0]['ticker'] in taken_companies,
                pair[0]['sentence_id'] not in selected_sentences and pair[0]['ticker'] in represented,
                pair[0]['sentence_id'] not in selected_sentences,
            ))
            selected_sentences[s['sentence_id']] = s
            mutants.append((s, m))
            used[s['sentence_id']] += 1
            taken_companies.add(s['ticker'])
    for ticker in selected:
        if any(s['ticker'] == ticker for s in selected_sentences.values()):
            continue
        options = [s for s in sentences if s['ticker'] == ticker and s['kind'] != 'forward_looking']
        rng.shuffle(options)
        covered = {f for s in selected_sentences.values() for f in features(s, companies[s['ticker']], docs[s['doc_id']])}
        chosen = min(options, key=lambda s: (
            -len(set(features(s, companies[ticker], docs[s['doc_id']])) & (set(FEATURE_PRIORITIES) - covered)),
            -sum(n['screen'] == 'ok' for n in s['numbers']),
        ))
        selected_sentences[chosen['sentence_id']] = chosen
    # Cover available newer features even if mutation carriers did not mention them.
    for feature in FEATURE_PRIORITIES:
        covered = {f for s in selected_sentences.values() for f in features(s, companies[s['ticker']], docs[s['doc_id']])}
        options = [s for s in sentences if s['kind'] != 'forward_looking'
                   and feature in features(s, companies[s['ticker']], docs[s['doc_id']])]
        if feature not in covered and options:
            chosen = rng.choice(options)
            selected_sentences[chosen['sentence_id']] = chosen
    controls = [s for s in sentences if s['kind'] == 'forward_looking']
    rng.shuffle(controls)
    chosen_controls = []
    for s in controls:
        if s['ticker'] not in {r['ticker'] for r in chosen_controls}:
            chosen_controls.append(s)
        if len(chosen_controls) == 5:
            break
    if len(chosen_controls) != 5:
        raise ValueError('Need five guidance controls at distinct sampled companies')
    originals = sorted(selected_sentences.values(), key=lambda s: s['sentence_id'])
    items = [make_item(s, docs[s['doc_id']], companies[s['ticker']], version=spot.version) for s in originals]
    items += [make_item(s, docs[s['doc_id']], companies[s['ticker']], mutation=m, version=spot.version)
              for s, m in sorted(mutants, key=lambda pair: (pair[1].mutator, pair[0]['sentence_id']))]
    items += [make_item(s, docs[s['doc_id']], companies[s['ticker']], version=spot.version)
              for s in sorted(chosen_controls, key=lambda s: s['sentence_id'])]
    if not 60 <= len(items) <= 80:
        raise ValueError(f'Outside preregistered size range: {len(items)}')
    if len({it['item_hash'] for it in items}) != len(items):
        raise ValueError('Duplicate item inputs')
    selection.update(selected_companies=selected, mutation_candidate_counts={m: len(candidates[m]) for m in MUTATORS},
                     selected_sentence_ids=sorted([*selected_sentences, *(s['sentence_id'] for s in chosen_controls)]))
    if shortfalls:
        selection['mutant_shortfalls'] = shortfalls
    return items, selection


def full_pool(sentences, spot):
    """Frame sentences a full set may use: excluded companies and every earlier set's sentences removed.

    Earlier sentences are matched by source record SHA-256 from each frozen manifest; a frame
    sentence with identical text is removed too. Read-only.
    """
    by_hash = {digest(canonical(s)): s for s in sentences}
    used, removed = {}, {}
    for name in spot.exclude_sentences_of:
        earlier = SETS[name]
        frozen = json.loads((ROOT / 'eval' / earlier.manifest).read_bytes())
        if frozen['version'] != earlier.version or frozen['corpus_sha256'] != digest((ROOT / 'eval' / earlier.corpus).read_bytes()):
            raise ValueError(f'Frozen {earlier.version} files changed')
        rows = [r for r in frozen['source_records'] if r['kind'] == 'sentence']
        missing = [r['id'] for r in rows if r['sha256'] not in by_hash]
        if missing:
            raise ValueError(f'{earlier.version} sentences missing from the frame: {missing[:3]}')
        removed[earlier.version] = sorted(r['id'] for r in rows)
        used.update((r['sha256'], r['id']) for r in rows)
    texts = {by_hash[h]['text'] for h in used}
    pool, same_text = [], []
    for s in sorted(sentences, key=lambda s: s['sentence_id']):
        if s['ticker'] in spot.excluded or digest(canonical(s)) in used:
            continue
        if s['text'] in texts:
            same_text.append(s['sentence_id'])
            continue
        pool.append(s)
    return pool, {**removed, 'identical_text': same_text}


def full_exclusion_sources(spot):
    """Check the full set's development groups against the frozen derivations and earlier sets (read-only)."""
    groups = {key: companies for key, companies, _ in spot.development_groups}
    references = []
    for name in spot.exclude_sentences_of:
        earlier = SETS[name]
        manifest_bytes = (ROOT / 'eval' / earlier.manifest).read_bytes()
        frozen = json.loads(manifest_bytes)
        references.append({'version': earlier.version, 'seed': frozen['seed'],
                           'manifest': f'eval/{earlier.manifest}', 'manifest_sha256': digest(manifest_bytes),
                           'corpus': f'eval/{earlier.corpus}', 'corpus_sha256': frozen['corpus_sha256']})
    records = {}
    for owner, prefix in (('v3', 'spot500_v3_'), ('full', '')):
        path, expected = SETS[owner].derivation
        raw = (ROOT / 'eval' / path).read_bytes()
        if digest(raw) != expected:
            raise ValueError(f'Frozen exclusion derivation changed: eval/{path}')
        record = json.loads(raw)
        for key, field in ((f'{prefix}fix_test_companies' if prefix else 'verifier_fix_test_companies', 'verifier_fix_tests'),
                           (f'{prefix}fix_code_companies' if prefix else 'verifier_fix_code_companies', 'verifier_fix_code')):
            if frozenset(record[field]['companies']) != groups[key]:
                raise ValueError(f'{key} differ from eval/{path}')
        if owner == 'full' and frozenset(record['reviewer_probes']['companies']) != groups['reviewer_probe_companies']:
            raise ValueError(f'reviewer_probe_companies differ from eval/{path}')
        records[owner] = {'file': f'eval/{path}', 'sha256': expected, **({'record': record} if owner == 'full' else {})}
    return references, records


def earlier_companies(spot):
    """Every company an earlier set sampled (read-only): company_seen versus company_new in full2."""
    out = set()
    for name in spot.exclude_sentences_of:
        out |= set(json.loads((ROOT / 'eval' / SETS[name].manifest).read_bytes())['selection']['selected_companies'])
    return frozenset(out)


def history_tag(spot, ticker, seen=None):
    """One company-history tag: in development first; then, with earlier sets known, new or seen."""
    if ticker in spot.in_development:
        return 'company_in_development'
    if seen is None:
        return 'company_fresh'
    return 'company_seen' if ticker in seen else 'company_new'


def build_full_items(companies, docs, sentences, spot):
    """Every eligible company at FULL_SCALE. Pure source-to-items transform, like build_items.

    Order: guidance controls at distinct companies; every candidate of a mutator that has no more
    candidates than its quota (basis); feature coverage (scarcest feature first), as v1 reserves
    feature carriers before its mutants; the other mutators (scarce first, with v1's preferences,
    which favour sentences already chosen); one original per company; and a seeded round-robin
    fill to the total.
    """
    scale, rng = FULL_SCALE, random.Random(spot.seed)
    pool, removed = full_pool(sentences, spot)
    historical, forward = defaultdict(list), defaultdict(list)
    for s in pool:
        (forward if s['kind'] == 'forward_looking' else historical)[s['ticker']].append(s)
    eligible = sorted(historical)
    companies = {t: companies[t] for t in eligible}
    docs = {k: d for k, d in docs.items() if d['ticker'] in companies}
    feats = {s['sentence_id']: features(s, companies[s['ticker']], docs[s['doc_id']]) for s in pool if s['ticker'] in companies}
    candidates = mutation_candidates([s for t in eligible for s in historical[t]], docs, companies, seed=spot.seed)
    for mutator in MUTATORS:
        rng.shuffle(candidates[mutator])
    chosen, per_company, mutants, mutated = {}, Counter(), [], defaultdict(set)

    def room(s):
        return s['sentence_id'] in chosen or per_company[s['ticker']] < scale['per_company']

    def add(s):
        if s['sentence_id'] not in chosen:
            chosen[s['sentence_id']] = s
            per_company[s['ticker']] += 1

    controls = [s for t in eligible for s in forward.get(t, [])]
    rng.shuffle(controls)
    chosen_controls, control_companies = [], set()
    for s in controls:
        if s['ticker'] not in control_companies and len(chosen_controls) < scale['controls']:
            chosen_controls.append(s)
            control_companies.add(s['ticker'])

    def coverage():
        counts = Counter()
        for sid in chosen:
            counts.update({f: 1 + len(mutated[sid]) for f in feats[sid]})
        for s in chosen_controls:
            counts.update(feats[s['sentence_id']])
        return counts

    frame_counts = Counter(f for s in pool if s['ticker'] in companies for f in feats[s['sentence_id']])

    def cover():
        for feature in sorted(frame_counts, key=lambda f: (frame_counts[f], f)):
            while coverage()[feature] < scale['feature_target']:
                options = [s for t in eligible for s in historical[t] if s['sentence_id'] not in chosen
                           and feature in feats[s['sentence_id']] and room(s)]
                if not options:
                    break
                add(rng.choice(options))

    shortfalls = {}

    def take_mutants(mutator):
        taken = set()
        for pick in range(scale['mutants']):
            options = [(s, m) for s, m in candidates[mutator] if len(mutated[s['sentence_id']]) < 2
                       and mutator not in mutated[s['sentence_id']] and room(s)]
            if not options:
                shortfalls[mutator] = {'quota': scale['mutants'], 'taken': pick, 'candidates': len(candidates[mutator])}
                return
            represented = set(per_company)
            s, m = min(options, key=lambda pair: (
                mutator == 'direction' and pick == 0 and not pair[1].detail['metric'].endswith('margin'),
                pair[0]['ticker'] in taken,
                pair[0]['sentence_id'] not in chosen and pair[0]['ticker'] in represented,
                pair[0]['sentence_id'] not in chosen,
            ))
            add(s)
            mutants.append((s, m))
            mutated[s['sentence_id']].add(mutator)
            taken.add(s['ticker'])

    # Scarce mutators first, as in v1. One that can only take every candidate (basis) goes
    # before feature coverage, so the per-company cap never blocks one of its carriers.
    by_scarcity = sorted(MUTATORS, key=lambda m: (len(candidates[m]), m))
    take_all = [m for m in by_scarcity if len(candidates[m]) <= scale['mutants']]
    for mutator in take_all:
        take_mutants(mutator)
    cover()
    for mutator in by_scarcity:
        if mutator not in take_all:
            take_mutants(mutator)

    def pick_original(options):
        rng.shuffle(options)
        counts = coverage()
        return min(options, key=lambda s: (-sum(counts[f] < scale['feature_target'] for f in feats[s['sentence_id']]),
                                           -sum(n['screen'] == 'ok' for n in s['numbers'])))

    for ticker in eligible:
        if not per_company[ticker]:
            add(pick_original(list(historical[ticker])))
    order = list(eligible)
    rng.shuffle(order)
    for round_size in range(2, scale['per_company'] + 1):
        for ticker in order:
            if len(chosen) + len(mutants) + len(chosen_controls) >= scale['total']:
                break
            options = [s for s in historical[ticker] if s['sentence_id'] not in chosen]
            if per_company[ticker] < round_size and options:
                add(pick_original(options))
    counts = coverage()
    feature_coverage = {}
    for feature in sorted(frame_counts):
        carriers = [s for s in pool if s['ticker'] in companies and feature in feats[s['sentence_id']]]
        entry = {'items': counts[feature], 'frame_sentences': len(carriers),
                 'frame_companies': len({s['ticker'] for s in carriers})}
        if counts[feature] < scale['feature_target']:
            left = [s for s in carriers if s['kind'] != 'forward_looking' and s['sentence_id'] not in chosen]
            entry['short_because'] = ('every frame sentence carrying it is already used' if not left else
                                      f"the {scale['per_company']}-per-company cap leaves {len(left)} sentences out")
        feature_coverage[feature] = entry
    originals = sorted(chosen.values(), key=lambda s: s['sentence_id'])
    items = [make_item(s, docs[s['doc_id']], companies[s['ticker']], version=spot.version) for s in originals]
    items += [make_item(s, docs[s['doc_id']], companies[s['ticker']], mutation=m, version=spot.version)
              for s, m in sorted(mutants, key=lambda pair: (pair[1].mutator, pair[0]['sentence_id']))]
    items += [make_item(s, docs[s['doc_id']], companies[s['ticker']], version=spot.version)
              for s in sorted(chosen_controls, key=lambda s: s['sentence_id'])]
    # Company history, a tag only: selection above never reads it.
    seen = earlier_companies(spot) if spot.history_seen else None
    for item in items:
        item['features'] = sorted([*item['features'], history_tag(spot, item['ticker'], seen)])
    if len({it['item_hash'] for it in items}) != len(items):
        raise ValueError('Duplicate item inputs')
    selection = {
        'scale': scale, 'eligible_company_count': len(eligible),
        'in_development_companies': sorted(set(eligible) & spot.in_development),
        'eligible_by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[t]['sub_industry']] for t in eligible).items())),
        'selected_companies': eligible, 'removed_sentences': removed,
        'mutation_candidate_counts': {m: len(candidates[m]) for m in MUTATORS},
        'mutant_counts': dict(sorted(Counter(m.mutator for _, m in mutants).items())),
        'guidance_controls': len(chosen_controls), 'originals_per_company': dict(sorted(Counter(per_company.values()).items())),
        'feature_coverage': feature_coverage,
        'selected_sentence_ids': sorted([*chosen, *(s['sentence_id'] for s in chosen_controls)])}
    if shortfalls:
        selection['mutant_shortfalls'] = shortfalls
    if seen is not None:
        selection['company_history'] = {tag: sorted(t for t in eligible if history_tag(spot, t, seen) == tag)
                                        for tag in ('company_in_development', 'company_new', 'company_seen')}
    return items, selection


def encode_jsonl(items):
    return b''.join(canonical(item) + b'\n' for item in items)


def exhibit_header_label(raw):
    """The release label in an exhibit's opening text, read as is_exhibit_991 reads it: '99.1', '99.01', '99', ..."""
    header = base.normalize_ws(html_module.unescape(re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))))[:4000]
    match = EXHIBIT_LABEL.search(header)
    return None if match is None else '99' if match.group(1) is None else f'99.{match.group(1)}'


def fallback_exhibit_rule(data_root, strict):
    """strict (is_exhibit_991), or, for an 8-K none of whose archived exhibits passes it, a release headed
    EX-99.01, or headed plain EX-99 when that is the 8-K's single EX-99-type exhibit."""
    data_root = Path(data_root)
    index = json.loads((data_root / 'archive/exhibits/index.json').read_text())
    by_accession = defaultdict(list)
    for url, row in sorted(index.items()):
        by_accession[row['accession']].append((url, row))
    read = {}

    def exhibits(accession):
        if accession not in read:
            rows = []
            for url, row in by_accession[accession]:
                raw = gzip.decompress((data_root / 'archive/exhibits' / (row['key'] + '.gz')).read_bytes())
                rows.append((strict(url, raw), exhibit_header_label(raw)))
            read[accession] = rows
        return read[accession]

    def rule(url, raw):
        if strict(url, raw):
            return True
        rows = exhibits(index[url]['accession'])
        if any(passed for passed, _ in rows):
            return False
        label = exhibit_header_label(raw)
        return label == '99.01' or (label == '99' and sum(passed or found is not None for passed, found in rows) == 1)
    return rule


def load_full2_frame(data_root, *, progress=True):
    """full's frame, unchanged, plus the companies the fallback exhibit rule gives a release/10-Q pair."""
    from eval import spot500_sources as sources
    data_root = Path(data_root)
    with patch.object(sources, 'EXCLUDED', frozenset()):
        companies, docs, sentences, provenance = sources.load_sources(data_root, progress=progress)
    strict_frame = digest(canonical([companies, docs, sentences]))
    universe = {row['ticker'] for row in json.loads(sources.UNIVERSE.read_text())['universe']}
    strict = sources.is_exhibit_991
    with patch.multiple(sources, EXCLUDED=frozenset(), is_exhibit_991=fallback_exhibit_rule(data_root, strict)):
        added = sources.load_sources(data_root, tickers=universe - set(companies), progress=False)
    index = json.loads((data_root / 'archive/exhibits/index.json').read_text())
    labels = {}
    for doc_id, doc in sorted(added[1].items()):
        raw = gzip.decompress((data_root / 'archive/exhibits' / (index[doc['url']]['key'] + '.gz')).read_bytes())
        doc['exhibit'] = 'EX-99.1' if strict(doc['url'], raw) else f"EX-{exhibit_header_label(raw)}"
        labels[doc_id] = doc['exhibit']
    fallback = {'rule': fallback_exhibit_rule.__doc__.strip().replace('\n    ', ' '),
                'strict_frame_sha256': strict_frame, 'companies': sorted(added[0]), 'documents': labels}
    return ({**companies, **added[0]}, {**docs, **added[1]}, [*sentences, *added[2]],
            {**provenance, 'records': [*provenance['records'], *added[3]['records']],
             'screen_counts': {**provenance['screen_counts'], **added[3]['screen_counts']},
             'exhibit_fallback': fallback})


def build(data_root, spot=None):
    from eval import spot500_sources as sources
    spot = spot or SETS['v1']
    if spot.exhibit_fallback:
        return build_from_sources(*load_full2_frame(data_root), spot=spot)
    # A set that keeps the original development companies screens them too; spot500_sources.py
    # itself is unchanged, so every earlier set's frame and builder hashes stay the same.
    with patch.object(sources, 'EXCLUDED', sources.EXCLUDED if spot.exclude_original else frozenset()):
        frame = sources.load_sources(data_root, progress=True)
    return build_from_sources(*frame, spot=spot)


def builder_hashes(spot):
    frozen = dict(spot.frozen_builder_hashes)
    return {p: frozen.get(p) or digest((ROOT / p).read_bytes()) for p in BUILDER_FILES}


def reference_set(spot):
    """Check a later set's extra exclusions against the frozen earlier set (read-only)."""
    earlier = SETS[spot.extra_excluded_from]
    manifest_bytes = (ROOT / 'eval' / earlier.manifest).read_bytes()
    corpus_bytes = (ROOT / 'eval' / earlier.corpus).read_bytes()
    frozen = json.loads(manifest_bytes)
    if frozen['version'] != earlier.version or frozen['corpus_sha256'] != digest(corpus_bytes):
        raise ValueError(f'Frozen {earlier.version} files changed')
    if frozenset(frozen['selection']['selected_companies']) != spot.extra_excluded:
        raise ValueError(f'Extra exclusions differ from the frozen {earlier.version} companies')
    return {'version': earlier.version, 'seed': frozen['seed'],
            'manifest': f'eval/{earlier.manifest}', 'manifest_sha256': digest(manifest_bytes),
            'corpus': f'eval/{earlier.corpus}', 'corpus_sha256': frozen['corpus_sha256']}


def exclusion_sources(spot):
    """Check v3-style exclusion groups against the frozen sets and derivation (read-only)."""
    groups = {key: companies for key, companies, _ in spot.exclusion_groups}
    references = []
    for name in spot.previous_sets:
        earlier = SETS[name]
        manifest_bytes = (ROOT / 'eval' / earlier.manifest).read_bytes()
        frozen = json.loads(manifest_bytes)
        if (frozen['version'] != earlier.version
                or frozen['corpus_sha256'] != digest((ROOT / 'eval' / earlier.corpus).read_bytes())):
            raise ValueError(f'Frozen {earlier.version} files changed')
        if frozenset(frozen['selection']['selected_companies']) != groups[f'{earlier.version}_companies']:
            raise ValueError(f'Exclusions differ from the frozen {earlier.version} companies')
        references.append({'version': earlier.version, 'seed': frozen['seed'],
                           'manifest': f'eval/{earlier.manifest}', 'manifest_sha256': digest(manifest_bytes),
                           'corpus': f'eval/{earlier.corpus}', 'corpus_sha256': frozen['corpus_sha256']})
    path, expected = spot.derivation
    raw = (ROOT / 'eval' / path).read_bytes()
    if digest(raw) != expected:
        raise ValueError(f'Frozen exclusion derivation changed: eval/{path}')
    derivation = json.loads(raw)
    for key, field in (('verifier_fix_test_companies', 'verifier_fix_tests'),
                       ('verifier_fix_code_companies', 'verifier_fix_code'),
                       ('reviewer_probe_companies', 'reviewer_probes')):
        if frozenset(derivation[field]['companies']) != groups[key]:
            raise ValueError(f'{key} differ from eval/{path}')
    return references, {'file': f'eval/{path}', 'sha256': expected, 'record': derivation}


def build_full_from_sources(companies, docs, sentences, provenance, spot):
    from eval.eval import DEFAULT_EST_COST_PER_ITEM
    references, derivations = full_exclusion_sources(spot)
    # The frame is v1's plus the original development companies, screened the same way.
    without = digest(canonical([{t: c for t, c in companies.items() if t not in EXCLUDED},
                                {k: d for k, d in docs.items() if d['ticker'] not in EXCLUDED},
                                [s for s in sentences if s['ticker'] not in EXCLUDED]]))
    if without != json.loads((ROOT / 'eval' / SETS['v1'].manifest).read_bytes())['source_pool_sha256']:
        raise ValueError("Without the original companies, the frame differs from spot500_v1's")
    items, selection = build_full_items(companies, docs, sentences, spot)
    selected = set(selection['selected_companies'])
    historical = {s['ticker'] for s in sentences if s['kind'] != 'forward_looking'}
    absent = {t: 'not in the frame: no stored release and 10-Q pair' if t not in companies
              else 'no historical sentence passes the frame screening' if t not in historical
              else 'every historical sentence was used by an earlier set' for t in sorted(spot.in_development - selected)}
    chosen_sids = set(selection['selected_sentence_ids'])
    raw = encode_jsonl(items)
    scale, groups = FULL_SCALE, spot.development_groups
    removed = selection['removed_sentences']
    history = {}
    for tag in ('company_in_development', 'company_fresh'):
        tagged = [it for it in items if tag in it['features']]
        history[tag] = {'companies': len({it['ticker'] for it in tagged}), 'items': len(tagged),
                        'originals': sum(it['mutator'] == 'none' for it in tagged),
                        'mutants': sum(it['mutator'] in MUTATORS for it in tagged),
                        'guidance_controls': sum(it['mutator'] == 'forward_looking_control' for it in tagged),
                        'gold_claims': sum(len(it['gold_claims']) for it in tagged),
                        'by_mutator': dict(sorted(Counter(it['mutator'] for it in tagged).items()))}
    counts = {'companies': len(selected), 'items': len(items), 'gold_claims': sum(len(it['gold_claims']) for it in items),
              'by_company': dict(sorted(Counter(it['ticker'] for it in items).items())),
              'by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[t]['sub_industry']] for t in selected).items())),
              'items_by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[it['ticker']]['sub_industry']]
                                                     for it in items).items())),
              'by_mutator': dict(sorted(Counter(it['mutator'] for it in items).items())),
              'by_feature': dict(sorted(Counter(f for it in items for f in it['features']).items())),
              'by_company_history': history}
    parts = [f"the {len(companies_)} {description}" for _, companies_, description in groups[1:]]
    changes = [f"seed {SEED} -> {spot.seed}",
               f"every company in the frame with an unused historical sentence instead of a 30-company sample, with up "
               f"to {scale['per_company']} original sentences each, chosen by seed; no company is excluded",
               f"every item tagged company_in_development (the original {len(EXCLUDED)} plus {', '.join(parts[:-1])} "
               f"and {parts[-1]}; {len(spot.in_development)} distinct companies) or company_fresh",
               "sentences used by " + ", ".join(f"{v} ({len(ids)})" for v, ids in removed.items() if v != 'identical_text')
               + f" removed by source record SHA-256, and {len(removed['identical_text'])} frame sentences with identical "
               "text; earlier companies stay with their unused sentences",
               f"{scale['mutants']} mutants per mutator instead of 5, {scale['controls']} guidance controls from different "
               f"companies instead of 5, at least {scale['feature_target']} items per source feature where the frame "
               f"allows, and {scale['total']} items in all"]
    for mutator, short in sorted(selection.get('mutant_shortfalls', {}).items()):
        changes.append(f"{mutator} mutants lowered from {short['quota']} to {short['taken']}: the frame has "
                       f"{short['candidates']} {mutator} candidates without the earlier sets' sentences")
    for feature, entry in selection['feature_coverage'].items():
        if 'short_because' in entry:
            changes.append(f"{feature}: {entry['items']} items, below {scale['feature_target']}: {entry['short_because']} "
                           f"({entry['frame_sentences']} frame sentences at {entry['frame_companies']} companies)")
    if len(items) != scale['total']:
        changes.append(f"{len(items)} items instead of {scale['total']}: the frame ran out of unused sentences")
    manifest = {
        'version': spot.version, 'seed': spot.seed, 'commit': spot.commit, 'commit_role': spot.commit_role,
        'procedure': {
            'selection': f"Python Random({spot.seed}); every company in the frame with an unused historical sentence, "
                         f"each with at least one and at most "
                         f"{scale['per_company']} original sentences; in order: {scale['controls']} guidance controls "
                         f"at distinct companies, every candidate of a mutator with no more candidates than its quota, "
                         f"feature coverage to {scale['feature_target']} items (scarcest feature first), the other "
                         f"mutators (scarce first, v1's preferences), one original per company, and a seeded "
                         f"round-robin fill to {scale['total']} items; source-only mutation feasibility",
            'quarters': 'latest two stored release/10-Q pairs; no 10-K-only Q4; numeric companyfacts screening',
            'items': f"{scale['mutants']} per mutator where candidates allow; at most two mutants per original; v1's "
                     f"gold rules, mutators and feasibility checks",
            'outputs_used_for_selection': False, 'human_review': False, 'changes_from_spot500_v1': changes},
        'superseded': FULL_SUPERSEDED,
        'excluded_tickers': sorted(spot.excluded),
        'exclusions': {'companies': sorted(spot.excluded),
                       'note': 'none: every company in the frame with an unused historical sentence is eligible'},
        'sentence_exclusions': {**removed, 'method': 'source record SHA-256 from each earlier frozen manifest; a frame '
                                                     'sentence with identical text is removed as well'},
        'company_history': {
            'tags': {'company_in_development': 'the original development companies plus every company named in '
                                               'verifier-fix tests, fix code or probes',
                     'company_fresh': 'every other company'},
            'in_development': sorted(spot.in_development), **{key: sorted(c) for key, c, _ in groups},
            'in_development_absent_from_set': absent,
            'method': 'the mechanical derivation of v3, re-run over 50a4eac..8d69d3c; selection never reads the tag'},
        'company_history_derivation': derivations['full'], 'spot500_v3_derivation': derivations['v3'],
        'reference_sets': references,
        'company_list': [{'ticker': t, 'name': companies[t]['name'], 'sector': SECTOR_BY_INDUSTRY[companies[t]['sub_industry']],
                          'sub_industry': companies[t]['sub_industry']} for t in sorted(selected)],
        'selection': selection, 'counts': counts, 'corpus_sha256': digest(raw),
        'items': [{'item_id': it['item_id'], 'sha256': digest(canonical(it))} for it in items],
        'source_records': ([{'kind': 'company', 'id': t, 'sha256': digest(canonical(companies[t]))} for t in sorted(selected)]
                           + [{'kind': 'document', 'id': k, 'sha256': digest(canonical(d))}
                              for k, d in sorted(docs.items()) if d['ticker'] in selected]
                           + [{'kind': 'sentence', 'id': s['sentence_id'], 'sha256': digest(canonical(s))}
                              for s in sentences if s['sentence_id'] in chosen_sids]),
        'source_pool_sha256': digest(canonical([companies, docs, sentences])),
        'frame': {'source_pool_without_original_sha256': without, 'equals': 'spot500_v1 source_pool_sha256',
                  'original_companies_added': sorted(EXCLUDED & set(companies)),
                  'original_companies_absent': sorted(EXCLUDED - set(companies))},
        'sources': {**provenance, 'records': [r for r in provenance['records'] if r['ticker'] in selected],
                    'screen_counts': {k: v for k, v in provenance['screen_counts'].items() if docs[k]['ticker'] in selected}},
        'builder_hashes': builder_hashes(spot),
        'cost_estimate': {'per_item_usd': DEFAULT_EST_COST_PER_ITEM, 'total_usd': round(len(items) * DEFAULT_EST_COST_PER_ITEM, 6),
                          'source': 'eval/eval.py DEFAULT_EST_COST_PER_ITEM', 'estimate_limit_usd': 5.5,
                          'live_budget_usd': 6.0}}
    return raw, json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode() + b'\n'


def full2_history_sources(spot):
    """Check full2's earlier sets, carried development list and round-8 derivation (read-only)."""
    if not spot.derivation:
        raise ValueError('full2 waits for the round-8 merge: its company_in_development derivation is pending')
    groups = {key: companies for key, companies, _ in spot.development_groups}
    references = []
    for name in spot.exclude_sentences_of:
        earlier = SETS[name]
        manifest_bytes = (ROOT / 'eval' / earlier.manifest).read_bytes()
        frozen = json.loads(manifest_bytes)
        if (frozen['version'] != earlier.version
                or frozen['corpus_sha256'] != digest((ROOT / 'eval' / earlier.corpus).read_bytes())):
            raise ValueError(f'Frozen {earlier.version} files changed')
        references.append({'version': earlier.version, 'seed': frozen['seed'],
                           'manifest': f'eval/{earlier.manifest}', 'manifest_sha256': digest(manifest_bytes),
                           'corpus': f'eval/{earlier.corpus}', 'corpus_sha256': frozen['corpus_sha256']})
    full = json.loads((ROOT / 'eval' / SETS['full'].manifest).read_bytes())
    if frozenset(full['company_history']['in_development']) != groups['spot500_full_in_development']:
        raise ValueError("The carried development list differs from the full manifest's")
    path, expected = spot.derivation
    raw = (ROOT / 'eval' / path).read_bytes()
    if digest(raw) != expected:
        raise ValueError(f'Frozen company-history derivation changed: eval/{path}')
    record = json.loads(raw)
    for key, field in (('round8_fix_test_companies', 'verifier_fix_tests'), ('round8_fix_code_companies', 'verifier_fix_code')):
        if frozenset(record[field]['companies']) != groups[key]:
            raise ValueError(f'{key} differ from eval/{path}')
    return references, {'file': f'eval/{path}', 'sha256': expected, 'record': record}


def build_full2_from_sources(companies, docs, sentences, provenance, spot):
    from eval.eval import DEFAULT_EST_COST_PER_ITEM
    references, derivation = full2_history_sources(spot)
    fallback = provenance.get('exhibit_fallback')
    if fallback is None:
        raise ValueError('full2 needs the frame from load_full2_frame')
    full_manifest = json.loads((ROOT / 'eval' / SETS['full'].manifest).read_bytes())
    if fallback['strict_frame_sha256'] != full_manifest['source_pool_sha256']:
        raise ValueError("Without the exhibit fallback, the frame differs from the full set's")
    provenance = {k: v for k, v in provenance.items() if k != 'exhibit_fallback'}
    items, selection = build_full_items(companies, docs, sentences, spot)
    selected = set(selection['selected_companies'])
    chosen_sids = set(selection['selected_sentence_ids'])
    raw = encode_jsonl(items)
    scale, groups, removed = FULL_SCALE, spot.development_groups, selection['removed_sentences']
    tags = ('company_in_development', 'company_new', 'company_seen')
    history = {}
    for tag in tags:
        tagged = [it for it in items if tag in it['features']]
        history[tag] = {'companies': len({it['ticker'] for it in tagged}), 'items': len(tagged),
                        'originals': sum(it['mutator'] == 'none' for it in tagged),
                        'mutants': sum(it['mutator'] in MUTATORS for it in tagged),
                        'guidance_controls': sum(it['mutator'] == 'forward_looking_control' for it in tagged),
                        'gold_claims': sum(len(it['gold_claims']) for it in tagged),
                        'by_mutator': dict(sorted(Counter(it['mutator'] for it in tagged).items()))}
    added = defaultdict(set)
    for doc_id, label in fallback['documents'].items():
        added[docs[doc_id]['ticker']].add(label)
    added = {t: sorted(labels) for t, labels in sorted(added.items())}
    historical = {s['ticker'] for s in sentences if s['kind'] != 'forward_looking'}
    absent = {t: 'not in the frame: no stored release and 10-Q pair' if t not in companies
              else 'no historical sentence passes the frame screening' if t not in historical
              else 'every historical sentence was used by an earlier set' for t in sorted(spot.in_development - selected)}
    counts = {'companies': len(selected), 'items': len(items), 'gold_claims': sum(len(it['gold_claims']) for it in items),
              'by_company': dict(sorted(Counter(it['ticker'] for it in items).items())),
              'by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[t]['sub_industry']] for t in selected).items())),
              'items_by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[it['ticker']]['sub_industry']]
                                                     for it in items).items())),
              'by_mutator': dict(sorted(Counter(it['mutator'] for it in items).items())),
              'by_feature': dict(sorted(Counter(f for it in items for f in it['features']).items())),
              'by_company_history': history}
    by_label = Counter(label for labels in added.values() for label in labels)
    changes = [f"seed {SEED} -> {spot.seed}",
               f"the full set's frame, unchanged, plus {len(added)} companies whose earnings 8-K has no exhibit read as "
               f"EX-99.1 but a release headed EX-99.01 ({by_label['EX-99.01']}) or a single plain EX-99 "
               f"({by_label['EX-99']}); no earlier company's pairs change",
               f"every company in the frame with an unused historical sentence, with up to {scale['per_company']} "
               f"original sentences each, chosen by seed; no company is excluded",
               "sentences used by " + ", ".join(f"{v} ({len(ids)})" for v, ids in removed.items() if v != 'identical_text')
               + f" removed by source record SHA-256, and {len(removed['identical_text'])} frame sentences with identical "
               "text; earlier companies stay with their unused sentences",
               f"every item tagged company_in_development ({len(spot.in_development)} companies: the full set's plus "
               f"those the round-8 fixes name), company_new (in no earlier set) or company_seen",
               f"{scale['mutants']} mutants per mutator, {scale['controls']} guidance controls from different companies, "
               f"at least {scale['feature_target']} items per source feature where the frame allows, and "
               f"{scale['total']} items in all"]
    for mutator, short in sorted(selection.get('mutant_shortfalls', {}).items()):
        blocked = short['candidates'] - short['taken']
        changes.append(f"{mutator} mutants lowered from {short['quota']} to {short['taken']}: the frame has "
                       f"{short['candidates']} {mutator} candidates without the earlier sets' sentences"
                       + (f", {blocked} of them blocked by the {scale['per_company']}-per-company cap or the "
                          f"two-mutant limit" if blocked else ''))
    for feature, entry in selection['feature_coverage'].items():
        if 'short_because' in entry:
            changes.append(f"{feature}: {entry['items']} items, below {scale['feature_target']}: {entry['short_because']} "
                           f"({entry['frame_sentences']} frame sentences at {entry['frame_companies']} companies)")
    if len(items) != scale['total']:
        changes.append(f"{len(items)} items instead of {scale['total']}: the frame ran out of unused sentences")
    manifest = {
        'version': spot.version, 'seed': spot.seed, 'commit': spot.commit, 'commit_role': spot.commit_role,
        'procedure': {
            'selection': f"Python Random({spot.seed}); every company in the frame with an unused historical sentence, "
                         f"each with at least one and at most {scale['per_company']} original sentences; in order: "
                         f"{scale['controls']} guidance controls at distinct companies, every candidate of a mutator "
                         f"with no more candidates than its quota, feature coverage to {scale['feature_target']} items "
                         f"(scarcest feature first), the other mutators (scarce first, v1's preferences), one original "
                         f"per company, and a seeded round-robin fill to {scale['total']} items; source-only mutation "
                         f"feasibility",
            'quarters': 'latest two stored release/10-Q pairs; no 10-K-only Q4; numeric companyfacts screening',
            'items': f"{scale['mutants']} per mutator where candidates allow; at most two mutants per original; v1's "
                     f"gold rules, mutators and feasibility checks",
            'outputs_used_for_selection': False, 'human_review': False, 'changes_from_spot500_v1': changes},
        'excluded_tickers': sorted(spot.excluded),
        'exclusions': {'companies': sorted(spot.excluded),
                       'note': 'none: every company in the frame with an unused historical sentence is eligible'},
        'sentence_exclusions': {**removed, 'method': 'source record SHA-256 from each earlier frozen manifest; a frame '
                                                     'sentence with identical text is removed as well'},
        'company_history': {
            'tags': {'company_in_development': "the full set's development companies plus every company named in the "
                                               "round-8 and name-resolution fix tests or fix code",
                     'company_new': 'a company no earlier set (v1, v2, v3, full) sampled',
                     'company_seen': 'every other company: sampled by an earlier set, here with other sentences'},
            'seen_sets': [f'spot500_{name}' for name in spot.exclude_sentences_of],
            'in_development': sorted(spot.in_development), **{key: sorted(c) for key, c, _ in groups},
            'in_development_absent_from_set': absent,
            'method': "the full set's list carried unchanged, plus v3's mechanical derivation re-run over the round-8 "
                      "range; selection never reads the tags"},
        'company_history_derivation': derivation,
        'frame': {'strict_frame_sha256': fallback['strict_frame_sha256'], 'equals': 'spot500_full source_pool_sha256',
                  'exhibit_rule': fallback['rule'], 'companies_added': added, 'documents_added': fallback['documents']},
        'reference_sets': references,
        'company_list': [{'ticker': t, 'name': companies[t]['name'], 'sector': SECTOR_BY_INDUSTRY[companies[t]['sub_industry']],
                          'sub_industry': companies[t]['sub_industry']} for t in sorted(selected)],
        'selection': selection, 'counts': counts, 'corpus_sha256': digest(raw),
        'items': [{'item_id': it['item_id'], 'sha256': digest(canonical(it))} for it in items],
        'source_records': ([{'kind': 'company', 'id': t, 'sha256': digest(canonical(companies[t]))} for t in sorted(selected)]
                           + [{'kind': 'document', 'id': k, 'sha256': digest(canonical(d))}
                              for k, d in sorted(docs.items()) if d['ticker'] in selected]
                           + [{'kind': 'sentence', 'id': s['sentence_id'], 'sha256': digest(canonical(s))}
                              for s in sentences if s['sentence_id'] in chosen_sids]),
        'source_pool_sha256': digest(canonical([companies, docs, sentences])),
        'sources': {**provenance, 'records': [r for r in provenance['records'] if r['ticker'] in selected],
                    'screen_counts': {k: v for k, v in provenance['screen_counts'].items() if docs[k]['ticker'] in selected}},
        'builder_hashes': builder_hashes(spot),
        'cost_estimate': {'per_item_usd': DEFAULT_EST_COST_PER_ITEM, 'total_usd': round(len(items) * DEFAULT_EST_COST_PER_ITEM, 6),
                          'source': 'eval/eval.py DEFAULT_EST_COST_PER_ITEM', 'estimate_limit_usd': 5.5,
                          'live_budget_usd': 6.0}}
    return raw, json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode() + b'\n'


def build_from_sources(companies, docs, sentences, provenance, *, spot=None):
    from eval.eval import DEFAULT_EST_COST_PER_ITEM
    spot = spot or SETS['v1']
    if spot.full and spot.exhibit_fallback:
        return build_full2_from_sources(companies, docs, sentences, provenance, spot)
    if spot.full:
        return build_full_from_sources(companies, docs, sentences, provenance, spot)
    reference = reference_set(spot) if spot.extra_excluded_from else None
    references, derivation = exclusion_sources(spot) if spot.exclusion_groups else (None, None)
    items, selection = build_items(companies, docs, sentences, spot)
    selected = set(selection['selected_companies'])
    chosen_sids = set(selection['selected_sentence_ids'])
    source_records = ([{'kind': 'company', 'id': t, 'sha256': digest(canonical(companies[t]))} for t in sorted(selected)]
                      + [{'kind': 'document', 'id': k, 'sha256': digest(canonical(d))}
                         for k, d in sorted(docs.items()) if d['ticker'] in selected]
                      + [{'kind': 'sentence', 'id': s['sentence_id'], 'sha256': digest(canonical(s))}
                         for s in sentences if s['sentence_id'] in chosen_sids])
    raw = encode_jsonl(items)
    counts = {'companies': len(selected), 'items': len(items), 'gold_claims': sum(len(it['gold_claims']) for it in items),
              'by_company': dict(sorted(Counter(it['ticker'] for it in items).items())),
              'by_sector': dict(sorted(Counter(SECTOR_BY_INDUSTRY[companies[t]['sub_industry']] for t in selected).items())),
              'by_mutator': dict(sorted(Counter(it['mutator'] for it in items).items())),
              'by_feature': dict(sorted(Counter(f for it in items for f in it['features']).items()))}
    # Persist only source inputs. The full pool fingerprint detects selection-frame changes.
    manifest = {'version': spot.version, 'seed': spot.seed, 'commit': spot.commit, 'commit_role': spot.commit_role,
                'procedure': {'selection': f'Python Random({spot.seed}), sorted source pools; reserve banks/REIT-utilities/variable-week and available new features; fill least represented GICS sector to 30; source-only mutation feasibility',
                              'quarters': 'latest two stored release/10-Q pairs; no 10-K-only Q4; numeric companyfacts screening',
                              'items': 'five per mutator; at most two mutants per original; fill one original per sampled company and newer-feature gaps; five distinct-company guidance controls',
                              'outputs_used_for_selection': False, 'human_review': False},
                'excluded_tickers': sorted(spot.excluded), 'company_list': [
                    {'ticker': t, 'name': companies[t]['name'], 'sector': SECTOR_BY_INDUSTRY[companies[t]['sub_industry']],
                     'sub_industry': companies[t]['sub_industry']} for t in sorted(selected)],
                'selection': selection, 'counts': counts, 'corpus_sha256': digest(raw),
                'items': [{'item_id': it['item_id'], 'sha256': digest(canonical(it))} for it in items],
                'source_records': source_records, 'source_pool_sha256': digest(canonical([companies, docs, sentences])),
                'sources': {**provenance, 'records': [r for r in provenance['records'] if r['ticker'] in selected],
                            'screen_counts': {k: v for k, v in provenance['screen_counts'].items() if docs[k]['ticker'] in selected}},
                'builder_hashes': builder_hashes(spot),
                'cost_estimate': {'per_item_usd': DEFAULT_EST_COST_PER_ITEM, 'total_usd': len(items) * DEFAULT_EST_COST_PER_ITEM,
                                  'source': 'eval/eval.py DEFAULT_EST_COST_PER_ITEM', 'live_budget_usd': 0.95}}
    if reference:
        # Only the seed and the excluded companies differ from the earlier set,
        # plus any quota the exclusions leave unfillable.
        changes = [f"seed {reference['seed']} -> {spot.seed}",
                   f"excluded before sampling: the original {len(EXCLUDED)} plus the {len(spot.extra_excluded)} "
                   f"companies sampled by {reference['version']}"]
        quota = next(r for r in selection['reservations'] if r['constraint'] == 'variable-week companies')
        if quota['minimum'] < 2:
            changes.append(f"variable-week quota lowered from 2 to {quota['minimum']}, the number of eligible "
                           f"variable-week companies left after the exclusions")
        manifest['procedure']['changes_from_' + reference['version']] = changes
        manifest['exclusions'] = {
            'original': sorted(EXCLUDED), reference['version'] + '_companies': sorted(spot.extra_excluded),
            'applied_to': 'the eligible company pool, before every reservation and the sector fill; '
                          'the sampling frame and source screening are unchanged'}
        manifest['reference_set'] = reference
    if derivation:
        # As for v2: only the seed, the exclusions and any quota they exhaust differ from v1.
        parts = [f"the {len(companies)} {description}" for _, companies, description in spot.exclusion_groups]
        changes = [f"seed {SEED} -> {spot.seed}",
                   f"excluded before sampling: the original {len(EXCLUDED)} plus {', '.join(parts[:-1])} and "
                   f"{parts[-1]}; {len(spot.excluded)} distinct companies in all"]
        for constraint, nominal, label, plural in HARD_QUOTAS:
            quota = next(r for r in selection['reservations'] if r['constraint'] == constraint)
            if quota['minimum'] < nominal:
                changes.append(f"{label} quota lowered from {nominal} to {quota['minimum']}, the number of eligible "
                               f"{plural} left after the exclusions")
        for mutator, short in sorted(selection.get('mutant_shortfalls', {}).items()):
            changes.append(f"{mutator} mutants lowered from {short['quota']} to {short['taken']}: the sampled "
                           f"companies have {short['candidates']} {mutator} candidates after the exclusions")
        manifest['procedure']['changes_from_spot500_v1'] = changes
        manifest['exclusions'] = {
            'original': sorted(EXCLUDED), **{key: sorted(companies) for key, companies, _ in spot.exclusion_groups},
            'applied_to': 'the eligible company pool, before every reservation and the sector fill; '
                          'the sampling frame and source screening are unchanged'}
        manifest['reference_sets'] = references
        manifest['exclusion_derivation'] = derivation
    return raw, json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode() + b'\n'


def write_frozen(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f'Refusing changed frozen output: {path}')
        return
    with path.open('xb') as handle:
        handle.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'eval')
    parser.add_argument('--set', dest='spot_set', choices=sorted(SETS), default='v1',
                        help='frozen set to build; v1 (the default) may only be rebuilt byte for byte')
    args = parser.parse_args(argv)
    spot = SETS[args.spot_set]
    raw, manifest = build(args.data_root, spot)
    write_frozen(args.output_dir / spot.corpus, raw)
    write_frozen(args.output_dir / spot.manifest, manifest)
    print(json.dumps({'corpus_sha256': digest(raw), 'manifest_sha256': digest(manifest),
                      'counts': json.loads(manifest)['counts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
