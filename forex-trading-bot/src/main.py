"""
Forex Trading Bot - Main Runner
Orchestrates: fetch → analyze → filter → signal → execute → track.
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from src.config import config
from src.oanda_client import OandaClient
from src.signal_generator import SignalGenerator
from src.risk_manager import RiskManager
from src.notifier import save_signal_log
from src.news_filter import news_filter
from src.session_filter import session_filter
from src.circuit_breaker import circuit_breaker
from src.spread_monitor import spread_monitor
from src.position_state import position_state
from src.trade_monitor import trade_monitor

# ── Logging Setup ───────────────────────────────────────────────

LOG_DIR = Path(config.LOG_DIR)
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"bot_{datetime.utcnow():%Y%m%d}.log"

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def run_once(dry_run: bool = True):
    """Run a single analysis cycle with all safety filters + position tracking."""
    logger.info("=" * 60)
    logger.info("Forex Bot Cycle Start | dry_run=%s", dry_run)
    logger.info("=" * 60)

    # ── 0. Pre-flight checks ────────────────────────────────────

    # Circuit breaker
    allowed, reason = circuit_breaker.check()
    if not allowed:
        logger.warning("Circuit breaker: %s", reason)
        return []

    client = OandaClient()
    generator = SignalGenerator(client)
    risk_mgr = RiskManager(
        risk_per_trade=config.RISK_PER_TRADE,
        max_daily_loss=config.MAX_DAILY_LOSS,
        max_open_positions=config.MAX_OPEN_POSITIONS,
    )

    # ── 1. Sync position state with OANDA ───────────────────────
    position_state.state = position_state._load()  # Reload from disk
    oanda_positions = client.get_open_positions()
    tracked = position_state.get_open_positions()
    logger.info(
        "Positions: OANDA=%d | Tracked=%d | Monitored=%d",
        len(oanda_positions), len(tracked), trade_monitor.get_active_count(),
    )

    # ── 2. Check for closed trades ──────────────────────────────
    closed_trades = trade_monitor.check_closed_trades()
    if closed_trades:
        for ct in closed_trades:
            logger.info(
                "Closed: %s %s P&L=%s",
                ct["direction"], ct["instrument"], ct.get("pnl", "?"),
            )
            # Remove from position state
            side = "long" if ct["direction"] == "BUY" else "short"
            position_state.remove_position(ct["instrument"], side)

    # ── 3. Account info ─────────────────────────────────────────
    try:
        account = client.get_account_summary()
        balance = account["balance"]
        logger.info("Account balance: %s %s", balance, account["currency"])
    except Exception as e:
        logger.error("Failed to get account info: %s", e)
        return []

    # ── 4. Scan pairs ───────────────────────────────────────────
    all_signals = []
    strong_signals = []
    n_positions = position_state.get_open_count()

    for instrument in config.DEFAULT_INSTRUMENTS:
        logger.info("── %s ──", instrument)

        # 4a. Session filter
        good_time, reason = session_filter.is_good_time(instrument)
        if not good_time:
            logger.info("Session filter: %s", reason)
            continue

        # 4b. News filter
        safe, reason = news_filter.is_safe_to_trade(instrument)
        if not safe:
            logger.info("News filter: %s", reason)
            continue

        # 4c. Already open?
        if position_state.is_already_open(instrument):
            logger.info("Already have a position for %s — skipping", instrument)
            continue

        # 4d. Analyze
        try:
            signal = generator.analyze(instrument, config.DEFAULT_GRANULARITY)
        except Exception as e:
            logger.error("Analysis error for %s: %s", instrument, e)
            continue

        if "error" in signal:
            logger.warning("Error for %s: %s", instrument, signal["error"])
            continue

        # 4e. Spread check
        if "current_price" in signal:
            spread = signal["current_price"]["spread"]
            spread_ok, spread_reason = spread_monitor.check_spread(instrument, spread)
            if not spread_ok:
                logger.info("Spread check: %s", spread_reason)
                continue
            signal["spread_pips"] = round(
                spread * (100 if "JPY" in instrument else 10000), 1
            )

        # Log signal
        sig_type = signal.get("signal", "HOLD")
        conf = signal.get("confidence", 0)
        tier = signal.get("tier", "NONE")
        confs = signal.get("confirmations", 0)
        logger.info(
            "Signal: %s (conf=%.2f, tier=%s, confs=%d/4) | %s",
            sig_type, conf, tier, confs,
            " | ".join(signal.get("reasons", [])[:3]),
        )

        # 4f. Risk management + trade setup (require ≥2 of 4 confirmations)
        if sig_type in ("BUY", "SELL") and conf >= 0.4 and confs >= 2:
            # Correlation check
            corr_ok, corr_reason = position_state.check_correlation(instrument, sig_type)
            if not corr_ok:
                logger.info("Correlation check: %s", corr_reason)
                continue

            # Max positions check
            if n_positions >= config.MAX_OPEN_POSITIONS:
                logger.info(
                    "Max positions reached: %d/%d", n_positions, config.MAX_OPEN_POSITIONS
                )
                continue

            setup = risk_mgr.build_trade_setup(signal, balance, n_positions)
            if setup:
                signal["trade_setup"] = {
                    "direction": setup.direction,
                    "units": setup.units,
                    "entry": setup.entry_price,
                    "sl": setup.stop_loss,
                    "tp": setup.take_profit,
                    "risk": setup.risk_amount,
                    "tier": tier,
                    "confidence": conf,
                }
                strong_signals.append(signal)

                if not dry_run:
                    try:
                        response = client.place_market_order(
                            instrument=setup.instrument,
                            units=setup.units,
                            stop_loss=setup.stop_loss,
                            take_profit=setup.take_profit,
                        )
                        order_id = None
                        if "orderFillTransaction" in response:
                            order_id = response["orderFillTransaction"].get("id")
                        elif "orderCreateTransaction" in response:
                            order_id = response["orderCreateTransaction"].get("id")

                        # Track position
                        position_state.add_position(
                            instrument=setup.instrument,
                            direction=setup.direction,
                            units=setup.units,
                            entry_price=setup.entry_price,
                            sl=setup.stop_loss,
                            tp=setup.take_profit,
                            order_id=order_id,
                        )

                        # Register for monitoring
                        trade_monitor.register_trade(
                            instrument=setup.instrument,
                            direction=setup.direction,
                            units=setup.units,
                            entry_price=setup.entry_price,
                            sl=setup.stop_loss,
                            tp=setup.take_profit,
                            order_id=order_id,
                        )

                        logger.info("✅ Order placed + tracked: %s %d units", setup.direction, setup.units)
                    except Exception as e:
                        logger.error("❌ Order failed: %s", e)
                else:
                    logger.info("[DRY RUN] Order not placed")

                n_positions += 1

        all_signals.append(signal)

    # ── 5. Save + report ────────────────────────────────────────
    if all_signals:
        save_signal_log(all_signals, balance)

    cb_status = circuit_breaker.get_status()
    trade_summary = trade_monitor.get_summary()
    logger.info(
        "Cycle complete | %d scanned | %d signals | Pos: %d | CB: %d losses | "
        "Trades: %d (%.0f%% WR) P&L: %s",
        len(config.DEFAULT_INSTRUMENTS),
        len(strong_signals),
        position_state.get_open_count(),
        cb_status["consecutive_losses"],
        trade_summary["total_trades"],
        trade_summary["win_rate"],
        trade_summary["total_pnl"],
    )

    return strong_signals


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Forex Trading Bot")
    parser.add_argument("--mode", choices=["once", "loop"], default="once")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--status", action="store_true", help="Show circuit breaker status")
    parser.add_argument("--reset-cb", action="store_true", help="Reset circuit breaker")
    parser.add_argument("--positions", action="store_true", help="Show open positions")
    parser.add_argument("--trades", action="store_true", help="Show trade history")
    args = parser.parse_args()

    if args.status:
        status = circuit_breaker.get_status()
        print(json.dumps(status, indent=2))
        sys.exit(0)

    if args.reset_cb:
        circuit_breaker.manual_reset()
        print("Circuit breaker reset")
        sys.exit(0)

    if args.positions:
        status = position_state.get_status()
        print(json.dumps(status, indent=2))
        sys.exit(0)

    if args.trades:
        summary = trade_monitor.get_summary()
        history = trade_monitor.get_trade_history(limit=10)
        print(json.dumps({"summary": summary, "recent": history}, indent=2, default=str))
        sys.exit(0)

    dry_run = not args.execute
    if args.mode == "once":
        run_once(dry_run=dry_run)
    else:
        logger.info("Starting loop: interval=%d min | dry_run=%s", args.interval, dry_run)
        while True:
            try:
                run_once(dry_run=dry_run)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)
            logger.info("Sleeping %d minutes...", args.interval)
            time.sleep(args.interval * 60)
