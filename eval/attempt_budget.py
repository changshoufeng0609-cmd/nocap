"""Process-local, persisted per-attempt admission for explicitly authorized dev runs.

Adapted from the audited Baseten wrapper in data/eval_runs. This is an estimated
admission limit, not a provider billing ceiling. No request text or secrets persist.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
from typing import Any

from receipts import extract as ex


class AttemptBudget:
    def __init__(self, path: Path, *, backend: ex.Backend, allowed_keys: set[str],
                 cap_usd: float = 3.0, max_attempts: int = 60):
        if not math.isfinite(cap_usd) or not 0 <= cap_usd <= 3 or not 1 <= max_attempts <= 60:
            raise ValueError("Expanded dev authorization is at most $3 and 60 attempts.")
        if path.exists():
            raise FileExistsError("Refusing to overwrite an attempt ledger.")
        self.path, self.backend, self.allowed_keys = path, backend, allowed_keys
        self.cap_usd, self.max_attempts = cap_usd, max_attempts
        self.lock, self.rows = threading.RLock(), []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.original = ex.RequestUsage.call
        self._save()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {"started_at": self.started_at, "cap_usd": self.cap_usd,
                    "max_attempts": self.max_attempts,
                    "charge_usd": round(sum(r["charge_usd"] for r in self.rows), 8),
                    "calls": sum(r["started"] for r in self.rows),
                    "estimated_calls": sum(r["started"] and r["cost_is_estimate"] for r in self.rows),
                    "tokens": {k: sum(r.get("usage", {}).get(k, 0) for r in self.rows)
                               for k in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")},
                    "attempts": [dict(r) for r in self.rows]}

    def _save(self):
        # Atomic replace: a failed write prevents dispatch; after dispatch the previous
        # conservative reservation remains on disk even if settlement cannot be saved.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.summary(), indent=2) + "\n")
        tmp.replace(self.path)

    def call(self, meter, fn, request):
        key = ex.request_key(request, meter.backend)
        if (meter.backend != self.backend or self.backend.name != "baseten"
                or self.backend.base_url != "https://inference.baseten.co/v1"
                or request.get("model") != "meta-models/muse-spark-open-1026"
                or request.get("max_tokens") != 12000 or key not in self.allowed_keys):
            raise ex.ModelCallRejected("Only the fixed expanded-dev Baseten requests are admitted.")
        estimate = ex.estimate_request_cost(request, self.backend)
        with self.lock:
            if (len(self.rows) >= self.max_attempts
                    or sum(r["charge_usd"] for r in self.rows) + estimate > self.cap_usd):
                raise ex.ModelCallRejected("Expanded-dev admission limit reached.")
            row = {"attempt": len(self.rows) + 1, "request_sha256": key, "started": False,
                   "estimate_usd": estimate, "charge_usd": estimate,
                   "cost_is_estimate": True, "status": "reserved"}
            self.rows.append(row)
            self._save()

        def dispatch():
            with self.lock:
                row.update(started=True, status="dispatched")
                self._save()
            return fn()

        try:
            response = self.original(meter, dispatch, request)
        except BaseException as error:
            with self.lock:
                row["status"] = type(error).__name__
                if not row["started"]:
                    row.update(charge_usd=0, cost_is_estimate=False)
                self._save()
            raise
        usage = ex._usage(response)
        with self.lock:
            row["status"] = "completed"
            if usage is not None:
                row.update(usage=usage, charge_usd=ex.cost_usd(usage, self.backend.prices), cost_is_estimate=False)
            self._save()
        return response

    @contextmanager
    def installed(self):
        if ex.RequestUsage.call is not self.original:
            raise RuntimeError("Another request guard is already installed.")
        guard = self

        def guarded(meter, fn, request):
            return guard.call(meter, fn, request)

        ex.RequestUsage.call = guarded
        try:
            yield self
        finally:
            ex.RequestUsage.call = self.original
            with self.lock:
                self._save()
