#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  echo 'No .env found - creating unique local secrets once.'
  python scripts/bootstrap.py
  echo 'SAVE the admin password printed above (shown only once).'
fi
echo 'Starting Ninja Image Hub on http://127.0.0.1:8081 (Ctrl+C stops it)'
exec python -m uvicorn app:app --host 127.0.0.1 --port 8081 --workers 1
