#!/usr/bin/env python3
"""
Log an expense to the Thailand Trip expenses sheet.
Usage: python3 log_expense.py "Category" "Item" "Amount" "Currency" "Paid By" ["Notes"]

Examples:
  python3 log_expense.py "Food" "Lunch at Baan Mon Muan" "450" "THB" "Jian Ming" "Scenic valley restaurant"
  python3 log_expense.py "Shopping" "Warorot Market snacks" "200" "THB" "Sheryl" "Dried fruits"
  python3 log_expense.py "Transport" "Taxi to temple" "150" "THB" "Jian Ming" ""
"""
import json, sys, os, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/opt/hermes/.hermes/skills/productivity/google-workspace/scripts')
import google_api
from googleapiclient.errors import HttpError

SGT = timezone(timedelta(hours=8))
SID = '1iis0wf9BQ-6pvDjmfvtzAjtvwTyCNlkPXBPCQXkbzQM'

def get_svc():
    return google_api.build_service('sheets', 'v4')

def find_category_row(svc, category):
    """Find the row index for a category in the expenses sheet."""
    result = svc.spreadsheets().values().get(
        spreadsheetId=SID, range='expenses!A1:A30'
    ).execute()
    rows = result.get('values', [])
    
    for i, row in enumerate(rows):
        if row and row[0].lower() == category.lower():
            return i + 1  # 1-indexed
    
    return None

def find_last_row_in_category(svc, category):
    """Find the last row in a category section."""
    result = svc.spreadsheets().values().get(
        spreadsheetId=SID, range='expenses!A1:B30'
    ).execute()
    rows = result.get('values', [])
    
    in_category = False
    last_row = None
    
    categories = ['Flights', 'Insurance', 'Connectivity', 'Transport', 'Accommodation', 'Activities', 'Food', 'Shopping']
    
    for i, row in enumerate(rows):
        if not row:
            continue
        cell = row[0] if row else ''
        
        if cell == category:
            in_category = True
            last_row = i + 1
        elif in_category and cell in categories:
            break
        elif in_category and cell and cell != category:
            last_row = i + 1
    
    return last_row

def log_expense(category, item, amount, currency, paid_by, notes=''):
    """Log an expense to the sheet."""
    svc = get_svc()
    today = datetime.now(SGT).strftime('%Y-%m-%d')
    
    # Determine which column to put the amount in
    amount_col_thb = 'D'
    amount_col_sgd = 'E'
    
    # Find where to insert the new row
    insert_after = find_last_row_in_category(svc, category)
    
    if not insert_after:
        # Category not found, append at the end (before TOTALS)
        result = svc.spreadsheets().values().get(
            spreadsheetId=SID, range='expenses!A1:A30'
        ).execute()
        rows = result.get('values', [])
        insert_after = len(rows) - 3  # Before TOTALS section
    
    # Prepare the new row
    new_row = [
        category,
        item,
        today,
        amount if currency == 'THB' else '',
        amount if currency == 'SGD' else '',
        currency,
        paid_by,
        'Credit Card',  # Default payment method
        notes,
        ''  # Receipt
    ]
    
    # Insert the row using INSERT_ROW + updateCells
    # First, insert a blank row
    sheet_id = 0  # expenses sheet ID
    
    # Find the actual sheet ID
    meta = svc.spreadsheets().get(spreadsheetId=SID).execute()
    for s in meta['sheets']:
        if s['properties']['title'] == 'expenses':
            sheet_id = s['properties']['sheetId']
            break
    
    # Insert row after the category section
    insert_idx = insert_after  # 0-indexed for API
    
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SID,
        body={
            'requests': [{
                'insertDimension': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': insert_idx,
                        'endIndex': insert_idx + 1
                    },
                    'inheritFromBefore': True
                }
            }]
        }
    ).execute()
    
    time.sleep(0.3)
    
    # Write the new row
    svc.spreadsheets().values().update(
        spreadsheetId=SID,
        range=f'expenses!A{insert_idx + 1}:J{insert_idx + 1}',
        valueInputOption='USER_ENTERED',
        body={'values': [new_row]}
    ).execute()
    
    return True

if __name__ == '__main__':
    if len(sys.argv) < 6:
        print("Usage: log_expense.py <category> <item> <amount> <currency> <paid_by> [notes]")
        print("Categories: Flights, Insurance, Connectivity, Transport, Accommodation, Activities, Food, Shopping")
        print("Currencies: THB, SGD")
        print("Paid by: Jian Ming, Sheryl")
        sys.exit(1)
    
    category = sys.argv[1]
    item = sys.argv[2]
    amount = sys.argv[3]
    currency = sys.argv[4].upper()
    paid_by = sys.argv[5]
    notes = sys.argv[6] if len(sys.argv) > 6 else ''
    
    try:
        log_expense(category, item, amount, currency, paid_by, notes)
        print(f"✅ Logged: {item} — {amount} {currency} ({paid_by})")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
