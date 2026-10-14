# AGENTS.md — for any coding agent using this repo

You are working with **genspark-gateway**: Genspark accounts exposed as an
OpenAI-compatible API. Follow this file exactly.

## 1. Start the service

```bash
cd <repo-root>
./scripts/start.py        # idempotent; prints health or starts the gateway
```

Requirements: `./scripts/install.sh` must have been run once (venv + deps),
and `.env` must exist with `COOKIE_FILES` pointing at Genspark login cookies
(comma-separated, primary first).
Health: `curl -sf http://127.0.0.1:8000/health` → per-account stats.

To stop: `./venv/bin/python scripts/stop.py` (PID-based; never `pkill -f` —
pattern matching can match your own command line and kill your shell).

## 2. Connection details (give these to whoever needs the API)

- **API endpoint:** `http://127.0.0.1:8000`
- **API key:** any non-empty string, sent as `Authorization: Bearer <anything>`.
  There is no real secret — the gateway binds to localhost, so any key works.
- **Model list (inbuilt capabilities fetch):** `GET /v1/models` returns
  `{"object":"list","data":[{"id","owned_by"}, ...]}` — 57 models.
  Always fetch this instead of hardcoding model names; resolve a requested
  name case-insensitively against it.

## 3. Calling a model

`POST /v1/chat/completions`, OpenAI shape:

```json
{"model": "claude-opus-5-5",
 "messages": [{"role": "user", "content": "your full prompt here"}],
 "stream": false}
```

Rules:
- **One request → one complete response.** Put everything the model needs
  in a single prompt. Never do multi-request loops.
- **Smart multi-account pool:** the primary account (first in
  `COOKIE_FILES`) serves everything it can; parallel bursts automatically
  spill to the other accounts. Per-account envelope is always respected
  (max 2 concurrent, 6s gaps). Fire parallel requests freely — the pool
  queues and routes them.
- A `502` means Genspark returned empty after retries across accounts —
  do NOT treat it as an answer; wait a minute and retry once. A `503`
  means all accounts are busy or degraded; back off and retry.
- Never forward an empty `content` to the user as a result.

## 4. Verify before you claim it works

```bash
./venv/bin/python scripts/test_gateway.py
```

`ALL GREEN` (exit 0) is the only acceptable state. If any check fails, fix
the cause and re-run — do not report success on partial greens.

## 5. Conventions

- Never commit `.env`, `*cookie*`, `*.log`, `*.pid`, `venv/`.
- Config lives in env/`.env`, never hardcoded: `COOKIE_FILES`, `API_KEY`,
  `HOST`, `PORT`, `MAX_CONCURRENT`, `MIN_START_GAP_S`, `STARTUP_PROBE`.
- The client in `genspark_client/` is vendored MIT upstream (see
  `ATTRIBUTION.md`); prefer small local patches over forks.
- The measured operating rules live in `RULES.md` — read it before tuning
  any envelope parameter.
