#!/usr/bin/env python3
import json
import subprocess
import os

# Read bot token
token_path = os.path.expanduser('~/.telegram_bot_token')
with open(token_path) as f:
    token = f.read().strip()

msg = """📊 Forex Signal Update
⏰ 2026-06-03 20:49 UTC
💰 Balance: 100000.0 SGD

🔴 EUR_USD → SELL
   Confidence: 40%
   Spread: 0.9 pips
   • EMA 20 < 50 (bearish)
   • Price below 200 EMA
   • RSI bearish zone (34.1)
   • Strong trend (ADX 34.3)
   SL: 1.16146 | TP: 1.15716

🔴 GBP_USD → SELL
   Confidence: 40%
   Spread: 1.8 pips
   • EMA 20 < 50 (bearish)
   • Price below 200 EMA
   • RSI bearish zone (33.9)
   • Strong trend (ADX 32.8)
   SL: 1.344 | TP: 1.3381

🔒 Safety: Session ✅ | News ✅ | Spread ✅ | Circuit Breaker: 0/3 losses"""

payload = json.dumps({'chat_id': 137588943, 'text': msg})
result = subprocess.run(
    ['curl', '-s', '-X', 'POST',
     f'https://api.telegram.org/bot{token}/sendMessage',
     '-H', 'Content-Type: application/json', '-d', payload],
    capture_output=True, text=True
)
print(result.stdout)
print(result.stderr)
