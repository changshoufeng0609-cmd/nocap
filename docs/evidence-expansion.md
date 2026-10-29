# Evidence expansion: Tesla

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
