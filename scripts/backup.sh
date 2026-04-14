#!/usr/bin/env bash
set -euo pipefail

TS=$(date +"%Y%m%d_%H%M%S")
mkdir -p /backups
pg_dump -h db -U ordersheet -d ordersheet > "/backups/ordersheet_${TS}.sql"
find /backups -type f -name '*.sql' -mtime +14 -delete
