# NEA Forecast Endpoints — Detailed Reference

Deep dive into Singapore's NEA (National Environment Agency) forecast APIs. Use this when SKILL.md workflow needs more context, edge cases, or when debugging unexpected responses.

## Endpoint Summary

| Horizon | Endpoint | Use Case | Granularity | Update Frequency |
|---------|----------|----------|-------------|------------------|
| 2-hour | `/v1/environment/2-hour-weather-forecast` | Now/next few hours | Per-area (44 areas) | ~30 min |
| 24-hour | `/v1/environment/24-hour-weather-forecast` | Tomorrow/specific time window | Per-region (5 regions) + general | ~1 hour |
| 4-day | `/v1/environment/4-day-weather-forecast` | Multi-day planning | Island-wide only | ~1 hour |
| Real-time stations | `/v2/real-time/api/{air-temperature,rainfall,...}` | Current conditions | Per-station | 1-5 min |

## 24-Hour Forecast — Deep Dive

### Time Periods (Singapore Time / UTC+8)

The 24-hour forecast typically returns 2-3 named periods:

| Period Name | Typical Window | Notes |
|-------------|----------------|-------|
| Night | 18:00 → 06:00 (next day) | First period in the response |
| Day | 06:00 → 12:00 | Often has `Invalid date` for `time.start` — NEA quirk |
| Afternoon/Evening | 12:00 → 18:00 | Final period; covers post-lunch thunderstorm peak |

**Critical quirk**: The first period in `periods[]` may have `time.start: "Invalid date"`. This is an NEA response bug, not a parsing error. Use the `end` timestamp or assume Day block is 06:00–12:00. Don't fail on it.

### Region Codes

| Region Code | Singapore Areas |
|-------------|-----------------|
| `north` | Mandai, Woodlands, Yishun, Sembawang, Sungei Kadut, Bukit Panjang, Choa Chu Kang, Lim Chu Kang, Tengah |
| `south` | Sentosa, Marine Parade, Southern Islands, Queenstown, Bukit Merah, Tanglin, Western Water Catchment |
| `east` | Tampines, Pasir Ris, Changi, Bedok, Punggol, Sengkang, Hougang, Serangoon, Pulau Tekong, Pulau Ubin |
| `west` | Jurong East, Jurong West, Jurong Island, Bukit Batok, Clementi, Boon Lay, Pioneer, Tuas, Western Islands |
| `central` | Toa Payoh, Bishan, Ang Mo Kio, Novena, Kallang, Geylang, City, Jalan Bahar, Central Water Catchment |

### Forecast Vocabulary

NEA's 24-hour forecast uses these strings (case-sensitive, includes the time-of-day suffix):

- `Partly Cloudy (Day)` / `Partly Cloudy (Night)`
- `Cloudy`
- `Light Rain` / `Light Showers`
- `Showers` / `Rain`
- `Moderate Rain` / `Moderate Showers`
- `Heavy Rain` / `Heavy Showers`
- `Thundery Showers` (most common Singapore PM pattern)
- `Thunderstorm`
- `Fair` (rarely used)
- `Windy`

### General Block vs Period Block

The `general` block is the **island-wide summary** for the day:
- `forecast`: e.g. "Thundery Showers" — most likely overall condition
- `temperature`: daily min/max
- `relative_humidity`: daily min/max
- `wind`: direction + speed range

The `periods[].regions` blocks are **time-specific, region-specific** forecasts. They can disagree with `general.forecast` (e.g. general says "Partly Cloudy" but a period says "Thundery Showers" in the north) — always lead with the period-specific data for the user's time window.

## 4-Day Outlook — Deep Dive

### Response Structure

```json
{
  "items": [{
    "update_timestamp": "2026-06-05T17:41:18+08:00",
    "timestamp": "2026-06-05T17:32:00+08:00",
    "forecasts": [
      {
        "date": "2026-06-06",
        "timestamp": "2026-06-06T00:00:00+08:00",
        "forecast": "Afternoon thundery showers",
        "temperature": {"low": 26, "high": 34},
        "relative_humidity": {"low": 60, "high": 95},
        "wind": {"direction": "SSE", "speed": {"low": 10, "high": 25}}
      }
    ]
  }]
}
```

### When 4-Day is Better Than 24-Hour

- "This weekend" or "next 3 days" — covers more days
- "Will it be hot on Tuesday?" — quick daily check
- Multi-day planning (events, trips)

### When 24-Hour is Better Than 4-Day

- "Tomorrow afternoon at Mandai" — region + time-block specific
- "Will it rain in the north this evening?" — region-specific
- Any question requiring region/time granularity

## 2-Hour Forecast vs 24-Hour — Disambiguation

The 2-hour forecast has per-**area** granularity (e.g. "Mandai", "Tampines", "Bukit Timah"), while the 24-hour has per-**region** granularity (north/south/east/west/central). For a question like "Mandai":

- 2-hour forecast: direct match → use it
- 24-hour forecast: Mandai is in `north` region → use it but note the broader region

Both can be used together: 2-hour for "right now", 24-hour for "later today/tomorrow".

## Common Pitfalls When Working With Multi-Day Forecasts

1. **Don't over-promise precision**: 4-day forecasts are trend-level, not hour-by-hour. Use phrases like "showers expected in the afternoon" not "rain at 3:47pm".

2. **Wind direction conventions**: NEA uses compass directions (N/S/E/W and combinations like SSE, WSW). Don't convert to degrees.

3. **Date timezone**: All NEA dates are in SGT (UTC+8). If the user is in another timezone, convert explicitly.

4. **`update_timestamp` is when NEA published the data, not the forecast target date**. If the user asks about tomorrow and the update_timestamp is from yesterday, the data is still fresh (NEA updates multiple times per day).

5. **Don't mix forecast horizons silently**: if the user asks about "Saturday" and you're showing 24-hour data from today, call out which day's data you're showing.

6. **Region interpretation for boundary cases**: Some areas are obvious (Sentosa = south). Others (like Bukit Panjang) span two regions. Default to the larger/more central region. Disclose the choice.

7. **`general.forecast` lag**: It can lag behind the period-specific forecasts by an hour or so. Lead with the period data.

8. **For "this weekend" questions, sum across Sat + Sun in the 4-day outlook** — don't show each day in isolation if the user wants a weekend summary.

## Practical Examples

### "Will it rain at Mandai Zoo tomorrow from 8:30 AM to 4 PM?"

Use 24-hour forecast (has region + time blocks). Find periods overlapping 8:30-16:00 (covers Day and Afternoon blocks). Look up `regions.north` for each. Cross-check with 4-day outlook for the date.

### "Weekend weather in Sentosa"

Use 4-day outlook. Filter `forecasts` to Sat + Sun entries. Note island-wide (no Sentosa-specific granularity). Mention that Sentosa is coastal so sea breeze may moderate temperatures.

### "What about next Monday for an outdoor event?"

Use 4-day outlook. If Monday is the 4th day, it's at the edge of the forecast — note the confidence drops off.

### "Tomorrow morning cycling at East Coast"

Use 24-hour forecast. East Coast is in the `east` region. Find the Day block (06:00–12:00). Look up `regions.east`. Also useful: check 2-hour forecast for the very near-term and current v2 wind speed (cycling cares about wind).
