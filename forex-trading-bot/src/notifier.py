"""
Forex Trading Bot - Telegram Signal Notifier
Uses Hermes send_message to deliver signals to Telegram.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Signal output directory
SIGNAL_LOG_DIR = Path("logs")


def format_signal_message(signals: list[dict], account_balance: float) -> str:
    """Format signals into a readable Telegram message."""
    lines = [
        f"📊 **Forex Bot Signal Update**",
        f"⏰ {datetime.utcnow():%Y-%m-%d %H:%M UTC}",
        f"💰 Balance: {account_balance:,.2f} SGD",
        "",
    ]

    for sig in signals:
        instrument = sig.get("instrument", "?")
        signal_type = sig.get("signal", "HOLD")
        confidence = sig.get("confidence", 0)

        # Emoji
        if signal_type == "BUY":
            emoji = "🟢"
        elif signal_type == "SELL":
            emoji = "🔴"
        else:
            emoji = "⚪"

        # Confidence bar
        bars = int(confidence * 5)
        conf_bar = "█" * bars + "░" * (5 - bars)

        lines.append(f"{emoji} **{instrument}** → {signal_type}")
        lines.append(f"   Confidence: {conf_bar} {confidence:.0%}")

        # Key reasons
        reasons = sig.get("reasons", [])[:3]
        for r in reasons:
            lines.append(f"   • {r}")

        # Trade setup if available
        if "suggested_stop_loss" in sig:
            lines.append(
                f"   SL: {sig['suggested_stop_loss']} | "
                f"TP: {sig['suggested_take_profit']}"
            )

        lines.append("")

    lines.append("_Dry run mode — no orders placed_")
    return "\n".join(lines)


def save_signal_log(signals: list[dict], account_balance: float):
    """Save signals to a JSON log file."""
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
    return out_file
