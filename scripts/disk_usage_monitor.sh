#!/usr/bin/env bash
set -euo pipefail
LOGDIR="/opt/hermes/logs"
LOG="$LOGDIR/disk_usage.log"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
mkdir -p "$LOGDIR"

# Header for this run
printf "[%s] Disk usage snapshot (top 5 directories under /):\n" "$TIMESTAMP" | tee -a "$LOG"

# Top-level directory sizes under root
du -h --max-depth=1 -x / 2>/dev/null | sort -hr | head -n 5 | tee -a "$LOG"

# Root filesystem summary
df -h / | tail -n 1 | awk '{print "Root FS: "$2" total, "$3" used, "$4" avail, "$5" use%"}' | tee -a "$LOG"

# End with a newline for readability
echo "" | tee -a "$LOG"