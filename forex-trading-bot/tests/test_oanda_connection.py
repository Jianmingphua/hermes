"""
Test OANDA connection and basic functionality.
Run with: cd /opt/hermes/forex-trading-bot && python -m pytest tests/ -v
"""

import os
import sys
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfig:
    """Test configuration loading."""

    def test_config_loads(self):
        from src.config import config
        assert config is not None

    def test_oanda_key_set(self):
        from src.config import config
        assert config.OANDA_API_KEY != "", "OANDA_API_KEY not set"
        assert len(config.OANDA_API_KEY) > 10, "OANDA_API_KEY looks too short"

    def test_default_instruments(self):
        from src.config import config
        assert len(config.DEFAULT_INSTRUMENTS) > 0
        assert "EUR_USD" in config.DEFAULT_INSTRUMENTS

    def test_risk_params(self):
        from src.config import config
        assert 0 < config.RISK_PER_TRADE < 0.1
        assert 0 < config.MAX_DAILY_LOSS < 0.2


class TestOandaConnection:
    """Test OANDA API connectivity. Requires valid API key."""

    def test_client_initializes(self):
        from src.oanda_client import OandaClient
        client = OandaClient()
        assert client is not None
        assert client.api_key != ""

    def test_get_account_summary(self):
        from src.oanda_client import OandaClient
        client = OandaClient()
        # Auto-discover account ID if not set
        if not client.account_id:
            client.account_id = client.get_account_id()
        summary = client.get_account_summary()
        assert "balance" in summary
        assert "currency" in summary
        assert summary["balance"] >= 0
        print(f"\n✅ Account: {summary['balance']} {summary['currency']}")
        print(f"   NAV: {summary['nav']}")
        print(f"   Open trades: {summary['open_trades']}")

    def test_get_candles(self):
        from src.oanda_client import OandaClient
        client = OandaClient()
        df = client.get_candles("EUR_USD", "H1", 100)
        assert not df.empty, "No candles returned"
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "close" in df.columns
        assert "volume" in df.columns
        print(f"\n✅ Candles: {len(df)} rows")
        print(f"   Range: {df.index[0]} → {df.index[-1]}")
        print(f"   Latest close: {df.iloc[-1]['close']}")

    def test_get_current_price(self):
        from src.oanda_client import OandaClient
        client = OandaClient()
        if not client.account_id:
            client.account_id = client.get_account_id()
        price = client.get_current_price("EUR_USD")
        assert "bid" in price
        assert "ask" in price
        assert price["ask"] > price["bid"]
        spread_pips = (price["ask"] - price["bid"]) * 10000
        print(f"\n✅ EUR/USD: bid={price['bid']} ask={price['ask']}")
        print(f"   Spread: {spread_pips:.1f} pips")

    def test_get_instruments(self):
        from src.oanda_client import OandaClient
        from oandapyV20.exceptions import V20Error
        client = OandaClient()
        if not client.account_id:
            client.account_id = client.get_account_id()
        try:
            instruments = client.get_instruments()
            assert len(instruments) > 0
            names = [i["name"] for i in instruments]
            assert "EUR_USD" in names
            print(f"\n✅ Available instruments: {len(instruments)}")
            forex = [i for i in instruments if i["type"] == "CURRENCY"]
            print(f"   Forex pairs: {len(forex)}")
        except V20Error as e:
            # OANDA instruments endpoint can return 522 Cloudflare errors
            pytest.skip(f"OANDA instruments endpoint unavailable: {e}")


