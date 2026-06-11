#!/bin/bash
# Forex Bot Health Check — no_agent mode wrapper.
# Exits 0 always: non-empty stdout = deliver, empty stdout = silent.
cd /opt/hermes/forex-trading-bot && source venv/bin/activate && python3 scripts/health_check.py
PY_EXIT=$?

# Also check systemd
export XDG_RUNTIME_DIR=/run/user/$(id -u)
SYSTEMD_OK=$(systemctl --user is-active forex-bot 2>/dev/null || echo "unknown")

if [ "$SYSTEMD_OK" != "active" ]; then
    echo "❌ systemd forex-bot service is $SYSTEMD_OK (not active)"
fi

# Always exit 0 (no_agent: empty output = silent, output = deliver)
exit 0