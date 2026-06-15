# Lorcana Booster Box & Illumineer's Trove Tracker

> **Running this from an iPhone with no computer?** See
> `IPHONE_SETUP.md` for step-by-step instructions to run this for free
> via GitHub Actions + GitHub Pages, with a bookmarkable dashboard URL.
> The rest of this README covers running it locally on a Mac/Linux/
> Windows computer.

A simple Python tool that checks a list of online TCG storefronts for
**Disney Lorcana Booster Boxes** and **Illumineer's Troves**, across the
sets you care about (The First Chapter through Attack of the Vine, plus
detection of newly-announced sets), and produces:

- `dashboard.html` — open this in any browser. Sorted by **set release
  order** (First Chapter → ... → Attack of the Vine → new/unknown sets),
  color-coded by status (in stock / preorder / sold out / unknown), with
  alerts at the top for new listings, status changes, possible new-set
  announcements, and items priced above MSRP. Sold Out / Unavailable
  items are pushed into their own section at the bottom so they don't
  clutter the main view.
- `results_latest.json` — machine-readable snapshot of the latest run.
- `results_history.jsonl` — one line per run, full history over time.
- `price_history.json` — per-listing price history (last 10 runs).
- `run_log.txt` — plain log of what happened each run.

### Each dashboard row also shows
- **Price**, with an **ABOVE MSRP** badge if it exceeds the typical price
  set in `config.json` (`$99.99` for Booster Boxes, `$49.99` for Troves
  by default — edit these to match what you consider fair).
- **Purchase limit** (e.g. "Limit 2 per customer"), when the store lists one.
- **Price trend** — an up/down/flat arrow plus the last 3 recorded prices
  for that listing, e.g. `↓ $99.99 → $109.99 → $104.99`.

---

## 1. One-time setup

You'll need Python 3 installed. Then, from this folder, run:

```bash
pip install requests beautifulsoup4 --break-system-packages
```

(Drop `--break-system-packages` if you're using a virtual environment —
recommended if you have one.)

---

## 2. Running it manually

```bash
python3 lorcana_tracker.py
```

Then open `dashboard.html` in your browser. Each run overwrites
`dashboard.html` and `results_latest.json`, and appends to
`results_history.jsonl`.

---

## 3. Customizing what it checks

### Store list status
`stores.json` currently has **25 stores** (3 CA-local + 22 nationwide
shippers), covering 38 check URLs. About half are marked
`"verified": true` (confirmed via research to show Lorcana product at
that exact URL) and half `"verified": false` with a `"notes"` field
explaining that the collection-page URL is a best guess based on common
Shopify patterns. Unverified entries that 404 or 403 will simply show up
in the "Stores That Failed to Load This Run" box - fix the URL (usually
just the last segment of the path, e.g. `/collections/lorcana` vs.
`/collections/disney-lorcana`) by visiting the site, searching "Lorcana",
and copying the resulting collection URL.

