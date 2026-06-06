#!/usr/bin/env bash
# Singapore NEA multi-day forecast fetcher
# Usage:
#   ./forecast.sh 24h          # 24-hour forecast (region+time-block granularity)
#   ./forecast.sh 4day         # 4-day outlook (island-wide)
#   ./forecast.sh both         # both
#
# Output: raw JSON, pretty-printed. Pipe to jq for filtering.
# Region keys: north, south, east, west, central

set -euo pipefail

case "${1:-both}" in
  24h)
    curl -s "https://api.data.gov.sg/v1/environment/24-hour-weather-forecast" | python3 -m json.tool
    ;;
  4day)
    curl -s "https://api.data.gov.sg/v1/environment/4-day-weather-forecast" | python3 -m json.tool
    ;;
  2h)
    curl -s "https://api.data.gov.sg/v1/environment/2-hour-weather-forecast" | python3 -m json.tool
    ;;
  both)
    echo "=== 24-HOUR FORECAST ==="
    curl -s "https://api.data.gov.sg/v1/environment/24-hour-weather-forecast" | python3 -m json.tool
    echo ""
    echo "=== 4-DAY OUTLOOK ==="
    curl -s "https://api.data.gov.sg/v1/environment/4-day-weather-forecast" | python3 -m json.tool
    ;;
  *)
    echo "Usage: $0 [24h|4day|2h|both]" >&2
    exit 1
    ;;
esac
