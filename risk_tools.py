"""
risk_tools.py  -  live external data for the Risk agent.

Every call is wrapped so a blocked network, a rate-limited public server, or a
MISSING store coordinate NEVER crashes the demo - it falls back to a labelled
estimate and reports is_live=False.

Results are cached per location, so scoring 194 flagged shipments that share ~30
stores only makes ~30 API calls, not 194 (avoids OSRM/NWS rate limits).

APIs (all free): NWS alerts + NWS road hazards, OSRM routing, Open-Meteo
graded forecast, EIA diesel (EIA_API_KEY in .env). Rurality (US Census) is
NOT called here - it's fetched once at DB build time into dim_store, since
population doesn't change day to day the way weather/diesel do.
"""
import os
import math
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
TIMEOUT = 8
HEADERS = {"User-Agent": "PourCastAI-student-project (contact: example@uni.edu)"}

_route_cache = {}
_weather_cache = {}
_hazard_cache = {"value": None}   # single statewide value, not per-location
_forecast_cache = {}


def _has_coords(*vals):
    return all(v is not None for v in vals)


def haversine_km(lat1, lon1, lat2, lon2):
    if not _has_coords(lat1, lon1, lat2, lon2):
        return None
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def get_route(o_lat, o_lon, d_lat, d_lon):
    """Return {distance_km, duration_hr, is_live}. Safe on missing coords."""
    if not _has_coords(o_lat, o_lon, d_lat, d_lon):
        return {"distance_km": None, "duration_hr": None, "is_live": False}

    key = (round(d_lat, 4), round(d_lon, 4))     # cache per destination store
    if key in _route_cache:
        return _route_cache[key]

    result = None
    try:
        url = (f"https://router.project-osrm.org/route/v1/driving/"
               f"{o_lon},{o_lat};{d_lon},{d_lat}?overview=false")
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        routes = r.json().get("routes")          # .get avoids KeyError on errors
        if routes:
            leg = routes[0]
            result = {"distance_km": leg["distance"]/1000,
                      "duration_hr": leg["duration"]/3600, "is_live": True}
    except Exception:
        result = None

    if result is None:                            # fallback: straight-line est.
        km = haversine_km(o_lat, o_lon, d_lat, d_lon)
        result = {"distance_km": km, "duration_hr": (km/70.0) if km else None,
                  "is_live": False}

    _route_cache[key] = result
    return result


def get_weather_alerts(lat, lon):
    """Return {severity 0-1, count, is_live}. Safe on missing coords."""
    if not _has_coords(lat, lon):
        return {"severity": 0.0, "count": 0, "is_live": False}

    key = (round(lat, 3), round(lon, 3))
    if key in _weather_cache:
        return _weather_cache[key]

    try:
        url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        feats = r.json().get("features", [])
        rank = {"Minor": 0.3, "Moderate": 0.6, "Severe": 0.85, "Extreme": 1.0}
        sev = max([rank.get(f["properties"].get("severity"), 0.2) for f in feats], default=0.0)
        out = {"severity": sev, "count": len(feats), "is_live": True}
    except Exception:
        out = {"severity": 0.0, "count": 0, "is_live": False}

    _weather_cache[key] = out
    return out


# Event types genuinely relevant to a truck making a delivery, distinct from
# get_weather_alerts() above (which is per-destination severe-alert severity).
# This checks STATEWIDE for hazards that affect travel corridors generally
# (a closed stretch of I-80 matters even if the destination store itself has
# no active alert). Same NWS domain/no new key - just a different query.
ROAD_HAZARD_EVENTS = [
    "Winter Storm Warning", "Winter Weather Advisory", "Ice Storm Warning",
    "Dense Fog Advisory", "High Wind Warning", "Blizzard Warning", "Flood Warning",
]


def get_road_hazards():
    """Return {count, severity 0-1, is_live} - statewide (Iowa) active
    advisories relevant to road travel. Cached for the process lifetime
    since it's one statewide call, not per-shipment."""
    if _hazard_cache["value"] is not None:
        return _hazard_cache["value"]

    try:
        url = "https://api.weather.gov/alerts/active?area=IA"
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        feats = r.json().get("features", [])
        relevant = [f for f in feats if f["properties"].get("event") in ROAD_HAZARD_EVENTS]
        # more relevant hazards active statewide = higher risk, capped at 1.0
        severity = min(len(relevant) / 5.0, 1.0)
        out = {"count": len(relevant), "severity": severity, "is_live": True}
    except Exception:
        out = {"count": 0, "severity": 0.0, "is_live": False}

    _hazard_cache["value"] = out
    return out


