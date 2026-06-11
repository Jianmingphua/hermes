"""
Forex Trading Bot - Trailing Stop + Partial Close Manager

Manages two profit-protection mechanisms:
  1. Partial close: close 50% of position at +1×ATR profit
  2. Trailing stop:
     - At +1×ATR profit → move stop loss to breakeven
     - At +2×ATR profit → trail at 1.5×ATR from current price

State is persisted to disk so it survives across cron cycles.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.oanda_client import OandaClient

logger = logging.getLogger(__name__)


# ── Per-instrument state keys ─────────────────────────────────────
# Stored in a JSON file keyed by "{instrument}_{side}".

# Fields tracked per position:
#   instrument          str
#   side                "long" | "short"
#   entry_price         float
#   atr_at_entry        float  (ATR(14) value when the trade was opened)
#   initial_units       int    (original full position size)
#   current_units       int    (remaining units after partial close)
#   partial_closed      bool   (whether the 50% partial close has been executed)
#   breakeven_moved     bool   (whether stop was moved to breakeven at +1×ATR)
#   trailing_active     bool   (whether 1.5×ATR trailing is active, i.e. +2×ATR hit)
#   highest_profit_atr  float  (highest ATR-multiple of profit seen so far)
#   current_sl          float  (the live stop-loss price we are managing)
#   opened_at           str    ISO timestamp


class TrailingStopManager:
    """
    Trailing stop + partial close manager.

    Call `process_position()` every cycle for each open position.
    It will:
      - Compute current profit in ATR units
      - Trigger partial close at +1×ATR (once)
      - Move SL to breakeven at +1×ATR (once)
      - Activate 1.5×ATR trailing at +2×ATR
      - Update trailing SL as profit increases
    """

    # ── Thresholds (in ATR multiples) ────────────────────────────
    PARTIAL_CLOSE_ATR = 1.0    # Close 50% at +1×ATR
    BREAKEVEN_ATR = 1.0        # Move SL to breakeven at +1×ATR
    TRAILING_START_ATR = 2.0   # Start trailing at +2×ATR
    TRAILING_DISTANCE_ATR = 1.5  # Trail distance once active

    PARTIAL_CLOSE_FRACTION = 0.5  # Close 50%

    def __init__(self, state_file: str = "logs/trailing_stop_state.json"):
        self.state_file = Path(state_file)
        self.state = self._load()

    # ── Persistence ───────────────────────────────────────────────

    def _load(self) -> dict:
        """Load trailing-stop state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"positions": {}}

    def _save(self):
        """Persist trailing-stop state to disk (atomic write — crash-safe)."""
        from src.file_utils import atomic_save
        atomic_save(self.state_file, self.state)

    def _key(self, instrument: str, side: str) -> str:
        return f"{instrument}_{side}"

    # ── Registration / deregistration ─────────────────────────────

    def register_position(
        self,
        instrument: str,
        side: str,
        entry_price: float,
        atr: float,
        units: int,
        current_sl: float,
    ):
        """
        Register a new position for trailing-stop management.

        Args:
            instrument: e.g. "EUR_USD"
            side: "long" or "short"
            entry_price: the fill price
            atr: ATR(14) at time of entry (used as the profit yardstick)
            units: initial position size (absolute value, always positive)
            current_sl: the initial stop-loss price
        """
        if atr <= 0:
            logger.warning(
                "ATR is %.6f for %s %s — skipping trailing stop registration",
                atr, instrument, side,
            )
            return

        k = self._key(instrument, side)
        self.state["positions"][k] = {
            "instrument": instrument,
            "side": side,
            "entry_price": entry_price,
            "atr_at_entry": atr,
            "initial_units": units,
            "current_units": units,
            "partial_closed": False,
            "breakeven_moved": False,
            "trailing_active": False,
            "highest_profit_atr": 0.0,
            "current_sl": current_sl,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        logger.info(
            "TrailingStop registered: %s %s %d units @ %s | ATR=%.6f | SL=%.5f",
            instrument, side, units, entry_price, atr, current_sl,
        )

    def unregister_position(self, instrument: str, side: str):
        """Remove a position from trailing-stop management (fully closed)."""
        k = self._key(instrument, side)
        if k in self.state["positions"]:
            del self.state["positions"][k]
            self._save()
            logger.info("TrailingStop unregistered: %s %s", instrument, side)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _profit_in_atr(current_price: float, entry_price: float,
                       side: str, atr: float) -> float:
        """
        Return profit expressed in ATR multiples.
        Positive = in profit, negative = in loss.
        """
        if atr <= 0:
            return 0.0
        if side == "long":
            return (current_price - entry_price) / atr
        else:
            return (entry_price - current_price) / atr

    @staticmethod
    def _trailing_sl_price(current_price: float, side: str,
                           trail_distance_atr: float, atr: float) -> float:
        """Compute the trailing stop price given current price and trail distance."""
        distance = trail_distance_atr * atr
        if side == "long":
            return current_price - distance
        else:
            return current_price + distance

    @staticmethod
    def _precision(instrument: str) -> int:
        return 3 if "JPY" in instrument else 5

    # ── Core processing ───────────────────────────────────────────

    def process_position(
        self,
        instrument: str,
        side: str,
        current_price: float,
    ) -> Optional[dict]:
        """
        Process one open position for this cycle.

        Args:
            instrument: e.g. "EUR_USD"
            side: "long" or "short"
            current_price: current market price (mid) for the instrument

        Returns:
            A dict describing actions taken, or None if no action:
            {
                "partial_close": {"units_closed": int} | None,
                "sl_update": {"old_sl": float, "new_sl": float} | None,
                "profit_atr": float,
            }
        """
        k = self._key(instrument, side)
        ts = self.state["positions"].get(k)
        if ts is None:
            return None

        atr = ts["atr_at_entry"]
        entry = ts["entry_price"]

        # Current profit in ATR terms
        profit_atr = self._profit_in_atr(current_price, entry, side, atr)

        # Track highest profit seen
        if profit_atr > ts["highest_profit_atr"]:
            ts["highest_profit_atr"] = profit_atr

        result = {
            "partial_close": None,
            "sl_update": None,
            "profit_atr": round(profit_atr, 4),
        }

        # ── 1. Partial close at +1×ATR ────────────────────────────
        if not ts["partial_closed"] and profit_atr >= self.PARTIAL_CLOSE_ATR:
            units_to_close = int(ts["initial_units"] * self.PARTIAL_CLOSE_FRACTION)
            if units_to_close <= 0:
                units_to_close = 1

            logger.info(
                "📊 PARTIAL CLOSE trigger: %s %s at +%.2f×ATR | close %d of %d units",
                instrument, side, profit_atr, units_to_close, ts["current_units"],
            )

            try:
                client = OandaClient()
                close_side = "long" if side == "long" else "short"
                resp = client.partial_close_position(
                    instrument=instrument,
                    side=close_side,
                    units=units_to_close,
                )

                # Update state
                ts["current_units"] = ts["current_units"] - units_to_close
                ts["partial_closed"] = True

                result["partial_close"] = {
                    "units_closed": units_to_close,
                    "remaining_units": ts["current_units"],
                }

                logger.info(
                    "✅ Partial close done: %s %s closed %d units, %d remaining",
                    instrument, side, units_to_close, ts["current_units"],
                )

                # If nothing remains, unregister
                if ts["current_units"] <= 0:
                    self.unregister_position(instrument, side)
                    return result

            except Exception as e:
                logger.error(
                    "❌ Partial close failed for %s %s: %s", instrument, side, e
                )
                # Don't mark as closed — will retry next cycle
                return result

        # ── 2. Move SL to breakeven at +1×ATR ─────────────────────
        if not ts["breakeven_moved"] and profit_atr >= self.BREAKEVEN_ATR:
            new_sl = round(entry, self._precision(instrument))
            old_sl = ts["current_sl"]

            # Only update if it actually improves the SL
            if (side == "long" and new_sl > old_sl) or \
               (side == "short" and new_sl < old_sl):
                logger.info(
                    "🔒 BREAKEVEN SL: %s %s | old=%.5f → new=%.5f (entry)",
                    instrument, side, old_sl, new_sl,
                )
                try:
                    self._update_sl_on_oanda(instrument, side, new_sl)
                    ts["current_sl"] = new_sl
                    ts["breakeven_moved"] = True
                    result["sl_update"] = {"old_sl": old_sl, "new_sl": new_sl}
                except Exception as e:
                    logger.error(
                        "❌ Breakeven SL update failed for %s %s: %s",
                        instrument, side, e,
                    )
            else:
                # SL already at or better than breakeven
                ts["breakeven_moved"] = True

        # ── 3. Trailing stop at +2×ATR ─────────────────────────────
        if profit_atr >= self.TRAILING_START_ATR:
            new_trail_sl = self._trailing_sl_price(
                current_price, side, self.TRAILING_DISTANCE_ATR, atr
            )
            new_trail_sl = round(new_trail_sl, self._precision(instrument))

            # Only move SL in our favour
            should_update = False
            if not ts["trailing_active"]:
                # First time hitting +2×ATR — activate trailing
                ts["trailing_active"] = True
                should_update = True
                logger.info(
                    "🏃 TRAILING ACTIVATED: %s %s at +%.2f×ATR | trail=%.5f",
                    instrument, side, profit_atr, new_trail_sl,
                )
            else:
                # Already trailing — only move if new SL is better
                old_sl = ts["current_sl"]
                if (side == "long" and new_trail_sl > old_sl) or \
                   (side == "short" and new_trail_sl < old_sl):
                    should_update = True

            if should_update:
                old_sl = ts["current_sl"]
                try:
                    self._update_sl_on_oanda(instrument, side, new_trail_sl)
                    ts["current_sl"] = new_trail_sl
                    result["sl_update"] = {"old_sl": old_sl, "new_sl": new_trail_sl}
                    logger.info(
                        "📈 TRAILING SL update: %s %s | %.5f → %.5f",
                        instrument, side, old_sl, new_trail_sl,
                    )
                except Exception as e:
                    logger.error(
                        "❌ Trailing SL update failed for %s %s: %s",
                        instrument, side, e,
                    )

        self._save()
        return result

    # ── OANDA order-update helper ─────────────────────────────────

    def _update_sl_on_oanda(self, instrument: str, side: str, new_sl: float):
        """
        Update the stop-loss on an existing OANDA position.

        OANDA doesn't have a direct "modify SL" endpoint. We must:
          1. Get the existing open trade ID(s) for this position
          2. Send an OrderUpdate (PUT) with the new stopLoss value
        """
        client = OandaClient()

        # Fetch open trades for this instrument to find the trade ID(s)
        trade_ids = client.get_open_trade_ids(instrument, side)

        if not trade_ids:
            raise RuntimeError(
                f"No open trades found for {instrument} {side} — cannot update SL"
            )

        for trade_id in trade_ids:
            client.update_trade_stop_loss(trade_id, new_sl)

    # ── Reconciliation ────────────────────────────────────────────

    def reconcile_with_oanda(self, oanda_positions: list[dict]):
        """
        Remove state entries for positions that no longer exist on OANDA.
        Call this once per cycle before processing.
        """
        # Build set of currently open (instrument, side) from OANDA
        oanda_open = set()
        for p in oanda_positions:
            if abs(int(p.get("long_units", 0))) > 0:
                oanda_open.add((p["instrument"], "long"))
            if abs(int(p.get("short_units", 0))) > 0:
                oanda_open.add((p["instrument"], "short"))

        to_remove = []
        for k, ts in self.state["positions"].items():
            key = (ts["instrument"], ts["side"])
            if key not in oanda_open:
                to_remove.append(k)
                logger.info(
                    "TrailingStop reconcile: removing %s %s (closed externally)",
                    ts["instrument"], ts["side"],
                )

        for k in to_remove:
            del self.state["positions"][k]

        if to_remove:
            self._save()

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current trailing-stop state for all managed positions."""
        positions = {}
        for k, ts in self.state["positions"].items():
            positions[k] = {
                "instrument": ts["instrument"],
                "side": ts["side"],
                "entry_price": ts["entry_price"],
                "atr_at_entry": ts["atr_at_entry"],
                "initial_units": ts["initial_units"],
                "current_units": ts["current_units"],
                "partial_closed": ts["partial_closed"],
                "breakeven_moved": ts["breakeven_moved"],
                "trailing_active": ts["trailing_active"],
                "highest_profit_atr": ts["highest_profit_atr"],
                "current_sl": ts["current_sl"],
            }
        return {"managed_count": len(positions), "positions": positions}


# Singleton
trailing_stop_manager = TrailingStopManager()
