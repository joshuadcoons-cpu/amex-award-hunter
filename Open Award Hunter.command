#!/bin/bash
# Amex Award Hunter - opens the dashboard in your browser. Double-click to run.
# Keep this window open while you use the dashboard; close it to stop.
cd "$(dirname "$0")"
PORT=8501
if [ ! -x .venv/bin/python ]; then
  echo "Not installed yet. Double-click 'Install Award Hunter.command' first."
  read -r -p "Press Return to close." _
  exit 1
fi
if ! grep -Eq '^SEATS_AERO_API_KEY=[A-Za-z0-9_]{16,}' .env 2>/dev/null; then
  echo "WARNING: no API key in .env yet. The dashboard will open but searches will not work."
fi
# Already running? Just open the browser.
if curl -s -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
  open "http://localhost:$PORT/"
  echo "Dashboard already running at http://localhost:$PORT/ - opened in your browser."
  exit 0
fi
echo "Starting Amex Award Hunter at http://localhost:$PORT/ ..."
echo "Leave this window open. Close it (or press Ctrl+C) to stop the dashboard."
( sleep 3; open "http://localhost:$PORT/" ) &
exec .venv/bin/python -m streamlit run app.py --server.port "$PORT" --server.headless true --browser.gatherUsageStats false
