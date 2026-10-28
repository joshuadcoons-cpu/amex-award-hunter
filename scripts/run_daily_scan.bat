@echo off
REM Used by Windows Task Scheduler. Logs go to logs\award_hunter.log and logs\scan_output.log
cd /d "%~dp0.."
if not exist logs mkdir logs
".venv\Scripts\python.exe" scripts\run_daily_scan.py >> logs\scan_output.log 2>&1
