#!/bin/bash
# Resumes the weather alert cron job at 8am SGT
# Triggered as a one-shot cron at 2026-06-09T00:00:00Z

JOB_ID="586e2c4a07b3"
LOG="/tmp/weather_rearm.log"

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') - Resuming weather alert job $JOB_ID" >> "$LOG"

# Resume the paused weather alert job (it already has the recurring 15-min schedule)
hermes cron resume "$JOB_ID" 2>> "$LOG"

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') - Resume command issued" >> "$LOG"
