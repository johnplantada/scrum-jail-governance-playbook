#!/usr/bin/env bash
# rotate-logs.sh — bound the repo-root *.log files (agent-*.log, runner.log,
# offload.log, subagents.log, …), which otherwise grow forever.
#
# Copy-truncate, not move: launchd's KeepAlive services hold their StandardOutPath
# file descriptor open, so a `mv` would silently divert their output to the archived
# file until restart — `cp` + truncate-in-place keeps every open fd writing to the
# live file. Archives land in logs/archive/<name>.<stamp>.log; the newest KEEP
# archives per log are retained, older ones deleted.
#
# Wired into schedule.sh as a WATCHER (daily). Tunables via env:
#   LOG_ROTATE_MAX_BYTES  rotate when a log exceeds this (default 5 MB)
#   LOG_ROTATE_KEEP       archives to keep per log (default 3)
set -euo pipefail
cd "$(dirname "$0")/.."

max="${LOG_ROTATE_MAX_BYTES:-5242880}"
keep="${LOG_ROTATE_KEEP:-3}"
stamp="$(date -u +%Y%m%d-%H%M%S)"

for log in ./*.log; do
  [ -f "$log" ] || continue
  size=$(wc -c < "$log" | tr -d '[:space:]')
  [ "$size" -gt "$max" ] || continue
  name="$(basename "$log" .log)"
  mkdir -p logs/archive
  cp "$log" "logs/archive/${name}.${stamp}.log"
  : > "$log"
  echo "rotate-logs: archived ${name}.log (${size} bytes) → logs/archive/${name}.${stamp}.log"
  # Prune: keep the newest $keep archives for this log (lexicographic == chronological):
  # newest first, skip $keep, delete the rest. NOT `head -n -N` — GNU-only; macOS head dies
  # with `head: illegal line count`, which under `set -euo pipefail` killed the whole rotation
  # on the first oversized log (truncated it, then crashed before rotating the rest).
  ls -1 "logs/archive/${name}."*.log 2>/dev/null | sort -r | tail -n +$((keep + 1)) | while read -r old; do
    rm -f "$old"
    echo "rotate-logs: pruned $old"
  done
done
