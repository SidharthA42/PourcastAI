"""
simulate.py  -  PourCastAI  (Step 5)

The Iowa dataset has SALES but no inventory levels and no shipments. This script
GENERATES those two tables - but derived from the REAL sales, so they stay
believable. That is the key point for your report's methodology:

    We do not invent inventory out of thin air. We start each store+item with a
    plausible opening stock, deplete it every day by the REAL bottles sold, and
    place a replenishment order (a shipment) whenever stock falls below the
    reorder point. Every simulated shipment exists because a real sale drained
    the shelf. Orders ship from the real Ankeny warehouse to the real store
    coordinates.

Reads:  fact_sales, dim_item, dim_store, dim_vendor  (from the shared DB)
Writes: fact_inventory_snapshot, fact_shipments

Run AFTER build_database.py:
    python simulate.py
"""

import math
import random
import sqlite3
import os
import statistics
from pathlib import Path
from datetime import datetime, timedelta

HERE    = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "pourcast.db"

RANDOM_SEED = 42   # default: reproducible, so teammates regenerating the DB match.


def resolve_seed():
    """Pick the RNG seed.

    Default (env unset)          -> 42, so `python simulate.py` by hand always
                                    produces the SAME data across machines.
    SIMULATE_SEED=daily          -> seed derived from today's date, so a nightly
                                    scheduled run produces GENUINELY DIFFERENT
                                    inventory/shipments each night (not the same
                                    rows with the dates bumped).
    SIMULATE_SEED=<integer>      -> use that exact seed.
    """
    val = os.getenv("SIMULATE_SEED", "").strip().lower()
    if val in ("", "42"):
        return RANDOM_SEED
    if val == "daily":
        from datetime import date
        return int(date.today().strftime("%Y%m%d"))   # e.g. 20260827
    try:
        return int(val)
    except ValueError:
        return RANDOM_SEED

# Opening stock and order size are still simple day-count rules - those are
# starting/refill conventions, not the "should we reorder" decision itself.
OPENING_DAYS = 21      # start with ~3 weeks of stock
ORDER_DAYS   = 28      # each order brings ~4 weeks of stock

# The reorder DECISION below is a statistical (s, S) safety-stock model,
# adapted from a teammate's inventory agent (Vishnu) and reimplemented
# against this project's schema. Replaces the old flat "reorder at 14 days
# of cover" rule, which couldn't distinguish a steady seller from a wildly
# variable one - both got the same 14-day buffer even though the variable
# one needs more.
Z_SERVICE_LEVEL = 1.645   # standard normal quantile for a 95% one-tailed
                          # service level (equivalent to scipy.stats.norm.ppf(0.95);
                          # hardcoded to avoid adding scipy for one constant)

ANKENY_LAT, ANKENY_LONG = 41.699, -93.558


def reorder_point(demand_avg, demand_std, lead_time_avg, lead_time_std):
    """Safety stock sized to BOTH demand variability and lead-time
    variability (an unpredictable seller, or a vendor with inconsistent
    delivery times, both warrant more buffer - a flat day-count threshold
    can't tell the two apart). Returns (safety_stock, reorder_point), both
    in whole bottles."""
    variance = (lead_time_avg * demand_std**2) + (demand_avg**2 * lead_time_std**2)
    safety_stock = Z_SERVICE_LEVEL * math.sqrt(max(variance, 0))
    rop = demand_avg * lead_time_avg + safety_stock
    return math.ceil(safety_stock), math.ceil(rop)


