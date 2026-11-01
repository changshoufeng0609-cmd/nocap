"""Record NoCap's built-in samples through the real API, for $0 replays (receipts.recordings).

    .venv/bin/python scripts/record_samples.py                  # list samples and which are recorded; no calls
    .venv/bin/python scripts/record_samples.py --confirm-spend  # record the missing ones with live Meta models
    .venv/bin/python scripts/record_samples.py --private --confirm-spend  # the Private sample, on Modal Glimmer

Slide, quote and Private samples are defined once in web/lib/samples.json, which the web client also reads, so a
replayed request is byte-for-byte the request the client sends. Each run goes through api.create_app with live requests
enabled, the local ledger and the ordinary guards; nothing is mocked. Recordings are written to
data/recordings/<kind>/ and copied into data/snapshot/recordings by scripts/build_snapshot.py. An existing
recording is never overwritten. --max-usd stops before the next sample once observed spend reaches the cap.

--private records only the Private samples (the public synthetic draft of the 2026-10-29 Private acceptance)
through POST /api/private/check with private mode on: the draft reaches only the operator's Modal Glimmer server
(receipts.private), and the hosted-model keys are blanked in this process. Deploy the server first, and stop it
afterwards (docs/open-weights.md):

    .venv/bin/modal deploy deploy/modal_glimmer.py
    .venv/bin/python scripts/record_samples.py --private --confirm-spend
    .venv/bin/modal app stop receipts-glimmer --yes

Its verdicts use data/snapshot, the evidence the deployed site serves, so the recording's data_fingerprint is the
snapshot's content fingerprint. The endpoint comes from the deployed Server unless RECEIPTS_MODAL_BASE_URL is
exported. The script waits up to --warm-timeout seconds for the server's /health before sending one request; it
never stops the app. Modal bills GPU time for the whole deployment, warm-up and idle time included, and --max-usd
does not see it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from receipts import api, ask, recordings, stream  # noqa: E402

SAMPLES = REPO / "web/lib/samples.json"
PUBLIC = REPO / "web/public"
OUT = REPO / "data/recordings"
SERVER = REPO / "deploy/modal_glimmer.py"
MODAL_APP, MODAL_CLASS, MODAL_WEIGHTS = "receipts-glimmer", "Glimmer", "receipts-glimmer-hf"  # as in SERVER
HOSTED_KEYS = ("MODEL_API_KEY", "FIREWORKS_API_KEY", "BASETEN_API_KEY")


def parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.split("\n\n"):
        name, data = None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if name and data:
            events.append({"event": name, "data": json.loads("\n".join(data))})
    return events


def plan() -> list[dict[str, Any]]:
    """Every sample as (kind, key, endpoint, body, request descriptor), keyed exactly as the API keys it."""
    spec = json.loads(SAMPLES.read_text(encoding="utf-8"))
    out = []
    for sample in spec.get("slides", []):
        path = PUBLIC / sample["image"].lstrip("/")
        mime = mimetypes.guess_type(path.name)[0]
        data_url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        context = stream.normalize_context(sample.get("context"))
        as_of = stream.canonical_as_of(sample.get("as_of"))
        key = recordings.slide_key(data_url, context, as_of)
        body = {"image_data_url": data_url, "context": sample.get("context"), "as_of": sample.get("as_of")}
        out.append({"kind": "slide", "key": key, "endpoint": "/api/slide", "body": body,
                    "request": {"sample_id": sample["id"], "image": sample["image"],
                                "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "context": context, "as_of": as_of, "source": sample.get("source")}})
    for sample in spec.get("filing_questions", []):
        question = ask.validate_question(sample["question"])
        as_of = stream.canonical_as_of(sample.get("as_of"))
        key = recordings.grounding_key(sample["filing_id"], question, as_of)
        body = {"question": sample["question"], "filing_id": sample["filing_id"], "as_of": sample.get("as_of")}
        out.append({"kind": "grounding", "key": key, "endpoint": "/api/grounding", "body": body,
                    "request": {"filing_id": sample["filing_id"], "question": question, "as_of": as_of}})
    for sample in spec.get("private", []):  # the exact draft is the key: recorded claim spans index into it
        context = stream.normalize_context(sample.get("context"))
        as_of = stream.canonical_as_of(sample.get("as_of"))
        key = recordings.private_key(sample["text"], context, as_of)
        body = {"text": sample["text"], "context": sample.get("context"), "as_of": sample.get("as_of")}
        out.append({"kind": "private", "key": key, "endpoint": "/api/private/check", "body": body,
                    "request": {"sample_id": sample["id"], "text": sample["text"], "context": context,
                                "as_of": as_of}})
    return out


def succeeded(kind: str, events: list[dict[str, Any]]) -> bool:
    names = [e["event"] for e in events]
    return "error" not in names and ("result" in names if kind == "grounding" else "done" in names)


def spend(events: list[dict[str, Any]]) -> float:
    final = [e["data"] for e in events if e["event"] in ("done", "result", "error")]
    return float(final[-1].get("cost_usd") or 0) if final else 0.0


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def sha256(path: str) -> str:
    return hashlib.sha256((REPO / path).read_bytes()).hexdigest()


def private_setup(warm_timeout_s: float) -> dict[str, Any]:
    """Point this process at the deployed Modal Glimmer server and nothing else, then wait until it is warm. Returns
    the server's provenance, kept with the recording. Exits without sending when the server isn't ready."""
    import runpy

    import httpx
    import modal

    from receipts import extract, private

    for name in HOSTED_KEYS:  # dotenv never overrides a variable that is set, so no hosted-model key loads here
        os.environ[name] = ""
    os.environ["RECEIPTS_PRIVATE_MODE"] = "1"
    if not os.environ.get("RECEIPTS_MODAL_BASE_URL", "").strip():
        url = modal.Server.from_name(MODAL_APP, MODAL_CLASS).get_url().rstrip("/")
        os.environ["RECEIPTS_MODAL_BASE_URL"] = url + "/v1"
    backend = private.configured_private_backend()  # PrivateUnavailable when misconfigured; nothing was sent
    token = extract._load_api_key(backend)
    health = backend.base_url[:-len("/v1")] + "/health"
    started, status = time.monotonic(), None
    with httpx.Client(timeout=20, follow_redirects=False) as http:
        while status != 200:
            if time.monotonic() - started > warm_timeout_s:
                raise SystemExit(f"Glimmer was not healthy within {warm_timeout_s:.0f} s; nothing was sent.")
            try:
                status = http.get(health, headers={"Authorization": f"Bearer {token}"}).status_code
            except httpx.TransportError:
                status = None
            if status not in (None, 200, 503):  # 503 while the container starts; anything else is a setup fault
                raise SystemExit(f"Glimmer /health answered HTTP {status}; nothing was sent.")
            if status != 200:
                time.sleep(5)
    warm_wait_s = round(time.monotonic() - started, 1)
    server = runpy.run_path(str(SERVER))  # the server definition, for its weights and vLLM flags; deploys nothing
    try:  # the Hugging Face revision vLLM served, from the weight cache the server mounts
        ref = f"hub/models--{server['MODEL_ID'].replace('/', '--')}/refs/main"
        revision = b"".join(modal.Volume.from_name(MODAL_WEIGHTS).read_file(ref)).decode().strip() or None
    except Exception:
        revision = None
    return {"backend": backend.name, "model": backend.model, "modal_app": MODAL_APP,
            "server": "deploy/modal_glimmer.py", "server_sha256": sha256("deploy/modal_glimmer.py"),
            "weights": server["MODEL_ID"], "weights_revision": revision, "vllm_command": server["server_command"](),
            "warm_wait_s": warm_wait_s}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--confirm-spend", action="store_true", help="send live requests for missing samples")
    parser.add_argument("--max-usd", type=float, default=1.0, help="stop once observed spend reaches this")
    parser.add_argument("--private", action="store_true",
                        help="record only the Private sample, on the deployed Modal Glimmer server")
    parser.add_argument("--warm-timeout", type=float, default=600.0,
                        help="with --private: seconds to wait for the server's /health before sending")
    args = parser.parse_args(argv)
    from fastapi.testclient import TestClient

    todo, missing = [], 0
    for item in plan():
        target = OUT / item["kind"] / f"{item['key']}.json"
        recorded = target.exists()
        wanted = (item["kind"] == "private") == args.private
        note = "" if recorded or wanted else "  (with --private)" if item["kind"] == "private" else "  (without --private)"
        print(f"{item['kind']:<9} {item['key'][:12]}  {'recorded' if recorded else 'missing '}  "
              f"{item['request'].get('sample_id') or item['request'].get('question')}{note}")
        if not recorded:
            missing += 1
            if wanted:
                todo.append((item, target))
    if not todo or not args.confirm_spend:
        print("nothing sent" if missing else "every sample is recorded")
        return 0
    runtime = private_setup(args.warm_timeout) if args.private else None
    settings = api.Settings.from_env({"RECEIPTS_LIVE_REQUESTS_ENABLED": "true", "RECEIPTS_DAILY_BUDGET_USD": "2",
                                      **({"RECEIPTS_DATA_DIR": str(REPO / "data/snapshot")} if args.private else {})})
    client = TestClient(api.create_app(settings))
    commit = git("rev-parse", "--short", "HEAD")
    total = 0.0
    for item, target in todo:
        if total >= args.max_usd:
            print(f"stopping: observed spend ${total:.4f} reached --max-usd {args.max_usd}")
            break
        recorded_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        started = time.monotonic()
        response = client.post(item["endpoint"], json=item["body"])
        wall_s = round(time.monotonic() - started, 2)
        events = parse_sse(response.text)
        cost = spend(events)
        total += cost
        if not succeeded(item["kind"], events):
            print(f"FAILED {item['kind']} {item['key'][:12]}: {events[-1]['data'] if events else response.status_code}"
                  f" (cost ${cost:.4f})")
            continue
        record = {"version": 1, "kind": item["kind"], "key": item["key"], "request": item["request"],
                  "recorded_utc": recorded_utc, "source_commit": commit, "events": events}
        if item["kind"] == "private":  # what produced it: server, weights revision, code and evidence
            record["runtime"] = {**(runtime or {}), "request_wall_s": wall_s,
                                 "source_dirty": bool(git("status", "--porcelain", "--", "receipts", "deploy", "scripts")),
                                 "route_sha256": {p: sha256(p) for p in ("receipts/api.py", "receipts/private.py")},
                                 "code_fingerprint": stream.code_fingerprint(),
                                 "data_dir": os.path.relpath(settings.data_dir, REPO),
                                 "data_fingerprint": stream.data_fingerprint(settings.data_dir)}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if recordings.Recordings([OUT]).load(item["kind"], item["key"]) is None:  # it must replay as written
            target.unlink()
            print(f"FAILED {item['kind']} {item['key'][:12]}: the stream would not replay, so nothing was kept")
            continue
        print(f"recorded {item['kind']} {item['key'][:12]}  ${cost:.6f}  {wall_s:.2f} s  -> {target.relative_to(REPO)}")
    print(f"observed spend this run: ${total:.6f}"
          + ("; Modal GPU time is billed separately (modal billing report)" if args.private else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
