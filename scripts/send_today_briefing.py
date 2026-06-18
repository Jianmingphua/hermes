#!/usr/bin/env python3
import subprocess
import json

token = open('/opt/hermes/.hermes/.telegram_bot_token').read().strip()

msg = (
    "\U0001f305 Good morning! Here's your daily briefing \u2014 Thursday, 18 June 2026.\n\n"
    "\U0001f4b0 CFO: Account: $96,688 | No open positions | \u26a0\ufe0f Circuit breaker tripped (L9) | 2 anomaly alerts today\n\n"
    "\U0001f4c5 COO: Thailand trip reminders active | Forex bot: running | Today is Thursday\n\n"
    "\U0001f5a5\ufe0f CTO: Disk: 71% used | RAM: 23Gi total | Data freshness: 2/3 sources recent\n\n"
    "\U0001f324\ufe0f Weatherman: Tampines: Partly Cloudy (Day), 31\u00b0C \u2014 good day for outdoor activities\n\n"
    "Have a great day! \U0001f680"
)

payload = json.dumps({'chat_id': 137588943, 'text': msg})
result = subprocess.run(
    ['curl', '-s', '-X', 'POST',
     f'https://api.telegram.org/bot{token}/sendMessage',
     '-H', 'Content-Type: application/json', '-d', payload],
    capture_output=True, text=True
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
