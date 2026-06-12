#!/usr/bin/env python3
"""
Family Meal Planner — Creates and manages a Google Sheet for weekly meal planning.
Run once to set up the sheet, then use meal_plan_generator.py for weekly planning.

Usage:
  python3 setup_meal_planner.py          # Create new sheet, prints SHEET_ID
  python3 setup_meal_planner.py --check  # Check if sheet exists in state
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

SGT = timezone(timedelta(hours=8))
STATE_FILE = Path("/opt/hermes/scripts/.meal_planner_state.json")
SHEET_NAME = "Family Meal Planner"

# Aisle categories for shopping list ordering
AISLES = [
    "Produce (Fruits & Veg)",
    "Meat & Seafood",
    "Dairy & Eggs",
    "Dry Goods & Pasta",
    "Canned & Jarred",
    "Frozen",
    "Bakery",
    "Condiments & Sauces",
    "Snacks",
    "Beverages",
    "Other",
]

# Default recipe database — SG hawker + home-style meals
DEFAULT_RECIPES = [
    # Format: [Name, Type, Aisle, PrepTime, Servings, Ingredients(score/10 Hawker-style)]
    # Local / Hawker
    [ "Hainanese Chicken Rice", "Dinner", "Meat & Seafood", 45, 4, "Chicken, rice, ginger, garlic, pandan", 9],
    [ "Char Kway Teow", "Lunch", "Dry Goods & Pasta", 20, 2, "Flat rice noodles, prawns, bean sprouts, eggs, cockles, lap cheong", 9],
    [ "Laksa", "Lunch", "Canned & Jarred", 30, 3, "Rice noodles, coconut milk, prawns, bean sprouts, laksa leaves, tau pok", 8],
    [ "Nasi Lemak", "Breakfast", "Canned & Jarred", 30, 3, "Rice, coconut milk, eggs, ikan bilis, peanuts, sambal, cucumber", 8],
    [ "Roti Prata", "Breakfast", "Dry Goods & Pasta", 15, 2, "Flour, ghee, eggs, sugar, salt", 8],
    [ "Bak Kut Teh", "Dinner", "Meat & Seafood", 60, 4, "Pork ribs, garlic, pepper, dark soy sauce, herbs", 8],
    [ "Satay", "Dinner", "Meat & Seafood", 40, 4, "Chicken/beef, lemongrass, turmeric, peanut sauce, cucumber, ketupat", 8],
    [ "Popiah", "Lunch", "Produce (Fruits & Veg)", 40, 4, "Popiah skin, lettuce, turnip, carrot, prawns, egg, bean sprouts, chili", 7],
    [ "Fish Head Curry", "Dinner", "Produce (Fruits & Veg)", 50, 4, "Fish head, okra, eggplant, curry leaves, coconut milk, tamarind", 8],
    [ "Hokkien Mee", "Lunch", "Dry Goods & Pasta", 25, 2, "Thick yellow noodles, prawns, squid, pork belly, bean sprouts", 8],
    # Home-style
    [ "Steamed Fish with Ginger", "Dinner", "Meat & Seafood", 20, 3, "Fish (snapper/seabass), ginger, soy sauce, spring onion, sesame oil", 7],
    [ "Stir-fry Kangkong", "Dinner", "Produce (Fruits & Veg)", 10, 3, "Kangkong, garlic, chili, belacan, dried shrimp", 7],
    [ "Egg Fried Rice", "Breakfast", "Dry Goods & Pasta", 10, 2, "Rice, eggs, spring onion, soy sauce, sesame oil", 6],
    [ "Tomato Egg Stir-fry", "Lunch", "Produce (Fruits & Veg)", 10, 2, "Eggs, tomato, sugar, ketchup, spring onion", 6],
    [ "Braised Pork Belly", "Dinner", "Meat & Seafood", 60, 4, "Pork belly, dark soy sauce, sugar, star anise, garlic, eggs", 7],
    [ "Sweet & Sour Pork", "Dinner", "Meat & Seafood", 30, 3, "Pork (shoulder), pineapple, bell pepper, onion, sweet & sour sauce", 7],
    [ "Yong Tau Foo Soup", "Lunch", "Meat & Seafood", 15, 2, "Yong tau foo items, clear broth, noodles, vegetables", 7],
    [ "Mee Rebus", "Lunch", "Dry Goods & Pasta", 25, 2, "Yellow noodles, curry gravy, bean sprouts, egg, fried shallots, lime", 7],
    [ "Porridge with Side Dishes", "Breakfast", "Dry Goods & Pasta", 15, 3, "Rice, pork, egg, ikan bilis, ginger, soy sauce", 7],
    # Western / Quick
    [ "Pasta Aglio Olio", "Lunch", "Dry Goods & Pasta", 15, 2, "Spaghetti, garlic, chili flakes, parsley, olive oil", 6],
    [ "Pasta Bolognese", "Dinner", "Dry Goods & Pasta", 30, 4, "Spaghetti, minced beef, tomato sauce, onion, garlic, carrot", 6],
    [ "Grilled Cheese Sandwich", "Breakfast", "Bakery", 10, 1, "Bread, cheese, butter", 4],
    [ "Scrambled Eggs on Toast", "Breakfast", "Dairy & Eggs", 5, 1, "Eggs, bread, butter, salt, pepper", 5],
    [ "Chicken Quesadilla", "Lunch", "Meat & Seafood", 15, 2, "Tortillas, chicken, cheese, bell pepper, salsa", 5],
    [ "Salmon Rice Bowl", "Dinner", "Meat & Seafood", 15, 2, "Salmon, rice, avocado, soy sauce, sesame, nori", 7],
    [ "Caesar Salad", "Lunch", "Produce (Fruits & Veg)", 10, 2, "Romaine, chicken, croutons, parmesan, caesar dressing", 5],
    [ "Korean Bibimbap", "Lunch", "Produce (Fruits & Veg)", 25, 2, "Rice, beef, spinach, carrot, egg, gochujang, sesame oil", 7],
    [ "Japanese Curry Rice", "Dinner", "Dry Goods & Pasta", 30, 4, "Curry roux, potato, carrot, onion, chicken, rice", 7],
    [ "Poke Bowl", "Lunch", "Meat & Seafood", 10, 1, "Tuna/salmon, rice, edamame, avocado, cucumber, soy sauce", 7],
    [ "Ramen (Instant Upgrade)", "Lunch", "Dry Goods & Pasta", 10, 1, "Instant ramen, egg, spring onion, nori, corn, pork belly", 6],
]


def load_google_api():
    """Import and init the Google API service."""
    sys.path.insert(0, '/opt/hermes/.hermes/skills/productivity/google-workspace/scripts')
    import google_api
    return google_api.build_service('sheets', 'v4')


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def create_meal_planner_sheet(service) -> str:
    """Create the multi-tab meal planner Google Sheet. Returns spreadsheet ID."""
    spreadsheet = service.spreadsheets().create(body={
        "properties": {"title": SHEET_NAME},
        "sheets": [
            {"properties": {"title": "This Week", "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1}}},
            {"properties": {"title": "Recipes", "gridProperties": {"frozenRowCount": 1}}},
            {"properties": {"title": "Shopping List", "gridProperties": {"frozenRowCount": 1}}},
            {"properties": {"title": "History", "gridProperties": {"frozenRowCount": 1}}},
        ]
    }).execute()

    sheet_id = spreadsheet["spreadsheetId"]

    # Fetch actual sheet IDs
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    now_sgt = datetime.now(SGT)
    week_start = now_sgt - timedelta(days=now_sgt.weekday())
    week_dates = [(week_start + timedelta(days=i)).strftime("%a %d %b") for i in range(7)]

    # ── This Week tab ──
    week_headers = ["Day", "Breakfast", "Lunch", "Dinner", "Prep Notes"]
    week_rows = [week_headers]
    for day_name in week_dates:
        week_rows.append([day_name, "", "", "", ""])

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="This Week!A1:E9",
        valueInputOption="USER_ENTERED",
        body={"values": week_rows},
    ).execute()

    # ── Recipes tab ──
    recipe_headers = ["Name", "Type", "Main Aisle", "Prep (min)", "Servings", "Key Ingredients", "Score (/10)"]
    recipe_rows = [recipe_headers] + DEFAULT_RECIPES

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"Recipes!A1:G{len(recipe_rows)}",
        valueInputOption="USER_ENTERED",
        body={"values": recipe_rows},
    ).execute()

    # ── Shopping List tab (empty, populated by generator) ──
    sl_headers = ["Aisle", "Ingredient", "Quantity", "For Meal", "Checked?"]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Shopping List!A1:E1",
        valueInputOption="USER_ENTERED",
        body={"values": [sl_headers]},
    ).execute()

    # ── History tab (empty, populated over time) ──
    hist_headers = ["Week Of", "Day", "Meal", "Recipe", "Rating (1-5)", "Notes"]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="History!A1:F1",
        valueInputOption="USER_ENTERED",
        body={"values": [hist_headers]},
    ).execute()

    # ── Formatting ──
    requests = []
    # Freeze headers on all sheets
    for sid in sheets.values():
        requests.append({
            "updateSheetProperties": {
                "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"
            }
        })

    # Bold headers
    for name, sid in sheets.items():
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 11},
                        "backgroundColor": {"red": 0.15, "green": 0.35, "blue": 0.55},
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)"
            }
        })

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": requests}
    ).execute()

    return sheet_id


def main():
    parser = argparse.ArgumentParser(description="Family Meal Planner Setup")
    parser.add_argument("--check", action="store_true", help="Check existing sheet state")
    args = parser.parse_args()

    state = load_state()

    if args.check:
        if "sheet_id" in state:
            print(f"Meal Planner sheet exists: {state['sheet_id']}")
            print(f"URL: https://docs.google.com/spreadsheets/d/{state['sheet_id']}/edit")
        else:
            print("No meal planner sheet found. Run without --check to create one.")
        return

    service = load_google_api()
    print("Creating Family Meel Planner sheet...")
    sheet_id = create_meal_planner_sheet(service)

    state["sheet_id"] = sheet_id
    state["created_at"] = datetime.now(SGT).isoformat()
    save_state(state)

    print(f"✅ Sheet created: {sheet_id}")
    print(f"   URL: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
    print(f"")
    print(f"Add this to your scripts state or copy the SHEET_ID for the generator.")


if __name__ == "__main__":
    main()
