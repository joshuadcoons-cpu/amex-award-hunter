@echo off
REM Amex Award Hunter - start the dashboard (double-click this file)
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Running first-time setup...
    call scripts\setup_windows.bat
    if errorlevel 1 exit /b 1
)
if not exist ".env" (
    echo.
    echo WARNING: .env file not found. Copy .env.example to .env and add your Seats.aero API key.
    echo The dashboard will still open so you can read the setup instructions.
    echo.
)
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
pause
