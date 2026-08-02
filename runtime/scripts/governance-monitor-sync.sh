#!/usr/bin/env bash
# Scheduler shim for the installed, tri-state reconciler.
set -euo pipefail
cd "$(dirname "$0")/.."
client="${SJG_ADMIN_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjg-admin}"
[ -x "$client" ] || { echo "governance-monitor: installed controller unavailable: $client" >&2; exit 69; }
exec "$client" monitor sync
