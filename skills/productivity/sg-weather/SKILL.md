---
name: sg-weather
description: "Singapore weather query - current conditions, 2-hour forecast, 24-hour outlook, and 4-day weather outlook from NEA APIs. Triggered by weather-related queries in conversation."
metadata:
  hermes:
    tags: [weather, singapore, nea, forecast]
---

# SG Weather

Query real-time Singapore weather data from NEA (National Environment Agency) APIs.

## When to Use

Use this skill whenever the user asks about weather, rain, temperature, humidity, forecast, or conditions in Singapore or any Singapore area. This is triggered by natural language, NOT by a slash command.

Examples that should load this skill:
- "what's the weather"
- "is it raining"
- "weather forecast"
- "how's the weather in Tampines"
- "is it going to rain today"
- "singapore weather"
- "what's the weather tomorrow"
- "weather this weekend at Sentosa"
- "will it rain on Sunday at Mandai from 2-5pm"
- "/weather" (treat as weather query, not a Hermes slash command)
- Any mention of a Singapore postal code with weather intent

**Forecast horizon dispatch:**
- "now" / "current" / "right now" → current conditions (v2 stations)
- "next few hours" / "later today" / no timeframe → 2-hour forecast (v1)
- "tomorrow" / "tonight" / "this afternoon" / "Monday" / specific time window → **24-hour forecast (v1)** + 4-day outlook (v1)
- "this weekend" / "next 3 days" / "next week" / multi-day planning → **4-day weather outlook (v1)**

## API Endpoints

Use the **v2 APIs** — they have better station coverage than v1.

| Data | URL |
|------|-----|
| 2-hour forecast | `https://api.data.gov.sg/v1/environment/2-hour-weather-forecast` |
| 24-hour forecast | `https://api.data.gov.sg/v1/environment/24-hour-weather-forecast` |
| 4-day weather outlook | `https://api.data.gov.sg/v1/environment/4-day-weather-forecast` |
| Air temperature | `https://api-open.data.gov.sg/v2/real-time/api/air-temperature` |
| Rainfall | `https://api-open.data.gov.sg/v2/real-time/api/rainfall` |
| Relative humidity | `https://api-open.data.gov.sg/v2/real-time/api/relative-humidity` |
| Wind speed | `https://api-open.data.gov.sg/v2/real-time/api/wind-speed` |
| Wind direction | `https://api-open.data.gov.sg/v2/real-time/api/wind-direction` |

### v2 API Response Structure

All v2 endpoints return:
```json
{
  "code": 0,
  "data": {
    "stations": [{"id": "S109", "name": "Ang Mo Kio Avenue 5", "location": {"latitude": 1.379, "longitude": 103.85}}],
    "readings": [{"timestamp": "...", "data": [{"stationId": "S109", "value": 29.4}]}],
    "readingType": "...",
    "readingUnit": "deg C"
  }
}
```

Build a `stations` dict keyed by `id`, then match `readings[0].data[*].stationId` to get values. **Always pick the closest station by distance** — never just the first result or a random distant station.

### v1 Forecast Response

The 2-hour forecast API (v1) returns area-level forecasts:
```json
{
  "items": [{
    "valid_period": {"start": "...", "end": "..."},
    "forecasts": [{"area": "Tampines", "forecast": "Cloudy"}]
  }],
  "area_metadata": [{"name": "Tampines", "label_location": {"latitude": 1.35, "longitude": 103.94}}]
}
```

### v1 24-Hour Forecast Response

The 24-hour forecast covers the next ~24 hours split into named time blocks (Night → Day → Evening). Useful for "tomorrow afternoon" or specific time-window planning.

