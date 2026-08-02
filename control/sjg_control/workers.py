"""Supervisor-owned bounded, non-recursive named workers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
import hashlib
import errno
import os
import shutil
import stat
import tempfile
import uuid

from .authz import Session
from .policy import OrganizationPolicy
from .store import Store, StateError


class WorkerError(RuntimeError):
    pass


class Launcher(Protocol):
    def run_worker(self, *, worker_type: str, model: str, prompt: str, workspace: Path,
                   environment: dict[str, str], timeout_seconds: int) -> str: ...


@dataclass(frozen=True)
class WorkerResult:
    attempt_id: str
    worker_type: str
    output: str
    changed_paths: tuple[str, ...]


def _paths(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise WorkerError("worker path scopes must be arrays")
    if len(values) > 200:
        raise WorkerError("worker path scope exceeds 200 entries")
    result = []
    for raw in values:
        if not isinstance(raw, str):
            raise WorkerError("worker paths must be strings")
        value = str(raw)
        path = PurePosixPath(value)
        if (not value or len(value.encode("utf-8")) > 255 or path.is_absolute()
                or ".." in path.parts or "\x00" in value or "\\" in value
                or any(ord(character) < 32 for character in value) or str(path) != value):
            raise WorkerError(f"unsafe worker path: {value!r}")
        result.append(str(path))
    if len(set(result)) != len(result):
        raise WorkerError("worker path scopes must not contain duplicates")
    return tuple(sorted(set(result)))


def _scoped_path(root: Path, relative: str, *, create_parents: bool = False) -> Path:
    """Resolve a lexical relative path while rejecting symlinks in every component."""
    candidate = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        candidate = candidate / part
        is_leaf = index == len(parts) - 1
        try:
            mode = os.lstat(candidate).st_mode
        except FileNotFoundError:
            if create_parents and not is_leaf:
                candidate.mkdir(mode=0o700)
                continue
            # Once a component is absent, descendants cannot already exist.
            return root.joinpath(*parts)
        if stat.S_ISLNK(mode):
            raise WorkerError(f"worker path contains a symlink: {relative}")
        if not is_leaf and not stat.S_ISDIR(mode):
            raise WorkerError(f"worker path parent is not a directory: {relative}")
    return root.joinpath(*parts)


def _parent_fd(root: Path, relative: str, *, create: bool) -> tuple[int, str] | None:
    """Hold every parent directory open so pathname swaps cannot redirect an operation."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        parts = PurePosixPath(relative).parts
        for component in parts[:-1]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except OSError as exc:
        os.close(descriptor)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise WorkerError(f"worker path contains a symlink or unsafe component: {relative}") from exc
        raise


