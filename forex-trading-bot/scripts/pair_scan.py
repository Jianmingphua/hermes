"""Quick pair scan - check which pairs meet thresholds right now."""
import sys, json
from datetime import datetime, timezone
import logging
logging.basicConfig(level=logging.WARNING)

from src.config import config
from src.oanda_client import OandaClient
from src.signal_generator import SignalGenerator
from src.indicators import TechnicalIndicators
from src.optimized_params import get_params
from src.session_filter import session_filter
from src.news_filter import news_filter
from src.spread_monitor import spread_monitor
from src.econ_calendar import econ_calendar
from src.position_state import position_state

c = OandaClient()
gen = SignalGenerator(c)

results = []

for instrument in config.DEFAULT_INSTRUMENTS:
    params = get_params(instrument)
    status = {
        "instrument": instrument,
        "granularity": params["granularity"],
        "session": "{}-{}UTC".format(params["session_start"], params["session_end"]),
    }

    # Session check
    good_time, reason = session_filter.is_good_time_custom(
        instrument, params["session_start"], params["session_end"]
    )
    status["session_ok"] = good_time
    status["session_reason"] = reason

    # News check
    safe, reason = news_filter.is_safe_to_trade(instrument)
    status["news_ok"] = safe

    # Econ calendar check
    cal_safe, cal_reason = econ_calendar.is_safe_to_trade(instrument)
    status["calendar_ok"] = cal_safe
    status["calendar_reason"] = cal_reason if not cal_safe else ""

    # Already open
    status["already_open"] = position_state.is_already_open(instrument)

    # Signal (only if session + news + calendar pass)
    if good_time and safe and cal_safe and not status["already_open"]:
        try:
            sig = gen.analyze(instrument)
            status["signal"] = sig.get("signal", "ERROR")
            status["confidence"] = sig.get("confidence", 0)
            status["confirmations"] = sig.get("confirmations", 0)
            status["tier"] = sig.get("tier", "NONE")
            status["h4_aligned"] = sig.get("h4_trend_aligned")
            status["atr"] = sig.get("atr_14", 0)
            spread = sig.get("current_price", {}).get("spread", 0)
            status["spread_pips"] = round(spread * (100 if "JPY" in instrument else 10000), 1)

            conf = status["confidence"]
            confs = status["confirmations"]
            min_conf = params["min_conf"]
            status["meets_threshold"] = (
                status["signal"] in ("BUY", "SELL")
                and conf >= 0.4
                and confs >= min_conf
            )
            status["min_conf_required"] = min_conf
        except Exception as e:
            status["signal"] = "ERROR"
            status["error"] = str(e)[:100]
            status["meets_threshold"] = False
    else:
        status["signal"] = "SKIPPED"
        status["meets_threshold"] = False

    results.append(status)

# Print summary
now = datetime.now(timezone.utc)
print("=" * 85)
print("LIVE PAIR SCAN | {} UTC".format(now.strftime("%Y-%m-%d %H:%M")))
print("=" * 85)
header = "{:<12} {:>4} {:>6} {:>5} {:>5} {:>5} {:>8} {:>6} {:>5} {:>4} {:>6}".format(
    "Pair", "Gran", "Sess", "News", "Cal", "Open", "Signal", "Conf", "Confs", "H4", "Trade?"
)
print(header)
print("-" * 85)

tradeable = 0
for r in results:
    sess = "OK" if r["session_ok"] else "NO"
    news = "OK" if r["news_ok"] else "NO"
    cal = "OK" if r.get("calendar_ok", True) else "NO"
    opn = "YES" if r.get("already_open") else "no"
    sig = r.get("signal", "-")
    conf = r.get("confidence", 0)
    confs = r.get("confirmations", 0)
    h4 = "OK" if r.get("h4_aligned") == True else ("NO" if r.get("h4_aligned") == False else "-")
    trade = ">>> GO <<<" if r.get("meets_threshold") else "no"
    if r.get("meets_threshold"):
        tradeable += 1

    print("{:<12} {:>4} {:>6} {:>5} {:>5} {:>5} {:>8} {:>6.2f} {:>5} {:>4} {:>6}".format(
        r["instrument"], r["granularity"], sess, news, cal, opn, sig, conf, confs, h4, trade))

print("-" * 85)
print("Tradeable pairs: {}/{}".format(tradeable, len(results)))
print()

# Detail for tradeable pairs
for r in results:
    if r.get("meets_threshold"):
        print("  >>> {} {} | conf={:.2f} | {}/{} confs | tier={} | H4={} | spread={} pips | atr={}".format(
            r["instrument"], r["signal"], r["confidence"], r["confirmations"],
            r.get("min_conf_required", "?"), r.get("tier", "?"),
            r.get("h4_aligned"), r.get("spread_pips", "?"), r.get("atr", "?")
        ))
    elif r.get("signal") not in ("SKIPPED", "ERROR", "-") and r.get("signal"):
        print("  ... {} {} | conf={:.2f} | {}/{} confs | H4={} | reason: below threshold".format(
            r["instrument"], r["signal"], r["confidence"], r["confirmations"],
            r.get("min_conf_required", "?"), r.get("h4_aligned")
        ))
