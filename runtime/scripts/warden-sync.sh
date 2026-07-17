#!/usr/bin/env bash
# warden-sync.sh — launchd wrapper for the warden's deterministic sweep (pattern of
# runner-watch.sh: cd to the repo, load .env, prefer the venv python). Installed as
# a launchd/cron job (StartInterval ~4h). Token-free: this is scripts/warden.py, not
# an agent wake — the warden BRAIN only runs when the runner routes a dept:warden event.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

# Respect the org kill switch: a halted org gets no queue churn either.
[ -f .halt ] && { echo "warden-sync: .halt engaged — skipped"; exit 0; }

py="$(pwd)/.venv/bin/python"; [ -x "$py" ] || py=python3
echo "=== $(date '+%F %T') :: warden sync ==="
PYTHONPATH=scripts exec "$py" scripts/warden.py sync
