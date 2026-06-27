#!/usr/bin/env python3
"""
World Cup 2026 - Daily Briefing
Today's matches, upcoming matches with odds, and group standings.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

WC_API = "https://worldcup26.ir"
ODDS_API = "https://api.the-odds-api.com/v4"
SGT = timezone(timedelta(hours=8))

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Load from .env if not in environment
if not ODDS_API_KEY:
    _env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("ODDS_API_KEY=") and not _line.startswith("#"):
                    ODDS_API_KEY = _line.split("=", 1)[1].strip()
                    break
SPORT_KEY = "soccer_fifa_world_cup"


def api_get(base, path, params=None):
    url = f"{base}{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"User-Agent": "WorldCupBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[ERROR] {path}: {e}", file=sys.stderr)
        return None


def parse_local_date_to_sgt(local_date_str):
    """Parse 'MM/DD/YYYY HH:MM' (US Eastern, UTC-4) and convert to SGT."""
    if not local_date_str:
        return None, "?"
    try:
        parts = local_date_str.strip().split(" ")
        if len(parts) == 2:
            date_part, time_part = parts
            month, day, year = date_part.split("/")
            hour, minute = time_part.split(":")
            eastern = timezone(timedelta(hours=-4))
            dt = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=eastern)
            dt_sgt = dt.astimezone(SGT)
            return dt_sgt, dt_sgt.strftime("%m/%d/%Y %H:%M")
        return None, local_date_str
    except Exception:
        return None, local_date_str


def time_to_sgt(start_time):
    """Convert ISO time to SGT string."""
    if not start_time:
        return "?"
    try:
        dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        return dt.astimezone(SGT).strftime("%m/%d %H:%M")
    except Exception:
        return start_time


def fetch_odds_for_match(match_id):
    """Fetch odds for a single match from The Odds API."""
    if not ODDS_API_KEY:
        return None
    data = api_get(ODDS_API, f"/sports/{SPORT_KEY}/odds", {
        "apiKey": ODDS_API_KEY,
        "sport": SPORT_KEY,
        "regions": "eu,uk",
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
    })
    if not data:
        return None
    # Find this match in the results
    for match in data:
        if match.get("id") == match_id or True:  # Match by home+away
            home = match.get("home_team", "")
            away = match.get("away_team", "")
            if home in match_id or away in match_id or match_id in str(match):
                return match
    return data[0] if data else None


def extract_best_1x2(bookmakers, home_team, away_team):
    """Find best 1X2 odds."""
    best = {"home": 0, "draw": 0, "away": 0}
    best_bm = {"home": "", "draw": "", "away": ""}

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
                if name == home_team and price > best["home"]:
                    best["home"] = price
                    best_bm["home"] = bm_name
                elif name == away_team and price > best["away"]:
                    best["away"] = price
                    best_bm["away"] = bm_name
                elif name.lower() == "draw" and price > best["draw"]:
                    best["draw"] = price
                    best_bm["draw"] = bm_name
    return best, best_bm


def extract_totals_25(bookmakers):
    """Find best Over/Under 2.5 odds."""
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
                if not price or point is None or float(point) != 2.5:
                    continue
                if name == "Over" and price > best_over:
                    best_over = price
                    best_over_bm = bm_name
                elif name == "Under" and price > best_under:
                    best_under = price
                    best_under_bm = bm_name
    return best_over, best_under, best_over_bm, best_under_bm


def fmt_team(name, width=10):
    """Pad/truncate team name for alignment."""
    return name[:width].ljust(width)

def implied_prob(decimal_odds):
    """Convert decimal odds to implied probability %."""
    if decimal_odds and decimal_odds > 1:
        return 100.0 / decimal_odds
    return 0.0

def format_odds_line(odds_data, home, away):
    """Format odds for a match — clean table layout."""
    if not odds_data:
        return ""
    bms = odds_data.get("bookmakers", [])
    if not bms:
        return ""

    best, best_bm = extract_best_1x2(bms, home, away)
    bo, bu, bo_bm, bu_bm = extract_totals_25(bms)

    lines = []
    if best["home"] and best["draw"] and best["away"]:
        hp = implied_prob(best["home"])
        dp = implied_prob(best["draw"])
        ap = implied_prob(best["away"])
        lines.append(
            f"  ┌ 1X2\n"
            f"  │ {fmt_team(home)} {best['home']:.2f}  ({hp:.0f}%)\n"
            f"  │ Draw       {best['draw']:.2f}  ({dp:.0f}%)\n"
            f"  └ {fmt_team(away)} {best['away']:.2f}  ({ap:.0f}%)"
        )
    if bo and bu:
        op = implied_prob(bo)
        up = implied_prob(bu)
        lines.append(
            f"  ┌ O/U 2.5\n"
            f"  │ Over  {bo:.2f}  ({op:.0f}%)\n"
            f"  └ Under {bu:.2f}  ({up:.0f}%)"
        )
    lines.append(f"  📈 {len(bms)} bookmakers")
    return "\n".join(lines)


def main():
    now = datetime.now(SGT)
    today_str = now.strftime("%m/%d/%Y")

    # Fetch World Cup games
    data = api_get(WC_API, "/get/games")
    if not data or "games" not in data:
        print("❌ Cannot fetch World Cup data")
        return

    # Fetch teams
    teams_data = api_get(WC_API, "/get/teams")
    team_cache = {}
    if teams_data:
        for t in teams_data.get("teams", []):
            tid = str(t.get("id", ""))
            if tid:
                team_cache[tid] = t.get("name_en", t.get("name", "?"))

    games = data["games"]

    # Fetch Odds API data (1 request gets all matches with odds)
    odds_data = None
    if ODDS_API_KEY:
        print("🔄 Fetching odds...", file=sys.stderr)
        odds_data = api_get(ODDS_API, f"/sports/{SPORT_KEY}/odds", {
            "apiKey": ODDS_API_KEY,
            "sport": SPORT_KEY,
            "regions": "eu,uk",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        })

    # Build odds lookup by home+away
    odds_lookup = {}
    if odds_data:
        for m in odds_data:
            home = m.get("home_team", "")
            away = m.get("away_team", "")
            key = f"{home}|{away}".lower()
            odds_lookup[key] = m

    # Today's matches
    today_games = []
    upcoming_games = []
    for g in games:
        sgt_dt, _ = parse_local_date_to_sgt(g.get("local_date", ""))
        if sgt_dt and sgt_dt.strftime("%m/%d/%Y") == today_str:
            today_games.append(g)
        elif sgt_dt and sgt_dt > now:
            upcoming_games.append(g)

    # Sort upcoming by time
    upcoming_games.sort(key=lambda g: g.get("local_date", ""))

    output = ""

    if today_games:
        output = f"⚽ World Cup 2026 — Today's Matches\n📅 {now.strftime('%A, %B %d, %Y')}\n\n"
        for g in today_games:
            home = g.get("home_team_name_en") or team_cache.get(g.get("home_team_id", "?"), "?")
            away = g.get("away_team_name_en") or team_cache.get(g.get("away_team_id", "?"), "?")
            status = g.get("time_elapsed", "notstarted")
            home_score = g.get("home_score", "0")
            away_score = g.get("away_score", "0")
            group = g.get("group", "?")
            sgt_dt, sgt_str = parse_local_date_to_sgt(g.get("local_date", ""))

            if status in ("finished", "Finished"):
                output += f"🏁 {home} {home_score}-{away_score} {away} (FT) | G{group}"
            elif status == "notstarted":
                output += f"⏳ {home} vs {away} — {sgt_str} SGT | G{group}"
            else:
                output += f"🔴 {home} {home_score}-{away_score} {away} ({status}) | G{group}"

            # Scorers — clean up raw JSON
            hs = g.get("home_scorers", "null")
            aus = g.get("away_scorers", "null")
            def clean_scorers(raw):
                if not raw or raw == "null":
                    return ""
                s = str(raw).strip("{}").replace('"', '').replace("'", '')
                return s
            hs_clean = clean_scorers(hs)
            aus_clean = clean_scorers(aus)
            if hs_clean:
                output += f"\n   🥅 {home}: {hs_clean}"
            if aus_clean:
                output += f"\n   🥅 {away}: {aus_clean}"
            output += "\n"

    # Upcoming matches with odds (show next 5)
    show_count = 5
    upcoming_to_show = upcoming_games[:show_count]
    if upcoming_to_show:
        if today_games:
            output += f"\n\n"
        else:
            output = f"⚽ World Cup 2026 — Next Matches\n📅 {now.strftime('%A, %B %d, %Y')}\n\n"

        for g in upcoming_to_show:
            home = g.get("home_team_name_en") or team_cache.get(g.get("home_team_id", "?"), "?")
            away = g.get("away_team_name_en") or team_cache.get(g.get("away_team_id", "?"), "?")
            sgt_dt, sgt_str = parse_local_date_to_sgt(g.get("local_date", ""))
            group = g.get("group", "?")

            output += f"⏳ {home} vs {away} — {sgt_str} SGT | G{group}\n"

            # Look up odds
            key = f"{home}|{away}".lower()
            odds_match = odds_lookup.get(key)
            if odds_match:
                output += format_odds_line(odds_match, home, away)
            output += "\n\n"

    if not today_games and not upcoming_to_show:
        output = f"⚽ World Cup 2026\n📅 {now.strftime('%A, %B %d, %Y')}\n\nNo matches today. 🏟️"

    # Group standings
    groups_data = api_get(WC_API, "/get/groups")
    if groups_data:
        output += "\n\n📊 Group Standings\n"
        groups = groups_data.get("groups", [])
        for g in sorted(groups, key=lambda x: x.get("name", "")):
            name = g.get("name", "?")
            teams = g.get("teams", [])
            output += f"\nGroup {name}\n"
            sorted_teams = sorted(teams, key=lambda t: (-int(t.get("pts", 0) or 0), -int(t.get("gd", 0) or 0)))
            # Table header
            output += f"  {'Team':<22} {'P':>2} {'W':>2} {'D':>2} {'L':>2} {'GF':>3} {'GA':>3} {'GD':>4} {'Pts':>3}\n"
            output += f"  {'─'*22} {'─'*2} {'─'*2} {'─'*2} {'─'*2} {'─'*3} {'─'*3} {'─'*4} {'─'*3}\n"
            for rank, t in enumerate(sorted_teams, 1):
                tid = str(t.get("team_id", ""))
                team_name = team_cache.get(tid, "?")
                played = t.get("mp", 0)
                won = t.get("w", 0)
                drawn = t.get("d", 0)
                lost = t.get("l", 0)
                gd = int(t.get("gd", 0) or 0)
                pts = int(t.get("pts", 0) or 0)
                gf = int(t.get("gf", 0) or 0)
                ga = int(t.get("ga", 0) or 0)
                output += f"  {team_name:<22} {played:>2} {won:>2} {drawn:>2} {lost:>2} {gf:>3} {ga:>3} {gd:>+4} {pts:>3}\n"

    print(output)


if __name__ == "__main__":
    main()
