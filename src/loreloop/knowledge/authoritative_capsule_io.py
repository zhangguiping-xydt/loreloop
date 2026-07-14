"""Race-resistant file and canonical JSON loading for Capsule replay."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .authoritative_capsule import JsonValue
from .authoritative_ids import IdentityContractError, canon_v4


class CapsuleIoError(ValueError):
    """A Capsule export cannot be loaded without following unsafe paths."""


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleIoError(f"capsule JSON contains duplicate field: {key}")
        result[key] = value
    return result


def read_export_files(export_dir: Path) -> dict[str, bytes]:
    """Read a flat export through directory-relative, no-follow file descriptors."""
    if export_dir.is_symlink():
        raise CapsuleIoError(f"export directory must not be a symlink: {export_dir}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(export_dir, flags)
    except OSError as exc:
        raise CapsuleIoError(f"cannot open export directory safely: {export_dir}") from exc
    files: dict[str, bytes] = {}
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise CapsuleIoError(f"export entry must be a regular file: {entry.name}")
                file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                try:
                    file_fd = os.open(entry.name, file_flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise CapsuleIoError(f"cannot open export file safely: {entry.name}") from exc
                with os.fdopen(file_fd, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise CapsuleIoError(f"export entry must be a regular file: {entry.name}")
                    files[entry.name] = handle.read()
    finally:
        os.close(directory_fd)
    return files


def parse_capsule(data: bytes) -> Mapping[str, JsonValue]:
    """Load exact canonical JSON while rejecting duplicate keys and non-UTF-8 bytes."""
    try:
        content = data.decode("utf-8")
        parsed = cast(
            JsonValue,
            json.loads(content, object_pairs_hook=_reject_duplicate_keys),
        )
        if not isinstance(parsed, dict):
            raise CapsuleIoError("capsule must be an object")
        if canon_v4(parsed).decode() + "\n" != content:
            raise CapsuleIoError("capsule JSON is not in canonical form")
    except UnicodeDecodeError as exc:
        raise CapsuleIoError("capsule is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise CapsuleIoError("capsule is not valid JSON") from exc
    except IdentityContractError as exc:
        raise CapsuleIoError(f"capsule is outside the canonical value domain: {exc}") from exc
    return parsed
