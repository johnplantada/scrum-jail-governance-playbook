#!/usr/bin/env bash
# Human-facing local log follower. Logs are observations; controller SQLite state is truth.
set -euo pipefail
cd "$(dirname "$0")/.."
shopt -s nullglob
logs=(runner.log agent-*.log worker-*.log governance-monitor.log)
[ "${#logs[@]}" -gt 0 ] || { echo "no local logs yet"; exit 0; }
exec tail -n 80 -F "${logs[@]}"
