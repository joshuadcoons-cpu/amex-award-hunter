#!/bin/bash
# Registers a launchd job that runs scripts/run_daily_scan.sh once a day.
# Usage:  ./scripts/setup_mac_schedule.sh 07 30      (hour minute, 24h clock; default 07:30)
# Remove: launchctl bootout gui/$(id -u)/com.amexawardhunter.dailyscan && rm ~/Library/LaunchAgents/com.amexawardhunter.dailyscan.plist
set -e
HOUR=${1:-7}; MINUTE=${2:-30}
cd "$(dirname "$0")/.."
PROJECT="$(pwd)"
LABEL="com.amexawardhunter.dailyscan"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT/scripts/run_daily_scan.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$((10#$HOUR))</integer>
    <key>Minute</key><integer>$((10#$MINUTE))</integer>
  </dict>
  <key>StandardOutPath</key><string>$PROJECT/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/logs/launchd.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Scheduled daily scan at $(printf '%02d:%02d' "$((10#$HOUR))" "$((10#$MINUTE))") via launchd ($LABEL)."
echo "The Mac must be awake and online at that time (launchd runs missed jobs when it wakes; see docs/SETUP_MAC.md)."
echo "Run once now:  launchctl kickstart gui/$(id -u)/$LABEL"
echo "Output:        $PROJECT/logs/scan_output.log"
