#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

# External macOS volumes can create AppleDouble `._*` files inside site-packages.
# pip treats those as broken distributions, so keep the venv tidy before startup.
find .venv -name '._*' -delete 2>/dev/null || true

if [ ! -f ".venv/.requirements-installed" ] || [ requirements.txt -nt ".venv/.requirements-installed" ]; then
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  find .venv -name '._*' -delete 2>/dev/null || true
  touch ".venv/.requirements-installed"
fi

if [ -f "frontend/package.json" ] && [ "${SKIP_FRONTEND_BUILD:-0}" != "1" ]; then
  find frontend -name '._*' -delete 2>/dev/null || true
  if [ ! -d "frontend/node_modules" ] || [ "frontend/package-lock.json" -nt "frontend/node_modules/.install-stamp" ]; then
    (cd frontend && npm install)
    touch "frontend/node_modules/.install-stamp"
  fi
  (cd frontend && npm run build)
  find frontend -name '._*' -delete 2>/dev/null || true
fi

python -m app.main