def get_precip_forecast(lat, lon):
    """Return {precip_prob 0-1, wind_kph, severity 0-1, is_live} from
    Open-Meteo's standard land forecast API (NOT the marine API - this
    project models truck routes, not vessels). Distinct from
    get_weather_alerts(): NWS alerts only fire for SEVERE weather, but a
    truck can be meaningfully slowed by heavy-but-non-severe rain/snow that
    never triggers an alert at all. This fills that gap with graded,
    continuous forecast data instead of a binary alert/no-alert signal."""
    if not _has_coords(lat, lon):
        return {"precip_prob": 0.0, "wind_kph": 0.0, "severity": 0.0, "is_live": False}

    key = (round(lat, 3), round(lon, 3))
    if key in _forecast_cache:
        return _forecast_cache[key]

    try:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               "&hourly=precipitation_probability,wind_speed_10m&forecast_days=1")
        r = requests.get(url, timeout=TIMEOUT)
        hourly = r.json()["hourly"]
        precip_prob = max(hourly["precipitation_probability"][:12], default=0) / 100.0
        wind_kph = max(hourly["wind_speed_10m"][:12], default=0.0)
        # blend: heavy precip probability and high wind both slow a truck down
        severity = min(0.6 * precip_prob + 0.4 * min(wind_kph / 50.0, 1.0), 1.0)
        out = {"precip_prob": precip_prob, "wind_kph": wind_kph,
               "severity": severity, "is_live": True}
    except Exception:
        out = {"precip_prob": 0.0, "wind_kph": 0.0, "severity": 0.0, "is_live": False}

    _forecast_cache[key] = out
    return out


def _cached_diesel_price(max_age_minutes=90):
    """Check ext_cache (written by the n8n cache-refresh workflow) before
    hitting the live API. Keeps per-question latency off the EIA API.
    Honestly reports whether the CACHED value was itself ever live -
    caching a fallback value doesn't make it live."""
    try:
        from db import connect  # local import: avoids a hard dependency at module load
        con = connect()
        row = con.execute(
            "SELECT value, fetched_at FROM ext_cache WHERE cache_key='diesel_price'").fetchone()
        live_row = con.execute(
            "SELECT value FROM ext_cache WHERE cache_key='diesel_price_live'").fetchone()
        con.close()
        if row is None:
            return None
        from datetime import datetime
        fetched = datetime.fromisoformat(row["fetched_at"])
        age_min = (datetime.now() - fetched).total_seconds() / 60
        if age_min <= max_age_minutes:
            was_live = bool(live_row and live_row["value"] == "1")
            return {"price": float(row["value"]), "is_live": was_live, "from_cache": True}
    except Exception:
        pass
    return None


def fetch_diesel_price_live():
    """Actually attempts a live EIA call (or the static fallback). Deliberately
    bypasses the cache read - this is what the n8n refresh job must call, not
    get_diesel_price() below, or a cached value just perpetuates itself
    forever (every refresh re-reads the cache instead of re-fetching), which
    is exactly the bug that made an old/wrong price stick around."""
    if EIA_API_KEY:
        try:
            url = ("https://api.eia.gov/v2/petroleum/pri/gnd/data/"
                   f"?api_key={EIA_API_KEY}&frequency=weekly"
                   "&data[0]=value&facets[series][]=EMD_EPD2D_PTE_R20_DPG"
                   "&sort[0][column]=period&sort[0][direction]=desc&length=1")
            r = requests.get(url, timeout=TIMEOUT)
            return {"price": float(r.json()["response"]["data"][0]["value"]), "is_live": True}
        except Exception:
            pass
    return {"price": 3.80, "is_live": False}


def get_diesel_price():
    """Midwest diesel $/gal -> {price, is_live}. Checks the n8n-refreshed
    cache first (fast, no network); falls back to a direct live call, then
    to a static estimate."""
    cached = _cached_diesel_price()
    if cached is not None:
        return cached
    return fetch_diesel_price_live()
