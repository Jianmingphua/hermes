"""
Forex Trading Bot - Signal Generator
Combines technical indicators + sentiment into actionable signals.
Includes multi-timeframe H4 trend confirmation.
"""

import logging
from datetime import datetime

import pandas as pd

from src.oanda_client import OandaClient
from src.indicators import TechnicalIndicators, HAS_TALIB
from src.optimized_params import get_params, get_strategy
from src.support_resistance import support_resistance

logger = logging.getLogger(__name__)


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate EMA — uses ta-lib if available, otherwise pandas fallback."""
    if HAS_TALIB:
        import talib
        return pd.Series(
            talib.EMA(series.values, timeperiod=period),
            index=series.index,
        )
    return series.ewm(span=period).mean()


class SignalGenerator:
    """Generate trading signals from market data + indicators."""

    def __init__(self, client: OandaClient):
        self.client = client
        self.indicators = TechnicalIndicators()

    # ── Multi-Timeframe H4 Trend Check ───────────────────────────

    def check_h4_trend(
        self,
        instrument: str,
        direction: str,
        ema_period: int = 50,
        candle_count: int = 200,
    ) -> tuple[bool, str]:
        """
        Confirm that the H4 trend aligns with the trade direction.

        For BUY:  H4 close must be > H4 EMA50 (bullish trend).
        For SELL: H4 close must be < H4 EMA50 (bearish trend).

        This is the highest-impact filter — avoids counter-trend entries.

        Args:
            instrument:  Pair name, e.g. "EUR_USD"
            direction:   "BUY" or "SELL"
            ema_period:  EMA period on H4 (default 50)
            candle_count: Number of H4 candles to fetch

        Returns:
            (is_aligned, reason) — reason is empty if aligned
        """
        try:
            h4_df = self.client.get_candles(
                instrument, granularity="H4", count=candle_count
            )
        except Exception as e:
            logger.warning(
                "H4 trend check failed for %s (fetch error: %s) — allowing", instrument, e
            )
            # Fail-open: if we can't fetch H4 data, don't block the trade
            return True, ""

        if h4_df.empty or len(h4_df) < ema_period:
            logger.warning(
                "H4 trend check: insufficient data for %s (%d candles) — allowing",
                instrument, len(h4_df),
            )
            return True, ""

        h4_ema = _calc_ema(h4_df["close"], ema_period)
        h4_close = h4_df["close"].iloc[-1]
        h4_ema_val = h4_ema.iloc[-1]

        if pd.isna(h4_ema_val):
            logger.warning(
                "H4 trend check: EMA is NaN for %s — allowing", instrument
            )
            return True, ""

        if direction == "BUY":
            if h4_close > h4_ema_val:
                logger.info(
                    "✅ H4 trend aligned for %s BUY: close=%.5f > H4_EMA%d=%.5f",
                    instrument, h4_close, ema_period, h4_ema_val,
                )
                return True, ""
            else:
                reason = (
                    f"⛔ H4 trend NOT aligned for {instrument} BUY: "
                    f"close={h4_close:.5f} ≤ H4_EMA{ema_period}={h4_ema_val:.5f} "
                    f"(counter-trend)"
                )
                logger.info(reason)
                return False, reason

        elif direction == "SELL":
            if h4_close < h4_ema_val:
                logger.info(
                    "✅ H4 trend aligned for %s SELL: close=%.5f < H4_EMA%d=%.5f",
                    instrument, h4_close, ema_period, h4_ema_val,
                )
                return True, ""
            else:
                reason = (
                    f"⛔ H4 trend NOT aligned for {instrument} SELL: "
                    f"close={h4_close:.5f} ≥ H4_EMA{ema_period}={h4_ema_val:.5f} "
                    f"(counter-trend)"
                )
                logger.info(reason)
                return False, reason

        # Unknown direction — allow
        return True, ""

    # ── Main Analysis Pipeline ───────────────────────────────────

    def analyze(
        self,
        instrument: str = "EUR_USD",
        granularity: str = None,
        count: int = 500,
    ) -> dict:
        """
        Full analysis pipeline:
        1. Fetch candles from OANDA (using per-pair optimized granularity)
        2. Calculate indicators
        3. Generate signal
        4. Get current price
        5. Multi-timeframe H4 trend confirmation
        """
        # Use per-pair optimized parameters
        params = get_params(instrument)
        strategy = get_strategy(instrument)  # Per-pair strategy config
        if granularity is None:
            granularity = params.get("granularity", "M15")

        logger.info("Analyzing %s %s...", instrument, granularity)

        # Fetch data
        df = self.client.get_candles(instrument, granularity, count)
        if df.empty:
            return {"error": "No data fetched", "instrument": instrument}

        # Add indicators
        df = self.indicators.add_all(df)

        # Generate signal with per-pair params
        signal = self.indicators.generate_signal(
            df,
            min_conf=params["min_conf"],
            rsi_ob=params["rsi_ob"],
            rsi_os=params["rsi_os"],
            signal_threshold_strong=strategy.get("signal_threshold_strong", 2.0),
            signal_threshold_medium=strategy.get("signal_threshold_medium", 1.5),
            signal_threshold_weak=strategy.get("signal_threshold_weak", 0.8),
            adx_floor=strategy.get("adx_floor", 20),
            kalman_enabled=strategy.get("kalman_enabled", False),
            kalman_velocity_threshold=strategy.get("kalman_velocity_threshold", 0.00005),
            kalman_confidence_threshold=strategy.get("kalman_confidence_threshold", 0.3),
        )

        # Get current price
        try:
            price = self.client.get_current_price(instrument)
            signal["current_price"] = price
        except Exception as e:
            logger.warning("Could not fetch current price: %s", e)

        # Add metadata
        signal["instrument"] = instrument
        signal["granularity"] = granularity
        signal["candles_analyzed"] = len(df)
        signal["analyzed_at"] = datetime.utcnow().isoformat()

        # ── Multi-timeframe H4 trend confirmation ──
        # All pairs use gate mode (h4_must_align=True): HOLD if H4 opposes
        # Additional: when H4 opposes and confidence is high (≥0.7), cap the score
        # to prevent strong counter-trend entries
        sig_type = signal.get("signal", "HOLD")
        h4_must_align = strategy.get("h4_must_align", True)
        if sig_type in ("BUY", "SELL"):
            aligned, h4_reason = self.check_h4_trend(instrument, sig_type)
            signal["h4_trend_aligned"] = aligned
            signal["h4_trend_reason"] = h4_reason

            if not aligned:
                if h4_must_align:
                    # Gate mode: block entirely
                    logger.info(
                        "🔒 H4 must_align BLOCKED %s %s (counter-trend)",
                        instrument, sig_type,
                    )
                    signal["signal"] = "HOLD"
                    signal["confidence"] = 0.0
                    signal["score"] = 0.0
                    signal["reasons"].append("H4 must_align: BLOCKED")
                    sig_type = "HOLD"
                else:
                    # Modifier mode (fallback): dampen score
                    h4_opposed_mult = strategy.get("h4_opposed_mult", 0.85)
                    signal["score"] = round(signal["score"] * h4_opposed_mult, 2)
                    signal["reasons"].append(f"H4 opposed (×{h4_opposed_mult})")
                    logger.info(
                        "⚠️ H4 opposed dampener: %s %s | score=%.2f",
                        instrument, sig_type, signal["score"],
                    )
            else:
                if h4_must_align:
                    signal["reasons"].append("H4 must_align: ✅")
                else:
                    h4_aligned_mult = strategy.get("h4_aligned_mult", 1.15)
                    signal["score"] = round(signal["score"] * h4_aligned_mult, 2)
                    signal["reasons"].append(f"H4 aligned (×{h4_aligned_mult})")
                logger.info(
                    "✅ H4 trend aligned: %s %s",
                    instrument, sig_type,
                )
        else:
            signal["h4_trend_aligned"] = None
            signal["h4_trend_reason"] = ""

        # ── Confidence cap for H4-opposed high-confidence signals ──
        # Even if signal passed H4 gate (e.g. fail-open due to missing data),
        # cap confidence when H4 data was available but opposed
        if (sig_type in ("BUY", "SELL")
                and signal.get("h4_trend_aligned") == False
                and signal.get("confidence", 0) >= 0.7):
            logger.info(
                "⚠️ High confidence (%.2f) but H4 opposed — capping score at 1.5",
                signal["confidence"],
            )
            signal["score"] = min(abs(signal["score"]), 1.5) * (1 if signal["score"] > 0 else -1)
            signal["reasons"].append("H4 opposed: score capped at 1.5")

        # ── Support/Resistance proximity check ────────────────────
        # Additional non-TA layer: avoid entering into established S/R zones.
        # Controlled by sr_enabled per-pair strategy flag.
        if (
            sig_type in ("BUY", "SELL")
            and "current_price" in signal
            and strategy.get("sr_enabled", True)
        ):
            mid_price = (
                signal["current_price"]["bid"]
                + signal["current_price"]["ask"]
            ) / 2
            atr = signal.get("atr_14", None)
            sr_modifier, sr_reason = support_resistance.get_modifier(
                instrument=instrument,
                current_price=mid_price,
                direction=sig_type,
                atr=atr,
            )
            if sr_modifier != 0:
                old_score = signal["score"]
                signal["score"] = round(signal["score"] + sr_modifier, 2)
                if sr_reason:
                    signal["reasons"].append(sr_reason)
                logger.info(
                    "📊 S/R modifier: %s %s | score=%.2f → %.2f",
                    instrument, sig_type, old_score, signal["score"],
                )

        # Calculate suggested stop loss / take profit based on ATR with per-pair multipliers
        if "atr_14" in df.columns:
            atr = df.iloc[-1]["atr_14"]
            signal["atr_14"] = round(float(atr), 5)
            if "current_price" in signal:
                mid = (
                    signal["current_price"]["bid"]
                    + signal["current_price"]["ask"]
                ) / 2
                sl_mult = params["sl_mult"]
                tp_mult = params["tp_mult"]
                if signal["signal"] == "BUY":
                    signal["suggested_stop_loss"] = round(mid - sl_mult * atr, 5)
                    signal["suggested_take_profit"] = round(mid + tp_mult * atr, 5)
                elif signal["signal"] == "SELL":
                    signal["suggested_stop_loss"] = round(mid + sl_mult * atr, 5)
                    signal["suggested_take_profit"] = round(mid - tp_mult * atr, 5)
                else:
                    signal["suggested_stop_loss"] = round(mid - sl_mult * atr, 5)
                    signal["suggested_take_profit"] = round(mid + tp_mult * atr, 5)
                signal["risk_reward_ratio"] = round(tp_mult / sl_mult, 2)

        logger.info(
            "Signal: %s | confidence=%.2f | H4_aligned=%s | %s",
            signal["signal"],
            signal["confidence"],
            signal.get("h4_trend_aligned"),
            " | ".join(signal["reasons"][:3]),
        )
        return signal

    def scan_pairs(
        self,
        instruments: list[str] | None = None,
        granularity: str = "H1",
    ) -> list[dict]:
        """Scan multiple pairs and return signals sorted by confidence."""
        if instruments is None:
            instruments = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"]

        results = []
        for inst in instruments:
            try:
                signal = self.analyze(inst, granularity)
                results.append(signal)
            except Exception as e:
                logger.error("Error analyzing %s: %s", inst, e)
                results.append({"instrument": inst, "error": str(e)})

        # Sort by confidence (highest first)
        results.sort(
            key=lambda x: x.get("confidence", 0),
            reverse=True,
        )
        return results
