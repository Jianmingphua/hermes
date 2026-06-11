"""
Forex Trading Bot - Technical Indicators
Uses ta-lib (C-based, fast) with pandas fallbacks.
"""

import logging

import numpy as np
import pandas as pd

from src.kalman_filter import KalmanFilterEstimator, HAS_PYKALMAN

logger = logging.getLogger(__name__)

# Try ta-lib, fall back to manual implementations
try:
    import talib
    HAS_TALIB = True
    logger.info("Using ta-lib for indicators")
except ImportError:
    HAS_TALIB = False
    logger.warning("ta-lib not available, using manual implementations")


class TechnicalIndicators:
    """Calculate technical indicators on OHLCV DataFrames."""

    @staticmethod
    def add_all(df: pd.DataFrame) -> pd.DataFrame:
        """Add a comprehensive set of indicators to the DataFrame."""
        if df.empty:
            return df

        required = ["open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        open_ = df["open"].values
        volume = df["volume"].values

        if HAS_TALIB:
            # ── Trend ──
            df["ema_20"] = talib.EMA(close, timeperiod=20)
            df["ema_50"] = talib.EMA(close, timeperiod=50)
            df["ema_200"] = talib.EMA(close, timeperiod=200)
            df["sma_20"] = talib.SMA(close, timeperiod=20)

            # ── MACD ──
            macd, macd_signal, macd_hist = talib.MACD(
                close, fastperiod=12, slowperiod=26, signalperiod=9
            )
            df["macd"] = macd
            df["macd_signal"] = macd_signal
            df["macd_hist"] = macd_hist

            # ── RSI ──
            df["rsi_14"] = talib.RSI(close, timeperiod=14)

            # ── Bollinger Bands ──
            upper, middle, lower = talib.BBANDS(
                close, timeperiod=20, nbdevup=2, nbdevdn=2
            )
            df["bb_upper"] = upper
            df["bb_middle"] = middle
            df["bb_lower"] = lower

            # ── ATR ──
            df["atr_14"] = talib.ATR(high, low, close, timeperiod=14)

            # ── Stochastic ──
            slowk, slowd = talib.STOCH(
                high, low, close,
                fastk_period=14, slowk_period=3, slowd_period=3,
            )
            df["stoch_k"] = slowk
            df["stoch_d"] = slowd

            # ── ADX ──
            df["adx_14"] = talib.ADX(high, low, close, timeperiod=14)

        else:
            # ── Manual implementations ──
            df["ema_20"] = df["close"].ewm(span=20).mean()
            df["ema_50"] = df["close"].ewm(span=50).mean()
            df["ema_200"] = df["close"].ewm(span=200).mean()
            df["sma_20"] = df["close"].rolling(20).mean()

            # MACD
            ema12 = df["close"].ewm(span=12).mean()
            ema26 = df["close"].ewm(span=26).mean()
            df["macd"] = ema12 - ema26
            df["macd_signal"] = df["macd"].ewm(span=9).mean()
            df["macd_hist"] = df["macd"] - df["macd_signal"]

            # RSI
            delta = df["close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            df["rsi_14"] = 100 - (100 / (1 + rs))

            # Bollinger Bands
            sma20 = df["close"].rolling(20).mean()
            std20 = df["close"].rolling(20).std()
            df["bb_upper"] = sma20 + 2 * std20
            df["bb_middle"] = sma20
            df["bb_lower"] = sma20 - 2 * std20

            # ATR
            tr1 = df["high"] - df["low"]
            tr2 = (df["high"] - df["close"].shift()).abs()
            tr3 = (df["low"] - df["close"].shift()).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df["atr_14"] = tr.rolling(14).mean()

            # Stochastic
            low14 = df["low"].rolling(14).min()
            high14 = df["high"].rolling(14).max()
            df["stoch_k"] = 100 * (df["close"] - low14) / (high14 - low14)
            df["stoch_d"] = df["stoch_k"].rolling(3).mean()

            # ADX (simplified)
            df["adx_14"] = 25.0  # placeholder

        # ── VWAP (manual, session-based) ──
        df["vwap"] = (
            (df["close"] * df["volume"]).cumsum() / df["volume"].cumsum()
        )

        logger.info(
            "Added indicators | %d columns | ta-lib=%s",
            len(df.columns),
            HAS_TALIB,
        )
        return df

    @staticmethod
    def generate_signal(
        df: pd.DataFrame,
        min_conf: int = 2,
        rsi_ob: int = 70,
        rsi_os: int = 30,
        signal_threshold_strong: float = 2.0,
        signal_threshold_medium: float = 1.5,
        signal_threshold_weak: float = 0.8,
        adx_floor: float = 20,
        kalman_enabled: bool = False,
        kalman_velocity_threshold: float = 0.00005,
        kalman_confidence_threshold: float = 0.3,
    ) -> dict:
        """
        Generate a trading signal with multi-confirmation scoring.
        Uses per-pair strategy parameters (from STRATEGY_CONFIGS).

        Requires at least min_conf of 4 major signal categories to agree:
          1. EMA (crossover)
          2. MACD (crossover)
          3. RSI (extreme)
          4. Bollinger Band (touch)

        Signal thresholds (per-pair configurable):
          Strong entry: score >= signal_threshold_strong AND confirmations >= min_conf
          Medium entry: score >= signal_threshold_medium AND confirmations >= 1
          Weak entry:   score >= signal_threshold_weak AND confirmations >= 1
        
        ADX floor: if ADX < adx_floor, signal is downgraded to HOLD.

        Confidence is calibrated by confirmation count and signal strength.

        Returns:
            dict: signal, confidence, score, confirmations, tier, reasons, indicators
        """
        if df.empty or len(df) < 50:
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "score": 0.0,
                "confirmations": 0,
                "tier": "NONE",
                "reasons": ["Insufficient data"],
                "indicators": {},
            }

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        reasons = []

        # ── Track which major categories fired ──
        ema_fired = False
        macd_fired = False
        rsi_fired = False
        bb_fired = False
        kalman_fired = False

        # Weighted score (keeps magnitude for confidence)
        raw_score = 0.0

        # ── 1. EMA (weight: strong) ──
        ema_contribution = 0.0
        ema_position = False
        if "ema_20" in df.columns and "ema_50" in df.columns:
            # Crossover (strongest signal)
            if latest["ema_20"] > latest["ema_50"] and prev["ema_20"] <= prev["ema_50"]:
                ema_contribution = 3.0
                reasons.append("EMA 20/50 bullish crossover")
                ema_fired = True
            elif latest["ema_20"] < latest["ema_50"] and prev["ema_20"] >= prev["ema_50"]:
                ema_contribution = -3.0
                reasons.append("EMA 20/50 bearish crossover")
                ema_fired = True
            elif latest["ema_20"] > latest["ema_50"]:
                ema_contribution = 1.0
                reasons.append("EMA 20 > 50 (bullish)")
                ema_position = True
            elif latest["ema_20"] < latest["ema_50"]:
                ema_contribution = -1.0
                reasons.append("EMA 20 < 50 (bearish)")
                ema_position = True
        raw_score += ema_contribution

        # ── 2. 200 EMA Trend Filter (modifies score, not a standalone signal) ──
        if "ema_200" in df.columns:
            if latest["close"] > latest["ema_200"]:
                raw_score += 0.5
                reasons.append("Price above 200 EMA")
            else:
                raw_score -= 0.5
                reasons.append("Price below 200 EMA")

        # ── 3. MACD (weight: strong) ──
        macd_contribution = 0.0
        macd_position = False
        if "macd" in df.columns and "macd_signal" in df.columns:
            if (latest["macd"] > latest["macd_signal"]
                    and prev["macd"] <= prev["macd_signal"]):
                macd_contribution = 2.5
                reasons.append("MACD bullish crossover")
                macd_fired = True
            elif (latest["macd"] < latest["macd_signal"]
                    and prev["macd"] >= prev["macd_signal"]):
                macd_contribution = -2.5
                reasons.append("MACD bearish crossover")
                macd_fired = True
            elif latest["macd"] > latest["macd_signal"]:
                macd_contribution = 0.5
                reasons.append("MACD above signal")
                macd_position = True
            else:
                macd_contribution = -0.5
                reasons.append("MACD below signal")
                macd_position = True
        raw_score += macd_contribution

        # ── 4. RSI (weight: moderate) ──
        # Uses per-pair optimized overbought/oversold thresholds
        # For M15: bands widened vs H4 since RSI stays in trend longer on lower TFs
        rsi_contribution = 0.0
        rsi_position = False
        if "rsi_14" in df.columns:
            rsi = latest["rsi_14"]
            if pd.notna(rsi):
                if rsi < rsi_os - 5:  # Deeply oversold (5 below threshold)
                    rsi_contribution = 2.5
                    reasons.append(f"RSI deeply oversold ({rsi:.1f})")
                    rsi_fired = True
                elif rsi < rsi_os:
                    rsi_contribution = 1.5
                    reasons.append(f"RSI oversold ({rsi:.1f})")
                    rsi_fired = True
                elif rsi > rsi_ob + 10:  # Deeply overbought (wider band for M15)
                    rsi_contribution = -2.5
                    reasons.append(f"RSI deeply overbought ({rsi:.1f})")
                    rsi_fired = True
                elif rsi > rsi_ob + 5:  # Moderately overbought (wider band)
                    rsi_contribution = -1.0
                    reasons.append(f"RSI overbought ({rsi:.1f})")
                    rsi_fired = True
                elif rsi < 40:
                    rsi_contribution = -0.5
                    reasons.append(f"RSI bearish zone ({rsi:.1f})")
                    rsi_position = True
                elif rsi > 60:
                    rsi_contribution = 0.5
                    reasons.append(f"RSI bullish zone ({rsi:.1f})")
                    rsi_position = True
        raw_score += rsi_contribution

        # ── 5. Bollinger Bands (weight: moderate) ──
        bb_contribution = 0.0
        bb_position = False
        if "bb_lower" in df.columns and "bb_upper" in df.columns:
            if latest["close"] <= latest["bb_lower"]:
                bb_contribution = 2.0
                reasons.append("Price at lower BB")
                bb_fired = True
            elif latest["close"] >= latest["bb_upper"]:
                bb_contribution = -2.0
                reasons.append("Price at upper BB")
                bb_fired = True
            elif latest["close"] <= latest["bb_middle"]:
                bb_contribution = -0.3
                reasons.append("Price below BB middle")
                bb_position = True
            elif latest["close"] > latest["bb_middle"]:
                bb_contribution = 0.3
                reasons.append("Price above BB middle")
                bb_position = True
        raw_score += bb_contribution

        # ── 6. ADX (modifier, not a standalone signal) ──
        adx_val = 0.0
        if "adx_14" in df.columns:
            adx_val = latest["adx_14"]
            if pd.notna(adx_val):
                if adx_val > 30:
                    reasons.append(f"Strong trend (ADX {adx_val:.1f})")
                    raw_score *= 1.2
                elif adx_val > 15:
                    reasons.append(f"Developing trend (ADX {adx_val:.1f})")
                else:
                    reasons.append(f"Range-bound (ADX {adx_val:.1f})")
                    # Very weak dampener for M15 — we want trades even in ranging markets
                    raw_score *= 0.95
                # ADX floor: below this, no signal (per-pair configurable)
                if adx_val < adx_floor:
                    reasons.append(f"ADX below floor ({adx_val:.1f} < {adx_floor})")
                    return {
                        "signal": "HOLD",
                        "confidence": 0.0,
                        "score": 0.0,
                        "confirmations": 0,
                        "tier": "NONE",
                        "reasons": reasons,
                        "indicators": {},
                    }

        # ── 7. Kalman Filter Trend Estimation ──
        kalman_score = 0.0
        kalman_signal = "HOLD"
        if kalman_enabled and HAS_PYKALMAN:
            try:
                kf_estimator = KalmanFilterEstimator(
                    velocity_threshold=kalman_velocity_threshold,
                    confidence_threshold=kalman_confidence_threshold,
                )
                kf_result = kf_estimator.analyze(df["close"])
                kalman_score = kf_result.get("kalman_score", 0.0)
                kalman_signal = kf_result.get("kalman_signal", "HOLD")
                kalman_fired = kf_result.get("kalman_fired", False)

                if kalman_fired:
                    raw_score += kalman_score
                    if kalman_signal == "BUY":
                        reasons.append(
                            f"Kalman trend UP (v={kf_result['kalman_velocity']:.6f}, "
                            f"conf={kf_result['kalman_confidence']:.2f})"
                        )
                    elif kalman_signal == "SELL":
                        reasons.append(
                            f"Kalman trend DOWN (v={kf_result['kalman_velocity']:.6f}, "
                            f"conf={kf_result['kalman_confidence']:.2f})"
                        )
                else:
                    reasons.append("Kalman: no clear trend")
            except Exception as e:
                logger.warning("Kalman filter error: %s", e)
                kalman_fired = False

        # ── Count confirmations ──
        # Full confirmations: crossovers, extremes, band touches (weight 1.0 each)
        # Half confirmations: positioned signals (EMA positioned, MACD positioned,
        #   RSI in zone, BB near middle) — weight 0.5 each
        # Kalman: counts as a full confirmation when it fires (trend + confidence)
        full_confs = sum([ema_fired, macd_fired, rsi_fired, bb_fired, kalman_fired])
        half_confs = sum([ema_position, macd_position, rsi_position, bb_position]) * 0.5
        confirmations = full_confs + half_confs

        # ── Determine signal (M15-optimized thresholds) ──
        # With tightened confirmations and min_conf=2:
        #   Strong entry (≥2.0 score + ≥2 confirmations): 2+ major signals agree
        #   Medium entry (≥1.5 score + ≥1 confirmation):  1 major + positioned support
        #   Weak entry  (≥0.8 score + ≥1 confirmation):   marginal, high-risk
        if raw_score >= signal_threshold_strong and confirmations >= min_conf:
            signal = "BUY"
        elif raw_score <= -signal_threshold_strong and confirmations >= min_conf:
            signal = "SELL"
        elif raw_score >= signal_threshold_medium and confirmations >= 1:
            signal = "BUY"
        elif raw_score <= -signal_threshold_medium and confirmations >= 1:
            signal = "SELL"
        elif raw_score >= signal_threshold_weak and confirmations >= 1:
            signal = "BUY"
        elif raw_score <= -signal_threshold_weak and confirmations >= 1:
            signal = "SELL"
        else:
            signal = "HOLD"

        # ── Calculate confidence ──
        # Base: from absolute score (max ~8.0 with all signals)
        base_confidence = min(abs(raw_score) / 7.0, 1.0)

        # Confirmation bonus: 0 confirmations = 0, 1 = +0.1, 2 = +0.2, 3 = +0.3, 4 = +0.4
        confirmation_bonus = confirmations * 0.1
        confidence = min(base_confidence + confirmation_bonus, 1.0)

        # ── Quality tier ──
        # With half-confirmations: 4 fired = 4.0 (HIGH), 2+ fired or 4+ half = MEDIUM,
        # 1+ fired or 2+ half = LOW
        if confirmations >= 3:
            tier = "HIGH"
        elif confirmations >= 1.5:
            tier = "MEDIUM"
        elif confirmations >= 0.5:
            tier = "LOW"
        else:
            tier = "NONE"

        # ── Collect indicator values ──
        indicators = {}
        for col in ["ema_20", "ema_50", "ema_200", "rsi_14", "atr_14", "adx_14"]:
            if col in df.columns and pd.notna(latest[col]):
                indicators[col] = round(float(latest[col]), 5)

        return {
            "signal": signal,
            "confidence": round(confidence, 3),
            "score": round(raw_score, 2),
            "confirmations": confirmations,
            "tier": tier,
            "direction_fired": {
                "ema": ema_fired,
                "macd": macd_fired,
                "rsi": rsi_fired,
                "bb": bb_fired,
                "kalman": kalman_fired,
            },
            "reasons": reasons,
            "indicators": indicators,
            "timestamp": str(df.index[-1]),
        }
