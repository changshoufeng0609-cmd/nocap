"""Export reviewable source-only history without rewriting the private repository.

Historical commits are reconstructed, not copied: source bytes and author/commit times are
preserved, Git author/committer email fields are replaced, and media, data, documentation
directories and environment files are excluded. A strict source allowlist and a mapping
record every original and derived commit. Never push here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("receipts/", "web/app/", "web/components/", "web/lib/", "web/scripts/", "deploy/", "spikes/", "scripts/")
EXACT = {"LICENSE", "AGENTS.md", "CLAUDE.md", "requirements.txt", "requirements-mcp.txt", "pyproject.toml",
         ".python-version", "vercel.json", "web/package.json", "web/package-lock.json", "web/next.config.ts",
         "web/tsconfig.json", "web/postcss.config.mjs", "web/eslint.config.mjs"}
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".css"}
TOKEN_PATTERNS = [re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
                  re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}"), re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
                  re.compile(rb"LLM_[A-Za-z0-9_-]{24,}")]
# Exact whole-value digest of the public DUMMY_KEY fixture in spikes/b_claims.py.
# A substring such as "test" never exempts a credential-pattern candidate.
PUBLIC_PLACEHOLDER_SHA256 = {"95669c027105c9fb7cbaf51ebb0f75a11f20d520ba074bad60af5e5ed8db9453"}


def git(*args: str, cwd: Path = ROOT, env: dict | None = None, data: bytes | None = None) -> bytes:
    # Git path/config overrides can defeat cwd isolation. Only our six identity/time
    # fields may survive; disable hooks, signing and external global/system config.
    identities = {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
                  "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"}
    supplied = os.environ if env is None else env
    safe_env = {k: v for k, v in supplied.items() if not k.startswith("GIT_")}
    if env is not None:
        safe_env.update({k: env[k] for k in identities if k in env})
    safe_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.check_output(["git", "-c", "core.hooksPath=" + os.devnull,
                                    "-c", "commit.gpgsign=false", *args],
                                   cwd=cwd, env=safe_env, input=data, stderr=subprocess.PIPE)


def allowed(name: str, mode: str) -> bool:
    p = Path(name)
    return (mode in {"100644", "100755"} and not p.is_absolute() and ".." not in p.parts
            and not any(part.startswith(".env") for part in p.parts)
            and (name in EXACT or (name.startswith(PREFIXES) and p.suffix in SUFFIXES)))


def known_secrets() -> list[bytes]:
    from dotenv import dotenv_values
    from urllib.parse import urlsplit
    values = []
    for filename in (".env", ".env.local", ".env.nocap-spend.local"):
        for key, value in dotenv_values(ROOT / filename).items():
            if value and len(value) >= 12 and re.search(r"KEY|TOKEN|SECRET|DATABASE_URL", key):
                values.append(value.encode())
                if value.startswith(("postgres://", "postgresql://")):
                    password = urlsplit(value).password
                    if password and len(password) >= 12:
                        values.append(password.encode())
    return list(set(values))


def audit(body: bytes, secrets: list[bytes], label: str) -> None:
    if b"\x00" in body or any(value in body for value in secrets):
        raise ValueError(f"Unsafe export content: {label}; content was not printed")
    body.decode("utf-8")
    for pattern in TOKEN_PATTERNS:
        for match in pattern.finditer(body):
            if hashlib.sha256(match.group()).hexdigest() not in PUBLIC_PLACEHOLDER_SHA256:
                raise ValueError(f"Credential-pattern candidate: {label}; content was not printed")


def prepare(ref: str) -> tuple[list[dict], dict[str, bytes]]:
    commits = git("rev-list", "--reverse", "--first-parent", ref).decode().splitlines()
    secrets, blobs, snapshots = known_secrets(), {}, []
    for commit in commits:
        author, authored, committed, subject = git("show", "-s", "--format=%an%x00%aI%x00%cI%x00%s", commit).decode().strip().split("\x00")
        audit((author + subject).encode(), secrets, "commit metadata")
        tree = {}
        for raw in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not raw:
                continue
            meta, rawname = raw.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            name = rawname.decode()
            audit(rawname, secrets, "source path")
            if kind != "blob" or not allowed(name, mode):
                continue
            if oid not in blobs:
                body = git("cat-file", "blob", oid)
                audit(body, secrets, name)
                blobs[oid] = body
            tree[name] = {"git_blob": oid, "mode": mode, "sha256": hashlib.sha256(blobs[oid]).hexdigest()}
        snapshots.append({"original_commit": commit, "author_name": author, "authored_at": authored,
                          "committed_at": committed, "subject": subject, "files": tree})
    return snapshots, blobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    snapshots, blobs = prepare(args.ref)
    if args.dry_run:
        print(json.dumps({"commits": len(snapshots), "audited_unique_source_blobs": len(blobs),
                          "final_files": len(snapshots[-1]["files"]), "source_head": snapshots[-1]["original_commit"]}))
        return
    if not args.output or not args.manifest:
        parser.error("--output and --manifest are required unless --dry-run")
    out = args.output.resolve()
    if out.exists():
        parser.error("Output must be a new directory; existing repositories are never changed")
    out.mkdir(parents=True)
    git("init", "-b", "source-history", cwd=out)
    previous = set()
    mapping = []
    for row in snapshots:
        for name in previous - row["files"].keys():
            (out / name).unlink()
        for name, metadata in row["files"].items():
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blobs[metadata["git_blob"]])
            path.chmod(0o755 if metadata["mode"] == "100755" else 0o644)
        git("add", "--all", cwd=out)
        env = dict(os.environ, GIT_AUTHOR_NAME=row["author_name"], GIT_AUTHOR_EMAIL="source-history@nocap.invalid",
                   GIT_COMMITTER_NAME="NoCap filtered-history export", GIT_COMMITTER_EMAIL="source-history@nocap.invalid",
                   GIT_AUTHOR_DATE=row["authored_at"], GIT_COMMITTER_DATE=row["committed_at"])
        message = (row["subject"] + "\n\nFiltered source snapshot; not the original commit object.\n"
                   + "Original-commit: " + row["original_commit"] + "\nGit identity email fields replaced. Source allowlist applied.\n")
        git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-F", "-", cwd=out, env=env, data=message.encode())
        derived = git("rev-parse", "HEAD", cwd=out).decode().strip()
        observed = {line.split(b"\t", 1)[1].decode(): line.split(b"\t", 1)[0].decode().split()[2]
                    for line in git("ls-tree", "-r", "-z", "HEAD", cwd=out).split(b"\0") if line}
        assert observed == {name: metadata["git_blob"] for name, metadata in row["files"].items()}
        mapping.append({**row, "exported_commit": derived})
        previous = set(row["files"])
    manifest = {"exported_at": datetime.now(timezone.utc).isoformat(), "source_head": snapshots[-1]["original_commit"],
                "branch": "source-history", "scope": "Reconstructed first-parent source-only history; original blob bytes and author/commit times preserved; Git author/committer email fields replaced. Media, data, documentation directories and environment files excluded by source allowlist. Email-like text inside source bytes, names or subjects is not rewritten. Timestamps are Git metadata, not an independent timestamp attestation.",
                "private_history_rewritten": False, "source_blob_comparisons_passed": True, "commits": mapping}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(out), "manifest": str(args.manifest), "commits": len(mapping), "status": "verified"}))


if __name__ == "__main__":
    main()
