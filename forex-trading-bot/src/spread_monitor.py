"""
Forex Trading Bot - Spread Monitor
Aborts trades when spreads are too wide (news, low liquidity).
"""

import json
import logging
from pathlib import Path
from typing import Optional

from src.circuit_breaker import circuit_breaker

logger = logging.getLogger(__name__)

# Maximum acceptable spread per pair (in pips for forex, price units for crypto/metals)
MAX_SPREAD_PIPS = {
    "EUR_USD": 2.0,
    "GBP_USD": 2.5,
    "USD_JPY": 2.0,
    "AUD_USD": 2.5,
    "NZD_USD": 3.0,
    "USD_CAD": 2.5,
    "USD_CHF": 2.5,
    "EUR_GBP": 2.0,
    "EUR_JPY": 3.0,
    "GBP_JPY": 3.5,
    # Metals (in price units, e.g., $0.10 for silver)
    "XAG_USD": 0.10,
    "XAU_USD": 5.0,
}

# Crypto spreads (in price units)
MAX_SPREAD_PRICE = {
    "BTC_USD": 100.0,
    "ETH_USD": 10.0,
    "LTC_USD": 2.0,
    "BCH_USD": 5.0,
}

# Default max spread for pairs not in the list
DEFAULT_MAX_SPREAD_PIPS = 3.0


class SpreadMonitor:
    """Monitors bid-ask spreads and prevents trading when too wide."""

    def __init__(self, state_file: str = "logs/spread_history.json"):
        self.state_file = Path(state_file)
        self.history = self._load()

    def _load(self) -> dict:
        """Load spread history from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {}

    def _save(self):
        """Persist spread history to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.history, f, indent=2)

    def record_spread(self, instrument: str, spread_pips: float):
        """Record a spread observation for rolling average calculation."""
        if instrument not in self.history:
            self.history[instrument] = []
        self.history[instrument].append(spread_pips)
        # Keep last 20 observations
        self.history[instrument] = self.history[instrument][-20:]
        self._save()

    def get_avg_spread(self, instrument: str) -> Optional[float]:
        """Get the 20-period average spread in pips."""
        spreads = self.history.get(instrument, [])
        if len(spreads) < 5:  # Need minimum data
            return None
        return sum(spreads) / len(spreads)

    def check_spread(self, instrument: str, current_spread: float) -> tuple[bool, str]:
        """
        Check if the current spread is acceptable for trading.
        Two checks:
        1. Absolute: spread must be below max threshold
        2. Relative: spread must be < 2x the 20-period average (avoids news spikes)

        For crypto instruments, spread is in price units (not pips).
        """
        # Crypto instruments use price units directly
        if instrument in MAX_SPREAD_PRICE:
            max_spread = MAX_SPREAD_PRICE[instrument]
            if current_spread > max_spread:
                return False, (
                    f"🔴 Spread too wide for {instrument}: "
                    f"${current_spread:.2f} (max: ${max_spread:.2f})"
                )
            return True, ""

        # Forex/metal instruments use pips
        if "JPY" in instrument:
            spread_pips = current_spread * 100
        elif instrument in ("XAG_USD", "XAU_USD"):
            # Metals: spread is already in price units, compare directly
            spread_pips = current_spread
        else:
            spread_pips = current_spread * 10000

        # Record for future average calculation
        self.record_spread(instrument, spread_pips)

        # Check 1: Absolute max spread
        max_spread = MAX_SPREAD_PIPS.get(instrument, DEFAULT_MAX_SPREAD_PIPS)
        if spread_pips > max_spread:
            return False, (
                f"🔴 Spread too wide for {instrument}: "
                f"{spread_pips:.1f} pips (max: {max_spread:.1f})"
            )

        # Check 2: Relative to 20-period average (skip if insufficient data)
        avg_spread = self.get_avg_spread(instrument)
        if avg_spread is not None and avg_spread > 0:
            ratio = spread_pips / avg_spread
            if ratio > 2.0:
                return False, (
                    f"🔴 Spread spike for {instrument}: "
                    f"{spread_pips:.1f} pips vs avg {avg_spread:.1f} pips "
                    f"({ratio:.1f}x average)"
                )

        return True, ""

    def get_max_spread(self, instrument: str) -> float:
        """Get the maximum acceptable spread in pips for a pair."""
        return MAX_SPREAD_PIPS.get(instrument, DEFAULT_MAX_SPREAD_PIPS)


# Singleton
spread_monitor = SpreadMonitor()
