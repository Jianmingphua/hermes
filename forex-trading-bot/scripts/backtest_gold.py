"""
Gold (XAU/USD) Strategy Backtest
==================================
Vectorized backtest using backtrader.
Tests the gold-specific strategy on historical H1 data from OANDA.
"""

import sys
import logging
from datetime import datetime, timezone

import backtrader as bt
import pandas as pd

sys.path.insert(0, '/opt/hermes/forex-trading-bot')
from src.oanda_client import OandaClient

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# ── Gold Strategy for Backtrader ─────────────────────────────────

class GoldStrategy(bt.Strategy):
    """
    Gold-specific trend-following + momentum strategy.
    Parameters tuned for XAU/USD characteristics.
    """

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
        ('max_spread_pips', 80),
        ('session_start', 7),    # London open UTC
        ('session_end', 21),     # NY close UTC
    )

    def __init__(self):
        # Indicators
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

        # Tracking
        self.order = None
        self.trades = []
        self.bar_count = 0

    def log(self, txt, dt=None):
        dt = dt or self.datas[0].datetime.datetime(0)
        logger.info(f'{dt.isoformat()} {txt}')

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        if order.status in [order.Completed]:
            if order.isbuy():
                self.trades.append({
                    'type': 'BUY',
                    'price': order.executed.price,
                    'size': order.executed.size,
                    'commission': order.executed.comm,
                    'bar': self.bar_count,
                })
            else:
                self.trades.append({
                    'type': 'SELL',
                    'price': order.executed.price,
                    'size': order.executed.size,
                    'commission': order.executed.comm,
                    'bar': self.bar_count,
                })
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            pass
        self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            pass  # Trade closed, P&L recorded by cerebro

    def next(self):
        self.bar_count += 1

        # Skip if order pending
        if self.order:
            return

        # Session filter: only trade during London + NY hours
        hour = self.datas[0].datetime.datetime(0).hour
        if hour < self.p.session_start or hour >= self.p.session_end:
            return

        # Need enough data for all indicators
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

        # ── Calculate Confirmations ──
        confirmations = 0
        direction = 0  # 1 = bullish, -1 = bearish

        # 1. EMA Trend
        if close > ema_m > ema_s and ema_f > ema_m:
            confirmations += 1
            direction += 1
        elif close < ema_m < ema_s and ema_f < ema_m:
            confirmations += 1
            direction -= 1

        # 2. MACD Crossover
        if macd_hist > 0 and prev_macd_hist <= 0:
            confirmations += 1
            direction += 1
        elif macd_hist < 0 and prev_macd_hist >= 0:
            confirmations += 1
            direction -= 1
        elif macd_hist > 0:
            direction += 0.5
        else:
            direction -= 0.5

        # 3. RSI
        if rsi_val < self.p.rsi_os:
            confirmations += 1
            direction += 1
        elif rsi_val > self.p.rsi_ob:
            confirmations += 1
            direction -= 1
        elif rsi_val > 55:
            direction += 0.3
        elif rsi_val < 45:
            direction -= 0.3

        # 4. ADX (volatility/trend strength)
        if adx_val > self.p.adx_threshold:
            confirmations += 1
            # ADX confirms direction but doesn't set it
            if direction > 0:
                direction += 0.5
            elif direction < 0:
                direction -= 0.5

        # ── Entry Logic ──
        if not self.position:
            if confirmations >= self.p.min_confirmations:
                if direction > 0:
                    # BUY
                    sl = close - (atr_val * self.p.sl_atr_mult)
                    tp = close + (atr_val * self.p.tp_atr_mult)
                    size = self._calculate_size(close, sl)
                    if size > 0:
                        self.order = self.buy(size=size)
                        self.log(f'BUY {size} @ {close:.2f} SL={sl:.2f} TP={tp:.2f} conf={confirmations}')
                elif direction < 0:
                    # SELL
                    sl = close + (atr_val * self.p.sl_atr_mult)
                    tp = close - (atr_val * self.p.tp_atr_mult)
                    size = self._calculate_size(close, sl)
                    if size > 0:
                        self.order = self.sell(size=size)
                        self.log(f'SELL {size} @ {close:.2f} SL={sl:.2f} TP={tp:.2f} conf={confirmations}')

        # ── Exit Logic (ATR-based SL/TP) ──
        elif self.position:
            if self.position.size > 0:  # Long
                sl = self.position.price - (atr_val * self.p.sl_atr_mult)
                tp = self.position.price + (atr_val * self.p.tp_atr_mult)
                if close <= sl or close >= tp:
                    self.order = self.close()
                    self.log(f'CLOSE LONG @ {close:.2f}')
            else:  # Short
                sl = self.position.price + (atr_val * self.p.sl_atr_mult)
                tp = self.position.price - (atr_val * self.p.tp_atr_mult)
                if close >= sl or close <= tp:
                    self.order = self.close()
                    self.log(f'CLOSE SHORT @ {close:.2f}')

    def _calculate_size(self, entry: float, sl: float) -> int:
        """Calculate position size based on risk."""
        risk_amount = self.broker.getvalue() * self.p.risk_per_trade
        stop_dist = abs(entry - sl)
        if stop_dist <= 0:
            return 0
        size = int(risk_amount / stop_dist)
        return min(size, 500)  # Cap at 500 oz


