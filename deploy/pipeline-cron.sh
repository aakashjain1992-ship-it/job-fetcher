#!/bin/bash
# Weekly pipeline cron — runs every Saturday 6am UTC
# Installed by setup.sh. Logs to /var/log/job-fetcher-cron.log
set -e
APP_DIR="/opt/job-fetcher"
cd "$APP_DIR"
source .env
echo "=== Pipeline run: $(date -u) ==="
"$APP_DIR/venv/bin/python" main.py 2>&1
echo "=== Done: $(date -u) ==="
