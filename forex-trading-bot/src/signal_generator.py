"""
Forex Trading Bot - Signal Generator
Combines technical indicators + sentiment into actionable signals.
"""

import logging
from datetime import datetime

from src.oanda_client import OandaClient
from src.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


class SignalGenerator:
    """Generate trading signals from market data + indicators."""

    def __init__(self, client: OandaClient):
        self.client = client
        self.indicators = TechnicalIndicators()

    def analyze(
        self,
        instrument: str = "EUR_USD",
        granularity: str = "H1",
        count: int = 500,
    ) -> dict:
        """
        Full analysis pipeline:
        1. Fetch candles from OANDA
        2. Calculate indicators
        3. Generate signal
        4. Get current price
        """
        logger.info("Analyzing %s %s...", instrument, granularity)

        # Fetch data
        df = self.client.get_candles(instrument, granularity, count)
        if df.empty:
            return {"error": "No data fetched", "instrument": instrument}

        # Add indicators
        df = self.indicators.add_all(df)

        # Generate signal
        signal = self.indicators.generate_signal(df)

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

        # Calculate suggested stop loss / take profit based on ATR
        if "atr_14" in df.columns:
            atr = df.iloc[-1]["atr_14"]
            signal["atr_14"] = round(float(atr), 5)
            if "current_price" in signal:
                mid = (
                    signal["current_price"]["bid"]
                    + signal["current_price"]["ask"]
                ) / 2
                signal["suggested_stop_loss"] = round(
                    mid - 2 * atr, 5
                )
                signal["suggested_take_profit"] = round(
                    mid + 3 * atr, 5
                )
                signal["risk_reward_ratio"] = 1.5

        logger.info(
            "Signal: %s | confidence=%.2f | %s",
            signal["signal"],
            signal["confidence"],
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
