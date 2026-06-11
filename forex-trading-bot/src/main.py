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
from src.optimized_params import get_params
from src.oanda_client import OandaClient
from src.signal_generator import SignalGenerator
from src.risk_manager import RiskManager
from src.notifier import save_signal_log
from src.news_filter import news_filter
from src.session_filter import session_filter
from src.circuit_breaker import circuit_breaker
from src.spread_monitor import spread_monitor
from src.econ_calendar import econ_calendar
from src.position_state import position_state
from src.trade_monitor import trade_monitor
from src.trade_journal import trade_journal
from src.balance_tracker import balance_tracker

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
    from src.file_utils import FileLock
    
    # Acquire cross-process lock — prevents two cron instances running simultaneously.
    # If another cycle is still running, we skip this one (non-blocking).
    try:
        lock = FileLock("logs/bot.lock", timeout=0)
        lock.__enter__()
    except TimeoutError:
        logger.warning("Another bot cycle is still running — skipping this one")
        return []
    
    try:
        return _run_once_inner(dry_run=dry_run)
    finally:
        lock.__exit__()


def _run_once_inner(dry_run: bool = True):
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

    # ── 1a. Reconcile trade monitor with OANDA ──────────────────
    # Re-registers positions that exist on OANDA but aren't in local state
    # (e.g. after bot restart between cycles). Prevents UNKNOWN exits.
    trade_monitor.reconcile_with_oanda()

    # ── 1b. Update unrealized P&L for active trades ─────────────
    trade_monitor.update_unrealized_pnl()

    from src.trailing_stop import trailing_stop_manager

    # ── 1c. Trailing stop + partial close management ────────────
    trailing_stop_manager.reconcile_with_oanda(oanda_positions)
    for p in oanda_positions:
        inst = p["instrument"]
        side = "long" if abs(int(p["long_units"])) > 0 else "short"
        try:
            current_price = client.get_current_price(inst)
            mid = (current_price["bid"] + current_price["ask"]) / 2
            result = trailing_stop_manager.process_position(inst, side, mid)
            if result and (result.get("sl_update") or result.get("partial_close")):
                logger.info(
                    "TrailingStop: %s %s | profit_atr=%.2f | sl_update=%s | partial=%s",
                    inst, side, result.get("profit_atr", 0),
                    result.get("sl_update"), result.get("partial_close"),
                )
        except Exception as e:
            logger.warning("TrailingStop error for %s: %s", inst, e)

    # ── 1d. Account info + Balance tracking ────────────────────────
    # Fetch balance once, use for both circuit breaker and P&L delta.
    try:
        account = client.get_account_summary()
        balance = account["balance"]
        unrealized_pnl = account.get("unrealized_pnl", 0.0)
        logger.info(
            "Account balance: %s %s | Unrealized P&L: %s",
            balance, account["currency"], unrealized_pnl,
        )
        # Update circuit breaker with current balance and unrealized P&L
        circuit_breaker.set_account_balance(balance)
        circuit_breaker.update_unrealized_pnl(unrealized_pnl)

        # Balance tracking: compute realized P&L delta since last cycle
        balance_tracker.last_balance = balance_tracker.current_balance
        balance_tracker.current_balance = balance
        realized_pnl_delta = balance_tracker.compute_realized_pnl()

        # Update risk manager daily P&L for loss limit check
        # Pass as percentage of balance (max_daily_loss is 0.03 = 3%)
        daily_pnl_pct = unrealized_pnl / balance if balance > 0 else 0
        risk_mgr.update_daily_pnl(daily_pnl_pct)
        # Persist immediately — if we crash before end of cycle, next run won't
        # double-count this P&L delta (the balance is already saved as the new baseline).
        balance_tracker.persist()
    except Exception as e:
        logger.error("Failed to get account info: %s", e)
        return []

    # ── 2. Check for closed trades ──────────────────────────────
    closed_trades = trade_monitor.check_closed_trades(
        realized_pnl_pool=realized_pnl_delta,
    )
    if closed_trades:
        for ct in closed_trades:
            logger.info(
                "Closed: %s %s P&L=%s",
                ct["direction"], ct["instrument"], ct.get("pnl", "?"),
            )
            # Remove from position state
            side = "long" if ct["direction"] == "BUY" else "short"
            position_state.remove_position(ct["instrument"], side)

            # Log to trade journal
            opened_at = ct.get("opened_at", "")
            duration_min = 0
            if opened_at:
                try:
                    from datetime import datetime, timezone as tz
                    opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    duration_min = (datetime.now(tz.utc) - opened_dt).total_seconds() / 60
                except Exception:
                    pass
            # Determine exit reason
            exit_reason = "UNKNOWN"
            sl = ct.get("stop_loss", 0)
            tp = ct.get("take_profit", 0)
            ep = ct.get("entry_price", 0)
            pnl = ct.get("pnl", 0)
            if pnl > 0 and tp and abs(tp - ep) > 0:
                exit_reason = "TP_HIT"
            elif pnl < 0 and sl and abs(sl - ep) > 0:
                exit_reason = "SL_HIT"
            elif pnl > 0:
                # Positive P&L but no TP info (e.g. reconciled position) — still a win
                exit_reason = "TP_HIT"
            elif pnl < 0:
                # Negative P&L but no SL info — still a loss
                exit_reason = "SL_HIT"
            # If pnl == 0 and no SL/TP info, remains UNKNOWN (true unknown)
            trade_journal.log_exit(
                instrument=ct["instrument"],
                direction=ct["direction"],
                entry_price=ep,
                exit_price=ep + pnl / max(abs(ct.get("units", 1)), 1),
                pnl=pnl,
                exit_reason=exit_reason,
                duration_minutes=duration_min,
                units=ct.get("units", 0),
            )

    # ── 3b. Circuit breaker check ───────────────────────────────
    allowed, reason = circuit_breaker.check()
    if not allowed:
        logger.warning("Circuit breaker: %s", reason)
        return []
    all_signals = []
    strong_signals = []
    n_positions = position_state.get_open_count()

    for instrument in config.DEFAULT_INSTRUMENTS:
        logger.info("── %s ──", instrument)
        filters_passed = []
        filters_failed = []

        # 4a. Session filter (per-pair optimized windows)
        params = get_params(instrument)
        good_time, reason = session_filter.is_good_time_custom(
            instrument, params["session_start"], params["session_end"]
        )
        if not good_time:
            logger.info("Session filter: %s", reason)
            filters_failed.append(f"session:{reason}")
            continue
        filters_passed.append("session")

        # 4b. News filter
        safe, reason = news_filter.is_safe_to_trade(instrument)
        if not safe:
            logger.info("News filter: %s", reason)
            filters_failed.append(f"news:{reason}")
            continue
        filters_passed.append("news")

        # 4b2. Economic calendar filter
        cal_safe, cal_reason = econ_calendar.is_safe_to_trade(instrument)
        if not cal_safe:
            logger.info("Econ calendar: %s", cal_reason)
            filters_failed.append(f"econ_calendar:{cal_reason}")
            continue
        filters_passed.append("econ_calendar")

        # 4c. Already open?
        if position_state.is_already_open(instrument):
            logger.info("Already have a position for %s — skipping", instrument)
            continue

        # 4d. Analyze (uses per-pair optimized granularity)
        try:
            signal = generator.analyze(instrument)
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
                filters_failed.append(f"spread:{spread_reason}")
                continue
            filters_passed.append("spread")
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

        # 4f. Risk management + trade setup
        # Three pathways to pass the gate:
        #   Path A: Strong — conf >= 0.4 AND confs >= 1.5 (MEDIUM+ tier, at least 3 half-confs or 1 full + 1 half)
        #   Path B: Weak — tier == LOW AND conf >= 0.15 (single positioned indicator, low conviction)
        #   Path C: Score bypass — raw score >= 3.0 AND confs >= 1.0 (very strong setup e.g. all positioned but no crossovers yet)
        _gate_pass = sig_type in ("BUY", "SELL")
        _cond_a = conf >= 0.4 and confs >= 1.5
        _cond_b = tier == "LOW" and conf >= 0.15
        _cond_c = signal.get("score", 0) >= 3.0 and confs >= 1.0
        if _gate_pass and (_cond_a or _cond_b or _cond_c):
            logger.info("Signal strength GATE PASSED: %s %s (conf=%.3f, tier=%s, confs=%.1f, score=%.2f)", sig_type, instrument, conf, tier, confs, signal.get("score", 0))
            filters_passed.append("signal_strength")
            # Correlation check
            corr_ok, corr_reason = position_state.check_correlation(instrument, sig_type)
            if not corr_ok:
                logger.info("Correlation check: %s", corr_reason)
                filters_failed.append(f"correlation:{corr_reason}")
                continue
            filters_passed.append("correlation")

            # Max positions check
            if n_positions >= config.MAX_OPEN_POSITIONS:
                logger.info(
                    "Max positions reached: %d/%d", n_positions, config.MAX_OPEN_POSITIONS
                )
                continue

            setup = risk_mgr.build_trade_setup(
                signal, balance, n_positions,
                sl_mult=params["sl_mult"],
                tp_mult=params["tp_mult"],
            )
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

                        # Register for trailing stop management
                        atr_val = signal.get("atr_14", 0)
                        ts_side = "long" if setup.direction == "BUY" else "short"
                        trailing_stop_manager.register_position(
                            instrument=setup.instrument,
                            side=ts_side,
                            entry_price=setup.entry_price,
                            atr=atr_val,
                            units=abs(setup.units),
                            current_sl=setup.stop_loss,
                        )

                        logger.info("✅ Order placed + tracked: %s %d units", setup.direction, setup.units)

                        # Log to trade journal
                        trade_journal.log_entry({
                            "instrument": setup.instrument,
                            "direction": setup.direction,
                            "units": abs(setup.units),
                            "entry_price": setup.entry_price,
                            "sl": setup.stop_loss,
                            "tp": setup.take_profit,
                            "confidence": conf,
                            "tier": tier,
                            "confirmations": confs,
                            "reasons": signal.get("reasons", []),
                            "atr": signal.get("atr_14", 0),
                            "spread_pips": signal.get("spread_pips", 0),
                            "session": f"{params['session_start']}-{params['session_end']}UTC",
                            "filters_passed": filters_passed,
                            "filters_failed": filters_failed,
                            "h4_trend_aligned": signal.get("h4_trend_aligned", None),
                            "signal_score": signal.get("score", 0),
                        })
                    except Exception as e:
                        logger.error("❌ Order failed: %s", e)
                else:
                    logger.info("[DRY RUN] Order not placed")

                n_positions += 1

        all_signals.append(signal)

    # ── 4g. Gold Strategy (XAU/USD) ───────────────────────────────
    gold_instrument = "XAU_USD"
    if not position_state.is_already_open(gold_instrument):
        try:
            from src.gold_strategy import GoldSignalGenerator, GoldRiskManager, GoldSessionFilter
            gold_gen = GoldSignalGenerator()
            gold_risk = GoldRiskManager()
            gold_session = GoldSessionFilter()

            # Gold session filter
            from datetime import datetime as dt
            from datetime import timezone as tz
            utc_hour = dt.now(tz.utc).hour
            gold_time_ok, gold_time_reason = gold_session.is_good_time(utc_hour)
            if not gold_time_ok:
                logger.info("Gold session filter: %s", gold_time_reason)
            else:
                # Fetch gold data
                gold_df = client.get_candles(gold_instrument, "H4", 200)
                if not gold_df.empty and len(gold_df) >= 200:
                    gold_price = client.get_current_price(gold_instrument)
                    gold_signal = gold_gen.analyze(gold_df, gold_price)

                    logger.info(
                        "Gold signal: %s (conf=%.2f, tier=%s, confs=%d/4) | spread=%.1f pips",
                        gold_signal.signal, gold_signal.confidence,
                        gold_signal.tier, gold_signal.confirmations,
                        gold_signal.spread_pips,
                    )

                    # Validate and execute
                    if gold_signal.signal in ("BUY", "SELL"):
                        valid, val_reason = gold_risk.validate_signal(gold_signal, balance)
                        if valid:
                            units, risk_amt = gold_risk.calculate_position_size(
                                balance, gold_signal.entry_price,
                                gold_signal.stop_loss, gold_signal.atr,
                            )
                            if units > 0:
                                gold_signal.units = units
                                gold_signal.risk_amount = risk_amt

                                if not dry_run:
                                    try:
                                        direction = gold_signal.signal
                                        order_units = units if direction == "BUY" else -units
                                        response = client.place_market_order(
                                            instrument=gold_instrument,
                                            units=order_units,
                                            stop_loss=gold_signal.stop_loss,
                                            take_profit=gold_signal.take_profit,
                                        )
                                        order_id = None
                                        if "orderFillTransaction" in response:
                                            order_id = response["orderFillTransaction"].get("id")

                                        position_state.add_position(
                                            instrument=gold_instrument,
                                            direction=direction,
                                            units=units,
                                            entry_price=gold_signal.entry_price,
                                            sl=gold_signal.stop_loss,
                                            tp=gold_signal.take_profit,
                                            order_id=order_id,
                                        )
                                        trade_monitor.register_trade(
                                            instrument=gold_instrument,
                                            direction=direction,
                                            units=units,
                                            entry_price=gold_signal.entry_price,
                                            sl=gold_signal.stop_loss,
                                            tp=gold_signal.take_profit,
                                            order_id=order_id,
                                        )
                                        trade_journal.log_entry({
                                            "instrument": gold_instrument,
                                            "direction": direction,
                                            "units": units,
                                            "entry_price": gold_signal.entry_price,
                                            "sl": gold_signal.stop_loss,
                                            "tp": gold_signal.take_profit,
                                            "confidence": gold_signal.confidence,
                                            "tier": gold_signal.tier,
                                            "confirmations": gold_signal.confirmations,
                                            "reasons": gold_signal.reasons,
                                            "atr": gold_signal.atr,
                                            "spread_pips": gold_signal.spread_pips,
                                            "session": "london+ny",
                                            "filters_passed": ["gold_session", "gold_signal"],
                                            "filters_failed": [],
                                            "h4_trend_aligned": None,
                                            "signal_score": gold_signal.score,
                                        })
                                        logger.info("✅ GOLD order placed: %s %d oz", direction, units)
                                        n_positions += 1
                                    except Exception as e:
                                        logger.error("❌ Gold order failed: %s", e)
                                else:
                                    logger.info("[DRY RUN] Gold order not placed: %s %d oz", gold_signal.signal, units)
                        else:
                            logger.info("Gold signal rejected: %s", val_reason)
        except Exception as e:
            logger.warning("Gold strategy error: %s", e, exc_info=True)
    else:
        logger.info("Gold position already open — skipping")

    # ── 4h. Crypto Strategy (BTC/USD, ETH/USD) ────────────────────
    crypto_strong_signals = []
    for crypto_inst in config.CRYPTO_INSTRUMENTS:
        if position_state.is_already_open(crypto_inst):
            logger.info("Crypto position already open for %s — skipping", crypto_inst)
            continue

        try:
            from src.crypto_strategy import CryptoSignalGenerator, CryptoRiskManager, CryptoSessionFilter
            crypto_gen = CryptoSignalGenerator()
            crypto_risk = CryptoRiskManager(
                risk_per_trade=config.CRYPTO_RISK_PER_TRADE,
                max_daily_loss=config.CRYPTO_MAX_DAILY_LOSS,
                max_open_positions=config.CRYPTO_MAX_OPEN_POSITIONS,
            )
            crypto_session = CryptoSessionFilter()

            # Crypto session filter (always True — 24/7)
            crypto_time_ok, crypto_time_reason = crypto_session.is_good_time()
            if not crypto_time_ok:
                logger.info("Crypto session filter: %s", crypto_time_reason)
                continue

            # Fetch crypto data (H1 for signals, H4 for trend)
            crypto_df = client.get_candles(crypto_inst, "H1", 200)
            crypto_h4_df = client.get_candles(crypto_inst, "H4", 200)

            if not crypto_df.empty and len(crypto_df) >= 200:
                crypto_price = client.get_current_price(crypto_inst)

                # Spread check
                spread = crypto_price.get("spread", 0)
                spread_ok, spread_reason = crypto_gen.spread_monitor.check_spread(crypto_inst, spread)
                if not spread_ok:
                    logger.info("Crypto spread check: %s", spread_reason)
                    continue

                crypto_signal = crypto_gen.analyze(crypto_df, crypto_price, crypto_h4_df, crypto_inst)

                logger.info(
                    "Crypto signal %s: %s (conf=%.2f, tier=%s, confs=%d/4) | spread=$%.2f",
                    crypto_inst,
                    crypto_signal.signal, crypto_signal.confidence,
                    crypto_signal.tier, crypto_signal.confirmations,
                    crypto_signal.spread_price,
                )

                # Validate and execute
                if crypto_signal.signal in ("BUY", "SELL"):
                    valid, val_reason = crypto_risk.validate_signal(crypto_signal, balance)
                    if valid:
                        units, risk_amt = crypto_risk.calculate_position_size(
                            balance, crypto_signal.entry_price,
                            crypto_signal.stop_loss, crypto_signal.atr,
                        )
                        if units > 0:
                            crypto_signal.units = units
                            crypto_signal.risk_amount = risk_amt

                            if not dry_run:
                                try:
                                    direction = crypto_signal.signal
                                    order_units = units if direction == "BUY" else -units
                                    response = client.place_market_order(
                                        instrument=crypto_inst,
                                        units=order_units,
                                        stop_loss=crypto_signal.stop_loss,
                                        take_profit=crypto_signal.take_profit,
                                    )
                                    order_id = None
                                    if "orderFillTransaction" in response:
                                        order_id = response["orderFillTransaction"].get("id")

                                    position_state.add_position(
                                        instrument=crypto_inst,
                                        direction=direction,
                                        units=units,
                                        entry_price=crypto_signal.entry_price,
                                        sl=crypto_signal.stop_loss,
                                        tp=crypto_signal.take_profit,
                                        order_id=order_id,
                                    )
                                    trade_monitor.register_trade(
                                        instrument=crypto_inst,
                                        direction=direction,
                                        units=units,
                                        entry_price=crypto_signal.entry_price,
                                        sl=crypto_signal.stop_loss,
                                        tp=crypto_signal.take_profit,
                                        order_id=order_id,
                                    )
                                    trade_journal.log_entry({
                                        "instrument": crypto_inst,
                                        "direction": direction,
                                        "units": units,
                                        "entry_price": crypto_signal.entry_price,
                                        "sl": crypto_signal.stop_loss,
                                        "tp": crypto_signal.take_profit,
                                        "confidence": crypto_signal.confidence,
                                        "tier": crypto_signal.tier,
                                        "confirmations": crypto_signal.confirmations,
                                        "reasons": crypto_signal.reasons,
                                        "atr": crypto_signal.atr,
                                        "spread_pips": crypto_signal.spread_price,
                                        "session": "24/7",
                                        "filters_passed": ["crypto_session", "crypto_signal"],
                                        "filters_failed": [],
                                        "h4_trend_aligned": crypto_signal.h4_trend_aligned,
                                        "signal_score": crypto_signal.score,
                                    })
                                    logger.info("✅ CRYPTO order placed: %s %d units", direction, units)
                                    n_positions += 1
                                    crypto_strong_signals.append(crypto_signal)
                                except Exception as e:
                                    logger.error("❌ Crypto order failed: %s", e)
                            else:
                                logger.info("[DRY RUN] Crypto order not placed: %s %d units", crypto_signal.signal, units)
                                crypto_strong_signals.append(crypto_signal)
                    else:
                        logger.info("Crypto signal rejected for %s: %s", crypto_inst, val_reason)
        except Exception as e:
            logger.warning("Crypto strategy error for %s: %s", crypto_inst, e, exc_info=True)

    # ── 5. Save + report ────────────────────────────────────────
    if all_signals:
        save_signal_log(all_signals, balance)

    cb_status = circuit_breaker.get_status()
    trade_summary = trade_monitor.get_summary()
    logger.info(
        "Cycle complete | %d forex + %d crypto scanned | %d forex signals + %d crypto signals | Pos: %d | CB: %d losses | "
        "Trades: %d (%.0f%% WR) P&L: %s",
        len(config.DEFAULT_INSTRUMENTS),
        len(config.CRYPTO_INSTRUMENTS),
        len(strong_signals),
        len(crypto_strong_signals),
        position_state.get_open_count(),
        cb_status["consecutive_losses"],
        trade_summary["total_trades"],
        trade_summary["win_rate"],
        trade_summary["total_pnl"],
    )

    # ── Persist balance for next cycle ──────────────────────
    # (Already persisted early in the cycle right after compute_realized_pnl
    # to prevent double-counting on crash. This second persist is a safety net.)
    balance_tracker.persist()

    return strong_signals


