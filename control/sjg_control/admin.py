"""Operator client for the supervisor-owned scheduler and provider service."""
from __future__ import annotations

from typing import Any
import argparse
import json
import os
import sys
import uuid

from .protocol import ProtocolError, request


def send(operation: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    socket_path = os.environ.get("SJG_SUPERVISOR_SOCKET", "")
    if not socket_path:
        raise ProtocolError("SJG_SUPERVISOR_SOCKET is not set")
    return request(socket_path, {
        "schema": "sjg.admin/v1",
        "request_id": str(uuid.uuid4()),
        "operation": operation,
        "arguments": arguments or {},
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sjg-admin")
    sub = parser.add_subparsers(dest="area", required=True)
    provider = sub.add_parser("provider")
    provider.add_argument("command", choices=("check-auth",))
    runner = sub.add_parser("runner")
    runner.add_argument("command", choices=("tick", "preview", "pause", "resume"))
    monitor = sub.add_parser("monitor")
    monitor.add_argument("command", choices=("audit", "sync"))
    monitor.add_argument("--dry-run", action="store_true")
    wake = sub.add_parser("wake")
    wake.add_argument("command", choices=("run",))
    wake.add_argument("--role", required=True)
    wake.add_argument("--reason", default="manual")
    wake.add_argument("--note", default="")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    ns = parser.parse_args(argv)
    if ns.area == "provider":
        operation, arguments = "provider.check_auth", {}
    elif ns.area == "runner":
        operation, arguments = f"runner.{ns.command}", {}
    elif ns.area == "monitor":
        operation = f"monitor.{ns.command}"
        arguments = {"dry_run": True if ns.command == "audit" else ns.dry_run}
    elif ns.area == "wake":
        operation, arguments = "wake.run", {"role": ns.role, "reason": ns.reason, "note": ns.note}
    else:
        operation, arguments = "status", {}
    response = send(operation, arguments)
    if response.get("status") != "ok":
        print(f"sjg-admin: {response.get('status')}: {response.get('error', 'request failed')}",
              file=sys.stderr)
        return 1
    if ns.area == "status" and not ns.json:
        for key, value in sorted((response.get("result") or {}).items()):
            print(f"{key}: {value}")
    else:
        print(json.dumps(response.get("result"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProtocolError) as exc:
        raise SystemExit(f"sjg-admin: {exc}")