```json
{
  "items": [{
    "update_timestamp": "2026-06-05T17:41:19+08:00",
    "timestamp": "2026-06-05T17:32:00+08:00",
    "valid_period": {"start": "2026-06-05T18:00:00+08:00", "end": "2026-06-06T18:00:00+08:00"},
    "general": {
      "forecast": "Thundery Showers",
      "relative_humidity": {"low": 55, "high": 95},
      "temperature": {"low": 26, "high": 34},
      "wind": {"speed": {"low": 10, "high": 20}, "direction": "SSE"}
    },
    "periods": [
      {"time": {"start": "2026-06-05T18:00:00+08:00", "end": "2026-06-06T06:00:00+08:00"},
       "regions": {"north": "Partly Cloudy (Night)", "south": "Partly Cloudy (Night)", "east": "...", "central": "...", "west": "..."}},
      {"time": {"start": "2026-06-06T12:00:00+08:00", "end": "2026-06-06T18:00:00+08:00"},
       "regions": {"north": "Thundery Showers", "central": "Thundery Showers", "east": "Thundery Showers", "south": "Cloudy", "west": "Cloudy"}}
    ]
  }]
}
```

**Key fields:**
- `general.forecast` — island-wide summary (e.g. "Thundery Showers", "Partly Cloudy")
- `general.temperature.low/high` — daily min/max in °C
- `general.relative_humidity.low/high` — daily min/max percentage
- `general.wind` — direction + speed range (km/h)
- `periods[].regions.<region>` — region-specific forecast (regions: `west`, `east`, `north`, `south`, `central`)
- `periods[].time.start/end` — exact timestamps for the period

**Singapore region → area mapping (use to match user location):**
- `north` — Mandai, Woodlands, Yishun, Sembawang, Sungei Kadut, Bukit Panjang, Choa Chu Kang
- `south` — Sentosa, Marine Parade, Southern Islands, Queenstown, Bukit Merah, Tanglin
- `east` — Tampines, Pasir Ris, Changi, Bedok, Punggol, Sengkang, Hougang, Serangoon
- `west` — Jurong East/West/Island, Bukit Batok, Clementi, Boon Lay, Pioneer, Tuas, Western Islands
- `central` — Toa Payoh, Bishan, Ang Mo Kio, Novena, Kallang, Geylang, City, Jalan Bahar, Central Water Catchment, Western Water Catchment

### v1 4-Day Weather Outlook Response

For "this weekend", "next week", or general multi-day planning. Returns one entry per day:

```json
{
  "items": [{
    "update_timestamp": "2026-06-05T17:41:18+08:00",
    "forecasts": [
      {"date": "2026-06-06", "forecast": "Afternoon thundery showers",
       "temperature": {"low": 26, "high": 34},
       "relative_humidity": {"low": 60, "high": 95},
       "wind": {"direction": "SSE", "speed": {"low": 10, "high": 25}}},
      {"date": "2026-06-07", "forecast": "Morning thundery showers", ...}
    ]
  }]
}
```

**Note**: 4-day outlook is **island-wide only** — no region breakdown. For region-specific multi-day questions, the 24-hour forecast is more useful (it has the region breakdown).
```

## All Singapore Areas

Ang Mo Kio, Bedok, Bishan, Boon Lay, Bukit Batok, Bukit Merah, Bukit Panjang, Bukit Timah, Central Water Catchment, Changi, Choa Chu Kang, City, Clementi, Geylang, Hougang, Jalan Bahar, Jurong East, Jurong Island, Jurong West, Kallang, Lim Chu Kang, Mandai, Marine Parade, Novena, Pasir Ris, Paya Lebar, Pioneer, Pulau Tekong, Pulau Ubin, Punggol, Queenstown, Seletar, Sembawang, Sengkang, Sentosa, Serangoon, Southern Islands, Sungei Kadut, Tampines, Tanglin, Tengah, Toa Payoh, Tuas, Western Islands, Western Water Catchment, Woodlands, Yishun

## Workflow

### Step 1: Determine the area

If the user specifies an area by name (e.g. "Tampines", "in Bedok", "near Seletar"), use that area.

For ambiguous names (e.g. "Jurong" matches Jurong East, Jurong Island, Jurong West), show results for all matching areas.

**If the user provides a postal code**, you MUST geocode it first — NEVER guess the location. Use Nominatim:
```
curl "https://nominatim.openstreetmap.org/search?format=json&q={postal}+Singapore"
```
Then use the returned lat/lon to find the closest forecast area from `area_metadata`. Singapore postal codes are 6 digits; the first 2 digits indicate the sector but DO NOT rely on sector knowledge alone — always geocode.

If the user does NOT specify an area:
1. Check memory for the user's saved location preference
2. If found, use it directly — mention which area you're using
3. If not found, ask the user to pick an area

### Step 2: Fetch data

Fetch the 2-hour weather forecast:
```bash
curl -s "https://api.data.gov.sg/v1/environment/2-hour-weather-forecast"
```

Fetch 24-hour forecast (for "tomorrow" / specific-time-window queries):
```bash
curl -s "https://api.data.gov.sg/v1/environment/24-hour-weather-forecast"
```

Fetch 4-day outlook (for "this weekend" / multi-day planning):
```bash
curl -s "https://api.data.gov.sg/v1/environment/4-day-weather-forecast"
```

Fetch current conditions (v2 APIs):
```bash
curl -s "https://api-open.data.gov.sg/v2/real-time/api/air-temperature"
curl -s "https://api-open.data.gov.sg/v2/real-time/api/rainfall"
curl -s "https://api-open.data.gov.sg/v2/real-time/api/relative-humidity"
curl -s "https://api-open.data.gov.sg/v2/real-time/api/wind-speed"
```

### Step 3: Parse and present

**Always** find the closest weather stations by distance. Use the area's `label_location` from the forecast API, or the geocoded coordinates for postal codes. Calculate distance to every reporting station and pick the nearest one for each data type. Never show a distant station without noting the distance, and NEVER use a station from a completely different region of Singapore.

Present in this format:
```
Weather for [Area]:

  2-hour forecast: [Condition]
  Valid: [start] to [end]

  Current conditions (nearest stations):
  - Temperature: XX°C ([Station name], X.X km away)
  - Humidity: XX% ([Station name], X.X km away)
  - Rain: X.Xmm / No rain
  - Wind: XX km/h [Direction]
