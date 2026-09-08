"""
add_warehouse_table.py  -  ONE-TIME setup. Adds warehouse_inventory as a new
table in the shared pourcast.db. Purely additive - does not touch, drop, or
modify any existing table or view. Safe to re-run (IF NOT EXISTS).
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "pourcast.db"

SQL = """
CREATE TABLE IF NOT EXISTS warehouse_inventory (
    item_number         INTEGER PRIMARY KEY,   -- shared key -> dim_item
    item_name           TEXT,
    on_hand             INTEGER,   -- SIMULATED warehouse stock (see warehouse_simulate.py)
    min_count           INTEGER,   -- safety stock, aggregated across all tracked stores
    max_count           INTEGER,   -- order-up-to level (28-day supply + safety stock)
    reorder_point       INTEGER,   -- same (s,S) formula as store-level, aggregated demand
    last_delivery_date  TEXT,      -- most recent factory->warehouse delivery (simulated)
    FOREIGN KEY (item_number) REFERENCES dim_item(item_number)
);
"""

con = sqlite3.connect(DB_PATH)
con.executescript(SQL)
con.commit()
con.close()
print("warehouse_inventory table created (or already existed). No other tables touched.")