Getting to 100 stores is realistic over time, but padding the list with
many more guessed URLs right now would mostly produce fetch errors. The
easiest way to keep growing it:
- Ask me for another research batch any time ("find 15 more nationwide
  TCG stores") and I'll search and append more entries the same way.
- Add your own favorites directly - same format as above.

### Add/remove stores — edit `stores.json`
Each store needs:
- `name` — display name
- `region` — "CA" or "US" (just a label shown on the dashboard)
- `city` — free text, e.g. "Sacramento, CA" or "Ships nationwide"
- `check_urls` — one or more URLs to the store's Lorcana / sealed product
  collection or search page

To find a good URL for a new store: go to their site, search "Lorcana",
and copy the URL of the resulting category/search page.

### Add/remove target sets or product types — edit `config.json`
- `target_sets` — list of Lorcana set names to watch for, **in release
  order** (this also controls dashboard sort order)
- `product_keywords` — phrases that identify a Booster Box vs. an
  Illumineer's Trove listing
- `msrp` — default typical price per product type; listings priced above
  this get an **ABOVE MSRP** badge and an alert
- `set_overrides` — per-set controls:
  - `"enabled": false` completely excludes that set's Booster
    Box/Trove listings from results, alerts, and price history (as if
    you removed it from `target_sets`, but without losing your place in
    the release-order sort if you re-enable it later). **By default,
    every set released in 2025 or earlier is disabled** (First Chapter
    through Whispers in the Well); Winterspell, Wilds Unknown, and Attack
    of the Vine (all 2026 releases) remain enabled.
  - `"msrp": {...}` (optional) overrides the global `msrp` values for
    that specific set only — useful since older sets trade differently
    than brand-new releases.
  - To re-enable an older set (e.g. if you're hunting a reprint), just
    flip its `"enabled"` to `true`.
- `known_sets_for_new_detection` — used to flag listings that mention
  "Lorcana" + "booster/trove" but don't match any known set — these get
  flagged as **POSSIBLE NEW SET** so you find out about new
  announcements automatically. New-set candidates always show up
  regardless of `set_overrides`, since by definition they're not in that
  list yet.

### A note on price/limit extraction
Price and "Limit X per customer" text are pulled from the surrounding
product card via simple pattern matching. Most Shopify-style stores show
both right on the collection page, so this usually works well — but if a
store hides price behind a "view item" click, price/limit may show as
"-". Price history only accumulates once a price is successfully read.

---

## 4. Scheduling it to run automatically ("a few times a day")

The script itself doesn't run in the background — your computer's
scheduler runs it for you. Pick whichever matches your OS:

### macOS / Linux (cron)
1. Open your crontab:
   ```bash
   crontab -e
   ```
2. Add a line to run it at 8am, 1pm, and 6pm every day (adjust the
   path to wherever you put this folder):
   ```cron
   0 8,13,18 * * * cd /full/path/to/lorcana_tracker && /usr/bin/python3 lorcana_tracker.py >> cron.log 2>&1
   ```
3. Save and exit. To check it ran, look at `run_log.txt` or
   `results_history.jsonl`.

### Windows (Task Scheduler)
1. Open **Task Scheduler** → **Create Basic Task**.
2. Name it "Lorcana Tracker", set trigger to **Daily**, and under
   "Advanced" set it to repeat every few hours.
3. Action: **Start a program**
   - Program/script: `python` (or full path to `python.exe`)
   - Add arguments: `lorcana_tracker.py`
   - Start in: the full path to this folder
4. Save. Right-click the task → **Run** to test it.

### Using Claude Code
If you have Claude Code installed, you can ask it to run
`python3 lorcana_tracker.py` in this folder on a schedule using your OS
scheduler as above — Claude Code can help you set up the cron entry or
Task Scheduler task interactively, and can re-run the script on demand
whenever you want a fresh check.

### Adjusting frequency
Just change how often the scheduled task fires — e.g. for hourly checks
during a known preorder window, use:
```cron
0 * * * * cd /full/path/to/lorcana_tracker && /usr/bin/python3 lorcana_tracker.py >> cron.log 2>&1
```

---

## 5. A note on reliability

Some storefronts (especially larger ones using Cloudflare or similar
bot-protection) may occasionally return errors to automated requests
even though the page loads fine in a browser. When this happens:

- The store will show up in the **"Stores That Failed to Load This Run"**
  box on the dashboard with the error message.
- This doesn't mean the product isn't available — just that this run
  couldn't check it. It's usually intermittent; subsequent runs often
  succeed.
- If a specific store consistently fails, try checking it manually, or
  let me know and I can add a fallback fetching method (e.g.
  `cloudscraper`) for that site.

Because storefront HTML changes over time and varies by platform
(Shopify vs. custom sites), the "status" detection is a best-effort
heuristic. Treat **"Unknown (check manually)"** as "go look at the
listing yourself" rather than "not available."

---

## 6. Adding your own local CA shops

Just add an entry to `stores.json` like:

```json
{
  "name": "Your Local Shop",
  "region": "CA",
  "city": "Your City, CA",
  "check_urls": ["https://theirsite.com/collections/lorcana"]
}
```

No code changes needed — the next run will pick it up automatically.
