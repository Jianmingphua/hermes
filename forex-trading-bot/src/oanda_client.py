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
    ) -> dict:
        """
        Place a market order.

        Args:
            instrument: e.g. "EUR_USD"
            units: positive = buy, negative = sell (in units of base currency)
            stop_loss: optional stop loss price
            take_profit: optional take profit price

        Returns:
            Order response dict
        """
        order_body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
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

        # Parse fill or create confirmation
        if "orderFillTransaction" in response:
            fill = response["orderFillTransaction"]
            logger.info(
                "ORDER FILLED: %s %s units @ %s | P&L=%s",
                fill.get("instrument"),
                fill.get("units"),
                fill.get("price"),
                fill.get("pl"),
            )
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

    def get_open_positions(self) -> list[dict]:
        """Get all open positions."""
        r = positions.OpenPositions(accountID=self.account_id)
        response = self.client.request(r)

        pos_list = []
        for p in response.get("positions", []):
            pos = {
                "instrument": p["instrument"],
                "long_units": int(p["long"]["units"]),
                "short_units": int(p["short"]["units"]),
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
