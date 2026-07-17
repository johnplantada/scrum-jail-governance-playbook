#!/usr/bin/env bash
# The output predicate for the org's SAFe ceremony: has the org actually shipped real output
# to production? A "real ship" = a SUCCESSFUL run of the product repo's deploy workflow on
# `main`. Every output-gated ceremony — the [DEMO] gate, acceptance-criteria rigor, and PI
# Planning — checks this first (DESIGN.md §12), so process can never run ahead of delivery.
#
# Why `--branch main`: the only historical green deploy predates this org and ran on a
# throwaway branch with a different workflow; scoping to main excludes it, so until the
# current AWS pipeline goes green on main, `shipped=no` and ceremony stays dormant.
#
# Output (one key=value per line; eval- or grep-friendly):
#   shipped=<yes|no>        yes once the deploy workflow has a green run on main
#   last_ship_sha=<sha>     commit of the last successful prod deploy (empty if none)
#   last_ship_at=<iso8601>  when it deployed (empty if none)
#
# Read-only. Degrades safely (shipped=no) if gh is absent or offline.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

# No identity fallback; empty degrades to shipped=no like a missing gh.
repo="${PRODUCT_GH_REPO:-}"
shipped=no last_sha="" last_at=""

if command -v gh >/dev/null 2>&1; then
  line="$(gh run list --repo "$repo" --workflow=deploy.yml --status=success --branch main -L 1 \
            --json createdAt,headSha --jq '.[0] | "\(.createdAt) \(.headSha)"' 2>/dev/null || true)"
  if [ -n "${line// /}" ] && [ "$line" != "null null" ]; then
    last_at="${line%% *}"
    last_sha="${line##* }"
    shipped=yes
  fi
fi

echo "shipped=$shipped"
echo "last_ship_sha=${last_sha}"
echo "last_ship_at=${last_at}"
