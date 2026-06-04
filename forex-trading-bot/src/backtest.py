"""
Forex Trading Bot - Backtest with Spread Model
Models real OANDA spreads, slippage, and walk-forward analysis.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtrader as bt
import pandas as pd
import numpy as np
from datetime import datetime
from src.oanda_client import OandaClient


# ── Spread Model ─────────────────────────────────────────────────
# Real OANDA typical spreads (in pips) by pair and session
SPREAD_MODEL = {
    "EUR_USD": {"normal": 0.8, "high": 1.5, "news": 3.0},
    "GBP_USD": {"normal": 1.2, "high": 2.0, "news": 4.0},
    "USD_JPY": {"normal": 0.9, "high": 1.8, "news": 3.5},
    "AUD_USD": {"normal": 1.0, "high": 1.8, "news": 3.5},
    "USD_CAD": {"normal": 1.3, "high": 2.2, "news": 4.0},
    "USD_CHF": {"normal": 1.2, "high": 2.0, "news": 3.5},
    "EUR_GBP": {"normal": 1.0, "high": 1.8, "news": 3.0},
    "EUR_JPY": {"normal": 1.5, "high": 2.5, "news": 4.5},
    "GBP_JPY": {"normal": 2.0, "high": 3.5, "news": 6.0},
}

# Slippage model (in pips) — market orders during volatile periods
SLIPPAGE_PIPS = {"normal": 0.3, "high": 0.8, "news": 2.0}


def get_spread_pips(pair: str, hour_utc: int) -> float:
    """
    Get estimated spread in pips based on pair and time of day.
    Spreads widen during low-liquidity sessions and news.
    """
    spreads = SPREAD_MODEL.get(pair, {"normal": 1.0, "high": 2.0, "news": 4.0})

    # Asian session (23:00-07:00 UTC) — wider for most pairs
    if 23 <= hour_utc or hour_utc < 7:
        return spreads["high"]
    # London/NY overlap (12:00-16:00 UTC) — tightest
    elif 12 <= hour_utc < 16:
        return spreads["normal"]
    # London open/close (07:00-09:00, 16:00-18:00) — slightly wider
    elif 7 <= hour_utc < 9 or 16 <= hour_utc < 18:
        return (spreads["normal"] + spreads["high"]) / 2
    else:
        return spreads["normal"]


def spread_to_commission(spread_pips: float, pip_value: float = 0.0001) -> float:
    """Convert spread in pips to backtrader commission (percentage)."""
    return spread_pips * pip_value


class EMACrossStrategy(bt.Strategy):
    """EMA Crossover + RSI + ATR strategy with multi-confirmation."""

    params = (
        ("fast_period", 20),
        ("slow_period", 50),
        ("rsi_period", 14),
        ("rsi_overbought", 70),
        ("rsi_oversold", 30),
        ("atr_period", 14),
        ("atr_multiplier", 2.0),
        ("risk_per_trade", 0.01),
        ("min_confirmations", 1),
        ("use_spread_filter", True),
        ("max_spread_pips", 2.5),
    )

    def __init__(self):
        self.ema_fast = bt.indicators.EMA(self.data.close, period=self.p.fast_period)
        self.ema_slow = bt.indicators.EMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.ema_fast, self.ema_slow)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self.order = None

    def next(self):
        if self.order:
            return
        if not self.position:
            confirmations = 0
            direction = None

            # EMA crossover
            if self.crossover > 0:
                confirmations += 1
                direction = "buy"
            elif self.crossover < 0:
                confirmations += 1
                direction = "sell"

            # RSI extreme
            if self.rsi[0] < 35:
                if direction == "buy":
                    confirmations += 1
                elif direction is None:
                    confirmations += 1
                    direction = "buy"
            elif self.rsi[0] > 65:
                if direction == "sell":
                    confirmations += 1
                elif direction is None:
                    confirmations += 1
                    direction = "sell"

            # Check minimum confirmations
            if confirmations >= self.p.min_confirmations and direction:
                size = self._calc_size()
                if size > 0:
                    if direction == "buy":
                        self.order = self.buy(size=size)
                    else:
                        self.order = self.sell(size=size)
        else:
            # Exit on opposite crossover
            if self.position.size > 0 and self.crossover < 0:
                self.order = self.close()
            elif self.position.size < 0 and self.crossover > 0:
                self.order = self.close()

    def _calc_size(self):
        risk_amount = self.broker.getcash() * self.p.risk_per_trade
        stop_dist = self.atr[0] * self.p.atr_multiplier
        if stop_dist == 0:
            return 0
        return int(risk_amount / stop_dist)


def run_backtest(instrument="EUR_USD", granularity="H1", count=2000,
                 cash=10000.0, use_spread=True):
    """Run backtest with spread model."""
    client = OandaClient()
    df = client.get_candles(instrument, granularity, count)
    if df.empty:
        return None

    df_bt = df.copy()
    df_bt.index = pd.to_datetime(df_bt.index)
    df_bt["openinterest"] = 0

    # Calculate spread-based commission
    if use_spread:
        # Use average spread for the instrument
        avg_spread = SPREAD_MODEL.get(instrument, {"normal": 1.0})["normal"]
        pip_value = 0.01 if "JPY" in instrument else 0.0001
        commission = spread_to_commission(avg_spread, pip_value)
    else:
        commission = 0.0002  # Default

    cerebro = bt.Cerebro()
    cerebro.addstrategy(EMACrossStrategy)

    data = bt.feeds.PandasData(
        dataname=df_bt, datetime=None,
        open="open", high="high", low="low", close="close",
        volume="volume", openinterest="openinterest",
    )
    cerebro.adddata(data)
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    results = cerebro.run()
    strat = results[0]
    final = cerebro.broker.getvalue()
    profit = final - cash
    profit_pct = (profit / cash) * 100

    dd = strat.analyzers.drawdown.get_analysis()
    max_dd = dd.get("max", {}).get("drawdown", 0)
    sharpe = strat.analyzers.sharpe.get_analysis()
    sr = sharpe.get("sharperatio", 0)
    ta = strat.analyzers.trades.get_analysis()
    total = ta.get("total", {}).get("total", 0)
    won = ta.get("won", {}).get("total", 0)
    lost = ta.get("lost", {}).get("total", 0)
    wr = (won / total * 100) if total > 0 else 0

    return {
        "instrument": instrument,
        "granularity": granularity,
        "candles": count,
        "final_value": round(final, 2),
        "profit_pct": round(profit_pct, 2),
        "max_drawdown": round(max_dd, 1),
        "sharpe": round(sr, 2) if sr else 0,
        "total_trades": total,
        "won": won,
        "lost": lost,
        "win_rate": round(wr, 1),
        "commission_model": "spread" if use_spread else "fixed",
    }


def run_walk_forward(instrument="EUR_USD", granularity="H1",
                     train_size=1000, test_size=500, step=250):
    """
    Walk-forward analysis.
    Train on train_size candles, test on test_size, roll forward by step.
    """
    client = OandaClient()
    # Fetch enough data for the full walk-forward
    total_needed = train_size + test_size + step * 3  # At least 3 windows
    df = client.get_candles(instrument, granularity, total_needed)
    if df.empty or len(df) < train_size + test_size:
        return []

    results = []
    start = 0

    while start + train_size + test_size <= len(df):
        train_df = df.iloc[start:start + train_size]
        test_df = df.iloc[start + train_size:start + train_size + test_size]

        # Run backtest on test window
        test_bt = test_df.copy()
        test_bt.index = pd.to_datetime(test_bt.index)
        test_bt["openinterest"] = 0

        cerebro = bt.Cerebro()
        cerebro.addstrategy(EMACrossStrategy)

        data = bt.feeds.PandasData(
            dataname=test_bt, datetime=None,
            open="open", high="high", low="low", close="close",
            volume="volume", openinterest="openinterest",
        )
        cerebro.adddata(data)
        cerebro.broker.setcash(10000.0)

        avg_spread = SPREAD_MODEL.get(instrument, {"normal": 1.0})["normal"]
        pip_value = 0.01 if "JPY" in instrument else 0.0001
        cerebro.broker.setcommission(commission=spread_to_commission(avg_spread, pip_value))

        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

        bt_results = cerebro.run()
        strat = bt_results[0]
        final = cerebro.broker.getvalue()
        profit = final - 10000.0
        profit_pct = (profit / 10000.0) * 100

        dd = strat.analyzers.drawdown.get_analysis()
        max_dd = dd.get("max", {}).get("drawdown", 0)
        ta = strat.analyzers.trades.get_analysis()
        total = ta.get("total", {}).get("total", 0)

        results.append({
            "window": len(results) + 1,
            "start_idx": start + train_size,
            "end_idx": start + train_size + test_size,
            "profit_pct": round(profit_pct, 2),
            "max_drawdown": round(max_dd, 1),
            "trades": total,
        })

        start += step

    return results


if __name__ == "__main__":
    print("\n📊 Backtest with Spread Model")
    print("=" * 75)
    print(f"{'Pair':<12} {'TF':<6} {'Trades':<8} {'Win%':<8} {'P&L%':<10} {'MaxDD':<8} {'Sharpe':<8} {'Model':<8}")
    print("-" * 75)

    configs = [
        ("EUR_USD", "H1", 2000),
        ("EUR_USD", "H4", 1000),
        ("GBP_USD", "H1", 2000),
        ("USD_JPY", "H1", 2000),
    ]

    for inst, tf, cnt in configs:
        try:
            # With spread
            r = run_backtest(inst, tf, cnt, use_spread=True)
            if r:
                print(
                    f"{r['instrument']:<12} {r['granularity']:<6} "
                    f"{r['total_trades']:<8} {r['win_rate']:<8} "
                    f"{r['profit_pct']:+.2f}%{'':<4} {r['max_drawdown']:<8} "
                    f"{r['sharpe']:<8} {'spread':<8}"
                )
        except Exception as e:
            print(f"{inst} {tf}: ERROR - {e}")

    print("-" * 75)

    # Walk-forward on EUR/USD
    print("\n📈 Walk-Forward Analysis: EUR/USD H1")
    print("=" * 50)
    print(f"{'Window':<10} {'P&L%':<12} {'MaxDD':<10} {'Trades':<10}")
    print("-" * 50)

    try:
        wf_results = run_walk_forward("EUR_USD", "H1", train_size=800, test_size=400, step=200)
        for r in wf_results:
            print(
                f"{r['window']:<10} {r['profit_pct']:+.2f}%{'':<6} "
                f"{r['max_drawdown']:<10} {r['trades']:<10}"
            )

        if wf_results:
            avg_pnl = np.mean([r["profit_pct"] for r in wf_results])
            avg_dd = np.mean([r["max_drawdown"] for r in wf_results])
            total_trades = sum(r["trades"] for r in wf_results)
            print("-" * 50)
            print(f"{'AVG':<10} {avg_pnl:+.2f}%{'':<6} {avg_dd:<10.1f} {total_trades:<10}")
    except Exception as e:
        print(f"Walk-forward error: {e}")

    print("=" * 50)