```

If multiple areas match, show each area's forecast separately.

### Step 4: Save preference

If the user specified an area for the first time, ask if they want to save it as their default location for future quick lookups.

## Pitfalls

- **NEVER guess postal code locations.** Always geocode via Nominatim. Postal code sectors can be misleading (e.g., 588207 is Bukit Timah Fire Station at Upper Bukit Timah Road — NOT Tampines).
- **NEVER use `web_search` tool** — it does not exist. For web scraping, use the local Firecrawl instance at `http://localhost:3002` (`/v1/scrape` for pages, `/v1/search` for search).
- **NEVER show a weather station from the wrong part of Singapore.** Always compute distance and pick the nearest station. If the nearest station is >5km away, say so explicitly.
- **v1 air temperature API has very limited coverage** (sometimes only 1 station). Always prefer the v2 API (`api-open.data.gov.sg/v2/real-time/api/air-temperature`).
- **User preference: do not store user location in memory.** Always geocode postal codes fresh. Only store the geocoding method preference, not the location itself.
- **24-hour forecast updates ~once per hour (5-6pm is typical).** If the response shows `update_timestamp` from earlier today and the user asks about tomorrow, note the data is from the latest NEA update — not stale.
- **4-day outlook is island-wide only** — no regional split. For "will it rain in Mandai on Sunday", use 24-hour forecast instead (it has region breakdown).
- **24-hour `periods[].time.start` may be `"Invalid date"`** in the response — this is an NEA data quirk for the daytime period (the first period). Don't error out on it; use the `end` timestamp or `valid_period` to disambiguate, and note the daytime block covers roughly 06:00–12:00 SGT.
- **24-hour forecast uses Singapore region names (`north`, `south`, etc.), not the 2-hour forecast's area names (`Tampines`, `Mandai`).** Use the region→area mapping table in the 24-Hour Forecast Response section above to translate.
- **For "tomorrow" queries**: prefer the 24-hour forecast (gives region-specific time-block forecasts) over the 4-day outlook (island-wide only). Cross-check the date in 4-day outlook to confirm the day.
- **Rainfall readings can lag** — if the user asks "is it raining right now" and the 2-hour forecast says "Light Rain" but the v2 rainfall API shows 0mm, trust the live rainfall reading (the forecast is up to 2 hours old by definition).
- **Cron no_agent script path: `~/.hermes/scripts/` vs project path**: When running a weather alert via cron with `no_agent=True`, the cron resolves relative script paths under `~/.hermes/scripts/` — NOT `/opt/hermes/scripts/`. Patching the wrong copy silently does nothing. Always check both: `diff /opt/hermes/scripts/sg_weather_alert.py ~/.hermes/scripts/sg_weather_alert.py`. If different, patch the cron copy or symlink it.

