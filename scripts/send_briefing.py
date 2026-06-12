#!/usr/bin/env python3
"""Send the daily briefing to Telegram."""
import json
import os
import sys
import urllib.request
import urllib.error

_home = os.path.expanduser("~")
_token_path = os.path.join(_home, ".hermes", ".telegram_bot_token")
_env_path = os.path.join(_home, ".hermes", ".env")

TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    if os.path.exists(_token_path):
        with open(_token_path) as f:
            TOKEN=f.read().strip()
    elif os.path.exists(_env_path):
        with open(_env_path) as f:
            for line in f:
                if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                    TOKEN=line.strip().split("=", 1)[1].strip()
                    break

if not TOKEN:
    print("ERROR: No Telegram bot token found")
    sys.exit(1)

chat_id = os.environ.get("HERMES_CRON_AUTO_DELIVER_CHAT_ID", "").strip()
if not chat_id:
    print("ERROR: No chat ID found")
    sys.exit(1)

message = sys.stdin.read().strip()
if not message:
    print("ERROR: No message content")
    sys.exit(1)

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = json.dumps({"chat_id": chat_id, "text": message}).encode()

try:
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        if result.get("ok"):
            print("Message sent successfully!")
        else:
            print(f"Error: {result}")
            sys.exit(1)
except Exception as e:
    print(f"Failed: {e}")
    sys.exit(1)
