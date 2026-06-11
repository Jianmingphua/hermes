"""
Forex Trading Bot - Support & Resistance Detector

Identifies key support/resistance levels from H1 price action.
Used as a non-TA modifier: filters out trades that go against
established S/R levels.

Strategy:
  - Fetch H1 candles for the pair
  - Find swing highs/lows over a rolling window (fractal-style)
  - Group nearby levels into zones (tolerance based on ATR)
  - Check if current price is within a zone of an S/R level
  - Return score modifier: signals going INTO resistance are penalized
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.oanda_client import OandaClient

logger = logging.getLogger(__name__)

# Minimum candle span for a swing point to be considered significant
SWING_WINDOW = 5   # candles on each side to confirm a swing
# Proximity tolerance: how close to S/R before it matters (in ATR multiples)
ZONE_TOLERANCE_ATR = 0.5
# Significance threshold: only consider levels with N+ touches
MIN_TOUCHES = 1


class SupportResistance:
    """
    Detects S/R levels from H1 data and scores trade signals
    against them.

    Higher score = price near support (favours BUY, penalises SELL).
    Lower score = price near resistance (favours SELL, penalises BUY).
    Neutral = no S/R nearby.
    """

    def __init__(self):
        self._client: Optional[OandaClient] = None

    @property
    def client(self) -> OandaClient:
        if self._client is None:
            self._client = OandaClient()
        return self._client

    def _fetch_h1_data(self, instrument: str, count: int = 200) -> pd.DataFrame:
        """Fetch H1 candles for S/R analysis."""
        try:
            df = self.client.get_candles(instrument, "H1", count)
            return df
        except Exception as e:
            logger.warning("S/R: could not fetch H1 data for %s: %s", instrument, e)
            return pd.DataFrame()

    def _find_swing_points(self, df: pd.DataFrame) -> tuple[list, list]:
        """
        Find swing highs and lows using a rolling window.

        A swing high is a point where the high is higher than SWING_WINDOW
        candles on either side. A swing low is where the low is lower than
        SWING_WINDOW candles on either side.

        Returns:
            (swing_highs, swing_lows) — lists of (index, price) tuples.
        """
        highs = df["high"].values
        lows = df["low"].values
        n = len(highs)

        swing_highs = []
        swing_lows = []

        for i in range(SWING_WINDOW, n - SWING_WINDOW):
            # Swing high
            left = highs[i - SWING_WINDOW:i]
            right = highs[i + 1:i + SWING_WINDOW + 1]
            if len(left) >= SWING_WINDOW and len(right) >= SWING_WINDOW:
                if highs[i] > left.max() and highs[i] > right.max():
                    swing_highs.append((i, highs[i]))

            # Swing low
            left_l = lows[i - SWING_WINDOW:i]
            right_l = lows[i + 1:i + SWING_WINDOW + 1]
            if len(left_l) >= SWING_WINDOW and len(right_l) >= SWING_WINDOW:
                if lows[i] < left_l.min() and lows[i] < right_l.min():
                    swing_lows.append((i, lows[i]))

        logger.debug(
            "S/R: found %d swing highs, %d swing lows on %d candles",
            len(swing_highs), len(swing_lows), n,
        )
        return swing_highs, swing_lows

    def _group_nearby_levels(
        self, levels: list[tuple[int, float]],
        atr: float,
        min_touches: int = MIN_TOUCHES,
    ) -> list[dict]:
        """
        Group nearby price levels into zones.
        Levels within ZONE_TOLERANCE_ATR * ATR of each other are merged.

        Each returned level has: price, touch_count, strength.
        """
        if not levels or atr <= 0:
            return []

        zone_tolerance = ZONE_TOLERANCE_ATR * atr

        # Sort by price
        sorted_levels = sorted(levels, key=lambda x: x[1])

        zones = []
        current_zone = {"prices": [], "touch_count": 0, "mean_price": 0.0}

        for idx, price in sorted_levels:
            if not current_zone["prices"]:
                current_zone = {
                    "prices": [price],
                    "touch_count": 1,
                    "mean_price": price,
                }
            elif abs(price - current_zone["mean_price"]) <= zone_tolerance:
                current_zone["prices"].append(price)
                current_zone["touch_count"] += 1
                current_zone["mean_price"] = np.mean(current_zone["prices"])
            else:
                if current_zone["touch_count"] >= min_touches:
                    zones.append({
                        "price": round(current_zone["mean_price"], 5),
                        "touch_count": current_zone["touch_count"],
                        "strength": min(current_zone["touch_count"] / 3.0, 1.0),
                    })
                current_zone = {
                    "prices": [price],
                    "touch_count": 1,
                    "mean_price": price,
                }

        # Don't forget the last zone
        if current_zone["touch_count"] >= min_touches:
            zones.append({
                "price": round(current_zone["mean_price"], 5),
                "touch_count": current_zone["touch_count"],
                "strength": min(current_zone["touch_count"] / 3.0, 1.0),
            })

        return zones

    def get_modifier(
        self,
        instrument: str,
        current_price: float,
        direction: str,
        atr: float = None,
    ) -> tuple[float, str]:
        """
        Get score modifier based on S/R proximity.

        Args:
            instrument: e.g. "EUR_USD"
            current_price: current market price (mid)
            direction: "BUY" or "SELL"
            atr: optional ATR value for zone tolerance (fetched if not provided)

        Returns:
            (modifier, reason):
            - modifier: score adjustment (-1.0 to +1.0)
            - reason: human-readable explanation
        """
        df = self._fetch_h1_data(instrument)
        if df.empty or len(df) < 50:
            return 0.0, ""

        # Compute ATR for zone tolerance if not provided
        if atr is None or atr <= 0:
            # Simple ATR from high-low range
            tr = df["high"] - df["low"]
            atr = tr.rolling(14).mean().iloc[-1]
            if pd.isna(atr) or atr <= 0:
                return 0.0, ""

        swing_highs, swing_lows = self._find_swing_points(df)

        # Group nearby levels
        resistance_levels = self._group_nearby_levels(swing_highs, atr)
        support_levels = self._group_nearby_levels(swing_lows, atr)

        zone_tolerance = ZONE_TOLERANCE_ATR * atr
        reason_parts = []

        # Check proximity to resistance (penalises BUY)
        near_resistance = False
        for level in resistance_levels:
            distance = level["price"] - current_price
            if 0 <= distance <= zone_tolerance:
                # Price is near resistance from below — BUY goes into resistance
                near_resistance = True
                reason_parts.append(
                    f"Resistance at {level['price']:.5f} "
                    f"(strength={level['strength']:.1f})"
                )

        # Check proximity to support (penalises SELL)
        near_support = False
        for level in support_levels:
            distance = current_price - level["price"]
            if 0 <= distance <= zone_tolerance:
                # Price is near support from above — SELL goes into support
                near_support = True
                reason_parts.append(
                    f"Support at {level['price']:.5f} "
                    f"(strength={level['strength']:.1f})"
                )

        # Determine modifier based on direction and proximity
        # BUY signals use positive score:  penalise = subtract, reward = add
        # SELL signals use negative score: penalise = add (less negative), reward = subtract (more negative)
        modifier = 0.0
        if direction == "BUY" and near_resistance:
            # BUY into resistance = bad → penalise by subtracting from score
            modifier = -0.8
            reason = f"📊 S/R: BUY blocked by resistance — {' | '.join(reason_parts)}"
        elif direction == "SELL" and near_support:
            # SELL into support = bad → penalise by adding to score (less negative)
            modifier = 0.8
            reason = f"📊 S/R: SELL blocked by support — {' | '.join(reason_parts)}"
        elif direction == "BUY" and near_support:
            # BUY at support = good → reward by adding to score
            modifier = 0.5
            reason = f"📊 S/R: BUY at support — {' | '.join(reason_parts)}"
        elif direction == "SELL" and near_resistance:
            # SELL at resistance = good → reward by subtracting from score (more negative)
            modifier = -0.5
            reason = f"📊 S/R: SELL at resistance — {' | '.join(reason_parts)}"
        else:
            modifier = 0.0
            reason = ""

        if reason:
            logger.info(reason)

        return modifier, reason


# Singleton
support_resistance = SupportResistance()