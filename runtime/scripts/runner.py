#!/usr/bin/env python3
"""Operator shim for the installed durable runner.

The former workspace-local poller, cursors, spool, budgets, and provider launch path
were removed. They now live in the sealed controller's transactional SQLite service.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "tick"
    if mode not in {"tick", "preview"}:
        print("usage: runner.py <tick|preview>", file=sys.stderr)
        return 2
    client = os.environ.get("SJG_ADMIN_BIN", "/usr/local/libexec/scrum-jail/current/bin/sjg-admin")
    try:
        completed = subprocess.run([client, "runner", mode], check=False)
    except OSError as exc:
        print(f"runner: installed controller unavailable: {exc}", file=sys.stderr)
        return 69
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
