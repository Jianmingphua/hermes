#!/usr/bin/env python3
"""
Family Meal Plan Generator — Weekly planning with weather awareness.
Generates a weekly meal plan, writes to Google Sheet, outputs shopping list.

Usage:
  python3 meal_plan_generator.py              # Generate for next week
  python3 meal_plan_generator.py --dry-run    # Print plan without writing to sheet
"""
import argparse
import json
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

SGT = timezone(timedelta(hours=8))
STATE_FILE = Path("/opt/hermes/scripts/.meal_planner_state.json")

# Weather-adjusted meal preferences
HOT_WEATHER_MEALS = ["Poke Bowl", "Caesar Salad", "Salmon Rice Bowl", "Ramen (Instant Upgrade)"]
RAINY_WEATHER_MEALS = ["Porridge with Side Dishes", "Japanese Curry Rice", "Braised Pork Belly",
                       "Bak Kut Teh", "Yong Tau Foo Soup", "Mee Rebus"]
COMFORT_MEALS = ["Laksa", "Hainanese Chicken Rice", "Fish Head Curry"]


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_sg_weather() -> dict:
    """Fetch current SG weather for meal planning adjustments."""
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
            return {
                "forecast": tampines.get("forecast", "Unknown"),
                "hot": "Shower" not in tampines.get("forecast", "") and "Rain" not in tampines.get("forecast", ""),
                "rainy": "Rain" in tampines.get("forecast", "") or "Shower" in tampines.get("forecast", ""),
            }
    except Exception:
        return {"forecast": "Unknown", "hot": False, "rainy": False}


def load_google_api_svc():
    sys.path.insert(0, '/opt/hermes/.hermes/skills/productivity/google-workspace/scripts')
    import google_api
    return google_api.build_service('sheets', 'v4')


