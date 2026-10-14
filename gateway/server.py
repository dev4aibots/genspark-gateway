#!/usr/bin/env python3
"""Single-account Genspark gateway — OpenAI-compatible, seamless by design.

ONE Genspark account serves all requests through:
  - a concurrency cap (default 2) with a dedicated client session per slot
    (a shared session across threads gets later chats voided by Genspark),
  - a pacer (default 6s between chat starts; bursts get voided into empty
    HTTP-200 responses),
  - an empty-response guard (retries, never forwards empty text),
  - a drained-account guard (clear 503 instead of silent garbage).

Configuration (environment variables):
  COOKIE_FILE   path to a Genspark login cookie file (REQUIRED)
  API_KEY       bearer key for this gateway (default: genspark-local-key)
  HOST          bind host (default: 127.0.0.1)
  PORT          bind port (default: 8000)
  MAX_CONCURRENT concurrent chats (default: 2)
  MIN_START_GAP_S seconds between chat starts (default: 6.0)

Endpoints:
  GET  /health               {"account","logged_in","inflight",...}
  GET  /v1/models            OpenAI-style model list (inbuilt capabilities)
  POST /v1/chat/completions  OpenAI-style chat (non-streaming or SSE stream)

One request = one complete response. Each request costs 1 Genspark credit.
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from genspark_client import GensparkClient, stream_text, KNOWN_MODELS, DEFAULT_MODEL

COOKIE_FILE = os.environ.get("COOKIE_FILE", "")
API_KEY = os.environ.get("API_KEY", "genspark-local-key")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
MIN_START_GAP_S = float(os.environ.get("MIN_START_GAP_S", "6.0"))
RETRY_DELAY_S = 12
MAX_ATTEMPTS = 3
DRAINED_AFTER_EMPTIES = 5

if not COOKIE_FILE:
    raise SystemExit("COOKIE_FILE is not set. See .env.example.")

app = FastAPI(title="Genspark Single-Account Gateway")

_slots: list[GensparkClient] = [
    GensparkClient(cookie_file=COOKIE_FILE) for _ in range(MAX_CONCURRENT)
]
_slots_free: list[GensparkClient] = list(_slots)
_slots_lock = threading.Lock()
_gate = threading.Semaphore(MAX_CONCURRENT)
_pacer_lock = threading.Lock()
_last_start = 0.0
_state_lock = threading.Lock()
_consec_empty = 0
_inflight = 0


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
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def _resolve_model(requested: str) -> str:
    if requested in KNOWN_MODELS:
        return requested
    low = requested.lower()
    for mid in KNOWN_MODELS:
        if low in mid.lower() or mid.lower() in low:
            return mid
    return DEFAULT_MODEL


def _chat_once(client: GensparkClient, prompt: str, model: str) -> str:
    resp = client.chat(prompt, model=model)
    if getattr(resp, "status_code", 200) != 200:
        raise RuntimeError(f"upstream HTTP {resp.status_code}")
    return "".join(stream_text(resp))


def _chat_seamless(prompt: str, model: str) -> str:
    """Queued, paced, guarded chat. Never returns empty without raising."""
    global _consec_empty, _last_start, _inflight
    with _state_lock:
        if _consec_empty >= DRAINED_AFTER_EMPTIES:
            raise HTTPException(
                status_code=503,
                detail="Genspark account appears out of credits "
                       "(consecutive empty responses). Credits refill daily.")
    _gate.acquire()
    try:
        with _slots_lock:
            client = _slots_free.pop()
        try:
            with _pacer_lock:
                wait = MIN_START_GAP_S - (time.time() - _last_start)
                if wait > 0:
                    time.sleep(wait)
                _last_start = time.time()
            with _state_lock:
                _inflight += 1
            try:
                text = ""
                for attempt in range(MAX_ATTEMPTS):
                    if attempt:
                        time.sleep(RETRY_DELAY_S)
                    text = _chat_once(client, prompt, model)
                    if text.strip():
                        break
                if text.strip():
                    with _state_lock:
                        _consec_empty = 0
                    return text
                with _state_lock:
                    _consec_empty += 1
                raise HTTPException(
                    status_code=502,
                    detail=f"Genspark returned empty responses "
                           f"({MAX_ATTEMPTS} attempts); not forwarding "
                           f"broken output.")
            finally:
                with _state_lock:
                    _inflight -= 1
        finally:
            with _slots_lock:
                _slots_free.append(client)
    finally:
        _gate.release()


@app.get("/health")
def health():
    with _state_lock:
        return {"account": Path(COOKIE_FILE).name,
                "logged_in": _slots[0].is_logged_in(),
                "inflight": _inflight,
                "max_concurrent": MAX_CONCURRENT,
                "min_start_gap_s": MIN_START_GAP_S,
                "consec_empty": _consec_empty,
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

    text = _chat_seamless(prompt, model)
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


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
