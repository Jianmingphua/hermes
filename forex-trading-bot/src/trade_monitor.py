"""
Forex Trading Bot - Trade Monitor
Tracks open trades, detects closures, records P&L to circuit breaker.

P&L Tracking Strategy:
- For active trades: track unrealized P&L from OANDA's open positions each cycle
- When a position closes: calculate realized P&L from entry price vs close price
  (OANDA's Transaction API is unreliable on practice accounts, so we compute it)
- Store last known unrealized P&L so we have a fallback if close price unavailable
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
        """Persist trades to disk (atomic write — crash-safe)."""
        from src.file_utils import atomic_save
        atomic_save(self.trades_file, self.trades)

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
            "last_unrealized_pnl": 0.0,  # Track running unrealized P&L
        }
        self.trades["active_trades"].append(trade)
        self._save()
        logger.info("Trade registered for monitoring: %s %s @ %s", direction, instrument, entry_price)

    def update_unrealized_pnl(self):
        """
        Update unrealized P&L for all active trades from OANDA's open positions.
        Call this at the start of each cycle before checking for closures.
        """
        try:
            client = OandaClient()
            oanda_positions = client.get_open_positions()

            for p in oanda_positions:
                inst = p["instrument"]
                pnl = float(p.get("unrealized_pnl", 0))

                # Determine direction from position side
                if abs(int(p["long_units"])) > 0:
                    direction = "BUY"
                elif abs(int(p["short_units"])) > 0:
                    direction = "SELL"
                else:
                    continue

                # Update matching active trade
                for trade in self.trades["active_trades"]:
                    if trade["instrument"] == inst and trade["direction"] == direction:
                        trade["last_unrealized_pnl"] = round(pnl, 2)

            self._save()
        except Exception as e:
            logger.warning("Could not update unrealized P&L: %s", e)

    def reconcile_with_oanda(self):
        """
        On bot startup, re-register positions that exist on OANDA but aren't
        in local state. This prevents UNKNOWN exits when the bot restarts
        between cycles.

        Called at the start of each cycle before check_closed_trades().
        """
        try:
            client = OandaClient()
            oanda_positions = client.get_open_positions()

            # Build set of locally tracked (instrument, direction) pairs
            locally_tracked = set()
            for t in self.trades.get("active_trades", []):
                locally_tracked.add((t["instrument"], t["direction"]))

            # Find OANDA positions not in local state
            for p in oanda_positions:
                inst = p["instrument"]
                long_units = abs(int(p.get("long_units", 0)))
                short_units = abs(int(p.get("short_units", 0)))

                if long_units > 0 and (inst, "BUY") not in locally_tracked:
                    # Re-register this position
                    trade = {
                        "instrument": inst,
                        "direction": "BUY",
                        "units": long_units,
                        "entry_price": float(p.get("average_price", 0)),
                        "stop_loss": 0,  # Unknown after restart
                        "take_profit": 0,  # Unknown after restart
                        "order_id": p.get("id", ""),
                        "opened_at": datetime.now(timezone.utc).isoformat(),  # Approximate
                        "status": "open",
                        "last_unrealized_pnl": float(p.get("unrealized_pnl", 0)),
                    }
                    self.trades["active_trades"].append(trade)
                    logger.info(
                        "Reconciled missing position: %s BUY %d units @ %s",
                        inst, long_units, trade["entry_price"]
                    )

                if short_units > 0 and (inst, "SELL") not in locally_tracked:
                    trade = {
                        "instrument": inst,
                        "direction": "SELL",
                        "units": -short_units,
                        "entry_price": float(p.get("average_price", 0)),
                        "stop_loss": 0,
                        "take_profit": 0,
                        "order_id": p.get("id", ""),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "status": "open",
                        "last_unrealized_pnl": float(p.get("unrealized_pnl", 0)),
                    }
                    self.trades["active_trades"].append(trade)
                    logger.info(
                        "Reconciled missing position: %s SELL %d units @ %s",
                        inst, short_units, trade["entry_price"]
                    )

            if oanda_positions:
                self._save()

        except Exception as e:
            logger.warning("Could not reconcile with OANDA: %s", e)

    def check_closed_trades(self, realized_pnl_pool: float = None) -> list[dict]:
        """
        Check OANDA for closed trades.
        Compare active trades against current open positions.
        When a trade is no longer open, calculate realized P&L.

        Args:
            realized_pnl_pool: Optional total realized P&L since last cycle
                (from BalanceTracker). When set, used to correct 0.0 P&L on
                trades that closed unattended (cron gaps, external closures).
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
                if abs(int(p["long_units"])) > 0:
                    currently_open.add((inst, "BUY"))
                    oanda_pnl[(inst, "BUY")] = pnl
                if abs(int(p["short_units"])) > 0:
                    currently_open.add((inst, "SELL"))
                    oanda_pnl[(inst, "SELL")] = pnl

            # Check which tracked trades are no longer open
            still_active = []
            closing_trades = []  # Trades that are confirmed closing (not duplicates)
            for trade in self.trades["active_trades"]:
                key = (trade["instrument"], trade["direction"])
                if key not in currently_open:
                    # Check if already recorded (avoid duplicates)
                    dedup_tolerance = 0.01 if "JPY" in trade["instrument"] else 0.0001
                    already_closed = any(
                        t["instrument"] == trade["instrument"]
                        and t["direction"] == trade["direction"]
                        and t.get("status") == "closed"
                        and abs(t.get("entry_price", 0) - trade.get("entry_price", 0)) < dedup_tolerance
                        for t in self.trades.get("closed_trades", [])
                    )
                    if not already_closed:
                        closing_trades.append(trade)
                else:
                    still_active.append(trade)

            # Two-pass P&L attribution to prevent double-counting the pool:
            # Pass 1: count how many closing trades need the pool (last_unrealized was 0.0)
            pool_unknown_trades = [
                t for t in closing_trades
                if t.get("last_unrealized_pnl", 0) == 0.0
            ]
            pool_remaining = realized_pnl_pool

            # Pass 2: attribute fair-share P&L to each closing trade
            n_pool_unknown = len(pool_unknown_trades)
            for trade in closing_trades:
                realized_pnl = trade.get("last_unrealized_pnl", 0)

                if realized_pnl == 0.0 and realized_pnl_pool is not None and n_pool_unknown > 0:
                    # Fair share: divide the pool equally, cap at remaining
                    fair_share = realized_pnl_pool / n_pool_unknown
                    attributed = min(fair_share, pool_remaining) if pool_remaining is not None else 0.0
                    realized_pnl = round(attributed, 2)
                    if pool_remaining is not None:
                        pool_remaining -= realized_pnl
                    logger.info(
                        "Using balance delta for %s %s: P&L=%.2f "
                        "(pool %d-way split — last_unrealized was 0, cron gap detected)",
                        trade["direction"], trade["instrument"], realized_pnl,
                        n_pool_unknown,
                    )

                closed_trade = self._close_trade(trade, realized_pnl)
                closed.append(closed_trade)

            self.trades["active_trades"] = still_active
            self._save()

        except Exception as e:
            logger.warning("Could not check closed trades: %s", e)

        return closed

    def _close_trade(self, trade: dict, realized_pnl: float = 0) -> dict:
        """Record a closed trade with realized P&L and update circuit breaker."""
        now = datetime.now(timezone.utc)

        closed_trade = {
            **trade,
            "closed_at": now.isoformat(),
            "status": "closed",
            "pnl": round(realized_pnl, 2),
        }

        # Add to closed trades history
        self.trades["closed_trades"].append(closed_trade)

        # Update circuit breaker
        circuit_breaker.record_trade(
            instrument=trade["instrument"],
            direction=trade["direction"],
            pnl=realized_pnl,
        )

        logger.info(
            "Trade closed: %s %s | P&L: %s | CB losses: %d",
            trade["direction"], trade["instrument"], realized_pnl,
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
