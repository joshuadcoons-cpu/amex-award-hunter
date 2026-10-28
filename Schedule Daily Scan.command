#!/bin/bash
# Amex Award Hunter - turn the automatic morning scan on or off. Double-click to run.
cd "$(dirname "$0")"
LABEL="com.amexawardhunter.dailyscan"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Amex Award Hunter - daily scan schedule"
echo
if [ -f "$PLIST" ]; then
  echo "A daily scan is currently scheduled."
  read -r -p "Change the time (c), turn it off (o), or keep it (Return)? " ans
else
  echo "No daily scan is scheduled yet."
  read -r -p "Schedule one? (Return = yes, n = no) " ans
  [ -z "$ans" ] && ans="c"
fi
case "$ans" in
  [Cc]*)
    read -r -p "Hour (0-23, default 7): " H; read -r -p "Minute (0-59, default 30): " M
    bash scripts/setup_mac_schedule.sh "${H:-7}" "${M:-30}"
    ;;
  [Oo]*)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Daily scan turned off."
    ;;
  *) echo "No change." ;;
esac
echo
read -r -p "Press Return to close." _
