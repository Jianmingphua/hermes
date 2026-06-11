"""
Forex Trading Bot - Trade Journal & Analytics Dashboard
Logs every trade with full context: entry/exit reason codes, P&L, duration,
signal quality, filters that passed/failed. Generates summary reports.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Comprehensive trade journal that records every trading decision with full context.
    Enables post-trade analysis: what works, what doesn't, filter effectiveness.
    """

    def __init__(self, journal_dir: str = "logs/trade_journal"):
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.entries_file = self.journal_dir / "entries.jsonl"
        self.closed_file = self.journal_dir / "closed.jsonl"
        self.daily_file = self.journal_dir / "daily_stats.jsonl"

    # ── Logging Entry/Exit ─────────────────────────────────────────

    def log_entry(self, trade: dict):
        """
        Log a new trade entry with full context.

        trade dict should contain:
            instrument, direction, units, entry_price, sl, tp,
            confidence, tier, confirmations, reasons, atr,
            spread_pips, session, filters_passed, filters_failed
        """
        entry = {
            "event": "ENTRY",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instrument": trade.get("instrument", ""),
            "direction": trade.get("direction", ""),
            "units": trade.get("units", 0),
            "entry_price": trade.get("entry_price", 0),
            "stop_loss": trade.get("sl", 0),
            "take_profit": trade.get("tp", 0),
            "confidence": trade.get("confidence", 0),
            "tier": trade.get("tier", "NONE"),
            "confirmations": trade.get("confirmations", 0),
            "reasons": trade.get("reasons", []),
            "atr": trade.get("atr", 0),
            "spread_pips": trade.get("spread_pips", 0),
            "session": trade.get("session", ""),
            "filters_passed": trade.get("filters_passed", []),
            "filters_failed": trade.get("filters_failed", []),
            "h4_trend_aligned": trade.get("h4_trend_aligned", None),
            "signal_score": trade.get("signal_score", 0),
        }
        self._append_jsonl(self.entries_file, entry)
        logger.info(
            "Journal ENTRY: %s %s %d @ %s | conf=%.2f tier=%s",
            entry["direction"], entry["instrument"], entry["units"],
            entry["entry_price"], entry["confidence"], entry["tier"],
        )

    def log_exit(self, instrument: str, direction: str, entry_price: float,
                 exit_price: float, pnl: float, exit_reason: str,
                 duration_minutes: float = 0, units: int = 0):
        """
        Log a trade exit with full P&L context.
        
        Includes deduplication: if an EXIT with the same instrument/direction/entry_price
        already exists in the journal, this is a no-op (avoids double-counting).
        This protects against the race condition where two cron cycles both detect
        the same trade closure.

        exit_reason: 'TP_HIT', 'SL_HIT', 'TRAILING_STOP', 'PARTIAL_CLOSE',
                     'CIRCUIT_BREAKER', 'MANUAL', 'TIMEOUT'
        """
        # ── Dedup check: already logged this exit? ─────────────────
        existing = self._load_jsonl(self.closed_file)
        for e in existing:
            if (e.get("event") == "EXIT"
                    and e.get("instrument") == instrument
                    and e.get("direction") == direction
                    and abs(e.get("entry_price", 0) - entry_price) < self._dedup_tolerance(instrument)):
                logger.debug(
                    "Journal EXIT dedup: %s %s @ %s already logged — skipping",
                    direction, instrument, entry_price,
                )
                return
        
        closed = {
            "event": "EXIT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instrument": instrument,
            "direction": direction,
            "units": units,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "exit_reason": exit_reason,
            "duration_minutes": round(duration_minutes, 1),
            "duration_hours": round(duration_minutes / 60, 2),
        }
        self._append_jsonl(self.closed_file, closed)
        logger.info(
            "Journal EXIT: %s %s | P&L=%s | reason=%s | duration=%.0fmin",
            direction, instrument, pnl, exit_reason, duration_minutes,
        )

    @staticmethod
    def _dedup_tolerance(instrument: str) -> float:
        """Return entry-price tolerance for dedup based on instrument precision."""
        return 0.01 if "JPY" in instrument else 0.0001

    def log_cycle(self, instruments_scanned: int, signals_found: int,
                  trades_opened: int, open_positions: int,
                  balance: float, unrealized_pnl: float):
        """Log a summary of each bot cycle for activity tracking."""
        stats = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instruments_scanned": instruments_scanned,
            "signals_found": signals_found,
            "trades_opened": trades_opened,
            "open_positions": open_positions,
            "balance": balance,
            "unrealized_pnl": unrealized_pnl,
        }
        self._append_jsonl(self.daily_file, stats)

    # ── Analytics & Reporting ──────────────────────────────────────

    def get_trade_stats(self, days: int = 30) -> dict:
        """Get comprehensive trade statistics for the last N days."""
        closed = self._load_jsonl(self.closed_file)
        if not closed:
            return {"total_trades": 0, "message": "No closed trades yet"}

        df = pd.DataFrame(closed)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        cutoff = pd.Timestamp.now(tz="timezone.utc") - pd.Timedelta(days=days)
        recent = df[df["timestamp"] >= cutoff] if len(df) > 0 else df

        if recent.empty:
            recent = df  # fallback to all data

        wins = recent[recent["pnl"] > 0]
        losses = recent[recent["pnl"] < 0]
        total_pnl = recent["pnl"].sum()

        # Exit reason breakdown
        exit_reasons = recent.groupby("exit_reason")["pnl"].agg(["count", "sum"]).to_dict()

        # Per-pair breakdown
        pair_stats = recent.groupby("instrument")["pnl"].agg(["count", "sum", "mean"]).to_dict()

        # Duration analysis
        avg_duration = recent["duration_minutes"].mean() if "duration_minutes" in recent.columns else 0

        return {
            "period_days": days,
            "total_trades": len(recent),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(recent) - len(wins) - len(losses),
            "win_rate": round(len(wins) / len(recent) * 100, 1) if len(recent) > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(recent["pnl"].mean(), 2) if len(recent) > 0 else 0,
            "max_win": round(recent["pnl"].max(), 2) if len(recent) > 0 else 0,
            "max_loss": round(recent["pnl"].min(), 2) if len(recent) > 0 else 0,
            "avg_duration_min": round(avg_duration, 1),
            "profit_factor": round(abs(wins["pnl"].sum() / losses["pnl"].sum()), 2) if len(losses) > 0 and losses["pnl"].sum() != 0 else float("inf"),
            "exit_reasons": exit_reasons,
            "pair_stats": pair_stats,
        }

    def get_filter_effectiveness(self) -> dict:
        """Analyze which filters are blocking the most trades."""
        entries = self._load_jsonl(self.entries_file)
        if not entries:
            return {"message": "No entry data yet"}

        # Count filter pass/fail rates
        all_passed = {}
        all_failed = {}
        for e in entries:
            for f in e.get("filters_passed", []):
                all_passed[f] = all_passed.get(f, 0) + 1
            for f in e.get("filters_failed", []):
                all_failed[f] = all_failed.get(f, 0) + 1

        return {
            "total_entries": len(entries),
            "filters_passed": all_passed,
            "filters_failed": all_failed,
        }

    def get_daily_pnl(self, days: int = 14) -> list[dict]:
        """Get daily P&L for charting."""
        closed = self._load_jsonl(self.closed_file)
        if not closed:
            return []

        df = pd.DataFrame(closed)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["date"] = df["timestamp"].dt.date
        daily = df.groupby("date")["pnl"].sum().reset_index()
        daily = daily.tail(days)
        return [{"date": str(row["date"]), "pnl": round(row["pnl"], 2)} for _, row in daily.iterrows()]

    def generate_report(self) -> str:
        """Generate a human-readable text report."""
        stats = self.get_trade_stats()
        daily = self.get_daily_pnl(7)

        lines = [
            "📊 TRADE JOURNAL REPORT",
            "=" * 50,
            f"Period: Last {stats.get('period_days', 30)} days",
            f"Total Trades: {stats.get('total_trades', 0)}",
            f"Win Rate: {stats.get('win_rate', 0)}% ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)",
            f"Total P&L: {stats.get('total_pnl', 0):+.2f} SGD",
            f"Avg P&L: {stats.get('avg_pnl', 0):+.2f} SGD",
            f"Max Win: {stats.get('max_win', 0):+.2f} | Max Loss: {stats.get('max_loss', 0):+.2f}",
            f"Profit Factor: {stats.get('profit_factor', 0)}",
            f"Avg Duration: {stats.get('avg_duration_min', 0):.0f} min",
            "",
            "📈 Daily P&L (last 7 days):",
        ]
        for d in daily:
            emoji = "🟢" if d["pnl"] > 0 else "🔴" if d["pnl"] < 0 else "⚪"
            lines.append(f"  {emoji} {d['date']}: {d['pnl']:+.2f}")

        return "\n".join(lines)

    # ── Helpers ────────────────────────────────────────────────────

    def _append_jsonl(self, path: Path, record: dict):
        """Append a JSON line to a file (atomic read-modify-write)."""
        from src.file_utils import atomic_save
        
        # Read existing entries
        entries = self._load_jsonl(path)
        entries.append(record)
        
        # Write back atomically (all-or-nothing)
        # Use compact JSON lines for efficiency
        tmp_path = path.parent / f".{path.name}.tmp"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write newline-delimited JSON manually for compact storage
        import tempfile, os, json as _json
        fd, abs_tmp = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f".{path.name}.",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w") as f:
                for entry in entries:
                    f.write(_json.dumps(entry, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(abs_tmp, str(path))
        except Exception:
            try:
                os.unlink(abs_tmp)
            except OSError:
                pass
            raise

    def _load_jsonl(self, path: Path) -> list[dict]:
        """Load all records from a JSONL file."""
        if not path.exists():
            return []
        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records


# Singleton
trade_journal = TradeJournal()
