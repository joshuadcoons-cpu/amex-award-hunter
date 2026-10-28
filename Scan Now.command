#!/bin/bash
# Amex Award Hunter - run one full scan right now (same as the scheduled morning scan). Double-click to run.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Not installed yet. Double-click 'Install Award Hunter.command' first."
  read -r -p "Press Return to close." _
  exit 1
fi
echo "Scanning Seats.aero (about a minute, ~10 API calls)..."
echo
.venv/bin/python scripts/run_daily_scan.py "$@" | grep -v '^\['
echo
echo "Done. Open the dashboard ('Open Award Hunter.command') to see the results."
read -r -p "Press Return to close." _
