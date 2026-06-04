#!/usr/bin/env python3
"""Send a Telegram message with inline keyboard buttons (confirm/deny/edit)."""
import json
import sys
import urllib.request
import urllib.error
import os

_home = os.path.expanduser("~")
_token_path = os.path.join(_home, ".hermes", ".telegram_bot_token")

# Try env var first, then file
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    if os.path.exists(_token_path):
        with open(_token_path) as f:
            TOKEN = f.read().strip()
    else:
        # Try reading from .env file
        env_path = os.path.join(_home, ".hermes", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                        TOKEN = line.strip().split("=", 1)[1].strip()
                        break
if not TOKEN:
    print("ERROR: No Telegram bot token found")
    sys.exit(1)


def send_inline_keyboard(chat_id, text, buttons, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        if result.get("ok"):
            print("SENT:" + str(result["result"]["message_id"]))
        else:
            print("ERROR:" + str(result))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print("HTTP_ERROR:" + str(e.code) + ":" + body)
        sys.exit(1)


def answer_callback_query(callback_query_id, text=None, show_alert=False):
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        if result.get("ok"):
            print("ANSWERED")
        else:
            print("ERROR:" + str(result))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print("HTTP_ERROR:" + str(e.code) + ":" + body)
        sys.exit(1)


def edit_message_reply_markup(chat_id, message_id, buttons=None):
    url = f"https://api.telegram.org/bot{TOKEN}/editMessageReplyMarkup"
    payload = {"chat_id": chat_id, "message_id": message_id}
    if buttons is not None:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        if result.get("ok"):
            print("EDITED")
        else:
            print("ERROR:" + str(result))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print("HTTP_ERROR:" + str(e.code) + ":" + body)
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Telegram inline keyboard helper")
    subparsers = parser.add_subparsers(dest="command")

    # send
    p_send = subparsers.add_parser("send")
    p_send.add_argument("--chat-id", required=True)
    p_send.add_argument("--text", required=True)
    p_send.add_argument("--buttons", required=True, help="JSON array of button rows")

    # answer
    p_ans = subparsers.add_parser("answer")
    p_ans.add_argument("--callback-query-id", required=True)
    p_ans.add_argument("--text", default=None)
    p_ans.add_argument("--show-alert", action="store_true")

    # edit
    p_edit = subparsers.add_parser("edit")
    p_edit.add_argument("--chat-id", required=True)
    p_edit.add_argument("--message-id", required=True)
    p_edit.add_argument("--buttons", default=None, help="JSON array or 'none' to remove")

    args = parser.parse_args()

    if args.command == "send":
        buttons = json.loads(args.buttons)
        send_inline_keyboard(args.chat_id, args.text, buttons)
    elif args.command == "answer":
        answer_callback_query(args.callback_query_id, args.text, args.show_alert)
    elif args.command == "edit":
        buttons = None if args.buttons == "none" or args.buttons is None else json.loads(args.buttons)
        edit_message_reply_markup(args.chat_id, int(args.message_id), buttons)
    else:
        parser.print_help()
