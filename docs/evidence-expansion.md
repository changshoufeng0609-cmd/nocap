# Evidence expansions

## Tesla

On 2026-10-29, NoCap added **TSLA as its eleventh company**, using the existing cached SEC Submissions
and companyfacts inputs through `receipts.edgar` and `receipts.ingest`. The expansion made **0 live SEC
requests and 0 model calls**. It adds 347 original XBRL facts, 32 derived quarterly facts and 57 filing
records. All original ten-company rows were unchanged, verified by canonical row hashes before and after.

The most recent eight fiscal quarters run from **Q4 FY2024 through Q3 FY2026**. Revenue, cost of revenue,
gross profit, research and development, operating expenses, operating income, net income and operating
cash flow have values in all eight. Basic and diluted EPS have values in **6/8**; EPS is never derived
by subtracting annual and year-to-date figures. Older comparative observations remain for provenance and
year-over-year calculations.

This is an **XBRL-only expansion**. Tesla's image-based earnings release tables, segment breakdowns and
non-GAAP figures were not ingested. Before the corresponding 10-Q is accepted, a claim can remain
unverifiable even if an earlier earnings-related 8-K exists. This does not claim complete coverage of
every metric or quarter.

Reproduce from an existing local EDGAR cache:

```bash
.venv/bin/python -m receipts.ingest --tickers TSLA --quarters 8 --offline
.venv/bin/python scripts/build_snapshot.py --no-model
```

`--offline` fails on missing cached inputs; it does not fetch from SEC. A fresh clone already receives
the committed evidence snapshot and does not need ingestion. The XBRL ingestion default now includes
TSLA; the separate press-release ingestion defaults deliberately retain the original ten companies.
Extraction vocabulary and model request bytes were not changed by this extension.

The [audit record](../eval/evidence_expansion/tsla-20261029.json) retains cache URLs and hashes, quarter
coverage and validation boundaries. All **347/347 XBRL rows** match the cached source's value, unit concept,
period and accession; **379/379** facts join to an acceptance timestamp, and every derived fact uses the
latest acceptance time among its supporting accessions. A Q3 revenue probe returns `?` one second before
the 10-Q's acceptance and `✓` at acceptance. Because an earlier 8-K has no stored revenue cell, that
pre-acceptance result is `no_evidence`, not `look_ahead`. These are integration checks, not an independent
model-extraction accuracy study. No held-out split was evaluated for this extension.

## Q3 2026 top-up: Microsoft, Alphabet, Amazon

On 2026-10-30, NoCap added **the quarter ended September 30, 2026 for Microsoft (Q1 FY2027), Alphabet and Amazon
(Q3 2026)**, whose earnings 8-Ks and 10-Qs reached EDGAR on October 28 and 29. The top-up made **16 live SEC requests
and 3 Muse Spark 1.3 calls ($0.083, Standard mode)**. It adds 97 original XBRL facts, 1 derived quarterly fact,
**263 earnings-release facts** and 7 filing records. Every earlier company, filing, fact and fiscal-period row was
unchanged, verified by canonical row hashes before and after, and all 161 earlier press-release files are
byte-identical.

JPMorgan and Bank of America had filed only their earnings 8-Ks, and Apple neither its 8-K nor its 10-K. Press-release
ingestion pairs each earnings 8-K with its filed 10-Q or 10-K, so those three wait for a later top-up. AMD and NVIDIA
have not reported a newer quarter.

