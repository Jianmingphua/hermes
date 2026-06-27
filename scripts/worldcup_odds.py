#!/usr/bin/env python3
"""
World Cup 2026 - Odds Fetcher (The Odds API)
Pulls betting odds from 40+ bookmakers. Free tier: 500 credits/month.

Usage:
  python3 worldcup_odds.py              # Upcoming matches with best odds
  python3 worldcup_odds.py --live       # Include live matches

State: /opt/hermes/scripts/worldcup_odds_cache.json
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

API_BASE = "https://api.the-odds-api.com/v4"
SGT = timezone(timedelta(hours=8))
STATE_FILE = "/opt/hermes/scripts/worldcup_odds_cache.json"

API_KEY = os.environ.get("ODDS_API_KEY", "")

# Load from .env if not in environment
if not API_KEY:
    _env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("ODDS_API_KEY=") and not _line.startswith("#"):
                    API_KEY = _line.split("=", 1)[1].strip()
                    break

SPORT_KEY = "soccer_fifa_world_cup"
MARKETS = "h2h,spreads,totals"  # 1X2, handicap, over/under
REGIONS = "eu,uk"  # European + UK bookmakers


def api_get(path, params):
    """GET request to The Odds API."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "WorldCupOdds/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[ERROR] Odds API: {e}", file=sys.stderr)
        return None


def load_cache():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_fetch": None, "odds": {}}


