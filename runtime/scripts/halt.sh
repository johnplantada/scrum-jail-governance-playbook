#!/usr/bin/env bash
# Operator client for the supervisor-owned kill switch.
set -euo pipefail
client="${SJG_ADMIN_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjg-admin}"
[ -x "$client" ] || { echo "halt: installed controller unavailable: $client" >&2; exit 69; }
[ -n "${SJG_SUPERVISOR_SOCKET:-}" ] || {
  echo "halt: SJG_SUPERVISOR_SOCKET is not set" >&2
  exit 77
}
exec "$client" runner pause
