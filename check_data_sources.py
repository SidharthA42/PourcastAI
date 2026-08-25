"""
check_data_sources.py  -  tests every external data source the Risk agent
uses, one at a time, and prints a clear PASS/FALLBACK/FAIL report.

Run:  python check_data_sources.py

This exists because a silent fallback (e.g. Census -> static population
table) still lets the app run fine, so you'd never notice a source went
down unless you check deliberately. Run this before a demo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import risk_tools
from db import connect

# A real Iowa coordinate pair: Ankeny warehouse -> an Iowa City-area point,
# so the routing/weather/forecast checks below exercise a real route, not a
# zero-distance same-point call.
ANKENY_LAT, ANKENY_LON = 41.699, -93.558
TEST_LAT, TEST_LON = 41.6611, -91.5302   # Iowa City area


def status_line(name, is_live, detail):
    tag = "LIVE  " if is_live else "FALLBACK"
    print(f"  [{tag}] {name:<28} {detail}")
    return is_live


def main():
    print("PourCastAI - data source check\n" + "=" * 50)
    results = {}

    print("\n1. NWS weather alerts (point-based, per destination)")
    w = risk_tools.get_weather_alerts(TEST_LAT, TEST_LON)
    results["NWS weather alerts"] = status_line(
        "NWS weather alerts", w["is_live"], f"{w['count']} active alert(s), severity={w['severity']}")

    print("\n2. NWS road hazards (statewide)")
    h = risk_tools.get_road_hazards()
    results["NWS road hazards"] = status_line(
        "NWS road hazards", h["is_live"], f"{h['count']} hazard(s) statewide, severity={h['severity']}")

    print("\n3. OSRM routing (Ankeny -> test point)")
    r = risk_tools.get_route(ANKENY_LAT, ANKENY_LON, TEST_LAT, TEST_LON)
    results["OSRM routing"] = status_line(
        "OSRM routing", r["is_live"],
        f"{r['distance_km']} km, {r['duration_hr']} hr" if r["distance_km"] else "no route")

    print("\n4. Open-Meteo forecast (graded precip/wind)")
    f = risk_tools.get_precip_forecast(TEST_LAT, TEST_LON)
    results["Open-Meteo forecast"] = status_line(
        "Open-Meteo forecast", f["is_live"],
        f"precip_prob={f['precip_prob']}, wind={f['wind_kph']} kph")

    print("\n5. EIA diesel price (checks n8n cache first, then live, then static)")
    d = risk_tools.fetch_diesel_price_live()   # bypasses cache deliberately - see risk_tools.py
    results["EIA diesel price"] = status_line(
        "EIA diesel price (live attempt)", d["is_live"], f"${d['price']}/gal")
    cached = risk_tools._cached_diesel_price()
    if cached:
        print(f"  [CACHE ]  n8n-cached value: ${cached['price']}/gal "
              f"(was_live={cached['is_live']})")
    else:
        print("  [CACHE ]  no cached value yet - has n8n's refresh workflow run?")

    print("\n6. US Census population")
    try:
        # Direct live test RIGHT NOW, independent of any past build - so you
        # can confirm a newly-added CENSUS_API_KEY works without doing a
        # full DB rebuild first.
        import build_database
        _pop, live_now = build_database.fetch_county_population({"POLK"})
        status_line("US Census (live test now)", live_now,
                    "key works, direct call succeeded" if live_now else
                    "direct call failed - check CENSUS_API_KEY in .env")

        # What the CURRENT DATABASE actually has (may be stale if you changed
        # the key after the last build_database.py run - rebuild to refresh).
        con = connect()
        row = con.execute(
            "SELECT COUNT(*) total, COUNT(population) has_pop FROM dim_store").fetchone()
        live_row = con.execute(
            "SELECT value, fetched_at FROM ext_cache WHERE cache_key='census_live'").fetchone()
        con.close()
        if live_row is None:
            results["US Census population"] = status_line(
                "US Census (in DB)", False, "no build record found - run build_database.py")
        else:
            census_ok = live_row["value"] == "1"
            results["US Census population"] = status_line(
                "US Census (in DB)", census_ok,
                f"{row['has_pop']}/{row['total']} stores populated, from build at "
                f"{live_row['fetched_at']} "
                f"({'live Census call' if census_ok else 'static fallback table'})"
                + ("" if census_ok == live_now else
                   "  <- MISMATCH: re-run build_database.py to pick up the current key"))
    except Exception as e:
        results["US Census population"] = status_line("US Census population", False, str(e))

    print("\n" + "=" * 50)
    live_count = sum(results.values())
    print(f"Summary: {live_count}/{len(results)} sources LIVE right now "
          f"({len(results) - live_count} on fallback data).")
    print("A source on FALLBACK doesn't break the app - it's designed not to - "
          "but check your network/API keys if you expected it to be live.")


if __name__ == "__main__":
    main()
