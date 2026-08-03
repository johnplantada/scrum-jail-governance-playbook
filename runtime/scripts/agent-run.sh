#!/usr/bin/env bash
# Compatibility-free supervisor client. Provider launch, role identity, budgets, locks,
# prompts, and subscription authentication are owned by the installed control plane.
set -euo pipefail
[ "$#" -eq 1 ] || { echo "usage: agent-run.sh <canonical-role>" >&2; exit 2; }
client="${SJG_ADMIN_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjg-admin}"
[ -x "$client" ] || {
  echo "agent-run: installed supervisor client not found: $client" >&2
  exit 69
}
[ -n "${SJG_SUPERVISOR_SOCKET:-}" ] || {
  echo "agent-run: SJG_SUPERVISOR_SOCKET is not set; direct provider launch is disabled" >&2
  exit 77
}
exec "$client" wake run --role "$1" --reason "${WAKE_REASON:-manual}" --note "${WAKE_NOTE:-}"
