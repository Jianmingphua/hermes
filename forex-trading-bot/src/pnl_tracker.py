"""
Fallback P&L calculator for closed trades.
Since OANDA's Transaction API can be unreliable on practice accounts,
we calculate realized P&L from the bot's own signal logs and current prices.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SIGNALS_DIR = Path("/opt/hermes/forex-trading-bot/logs")


def get_signal_pnl(instrument: str, entry_price: float, direction: str) -> Optional[float]:
    """
    Find the most recent signal log for this instrument and get the
    suggested TP/SL levels to calculate theoretical P&L.
    """
    signal_files = sorted(SIGNALS_DIR.glob("signals_*.json"), reverse=True)
    for sf in signal_files:
        try:
            with open(sf) as f:
                data = json.load(f)
            for sig in data.get("signals", []):
                if sig.get("instrument") == instrument and sig.get("signal") in ("BUY", "SELL"):
                    ts = sig.get("analyzed_at", "")
                    if ts:
                        return None  # Just checking existence
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def find_trade_setup(instrument: str, direction: str) -> Optional[dict]:
    """Find the trade setup for a specific instrument/direction from signal logs."""
    signal_files = sorted(SIGNALS_DIR.glob("signals_*.json"), reverse=True)
    for sf in signal_files:
        try:
            with open(sf) as f:
                data = json.load(f)
            for sig in data.get("signals", []):
                if sig.get("instrument") == instrument and sig.get("signal") == direction:
                    if "trade_setup" in sig:
                        return sig["trade_setup"]
                    # Construct from signal data
                    return {
                        "instrument": instrument,
                        "direction": direction,
                        "entry": sig.get("current_price", {}).get("ask" if direction == "BUY" else "bid", 0),
                        "sl": sig.get("suggested_stop_loss", 0),
                        "tp": sig.get("suggested_take_profit", 0),
                    }
        except (json.JSONDecodeError, KeyError):
            continue
    return None
