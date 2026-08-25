"""
hubspot_seed.py  -  PourCastAI (Step 3)

Populates HubSpot from the shared local DB:
    dim_store            -> Companies   (one per store, permanent)
    gold_shipments_open,
    filtered to currently
    open shipments only  -> Deals       (re-run this anytime; it's idempotent)

This mirrors a real CRM's job: hold the CURRENT operational picture, not a
copy of history. Full history stays in the shared DB / Delta Lake -- this
script only pushes what's open right now.

Idempotent: re-running this will UPDATE existing Companies/Deals (matched by
store_number / shipment_id) instead of creating duplicates. Safe to run
after every simulate.py refresh.

Run:  python hubspot_seed.py
"""
import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

from db import connect

load_dotenv(Path(__file__).resolve().parent / ".env")
TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
if not TOKEN:
    raise SystemExit("HUBSPOT_ACCESS_TOKEN not found in .env")

BASE = "https://api.hubapi.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
TIMEOUT = 15

# From your pipeline check -- internal pipeline id is "default", stages map
# Ordered/In Transit/Delivered/At Risk to HubSpot's original internal stage
# ids (renamed in the UI, but the ids underneath stay the same).
PIPELINE_ID = "default"
STAGE_ORDERED    = "appointmentscheduled"
STAGE_IN_TRANSIT = "qualifiedtobuy"
STAGE_DELIVERED  = "presentationscheduled"
STAGE_AT_RISK    = "contractsent"


# ----------------------------------------------------------------------------
# One-time setup: make sure the custom properties we need actually exist.
# Free plan allows 10 custom properties total across all objects -- this
# script uses 4 (store_number, item_number, shipment_id, quantity_bottles),
# leaving headroom for a risk_score property later.
# ----------------------------------------------------------------------------
def ensure_property(object_type, name, label, group_name, prop_type="number"):
    r = requests.get(f"{BASE}/crm/v3/properties/{object_type}/{name}",
                      headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 200:
        return   # already exists
    body = {
        "name": name, "label": label, "type": prop_type,
        "fieldType": {"number": "number", "date": "date"}.get(prop_type, "text"),
        "groupName": group_name,
    }
    r = requests.post(f"{BASE}/crm/v3/properties/{object_type}",
                       headers=HEADERS, json=body, timeout=TIMEOUT)
    if r.status_code not in (200, 201):
        print(f"  WARNING: couldn't create property {object_type}.{name}: {r.text[:200]}")


def ensure_all_properties():
    print("Checking custom properties...")
    ensure_property("companies", "store_number", "Store Number", "companyinformation")
    ensure_property("deals", "item_number", "Item Number", "dealinformation")
    ensure_property("deals", "shipment_id", "Shipment ID", "dealinformation")
    ensure_property("deals", "quantity_bottles", "Quantity (Bottles)", "dealinformation")
    ensure_property("deals", "store_number", "Store Number", "dealinformation")
    ensure_property("deals", "order_date", "Order Date", "dealinformation", prop_type="date")
    ensure_property("deals", "carrier_id", "Carrier ID", "dealinformation")


# ----------------------------------------------------------------------------
# Search-then-create/update helpers (idempotent upsert, since Free plan
# doesn't support the batch/upsert-by-external-id endpoint cleanly).
# ----------------------------------------------------------------------------
def find_by_property(object_type, prop_name, value):
    body = {"filterGroups": [{"filters": [
        {"propertyName": prop_name, "operator": "EQ", "value": str(value)}]}],
        "properties": [prop_name], "limit": 1}
    r = requests.post(f"{BASE}/crm/v3/objects/{object_type}/search",
                       headers=HEADERS, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0]["id"] if results else None


def upsert_object(object_type, match_prop, match_value, properties):
    existing_id = find_by_property(object_type, match_prop, match_value)
    if existing_id:
        r = requests.patch(f"{BASE}/crm/v3/objects/{object_type}/{existing_id}",
                            headers=HEADERS, json={"properties": properties}, timeout=TIMEOUT)
        r.raise_for_status()
        return existing_id, "updated"
    r = requests.post(f"{BASE}/crm/v3/objects/{object_type}",
                       headers=HEADERS, json={"properties": properties}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["id"], "created"


def associate_deal_to_company(deal_id, company_id):
    r = requests.put(
        f"{BASE}/crm/v4/objects/deals/{deal_id}/associations/default/companies/{company_id}",
        headers=HEADERS, timeout=TIMEOUT)
    if r.status_code not in (200, 201, 204):
        print(f"  WARNING: association failed for deal {deal_id}: {r.text[:200]}")


# ----------------------------------------------------------------------------
# Stage logic: currently-open shipments only get Ordered / In Transit here.
# Delivered and At Risk are meant to be set LATER by the pipeline itself
# (Delivered once promised_eta passes; At Risk once the Risk agent's score
# crosses its threshold) -- that becomes Step 4, and is the actual "live CRM
# reflects agent decisions" story for the report. Seeding only establishes
# the starting state.
# ----------------------------------------------------------------------------
def initial_stage(order_date, promised_eta, today_str):
    return STAGE_ORDERED if order_date >= today_str else STAGE_IN_TRANSIT


def seed_companies(con):
    print("\nSeeding Companies (stores)...")
    stores = con.execute("SELECT * FROM dim_store").fetchall()
    for s in stores:
        props = {
            "name": s["store_name"],
            "city": s["city"],
            "zip": s["zip_code"],
            "address": s["address"],
            "store_number": s["store_number"],
        }
        _id, action = upsert_object("companies", "store_number", s["store_number"], props)
        print(f"  store {s['store_number']:<6} {action:<8} (company id {_id})")
    print(f"Done: {len(stores)} companies.")
    return {s["store_number"]: None for s in stores}   # filled in below


def seed_deals(con):
    print("\nSeeding Deals (currently open shipments)...")
    shipments = con.execute(
        "SELECT * FROM gold_shipments_open WHERE promised_eta >= date('now') "
        "ORDER BY promised_eta"
    ).fetchall()
    today_str = con.execute("SELECT date('now')").fetchone()[0]

    company_cache = {}
    created = updated = 0

    for sh in shipments:
        store_num = sh["store_number"]
        if store_num not in company_cache:
            company_cache[store_num] = find_by_property("companies", "store_number", store_num)
        company_id = company_cache[store_num]

        props = {
            "dealname": f"Shipment #{sh['shipment_id']} - {sh['store_name']} - {sh['item_description']}",
            "amount": sh["shipment_value"],
            "pipeline": PIPELINE_ID,
            "dealstage": initial_stage(sh["order_date"], sh["promised_eta"], today_str),
            "closedate": sh["promised_eta"],
            "shipment_id": sh["shipment_id"],
            "item_number": sh["item_number"],
            "quantity_bottles": sh["quantity_bottles"],
            "store_number": sh["store_number"],
            "order_date": sh["order_date"],
            "carrier_id": sh["carrier_id"],
        }
        deal_id, action = upsert_object("deals", "shipment_id", sh["shipment_id"], props)
        if company_id:
            associate_deal_to_company(deal_id, company_id)
        created += (action == "created")
        updated += (action == "updated")
        time.sleep(0.05)   # polite pacing, well under the 100/10s limit

    print(f"Done: {created} created, {updated} updated, {len(shipments)} total open shipments.")


def main():
    con = connect()
    ensure_all_properties()
    seed_companies(con)
    seed_deals(con)
    con.close()
    print("\nHubSpot is now seeded. Re-run this script anytime after "
          "simulate.py to refresh the open-shipments snapshot.")


if __name__ == "__main__":
    main()
