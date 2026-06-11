"""
Forex Trading Bot - Risk Management
Position sizing, drawdown limits, and trade validation.
"""

import logging
import math
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
        min_confidence: float = 0.15,
        min_risk_reward: float = 1.0,
    ):
        self.risk_per_trade = risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions
        self.min_confidence = min_confidence
        self.min_risk_reward = min_risk_reward
        self.daily_pnl: float = 0.0

    def update_daily_pnl(self, pnl: float):
        """Update the daily P&L from the account (called by main loop)."""
        self.daily_pnl = round(pnl, 2)

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
        confidence: float = 0.5,
        atr: float = 0,
        avg_atr: float = 0,
    ) -> int:
        """
        Calculate position size based on risk %, stop distance, signal confidence,
        and relative volatility.

        Dynamic sizing:
        - Base: risk_per_trade % of balance
        - Confidence multiplier: 0.5x (low conf) to 1.5x (high conf)
        - Volatility multiplier: reduce size when ATR is above average (riskier)
        """
        risk_amount = account_balance * self.risk_per_trade
        price_distance = abs(entry_price - stop_loss_price)

        # Safety: NaN, zero, or negative balance — skip trade
        if math.isnan(account_balance) or account_balance <= 0:
            logger.warning("Invalid account balance (NaN/zero/negative), skipping trade")
            return 0

        # Safety: price distance too small — can't size safely
        if price_distance < 0.00001:
            logger.warning("Price distance too small (%s), skipping trade", price_distance)
            return 0

        if price_distance == 0:
            logger.warning("Stop loss equals entry price, skipping trade")
            return 0

        # Base units from risk
        base_units = int(risk_amount / price_distance)

        # Confidence multiplier: maps 0.4-1.0 confidence → 0.5x-1.5x
        # conf=0.4 → 0.5x, conf=0.7 → 1.0x, conf=1.0 → 1.5x
        conf_mult = max(0.5, min(1.5, confidence * 1.5))

        # Volatility multiplier: reduce size when current ATR > average ATR
        vol_mult = 1.0
        if avg_atr > 0 and atr > 0:
            vol_ratio = atr / avg_atr
            if vol_ratio > 1.5:
                vol_mult = 0.5  # Very high vol → half size
            elif vol_ratio > 1.2:
                vol_mult = 0.75  # Above average vol → 75% size
            elif vol_ratio < 0.8:
                vol_mult = 1.1  # Low vol → slight boost

        units = int(base_units * conf_mult * vol_mult)

        # OANDA minimum is typically 1 unit, max varies by instrument
        units = max(units, 1000)  # Minimum 1000 units (0.001 lot)
        units = min(units, 50_000)  # Cap at 50K units

        logger.info(
            "Position sizing: balance=%s | risk=%s | dist=%s | conf=%.2f (x%.2f) | vol=x%.2f → %d units",
            account_balance, risk_amount, price_distance, confidence, conf_mult, vol_mult, units,
        )
        return units

    def build_trade_setup(
        self,
        signal: dict,
        account_balance: float,
        current_positions: int = 0,
        sl_mult: float = 1.5,
        tp_mult: float = 2.5,
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

        # Calculate stops based on ATR with per-pair optimized multipliers
        stop_distance = sl_mult * atr
        if direction == "BUY":
            stop_loss = entry - stop_distance
            take_profit = entry + (tp_mult * atr)
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - (tp_mult * atr)

        # Calculate position size with dynamic confidence + volatility adjustment
        atr_val = signal.get("atr_14", atr)
        units = self.calculate_position_size(
            account_balance, entry, stop_loss, signal["instrument"],
            confidence=signal.get("confidence", 0.5),
            atr=atr_val,
            avg_atr=signal.get("avg_atr_14", 0),
        )
        if direction == "SELL":
            units = -units

        risk_amount = account_balance * self.risk_per_trade
        rr_ratio = round(tp_mult / sl_mult, 2)

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
