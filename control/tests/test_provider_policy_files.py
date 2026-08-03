from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from sjg_control.provider import (CODEX_PARENT_PROFILE, codex_profile_override,
                                  load_claude_settings, materialize_claude_policy,
                                  materialize_codex_policy,
                                  validate_codex_config)


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
        self.assertEqual("never", config["approval_policy"])
        self.assertEqual("scrum-jail-parent", config["default_permissions"])
        parent = config["permissions"]["scrum-jail-parent"]
        self.assertNotIn("extends", parent)
        self.assertEqual("read", parent["filesystem"][":minimal"])
        self.assertEqual({".": "write"}, parent["filesystem"][":workspace_roots"])
        self.assertEqual("deny", parent["filesystem"][
            "/__SJG_MANAGED_HOME__/.codex"])
        self.assertEqual("read", parent["filesystem"]["/__SJG_CONTROL_ROOT__"])
        self.assertFalse(parent["network"]["enabled"])
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

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI is not installed")
    def test_codex_profile_executes_but_denies_auth_sentinel_read(self):
        source = ROOT / "provider-policy" / "codex"
        executable = shutil.which("codex")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            policy = materialize_codex_policy(source, home)
            self.assertEqual(0o711, (policy / "rules").stat().st_mode & 0o777)
            self.assertEqual(0o444, (policy / "rules/default.rules").stat().st_mode & 0o777)
            sentinel = policy / "sjg-auth-sentinel"
            sentinel.write_text("sentinel-must-not-escape\n", encoding="ascii")
            sentinel.chmod(0o400)
            workspace = root / "workspace"
            workspace.mkdir()
            environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C",
                           "LC_ALL": "C"}
            socket_override = codex_profile_override(
                CODEX_PARENT_PROFILE, "network.unix_sockets", root / "wake.sock", "allow")
            prefix = [str(executable), "sandbox", "-c", socket_override,
                      "--permission-profile",
                      CODEX_PARENT_PROFILE, "--cd", str(workspace), "--"]
            healthy = subprocess.run(
                [*prefix, "/bin/echo", "SJG_SANDBOX_OK"], text=True,
                capture_output=True, timeout=30, env=environment,
            )
            if healthy.returncode and "Operation not permitted" in healthy.stderr:
                self.skipTest("nested Codex sandbox is unavailable in the test sandbox")
            self.assertEqual(0, healthy.returncode, healthy.stderr)
            self.assertEqual("SJG_SANDBOX_OK", healthy.stdout.strip())
            denied = subprocess.run(
                [*prefix, "/bin/cat", str(sentinel)], text=True,
                capture_output=True, timeout=30, env=environment,
            )
            self.assertNotEqual(0, denied.returncode)
            self.assertNotIn("sentinel-must-not-escape", denied.stdout + denied.stderr)

    def test_materialized_claude_policy_is_traversable_but_not_writable(self):
        source = ROOT / "provider-policy" / "claude"
        with tempfile.TemporaryDirectory() as temporary:
            target = materialize_claude_policy(source, Path(temporary) / "policy")
            self.assertEqual(0o711, target.stat().st_mode & 0o777)
            for path in target.glob("*.json"):
                self.assertEqual(0o444, path.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
