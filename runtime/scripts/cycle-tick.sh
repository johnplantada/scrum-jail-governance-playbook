#!/usr/bin/env bash
# Increment a per-agent wake counter and print "update" every N wakes, "tick" otherwise.
# Usage: scripts/cycle-tick.sh <agent-name> [N]
#   agent-name  name of the agent (matches agents/<name>.md)
#   N           review interval (default: 5, or global.review_interval from org-chart.yaml)
# Output: "update" when the Nth wake is reached (counter resets); "tick" otherwise.
# State: .cycles/<agent-name> (gitignored)
set -euo pipefail
cd "$(dirname "$0")/.."

agent="${1:?usage: cycle-tick.sh <agent-name> [N]}"

# Resolve N: explicit arg wins, then org-chart.yaml, then 5.
n="${2:-}"
if [ -z "$n" ] && [ -f org-chart.yaml ]; then
  n=$(grep -m1 'review_interval:' org-chart.yaml | awk '{print $2}' | tr -d '[:space:]' || true)
fi
n="${n:-5}"

mkdir -p .cycles
file=".cycles/$agent"
count=0
[ -f "$file" ] && count=$(cat "$file")

count=$((count + 1))
if [ "$count" -ge "$n" ]; then
  echo 0 > "$file"
  echo "update"
else
  echo "$count" > "$file"
  echo "tick"
fi
