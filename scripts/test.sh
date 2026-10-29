#!/usr/bin/env bash
# Runs every lint, type, test and build check. Requires a reachable PostgreSQL for backend tests;
# set FACTORYOPS_TEST_DATABASE_URL (database name must end in _test; it is wiped).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> backend: ruff"
(cd "$ROOT/backend" && uv run ruff check . && uv run ruff format --check .)
echo "==> backend: pytest"
(cd "$ROOT/backend" && uv run pytest)

echo "==> frontend: eslint + typecheck"
(cd "$ROOT/frontend" && npm run lint && npm run typecheck)
echo "==> frontend: vitest"
(cd "$ROOT/frontend" && npm test)
echo "==> frontend: build"
(cd "$ROOT/frontend" && npm run build)

echo "All checks passed."
