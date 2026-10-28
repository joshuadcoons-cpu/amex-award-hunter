@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" scripts\test_connection.py
pause
