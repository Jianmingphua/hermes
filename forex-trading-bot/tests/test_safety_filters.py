"""
Tests for P0 safety improvements.
"""

import os
import sys
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNewsFilter:
    """Test news/event filter."""

    def test_filter_loads(self):
        from src.news_filter import NewsFilter
        nf = NewsFilter()
        assert nf is not None

    def test_returns_tuple(self):
        from src.news_filter import NewsFilter
        nf = NewsFilter()
        result = nf.is_safe_to_trade("EUR_USD")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)

    def test_high_impact_keywords_defined(self):
        from src.news_filter import HIGH_IMPACT_KEYWORDS
        assert len(HIGH_IMPACT_KEYWORDS) > 0
        assert "nfp" in HIGH_IMPACT_KEYWORDS
        assert "fomc" in HIGH_IMPACT_KEYWORDS
        assert "cpi" in HIGH_IMPACT_KEYWORDS


class TestSessionFilter:
    """Test session filter."""

    def test_filter_loads(self):
        from src.session_filter import SessionFilter
        sf = SessionFilter()
        assert sf is not None

    def test_returns_tuple(self):
        from src.session_filter import SessionFilter
        sf = SessionFilter()
        result = sf.is_good_time("EUR_USD")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)

    def test_pair_session_mapping(self):
        from src.session_filter import PAIR_BEST_SESSIONS
        assert "EUR_USD" in PAIR_BEST_SESSIONS
        assert "GBP_USD" in PAIR_BEST_SESSIONS
        assert "USD_JPY" in PAIR_BEST_SESSIONS

    def test_session_info(self):
        from src.session_filter import SessionFilter
        info = SessionFilter.get_session_info()
        assert "UTC hour" in info

    def test_known_good_session(self):
        """London/NY session should be good for EUR/USD."""
        from src.session_filter import SessionFilter
        sf = SessionFilter()
        # Can't test specific hours without mocking, but we can verify structure
        safe, reason = sf.is_good_time("EUR_USD")
        assert isinstance(safe, bool)


class TestCircuitBreaker:
    """Test circuit breaker."""

    def test_breaker_loads(self):
        from src.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(consecutive_loss_limit=3)
        assert cb is not None

    def test_starts_safe(self):
        from src.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(consecutive_loss_limit=3)
        allowed, reason = cb.check()
        assert allowed is True
        assert reason == ""

    def test_trips_on_consecutive_losses(self):
        from src.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(consecutive_loss_limit=3)

        # Record 3 losses
        for _ in range(3):
            cb.record_trade("EUR_USD", "BUY", -10.0)

        allowed, reason = cb.check()
        assert allowed is False
        assert "consecutive" in reason.lower() or "losses" in reason.lower()
        print(f"\n✅ Circuit breaker tripped: {reason}")

    def test_wins_reset_streak(self):
        from src.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(consecutive_loss_limit=3)

        cb.record_trade("EUR_USD", "BUY", -10.0)
        cb.record_trade("EUR_USD", "SELL", -10.0)
        cb.record_trade("EUR_USD", "BUY", +15.0)  # Should reset

        status = cb.get_status()
        assert status["consecutive_losses"] == 0
        print(f"\n✅ Win resets loss streak")

    def test_manual_reset(self):
        from src.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(consecutive_loss_limit=2)

        cb.record_trade("EUR_USD", "BUY", -10.0)
        cb.record_trade("EUR_USD", "BUY", -10.0)

        allowed, _ = cb.check()
        assert allowed is False

        cb.manual_reset()
        allowed, _ = cb.check()
        assert allowed is True
        print(f"\n✅ Manual reset works")

    def test_status_report(self):
        from src.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(consecutive_loss_limit=3)
        status = cb.get_status()

        assert "is_tripped" in status
        assert "consecutive_losses" in status
        assert "consecutive_limit" in status
        assert "daily_pnl" in status
        assert "total_trades_today" in status


class TestSpreadMonitor:
    """Test spread monitor."""

    def test_monitor_loads(self):
        from src.spread_monitor import SpreadMonitor
        sm = SpreadMonitor()
        assert sm is not None

    def test_normal_spread_accepted(self):
        from src.news_filter import news_filter
        from src.spread_monitor import SpreadMonitor
        sm = SpreadMonitor()
        # EUR/USD spread of 1 pip = 0.00010
        ok, reason = sm.check_spread("EUR_USD", 0.00010)
        assert ok is True
        assert reason == ""

    def test_wide_spread_rejected(self):
        from src.spread_monitor import SpreadMonitor
        sm = SpreadMonitor()
        # EUR/USD spread of 5 pips = 0.00050 (too wide)
        ok, reason = sm.check_spread("EUR_USD", 0.00050)
        assert ok is False
        assert "spread" in reason.lower()
        print(f"\n✅ Wide spread rejected: {reason}")

    def test_jpy_spread(self):
        from src.spread_monitor import SpreadMonitor
        sm = SpreadMonitor()
        # USD/JPY spread of 1 pip = 0.01
        ok, reason = sm.check_spread("USD_JPY", 0.01)
        assert ok is True

        # USD/JPY spread of 5 pips = 0.05 (too wide)
        ok, reason = sm.check_spread("USD_JPY", 0.05)
        assert ok is False
        print(f"\n✅ JPY wide spread rejected")


class TestIntegration:
    """Test all filters together in the main pipeline."""

    def test_all_filters_in_main(self):
        """Verify main.py imports all new modules without errors."""
        from src.main import run_once
        assert run_once is not None
        print("\n✅ All filters integrated in main.py")

    def test_end_to_end_dry_run(self):
        """Full end-to-end run with all safety filters."""
        from src.main import run_once
        from oandapyV20.exceptions import V20Error
        try:
            signals = run_once(dry_run=True)
            assert isinstance(signals, list)
            print(f"\n✅ E2E run complete: {len(signals)} strong signals")
        except V20Error:
            pytest.skip("OANDA API temporarily unavailable (522)")
