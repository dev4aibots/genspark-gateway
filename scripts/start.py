#!/usr/bin/env python3
"""Start the gateway if it isn't already running. PID-based, idempotent.

Reads config from .env (or environment). Never pattern-matches process
names (pkill -f can match its own command line and kill the caller).
"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env() -> None:
    env_file = os.path.join(HERE, ".env")
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError):
        return False


def main() -> int:
    load_env()
    port = int(os.environ.get("PORT", "8000"))
    pidfile = os.path.join(HERE, "gateway.pid")
    log = os.path.join(HERE, "gateway.log")
    cookie = os.environ.get("COOKIE_FILES", "")
    cookies = [c.strip() for c in cookie.split(",") if c.strip()]
    if not cookies or not all(os.path.exists(c) for c in cookies):
        print(f"COOKIE_FILES not set or missing: {cookie!r}", file=sys.stderr)
        print("Copy .env.example to .env and point COOKIE_FILES at your "
              "Genspark login cookie files (comma-separated, primary first).",
              file=sys.stderr)
        return 2
    if port_open(port):
        print(f"gateway already up on 127.0.0.1:{port}")
        return 0
    try:
        old = int(open(pidfile).read().strip())
        if pid_alive(old):
            print(f"killing stale pid {old}")
            os.kill(old, 15)
            time.sleep(2)
    except (OSError, ValueError):
        pass
    venv_py = os.path.join(HERE, "venv", "bin", "python")
    if not os.path.exists(venv_py):
        print("venv not found; run ./scripts/install.sh first", file=sys.stderr)
        return 2
    with open(log, "a") as lf:
        proc = subprocess.Popen(
            [venv_py, "-m", "gateway.server"],
            cwd=HERE, stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "PYTHONPATH": HERE})
    open(pidfile, "w").write(str(proc.pid))
    print(f"started gateway pid {proc.pid}, log {log}")
    for _ in range(12):
        time.sleep(3)
        if port_open(port):
            print(f"healthy on 127.0.0.1:{port}")
            return 0
    print("FAILED to come up; see gateway.log", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
