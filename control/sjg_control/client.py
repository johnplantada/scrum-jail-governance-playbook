"""Unprivileged client used by the stamped shell interfaces."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import uuid

from .protocol import ProtocolError, request


def request_id(operation: str, arguments: dict[str, Any]) -> str:
    """Make retries inside one wake reuse a key without trusting caller identity fields."""
    wake_id = os.environ.get("SJG_WAKE_ID")
    if not wake_id:
        return f"wake-{uuid.uuid4()}"
    digest = hashlib.sha256(json.dumps(
        [operation, arguments], sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()[:32]
    wake = "".join(character for character in wake_id if character.isalnum() or character in "._:-")
    return f"wake-{wake[:64]}-{digest}"


def send(operation: str, arguments: dict[str, Any], *, key: str | None = None) -> dict[str, Any]:
    socket_path = os.environ.get("SJG_WAKE_SOCKET", "")
    if not socket_path:
        raise ProtocolError("SJG_WAKE_SOCKET is not set; this command is available only inside a governed wake")
    payload = {
        "schema": "sjg.request/v1",
        "idempotency_key": key or request_id(operation, arguments),
        "operation": operation,
        "arguments": arguments,
    }
    return request(socket_path, payload)


def _print_response(response: dict[str, Any]) -> int:
    if response.get("status") == "ok":
        result = response.get("result")
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"sjgctl: {response.get('status', 'error')}: {response.get('error', 'request failed')}",
          file=sys.stderr)
    return 75 if response.get("status") in {"retryable", "indeterminate"} else 1


def pm_arguments(argv: list[str]) -> tuple[str, dict[str, Any]]:
    parser = argparse.ArgumentParser(prog="sjgctl pm")
    parser.add_argument("command", choices=("create", "tasks", "tree", "move", "comment",
                                            "comments", "done", "drop", "route",
                                            "pr-create", "pr-update", "pr-comment", "pr-review"))
    parser.add_argument("--id", type=int)
    parser.add_argument("--role")
    parser.add_argument("--also-role", action="append", default=[])
    parser.add_argument("--title")
    parser.add_argument("--desc", default="")
    parser.add_argument("--type", dest="kind")
    parser.add_argument("--parent", type=int)
    parser.add_argument("--to")
    parser.add_argument("--body")
    parser.add_argument("--pr")
    parser.add_argument("--demo")
    parser.add_argument("--reason")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--repo", choices=("org", "product"))
    parser.add_argument("--topic")
    parser.add_argument("--event", choices=("COMMENT", "REQUEST_CHANGES"))
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args(argv)
    if not args.command.startswith("pr-") and args.repo is not None:
        parser.error("issue and Project commands are fixed to the org repository")
    values = {key: value for key, value in vars(args).items()
              if key != "command" and value not in (None, "", [], False)}
    if "desc" in values:
        values["body"] = values.pop("desc")
    if args.command in {"route", "create"} and "role" in values:
        values["target_role"] = values.pop("role")
    elif "role" in values:
        parser.error("--role is valid only for create or route")
    is_pr = args.command.startswith("pr-")
    values["repository"] = values.pop("repo", None) or ("product" if is_pr else "org")
    operation = {
        "create": "pm.issue.create", "tasks": "read.issue.list",
        "tree": "read.issue.children", "move": "pm.issue.move",
        "comment": "pm.issue.comment", "comments": "read.issue.comments",
        "done": "pm.issue.done", "drop": "pm.issue.drop", "route": "pm.issue.route",
        "pr-create": "pr.create", "pr-update": "pr.update",
        "pr-comment": "pr.comment", "pr-review": "pr.review",
    }[args.command]
    return operation, values


def _git(workspace: Path, *args: str) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": os.fspath(workspace)}
    process = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c",
                              "diff.external=", "-C", os.fspath(workspace), *args],
                             text=True, capture_output=True, timeout=30, env=env)
    if process.returncode:
        raise ProtocolError(process.stderr.strip() or "local Git inspection failed")
    return process.stdout


def repository_manifest(workspace: str, base: str) -> tuple[str, list[dict[str, Any]]]:
    root = Path(workspace).resolve(strict=True)
    base_oid = _git(root, "rev-parse", f"{base}^{{commit}}").strip()
    untracked = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", os.fspath(root),
         "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True, timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C",
             "GIT_CONFIG_NOSYSTEM": "1", "HOME": os.fspath(root)}, check=True).stdout
    if untracked:
        raise ProtocolError("untracked files are not submitted; commit or remove them first")
    raw = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=", "-C",
         os.fspath(root), "diff", "--no-ext-diff", "--name-status", "-z", base_oid, "--"],
        capture_output=True, timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C",
             "GIT_CONFIG_NOSYSTEM": "1", "HOME": os.fspath(root)}, check=True).stdout
    fields = raw.split(b"\0")
    changes: list[dict[str, Any]] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii", "strict")
        index += 1
        if status.startswith(("R", "C")):
            # Represent renames/copies as a delete plus an upsert; authorization validates both.
            old = fields[index].decode("utf-8", "strict")
            new = fields[index + 1].decode("utf-8", "strict")
            index += 2
            changes.append({"operation": "delete", "path": old, "mode": "100644"})
            path = new
        else:
            path = fields[index].decode("utf-8", "strict")
            index += 1
            if status == "D":
                changes.append({"operation": "delete", "path": path, "mode": "100644"})
                continue
        raw_candidate = root / path
        if raw_candidate.is_symlink():
            raise ProtocolError(f"changed path is a symlink: {path}")
        candidate = raw_candidate.resolve(strict=True)
        if root not in candidate.parents:
            raise ProtocolError(f"changed path escapes workspace: {path}")
        data = candidate.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            raise ProtocolError(f"changed file exceeds two-megabyte limit: {path}")
        mode = "100755" if os.access(candidate, os.X_OK) else "100644"
        changes.append({
            "operation": "upsert", "path": path, "mode": mode,
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_base64": base64.b64encode(data).decode("ascii"),
        })
    if len(changes) > 200:
        raise ProtocolError("submission exceeds 200 changed paths")
    return base_oid, changes


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("usage: sjgctl <pm|repo|worker|request> ...")
    area = argv.pop(0)
    if area == "pm":
        operation, arguments = pm_arguments(argv)
    elif area == "repo":
        parser = argparse.ArgumentParser(prog="sjgctl repo")
        parser.add_argument("command", choices=("submit", "status"))
        parser.add_argument("--repository", choices=("org", "product"), required=True)
        parser.add_argument("--topic")
        parser.add_argument("--workspace", default=".")
        parser.add_argument("--base", default="HEAD")
        ns = parser.parse_args(argv)
        if ns.command == "status":
            operation, arguments = "repo.status", {"repository": ns.repository}
        else:
            if not ns.topic:
                parser.error("submit requires --topic")
            base_oid, changes = repository_manifest(ns.workspace, ns.base)
            operation = "repo.submit"
            arguments = {"repository": ns.repository, "topic": ns.topic,
                         "base_oid": base_oid, "changes": changes}
    elif area == "worker":
        parser = argparse.ArgumentParser(prog="sjgctl worker")
        parser.add_argument("type", choices=("researcher", "drafter", "implementer"))
        parser.add_argument("--task", required=True)
        parser.add_argument("--repo", choices=("org", "product"), default="org")
        parser.add_argument("--read-path", action="append", default=[])
        parser.add_argument("--write-path", action="append", default=[])
        ns = parser.parse_args(argv)
        operation = "worker.run"
        arguments = {"repository": ns.repo, "type": ns.type, "task": ns.task,
                     "readPaths": ns.read_path, "writePaths": ns.write_path}
    elif area == "request":
        if len(argv) != 1:
            raise SystemExit("usage: sjgctl request REQUEST.json")
        with open(argv[0], encoding="utf-8") as handle:
            payload = json.load(handle)
        return _print_response(request(os.environ["SJG_WAKE_SOCKET"], payload))
    else:
        raise SystemExit(f"unknown sjgctl area: {area}")
    return _print_response(send(operation, arguments))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProtocolError, OSError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"sjgctl: {exc}")
