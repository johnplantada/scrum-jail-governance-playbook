#!/usr/bin/env python3
"""Fail closed when a generated workflow regains automatic or mutable execution."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [ROOT / ".github/workflows/ci.yml", *sorted((ROOT / "scripts/templates/product-repo").glob("*.yml"))]
PIN = re.compile(r"^\s*uses:\s*[^./\s]+/[^@\s]+@([0-9a-f]{40})(?:\s|$)", re.MULTILINE)


class WorkflowPolicy(unittest.TestCase):
    def test_all_workflows_are_manual_and_human_guarded(self):
        for path in WORKFLOWS:
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertIn("workflow_dispatch:", text)
                self.assertNotRegex(text, r"(?m)^\s+(?:pull_request|push|schedule):")
                self.assertIn("github.actor == vars.HUMAN_PRINCIPAL_LOGIN", text)
                self.assertIn("github.ref == 'refs/heads/main'", text)

    def test_external_actions_use_full_commit_pins(self):
        for path in WORKFLOWS:
            text = path.read_text()
            uses = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", text)
            with self.subTest(path=path.name):
                for value in uses:
                    self.assertRegex(value, r"^[^./\s]+/[^@\s]+@[0-9a-f]{40}$")

    def test_preview_credentials_are_not_available_to_build(self):
        text = (ROOT / "scripts/templates/product-repo/preview-deploy.yml").read_text()
        build, deploy = text.split("\n  deploy:\n", 1)
        self.assertNotIn("id-token: write", build)
        self.assertNotIn("configure-aws-credentials", build)
        self.assertIn("environment: preview", deploy)
        self.assertIn("id-token: write", deploy)

    def test_expressions_are_never_interpolated_inside_shell_programs(self):
        for path in WORKFLOWS:
            lines = path.read_text().splitlines()
            in_block = False
            block_indent = 0
            for line in lines:
                indent = len(line) - len(line.lstrip())
                if in_block and line.strip() and indent <= block_indent:
                    in_block = False
                if re.match(r"^\s*run:\s*", line):
                    with self.subTest(path=path.name, line=line):
                        self.assertNotIn("${{", line)
                    if re.match(r"^\s*run:\s*\|", line):
                        in_block = True
                        block_indent = indent
                        continue
                if in_block:
                    with self.subTest(path=path.name, line=line):
                        self.assertNotIn("${{", line)

    def test_provenance_precedes_setup_and_dependency_installation(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text()
        verification = text.index("python -I scripts/verify-provenance.py --release")
        self.assertLess(verification, text.index("actions/setup-python@"))
        self.assertLess(verification, text.index("pip install"))


if __name__ == "__main__":
    unittest.main()
