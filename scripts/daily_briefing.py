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
from datetime import datetime, timezone, timedelta
from pathlib import Path

SGT = timezone(timedelta(hours=8))
BOT_DIR = Path("/opt/hermes/forex-trading-bot")
LOG_DIR = BOT_DIR / "logs"
BOARD_STATE_DIR = Path("/opt/hermes/scripts/board_state")
BOARD_STATE_DIR.mkdir(exist_ok=True)

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
        parts.append(f"⚠️ Circuit breaker tripped (L{cb.get('escalation_level', '?')})")
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

    # Cron count
    try:
        result = subprocess.run(
            ["cronjob", "list"],  # won't work from subprocess, skip
            capture_output=True, text=True, timeout=5
        )
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


# ── Weatherman Agent — SG Weather ──────────────────────────────
def run_weatherman() -> str:
    """Generate weather report for Tampines."""
    import urllib.request
    try:
        req = urllib.request.Request(
            "https://api.data.gov.sg/v1/environment/2-hour-weather-forecast",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            forecasts = data.get("items", [{}])[0].get("forecasts", [])
            tampines = next((f for f in forecasts if f.get("area") == "Tampines"), {})
            forecast = tampines.get("forecast", "Unknown")

        # Current temp
        req2 = urllib.request.Request(
            "https://api-open.data.gov.sg/v2/real-time/api/air-temperature",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req2, timeout=10) as resp:
            temp_data = json.loads(resp.read())
            stations = temp_data.get("data", {}).get("stations", [])
            readings = temp_data.get("data", {}).get("readings", [])
            temp_str = ""
            if readings and stations:
                # Find Tampines-area station
                station_map = {s["id"]: s for s in stations}
                for r in readings[0].get("data", []):
                    sid = r.get("stationId", "")
                    station = station_map.get(sid, {})
                    name = station.get("name", "").lower()
                    if "tampines" in name or "pasir" in name or "bedok" in name:
                        temp_str = f", {r.get('value', '?')}°C"
                        break
                if not temp_str and readings[0].get("data"):
                    temp_str = f", {readings[0]['data'][0].get('value', '?')}°C"

        # Advice
        advice = []
        f_lower = forecast.lower()
        if "rain" in f_lower or "shower" in f_lower or "thunder" in f_lower:
            advice.append("bring umbrella")
        if "cloudy" in f_lower or "overcast" in f_lower:
            advice.append("good day for outdoor activities")

        advice_str = f" — {', '.join(advice)}" if advice else ""
        return f"Tampines: {forecast}{temp_str}{advice_str}"

    except Exception as e:
        return f"Weather: Unable to fetch ({str(e)[:40]})"


# ── Compile briefing ───────────────────────────────────────────
def compile_briefing() -> str:
    """Run all agents and compile the unified briefing."""
    now = datetime.now(SGT)
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M%p").lstrip("0")

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
        f"🌅 Good morning! Here's your daily briefing — {date_str}.",
        f"",
        f"💰 CFO: {cfo}",
        f"",
        f"📅 COO: {coo}",
        f"",
        f"🖥️ CTO: {cto}",
        f"",
        f"🌦️ Weatherman: {weather}",
        f"",
        f"Have a great day! 🚀",
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
