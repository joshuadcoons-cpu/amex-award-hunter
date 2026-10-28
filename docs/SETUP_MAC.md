# macOS setup guide

**Non-technical path:** read [START_HERE.md](../START_HERE.md) and double-click
`Install Award Hunter.command`. The rest of this page is the manual, step-by-step equivalent.

The app is plain Python and runs identically on macOS. Only the launcher scripts differ
from Windows (`.sh` instead of `.bat`, launchd instead of Task Scheduler). `scripts/setup_mac.sh`
finds Python 3.12+ or, if none is installed, downloads a private copy via `uv`.

## 1. Install Python 3.12+
Either download the macOS installer from <https://www.python.org/downloads/macos/>, or with
Homebrew: `brew install python@3.12`. Check in Terminal: `python3 --version`.

## 2. Unzip the project
Move `amex-award-hunter` somewhere simple, e.g. `~/AwardHunter/amex-award-hunter`.

## 3. First-time setup
Open Terminal, then:
```bash
cd ~/AwardHunter/amex-award-hunter
chmod +x scripts/*.sh
./scripts/setup_mac.sh
```
This creates `.venv`, installs dependencies, and copies `.env.example` to `.env`.

## 4. Add your Seats.aero API key
```bash
open -e .env
```
Replace `replace_with_your_key` with your Seats.aero Pro Partner API key and save.
`.env` is git-ignored and is the only place the key lives.

## 5. Test the connection
```bash
./scripts/test_connection.sh
```
Expect `SUCCESS: connected to Seats.aero Partner API`. If it reports auth scheme `raw`,
add `SEATS_AERO_AUTH_SCHEME=raw` to `.env`.

## 6. Start the dashboard
```bash
./scripts/start_dashboard.sh
```
Your browser opens at <http://localhost:8501>. Go to **Search** and press **Search Awards**.
Stop the dashboard with Ctrl+C in Terminal.

Tip: to launch from Finder, right-click `scripts/start_dashboard.sh` → Open With → Terminal
(or rename a copy to `start_dashboard.command`).

## 7. Schedule the daily scan (launchd)
```bash
./scripts/setup_mac_schedule.sh 07 30
```
Runs `scripts/run_daily_scan.sh` every day at 07:30 without the dashboard open. Output goes
to `logs/scan_output.log`. Run it immediately to test:
```bash
launchctl kickstart gui/$(id -u)/com.amexawardhunter.dailyscan
```
The Mac must be awake and online; if it is asleep at the scheduled time, launchd runs the
job when it next wakes. To keep it reliable, either leave the Mac on, or set a scheduled
wake in System Settings → Energy (or `sudo pmset repeat wakeorpoweron MTWRFSU 07:25:00`).

Remove the schedule:
```bash
launchctl bootout gui/$(id -u)/com.amexawardhunter.dailyscan
rm ~/Library/LaunchAgents/com.amexawardhunter.dailyscan.plist
```

## 8. Alerts
* **Mac notification banners** are on by default (`ALERT_MACOS_NOTIFICATIONS=1`); the scheduled
  scan posts them through `osascript`. Allow notifications for *Script Editor / osascript* in
  System Settings → Notifications if none appear.
* **Phone push (free):** install the ntfy app, subscribe to a hard-to-guess topic, and set
  `NTFY_TOPIC=that-name` in `.env` (self-hosted server: `NTFY_SERVER=`).
* **Webhook / email:** `ALERT_WEBHOOK_URL` or `ALERT_EMAIL_TO` + `SMTP_*` (see
  docs/SETUP_WINDOWS.md section 8).
* Test any of them: `.venv/bin/python scripts/run_daily_scan.py --test-alert` or
  Settings → Alerts → *Send test alert*.
* At most `max_alerts_per_run` (default 4) individual alerts per scan; the rest are folded into one
  digest and recorded so they never re-alert. A daily "scan finished" summary follows scheduled scans.

## Troubleshooting
| Symptom | Fix |
|---------|-----|
| `permission denied` running a script | `chmod +x scripts/*.sh` |
| `Python 3.12 or newer is required` | Install from python.org or Homebrew; make sure `python3` points to it (`which python3`). |
| `SEATS_AERO_API_KEY is not configured` | `.env` missing or still has the placeholder; it must sit next to `app.py`. |
| HTTP 401/403 | Wrong key, extra spaces, or the word `Bearer` pasted into the key. |
| HTTP 429 / budget reached | 1,000 calls/day quota or `API_DAILY_BUDGET`; resets midnight UTC (4–5 pm Pacific). |
| Port 8501 busy | `.venv/bin/python -m streamlit run app.py --server.port 8502` |
| Reset data | delete `data/awards.sqlite` and search again. |
