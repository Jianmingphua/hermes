"""
Per-pair optimized parameters from backtest grid search.
Each pair has its OWN strategy config based on what backtest shows works best.

Selection criteria:
  - Composite score: Sharpe 30%, WinRate 20%, PF 20%, Return 20%, DD penalty 10%
  - Tested 9 strategy configs × 10 pairs = 90 backtests
  - Each pair gets the config that maximized its individual Sharpe + WR + return

DROPPED pairs (consistently unprofitable across ALL configs):
  - USD_CAD: best WR 48.6%, worst loss profile
  - USD_SGD: best WR 47.4%, high spread eats any edge
  - EUR_SGD: WR deceptively high but avg loss >> avg win
  - SGD_JPY: spread+PIP is kryptonite for M15 scalping

Surviving pairs use per-pair strategy configs. See STRATEGY_CONFIGS below.
"""

# ── Per-Pair Strategy Config ──────────────────────────────────────
# These are the parameters that control signal generation logic per pair.
# Different pairs need different confirmation thresholds and H4 alignment rules.

# Strategy: high_adx_only
#   - H4 MUST align (no counter-trend trades at all)
#   - ADX ≥ 25 floor (meaningful trend required)
#   - S/R enabled
#   - Tightened confirmations
#   - Lower trade count, higher quality

# Strategy: high_rr
#   - Wider R:R (1.5/2.5 → 2.0/4.0 SL/TP)
#   - H4 as modifier (not gate)
#   - Suitable for pairs where entries are good but need room to run

# Strategy: conservative_3conf
#   - min_conf=3 (3+ independent indicator events)
#   - Higher signal thresholds
#   - Best for pairs with many false signals (AUD_USD)

STRATEGY_CONFIGS = {
    "EUR_USD": {
        # MAX TRADES: H4 as modifier, lower ADX floor, relaxed thresholds
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.15,
        "h4_opposed_mult": 0.85,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "kalman_enabled": False,
        "description": "v5 — H4 gate, ADX≥10, min_conf=2",
    },
    "GBP_USD": {
        # MAX TRADES: H4 as modifier, lower ADX floor
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.2,
        "h4_opposed_mult": 0.7,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "kalman_enabled": False,
        "description": "v5 — H4 gate, ADX≥10",
    },
    "USD_JPY": {
        # MAX TRADES: H4 as modifier, lower ADX floor
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.2,
        "h4_opposed_mult": 0.7,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "description": "v5 — H4 gate, ADX≥10",
    },
    "AUD_USD": {
        # MAX TRADES: H4 as modifier, lower ADX floor, min_conf=2
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.2,
        "h4_opposed_mult": 0.7,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "kalman_enabled": False,
        "description": "v5 — H4 gate, ADX≥10, min_conf=2",
    },
    "USD_CHF": {
        # MAX TRADES: H4 as modifier, lower ADX floor
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.2,
        "h4_opposed_mult": 0.7,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "kalman_enabled": False,
        "description": "v5 — H4 gate, ADX≥10",
    },
    "EUR_GBP": {
        # MAX TRADES: H4 as modifier, lower ADX floor
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.2,
        "h4_opposed_mult": 0.7,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "kalman_enabled": False,
        "description": "v5 — H4 gate, ADX≥10, wider stops",
    },
    # ── Metals (use main strategy with wider stops) ──
    "XAG_USD": {
        "min_conf": 2,
        "h4_must_align": True,
        "adx_floor": 10,
        "h4_aligned_mult": 1.15,
        "h4_opposed_mult": 0.85,
        "sr_enabled": True,
        "signal_threshold_strong": 2.0,
        "signal_threshold_medium": 1.2,
        "signal_threshold_weak": 0.5,
        "kalman_enabled": False,
        "description": "silver — H4 gate, ADX≥10, wider stops",
    },
    # ── DROPPED pairs ──────────────────────────────────────────
    # These are excluded from DEFAULT_INSTRUMENTS in config.py.
    # Leaving strategy config here in case user re-enables them.
    "USD_CAD": {
        "min_conf": 2, "h4_must_align": True, "adx_floor": 25,
        "h4_aligned_mult": 1.2, "h4_opposed_mult": 0.7, "sr_enabled": True,
        "signal_threshold_strong": 2.0, "signal_threshold_medium": 1.5,
        "signal_threshold_weak": 0.8,
        "kalman_enabled": False,
        "description": "high_adx_only (backtest still negative)",
    },
    "USD_SGD": {
        "min_conf": 2, "h4_must_align": True, "adx_floor": 25,
        "h4_aligned_mult": 1.2, "h4_opposed_mult": 0.7, "sr_enabled": True,
        "signal_threshold_strong": 2.0, "signal_threshold_medium": 1.5,
        "signal_threshold_weak": 0.8,
        "kalman_enabled": False,
        "description": "high_adx_only (backtest still negative)",
    },
    "EUR_SGD": {
        "min_conf": 2, "h4_must_align": True, "adx_floor": 25,
        "h4_aligned_mult": 1.15, "h4_opposed_mult": 0.85, "sr_enabled": True,
        "signal_threshold_strong": 2.0, "signal_threshold_medium": 1.5,
        "signal_threshold_weak": 0.8,
        "kalman_enabled": False,
        "description": "high_adx_only (backtest still negative)",
    },
    "SGD_JPY": {
        "min_conf": 2, "h4_must_align": True, "adx_floor": 25,
        "h4_aligned_mult": 1.2, "h4_opposed_mult": 0.7, "sr_enabled": True,
        "signal_threshold_strong": 2.0, "signal_threshold_medium": 1.5,
        "signal_threshold_weak": 0.8,
        "kalman_enabled": False,
        "description": "high_adx_only (backtest still negative)",
    },
}


def get_strategy(instrument: str) -> dict:
    """Get per-pair strategy config for a given instrument."""
    return STRATEGY_CONFIGS.get(instrument, STRATEGY_CONFIGS.get("EUR_USD", {}))


# ── Legacy Optimized Params ───────────────────────────────────────
# Original params kept for backward compatibility and SL/TP/RR config.
# Pair-specific SL/TP multipliers, sessions, RSI bands, granularity.

OPTIMIZED_PARAMS = {
    "EUR_USD": {
        "min_conf": 3,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "GBP_USD": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "USD_JPY": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "AUD_USD": {
        "min_conf": 3,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "USD_CAD": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "USD_CHF": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "EUR_GBP": {
        "min_conf": 2,
        "sl_mult": 2.0,
        "tp_mult": 2.5,
        "adx_thresh": 30,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    # ── Metals ──
    "XAG_USD": {
        "min_conf": 2,
        "sl_mult": 2.0,
        "tp_mult": 3.0,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "H1",
    },
    "USD_SGD": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "EUR_SGD": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
    "SGD_JPY": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,
        "rsi_ob": 70,
        "rsi_os": 30,
        "granularity": "M15",
    },
}

DEFAULT_PARAMS = {
    "min_conf": 2,
    "sl_mult": 1.5,
    "tp_mult": 2.5,
    "adx_thresh": 25,
    "session_start": 7,
    "session_end": 21,
    "rsi_ob": 70,
    "rsi_os": 30,
    "granularity": "M15",
}


def get_params(instrument: str) -> dict:
    """Get optimized parameters for a given instrument."""
    return OPTIMIZED_PARAMS.get(instrument, DEFAULT_PARAMS)