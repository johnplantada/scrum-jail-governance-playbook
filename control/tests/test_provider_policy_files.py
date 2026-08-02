from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

from sjg_control.provider import load_claude_settings, validate_codex_config


ROOT = Path(__file__).resolve().parents[1]


class ProviderPolicyFileTests(unittest.TestCase):
    def test_every_claude_settings_file_is_valid_for_its_scope(self):
        directory = ROOT / "provider-policy" / "claude"
        for path in sorted(directory.glob("*.json")):
            with self.subTest(path=path.name):
                profile = path.stem.replace("-settings", "")
                settings = load_claude_settings(path, profile=profile)
                permissions = settings["permissions"]
                self.assertNotIn("WebSearch", permissions["allow"])
                self.assertNotIn("WebFetch", permissions["allow"])
                self.assertIn("WebSearch", permissions["deny"])
                self.assertIn("WebFetch", permissions["deny"])
                filesystem = settings["sandbox"]["filesystem"]
                self.assertIn("~/.claude", filesystem["denyRead"])
                self.assertIn("~/.config/claude", filesystem["denyRead"])

    def test_codex_config_is_parseable_and_network_closed(self):
        path = ROOT / "provider-policy" / "codex" / "config.toml"
        validate_codex_config(path)
        with path.open("rb") as handle:
            config = tomllib.load(handle)
        self.assertEqual("workspace-write", config["sandbox_mode"])
        self.assertEqual("never", config["approval_policy"])
        self.assertFalse(config["sandbox_workspace_write"]["network_access"])
        self.assertEqual([], config["sandbox_workspace_write"]["writable_roots"])
        self.assertEqual("none", config["shell_environment_policy"]["inherit"])

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
    def test_codex_execpolicy_forbids_direct_brokers_and_allows_local_git(self):
        rules = ROOT / "provider-policy" / "codex" / "rules" / "default.rules"
        cases = {
            ("gh", "api", "repos/o/r"): "forbidden",
            ("git", "push", "origin", "agent/software-implementer/x"): "forbidden",
            ("curl", "https://api.github.com"): "forbidden",
            ("claude", "-p", "nested"): "forbidden",
            ("git", "status"): "allow",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                process = subprocess.run(
                    ["codex", "execpolicy", "check", "--rules", str(rules), "--pretty", "--", *command],
                    text=True, capture_output=True, timeout=20,
                )
                self.assertEqual(0, process.returncode, process.stderr)
                result = json.loads(process.stdout)
                decision = result.get("decision") or "allow"
                self.assertEqual(expected, decision)


if __name__ == "__main__":
    unittest.main()
