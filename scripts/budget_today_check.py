#!/usr/bin/env python3
"""Check if any expenses were logged today in the Budget Tracker sheet."""
import os
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

_home = os.path.expanduser("~")
_token = os.path.join(_home, ".hermes", "google_token.json")
SHEET_ID = "1ET7l9dwYouJygbLApuzHBJ5ZaWc3XtrZ1oLVfLtrvv8"

SGT = timezone(timedelta(hours=8))
today_sgt = datetime.now(SGT).strftime("%Y-%m-%d")

creds = Credentials.from_authorized_user_file(_token)
service = build("sheets", "v4", credentials=creds)

result = service.spreadsheets().values().get(
    spreadsheetId=SHEET_ID,
    range="Expenses!A:A",
).execute()

values = result.get("values", [])
data_rows = values[1:] if len(values) > 1 else []

count = sum(1 for r in data_rows if r and r[0].strip() == today_sgt)

if count > 0:
    print(f"FOUND:{count}")
else:
    print("NONE")
