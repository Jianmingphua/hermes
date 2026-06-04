"""
Forex Trading Bot - Trade Monitor
Tracks open trades, detects closures, records P&L to circuit breaker.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.oanda_client import OandaClient
from src.circuit_breaker import circuit_breaker

logger = logging.getLogger(__name__)


class TradeMonitor:
    """
    Monitors trades placed by the bot.
    Detects when positions close (SL/TP hit, manual close) and records results.
    """

    def __init__(self, trades_file: str = "logs/active_trades.json"):
        self.trades_file = Path(trades_file)
        self.trades = self._load()

    def _load(self) -> dict:
        """Load active trades from disk."""
        if self.trades_file.exists():
            try:
                with open(self.trades_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"active_trades": [], "closed_trades": []}

    def _save(self):
        """Persist trades to disk."""
        self.trades_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trades_file, "w") as f:
            json.dump(self.trades, f, indent=2, default=str)

    def register_trade(self, instrument: str, direction: str, units: int,
                       entry_price: float, sl: float, tp: float,
                       order_id: str = None):
        """Register a newly placed trade for monitoring."""
        trade = {
            "instrument": instrument,
            "direction": direction,
            "units": units,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit": tp,
            "order_id": order_id,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "status": "open",
        }
        self.trades["active_trades"].append(trade)
        self._save()
        logger.info("Trade registered for monitoring: %s %s @ %s", direction, instrument, entry_price)

    def check_closed_trades(self) -> list[dict]:
        """
        Check OANDA for closed trades.
        Compare active trades against current open positions.
        When a trade is no longer open, record its result.

        Returns:
            List of newly closed trades with P&L.
        """
        closed = []
        try:
            client = OandaClient()
            oanda_positions = client.get_open_positions()

            # Build set of currently open (instrument, direction) pairs
            currently_open = set()
            oanda_pnl = {}
            for p in oanda_positions:
                inst = p["instrument"]
                pnl = float(p.get("unrealized_pnl", 0))
                if int(p["long_units"]) > 0:
                    currently_open.add((inst, "BUY"))
                    oanda_pnl[(inst, "BUY")] = pnl
                if int(p["short_units"]) > 0:
                    currently_open.add((inst, "SELL"))
                    oanda_pnl[(inst, "SELL")] = pnl

            # Check which tracked trades are no longer open
            still_active = []
            for trade in self.trades["active_trades"]:
                key = (trade["instrument"], trade["direction"])
                if key not in currently_open:
                    # Trade closed — calculate result
                    closed_trade = self._close_trade(trade, oanda_pnl.get(key, 0))
                    closed.append(closed_trade)
                else:
                    still_active.append(trade)

            self.trades["active_trades"] = still_active
            self._save()

        except Exception as e:
            logger.warning("Could not check closed trades: %s", e)

        return closed

    def _close_trade(self, trade: dict, unrealized_pnl: float = 0) -> dict:
        """Record a closed trade and update circuit breaker."""
        now = datetime.now(timezone.utc)

        # Calculate P&L
        # For a more accurate P&L, we'd need the fill price from OANDA
        # For now, use unrealized P&L at time of detection
        pnl = unrealized_pnl

        closed_trade = {
            **trade,
            "closed_at": now.isoformat(),
            "status": "closed",
            "pnl": round(pnl, 2),
        }

        # Add to closed trades history
        self.trades["closed_trades"].append(closed_trade)

        # Update circuit breaker
        circuit_breaker.record_trade(
            instrument=trade["instrument"],
            direction=trade["direction"],
            pnl=pnl,
        )

        logger.info(
            "Trade closed: %s %s | P&L: %s | CB losses: %d",
            trade["direction"], trade["instrument"], pnl,
            circuit_breaker.get_status()["consecutive_losses"],
        )

        return closed_trade

    def get_active_count(self) -> int:
        """Get number of actively monitored trades."""
        return len(self.trades.get("active_trades", []))

    def get_active_trades(self) -> list[dict]:
        """Get list of active trades."""
        return self.trades.get("active_trades", [])

    def get_trade_history(self, limit: int = 20) -> list[dict]:
        """Get recent closed trades."""
        closed = self.trades.get("closed_trades", [])
        return closed[-limit:]

    def get_summary(self) -> dict:
        """Get trading summary."""
        closed = self.trades.get("closed_trades", [])
        if not closed:
            return {"total_trades": 0, "win_rate": 0, "total_pnl": 0}

        wins = [t for t in closed if t.get("pnl", 0) > 0]
        losses = [t for t in closed if t.get("pnl", 0) < 0]
        total_pnl = sum(t.get("pnl", 0) for t in closed)

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(closed), 2) if closed else 0,
            "active_trades": len(self.trades.get("active_trades", [])),
        }


# Singleton
trade_monitor = TradeMonitor()
