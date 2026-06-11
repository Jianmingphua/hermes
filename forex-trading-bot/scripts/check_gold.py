import pandas as pd
import talib
from src.oanda_client import OandaClient

client = OandaClient()

# Fetch H1 candles for ATR calculation
df = client.get_candles('XAU_USD', 'H1', 500)
df['atr'] = talib.ATR(df['high'], df['low'], df['close'], timeperiod=14)
print(f'XAU/USD H1 ATR(14) current: ${df["atr"].iloc[-1]:.2f}')
print(f'XAU/USD H1 ATR avg: ${df["atr"].mean():.2f}')
print(f'XAU/USD current: ${df["close"].iloc[-1]:.2f}')

# Also check M15
df15 = client.get_candles('XAU_USD', 'M15', 500)
df15['atr'] = talib.ATR(df15['high'], df15['low'], df15['close'], timeperiod=14)
print(f'XAU/USD M15 ATR(14) current: ${df15["atr"].iloc[-1]:.2f}')
print(f'XAU/USD M15 ATR avg: ${df15["atr"].mean():.2f}')

# Daily range
daily_high = df['high'].rolling(24).max()
daily_low = df['low'].rolling(24).min()
daily_range = (daily_high - daily_low).dropna()
print(f'XAU/USD avg daily range: ${daily_range.mean():.2f}')
print(f'XAU/USD max daily range: ${daily_range.max():.2f}')
