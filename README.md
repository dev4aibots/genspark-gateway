# genspark-gateway

Genspark accounts → an OpenAI-compatible API with **57 flagship models**
(Claude Opus 5.5, GPT-5.5/6, Gemini 3.x, Grok 4.x, DeepSeek, Kimi, GLM…),
free via Genspark. Single request → one complete response.

Built for **coding agents**: start the service, read the endpoint + key below,
fetch the model list inbuilt, and call any model.

## Install (anyone)

```bash
git clone <this-repo> && cd genspark-gateway
./scripts/install.sh        # creates venv, installs deps
# drop your cookies1.txt, cookies2.txt, ... into cookies/  (see cookies/README.md)
./scripts/start.py           # starts on 127.0.0.1:8000, auto-finds all cookies
./venv/bin/python scripts/test_gateway.py   # ALL GREEN check
```

No config needed: the gateway auto-discovers `cookies/cookies*.txt`
(primary first). `COOKIE_FILES` in `.env` overrides this if you prefer
explicit paths.

### Getting `COOKIE_FILES`

1. Log in to [genspark.ai](https://genspark.ai) in your browser (free account).
2. Open DevTools → Application → Cookies → copy the session cookies into a
   text file, e.g. `~/genspark-cookie.txt`, in Netscape cookie-jar format
   (the `genspark_client` reads it with `http.cookiejar`).
3. Set `COOKIE_FILES=/absolute/path/to/cookie1.txt,/absolute/path/to/cookie2.txt`
   in `.env` — comma-separated, **primary first**. Add as many accounts as
   you have; the pool spreads parallel load across them automatically.

> Cookies are secrets: `.gitignore` excludes them — never commit them.

## Use

**Endpoint:** `http://127.0.0.1:8000`
**API key:** any non-empty string — send as `Authorization: Bearer <anything>` (no real secret; localhost-only gateway)

```bash
# inbuilt model list (capabilities)
curl -H "Authorization: Bearer genspark-local-key" \
  http://127.0.0.1:8000/v1/models

# chat any model (OpenAI shape)
curl -H "Authorization: Bearer genspark-local-key" \
     -H "Content-Type: application/json" \
     -d '{"model":"claude-opus-5-5","messages":[{"role":"user","content":"Explain recursion in one paragraph."}]}' \
  http://127.0.0.1:8000/v1/chat/completions

# health
curl http://127.0.0.1:8000/health
```

`stream: true` is supported (SSE).

## How it stays seamless

Genspark voids bursts: >~6 rapid or highly-concurrent chats on one account
come back as silent empty HTTP-200s. The gateway enforces the measured safe
envelope (see `RULES.md`) instead of letting you hit it:

- **smart multi-account pool**: primary account serves everything it can;
  parallel bursts automatically spill to other accounts,
- **max 2 concurrent chats per account**, extras queue (6s pacer between starts),
- **one dedicated client session per slot** (a shared session across
  threads gets later chats voided),
- **empty-response guard**: retries on a *different* account, never forwards
  empty text; 5 consecutive empties → account auto-degraded 5 min, then
  re-probed (accounts recover on their own).

## Layout

```
gateway/server.py        the service (env-configured, see .env.example)
gateway/pool.py          smart multi-account pool (routing, health, degrade)
RULES.md                 measured operating rules (from live tests)
genspark_client/         Genspark API client (vendored, MIT — see ATTRIBUTION.md)
scripts/install.sh       venv + deps
scripts/start.py         PID-based safe start (idempotent)
scripts/stop.py          PID-based stop
scripts/test_gateway.py  green-check: health, models, chat x3, auth
AGENTS.md                instructions for coding agents
```

## License

MIT for the gateway code. The vendored client is MIT © its upstream
authors — see `ATTRIBUTION.md` and `LICENSE.upstream`.
