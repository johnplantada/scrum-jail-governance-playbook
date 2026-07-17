#!/usr/bin/env bash
# cost-sync.sh — export the spreadsheet-ready cost CSV (costs.csv) from the live spend ledger
# (state/spend.jsonl, which agents append to as they run). Cheap and local: no network, no model
# call — just a re-export so a spreadsheet/dashboard pointed at costs.csv stays current. schedule.sh
# runs it hourly; run it by hand anytime. Safe while the org is halted.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/costs.py --csv costs.csv >/dev/null
