#!/usr/bin/env bash
# runner-watch.sh — wrapper for the runner tick: the org's ONE scheduled process
# (GITHUB-NATIVE-PLAN.md; it retired the old watcher fleet). Install it yourself as a
# launchd job or cron entry running every ~5 minutes.
#
# It also SELF-DEPLOYS: before each tick it fast-forwards this runtime checkout to
# origin/main, so a merged PR goes live on its own. This wrapper runs whatever is checked
# out here, so shipping used to need a manual `git pull` + `launchctl kickstart` — and a
# plain restart relaunched stale code (incident 2026-07-11). Set RUNNER_NO_PULL=1 to pin
# the checkout (local debugging).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

# Self-deploy — fail-soft and non-mutating: only when on main and not halted, only a
# fast-forward (never a merge commit, rebase, or touch to local/ignored state), and it
# NEVER aborts the tick. A pull that can't ff (offline, diverged, dirty tree) just logs and
# the tick runs the code already checked out. The kill switch wins: halted → no pull.
if [ -z "${RUNNER_NO_PULL:-}" ] && [ ! -f .halt ] \
   && [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)" = "main" ]; then
  before="$(git rev-parse --short HEAD 2>/dev/null || true)"
  if git fetch --quiet origin main 2>/dev/null \
     && git merge --ff-only --quiet FETCH_HEAD 2>/dev/null; then
    after="$(git rev-parse --short HEAD 2>/dev/null || true)"
    if [ "$before" != "$after" ]; then
      echo "runner: self-deploy — fast-forwarded $before → $after (origin/main)"
    fi
  else
    echo "runner: self-deploy skipped — no fast-forward (offline, diverged, or dirty tree); running $before" >&2
  fi
fi

py="$(pwd)/.venv/bin/python"; [ -x "$py" ] || py=python3
exec "$py" scripts/runner.py tick
