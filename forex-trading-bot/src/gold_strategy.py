"""
Gold (XAU/USD) Dedicated Trading Strategy
==========================================
Designed specifically for gold's unique characteristics:
- High volatility (ATR $17-19 on H1 vs $4-12 for forex)
- Wide spreads (50-62 pips on OANDA practice)
- Strong trending behavior with round-number respect
- Session-sensitive (London/NY overlap is key)

Strategy: Trend-Following + Momentum Confirmation
- Trend: 50/200 EMA filter (only trade in trend direction)
- Momentum: RSI(14) + MACD crossover
- Volatility: ATR(14) for stop-loss and position sizing
- Session: London + NY overlap only (13:00-17:00 UTC)
- Entry: 3 of 4 confirmations required (stricter than forex)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Try ta-lib, fall back to manual implementations
try:
    import talib
    HAS_TALIB = True
    logger.info("Gold strategy: using ta-lib for indicators")
except ImportError:
    HAS_TALIB = False
    logger.warning("Gold strategy: ta-lib not available, using manual implementations")


# ── Gold-Specific Constants ──────────────────────────────────────

GOLD_SESSIONS = {
    "london":    {"start": 7,  "end": 16},
    "new_york":  {"start": 12, "end": 21},
    "overlap":   {"start": 13, "end": 17},  # London/NY overlap — peak gold window
}

# Spread threshold: gold spreads are naturally wider
MAX_SPREAD_PIPS = 80  # 80 pips = $0.80

# ATR-based stop/take-profit multipliers
SL_ATR_MULT = 1.5    # Stop loss: 1.5x ATR
TP_ATR_MULT = 2.5    # Take profit: 2.5x ATR (1:1.67 R:R)

# EMA periods for gold (slightly longer than forex due to noise)
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200

# RSI settings
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# MACD settings (standard)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Minimum confirmations to trigger (stricter than forex)
MIN_CONFIRMATIONS = 3

# Risk per trade (lower than forex due to wider stops)
RISK_PER_TRADE = 0.005  # 0.5% of balance per trade
MAX_DAILY_LOSS = 0.02   # 2% max daily loss
MAX_OPEN_POSITIONS = 2   # Fewer positions due to larger size


@dataclass
class GoldSignal:
    """Represents a gold trading signal with full context."""
    instrument: str = "XAU_USD"
    signal: str = "HOLD"          # BUY, SELL, HOLD
    confidence: float = 0.0
    score: float = 0.0
    confirmations: int = 0
    tier: str = "NONE"            # HIGH, MEDIUM, LOW, NONE
    direction_fired: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    atr: float = 0.0
    spread_pips: float = 0.0
    risk_reward_ratio: float = 0.0
    units: int = 0
    risk_amount: float = 0.0


class GoldSessionFilter:
    """Session filter optimized for gold's volatility patterns."""

    # Gold's best sessions — London open and London/NY overlap
    BEST_SESSIONS = ["london", "overlap"]

    def is_good_time(self, utc_hour: int) -> tuple[bool, str]:
        """Check if current UTC hour is good for gold trading."""
        active = self._get_active_sessions(utc_hour)

        for session in self.BEST_SESSIONS:
            if session in active:
                return True, ""

        next_good = self._get_next_good_session(utc_hour)
        return False, f"Outside gold trading hours. Next: {next_good}"

    def _get_active_sessions(self, hour: int) -> list[str]:
        active = []
        for name, sess in GOLD_SESSIONS.items():
            start, end = sess["start"], sess["end"]
            if start <= end:
                if start <= hour < end:
                    active.append(name)
            else:
                if hour >= start or hour < end:
                    active.append(name)
        return active

    def _get_next_good_session(self, hour: int) -> str:
        for offset in range(1, 24):
            future = (hour + offset) % 24
            active = self._get_active_sessions(future)
            for session in self.BEST_SESSIONS:
                if session in active:
                    return f"{session} at {future:02d}:00 UTC"
        return "unknown"


