"""
inventory_agent.py  -  Vishnu's agent (Inventory).

Reads the shared 'gold_inventory_health' view and returns the store+item
combinations whose days-of-cover has fallen below the threshold. Those flags
are the ONLY thing handed to the Risk agent - keyed by the shared
(store_number, item_number), so the two agents can never disagree.
"""
from db import connect

THRESHOLD_DAYS = 14      # your professor's "<14 days" rule

def get_low_stock(threshold=THRESHOLD_DAYS):
    con = connect()
    rows = con.execute(
        "SELECT store_number, store_name, county, item_number, item_description, "
        "       vendor_number, on_hand_bottles, days_of_cover, "
        "       state_bottle_cost, state_bottle_retail, inventory_value "
        "FROM gold_inventory_health WHERE days_of_cover < ? "
        "ORDER BY days_of_cover", (threshold,)).fetchall()
    con.close()
    return [dict(r) for r in rows]

def summary():
    con = connect()
    total = con.execute("SELECT COUNT(*) FROM gold_inventory_health").fetchone()[0]
    total_value = con.execute(
        "SELECT COALESCE(SUM(inventory_value), 0) FROM gold_inventory_health").fetchone()[0]
    con.close()
    low = get_low_stock()
    value_at_risk = round(sum(f["inventory_value"] or 0 for f in low), 2)
    return {
        "tracked": total,
        "at_risk": len(low),
        "flags": low,
        "total_inventory_value": round(total_value, 2),
        "value_at_risk": value_at_risk,
    }

def item_catalog():
    """Every tracked item (only ~25, small slice) with price, category, and
    total on-hand stock across all tracked stores. Unlike `flags` (just the
    low-stock subset), this covers ANY item the user might ask about by name
    or category - e.g. 'price of X', 'inventory for every whiskey we have' -
    and gives the LLM enough to answer both without a second query."""
    con = connect()
    rows = con.execute("""
        SELECT it.item_number, it.item_description, it.category_name, it.vendor_number,
               v.vendor_name, it.pack, it.bottle_volume_ml,
               it.state_bottle_cost, it.state_bottle_retail,
               COALESCE(SUM(s.on_hand_bottles), 0) AS total_on_hand
        FROM dim_item it
        LEFT JOIN dim_vendor v ON v.vendor_number = it.vendor_number
        LEFT JOIN fact_inventory_snapshot s
               ON s.item_number = it.item_number
              AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_inventory_snapshot)
        GROUP BY it.item_number
        ORDER BY it.item_description
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]

def item_detail(item_number):
    """Full detail for ONE item: its catalog record plus every store that
    carries it, with per-store stock/cover so 'what does this item look like
    across our footprint' is answerable from a single click, not a new
    question to the chat."""
    con = connect()
    item = con.execute("""
        SELECT it.item_number, it.item_description, it.category_name,
               it.vendor_number, v.vendor_name, v.lead_time_days, v.lead_time_std,
               v.reliability_score,
               it.pack, it.bottle_volume_ml, it.state_bottle_cost, it.state_bottle_retail
        FROM dim_item it
        LEFT JOIN dim_vendor v ON v.vendor_number = it.vendor_number
        WHERE it.item_number = ?
    """, (item_number,)).fetchone()
    stores = con.execute("""
        SELECT store_number, store_name, county, on_hand_bottles, days_of_cover,
               reorder_point, safety_stock, low_stock_flag
        FROM gold_inventory_health
        WHERE item_number = ?
        ORDER BY days_of_cover
    """, (item_number,)).fetchall()
    con.close()
    if item is None:
        return None
    return {"item": dict(item), "stores": [dict(r) for r in stores]}

if __name__ == "__main__":
    s = summary()
    print(f"Tracking {s['tracked']} store-items, {s['at_risk']} at risk of stockout:")
    for f in s["flags"][:10]:
        print(f"  store {f['store_number']:<5} {f['item_description'][:28]:<28} "
              f"{f['days_of_cover']:>5} days ({f['on_hand_bottles']} bottles)")