The three companies now run from the quarter ended September 30, 2023 (Microsoft's Q1 FY2024) through the September
2026 quarter, **13 quarters**, like META, NFLX and TSLA. The ingest window is computed from the run date:
`--quarters 8` on 2026-10-30 keeps facts ending since 2023-09-13, and a dry rebuild from the earlier cached inputs
reproduced every stored Microsoft, Alphabet and Amazon row exactly. `--quarters 9` would also have added an older
quarter.

Muse Spark mapped each earnings release (EX-99.1) in one call at reasoning effort minimal, and code kept a cell only
when its own parse matched. Microsoft has 56 release facts (4 non-GAAP, 32 segment), Alphabet 69 (5 non-GAAP,
24 segment) and Amazon 138 (84 segment). All **88/88** GAAP facts that the paired 10-Q also reports agree with its
XBRL, as do 13/13 more compared with the latest 10-Q or 10-K.

Reproduce:

```bash
.venv/bin/python -m receipts.ingest --tickers MSFT,GOOGL,AMZN --quarters 8 --refresh
.venv/bin/python -m receipts.press_release --tickers MSFT GOOGL AMZN --quarters 1 --dry-run
.venv/bin/python -m receipts.press_release --tickers MSFT GOOGL AMZN --quarters 1 --validate --budget-usd 3.31 --concurrency 1
.venv/bin/python scripts/build_snapshot.py --no-model
```

`--budget-usd` caps the cumulative press-release spend ledger, not one run: 3.31 is the $2.41 already recorded plus at
most $0.90 of new spend. Cached model responses make a re-run free. `--validate` rewrites
`data/pr_facts/_validation.json`; the earlier report was restored and this run's report is kept in the audit record.

The [audit record](../eval/evidence_expansion/q3-2026-topup.json) retains the requests, input hashes, row counts,
canonical hashes, spend and every check. The 693-claim dev replay (no network or model) is unchanged claim for claim,
the three saved examples still score 7/2/2, 5/3/0 and 6/3/0, the snapshot is 6.27 MB, and the qualifier grid and the
test suite pass. The frozen Q&A benchmark still reproduces **200/200**: it now reads the October 29 `facts.sqlite` it
was scored on from an archived copy held to the same pinned hash, and scoring it over the new snapshot gives the same
200 records. For each company, a revenue claim and one release figure (Microsoft's non-GAAP diluted EPS, Google Cloud
revenue, AWS net sales) returned `?` before the top-up and now return `✓` with the new receipts, from the second the
8-K was accepted. One second earlier they return `✗` `look_ahead`, citing the 8-K, because the release's own figure
dates it. These are integration checks, not an independent model-extraction accuracy study. No held-out split was
evaluated for this top-up.

## October 31 expansion: 21 more large caps

On 2026-10-31, NoCap grew from 11 to **32 companies**: Broadcom, Oracle, Salesforce, Adobe, Intel, Qualcomm, Cisco, IBM,
Palantir, Uber, Walmart, Costco, Disney, Nike, McDonald's, Coca-Cola, Eli Lilly, UnitedHealth, Johnson & Johnson,
Goldman Sachs and Wells Fargo. The expansion made **424 live SEC requests and 172 Muse Spark 1.3 calls ($4.18, Standard
mode)**. It adds 6,473 XBRL and derived facts, 1,163 filing records and **9,229 earnings-release facts**, for 27,617 stored
facts. Every earlier company, filing, fact and fiscal-period row and all 167 earlier press-release files are unchanged,
verified by canonical row hashes before and after.

Of 24 candidates, a company was kept only if revenue, net income and diluted EPS each had values in at least 6 of its
latest 8 quarters. **Visa** was dropped because it reports EPS only per share class, **Mastercard** because it tags only
net income including noncontrolling interests, and **Exxon Mobil** because its ticker now belongs to a new holding company
with one quarter of filings (all three joined later: Visa and Mastercard with the S&P 500 on October 31, Exxon Mobil
across both registrants on November 1). Broadcom tags net income only as `ProfitLoss`; ingest now accepts that concept, but only where
the same filing reports no noncontrolling interest, which is when the two are equal. All 11 earlier companies rebuild to
identical rows under the new code.

XBRL covers the latest eight fiscal quarters plus the year-ago year, as before, for every company. Earnings releases are
mapped for up to eight paired quarters. **A new XBRL gate** decides which release figures may become evidence. Once the
paired 10-Q or 10-K is filed, a quarterly or annual GAAP figure that its XBRL contradicts is dropped unless the cause is a
documented restatement. A whole file is quarantined, never read, if its columns were read with swapped durations or one
period column holds two or more unexplained mismatches. The gate dropped 39 figures, mostly release "net income" lines that
include noncontrolling interests (Palantir, UnitedHealth, Walmart, Intel, Coca-Cola), and quarantined 12 files: 7 of
Disney's 8, which print costs in parentheses, 3 of Qualcomm's, 1 Cisco and 1 Johnson & Johnson. Among the figures still
served, **1,989 of 1,993** paired GAAP figures agree with XBRL; the other 4, and 2 of the 171 compared with a later report,
are documented restatements. The gate finds nothing in any earlier company's 83 release files.

Reproduce from the local EDGAR and model caches:

```bash
.venv/bin/python -m receipts.ingest --tickers AVGO,ORCL,CRM,ADBE,INTC,QCOM,CSCO,IBM,PLTR,UBER,WMT,COST,DIS,NKE,MCD,KO,LLY,UNH,JNJ,GS,WFC --quarters 8 --offline
.venv/bin/python -m receipts.press_release --tickers AVGO ORCL CRM ADBE INTC QCOM CSCO IBM PLTR UBER WMT COST DIS NKE MCD KO LLY UNH JNJ GS WFC --quarters 8 --validate --offline --validation-out /tmp/nocap-validation.json
.venv/bin/python scripts/build_snapshot.py --no-model
```

The [audit record](../eval/evidence_expansion/expansion-20261031.json) retains the candidates and the reasons for each
drop, input hashes, row counts, canonical hashes, every model call, every dropped and quarantined figure, and every check.
The 693-claim dev replay is unchanged claim for claim. That required one verifier rule at the time: across 32 companies a
wrong gross margin, tax rate or growth rate that equals another company's is chance, so it no longer named that company
(since replaced by the draft-scoped rule below). The three
saved examples still score 7/2/0/2, 5/3/0/0 and 6/1/2/0 (✓/✗/not public/?), the slide, 10-K and Private recordings still
replay, the snapshot is 13.46 MB of its 16 MiB limit, the frozen Q&A benchmark still reproduces **200/200**, and the
qualifier grid and the test suite pass. Latest-quarter revenue claims return `✓` with receipts for 16 of 17 probed
companies; Oracle's newest quarter returns `?` because its latest 10-Q repeats last year's fiscal label, which ingest
refuses to guess around. These are integration checks, not an independent extraction accuracy study. No held-out split was
evaluated for this expansion.

### Peer attribution at S&P 500 scale

Before the S&P 500 step, a wrong figure that exactly matched another covered company's number was attributed to that
company (`entity_mismatch`), whichever company it was. Across about 500 companies an equal three-digit figure is far more
often a coincidence than its owner, so attribution now considers only companies the same draft or its context line names;
any other wrong figure stays `✗` with its filed value (`value_mismatch`). On the 693-claim dev replay every status is
unchanged. Fifteen wrong figures whose owner the draft never names now report `value_mismatch` instead of
`entity_mismatch`: 11 planted entity swaps and 3 other wrong figures in the seeded dev set, and 1 entity error in the trap
dev set. Nothing else changed. Every planted error is still caught, but correct-reason counts fall from 97/109 to 86/109
on seeded dev and from 36/40 to 35/40 on trap dev. The saved roundup example still reports its JPMorgan-for-Alphabet
error as `entity_mismatch`, because that draft names both. The
[audit record](../eval/evidence_expansion/sp500-phase-a-20261031.json) lists the 15 claims.

## S&P 500: 467 more companies

On 2026-10-31, NoCap grew from 32 to **499 companies**: every S&P 500 company but one. The constituent list (Wikipedia,
accessed 2026-10-30 20:14 UTC, hashed in the record) has 503 rows for 500 companies. Alphabet, Fox and News Corp list
two share classes that file as one SEC registrant, and ingest maps one ticker to one CIK, so GOOG, FOX and NWS are
covered through GOOGL, FOXA and NWSA. **Exxon Mobil was excluded**: SEC's ticker map now points XOM at ExxonMobil
Holdings Corp (CIK 2115436), a new registrant with one 10-Q, while its earlier quarters are filed under Exxon Mobil Corp
(CIK 34088); it joined on November 1 (Coverage improvements, below). That left 467 companies to add. None was dropped for thin coverage; the gaps are counted below instead.

**Phase A** changed the code a 500-company universe needs (the peer-attribution rule above; tickers that are one or two
letters or everyday words, such as T, F, ON, NOW and KEY, never name a company alone; a filing that reuses last year's
fiscal label gets the next year's when it is free; the contract-revenue rule below) and ran a **30-company pilot**, a
seeded sample weighted toward banks, insurers, REITs and utilities (4 of each, and 2 from every other sector but
Communication Services). **Phase B** added the other **437 in nine batches** (eight of 50, one of 37). Every batch had
to pass the same gates: a spend guard, the XBRL gate on its release files, revenue confirmation, the label gate,
canonical row hashes showing every earlier row and release file unchanged, the 693-claim dev replay identical to its
reference, the saved examples, recordings and qualifier grid unchanged on a scratch snapshot, and a clean XBRL-gate
re-run after relabelling. One failure got through: batch 3's ingest stopped on a KeyError after 43 of its 50 companies,
and the batch driver, a scratch script, accepted the exit code. The wrap-up's reconciliation caught it (503 rows, less 3
share classes and XOM, is 499 planned; 492 were stored), and fix 1 below closed it.

The pilot made **376 live SEC requests** and **121 Muse Spark 1.3 calls ($2.75, Standard mode)**, moving the
press-release spend ledger from $6.67 to $9.43. Phase B logged **4,459 live SEC requests** (793 for ingest in the eight
batches that printed a count, 3,666 in the release dry runs; the mapping read only cached filings) and made **1,848 Muse
Spark 1.3 calls ($40.21, Standard mode)**, 1.13 times the dry runs' estimate of $35.61 and between the pilot's
projections of $40.13 (its mean cost per company) and $44.35 (weighted by sector). The ledger went from $9.43 to $49.64.
Before each batch, the ledger plus 1.15 times the batch's estimate had to stay under $70; the highest check was $49.63.
The fixes after review made no SEC request and no model call, so the whole step cost **1,969 calls and $42.97**.

XBRL covers the latest eight fiscal quarters plus the year-ago year for all 499 companies, and all but 7 have at least
eight labelled quarters: five new registrants (FDXF, HONA, Q, SKYD, VYLR), Ferguson after its fiscal-year change, and
PPL, some of whose filings declare implausible fiscal years. Release tables were mapped for the latest four paired
quarters of each new company; the first 32 keep up to eight. **460 companies have release figures** and 39 are
XBRL-only: 9 because the XBRL gate quarantined every release file they had (AES, COR, DHR, HON, MCK, NXPI, RL, ROK,
WAB), 25 without any release file, and 5 whose files hold no figures. After four review rounds and the first
latest-quarter top-up (below) the [snapshot](../data/snapshot/manifest.json) holds 499 companies, 28,535 filings,
**144,191 XBRL and derived facts** and **96,171 earnings-release facts** in 87.07 MB (fingerprint `48162f02e758`).

That first top-up ran on November 1 with `scripts/topup_latest_quarter.py`: refreshed SEC Submissions showed newer
filings for only Bank of America, Digital Realty and Flex, so it added Bank of America's Q3 2026 and Flex's Q2 FY2027
(4 Muse Spark 1.3 calls, $0.10), every gate passing. Another 54 companies had published third-quarter earnings without
a 10-Q yet; the mapper reads a release together with its paired 10-Q, so their quarter arrives with later top-ups.

### What the gates removed

In the pilot, the XBRL gate quarantined 6 of 99 release files (4 Cencora, 2 Marathon Petroleum) and dropped 38 figures:
32 release "net income" lines equal to XBRL's ProfitLoss, which includes noncontrolling interests, rather than
NetIncomeLoss, and 6 Philip Morris cost-of-revenue figures with the wrong sign. No served pilot figure contradicts XBRL:
of 1,539 paired GAAP figures, 1,521 agree and the other 18 are not served, and the 2 of 230 that differ from a later
report are documented restatements. In Phase B the gate read 1,611 release files, quarantined **88 of them (5,013
figures)** and dropped **799 figures** from the files it kept. The label gate reset 154 Phase B release labels to the
store's: 107 took the store's label, and 47 lost theirs because the store leaves those periods unlabelled (ACGL 10, SKYD
12, CAH 12, FERG 13). An unlabelled period answers `?`.

**Revenue held back.** A contract-revenue figure (RevenueFromContractWithCustomer) can be a slice rather than the total:
Fifth Third's is about $150M a quarter against $1.2B of noninterest expense, and Camden's was $13M for 2025 against
$567M cost of revenue. Since Phase A it stands for revenue only when it is at least half of both the operating expenses
and the cost of revenue in the same filing. For financial, real-estate and utility filers (by SIC code), and for any
filer whose operating-lease income is 10% or more of it, ingest also holds it back until `scripts/confirm_revenue.py`
finds at least two periods where a filed version equals the total in the company's own earnings release, and none where
it does not. Phase B confirmed 11 companies this way (AJG, BEN, BLK, EQIX, ERIE, HST, ICE, IVZ, NDAQ, TPL, TROW). Nine
stay held back (AWK, CBOE, DUK, ETR, IRM, NEE, NI, OKE, SRE) because none has two comparable periods; ETR and SRE still
serve revenue for most quarters from other concepts. Holding back costs coverage, by design. Served as revenue, a slice
would mark a correct revenue figure `✗` against a number that is not revenue, and would verify a wrong one that happens
to equal it. A `?` sends the reader to the filing; a false `✗` teaches them to ignore the checker.

### Fixes after review

Two defects came out of the wrap-up, four out of an independent QA of 40 Phase B companies, four more out of a second
QA round and its review, three out of a third round, three out of a final code review, and one more found while
checking it. All seventeen were fixed offline, and each changed only the rows it was meant to (see the old-rows proof
below).

1. **Seven companies missing** (wrap-up finding 1). Invitation Homes reported $0 contract revenue for Q1 2025 and tagged
   no lease income, and the contract-revenue check then read a lease entry that did not exist. It now runs no ratio test
   against contract revenue that is not positive, and a test reproduces the old KeyError. An offline ingest added INVH,
   ES, EBAY, KHC, SLB, CB and CRL (374 filings, 1,928 facts, 116 fiscal periods), whose 1,241 release facts, mapped and
   gated in batch 3, needed no change.
2. **CrowdStrike and Kroger one fiscal year off** (wrap-up finding 4). At both, two of three 10-Ks declared fiscal years
   that the 10-Qs contradicted, and the old rule let the 10-Ks win. Now the convention at least two-thirds of all 10-Qs
   and 10-Ks share wins; without one, every label is dropped. CrowdStrike's "Second Quarter Fiscal Year 2027 ... ended
   July 31, 2026" is now Q2 FY2027, and "Kroger's second quarter 2026 ended on August 15, 2026" is Q2 FY2026, as their
   releases say. 514 stored facts and 319 release facts were relabelled, and FedEx Freight, with one 10-K against one
   10-Q, now has every label gated. Across all 499 companies, the old and new code differ only at CRWD, KR and FDXF.
3. **Figures dated with the wrong period length** (QA). L3Harris tagged full-year figures as its fourth quarter, and
   Comfort Systems tagged a quarter under a full-year date. Four checks now drop such a figure: a quarter equal to its
   10-K year while another quarter is non-zero; a fourth quarter, of a metric that is never negative, above every filed
   nine-month figure; four quarters that miss an unrestated 10-K year beyond rounding; and a 10-Q "full-year" figure
   that no 10-K figure matches. They drop 15 figures at 7 companies (L3Harris 6, Comfort Systems 4, Axon 1, Tractor
   Supply 1, and dividends per share at Altria, Ross and Williams-Sonoma), after which the true fourth quarters derive:
   L3Harris operating income $393M, as released, and Tractor Supply net income $227.4M. On the QA's re-check, 10-K "Q4"
   facts holding a full-year value fall from 7 to 0.
4. **Derived fourth quarters on mixed bases** (QA). A fourth quarter not filed on its own is the fiscal year minus nine
   months, and after a restatement the two can be on different bases (Dollar Tree, Martin Marietta). Now a restated year
   is not paired with nine-month figures whose versions differ only by rounding (Martin Marietta's 476 and 206); when a
   component was restated and a later filing kept the total, only the latest pair is derived, public from the later
   filing (Dollar Tree keeps 3,116.9 and 533.6, not -4,377.1 and 504.9); a derived quarter of revenue, cost of revenue,
   R&D or operating expenses must lie between zero and its total; and a derived fourth quarter of those must lie within
   10–75% of its year. Only 6 derived quarters fall outside the band: Crown Castle's cost of revenue (a narrower
   nine-month concept), Occidental's revenue (a year without OxyChem against nine months with it), and Atmos and
   CenterPoint cost lines whose quarters do not add up. Real impairment quarters reach 65%, and Atmos' summer revenue is
   13.7%. The rules remove 203 derived facts and add 32. By the QA's own test, implausible derived fourth quarters fall
   from 41 of 3,590 to 24 of 3,565; the rest are gross profit, which can be negative, or real (Intel, Lockheed Martin,
   Truist, Apollo).
5. **Net income including noncontrolling interests** (QA). Some releases' net income includes noncontrolling interests
   (CF Industries), and 13 of the 17 companies the QA found tag no NetIncomeLoss in any 10-Q or 10-K, so XBRL could not
   catch it. At a company with any noncontrolling interest, a release net income with no XBRL net income for its period
   is now dropped unless companyfacts shows it is the attributable figure: 265 figures at 55 companies go, and 29
   correct ones stay, Costco's 8 among them. On the QA's re-check, such figures that stand alone for their period fall
   from 89 at 17 companies to 0.
6. **GAAP rows stored as non-GAAP** (QA). The QA found 177 non-GAAP facts at 25 companies whose row label reads as GAAP,
   such as United Airlines' "Net income (GAAP)". A non-GAAP fact is now dropped when its label says "GAAP" without
   "non-GAAP", "adjusted" or "excluding", whatever the hyphen: 171 facts at 24 companies. Fortinet's six "Non‐GAAP
   operating income" rows, written with a Unicode hyphen, stay. A broader rule, dropping every non-GAAP figure equal to
   a GAAP one (2,628 facts), was rejected because it also removed true non-GAAP figures such as NVIDIA's $1.30 non-GAAP
   diluted EPS.
7. **Release figures against all same-period XBRL** (QA round 2). The mapper compares a release with its paired filing
   and excuses a mismatch with an older filing as a possible recast. Dow's Q2 2026 10-Q is missing from SEC's
   companyfacts, so its swapped column, $0.99 stored as Q2 2025 diluted EPS (true -$1.18), slipped through. Every served
   release GAAP figure is now compared with every XBRL value the store holds for its dates, reported or derived, and a
   mismatch is excused only by a documented restatement. Against a reported value the release figure goes: 49 at 21
   companies, among them Dow's swap, Charter's and United's trailing-twelve-month columns stored as FY2025, and Baker
   Hughes' pre-tax income stored as operating income. Against a derived quarter, the company's own release published
   within 100 days of the derived value's filing wins when its row agrees with reported XBRL elsewhere and disagrees
   nowhere: 19 derived values at 8 companies leave the store, among them Bank of America's Q4 2025 revenue of $31,180M
   (its release says $28,367M) and NetApp's, Hasbro's and CrowdStrike's artifacts. Otherwise the release figure goes:
   153 at 80 companies, among them year-later comparatives, consolidated net income, Arch's and Chubb's "Net premiums
   earned" as revenue, and costs printed in parentheses. Illinois Tool Works tags a narrower CostOfRevenue beside the
   CostOfGoodsAndServicesSold its income statement reports; ingest now takes the larger, and ITW is the only company
   where that applies.
8. **Labels against dates** (QA round 2). A release figure whose FY or quarter label its dates contradict, and an
   unlabelled twelve-month figure that does not end at a fiscal year end, is dropped: 370 figures, 323 of them
   trailing-twelve-month figures and 47 of them Costco, Domino's and PepsiCo fourth quarters, which last 16 weeks but
   were stored with 13-week dates.
9. **x1000 slips** (QA round 2). A figure about 1,000 times off the company's other figures for its row, at a precision
   about three places off theirs, is a misread scale: 4 of Block's 2024 free cash flow figures, read in thousands beside
   the same row in millions. A real small figure printed at its row's precision, such as HPE's $1M Routing segment,
   stays.
10. **Dividends served as filed** (review of round 2). A dividend tagged for a period is what the board declared during
    it, so FY - 9M is not the quarter's dividend when declarations do not follow quarters: Eli Lilly declares two
    dividends in its second and fourth quarters and none in the others, so its derived Q3 2025 was $0 and Q4 2025 $3.23,
    its $1.50 and the next $1.73. Of the 833 dividends ingest derived, 137 were not the company's quarterly rate, 57 of
    them zeros at 25 companies. No dividend quarter is derived any more, so filed quarters and years and release figures
    are what is served, and the 7 Lilly and Blackstone release dividends that round 2 had dropped against derived ones
    are back.
11. **GAAP lines stored as non-GAAP** (QA round 3). A section header ("Expenses Excluding Interest" over Schwab's net
    income), a reconciliation heading ("Adjusted Diluted EPS" over Vulcan's first row, which is GAAP EPS) or a footnote
    parsed as a heading ("* Non-GAAP financial measure." over GE HealthCare's income statement) made a GAAP line the
    period's only non-GAAP figure, so a true adjusted claim, such as Schwab's $2.93B adjusted net income, met the GAAP
    number and got `✗`. A non-GAAP net income, EPS, operating income or gross profit equal to the same-period GAAP filing
    value is now dropped unless its own cell in the release says non-GAAP: its row or column, a table titled non-GAAP
    with no row singled out as adjusted, or an adjusted row repeating the number. That drops 214 figures at 33
    companies, and the QA's 173 exposed periods fall to 42, none of them a different adjusted figure. Equal values that
    are genuinely non-GAAP stay: NVIDIA's $1.30 diluted EPS and Walmart's adjusted operating income still verify, and
    revenue, behind the qualifier grid's 48 non-GAAP verifications, is left alone.
12. **Dividend quarters at the company's rate** (QA round 3). A filed quarter is what the board declared during it:
    Pfizer's first quarter files $0 and its second $0.86, two declarations of $0.43. A quarter's dividend is now served
    only when it is positive and equals the filed quarter before or after it, which removes 1,061 quarters at 185
    companies; releases still state the rate, and Lilly's 4 second-quarter release dividends that round 2 had dropped
    against the doubled filed quarters are back.
13. **Per-share slips** (QA round 3). Per-share values beyond $1,500 of EPS or $60 of dividends are tagging slips and are
    never served: 24 at Halliburton, Mosaic, Howmet and Seagate. Mosaic's "$0.22" dividend had been answered with
    $220,000.
14. **Derived quarters from one concept** (code review). A derived quarter took its components from whatever concept
    was filed. Ares' Q4 2023 net income was its full-year NetIncomeLoss ($474.3M) minus nine months of ProfitLoss
    ($561.9M), which includes noncontrolling interests, so it read -$87.6M and every claim about the quarter got `✗`. A
    total now pairs only with components of its own concept, or of one its own filing reports for the period at the
    same value, as JPMorgan's 10-K does for Revenues and RevenuesNetOfInterestExpense. Of the 93 served derived values
    the review found mixing concepts, 36 go, 26 come back at the same value from a filing whose concept matches, and 31
    stay as one measure. A total restated since it was first filed under its concept no longer derives against that
    filing's components: Crown Castle's FY2023 cost of goods sold, $316M as filed and $304M restated, would have given
    a $36M quarter against its unchanged $268M nine months. BlackRock's Q4 2024 revenue now derives ($5,677M, as
    released), and Hasbro's, from its larger Revenues line, meets its release ($1,445.9M) and leaves the store under
    fix 7.
15. **ProfitLoss as net income** (code review). ProfitLoss includes noncontrolling interests, and ingest let it stand
    for net income in any period whose filing tagged no NCI. Ares tags none for Q3 2023, so its $197.0M ProfitLoss
    verified a net income claim, although its FY2023 ProfitLoss ($885.8M) against NetIncomeLoss ($474.3M) shows a large
    NCI. ProfitLoss now stands for net income only at a company whose 10-Qs and 10-Ks in the window show no NCI
    (Broadcom), or in a period whose whole NCI the filing tags as zero and no filing contradicts (AIG, Caterpillar). APA
    tags only its redeemable NCI as zero for Q3 2023 while its next 10-Q reports $96M of nonredeemable NCI, so its
    ProfitLoss goes too. That removes 830 ProfitLoss facts at 72 companies and 12 quarters derived from them, and
    Prologis' and Duke's fourth quarters now derive from the attributable figures ($1,278.9M, not $1,342.2M, for
    Prologis' Q4 2024). With that XBRL gone, 44 release net income lines equal to it no longer pass as checked and are
    dropped, among them Simon's and Entergy's "Consolidated net income", and AEP's Q2 2025 release figure ($1,226M),
    which fix 7 had dropped against its ProfitLoss ($1,288.3M), is back. 120 net income periods at 22 companies lose
    their only XBRL value; a release still answers 17 of them, and the rest answer `?`.
16. **Release quarters that are another period's cell** (code review). Fix 7 let a release quarter outrank a derived one
    on the row's record in other periods alone, so a twelve-month cell read as Q4 could remove the right fourth
    quarter. A release quarter that repeats another period's figure in its own release, or that lies outside 10–75% of
    the reported fiscal year, no longer outranks a derived value. In the review's synthetic case the derived $250M now
    stays and the release's $1,000M goes; all 20 decisions fix 7 has recorded so far pass the new test.
17. **Broad revenue lines the releases contradict** (found checking the code review). Where a filing reports Revenues
    above its contract revenue for a period, ingest takes the larger figure as the total. Hasbro's filings from 2025
    tag a Revenues line 13–15% above the net revenues its income statement and releases report ($1,006.1M against
    $887.1M for Q1 2025), and Republic Services' its gross revenue before intercompany eliminations ($18,418M against
    $16,032M for 2024), so a true revenue claim got `✗`. `scripts/confirm_revenue.py` now decides from the company's
    own releases: when they equal contract revenue in at least two such periods (a fourth quarter derived for each
    concept) and the broad figure in none, ingest serves contract revenue there. That replaces 57 revenue figures at
    the two companies with 59, since Hasbro's two fourth quarters, outranked before, now derive as released. ADM,
    Dollar Tree, Southern, AEP and PPL, whose releases back the broad figure, are unchanged. The same commit closes six
    lower-severity review findings without changing a stored row: a period's other release figures are checked
    against the release that outranks its derived value, a second gate run counts what the first dropped, the archive
    scripts keep their index when a run fails, a confirmation is revoked when a re-check disagrees, records carry the
    store's run date, and fix 11 reads transposed tables.

The data fixes of all four rounds leave the 693-claim dev replay identical, on the full data and on the frozen
62-company data; from round 2 on it was compared with a baseline that includes the verifier's same-filing growth
change. The saved examples still score 7/2/0/2, 5/3/0/0 and 6/1/2/0 (✓/✗/not public/?), all three recordings replay,
the qualifier grid is identical, and a second pass of the XBRL gate over the 1,804 live release files, of the label gate
and of the release gates changes nothing. Re-run on the fixed data, the code review's own scans find no derived value
mixing concepts beyond the 31 that are one measure, and ProfitLoss served as net income only at Broadcom, Cboe and
VeriSign, whose filings in the window show no NCI, and at AIG, Caterpillar and Ford in periods whose whole NCI is tagged
as zero.

### Old rows

Phase B changed no earlier row. Canonical row hashes taken before batch 1 (62 companies) and after the wrap-up show no
earlier company, filing, fact or fiscal period missing or altered, while the store grew from 62 to 492 companies, 3,706
to 28,157 filings, 18,886 to 145,198 facts and 1,023 to 7,961 fiscal periods; all 689 earlier release files are
unchanged among 3,909. The pilot shows the same for the 32 companies before it. The fixes then changed rows on purpose,
and the comparison with batch 9 accounts for each: 7 companies, 374 filings, 1,928 facts and 116 fiscal periods added
(fix 1); 521 facts and 30 fiscal periods relabelled and 1 period removed (fix 2); 218 facts removed and 34 added (fixes
3 and 4); 13 company rows whose alias lists the newly merged alias code recomputed, with no lookup key changed; and 355
release files, all from fixes 2, 5 and 6. Against the first round's data, round 2 removes the 19 rejected derived values
and replaces ITW's 27 cost-of-revenue rows; no company, filing or fiscal period changes, and 416 release files do: the
208 that lost gated figures and their reports. Fix 10 then removes the 833 derived dividends at 305 companies and
restores the 7 release dividends in 4 files; re-ingesting those companies gives 45 of them one more alias each from the
merged alias code, and nothing else changes. Round 3 removes the 1,061 dividend quarters and 24 per-share slips,
rewrites 70 release files for fix 11 and restores Lilly's 2; re-ingesting 186 companies gives 55 of them more common
names from the merged name code, none removed, and nothing else changes. Round 4 removes the 830 ProfitLoss facts and
12 quarters derived from them (fix 15) and 65 derived quarters that mix concepts (fix 14), re-derives 29 values
unchanged from one concept and adds 6 (Prologis' three, Duke's, BlackRock's and AEP's), for 144,132 facts; 50 release
files change, the 24 that lost NCI-inclusive net income and AEP's restored one, each with its report; re-ingesting 104
companies gives 18 of them more common names, none removed, and nothing else changes. Fix 17 then changes only
Hasbro's and Republic Services' revenue rows, 57 replaced by 59, for 144,134 facts.

### Independent QA

An independent QA review sampled 40 Phase B companies and checked them against SEC's raw data without relying on the
ingest's own audit; fixes 3 to 6 answer its findings. A second round sampled 40 more and scanned the whole snapshot;
fixes 7 to 9 answer it, and its scripts, re-run on the fixed data, return the true value or `?` for all 24 facts it
found. Among sampled companies, those with a trust error fell from 5 of 40 in the first round to 2 of 40 in the
second. A third round re-derived 115 metric-periods, 39 fiscal labels and 20 verifier round trips for 20 fresh
companies and found no error on the core GAAP path. It also found four older trust-error classes: fixes 11 to 13
answer three of them, and its scripts, re-run on the fixed data, return the true value or `?` for each. The fourth,
stock splits the verifier missed (Carvana 5-for-1, CrowdStrike 4-for-1), is now dated from the companies' own XBRL
split facts ([`receipts/splits.py`](../receipts/splits.py), generated by `scripts/build_split_table.py`). The QA
reports stay local, and the samples are small: these are spot checks, not an accuracy estimate for all 499 companies.

### Known limits

- **SEC's companyfacts lag.** On 2026-10-31, SEC's companyfacts lacked 52 stored 10-Qs and 10-Ks at 40 companies, 43 of
  them the newest one or two quarters of 32 companies (17 utilities, 8 REITs, and ABT, C, CLX, HUM, JCI, V and VRSK).
  Since November 1 such a filing is read from its own XBRL instance (Coverage improvements, below).
- **Helper labels in the mapping prompt.** Muse Spark maps a release with a prompt that lists only the periods the store
  has labelled, so it can give a helper label to a period the store refused to label. `scripts/relabel_release_facts.py`
  then resets every release label to the store's, so a rejected label never reaches a receipt; values are unaffected.
- **Fiscal-year changes.** FedEx, FedEx Freight and Ferguson changed fiscal years, and their new-calendar quarters stay
  unlabelled, answering `?`, until the store learns the new year end. Ferguson moved its year end from July 31 to
  December 31, and its 10-Qs for the quarters ended October 31, 2025 and March 31, 2026 both declare fiscal 2026 Q1,
  so its calendar stops at Q4 FY2025 (July 31, 2025) although companyfacts list those 10-Qs: claims about its later
  quarters answer `?`, never a wrong verdict.
- **Trailing twelve months.** Release figures for trailing twelve months are not served.
- **Dividends per share.** A quarter's dividend comes only from a release or from a filed quarter that a neighbouring
  filed quarter confirms, so a fourth quarter, a raise not yet repeated and a quarter declared off-cycle answer `?`
  unless a release states the rate, among them Coca-Cola's Q4 2025 $0.51 and Pfizer's quarters.
- **Duke Energy releases.** The XBRL gate quarantined three of Duke's releases because their "Total operating revenues"
  (9,178M for Q1 2026) differ from its contract revenue (8,995M); Duke's revenue is now served from that comparison
  total (Coverage improvements, below), but those three files' other figures stay unread.
- **Noncontrolling interests tagged in part.** ProfitLoss stands for net income only where the whole NCI is known.
  Estée Lauder tags only its redeemable NCI for FY2025 and FY2026, so its XBRL net income for those years and their
  quarters answers `?`, and its Q4 FY2025 release net loss ($546M) is dropped.
- **Concepts a 10-K does not tag both ways.** 36 derived quarters at 19 companies are gone without a replacement (net
  income 11, operating cash flow 8, revenue 6, R&D 6, cost of revenue 5), because their components are filed under
  another concept than the total; a release figure answers 7 of them, and the others answer `?`.

### Reproduce

Reproduce from the local EDGAR and model caches. `data/archive` keeps every company's companyfacts and earnings exhibits
gzip-compressed, and the restore commands put them back in the EDGAR cache:

```bash
T=$(.venv/bin/python -c "import json; a, b = (json.load(open(f'eval/evidence_expansion/sp500-phase-{x}-20261031.json')) for x in 'ab'); print(*[c['ticker'] for c in a['pilot']['companies']], *[t for x in b['batches'] for t in x['tickers']])")
CIKS=$(.venv/bin/python -c "import json; print(*json.load(open('data/archive/companyfacts/index.json')))")
.venv/bin/python scripts/archive_companyfacts.py restore $CIKS
.venv/bin/python scripts/archive_exhibits.py restore $CIKS
.venv/bin/python -m receipts.ingest --tickers "${T// /,}" --quarters 8 --offline
.venv/bin/python -m receipts.press_release --tickers $T --quarters 4 --validate --offline --validation-out /tmp/nocap-validation.json
.venv/bin/python scripts/confirm_revenue.py $T
.venv/bin/python -m receipts.ingest --tickers "${T// /,}" --quarters 8 --offline
.venv/bin/python scripts/relabel_release_facts.py
.venv/bin/python scripts/gate_release_facts.py
.venv/bin/python -m receipts.ingest --tickers BAC,CRWD,GEN,GPN,HAS,TWLO,VRT --quarters 8 --offline
.venv/bin/python scripts/gate_release_facts.py
.venv/bin/python scripts/build_snapshot.py --no-model
.venv/bin/python scripts/build_split_table.py  # receipts/splits.py from data/archive and the snapshot; --check compares
```

`confirm_revenue.py` records the companies it confirms and those whose releases show contract revenue is the total,
and the second ingest serves their revenue.
`gate_release_facts.py` records in `data/derived_rejections.json` the derived quarters a company's own release outranks
and lists the companies to ingest again; after that ingest, its second pass changes nothing. Windows count back from the
run date (2026-10-31), and `--offline` fails on a missing cached input rather than fetching it. The restored archives
need several GB of free disk; Phase B re-archived each batch with `--delete-raw` once its gates passed.

The [Phase A](../eval/evidence_expansion/sp500-phase-a-20261031.json) and
[Phase B](../eval/evidence_expansion/sp500-phase-b-20261031.json) audit records keep every count above, per batch and
per company, with the constituent list's hash, every dropped or quarantined pilot figure, the reconciliation and the
seventeen fixes' row accounting. These are integration checks, not an extraction accuracy study. No held-out split was
evaluated for this expansion.

#### Latest-quarter top-up

`scripts/topup_latest_quarter.py` runs the same batch steps for the companies that filed after the snapshot was built:
a 10-Q, 10-K or Item 2.02 earnings 8-K accepted after the snapshot's newest filing for that company. The dry run reads
cached Submissions only (no SEC request, no model call) and prints the plan, the batches and the mapping-cost estimate.
The second command records the dev-replay baseline on the untouched data, and the third is the real run:

```bash
.venv/bin/python scripts/topup_latest_quarter.py --auto --dry-run --out-dir /tmp/nocap-topup --max-ledger-usd 70
.venv/bin/python scripts/topup_latest_quarter.py --record-dev-baseline /tmp/nocap-topup/dev-baseline.json
SEC_USER_AGENT="Your Name your.email@example.com" .venv/bin/python scripts/topup_latest_quarter.py --auto \
  --out-dir /tmp/nocap-topup --dev-baseline /tmp/nocap-topup/dev-baseline.json --max-ledger-usd 70
```

Put your real SEC contact in `SEC_USER_AGENT`; the driver refuses the placeholder and never writes the value anywhere.
Cached Submissions predate the newest filings, so `--plan-only` in place of `--dry-run` (with the contact set) refetches
them from SEC, without a model call or any ingest, and prints the plan the real run would follow.

The real run refetches stale Submissions and plans again, then checks the split table, the dev replay and a scratch
snapshot on the untouched data. Each batch of up to 50 companies goes through the disk floor, the spend guard (ledger
plus 1.15 times the estimate within `--max-ledger-usd`), row hashes, ingest with `--refresh` in the stored window,
mapping with `--validate` and the XBRL gate, revenue confirmation, the label and release gates, the old-rows proof, the
dev replay, a scratch snapshot, `build_split_table.py --check` and the archives. The first failure stops the run with
that step's exit code (10 to 22, or 3 before batch 1). Reports and logs stay in `--out-dir`, with relative paths and the
User-Agent redacted, and a re-run with the same `--out-dir` skips the batches that passed. `--tickers` replaces `--auto`
to re-run named companies, such as those whose companyfacts lagged. The driver never rebuilds `data/snapshot`: after the
last batch, run `scripts/build_snapshot.py --no-model`, `scripts/build_split_table.py` and the test suite.

## Balance-sheet figures

On 2026-11-01, NoCap added the balance sheet: cash and cash equivalents, total assets, total liabilities,
shareholders' equity, long-term debt, total debt and shares outstanding, each at a 10-Q's or 10-K's balance-sheet date
([`receipts/balance_sheet.py`](../receipts/balance_sheet.py)). Before, a draft's "cash and cash equivalents of $34.5
billion" or "total debt of $28 billion" returned `?`, because no such metric existed. The step made no SEC request
and no model call: it reads the archived companyfacts (`data/archive/companyfacts`, each checked against the sha256 its
index records) and keeps only figures from the 10-Qs and 10-Ks the store already holds, so balance sheets come from
the same filings as every other XBRL fact.

| Item | XBRL concept (per filing and date, the first one tagged wins) | A mismatch is |
|---|---|---|
| Cash and cash equivalents | CashAndCashEquivalentsAtCarryingValue; a company that tags it nowhere in the window gets cash, cash equivalents and restricted cash, named so | `✗` (`?` on the restricted-inclusive total) |
| Total assets | Assets | `✗` |
| Total liabilities | Liabilities | `✗` |
| Shareholders' equity | StockholdersEquity (the parent's, never including noncontrolling interests) | `✗` |
| Long-term debt | LongTermDebtNoncurrent (or LongTermDebtAndCapitalLeaseObligations), and LongTermDebt with its current portion | `?` |
| Total debt | DebtLongtermAndShorttermCombinedAmount (or DebtAndCapitalLeaseObligations) | `?` |
| Shares outstanding | CommonStockSharesOutstanding at the balance-sheet date, and dei:EntityCommonStockSharesOutstanding as of the cover-page date | `?` |

Debt is only what a company tags as a total: NoCap never adds borrowings up, so total debt answers `?` at a company
that tags no total. A bare "cash", debt and share counts are reported in several ways (with marketable securities, with
or without the current portion, as of different dates), so a figure that matches none of the filed ones answers `?`
with them as receipts, never `✗`. Every version of a date counts, as for the other facts, but a later comparative
that repeats a value, or writes it more coarsely (Apple's 10-Qs tag its FY2025 long-term debt as $90.7 billion beside
the 10-K's $90.678 billion), is not stored again. A filing that gives one concept two values for a date, a negative
value of an item that is never negative, cash above total assets and total assets that the same balance sheet's total
of liabilities and equity contradicts are left out; across all 499 companies, none of these occurred.

A figure is read as a balance-sheet item only from its own words, in metric_text and in the quote right around the
number; extraction's schema and prompt are unchanged, and such figures arrive as `other`. "Free cash flow", "cash
returned", "net debt", "total current assets", "assets under management", "diluted shares outstanding", "cash and
marketable securities", a change ("cash rose $4 billion") and a comparison base ("up from $29.9 billion") stay `?`.
The date is the balance-sheet date the claim's period names on the company's fiscal calendar: a written date, the end
of the period ("at the end of the quarter"), a year end ("at the end of the prior fiscal year"; "at year-end" only in a
story about a fiscal year or its fourth quarter) or "a year ago". The start of a period, an average, "the prior year"
and a period the quote's own words contradict answer `?`, and as_of and look-ahead work as for every other figure.

The facts are rebuilt in every snapshot (`scripts/build_snapshot.py` reads `data/archive/companyfacts`); `python -m
receipts.balance_sheet` adds them to `data/facts.sqlite` for local use. On the data of 2026-11-01 they are **39,619
facts at 498 companies** (6,027 cash, 6,156 total assets, 4,438 total liabilities, 5,763 equity, 2,577 long-term debt
with and 4,473 without its current portion, 1,198 total debt, 8,987 share counts; VYLR, a new registrant, has none), and
64 companies, the large banks among them, have cash only with restricted cash. They add 7.2 MB to `facts.sqlite`
(37.0 to 44.2 MB) and about 12 s to the build; a scratch snapshot measured 94.6 MB against the 112 MiB limit, and
`/api/health` counts 280,902 facts. With them, the 693-claim dev replay is unchanged, on the frozen 62-company data and
on that data with their balance sheets added (the dev corpus holds no balance-sheet figure), the saved examples score
7/2/0/2, 5/3/0/0 and 6/1/2/0 as before, and an offline probe of 65 random companies' latest balance sheets verified
every true cash and total-assets figure (26 cash figures through the restricted-inclusive total), marked every total
assets figure 10% off `✗` and every cash figure 10% off `✗` (or `?` where only the restricted-inclusive total is tagged),
and read no free-cash-flow sentence as a balance.

## Coverage improvements, November 1

After the S&P 500 expansion, these changes closed gaps that its known limits listed. Each passed the same gates as a
Phase B batch: pytest, the 693-claim dev replay identical to its reference, the saved examples (7/2/0/2, 5/3/0/0,
6/1/2/0), the three recordings and the qualifier grid unchanged on a scratch snapshot, the label gate, the release gates
and the XBRL gate re-run, canonical row hashes showing every changed row explained, and the independent QA kit on the
companies each touched. Decisions that a company's own releases must confirm are recorded with their evidence in
`data/revenue_confirmations.json`, and a later release that contradicts one revokes it. Together they made 122 live SEC
requests and 40 Muse Spark 1.3 calls ($1.14, Standard mode), moving the press-release ledger from $49.74 to $50.88.

- **Net income as ProfitLoss less the noncontrolling interest.** Where a filing tags ProfitLoss and its whole
  noncontrolling interest for a period but no NetIncomeLoss, net income is their difference, stored as derived with
  the arithmetic as its receipt: 764 figures at 35 companies, Mastercard's Q3 2026 $4,620M ($4,628M less $8M) among
  them. Each matched companyfacts exactly.
- **Net income after preferred dividends.** Invesco's releases report "Net income/(loss) attributable to Invesco Ltd."
  after preferred dividends. Where a company's releases equal NetIncomeLossAvailableToCommonStockholdersBasic in at
  least two periods in which it differs from the other definitions, and equal those in none, that figure is its net
  income, and the receipt names it. Only Invesco qualifies: its Q2 2025 net loss of $12.5M now verifies, and $203.4M
  (ProfitLoss less the noncontrolling interest) is `✗`; its 13 release figures for that net income, which the gates had
  dropped against ProfitLoss, are served again.
- **A comparison total as revenue.** Duke, Xcel and Sempra tag their "Total operating revenues" as
  RegulatedAndUnregulatedOperatingRevenue. Where a company's release totals equal it in at least two periods and differ
  in none, it is served as revenue for the periods whose filing tags no broader revenue: 81 figures, Duke's Q1 2026
  $9,178M among them.
- **Bank revenue.** Where a bank's release totals equal net interest income plus noninterest income, both filed by one
  filing for one period, in at least two periods and differ in none, their sum is its revenue where the filing tags no
  broader revenue. Thirteen banks qualify; BNY, Regions, Truist and Northern Trust gain 119 figures (Regions' Q2 2025
  $1,905M). Fifth Third's releases print only a taxable-equivalent total, which is not GAAP, so its revenue stays `?`.
- **Exxon Mobil.** ExxonMobil Holdings Corp (CIK 2115436) became XOM's registrant on 2026-07-01; Exxon Mobil Corp (CIK
  34088) filed every earlier report. `receipts.ingest.PREDECESSOR_CIKS` merges a predecessor registrant's filings and
  companyfacts into the company's one row, each filing keeping the URL of a registrant that lists it. XOM is now the
  500th company: 231 XBRL and derived facts over 12 quarters, one fiscal calendar across the switch, and the 13 figures
  both registrants report (the Q2 2025 comparatives) identical.
- **Release tables under a rowspan.** The release-table grid ignored rowspan, so a header row under a unit label that
  spans two rows was read one column to the left. The XBRL gate had quarantined such files when their GAAP rows met
  XBRL (Exxon Mobil's four), but where the moved rows were segment or non-GAAP figures, or the period's 10-Q was not in
  companyfacts, they were served: Dow's Q2 2025 diluted EPS of -$1.18 as Q2 2026's, and Coca-Cola's and Cboe's segment
  revenue with the years exchanged. A one-off gate (`scripts/gate_rowspan_headers.py`) re-read every live release file
  as HTML lays it out and dropped 39 served facts that XBRL proves were read under another period, and 14 that no
  reading can vouch for. With the grid fixed (`a40299d`), `scripts/remap_releases.py` re-mapped those files, XOM's and
  the 22 other quarantined files the scan named (35 files, $1.04): 11 quarantined files are served again.
- **16-week quarters.** Costco's, Domino's and PepsiCo's 16-week release quarters were stored with 13-week dates and
  dropped by the label gate; re-dated from the fiscal calendar, 47 of their figures are back, and Kroger's 22 first-
  quarter figures carry their 16-week dates and labels.
- **Filings SEC's companyfacts lack.** Fifty-three stored 10-Qs and 10-Ks at 40 companies were missing from SEC's
  companyfacts, so their quarters had no XBRL facts and no fiscal label. `scripts/fetch_filing_xbrl.py` keeps the XBRL
  instance SEC extracts from each (`data/archive/filing_xbrl`, 50 of them; the other three are cover-page-only
  amendments), and `receipts.filing_xbrl` reads it as companyfacts list a filing; on two filings that companyfacts do
  list it reproduced all 348 and 253 of their entries. Ingest merges a kept filing only where companyfacts lack it. The
  38 companies gain 1,511 facts, and each one's calendar reaches its newest 10-Q or 10-K: AEP's Q2 2026 revenue of
  $5,445.0M and Sempra's of $2,997.0M now verify against their 10-Qs. Essex's 10-Q reports total revenues of $492.7M
  for Q3 2026, so a draft that gives Essex's revenue as $490.3M, the release's correct figure for "Total rental and
  other property revenues" (which leaves out management fees), is `✗`, and $492.7M verifies.
- **The XBRL gate compares with the store's choice.** The mapper's paired-filing gate compared a release with the first
  concept its 10-Q tags, so a newly readable 10-Q would have made it drop AEP's attributable net income (the 10-Q tags
  ProfitLoss; the store derives ProfitLoss less the noncontrolling interest) and Sempra's and Duke's revenue (contract
  revenue first; the store serves the comparison total their releases confirm), and hold back a Duke release. A figure
  that equals the one the store serves for the period now passes (`receipts.press_release.store_values`); one that
  equals neither is dropped as before, and the gate re-run over all 1,861 live release files drops and holds back
  nothing.

- **Exxon Mobil's earlier balance sheets.** Balance-sheet facts, built from companyfacts when a snapshot is built
  (`receipts.balance_sheet`), now merge the predecessor registrant's companyfacts too, so XOM's balance sheets from CIK
  34088's filings are served with their original filing and acceptance time: XOM went from 16 to 82 balance-sheet
  facts, and an offline build over all 500 companies changed no other company's.

After these changes the store holds **500 companies**, 28,607 filings, **146,954 XBRL and derived facts** and **96,885
earnings-release facts**, and the snapshot adds **39,701 balance-sheet facts** and 7,572 evidence-only alternative
net-income figures (5,267 consolidated including noncontrolling interests, 2,305 to common stockholders), each stored
only where it differs from the served net income: 97.68 MB, fingerprint `1381a6507b55` (commit `66d4823`). The independent QA kit reads the kept instances with its own reader, which agrees with
`receipts.filing_xbrl` on all 20,407 of their entries.