class GoldSpreadMonitor:
    """Spread monitor with gold-specific thresholds."""

    def __init__(self, max_spread_pips: float = MAX_SPREAD_PIPS):
        self.max_spread_pips = max_spread_pips

    def check_spread(self, spread: float) -> tuple[bool, str]:
        """
        Check if spread is acceptable for gold.
        Spread is in price units (e.g., 0.62 for XAU/USD).
        """
        spread_pips = spread * 100  # Convert to pips (1 pip = $0.01 for gold)
        if spread_pips > self.max_spread_pips:
            return False, f"Spread too wide: {spread_pips:.1f} pips (max {self.max_spread_pips})"
        return True, ""


class GoldSignalGenerator:
    """
    Gold-specific signal generator.
    Uses 4 confirmation signals:
    1. EMA trend alignment (price vs 50/200 EMA)
    2. MACD crossover
    3. RSI momentum / divergence
    4. ATR-based volatility filter (avoid low-vol entries)
    """

    def __init__(self):
        self.session_filter = GoldSessionFilter()
        self.spread_monitor = GoldSpreadMonitor()

    def analyze(self, df: pd.DataFrame, current_price: dict) -> GoldSignal:
        """
        Analyze gold data and generate signal.

        Args:
            df: DataFrame with OHLCV data (at least 200 bars)
            current_price: Dict with bid/ask/spread from OANDA

        Returns:
            GoldSignal with full analysis
        """
        sig = GoldSignal()

        if len(df) < EMA_SLOW + 10:
            sig.reasons.append("Insufficient data")
            return sig

        # Calculate all indicators
        indicators = self._calculate_indicators(df)
        sig.indicators = indicators

        # Current values
        close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        ema_20 = indicators['ema_20']
        ema_50 = indicators['ema_50']
        ema_200 = indicators['ema_200']
        rsi = indicators['rsi']
        macd_line = indicators['macd']
        macd_signal_line = indicators['macd_signal']
        macd_hist = indicators['macd_histogram']
        prev_macd_hist = indicators.get('prev_macd_histogram', macd_hist)
        atr = indicators['atr']
        adx = indicators['adx']

        sig.atr = atr
        sig.entry_price = current_price['ask']  # Use ask for buy, bid for sell

        # ── Confirmation 1: EMA Trend Alignment ──
        ema_bullish = (close > ema_50 > ema_200) and (ema_20 > ema_50)
        ema_bearish = (close < ema_50 < ema_200) and (ema_20 < ema_50)
        ema_score = 0
        if ema_bullish:
            ema_score = 1.5
            sig.direction_fired['ema'] = True
            sig.reasons.append(f"EMA bullish: price > EMA50 > EMA200")
        elif ema_bearish:
            ema_score = -1.5
            sig.direction_fired['ema'] = True
            sig.reasons.append(f"EMA bearish: price < EMA50 < EMA200")
        else:
            sig.reasons.append(f"EMA mixed: price={'above' if close > ema_50 else 'below'} EMA50, EMA50={'above' if ema_50 > ema_200 else 'below'} EMA200")

        # ── Confirmation 2: MACD Crossover ──
        macd_score = 0
        if macd_hist > 0 and prev_macd_hist <= 0:
            macd_score = 1.5
            sig.direction_fired['macd'] = True
            sig.reasons.append("MACD bullish crossover")
        elif macd_hist < 0 and prev_macd_hist >= 0:
            macd_score = -1.5
            sig.direction_fired['macd'] = True
            sig.reasons.append("MACD bearish crossover")
        elif macd_hist > 0:
            macd_score = 0.5
            sig.reasons.append("MACD above signal (momentum)")
        else:
            macd_score = -0.5
            sig.reasons.append("MACD below signal (momentum)")

        # ── Confirmation 3: RSI Momentum ──
        rsi_score = 0
        if rsi < RSI_OVERSOLD:
            rsi_score = 1.0
            sig.direction_fired['rsi'] = True
            sig.reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_OVERBOUGHT:
            rsi_score = -1.0
            sig.direction_fired['rsi'] = True
            sig.reasons.append(f"RSI overbought ({rsi:.1f})")
        elif rsi < 45:
            rsi_score = -0.3
            sig.reasons.append(f"RSI bearish zone ({rsi:.1f})")
        elif rsi > 55:
            rsi_score = 0.3
            sig.reasons.append(f"RSI bullish zone ({rsi:.1f})")
        else:
            sig.reasons.append(f"RSI neutral ({rsi:.1f})")

        # ── Confirmation 4: Volatility Filter (ADX + ATR) ──
        vol_score = 0
        if adx > 25:
            vol_score = 1.0
            sig.direction_fired['volatility'] = True
            sig.reasons.append(f"Strong trend (ADX {adx:.1f})")
        elif adx > 20:
            vol_score = 0.5
            sig.reasons.append(f"Developing trend (ADX {adx:.1f})")
        else:
            vol_score = -0.5
            sig.reasons.append(f"Range-bound (ADX {adx:.1f})")

        # ── Calculate Total Score ──
        total_score = ema_score + macd_score + rsi_score + vol_score
        sig.score = total_score

        # Count confirmations (directional signals that align)
        bullish_count = sum(1 for v in [ema_score, macd_score, rsi_score, vol_score] if v > 0)
        bearish_count = sum(1 for v in [ema_score, macd_score, rsi_score, vol_score] if v < 0)

        # Determine signal direction
        if total_score >= 3.0 and bullish_count >= MIN_CONFIRMATIONS:
            sig.signal = "BUY"
            sig.confirmations = bullish_count
        elif total_score <= -3.0 and bearish_count >= MIN_CONFIRMATIONS:
            sig.signal = "SELL"
            sig.confirmations = bearish_count
        else:
            sig.signal = "HOLD"
            sig.confirmations = max(bullish_count, bearish_count)

        # Confidence: normalize score to 0-1 range
        # Max possible score is 5.0 (1.5 + 1.5 + 1.0 + 1.0)
        sig.confidence = min(abs(total_score) / 5.0, 1.0)

        # Quality tier
        if sig.confirmations >= 4 and sig.confidence >= 0.7:
            sig.tier = "HIGH"
        elif sig.confirmations >= 3 and sig.confidence >= 0.5:
            sig.tier = "MEDIUM"
        elif sig.confirmations >= 2:
            sig.tier = "LOW"
        else:
            sig.tier = "NONE"

        # ── Trade Setup (ATR-based) ──
        if sig.signal in ("BUY", "SELL"):
            if sig.signal == "BUY":
                sig.stop_loss = close - (atr * SL_ATR_MULT)
                sig.take_profit = close + (atr * TP_ATR_MULT)
                sig.entry_price = current_price['ask']
            else:
                sig.stop_loss = close + (atr * SL_ATR_MULT)
                sig.take_profit = close - (atr * TP_ATR_MULT)
                sig.entry_price = current_price['bid']

            sig.risk_reward_ratio = TP_ATR_MULT / SL_ATR_MULT

        # Spread check
        spread = current_price.get('spread', 0)
        sig.spread_pips = spread * 100

        return sig

    def _calculate_indicators(self, df: pd.DataFrame) -> dict:
        """Calculate all technical indicators for gold."""
        close = df['close']
        high = df['high']
        low = df['low']

        if HAS_TALIB:
            close_arr = close.values
            high_arr = high.values
            low_arr = low.values

            ema_20 = talib.EMA(close_arr, timeperiod=EMA_FAST)[-1]
            ema_50 = talib.EMA(close_arr, timeperiod=EMA_MID)[-1]
            ema_200 = talib.EMA(close_arr, timeperiod=EMA_SLOW)[-1]

            rsi = talib.RSI(close_arr, timeperiod=RSI_PERIOD)[-1]

            macd_line, macd_signal_line, macd_hist = talib.MACD(
                close_arr,
                fastperiod=MACD_FAST,
                slowperiod=MACD_SLOW,
                signalperiod=MACD_SIGNAL,
            )
            macd_hist_val = macd_hist[-1]
            prev_macd_hist = macd_hist[-2] if len(macd_hist) > 1 else macd_hist_val

            atr = talib.ATR(high_arr, low_arr, close_arr, timeperiod=14)[-1]
            adx = talib.ADX(high_arr, low_arr, close_arr, timeperiod=14)[-1]
        else:
            # Manual fallbacks
            ema_20 = close.ewm(span=EMA_FAST).mean().iloc[-1]
            ema_50 = close.ewm(span=EMA_MID).mean().iloc[-1]
            ema_200 = close.ewm(span=EMA_SLOW).mean().iloc[-1]

            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            rsi = (100 - (100 / (1 + rs))).iloc[-1]

            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd_line = ema12 - ema26
            macd_signal_line = macd_line.ewm(span=9).mean()
            macd_hist = macd_line - macd_signal_line
            macd_hist_val = macd_hist.iloc[-1]
            prev_macd_hist = macd_hist.iloc[-2] if len(macd_hist) > 1 else macd_hist_val

            tr1 = high - low
            tr2 = (high - close.shift()).abs()
            tr3 = (low - close.shift()).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]

            # Simplified ADX
            adx = 25.0

        return {
            "ema_20": round(ema_20, 2),
            "ema_50": round(ema_50, 2),
            "ema_200": round(ema_200, 2),
            "rsi_14": round(rsi, 2),
            "atr_14": round(atr, 2),
            "adx_14": round(adx, 2),
            "macd": round(macd_line[-1], 2),
            "macd_signal": round(macd_signal_line[-1], 2),
            "macd_histogram": round(macd_hist_val, 2),
            "prev_macd_histogram": round(prev_macd_hist, 2),
            "current_price": round(close[-1], 2),
        }


