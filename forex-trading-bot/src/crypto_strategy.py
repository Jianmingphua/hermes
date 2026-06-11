"""
Forex Trading Bot - Crypto Strategy Module
============================================
Dedicated signal generator for crypto pairs (BTC_USD, ETH_USD).

Crypto characteristics:
- 24/7 trading (no session filter)
- High volatility, large ATR (BTC: $400-800, ETH: $15-30 per H1 candle)
- Strong trending behavior with violent pullbacks
- Wider spreads than forex (BTC: ~$40, ETH: ~$3)
- Position sizing: 0.5% risk per trade (half of forex)

Strategy: Trend-Following + Momentum Confirmation
- Trend: EMA 20/50/200 alignment (price must be on correct side of all three)
- Momentum: MACD crossover + RSI extreme
- Volatility: ADX > 20 (meaningful trend required)
- Entry: 3 of 4 confirmations required
- No session filter — crypto trades 24/7
- Wider stops: 2.5x ATR SL, 4.0x ATR TP (1:1.67 R:R)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Crypto-Specific Constants ──────────────────────────────────────

CRYPTO_SESSIONS = {
    "always": {"start": 0, "end": 24},  # 24/7 — no session filter
}

# Spread thresholds (in price units, not pips)
MAX_SPREAD_PRICE = {
    "BTC_USD": 100.0,   # $100 spread max
    "ETH_USD": 10.0,    # $10 spread max
    "LTC_USD": 2.0,
    "BCH_USD": 5.0,
}

# ATR-based stop/take-profit multipliers (wider than forex)
SL_ATR_MULT = 2.5    # Stop loss: 2.5x ATR
TP_ATR_MULT = 4.0    # Take profit: 4.0x ATR (1:1.67 R:R)

# EMA periods
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

# Minimum confirmations to trigger
MIN_CONFIRMATIONS = 3

# Risk per trade (lower than forex due to wider stops)
RISK_PER_TRADE = 0.005  # 0.5% of balance per trade
MAX_DAILY_LOSS = 0.02   # 2% max daily loss
MAX_OPEN_POSITIONS = 2  # Fewer positions due to larger size


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate EMA."""
    return series.ewm(span=period).mean()


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ATR."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _calc_macd(series: pd.Series):
    """Calculate MACD line, signal, histogram."""
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ADX (simplified)."""
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    plus_dm = np.zeros(len(high))
    minus_dm = np.zeros(len(high))
    tr = np.zeros(len(high))

    for i in range(1, len(high)):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    atr = pd.Series(tr, index=df.index).rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx


@dataclass
class CryptoSignal:
    """Represents a crypto trading signal with full context."""
    instrument: str = ""
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
    spread_price: float = 0.0
    risk_reward_ratio: float = 0.0
    units: int = 0
    risk_amount: float = 0.0
    h4_trend_aligned: Optional[bool] = None
    h4_trend_reason: str = ""


class CryptoSessionFilter:
    """Session filter for crypto — always returns True (24/7 trading)."""

    def is_good_time(self, utc_hour: int = None) -> tuple[bool, str]:
        """Crypto trades 24/7 — always good time."""
        return True, ""


class CryptoSpreadMonitor:
    """Spread monitor with crypto-specific thresholds."""

    def __init__(self):
        self.max_spread = MAX_SPREAD_PRICE

    def check_spread(self, instrument: str, spread: float) -> tuple[bool, str]:
        """
        Check if spread is acceptable for crypto.
        Spread is in price units (e.g., 43.0 for BTC_USD).
        """
        max_spread = self.max_spread.get(instrument, 50.0)
        if spread > max_spread:
            return False, f"Spread too wide: ${spread:.2f} (max ${max_spread:.2f})"
        return True, ""


class CryptoSignalGenerator:
    """
    Crypto-specific signal generator.
    Uses 4 confirmation signals:
    1. EMA trend alignment (price vs 20/50/200 EMA stack)
    2. MACD crossover
    3. RSI momentum / extreme
    4. ADX trend strength filter

    Key differences from forex strategy:
    - No session filter (24/7)
    - Wider stops (2.5x ATR SL, 4x ATR TP)
    - Lower risk per trade (0.5%)
    - Requires 3 of 4 confirmations (stricter)
    - H4 trend alignment as modifier (not gate)
    """

    def __init__(self):
        self.session_filter = CryptoSessionFilter()
        self.spread_monitor = CryptoSpreadMonitor()

    def check_h4_trend(
        self,
        instrument: str,
        direction: str,
        df: pd.DataFrame,
        ema_period: int = 50,
    ) -> tuple[bool, str]:
        """
        Confirm that the H4 trend aligns with the trade direction.
        Uses the provided DataFrame (assumed to be H4 data).
        """
        if df.empty or len(df) < ema_period:
            return True, ""

        h4_ema = _calc_ema(df["close"], ema_period)
        h4_close = df["close"].iloc[-1]
        h4_ema_val = h4_ema.iloc[-1]

        if pd.isna(h4_ema_val):
            return True, ""

        if direction == "BUY":
            if h4_close > h4_ema_val:
                return True, ""
            else:
                return False, f"H4 trend NOT aligned for {instrument} BUY"
        elif direction == "SELL":
            if h4_close < h4_ema_val:
                return True, ""
            else:
                return False, f"H4 trend NOT aligned for {instrument} SELL"

        return True, ""

    def analyze(self, df: pd.DataFrame, current_price: dict, h4_df: pd.DataFrame = None, instrument: str = "") -> CryptoSignal:
        """
        Analyze crypto data and generate signal.

        Args:
            df: DataFrame with OHLCV data (at least 200 bars)
            current_price: Dict with bid/ask/spread from OANDA
            h4_df: Optional H4 DataFrame for trend confirmation
            instrument: Instrument name (e.g. "BTC_USD")

        Returns:
            CryptoSignal with full analysis
        """
        sig = CryptoSignal()
        sig.instrument = instrument

        if len(df) < EMA_SLOW + 10:
            sig.reasons.append("Insufficient data")
            return sig

        # Calculate all indicators
        close = df["close"]
        indicators = self._calculate_indicators(df)
        sig.indicators = indicators

        # Current values
        ema_20 = indicators["ema_20"]
        ema_50 = indicators["ema_50"]
        ema_200 = indicators["ema_200"]
        rsi = indicators["rsi_14"]
        macd_line = indicators["macd"]
        macd_signal_line = indicators["macd_signal"]
        macd_hist = indicators["macd_histogram"]
        prev_macd_hist = indicators.get("prev_macd_histogram", macd_hist)
        atr = indicators["atr_14"]
        adx = indicators["adx_14"]
        current_close = indicators["current_price"]

        sig.atr = atr
        sig.entry_price = current_price["ask"]  # Use ask for buy, bid for sell

        # ── Confirmation 1: EMA Trend Alignment ──
        ema_bullish = (current_close > ema_50 > ema_200) and (ema_20 > ema_50)
        ema_bearish = (current_close < ema_50 < ema_200) and (ema_20 < ema_50)
        ema_score = 0
        if ema_bullish:
            ema_score = 1.5
            sig.direction_fired["ema"] = True
            sig.reasons.append("EMA bullish: price > EMA50 > EMA200")
        elif ema_bearish:
            ema_score = -1.5
            sig.direction_fired["ema"] = True
            sig.reasons.append("EMA bearish: price < EMA50 < EMA200")
        else:
            sig.reasons.append(
                f"EMA mixed: price={'above' if current_close > ema_50 else 'below'} EMA50"
            )

        # ── Confirmation 2: MACD Crossover ──
        macd_score = 0
        if macd_hist > 0 and prev_macd_hist <= 0:
            macd_score = 1.5
            sig.direction_fired["macd"] = True
            sig.reasons.append("MACD bullish crossover")
        elif macd_hist < 0 and prev_macd_hist >= 0:
            macd_score = -1.5
            sig.direction_fired["macd"] = True
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
            sig.direction_fired["rsi"] = True
            sig.reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > RSI_OVERBOUGHT:
            rsi_score = -1.0
            sig.direction_fired["rsi"] = True
            sig.reasons.append(f"RSI overbought ({rsi:.1f})")
        elif rsi < 45:
            rsi_score = -0.3
            sig.reasons.append(f"RSI bearish zone ({rsi:.1f})")
        elif rsi > 55:
            rsi_score = 0.3
            sig.reasons.append(f"RSI bullish zone ({rsi:.1f})")
        else:
            sig.reasons.append(f"RSI neutral ({rsi:.1f})")

        # ── Confirmation 4: ADX Trend Strength ──
        vol_score = 0
        if adx > 25:
            vol_score = 1.0
            sig.direction_fired["adx"] = True
            sig.reasons.append(f"Strong trend (ADX {adx:.1f})")
        elif adx > 15:
            vol_score = 0.5
            sig.reasons.append(f"Developing trend (ADX {adx:.1f})")
        else:
            vol_score = -1.0
            sig.reasons.append(f"Weak/no trend (ADX {adx:.1f})")

        # ── ADX floor: below 15, no trade ──
        if adx < 15:
            sig.signal = "HOLD"
            sig.confidence = 0.0
            sig.score = 0.0
            sig.confirmations = 0
            sig.tier = "NONE"
            sig.reasons.append(f"ADX below floor ({adx:.1f} < 15)")
            return sig

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

        # ── H4 Trend Confirmation (modifier, not gate) ──
        if sig.signal in ("BUY", "SELL") and h4_df is not None:
            aligned, h4_reason = self.check_h4_trend(
                sig.instrument, sig.signal, h4_df
            )
            sig.h4_trend_aligned = aligned
            sig.h4_trend_reason = h4_reason

            if not aligned:
                # Dampen score but don't block
                sig.score = round(sig.score * 0.8, 2)
                sig.reasons.append(f"H4 opposed (×0.8)")
                logger.info(
                    "⚠️ H4 opposed dampener: %s %s | score=%.2f",
                    sig.instrument, sig.signal, sig.score,
                )
            else:
                sig.score = round(sig.score * 1.15, 2)
                sig.reasons.append("H4 aligned (×1.15)")

        # ── Trade Setup (ATR-based) ──
        if sig.signal in ("BUY", "SELL"):
            if sig.signal == "BUY":
                sig.stop_loss = current_close - (atr * SL_ATR_MULT)
                sig.take_profit = current_close + (atr * TP_ATR_MULT)
                sig.entry_price = current_price["ask"]
            else:
                sig.stop_loss = current_close + (atr * SL_ATR_MULT)
                sig.take_profit = current_close - (atr * TP_ATR_MULT)
                sig.entry_price = current_price["bid"]

            sig.risk_reward_ratio = TP_ATR_MULT / SL_ATR_MULT

        # Spread check
        spread = current_price.get("spread", 0)
        sig.spread_price = spread

        return sig

    def _calculate_indicators(self, df: pd.DataFrame) -> dict:
        """Calculate all technical indicators for crypto."""
        close = df["close"]

        ema_20 = _calc_ema(close, EMA_FAST).iloc[-1]
        ema_50 = _calc_ema(close, EMA_MID).iloc[-1]
        ema_200 = _calc_ema(close, EMA_SLOW).iloc[-1]

        rsi = _calc_rsi(close, RSI_PERIOD).iloc[-1]

        macd_line, macd_signal_line, macd_hist = _calc_macd(close)
        macd_hist_val = macd_hist.iloc[-1]
        prev_macd_hist = macd_hist.iloc[-2] if len(macd_hist) > 1 else macd_hist_val

        atr = _calc_atr(df, 14).iloc[-1]
        adx = _calc_adx(df, 14).iloc[-1]

        return {
            "ema_20": round(float(ema_20), 2),
            "ema_50": round(float(ema_50), 2),
            "ema_200": round(float(ema_200), 2),
            "rsi_14": round(float(rsi), 2),
            "atr_14": round(float(atr), 2),
            "adx_14": round(float(adx), 2) if not np.isnan(adx) else 0.0,
            "macd": round(float(macd_line.iloc[-1]), 2),
            "macd_signal": round(float(macd_signal_line.iloc[-1]), 2),
            "macd_histogram": round(float(macd_hist_val), 2),
            "prev_macd_histogram": round(float(prev_macd_hist), 2),
            "current_price": round(float(close.iloc[-1]), 2),
        }


class CryptoRiskManager:
    """Risk management calibrated for crypto's volatility."""

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
        Calculate position size for crypto.
        Returns (units, risk_amount).

        Crypto position sizing: risk 0.5% of balance per trade.
        Units = risk_amount / stop_distance_in_price
        """
        risk_amount = balance * self.risk_per_trade
        stop_distance = abs(entry_price - stop_loss)

        if stop_distance <= 0:
            return 0, 0

        units = int(risk_amount / stop_distance)

        # Cap at reasonable size
        max_units = 5  # Max 5 BTC/ETH units for 100K account
        if units > max_units:
            units = max_units
            risk_amount = units * stop_distance

        # Minimum 1 unit
        units = max(units, 1)

        return units, round(risk_amount, 2)

    def validate_signal(self, signal: CryptoSignal, balance: float) -> tuple[bool, str]:
        """Validate a crypto signal before execution."""
        if signal.signal == "HOLD":
            return False, "No signal"

        if signal.tier == "NONE":
            return False, "Signal tier too weak"

        if signal.confirmations < MIN_CONFIRMATIONS:
            return False, f"Insufficient confirmations: {signal.confirmations}/{MIN_CONFIRMATIONS}"

        spread_max = MAX_SPREAD_PRICE.get(signal.instrument, 50.0)
        if signal.spread_price > spread_max:
            return False, f"Spread too wide: ${signal.spread_price:.2f}"

        if signal.atr < 1.0:
            return False, f"ATR too low: ${signal.atr:.2f}"

        return True, ""
