#!/usr/bin/env python3
"""
Weatherman Alert Monitor
Monitors real-time weather conditions for Tampines & Seletar.
Only outputs when SIGNIFICANT changes are detected (for cron no_agent delivery).

Alert triggers:
- Rain started/stopped (actual rainfall > threshold)
- PSI spike (>100 unhealthy, >150 very unhealthy)
- UV extreme (>=11) or sudden jump
- Temperature swing (>5°C change from last check)
- Heavy rain (>10mm at any station)
- Haze (PM2.5 > 100)
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SGT = timezone(timedelta(hours=8))
STATE_DIR = Path("/opt/hermes/scripts/board_state")
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "weatherman_last_alert.json"

API_KEY = "7TPJnYHHaUltgiGH0qkXjLcETeaDh3Cu"

TAMPINES_STATIONS = ["tampines", "simei", "pasir ris", "bedok", "tanah merah", "expo", "upper east coast"]
SELETAR_STATIONS = ["seletar", "yishun", "sembawang", "canberra", "woodlands", "admiralty", "khatib"]

# Thresholds
RAIN_START_THRESHOLD = 1.0      # mm — alert when rain starts
RAIN_HEAVY_THRESHOLD = 10.0     # mm — alert for heavy rain
PSI_UNHEALTHY = 100             # alert when PSI crosses this
PSI_VERY_UNHEALTHY = 150        # urgent alert
UV_EXTREME = 11                 # alert when UV >= this
UV_HIGH = 8                     # warn when UV >= this
PM25_UNHEALTHY = 100            # µg/m³
TEMP_SWING_THRESHOLD = 5.0      # °C change from last check


def fetch_json(url):
    """Fetch JSON from data.gov.sg API with key auth."""
    headers = {
        "User-Agent": "SG-Weatherman-Alert/2.0",
        "x-api-key": API_KEY,
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def load_state():
    """Load last alert state."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state):
    """Save last alert state."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_area_rainfall(area_name):
    """Get current rainfall readings for nearest area stations."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/rainfall")
    if not data or not data.get("data"):
        return []

    stations = data["data"].get("stations", [])
    readings = data["data"].get("readings", [])
    if not readings or not stations:
        return []

    if "tampines" in area_name.lower():
        target_lat, target_lon = 1.3494, 103.9564
    else:
        target_lat, target_lon = 1.4044, 103.8688

    station_map = {s["id"]: s for s in stations}

    # Find stations within ~15km radius
    nearby = []
    for s in stations:
        slat = s.get("location", {}).get("latitude", 0)
        slon = s.get("location", {}).get("longitude", 0)
        d = ((target_lat - slat) ** 2 + (target_lon - slon) ** 2) ** 0.5
        if d < 0.15:
            nearby.append(s["id"])

    if not nearby:
        sorted_stations = sorted(stations, key=lambda s: (
            (target_lat - s.get("location", {}).get("latitude", 0)) ** 2 +
            (target_lon - s.get("location", {}).get("longitude", 0)) ** 2
        ) ** 0.5)
        nearby = [s["id"] for s in sorted_stations[:3]]

    results = []
    for r in readings[0].get("data", []):
        sid = r.get("stationId", "")
        if sid in nearby:
            station = station_map.get(sid, {})
            results.append({
                "station": station.get("name", ""),
                "value": r.get("value", 0),
            })
    return results


def get_area_temp(area_name):
    """Get current temperature for area (nearest station by coordinates)."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/air-temperature")
    if not data or not data.get("data"):
        return None, None

    stations = data["data"].get("stations", [])
    readings = data["data"].get("readings", [])
    if not readings or not stations:
        return None, None

    if "tampines" in area_name.lower():
        target_lat, target_lon = 1.3494, 103.9564
    else:
        target_lat, target_lon = 1.4044, 103.8688

    station_map = {s["id"]: s for s in stations}

    best_station = None
    best_dist = float("inf")
    for s in stations:
        slat = s.get("location", {}).get("latitude", 0)
        slon = s.get("location", {}).get("longitude", 0)
        d = ((target_lat - slat) ** 2 + (target_lon - slon) ** 2) ** 0.5
        if d < best_dist:
            best_dist = d
            best_station = s

    if best_station:
        sid = best_station["id"]
        for r in readings[0].get("data", []):
            if r.get("stationId") == sid:
                return best_station.get("name", ""), r.get("value")
    return None, None


def get_psi():
    """Get PSI reading (east/central region)."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/psi")
    if not data or not data.get("data"):
        return None
    items = data["data"].get("items", [])
    if not items:
        return None
    readings = items[0].get("readings", {})
    if "psi_twenty_four_hourly" in readings:
        vals = readings["psi_twenty_four_hourly"]
    else:
        vals = readings
    if not vals:
        return None
    for region in ["east", "central", "northeast", "north"]:
        if region in vals:
            return vals[region]
    return next(iter(vals.values()))


