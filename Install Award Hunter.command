#!/bin/bash
# ============================================================================
#  Amex Award Hunter - one-time installer for macOS.  Double-click to run.
#  If macOS refuses to open it: right-click the file -> Open -> Open.
# ============================================================================
cd "$(dirname "$0")"
set -o pipefail
clear
echo "============================================================"
echo "   Amex Award Hunter - one-time setup"
echo "============================================================"
echo
echo "This will:"
echo "  1. Set up a private Python environment inside this folder"
echo "  2. Check your Seats.aero API key"
echo "  3. Schedule a daily scan and run the first one"
echo "  4. Open the dashboard in your browser"
echo
read -r -p "Press Return to begin (or close this window to cancel)... " _

# ---- 1. environment -------------------------------------------------------
if ! bash scripts/setup_mac.sh; then
  echo
  echo "Setup did not finish. Scroll up for the error, or send a screenshot of this window for help."
  read -r -p "Press Return to close." _
  exit 1
fi

# ---- 2. API key -----------------------------------------------------------
has_key() { grep -Eq '^SEATS_AERO_API_KEY=[A-Za-z0-9_]{16,}' .env 2>/dev/null && ! grep -q 'replace_with_your_key' .env; }
if ! has_key; then
  echo
  echo "Paste your Seats.aero Pro API key (seats.aero -> Settings -> API), then press Return."
  read -r -p "API key: " KEY
  KEY=$(echo "$KEY" | tr -d '[:space:]' | sed 's/^[Bb]earer//')
  if [ -z "$KEY" ]; then
    echo "No key entered. Open .env in this folder later and paste it after SEATS_AERO_API_KEY=."
  else
    if grep -q '^SEATS_AERO_API_KEY=' .env; then
      sed -i '' "s|^SEATS_AERO_API_KEY=.*|SEATS_AERO_API_KEY=$KEY|" .env
    else
      echo "SEATS_AERO_API_KEY=$KEY" >> .env
    fi
  fi
fi

echo
echo "Testing the connection to Seats.aero..."
if .venv/bin/python scripts/test_connection.py | tee /tmp/awardhunter_conntest.txt; then
  if grep -q 'Auth scheme   : raw' /tmp/awardhunter_conntest.txt; then
    if grep -q '^SEATS_AERO_AUTH_SCHEME=' .env; then
      sed -i '' 's|^SEATS_AERO_AUTH_SCHEME=.*|SEATS_AERO_AUTH_SCHEME=raw|' .env
    else
      echo "SEATS_AERO_AUTH_SCHEME=raw" >> .env
    fi
  fi
else
  echo
  echo "The connection test failed. Check the key in .env (open it with TextEdit) and double-click this installer again."
  read -r -p "Press Return to close." _
  exit 1
fi
rm -f /tmp/awardhunter_conntest.txt

# ---- 3. schedule + first scan --------------------------------------------
echo
read -r -p "Run a scan automatically every morning at 7:30? [Y/n] " yn
case "${yn:-Y}" in
  [Yy]*) bash scripts/setup_mac_schedule.sh 07 30 ;;
  *)     echo "Skipped. You can schedule later by double-clicking 'Schedule Daily Scan.command'." ;;
esac

echo
read -r -p "Run the first scan now? It takes about a minute and uses ~10 of your 1,000 daily API calls. [Y/n] " yn
case "${yn:-Y}" in
  [Yy]*) .venv/bin/python scripts/run_daily_scan.py --no-alerts | grep -v '^\[' ;;
  *)     echo "Skipped. Use 'Scan Now.command' or the Search page in the dashboard." ;;
esac

# ---- 4. dashboard ----------------------------------------------------------
echo
echo "All set. Opening the dashboard. To open it again later, double-click 'Open Award Hunter.command'."
echo
exec bash "Open Award Hunter.command"
