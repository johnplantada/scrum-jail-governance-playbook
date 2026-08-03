"""Contract tests for the unprivileged pm-gh broker client."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).with_name("pm-gh.sh")


class PmGhContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copy2(SOURCE, scripts / "pm-gh.sh")
        (scripts / "pm-gh.sh").chmod(0o755)
        self.trace = self.root / "trace"
        client = self.root / "sjgctl"
        client.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$TRACE\"\n", encoding="utf-8")
        client.chmod(0o755)
        self.env = {**os.environ, "SJGCTL_BIN": str(client), "SJG_WAKE_SOCKET": "/wake.sock",
                    "TRACE": str(self.trace), "PATH": "/usr/bin:/bin"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_pm(self, *args: str, env=None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.root / "scripts" / "pm-gh.sh"), *args],
                              cwd=self.root, env=env or self.env, text=True,
                              capture_output=True, check=False)

    def test_forwards_only_to_installed_control_client(self) -> None:
        result = self.run_pm("comment", "--id", "7", "--body", "hello")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("pm comment --id 7 --body hello", self.trace.read_text().strip())

    def test_requires_governed_wake(self) -> None:
        env = dict(self.env)
        env.pop("SJG_WAKE_SOCKET")
        result = self.run_pm("tasks", "--role", "software-implementer", env=env)
        self.assertEqual(77, result.returncode)
        self.assertIn("governed wake", result.stderr)

    def test_contains_no_direct_github_mutation(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("gh issue", source)
        self.assertNotIn("gh api", source)
        self.assertNotIn("gh project", source)


if __name__ == "__main__":
    unittest.main()
