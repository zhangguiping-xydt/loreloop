"""Canonical filesystem paths for LoreLoop state and operator-owned data."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

STATE_DIR_NAME = ".loreloop"


class StatePathError(RuntimeError):
    pass


def state_root(workdir: Path) -> Path:
    return workdir / STATE_DIR_NAME


def state_path(workdir: Path, *parts: str) -> Path:
    return state_root(workdir).joinpath(*parts)


def ensure_private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise StatePathError(f"refusing symlinked private directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise StatePathError(f"private path is not a directory: {path}")
    os.chmod(path, 0o700)
    return path


def ensure_state_root(workdir: Path) -> Path:
    return ensure_private_directory(state_root(workdir))


def reject_symlink(path: Path, *, label: str = "path") -> None:
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return
    if stat.S_ISLNK(info.st_mode):
        raise StatePathError(f"refusing symlinked {label}: {path}")


def chmod_fd(fd: int, mode: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(fd, mode)


def secure_append_text(path: Path, text: str) -> None:
    ensure_private_directory(path.parent)
    reject_symlink(path, label="state file")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        chmod_fd(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def secure_atomic_write_text(path: Path, text: str) -> None:
    ensure_private_directory(path.parent)
    reject_symlink(path, label="state file")
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        chmod_fd(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def key_directory() -> Path:
    configured = os.environ.get("LORELOOP_KEY_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    return (Path.home() / ".loreloop/keys").absolute()


def require_key_directory_outside(workdir: Path) -> Path:
    key_dir = key_directory()
    reject_symlink(key_dir, label="key directory")
    resolved_key_dir = key_dir.resolve()
    root = workdir.resolve()
    if resolved_key_dir == root or resolved_key_dir.is_relative_to(root):
        raise StatePathError(
            f"LORELOOP_KEY_DIR must be outside the project tree: {key_dir} resolves under {root}"
        )
    return key_dir


def registry_file() -> Path:
    configured = os.environ.get("LORELOOP_REGISTRY")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".loreloop/projects.json"
