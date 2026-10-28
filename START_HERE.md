# Amex Award Hunter — Start here (Mac)

This tool quietly checks Seats.aero every morning for **business-class award seats for two
from Southern California to Europe**, works out how many **Amex Membership Rewards points** each
one costs, pairs flights into complete round trips, ranks them with **the least cash out of pocket
first**, and sends you an alert when something is worth jumping on.

It never books anything and never moves your points. It finds; you verify and book.

---

## Install (about 5 minutes, one time)

1. **Put the folder somewhere permanent.** Drag the `amex-award-hunter` folder out of your
   Downloads into your home folder or Documents. (If it stays in Downloads and you tidy up later,
   the scheduled scan stops working.)

2. **Double-click `Install Award Hunter.command`.**
   A Terminal window opens and walks you through everything. Press Return when it asks.

   * If your Mac says it *"cannot be opened because it is from an unidentified developer"*:
     **right-click** the file, choose **Open**, then click **Open** in the dialog. That only has to
     be done once.
   * If that still does not work, open the **Terminal** app (Spotlight → type Terminal), type
     `bash ` (with a space), drag `Install Award Hunter.command` into the window, and press Return.

3. The installer will:
   * set up Python privately inside the folder (it downloads one if your Mac has none),
   * check your Seats.aero key (it is already in the `.env` file; the installer asks only if it is missing),
   * offer to schedule a scan **every morning at 7:30**,
   * run the first scan, and
   * open the dashboard in your browser.

That's it. Leave your Mac on (or just asleep with the lid open) and it does the rest.

---

## Day to day

| Want to… | Do this |
|---|---|
| See the best deals | Double-click **`Open Award Hunter.command`** (the page is `http://localhost:8501`). Keep that window open while you browse; close it when done. |
| Scan right now | Double-click **`Scan Now.command`**, or press **Search Awards** in the dashboard. |
| Change the morning time or turn it off | Double-click **`Schedule Daily Scan.command`**. |
| Get the newest version after Claude changes the code | Double-click **`Update Award Hunter.command`**. It pulls the latest code from GitHub and keeps your key, data and logs. No zip needed. |
| Get alerts on your phone | See *Alerts* below. |

### The dashboard (four pages)
* **Home** — opens first. What the tool does, how it works, and how deals are scored, with the live
  numbers and a button to Best Deals.
* **Best Deals** — the home page. The big number is the lowest cash you'd pay for a complete
  round trip for two. Below it, cards for the best trips; switch the ranking between
  *Least cash*, *Fewest points*, *All-in cost* and *Best overall*. Each card has a
  **Verify & book** link to the airline program. **Scan now** is in the top-right corner.
* **Search** — where you fly from, which countries, dates, trip length, and your caps on cash
  and points. Press **Search Awards**. Everything else is under *Advanced*.
* **Settings** — alerts (with a **Send test alert** button), the *Minimize cash / Balanced /
  Minimize points* switch, and connection status.

### Watchlist: "tell me the moment seats open for THIS trip"
On **Best Deals**, under *Watchlist → Add a watch*, pick a country (or airports) and a departure window,
for example "Rome, any departure May 1–20, nonstop". From then on every scan covers those dates and
airports, and the moment two business seats appear you get an urgent alert, whatever the score.
Each watch shows how many matches exist right now; pause or delete it any time.

### Sweet-spot badge
A ★ **sweet spot** badge means the award is priced at that program's realistic floor for West Coast → Europe
business (for example Aeroplan at 60–70k, Flying Blue at 50k or less). ★ **best price** means at or under the
lowest level ever seen. Those are the ones to act on fast.

### Your points balance
Open **My Amex points** in the sidebar and enter your Membership Rewards balance (and, optionally, how many
points you earn a month). Every trip then says **you have enough** or **short 40,000 · ~2 mo**, the hero
tile shows it, and a **"Only what I can afford"** box filters the list.

### Amex transfer bonuses
Amex regularly offers 15–40% extra miles when you transfer to a specific airline. The tool checks a public
tracker every scan and lists what's running on the **Home** page (and under "Amex transfer bonuses running
now" on Best Deals). Because these offers are often targeted, nothing is applied automatically: open your
Amex account, look at the transfer page, and if the bonus is there press **"It's in my account → apply"**.
From then on the points math uses it, cards show a **+25% bonus** badge, and the next scan re-scores with it.
You also get an alert the day a new bonus appears for an airline the tool searches.

### Alerts
* **Mac notification banners** are on automatically. When the morning scan finds something good,
  a banner appears in the top-right corner. (If none appear, open *System Settings → Notifications*
  and allow notifications for *Script Editor* / *osascript*.)
* **Push alerts on your iPhone, free:** install the **ntfy** app from the App Store, tap **+** and
  subscribe to a topic with a name nobody would guess (for example `mark-awards-7h3k`). Then open
  the file `.env` in the project folder with TextEdit, put the same name after `NTFY_TOPIC=`, save,
  and in the dashboard go to *Settings → Alerts → Send test alert*.
* At most 4 individual alerts per scan; anything beyond that is folded into one
  "more deals" message so you are never flooded. The same deal is never sent twice.
* After each morning scan a short *"scan finished"* summary is sent so you know it ran.

### What "worth jumping on" means (defaults; change them in Settings → Alerts)
* A complete round trip for two with **$600 or less** in total taxes/fees, **320,000 points or
  fewer**, and a score of 78+.
* A single award scoring 80+ (88+ is flagged *exceptional*).
* Prices only count when the program reports seat counts and fees; missing data is never
  treated as good news.

---

## Before you transfer points
Every result is a cached observation from Seats.aero, not a reservation. **Always open the
airline program, confirm both seats are bookable at that price, and check the taxes on the
payment page before transferring Amex points.** Transfers are irreversible.

---

## If something goes wrong
| What you see | What to do |
|---|---|
| "No API key" or 401/403 in the connection test | Open `.env` with TextEdit; the line `SEATS_AERO_API_KEY=` must have your key with no spaces and no word "Bearer". |
| "budget reached" or HTTP 429 | You used the day's 1,000 Seats.aero calls. It resets at midnight UTC (4–5 pm Pacific). |
| The morning scan didn't run | The Mac was off or fully asleep. It runs at the next wake. To wake it on schedule: *System Settings → Energy* (or `sudo pmset repeat wakeorpoweron MTWRFSU 07:25:00` in Terminal). |
| Dashboard page is blank | Make sure the `Open Award Hunter.command` window is still open, then reload the page. |
| Nothing qualifies | On the Search page, raise *Max Amex points per person* or *Max cash fees*, widen the window, or allow more connections. |
| Start over | Delete `data/awards.sqlite` (history is lost) and run a scan. |

The log of every scheduled scan is in `logs/scan_output.log`.
