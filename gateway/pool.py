#!/usr/bin/env python3
"""Smart multi-account pool for the Genspark gateway.

Implements RULES.md:
  - per-account envelope: 2 concurrent chats, 6s start gap, dedicated
    client per slot, empty-guard with cross-account retries, 5-empties
    -> 5-min degrade with auto re-probe.
  - routing: primary account first (unless recently empty); spill to others
    only under parallel pressure; least-loaded healthy account wins with
    recently-empty accounts deprioritized; empirical per-account state
    (ok/empty streaks) persists across restarts via pool_state.json, with
    streaks expiring after 6h; queue instead of violating any envelope.
"""
import sys
import os
import json
import threading
import time
from dataclasses import dataclass, field

from genspark_client import GensparkClient, stream_text

MAX_CONCURRENT = 2
MIN_START_GAP_S = 6.0
DEGRADE_AFTER_EMPTIES = 5
DEGRADE_SECONDS = 300.0
EMPTY_COOLDOWN_S = 120.0  # deprioritize an account for 2 min after an empty
STATE_FILE = os.environ.get("POOL_STATE_FILE", "pool_state.json")
STATE_TTL_S = 6 * 3600  # forget learned empty-streaks after 6h (refill is daily)


@dataclass(eq=False)  # eq=False keeps identity hash (needed for exclude-set)
class Account:
    cookie_file: str
    max_concurrent: int = MAX_CONCURRENT
    clients: list = field(default_factory=list)
    free_slots: list = field(default_factory=list)
    sem: threading.Semaphore = None
    pacer_lock: threading.Lock = field(default_factory=threading.Lock)
    last_start: float = 0.0
    consec_empty: int = 0
    degraded_until: float = 0.0
    last_empty: float = 0.0
    ok_count: int = 0
    empty_count: int = 0
    inflight: int = 0

    def __post_init__(self):
        self.clients = [GensparkClient(cookie_file=self.cookie_file)
                        for _ in range(self.max_concurrent)]
        self.free_slots = list(range(self.max_concurrent))
        self.sem = threading.Semaphore(self.max_concurrent)

    @property
    def name(self):
        return self.cookie_file.rsplit("/", 1)[-1]

    def healthy(self) -> bool:
        return time.time() >= self.degraded_until


