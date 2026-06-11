"""
Per-Pair Parameter Optimization for Forex Bot
================================================
Grid search over confirmation thresholds, SL/TP ATR multipliers,
and session filters for each currency pair.

Outputs optimal params per pair based on Sharpe ratio, win rate, and max drawdown.
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

import backtrader as bt
import pandas as pd

sys.path.insert(0, '/opt/hermes/forex-trading-bot')
from src.oanda_client import OandaClient
from src.config import config

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("/opt/hermes/forex-trading-bot/optimization_results")
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Parameter Grid ──────────────────────────────────────────────

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "EUR_GBP", "AUD_USD", "USD_CAD", "USD_CHF"]
GRANULARITIES = ["H1", "H4"]

# Grid: (min_conf, sl_mult, tp_mult, adx_thresh, session_start, session_end, rsi_ob, rsi_os)
PARAM_GRID = [
    # Base configs
    (2, 1.5, 2.5, 25, 7, 21, 70, 30),
    (2, 1.5, 3.0, 25, 7, 21, 70, 30),
    (2, 2.0, 3.0, 25, 7, 21, 70, 30),
    (3, 1.5, 2.5, 25, 7, 21, 70, 30),
    (3, 1.5, 3.0, 25, 7, 21, 70, 30),
    (3, 2.0, 3.0, 25, 7, 21, 70, 30),
    # Tighter stops
    (2, 1.2, 2.0, 25, 7, 21, 70, 30),
    (3, 1.2, 2.0, 25, 7, 21, 70, 30),
    # Wider stops
    (2, 2.0, 3.5, 25, 7, 21, 70, 30),
    (3, 2.0, 3.5, 25, 7, 21, 70, 30),
    # Lower ADX threshold (more trades)
    (2, 1.5, 2.5, 20, 7, 21, 70, 30),
    (3, 1.5, 2.5, 20, 7, 21, 70, 30),
    # Higher ADX threshold (stronger trends only)
    (2, 1.5, 2.5, 30, 7, 21, 70, 30),
    (3, 1.5, 2.5, 30, 7, 21, 70, 30),
    # Session-specific: London + NY overlap only
    (2, 1.5, 2.5, 25, 13, 17, 70, 30),
    (3, 1.5, 2.5, 25, 13, 17, 70, 30),
    # Session-specific: London only
    (2, 1.5, 2.5, 25, 7, 16, 70, 30),
    (3, 1.5, 2.5, 25, 7, 16, 70, 30),
    # RSI extremes
    (2, 1.5, 2.5, 25, 7, 21, 75, 25),
    (3, 1.5, 2.5, 25, 7, 21, 75, 25),
    (2, 1.5, 2.5, 25, 7, 21, 65, 35),
    (3, 1.5, 2.5, 25, 7, 21, 65, 35),
    # 1 confirmation (aggressive)
    (1, 1.5, 2.5, 25, 7, 21, 70, 30),
    (1, 1.5, 3.0, 25, 7, 21, 70, 30),
    (1, 2.0, 3.0, 25, 7, 21, 70, 30),
    # 4 confirmation (conservative)
    (4, 1.5, 2.5, 25, 7, 21, 70, 30),
    (4, 2.0, 3.0, 25, 7, 21, 70, 30),
]


# ── Backtrader Strategy ─────────────────────────────────────────

class ForexOptStrategy(bt.Strategy):
    """Forex strategy with tunable parameters for optimization."""

    params = (
        ('min_conf', 2),
        ('sl_mult', 1.5),
        ('tp_mult', 2.5),
        ('adx_thresh', 25),
        ('session_start', 7),
        ('session_end', 21),
        ('rsi_ob', 70),
        ('rsi_os', 30),
        ('risk_per_trade', 0.005),
    )

    def __init__(self):
        self.ema_f = bt.indicators.EMA(period=20)
        self.ema_m = bt.indicators.EMA(period=50)
        self.ema_s = bt.indicators.EMA(period=200)
        self.rsi = bt.indicators.RSI(period=14)
        self.macd = bt.indicators.MACD(period_me1=12, period_me2=26, period_signal=9)
        self.atr = bt.indicators.ATR(period=14)
        self.adx = bt.indicators.ADX(period=14)
        self.order = None
        self.bar_count = 0

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        self.order = None

    def next(self):
        self.bar_count += 1
        if self.order:
            return

        hour = self.datas[0].datetime.datetime(0).hour
        if hour < self.p.session_start or hour >= self.p.session_end:
            return
        if len(self) < 210:
            return

        close = self.data.close[0]
        ema_f = self.ema_f[0]
        ema_m = self.ema_m[0]
        ema_s = self.ema_s[0]
        rsi_v = self.rsi[0]
        macd_hist = self.macd.macd[0] - self.macd.signal[0]
        prev_macd_hist = (self.macd.macd[-1] - self.macd.signal[-1]) if len(self) > 1 else macd_hist
        atr_v = self.atr[0]
        adx_v = self.adx[0]

        conf = 0
        direction = 0

        # EMA
        if close > ema_m > ema_s and ema_f > ema_m:
            conf += 1; direction += 1
        elif close < ema_m < ema_s and ema_f < ema_m:
            conf += 1; direction -= 1

        # MACD
        if macd_hist > 0 and prev_macd_hist <= 0:
            conf += 1; direction += 1
        elif macd_hist < 0 and prev_macd_hist >= 0:
            conf += 1; direction -= 1
        elif macd_hist > 0:
            direction += 0.5
        else:
            direction -= 0.5

        # RSI
        if rsi_v < self.p.rsi_os:
            conf += 1; direction += 1
        elif rsi_v > self.p.rsi_ob:
            conf += 1; direction -= 1
        elif rsi_v > 55:
            direction += 0.3
        elif rsi_v < 45:
            direction -= 0.3

        # ADX
        if adx_v > self.p.adx_thresh:
            conf += 1
            if direction > 0: direction += 0.5
            elif direction < 0: direction -= 0.5

        if not self.position:
            if conf >= self.p.min_conf:
                if direction > 0:
                    sl = close - (atr_v * self.p.sl_mult)
                    tp = close + (atr_v * self.p.tp_mult)
                    size = self._calc_size(close, sl)
                    if size > 0: self.order = self.buy(size=size)
                elif direction < 0:
                    sl = close + (atr_v * self.p.sl_mult)
                    tp = close - (atr_v * self.p.tp_mult)
                    size = self._calc_size(close, sl)
                    if size > 0: self.order = self.sell(size=size)
        elif self.position:
            if self.position.size > 0:
                sl = self.position.price - (atr_v * self.p.sl_mult)
                tp = self.position.price + (atr_v * self.p.tp_mult)
                if close <= sl or close >= tp:
                    self.order = self.close()
            else:
                sl = self.position.price + (atr_v * self.p.sl_mult)
                tp = self.position.price - (atr_v * self.p.tp_mult)
                if close >= sl or close <= tp:
                    self.order = self.close()

    def _calc_size(self, entry, sl):
        risk = self.broker.getvalue() * self.p.risk_per_trade
        dist = abs(entry - sl)
        if dist <= 0: return 0
        return min(int(risk / dist), 100000)


class ForexData(bt.feeds.PandasData):
    params = (
        ('datetime', None), ('open', 'open'), ('high', 'high'),
        ('low', 'low'), ('close', 'close'), ('volume', 'volume'),
        ('openinterest', -1),
    )


# ── Optimization Runner ─────────────────────────────────────────

def run_single(df, params, initial_balance=100000.0):
    """Run a single backtest with given params. Returns metrics dict."""
    cerebro = bt.Cerebro()
    cerebro.addstrategy(ForexOptStrategy, **params)
    cerebro.adddata(ForexData(dataname=df))
    cerebro.broker.setcash(initial_balance)
    cerebro.broker.setcommission(commission=0.0002)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='ta')
    cerebro.addanalyzer(bt.analyzers.SQN, _name='sqn')

    results = cerebro.run()
    strat = results[0]

    final = cerebro.broker.getvalue()
    ta = strat.analyzers.ta.get_analysis()
    dd = strat.analyzers.dd.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis()
    sqn = strat.analyzers.sqn.get_analysis()

    total = ta.get('total', {}).get('total', 0)
    won = ta.get('won', {}).get('total', 0)
    lost = ta.get('lost', {}).get('total', 0)
    net_pnl = ta.get('pnl', {}).get('net', {}).get('total', 0)

    return {
        'return_pct': round((final - initial_balance) / initial_balance * 100, 2),
        'final': round(final, 2),
        'trades': total,
        'won': won,
        'lost': lost,
        'win_rate': round(won / total * 100, 1) if total > 0 else 0,
        'net_pnl': round(net_pnl, 2),
        'sharpe': round(sharpe.get('sharperatio', 0), 3) if sharpe.get('sharperatio') else 0,
        'max_dd': round(dd.get('max', {}).get('drawdown', 0), 2),
        'sqn': round(sqn.get('sqn', 0), 2),
    }


def optimize_pair(instrument, df, granularity):
    """Run full parameter grid for a single pair."""
    results = []

    for i, (mc, sl, tp, adx, ss, se, rsi_ob, rsi_os) in enumerate(PARAM_GRID):
        params = {
            'min_conf': mc,
            'sl_mult': sl,
            'tp_mult': tp,
            'adx_thresh': adx,
            'session_start': ss,
            'session_end': se,
            'rsi_ob': rsi_ob,
            'rsi_os': rsi_os,
        }

        try:
            r = run_single(df, params)
            r['params'] = params
            r['config_id'] = i
            results.append(r)
        except Exception as e:
            logger.warning(f'{instrument} {granularity} config {i} failed: {e}')

    return results


def score_result(r):
    """
    Composite score for ranking configurations.
    Weights: Sharpe 30%, WinRate 25%, Return 20%, MaxDD penalty 15%, SQN 10%
    """
    if r['trades'] < 5:
        return -999  # Too few trades

    sharpe_score = min(r['sharpe'] / 10.0, 1.0) * 30  # Normalize to 0-30
    wr_score = r['win_rate'] / 100.0 * 25
    ret_score = min(max(r['return_pct'], 0) / 20.0, 1.0) * 20
    dd_score = max(0, 10 - r['max_dd']) / 10.0 * 15  # Lower DD = higher score
    sqn_score = min(max(r['sqn'], 0) / 3.0, 1.0) * 10

    return sharpe_score + wr_score + ret_score + dd_score + sqn_score


def main():
    client = OandaClient()

    all_results = {}

    for instrument in PAIRS:
        print(f'\n{"="*60}')
        print(f'Optimizing {instrument}...')
        print(f'{"="*60}')

        for gran in GRANULARITIES:
            count = 5000 if gran == 'H1' else 2000
            df = client.get_candles(instrument, gran, count)
            if len(df) < 210:
                print(f'  {gran}: insufficient data ({len(df)} bars)')
                continue

            print(f'  {gran}: {len(df)} bars | {df.index[0].date()} → {df.index[-1].date()}')

            pair_results = optimize_pair(instrument, df, gran)

            if not pair_results:
                print(f'  {gran}: no results')
                continue

            # Sort by composite score
            pair_results.sort(key=score_result, reverse=True)

            # Save top 5
            top5 = pair_results[:5]
            all_results[f'{instrument}_{gran}'] = top5

            print(f'  Top 5 configs:')
            for rank, r in enumerate(top5, 1):
                p = r['params']
                print(f'    #{rank}: conf={p["min_conf"]} SL={p["sl_mult"]} TP={p["tp_mult"]} '
                      f'ADX={p["adx_thresh"]} sess={p["session_start"]}-{p["session_end"]} | '
                      f'Ret={r["return_pct"]:+.2f}% Trades={r["trades"]} WR={r["win_rate"]}% '
                      f'Sharpe={r["sharpe"]} DD={r["max_dd"]}% SQN={r["sqn"]}')

    # Save full results
    output_file = OUTPUT_DIR / f'optimization_{datetime.now():%Y%m%d_%H%M%S}.json'
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f'\nFull results saved to {output_file}')

    # Print summary table
    print(f'\n{"="*100}')
    print(f'OPTIMIZATION SUMMARY')
    print(f'{"="*100}')
    print(f'{"Pair":<12} {"Gran":<5} {"Conf":>4} {"SL":>5} {"TP":>5} {"ADX":>4} {"Session":>9} '
          f'{"Ret%":>7} {"Trades":>7} {"WR%":>6} {"Sharpe":>7} {"MaxDD%":>7} {"SQN":>6}')
    print(f'{"-"*100}')

    for key, results in all_results.items():
        if results:
            r = results[0]  # Best config
            p = r['params']
            session = f'{p["session_start"]}-{p["session_end"]}'
            print(f'{key.split("_")[0] + "_" + key.split("_")[1]:<12} {key.split("_")[-1]:<5} '
                  f'{p["min_conf"]:>4} {p["sl_mult"]:>5.1f} {p["tp_mult"]:>5.1f} {p["adx_thresh"]:>4} '
                  f'{session:>9} {r["return_pct"]:>+7.2f} {r["trades"]:>7} {r["win_rate"]:>6.1f} '
                  f'{r["sharpe"]:>7.3f} {r["max_dd"]:>7.2f} {r["sqn"]:>6.2f}')


if __name__ == '__main__':
    main()
