"""
Forex Trading Bot - Position State Manager
Tracks open positions across cron runs.
Prevents duplicate entries and correlated overexposure.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.oanda_client import OandaClient

logger = logging.getLogger(__name__)

# Correlation groups — pairs that move together
# Max 1 position per group at a time (strictest setting)
CORRELATION_GROUPS = [
    {
        "name": "USD_SHORT",
        "pairs": ["EUR_USD", "GBP_USD", "EUR_GBP"],
        "direction": "BUY",
    },
    {
        "name": "USD_LONG",
        "pairs": ["USD_JPY", "USD_CAD", "USD_CHF", "USD_SGD"],
        "direction": "BUY",
    },
    {
        "name": "USD_SHORT_SELL",
        "pairs": ["EUR_USD", "GBP_USD", "EUR_GBP"],
        "direction": "SELL",
    },
    {
        "name": "USD_LONG_SELL",
        "pairs": ["USD_JPY", "USD_CAD", "USD_CHF", "USD_SGD"],
        "direction": "SELL",
    },
    {
        "name": "COMMODITY_SHORT",
        "pairs": ["AUD_USD", "NZD_USD", "USD_CAD"],
        "direction": "SELL",
    },
    {
        "name": "COMMODITY_LONG",
        "pairs": ["AUD_USD", "NZD_USD", "USD_CAD"],
        "direction": "BUY",
    },
    {
        "name": "JPY_CROSS_SHORT",
        "pairs": ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "SGD_JPY"],
        "direction": "SELL",
    },
    {
        "name": "JPY_CROSS_LONG",
        "pairs": ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "SGD_JPY"],
        "direction": "BUY",
    },
    {
        "name": "SGD_SHORT",
        "pairs": ["USD_SGD", "EUR_SGD", "SGD_JPY"],
        "direction": "SELL",
    },
    {
        "name": "SGD_LONG",
        "pairs": ["USD_SGD", "EUR_SGD", "SGD_JPY"],
        "direction": "BUY",
    },
    # ── Crypto (independent group — no correlation with forex) ──
    {
        "name": "CRYPTO_LONG",
        "pairs": ["BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD"],
        "direction": "BUY",
    },
    {
        "name": "CRYPTO_SHORT",
        "pairs": ["BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD"],
        "direction": "SELL",
    },
    # ── Metals (correlated with USD shorts) ──
    {
        "name": "METAL_LONG",
        "pairs": ["XAG_USD", "XAU_USD"],
        "direction": "BUY",
    },
    {
        "name": "METAL_SHORT",
        "pairs": ["XAG_USD", "XAU_USD"],
        "direction": "SELL",
    },
]

# Max 1 position per correlation group at a time
MAX_CORRELATED_GROUP = 1


class PositionState:
    """
    Manages position state across cron runs.
    Persists to disk so each run knows what's already open.
    """

    def __init__(self, state_file: str = "logs/position_state.json"):
        self.state_file = Path(state_file)
        self.state = self._load()

    def _load(self) -> dict:
        """Load state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                # Validate — check if positions still exist on OANDA
                return self._validate_state(state)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"positions": [], "last_sync": None}

    def _validate_state(self, state: dict) -> dict:
        """Check OANDA for actual open positions, reconcile with local state."""
        try:
            client = OandaClient()
            oanda_positions = client.get_open_positions()

            # Build set of actually open instruments
            oanda_open = set()
            for p in oanda_positions:
                if abs(int(p["long_units"])) > 0:
                    oanda_open.add((p["instrument"], "long"))
                if abs(int(p["short_units"])) > 0:
                    oanda_open.add((p["instrument"], "short"))

            # Remove locally tracked positions that no longer exist on OANDA
            tracked = state.get("positions", [])
            still_open = []
            for pos in tracked:
                side = pos.get("side", "long")
                key = (pos["instrument"], side)
                if key in oanda_open:
                    still_open.append(pos)
                else:
                    logger.info(
                        "Position closed externally: %s %s",
                        pos["instrument"], side
                    )

            state["positions"] = still_open
            state["last_sync"] = datetime.now(timezone.utc).isoformat()
            return state

        except Exception as e:
            logger.warning("Could not validate state with OANDA: %s", e)
            return state

    def _save(self):
        """Persist state to disk (atomic write — crash-safe)."""
        from src.file_utils import atomic_save
        atomic_save(self.state_file, self.state)

    def get_open_positions(self) -> list[dict]:
        """Get list of tracked open positions."""
        return self.state.get("positions", [])

    def get_open_count(self) -> int:
        """Get number of open positions."""
        return len(self.get_open_positions())

    def is_already_open(self, instrument: str, direction: str = None) -> bool:
        """Check if a position is already open for this instrument."""
        for pos in self.get_open_positions():
            if pos["instrument"] == instrument:
                if direction is None:
                    return True
                side = "long" if direction == "BUY" else "short"
                if pos.get("side") == side:
                    return True
        return False

    def check_correlation(self, instrument: str, direction: str) -> tuple[bool, str]:
        """
        Check if adding this position would exceed correlated exposure.

        Groups:
            {EUR_USD, GBP_USD, EUR_GBP} — USD shorts
            {USD_JPY, USD_CAD, USD_CHF} — USD longs

        Max 1 position per group at a time.

        Returns:
            (is_safe, reason)
        """
        side = "long" if direction == "BUY" else "short"

        for group in CORRELATION_GROUPS:
            if instrument not in group["pairs"]:
                continue
            if direction != group["direction"]:
                continue

            # Count how many positions in this group are already open
            group_count = 0
            group_open = []
            for pos in self.get_open_positions():
                if pos["instrument"] in group["pairs"]:
                    pos_side = "long" if pos.get("side") == "long" else "short"
                    if pos_side == side:
                        group_count += 1
                        group_open.append(pos["instrument"])

            if group_count >= MAX_CORRELATED_GROUP:
                return False, (
                    f"🔴 Correlation limit: {group_count}/{MAX_CORRELATED_GROUP} "
                    f"{group['name']} positions already open "
                    f"({', '.join(group_open)}) — cannot add {instrument}"
                )

        return True, ""

    def add_position(self, instrument: str, direction: str, units: int,
                     entry_price: float, sl: float, tp: float,
                     order_id: str = None):
        """Record a new position."""
        side = "long" if direction == "BUY" else "short"
        position = {
            "instrument": instrument,
            "side": side,
            "units": units,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
            "order_id": order_id,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state["positions"].append(position)
        self._save()
        logger.info("Position tracked: %s %s %d units @ %s", direction, instrument, units, entry_price)

    def remove_position(self, instrument: str, side: str = None):
        """Remove a closed position."""
        positions = self.state.get("positions", [])
        remaining = []
        for pos in positions:
            if pos["instrument"] == instrument:
                if side is None or pos.get("side") == side:
                    logger.info("Position removed: %s %s", instrument, side or "any")
                    continue
            remaining.append(pos)
        self.state["positions"] = remaining
        self._save()

    def get_status(self) -> dict:
        """Get current position status."""
        positions = self.get_open_positions()
        return {
            "open_count": len(positions),
            "positions": [
                f"{p['instrument']} {p['side']} {p['units']} @ {p['entry_price']}"
                for p in positions
            ],
        }


# Singleton
position_state = PositionState()
