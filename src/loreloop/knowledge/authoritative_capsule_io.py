"""Race-resistant file and canonical JSON loading for Capsule replay."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .authoritative_capsule import CAPSULE_FILENAME, JsonValue
from .authoritative_ids import MAX_SAFE_INTEGER, IdentityContractError, canon_v4

MAX_CAPSULE_BYTES = 128 * 1024 * 1024
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_MANAGED_TOTAL_BYTES = 256 * 1024 * 1024
_MIN_DOCUMENTS = 6
_MAX_DOCUMENTS = 8


class CapsuleIoError(ValueError):
    """A Capsule export cannot be loaded without following unsafe paths."""


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleIoError(f"capsule JSON contains duplicate field: {key}")
        result[key] = value
    return result


def _parse_integer(value: str) -> int:
    digits = value.removeprefix("-")
    if len(digits) > 16:
        raise CapsuleIoError("capsule JSON integer is outside the safe range")
    parsed = int(value)
    if not -MAX_SAFE_INTEGER <= parsed <= MAX_SAFE_INTEGER:
        raise CapsuleIoError("capsule JSON integer is outside the safe range")
    return parsed


def _safe_document_filename(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.endswith(".md")
        or Path(value).name != value
        or value in {".", ".."}
        or "\\" in value
        or "\x00" in value
    ):
        raise CapsuleIoError(f"capsule contains an unsafe document filename: {value!r}")
    return value


def managed_document_filenames(capsule_data: bytes) -> tuple[str, ...]:
    """Return the exact top-level Markdown set named by a bounded canonical Capsule."""
    root = parse_capsule(capsule_data)
    documents = root.get("documents")
    if not isinstance(documents, list) or not _MIN_DOCUMENTS <= len(documents) <= _MAX_DOCUMENTS:
        raise CapsuleIoError("capsule documents must be a bounded array")
    filenames: list[str] = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise CapsuleIoError(f"capsule document {index} must be an object")
        filenames.append(_safe_document_filename(document.get("filename")))
    if len(filenames) != len(set(filenames)):
        raise CapsuleIoError("capsule contains duplicate document filenames")
    return tuple(filenames)


def _read_regular_at(directory_fd: int, name: str, limit: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CapsuleIoError(f"managed export entry must be a regular file: {name}")
        if metadata.st_size > limit:
            raise CapsuleIoError(f"managed export entry exceeds its size limit: {name}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise CapsuleIoError(f"managed export entry exceeds its size limit: {name}")
        if len(data) != metadata.st_size:
            raise CapsuleIoError(f"managed export entry changed while it was read: {name}")
        return data
    except FileNotFoundError as exc:
        raise CapsuleIoError(f"export file set mismatch; missing managed document: {name}") from exc
    except OSError as exc:
        raise CapsuleIoError(f"cannot open managed export file safely: {name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_export_files(export_dir: Path) -> dict[str, bytes]:
    """Read only Capsule-bound files; ignore real, top-level operator files/directories."""
    if export_dir.is_symlink():
        raise CapsuleIoError(f"export directory must not be a symlink: {export_dir}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(export_dir, flags)
    except OSError as exc:
        raise CapsuleIoError(f"cannot open export directory safely: {export_dir}") from exc
    try:
        capsule_present = False
        with os.scandir(directory_fd) as directory_entries:
            for entry in directory_entries:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise CapsuleIoError(
                        f"export entry must be a regular file or real directory: {entry.name}"
                    )
                capsule_present = capsule_present or entry.name == CAPSULE_FILENAME
        if not capsule_present:
            raise CapsuleIoError(f"export is missing {CAPSULE_FILENAME}")
        capsule = _read_regular_at(directory_fd, CAPSULE_FILENAME, MAX_CAPSULE_BYTES)
        filenames = managed_document_filenames(capsule)
        files = {CAPSULE_FILENAME: capsule}
        total = len(capsule)
        if total > MAX_MANAGED_TOTAL_BYTES:
            raise CapsuleIoError("managed export exceeds the total size limit")
        for filename in filenames:
            document = _read_regular_at(directory_fd, filename, MAX_DOCUMENT_BYTES)
            total += len(document)
            if total > MAX_MANAGED_TOTAL_BYTES:
                raise CapsuleIoError("managed export exceeds the total size limit")
            files[filename] = document
        return files
    except OSError as exc:
        raise CapsuleIoError(f"cannot inspect export directory safely: {export_dir}") from exc
    finally:
        os.close(directory_fd)


def parse_capsule(data: bytes) -> Mapping[str, JsonValue]:
    """Load exact canonical JSON while rejecting duplicate keys and non-UTF-8 bytes."""
    if len(data) > MAX_CAPSULE_BYTES:
        raise CapsuleIoError("capsule exceeds the supported size limit")
    try:
        content = data.decode("utf-8")
        parsed = cast(
            JsonValue,
            json.loads(
                content,
                object_pairs_hook=_reject_duplicate_keys,
                parse_int=_parse_integer,
            ),
        )
        del content
        if not isinstance(parsed, dict):
            raise CapsuleIoError("capsule must be an object")
        if canon_v4(parsed) + b"\n" != data:
            raise CapsuleIoError("capsule JSON is not in canonical form")
    except CapsuleIoError:
        raise
    except UnicodeDecodeError as exc:
        raise CapsuleIoError("capsule is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise CapsuleIoError("capsule is not valid JSON") from exc
    except IdentityContractError as exc:
        raise CapsuleIoError(f"capsule is outside the canonical value domain: {exc}") from exc
    except RecursionError as exc:
        raise CapsuleIoError("capsule JSON nesting is too deep") from exc
    except ValueError as exc:
        raise CapsuleIoError("capsule JSON contains an invalid value") from exc
    return parsed
