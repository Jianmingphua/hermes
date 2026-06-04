"""
Forex Trading Bot - Risk Management
Position sizing, drawdown limits, and trade validation.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeSetup:
    """A validated trade setup ready for execution."""
    instrument: str
    direction: str  # 'BUY' or 'SELL'
    units: int
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    risk_reward_ratio: float
    confidence: float
    reasons: list[str] = field(default_factory=list)


class RiskManager:
    """Validates and sizes trades according to risk rules."""

    def __init__(
        self,
        risk_per_trade: float = 0.01,
        max_daily_loss: float = 0.03,
        max_open_positions: int = 3,
        min_confidence: float = 0.4,
        min_risk_reward: float = 1.5,
    ):
        self.risk_per_trade = risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions
        self.min_confidence = min_confidence
        self.min_risk_reward = min_risk_reward
        self.daily_pnl: float = 0.0

    def validate_signal(self, signal: dict) -> list[str]:
        """Check if a signal meets minimum criteria. Returns list of issues."""
        issues = []

        if signal.get("confidence", 0) < self.min_confidence:
            issues.append(
                f"Confidence too low: {signal.get('confidence', 0):.2f} "
                f"< {self.min_confidence}"
            )

        if signal.get("signal") == "HOLD":
            issues.append("Signal is HOLD")

        if self.daily_pnl <= -self.max_daily_loss:
            issues.append(
                f"Daily loss limit hit: {self.daily_pnl:.2%}"
            )

        return issues

    def calculate_position_size(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss_price: float,
        instrument: str,
    ) -> int:
        """
        Calculate position size based on risk % and stop distance.

        Returns:
            Number of units (positive=int, will be negated for sells)
        """
        risk_amount = account_balance * self.risk_per_trade
        price_distance = abs(entry_price - stop_loss_price)

        if price_distance == 0:
            logger.warning("Stop loss equals entry price, using minimum size")
            return 1000

        # For forex, 1 unit = 1 unit of base currency
        # Risk per unit = price_distance * 1 unit
        units = int(risk_amount / price_distance)

        # OANDA minimum is typically 1 unit, max varies by instrument
        units = max(units, 1000)  # Minimum 1000 units (0.001 lot)
        units = min(units, 100_000)  # Cap at 100K units (1 standard lot)

        logger.info(
            "Position sizing: balance=%s | risk=%s | distance=%s → %d units",
            account_balance,
            risk_amount,
            price_distance,
            units,
        )
        return units

    def build_trade_setup(
        self,
        signal: dict,
        account_balance: float,
        current_positions: int = 0,
    ) -> Optional[TradeSetup]:
        """
        Build a complete trade setup from a signal.
        Returns None if the trade doesn't pass risk checks.
        """
        # Validate
        issues = self.validate_signal(signal)
        if issues:
            logger.info("Signal rejected: %s", "; ".join(issues))
            return None

        if current_positions >= self.max_open_positions:
            logger.info(
                "Max positions reached: %d/%d",
                current_positions,
                self.max_open_positions,
            )
            return None

        # Extract prices
        direction = signal["signal"]
        atr = signal.get("atr_14", 0)

        if "current_price" in signal:
            price_info = signal["current_price"]
            if direction == "BUY":
                entry = float(price_info["ask"])
            else:
                entry = float(price_info["bid"])
        else:
            entry = signal.get("indicators", {}).get("ema_20", 0)
            if entry == 0:
                return None

        # Calculate stops based on ATR
        stop_distance = 2 * atr
        if direction == "BUY":
            stop_loss = entry - stop_distance
            take_profit = entry + (stop_distance * 1.5)
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - (stop_distance * 1.5)

        # Calculate position size
        units = self.calculate_position_size(
            account_balance, entry, stop_loss, signal["instrument"]
        )
        if direction == "SELL":
            units = -units

        risk_amount = account_balance * self.risk_per_trade
        rr_ratio = 1.5  # Fixed by our 2×ATR stop / 3×ATR target

        # OANDA price precision: JPY pairs = 3 decimals, others = 5 decimals
        precision = 3 if "JPY" in signal["instrument"] else 5

        setup = TradeSetup(
            instrument=signal["instrument"],
            direction=direction,
            units=units,
            entry_price=round(entry, precision),
            stop_loss=round(stop_loss, precision),
            take_profit=round(take_profit, precision),
            risk_amount=round(risk_amount, 2),
            risk_reward_ratio=rr_ratio,
            confidence=signal["confidence"],
            reasons=signal.get("reasons", []),
        )

        logger.info(
            "Trade setup: %s %d units @ %s | SL=%s TP=%s | R:R=1:%.1f",
            setup.direction,
            setup.units,
            setup.entry_price,
            setup.stop_loss,
            setup.take_profit,
            setup.risk_reward_ratio,
        )
        return setup
