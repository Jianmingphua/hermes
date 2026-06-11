#!/usr/bin/env python3
"""
Forex Bot Health Check — Cron-friendly watchdog.
Exits with:
  0 + stdout = healthy (outputs status for cron delivery)
  1 = healthy but silent (no need to alert)
  2 = unhealthy (something is wrong, output explains)

Runs every 15 min to ensure the bot is operational.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BOT_DIR = Path("/opt/hermes/forex-trading-bot")
LOG_DIR = BOT_DIR / "logs"
CB_FILE = LOG_DIR / "circuit_breaker.json"
BT_FILE = LOG_DIR / "balance_tracker.json"
PID_FILE = LOG_DIR / "bot.pid"
MAX_LOG_AGE_MIN = 20  # If no log in 20 min, bot might be stuck

issues = []

# ── 1. Check if bot process is running ─────────────────────────
try:
    result = subprocess.run(
        ["pgrep", "-f", "main.py.*--mode.*loop"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        # No loop process — might be running via cron (no --loop flag)
        result2 = subprocess.run(
            ["pgrep", "-f", "main.py"],
            capture_output=True, text=True, timeout=5,
        )
        if result2.returncode != 0:
            issues.append("No main.py process running")
except subprocess.TimeoutExpired:
    issues.append("Process check timed out")

# ── 2. Check recent log activity ──────────────────────────────
try:
    logs = sorted(LOG_DIR.glob("bot_*.log"))
    if logs:
        latest_log = logs[-1]
        mtime = datetime.fromtimestamp(latest_log.stat().st_mtime, tz=timezone.utc)
        age_min = (datetime.now(timezone.utc) - mtime).total_seconds() / 60
        if age_min > MAX_LOG_AGE_MIN:
            issues.append(
                f"Log file stale: {latest_log.name} last modified {age_min:.0f} min ago"
            )
except OSError as e:
    issues.append(f"Cannot read logs: {e}")

# ── 3. Check circuit breaker state ────────────────────────────
if CB_FILE.exists():
    try:
        cb = json.loads(CB_FILE.read_text())
        if cb.get("is_tripped", False):
            # Check if cooldown has expired — if so, don't alert
            cooldown_str = cb.get("cooldown_until")
            if cooldown_str:
                from datetime import datetime, timezone
                cooldown_until = datetime.fromisoformat(cooldown_str)
                now = datetime.now(timezone.utc)
                if now >= cooldown_until:
                    # Cooldown expired — stale state, skip
                    pass
                else:
                    issues.append(
                        f"Circuit breaker TRIPPED: {cb.get('trip_reason', 'unknown')} "
                        f"(cooldown until {cooldown_until.strftime('%Y-%m-%d %H:%M')} UTC)"
                    )
            else:
                # No cooldown = hard trip (manual reset required)
                issues.append(
                    f"Circuit breaker HARD TRIPPED: {cb.get('trip_reason', 'unknown')} "
                    f"(requires manual reset)"
                )
    except (json.JSONDecodeError, OSError):
        pass

# ── 4. Check balance — alert on large unexpected drop ────────
if BT_FILE.exists():
    try:
        bt = json.loads(BT_FILE.read_text())
        balance = bt.get("balance", 0)
        if balance < 98000:  # Below 98K from 100K start
            issues.append(
                f"Account balance low: {balance:.2f} SGD (below 98K threshold)"
            )
    except (json.JSONDecodeError, OSError):
        pass

# ── Output ──────────────────────────────────────────────────────
if issues:
    print("❌ Forex Bot Health Issues:")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")
# Exit 0 always — no_agent mode sends stdout if non-empty, stays silent if empty.
# Exit code 0+empty stdout = silent (healthy).
# Exit code 0+non-empty stdout = message delivered (issues found).
sys.exit(0)