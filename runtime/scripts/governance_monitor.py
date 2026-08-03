#!/usr/bin/env python3
"""Operator shim for the installed tri-state governance reconciler."""
from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "audit"
    if mode not in {"audit", "sync"}:
        print("usage: governance_monitor.py <audit|sync> [--dry-run]", file=sys.stderr)
        return 2
    command = [os.environ.get("SJG_ADMIN_BIN", "/usr/local/libexec/scrum-jail/current/bin/sjg-admin"),
               "monitor", mode]
    if "--dry-run" in argv[1:]:
        command.append("--dry-run")
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"governance-monitor: installed controller unavailable: {exc}", file=sys.stderr)
        return 69


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
