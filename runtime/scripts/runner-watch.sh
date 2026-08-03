#!/usr/bin/env bash
# Scheduler shim. Control code upgrades and policy activation are Human actions; this
# wrapper never self-fetches or executes workspace-local authority code.
set -euo pipefail
cd "$(dirname "$0")/.."
client="${SJG_ADMIN_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjg-admin}"
[ -x "$client" ] || { echo "runner: installed controller unavailable: $client" >&2; exit 69; }
exec "$client" runner tick
