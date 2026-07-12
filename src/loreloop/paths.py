"""Canonical filesystem paths for LoreLoop state and operator-owned data."""

from __future__ import annotations

import json
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


def trust_locations_file() -> Path:
    configured = os.environ.get("LORELOOP_TRUST_REGISTRY")
    if configured:
        return Path(configured).expanduser().absolute()
    return (Path.home() / ".loreloop/trust-locations.json").absolute()


def load_trust_locations() -> dict[str, Path]:
    path = trust_locations_file()
    if not path.exists():
        return {}
    reject_symlink(path, label="trust-location registry")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StatePathError(f"cannot read trust-location registry {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"version", "projects"}:
        raise StatePathError(f"invalid trust-location registry {path}")
    if data["version"] != 1 or not isinstance(data["projects"], dict):
        raise StatePathError(f"invalid trust-location registry {path}")
    locations: dict[str, Path] = {}
    for raw_project, raw_directory in data["projects"].items():
        if (
            not isinstance(raw_project, str)
            or not raw_project
            or not Path(raw_project).is_absolute()
            or not isinstance(raw_directory, str)
            or not raw_directory
            or not Path(raw_directory).is_absolute()
        ):
            raise StatePathError(f"invalid trust-location registry {path}")
        locations[str(Path(raw_project).resolve())] = Path(raw_directory).resolve()
    return locations


def register_key_directory(workdir: Path, directory: Path) -> None:
    resolved_workdir = workdir.resolve()
    resolved_directory = directory.expanduser().resolve()
    if resolved_directory == resolved_workdir or resolved_directory.is_relative_to(
        resolved_workdir
    ):
        raise StatePathError("trust credential directory must be outside the project tree")
    locations = load_trust_locations()
    locations[str(resolved_workdir)] = resolved_directory
    payload = {
        "version": 1,
        "projects": {project: str(key_dir) for project, key_dir in sorted(locations.items())},
    }
    secure_atomic_write_text(
        trust_locations_file(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def unregister_key_directory(workdir: Path) -> None:
    locations = load_trust_locations()
    if locations.pop(str(workdir.resolve()), None) is None:
        return
    payload = {
        "version": 1,
        "projects": {project: str(key_dir) for project, key_dir in sorted(locations.items())},
    }
    secure_atomic_write_text(
        trust_locations_file(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def key_directory(workdir: Path | None = None) -> Path:
    configured = os.environ.get("LORELOOP_KEY_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    if workdir is not None:
        registered = load_trust_locations().get(str(workdir.resolve()))
        if registered is not None:
            return registered
    return (Path.home() / ".loreloop/keys").absolute()


def require_key_directory_outside(workdir: Path, *, directory: Path | None = None) -> Path:
    key_dir = directory.expanduser().absolute() if directory is not None else key_directory(workdir)
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
