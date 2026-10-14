# Attribution

## genspark_client/ (vendored)

Source: [SharpWizard/genspark-py](https://github.com/SharpWizard/genspark-py)
License: MIT (see `LICENSE.upstream`).

Vendored because it is the Cloudflare-bypass login + chat layer this
gateway is built on. Local changes vs upstream `e0cb3be`:

- `KNOWN_MODELS` extended from 30 → 57 entries, scanned from the live
  Genspark web UI (2026-10-14): Claude Opus 5/5.5/4.8, Sonnet 5/5.5,
  Haiku 5.5, Gemini 3.1-flash-lite, 3.6/3.7/3.8-flash, Grok 4.6/4.7,
  Kimi K3, GPT-6 Sol/Luna, GPT-6.1 Sol, GPT-5.6 Luna/Sol/Terra,
  DeepSeek V4.1 Flash, GLM-5.3 (+flash), MiniMax M3, Nemotron 3 Ultra,
  MiMo V2.6 (flash/pro).

If upstream adds models, re-scan the Genspark model picker and extend
`KNOWN_MODELS` the same way.

## gateway/ (this repo's code)

Original work for this repo, MIT.
