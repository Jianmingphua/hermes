#!/usr/bin/env python3
"""
Singapore Weather Alert Script
Monitors NEA 2-hour weather forecast and rainfall for Tampines and Seletar.
Only outputs a message when bad weather is detected (for cron no_agent delivery).
Locations are shown in separate sections.
"""

import json
import urllib.request
import urllib.error
import os
import sys
from datetime import datetime

# Configuration
AREAS = ["Tampines", "Seletar"]
FORECAST_URL = "https://api.data.gov.sg/v1/environment/2-hour-weather-forecast"
RAINFALL_URL = "https://api.data.gov.sg/v1/environment/rainfall"

# Bad weather conditions (case-insensitive match)
BAD_WEATHER = [
    "heavy rain",
    "heavy showers",
    "thundery showers",
    "thunderstorm",
    "storm",
    "moderate rain",
    "moderate showers",
    "light rain",
    "light showers",
    "showers",
    "rain",
]

# Rainfall threshold (mm per reading period)
RAIN_THRESHOLD = 2.0  # mm

def fetch_json(url):
    """Fetch JSON from URL with timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "SG-Weather-Alert/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

def check_forecast():
    """Check 2-hour weather forecast for monitored areas."""
    alerts = []
    try:
        data = fetch_json(FORECAST_URL)
        for item in data.get("items", []):
            valid_from = item.get("valid_period", {}).get("start", "unknown")
            valid_to = item.get("valid_period", {}).get("end", "unknown")
            for forecast in item.get("forecasts", []):
                if forecast.get("area") in AREAS:
                    condition = forecast.get("forecast", "").lower()
                    for bad in BAD_WEATHER:
                        if bad in condition:
                            alerts.append({
                                "type": "forecast",
                                "area": forecast["area"],
                                "condition": forecast["forecast"],
                                "valid_from": valid_from,
                                "valid_to": valid_to,
                            })
                            break
    except Exception as e:
        print(f"Warning: Could not fetch forecast: {e}", file=sys.stderr)
    return alerts

def check_rainfall():
    """Check current rainfall across all stations."""
    alerts = []
    try:
        data = fetch_json(RAINFALL_URL)
        # Build station map
        station_map = {}
        for station in data.get("metadata", {}).get("stations", []):
            station_map[station["id"]] = station

        for item in data.get("items", []):
            for reading in item.get("readings", []):
                value = reading.get("value", 0)
                if value >= RAIN_THRESHOLD:
                    station_id = reading.get("station_id", "")
                    station = station_map.get(station_id, {})
                    station_name = station.get("name", station_id)
                    lat = station.get("location", {}).get("latitude", 0)
                    lon = station.get("location", {}).get("longitude", 0)
                    alerts.append({
                        "type": "rainfall",
                        "station": station_name,
                        "value": value,
                        "lat": lat,
                        "lon": lon,
                    })
    except Exception as e:
        print(f"Warning: Could not fetch rainfall: {e}", file=sys.stderr)
    return alerts

def find_nearest_stations(rainfall_alerts, target_areas_coords):
    """Find rainfall stations nearest to each target area."""
    # Simple nearest by station name matching for known stations
    # Tampines area stations
    tampines_keywords = ["tampines", "simei", "pasir ris", "bedok", "tanah merah", "expo"]
    seletar_keywords = ["seletar", "yishun", "sembawang", "canberra", "woodlands", "admiralty"]

    result = {}
    for alert in rainfall_alerts:
        station_lower = alert["station"].lower()
        for kw in tampines_keywords:
            if kw in station_lower:
                result.setdefault("Tampines", []).append(alert)
                break
        for kw in seletar_keywords:
            if kw in station_lower:
                result.setdefault("Seletar", []).append(alert)
                break
    return result

def format_alert_message(forecast_alerts, rainfall_alerts):
    """Format alert message for Telegram with separate sections per location."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"⛈ WEATHER ALERT"]
    lines.append(f"🕐 {now}")
    lines.append("")

    # Group forecast alerts by area
    forecast_by_area = {}
    for a in forecast_alerts:
        area = a["area"]
        if area not in forecast_by_area:
            forecast_by_area[area] = a

    # Group rainfall alerts by nearest area
    rainfall_by_area = find_nearest_stations(rainfall_alerts, {})

    # Track if we have any content
    has_content = False

    for area in AREAS:
        area_forecast = forecast_by_area.get(area)
        area_rainfall = rainfall_by_area.get(area, [])

        if not area_forecast and not area_rainfall:
            continue

        has_content = True
        lines.append(f"📍 {area}")
        lines.append("─" * 20)

        if area_forecast:
            lines.append(f"  🌧 Forecast: {area_forecast['condition']}")
            lines.append(f"     Valid: {area_forecast['valid_from'][:16]} to {area_forecast['valid_to'][:16]}")

        if area_rainfall:
            lines.append(f"  💧 Rainfall:")
            # Show top 3 stations per area
            sorted_rain = sorted(area_rainfall, key=lambda x: x["value"], reverse=True)[:3]
            for r in sorted_rain:
                lines.append(f"     • {r['station']}: {r['value']}mm")

        lines.append("")

    if not has_content:
        # Fallback: show all rainfall if no area-specific match
        if rainfall_alerts:
            lines.append("📍 Other Areas")
            lines.append("─" * 20)
            lines.append("  💧 Rainfall:")
            sorted_rain = sorted(rainfall_alerts, key=lambda x: x["value"], reverse=True)[:5]
            for r in sorted_rain:
                lines.append(f"     • {r['station']}: {r['value']}mm")
            lines.append("")

    lines.append("Stay safe! ☔")
    return "\n".join(lines)

def main():
    forecast_alerts = check_forecast()
    rainfall_alerts = check_rainfall()

    if forecast_alerts or rainfall_alerts:
        message = format_alert_message(forecast_alerts, rainfall_alerts)
        print(message)
    # Exit 0 always — no_agent mode: 0+output = deliver, 0+empty = silent.
    sys.exit(0)

if __name__ == "__main__":
    main()
