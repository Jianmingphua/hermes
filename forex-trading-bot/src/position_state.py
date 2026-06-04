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
CORRELATION_GROUPS = [
    {"name": "USD_SHORT", "pairs": ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"], "direction": "SELL"},
    {"name": "USD_LONG", "pairs": ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"], "direction": "BUY"},
    {"name": "JPY_SHORT", "pairs": ["USD_JPY", "EUR_JPY", "GBP_JPY"], "direction": "SELL"},
    {"name": "JPY_LONG", "pairs": ["USD_JPY", "EUR_JPY", "GBP_JPY"], "direction": "BUY"},
]

MAX_CORRELATED_EXPOSURE = 2  # Max positions in same correlation group


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
                if int(p["long_units"]) > 0:
                    oanda_open.add((p["instrument"], "long"))
                if int(p["short_units"]) > 0:
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
        """Persist state to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

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
            for pos in self.get_open_positions():
                if pos["instrument"] in group["pairs"]:
                    pos_side = "long" if pos.get("side") == "long" else "short"
                    if pos_side == side:
                        group_count += 1

            if group_count >= MAX_CORRELATED_EXPOSURE:
                return False, (
                    f"🔴 Correlation limit: {group_count}/{MAX_CORRELATED_EXPOSURE} "
                    f"{group['name']} positions already open"
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
