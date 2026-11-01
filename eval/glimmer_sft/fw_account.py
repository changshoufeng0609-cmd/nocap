"""Read-only Fireworks checks for the Glimmer SFT experiment: the key's account, the base model, billing line items.

Reads FIREWORKS_API_KEY (and optional FIREWORKS_ACCOUNT_ID) from the process environment only; never prints them.
No call here creates, trains, samples or deploys anything.

    python eval/glimmer_sft/fw_account.py --out <scratch>/account_before.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API = "https://api.fireworks.ai"
BASE_MODEL = "accounts/fireworks/models/muse-glimmer-30b"
PROMOTION_DEADLINE = datetime(2026, 12, 1, tzinfo=timezone(timedelta(hours=-8)))  # LoRA SFT jobs started before


def api_key() -> str:
    key = os.environ.get("FIREWORKS_API_KEY", "").strip()
    if not key:
        raise SystemExit("FIREWORKS_API_KEY is not set in this process's environment; export it first "
                         "(this script does not read .env files). Nothing was sent.")
    return key


def redact(text: str) -> str:
    key = os.environ.get("FIREWORKS_API_KEY", "")
    return text.replace(key, "***") if key else text


def _get(path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
    import httpx

    r = httpx.get(f"{API}{path}", params=params, timeout=60, headers={"Authorization": f"Bearer {api_key()}"})
    try:
        body = r.json()
    except ValueError:
        body = {"text": redact(r.text[:500])}
    return r.status_code, body


def account_id() -> str:
    explicit = os.environ.get("FIREWORKS_ACCOUNT_ID", "").strip()
    if explicit:
        return explicit
    status, body = _get("/v1/accounts", {"pageSize": "1"})
    accounts = body.get("accounts") if isinstance(body, dict) else None
    if status != 200 or not accounts:
        raise SystemExit(f"could not resolve the key's account (HTTP {status}); set FIREWORKS_ACCOUNT_ID")
    return accounts[0]["name"].split("/")[-1]


def billing_summary(account: str, start: datetime, end: datetime) -> dict[str, Any]:
    status, body = _get(f"/v1/accounts/{account}/billing/summary",
                        {"startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "granularity": "DAILY"})
    return {"http_status": status, "body": body}


def model_info(name: str = BASE_MODEL) -> dict[str, Any]:
    status, body = _get(f"/v1/{name}")
    keep = ("name", "displayName", "state", "kind", "contextLength", "supportsLora", "supportsServerless", "tunable",
            "baseModelDetails", "public", "trainingPricing", "pricing", "deprecationDate")
    return {"http_status": status, **({k: body[k] for k in keep if k in body} if isinstance(body, dict) else {})}


def snapshot() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    account = account_id()
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {"checked_utc": now.isoformat(timespec="seconds"), "account": account,
            "promotion_window_open": now < PROMOTION_DEADLINE, "promotion_deadline": PROMOTION_DEADLINE.isoformat(),
            "base_model": model_info(),
            "billing_month_to_date": billing_summary(account, month, now + timedelta(hours=1))}


def idle_check(session_id: str, model_id: str | None) -> dict[str, Any]:
    """Read-only: is anything left that could bill? Deployments in any state, the training session, the model."""
    account = account_id()
    out: dict[str, Any] = {"checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    status, body = _get(f"/v1/accounts/{account}/deployments", {"pageSize": "200"})
    deps = body.get("deployments", []) if isinstance(body, dict) else []
    out["deployments"] = {"http_status": status, "count": len(deps),
                          "items": [{"name": d.get("name", "").replace(account, "<account>"), "state": d.get("state"),
                                     "baseModel": str(d.get("baseModel", "")).replace(account, "<account>")}
                                    for d in deps]}
    status, body = _get(f"/v1/accounts/{account}/trainingSessions/{session_id}")
    out["training_session"] = {"http_status": status,
                               "state": body.get("state") if isinstance(body, dict) else None}
    status, body = _get(f"/v1/accounts/{account}/trainingSessions", {"pageSize": "50"})
    sessions = body.get("trainingSessions") if isinstance(body, dict) else None
    out["training_sessions_list"] = {"http_status": status,
                                     "items": [{"name": s.get("name", "").replace(account, "<account>"),
                                                "state": s.get("state")} for s in (sessions or [])]}
    if model_id:
        status, body = _get(f"/v1/accounts/{account}/models/{model_id}")
        out["model"] = {"http_status": status, **({k: body.get(k) for k in ("state", "kind", "deployedModelRefs")}
                                                  if isinstance(body, dict) else {})}
    ended = {"DELETED", "DELETING", "STOPPED", "FAILED"}
    out["deployment_running"] = any((d.get("state") or "").upper() not in ended for d in deps)
    out["session_alive"] = out["training_session"]["http_status"] == 200 and \
        (out["training_session"]["state"] or "").upper() not in ("DELETED", "EXPIRED", "CLOSED", "STATE_DELETED")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--idle-check", metavar="SESSION_ID", help="report deployments, this training session and --model")
    ap.add_argument("--model", metavar="MODEL_ID", help="with --idle-check: a promoted model id to look up")
    args = ap.parse_args(argv)
    if args.idle_check:
        report = idle_check(args.idle_check, args.model)
        args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=1)[:4000])
        return 1 if report["deployment_running"] or report["session_alive"] else 0
    snap = snapshot()
    args.out.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
    shown = {**snap, "account": "<redacted; see the file>"}
    print(json.dumps(shown, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
