#!/bin/bash
# Amex Award Hunter - start the dashboard. Double-click in Finder (after: chmod +x) or run from Terminal.
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "Virtual environment not found. Running first-time setup..."
  ./scripts/setup_mac.sh || exit 1
fi
if [ ! -f .env ]; then
  echo "WARNING: .env not found. Copy .env.example to .env and add your Seats.aero API key."
fi
exec .venv/bin/python -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
