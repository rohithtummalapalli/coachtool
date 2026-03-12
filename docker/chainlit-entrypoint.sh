#!/usr/bin/env sh
set -eu

cd /app

exec chainlit run app.py --host 0.0.0.0 --port "${CHAINLIT_PORT:-8000}" --headless
