"""
Forex Trading Bot - Circuit Breaker
Monitors consecutive losses and pauses trading when threshold is hit.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_CONSECUTIVE_LOSS_LIMIT = 3
DEFAULT_DAILY_LOSS_PCT = 0.02  # 2% (tightened from 3%)
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_INITIAL_BALANCE = 100000.0  # Reference balance for % calculations


class CircuitBreaker:
    """
    Tracks trading performance and pauses trading when:
    1. Consecutive losses exceed threshold
    2. Daily loss exceeds threshold
    3. Manual pause triggered
    """

    def __init__(
        self,
        consecutive_loss_limit: int = DEFAULT_CONSECUTIVE_LOSS_LIMIT,
        daily_loss_pct: float = DEFAULT_DAILY_LOSS_PCT,
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
        state_file: str = "logs/circuit_breaker.json",
        disabled: bool = False,
    ):
        self.consecutive_loss_limit = consecutive_loss_limit
        self.daily_loss_pct = daily_loss_pct
        self.cooldown_minutes = cooldown_minutes
        self.state_file = Path(state_file)
        self._account_balance = DEFAULT_INITIAL_BALANCE
        self.state = self._load_state()
        self.disabled = disabled

    def set_account_balance(self, balance: float):
        """Update the reference balance for daily loss percentage calculation.
        Tracks the highest ever balance and trips if a 20% drawdown occurs.
        """
        self._account_balance = balance

        # Track highest ever balance
        if balance > self.state.get("highest_balance", 0):
            self.state["highest_balance"] = balance
            self._save_state()

        # Check for 20%+ drawdown from peak — hard trip, no auto-reset
        highest = self.state.get("highest_balance", balance)
        if highest > 0 and balance < 0.8 * highest:
            logger.warning(
                "Balance floor breached: $%.2f < 80%% of peak $%.2f",
                balance, highest,
            )
            if not self.state["is_tripped"]:
                self._trip(
                    f"Balance floor breached: ${balance:.2f} < 80% of peak ${highest:.2f}",
                    hard_trip=True,
                )

    def update_unrealized_pnl(self, unrealized_pnl: float):
        """
        Update unrealized P&L from open positions.
        This is called each cycle so the circuit breaker can account for
        paper losses on open trades.
        """
        self.state["unrealized_pnl"] = round(unrealized_pnl, 2)
        self._check_trip()
        self._save_state()

    def _load_state(self) -> dict:
        """Load state from disk with migration for new fields."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                # Reset daily stats if it's a new day
                last_date = state.get("date", "")
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if last_date != today:
                    state = self._fresh_state()
                else:
                    # Migrate: ensure all fresh-state keys exist
                    fresh = self._fresh_state()
                    for k, v in fresh.items():
                        state.setdefault(k, v)
                return state
            except (json.JSONDecodeError, KeyError):
                pass
        return self._fresh_state()

    def _fresh_state(self) -> dict:
        """Create fresh state for a new day."""
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "consecutive_losses": 0,
            "daily_pnl": 0.0,
            "total_trades_today": 0,
            "is_tripped": False,
            "tripped_at": None,
            "trip_reason": "",
            "cooldown_until": None,
            "escalation_level": 0,
            "highest_balance": self._account_balance,
            "trade_history": [],
        }

    def _save_state(self):
        """Persist state to disk (atomic write — crash-safe)."""
        from src.file_utils import atomic_save
        atomic_save(self.state_file, self.state)

    def record_trade(self, instrument: str, direction: str, pnl: float):
        """Record a completed trade result."""
        self.state["total_trades_today"] += 1
        self.state["daily_pnl"] = round(self.state["daily_pnl"] + pnl, 2)

        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "instrument": instrument,
            "direction": direction,
            "pnl": round(pnl, 2),
        }
        self.state["trade_history"].append(trade)

        if pnl < 0:
            self.state["consecutive_losses"] += 1
            logger.warning(
                "Loss recorded: %s %s P&L=%s | consecutive=%d/%d",
                instrument, direction, pnl,
                self.state["consecutive_losses"],
                self.consecutive_loss_limit,
            )
        else:
            if self.state["consecutive_losses"] > 0:
                logger.info("Consecutive loss streak reset (won %s)", pnl)
            self.state["consecutive_losses"] = 0

        # Check if we should trip
        self._check_trip()
        self._save_state()

    def check(self) -> tuple[bool, str]:
        """
        Check if trading is allowed.

        Returns:
            (is_allowed, reason) — reason is empty if allowed
        """
        if self.disabled:
            return True, ""

        state = self.state

        # Check if manually tripped
        if state["is_tripped"]:
            cooldown_str = state.get("cooldown_until")
            if cooldown_str:
                cooldown_until = datetime.fromisoformat(cooldown_str)
                now = datetime.now(timezone.utc)
                if now < cooldown_until:
                    remaining = (cooldown_until - now).total_seconds() / 60
                    return False, (
                        f"🔴 Circuit breaker ACTIVE — %s | "
                        f"Cooldown: %.0f min remaining"
                    ) % (state["trip_reason"], remaining)
                else:
                    # Cooldown expired, auto-reset
                    logger.info("Circuit breaker cooldown expired — resetting")
                    self._reset()
                    return True, ""
            else:
                return False, f"🔴 Circuit breaker ACTIVE — {state['trip_reason']}"

        return True, ""

    def _check_trip(self):
        """Check if circuit breaker should trip."""
        state = self.state

        # Consecutive losses
        if state["consecutive_losses"] >= self.consecutive_loss_limit:
            self._trip(
                f"{state['consecutive_losses']} consecutive losses"
            )
            return

        # Daily loss limit (percentage of account balance, including unrealized P&L)
        unrealized = state.get("unrealized_pnl", 0.0)
        total_daily_pnl = state["daily_pnl"] + unrealized
        if self._account_balance > 0:
            daily_loss_pct = abs(total_daily_pnl) / self._account_balance
            if total_daily_pnl < 0 and daily_loss_pct >= self.daily_loss_pct:
                self._trip(
                    f"Daily loss limit: {daily_loss_pct:.2%} "
                    f"(realized={state['daily_pnl']:.2f}, unrealized={unrealized:.2f}, "
                    f"balance={self._account_balance:.2f})"
                )

    def _trip(self, reason: str, hard_trip: bool = False):
        """Trip the circuit breaker."""
        self.state["is_tripped"] = True
        self.state["tripped_at"] = datetime.now(timezone.utc).isoformat()
        self.state["trip_reason"] = reason

        if hard_trip:
            # Hard trip — no auto-reset (no cooldown_until)
            self.state["cooldown_until"] = None
            logger.warning("🔴 CIRCUIT BREAKER HARD TRIPPED: %s (no auto-reset)", reason)
        else:
            # Cooldown with escalation
            level = self.state.get("escalation_level", 0)
            cooldowns = [self.cooldown_minutes, 120, 240]
            cooldown = cooldowns[min(level, len(cooldowns) - 1)]
            self.state["escalation_level"] = level + 1
            self.state["cooldown_until"] = (
                datetime.now(timezone.utc)
                + timedelta(minutes=cooldown)
            ).isoformat()
            logger.warning(
                "🔴 CIRCUIT BREAKER TRIPPED: %s | escalation=%d | cooldown=%d min",
                reason, level, cooldown,
            )

    def _reset(self):
        """Reset circuit breaker."""
        self.state["is_tripped"] = False
        self.state["tripped_at"] = None
        self.state["trip_reason"] = ""
        self.state["cooldown_until"] = None
        self.state["consecutive_losses"] = 0
        self._save_state()

    def manual_reset(self):
        """Manually reset the circuit breaker."""
        self._reset()
        logger.info("Circuit breaker manually reset")

    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        unrealized = self.state.get("unrealized_pnl", 0.0)
        return {
            "is_tripped": self.state["is_tripped"],
            "consecutive_losses": self.state["consecutive_losses"],
            "consecutive_limit": self.consecutive_loss_limit,
            "daily_pnl": round(self.state["daily_pnl"], 2),
            "unrealized_pnl": unrealized,
            "total_pnl": round(self.state["daily_pnl"] + unrealized, 2),
            "total_trades_today": self.state["total_trades_today"],
            "trip_reason": self.state["trip_reason"],
        }


# Singleton
circuit_breaker = CircuitBreaker(disabled=True)
