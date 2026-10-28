@echo off
REM Amex Award Hunter - one-time setup: create a virtual environment and install dependencies.
cd /d "%~dp0.."
where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher not found. Install Python 3.12 or newer from https://www.python.org/downloads/windows/
    echo and tick "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)
if not exist ".venv" (
    echo Creating virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 ( echo Failed to create virtual environment. & pause & exit /b 1 )
)
echo Installing dependencies (this can take a few minutes the first time)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo Dependency installation failed. & pause & exit /b 1 )
if not exist ".env" (
    copy .env.example .env >nul
    echo Created .env from .env.example. Open .env in Notepad and paste your Seats.aero API key.
)
echo.
echo Setup complete. Next: edit .env, then run scripts\test_connection.bat
exit /b 0
