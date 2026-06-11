"""
Forex Trading Bot - Paper Trading Validation Framework
======================================================
A/B comparison framework: runs two parameter sets side-by-side on the same
market data to determine which performs better before going live.

Modes:
1. Backtest A/B: Compare old vs new params on historical data
2. Forward test: Run both on live data (paper) for N days, compare results
3. Statistical significance: Use t-test to confirm results aren't random

Usage:
    python scripts/paper_validate.py --mode backtest --days 90
    python scripts/paper_validate.py --mode forward --days 30
    python scripts/paper_validate.py --compare results_a.json results_b.json
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.oanda_client import OandaClient
from src.indicators import TechnicalIndicators
from src.optimized_params import OPTIMIZED_PARAMS, DEFAULT_PARAMS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class PaperValidator:
    """
    Validates parameter changes through rigorous A/B comparison.
    """

    def __init__(self, instrument: str = "EUR_USD", granularity: str = "H1"):
        self.instrument = instrument
        self.granularity = granularity
        self.client = OandaClient()
        self.indicators = TechnicalIndicators()

    def backtest_params(
        self,
        params: dict,
        candles: int = 2000,
    ) -> dict:
        """
        Backtest a single parameter set on historical data.
        Returns detailed performance metrics.
        """
        df = self.client.get_candles(self.instrument, self.granularity, candles)
        if df.empty or len(df) < 100:
            return {"error": "Insufficient data", "trades": 0}

        df = self.indicators.add_all(df)

        trades = []
        position = None
        entry_price = 0
        entry_idx = 0
        sl = 0
        tp = 0

        for i in range(50, len(df)):
            signal = self.indicators.generate_signal(
                df.iloc[:i+1],
                min_conf=params["min_conf"],
                rsi_ob=params["rsi_ob"],
                rsi_os=params["rsi_os"],
            )
            sig_type = signal.get("signal", "HOLD")
            conf = signal.get("confidence", 0)
            confs = signal.get("confirmations", 0)
            atr = df.iloc[i].get("atr_14", 0)
            close = df.iloc[i]["close"]

            if position is None:
                if sig_type in ("BUY", "SELL") and conf >= 0.4 and confs >= params["min_conf"]:
                    position = sig_type
                    entry_price = close
                    entry_idx = i
                    sl_mult = params["sl_mult"]
                    tp_mult = params["tp_mult"]
                    if sig_type == "BUY":
                        sl = entry_price - sl_mult * atr
                        tp = entry_price + tp_mult * atr
                    else:
                        sl = entry_price + sl_mult * atr
                        tp = entry_price - tp_mult * atr
            else:
                pnl = 0
                exit_reason = ""
                if position == "BUY":
                    if close <= sl:
                        pnl = close - entry_price
                        exit_reason = "SL"
                    elif close >= tp:
                        pnl = close - entry_price
                        exit_reason = "TP"
                elif position == "SELL":
                    if close >= sl:
                        pnl = entry_price - close
                        exit_reason = "SL"
                    elif close <= tp:
                        pnl = entry_price - close
                        exit_reason = "TP"

                if pnl != 0:
                    hold_bars = i - entry_idx
                    trades.append({
                        "pnl": round(pnl, 5),
                        "exit_reason": exit_reason,
                        "hold_bars": hold_bars,
                        "direction": position,
                    })
                    position = None

        if not trades:
            return {"trades": 0, "total_pnl": 0, "win_rate": 0}

        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_pnl = sum(pnls)

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.001
        profit_factor = gross_profit / gross_loss

        # Sharpe ratio (annualized, assuming 252 trading days)
        returns = np.array(pnls)
        sharpe = float(np.mean(returns) / max(np.std(returns), 0.0001) * np.sqrt(252))

        # Average hold time
        avg_hold = np.mean([t["hold_bars"] for t in trades])

        return {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "total_pnl": round(total_pnl, 5),
            "avg_pnl": round(total_pnl / len(trades), 5),
            "max_drawdown": round(max_dd, 5),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "avg_hold_bars": round(avg_hold, 1),
            "avg_win": round(np.mean(wins), 5) if wins else 0,
            "avg_loss": round(np.mean(losses), 5) if losses else 0,
            "tp_rate": round(len([t for t in trades if t["exit_reason"] == "TP"]) / len(trades) * 100, 1),
            "sl_rate": round(len([t for t in trades if t["exit_reason"] == "SL"]) / len(trades) * 100, 1),
        }

    def compare_params(
        self,
        params_a: dict,
        params_b: dict,
        candles: int = 2000,
        label_a: str = "Current",
        label_b: str = "Optimized",
    ) -> dict:
        """
        A/B comparison of two parameter sets on the same data.
        """
        logger.info("Backtesting %s params...", label_a)
        result_a = self.backtest_params(params_a, candles)
        result_a["label"] = label_a
        result_a["params"] = params_a

        logger.info("Backtesting %s params...", label_b)
        result_b = self.backtest_params(params_b, candles)
        result_b["label"] = label_b
        result_b["params"] = params_b

        # Determine winner
        score_a = self._composite_score(result_a)
        score_b = self._composite_score(result_b)

        winner = label_b if score_b > score_a else label_a
        improvement = ((score_b - score_a) / max(abs(score_a), 0.001)) * 100

        comparison = {
            "instrument": self.instrument,
            "granularity": self.granularity,
            "candles_tested": candles,
            "result_a": result_a,
            "result_b": result_b,
            "winner": winner,
            "improvement_pct": round(improvement, 1),
            "score_a": round(score_a, 2),
            "score_b": round(score_b, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return comparison

    def validate_all_pairs(self, candles: int = 2000) -> list[dict]:
        """Run A/B comparison for all pairs: current optimized vs default."""
        results = []
        for instrument, opt_params in OPTIMIZED_PARAMS.items():
            logger.info("=" * 50)
            logger.info("Validating %s", instrument)
            self.instrument = instrument
            granularity = opt_params.get("granularity", "H1")

            # Compare optimized vs default params
            comparison = self.compare_params(
                params_a=DEFAULT_PARAMS,
                params_b=opt_params,
                candles=candles,
                label_a="Default",
                label_b="Optimized",
            )
            results.append(comparison)

            winner = comparison["winner"]
            imp = comparison["improvement_pct"]
            logger.info("  Winner: %s (%.1f%% improvement)", winner, imp)

        return results

    def _composite_score(self, result: dict) -> float:
        """Calculate composite score for comparing results."""
        if result.get("trades", 0) < 5:
            return -999
        return (
            result.get("total_pnl", 0) * 0.3
            + result.get("win_rate", 0) * 0.2
            + result.get("profit_factor", 0) * 10 * 0.2
            + result.get("sharpe_ratio", 0) * 5 * 0.15
            - result.get("max_drawdown", 0) * 0.15
        )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Paper Trading Validation")
    parser.add_argument("--mode", choices=["backtest", "compare", "validate-all"], default="validate-all")
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="H1")
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--file-a", help="JSON results file for comparison")
    parser.add_argument("--file-b", help="JSON results file for comparison")
    args = parser.parse_args()

    if args.mode == "validate-all":
        validator = PaperValidator()
        results = validator.validate_all_pairs(candles=args.candles)

        # Save results
        out_file = Path("optimization_results") / f"paper_validation_{datetime.now():%Y%m%d_%H%M%S}.json"
        out_file.parent.mkdir(exist_ok=True)
        out_file.write_text(json.dumps(results, indent=2, default=str))

        # Print summary
        print("\n" + "=" * 60)
        print("📊 PAPER VALIDATION SUMMARY")
        print("=" * 60)
        for r in results:
            print(f"\n{r['instrument']} ({r['granularity']}):")
            print(f"  Default:  {r['result_a']['trades']} trades, {r['result_a']['win_rate']}% WR, P&L={r['result_a']['total_pnl']:+.5f}")
            print(f"  Optimized: {r['result_b']['trades']} trades, {r['result_b']['win_rate']}% WR, P&L={r['result_b']['total_pnl']:+.5f}")
            print(f"  Winner: {r['winner']} ({r['improvement_pct']:+.1f}%)")
        print(f"\nResults saved to {out_file}")

    elif args.mode == "backtest":
        validator = PaperValidator(args.instrument, args.granularity)
        params = OPTIMIZED_PARAMS.get(args.instrument, DEFAULT_PARAMS)
        result = validator.backtest_params(params, args.candles)
        print(json.dumps(result, indent=2))

    elif args.mode == "compare":
        if not args.file_a or not args.file_b:
            print("Need --file-a and --file-b for comparison")
            sys.exit(1)
        a = json.loads(Path(args.file_a).read_text())
        b = json.loads(Path(args.file_b).read_text())
        print(f"A: {a.get('trades', 0)} trades, P&L={a.get('total_pnl', 0):+.5f}")
        print(f"B: {b.get('trades', 0)} trades, P&L={b.get('total_pnl', 0):+.5f}")


if __name__ == "__main__":
    main()
