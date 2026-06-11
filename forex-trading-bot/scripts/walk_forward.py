"""
Forex Trading Bot - Walk-Forward Optimization
===============================================
Avoids overfitting by re-optimizing parameters on rolling windows.

Process:
1. Split historical data into N windows (e.g., 8 windows of 500 candles each)
2. For each window:
   a. Optimize parameters on the training portion (first 80%)
   b. Validate on the out-of-sample portion (last 20%)
3. Select parameters that perform consistently across windows
4. Use the most recent window's optimal params for live trading

This is the gold standard for avoiding overfitted backtest results.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import backtrader as bt
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.oanda_client import OandaClient
from src.indicators import TechnicalIndicators

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Backtrader Strategy ──────────────────────────────────────────

class WalkForwardStrategy(bt.Strategy):
    """Strategy that accepts configurable parameters for walk-forward testing."""

    params = (
        ("min_conf", 2),
        ("sl_mult", 1.5),
        ("tp_mult", 2.5),
        ("rsi_ob", 70),
        ("rsi_os", 30),
        ("adx_thresh", 25),
    )

    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.order = None
        self.entry_price = None
        self.entry_date = None
        self.trades = []
        self.wins = 0
        self.losses = 0

    def next(self):
        if self.order:
            return

        # Build DataFrame from current data
        df = self._build_df()
        if len(df) < 50:
            return

        df = self.indicators.add_all(df)
        signal = self.indicators.generate_signal(
            df,
            min_conf=self.p.min_conf,
            rsi_ob=self.p.rsi_ob,
            rsi_os=self.p.rsi_os,
        )

        sig_type = signal.get("signal", "HOLD")
        conf = signal.get("confidence", 0)
        confs = signal.get("confirmations", 0)

        if sig_type == "BUY" and conf >= 0.4 and confs >= self.p.min_conf:
            if not self.position:
                size = self._calc_size(signal)
                if size > 0:
                    self.order = self.buy(size=size)
                    self.entry_price = self.data.close[0]
                    self.entry_date = self.data.datetime.date(0)
        elif sig_type == "SELL" and conf >= 0.4 and confs >= self.p.min_conf:
            if not self.position:
                size = self._calc_size(signal)
                if size > 0:
                    self.order = self.sell(size=size)
                    self.entry_price = self.data.close[0]
                    self.entry_date = self.data.datetime.date(0)

    def _calc_size(self, signal):
        atr = signal.get("atr_14", 0)
        if atr <= 0:
            return 0
        risk_amount = self.broker.getcash() * 0.01
        stop_dist = self.p.sl_mult * atr
        if stop_dist <= 0:
            return 0
        return int(risk_amount / stop_dist)

    def _build_df(self):
        """Build a DataFrame from the current data feed."""
        data = {
            "date": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
        for i in range(len(self.data)):
            data["date"].append(self.data.datetime.date(-i))
            data["open"].append(self.data.open(-i))
            data["high"].append(self.data.high(-i))
            data["low"].append(self.data.low(-i))
            data["close"].append(self.data.close(-i))
            data["volume"].append(self.data.volume(-i))
        df = pd.DataFrame(data).iloc[::-1].reset_index(drop=True)
        return df

    def notify_order(self, order):
        if order.status in [order.Completed]:
            if order.isbuy():
                sl = order.executed.price - self.p.sl_mult * 0.001
                tp = order.executed.price + self.p.tp_mult * 0.001
            else:
                sl = order.executed.price + self.p.sl_mult * 0.001
                tp = order.executed.price - self.p.tp_mult * 0.001
        self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            pnl = trade.pnlcomm
            self.trades.append(pnl)
            if pnl > 0:
                self.wins += 1
            else:
                self.losses += 1


# ── Walk-Forward Engine ──────────────────────────────────────────

class WalkForwardOptimizer:
    """
    Performs walk-forward optimization across multiple time windows.
    """

    def __init__(
        self,
        instrument: str = "EUR_USD",
        granularity: str = "H1",
        total_candles: int = 4000,
        n_windows: int = 8,
        train_ratio: float = 0.8,
        output_dir: str = "optimization_results",
    ):
        self.instrument = instrument
        self.granularity = granularity
        self.total_candles = total_candles
        self.n_windows = n_windows
        self.train_ratio = train_ratio
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = OandaClient()

    def fetch_data(self) -> pd.DataFrame:
        """Fetch all historical data."""
        logger.info("Fetching %d %s candles for %s...", self.total_candles, self.granularity, self.instrument)
        df = self.client.get_candles(self.instrument, self.granularity, self.total_candles)
        logger.info("Fetched %d candles", len(df))
        return df

    def get_windows(self, df: pd.DataFrame) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
        """Split data into train/test windows."""
        window_size = len(df) // self.n_windows
        train_size = int(window_size * self.train_ratio)
        windows = []
        for i in range(self.n_windows):
            start = i * window_size
            end = start + window_size
            if end > len(df):
                break
            train = df.iloc[start : start + train_size]
            test = df.iloc[start + train_size : end]
            windows.append((train, test))
        logger.info("Created %d windows (train=%d, test=%d each)", len(windows), train_size, window_size - train_size)
        return windows

    def optimize_window(self, train_df: pd.DataFrame) -> dict:
        """Find best parameters for a single training window."""
        # Test a grid of min_conf values
        best_score = -999
        best_params = {"min_conf": 2, "sl_mult": 1.5, "tp_mult": 2.5}

        for min_conf in [2, 3, 4]:
            for sl_mult in [1.0, 1.5, 2.0]:
                for tp_mult in [2.0, 2.5, 3.0]:
                    score = self._evaluate_params(train_df, min_conf, sl_mult, tp_mult)
                    if score > best_score:
                        best_score = score
                        best_params = {
                            "min_conf": min_conf,
                            "sl_mult": sl_mult,
                            "tp_mult": tp_mult,
                            "score": score,
                        }

        return best_params

    def _evaluate_params(self, df: pd.DataFrame, min_conf: int, sl_mult: float, tp_mult: float) -> float:
        """Evaluate a parameter set on a DataFrame. Returns Sharpe-like score."""
        ind = TechnicalIndicators()
        df = ind.add_all(df)

        trades = []
        position = None
        entry_price = 0

        for i in range(50, len(df)):
            signal = ind.generate_signal(df.iloc[:i+1], min_conf=min_conf)
            sig_type = signal.get("signal", "HOLD")
            conf = signal.get("confidence", 0)
            confs = signal.get("confirmations", 0)
            atr = df.iloc[i].get("atr_14", 0)
            close = df.iloc[i]["close"]

            if position is None:
                if sig_type in ("BUY", "SELL") and conf >= 0.4 and confs >= min_conf:
                    position = sig_type
                    entry_price = close
                    sl = entry_price - sl_mult * atr if sig_type == "BUY" else entry_price + sl_mult * atr
                    tp = entry_price + tp_mult * atr if sig_type == "BUY" else entry_price - tp_mult * atr
            else:
                # Check SL/TP
                if position == "BUY":
                    if close <= sl:
                        trades.append(close - entry_price)
                        position = None
                    elif close >= tp:
                        trades.append(close - entry_price)
                        position = None
                elif position == "SELL":
                    if close >= sl:
                        trades.append(entry_price - close)
                        position = None
                    elif close <= tp:
                        trades.append(entry_price - close)
                        position = None

        if not trades:
            return -999

        total_pnl = sum(trades)
        wins = len([t for t in trades if t > 0])
        win_rate = wins / len(trades)
        avg_win = sum(t for t in trades if t > 0) / max(wins, 1)
        avg_loss = sum(t for t in trades if t < 0) / max(len(trades) - wins, 1)

        # Profit factor
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / max(gross_loss, 0.001)

        # Composite score
        score = total_pnl * 0.3 + win_rate * 100 * 0.25 + profit_factor * 10 * 0.25 + avg_win * 10 * 0.2
        return score

    def run(self) -> dict:
        """Run full walk-forward optimization."""
        logger.info("=" * 60)
        logger.info("Walk-Forward Optimization: %s %s", self.instrument, self.granularity)
        logger.info("=" * 60)

        df = self.fetch_data()
        windows = self.get_windows(df)

        results = []
        for i, (train, test) in enumerate(windows):
            logger.info("Window %d/%d: train=%d test=%d", i + 1, len(windows), len(train), len(test))

            # Optimize on training data
            best = self.optimize_window(train)
            logger.info("  Best train params: min_conf=%d sl=%.1f tp=%.1f score=%.2f",
                        best["min_conf"], best["sl_mult"], best["tp_mult"], best["score"])

            # Validate on out-of-sample test data
            test_score = self._evaluate_params(test, best["min_conf"], best["sl_mult"], best["tp_mult"])
            logger.info("  Test score: %.2f", test_score)

            results.append({
                "window": i + 1,
                "train_params": best,
                "test_score": test_score,
                "train_size": len(train),
                "test_size": len(test),
            })

        # Summary
        avg_test_score = sum(r["test_score"] for r in results) / len(results)
        consistent = len([r for r in results if r["test_score"] > 0]) / len(results)

        # Most recent window's params (for live trading)
        latest_params = results[-1]["train_params"] if results else {}

        summary = {
            "instrument": self.instrument,
            "granularity": self.granularity,
            "n_windows": len(results),
            "avg_test_score": round(avg_test_score, 2),
            "consistency": round(consistent * 100, 1),
            "recommended_params": latest_params,
            "window_results": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Save results
        out_file = self.output_dir / f"walkforward_{self.instrument}_{self.granularity}_{datetime.now():%Y%m%d_%H%M%S}.json"
        out_file.write_text(json.dumps(summary, indent=2, default=str))
        logger.info("Results saved to %s", out_file)
        logger.info("Recommended params: %s", latest_params)
        logger.info("Consistency: %.0f%% (%.1f avg test score)", consistent * 100, avg_test_score)

        return summary


def main():
    """Run walk-forward optimization for all pairs."""
    import argparse
    parser = argparse.ArgumentParser(description="Walk-Forward Optimization")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="H1")
    parser.add_argument("--candles", type=int, default=4000)
    parser.add_argument("--windows", type=int, default=8)
    args = parser.parse_args()

    optimizer = WalkForwardOptimizer(
        instrument=args.instrument,
        granularity=args.granularity,
        total_candles=args.candles,
        n_windows=args.windows,
    )
    result = optimizer.run()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
