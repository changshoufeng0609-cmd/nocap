"""Public API: check a draft end to end.

    from receipts.check import check
    for claim, verdict in check(draft_text, as_of="2026-08-26T20:30:00Z"):
        print(verdict.status, claim.value_text, verdict.explanation)

Muse Spark proposes the claims (receipts.extract); deterministic code decides every verdict
(receipts.verify) against the EvidenceStore, which defaults to receipts.store.Store().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from receipts import extract as _extract
from receipts import verify as _verify
from receipts.coverage import numeric_coverage
from receipts.types import Claim, EvidenceStore, Verdict

log = logging.getLogger(__name__)


def default_store() -> EvidenceStore:
    """receipts.store.Store(), or a clear error when the evidence layer isn't available."""
    try:
        from receipts.store import Store  # built by the evidence agent; may not exist yet
    except ImportError as e:
        raise RuntimeError("No evidence store: receipts.store is not available yet. Build it (receipts.ingest "
                           "writes data/facts.sqlite) or pass store=... to check().") from e
    try:
        return Store()
    except Exception as e:  # e.g. data/facts.sqlite not built yet
        raise RuntimeError(f"Could not open the evidence store ({type(e).__name__}: {e}). Build data/facts.sqlite "
                           f"with receipts.ingest, or pass store=... to check().") from e


@dataclass
class CheckResult:
    results: list[tuple[Claim, Verdict]]
    context: Optional[str]
    as_of: Optional[datetime]
    warnings: list[str] = field(default_factory=list)
    extraction: Optional[_extract.ExtractResult] = None
    coverage: Optional[dict[str, Any]] = None


def check_detailed(text: str, *, as_of: Any = None, context: Optional[str] = None,
                   store: Optional[EvidenceStore] = None, **extract_kwargs: Any) -> CheckResult:
    """check() plus the context line used, extraction warnings and per-call cost.

    context=None infers the line from the first passage; context="" uses none. extract_kwargs go to
    receipts.extract.extract_detailed (client, cache_dir, use_cache, ...).
    """
    as_of_dt = _verify.to_utc(as_of)
    store = store if store is not None else default_store()  # fail before spending on the model API
    if "extra_cache_dirs" not in extract_kwargs and hasattr(store, "db_path"):
        extract_kwargs["extra_cache_dirs"] = [store.db_path.parent / "spark_cache"]
    ctx = _extract.infer_context(text) if context is None else (context.strip() or None)
    extraction = _extract.extract_detailed(text, ctx if ctx is not None else "", **extract_kwargs)
    results: list[tuple[Claim, Verdict]] = []
    named = _verify.named_in_draft(text, store)  # the only companies a wrong figure may be attributed to
    for claim in extraction.claims:
        try:
            verdict = _verify.verify(claim, store, as_of=as_of_dt, context=ctx, draft=text,
                                     **({"named": named} if named else {}))
        except Exception as e:  # one bad claim must not sink the whole draft
            log.exception("verify failed for %s", claim.id)
            verdict = Verdict(claim_id=claim.id, status="unverifiable", reason="no_evidence", as_of=as_of_dt,
                              explanation=f"NoCap hit an internal error ({type(e).__name__}) checking this claim.")
        results.append((claim, verdict))
    return CheckResult(results, ctx, as_of_dt, list(extraction.warnings), extraction,
                       numeric_coverage(text, extraction.claims))


def check(text: str, *, as_of: Any = None, context: Optional[str] = None,
          store: Optional[EvidenceStore] = None) -> list[tuple[Claim, Verdict]]:
    """Every numeric claim in `text` with its verdict, in document order.

    as_of: datetime or ISO time; evidence accepted after it counts as look-ahead.
    context: one line naming the company and fiscal period, e.g. "NVIDIA Q2 FY2027"; inferred when None.
    store: an EvidenceStore; defaults to receipts.store.Store().
    """
    return check_detailed(text, as_of=as_of, context=context, store=store).results
