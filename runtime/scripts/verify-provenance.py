#!/usr/bin/env python3
"""Verify a stamped payload against playbook/SOURCE.json."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true",
                        help="also require a clean, release-qualified source")
    args = parser.parse_args(argv)
    path = ROOT / "playbook" / "SOURCE.json"
    try:
        provenance = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"provenance: cannot read {path}: {exc}")
    if provenance.get("schemaVersion") != 1:
        raise SystemExit("provenance: unsupported schema")
    if args.release and (provenance.get("sourceState") != "clean"
                         or provenance.get("releaseQualified") is not True):
        raise SystemExit("provenance: dirty development stamp is not release-qualified")
    if args.release:
        expected = os.environ.get("SJG_EXPECTED_SOURCE_MANIFEST_SHA256", "")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not expected or expected != actual:
            raise SystemExit("provenance: release manifest is not bound to the Human-controlled expected digest")
    try:
        organization = json.loads((ROOT / "organization.yaml").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"provenance: organization.yaml is missing or malformed: {exc}")
    control = organization.get("controlRelease") or {}
    if control.get("sourceTreeSha256") != provenance.get("sourceTreeSha256"):
        raise SystemExit("provenance: organization control release is not bound to this source tree")
    if control.get("version") != (ROOT / "VERSION").read_text(encoding="utf-8").strip():
        raise SystemExit("provenance: organization control version does not match the stamp")
    rows = []
    for entry in provenance.get("files") or []:
        relative = str(entry.get("path") or "")
        candidate = (ROOT / relative).resolve(strict=False)
        if ROOT != candidate and ROOT not in candidate.parents:
            raise SystemExit(f"provenance: path escapes stamp: {relative}")
        if candidate.is_symlink() or not candidate.is_file():
            raise SystemExit(f"provenance: file absent or non-regular: {relative}")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        mode = format(stat.S_IMODE(candidate.stat().st_mode), "04o")
        if digest != entry.get("sha256") or mode != entry.get("mode"):
            raise SystemExit(f"provenance: content or mode drift: {relative}")
        rows.append(entry)
    payload = hashlib.sha256(json.dumps(rows, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()
    if payload != provenance.get("payloadTreeSha256"):
        raise SystemExit("provenance: payload tree digest mismatch")
    listed = {str(entry.get("path") or "") for entry in rows}
    required = {"organization.yaml", "VERSION", "scripts/pm-gh.sh",
                "scripts/git-push-branch.sh", "scripts/agent-run.sh",
                ".github/workflows/ci.yml", "playbook/SOURCE.md"}
    missing = required - listed
    if missing:
        raise SystemExit("provenance: security-bearing files are not covered: "
                         + ", ".join(sorted(missing)))
    security_files = set()
    for security_root in (ROOT / "scripts", ROOT / ".github" / "workflows"):
        for candidate in security_root.rglob("*"):
            if ("__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo"}):
                continue
            if candidate.is_symlink():
                raise SystemExit(f"provenance: unsafe symlink in security namespace: {candidate.relative_to(ROOT)}")
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise SystemExit(f"provenance: non-regular security entry: {candidate.relative_to(ROOT)}")
            security_files.add(candidate.relative_to(ROOT).as_posix())
    unlisted = security_files - listed
    if unlisted:
        raise SystemExit("provenance: unlisted security-bearing files: "
                         + ", ".join(sorted(unlisted)))
    if (ROOT / ".codex/config.toml").exists() or (ROOT / ".claude/settings.json").exists():
        raise SystemExit("provenance: generated workspace contains a mutable provider policy")
    state = provenance.get("sourceState")
    print(f"ok: {len(rows)} stamped files match provenance ({state})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
