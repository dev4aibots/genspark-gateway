#!/usr/bin/env python3
"""Stop the gateway using its pidfile. Never pattern-matches process names."""
import os
import signal
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIDFILE = os.path.join(HERE, "gateway.pid")


def main() -> int:
    try:
        pid = int(open(PIDFILE).read().strip())
    except (OSError, ValueError):
        print("not running (no pidfile)")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("not running (stale pidfile)")
        os.remove(PIDFILE)
        return 0
    for _ in range(10):
        time.sleep(1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"stopped pid {pid}")
            os.remove(PIDFILE)
            return 0
    print(f"pid {pid} did not stop; SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
