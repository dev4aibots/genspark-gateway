#!/usr/bin/env python3
"""Multi-account Genspark gateway — OpenAI-compatible, seamless by design.

A pool of Genspark accounts serves requests (RULES.md):
  - primary account (first in COOKIE_FILES) serves everything it can;
  - parallel bursts automatically spill to other accounts;
  - per-account envelope always respected (2 concurrent, 6s start gap);
  - empty retries go to a DIFFERENT account;
  - 5 consecutive empties -> account degraded 5 min, auto re-probed.

Configuration (environment variables):
  COOKIE_FILES  comma-separated cookie paths, primary first (REQUIRED)
  API_KEY       legacy env var, ignored: any non-empty Bearer <redacted> accepted
  HOST          bind host (default: 127.0.0.1)
  PORT          bind port (default: 8000)
  MAX_CONCURRENT concurrent chats per account (default: 2)
  MIN_START_GAP_S seconds between chat starts per account (default: 6.0)
  STARTUP_PROBE true/false: probe each account once at startup (default: true)

Endpoints:
  GET  /health               {"accounts":[{...per-account stats...}],...}
  GET  /v1/models            OpenAI-style model list (inbuilt capabilities)
  POST /v1/chat/completions  OpenAI-style chat (non-streaming or SSE stream)

One request = one complete response. Never forwards empty/broken output.
"""
import json
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from gateway.pool import AccountPool
from genspark_client import stream_text, KNOWN_MODELS, DEFAULT_MODEL

COOKIE_FILES = [c.strip() for c in
                os.environ.get("COOKIE_FILES", "").split(",") if c.strip()]
if not COOKIE_FILES:
    # auto-discover: cookies/ dir first, then cookies*.txt in repo root
    _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import glob as _glob
    COOKIE_FILES = sorted(
        _glob.glob(os.path.join(_here, "cookies", "cookies*.txt")) or
        _glob.glob(os.path.join(_here, "cookies*.txt")))
API_KEY = os.environ.get("API_KEY", "genspark-local-key")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
MIN_START_GAP_S = float(os.environ.get("MIN_START_GAP_S", "6.0"))
RETRY_DELAY_S = 12
MAX_ATTEMPTS = 3
STARTUP_PROBE = os.environ.get("STARTUP_PROBE", "true").lower() == "true"

if not COOKIE_FILES:
    raise SystemExit("COOKIE_FILES not set: comma-separated cookie paths, "
                     "primary first")

app = FastAPI(title="Genspark Multi-Account Gateway")
pool = AccountPool(COOKIE_FILES, MAX_CONCURRENT, MIN_START_GAP_S)
if STARTUP_PROBE:
    if len(COOKIE_FILES) > 10:
        # large pool: fast free login check; sick accounts degrade lazily
        print("quick check:", pool.quick_check(), flush=True)
    else:
        print("startup probe:", pool.startup_probe(), flush=True)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


def _check_auth(authorization: Optional[str]):
    # Open gateway: any non-empty Bearer token is accepted. This binds to
    # localhost, so there is no real secret — clients just need *some* key.
    if not authorization or not authorization.startswith("Bearer ") \
            or len(authorization) <= 7:
        raise HTTPException(status_code=401, detail="Missing API key")


def _resolve_model(requested: str) -> str:
    if requested in KNOWN_MODELS:
        return requested
    low = requested.lower()
    for mid in KNOWN_MODELS:
        if low in mid.lower() or mid.lower() in low:
            return mid
    return DEFAULT_MODEL


def _chat_once(acct, slot: int, prompt: str, model: str) -> str:
    resp = acct.clients[slot].chat(prompt, model=model)
    if getattr(resp, "status_code", 200) != 200:
        raise RuntimeError(f"upstream HTTP {resp.status_code}")
    return "".join(stream_text(resp))


def _chat_seamless(prompt: str, model: str) -> tuple[str, str]:
    """Queued, paced, pool-routed chat. Returns (text, account_name).
    Never returns empty without raising."""
    exclude = set()
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_DELAY_S)
        try:
            acct, slot = pool.acquire(
                exclude=exclude,
                timeout=30.0 if attempt else 300.0)
        except TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="All Genspark accounts busy or degraded; "
                       "try again shortly.")
        try:
            pool.wait_gap(acct)  # per-account pacer
            text = _chat_once(acct, slot, prompt, model)
            if text.strip():
                pool.record_ok(acct)
                return text, acct.name
            pool.record_empty(acct)
            exclude.add(acct)  # retry on a DIFFERENT account
        finally:
            pool.release(acct, slot)
    raise HTTPException(
        status_code=502,
        detail=f"Genspark returned empty responses ({MAX_ATTEMPTS} attempts "
               f"across accounts); not forwarding broken output.")


@app.get("/health")
def health():
    return {"accounts": pool.stats(),
            "max_concurrent_per_account": MAX_CONCURRENT,
            "min_start_gap_s": MIN_START_GAP_S,
            "models": len(KNOWN_MODELS)}


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    return {"object": "list",
            "data": [{"id": m, "object": "model", "created": 0,
                      "owned_by": i["vendor"]}
                     for m, i in KNOWN_MODELS.items()]}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest,
                     authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    model = _resolve_model(req.model)
    prompt = "\n".join(m.content for m in req.messages if m.role == "user") \
        or (req.messages[-1].content if req.messages else "")
    if not prompt:
        raise HTTPException(status_code=400, detail="No prompt provided")

    text, _acct = _chat_seamless(prompt, model)
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if req.stream:
        def gen():
            step = 120
            for i in range(0, len(text), step):
                yield ("data: " + json.dumps(
                    {"id": cid, "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": model,
                     "choices": [{"index": 0,
                                  "delta": {"content": text[i:i + step]},
                                  "finish_reason": None}]}) + "\n\n")
            yield ("data: " + json.dumps(
                {"id": cid, "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": model,
                 "choices": [{"index": 0, "delta": {},
                              "finish_reason": "stop"}]}) + "\n\n")
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return {"id": cid, "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
