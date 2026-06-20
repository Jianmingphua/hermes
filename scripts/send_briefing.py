#!/usr/bin/env python3
import urllib.request
import urllib.parse
import yaml

with open('/opt/hermes/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

bot_token = config.get('telegram', {}).get('bot_token', '')
chat_id = config.get('telegram', {}).get('chat_id', '')

message = (
    "Good morning! Here's your daily briefing - Saturday, 20 June 2026.\n\n"
    "CFO: Account: $96,606 | No open positions | Circuit breaker tripped (L31) | 2 anomaly alerts today\n\n"
    "COO: Thailand trip reminders active | Forex bot: running | Today is Saturday\n\n"
    "CTO: Disk: 71% used | RAM: 23Gi total | Data freshness: 2/3 sources recent\n\n"
    "Weatherman: Tampines: Partly Cloudy (Day), 30.4C - good day for outdoor activities\n\n"
    "Have a great day!"
)

url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
data = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode()
req = urllib.request.Request(url, data=data)
resp = urllib.request.urlopen(req)
print(resp.read().decode())
