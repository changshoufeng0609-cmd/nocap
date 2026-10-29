# Development history and source provenance

NoCap development began on **October 27, 2026**. The sole contributor is **SHOU-FENG CHANG
(`Robert_Chang`)**, known as Robert Chang. The earlier Taiwan portfolio-agent project supplied the
motivation only; no code from another repository was copied into this implementation.

The public `main` branch is a clean release export, initially published on October 29 after narration
licensing cleanup. That initial export date is not the date every feature was written. The original
incremental development history remains intact in the private development repository.

## Filtered source-history export

The [public source-history branch](https://github.com/changshoufeng0609-cmd/nocap/tree/source-history)
contains **65 reconstructed commits**, ending at `adaf6ba849b561e07a22367815a35a00a69f7296`,
with **104 source/configuration files** in its final tree. It corresponds to private source checkpoint
`61e10552409419d940735f91dd41f139a088c992` after the J2 fixes.
The [complete original-to-export mapping](source-history-map.json) has SHA-256
`431ac56068010ba975b02ae54dd27ecd5fbddc0c174981e4b03d88e2b61b4f27`.
Recorded author dates begin at October 27, 15:25:30 Taipei; the last recorded commit is October 29,
19:03:01 Taipei. The reviewed exporter is
[`scripts/export_source_history.py`](../scripts/export_source_history.py). It reconstructs a separate
first-parent history using a strict source-path allowlist:

- Python/TypeScript/JavaScript/CSS source and necessary project configuration are included.
- Source blob bytes, original author names and author/commit times are preserved.
- Git author and committer email fields are replaced with `source-history@nocap.invalid`.
- Media, evidence data, documentation directories, environment files and obsolete narration are excluded.
- A manifest maps each private original commit to its new exported commit and records every file's Git
  blob ID and SHA-256. Every reconstructed tree is checked against its original source blob IDs.

These are **reconstructed commits, not the original commit objects**. Git dates and author names are
self-reported repository metadata, not independent timestamp attestation. Emails or other text inside
source bytes, author names or commit subjects are not automatically rewritten. Known credential values
and token patterns are scanned without printing them; this does not guarantee detection of every unknown
credential. The exporter never pushes, rewrites the private history or publishes old media.

## Release and media

The [release export record](release-provenance.json) binds a released source checkpoint and archive hash.
The [demo record](demo/README.md) separately binds the current 165-second continuous browser walkthrough,
18 English cues, local Kokoro narration and the exact disclosed recorded responses. Third-party
libraries, filings and model weights retain their respective licenses and provenance.
