#!/usr/bin/env python3
"""Generated workspaces contain clients and hints, never the trusted control plane."""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ControlBoundaryTests(unittest.TestCase):
    def test_project_local_provider_policy_is_absent(self):
        self.assertFalse((ROOT / ".codex" / "config.toml").exists())
        self.assertFalse((ROOT / ".claude" / "settings.json").exists())

    def test_broker_clients_contain_no_authenticated_network_commands(self):
        pm = (ROOT / "scripts" / "pm-gh.sh").read_text(encoding="utf-8")
        push = (ROOT / "scripts" / "git-push-branch.sh").read_text(encoding="utf-8")
        for forbidden in ("gh issue", "gh api", "gh project"):
            self.assertNotIn(forbidden, pm)
        for forbidden in ("git push", "git fetch", "upload-pack"):
            self.assertNotIn(forbidden, push)

    def test_no_workspace_provider_launcher_exists(self):
        self.assertFalse((ROOT / "scripts" / "agent_cycle.py").exists())


if __name__ == "__main__":
    unittest.main()
