#!/usr/bin/env bash
# Install the genspark-gateway: creates a venv and installs dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -q -r requirements.txt
echo "Installed. Next:"
echo "  1. cp .env.example .env   # then set COOKIE_FILE to your Genspark cookie"
echo "  2. ./scripts/start.py"
