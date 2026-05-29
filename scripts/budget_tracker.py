#!/usr/bin/env python3
"""Append a row to the Budget Tracker Google Sheet."""
import argparse
import os

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

_home = os.path.expanduser("~")
_token = os.path.join(_home, ".hermes", "google_token.json")
SHEET_ID = "1ET7l9dwYouJygbLApuzHBJ5ZaWc3XtrZ1oLVfLtrvv8"
SHEET_TAB_ID = 730938191  # Expenses tab sheetId (numeric grid id)


def main():
    parser = argparse.ArgumentParser(description="Append expense to Budget Tracker")
    parser.add_argument("--date", help="Date (YYYY-MM-DD)")
    parser.add_argument("--person", help="Person name")
    parser.add_argument("--category", help="Expense category")
    parser.add_argument("--description", help="Description")
    parser.add_argument("--amount", help="Amount (number)")
    parser.add_argument("--payment", help="Payment method")
    parser.add_argument("--receipt", default="No", help="Receipt (Yes/No/Digital)")
    parser.add_argument("--notes", default="", help="Notes")
    parser.add_argument("--delete-row", type=int, default=0, help="Delete 1-indexed data row (e.g. 5 = row 5 in Sheet, excluding header)")
    args = parser.parse_args()

    creds = Credentials.from_authorized_user_file(_token)
    service = build("sheets", "v4", credentials=creds)

    if args.delete_row < 0:
        parser.error("--delete-row must be >= 0")
    if args.delete_row > 0:
        # Convert 1-indexed data row to 0-indexed sheet row (+1 for header)
        sheet_row = args.delete_row  # data row 1 = sheet row 1 (0-indexed, header is row 0)
        request = {
            "deleteDimension": {
                "range": {
                    "sheetId": SHEET_TAB_ID,
                    "dimension": "ROWS",
                    "startIndex": sheet_row,
                    "endIndex": sheet_row + 1,
                }
            }
        }
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [request]},
        ).execute()
        print(f"DELETED:Expenses!row{args.delete_row}")
        return

    if not all([args.date, args.person, args.category, args.description, args.amount, args.payment]):
        parser.error("--date, --person, --category, --description, --amount, --payment are required for append")
    row = [
        args.date,
        args.person,
        args.category,
        args.description,
        float(args.amount),
        args.payment,
        args.receipt,
        args.notes,
    ]

    result = service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Expenses!A:H",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    updated_range = result.get("updates", {}).get("updatedRange", "unknown")
    print(f"APPENDED:{updated_range}")


if __name__ == "__main__":
    main()
