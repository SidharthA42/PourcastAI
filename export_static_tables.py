"""
export_static_tables.py  -  PourCastAI (Step 6, part 1)

Exports the tables that were never going to come from HubSpot or a live API:
dim_item, dim_vendor, dim_carrier (catalog/reference data) and fact_sales
(historical REAL transactions -- the whole reason simulate.py exists).

These don't change day-to-day the way HubSpot deals or weather forecasts do,
so a one-time CSV upload to a Volume is the right call, same reasoning as
store_coords.csv in Step 5.

Run:  python export_static_tables.py
Produces: dim_item.csv, dim_vendor.csv, dim_carrier.csv, fact_sales.csv
"""
import csv
from db import connect

TABLES = ["dim_item", "dim_vendor", "dim_carrier", "fact_sales", "fact_inventory_snapshot"]

con = connect()
for table in TABLES:
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"  {table}: 0 rows, skipping")
        continue
    columns = rows[0].keys()
    with open(f"{table}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([r[c] for c in columns])
    print(f"  {table}: wrote {len(rows)} rows -> {table}.csv")

# Diesel price is a single cached value (kept fresh locally by n8n, per the
# Step 1 decision -- EIA is blocked from Databricks compute). This is a
# write-through snapshot, not a live feed: Gold will use whatever value was
# cached the last time this export ran.
diesel_row = con.execute(
    "SELECT value, fetched_at FROM ext_cache WHERE cache_key='diesel_price'").fetchone()
live_row = con.execute(
    "SELECT value FROM ext_cache WHERE cache_key='diesel_price_live'").fetchone()
with open("diesel_price.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["price", "fetched_at", "is_live"])
    if diesel_row:
        was_live = bool(live_row and live_row["value"] == "1")
        writer.writerow([diesel_row["value"], diesel_row["fetched_at"], was_live])
    else:
        writer.writerow([3.80, "", False])   # same static fallback risk_tools.py uses
print("  diesel_price: wrote 1 row -> diesel_price.csv")
con.close()

print("\nUpload all 4 CSVs to the same Volume as store_coords.csv "
      "(Catalog -> pourcastai -> bronze -> landing -> Upload).")
