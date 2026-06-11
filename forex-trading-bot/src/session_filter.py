"""
Forex Trading Bot - Session Filter
Only trade during high-liquidity sessions.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Forex session times (UTC)
SESSIONS = {
    "sydney":    {"start": 22, "end": 7,  "overlap": []},
    "tokyo":     {"start": 0,  "end": 9,  "overlap": ["sydney"]},
    "london":    {"start": 7,  "end": 16, "overlap": ["tokyo", "new_york"]},
    "new_york":  {"start": 12, "end": 21, "overlap": ["london"]},
}

# Best sessions for each pair (based on liquidity/volatility)
PAIR_BEST_SESSIONS = {
    "EUR_USD": ["london", "new_york"],
    "GBP_USD": ["london", "new_york"],
    "USD_JPY": ["tokyo", "london"],
    "AUD_USD": ["sydney", "tokyo"],
    "NZD_USD": ["sydney", "tokyo"],
    "USD_CAD": ["new_york", "london"],
    "USD_CHF": ["london", "new_york"],
    "EUR_GBP": ["london", "new_york"],
    "EUR_JPY": ["london", "tokyo"],
    "GBP_JPY": ["london", "tokyo"],
    # Metals
    "XAG_USD": ["london", "new_york"],
    "XAU_USD": ["london", "new_york"],
}

# Minimum session overlap (hours) to consider "active"
MIN_SESSION_HOURS = 2

# Blocked hours (UTC) — low-quality entry windows to avoid
#   07:00-08:00 UTC -> London open churn (spikes, reversals, thin quotes)
#   13:00-14:00 UTC -> London close churn (wider spreads, erratic moves)
#   16:00-18:00 UTC -> Asian session thin liquidity (low volume, choppy)
#   21:00-22:00 UTC -> NY close illiquidity (wide spreads, erratic moves)
BLOCKED_HOURS = [
    (7, 8),    # London open churn
    (13, 14),  # London close churn
    (16, 18),  # Asian thin liquidity
    (21, 22),  # NY close illiquidity
]


class SessionFilter:
    """Determines if current time is suitable for trading a given pair."""

    # ── Time-of-Day Block ─────────────────────────────────────────

    def is_blocked_hour(self, hour: int) -> tuple[bool, str]:
        """
        Check if the current UTC hour falls inside a blocked window.

        Blocked windows:
            07:00-08:00 UTC -> London open churn
            21:00-22:00 UTC -> NY close illiquidity

        Returns:
            (is_blocked, reason) — reason is empty if not blocked
        """
        for start, end in BLOCKED_HOURS:
            if start <= hour < end:
                labels = {
                    (7, 8): "London open churn",
                    (13, 14): "London close churn",
                    (16, 18): "Asian thin liquidity",
                    (21, 22): "NY close illiquidity",
                }
                label = labels.get((start, end), "Blocked window")
                return True, (
                    f"⏸ Blocked hour {hour:02d}:00 UTC — {label} "
                    f"({start:02d}:00-{end:02d}:00 UTC)"
                )
        return False, ""

    def is_good_time(self, instrument: str) -> tuple[bool, str]:
        """
        Check if current UTC time is a good trading session for this pair.
        Uses per-pair custom session windows if available, otherwise falls back
        to default PAIR_BEST_SESSIONS mapping.

        Returns:
            (is_good, reason) — reason is empty if good
        """
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Time-of-day block check
        blocked, block_reason = self.is_blocked_hour(hour)
        if blocked:
            return False, block_reason

        # Get best sessions for this pair
        best_sessions = PAIR_BEST_SESSIONS.get(instrument, ["london", "new_york"])

        # Check if we're in any of the best sessions
        active_sessions = self._get_active_sessions(hour)

        for session in best_sessions:
            if session in active_sessions:
                return True, ""

        # Not in a good session
        next_session = self._get_next_good_session(hour, best_sessions)
        return False, (
            f"⏸ Outside best session for {instrument} "
            f"(best: {', '.join(best_sessions)}). "
            f"Next: {next_session}"
        )

    def is_good_time_custom(self, instrument: str, session_start: int, session_end: int) -> tuple[bool, str]:
        """
        Check if current UTC time is within a custom session window.
        Used by per-pair optimized parameters.
        Also checks blocked hours (London open churn, NY close illiquidity).

        Args:
            instrument: Pair name (for logging)
            session_start: Start hour (UTC, inclusive)
            session_end: End hour (UTC, exclusive)

        Returns:
            (is_good, reason)
        """
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Time-of-day block check (applies regardless of session window)
        blocked, block_reason = self.is_blocked_hour(hour)
        if blocked:
            return False, block_reason

        if session_start <= session_end:
            in_session = session_start <= hour < session_end
        else:  # Wraps midnight (e.g., 22-7)
            in_session = hour >= session_start or hour < session_end

        if in_session:
            return True, ""

        return False, (
            f"⏸ Outside session window for {instrument} "
            f"({session_start:02d}:00-{session_end:02d}:00 UTC). "
            f"Current: {hour:02d}:00 UTC"
        )

    def _get_active_sessions(self, hour: int) -> list[str]:
        """Get list of currently active sessions."""
        active = []
        for name, sess in SESSIONS.items():
            start, end = sess["start"], sess["end"]
            if start <= end:
                if start <= hour < end:
                    active.append(name)
            else:  # Wraps midnight (e.g., Sydney 22-7)
                if hour >= start or hour < end:
                    active.append(name)
        return active

    def _get_next_good_session(self, hour: int, best_sessions: list[str]) -> str:
        """Find the next good session start time."""
        for offset in range(1, 24):
            future_hour = (hour + offset) % 24
            active = self._get_active_sessions(future_hour)
            for session in best_sessions:
                if session in active:
                    return f"{session} at {future_hour:02d}:00 UTC"
        return "unknown"

    @staticmethod
    def get_session_info() -> str:
        """Get current session status for all pairs."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        active = SessionFilter._get_active_sessions_static(hour)

        lines = [f"Current UTC hour: {hour:02d}:00", "Active sessions: " + ", ".join(active), ""]

        for pair, sessions in PAIR_BEST_SESSIONS.items():
            in_session = any(s in active for s in sessions)
            status = "✅" if in_session else "⏸"
            lines.append(f"  {status} {pair}: best={', '.join(sessions)}")

        return "\n".join(lines)

    @staticmethod
    def _get_active_sessions_static(hour: int) -> list[str]:
        active = []
        for name, sess in SESSIONS.items():
            start, end = sess["start"], sess["end"]
            if start <= end:
                if start <= hour < end:
                    active.append(name)
            else:
                if hour >= start or hour < end:
                    active.append(name)
        return active


# Singleton
session_filter = SessionFilter()
