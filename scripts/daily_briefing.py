#!/usr/bin/env python3
"""
Multi-Agent Board — Daily Unified Briefing Generator.
Runs at 7:30am SGT. Collects state from all agent reports,
compiles a comprehensive daily briefing, outputs for TTS delivery.

This is the "Board Secretary" that reads all agent states and
produces a single unified morning briefing.
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

SGT = timezone(timedelta(hours=8))
BOT_DIR = Path("/opt/hermes/forex-trading-bot")
LOG_DIR = BOT_DIR / "logs"
BOARD_STATE_DIR = Path("/opt/hermes/scripts/board_state")
BOARD_STATE_DIR.mkdir(exist_ok=True)

# ── API Key for data.gov.sg ─────────────────────────────────────
API_KEY = "7TPJnYHHaUltgiGH0qkXjLcETeaDh3Cu"




def fetch_json(url, params=None):
    """Fetch JSON from data.gov.sg API with key auth."""
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"
    headers = {
        "User-Agent": "SG-Weatherman/2.0",
        "x-api-key": API_KEY,
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def fetch_v1_json(url):
    """Fetch from legacy v1 API (no key needed, but add UA)."""
    headers = {"User-Agent": "SG-Weatherman/2.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def find_area_temp(area_name):
    """Find current temperature for an area (Tampines or Seletar) by nearest station."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/air-temperature")
    if not data:
        return None, None

    stations = data.get("data", {}).get("stations", [])
    readings = data.get("data", {}).get("readings", [])
    if not readings or not stations:
        return None, None

    # Target coordinates (geocoded)
    if "tampines" in area_name.lower():
        target_lat, target_lon = 1.3494, 103.9564  # Tampines
    else:
        target_lat, target_lon = 1.4044, 103.8688  # Seletar

    station_map = {s["id"]: s for s in stations}

    # Find nearest station by coordinates
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
                return best_station.get("name", "Unknown"), r.get("value")

    # Fallback: return first reading
    if readings[0].get("data"):
        first = readings[0]["data"][0]
        sid = first.get("stationId", "")
        station = station_map.get(sid, {})
        return station.get("name", "Unknown"), first.get("value")
    return None, None


def fetch_rainfall_area(area_name):
    """Get current rainfall for area stations (nearest by coordinates)."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/rainfall")
    if not data:
        return []

    stations = data.get("data", {}).get("stations", [])
    readings = data.get("data", {}).get("readings", [])
    if not readings or not stations:
        return []

    if "tampines" in area_name.lower():
        target_lat, target_lon = 1.3494, 103.9564  # Tampines
    else:
        target_lat, target_lon = 1.4044, 103.8688  # Seletar

    station_map = {s["id"]: s for s in stations}

    # Find stations within ~15km radius
    nearby = []
    for s in stations:
        slat = s.get("location", {}).get("latitude", 0)
        slon = s.get("location", {}).get("longitude", 0)
        d = ((target_lat - slat) ** 2 + (target_lon - slon) ** 2) ** 0.5
        if d < 0.15:  # ~15km radius
            nearby.append(s["id"])

    if not nearby:
        # Fallback: just take the 3 nearest
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


def fetch_pm25():
    """Get PM2.5 readings by region."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/pm25")
    if not data or not data.get("data"):
        return None
    items = data["data"].get("items", [])
    if not items:
        return None
    readings = items[0].get("readings", {}).get("pm25_one_hourly", {})
    return readings if readings else None


