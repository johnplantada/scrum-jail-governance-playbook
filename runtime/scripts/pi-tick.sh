#!/usr/bin/env bash
# Derive the current PI (Program Increment) number and iteration number from the org's
# review cadence. One completed iteration == one CLOSED [REVIEW] issue on the org repo
# (the CEO opens a [REVIEW] issue each interval and closes it with the conclusion).
# A PI bundles ITERS_PER_PI iterations, so PI Planning is due whenever a new PI boundary
# is crossed.
#
# Usage: scripts/pi-tick.sh [iters_per_pi]
#   iters_per_pi  iterations per PI (default: global.pi_interval from org-chart.yaml, or 3)
#
# Output (one key=value per line, easy to eval or grep):
#   iterations_done=<N>          completed review cycles so far
#   pi=<N>                       current PI number (1-based)
#   iteration=<N>                iteration within the current PI (1-based)
#   pi_planning_due=<yes|no>     yes when the *next* iteration starts a fresh PI (by cadence)
#   pi_planning_eligible=<yes|no> yes only when due AND the org actually shipped to prod
#                                (scripts/last-ship.sh). You cannot plan a Program Increment
#                                for a program that has incremented nothing.
#
# Read-only: counts closed [REVIEW] issues via `gh`; never posts. Exposes the counter for
# the CEO to act on — it does NOT auto-trigger PI Planning (that stays a human/CEO decision).
# Degrades safely (iterations_done=0) if gh is absent or offline.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

# No identity fallback; empty degrades to iterations_done=0 like a missing gh.
repo="${ORG_GH_REPO:-}"

# Resolve iters_per_pi: explicit arg wins, then org-chart.yaml, then 3.
iters_per_pi="${1:-}"
if [ -z "$iters_per_pi" ] && [ -f org-chart.yaml ]; then
  iters_per_pi=$(grep -m1 'pi_interval:' org-chart.yaml | awk '{print $2}' | tr -d '[:space:]' || true)
fi
iters_per_pi="${iters_per_pi:-3}"

# Count completed iterations = closed issues titled [REVIEW] on the org repo.
iterations_done=0
if command -v gh >/dev/null 2>&1; then
  iterations_done=$(gh issue list --repo "$repo" --state closed --search '"[REVIEW]" in:title' \
    --limit 500 --json title --jq '[.[] | select(.title | startswith("[REVIEW]"))] | length' \
    2>/dev/null || echo 0)
fi
iterations_done="${iterations_done:-0}"

pi=$(( iterations_done / iters_per_pi + 1 ))
iteration=$(( iterations_done % iters_per_pi + 1 ))

# PI Planning is due when the next iteration would open a new PI, i.e. we've just closed
# the last iteration of the current PI.
if [ "$iteration" -eq 1 ] && [ "$iterations_done" -gt 0 ]; then
  pi_planning_due=yes
else
  pi_planning_due=no
fi

# Output-gate the ceremony: PI Planning is only ELIGIBLE if the org actually shipped real
# output to prod (scripts/last-ship.sh). Otherwise the CEO closes the review with a one-line
# suppression conclusion naming the blocker instead of convening planning over an empty
# increment.
shipped=no
if [ -x scripts/last-ship.sh ]; then
  eval "$(scripts/last-ship.sh 2>/dev/null | grep '^shipped=' || echo 'shipped=no')"
fi
if [ "$pi_planning_due" = yes ] && [ "$shipped" = yes ]; then
  pi_planning_eligible=yes
else
  pi_planning_eligible=no
fi

echo "iterations_done=$iterations_done"
echo "pi=$pi"
echo "iteration=$iteration"
echo "pi_planning_due=$pi_planning_due"
echo "pi_planning_eligible=$pi_planning_eligible"
