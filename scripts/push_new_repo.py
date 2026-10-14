#!/usr/bin/env python3
"""Push a local directory as the initial commit of a (new, empty) GitHub repo.

Uses the vault-backed GitHub PAT (custom.github-push) via the REST
git-database API. Respects .gitignore-style exclusions plus built-ins
(never pushes .env, cookies, venvs, logs, pids).

Usage: push_new_repo.py <owner/repo> <directory>
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import http.client

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import dynamic_credential_entry  # noqa: E402

ALWAYS_EXCLUDE = {".env", "gateway.pid", "gateway.log"}
EXCLUDE_DIRS = {"venv", ".venv", "__pycache__", ".git", "test-results"}
EXCLUDE_SUFFIX = (".log", ".pid")


def is_excluded(rel: str) -> bool:
    parts = rel.split(os.sep)
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    base = parts[-1]
    if base in ALWAYS_EXCLUDE or base.endswith(EXCLUDE_SUFFIX):
        return True
    if "cookie" in base.lower() and base.endswith(".txt"):
        return True
    return False


class GH:
    def __init__(self, repo: str):
        self.repo = repo
        self.surr = dynamic_credential_entry("custom.github-push")["surrogate"]
        assert self.surr.startswith("hsurr:")

    def api(self, method: str, path: str, payload=None, timeout=120):
        url = f"https://api.github.com/repos/{self.repo}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(4):
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={"User-Agent": "repo-push",
                         "Accept": "application/vnd.github+json",
                         "Authorization": f"Bearer {self.surr}",
                         "Content-Type": "application/json"})
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
                body = resp.read()
                return json.loads(body.decode()) if body else {}
            except urllib.error.HTTPError as e:
                eb = e.read()[:300].decode("utf-8", "replace")
                if e.code in (429,) or (e.code == 403 and "rate limit" in eb.lower()):
                    time.sleep(int(e.headers.get("Retry-After", 60)) + 2)
                    continue
                if e.code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {eb}")
            except (urllib.error.URLError, TimeoutError,
                    http.client.RemoteDisconnected, ConnectionError,
                    OSError):
                if attempt < 3:
                    time.sleep(2 ** attempt + 1)
                    continue
                raise
        raise RuntimeError("unreachable")


def collect(directory: str):
    files = []
    for root, dirs, names in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, directory)
            if not is_excluded(rel):
                files.append((rel, full))
    return sorted(files)


def main() -> int:
    repo, directory = sys.argv[1], sys.argv[2]
    gh = GH(repo)
    # find parent: none for empty repo, else current main tip
    parents = []
    main_sha = None
    try:
        ref = gh.api("GET", "/git/ref/heads/main")
        main_sha = ref["object"]["sha"]
        parents = [main_sha]
        print(f"main exists at {main_sha[:12]}; committing on top")
    except RuntimeError as e:
        msg = str(e)
        if "HTTP 404" in msg or ("HTTP 409" in msg and "empty" in msg.lower()):
            print("repo is empty; initial commit")
        else:
            raise
    files = collect(directory)
    print(f"pushing {len(files)} files to {repo}")
    entries = []
    for rel, full in files:
        with open(full, "rb") as f:
            raw = f.read()
        if len(raw) > 90_000_000:
            print(f"  SKIP (too large): {rel}")
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        res = gh.api("POST", "/git/blobs",
                     {"content": b64, "encoding": "base64"})
        mode = "100755" if os.access(full, os.X_OK) and rel.startswith("scripts/") else "100644"
        entries.append({"path": rel, "mode": mode, "type": "blob",
                        "sha": res["sha"]})
        print(f"  blob {rel} ({len(raw)} bytes)")
    tree = gh.api("POST", "/git/trees", {"tree": entries}, timeout=300)
    commit = gh.api("POST", "/git/commits", {
        "message": ("genspark-gateway: single-account OpenAI-compatible "
                    "gateway for Genspark's 57 flagship models\n\n"
                    "- FastAPI service (env-configured): /health, /v1/models, "
                    "/v1/chat/completions\n"
                    "- Seamless by design: 2 concurrent max, 6s pacer, "
                    "dedicated session per slot, empty-response guard\n"
                    "- scripts/install.sh, start.py (PID-safe), stop.py, "
                    "test_gateway.py green-check\n"
                    "- README + AGENTS.md so any coding agent can install, "
                    "start, and call the API\n"
                    "- Vendored Genspark client (MIT, SharpWizard/genspark-py) "
                    "with 57-model registry"),
        "tree": tree["sha"], "parents": parents,
        "author": {"name": "Dev4AIBots", "email": "dev4aibots@users.noreply.github.com"},
        "committer": {"name": "Dev4AIBots", "email": "dev4aibots@users.noreply.github.com"},
    })
    gh.api("PATCH" if main_sha else "POST",
           f"/git/refs/heads/main" if main_sha else "/git/refs",
           {"sha": commit["sha"]} if main_sha
           else {"ref": "refs/heads/main", "sha": commit["sha"]})
    print(f"pushed: {repo}@main = {commit['sha'][:12]} "
          f"({len(entries)} files)")
    # verify
    ref = gh.api("GET", "/git/ref/heads/main")
    ok = ref["object"]["sha"] == commit["sha"]
    print("verify ref:", "OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
