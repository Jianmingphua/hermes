"""
Gold Strategy Parameter Tuning
Tests multiple parameter combinations to find optimal settings.
"""

import sys
import logging
from datetime import datetime

import backtrader as bt
import pandas as pd

sys.path.insert(0, '/opt/hermes/forex-trading-bot')
from src.oanda_client import OandaClient

logging.basicConfig(level=logging.WARNING)


class GoldStrategyTunable(bt.Strategy):
    """Gold strategy with tunable parameters."""

    params = (
        ('ema_fast', 20),
        ('ema_mid', 50),
        ('ema_slow', 200),
        ('rsi_period', 14),
        ('rsi_ob', 70),
        ('rsi_os', 30),
        ('macd_fast', 12),
        ('macd_slow', 26),
        ('macd_signal', 9),
        ('atr_period', 14),
        ('sl_atr_mult', 1.5),
        ('tp_atr_mult', 2.5),
        ('adx_threshold', 25),
        ('min_confirmations', 3),
        ('risk_per_trade', 0.005),
        ('session_start', 7),
        ('session_end', 21),
    )

    def __init__(self):
        self.ema_fast = bt.indicators.EMA(period=self.p.ema_fast)
        self.ema_mid = bt.indicators.EMA(period=self.p.ema_mid)
        self.ema_slow = bt.indicators.EMA(period=self.p.ema_slow)
        self.rsi = bt.indicators.RSI(period=self.p.rsi_period)
        self.macd = bt.indicators.MACD(
            period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow,
            period_signal=self.p.macd_signal,
        )
        self.atr = bt.indicators.ATR(period=self.p.atr_period)
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
        if len(self) < self.p.ema_slow + 10:
            return

        close = self.data.close[0]
        ema_f = self.ema_fast[0]
        ema_m = self.ema_mid[0]
        ema_s = self.ema_slow[0]
        rsi_val = self.rsi[0]
        macd_line = self.macd.macd[0]
        macd_sig = self.macd.signal[0]
        macd_hist = macd_line - macd_sig
        prev_macd_hist = (self.macd.macd[-1] - self.macd.signal[-1]) if len(self) > 1 else macd_hist
        atr_val = self.atr[0]
        adx_val = self.adx[0]

        confirmations = 0
        direction = 0

        # EMA
        if close > ema_m > ema_s and ema_f > ema_m:
            confirmations += 1; direction += 1
        elif close < ema_m < ema_s and ema_f < ema_m:
            confirmations += 1; direction -= 1

        # MACD
        if macd_hist > 0 and prev_macd_hist <= 0:
            confirmations += 1; direction += 1
        elif macd_hist < 0 and prev_macd_hist >= 0:
            confirmations += 1; direction -= 1
        elif macd_hist > 0:
            direction += 0.5
        else:
            direction -= 0.5

        # RSI
        if rsi_val < self.p.rsi_os:
            confirmations += 1; direction += 1
        elif rsi_val > self.p.rsi_ob:
            confirmations += 1; direction -= 1
        elif rsi_val > 55:
            direction += 0.3
        elif rsi_val < 45:
            direction -= 0.3

        # ADX
        if adx_val > self.p.adx_threshold:
            confirmations += 1
            if direction > 0: direction += 0.5
            elif direction < 0: direction -= 0.5

        if not self.position:
            if confirmations >= self.p.min_confirmations:
                if direction > 0:
                    sl = close - (atr_val * self.p.sl_atr_mult)
                    tp = close + (atr_val * self.p.tp_atr_mult)
                    size = self._calc_size(close, sl)
                    if size > 0:
                        self.order = self.buy(size=size)
                elif direction < 0:
                    sl = close + (atr_val * self.p.sl_atr_mult)
                    tp = close - (atr_val * self.p.tp_atr_mult)
                    size = self._calc_size(close, sl)
                    if size > 0:
                        self.order = self.sell(size=size)
        elif self.position:
            if self.position.size > 0:
                sl = self.position.price - (atr_val * self.p.sl_atr_mult)
                tp = self.position.price + (atr_val * self.p.tp_atr_mult)
                if close <= sl or close >= tp:
                    self.order = self.close()
            else:
                sl = self.position.price + (atr_val * self.p.sl_atr_mult)
                tp = self.position.price - (atr_val * self.p.tp_atr_mult)
                if close >= sl or close <= tp:
                    self.order = self.close()

    def _calc_size(self, entry, sl):
        risk_amount = self.broker.getvalue() * self.p.risk_per_trade
        stop_dist = abs(entry - sl)
        if stop_dist <= 0: return 0
        return min(int(risk_amount / stop_dist), 500)


