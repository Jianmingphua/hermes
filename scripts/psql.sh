#!/bin/bash
# Connect to Hermes Postgres via Docker
# Usage: ./psql.sh [database] [user]
DB="${1:-hermes_db}"
USER="${2:-hermes}"
docker exec -it hermes-postgres psql -U "$USER" -d "$DB"
