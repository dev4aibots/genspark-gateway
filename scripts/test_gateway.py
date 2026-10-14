#!/usr/bin/env python3
"""Green-check for the genspark-gateway. Run: ./venv/bin/python scripts/test_gateway.py

Checks, in order:
  1. /health is up and at least the primary account is logged in/healthy
  2. /v1/models returns the inbuilt model list (auth enforced)
  3. /v1/chat/completions answers on 3 different models (non-empty)
  4. wrong API key is rejected (401)
Exit 0 + ALL GREEN only if every check passes.
"""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")
KEY = os.environ.get("API_KEY", "genspark-local-key")
PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((PASS, name, detail))
        print(f"[{PASS}] {name} — {detail}", flush=True)
    except Exception as e:
        results.append((FAIL, name, str(e)[:160]))
        print(f"[{FAIL}] {name} — {e}", flush=True)


def get(path, key=KEY):
    req = urllib.request.Request(BASE + path,
                                 headers={"Authorization": f"Bearer {key}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def post_chat(model, prompt, key=KEY, timeout=300):
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps({"model": model, "messages": [
            {"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def main() -> int:
    check("health: gateway up, primary account healthy",
          lambda: (lambda h: f"{h['accounts'][0]['account']} "
                   f"models={h['models']}"
                   if h["accounts"] and h["accounts"][0]["healthy"] else
                   (_ for _ in ()).throw(
                       RuntimeError("primary account not healthy")))(get("/health")))

    def _models():
        d = get("/v1/models")
        n = len(d["data"])
        vendors = sorted({m["owned_by"] for m in d["data"]})
        assert n >= 10, f"only {n} models"
        return f"{n} models, vendors={','.join(vendors)}"
    check("models: inbuilt model list fetchable", _models)

    for model, prompt in [
        ("claude-4-5-haiku", "Say the word BLUE. Nothing else."),
        ("gpt-5.5", "What is 15% of 240? Reply with just the number."),
        ("gemini-3.8-flash", "Name one planet. One word."),
    ]:
        def _chat(m=model, p=prompt):
            d = post_chat(m, p)
            txt = d["choices"][0]["message"]["content"]
            assert txt.strip(), "empty response"
            return f"{len(txt)} chars: {txt[:50]!r}"
        check(f"chat: {model} answers non-empty", _chat)

    def _auth():
        try:
            get("/v1/models", key="wrong-key")
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"expected 401, got {e.code}"
            return "wrong key -> 401"
        raise RuntimeError("wrong key was accepted!")
    check("auth: wrong API key rejected", _auth)

    n_fail = sum(1 for r in results if r[0] == FAIL)
    print(f"\n{'ALL GREEN' if not n_fail else f'{n_fail} FAILURES'} "
          f"({len(results) - n_fail}/{len(results)} passed)")
    return 0 if not n_fail else 1


if __name__ == "__main__":
    sys.exit(main())
