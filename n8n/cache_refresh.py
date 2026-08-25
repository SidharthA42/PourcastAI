"""
n8n/cache_refresh.py  -  called by the n8n "API cache refresh" workflow.

Pulls the live diesel price ONCE and writes it into the shared DB's
ext_cache table. risk_tools.get_diesel_price() reads that cache first,
so a chat question never has to wait on the EIA API mid-conversation -
n8n has already done that polling in the background, on its own schedule.

IMPORTANT: this calls risk_tools.fetch_diesel_price_live(), which always
does a real fetch attempt - NOT risk_tools.get_diesel_price(), which checks
the cache first. Calling the cache-checking version here would mean this
"refresh" job just re-reads whatever's already cached and rewrites it with
a new timestamp forever, never actually re-fetching - a stale or wrong
value would perpetuate itself indefinitely instead of being corrected.

Run manually to test:  python n8n/cache_refresh.py
n8n runs this via an Execute Command node (see n8n/workflows/*.json).
"""
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # import db.py, risk_tools.py

from db import connect
import risk_tools


def refresh_diesel_cache():
    con = connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS ext_cache (
            cache_key TEXT PRIMARY KEY, value TEXT, fetched_at TEXT)
    """)
    price = risk_tools.fetch_diesel_price_live()   # ALWAYS a real fetch attempt
    now = dt.datetime.now().isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO ext_cache (cache_key, value, fetched_at) VALUES ('diesel_price', ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
        (str(price["price"]), now))
    # Store whether THIS fetch was actually live, honestly - a cached
    # fallback value should keep reporting itself as a fallback, not "live".
    con.execute(
        "INSERT INTO ext_cache (cache_key, value, fetched_at) VALUES ('diesel_price_live', ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
        ("1" if price["is_live"] else "0", now))
    con.commit()
    con.close()
    print(f"[cache_refresh] diesel_price={price['price']} live={price['is_live']} at {now}")


if __name__ == "__main__":
    refresh_diesel_cache()
