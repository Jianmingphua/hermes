"""
Forex Trading Bot - Balance Tracker
Tracks account balance across cycles to compute realized P&L.
Provides a reliable alternative to per-trade P&L tracking which
breaks when cron cycles are missed (positions close unattended).

Logic:
  - At each cycle start, fetch current balance from OANDA
  - Compute delta = current_balance - last_known_balance
  - Delta reflects net realized P&L of all trades closed since last cycle
  - Store current balance as last_known for next cycle
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.oanda_client import OandaClient

logger = logging.getLogger(__name__)


class BalanceTracker:
    """Tracks account balance across cron cycles for reliable P&L attribution."""

    def __init__(self, state_file: str = "logs/balance_tracker.json"):
        self.state_file = Path(state_file)
        self.last_balance = None
        self.current_balance = None
        self._load()

    def _load(self):
        """Load last known balance from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                self.last_balance = float(state.get("balance", 0))
                logger.debug(
                    "BalanceTracker loaded: last_balance=%s", self.last_balance
                )
            except (json.JSONDecodeError, ValueError, KeyError):
                self.last_balance = None
        else:
            self.last_balance = None

    def _save(self, balance: float):
        """Persist balance to disk (atomic write — crash-safe)."""
        from src.file_utils import atomic_save
        atomic_save(self.state_file, {
            "balance": round(balance, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def fetch_current(self) -> float:
        """Fetch current balance from OANDA. Returns balance amount."""
        try:
            client = OandaClient()
            account = client.get_account_summary()
            self.current_balance = float(account["balance"])
            return self.current_balance
        except Exception as e:
            logger.warning("BalanceTracker: could not fetch balance: %s", e)
            return self.last_balance or 0.0

    def compute_realized_pnl(self) -> float:
        """
        Compute realized P&L since last cycle = current - last.
        Positive means profit, negative means loss.
        Returns 0 if no prior balance data.
        """
        if self.last_balance is None:
            self.last_balance = self.current_balance
            return 0.0

        delta = self.current_balance - self.last_balance
        logger.info(
            "BalanceTracker: realized P&L = %.2f (%.2f → %.2f)",
            delta, self.last_balance, self.current_balance,
        )
        return round(delta, 2)

    def persist(self):
        """Save current balance as last known for next cycle."""
        if self.current_balance is not None:
            self.last_balance = self.current_balance
            self._save(self.current_balance)

    def reset(self, balance: float):
        """Hard reset the tracker to a specific balance value."""
        self.last_balance = balance
        self.current_balance = balance
        self._save(balance)
        logger.info("BalanceTracker reset to %.2f", balance)


# ── Singleton ──────────────────────────────────────────────────
balance_tracker = BalanceTracker()
