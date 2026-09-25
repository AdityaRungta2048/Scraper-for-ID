#!/usr/bin/env bash
# Runs every automated check: backend lint/types/tests, frontend lint/types/tests/build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python}"

echo "== backend: ruff" && (cd "$ROOT/backend" && $PY -m ruff check app tests && $PY -m ruff format --check app tests)
echo "== backend: mypy" && (cd "$ROOT/backend" && $PY -m mypy app)
echo "== backend: pytest" && (cd "$ROOT/backend" && $PY -m pytest -q)
echo "== frontend: eslint" && (cd "$ROOT/frontend" && npx eslint .)
echo "== frontend: tsc" && (cd "$ROOT/frontend" && npx tsc --noEmit)
echo "== frontend: vitest" && (cd "$ROOT/frontend" && npx vitest run)
echo "== frontend: build" && (cd "$ROOT/frontend" && NEXT_TELEMETRY_DISABLED=1 npx next build >/dev/null)
echo "All checks passed."
