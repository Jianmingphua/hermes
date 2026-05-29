#!/usr/bin/env python3
"""Send a Telegram message with inline keyboard buttons."""
import json
import sys
import urllib.request
import urllib.error
import os

_home = os.path.expanduser("~")
_token_path = os.path.join(_home, ".hermes", ".telegram_bot_token")
with open(_token_path) as f:
    TOKEN = f.read().strip()


def send_inline_keyboard(chat_id, text, buttons):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        if result.get("ok"):
            print("SENT:" + str(result["result"]["message_id"]))
        else:
            print("ERROR:" + str(result))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print("HTTP_ERROR:" + str(e.code) + ":" + body)
        sys.exit(1)


def answer_callback_query(callback_query_id, text=None):
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        if result.get("ok"):
            print("ANSWERED")
        else:
            print("ERROR:" + str(result))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print("HTTP_ERROR:" + str(e.code) + ":" + body)
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--buttons", required=True, help="JSON array of button rows")
    args = parser.parse_args()
    buttons = json.loads(args.buttons)
    send_inline_keyboard(args.chat_id, args.text, buttons)
