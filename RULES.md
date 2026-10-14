# Genspark Gateway Rules — measured, 2026-10-14

Every rule below comes from a live test, not theory. Break one and Genspark
silently voids your chats into empty HTTP-200 responses.

## Per-account envelope (each account independently)

1. **Max 2 concurrent chats per account.** 4 parallel worked on a fresh
   account; 12 parallel got voided (2/12, then 0/12). 2 is the safe line.
2. **Min 6s gap between chat starts per account.** Rapid bursts get voided
   into silent empty HTTP-200s. The pacer sleeps, never skips.
3. **One dedicated GensparkClient per slot.** Sharing one session across
   threads gets later chats voided. 2 slots = 2 separate clients.
4. **Never forward empty content.** Retry up to 3x with ~12s backoff.
   A retry goes to a DIFFERENT account — empties look per-account.
5. **5 consecutive empties → account degraded for 5 min.** Route around it;
   auto re-probe when the timer expires (accounts recover on their own).

## Pool routing (many accounts, one gateway)

6. **Primary account serves everything it can.** Other accounts stay idle
   until parallel pressure needs them — single-account spirit, pool muscle.
7. **Pick order:** primary (slot free) → least-loaded healthy account →
   queue and wait. Never exceed any account's envelope to go faster.
8. **Never run two gateway processes on the same account simultaneously.**
   Interleaved requests void each other while burning usage.

## What the pool optimizes for (user principle)

9. **The only signal is working responses.** Route on success/failure only —
   never on credit balance or any hidden quota. A numeric balance endpoint
   exists (`GET /api/payment/get_credit_balance`) and may be read as a
   passive diagnostic, but it must never change routing, retries, or any
   request rule. Credits are not our problem until requests stop working;
   when they do, the empty-response handling above already routes around it.
10. **Accounts recover on their own.** Hammered accounts returned empties,
    then worked again later. The pool's degrade-and-reprobe handles
    this without human intervention.
