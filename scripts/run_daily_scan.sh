#!/bin/bash
# Used by launchd (or cron). Appends output to logs/scan_output.log
cd "$(dirname "$0")/.."
mkdir -p logs
.venv/bin/python scripts/run_daily_scan.py "$@" >> logs/scan_output.log 2>&1