class GoldData(bt.feeds.PandasData):
    """Custom data feed for gold with proper column mapping."""
    params = (
        ('datetime', None),
        ('open', 'open'),
        ('high', 'high'),
        ('low', 'low'),
        ('close', 'close'),
        ('volume', 'volume'),
        ('openinterest', -1),
    )


def fetch_gold_data(granularity: str = 'H1', count: int = 5000) -> pd.DataFrame:
    """Fetch gold data from OANDA."""
    client = OandaClient()
    df = client.get_candles('XAU_USD', granularity, count)
    return df


def run_backtest(
    granularity: str = 'H1',
    count: int = 5000,
    initial_balance: float = 100000.0,
    commission: float = 0.0002,  # 0.02% per trade (gold spread estimate
    slippage: float = 0.05,      # $0.05 slippage per trade
) -> dict:
    """Run backtest and return results."""

    print(f'\n{"="*60}')
    print(f'Gold Strategy Backtest | {granularity} | {count} bars')
    print(f'{"="*60}')

    # Fetch data
    df = fetch_gold_data(granularity, count)
    if df.empty:
        print('ERROR: No data fetched')
        return {}

    print(f'Data: {len(df)} bars | {df.index[0]} → {df.index[-1]}')
    print(f'Price range: ${df["low"].min():.2f} - ${df["high"].max():.2f}')

    # Create cerebro
    cerebro = bt.Cerebro()

    # Add strategy
    cerebro.addstrategy(GoldStrategy)

    # Add data
    data = GoldData(dataname=df)
    cerebro.adddata(data)

    # Broker settings
    cerebro.broker.setcash(initial_balance)
    cerebro.broker.setcommission(commission=commission)

    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.SQN, _name='sqn')

    # Run
    results = cerebro.run()
    strat = results[0]

    # Extract results
    final_value = cerebro.broker.getvalue()
    total_return = (final_value - initial_balance) / initial_balance * 100

    # Trade analysis
    trade_analysis = strat.analyzers.trades.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis()
    drawdown = strat.analyzers.drawdown.get_analysis()
    returns = strat.analyzers.returns.get_analysis()
    sqn = strat.analyzers.sqn.get_analysis()

    total_trades = trade_analysis.get('total', {}).get('total', 0)
    won = trade_analysis.get('won', {}).get('total', 0)
    lost = trade_analysis.get('lost', {}).get('total', 0)
    win_rate = (won / total_trades * 100) if total_trades > 0 else 0

    gross_profit = trade_analysis.get('won', {}).get('pnl', {}).get('gross', {}).get('total', 0)
    gross_loss = trade_analysis.get('lost', {}).get('pnl', {}).get('gross', {}).get('total', 0)
    net_pnl = trade_analysis.get('pnl', {}).get('net', {}).get('total', 0)

    avg_win = trade_analysis.get('won', {}).get('pnl', {}).get('average', 0) if won > 0 else 0
    avg_loss = trade_analysis.get('lost', {}).get('pnl', {}).get('average', 0) if lost > 0 else 0

    # Max consecutive wins/losses
    max_consec_wins = trade_analysis.get('streak', {}).get('won', {}).get('longest', 0)
    max_consec_losses = trade_analysis.get('streak', {}).get('lost', {}).get('longest', 0)

    results = {
        'granularity': granularity,
        'bars': len(df),
        'date_range': f'{df.index[0].date()} → {df.index[-1].date()}',
        'initial_balance': initial_balance,
        'final_balance': round(final_value, 2),
        'total_return_pct': round(total_return, 2),
        'total_trades': total_trades,
        'won': won,
        'lost': lost,
        'win_rate': round(win_rate, 1),
        'gross_profit': round(gross_profit, 2),
        'gross_loss': round(gross_loss, 2),
        'net_pnl': round(net_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'max_consec_wins': max_consec_wins,
        'max_consec_losses': max_consec_losses,
        'sharpe_ratio': round(sharpe.get('sharperatio', 0), 3) if sharpe.get('sharperatio') else 0,
        'max_drawdown_pct': round(drawdown.get('max', {}).get('drawdown', 0), 2),
        'max_drawdown_dollars': round(drawdown.get('max', {}).get('moneydown', 0), 2),
        'sqn': round(sqn.get('sqn', 0), 2),
    }

    # Print results
    print(f'\n--- Results ---')
    print(f'Final Balance:    ${final_value:,.2f} ({total_return:+.2f}%)')
    print(f'Total Trades:     {total_trades} ({won}W / {lost}L)')
    print(f'Win Rate:         {win_rate:.1f}%')
    print(f'Net P&L:          ${net_pnl:+,.2f}')
    print(f'Avg Win:          ${avg_win:+,.2f}')
    print(f'Avg Loss:         ${avg_loss:+,.2f}')
    print(f'Sharpe Ratio:     {results["sharpe_ratio"]}')
    print(f'Max Drawdown:     {results["max_drawdown_pct"]}% (${results["max_drawdown_dollars"]:,.2f})')
    print(f'SQN:              {results["sqn"]}')
    print(f'Max Consec Wins:  {max_consec_wins}')
    print(f'Max Consec Losses:{max_consec_losses}')

    return results


if __name__ == '__main__':
    # Test multiple timeframes
    for gran, count in [('H1', 5000), ('H4', 2000), ('D', 500)]:
        try:
            run_backtest(granularity=gran, count=count)
        except Exception as e:
            print(f'\n{gran} backtest failed: {e}')
            import traceback
            traceback.print_exc()
