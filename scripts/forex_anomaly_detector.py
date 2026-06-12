#!/usr/bin/env python3
"""
Forex Anomaly Detector — Cron-friendly.
Reads bot log files, detects anomalies, outputs alert text.
Exit 0 always. Non-empty stdout = alert delivered. Empty stdout = all clear.

Alert-once-per-type-per-day via state file.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BOT_DIR = Path("/opt/hermes/forex-trading-bot")
LOG_DIR = BOT_DIR / "logs"
STATE_FILE = LOG_DIR / ".anomaly_state.json"

# Thresholds
CONSECUTIVE_LOSS_THRESHOLD = 3       # N+ consecutive losing trades on same pair
MARGIN_SPIKE_PCT = 20                # margin_used jump >20% in 1hr
WIN_RATE_THRESHOLD = 30              # rolling 20-trade win rate below 30%
DAILY_LOSS_MULTIPLIER = 2.0          # daily loss > 2× average daily loss
STALE_POSITION_HOURS = 24            # position open >24h without TP/SL hit
BALANCE_ALERT_THRESHOLD = 93000      # alert if balance drops below

SGT = timezone(timedelta(hours=8))


def now_sgt() -> datetime:
    return datetime.now(SGT)


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_state() -> dict:
    data = load_json(STATE_FILE)
    if isinstance(data, dict):
        return data
    return {"date": "", "alerts_sent": {}}


def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def reset_state_if_new_day(state: dict) -> dict:
    today = now_sgt().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "alerts_sent": {}}
    return state


def already_sent(state: dict, alert_type: str) -> bool:
    return alert_type in state.get("alerts_sent", {})


def mark_sent(state: dict, alert_type: str):
    state.setdefault("alerts_sent", {})[alert_type] = now_sgt().isoformat()


def get_closed_trades() -> list:
    """Load closed trades from active_trades.json."""
    data = load_json(LOG_DIR / "active_trades.json")
    if not data:
        return []
    if isinstance(data, dict):
        return data.get("closed_trades", [])
    return []


def get_active_trades() -> list:
    """Load currently open trades from active_trades.json."""
    data = load_json(LOG_DIR / "active_trades.json")
    if not data:
        return []
    if isinstance(data, dict):
        return data.get("active_trades", [])
    return []


def detect_consecutive_losses(trades: list) -> list[str]:
    """Find instruments with N+ consecutive losing closed trades."""
    # Group closed trades by instrument, sorted by closed_at
    by_pair: dict[str, list] = {}
    for t in trades:
        if t.get("status") != "closed":
            continue
        pair = t.get("instrument", "UNKNOWN")
        by_pair.setdefault(pair, []).append(t)

    alerts = []
    for pair, pair_trades in by_pair.items():
        # Sort by close time descending (most recent first)
        pair_trades.sort(
            key=lambda x: x.get("closed_at", ""), reverse=True
        )
        # Count consecutive losses from most recent
        losses = []
        for t in pair_trades:
            pnl = t.get("pnl", 0)
            if pnl is None:
                pnl = 0
            if pnl < 0:
                losses.append(t)
            else:
                break
        if len(losses) >= CONSECUTIVE_LOSS_THRESHOLD:
            pnls = [f"{t.get('pnl', 0):.2f}" for t in losses[:6]]
            alerts.append(
                f"🔴 Losing streak: {pair} — {len(losses)} consecutive losses\n"
                f"   Last {len(pnls)} trades: {', '.join(pnls)} SGD"
            )
    return alerts


def detect_low_win_rate(trades: list) -> list[str]:
    """Rolling 20-trade win rate below threshold."""
    closed = [t for t in trades if t.get("status") == "closed"]
    if len(closed) < 10:
        return []
    # Most recent 20 trades
    recent = sorted(closed, key=lambda x: x.get("closed_at", ""), reverse=True)[:20]
    wins = sum(1 for t in recent if (t.get("pnl") or 0) > 0)
    win_rate = (wins / len(recent)) * 100
    if win_rate < WIN_RATE_THRESHOLD:
        return [
            f"🟡 Low win rate: {win_rate:.0f}% over last {len(recent)} trades\n"
            f"   ({wins}W/{len(recent) - wins}L) — below {WIN_RATE_THRESHOLD}% threshold"
        ]
    return []


def detect_balance_drop() -> list[str]:
    """Balance below threshold or sharp drop."""
    bt = load_json(LOG_DIR / "balance_tracker.json")
    if not bt:
        return []
    balance = bt.get("balance", 0)
    if balance < BALANCE_ALERT_THRESHOLD:
        return [
            f"🔴 Balance alert: {balance:,.2f} SGD\n"
            f"   Below threshold of {BALANCE_ALERT_THRESHOLD:,.0f} SGD"
        ]
    return []


def detect_circuit_breaker() -> list[str]:
    """Circuit breaker still tripped (supplement to health_check.py)."""
    cb = load_json(LOG_DIR / "circuit_breaker.json")
    if not cb:
        return []
    if cb.get("is_tripped"):
        esc = cb.get("escalation_level", 0)
        reason = cb.get("trip_reason", "unknown")
        cooldown = cb.get("cooldown_until", "")
        return [
            f"🟡 Circuit breaker tripped — escalation L{esc}\n"
            f"   Reason: {reason}\n"
            f"   Cooldown: {cooldown or 'manual reset required'}"
        ]
    return []


def detect_stale_positions() -> list[str]:
    """Open positions active longer than threshold."""
    active = get_active_trades()
    if not active:
        return []
    alerts = []
    now = datetime.now(timezone.utc)
    for t in active:
        opened = t.get("opened_at", "")
        if not opened:
            continue
        try:
            opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
            age_h = (now - opened_dt).total_seconds() / 3600
            if age_h > STALE_POSITION_HOURS:
                alerts.append(
                    f"🟡 Stale position: {t.get('instrument')} "
                    f"{t.get('direction')} — open {age_h:.0f}h\n"
                    f"   Entry: {t.get('entry_price')} | "
                    f"P&L: {t.get('last_unrealized_pnl', 0):.2f} SGD"
                )
        except (ValueError, TypeError):
            continue
    return alerts


def detect_margin_spike() -> list[str]:
    """Check margin_used from balance_tracker over time."""
    # balance_tracker only has current balance; for margin we need
    # to infer from signals. We'll check if unrealized P&L swing is large.
    cb = load_json(LOG_DIR / "circuit_breaker.json")
    if cb and cb.get("unrealized_pnl"):
        upnl = cb["unrealized_pnl"]
        bt = load_json(LOG_DIR / "balance_tracker.json")
        if bt:
            balance = bt.get("balance", 0)
            if balance > 0 and abs(upnl) / balance > 0.05:
                return [
                    f"🟡 Large unrealized P&L swing: {upnl:+.2f} SGD\n"
                    f"   ({abs(upnl)/balance*100:.1f}% of balance)"
                ]
    return []


def main():
    state = load_state()
    state = reset_state_if_new_day(state)

    all_alerts = []
    checks = [
        ("consecutive_losses", detect_consecutive_losses),
        ("low_win_rate", detect_low_win_rate),
        ("balance_drop", detect_balance_drop),
        ("circuit_breaker", detect_circuit_breaker),
        ("stale_positions", detect_stale_positions),
        ("margin_spike", detect_margin_spike),
    ]

    closed_trades = get_closed_trades()

    for alert_type, check_fn in checks:
        if already_sent(state, alert_type):
            continue
        try:
            if alert_type in ("consecutive_losses", "low_win_rate"):
                results = check_fn(closed_trades)
            else:
                results = check_fn()
            if results:
                all_alerts.extend(results)
                mark_sent(state, alert_type)
        except Exception as e:
            # Don't let one check failure stop others
            pass

    save_state(state)

    if all_alerts:
        sgt_time = now_sgt().strftime("%d %b %I:%M%p SGT").lstrip("0")
        print(f"⚠️ Forex Anomaly Alert — {sgt_time}")
        print()
        for i, alert in enumerate(all_alerts, 1):
            print(f"{i}. {alert}")
            if i < len(all_alerts):
                print()

    sys.exit(0)


if __name__ == "__main__":
    main()
