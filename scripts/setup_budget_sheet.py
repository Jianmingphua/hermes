#!/usr/bin/env python3
"""Create and format the Budget Tracker Google Sheet."""
import json
import sys
import os

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

TOKEN_PATH = os.path.expanduser("~/.hermes/google_token.json")
FOLDER_ID = "1VyC1VaT2JXFWygUfu4WxjS-jfX-5UR3D"

creds = Credentials.from_authorized_user_file(TOKEN_PATH)
service = build("sheets", "v4", credentials=creds)

# Create the spreadsheet inside the Budget Tracker folder
spreadsheet = service.spreadsheets().create(body={
    "properties": {"title": "Budget Tracker"},
    "sheets": [{"properties": {"title": "Expenses"}}]
}).execute()

sheet_id = spreadsheet["spreadsheetId"]
sheet_url = spreadsheet["spreadsheetUrl"]
print(f"Created spreadsheet: {sheet_url}")

# Move to the Budget Tracker folder
drive_service = build("drive", "v3", credentials=creds)
drive_service.files().update(
    fileId=sheet_id,
    addParents=FOLDER_ID,
    fields="id, parents"
).execute()
print("Moved to Budget Tracker folder.")

# Sheet ID for the "Expenses" tab (always 0 for first sheet)
s_id = 0

# --- Headers ---
headers = [["Date", "Person", "Category", "Description", "Amount (SGD)", "Payment Method", "Receipt", "Notes"]]

service.spreadsheets().values().update(
    spreadsheetId=sheet_id,
    range="Expenses!A1:H1",
    valueInputOption="RAW",
    body={"values": headers},
).execute()

# --- Data validation ranges and rules ---
validation_ranges = [
    {"sheetId": s_id, "startRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 2},   # Person
    {"sheetId": s_id, "startRowIndex": 1, "startColumnIndex": 2, "endColumnIndex": 3},   # Category
    {"sheetId": s_id, "startRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 6},   # Payment Method
    {"sheetId": s_id, "startRowIndex": 1, "startColumnIndex": 6, "endColumnIndex": 7},   # Receipt
    {"sheetId": s_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},   # Date
]

person_values = ["You", "Wife"]
category_values = [
    "Food & Dining", "Groceries", "Transport", "Fitness", "Health",
    "Housing & Utilities", "Bills & Subscriptions", "Shopping",
    "Entertainment", "Education", "Kids", "Pets", "Travel",
    "Gifts & Donations", "Maintenance", "Savings & Investments",
    "Income", "Other",
]
payment_values = [
    "Credit Card", "Bank Transfer", "Cash", "PayNow / PayLah",
    "Apple / Google Pay", "Nets", "GIRO",
]
receipt_values = ["Yes", "No", "Digital"]

def one_of_list(values, strict=False):
    return {
        "condition": {
            "type": "ONE_OF_LIST",
            "values": [{"userEnteredValue": v} for v in values],
        },
        "strict": strict,
        "showCustomUi": True,
    }

validation_rules = [
    one_of_list(person_values),       # Person
    one_of_list(category_values),     # Category
    one_of_list(payment_values),      # Payment Method
    one_of_list(receipt_values),      # Receipt
    {"condition": {"type": "DATE_IS_VALID"}, "strict": True, "showCustomUi": True},  # Date
]

requests = [
    # Bold white header on blue background
    {
        "repeatCell": {
            "range": {"sheetId": s_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {
                        "bold": True,
                        "fontSize": 11,
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                    },
                    "backgroundColor": {"red": 0.2, "green": 0.4, "blue": 0.8},
                    "horizontalAlignment": "CENTER",
                }
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)",
        }
    },
    # Freeze header row
    {
        "updateSheetProperties": {
            "properties": {"sheetId": s_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    },
    # Alternating row colors
    {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{"sheetId": s_id, "startRowIndex": 1}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=ISEVEN(ROW())"}]},
                    "format": {"backgroundColor": {"red": 0.95, "green": 0.97, "blue": 1.0}},
                },
            },
            "index": 0,
        }
    },
    # Amount column: number format, right-aligned
    {
        "repeatCell": {
            "range": {"sheetId": s_id, "startRowIndex": 1, "startColumnIndex": 4, "endColumnIndex": 5},
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"},
                    "horizontalAlignment": "RIGHT",
                }
            },
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)",
        }
    },
]

# Column widths (A=Date, B=Person, C=Category, D=Desc, E=Amt, F=Payment, G=Receipt, H=Notes)
col_widths = [110, 100, 180, 250, 120, 160, 100, 200]
for i, w in enumerate(col_widths):
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": s_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w},
            "fields": "pixelSize",
        }
    })

# Data validation
for vr, rule in zip(validation_ranges, validation_rules):
    requests.append({"setDataValidation": {"range": vr, "rule": rule}})

service.spreadsheets().batchUpdate(
    spreadsheetId=sheet_id,
    body={"requests": requests},
).execute()

