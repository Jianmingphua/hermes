#!/usr/bin/env python3
"""Append a row to the Budget Tracker Google Sheet."""
import argparse
import os

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

_home = os.path.expanduser("~")
_token = os.path.join(_home, ".hermes", "google_token.json")
SHEET_ID = "1ET7l9dwYouJygbLApuzHBJ5ZaWc3XtrZ1oLVfLtrvv8"


def main():
    parser = argparse.ArgumentParser(description="Append expense to Budget Tracker")
    parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    parser.add_argument("--person", required=True, help="Person name")
    parser.add_argument("--category", required=True, help="Expense category")
    parser.add_argument("--description", required=True, help="Description")
    parser.add_argument("--amount", required=True, help="Amount (number)")
    parser.add_argument("--payment", required=True, help="Payment method")
    parser.add_argument("--receipt", default="No", help="Receipt (Yes/No/Digital)")
    parser.add_argument("--notes", default="", help="Notes")
    args = parser.parse_args()

    creds = Credentials.from_authorized_user_file(_token)
    service = build("sheets", "v4", credentials=creds)

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
