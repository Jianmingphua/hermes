#!/usr/bin/env python3
"""Send the daily briefing to Telegram."""
import yaml
import urllib.request
import urllib.parse
import sys

with open('/opt/hermes/config/telegram.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

bot_token = cfg['bot_token']
chat_id = cfg['chat_id']

if len(sys.argv) > 1:
    message = sys.argv[1]
else:
    message = sys.stdin.read()

url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
data = urllib.parse.urlencode({
    'chat_id': chat_id,
    'text': message,
}).encode()
req = urllib.request.Request(url, data=data, method='POST')
resp = urllib.request.urlopen(req)
print(resp.read().decode())
