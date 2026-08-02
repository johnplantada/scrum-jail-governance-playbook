#!/usr/bin/env python3
"""End-to-end generator boundary tests from the golden checkout."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml


SOURCE = Path(__file__).with_name("orggen")
ROOT = SOURCE.parents[1]
loader = importlib.machinery.SourceFileLoader("orggen_under_test", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
orggen = importlib.util.module_from_spec(spec)
loader.exec_module(orggen)


class RoleInputContract(unittest.TestCase):
    def test_rejects_noncanonical_duplicate_missing_and_custom_roles(self):
        invalid = (
            "../../escaped",
            "project-orchestrator,commercial-planner,software-implementer,governance-reviewer,governance-reviewer",
            "project-orchestrator,commercial-planner,governance-reviewer",
            "project-orchestrator,commercial-planner,software-implementer,governance-reviewer,customer-researcher",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(SystemExit):
                orggen.roles(value)

    def test_optional_policy_reviewer_is_the_only_extension(self):
        spec = ",".join(orggen.DEFAULT_ROLES + ["policy-reviewer"])
        self.assertEqual({*orggen.DEFAULT_ROLES, "policy-reviewer"},
                         {row[0] for row in orggen.roles(spec)})

    def test_containment_check_is_independent_of_parser_validation(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            orggen.role_mandate_path(str(Path(tmp) / "org"), "../../escaped")

    def test_source_digest_rejects_symlinked_manifest_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / "external"
            external.mkdir()
            (external / "value.txt").write_text("one\n")
            repository = root / "golden"
            repository.mkdir()
            (repository / "linked").symlink_to(external, target_is_directory=True)
            (repository / "stamp-manifest.yaml").write_text(
                "source_roots:\n  - linked/value.txt\n")
            prior = orggen.REPO
            orggen.REPO = str(repository)
            try:
                with self.assertRaises(SystemExit):
                    orggen.source_tree_digest()
            finally:
                orggen.REPO = prior


class StampContract(unittest.TestCase):
    def stamp(self, parent: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        target = parent / "generated-org"
        return subprocess.run([
            str(SOURCE), "init", str(target), "--org", "generated-org",
            "--product", "Product", "--goal", "Verified",
            "--principal-github", "principal", "--product-repo", "principal/product",
            "--allow-dirty", *extra,
        ], cwd=ROOT, text=True, capture_output=True)

    def test_fresh_stamp_is_structured_provenanced_and_credentialless(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            result = self.stamp(parent)
            self.assertEqual(0, result.returncode, result.stderr)
            target = parent / "generated-org"
            organization = json.loads((target / "organization.yaml").read_text())
            self.assertEqual(set(orggen.DEFAULT_ROLES),
                             {agent["id"] for agent in organization["agents"]})
            self.assertEqual("1.0.0", organization["controlRelease"]["version"])
            self.assertRegex(organization["controlRelease"]["sourceTreeSha256"], r"^[0-9a-f]{64}$")
            self.assertEqual({"hostedExecution": "manual-only"}, organization["workflows"])
            provenance = json.loads((target / "playbook/SOURCE.json").read_text())
            self.assertEqual(provenance["sourceState"] == "clean",
                             provenance["releaseQualified"])
            self.assertNotEqual("", provenance["payloadTreeSha256"])
            self.assertTrue((target / "playbook/docs/CONTROL-PLANE.md").is_file())
            self.assertFalse((target / ".codex/config.toml").exists())
            self.assertFalse((target / ".claude/settings.json").exists())
            self.assertTrue((target / "scripts/worker-run").stat().st_mode & 0o111)
            objective = yaml.safe_load(
                (target / ".github/ISSUE_TEMPLATE/objective.yml").read_text())
            accountable = next(value for value in objective["body"]
                               if value.get("id") == "role")
            self.assertEqual(["project-orchestrator"], accountable["attributes"]["options"])
            verify = subprocess.run(["python3", "scripts/verify-provenance.py"], cwd=target,
                                    text=True, capture_output=True)
            self.assertEqual(0, verify.returncode, verify.stderr)

    def test_rejects_repository_path_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.stamp(Path(tmp), "--product-repo", "../..")
            self.assertNotEqual(0, result.returncode)
            self.assertFalse((Path(tmp) / "generated-org").exists())

    def test_stamp_modes_are_independent_of_caller_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            prior = os.umask(0)
            try:
                result = self.stamp(Path(tmp))
            finally:
                os.umask(prior)
            self.assertEqual(0, result.returncode, result.stderr)
            target = Path(tmp) / "generated-org"
            self.assertEqual(0o644, (target / "organization.yaml").stat().st_mode & 0o777)
            self.assertEqual(0o644, (target / "agents/software-implementer.md").stat().st_mode & 0o777)
            self.assertEqual(0o755, (target / "scripts/worker-run").stat().st_mode & 0o777)

    def test_provenance_rejects_unlisted_security_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.stamp(Path(tmp))
            self.assertEqual(0, result.returncode, result.stderr)
            target = Path(tmp) / "generated-org"
            (target / "scripts/json.py").write_text("raise RuntimeError('shadow')\n")
            verify = subprocess.run(
                ["python3", "-I", "scripts/verify-provenance.py"], cwd=target,
                text=True, capture_output=True)
            self.assertNotEqual(0, verify.returncode)
            self.assertIn("unlisted security-bearing", verify.stderr)

    def test_provenance_rejects_security_namespace_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.stamp(Path(tmp))
            self.assertEqual(0, result.returncode, result.stderr)
            target = Path(tmp) / "generated-org"
            (target / "outside.py").write_text("raise RuntimeError('must not run')\n")
            (target / "scripts/test_alias.py").symlink_to("../outside.py")
            verify = subprocess.run(
                ["python3", "-I", "scripts/verify-provenance.py"], cwd=target,
                text=True, capture_output=True)
            self.assertNotEqual(0, verify.returncode)
            self.assertIn("unsafe symlink", verify.stderr)

    def test_validation_failure_is_atomic_and_leaves_no_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            result = self.stamp(parent, "--principal-github", "bad/login")
            self.assertNotEqual(0, result.returncode)
            self.assertFalse((parent / "generated-org").exists())
            self.assertEqual([], list(parent.glob(".generated-org.staging-*")))

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "generated-org"
            target.mkdir()
            sentinel = target / "sentinel"
            sentinel.write_text("keep")
            result = self.stamp(parent)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("keep", sentinel.read_text())

    def test_installed_release_must_match_organization_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            stamped = self.stamp(parent)
            self.assertEqual(0, stamped.returncode, stamped.stderr)
            prefix = parent / "control"
            installed = subprocess.run([
                "python3", str(ROOT / "bin/sjg-install"), "--prefix", str(prefix),
                "--allow-dirty"], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(0, installed.returncode, installed.stderr)
            activated = subprocess.run([
                "python3", str(ROOT / "bin/sjg-activate"), "--prefix", str(prefix),
                "--version", "1.0.0", "--organization",
                str(parent / "generated-org/organization.yaml"), "--allow-dirty"],
                cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(0, activated.returncode, activated.stderr)
            self.assertEqual("releases/1.0.0", (prefix / "current").readlink().as_posix())
            self.assertTrue((prefix / "current/bin/sjgctl").exists())
            self.assertEqual(0o555, (prefix / "current/bin/sjgctl").stat().st_mode & 0o777)

    def test_activation_rejects_an_unlisted_release_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            stamped = self.stamp(parent)
            self.assertEqual(0, stamped.returncode, stamped.stderr)
            prefix = parent / "control"
            installed = subprocess.run([
                "python3", str(ROOT / "bin/sjg-install"), "--prefix", str(prefix),
                "--allow-dirty"], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(0, installed.returncode, installed.stderr)
            release = prefix / "releases/1.0.0"
            release.chmod(0o755)
            extra = release / "unlisted.py"
            extra.write_text("raise RuntimeError('unexpected')\n", encoding="utf-8")
            extra.chmod(0o444)
            release.chmod(0o555)
            activated = subprocess.run([
                "python3", str(ROOT / "bin/sjg-activate"), "--prefix", str(prefix),
                "--version", "1.0.0", "--organization",
                str(parent / "generated-org/organization.yaml"), "--allow-dirty"],
                cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(0, activated.returncode)
            self.assertIn("unlisted or missing", activated.stderr)

    def test_activation_rejects_a_noncompiling_organization(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            stamped = self.stamp(parent)
            self.assertEqual(0, stamped.returncode, stamped.stderr)
            prefix = parent / "control"
            installed = subprocess.run([
                "python3", str(ROOT / "bin/sjg-install"), "--prefix", str(prefix),
                "--allow-dirty"], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(0, installed.returncode, installed.stderr)
            release_manifest = json.loads(
                (prefix / "releases/1.0.0/MANIFEST.json").read_text())
            invalid = parent / "invalid-organization.json"
            invalid.write_text(json.dumps({"controlRelease": {
                "version": release_manifest["version"],
                "sourceTreeSha256": release_manifest["sourceTreeSha256"]}}))
            activated = subprocess.run([
                "python3", str(ROOT / "bin/sjg-activate"), "--prefix", str(prefix),
                "--version", "1.0.0", "--organization", str(invalid), "--allow-dirty"],
                cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(0, activated.returncode)
            self.assertIn("does not compile", activated.stderr)
            self.assertFalse((prefix / "current").exists())

    def test_install_and_activation_reject_version_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "control"
            installed = subprocess.run([
                "python3", str(ROOT / "bin/sjg-install"), "--prefix", str(prefix),
                "--version", "../escape", "--allow-dirty"], cwd=ROOT,
                text=True, capture_output=True)
            self.assertNotEqual(0, installed.returncode)
            activated = subprocess.run([
                "python3", str(ROOT / "bin/sjg-activate"), "--prefix", str(prefix),
                "--version", "../escape", "--organization", "missing"], cwd=ROOT,
                text=True, capture_output=True)
            self.assertNotEqual(0, activated.returncode)


if __name__ == "__main__":
    unittest.main()
