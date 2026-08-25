"""
build_database.py  -  PourCastAI  (Steps 2-4)   [DuckDB + robust coord parsing]

Reads the FULL Iowa Liquor Sales CSV without loading it all: DuckDB scans it and
we pull only a small SLICE (busiest items x stores, recent months) into the
shared SQLite DB.

Store coordinates in the Iowa data come in several formats (POINT (lon lat),
"(lat, lon)", or a multi-line address ending in "(lat, lon)"). We keep that
field raw and parse it in Python, which handles every format and never breaks
the SQL.

Run order:  python build_database.py   then   python simulate.py
"""
import re
import sqlite3
import random
import os
from pathlib import Path

import duckdb
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")   # optional - keyless works but
                                                    # is throttled (~500/day/IP)

# --------------------------- CONFIG -----------------------------------------
HERE     = Path(__file__).resolve().parent
RAW_CSV  = HERE / "data" / "raw" / "Iowa_Liquor_Sales.csv"
DB_PATH  = HERE / "data" / "pourcast.db"
SCHEMA   = HERE / "schema.sql"

TOP_N_ITEMS  = 25
TOP_M_STORES = 20
MONTHS_BACK  = 12
RANDOM_SEED  = 42

ANKENY_LAT, ANKENY_LONG = 41.699, -93.558
CARRIERS = [(1, "Ruan"), (2, "Simulated Carrier A"), (3, "Simulated Carrier B")]


def parse_latlon(text):
    """Pull (lat, lon) out of any Iowa 'Store Location' format. None if absent."""
    if not isinstance(text, str):
        return (None, None)
    nums = [float(x) for x in re.findall(r'-?\d+\.\d+', text)]   # decimals only (skip zips)
    lat = next((n for n in nums if 24 <= n <= 50), None)         # Iowa lat ~40-43
    lon = next((n for n in nums if -130 <= n <= -60), None)      # Iowa lon ~-90 to -97
    return (lat, lon)


# Fallback if the Census API is unreachable at build time (e.g. campus network
# blocks it, same issue noted for Socrata/AISStream elsewhere in this
# project). Covers Iowa's biggest metro counties, which is most of what a
# top-20-store slice by sales volume tends to include.
FALLBACK_COUNTY_POPULATION = {
    "POLK": 500000, "LINN": 230000, "JOHNSON": 155000, "BLACK HAWK": 130000,
    "SCOTT": 175000, "POTTAWATTAMIE": 93000, "DUBUQUE": 97000, "STORY": 100000,
    "WOODBURY": 105000, "DALLAS": 100000,
}
RURAL_POPULATION_THRESHOLD = 20_000   # documented simplification, not an official
                                       # USDA Rural-Urban Continuum Code - see schema.sql


def fetch_county_population(county_names):
    """One-time Census ACS5 pull for Iowa (state FIPS 19) county population.
    Only called once at build time, not per-request - population doesn't
    change day to day, so there's no reason to hit this live per question
    the way weather/diesel are. Returns (populations, was_live):
    populations = {COUNTY_NAME_UPPER: population}, falling back to a small
    static table for counties it's missing; was_live = whether the ACTUAL
    Census API call succeeded, independent of whether every county ended up
    populated (a column being filled doesn't mean it came from a live call -
    the fallback table fills it too)."""
    result = {}
    was_live = False
    try:
        url = "https://api.census.gov/data/2022/acs/acs5?get=NAME,B01003_001E&for=county:*&in=state:19"
        if CENSUS_API_KEY:
            url += f"&key={CENSUS_API_KEY}"
        r = requests.get(url, timeout=10)
        if not r.ok:
            # Print the ACTUAL response so a future failure is diagnosable
            # instead of a bare "JSONDecodeError" with no context - Census
            # returns plain-text/HTML error bodies, not JSON, on throttling
            # or malformed requests, which is what breaks json parsing.
            print(f"  Census returned HTTP {r.status_code}: {r.text[:200]!r}")
            r.raise_for_status()
        rows = r.json()[1:]   # first row is the header
        for name_field, pop, _state, _county_fips in rows:
            county_key = name_field.split(" County")[0].upper()
            result[county_key] = int(pop)
        was_live = True
        print(f"  Census: fetched population for {len(result)} Iowa counties (LIVE)")
    except Exception as e:
        print(f"  Census API unreachable ({type(e).__name__}: {e}) - using fallback populations")

    out = {}
    for c in county_names:
        if c is None:
            continue
        cu = c.upper()
        out[cu] = result.get(cu, FALLBACK_COUNTY_POPULATION.get(cu))
    return out, was_live


