"""Read-only independent reconciliation of the expanded dev response caches and ledger.

Uses Decimal arithmetic and the published token prices, not extraction's cost helper.
Never imports a model SDK or calls an endpoint.
"""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def audit(path: Path) -> dict:
    result = json.loads(path.read_text())
    fixed_path = ROOT / "eval/expanded_dev_manifest.json"
    fixed = json.loads(fixed_path.read_text())
    assert hashlib.sha256(fixed_path.read_bytes()).hexdigest() == result["manifest_sha256"]
    assert result["source_hashes"] == result["source_hashes_after"], "Verifier changed during run"
    rows = result["metering"]["attempts"]
    baseten = next(run for run in result["runs"] if run["target"] == "spark-open-baseten")
    response_cache = {rec["request_sha256"]: rec for rec in baseten["response_cache_records"]}
    total, tokens = Decimal(0), Counter()
    successful, estimated, dispatched = 0, 0, 0
    for row in rows:
        dispatched += row["started"]
        if row["cost_is_estimate"]:
            assert row["charge_usd"] == row["estimate_usd"]
            estimated += bool(row["started"])
            total += Decimal(str(row["charge_usd"]))
            continue
        if not row["started"]:
            assert row["charge_usd"] == 0
            continue
        u = row["usage"]
        actual = ((Decimal(u["input_tokens"] - u["cached_tokens"]) * Decimal("1.25")
                   + Decimal(u["cached_tokens"]) * Decimal("0.15")
                   + Decimal(u["output_tokens"]) * Decimal("4.25")) / Decimal(1_000_000))
        assert abs(actual - Decimal(str(row["charge_usd"]))) < Decimal("0.0000000001")
        if row["status"] == "completed":
            successful += 1
            cache = response_cache[row["request_sha256"]]
            assert cache["usage"] == u
        total += actual
        tokens.update(u)
    assert total == Decimal(str(result["metering"]["charge_usd"]))
    assert dict(tokens) == result["metering"]["tokens"]
    assert dispatched == result["metering"]["calls"] <= 60
    assert estimated == result["metering"]["estimated_calls"]
    assert total <= Decimal("3.00")
    checks = {}
    for run in result["runs"]:
        expected_ids = {r["item_id"] for r in fixed["items"]}
        assert set(run["records"]) == expected_ids
        for rec in run["response_cache_records"]:
            raw = ROOT / run["cache_dir"] / (rec["request_sha256"] + ".json")
            assert hashlib.sha256(raw.read_bytes()).hexdigest() == rec["cache_sha256"]
        checks[run["target"]] = {"items": len(run["records"]),
                                 "errors": sum(bool(r["error"]) for r in run["records"].values()),
                                 "cache_hits": run["application_cache_hits"],
                                 "cache_misses": run["application_cache_misses"],
                                 "unique_responses": len(run["response_cache_records"])}
    return {"record_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "passed": True, "source_unchanged_during_run": True,
            "guard_dispatched_attempts": dispatched, "successful_usage_records": successful,
            "retained_estimate_attempts": estimated, "tokens": dict(tokens),
            "independent_usage_priced_usd": str(total), "targets": checks,
            "prices_per_million": {"input": "1.25", "cached_input": "0.15", "output": "4.25"},
            "pricing_source": "https://www.baseten.co/library/muse-spark-open/",
            "price_checked_on": "2026-10-29", "new_model_calls_for_audit": 0,
            "limitation": "SDK usage at published prices; not a provider invoice or remaining credit balance."}


if __name__ == "__main__":
    report = audit(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        with Path(sys.argv[2]).open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    print(json.dumps(report, indent=2))
