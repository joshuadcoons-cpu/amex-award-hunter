#!/bin/bash
# Amex Award Hunter - one-time setup on macOS.
# Creates a private Python environment (.venv) and installs the dependencies.
# If no suitable Python is installed, it fetches one automatically using "uv"
# (https://docs.astral.sh/uv/), so nothing else needs to be installed by hand.
set -e
cd "$(dirname "$0")/.."

need_major=3; need_minor=12

version_ok() {  # $1 = python executable
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= ($need_major, $need_minor) else 1)" >/dev/null 2>&1
}

find_python() {
  local candidates=(python3.13 python3.12 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3)
  for f in /Library/Frameworks/Python.framework/Versions/3.1*/bin/python3; do
    [ -x "$f" ] && candidates+=("$f")
  done
  for c in "${candidates[@]}"; do
    if command -v "$c" >/dev/null 2>&1 && version_ok "$c"; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

USE_UV=0
if [ -x .venv/bin/python ] && version_ok .venv/bin/python; then
  echo "Using existing environment (.venv)."
else
  PY=$(find_python || true)
  if [ -n "$PY" ]; then
    echo "Using $PY ($("$PY" --version 2>&1))"
    rm -rf .venv
    "$PY" -m venv .venv
  else
    echo "No Python $need_major.$need_minor+ found. Downloading a private copy with uv (one-time, ~30 MB)..."
    if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    export PATH="$HOME/.local/bin:$PATH"
    uv python install 3.12
    rm -rf .venv
    uv venv --python 3.12 .venv
    USE_UV=1
  fi
fi

echo "Installing dependencies (a few minutes the first time)..."
if [ "$USE_UV" = "1" ] || ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
  uv pip install --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi
mkdir -p data logs
chmod +x scripts/*.sh *.command 2>/dev/null || true
# Clear the "downloaded from the internet" flag so the double-click launchers open without warnings.
xattr -dr com.apple.quarantine . 2>/dev/null || true
echo
echo "Setup complete."
