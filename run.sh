#!/usr/bin/env bash
# Boot the Souly API. Binds 0.0.0.0 so other devices on the MiFi can reach it.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${SOULY_HOST:-0.0.0.0}"
PORT="${SOULY_PORT:-8000}"

echo "Souly API starting on http://${HOST}:${PORT}  (docs: /docs)"
echo "On the MiFi, other devices reach this at http://<this-machine-ip>:${PORT}"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
