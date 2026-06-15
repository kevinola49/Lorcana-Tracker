#!/usr/bin/env python3
"""
Lorcana Preorder/Stock Tracker
================================
Checks a list of online TCG storefronts for Disney Lorcana Booster Boxes
and Illumineer's Troves (specific sets + any newly-announced sets), and
builds an HTML dashboard + JSON history log.

USAGE:
    python3 lorcana_tracker.py

OUTPUT:
    results_latest.json   - snapshot from the most recent run
    results_history.jsonl - append-only log, one line per run
    dashboard.html         - human-readable dashboard (open in a browser)

SCHEDULING:
    See README.md for cron / Task Scheduler / Claude Code instructions.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Install with:")
    print("    pip install requests beautifulsoup4 --break-system-packages")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STORES_PATH = os.path.join(BASE_DIR, "stores.json")
LATEST_PATH = os.path.join(BASE_DIR, "results_latest.json")
HISTORY_PATH = os.path.join(BASE_DIR, "results_history.jsonl")
DASHBOARD_PATH = os.path.join(BASE_DIR, "dashboard.html")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
DOCS_DASHBOARD_PATH = os.path.join(DOCS_DIR, "index.html")
PRICE_HISTORY_PATH = os.path.join(BASE_DIR, "price_history.json")
LOG_PATH = os.path.join(BASE_DIR, "run_log.txt")


def log(msg):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def classify_status(text_blob, cfg):
    """Given a chunk of text near a product, guess its availability status."""
    blob = text_blob.lower()
    for kw in cfg["out_of_stock_keywords"]:
        if kw in blob:
            return "Sold Out / Unavailable"
    for kw in cfg["preorder_keywords"]:
        if kw in blob:
            return "Preorder Open"
    for kw in cfg["in_stock_keywords"]:
        if kw in blob:
            return "In Stock"
    return "Unknown (check manually)"


def find_matching_set(title_lower, cfg):
    for set_name in cfg["target_sets"]:
        if set_name.lower() in title_lower:
            return set_name
    return None


def looks_like_new_set(title_lower, cfg):
    """Heuristic: mentions Lorcana + booster/trove but not any known set name."""
    if "lorcana" not in title_lower:
        return False
    is_product = any(
        any(kw in title_lower for kw in kws)
        for kws in cfg["product_keywords"].values()
    )
    if not is_product:
        return False
    for known in cfg["known_sets_for_new_detection"]:
        if known in title_lower:
            return False
    return True


def find_product_type(title_lower, cfg):
    for ptype, kws in cfg["product_keywords"].items():
        if any(kw in title_lower for kw in kws):
            return ptype
    return None


PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
LIMIT_RE = re.compile(r"limit\s*(\d+)\s*per\s*(?:customer|household|order)", re.IGNORECASE)


def extract_price(card_text):
    """Pull the first plausible dollar price out of a product card's text."""
    for match in PRICE_RE.finditer(card_text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        # Ignore obviously-wrong values (e.g. "$0.00" placeholders, huge numbers)
        if 1.0 <= value <= 2000.0:
            return value
    return None


def extract_purchase_limit(card_text):
    m = LIMIT_RE.search(card_text)
    if m:
        return f"Limit {m.group(1)} per customer"
    return None


def check_msrp_flag(price, product_type, cfg, set_name=None):
    if price is None or product_type is None:
        return False
    msrp = None
    if set_name is not None:
        override = cfg.get("set_overrides", {}).get(set_name, {})
        msrp = override.get("msrp", {}).get(product_type)
    if msrp is None:
        msrp = cfg.get("msrp", {}).get(product_type)
    if msrp is None:
        return False
    return price > msrp


def is_set_enabled(set_name, cfg):
    """Sets with no override are enabled by default."""
    if set_name is None:
        return True
    override = cfg.get("set_overrides", {}).get(set_name, {})
    return override.get("enabled", True)


def check_store(store, cfg):
    """Fetch a store's pages and return a list of matched product dicts."""
    findings = []
    headers = {"User-Agent": cfg["request_settings"]["user_agent"]}
    timeout = cfg["request_settings"]["timeout_seconds"]

    for url in store["check_urls"]:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
        except Exception as e:
            log(f"  ERROR fetching {url}: {e}")
            findings.append({
                "store": store["name"],
                "region": store["region"],
                "city": store.get("city", ""),
                "url": url,
                "product_title": None,
                "matched_set": None,
                "product_type": None,
                "status": "FETCH ERROR",
                "price": None,
                "purchase_limit": None,
                "above_msrp": False,
                "is_new_set_candidate": False,
                "error": str(e),
            })
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # Heuristic: most storefronts wrap each product card in an <a> or
        # container that includes the product title text. We scan all
        # elements whose text mentions "lorcana" and treat nearby text as
        # the "card" for status keyword matching.
        candidates = soup.find_all(string=re.compile("lorcana", re.IGNORECASE))

        seen_titles = set()
        for node in candidates:
            # Walk up to a reasonably-sized container for context text
            container = node.parent
            depth = 0
            while container and depth < 4 and len(container.get_text(strip=True)) < 40:
                container = container.parent
                depth += 1
            if not container:
                continue

            card_text = container.get_text(separator=" ", strip=True)
            title_text = node.strip() if isinstance(node, str) else node.get_text(strip=True)
            title_lower = title_text.lower()

            if title_text in seen_titles:
                continue

            matched_set = find_matching_set(title_lower, cfg)
            product_type = find_product_type(title_lower, cfg)
            new_set_candidate = looks_like_new_set(title_lower, cfg)

            if not (matched_set and product_type) and not new_set_candidate:
                continue

            # Skip listings for sets the user has disabled (e.g. older sets),
            # unless this looks like a brand-new/unannounced set, which we
            # always want to surface regardless of set_overrides.
            if matched_set and not new_set_candidate and not is_set_enabled(matched_set, cfg):
                continue

            seen_titles.add(title_text)
            status = classify_status(card_text, cfg)
            price = extract_price(card_text)
            purchase_limit = extract_purchase_limit(card_text)
            above_msrp = check_msrp_flag(price, product_type, cfg, set_name=matched_set)

            findings.append({
                "store": store["name"],
                "region": store["region"],
                "city": store.get("city", ""),
                "url": url,
                "product_title": title_text,
                "matched_set": matched_set,
                "product_type": product_type,
                "status": status,
                "price": price,
                "purchase_limit": purchase_limit,
                "above_msrp": above_msrp,
                "is_new_set_candidate": new_set_candidate,
                "error": None,
            })

        time.sleep(cfg["request_settings"]["delay_between_requests_seconds"])

    return findings


def set_sort_index(set_name, cfg):
    if set_name is None:
        return len(cfg["target_sets"]) + 1  # unknown/new sets sort last
    try:
        return cfg["target_sets"].index(set_name)
    except ValueError:
        return len(cfg["target_sets"]) + 1


STATUS_SORT_ORDER = {
    "In Stock": 0,
    "Preorder Open": 1,
    "Unknown (check manually)": 2,
    "Sold Out / Unavailable": 3,
    "FETCH ERROR": 4,
}


def render_row(item, status_class, flag_html):
    title = item.get("product_title") or "(fetch error - see notes)"
    set_label = item.get("matched_set") or ("?" if item.get("is_new_set_candidate") else "-")
    ptype_label = item.get("product_type") or "-"

    if item.get("price") is not None:
        price_html = f"${item['price']:.2f}"
        if item.get("above_msrp"):
            price_html += ' <span class="badge badge-msrp">ABOVE MSRP</span>'
    else:
        price_html = "-"

    limit_html = item.get("purchase_limit") or "-"

    if item.get("price_history_text"):
        trend_html = f"{item.get('price_trend_arrow', '')} {item['price_history_text']}"
    else:
        trend_html = "-"

    return f"""
        <tr class="{status_class}">
          <td>{item['store']}<br><span class="muted">{item.get('region','')} &middot; {item.get('city','')}</span></td>
          <td>{title}</td>
          <td>{set_label}</td>
          <td>{ptype_label}</td>
          <td>{item['status']}</td>
          <td>{price_html}</td>
          <td>{limit_html}</td>
          <td>{trend_html}</td>
          <td>{flag_html}</td>
          <td><a href="{item['url']}" target="_blank">Visit</a></td>
        </tr>"""


def build_dashboard(latest_run, previous_run, cfg):
    """Generate dashboard.html from latest results, highlighting changes."""

    prev_index = {}
    if previous_run:
        for item in previous_run.get("results", []):
            key = (item["store"], item.get("product_title"), item.get("url"))
            prev_index[key] = item.get("status")

    new_set_alerts = []
    status_change_alerts = []
    msrp_alerts = []
    available_rows = []
    soldout_rows = []

    # Sort: by set release order, then status priority, then store name
    def sort_key(item):
        return (
            set_sort_index(item.get("matched_set"), cfg),
            STATUS_SORT_ORDER.get(item["status"], 9),
            item["store"],
        )

    sorted_results = sorted(latest_run["results"], key=sort_key)

    for item in sorted_results:
        key = (item["store"], item.get("product_title"), item.get("url"))
        prev_status = prev_index.get(key)
        is_changed = prev_status is not None and prev_status != item["status"]
        is_first_seen = prev_status is None

        if item.get("is_new_set_candidate"):
            new_set_alerts.append(item)

        if is_changed and item["status"] in ("Preorder Open", "In Stock"):
            status_change_alerts.append((item, prev_status))

        if item.get("above_msrp"):
            msrp_alerts.append(item)

        status = item["status"]
        if status == "In Stock":
            status_class = "status-instock"
        elif status == "Preorder Open":
            status_class = "status-preorder"
        elif status == "FETCH ERROR":
            status_class = "status-error"
        elif status == "Sold Out / Unavailable":
            status_class = "status-soldout"
        else:
            status_class = "status-unknown"

        flag_html = ""
        if item.get("is_new_set_candidate"):
            flag_html += '<span class="badge badge-newset">POSSIBLE NEW SET</span> '
        if is_changed:
            flag_html += f'<span class="badge badge-changed">CHANGED: {prev_status} &rarr; {status}</span> '
        elif is_first_seen:
            flag_html += '<span class="badge badge-new">NEW LISTING</span> '

        row_html = render_row(item, status_class, flag_html)

        if status == "Sold Out / Unavailable":
            soldout_rows.append(row_html)
        else:
            available_rows.append(row_html)

    alerts_html = ""
    if new_set_alerts or status_change_alerts or msrp_alerts:
        alert_items = []
        for item, prev in status_change_alerts:
            alert_items.append(
                f"<li><strong>{item['store']}</strong>: "
                f"\"{item.get('product_title')}\" changed from "
                f"<em>{prev}</em> to <em>{item['status']}</em>. "
                f"<a href='{item['url']}' target='_blank'>View</a></li>"
            )
        for item in new_set_alerts:
            alert_items.append(
                f"<li><strong>{item['store']}</strong>: possible newly announced product "
                f"\"{item.get('product_title')}\" "
                f"(status: {item['status']}). "
                f"<a href='{item['url']}' target='_blank'>View</a></li>"
            )
        for item in msrp_alerts:
            alert_items.append(
                f"<li><strong>{item['store']}</strong>: "
                f"\"{item.get('product_title')}\" is priced at "
                f"${item['price']:.2f}, above typical MSRP "
                f"(${cfg['msrp'].get(item.get('product_type'), 0):.2f}). "
                f"<a href='{item['url']}' target='_blank'>View</a></li>"
            )
        alerts_html = f"""
        <div class="alert-box">
          <h2>Attention Needed</h2>
          <ul>{''.join(alert_items)}</ul>
        </div>"""

    errors = [r for r in latest_run["results"] if r["status"] == "FETCH ERROR"]
    errors_html = ""
    if errors:
        items = "".join(
            f"<li>{e['store']} &mdash; {e['url']} &mdash; {e.get('error','')}</li>"
            for e in errors
        )
        errors_html = f"""
        <div class="error-box">
          <h2>Stores That Failed to Load This Run</h2>
          <ul>{items}</ul>
          <p class="muted">These sites may block automated requests, have changed their
          page structure, or be temporarily down. Check them manually if needed.</p>
        </div>"""

    run_time = latest_run["run_timestamp_local_note"]

    table_header = """
      <tr>
        <th>Store</th>
        <th>Product Listing</th>
        <th>Set</th>
        <th>Product Type</th>
        <th>Status</th>
        <th>Price</th>
        <th>Limit</th>
        <th>Price Trend (last 3)</th>
        <th>Flags</th>
        <th>Link</th>
      </tr>"""

    soldout_section = ""
    if soldout_rows:
        soldout_section = f"""
  <h2 class="section-heading">Sold Out / Unavailable</h2>
  <table>
    <thead>{table_header}</thead>
    <tbody>
      {''.join(soldout_rows)}
    </tbody>
  </table>"""


    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Lorcana Booster Box / Trove Tracker</title>
<style>
  :root {{
    --steel-navy: #1b2a38;
    --steel-blue: #3d5a73;
    --steel-blue-light: #6f93ad;
    --accent-orange: #e8762c;
    --bg-light: #f4f6f8;
    --white: #ffffff;
    --text-dark: #1b2a38;
    --muted: #6c7a89;
    --green: #2e7d32;
    --green-bg: #e6f4ea;
    --amber: #b6790a;
    --amber-bg: #fdf1de;
    --red: #b3261e;
    --red-bg: #fbeae8;
    --gray-bg: #eceff1;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    margin: 0;
    background: var(--bg-light);
    color: var(--text-dark);
  }}
  header {{
    background: var(--steel-navy);
    color: var(--white);
    padding: 24px 32px;
    border-bottom: 4px solid var(--accent-orange);
  }}
  header h1 {{
    margin: 0;
    font-size: 1.5rem;
    letter-spacing: 0.02em;
  }}
  header p {{
    margin: 6px 0 0;
    color: var(--steel-blue-light);
    font-size: 0.9rem;
  }}
  main {{
    padding: 24px 32px;
    max-width: 1200px;
    margin: 0 auto;
  }}
  .alert-box, .error-box {{
    background: var(--white);
    border-left: 6px solid var(--accent-orange);
    border-radius: 4px;
    padding: 16px 20px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  .error-box {{
    border-left-color: var(--steel-blue);
  }}
  .alert-box h2, .error-box h2 {{
    margin-top: 0;
    font-size: 1.05rem;
    color: var(--steel-navy);
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--white);
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    border-radius: 4px;
    overflow: hidden;
  }}
  th, td {{
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid var(--gray-bg);
    font-size: 0.9rem;
    vertical-align: top;
  }}
  th {{
    background: var(--steel-blue);
    color: var(--white);
    position: sticky;
    top: 0;
  }}
  tr.status-instock {{ background: var(--green-bg); }}
  tr.status-preorder {{ background: var(--amber-bg); }}
  tr.status-soldout {{ background: var(--gray-bg); }}
  tr.status-error {{ background: var(--red-bg); }}
  .muted {{ color: var(--muted); font-size: 0.8rem; }}
  .badge {{
    display: inline-block;
    font-size: 0.7rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 12px;
    color: var(--white);
    margin-bottom: 2px;
  }}
  .badge-newset {{ background: var(--accent-orange); }}
  .badge-changed {{ background: var(--green); }}
  .badge-new {{ background: var(--steel-blue); }}
  .badge-msrp {{ background: var(--red); }}
  .section-heading {{
    color: var(--steel-navy);
    margin: 28px 0 10px;
    font-size: 1.1rem;
    border-bottom: 2px solid var(--accent-orange);
    padding-bottom: 4px;
  }}
  footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
    padding: 24px;
  }}
