#!/usr/bin/env bash
# The evidence predicate for the [DEMO] gate: is a PR's demo evidence MACHINE-produced
# and bound to the code actually being shipped? A demo's evidence is real only if the
# product repo's demo-evidence workflow has a GREEN run on the PR's CURRENT head SHA —
# the same pattern as last-ship.sh (predicate in code, not prose). IT runs this before
# posting a [DEMO]; the CEO runs it before relaying a deploy approval (DESIGN §12); the run URL goes
# in the [DEMO] post so Business and the Chairman can open the artifacts.
#
# A stale run (evidence generated, then more commits pushed) does NOT verify — evidence
# must match the head SHA, or IT re-runs the workflow.
#
# Usage: scripts/demo-verify.sh <prod-pr-number> [workflow-file]
#   workflow-file  defaults to demo-evidence.yml (the template in
#                  scripts/templates/product-repo/ — copy it into the product repo)
#
# Output (one key=value per line; eval- or grep-friendly):
#   verified=<yes|no>      yes only when a green evidence run exists on the head SHA
#   head_sha=<sha>         the PR's current head commit
#   run_id=<id>            the matching workflow run (empty if none)
#   run_url=<url>          cite THIS in the [DEMO] post (empty if none)
#   reason=<...>           why verification failed (empty when verified=yes)
#
# Read-only. Degrades safely (verified=no) if gh is absent or offline.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

pr="${1:?usage: demo-verify.sh <prod-pr-number> [workflow-file]}"
wf="${2:-demo-evidence.yml}"
# No identity fallback; empty degrades to verified=no with a reason, like a missing gh.
repo="${PRODUCT_GH_REPO:-}"

verified=no head_sha="" run_id="" run_url="" reason=""

if ! command -v gh >/dev/null 2>&1; then
  reason="gh not installed"
else
  head_sha="$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid 2>/dev/null || true)"
  if [ -z "$head_sha" ]; then
    reason="prod-PR-#$pr not found in $repo"
  else
    line="$(gh run list --repo "$repo" --workflow="$wf" --commit "$head_sha" -L 1 \
              --json databaseId,status,conclusion,url \
              --jq '.[0] | "\(.databaseId) \(.status) \(.conclusion) \(.url)"' 2>/dev/null || true)"
    if [ -z "${line// /}" ] || [ "$line" = "null null null null" ]; then
      reason="no $wf run on head $head_sha — run the evidence workflow (or push triggered none)"
    else
      run_id="$(echo "$line" | awk '{print $1}')"
      status="$(echo "$line" | awk '{print $2}')"
      conclusion="$(echo "$line" | awk '{print $3}')"
      run_url="$(echo "$line" | awk '{print $4}')"
      if [ "$status" != "completed" ]; then
        reason="evidence run $run_id still $status — wait for it"
      elif [ "$conclusion" != "success" ]; then
        reason="evidence run $run_id concluded $conclusion — fix and re-run before demoing"
      else
        verified=yes
      fi
    fi
  fi
fi

echo "verified=$verified"
echo "head_sha=$head_sha"
echo "run_id=$run_id"
echo "run_url=$run_url"
echo "reason=$reason"
[ "$verified" = yes ]
