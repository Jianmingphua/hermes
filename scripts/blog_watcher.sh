#!/bin/bash
# Blog Watcher — uses forex venv for feedparser
exec /opt/hermes/forex-trading-bot/venv/bin/python3 \
  /opt/hermes/scripts/blog_watcher.py "$@"