# NEA Singapore Weather APIs — Reference

## v1 vs v2: Critical Difference

**v1 APIs** (`api.data.gov.sg/v1/environment/*`) are the original endpoints.
Some return station-level readings but at times only a small subset of stations
reply. The `air-temperature` endpoint in particular has been observed returning
only 1 station (East Coast Parkway) for extended periods. Do NOT rely on v1 for
current-condition readings.

**v2 APIs** (`api-open.data.gov.sg/v2/real-time/api/*`) are the newer endpoints.
They consistently return all stations (16 for temperature, 76 for rainfall, etc.).
ALWAYS prefer v2 for current-condition data.

**Exception**: The 2-hour area-level forecast is only available via v1:
`https://api.data.gov.sg/v1/environment/2-hour-weather-forecast`

## Known Pitfalls

### Pitfall: v1 air-temperature returns too few stations
Using v1 `air-temperature` can show a misleading "nearest station" that is 20+
km away when only 1 of 16 stations is reporting.
**Fix**: Always use the v2 endpoint instead.

### Pitfall: Ambiguous area names
Some user queries map to multiple forecast areas:
- "Jurong" → Jurong East, Jurong Island, Jurong West
- "Central" → Central Water Catchment or the downtown area

When ambiguous, show results for ALL matching areas. Never silently pick one.

### Pitfall: Showing distant station data without context
Never display a reading without noting the station name and approximate distance
from the queried area.

### Pitfall: Hermes gateway overwrites bot commands
Custom `set_my_commands` registrations get overwritten when Hermes gateway
restarts. Use the skills system (natural language triggers) instead.

## v2 API Response Structure

All v2 endpoints return the same shape. Key fields:
- `data.stations`: Array of station metadata, key by `id`
- `data.readings[0].data`: Array of `{stationId, value}` — always use index 0
- `data.readingUnit`: Unit string ("deg C", "%", "mm")

## Distance Formula

```python
import math
dist_km = math.sqrt((lat1 - lat2)**2 + (lon1 - lon2)**2) * 111
```

Use the `area_metadata` label_location from the forecast API for exact area
coordinates when available.
