# genspark-gateway

One Genspark account → an OpenAI-compatible API with **57 flagship models**
(Claude Opus 5.5, GPT-5.5/6, Gemini 3.x, Grok 4.x, DeepSeek, Kimi, GLM…),
free via Genspark's daily credits. Single request → one complete response.

Built for **coding agents**: start the service, read the endpoint + key below,
fetch the model list inbuilt, and call any model.

## Install (anyone)

```bash
git clone <this-repo> && cd genspark-gateway
./scripts/install.sh        # creates venv, installs deps
cp .env.example .env        # then set COOKIE_FILE (see below)
./scripts/start.py           # starts on 127.0.0.1:8000
./venv/bin/python scripts/test_gateway.py   # ALL GREEN check (~4 credits)
```

### Getting `COOKIE_FILE`

1. Log in to [genspark.ai](https://genspark.ai) in your browser (free account).
2. Open DevTools → Application → Cookies → copy the session cookies into a
   text file, e.g. `~/genspark-cookie.txt`, in Netscape cookie-jar format
   (the `genspark_client` reads it with `http.cookiejar`).
3. Set `COOKIE_FILE=/absolute/path/to/your-genspark-cookie.txt` in `.env`.

> Cookies are secrets: `.gitignore` excludes them — never commit them.

## Use

**Endpoint:** `http://127.0.0.1:8000`
**API key:** value of `API_KEY` in `.env` (default `genspark-local-key`)

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

`stream: true` is supported (SSE). Each request costs 1 Genspark credit
(100/day per free account, refills daily).

## How it stays seamless

Genspark voids bursts: >~6 rapid or highly-concurrent chats on one account
come back as empty HTTP-200s (still burning credits). The gateway enforces
the measured safe envelope instead of letting you hit it:

- **max 2 concurrent chats**, extras queue (6s pacer between starts),
- **one dedicated client session per slot** (a shared session across
  threads gets later chats voided),
- **empty-response guard**: retries automatically, never forwards empty
  text; persistent empties → clear `503 out of credits` instead of garbage.

## Layout

```
gateway/server.py        the service (env-configured, see .env.example)
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