def _copy_scoped_input(root: Path, relative: str, destination: Path) -> bool:
    parent = _parent_fd(root, relative, create=False)
    if parent is None:
        return False
    directory, leaf = parent
    try:
        try:
            source_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise WorkerError(f"worker input is a symlink: {relative}") from exc
            raise
        with os.fdopen(source_fd, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise WorkerError(f"worker input is not a regular file: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as target:
                shutil.copyfileobj(source, target)
            os.chmod(destination, stat.S_IMODE(metadata.st_mode))
        return True
    finally:
        os.close(directory)


def _parent_identity_matches(root: Path, relative: str, held_directory: int) -> bool:
    try:
        current = _parent_fd(root, relative, create=False)
    except WorkerError:
        return False
    if current is None:
        return False
    current_directory, _leaf = current
    try:
        held = os.fstat(held_directory)
        reopened = os.fstat(current_directory)
        return (held.st_dev, held.st_ino) == (reopened.st_dev, reopened.st_ino)
    finally:
        os.close(current_directory)


def _replace_regular_file(source: Path, root: Path, relative: str) -> None:
    """Import through held directory FDs and atomic rename; never follow workspace links."""
    parent = _parent_fd(root, relative, create=True)
    assert parent is not None
    directory, leaf = parent
    temporary = f".sjg-import-{uuid.uuid4().hex}"
    temporary_created = False
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(source_fd, "rb") as input_file:
            metadata = os.fstat(input_file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise WorkerError(f"worker output is not a regular file: {relative}")
            output_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600, dir_fd=directory)
            temporary_created = True
            with os.fdopen(output_fd, "wb") as output_file:
                shutil.copyfileobj(input_file, output_file)
                output_file.flush()
                os.fsync(output_file.fileno())
                os.fchmod(output_file.fileno(), stat.S_IMODE(metadata.st_mode))
        os.rename(temporary, leaf, src_dir_fd=directory, dst_dir_fd=directory)
        temporary_created = False
        if not _parent_identity_matches(root, relative, directory):
            raise WorkerError(f"worker import conflicted with a workspace path change: {relative}")
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def _unlink_scoped(root: Path, relative: str) -> None:
    parent = _parent_fd(root, relative, create=False)
    if parent is None:
        return
    directory, leaf = parent
    try:
        try:
            metadata = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(metadata.st_mode):
            raise WorkerError(f"worker output deletion targets a directory: {relative}")
        os.unlink(leaf, dir_fd=directory)
        if not _parent_identity_matches(root, relative, directory):
            raise WorkerError(f"worker deletion conflicted with a workspace path change: {relative}")
    finally:
        os.close(directory)


def snapshot(root: Path, paths: tuple[str, ...]) -> dict[str, tuple[str, int]]:
    root = root.resolve(strict=True)
    result = {}
    for relative in paths:
        path = _scoped_path(root, relative)
        if path.exists():
            if not path.is_file():
                raise WorkerError(f"worker path is not a regular file: {relative}")
            data = path.read_bytes()
            result[relative] = (hashlib.sha256(data).hexdigest(), len(data))
        else:
            result[relative] = ("absent", 0)
    return result


def snapshot_tree(root: Path) -> dict[str, tuple[str, int]]:
    """Inventory the whole scratch tree so undeclared new files cannot hide."""
    root = root.resolve(strict=True)
    result: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            raise WorkerError(f"worker created a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise WorkerError(f"worker created a non-regular file: {relative}")
        data = path.read_bytes()
        result[relative] = (hashlib.sha256(data).hexdigest(), len(data))
    return result


class WorkerSupervisor:
    def __init__(self, policy: OrganizationPolicy, store: Store, launcher: Launcher,
                 *, luna_models: dict[str, str], scratch_root: str | Path,
                 timeout_seconds: int = 900, max_patch_bytes: int = 2 * 1024 * 1024,
                 max_output_bytes: int = 256 * 1024):
        self.policy = policy
        self.store = store
        self.launcher = launcher
        self.luna_models = dict(luna_models)
        self.scratch_root = Path(scratch_root)
        self.timeout_seconds = timeout_seconds
        self.max_patch_bytes = max_patch_bytes
        self.max_output_bytes = max_output_bytes

    def run(self, session: Session, arguments: dict[str, Any], *, parent_workspace: str | Path) -> WorkerResult:
        role = self.policy.role(session.role)
        worker_type = str(arguments.get("type") or "")
        if worker_type not in role.worker_types:
            raise WorkerError(f"worker type {worker_type!r} is not allowed")
        task = arguments.get("task")
        if not isinstance(task, str) or not task.strip() or len(task.encode("utf-8")) > 32768:
            raise WorkerError("worker task must be non-empty and at most 32 KiB")
        if worker_type not in self.luna_models:
            raise WorkerError(f"no sealed Luna model for worker type {worker_type}")
        read_paths = _paths(arguments.get("readPaths") or [])
        write_paths = _paths(arguments.get("writePaths") or [])
        if worker_type != "implementer" and write_paths:
            raise WorkerError(f"{worker_type} is read-only")
        if worker_type == "implementer" and not write_paths:
            raise WorkerError("implementer requires declared write paths")
        if not set(write_paths) <= set(read_paths):
            raise WorkerError("every write path must also be declared as an input path")
        attempt = self.store.reserve_worker(
            session.session_id, worker_type,
            cumulative_cap=role.max_workers_per_wake,
            concurrent_cap=role.max_workers_concurrent,
            global_cap=self.policy.global_max_agents,
        )
        parent = Path(parent_workspace).resolve(strict=True)
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f"worker-{attempt}-", dir=self.scratch_root)).resolve()
        try:
            for relative in sorted(set(read_paths) | set(write_paths)):
                _copy_scoped_input(parent, relative, scratch / relative)
            before = snapshot_tree(scratch)
            self.store.connection.execute(
                "UPDATE worker_attempt SET state='running' WHERE attempt_id=?", (attempt,))
            output = self.launcher.run_worker(
                worker_type=worker_type, model=self.luna_models[worker_type],
                prompt=task, workspace=scratch,
                environment={"SJG_WORKER_ATTEMPT": attempt},
                timeout_seconds=self.timeout_seconds,
            )
            if not isinstance(output, str) or len(output.encode("utf-8")) > self.max_output_bytes:
                raise WorkerError("worker textual output exceeds the response-size limit")
            after = snapshot_tree(scratch)
            changed = tuple(sorted(path for path in set(before) | set(after)
                                   if before.get(path) != after.get(path)))
            forbidden = set(changed) - set(write_paths)
            if forbidden:
                raise WorkerError("worker changed paths outside its scope: " + ", ".join(sorted(forbidden)))
            total = sum(after.get(path, ("absent", 0))[1] for path in changed)
            if total > self.max_patch_bytes:
                raise WorkerError("worker output exceeds the patch-size limit")
            for relative in changed:
                source = scratch / relative
                if relative not in after:
                    _unlink_scoped(parent, relative)
                else:
                    _replace_regular_file(source, parent, relative)
            artifact = hashlib.sha256(output.encode()).hexdigest()
            self.store.connection.execute(
                "UPDATE worker_attempt SET state='succeeded',finished_at=datetime('now'),artifact_sha256=? WHERE attempt_id=?",
                (artifact, attempt),
            )
            return WorkerResult(attempt, worker_type, output, changed)
        except Exception:
            self.store.connection.execute(
                "UPDATE worker_attempt SET state='failed',finished_at=datetime('now') WHERE attempt_id=?",
                (attempt,),
            )
            raise
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