def fetch_psi():
    """Get PSI readings by region (24-hourly)."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/psi")
    if not data or not data.get("data"):
        return None
    items = data["data"].get("items", [])
    if not items:
        return None
    readings = items[0].get("readings", {})
    # API returns psi_twenty_four_hourly (not psi_one_hourly)
    if "psi_twenty_four_hourly" in readings:
        return readings["psi_twenty_four_hourly"]
    return readings if readings else None


def fetch_uv():
    """Get UV index (v1 API, only available 7AM-7PM)."""
    data = fetch_v1_json("https://api.data.gov.sg/v1/environment/uv-index")
    if not data:
        return None
    items = data.get("items", [])
    if not items:
        return None
    # v1 returns items[0]["index"] as a list of {value, timestamp}
    index_list = items[0].get("index", [])
    if index_list:
        # Return the most recent value
        return index_list[0].get("value")
    return None


def fetch_humidity():
    """Get relative humidity for area."""
    data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/relative-humidity")
    if not data or not data.get("data"):
        return None
    stations = data["data"].get("stations", [])
    readings = data["data"].get("readings", [])
    if not readings or not stations:
        return None

    station_map = {s["id"]: s for s in stations}
    # Average across all stations for a general reading
    vals = []
    for r in readings[0].get("data", []):
        v = r.get("value")
        if v is not None:
            vals.append(v)
    if vals:
        return round(sum(vals) / len(vals), 1)
    return None


def fetch_wind():
    """Get wind speed and direction for area."""
    speed_data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/wind-speed")
    dir_data = fetch_json("https://api-open.data.gov.sg/v2/real-time/api/wind-direction")

    result = {}
    if speed_data and speed_data.get("data"):
        readings = speed_data["data"].get("readings", [])
        if readings:
            vals = [r.get("value") for r in readings[0].get("data", []) if r.get("value") is not None]
            if vals:
                result["speed"] = round(sum(vals) / len(vals), 1)

    if dir_data and dir_data.get("data"):
        readings = dir_data["data"].get("readings", [])
        if readings:
            vals = [r.get("value") for r in readings[0].get("data", []) if r.get("value") is not None]
            if vals:
                avg_deg = sum(vals) / len(vals)
                # Convert to cardinal
                dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
                idx = round(avg_deg / 22.5) % 16
                result["direction"] = dirs[idx]

    return result if result else None


def get_nearest_region_pm25(readings):
    """Get PM2.5 for central region (closest to Tampines)."""
    if not readings:
        return None
    # Tampines is closest to East region, but Central is also useful
    for region in ["east", "central", "north", "northeast"]:
        if region in readings:
            return readings[region]
    # Return first available
    return next(iter(readings.values())) if readings else None


def get_nearest_region_psi(readings):
    """Get PSI for central/east region."""
    if not readings:
        return None
    for region in ["east", "central", "northeast", "north"]:
        if region in readings:
            return readings[region]
    return next(iter(readings.values())) if readings else None


def get_nearest_region_uv(uv_val):
    """Get UV index value (already a single scalar from v1 API)."""
    return uv_val


def get_aqi_advice(pm25):
    """Return health advice based on PM2.5 (µg/m³)."""
    if pm25 is None:
        return None
    if pm25 > 150:
        return "HAZE ALERT: PM2.5 very unhealthy — avoid outdoor exertion"
    elif pm25 > 100:
        return "PM2.5 unhealthy — limit outdoor activities"
    elif pm25 > 55:
        return "PM2.5 moderate — sensitive groups take care"
    return None


def get_psi_advice(psi):
    """Return health advice based on PSI."""
    if psi is None:
        return None
    if psi > 200:
        return "HAZEOUS: PSI >200 — stay indoors"
    elif psi > 150:
        return "PQI unhealthy — avoid prolonged outdoor exertion"
    elif psi > 100:
        return "PSI unhealthy for sensitive groups"
    return None


def get_uv_advice(uv):
    """Return sun protection advice based on UV index."""
    if uv is None:
        return None
    if uv >= 11:
        return "UV EXTREME — avoid sun entirely 10am-4pm"
    elif uv >= 8:
        return "UV very high — seek shade, SPF 50+"
    elif uv >= 6:
        return "UV high — wear hat and sunscreen"
    return None


def get_rain_advice(rainfall_mm):
    """Return advice based on current rainfall."""
    if rainfall_mm is None or rainfall_mm == 0:
        return None
    if rainfall_mm >= 10:
        return "HEAVY RAIN — bring umbrella, flooding possible"
    elif rainfall_mm >= 4:
        return "Rain falling — bring umbrella"
    elif rainfall_mm >= 1:
        return "Light rain — umbrella handy"
    return None


# ── CFO Agent — Financial summary ──────────────────────────────
def run_cfo() -> str:
    """Generate CFO report: forex account status, anomalies, weekly P&L."""
    bt = _load_json(LOG_DIR / "balance_tracker.json")
    cb = _load_json(LOG_DIR / "circuit_breaker.json")
    anomaly_state = _load_json(LOG_DIR / ".anomaly_state.json")
    trades_data = _load_json(LOG_DIR / "active_trades.json")

    if not bt:
        return "CFO: No account data available."

    balance = bt.get("balance", 0)
    parts = [f"Account: ${balance:,.0f}"]

    # Active trades
    active = []
    if isinstance(trades_data, dict):
        active = trades_data.get("active_trades", [])
    if active:
        unrealized = sum(t.get("last_unrealized_pnl", 0) or 0 for t in active)
        parts.append(f"Unrealized P&L: {unrealized:+.0f}")
        parts.append(f"Open positions: {len(active)}")
    else:
        parts.append("No open positions")

    # Circuit breaker
    if cb and cb.get("is_tripped"):
        parts.append(f"Circuit breaker tripped (L{cb.get('escalation_level', '?')})")
    else:
        parts.append("Circuit breaker: inactive")

    # Recent anomalies
    if anomaly_state and anomaly_state.get("alerts_sent"):
        alert_count = len(anomaly_state["alerts_sent"])
        if alert_count > 0:
            parts.append(f"{alert_count} anomaly alerts today")

    return " | ".join(parts)


# ── COO Agent — Operations & calendar ──────────────────────────
def run_coo() -> str:
    """Generate COO report: upcoming events, reminders, trip status."""
    parts = []

    # Check Thailand trip
    reminder_state = _load_json(Path("/opt/hermes/scripts/reminder_state.json"))
    if reminder_state:
        parts.append("Thailand trip reminders active")

    # Check for today's cron jobs status
    try:
        result = subprocess.run(
            ["pgrep", "-f", "main.py"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts.append("Forex bot: running")
        else:
            parts.append("Forex bot: not in loop mode (cron-driven)")
    except Exception:
        pass

    # Day of week context
    now = datetime.now(SGT)
    day_name = now.strftime("%A")
    parts.append(f"Today is {day_name}")

    if now.weekday() == 4:  # Friday
        parts.append("Weekly report will be sent at 6pm")

    return " | ".join(parts) if parts else "COO: All clear"


# ── CTO Agent — System health ──────────────────────────────────
def run_cto() -> str:
    """Generate CTO report: system health, disk, cron status."""
    parts = []

    # Disk usage
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                cols = lines[1].split()
                if len(cols) >= 5:
                    used_pct = cols[4].replace("%", "")
                    parts.append(f"Disk: {used_pct}% used")
    except Exception:
        pass

    # Memory
    try:
        result = subprocess.run(
            ["free", "-h"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                cols = lines[1].split()
                if len(cols) >= 3:
                    total = cols[1]
                    parts.append(f"RAM: {total} total")
    except Exception:
        pass

    # Check key log file ages
    key_logs = {
        "forex health": LOG_DIR / ".cb_notified",
        "anomaly state": LOG_DIR / ".anomaly_state.json",
        "balance": LOG_DIR / "balance_tracker.json",
    }
    fresh_count = 0
    for name, path in key_logs.items():
        if path.exists():
            age_h = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600
            if age_h < 2:
                fresh_count += 1
    parts.append(f"Data freshness: {fresh_count}/{len(key_logs)} sources recent")

    return " | ".join(parts) if parts else "CTO: All systems nominal"


# ── Weatherman Agent — SG Weather (v2 — data.gov.sg) ───────────
def run_weatherman() -> str:
    """Generate comprehensive weather report for Tampines & Seletar."""
    now = datetime.now(SGT)
    hour = now.hour

    # ── Forecast (2-hour) ────────────────────────────────────────
    forecasts = {}
    forecast_data = fetch_v1_json("https://api.data.gov.sg/v1/environment/2-hour-weather-forecast")
    if forecast_data:
        for item in forecast_data.get("items", []):
            for fc in item.get("forecasts", []):
                area = fc.get("area", "")
                if area in ("Tampines", "Seletar"):
                    forecasts[area] = fc.get("forecast", "Unknown")

    # ── Current temperature ───────────────────────────────────────
    tampines_station, tampines_temp = find_area_temp("Tampines")
    seletar_station, seletar_temp = find_area_temp("Seletar")

    # ── Rainfall (actual) ────────────────────────────────────────
    tampines_rain = fetch_rainfall_area("Tampines")
    seletar_rain = fetch_rainfall_area("Seletar")

    # ── Air quality ──────────────────────────────────────────────
    pm25_readings = fetch_pm25()
    psi_readings = fetch_psi()
    pm25_val = get_nearest_region_pm25(pm25_readings) if pm25_readings else None
    psi_val = get_nearest_region_psi(psi_readings) if psi_readings else None

    # ── UV Index ─────────────────────────────────────────────────
    uv_val = fetch_uv()  # v1 API returns scalar directly

    # ── Humidity ─────────────────────────────────────────────────
    humidity = fetch_humidity()

    # ── Wind ─────────────────────────────────────────────────────
    wind = fetch_wind()

    # ── Build report ─────────────────────────────────────────────
    sections = []

    for area in ["Tampines", "Seletar"]:
        area_parts = []
        fc = forecasts.get(area, "Unknown")
        temp = tampines_temp if area == "Tampines" else seletar_temp
        station = tampines_station if area == "Tampines" else seletar_station
        rain = tampines_rain if area == "Tampines" else seletar_rain

        # Temperature + forecast
        temp_str = f"{temp}°C" if temp is not None else "?"
        area_parts.append(f"{fc}, {temp_str}")

        # Rainfall (if actually raining)
        if rain:
            max_rain = max(r["value"] for r in rain)
            if max_rain > 0:
                rain_stations = [f"{r['station']}({r['value']}mm)" for r in rain if r["value"] > 0]
                if rain_stations:
                    area_parts.append(f"Rain: {', '.join(rain_stations[:2])}")

        # Health advisories
        advices = []
        max_rain_val = max(r["value"] for r in rain) if rain else 0
        rain_adv = get_rain_advice(max_rain_val)
        if rain_adv:
            advices.append(rain_adv)

        if area == "Tampines":  # Only add air quality once (covers both areas)
            pm25_adv = get_aqi_advice(pm25_val)
            if pm25_adv:
                advices.append(pm25_adv)
            psi_adv = get_psi_advice(psi_val)
            if psi_adv:
                advices.append(psi_adv)
            uv_adv = get_uv_advice(uv_val)
            if uv_adv:
                advices.append(uv_adv)

        if advices:
            area_parts.append(" | ".join(advices))

        sections.append(f"**{area}**: {' — '.join(area_parts)}")

    # Air quality summary (if notable)
    aqi_lines = []
    if pm25_val is not None:
        aqi_lines.append(f"PM2.5: {pm25_val}µg/m³")
    if psi_val is not None:
        aqi_lines.append(f"PSI: {psi_val}")
    if uv_val is not None and 7 <= hour <= 19:
        aqi_lines.append(f"UV: {uv_val}")
    if humidity is not None:
        aqi_lines.append(f"Humidity: {humidity}%")
    if wind:
        wind_str = ""
        if "speed" in wind:
            wind_str += f"{wind['speed']}km/h"
        if "direction" in wind:
            wind_str += f" {wind['direction']}"
        if wind_str:
            aqi_lines.append(f"Wind: {wind_str}")

    # Compile final report
    main_report = " | ".join(sections)

    if aqi_lines:
        main_report += f" | {' | '.join(aqi_lines)}"

    return main_report


# ── Compile briefing ───────────────────────────────────────────
def compile_briefing() -> str:
    """Run all agents and compile the unified briefing."""
    now = datetime.now(SGT)
    date_str = now.strftime("%A, %d %B %Y")

    # Run all agents
    cfo = run_cfo()
    coo = run_coo()
    cto = run_cto()
    weather = run_weatherman()

    # Save board state
    board_state = {
        "timestamp": now.isoformat(),
        "agents": {
            "cfo": cfo,
            "coo": coo,
            "cto": cto,
            "weatherman": weather,
        }
    }
    (BOARD_STATE_DIR / "latest_briefing.json").write_text(
        json.dumps(board_state, indent=2)
    )

    # Format for TTS / Telegram
    lines = [
        f"Good morning! Here's your daily briefing — {date_str}.",
        f"",
        f"CFO: {cfo}",
        f"",
        f"COO: {coo}",
        f"",
        f"CTO: {cto}",
        f"",
        f"Weatherman: {weather}",
        f"",
        f"Have a great day!",
    ]

    return "\n".join(lines)


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


if __name__ == "__main__":
    print(compile_briefing())
