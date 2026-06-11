"""Forex Trading Bot - Telegram Signal Notifier
Uses Hermes send_message to deliver signals to Telegram.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Signal output directory
SIGNAL_LOG_DIR = Path("logs")

# ── Tier ordering (for comparison) ──────────────────────────────
TIER_MAP = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}

# Backward compat constants (numeric, as before)
TIER_LOW = 1
TIER_MEDIUM = 2
TIER_HIGH = 3


def send_telegram_alert(message: str) -> bool:
    """Send a message to the user's Telegram via Hermes send_message tool.

    Wrapped in try/except because send_message is only available in the
    cron/agent context, not when running standalone.
    Returns True if sent, False otherwise.
    """
    try:
        from hermes.agent import send_message
        send_message(message)
        logger.info("Telegram alert sent (%d chars)", len(message))
        return True
    except ImportError:
        logger.warning("send_message not available (not in agent context)")
    except Exception as e:
        logger.error("Failed to send Telegram alert: %s", e)
    return False


def notify_fill(order_info: dict) -> None:
    """Format and send a concise trade fill alert."""
    instrument = order_info.get("instrument", "?")
    units = int(order_info.get("units", 0))
    direction = "BUY" if units > 0 else "SELL"
    price = order_info.get("price", "?")
    pl = order_info.get("pl")
    trade_id = order_info.get("trade_id", order_info.get("id", "?"))

    emoji = "🟢" if direction == "BUY" else "🔴"
    lines = [
        f"{emoji} **Trade Filled**",
        f"📈 {instrument} {direction}",
        f"   Units: {abs(units):,} @ {price}",
    ]
    if pl is not None:
        emoji_pl = "✅" if float(pl) >= 0 else "❌"
        lines.append(f"   P&L: {emoji_pl} {pl}")
    lines.append(f"   Trade ID: `{trade_id}`")
    send_telegram_alert("\n".join(lines))


def notify_circuit_breaker(reason: str, cooldown_min: int) -> None:
    """Alert when the circuit breaker trips."""
    lines = [
        "⚠️ **Circuit Breaker Tripped**",
        f"   {reason}",
        f"   Cooldown: {cooldown_min} min",
    ]
    send_telegram_alert("\n".join(lines))


def format_signal_message(
    signals: list[dict],
    account_balance: float,
    dry_run: bool = False,
) -> Optional[str]:
    """Format signals into a readable Telegram message.

    Only includes BUY/SELL signals (skips HOLD).
    Returns None if there are no actionable signals.
    """
    lines = [
        f"📊 Forex Bot Cycle",
        f"⏰ {datetime.utcnow():%Y-%m-%d %H:%M} UTC",
        f"💰 {account_balance:,.2f} SGD",
        "",
    ]
    has_trades = False
    for sig in signals:
        if sig.get("signal") not in ("BUY", "SELL"):
            continue
        has_trades = True
        ts = sig.get("trade_setup", {})
        if ts:
            emoji = "🟢" if ts.get("direction") == "BUY" else "🔴"
            lines.append(f"{emoji} {sig['instrument']} — {ts['direction']}")
            lines.append(f"   Entry: {ts['entry']} | SL: {ts['sl']} | TP: {ts['tp']}")
            lines.append(f"   Conf: {ts['confidence']:.0%} | Units: {abs(ts['units']):,}")
            lines.append("")
        else:
            emoji = "🟢" if sig.get("signal") == "BUY" else "🔴"
            lines.append(
                f"{emoji} {sig.get('instrument', '?')} — {sig.get('signal', '?')} "
                f"(conf: {sig.get('confidence', 0):.0%})"
            )
            lines.append("")
    if dry_run:
        lines.append("_Dry run — no orders_")
    return "\n".join(lines) if has_trades else None


def save_signal_log(
    signals: list[dict],
    account_balance: float,
    dry_run: bool = False,
):
    """Save signals to a JSON log file and send Telegram alert for strong signals."""
    SIGNAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "account_balance": account_balance,
        "signals": signals,
    }
    out_file = SIGNAL_LOG_DIR / f"signals_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("Signals saved to %s", out_file)

    # Send Telegram alert for strong signals (tier >= MEDIUM)
    strong_signals = [
        s for s in signals
        if s.get("signal") in ("BUY", "SELL")
        and TIER_MAP.get(s.get("tier", ""), 0) >= TIER_MEDIUM
    ]
    if strong_signals:
        msg = format_signal_message(signals, account_balance, dry_run=dry_run)
        if msg:
            send_telegram_alert(msg)
        else:
            logger.debug("No actionable signals to notify about")

    return out_file
