#!/usr/bin/env python3
"""Smart multi-account pool for the Genspark gateway.

Implements RULES.md:
  - per-account envelope: 2 concurrent chats, 6s start gap, dedicated
    client per slot, empty-guard with cross-account retries, 5-empties
    -> 5-min degrade with auto re-probe.
  - routing: primary account first; spill to others only under parallel
    pressure; least-loaded healthy account wins; queue instead of
    violating any envelope.
"""
import threading
import time
from dataclasses import dataclass, field

from genspark_client import GensparkClient, stream_text

MAX_CONCURRENT = 2
MIN_START_GAP_S = 6.0
DEGRADE_AFTER_EMPTIES = 5
DEGRADE_SECONDS = 300.0


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

    # -- acquisition ----------------------------------------------------
    def _pick(self, exclude: set[Account]) -> Account | None:
        now = time.time()
        cands = [a for a in self.accounts
                 if a not in exclude and a.healthy() and a.free_slots]
        if not cands:
            return None
        primary = self.accounts[0]
        if primary in cands:
            gap_ok = (now - primary.last_start) >= self.min_gap
            others = [a for a in cands if a is not primary]
            if gap_ok or not others:
                return primary
        # least-loaded healthy account, tie-break by oldest last start
        return min(cands, key=lambda a: (a.inflight, a.last_start))

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

    def record_empty(self, acct: Account):
        with self._lock:
            acct.consec_empty += 1
            acct.empty_count += 1
            if acct.consec_empty >= DEGRADE_AFTER_EMPTIES:
                acct.degraded_until = time.time() + DEGRADE_SECONDS

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
                    with self._lock:
                        acct.degraded_until = time.time() + DEGRADE_SECONDS
                    results[acct.name] = "EMPTY_DEGRADED"
            except Exception as e:
                results[acct.name] = f"ERR {str(e)[:60]}"
                with self._lock:
                    acct.degraded_until = time.time() + DEGRADE_SECONDS
            time.sleep(2)  # gentle between probes
        return results
