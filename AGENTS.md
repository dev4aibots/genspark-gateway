# AGENTS.md — for any coding agent using this repo

You are working with **genspark-gateway**: one Genspark account exposed as an
OpenAI-compatible API. Follow this file exactly.

## 1. Start the service

```bash
cd <repo-root>
./scripts/start.py        # idempotent; prints health or starts the gateway
```

Requirements: `./scripts/install.sh` must have been run once (venv + deps),
and `.env` must exist with `COOKIE_FILE` pointing at a Genspark login cookie.
Health: `curl -sf http://127.0.0.1:8000/health` → `{"logged_in": true, ...}`.

To stop: `./venv/bin/python scripts/stop.py` (PID-based; never `pkill -f` —
pattern matching can match your own command line and kill your shell).

## 2. Connection details (give these to whoever needs the API)

- **API endpoint:** `http://127.0.0.1:8000`
- **API key:** the `API_KEY` value in `.env` (default `genspark-local-key`);
  send as `Authorization: Bearer <key>`.
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
  in a single prompt. Never do multi-request loops (each request = 1 credit,
  100/day per account).
- The gateway queues and paces requests itself (max 2 concurrent, 6s gaps).
  Fire-and-forget parallel bursts are handled, just slower.
- A `502` means Genspark returned empty after retries — do NOT treat it as
  an answer; wait a minute and retry once. A `503` means the account is out
  of credits until the daily refill.
- Never forward an empty `content` to the user as a result.

## 4. Verify before you claim it works

```bash
./venv/bin/python scripts/test_gateway.py
```

`ALL GREEN` (exit 0) is the only acceptable state. If any check fails, fix
the cause and re-run — do not report success on partial greens.

## 5. Conventions

- Never commit `.env`, `*cookie*`, `*.log`, `*.pid`, `venv/`.
- Config lives in env/`.env`, never hardcoded: `COOKIE_FILE`, `API_KEY`,
  `HOST`, `PORT`, `MAX_CONCURRENT`, `MIN_START_GAP_S`.
- The client in `genspark_client/` is vendored MIT upstream (see
  `ATTRIBUTION.md`); prefer small local patches over forks.
