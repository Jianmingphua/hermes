#!/bin/bash
# Script: postgres_cis_audit_and_alert.sh
# Description: Runs PostgreSQL CIS audit and alerts if compliance < 100%

# Directory to store reports
REPORT_DIR="/opt/hermes/scripts/postgres_cis_reports"
mkdir -p "$REPORT_DIR"

# Timestamp for report
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
REPORT_FILE="$REPORT_DIR/postgres_cis_audit_$TIMESTAMP.json"

# Get PostgreSQL password from .pgpass file
PG_PASSWORD=$(grep "^localhost:5432:hermes_db:hermes:" /opt/hermes/.pgpass | cut -d: -f5)
if [ -z "$PG_PASSWORD" ]; then
    echo "Error: Could not retrieve PostgreSQL password from .pgpass"
    exit 1
fi

# Run the audit script
cd /opt/hermes/.hermes/skills/devops/postgres-hardening/scripts
PG_PASSWORD=$PG_PASSWORD python3 audit_postgres.py --format json --output "$REPORT_FILE"

# Check if the audit succeeded
if [ $? -ne 0 ]; then
    echo "Error: PostgreSQL CIS audit failed"
    exit 1
fi

# Extract compliance percentage from the JSON report
COMPLIANCE=$(jq -r '.summary.compliance_pct' "$REPORT_FILE")

# Check if compliance is 100% using bc for floating point comparison
if [ "$(echo "$COMPLIANCE < 100" | bc -l)" -eq 1 ]; then
    echo "ALERT: PostgreSQL CIS compliance is $COMPLIANCE% (expected 100%). Report: $REPORT_FILE"
    exit 0
elif [ "$(echo "$COMPLIANCE == 100" | bc -l)" -eq 1 ]; then
    # Exactly 100, do nothing (no output)
    exit 0
else
    # Greater than 100? Should not happen, but treat as error
    echo "ERROR: PostgreSQL CIS compliance is $COMPLIANCE% (unexpected). Report: $REPORT_FILE"
    exit 1
fi