class TestIndicators:
    """Test technical indicator calculations."""

    def test_add_indicators(self):
        from src.oanda_client import OandaClient
        from src.indicators import TechnicalIndicators
        from oandapyV20.exceptions import V20Error

        client = OandaClient()
        try:
            df = client.get_candles("EUR_USD", "H1", 200)
        except V20Error:
            pytest.skip("OANDA API temporarily unavailable (522)")
        df = TechnicalIndicators.add_all(df)

        # Check key indicators exist
        for col in ["ema_20", "ema_50", "rsi_14", "atr_14"]:
            assert col in df.columns, f"Missing column: {col}"

        print(f"\n✅ Indicators added: {len(df.columns)} total columns")

    def test_generate_signal(self):
        from src.oanda_client import OandaClient
        from src.indicators import TechnicalIndicators
        from oandapyV20.exceptions import V20Error

        client = OandaClient()
        try:
            df = client.get_candles("EUR_USD", "H1", 200)
        except V20Error:
            pytest.skip("OANDA API temporarily unavailable (522)")
        df = TechnicalIndicators.add_all(df)
        signal = TechnicalIndicators.generate_signal(df)

        assert "signal" in signal
        assert signal["signal"] in ("BUY", "SELL", "HOLD")
        assert 0 <= signal["confidence"] <= 1
        assert "confirmations" in signal
        assert "tier" in signal
        assert signal["tier"] in ("HIGH", "MEDIUM", "LOW", "NONE")
        assert 0 <= signal["confirmations"] <= 4
        assert len(signal["reasons"]) > 0

        print(f"\n✅ Signal: {signal['signal']} (confidence={signal['confidence']:.2f})")
        for r in signal["reasons"][:5]:
            print(f"   • {r}")


class TestSignalGenerator:
    """Test the full signal generation pipeline."""

    def test_analyze(self):
        from src.oanda_client import OandaClient
        from src.signal_generator import SignalGenerator

        client = OandaClient()
        gen = SignalGenerator(client)
        result = gen.analyze("EUR_USD", "H1", 200)

        assert "signal" in result
        assert "instrument" in result
        assert result["instrument"] == "EUR_USD"
        assert "tier" in result
        assert "confirmations" in result

        print(f"\n✅ Analysis: {result['signal']} | confidence={result.get('confidence', 0):.2f}")
        if "current_price" in result:
            print(f"   Price: {result['current_price']['bid']} / {result['current_price']['ask']}")
        if "suggested_stop_loss" in result:
            print(f"   SL: {result['suggested_stop_loss']} | TP: {result['suggested_take_profit']}")

    def test_scan_pairs(self):
        from src.oanda_client import OandaClient
        from src.signal_generator import SignalGenerator

        client = OandaClient()
        gen = SignalGenerator(client)
        results = gen.scan_pairs(["EUR_USD", "GBP_USD"], "H1")

        assert len(results) == 2
        for r in results:
            assert "signal" in r

        print(f"\n✅ Scan results:")
        for r in results:
            print(f"   {r['instrument']}: {r['signal']} ({r.get('confidence', 0):.2f})")


class TestRiskManager:
    """Test risk management logic."""

    def test_validate_signal(self):
        from src.risk_manager import RiskManager

        rm = RiskManager()

        # Good signal
        good = {"signal": "BUY", "confidence": 0.7}
        issues = rm.validate_signal(good)
        assert len(issues) == 0, f"Unexpected issues: {issues}"

        # Low confidence
        bad = {"signal": "BUY", "confidence": 0.1}
        issues = rm.validate_signal(bad)
        assert len(issues) > 0

        print("\n✅ Risk validation working")

    def test_position_sizing(self):
        from src.risk_manager import RiskManager

        rm = RiskManager(risk_per_trade=0.01)
        units = rm.calculate_position_size(
            account_balance=10000,
            entry_price=1.0850,
            stop_loss_price=1.0800,
            instrument="EUR_USD",
        )
        assert units > 0
        print(f"\n✅ Position size: {units} units (balance=10000, risk=1%)")

    def test_trade_setup(self):
        from src.risk_manager import RiskManager

        rm = RiskManager()
        signal = {
            "signal": "BUY",
            "confidence": 0.7,
            "instrument": "EUR_USD",
            "atr_14": 0.0010,
            "current_price": {"bid": 1.0850, "ask": 1.0852},
            "reasons": ["EMA crossover"],
        }
        setup = rm.build_trade_setup(signal, account_balance=10000)
        assert setup is not None
        assert setup.direction == "BUY"
        assert setup.units > 0
        assert setup.stop_loss < setup.entry_price
        assert setup.take_profit > setup.entry_price

        print(f"\n✅ Trade setup: {setup.direction} {setup.units} units")
        print(f"   Entry: {setup.entry_price} | SL: {setup.stop_loss} | TP: {setup.take_profit}")
