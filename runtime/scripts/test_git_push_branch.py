#!/usr/bin/env python3
"""Contract tests for the credentialless branch-submission client."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).with_name("git-push-branch.sh")


def run(*args: str, cwd: Path, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def must_run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


class GovernedPushContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "org"
        self.repo.mkdir()
        must_run("git", "init", "-q", "--initial-branch=main", cwd=self.repo)
        must_run("git", "config", "user.email", "test@example.com", cwd=self.repo)
        must_run("git", "config", "user.name", "Test", cwd=self.repo)
        must_run("git", "config", "commit.gpgsign", "false", cwd=self.repo)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        must_run("git", "add", "seed.txt", cwd=self.repo)
        must_run("git", "commit", "-qm", "seed", cwd=self.repo)
        (self.repo / "scripts").mkdir()
        shutil.copy2(SOURCE, self.repo / "scripts" / SOURCE.name)
        (self.repo / "scripts" / SOURCE.name).chmod(0o755)
        must_run("git", "add", "scripts/git-push-branch.sh", cwd=self.repo)
        must_run("git", "commit", "-qm", "add governed push client", cwd=self.repo)
        self.trace = self.root / "trace"
        client = self.root / "sjgctl"
        client.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$TRACE\"\n", encoding="utf-8")
        client.chmod(0o755)
        self.env = {**os.environ, "SJGCTL_BIN": str(client), "SJG_WAKE_SOCKET": "/wake.sock",
                    "AGENT_NAME": "software-implementer", "TRACE": str(self.trace)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_submits_current_scoped_branch_without_network_git(self) -> None:
        must_run("git", "switch", "-q", "-c", "agent/software-implementer/fix", cwd=self.repo)
        result = run(str(self.repo / "scripts" / SOURCE.name), cwd=self.repo, env=self.env)
        self.assertEqual(0, result.returncode, result.stderr)
        trace = self.trace.read_text(encoding="utf-8")
        self.assertIn("repo submit --repository org --topic fix", trace)
        self.assertNotIn(" push ", trace)

    def test_refuses_protected_branch(self) -> None:
        result = run(str(self.repo / "scripts" / SOURCE.name), cwd=self.repo, env=self.env)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("agent submissions require", result.stderr)

    def test_product_alias_does_not_depend_on_workspace_environment(self) -> None:
        must_run("git", "switch", "-q", "-c", "agent/software-implementer/product-fix",
                 cwd=self.repo)
        result = run(str(self.repo / "scripts" / SOURCE.name), "--product",
                     cwd=self.repo, env=self.env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("repo submit --repository product --topic product-fix",
                      self.trace.read_text(encoding="utf-8"))

    def test_script_contains_no_network_git(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("git push", source)
        self.assertNotIn("git fetch", source)
        self.assertNotIn("upload-pack", source)


if __name__ == "__main__":
    unittest.main()
