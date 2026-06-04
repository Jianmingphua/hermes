"""
Forex Trading Bot - News & Event Filter
Prevents trading around high-impact economic events.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# High-impact events that cause 50-200 pip spikes
HIGH_IMPACT_KEYWORDS = [
    "non-farm payrolls", "nfp", "unemployment rate",
    "fomc", "federal funds rate", "interest rate decision",
    "cpi", "consumer price index", "inflation",
    "gdp", "gross domestic product",
    "retail sales", "pmi", "manufacturing pmi",
    "trade balance", "current account",
    "consumer confidence", "consumer sentiment",
    "ism", "industrial production",
    "building permits", "housing starts",
    "central bank", "fed chair", "ecb press conference",
    "boe", "boj", "rba", "boc", "snb",
    "monetary policy", "rate decision",
]

# Major currencies affected by each event type
CURRENCY_EVENT_MAP = {
    "USD": ["nfp", "fomc", "cpi", "gdp", "retail sales", "unemployment", "ism", "consumer confidence", "federal funds", "fed"],
    "EUR": ["ecb", "interest rate decision", "cpi", "gdp", "pmi", "unemployment"],
    "GBP": ["boe", "interest rate decision", "cpi", "gdp", "pmi", "unemployment"],
    "JPY": ["boj", "interest rate decision", "cpi", "gdp", "trade balance"],
    "AUD": ["rba", "interest rate decision", "cpi", "gdp", "unemployment", "trade balance"],
    "CAD": ["boc", "interest rate decision", "cpi", "gdp", "unemployment"],
    "CHF": ["snb", "interest rate decision", "cpi", "trade balance"],
    "NZD": ["rbnz", "interest rate decision", "cpi", "gdp", "unemployment"],
}

# Trading pause window (minutes before/after event)
PAUSE_BEFORE_MINUTES = 15
PAUSE_AFTER_MINUTES = 30


class NewsFilter:
    """Checks if it's safe to trade based on upcoming economic events."""

    def __init__(self):
        self._cache: Optional[dict] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=15)

    def is_safe_to_trade(self, instrument: str) -> tuple[bool, str]:
        """
        Check if it's safe to trade the given instrument.

        Returns:
            (is_safe, reason) — reason is empty string if safe
        """
        # Extract currencies from instrument (e.g., "EUR_USD" → ["EUR", "USD"])
        parts = instrument.split("_")
        if len(parts) != 2:
            return True, ""

        base_ccy, quote_ccy = parts[0], parts[1]

        # Check 1: Hardcoded high-impact windows (known recurring events)
        safe, reason = self._check_recurring_events(base_ccy, quote_ccy)
        if not safe:
            return False, reason

        # Check 2: Forex Factory calendar
        safe, reason = self._check_forex_factory(base_ccy, quote_ccy)
        if not safe:
            return False, reason

        return True, ""

    def _check_recurring_events(self, base_ccy: str, quote_ccy: str) -> tuple[bool, str]:
        """Check known recurring high-impact event times."""
        now = datetime.now(timezone.utc)

        # NFP: First Friday of each month, 13:30 UTC (8:30 AM EST)
        if now.weekday() == 4 and now.day <= 7:  # First Friday
            nfp_time = now.replace(hour=13, minute=30, second=0, microsecond=0)
            if self._is_within_window(now, nfp_time, PAUSE_BEFORE_MINUTES, PAUSE_AFTER_MINUTES):
                if "USD" in (base_ccy, quote_ccy):
                    return False, f"🔴 NFP release window — avoid USD pairs"

        # FOMC: ~8 times per year, usually Wednesday 18:00 UTC
        # Can't predict exactly without calendar, but we check via FF below

        return True, ""

    def _check_forex_factory(self, base_ccy: str, quote_ccy: str) -> tuple[bool, str]:
        """Check Forex Factory calendar for upcoming high-impact events."""
        events = self._get_forex_factory_events()
        if not events:
            return True, ""

        now = datetime.now(timezone.utc)

        for event in events:
            event_time = event.get("time")
            if not event_time:
                continue

            # Check if event affects our currencies
            event_ccy = event.get("currency", "")
            if event_ccy not in (base_ccy, quote_ccy):
                continue

            # Check if event is high impact
            impact = event.get("impact", "")
            if impact not in ("High", "HIGH", "high", "3", 3):
                continue

            # Check if we're within the pause window
            if isinstance(event_time, str):
                try:
                    event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue

            if self._is_within_window(now, event_time, PAUSE_BEFORE_MINUTES, PAUSE_AFTER_MINUTES):
                return False, (
                    f"🔴 High-impact event: {event.get('title', 'Unknown')} "
                    f"({event_ccy}) at {event_time:%H:%M UTC}"
                )

        return True, ""

    def _get_forex_factory_events(self) -> list[dict]:
        """Fetch today's events from Forex Factory.
        
        Note: FF RSS is unreliable. We use a hybrid approach:
        1. Hardcoded recurring events (NFP, FOMC, etc.)
        2. FF website scrape as fallback
        """
        # Check cache
        if self._cache and self._cache_time:
            if datetime.now(timezone.utc) - self._cache_time < self._cache_ttl:
                return self._cache.get("events", [])

        events = []
        
        # Generate recurring high-impact events for the next 24 hours
        now = datetime.now(timezone.utc)
        events.extend(self._generate_recurring_events(now))

        # Try FF scrape as supplementary
        try:
            url = "https://www.forexfactory.com/calendar"
            response = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            })
            if response.status_code == 200:
                # Parse HTML for event data
                from html.parser import HTMLParser
                # Simple extraction - look for event rows
                content = response.text
                # FF embeds event data in JavaScript
                import re
                event_pattern = re.findall(
                    r'class="calendar__event"[^>]*>.*?'
                    r'class="calendar__currency">(\w+)</span>.*?'
                    r'class="calendar__impact">.*?'
                    r'class="calendar__impact--(\w+)".*?'
                    r'class="calendar__date">.*?(\d{2}:\d{2})',
                    content, re.DOTALL
                )
                for currency, impact, time_str in event_pattern[:20]:
                    events.append({
                        "title": f"FF Event ({currency})",
                        "currency": currency,
                        "impact": impact.capitalize(),
                        "time": f"{now.strftime('%Y-%m-%d')} {time_str}",
                    })
        except Exception as e:
            logger.debug("FF scrape failed (using recurring events only): %s", e)

        self._cache = {"events": events}
        self._cache_time = datetime.now(timezone.utc)
        return events

    def _generate_recurring_events(self, now: datetime) -> list[dict]:
        """Generate known recurring high-impact events."""
        events = []
        today = now.date()

        # NFP: First Friday of each month, 13:30 UTC
        if now.weekday() == 4 and now.day <= 7:
            nfp_time = datetime(today.year, today.month, today.day, 13, 30, tzinfo=timezone.utc)
            events.append({
                "title": "Non-Farm Payrolls",
                "currency": "USD",
                "impact": "High",
                "time": nfp_time.isoformat(),
            })

        # FOMC: Roughly every 6 weeks on Wednesday, 18:00 UTC
        # Approximate: 8 meetings per year
        fomc_dates_2026 = [
            (1, 28), (3, 18), (4, 29), (6, 17),
            (7, 29), (9, 16), (11, 4), (12, 16),
        ]
        for month, day in fomc_dates_2026:
            if today.month == month and abs(today.day - day) <= 1:
                fomc_time = datetime(today.year, today.month, today.day, 18, 0, tzinfo=timezone.utc)
                events.append({
                    "title": "FOMC Statement",
                    "currency": "USD",
                    "impact": "High",
                    "time": fomc_time.isoformat(),
                })

        # ECB Rate Decision: Every 6 weeks on Thursday, 12:45 UTC
        ecb_dates_2026 = [
            (1, 22), (3, 5), (4, 16), (6, 4),
            (7, 16), (9, 10), (10, 22), (12, 17),
        ]
        for month, day in ecb_dates_2026:
            if today.month == month and abs(today.day - day) <= 1:
                ecb_time = datetime(today.year, today.month, today.day, 12, 45, tzinfo=timezone.utc)
                events.append({
                    "title": "ECB Rate Decision",
                    "currency": "EUR",
                    "impact": "High",
                    "time": ecb_time.isoformat(),
                })

        # BOE Rate Decision: Every 6 weeks on Thursday, 12:00 UTC
        boe_dates_2026 = [
            (1, 22), (3, 5), (4, 16), (6, 4),
            (7, 16), (9, 10), (10, 22), (12, 17),
        ]
        for month, day in boe_dates_2026:
            if today.month == month and abs(today.day - day) <= 1:
                boe_time = datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc)
                events.append({
                    "title": "BOE Rate Decision",
                    "currency": "GBP",
                    "impact": "High",
                    "time": boe_time.isoformat(),
                })

        return events

    @staticmethod
    def _is_within_window(
        now: datetime,
        event_time: datetime,
        before_min: int,
        after_min: int,
    ) -> bool:
        """Check if 'now' is within ±window of event_time."""
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)

        diff = abs((now - event_time).total_seconds())
        window_seconds = max(before_min, after_min) * 60
        return diff <= window_seconds


# Singleton
news_filter = NewsFilter()