def daterange(start, end):
    """Yield every date from start to end inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    seed = resolve_seed()
    random.seed(seed)
    print(f"[simulate] RNG seed = {seed} "
          f"({'daily/varied' if seed != RANDOM_SEED else 'default/reproducible'})")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # clear any previous simulation so re-runs are clean
    con.execute("DELETE FROM fact_inventory_snapshot")
    con.execute("DELETE FROM fact_shipments")

    # look-ups we need
    vendor_lead = {r["vendor_number"]: r["lead_time_days"]
                   for r in con.execute("SELECT vendor_number, lead_time_days FROM dim_vendor")}
    vendor_lead_std = {r["vendor_number"]: r["lead_time_std"]
                       for r in con.execute("SELECT vendor_number, lead_time_std FROM dim_vendor")}
    item_vendor = {r["item_number"]: r["vendor_number"]
                   for r in con.execute("SELECT item_number, vendor_number FROM dim_item")}
    store_xy = {r["store_number"]: (r["latitude"], r["longitude"])
                for r in con.execute("SELECT store_number, latitude, longitude FROM dim_store")}
    carrier_ids = [r["carrier_id"] for r in con.execute("SELECT carrier_id FROM dim_carrier")]

    # overall date span of the real sales
    span = con.execute("SELECT MIN(sale_date) lo, MAX(sale_date) hi FROM fact_sales").fetchone()
    start = datetime.strptime(span["lo"], "%Y-%m-%d").date()
    end   = datetime.strptime(span["hi"], "%Y-%m-%d").date()
    n_days = (end - start).days + 1

    # The Kaggle dataset's real dates end years in the past (e.g. 2020).
    # Depletion math below still uses those REAL dates to look up real daily
    # sales - that part must stay untouched, it's what keeps the simulation
    # believable. But every date we WRITE to the DB (and therefore every date
    # the UI/agents ever show) gets shifted by a constant offset so the last
    # simulated day lands on today - otherwise a shipment ETA reads "2016"
    # while the app is demoed in 2026, which makes "days until late" and
    # "is this shipment still in transit" nonsensical.
    offset = datetime.now().date() - end
    print(f"Simulating {start} -> {end}  ({n_days} days); "
          f"shifting written dates by {offset.days} days so today = {end + offset}")

    # which store+item combinations actually have sales
    combos = con.execute(
        "SELECT DISTINCT store_number, item_number FROM fact_sales").fetchall()

    snapshots, shipments = [], []

    for combo in combos:
        sn, itn = combo["store_number"], combo["item_number"]

        # real daily sales for this store+item, as {date_string: bottles}
        rows = con.execute(
            "SELECT sale_date, SUM(bottles_sold) q FROM fact_sales "
            "WHERE store_number=? AND item_number=? GROUP BY sale_date",
            (sn, itn)).fetchall()
        sold_on = {r["sale_date"]: int(r["q"]) for r in rows}

        # Full daily series over the WHOLE date range, treating no-sale days
        # as 0 - needed so demand_std reflects true day-to-day variability,
        # not just variance among the days that happened to have a sale.
        daily_qtys = [sold_on.get(d.strftime("%Y-%m-%d"), 0) for d in daterange(start, end)]
        avg_daily = sum(daily_qtys) / n_days
        if avg_daily <= 0:
            continue    # nothing to simulate
        demand_std = statistics.pstdev(daily_qtys)

        vendor = item_vendor.get(itn)
        lead = vendor_lead.get(vendor, 5) or 5              # default 5 days if missing
        lead_std = vendor_lead_std.get(vendor) or (lead * 0.20)

        safety_stock, rop = reorder_point(avg_daily, demand_std, lead, lead_std)
        reorder_pt   = max(1, rop)
        order_qty    = max(1, math.ceil(ORDER_DAYS * avg_daily))
        on_hand      = math.ceil(OPENING_DAYS * avg_daily)

        dest_lat, dest_long = store_xy.get(sn, (None, None))

        arrivals = {}          # {date: qty arriving that day}
        order_in_transit = False

        for d in daterange(start, end):
            ds = d.strftime("%Y-%m-%d")            # REAL date - used only to look up real sales
            disp_ds = (d + offset).strftime("%Y-%m-%d")   # SHIFTED date - what gets written/shown

            # 1. receive any delivery due today
            if d in arrivals:
                on_hand += arrivals.pop(d)
                order_in_transit = False

            # 2. deplete by the REAL sales for the day
            on_hand = max(0, on_hand - sold_on.get(ds, 0))

            # 3. record today's snapshot (shifted date)
            cover = round(on_hand / avg_daily, 2)
            snapshots.append((disp_ds, sn, itn, on_hand, reorder_pt, safety_stock, cover))

            # 4. reorder if AT or BELOW the reorder point and nothing already
            #    on the way - "at or below" (not just below) matches the
            #    CRITICAL definition being adapted: if stock has already hit
            #    the calculated threshold, order now, don't wait to dip
            #    further under it.
            if on_hand <= reorder_pt and not order_in_transit:
                eta = d + timedelta(days=int(lead))
                arrivals[eta] = order_qty
                order_in_transit = True
                carrier = random.choice(carrier_ids)
                disp_eta = eta + offset
                shipments.append((
                    disp_ds, disp_eta.strftime("%Y-%m-%d"), sn, itn, vendor, carrier,
                    order_qty, ANKENY_LAT, ANKENY_LONG, dest_lat, dest_long))

    # write results
    con.executemany(
        "INSERT INTO fact_inventory_snapshot "
        "(snapshot_date,store_number,item_number,on_hand_bottles,reorder_point,safety_stock,days_of_cover) "
        "VALUES (?,?,?,?,?,?,?)", snapshots)
    con.executemany(
        "INSERT INTO fact_shipments "
        "(order_date,promised_eta,store_number,item_number,vendor_number,carrier_id,"
        " quantity_bottles,origin_lat,origin_long,dest_lat,dest_long) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", shipments)
    con.commit()

    print(f"  fact_inventory_snapshot  {len(snapshots):>8} rows")
    print(f"  fact_shipments           {len(shipments):>8} rows")

    # tiny preview of what each agent will read
    print("\nLow-stock items the Inventory agent would flag (latest day):")
    for r in con.execute(
        "SELECT store_number, item_description, on_hand_bottles, reorder_point, safety_stock "
        "FROM gold_inventory_health WHERE low_stock_flag=1 "
        "ORDER BY on_hand_bottles - reorder_point LIMIT 5"):
        print(f"   store {r['store_number']}  {r['item_description'][:30]:<30} "
              f"on_hand={r['on_hand_bottles']} reorder_point={r['reorder_point']} "
              f"(safety_stock={r['safety_stock']})")

    con.close()
    print("\nDone. Both agents can now read the shared DB.")


if __name__ == "__main__":
    main()
