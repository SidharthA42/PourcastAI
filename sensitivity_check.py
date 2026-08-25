"""
sensitivity_check.py  -  justifies the assumptions baked into the reorder-
point model (simulate.py), instead of leaving them as unexplained constants.

Same idea as a teammate's inventory-agent sensitivity script (Vishnu):
re-run the reorder math under a few different assumptions and report how
many store-items would be flagged CRITICAL under each, so the report can
cite "we tested X, Y, Z and picked N because ..." rather than "we picked N."

Tests two separate assumptions independently:
  1. Z_SERVICE_LEVEL (95% is the current default) - how much safety margin
  2. lead_time_std as a % of lead_time_days (20% is the current default) -
     how much delivery-time variability we assume, since no public dataset
     tracks Iowa's actual vendor delivery reliability

Run:  python sensitivity_check.py
"""
import math
import statistics
from db import connect
from simulate import daterange
from datetime import datetime

# same z-table values a service-level sensitivity check would use:
# 90% -> 1.282, 95% -> 1.645 (current default), 99% -> 2.326
Z_VALUES = {"90%": 1.282, "95%": 1.645, "99%": 2.326}
LEAD_STD_PCT_VALUES = [0.10, 0.20, 0.30]   # current default is 0.20


def build_demand_table(con):
    """One row per store+item: avg_daily demand, demand_std, and the
    vendor's lead_time_avg (demand_std/lead_time come from REAL sales +
    dim_vendor, only the sensitivity constants below are varied)."""
    span = con.execute("SELECT MIN(sale_date) lo, MAX(sale_date) hi FROM fact_sales").fetchone()
    start = datetime.strptime(span["lo"], "%Y-%m-%d").date()
    end = datetime.strptime(span["hi"], "%Y-%m-%d").date()
    n_days = (end - start).days + 1

    vendor_lead = {r["vendor_number"]: r["lead_time_days"]
                   for r in con.execute("SELECT vendor_number, lead_time_days FROM dim_vendor")}
    item_vendor = {r["item_number"]: r["vendor_number"]
                   for r in con.execute("SELECT item_number, vendor_number FROM dim_item")}

    table = []
    combos = con.execute("SELECT DISTINCT store_number, item_number FROM fact_sales").fetchall()
    for combo in combos:
        sn, itn = combo["store_number"], combo["item_number"]
        rows = con.execute(
            "SELECT sale_date, SUM(bottles_sold) q FROM fact_sales "
            "WHERE store_number=? AND item_number=? GROUP BY sale_date",
            (sn, itn)).fetchall()
        sold_on = {r["sale_date"]: int(r["q"]) for r in rows}
        daily = [sold_on.get(d.strftime("%Y-%m-%d"), 0) for d in daterange(start, end)]
        avg_daily = sum(daily) / n_days
        if avg_daily <= 0:
            continue
        demand_std = statistics.pstdev(daily)
        lead = vendor_lead.get(item_vendor.get(itn), 5) or 5
        table.append({"avg_daily": avg_daily, "demand_std": demand_std, "lead": lead})
    return table


def critical_count(table, z, lead_std_pct):
    con = connect()
    n_critical = 0
    for r in table:
        lead_std = r["lead"] * lead_std_pct
        variance = (r["lead"] * r["demand_std"]**2) + (r["avg_daily"]**2 * lead_std**2)
        safety_stock = z * math.sqrt(max(variance, 0))
        rop = r["avg_daily"] * r["lead"] + safety_stock
        # "critical" = an item whose average on-hand roughly equals its
        # reorder point, i.e. it would sit right at the CRITICAL boundary -
        # here approximated by just counting how the threshold itself moves
        n_critical += 1 if rop > r["avg_daily"] * 14 else 0   # vs a 14-day-cover baseline
    con.close()
    return n_critical


def main():
    con = connect()
    table = build_demand_table(con)
    con.close()
    print(f"Testing {len(table)} store-item combinations with real demand data\n")

    print("=== Service level sensitivity (lead_time_std fixed at 20%) ===")
    for label, z in Z_VALUES.items():
        n = critical_count(table, z, 0.20)
        marker = "  <- current default" if label == "95%" else ""
        print(f"  {label:>4} service level (z={z}) -> {n} of {len(table)} items would need "
              f"MORE buffer than a flat 14-day rule{marker}")

    print("\n=== Lead-time variability sensitivity (service level fixed at 95%) ===")
    for pct in LEAD_STD_PCT_VALUES:
        n = critical_count(table, 1.645, pct)
        marker = "  <- current default" if pct == 0.20 else ""
        print(f"  lead_time_std = {int(pct*100)}% of lead_time_days -> {n} of {len(table)} items "
              f"would need MORE buffer than a flat 14-day rule{marker}")

    print("\nInterpretation: higher service level / higher assumed lead-time variability both\n"
          "push more items above a flat 14-day threshold - that's the WHOLE POINT of the\n"
          "statistical model (it gives variable items more buffer than steady ones, where a\n"
          "flat day-count rule treats them identically). Use these numbers to justify why\n"
          "95% / 20% were chosen, in the report's methodology section.")


if __name__ == "__main__":
    main()
