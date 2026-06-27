#!/usr/bin/env python3
"""
World Cup 2026 Live Goal Notifier
Polls the worldcup26.ir API and outputs goal notifications to stdout.
Designed for Hermes cron — stdout is delivered to Telegram.

State: /opt/hermes/scripts/worldcup_state.json
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

API_BASE = "https://worldcup26.ir"
STATE_FILE = "/opt/hermes/scripts/worldcup_state.json"
SGT = timezone(timedelta(hours=8))


def api_get(path):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "WorldCupBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[ERROR] API call failed: {e}", file=sys.stderr)
        return None


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"goals": {}, "matches": {}, "last_check": None}


def save_state(state):
    state["last_check"] = datetime.now(SGT).isoformat()
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def parse_scorers(scorers_str):
    """Parse scorers like '{\"J. Neves 6'\",\"C. Metcalfe 75'\"}' into clean list."""
    if not scorers_str or scorers_str == "null":
        return []
    
    # The API returns a JSON set-like format with escaped quotes and curly braces
    # Try to extract individual scorer entries

    # Remove outer braces and split by comma
    cleaned = scorers_str.strip()
    if cleaned.startswith("{"):
        cleaned = cleaned[1:]
    if cleaned.endswith("}"):
        cleaned = cleaned[:-1]
    
    # Extract quoted strings (they have escaped quotes)
    # Pattern: \"Name Minute'\"
    entries = re.findall(r'"([^"]+?)"', cleaned)
    
    result = []
    for entry in entries:
        entry = entry.strip()
        if entry:
            result.append(entry)
    
    return result


def extract_minute(scorer_str):
    """Extract minute from 'J. Neves 6'' → 6'"""
    if not scorer_str:
        return "?"
    # Match pattern: number followed by optional +, then '
    match = re.search(r"(\d+\+?\d*)\s*'", scorer_str)
    if match:
        return f"{match.group(1)}'"
    return "?"


def main():
    state = load_state()

    data = api_get("/get/games")
    if not data or "games" not in data:
        save_state(state)
        return

    # Fetch stadium names
    stadium_data = api_get("/get/stadiums")
    stadium_cache = {}
    if stadium_data:
        for s in stadium_data.get("stadiums", []):
            sid = str(s.get("id", ""))
            if sid:
                stadium_cache[sid] = s.get("fifa_name", s.get("name_en", "🏟️"))

    games = data["games"]
    messages = []

    for game in games:
        game_id = str(game.get("id", ""))
        home_team = game.get("home_team_name_en", "Unknown")
        away_team = game.get("away_team_name_en", "Unknown")
        home_score_str = str(game.get("home_score", "0"))
        away_score_str = str(game.get("away_score", "0"))
        home_scorers_raw = game.get("home_scorers", "null")
        away_scorers_raw = game.get("away_scorers", "null")
        time_elapsed = game.get("time_elapsed", "notstarted")
        group = game.get("group", "?")
        stadium_id = str(game.get("stadium_id", ""))
        stadium = stadium_cache.get(stadium_id, "🏟️")
        match_date = game.get("local_date", "")

        try:
            home_score = int(home_score_str) if home_score_str and home_score_str != "null" else 0
            away_score = int(away_score_str) if away_score_str and away_score_str != "null" else 0
        except ValueError:
            home_score = away_score = 0

        home_scorers = parse_scorers(home_scorers_raw)
        away_scorers = parse_scorers(away_scorers_raw)

        # Match status tracking
        match_key = f"{game_id}_status"
        prev_status = state.get("matches", {}).get(match_key)

        # Kick-off notification
        if prev_status in ("notstarted", None) and time_elapsed not in ("notstarted", "Finished", "finished"):
            if prev_status == "notstarted":
                messages.append(
                    f"🔔 KICK-OFF\n\n"
                    f"{home_team} vs {away_team}\n"
                    f"🏟️ {stadium} | Group {group}\n"
                    f"🕐 {match_date}"
                )

        # Full-time notification
        if prev_status not in ("Finished", "finished") and time_elapsed in ("Finished", "finished"):
            messages.append(
                f"🏁 FULL-TIME\n\n"
                f"{home_team} {home_score} - {away_score} {away_team}\n"
                f"🏟️ {stadium} | Group {group}"
            )

        # Goal detection — count scorers per team per game
        home_key = f"{game_id}_home_goals"
        away_key = f"{game_id}_away_goals"
        prev_home = state.get("goals", {}).get(home_key, 0)
        prev_away = state.get("goals", {}).get(away_key, 0)

        # New home goals
        if len(home_scorers) > prev_home:
            for i in range(prev_home, len(home_scorers)):
                scorer = home_scorers[i]
                minute = extract_minute(scorer)
                messages.append(
                    f"⚽ GOAL!\n\n"
                    f"{home_team} {home_score} - {away_score} {away_team}\n\n"
                    f"🏟️ {stadium} | Group {group}\n"
                    f"🥅 {home_team}: {scorer} ({minute})"
                )

        # New away goals
        if len(away_scorers) > prev_away:
            for i in range(prev_away, len(away_scorers)):
                scorer = away_scorers[i]
                minute = extract_minute(scorer)
                messages.append(
                    f"⚽ GOAL!\n\n"
                    f"{home_team} {home_score} - {away_score} {away_team}\n\n"
                    f"🏟️ {stadium} | Group {group}\n"
                    f"🥅 {away_team}: {scorer} ({minute})"
                )

        # Update state
        if "goals" not in state:
            state["goals"] = {}
        if "matches" not in state:
            state["matches"] = {}
        state["goals"][home_key] = len(home_scorers)
        state["goals"][away_key] = len(away_scorers)
        state["matches"][match_key] = time_elapsed

    # Check if this is first run (no prior state file)
    is_first_run = state.get("last_check") is None

    # Output messages (skip first run to avoid spamming historical data)
    if messages and not is_first_run:
        for i, msg in enumerate(messages):
            if i > 0:
                print("\n---\n")
            print(msg)
    
    # Logging to stderr
    live_count = sum(1 for g in games if g.get("time_elapsed") not in ("notstarted", "finished", "Finished"))
    if is_first_run:
        msg_type = "state initialized (historical data silent)"
    elif messages:
        msg_type = f"{len(messages)} notifications"
    else:
        msg_type = "no changes"
    print(f"[{datetime.now(SGT).strftime('%Y-%m-%d %H:%M:%S')} SGT] "
          f"{len(games)} total, {live_count} live, {msg_type}", file=sys.stderr)

    save_state(state)


if __name__ == "__main__":
    main()