def get_pm25():
    """Get PM2.5 reading."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/pm25")
    if not data or not data.get("data"):
        return None
    items = data["data"].get("items", [])
    if not items:
        return None
    readings = items[0].get("readings", {}).get("pm25_one_hourly", {})
    if not readings:
        return None
    for region in ["east", "central", "north", "northeast"]:
        if region in readings:
            return readings[region]
    return next(iter(readings.values()))


def get_uv():
    """Get UV index (v1 API, only available 7AM-7PM)."""
    headers = {
        "User-Agent": "SG-Weatherman-Alert/2.0",
        "x-api-key": API_KEY,
    }
    req = urllib.request.Request(
        "https://api.data.gov.sg/v1/environment/uv-index",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("items", [])
        if not items:
            return None
        index_list = items[0].get("index", [])
        if index_list:
            return index_list[0].get("value")
    except Exception:
        pass
    return None


def main():
    now = datetime.now(SGT)
    state = load_state()
    alerts = []
    new_state: dict = {"last_check": now.isoformat()}

    # ── Check rainfall ───────────────────────────────────────────
    for area in ["Tampines", "Seletar"]:
        rain = get_area_rainfall(area)
        max_rain = max((r["value"] for r in rain), default=0)
        rain_key = f"rain_{area.lower()}"
        prev_rain = state.get(rain_key, 0)

        # Rain just started
        if prev_rain < RAIN_START_THRESHOLD and max_rain >= RAIN_START_THRESHOLD:
            alerts.append(f"Rain starting in {area}: {max_rain}mm — bring umbrella")
        # Rain stopped
        elif prev_rain >= RAIN_START_THRESHOLD and max_rain < RAIN_START_THRESHOLD:
            alerts.append(f"Rain stopped in {area} — all clear")
        # Heavy rain
        if max_rain >= RAIN_HEAVY_THRESHOLD:
            alerts.append(f"Heavy rain in {area}: {max_rain}mm — avoid flooded areas")

        new_state[rain_key] = max_rain

    # ── Check PSI ────────────────────────────────────────────────
    psi = get_psi()
    prev_psi = state.get("psi", 0)
    if psi is not None:
        if prev_psi < PSI_UNHEALTHY and psi >= PSI_UNHEALTHY:
            alerts.append(f"PSI unhealthy: {psi} — limit outdoor activities")
        elif prev_psi < PSI_VERY_UNHEALTHY and psi >= PSI_VERY_UNHEALTHY:
            alerts.append(f"PSI very unhealthy: {psi} — stay indoors if possible")
        new_state["psi"] = psi

    # ── Check PM2.5 ──────────────────────────────────────────────
    pm25 = get_pm25()
    prev_pm25 = state.get("pm25", 0)
    if pm25 is not None:
        if prev_pm25 < PM25_UNHEALTHY and pm25 >= PM25_UNHEALTHY:
            alerts.append(f"Haze alert: PM2.5 {pm25}µg/m³ — avoid outdoor exertion")
        new_state["pm25"] = pm25

    # ── Check UV (only 7AM-7PM) ──────────────────────────────────
    hour = now.hour
    if 7 <= hour <= 19:
        uv = get_uv()
        prev_uv = state.get("uv", 0)
        if uv is not None:
            if prev_uv < UV_EXTREME and uv >= UV_EXTREME:
                alerts.append(f"UV EXTREME: {uv} — avoid sun 10am-4pm")
            elif prev_uv < UV_HIGH and uv >= UV_HIGH:
                alerts.append(f"UV high: {uv} — seek shade, wear sunscreen")
            new_state["uv"] = uv

    # ── Check temperature swing ──────────────────────────────────
    for area in ["Tampines", "Seletar"]:
        station, temp = get_area_temp(area)
        temp_key = f"temp_{area.lower()}"
        prev_temp = state.get(temp_key)
        if temp is not None and prev_temp is not None:
            swing = abs(temp - prev_temp)
            if swing >= TEMP_SWING_THRESHOLD:
                direction = "rising" if temp > prev_temp else "dropping"
                alerts.append(f"Temp {direction} in {area}: {prev_temp}°C → {temp}°C")
        if temp is not None:
            new_state[temp_key] = temp

    # ── Output ───────────────────────────────────────────────────
    save_state(new_state)

    if alerts:
        timestamp = now.strftime("%H:%M SGT")
        print(f"WEATHER ALERT {timestamp}")
        print()
        for a in alerts:
            print(f"- {a}")
        print()
        print("Stay safe!")
    # Exit 0 always — no_agent: output = deliver, empty = silent


if __name__ == "__main__":
    main()
