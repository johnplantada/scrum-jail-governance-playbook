#!/usr/bin/env bash
# metrics-watch.sh — wrapper for the demand-telemetry sweep (scripts/metrics_watch.py
# sweep, docs/METRICS.md). Same thin-wrapper pattern as runner-watch.sh:
# source .env for REPORTS_API_URL, then exec the collector.
# Install this as the launchd/cron target (~every 30 min), not metrics_watch.py
# directly, so the source-then-exec discipline stays in one place.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

py="$(pwd)/.venv/bin/python"; [ -x "$py" ] || py=python3
exec "$py" scripts/metrics_watch.py sweep