## Alert Keywords

Conditions that constitute bad weather:
- Light Rain, Light Showers
- Showers, Rain
- Moderate Rain, Moderate Showers
- Heavy Rain, Heavy Showers
- Thundery Showers, Thunderstorm, Storm

## Multi-Day / Specific Time Window Workflow

For queries like "tomorrow afternoon", "Saturday 2-5pm", "this weekend at Mandai", use this extended workflow:

### Step 1: Determine target date and time window

Parse the user's query to identify:
- **Date**: tomorrow / this Saturday / next Monday / specific date
- **Time window**: morning / afternoon / 8:30am-4pm / etc.
- **Location**: area name (e.g. "Mandai") or postal code

### Step 2: Fetch 24-hour forecast first (region-specific)

```bash
curl -s "https://api.data.gov.sg/v1/environment/24-hour-weather-forecast"
```

Cross-check the `valid_period.end` covers the user's target date. If the target is more than 24h out, also fetch the 4-day outlook.

### Step 3: Match user's location to a region

Use the region→area mapping table in the 24-Hour Forecast Response section. If the user's location spans multiple regions, show all of them.

For postal codes, geocode first (Nominatim) → find nearest area → map area to region.

### Step 4: Find the time period that overlaps the user's window

The 24-hour forecast has 2-3 named time blocks (Night, Day, Evening/Afternoon). Walk through `periods[]` and find the one(s) overlapping the user's window.

**Typical Singapore 24-hour forecast periods:**
- `Night`: 18:00 → 06:00 next day
- `Day`: 06:00 → 12:00
- `Afternoon/Evening`: 12:00 → 18:00

### Step 5: Compose answer

Format like:
```
Weather for [Location/Region] on [Day]:

  [Time block 1]: [Forecast] (X°C, humidity Y%, wind Z)
  [Time block 2]: [Forecast] (X°C, humidity Y%, wind Z)

  Daily: [general.forecast], [temp low]-[temp high]°C

  Practical takeaways:
  - [1-2 bullets on what to do based on forecast]
```

**Example for "tomorrow 8:30am-4pm at Mandai":**
```
Tomorrow (Sat 6 Jun) at Mandai (north region):

  8:30 AM – 12 PM: Partly Cloudy, warm & humid
  12 PM – 4 PM: ⚠️ Thundery Showers
  Temp: 26–34°C, humidity 60–95%, SSE wind 10–20 km/h

  Practical takeaways:
  - Morning should be dry — go for outdoor activities early
  - Pack rain gear for the afternoon
```

## Notes

- All APIs are free, no API key needed
- **v2 APIs block requests without a User-Agent header.** Always use `curl -s -A "Mozilla/5.0"` or set a `User-Agent` header programmatically. Python's `urllib` sends a default UA that gets 403'd.
- v2 data updates every 1-5 minutes
- For "Jurong" queries: check Jurong East, Jurong Island, and Jurong West separately
- For food/recommendation queries near a location, use Overpass API (`https://overpass-api.de/api/interpreter`) to find nearby amenities, and Firecrawl (`http://localhost:3002/v1/scrape`) to scrape review sites. Do NOT try `web_search`.
- **Quick fetch script**: `scripts/forecast.sh [24h|4day|2h|both]` returns pretty-printed JSON for the chosen horizon(s).
- **Deep reference**: `references/forecast-endpoints.md` — endpoint quirks, region mapping, period time-block windows, common pitfalls, and worked examples.

## User Saved Areas

Weather alerts configured for: Tampines & Seletar. Default query area: Tampines.

## Reference: Singapore Postal Code Sectors

Common sectors (for quick reference, but ALWAYS geocode to confirm):
- 48-50: Bishan, Ang Mo Kio, Upper Thomson
- 51-52: Tampines, Pasir Ris, Simei
- 56-57: Bedok, Upper East Coast
- 58-60: Bukit Timah, Clementi, Dover, Queensway
- 62-63: Jurong East, Jurong West, Boon Lay
- 79-82: Punggol, Sengkang, Hougang
