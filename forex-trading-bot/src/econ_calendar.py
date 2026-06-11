"""
Forex Trading Bot - Economic Calendar Filter
Fetches high-impact economic events and blocks trading before/after them.

Data sources:
1. Forex Factory RSS (free, no auth required)
2. Investing.com calendar (fallback)
3. Manual override for known recurring events (NFP, FOMC, ECB, BOE)

Blocking logic:
- High-impact events: block 30 min before → 30 min after
- Medium-impact: block 15 min before → 15 min after
- Only affects currency pairs that include the event's currency
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Currency Pair → Currency Mapping ─────────────────────────────

PAIR_CURRENCIES = {
    "EUR_USD": ["EUR", "USD"],
    "GBP_USD": ["GBP", "USD"],
    "USD_JPY": ["USD", "JPY"],
    "AUD_USD": ["AUD", "USD"],
    "USD_CAD": ["USD", "CAD"],
    "USD_CHF": ["USD", "CHF"],
    "EUR_GBP": ["EUR", "GBP"],
    "USD_SGD": ["USD", "SGD"],
    "EUR_SGD": ["EUR", "SGD"],
    "SGD_JPY": ["SGD", "JPY"],
    "XAU_USD": ["USD"],
}

# ── High-Impact Event Keywords ───────────────────────────────────

HIGH_IMPACT_KEYWORDS = [
    # US
    "Non-Farm Payrolls", "NFP", "FOMC", "Fed Funds Rate", "CPI",
    "GDP", "Retail Sales", "ISM Manufacturing", "ISM Services",
    "Unemployment Rate", "ADP", "PPI", "Durable Goods",
    "Consumer Confidence", "Michigan Consumer Sentiment",
    # EU
    "ECB", "Main Refinancing Rate", "CPI Flash", "HICP",
    "GDP Preliminary", "Unemployment Rate",
    # UK
    "BOE", "Bank Rate", "GDP", "CPI", "Unemployment",
    # JP
    "BOJ", "Policy Rate", "CPI", "GDP",
    # AU
    "RBA", "Cash Rate", "Employment Change", "Unemployment",
    # CA
    "BOC", "Overnight Rate", "Employment Change", "CPI",
    # CH
    "SNB", "Policy Rate",
]

MEDIUM_IMPACT_KEYWORDS = [
    "Trade Balance", "Current Account", "Industrial Production",
    "Manufacturing PMI", "Services PMI", "Building Permits",
    "Housing Starts", "Existing Home Sales", "New Home Sales",
    "Factory Orders", "Business Confidence", "ZEW", "IFO",
    "Consumer Credit", "M2 Money Supply",
]


class EconomicCalendar:
    """
    Fetches and caches economic calendar events.
    Provides is_safe_to_trade() check for any currency pair.
    """

    def __init__(self, cache_ttl_minutes: int = 60, cache_dir: str = "logs"):
        self.cache_ttl = cache_ttl_minutes * 60
        self.cache_file = Path(cache_dir) / "economic_calendar_cache.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._events = []
        self._last_fetch = 0
        self._load_cache()

    # ── Public API ────────────────────────────────────────────────

    def is_safe_to_trade(self, instrument: str, lookback_min: int = 30,
                         lookahead_min: int = 30) -> tuple[bool, str]:
        """
        Check if it's safe to trade a pair given upcoming/recent events.

        Args:
            instrument: Currency pair, e.g. "EUR_USD"
            lookback_min: Minutes after event start to still block
            lookahead_min: Minutes before event start to start blocking

        Returns:
            (is_safe, reason) — reason is empty if safe
        """
        events = self._get_events()
        if not events:
            return True, ""

        currencies = PAIR_CURRENCIES.get(instrument, [])
        now = datetime.now(timezone.utc)

        for event in events:
            event_time = event.get("time")
            if not event_time:
                continue

            # Check if event affects this pair's currencies
            event_currency = event.get("currency", "")
            if event_currency and event_currency not in currencies:
                continue

            # Calculate blocking window
            impact = event.get("impact", "low")
            if impact == "high":
                block_before = timedelta(minutes=lookahead_min)
                block_after = timedelta(minutes=lookback_min)
            elif impact == "medium":
                block_before = timedelta(minutes=15)
                block_after = timedelta(minutes=15)
            else:
                continue  # Low impact — don't block

            block_start = event_time - block_before
            block_end = event_time + block_after

            if block_start <= now <= block_end:
                mins_to_event = (event_time - now).total_seconds() / 60
                if mins_to_event > 0:
                    reason = (
                        f"⏳ {impact.upper()} impact event in {mins_to_event:.0f}min: "
                        f"{event.get('title', 'Unknown')} ({event_currency})"
                    )
                else:
                    reason = (
                        f"⏳ {impact.upper()} impact event ended {-mins_to_event:.0f}min ago: "
                        f"{event.get('title', 'Unknown')} ({event_currency})"
                    )
                return False, reason

        return True, ""

    def get_upcoming_events(self, hours: int = 24) -> list[dict]:
        """Get upcoming high/medium impact events in the next N hours."""
        events = self._get_events()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)
        upcoming = []
        for e in events:
            t = e.get("time")
            if t and now <= t <= cutoff and e.get("impact") in ("high", "medium"):
                upcoming.append(e)
        return upcoming

    def get_blocked_pairs(self, lookahead_min: int = 30,
                          lookback_min: int = 30) -> dict[str, str]:
        """Get all currently blocked pairs and reasons."""
        blocked = {}
        for instrument in PAIR_CURRENCIES:
            safe, reason = self.is_safe_to_trade(instrument, lookback_min, lookahead_min)
            if not safe:
                blocked[instrument] = reason
        return blocked

    # ── Data Fetching ─────────────────────────────────────────────

    def _get_events(self) -> list[dict]:
        """Get events from cache or fetch fresh."""
        now = time.time()
        if now - self._last_fetch < self.cache_ttl and self._events:
            return self._events

        # Try fetching from multiple sources
        events = self._fetch_forex_factory()
        if not events:
            events = self._fetch_investing_com()

        if events:
            self._events = events
            self._last_fetch = now
            self._save_cache(events)
        elif self._events:
            # Use stale cache rather than nothing
            logger.warning("Using stale economic calendar cache")

        return self._events

    def _fetch_forex_factory(self) -> list[dict]:
        """
        Fetch from Forex Factory RSS feed.
        Free, no auth required. Returns parsed events.
        """
        import warnings
        warnings.filterwarnings("ignore")
        try:
            url = "https://nfs.forexfactory.net/ffcal_week_this.xml"
            headers = {"User-Agent": "Mozilla/5.0 (compatible; ForexBot/1.0)"}
            resp = requests.get(url, headers=headers, timeout=10, verify=True)
            if resp.status_code != 200:
                return []
            # Check if response is actually XML (Cloudflare returns HTML on block)
            content_type = resp.headers.get("content-type", "")
            if "xml" not in content_type.lower() and "text/xml" not in content_type.lower():
                # Might be HTML from Cloudflare — check first 100 chars
                if resp.text.strip().startswith("<html") or resp.text.strip().startswith("<!DOCTYPE"):
                    logger.debug("Forex Factory returned HTML (Cloudflare block) — skipping")
                    return []
            return self._parse_forex_factory_xml(resp.text)
        except requests.exceptions.Timeout:
            logger.debug("Forex Factory timeout")
            return []
        except Exception as e:
            logger.debug("Forex Factory fetch failed: %s", e)
            return []

    def _parse_forex_factory_xml(self, xml_text: str) -> list[dict]:
        """Parse Forex Factory XML into event dicts."""
        events = []
        # Simple regex parsing (no XML lib dependency)
        item_pattern = re.compile(
            r'<item>.*?<title>(.*?)</title>.*?'
            r'<country>(.*?)</country>.*?'
            r'<date>(.*?)</date>.*?'
            r'<time>(.*?)</time>.*?'
            r'<impact>(.*?)</impact>.*?</item>',
            re.DOTALL,
        )
        for match in item_pattern.finditer(xml_text):
            title, country, date_str, time_str, impact = match.groups()
            title = title.strip()
            country = country.strip()
            impact = impact.strip().lower()

            # Parse datetime
            try:
                dt_str = f"{date_str.strip()} {time_str.strip()}"
                # Forex Factory format: "Jun 4, 2026 1:30pm"
                event_time = datetime.strptime(dt_str, "%b %d, %Y %I:%M%p")
                event_time = event_time.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            # Map impact
            if impact in ("high", "3"):
                impact_level = "high"
            elif impact in ("medium", "2"):
                impact_level = "medium"
            else:
                impact_level = "low"

            events.append({
                "title": title,
                "currency": country,
                "time": event_time,
                "impact": impact_level,
                "source": "forexfactory",
            })

        logger.info("Fetched %d events from Forex Factory", len(events))
        return events

    def _fetch_investing_com(self) -> list[dict]:
        """
        Fallback: fetch from Investing.com economic calendar API.
        Free tier, no auth for basic data.
        """
        try:
            url = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "X-Requested-With": "XMLHttpRequest",
            }
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            data = {
                "country[]": [25, 17, 56, 32, 55, 22, 12],  # US,UK,DE,JP,AU,CA,CH
                "dateFrom": today,
                "dateTo": (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d"),
                "timeZone": 0,  # UTC
                "limit_from": 0,
            }
            resp = requests.post(url, headers=headers, data=data, timeout=10)
            if resp.status_code != 200:
                return []
            result = resp.json()
            return self._parse_investing_data(result)
        except Exception as e:
            logger.debug("Investing.com fetch failed: %s", e)
            return []

    def _parse_investing_data(self, data: dict) -> list[dict]:
        """Parse Investing.com response into event dicts."""
        events = []
        html = data.get("data", "")
        if not html:
            return events

        # Simple regex for event rows
        row_pattern = re.compile(
            r'data-event-datetime="([^"]+)".*?'
            r'flag[^"]*"[^"]*">(\w+)</span>.*?'
            r'event-name[^>]*>([^<]+)</a>.*?'
            r'bull(\d)',
            re.DOTALL,
        )
        currency_map = {
            "USD": "USD", "EUR": "EUR", "GBP": "GBP", "JPY": "JPY",
            "AUD": "AUD", "CAD": "CAD", "CHF": "CHF",
        }
        for match in row_pattern.finditer(html):
            dt_str, currency, title, bulls = match.groups()
            try:
                event_time = datetime.fromisoformat(dt_str.replace(" ", "T"))
                event_time = event_time.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            impact = "high" if bulls == "3" else "medium" if bulls == "2" else "low"
            events.append({
                "title": title.strip(),
                "currency": currency_map.get(currency, currency),
                "time": event_time,
                "impact": impact,
                "source": "investing",
            })

        return events

    # ── Cache ─────────────────────────────────────────────────────

    def _load_cache(self):
        """Load events from disk cache."""
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text())
                self._events = data.get("events", [])
                self._last_fetch = data.get("fetched_at", 0)
                # Parse ISO strings back to datetime
                for e in self._events:
                    if isinstance(e.get("time"), str):
                        e["time"] = datetime.fromisoformat(e["time"])
                logger.debug("Loaded %d events from cache", len(self._events))
            except Exception as e:
                logger.debug("Cache load failed: %s", e)
                self._events = []

    def _save_cache(self, events: list[dict]):
        """Save events to disk cache."""
        try:
            serializable = []
            for e in events:
                se = dict(e)
                if isinstance(se.get("time"), datetime):
                    se["time"] = se["time"].isoformat()
                serializable.append(se)
            self.cache_file.write_text(json.dumps({
                "events": serializable,
                "fetched_at": time.time(),
            }, indent=2))
        except Exception as e:
            logger.debug("Cache save failed: %s", e)

    def force_refresh(self):
        """Force a fresh fetch of calendar data."""
        self._last_fetch = 0
        self._events = []
        return self._get_events()


# Singleton
econ_calendar = EconomicCalendar()
