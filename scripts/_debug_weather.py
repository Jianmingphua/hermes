#!/usr/bin/env python3
"""Debug: check if the APIs are reachable and returning data."""
import json
import urllib.request
import urllib.error
import sys

FORECAST_URL = "https://api.data.gov.sg/v1/environment/2-hour-weather-forecast"
RAINFALL_URL = "https://api.data.gov.sg/v1/environment/rainfall"

def fetch_json(url, label):
    req = urllib.request.Request(url, headers={"User-Agent": "SG-Weather-Debug/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        print(f"[OK] {label}: fetched successfully")
        return data
    except Exception as e:
        print(f"[FAIL] {label}: {e}", file=sys.stderr)
        return None

# Forecast
data = fetch_json(FORECAST_URL, "forecast")
if data:
    items = data.get("items", [])
    print(f"  items count: {len(items)}")
    for item in items:
        for fc in item.get("forecasts", []):
            if fc.get("area") in ["Tampines", "Seletar"]:
                print(f"  {fc['area']}: {fc['forecast']}")

# Rainfall
data = fetch_json(RAINFALL_URL, "rainfall")
if data:
    items = data.get("items", [])
    stations = {s["id"]: s for s in data.get("metadata", {}).get("stations", [])}
    print(f"  items count: {len(items)}")
    count = 0
    for item in items:
        for r in item.get("readings", []):
            if r.get("value", 0) >= 2.0:
                sid = r.get("station_id", "")
                name = stations.get(sid, {}).get("name", sid)
                print(f"  {name}: {r['value']}mm")
                count += 1
    if count == 0:
        print("  No stations with >= 2.0mm rainfall")