</style>
</head>
<body>
<header>
  <h1>Lorcana Booster Box &amp; Illumineer's Trove Tracker</h1>
  <p>Last run: {run_time} &middot; Tracking CA-priority + nationwide US storefronts</p>
</header>
<main>
{alerts_html}
{errors_html}
  <table>
    <thead>{table_header}</thead>
    <tbody>
      {''.join(available_rows) if available_rows else '<tr><td colspan="10">No matching products found this run.</td></tr>'}
    </tbody>
  </table>
{soldout_section}
</main>
<footer>
  Generated by lorcana_tracker.py. Edit stores.json and config.json to customize.
</footer>
</body>
</html>"""

    with open(DASHBOARD_PATH, "w") as f:
        f.write(html)

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(DOCS_DASHBOARD_PATH, "w") as f:
        f.write(html)


def load_price_history():
    if os.path.exists(PRICE_HISTORY_PATH):
        try:
            return load_json(PRICE_HISTORY_PATH)
        except Exception:
            return {}
    return {}


def price_history_key(item):
    return f"{item['store']}|{item.get('product_title')}|{item.get('url')}"


def update_price_history(results, history, run_timestamp):
    """Append today's price to each item's history (max 10 entries kept),
    and attach a trend arrow + last-3-prices summary to each result dict."""
    for item in results:
        if item.get("price") is None:
            continue
        key = price_history_key(item)
        entries = history.get(key, [])
        entries.append({"timestamp": run_timestamp, "price": item["price"]})
        entries = entries[-10:]
        history[key] = entries

        prices = [e["price"] for e in entries]
        if len(prices) >= 2:
            if prices[-1] > prices[-2]:
                arrow = "\u2191"  # up
            elif prices[-1] < prices[-2]:
                arrow = "\u2193"  # down
            else:
                arrow = "\u2192"  # flat
        else:
            arrow = "\u2192"

        last_three = prices[-3:]
        history_text = " \u2192 ".join(f"${p:.2f}" for p in last_three)
        item["price_trend_arrow"] = arrow
        item["price_history_text"] = history_text

    return history


def main():
    cfg = load_json(CONFIG_PATH)
    stores_data = load_json(STORES_PATH)

    run_timestamp = datetime.now(timezone.utc).isoformat()
    run_note = datetime.now().strftime("%Y-%m-%d %I:%M %p (local time)")

    log(f"Starting run at {run_timestamp}")

    all_results = []
    for store in stores_data["stores"]:
        log(f"Checking {store['name']} ({store['region']})...")
        findings = check_store(store, cfg)
        log(f"  -> {len(findings)} matching item(s) found")
        all_results.extend(findings)

    latest_run = {
        "run_timestamp_utc": run_timestamp,
        "run_timestamp_local_note": run_note,
        "results": all_results,
    }

    previous_run = None
    if os.path.exists(LATEST_PATH):
        try:
            previous_run = load_json(LATEST_PATH)
        except Exception:
            previous_run = None

    price_history = load_price_history()
    update_price_history(all_results, price_history, run_timestamp)
    with open(PRICE_HISTORY_PATH, "w") as f:
        json.dump(price_history, f, indent=2)

    with open(LATEST_PATH, "w") as f:
        json.dump(latest_run, f, indent=2)

    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(latest_run) + "\n")

    build_dashboard(latest_run, previous_run, cfg)
    log(f"Run complete. Dashboard written to {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