class GoldRiskManager:
    """Risk management calibrated for gold's volatility."""

    def __init__(
        self,
        risk_per_trade: float = RISK_PER_TRADE,
        max_daily_loss: float = MAX_DAILY_LOSS,
        max_open_positions: int = MAX_OPEN_POSITIONS,
    ):
        self.risk_per_trade = risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions

    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
        atr: float,
    ) -> tuple[int, float]:
        """
        Calculate position size for gold.
        Returns (units, risk_amount).

        Gold position sizing: risk 0.5% of balance per trade.
        Units = risk_amount / stop_distance_in_price
        """
        risk_amount = balance * self.risk_per_trade
        stop_distance = abs(entry_price - stop_loss)

        if stop_distance <= 0:
            return 0, 0

        # For gold, units are in ounces (1 unit = 1 oz)
        units = int(risk_amount / stop_distance)

        # Cap at reasonable size (max 500 oz for 100K account)
        max_units = 500
        if units > max_units:
            units = max_units
            risk_amount = units * stop_distance

        return units, round(risk_amount, 2)

    def validate_signal(self, signal: GoldSignal, balance: float) -> tuple[bool, str]:
        """Validate a gold signal before execution."""
        if signal.signal == "HOLD":
            return False, "No signal"

        if signal.tier == "NONE":
            return False, "Signal tier too weak"

        if signal.confirmations < MIN_CONFIRMATIONS:
            return False, f"Insufficient confirmations: {signal.confirmations}/{MIN_CONFIRMATIONS}"

        if signal.spread_pips > MAX_SPREAD_PIPS:
            return False, f"Spread too wide: {signal.spread_pips:.1f} pips"

        if signal.atr < 5.0:
            return False, f"ATR too low: ${signal.atr:.2f} (avoid low-vol entries)"

        return True, ""
