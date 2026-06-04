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
DEFAULT_DAILY_LOSS_PCT = 0.03  # 3%
DEFAULT_COOLDOWN_MINUTES = 60


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
    ):
        self.consecutive_loss_limit = consecutive_loss_limit
        self.daily_loss_pct = daily_loss_pct
        self.cooldown_minutes = cooldown_minutes
        self.state_file = Path(state_file)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                # Reset daily stats if it's a new day
                last_date = state.get("date", "")
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if last_date != today:
                    state = self._fresh_state()
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
            "trade_history": [],
        }

    def _save_state(self):
        """Persist state to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

    def record_trade(self, instrument: str, direction: str, pnl: float):
        """Record a completed trade result."""
        self.state["total_trades_today"] += 1
        self.state["daily_pnl"] += pnl

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

        # Daily loss limit
        # We need account balance to calculate percentage — use daily_pnl directly
        # The caller should pass balance info; here we use absolute threshold
        if state["daily_pnl"] < -1000:  # Absolute fallback
            self._trip(
                f"Daily loss limit: {state['daily_pnl']:.2f}"
            )

    def _trip(self, reason: str):
        """Trip the circuit breaker."""
        self.state["is_tripped"] = True
        self.state["tripped_at"] = datetime.now(timezone.utc).isoformat()
        self.state["trip_reason"] = reason
        self.state["cooldown_until"] = (
            datetime.now(timezone.utc)
            + timedelta(minutes=self.cooldown_minutes)
        ).isoformat()
        logger.warning("🔴 CIRCUIT BREAKER TRIPPED: %s", reason)

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
        return {
            "is_tripped": self.state["is_tripped"],
            "consecutive_losses": self.state["consecutive_losses"],
            "consecutive_limit": self.consecutive_loss_limit,
            "daily_pnl": round(self.state["daily_pnl"], 2),
            "total_trades_today": self.state["total_trades_today"],
            "trip_reason": self.state["trip_reason"],
        }


# Singleton
circuit_breaker = CircuitBreaker()
