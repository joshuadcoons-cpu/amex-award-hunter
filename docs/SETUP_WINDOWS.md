# Windows setup guide

You do not need Docker, Linux or any cloud account. Everything runs on your PC.

## 1. Install Python
1. Download **Python 3.12 or newer** (64-bit) from <https://www.python.org/downloads/windows/>.
2. Run the installer and **tick "Add python.exe to PATH"**, then *Install Now*.
3. Open a new Command Prompt and check: `py -3 --version` should print `Python 3.12.x` or higher.

## 2. Get the project onto your PC
Download/clone the `amex-award-hunter` folder somewhere simple, e.g. `C:\AwardHunter\amex-award-hunter`.

## 3. First-time setup (one double-click)
Double-click `scripts\setup_windows.bat`. It creates a private virtual environment
(`.venv`), installs the dependencies from `requirements.txt`, and copies
`.env.example` to `.env` if you don't have one yet.

Equivalent manual commands (Command Prompt in the project folder):
```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

## 4. Add your Seats.aero API key
1. Open `.env` in Notepad (it is in the project folder; it is ignored by git).
2. Replace `replace_with_your_key` with your **Seats.aero Pro Partner API key** (seats.aero → Settings → API).
3. Save. Never share this file or paste the key anywhere else.

```
SEATS_AERO_API_KEY=pro_xxxxxxxxxxxxxxxxxxxxxxxx
DATABASE_PATH=data/awards.sqlite
TIMEZONE=America/Los_Angeles
```

## 5. Test the connection
Double-click `scripts\test_connection.bat` (or run `.venv\Scripts\python.exe scripts\test_connection.py`).
You should see `SUCCESS: connected to Seats.aero Partner API` with the HTTP status, latency and
remaining daily quota. If it says both header forms were rejected, double-check the key.
If it succeeds with "auth scheme raw", add `SEATS_AERO_AUTH_SCHEME=raw` to `.env`.

## 6. Start the dashboard
Double-click `scripts\start_dashboard.bat`. Your browser opens at <http://localhost:8501>.
Go to **Search**, review the defaults (SAN/LAX/SFO/SEA → Europe minus France, 90 days,
2 seats, ≤70k Amex points per person), press **Search Awards**. The first scan uses roughly
20–40 of your 1,000 daily API calls.

## 7. Schedule the daily scan
Open PowerShell in the project folder and run:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_task.ps1 -Time 07:30
```
This registers a Windows Task Scheduler job that runs `scripts\run_daily_scan.bat`
every day at 07:30 (change the time as you like). The PC must be **on and online**
at that time (the task is allowed to wake the PC from sleep). The dashboard does
**not** need to be open. Output is appended to `logs\scan_output.log`.

Run it once by hand to confirm: `Start-ScheduledTask -TaskName "Amex Award Hunter Daily Scan"`.

## 8. Alerts (optional)
In `.env` set **either** a webhook (Discord/Slack/Teams incoming webhook URL):
```
ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/...
```
**or** email via SMTP (Gmail requires an App Password):
```
ALERT_EMAIL_TO=you@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=you@gmail.com
```
Thresholds are editable under Settings → Alerts. Unchanged deals are never re-sent.

## Troubleshooting
| Symptom | Fix |
|---------|-----|
| `'py' is not recognized` | Reinstall Python with "Add to PATH" ticked, or use `python` instead of `py -3`. |
| `SEATS_AERO_API_KEY is not configured` | `.env` is missing or still has the placeholder. It must sit next to `app.py`. |
| HTTP 401/403 in the connection test | Wrong key, extra spaces, or the word `Bearer` pasted into the key. Confirm API access is enabled on your Pro account. |
| HTTP 429 / "budget reached" | You hit the 1,000 calls/day quota or the app's `API_DAILY_BUDGET`. Resets at midnight UTC (4–5 pm Pacific). |
| Empty results | Look at Coverage in the scan summary. "empty" means searched with nothing matching; "error"/"skipped" means not searched. Loosen filters or include unreported seat counts. |
| Dashboard port busy | `.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502` |
| Reset everything | Delete `data\awards.sqlite` (history is lost) and run a new search. |