class AccountPool:
    def __init__(self, cookie_files: list[str],
                 max_concurrent: int = MAX_CONCURRENT,
                 min_start_gap_s: float = MIN_START_GAP_S):
        self.min_gap = min_start_gap_s
        self.accounts = [Account(cf, max_concurrent) for cf in cookie_files]
        self._lock = threading.Lock()
        self._load_state()

    # -- persisted empirical state --------------------------------------
    def _load_state(self):
        """Restore learned per-account empty-streaks across restarts.
        Purely empirical (no balance API). Streaks older than STATE_TTL_S
        are dropped — balances refill daily, so old failures may be stale."""
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        now = time.time()
        by_name = {a.name: a for a in self.accounts}
        for name, st in saved.get("accounts", {}).items():
            a = by_name.get(name)
            if not a:
                continue
            a.ok_count = st.get("ok", 0)
            a.empty_count = st.get("empty", 0)
            last_empty = st.get("last_empty", 0)
            if last_empty and (now - last_empty) < STATE_TTL_S:
                a.consec_empty = st.get("consec_empty", 0)
                a.last_empty = last_empty
                deg = st.get("degraded_until", 0)
                if deg > now:
                    a.degraded_until = deg

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({"accounts": {
                    a.name: {"ok": a.ok_count, "empty": a.empty_count,
                             "consec_empty": a.consec_empty,
                             "last_empty": a.last_empty,
                             "degraded_until": a.degraded_until}
                    for a in self.accounts}}, f)
        except OSError:
            pass

    # -- acquisition ----------------------------------------------------
    def _pick(self, exclude: set[Account]) -> Account | None:
        now = time.time()
        cands = [a for a in self.accounts
                 if a not in exclude and a.healthy() and a.free_slots]
        if not cands:
            return None
        # empirical learning (no balance API): accounts with a recent empty
        # sort last, so retries land on fresh accounts instead of dead ones.
        def rank(a):
            recent_empty = 0 if (now - a.last_empty) >= EMPTY_COOLDOWN_S else 1
            return (recent_empty, a.inflight, a.last_start)
        primary = self.accounts[0]
        if primary in cands:
            gap_ok = (now - primary.last_start) >= self.min_gap
            primary_fresh = (now - primary.last_empty) >= EMPTY_COOLDOWN_S
            others = [a for a in cands if a is not primary]
            if primary_fresh and (gap_ok or not others):
                return primary
        # least-loaded healthy account, fresh first, tie-break oldest start
        return min(cands, key=rank)

    def acquire(self, exclude: set[Account] | None = None,
                timeout: float = 300.0) -> tuple[Account, int]:
        """Return (account, slot). Blocks until a slot frees or timeout."""
        exclude = exclude or set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                acct = self._pick(exclude)
                if acct and acct.sem.acquire(blocking=False):
                    slot = acct.free_slots.pop()
                    acct.inflight += 1
                    return acct, slot
            time.sleep(0.2)
        raise TimeoutError("no healthy account slot became free in time")

    def release(self, acct: Account, slot: int):
        with self._lock:
            acct.free_slots.append(slot)
            acct.inflight -= 1
        acct.sem.release()

    def wait_gap(self, acct: Account):
        """Pacer: enforce min gap between chat starts on this account."""
        with acct.pacer_lock:
            wait = self.min_gap - (time.time() - acct.last_start)
            if wait > 0:
                time.sleep(wait)
            acct.last_start = time.time()

    # -- health ---------------------------------------------------------
    def record_ok(self, acct: Account):
        with self._lock:
            acct.consec_empty = 0
            acct.degraded_until = 0.0
            acct.ok_count += 1
        self._save_state()

    def record_empty(self, acct: Account):
        with self._lock:
            acct.consec_empty += 1
            acct.empty_count += 1
            acct.last_empty = time.time()
            if acct.consec_empty >= DEGRADE_AFTER_EMPTIES:
                acct.degraded_until = time.time() + DEGRADE_SECONDS
        self._save_state()

    def stats(self) -> list[dict]:
        now = time.time()
        out = []
        for a in self.accounts:
            out.append({
                "account": a.name,
                "healthy": a.healthy(),
                "degraded_for_s": max(0.0, round(a.degraded_until - now, 1)),
                "inflight": a.inflight,
                "free_slots": len(a.free_slots),
                "consec_empty": a.consec_empty,
                "ok": a.ok_count,
                "empty": a.empty_count,
            })
        return out

    def quick_check(self) -> dict[str, str]:
        """Fast, free health filter for large pools: is_logged_in only,
        no chats burned. Batches 10 at a time. Sick-but-logged-in accounts
        are caught lazily by the 5-consecutive-empty degrade rule."""
        results = {}

        def check_one(acct: Account):
            try:
                ok = acct.clients[0].is_logged_in()
                results[acct.name] = "OK" if ok else "NOT_LOGGED_IN"
                if not ok:
                    with self._lock:
                        acct.degraded_until = time.time() + DEGRADE_SECONDS
            except Exception as e:
                results[acct.name] = f"ERR {str(e)[:40]}"
                with self._lock:
                    acct.degraded_until = time.time() + DEGRADE_SECONDS

        threads = [threading.Thread(target=check_one, args=(a,))
                   for a in self.accounts]
        for i in range(0, len(threads), 10):
            batch = threads[i:i + 10]
            [t.start() for t in batch]
            [t.join() for t in batch]
        return results

    def startup_probe(self, model: str = "claude-4-5-haiku") -> dict[str, str]:
        """One tiny chat per account; degrade the ones that come back empty.
        Costs one small request per account — buys accurate initial routing."""
        results = {}
        for acct in self.accounts:
            try:
                if not acct.clients[0].is_logged_in():
                    results[acct.name] = "NOT_LOGGED_IN"
                    with self._lock:
                        acct.degraded_until = time.time() + DEGRADE_SECONDS
                    continue
                resp = acct.clients[0].chat("Reply with: OK", model=model)
                text = "".join(stream_text(resp)).strip()
                if text:
                    self.record_ok(acct)
                    results[acct.name] = "OK"
                else:
                    self.record_empty(acct)
                    # one empty at startup isn't 5 — force degrade directly
                    with self._lock:
                        acct.degraded_until = time.time() + DEGRADE_SECONDS
                    results[acct.name] = "EMPTY_DEGRADED"
            except Exception as e:
                results[acct.name] = f"ERR {str(e)[:60]}"
                with self._lock:
                    acct.degraded_until = time.time() + DEGRADE_SECONDS
            time.sleep(2)  # gentle between probes
        return results
