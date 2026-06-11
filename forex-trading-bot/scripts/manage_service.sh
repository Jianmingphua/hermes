#!/bin/bash
# ── Forex Bot Systemd Startup Wrapper ─────────────────────────
# Handles XDG_RUNTIME_DIR for user-level systemd.
# Called manually to enable/start/restart the forex bot service.

set -euo pipefail

SERVICE_NAME="forex-bot"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR"

case "${1:-status}" in
    start|stop|restart|status|enable|disable|logs|journal)
        systemctl --user "$1" "$SERVICE_NAME"
        ;;
    enable-start)
        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME"
        systemctl --user start "$SERVICE_NAME"
        systemctl --user status "$SERVICE_NAME"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|enable|disable|enable-start|logs|journal}"
        echo ""
        echo "  enable-start  — Register + start the bot service"
        echo "  logs          — Tail the bot's log file"
        echo "  journal       — View systemd journal for the bot"
        echo "  status        — Check if bot is running"
        exit 1
        ;;
esac