"""
risk_agent.py  -  Sid's agent (Risk & Logistics).

Takes the Inventory agent's low-stock flags, finds the matching inbound
shipments in the shared 'gold_shipments_open' view, and scores each route
0-100 using live weather + diesel + distance + carrier/vendor reliability.
Only flagged routes are scored (gated on Inventory), which is the deliberate
sequential-pipeline design in your report.
"""
from datetime import datetime, timedelta
from db import connect
import risk_tools as tools


def score_route(sh, diesel):
    """Return a dict with risk_score 0-100, updated_eta, and the drivers.

    Weights (documented rebalancing after adding 3 new factors - road hazards
    and graded forecast both expand what used to be a single 40% "weather"
    bucket into three more specific signals; rurality is a new structural
    factor independent of weather/distance/diesel/reliability):
        weather_alert   25%  (point-based NWS severe alerts, destination-specific)
        road_hazard     10%  (statewide NWS hazards along travel corridors)
        precip_forecast 10%  (Open-Meteo graded forecast - catches non-severe
                               weather that alerts miss entirely)
        distance        20%
        diesel          10%
        reliability     15%
        rural           10%  (US Census-derived; rural counties assumed to have
                               thinner road infrastructure / slower response,
                               independent of the weather/distance on a given day)
    """
    route = tools.get_route(sh["origin_lat"], sh["origin_long"], sh["dest_lat"], sh["dest_long"])
    weather = tools.get_weather_alerts(sh["dest_lat"], sh["dest_long"])
    hazards = tools.get_road_hazards()
    forecast = tools.get_precip_forecast(sh["dest_lat"], sh["dest_long"])

    dist = route["distance_km"]                                     # may be None
    # --- component scores (0-1 each), then weighted into 0-100 ---
    weather_risk    = weather["severity"]
    hazard_risk     = hazards["severity"]
    forecast_risk   = forecast["severity"]
    distance_risk   = min(dist / 300.0, 1.0) if dist is not None else 0.0
    diesel_risk     = min(max((diesel["price"] - 3.50) / 1.50, 0), 1)   # $3.50-5.00 band
    reliability_risk = 1.0 - (sh["reliability_score"] or 0.9)
    rural_risk      = 1.0 if sh.get("rural_flag") else 0.0

    score = 100 * (0.25*weather_risk + 0.10*hazard_risk + 0.10*forecast_risk +
                   0.20*distance_risk + 0.10*diesel_risk +
                   0.15*reliability_risk + 0.10*rural_risk)

    # ETA re-estimate: stretch the promised date if EITHER weather alerts OR
    # the graded forecast signal bad conditions, and we have a drive-time estimate.
    combined_weather = max(weather_risk, forecast_risk)
    base_eta = datetime.strptime(sh["promised_eta"], "%Y-%m-%d")
    if combined_weather > 0.5 and route["duration_hr"] is not None:
        updated = base_eta + timedelta(hours=route["duration_hr"] * (1 + combined_weather))
    else:
        updated = base_eta

    return {
        "shipment_id": sh["shipment_id"], "store_number": sh["store_number"],
        "item_description": sh["item_description"], "carrier_name": sh["carrier_name"],
        "distance_km": round(dist, 1) if dist is not None else None,
        "risk_score": round(score, 1),
        "risk_band": "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW",
        "promised_eta": sh["promised_eta"], "updated_eta": updated.strftime("%Y-%m-%d"),
        "weather_alerts": weather["count"],
        "road_hazards": hazards["count"],
        "precip_prob": round(forecast["precip_prob"], 2),
        "rural_delivery": bool(sh.get("rural_flag")),
        "shipment_value": sh["shipment_value"],   # dollars at risk if this reorder is late
        "data_live": route["is_live"] and weather["is_live"] and hazards["is_live"]
                     and forecast["is_live"],   # for UI labelling
    }


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def read_gold_scores():
    """DATABRICKS PATH. Read the pre-computed `gold_risk_scores` Delta table
    (synced into local SQLite by databricks_sync.py) and map its columns to the
    SAME shape run() returns, so the UI and LLM never care which path produced
    the numbers.

    Why this exists: the Gold notebook (07_gold_layer) already scores EVERY open
    shipment with the exact 7-factor formula. Re-running risk_agent.run() at
    query time both (a) throws that work away and (b) filters to only shipments
    that match a low-stock flag - which, when shipments come from a handful of
    HubSpot Deals, usually matches nothing. Reading the Gold table directly
    fixes both: all open shipments show, already scored, no live API calls.

    Returns None if the table isn't present (a pure local-sim setup that never
    ran databricks_sync.py) so get_pipeline_data() can fall back to run().
    """
    con = connect()
    if not _table_exists(con, "gold_risk_scores"):
        con.close()
        return None
    rows = con.execute("SELECT * FROM gold_risk_scores").fetchall()
    con.close()
    if not rows:
        return None

    def _num(v):
        return None if v is None else v

    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "shipment_id": d.get("shipment_id"),
            "store_number": d.get("store_number"),
            "item_description": d.get("item_description"),
            "carrier_name": d.get("carrier_name"),
            "distance_km": round(d["distance_km"], 1) if d.get("distance_km") is not None else None,
            "risk_score": _num(d.get("risk_score")),
            "risk_band": d.get("risk_band"),
            "promised_eta": d.get("promised_eta"),
            # The Gold layer stores promised_eta only; it doesn't recompute an
            # ETA slip the way the live scorer did. Mirror it so the UI's
            # "Delayed" derivation stays well-defined (no slip -> In Transit).
            "updated_eta": d.get("updated_eta") or d.get("promised_eta"),
            "weather_alerts": int(d["weather_alert_count"]) if d.get("weather_alert_count") is not None else 0,
            # Gold keeps hazard *severity* (0-1, statewide) rather than a per-
            # shipment count; expose it under the same key the UI reads.
            "road_hazards": _num(d.get("road_hazard_severity")),
            "precip_prob": round(d["precip_prob"], 2) if d.get("precip_prob") is not None else 0.0,
            "rural_delivery": bool(d.get("rural_flag")),
            "shipment_value": _num(d.get("shipment_value")),
            "data_live": bool(d.get("osrm_live") and d.get("alerts_live") and d.get("forecast_live")),
        })
    out.sort(key=lambda x: -(x["risk_score"] or 0))
    return out


def run(flags):
    """flags: list of dicts with store_number + item_number from the Inventory agent."""
    if not flags:
        return []
    con = connect()
    diesel = tools.get_diesel_price()
    wanted = {(f["store_number"], f["item_number"]) for f in flags}

    scored = []
    for sh in con.execute("SELECT * FROM gold_shipments_open").fetchall():
        if (sh["store_number"], sh["item_number"]) in wanted:
            scored.append(score_route(dict(sh), diesel))
    con.close()
    scored.sort(key=lambda x: -x["risk_score"])
    return scored


if __name__ == "__main__":
    import inventory_agent
    flags = inventory_agent.get_low_stock(threshold=30)   # wide net for a demo
    print(f"Scoring routes for {len(flags)} flagged store-items ...")
    for r in run(flags)[:8]:
        print(f"  [{r['risk_band']:<6} {r['risk_score']:>5}] store {r['store_number']} "
              f"{r['item_description'][:24]:<24} {r['distance_km']}km  ETA {r['updated_eta']}")
