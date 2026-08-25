"""
export_store_coords.py  -  PourCastAI (Step 5, part 1)

dim_store lives only in your local SQLite -- HubSpot Companies don't (and
shouldn't) hold GPS coordinates, same as a real CRM wouldn't. OSRM/Open-Meteo
need lat/long per store to run in Databricks, so this exports just the
columns needed: store_number, store_name, latitude, longitude.

This is reference/dimension data (store locations don't change day to day),
so a one-off CSV upload is the right call here -- same reasoning your
project already used for fetching Census population once at build time
instead of per-request.

Run:  python export_store_coords.py
Produces: store_coords.csv  (upload this to a Databricks Volume next)
"""
import csv
from db import connect

con = connect()
rows = con.execute(
    "SELECT store_number, store_name, latitude, longitude, population, rural_flag, county "
    "FROM dim_store WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
).fetchall()
con.close()

with open("store_coords.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["store_number", "store_name", "latitude", "longitude",
                      "population", "rural_flag", "county"])
    for r in rows:
        writer.writerow([r["store_number"], r["store_name"], r["latitude"], r["longitude"],
                          r["population"], r["rural_flag"], r["county"]])

print(f"Wrote store_coords.csv with {len(rows)} stores.")
print("Next: upload this file to a Databricks Volume (instructions follow).")
