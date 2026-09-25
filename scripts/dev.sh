#!/usr/bin/env bash
# Local development without Docker: SQLite + in-process queue.
#   ./scripts/dev.sh     -> API on :8000, UI on :3000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] || { echo "Create $ROOT/.env from .env.example first"; exit 1; }
set -a; source "$ROOT/.env"; set +a
(cd "$ROOT/backend" && python -m uvicorn app.main:app --reload --port 8000) &
API_PID=$!
trap 'kill $API_PID' EXIT
cd "$ROOT/frontend" && BACKEND_URL=http://localhost:8000 npm run dev