def save_cache(cache):
    with open(STATE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_odds():
    """Fetch all World Cup matches with odds."""
    params = {
        "apiKey": API_KEY,
        "sport": SPORT_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": "decimal",
    }
    data = api_get(f"/sports/{SPORT_KEY}/odds", params)
    return data


def time_to_sgt(start_time):
    """Convert ISO time to SGT string."""
    if not start_time:
        return "?"
    try:
        dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        dt_sgt = dt.astimezone(SGT)
        return dt_sgt.strftime("%m/%d %H:%M")
    except Exception:
        return start_time


def extract_best_1x2(bookmakers, home_team, away_team):
    """Find best (highest) 1X2 odds across all bookmakers."""
    best_home, best_draw, best_away = 0, 0, 0
    best_home_bm, best_draw_bm, best_away_bm = "", "", ""

    for bm in bookmakers:
        bm_name = bm.get("title", bm.get("key", "?"))
        for m in bm.get("markets", []):
            if m.get("key") != "h2h":
                continue
            for o in m.get("outcomes", []):
                name = o.get("name", "")
                price = o.get("price", 0)
                if not price:
                    continue
                if name == home_team:
                    if price > best_home:
                        best_home = price
                        best_home_bm = bm_name
                elif name == away_team:
                    if price > best_away:
                        best_away = price
                        best_away_bm = bm_name
                elif name.lower() == "draw":
                    if price > best_draw:
                        best_draw = price
                        best_draw_bm = bm_name

    return best_home, best_draw, best_away, best_home_bm, best_draw_bm, best_away_bm


def extract_best_totals(bookmakers, line=2.5):
    """Find best over/under odds for a specific line."""
    best_over, best_under = 0, 0
    best_over_bm, best_under_bm = "", ""

    for bm in bookmakers:
        bm_name = bm.get("title", bm.get("key", "?"))
        for m in bm.get("markets", []):
            if m.get("key") != "totals":
                continue
            for o in m.get("outcomes", []):
                name = o.get("name", "")
                price = o.get("price", 0)
                point = o.get("point")
                if not price or point is None or float(point) != line:
                    continue
                if name == "Over" and price > best_over:
                    best_over = price
                    best_over_bm = bm_name
                elif name == "Under" and price > best_under:
                    best_under = price
                    best_under_bm = bm_name

    return best_over, best_under, best_over_bm, best_under_bm


def implied_prob(decimal_odds):
    """Convert decimal odds to implied probability %."""
    if decimal_odds and decimal_odds > 1:
        return 100.0 / decimal_odds
    return 0.0

def format_match_odds(match):
    """Format odds for a single match into a Telegram-friendly block."""
    home = match.get("home_team", "?")
    away = match.get("away_team", "?")
    start_time = match.get("commence_time", "")
    sgt_time = time_to_sgt(start_time)
    bookmakers = match.get("bookmakers", [])

    output = f"⚡ {home} vs {away} — {sgt_time} SGT\n"

    if not bookmakers:
        return output + "  (no odds available)\n"

    lines = []

    # 1X2
    bh, bd, ba, bh_bm, bd_bm, ba_bm = extract_best_1x2(bookmakers, home, away)
    if bh and bd and ba:
        hp, dp, ap = implied_prob(bh), implied_prob(bd), implied_prob(ba)
        lines.append(
            f"  📊 1X2: "
            f"{home} {bh:.2f} ({hp:.0f}%) | "
            f"Draw {bd:.2f} ({dp:.0f}%) | "
            f"{away} {ba:.2f} ({ap:.0f}%)"
        )

    # O/U 2.5
    bo, bu, bo_bm, bu_bm = extract_best_totals(bookmakers, 2.5)
    if bo and bu:
        op, up = implied_prob(bo), implied_prob(bu)
        lines.append(
            f"  📊 O/U 2.5: "
            f"Over {bo:.2f} ({op:.0f}%) | "
            f"Under {bu:.2f} ({up:.0f}%)"
        )

    # Handicap
    for bm in bookmakers[:5]:
        for m in bm.get("markets", []):
            if m.get("key") == "spreads":
                outcomes = m.get("outcomes", [])
                if len(outcomes) >= 2:
                    h = outcomes[0]
                    a = outcomes[1]
                    lines.append(
                        f"  📊 Handicap: "
                        f"{h.get('name','?')} {h.get('point'):+} @ {h.get('price',0):.2f} | "
                        f"{a.get('name','?')} {a.get('point'):+} @ {a.get('price',0):.2f}"
                    )
                    break
        else:
            continue
        break

    lines.append(f"  📈 {len(bookmakers)} bookmakers")
    output += "\n".join(lines) + "\n"
    return output


def main():
    if not API_KEY:
        print("⚠️ ODDS_API_KEY environment variable not set.\n")
        print("Get a free key at: https://the-odds-api.com/#get-access\n")
        print("Then: export ODDS_API_KEY=your_key_here")
        return

    now = datetime.now(SGT)

    print("🔄 Fetching World Cup odds...", file=sys.stderr)
    data = fetch_odds()

    if not data:
        print("❌ Could not fetch odds. Check API key or rate limit.")
        return

    # Separate upcoming vs live
    upcoming = []
    live = []
    for match in data:
        start_time = match.get("commence_time", "")
        if start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                dt_sgt = dt.astimezone(SGT)
                if dt_sgt > now:
                    upcoming.append(match)
                else:
                    live.append(match)
            except Exception:
                upcoming.append(match)

    # Sort by start time
    upcoming.sort(key=lambda m: m.get("commence_time", ""))
    live.sort(key=lambda m: m.get("commence_time", ""))

    show_live = "--live" in sys.argv
    output_parts = []

    if upcoming:
        output_parts.append("🎰 World Cup 2026 — Upcoming Odds\n")
        for match in upcoming[:15]:
            output_parts.append(format_match_odds(match))

    if show_live and live:
        output_parts.append("\n🔴 Live Now\n")
        for match in live[:5]:
            output_parts.append(format_match_odds(match))

    if output_parts:
        print("\n".join(output_parts))
    else:
        print("No upcoming matches found with odds.")

    # Cache
    cache = load_cache()
    cache["last_fetch"] = now.isoformat()
    cache["match_count"] = len(data)
    save_cache(cache)

    print(f"\n[{now.strftime('%H:%M:%S')} SGT] {len(data)} matches "
          f"({len(upcoming)} upcoming, {len(live)} live)", file=sys.stderr)


if __name__ == "__main__":
    main()