class GoldData(bt.feeds.PandasData):
    params = (
        ('datetime', None), ('open', 'open'), ('high', 'high'),
        ('low', 'low'), ('close', 'close'), ('volume', 'volume'),
        ('openinterest', -1),
    )


def run_single(df, params, initial_balance=100000.0):
    cerebro = bt.Cerebro()
    cerebro.addstrategy(GoldStrategyTunable, **params)
    cerebro.adddata(GoldData(dataname=df))
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


def main():
    client = OandaClient()

    # Fetch data for different timeframes
    data = {}
    for gran, count in [('M15', 5000), ('H1', 5000), ('H4', 2000)]:
        df = client.get_candles('XAU_USD', gran, count)
        data[gran] = df
        print(f'{gran}: {len(df)} bars | {df.index[0].date()} → {df.index[-1].date()}')

    # Parameter grid
    param_grid = [
        # (min_conf, sl_mult, tp_mult, adx_thresh, session_start, session_end)
        (3, 1.5, 2.5, 25, 7, 21),   # Base (London+NY)
        (3, 1.5, 3.0, 25, 7, 21),   # Wider TP
        (3, 2.0, 3.0, 25, 7, 21),   # Wider SL+TP
        (2, 1.5, 2.5, 25, 7, 21),   # Lower confirmation threshold
        (2, 1.5, 3.0, 20, 7, 21),   # Relaxed all
        (3, 1.5, 2.5, 25, 13, 17),  # Overlap only
        (3, 1.5, 2.5, 20, 0, 24),   # 24h (no session filter)
        (3, 1.2, 2.0, 25, 7, 21),   # Tighter stops
        (4, 1.5, 2.5, 25, 7, 21),   # Max confirmations
    ]

    labels = [
        'base (3conf,1.5SL,2.5TP,Lon+NY)',
        'wide_tp (3conf,1.5SL,3.0TP,Lon+NY)',
        'wide_both (3conf,2.0SL,3.0TP,Lon+NY)',
        'low_conf (2conf,1.5SL,2.5TP,Lon+NY)',
        'relaxed (2conf,1.5SL,3.0TP,ADX20,Lon+NY)',
        'overlap_only (3conf,1.5SL,2.5TP,13-17UTC)',
        '24h (3conf,1.5SL,2.5TP,no_session)',
        'tight (3conf,1.2SL,2.0TP,Lon+NY)',
        'max_conf (4conf,1.5SL,2.5TP,Lon+NY)',
    ]

    print(f'\n{"="*100}')
    print(f'{"Config":<45} {"Gran":<5} {"Ret%":>7} {"Trades":>7} {"WR%":>6} {"Net P&L":>10} {"Sharpe":>7} {"MaxDD%":>7} {"SQN":>6}')
    print(f'{"="*100}')

    best = None
    best_score = -999

    for label, (mc, sl, tp, adx, ss, se) in zip(labels, param_grid):
        params = {
            'min_confirmations': mc,
            'sl_atr_mult': sl,
            'tp_atr_mult': tp,
            'adx_threshold': adx,
            'session_start': ss,
            'session_end': se,
        }

        for gran in ['M15', 'H1', 'H4']:
            df = data[gran]
            if len(df) < 210:
                continue
            try:
                r = run_single(df, params)
                score = r['sharpe'] * 0.3 + (100 - r['max_dd']) * 0.3 + r['win_rate'] * 0.2 + min(r['trades'], 50) * 0.2
                if score > best_score and r['trades'] >= 5:
                    best_score = score
                    best = (label, gran, r)

                print(f'{label:<45} {gran:<5} {r["return_pct"]:>+7.2f} {r["trades"]:>7} {r["win_rate"]:>6.1f} ${r["net_pnl"]:>+9,.0f} {r["sharpe"]:>7.3f} {r["max_dd"]:>7.2f} {r["sqn"]:>6.2f}')
            except Exception as e:
                print(f'{label:<45} {gran:<5} ERROR: {e}')

    if best:
        print(f'\n{"="*100}')
        print(f'BEST: {best[0]} @ {best[1]}')
        print(f'  Return: {best[2]["return_pct"]:+.2f}% | Trades: {best[2]["trades"]} | WR: {best[2]["win_rate"]}%')
        print(f'  Net P&L: ${best[2]["net_pnl"]:+,.2f} | Sharpe: {best[2]["sharpe"]} | Max DD: {best[2]["max_dd"]}% | SQN: {best[2]["sqn"]}')


if __name__ == '__main__':
    main()