# --- Add a Summary tab ---
summary_sheet_id = 1
requests2 = [
    # Add new sheet tab
    {
        "addSheet": {
            "properties": {
                "title": "Summary",
                "sheetId": summary_sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            }
        }
    },
]

service.spreadsheets().batchUpdate(
    spreadsheetId=sheet_id,
    body={"requests": requests2},
).execute()

# Populate Summary tab
summary_data = [
    ["BUDGET TRACKER SUMMARY", "", "", ""],
    ["", "", "", ""],
    ["Filter by Person:", "All", "", ""],
    ["", "", "", ""],
    ["Total Expenses (excl. Income)", "=SUMIF(Expenses!B:B,\"<>\"&$B$2,Expenses!E:E)-SUMIFS(Expenses!E:E,Expenses!B:B,\"<>\",$B$2,Expenses!C:C,\"Income\")", "", ""],
    ["Total Income", "=SUMIFS(Expenses!E:E,Expenses!C:C,\"Income\")", "", ""],
    ["Net (Income - Expenses)", "=B5+B6", "", ""],
    ["", "", "", ""],
    ["By Category", "Total", "# Entries", "Avg/Entry"],
    ["Food & Dining",       '=SUMIF(Expenses!C:C,A10,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A10)', "=IF(C10>0,B10/C10,0)"],
    ["Groceries",           '=SUMIF(Expenses!C:C,A11,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A11)', "=IF(C11>0,B11/C11,0)"],
    ["Transport",           '=SUMIF(Expenses!C:C,A12,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A12)', "=IF(C12>0,B12/C12,0)"],
    ["Housing & Utilities", '=SUMIF(Expenses!C:C,A13,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A13)', "=IF(C13>0,B13/C13,0)"],
    ["Bills & Subscriptions",'=SUMIF(Expenses!C:C,A14,Expenses!E:E)','=COUNTIF(Expenses!C:C,A14)', "=IF(C14>0,B14/C14,0)"],
    ["Shopping",            '=SUMIF(Expenses!C:C,A15,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A15)', "=IF(C15>0,B15/C15,0)"],
    ["Entertainment",       '=SUMIF(Expenses!C:C,A16,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A16)', "=IF(C16>0,B16/C16,0)"],
    ["Health",              '=SUMIF(Expenses!C:C,A17,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A17)', "=IF(C17>0,B17/C17,0)"],
    ["Education",           '=SUMIF(Expenses!C:C,A18,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A18)', "=IF(C18>0,B18/C18,0)"],
    ["Travel",              '=SUMIF(Expenses!C:C,A19,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A19)', "=IF(C19>0,B19/C19,0)"],
    ["Other",               '=SUMIF(Expenses!C:C,A20,Expenses!E:E)', '=COUNTIF(Expenses!C:C,A20)', "=IF(C20>0,B20/C20,0)"],
    ["", "", "", ""],
    ["By Person", "Total", "# Entries", "Avg/Entry"],
    ["You",   '=SUMIF(Expenses!B:B,"You",Expenses!E:E)',   '=COUNTIF(Expenses!B:B,"You")',   "=IF(C23>0,B23/C23,0)"],
    ["Wife",  '=SUMIF(Expenses!B:B,"Wife",Expenses!E:E)',  '=COUNTIF(Expenses!B:B,"Wife")',  "=IF(C24>0,B24/C24,0)"],
]

service.spreadsheets().values().update(
    spreadsheetId=sheet_id,
    range="Summary!A1:D24",
    valueInputOption="USER_ENTERED",
    body={"values": summary_data},
).execute()

# Format Summary header
requests3 = [
    {
        "repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    "backgroundColor": {"red": 0.2, "green": 0.4, "blue": 0.8},
                    "horizontalAlignment": "CENTER",
                }
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)",
        }
    },
    {
        "repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {
                "userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}},
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    },
    {
        "repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 8, "endRowIndex": 9, "startColumnIndex": 0, "endColumnIndex": 4},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    "backgroundColor": {"red": 0.18, "green": 0.35, "blue": 0.65},
                }
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    },
    {
        "repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 19, "endRowIndex": 20, "startColumnIndex": 0, "endColumnIndex": 4},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                    "backgroundColor": {"red": 0.18, "green": 0.35, "blue": 0.65},
                }
            },
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }
    },
    # Format columns B:D with 2 decimal places
    {
        "repeatCell": {
            "range": {"sheetId": summary_sheet_id, "startRowIndex": 4, "endRowIndex": 24, "startColumnIndex": 1, "endColumnIndex": 4},
            "cell": {
                "userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}},
            },
            "fields": "userEnteredFormat(numberFormat)",
        }
    },
    # Auto-resize columns
    {"autoResizeDimensions": {
        "dimensions": {"sheetId": summary_sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 4}
    }},
]

service.spreadsheets().batchUpdate(
    spreadsheetId=sheet_id,
    body={"requests": requests3},
).execute()

print(f"\nSetup complete!")
print(f"Spreadsheet URL: {sheet_url}")
print(f"Spreadsheet ID: {sheet_id}")