def load_recipes_from_sheet(service, sheet_id: str) -> list:
    """Load recipe database from the Recipes tab."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="Recipes!A2:G100"
        ).execute()
        rows = result.get("values", [])
        recipes = []
        for r in rows:
            if len(r) >= 4:
                recipes.append({
                    "name": r[0],
                    "type": r[1] if len(r) > 1 else "",
                    "aisle": r[2] if len(r) > 2 else "",
                    "prep": int(r[3]) if len(r) > 3 and r[3].isdigit() else 20,
                    "servings": int(r[4]) if len(r) > 4 and r[4].isdigit() else 2,
                    "ingredients": r[5] if len(r) > 5 else "",
                    "score": int(r[6]) if len(r) > 6 and r[6].isdigit() else 5,
                })
        return recipes
    except Exception:
        return []


def generate_weekly_plan(recipes: list, weather: dict, history: list = None) -> dict:
    """Generate a 7-day meal plan avoiding recent repeats."""
    history = history or {}
    used_names = set(history.get("recent_meals", []))

    # Categorize recipes
    breakfasts = [r for r in recipes if r.get("type") == "Breakfast"]
    lunches = [r for r in recipes if r.get("type") == "Lunch"]
    dinners = [r for r in recipes if r.get("type") == "Dinner"]

    if not breakfasts:
        breakfasts = [r for r in recipes if "Breakfast" in r.get("name", "") or "Egg" in r.get("name", "")]
    if not lunches:
        lunches = [r for r in recipes]
    if not dinners:
        dinners = [r for r in recipes]

    # Weather boost: prefer certain meals
    def pick_meal(pool: list, slot: str, day_used: set) -> Optional[dict]:
        if not pool:
            return None
        # Filter out recently used
        available = [r for r in pool if r["name"] not in day_used]
        if not available:
            available = pool

        # Score boost based on weather
        def score(r):
            s = r.get("score", 5)
            name = r["name"]
            if weather.get("rainy") and name in RAINY_WEATHER_MEALS:
                s += 3
            if weather.get("hot") and name in HOT_WEATHER_MEALS:
                s += 3
            if name in COMFORT_MEALS:
                s += 1  # slight general boost for comfort food
            # Penalize recent repeats heavily
            if name in used_names:
                s -= 5
            return s

        available.sort(key=score, reverse=True)
        # Pick from top 3 with some randomness
        top = available[:min(3, len(available))]
        return random.choice(top)

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    plan = {}
    day_used = set()

    for day in days:
        b = pick_meal(breakfasts, "breakfast", day_used)
        l = pick_meal(lunches, "lunch", day_used)
        d = pick_meal(dinners, "dinner", day_used)

        if b:
            day_used.add(b["name"])
        if l:
            day_used.add(l["name"])
        if d:
            day_used.add(d["name"])

        meal_notes = []
        if b and b.get("prep", 0) <= 10:
            meal_notes.append(f"{b['name']} is quick ({b['prep']}min)")
        if d and d.get("prep", 0) > 40:
            meal_notes.append(f"Start {d['name']} early ({d['prep']}min prep)")

        plan[day] = {
            "breakfast": b["name"] if b else "Leftovers / Cereal",
            "lunch": l["name"] if l else "Eat out",
            "dinner": d["name"] if d else "Takeaway",
            "notes": " | ".join(meal_notes),
            "recipes": [r for r in [b, l, d] if r],
        }

    return plan


def build_shopping_list(plan: dict) -> list:
    """Build a consolidated shopping list from the weekly plan."""
    # Aggregate ingredients by aisle
    aisle_items: dict[str, dict] = {}

    for day, meals in plan.items():
        for recipe in meals.get("recipes", []):
            aisle = recipe.get("aisle", "Other")
            ingredients = recipe.get("ingredients", "")
            if isinstance(ingredients, str):
                items = [i.strip() for i in ingredients.split(",") if i.strip()]
            else:
                items = ingredients

            for item in items:
                key = f"{aisle}|{item}"
                if key not in aisle_items:
                    aisle_items[key] = {"aisle": aisle, "item": item, "count": 0, "meals": set()}
                aisle_items[key]["count"] += 1
                aisle_items[key]["meals"].add(recipe.get("name", ""))

    # Sort by aisle order, then by item name
    sorted_items = sorted(aisle_items.values(), key=lambda x: x["aisle"])

    rows = []
    current_aisle = ""
    for item in sorted_items:
        if item["aisle"] != current_aisle:
            current_aisle = item["aisle"]
        meal_list = ", ".join(list(item["meals"])[:2])
        qty = f"{item['count']}x" if item["count"] > 1 else "1"
        rows.append([current_aisle, item["item"], qty, meal_list, ""])

    return rows


def write_plan_to_sheet(service, sheet_id: str, plan: dict, shopping_list: list,
                        week_dates: list):
    """Write the meal plan and shopping list to Google Sheet."""
    # This Week tab
    rows = [["Day", "Breakfast", "Lunch", "Dinner", "Prep Notes"]]
    for i, date_str in enumerate(week_dates):
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i]
        meals = plan.get(day_name, {})
        rows.append([
            f"{day_name} ({date_str})",
            meals.get("breakfast", ""),
            meals.get("lunch", ""),
            meals.get("dinner", ""),
            meals.get("notes", ""),
        ])

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="This Week!A1:E9",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()

    # Shopping List tab (clear existing, write new)
    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id,
        range="Shopping List!A2:E",
    ).execute()

    if shopping_list:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"Shopping List!A2:E{len(shopping_list) + 1}",
            valueInputOption="USER_ENTERED",
            body={"values": shopping_list},
        ).execute()


def format_telegram_message(plan: dict, weather: dict, week_dates: list) -> str:
    """Format the meal plan as a Telegram-friendly message."""
    now = datetime.now(SGT)
    week_start = week_dates[0] if week_dates else "this week"

    lines = [
        f"🍽️ Weekly Meal Plan — w/c {week_start}",
        f"",
    ]

    if weather.get("rainy"):
        lines.append("🌧️ Rainy weather expected — comfort meals included!")
    elif weather.get("hot"):
        lines.append("☀️ Hot weather — lighter meals and cold dishes planned!")
    lines.append("")

    for i, date_str in enumerate(week_dates):
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i]
        meals = plan.get(day_name, {})
        lines.append(f"📅 {day_name} ({date_str})")
        lines.append(f"  🌅 {meals.get('breakfast', '—')}")
        lines.append(f"  ☀️ {meals.get('lunch', '—')}")
        lines.append(f"  🌙 {meals.get('dinner', '—')}")
        if meals.get("notes"):
            lines.append(f"  💡 {meals['notes']}")
        lines.append("")

    lines.append("🛒 Shopping list has been updated in the Google Sheet!")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Family Meal Plan Generator")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    args = parser.parse_args()

    state = load_state()

    # Load recipes
    recipes = []
    if not args.dry_run and "sheet_id" in state:
        svc = load_google_api_svc()
        recipes = load_recipes_from_sheet(svc, state["sheet_id"])

    # Fallback to defaults if no sheet recipes
    if not recipes:
        # Use the default recipes from setup script format
        for r in DEFAULT_RECIPES:
            recipes.append({
                "name": r[0], "type": r[1], "aisle": r[2],
                "prep": r[3], "servings": r[4],
                "ingredients": r[5], "score": r[6] if len(r) > 6 else 5,
            })

    # Fetch weather
    weather = fetch_sg_weather() if not args.dry_run else {"forecast": "Unknown", "hot": False, "rainy": False}

    # Generate plan
    plan = generate_weekly_plan(recipes, weather, state.get("history", {}))

    # Build shopping list
    shopping_list = build_shopping_list(plan)

    # Week dates
    now = datetime.now(SGT)
    week_start = now - timedelta(days=now.weekday())
    week_dates = [(week_start + timedelta(days=i)).strftime("%d %b") for i in range(7)]

    if args.dry_run or args.output == "json":
        print(json.dumps({"plan": plan, "shopping_list": shopping_list[:20]}, indent=2, default=str))
        return

    # Write to sheet
    if "sheet_id" in state and not args.dry_run:
        svc = load_google_api_svc()
        write_plan_to_sheet(svc, state["sheet_id"], plan, shopping_list, week_dates)
        print(f"✅ Sheet updated: https://docs.google.com/spreadsheets/d/{state['sheet_id']}/edit")
        print("")

    # Update history
    plan_recipes = set()
    for day_meals in plan.values():
        for r in day_meals.get("recipes", []):
            plan_recipes.add(r.get("name", ""))

    state.setdefault("history", {})["recent_meals"] = list(plan_recipes)[-14:]
    state.setdefault("history", {})["last_generated"] = now.isoformat()
    save_state(state)

    # Output for Telegram
    msg = format_telegram_message(plan, weather, week_dates)
    print(msg)


if __name__ == "__main__":
    main()
