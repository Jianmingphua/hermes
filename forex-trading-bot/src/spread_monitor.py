"""
Forex Trading Bot - Spread Monitor
Aborts trades when spreads are too wide (news, low liquidity).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum acceptable spread per pair (in pips)
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
}

# Default max spread for pairs not in the list
DEFAULT_MAX_SPREAD_PIPS = 3.0


class SpreadMonitor:
    """Monitors bid-ask spreads and prevents trading when too wide."""

    def check_spread(self, instrument: str, current_spread: float) -> tuple[bool, str]:
        """
        Check if the current spread is acceptable for trading.

        Args:
            instrument: e.g. "EUR_USD"
            current_spread: spread in price units (e.g. 0.00010 = 1 pip for EUR/USD)

        Returns:
            (is_acceptable, reason) — reason is empty if acceptable
        """
        # Convert spread to pips
        # For most pairs, 1 pip = 0.0001; for JPY pairs, 1 pip = 0.01
        if "JPY" in instrument:
            spread_pips = current_spread * 100
        else:
            spread_pips = current_spread * 10000

        max_spread = MAX_SPREAD_PIPS.get(instrument, DEFAULT_MAX_SPREAD_PIPS)

        if spread_pips > max_spread:
            return False, (
                f"🔴 Spread too wide for {instrument}: "
                f"{spread_pips:.1f} pips (max: {max_spread:.1f})"
            )

        return True, ""

    def get_max_spread(self, instrument: str) -> float:
        """Get the maximum acceptable spread in pips for a pair."""
        return MAX_SPREAD_PIPS.get(instrument, DEFAULT_MAX_SPREAD_PIPS)


# Singleton
spread_monitor = SpreadMonitor()
