"""Crash-recoverable publication for an authoritative export directory."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path


class PublicationError(RuntimeError):
    """An export directory cannot be installed without risking mixed output."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _tree_digest(root: Path | None) -> str | None:
    if root is None or not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise PublicationError(f"publication tree is not a real directory: {root}")
    digest = hashlib.sha256(b"loreloop-export-tree-v1\0")
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise PublicationError(f"publication tree contains a symlink: {relative}")
        if path.is_dir():
            digest.update(b"d\0" + relative.encode() + b"\0")
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PublicationError(f"publication tree contains an unsafe node: {relative}")
        data = path.read_bytes()
        digest.update(b"f\0" + relative.encode() + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _journal_path(output: Path) -> Path:
    return output.parent / f".{output.name}.loreloop-journal.json"


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise PublicationError(f"refusing to remove unsafe publication stage: {path}")
    shutil.rmtree(path)


def recover_publication(output: Path) -> None:
    """Resolve an interrupted install without accepting a mixed document set."""
    journal_path = _journal_path(output)
    if not journal_path.exists():
        return
    if journal_path.is_symlink() or not journal_path.is_file():
        raise PublicationError(f"invalid publication journal: {journal_path}")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        stage_name = journal["stage_name"]
        expected_new = journal["new_digest"]
        expected_old = journal["old_digest"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PublicationError(f"invalid publication journal: {journal_path}") from exc
    if (
        journal.get("version") != 1
        or journal.get("target_name") != output.name
        or not isinstance(stage_name, str)
        or Path(stage_name).name != stage_name
        or not stage_name.startswith(f".{output.name}.loreloop-stage-")
    ):
        raise PublicationError(f"publication journal does not belong to {output}")
    stage = output.parent / stage_name
    output_digest = _tree_digest(output if output.exists() else None)
    stage_digest = _tree_digest(stage if stage.exists() else None)
    installed = output_digest == expected_new and stage_digest == expected_old
    first_install = output_digest == expected_new and expected_old is None and stage_digest is None
    not_installed = output_digest == expected_old and stage_digest == expected_new
    staged_first = output_digest is None and expected_old is None and stage_digest == expected_new
    if installed:
        _remove_tree(stage)
    elif first_install:
        pass
    elif not_installed or staged_first:
        _remove_tree(stage)
    else:
        raise PublicationError("publication recovery found an unrecognized target/stage state")
    journal_path.unlink()
    _fsync_directory(output.parent)


def _copy_existing(output: Path, stage: Path) -> None:
    if not output.exists():
        return
    _ = _tree_digest(output)
    for source in sorted(output.rglob("*"), key=lambda item: item.relative_to(output).as_posix()):
        relative = source.relative_to(output)
        target = stage / relative
        if source.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            os.chmod(target, stat.S_IMODE(source.stat().st_mode) & 0o777)


def _sync_tree(root: Path) -> None:
    directories = [root]
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            directories.append(path)
            continue
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _exchange(left: Path, right: Path) -> bool:
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        return False
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2)
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
        return False
    raise PublicationError(f"cannot atomically exchange export directories: errno {error}")


def publish_tree(
    output: Path,
    files: Iterable[tuple[str, str]],
    *,
    managed_filenames: Iterable[str] = (),
) -> None:
    """Install a complete tree; on Linux, updates switch with one directory exchange."""
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    recover_publication(output)
    stage = output.parent / f".{output.name}.loreloop-stage-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        _copy_existing(output, stage)
        for filename in managed_filenames:
            if Path(filename).name != filename:
                raise PublicationError(f"invalid managed filename: {filename}")
            stale = stage / filename
            if stale.exists() or stale.is_symlink():
                if stale.is_symlink() or not stale.is_file():
                    raise PublicationError(f"managed output is not a regular file: {filename}")
                stale.unlink()
        for filename, content in files:
            if Path(filename).name != filename:
                raise PublicationError(f"invalid publication filename: {filename}")
            target = stage / filename
            target.write_text(content, encoding="utf-8")
            os.chmod(target, 0o644)
        _sync_tree(stage)
        new_digest = _tree_digest(stage)
        old_digest = _tree_digest(output if output.exists() else None)
        journal = {
            "version": 1,
            "target_name": output.name,
            "stage_name": stage.name,
            "old_digest": old_digest,
            "new_digest": new_digest,
            "state": "install_intent",
        }
        _atomic_json(_journal_path(output), journal)
        if output.exists():
            if not _exchange(stage, output):
                raise PublicationError(
                    "atomic directory exchange is unavailable on this filesystem"
                )
        else:
            os.replace(stage, output)
        _fsync_directory(output.parent)
        journal["state"] = "installed"
        _atomic_json(_journal_path(output), journal)
        _remove_tree(stage)
        _journal_path(output).unlink()
        _fsync_directory(output.parent)
    except BaseException:
        if not _journal_path(output).exists():
            _remove_tree(stage)
        raise
