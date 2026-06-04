#!/usr/bin/env python3
"""Append, edit, or delete a row in the Budget Tracker Google Sheet.

Design principle: every common correction is a single idempotent call.
No round-trips needed to fix a wrong amount.

Examples:
  # Append with a 15% discount (amount is pre-discount price)
  budget_tracker.py --category Lunch --description Lunch --amount 11.50 \\
      --discount 15 --payment Cash

  # Append, amount is what was actually paid
  budget_tracker.py --category Lunch --description Lunch --amount 9.78 \\
      --payment Cash --notes "15% discount applied"

  # Correct a previously appended row (edit a single cell)
  budget_tracker.py --edit-row 24 --field amount --value 11.50

  # Delete a row
  budget_tracker.py --delete-row 24

  # Quick append with defaults (today's date, Cash payment)
  budget_tracker.py --category Lunch --description Lunch --amount 11.50
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

_home = os.path.expanduser("~")
_token = os.path.join(_home, ".hermes", "google_token.json")
SHEET_ID = "1ET7l9dwYouJygbLApuzHBJ5ZaWc3XtrZ1oLVfLtrvv8"
SHEET_TAB_ID = 730938191  # Expenses tab sheetId (numeric grid id)
SG_TZ = timezone(timedelta(hours=8))

# Column index mapping: A=Amount(4), B=Payment(5), C=Receipt(6), D=Notes(7)
# Full row: Date(0), Person(1), Category(2), Description(3), Amount(4), Payment(5), Receipt(6), Notes(7)
FIELD_MAP = {
    "date": 0, "person": 1, "category": 2, "description": 3,
    "amount": 4, "payment": 5, "receipt": 6, "notes": 7,
}


def get_sheets_service():
    creds = Credentials.from_authorized_user_file(_token)
    return build("sheets", "v4", credentials=creds)


def today_sgt():
    return datetime.now(SG_TZ).strftime("%Y-%m-%d")


def detect_payment(description: str, notes: str) -> str:
    """Best-effort payment method detection."""
    combined = (description + " " + notes).lower()
    if "paynow" in combined or "paynow" in combined:
        return "PayNow"
    if "nets" in combined:
        return "NETS"
    if "visa" in combined or "mastercard" in combined or "amex" in combined or "credit" in combined:
        return "Credit Card"
    if "apple pay" in combined or "google pay" in combined:
        return "Mobile Pay"
    return "Cash"


def main():
    parser = argparse.ArgumentParser(
        description="Budget Tracker: append, edit, or delete expenses"
    )
    # --- Append fields ---
    parser.add_argument("--date", default=today_sgt(), help="Date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--person", default="You", help="Person name (default: You)")
    parser.add_argument("--category", help="Expense category (e.g. Lunch, Transport, Groceries)")
    parser.add_argument("--description", help="Description")
    parser.add_argument("--amount", type=float, help="Amount (number)")
    parser.add_argument("--payment", help="Payment method (auto-detected if omitted)")
    parser.add_argument("--receipt", default="No", help="Receipt (Yes/No/Digital, default: No)")
    parser.add_argument("--notes", default="", help="Notes")
    parser.add_argument("--discount", type=float, default=0,
                        help="Discount percentage (e.g. 15 for 15%%). "
                             "Amount is the pre-discount price; paid amount shown in notes.")
    parser.add_argument("--discount-amount", type=float, default=0,
                        help="Flat discount amount (alternative to --discount). "
                             "Amount is the pre-discount price.")

    # --- Row operations ---
    parser.add_argument("--delete-row", type=int, default=0,
                        help="Delete 1-indexed data row")
    parser.add_argument("--edit-row", type=int, default=0,
                        help="Edit a single cell in a 1-indexed data row")
    parser.add_argument("--field", choices=list(FIELD_MAP.keys()),
                        help="Field to edit (requires --edit-row)")
    parser.add_argument("--value", help="New value for --edit-row --field")

    args = parser.parse_args()
    service = get_sheets_service()

    # --- Validate ---
    if args.edit_row > 0:
        if not args.field or args.value is None:
            parser.error("--edit-row requires --field and --value")
    if args.delete_row > 0 and args.edit_row > 0:
        parser.error("--delete-row and --edit-row are mutually exclusive")

    # ========== DELETE ==========
    if args.delete_row > 0:
        # Convert 1-indexed data row to 0-indexed sheet row (+1 for header)
        sheet_row = args.delete_row
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

    # ========== EDIT ==========
    if args.edit_row > 0:
        col_idx = FIELD_MAP[args.field]
        # Sheet row: data row 1 = sheet row 1 (0-indexed, header is row 0)
        sheet_row = args.edit_row
        col_letter = chr(ord('A') + col_idx)
        range_str = f"Expenses!{col_letter}{sheet_row}"

        body = {"values": [[args.value]]}
        result = service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=range_str,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
        print(f"UPDATED:{range_str}={args.value}")
        return

    # ========== APPEND ==========
    if not all([args.category, args.description, args.amount]):
        parser.error("--category, --description, --amount are required for append")

    # Auto-detect payment if not provided
    payment = args.payment or detect_payment(args.description, args.notes)

    # Compute discount info
    original_amount = args.amount
    paid_amount = args.amount
    notes_suffix = ""

    if args.discount > 0:
        discount_pct = args.discount
        paid_amount = round(original_amount * (1 - discount_pct / 100), 2)
        notes_suffix = f" | {discount_pct}% discount applied, paid {paid_amount:.2f}"
        stored_amount = original_amount
    elif args.discount_amount > 0:
        paid_amount = round(original_amount - args.discount_amount, 2)
        notes_suffix = f" | ${args.discount_amount:.2f} discount applied, paid {paid_amount:.2f}"
        stored_amount = original_amount
    else:
        stored_amount = original_amount

    final_notes = (args.notes + notes_suffix).strip()

    row = [
        args.date,
        args.person,
        args.category,
        args.description,
        stored_amount,
        payment,
        args.receipt,
        final_notes,
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
    print(f"  Amount: ${stored_amount:.2f}", end="")
    if paid_amount != stored_amount:
        print(f" (paid: ${paid_amount:.2f} after discount)", end="")
    print(f"  Payment: {payment}  Notes: {final_notes}")


if __name__ == "__main__":
    main()
