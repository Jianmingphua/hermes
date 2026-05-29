---
name: sg-weather
description: "Singapore weather query - current conditions and 2-hour forecast for any area in Singapore using NEA API. Triggered by weather-related queries in conversation."
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
- "/weather" (treat as weather query, not a Hermes slash command)
- Any mention of a Singapore postal code with weather intent

## API Endpoints

Use the **v2 APIs** — they have better station coverage than v1.

| Data | URL |
|------|-----|
| 2-hour forecast | `https://api.data.gov.sg/v1/environment/2-hour-weather-forecast` |
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

## Alert Keywords

Conditions that constitute bad weather:
- Light Rain, Light Showers
- Showers, Rain
- Moderate Rain, Moderate Showers
- Heavy Rain, Heavy Showers
- Thundery Showers, Thunderstorm, Storm

## Notes

- All APIs are free, no API key needed
- v2 data updates every 1-5 minutes
- v1 forecast covers the next 2 hours
- For "Jurong" queries: check Jurong East, Jurong Island, and Jurong West separately
- For food/recommendation queries near a location, use Overpass API (`https://overpass-api.de/api/interpreter`) to find nearby amenities, and Firecrawl (`http://localhost:3002/v1/scrape`) to scrape review sites. Do NOT try `web_search`.

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