def main():
    random.seed(RANDOM_SEED)
    if not RAW_CSV.exists():
        raise SystemExit(f"Put the Kaggle CSV at {RAW_CSV} first.")

    csv = str(RAW_CSV).replace("\\", "/")
    d = duckdb.connect()

    # Cleaned view over the raw CSV. Store Location is kept RAW (parsed later).
    d.execute(f"""
        CREATE VIEW clean AS
        SELECT
            strptime("Date", '%m/%d/%Y')::DATE                       AS sale_date,
            TRY_CAST("Store Number"  AS INTEGER)                     AS store_number,
            "Store Name" AS store_name, "Address" AS address, "City" AS city,
            "Zip Code"   AS zip_code,
            TRY_CAST("County Number" AS INTEGER)                     AS county_number,
            "County" AS county,
            "Store Location" AS store_location,
            TRY_CAST("Item Number"   AS INTEGER)                     AS item_number,
            "Item Description" AS item_description,
            TRY_CAST("Category" AS INTEGER) AS category, "Category Name" AS category_name,
            TRY_CAST("Vendor Number" AS INTEGER)                     AS vendor_number,
            "Vendor Name" AS vendor_name,
            TRY_CAST("Pack" AS INTEGER) AS pack,
            TRY_CAST("Bottle Volume (ml)" AS INTEGER)                AS bottle_volume_ml,
            TRY_CAST(replace("State Bottle Cost",   '$','') AS DOUBLE) AS state_bottle_cost,
            TRY_CAST(replace("State Bottle Retail", '$','') AS DOUBLE) AS state_bottle_retail,
            TRY_CAST("Bottles Sold" AS INTEGER)                      AS bottles_sold,
            TRY_CAST(replace("Sale (Dollars)", '$','') AS DOUBLE)    AS sale_dollars,
            TRY_CAST("Volume Sold (Liters)" AS DOUBLE)               AS volume_sold_liters
        FROM read_csv_auto('{csv}', all_varchar=true, ignore_errors=true)
        WHERE "Date" IS NOT NULL AND "Store Number" IS NOT NULL AND "Item Number" IS NOT NULL
    """)

    max_date = d.execute("SELECT MAX(sale_date) FROM clean").fetchone()[0]
    cutoff = d.execute(f"SELECT (DATE '{max_date}' - INTERVAL {MONTHS_BACK} MONTH)").fetchone()[0]
    print(f"Latest sale {max_date}; keeping from {cutoff}")

    top_items = [r[0] for r in d.execute(
        "SELECT item_number FROM clean GROUP BY item_number "
        f"ORDER BY SUM(bottles_sold) DESC LIMIT {TOP_N_ITEMS}").fetchall()]
    top_stores = [r[0] for r in d.execute(
        "SELECT store_number FROM clean GROUP BY store_number "
        f"ORDER BY SUM(bottles_sold) DESC LIMIT {TOP_M_STORES}").fetchall()]
    print(f"Selected {len(top_items)} items x {len(top_stores)} stores")

    it_list = ",".join(map(str, top_items))
    st_list = ",".join(map(str, top_stores))
    d.execute(f"""CREATE VIEW slice AS SELECT * FROM clean
                  WHERE item_number IN ({it_list}) AND store_number IN ({st_list})
                    AND sale_date >= DATE '{cutoff}'""")

    # One row per key, keeping the MOST RECENT attributes (arg_max by date).
    items = d.execute("""SELECT item_number,
        arg_max(item_description, sale_date), arg_max(category, sale_date),
        arg_max(category_name, sale_date),   arg_max(vendor_number, sale_date),
        arg_max(pack, sale_date),            arg_max(bottle_volume_ml, sale_date),
        arg_max(state_bottle_cost, sale_date), arg_max(state_bottle_retail, sale_date)
        FROM slice GROUP BY item_number""").fetchall()

    store_raw = d.execute("""SELECT store_number,
        arg_max(store_name, sale_date), arg_max(address, sale_date),
        arg_max(city, sale_date), arg_max(zip_code, sale_date),
        arg_max(county_number, sale_date), arg_max(county, sale_date),
        arg_max(store_location, sale_date)
        FROM slice GROUP BY store_number""").fetchall()
    # parse coordinates in Python (robust to all formats)
    stores = []
    missing = 0
    for sn, name, addr, city, zp, cn, cty, loc in store_raw:
        lat, lon = parse_latlon(loc)
        if lat is None:
            missing += 1
        stores.append([sn, name, addr, city, zp, cn, cty, lat, lon])

    print("Fetching county population (US Census, one-time)...")
    pop_by_county, census_was_live = fetch_county_population({s[6] for s in stores})
    for s in stores:
        pop = pop_by_county.get((s[6] or "").upper())
        rural = 1 if (pop is not None and pop < RURAL_POPULATION_THRESHOLD) else 0
        s.extend([pop, rural])
    stores = [tuple(s) for s in stores]

    vendors = d.execute("""SELECT vendor_number, arg_max(vendor_name, sale_date)
        FROM slice WHERE vendor_number IS NOT NULL GROUP BY vendor_number""").fetchall()

    sales = d.execute("""SELECT CAST(sale_date AS VARCHAR), store_number, item_number,
        vendor_number, bottles_sold, sale_dollars, volume_sold_liters FROM slice""").fetchall()

    vendor_rows = []
    for vn, name in vendors:
        lead_time_days = random.randint(2, 10)
        lead_time_std = round(lead_time_days * 0.20, 1)   # documented assumption - see schema.sql
        reliability_score = round(random.uniform(0.85, 0.99), 3)
        vendor_rows.append((vn, name, lead_time_days, lead_time_std, reliability_score))

    print("Writing shared database ...")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA.read_text())
    con.executemany("INSERT INTO dim_vendor VALUES (?,?,?,?,?)", vendor_rows)
    con.executemany("INSERT INTO dim_item VALUES (?,?,?,?,?,?,?,?,?)", items)
    con.executemany("INSERT INTO dim_store VALUES (?,?,?,?,?,?,?,?,?,?,?)", stores)
    con.executemany("INSERT INTO dim_carrier VALUES (?,?)", CARRIERS)
    con.executemany("INSERT INTO fact_sales "
        "(sale_date,store_number,item_number,vendor_number,bottles_sold,sale_dollars,volume_sold_liters) "
        "VALUES (?,?,?,?,?,?,?)", sales)
    con.commit()
    # Persist WHETHER Census was actually live at build time - a store's
    # population column being non-null doesn't prove that, since the static
    # fallback table fills it too. check_data_sources.py reads this instead
    # of inferring liveness from "is the column populated".
    import datetime as _dt
    con.execute(
        "INSERT INTO ext_cache (cache_key, value, fetched_at) VALUES ('census_live', ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
        ("1" if census_was_live else "0", _dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    for t in ["dim_vendor", "dim_item", "dim_store", "dim_carrier", "fact_sales"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<22} {n:>8} rows")
    if missing:
        print(f"  (note: {missing} store(s) had no coordinates; routes to them use fallbacks)")
    con.close()
    print(f"\nDone -> {DB_PATH}\nNext: python simulate.py")


if __name__ == "__main__":
    main()
