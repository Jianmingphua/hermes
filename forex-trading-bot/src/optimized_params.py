"""
Per-pair optimized parameters from backtest grid search.
Selected based on composite score: Sharpe 30%, WinRate 25%, Return 20%, MaxDD penalty 15%, SQN 10%.

Each pair has different optimal settings — this is the key improvement over
the old fixed 2/4 threshold for all pairs.
"""

# ── Optimized Parameters Per Pair ───────────────────────────────
# Format: (min_conf, sl_mult, tp_mult, adx_thresh, session_start, session_end, rsi_ob, rsi_os)

OPTIMIZED_PARAMS = {
    # All pairs: M15 with London + NY session focus
    # Research shows M15 scalping works best during London/NY sessions
    # Asian session filtered out (low volatility, spreads eat profits)
    # Session: 07:00-21:00 UTC = London open through NY close

    "EUR_USD": {
        "min_conf": 2,
        "sl_mult": 1.5,
        "tp_mult": 2.5,
        "adx_thresh": 20,
        "session_start": 7,
        "session_end": 21,  # London + NY sessions
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

    "EUR_GBP": {
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

# ── Default fallback for any pair not in the optimized set ──────
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
