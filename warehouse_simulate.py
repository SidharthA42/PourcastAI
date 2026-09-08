"""
warehouse_simulate.py  -  populates warehouse_inventory (Ankeny state warehouse
stock) from the SAME real sales data + reorder-point formula already used for
store-level inventory in simulate.py, aggregated up one level.

Key difference from store-level: demand_avg/demand_std here must be the SUM
of ALL tracked stores' demand for that item, not one store's demand - because
the warehouse serves every store at once. Getting this aggregation wrong
(e.g. summing each store's std instead of computing variance on the summed
series) would silently produce a nonsense reorder point, so this recomputes
demand_std from the AGGREGATED daily series, not from per-store numbers.

Vendor lead time (factory -> warehouse) reuses dim_vendor.lead_time_days /
lead_time_std - same real column already used for store-level shipments,
just applied at the warehouse's own reorder decision instead.

Opening warehouse stock is a DOCUMENTED ASSUMPTION (no public data exists on
Iowa's actual warehouse stock levels) - seeded at max_count, i.e. "assume the
warehouse starts full." State this plainly if asked.

Run AFTER simulate.py (needs fact_sales, dim_item, dim_vendor already built):
    python add_warehouse_table.py     (one-time)
    python warehouse_simulate.py      (re-run anytime to refresh)
"""
import math
import statistics
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "pourcast.db"

ORDER_DAYS = 28          # same convention as store-level simulate.py
Z_SERVICE_LEVEL = 1.645  # same 95% service level as store-level (see sensitivity_check.py)


def reorder_point(demand_avg, demand_std, lead_time_avg, lead_time_std):
    """Identical formula to simulate.py's reorder_point() - kept as its own
    copy here (not imported) so this script has zero dependency on
    simulate.py's module-level state, and can't accidentally be affected by
    changes made for store-level simulation."""
    variance = (lead_time_avg * demand_std**2) + (demand_avg**2 * lead_time_std**2)
    safety_stock = Z_SERVICE_LEVEL * math.sqrt(max(variance, 0))
    rop = demand_avg * lead_time_avg + safety_stock
    return math.ceil(safety_stock), math.ceil(rop)


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    vendor_lead = {r["vendor_number"]: r["lead_time_days"]
                   for r in con.execute("SELECT vendor_number, lead_time_days FROM dim_vendor")}
    vendor_lead_std = {r["vendor_number"]: r["lead_time_std"]
                       for r in con.execute("SELECT vendor_number, lead_time_std FROM dim_vendor")}
    item_vendor = {r["item_number"]: r["vendor_number"]
                   for r in con.execute("SELECT item_number, vendor_number FROM dim_item")}
    item_names = {r["item_number"]: r["item_description"]
                  for r in con.execute("SELECT item_number, item_description FROM dim_item")}

    span = con.execute("SELECT MIN(sale_date) lo, MAX(sale_date) hi FROM fact_sales").fetchone()
    start = datetime.strptime(span["lo"], "%Y-%m-%d").date()
    end = datetime.strptime(span["hi"], "%Y-%m-%d").date()
    n_days = (end - start).days + 1

    # "today" for last_delivery_date, matching simulate.py's date-shift convention
    offset = datetime.now().date() - end
    today_str = (end + offset).strftime("%Y-%m-%d")

    items = con.execute("SELECT DISTINCT item_number FROM fact_sales").fetchall()

    rows = []
    for it in items:
        itn = it["item_number"]

        # AGGREGATED across ALL tracked stores - this is the key correction
        # vs. per-store numbers: sum bottles sold per DAY across every store
        # first, THEN compute avg/std on that combined daily series.
        agg = con.execute(
            "SELECT sale_date, SUM(bottles_sold) q FROM fact_sales "
            "WHERE item_number=? GROUP BY sale_date", (itn,)).fetchall()
        sold_on = {r["sale_date"]: int(r["q"]) for r in agg}
        daily = [sold_on.get(d.strftime("%Y-%m-%d"), 0) for d in daterange(start, end)]

        avg_daily = sum(daily) / n_days
        if avg_daily <= 0:
            continue
        demand_std = statistics.pstdev(daily)

        vendor = item_vendor.get(itn)
        lead = vendor_lead.get(vendor, 5) or 5
        lead_std = vendor_lead_std.get(vendor) or (lead * 0.20)

        safety_stock, rop = reorder_point(avg_daily, demand_std, lead, lead_std)
        min_count = max(1, safety_stock)
        reorder_pt = max(1, rop)
        max_count = max(reorder_pt + 1, math.ceil(ORDER_DAYS * avg_daily) + safety_stock)

        # Documented assumption: warehouse starts FULL (at max_count). No
        # public data exists on Iowa's real warehouse stock, so rather than
        # inventing a partial number, we state the assumption plainly.
        on_hand = max_count

        rows.append((itn, item_names.get(itn, f"Item {itn}"), on_hand,
                     min_count, max_count, reorder_pt, today_str))

    con.execute("DELETE FROM warehouse_inventory")   # clean re-run
    con.executemany(
        "INSERT INTO warehouse_inventory "
        "(item_number, item_name, on_hand, min_count, max_count, reorder_point, last_delivery_date) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()

    print(f"warehouse_inventory populated: {len(rows)} items")
    print("(Assumption: warehouse starts at max_count / 'full' - no public data exists "
          "for real Iowa warehouse stock levels, so this is a documented starting assumption, "
          "same principle as the store-level lead_time_std assumption.)\n")
    for r in con.execute(
        "SELECT item_number, item_name, on_hand, min_count, reorder_point, max_count "
        "FROM warehouse_inventory ORDER BY item_name LIMIT 8"):
        print(f"  {r['item_number']:<8} {r['item_name'][:28]:<28} "
              f"on_hand={r['on_hand']:<6} reorder_pt={r['reorder_point']:<6} "
              f"min={r['min_count']:<5} max={r['max_count']}")

    con.close()


if __name__ == "__main__":
    main()
