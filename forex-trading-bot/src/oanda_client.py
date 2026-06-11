"""
Forex Trading Bot - OANDA API Client
Wraps oandapyV20 with a clean interface for market data and order execution.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.positions as positions
import oandapyV20.endpoints.pricing as pricing
import pandas as pd

from src.config import config

logger = logging.getLogger(__name__)


class OandaClient:
    """Client for OANDA REST API v20."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        environment: Optional[str] = None,
    ):
        self.api_key = api_key or config.OANDA_API_KEY
        self.account_id = account_id or config.OANDA_ACCOUNT_ID
        self.environment = environment or config.OANDA_ENVIRONMENT
        self.base_url = config.oanda_base_url

        if not self.api_key:
            raise ValueError("OANDA API key is required")

        self.client = oandapyV20.API(
            access_token=self.api_key,
            environment=self.environment,
        )
        logger.info(
            "OandaClient initialized | environment=%s | account=%s",
            self.environment,
            self.account_id or "unknown",
        )

    # ── Account ──────────────────────────────────────────────────

    def get_account_summary(self) -> dict:
        """Get account summary including balance, margin, P&L."""
        r = accounts.AccountSummary(accountID=self.account_id)
        response = self.client.request(r)
        account = response.get("account", {})
        summary = {
            "id": account.get("id"),
            "alias": account.get("alias"),
            "currency": account.get("currency"),
            "balance": float(account.get("balance", 0)),
            "nav": float(account.get("NAV", 0)),
            "unrealized_pnl": float(account.get("unrealizedPL", 0)),
            "margin_used": float(account.get("marginUsed", 0)),
            "margin_available": float(account.get("marginAvailable", 0)),
            "open_trades": int(account.get("openTradeCount", 0)),
        }
        logger.info(
            "Account: balance=%s %s | NAV=%s | P&L=%s | open_trades=%d",
            summary["balance"],
            summary["currency"],
            summary["nav"],
            summary["unrealized_pnl"],
            summary["open_trades"],
        )
        return summary

    def get_account_id(self) -> str:
        """Get the first account ID (useful when account_id is not set)."""
        r = accounts.AccountList()
        response = self.client.request(r)
        accounts_list = response.get("accounts", [])
        if accounts_list:
            return accounts_list[0]["id"]
        raise RuntimeError("No accounts found")

    # ── Market Data ──────────────────────────────────────────────

    def get_candles(
        self,
        instrument: str = "EUR_USD",
        granularity: str = "H1",
        count: int = 500,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fetch candlestick data and return as a pandas DataFrame.

        Args:
            instrument: e.g. "EUR_USD"
            granularity: "M1","M5","M15","H1","H4","D","W","M"
            count: number of candles (max 5000)
            from_time: ISO 8601 start time
            to_time: ISO 8601 end time

        Returns:
            DataFrame with columns: time, open, high, low, close, volume, complete
        """
        params = {
            "granularity": granularity,
            "count": min(count, 5000),
            "price": "M",  # mid prices; use "B" for bid, "A" for ask
        }
        if from_time:
            params["from"] = from_time
        if to_time:
            params["to"] = to_time

        r = instruments.InstrumentsCandles(instrument=instrument, params=params)
        response = self.client.request(r)

        candles = response.get("candles", [])
        if not candles:
            logger.warning("No candles returned for %s %s", instrument, granularity)
            return pd.DataFrame()

        rows = []
        for c in candles:
            row = {
                "time": pd.to_datetime(c["time"]),
                "open": float(c["mid"]["o"]),
                "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]),
                "close": float(c["mid"]["c"]),
                "volume": int(c["volume"]),
                "complete": c["complete"],
            }
            rows.append(row)

        df = pd.DataFrame(rows)
        df.set_index("time", inplace=True)
        df.sort_index(inplace=True)

        logger.info(
            "Fetched %d candles for %s %s | %s → %s",
            len(df),
            instrument,
            granularity,
            df.index[0],
            df.index[-1],
        )
        return df

    def get_current_price(self, instrument: str = "EUR_USD") -> dict:
        """Get current bid/ask price for an instrument."""
        params = {"instruments": instrument}
        r = pricing.PricingInfo(accountID=self.account_id, params=params)
        response = self.client.request(r)

        prices = response.get("prices", [])
        if not prices:
            raise ValueError(f"No price data for {instrument}")

        p = prices[0]
        result = {
            "instrument": p["instrument"],
            "bid": float(p["bids"][0]["price"]),
            "ask": float(p["asks"][0]["price"]),
            "spread": float(p["asks"][0]["price"]) - float(p["bids"][0]["price"]),
            "time": p["time"],
        }
        logger.debug(
            "%s: bid=%s ask=%s spread=%.5f",
            instrument,
            result["bid"],
            result["ask"],
            result["spread"],
        )
        return result

    # ── Order Execution ──────────────────────────────────────────

    def place_market_order(
        self,
        instrument: str,
        units: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        slippage_pips: float = 2.0,
    ) -> dict:
        """
        Place a market order with IOC timeInForce.

        Reads the actual fill price from the response and logs a warning
        if slippage exceeds 0.5 pips.

        Args:
            instrument: e.g. "EUR_USD"
            units: positive = buy, negative = sell (in units of base currency)
            stop_loss: optional stop loss price
            take_profit: optional take profit price
            slippage_pips: maximum allowed slippage in pips (default: 2.0)

        Returns:
            Order response dict with actual fill price added as "fill_price"
        """
        # Get current market price to set price bound and track slippage
        current_price = self.get_current_price(instrument)
        instrument_ask = current_price["ask"]
        instrument_bid = current_price["bid"]

        pip_value = 0.01 if "JPY" in instrument else 0.0001

        if units > 0:  # BUY — use ask price
            entry_price = instrument_ask
        else:  # SELL — use bid price
            entry_price = instrument_bid

        order_body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "IOC",
            }
        }

        if stop_loss:
            order_body["order"]["stopLossOnFill"] = {
                "price": f"{stop_loss:.5f}",
                "timeInForce": "GTC",
            }
        if take_profit:
            order_body["order"]["takeProfitOnFill"] = {
                "price": f"{take_profit:.5f}",
                "timeInForce": "GTC",
            }

        r = orders.OrderCreate(accountID=self.account_id, data=order_body)
        response = self.client.request(r)

        # Parse fill price from response
        fill_price = None
        if "orderFillTransaction" in response:
            fill = response["orderFillTransaction"]
            fill_price = float(fill.get("price", 0))
            fill_units = fill.get("units")
            logger.info(
                "ORDER FILLED: %s %s units @ %s | P&L=%s",
                fill.get("instrument"),
                fill_units,
                fill.get("price"),
                fill.get("pl"),
            )

            # Log slippage warning if fill price differs significantly
            actual_slippage = abs(fill_price - entry_price)
            if actual_slippage > 0.5 * pip_value:
                slippage_pips_actual = actual_slippage / pip_value
                logger.warning(
                    "Significant slippage on %s: requested=%.5f filled=%.5f "
                    "(%.2f pips)",
                    instrument,
                    entry_price,
                    fill_price,
                    slippage_pips_actual,
                )

            # Attach fill price to response for downstream use
            response["fill_price"] = fill_price
        elif "orderCreateTransaction" in response:
            create = response["orderCreateTransaction"]
            logger.info(
                "ORDER CREATED: %s %s units (id=%s)",
                create.get("instrument"),
                create.get("units"),
                create.get("id"),
            )

        return response

    def close_position(self, instrument: str, side: str = "long") -> dict:
        """Close an open position. side='long' or 'short'."""
        if side == "long":
            data = {"longUnits": "ALL"}
        else:
            data = {"shortUnits": "ALL"}

        r = positions.PositionClose(
            accountID=self.account_id,
            instrument=instrument,
            data=data,
        )
        response = self.client.request(r)
        logger.info("Closed %s position on %s", side, instrument)
        return response

    def partial_close_position(self, instrument: str, side: str,
                                units: int) -> dict:
        """
        Partially close a position by specifying units to close.

        Args:
            instrument: e.g. "EUR_USD"
            side: "long" or "short"
            units: number of units to close (positive integer)

        Returns:
            OANDA PositionClose response dict
        """
        units_str = str(abs(units))
        if side == "long":
            data = {"longUnits": units_str}
        else:
            data = {"shortUnits": units_str}

        r = positions.PositionClose(
            accountID=self.account_id,
            instrument=instrument,
            data=data,
        )
        response = self.client.request(r)
        logger.info(
            "Partial close: %s %s %d units", side, instrument, units
        )
        return response

    def get_open_trade_ids(self, instrument: str,
                            side: str = None) -> list[str]:
        """
        Get open trade IDs for an instrument (optionally filtered by side).

        Args:
            instrument: e.g. "EUR_USD"
            side: "long", "short", or None for both

        Returns:
            List of trade ID strings
        """
        import oandapyV20.endpoints.trades as trades

        params = {"instrument": instrument, "state": "OPEN"}
        r = trades.TradesList(accountID=self.account_id, params=params)
        response = self.client.request(r)

        trade_ids = []
        for t in response.get("trades", []):
            if side is None:
                trade_ids.append(t["id"])
            elif side == "long" and int(t.get("currentUnits", 0)) > 0:
                trade_ids.append(t["id"])
            elif side == "short" and int(t.get("currentUnits", 0)) < 0:
                trade_ids.append(t["id"])

        logger.debug(
            "Open trades for %s (%s): %s", instrument, side, trade_ids
        )
        return trade_ids

    def update_trade_stop_loss(self, trade_id: str,
                                new_sl: float) -> dict:
        """
        Update the stop-loss price on an existing open trade.

        OANDA uses TradeCRCDO (Create/Replace/Cancel/Update) via PUT.
        We send the new stopLoss value while preserving takeProfit.

        Args:
            trade_id: the OANDA trade ID to update
            new_sl: new stop-loss price

        Returns:
            OANDA response dict
        """
        import oandapyV20.endpoints.trades as trades

        # First fetch existing trade to get current TP (preserve it)
        r_get = trades.TradeDetails(
            accountID=self.account_id, tradeID=trade_id
        )
        existing = self.client.request(r_get)
        current_tp = existing.get("trade", {}).get("takeProfitOrder", {})

        precision = 3 if "JPY" in str(
            existing.get("trade", {}).get("instrument", "")
        ) else 5

        sl_str = f"{new_sl:.{precision}f}"

        data = {
            "trade": {
                "stopLoss": {
                    "price": sl_str,
                    "timeInForce": "GTC",
                }
            }
        }

        # Preserve existing take profit if set
        if current_tp and current_tp.get("price"):
            data["trade"]["takeProfit"] = {
                "price": current_tp["price"],
                "timeInForce": "GTC",
            }

        r = trades.TradeCRCDO(
            accountID=self.account_id, tradeID=trade_id, data=data
        )
        response = self.client.request(r)
        logger.info(
            "SL updated on trade %s → %s", trade_id, sl_str
        )
        return response

    def get_open_positions(self) -> list[dict]:
        """Get all open positions."""
        r = positions.OpenPositions(accountID=self.account_id)
        response = self.client.request(r)

        pos_list = []
        for p in response.get("positions", []):
            pos = {
                "instrument": p["instrument"],
                "long_units": abs(int(p["long"]["units"])),
                "short_units": abs(int(p["short"]["units"])),
                "unrealized_pnl": float(p.get("unrealizedPL", 0)),
            }
            pos_list.append(pos)

        logger.info("Open positions: %d", len(pos_list))
        return pos_list

    # ── Utility ──────────────────────────────────────────────────

    def get_instruments(self) -> list[dict]:
        """List all available instruments for this account."""
        r = accounts.AccountInstruments(accountID=self.account_id)
        response = self.client.request(r)

        instruments_list = []
        for inst in response.get("instruments", []):
            instruments_list.append({
                "name": inst["name"],
                "type": inst["type"],
                "display_name": inst["displayName"],
                "pip_location": inst["pipLocation"],
                "margin_rate": float(inst.get("marginRate", 0)),
            })

        logger.info("Available instruments: %d", len(instruments_list))
        return instruments_list
