from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from sjg_control.client import pm_arguments, repository_manifest, request_id
from sjg_control.protocol import ProtocolError, encode_message, receive_message


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        left, right = socket.socketpair()
        try:
            left.sendall(encode_message({"operation": "read.issue.get"}))
            self.assertEqual("read.issue.get", receive_message(right)["operation"])
        finally:
            left.close()
            right.close()

    def test_pm_parser_does_not_accept_repository_or_role_identity_override(self):
        operation, arguments = pm_arguments(["comment", "--id", "7", "--body", "hello"])
        self.assertEqual("pm.issue.comment", operation)
        self.assertEqual("org", arguments["repository"])
        with self.assertRaises(SystemExit):
            pm_arguments(["comment", "--id", "7", "--body", "hello", "--repository", "outside/repo"])

    def test_pr_parser_exposes_alias_not_repository_url_or_merge(self):
        operation, arguments = pm_arguments(
            ["pr-create", "--repo", "product", "--topic", "fix", "--title", "Fix"])
        self.assertEqual("pr.create", operation)
        self.assertEqual("product", arguments["repository"])
        with self.assertRaises(SystemExit):
            pm_arguments(["pr-merge", "--repo", "product", "--id", "7"])

    def test_route_maps_target_without_actor_identity_override(self):
        operation, arguments = pm_arguments(["route", "--id", "7", "--role", "commercial-planner"])
        self.assertEqual("pm.issue.route", operation)
        self.assertEqual("commercial-planner", arguments["target_role"])
        self.assertNotIn("role", arguments)

    def test_create_maps_primary_owner_without_actor_identity_override(self):
        operation, arguments = pm_arguments([
            "create", "--type", "epic", "--title", "Deliver", "--parent", "1",
            "--role", "software-implementer"])
        self.assertEqual("pm.issue.create", operation)
        self.assertEqual("software-implementer", arguments["target_role"])
        self.assertNotIn("role", arguments)

    def test_comments_use_the_paginated_comment_operation(self):
        operation, arguments = pm_arguments(["comments", "--id", "7"])
        self.assertEqual("read.issue.comments", operation)
        self.assertEqual(7, arguments["id"])

    def test_request_key_is_stable_within_a_wake(self):
        previous = os.environ.get("SJG_WAKE_ID")
        os.environ["SJG_WAKE_ID"] = "wake-42"
        try:
            one = request_id("read.issue.get", {"repository": "org", "issue": 7})
            two = request_id("read.issue.get", {"issue": 7, "repository": "org"})
            other = request_id("read.issue.get", {"repository": "org", "issue": 8})
        finally:
            if previous is None:
                os.environ.pop("SJG_WAKE_ID", None)
            else:
                os.environ["SJG_WAKE_ID"] = previous
        self.assertEqual(one, two)
        self.assertNotEqual(one, other)

    def test_manifest_has_no_remote_or_push_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            (root / "a.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c",
                            "user.email=t@example.com", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "one"], check=True)
            (root / "a.txt").write_text("two\n", encoding="utf-8")
            base, changes = repository_manifest(str(root), "HEAD")
            self.assertEqual(40, len(base))
            self.assertEqual("a.txt", changes[0]["path"])
            self.assertNotIn("remote", changes[0])

    def test_manifest_refuses_untracked_or_symlinked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            (root / "tracked.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c",
                            "user.email=t@example.com", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "one"], check=True)
            (root / "new.txt").write_text("new\n", encoding="utf-8")
            with self.assertRaises(ProtocolError):
                repository_manifest(str(root), "HEAD")
            (root / "new.txt").unlink()
            (root / "link.txt").symlink_to("tracked.txt")
            subprocess.run(["git", "-C", str(root), "add", "link.txt"], check=True)
            with self.assertRaises(ProtocolError):
                repository_manifest(str(root), "HEAD")


if __name__ == "__main__":
    unittest.main()
