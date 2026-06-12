#!/usr/bin/env python3
"""
Forex Weekly Performance Summary.
Runs every Friday 6pm SGT. Generates a weekly report and sends via telegram.
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BOT_DIR = Path("/opt/hermes/forex-trading-bot")
LOG_DIR = BOT_DIR / "logs"
SGT = timezone(timedelta(hours=8))


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def main():
    now = now_sgt = datetime.now(SGT)
    week_start = now - timedelta(days=now.weekday())
    week_start_str = week_start.strftime("%d %b")
    week_end_str = (week_start + timedelta(days=4)).strftime("%d %b")

    # Load data
    bt = load_json(LOG_DIR / "balance_tracker.json")
    cb = load_json(LOG_DIR / "circuit_breaker.json")
    trades_data = load_json(LOG_DIR / "active_trades.json")

    balance = bt.get("balance", 0) if bt else 0
    unrealized = 0.0
    active_count = 0

    closed_trades = []
    active_trades = []
    if isinstance(trades_data, dict):
        closed_trades = trades_data.get("closed_trades", [])
        active_trades = trades_data.get("active_trades", [])

    # This week's closed trades
    week_trades = []
    for t in closed_trades:
        closed_at = t.get("closed_at", "")
        if not closed_at:
            continue
        try:
            dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            dt_sgt = dt.astimezone(SGT)
            # Last 7 days
            if (now - dt_sgt).days < 7:
                week_trades.append(t)
        except (ValueError, TypeError):
            continue

    # Stats
    wins = [t for t in week_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in week_trades if (t.get("pnl") or 0) < 0]
    total_pnl = sum(t.get("pnl", 0) or 0 for t in week_trades)
    win_rate = (len(wins) / len(week_trades) * 100) if week_trades else 0

    # Best/worst trade
    if week_trades:
        best = max(week_trades, key=lambda t: t.get("pnl", 0) or 0)
        worst = min(week_trades, key=lambda t: t.get("pnl", 0) or 0)
    else:
        best = worst = None

    # By pair
    pair_stats: dict[str, dict] = {}
    for t in week_trades:
        pair = t.get("instrument", "?")
        pnl = t.get("pnl", 0) or 0
        if pair not in pair_stats:
            pair_stats[pair] = {"trades": 0, "pnl": 0.0, "wins": 0}
        pair_stats[pair]["trades"] += 1
        pair_stats[pair]["pnl"] += pnl
        if pnl > 0:
            pair_stats[pair]["wins"] += 1

    # Circuit breaker status
    cb_status = "🟢 Inactive"
    if cb and cb.get("is_tripped"):
        cb_status = f"🔴 Tripped (L{cb.get('escalation_level', '?')})"

    # Active positions
    active_count = len(active_trades)
    unrealized_pnl = sum(t.get("last_unrealized_pnl", 0) or 0 for t in active_trades)

    # Format report
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
    lines = [
        f"📊 Forex Weekly Report — {week_start_str} to {week_end_str}",
        f"",
        f"Account: {balance:,.2f} SGD",
        f"{pnl_emoji} Week P&L: {total_pnl:+.2f} SGD",
        f"Win rate: {win_rate:.0f}% ({len(wins)}W/{len(losses)}L out of {len(week_trades)} trades)",
        f"",
    ]

    if best and worst:
        lines.append(f"📈 Best trade: {best.get('instrument')} {best.get('direction')} — {best.get('pnl', 0):+.2f} SGD")
        lines.append(f"📉 Worst trade: {worst.get('instrument')} {worst.get('direction')} — {worst.get('pnl', 0):+.2f} SGD")
        lines.append("")

    if pair_stats:
        lines.append("By pair:")
        for pair, stats in sorted(pair_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = (stats["wins"] / stats["trades"] * 100) if stats["trades"] else 0
            emoji = "🟢" if stats["pnl"] >= 0 else "🔴"
            lines.append(f"  {emoji} {pair}: {stats['pnl']:+.2f} SGD ({stats['trades']} trades, {wr:.0f}% WR)")
        lines.append("")

    if active_count > 0:
        lines.append(f"Active positions: {active_count} | Unrealized P&L: {unrealized_pnl:+.2f} SGD")
        lines.append("")

    lines.append(f"Circuit breaker: {cb_status}")
    lines.append(f"Report generated: {now.strftime('%d %b %I:%M%p SGT').lstrip('0')}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