if __name__ == "__main__":
    import argparse
    import signal
    import sys as _sys

    # ── Graceful shutdown handler ─────────────────────────────
    _shutdown_requested = False

    def _handle_shutdown(signum, frame):
        global _shutdown_requested
        if _shutdown_requested:
            logger.warning("Second shutdown signal received — forcing exit")
            _sys.exit(1)
        _shutdown_requested = True
        sig_name = signal.Signals(signum).name
        logger.warning("🛑 %s received — saving state and shutting down...", sig_name)
        try:
            # Persist all critical state before exiting
            balance_tracker.persist()
            circuit_breaker._save_state()
            logger.info("State saved successfully on shutdown")
        except Exception as e:
            logger.error("Error saving state during shutdown: %s", e)
        _sys.exit(0)

    # Register signal handlers (SIGTERM = kill, SIGINT = Ctrl+C)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    parser = argparse.ArgumentParser(description="Forex Trading Bot")
    parser.add_argument("--mode", choices=["once", "loop"], default="once")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--status", action="store_true", help="Show circuit breaker status")
    parser.add_argument("--reset-cb", action="store_true", help="Reset circuit breaker")
    parser.add_argument("--positions", action="store_true", help="Show open positions")
    parser.add_argument("--trades", action="store_true", help="Show trade history")
    parser.add_argument("--report", action="store_true", help="Show trade journal report")
    parser.add_argument("--journal-stats", action="store_true", help="Show detailed journal stats (JSON)")
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

    if args.report:
        print(trade_journal.generate_report())
        sys.exit(0)

    if args.journal_stats:
        stats = trade_journal.get_trade_stats()
        print(json.dumps(stats, indent=2, default=str))
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
