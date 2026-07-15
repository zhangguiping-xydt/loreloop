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
MAX_JSON_VALUES = 4_000_000
MAX_JSON_OBJECT_MEMBERS = 3_000_000
MAX_JSON_CONTAINER_ITEMS = 100_000
MAX_JSON_ARRAY_ELEMENTS = 1_000_000
MAX_JSON_CONTAINERS = 1_000_000
MAX_JSON_DEPTH = 128
MAX_JSON_STRING_BYTES = 8 * 1024 * 1024
MAX_JSON_SCALAR_BYTES = 64
_MIN_DOCUMENTS = 6
_MAX_DOCUMENTS = 8
_REQUIRED_DOCUMENT_SUFFIXES = frozenset(
    {"功能清单", "需求规格", "系统架构", "详细设计", "用户手册", "验收规格"}
)
_OPTIONAL_DOCUMENT_SUFFIXES = frozenset({"接口契约", "数据库设计"})
_DOCUMENT_SUFFIXES = _REQUIRED_DOCUMENT_SUFFIXES | _OPTIONAL_DOCUMENT_SUFFIXES

_ROOT = 0
_OBJECT = 1
_ARRAY = 2
_VALUE = 0
_END = 1
_PENDING = 2
_KEY_OR_END = 3
_KEY = 4
_COLON = 5
_COMMA_OR_END = 6
_VALUE_OR_END = 7


class CapsuleIoError(ValueError):
    """A Capsule export cannot be loaded without following unsafe paths."""


def _skip_json_string(data: bytes, start: int) -> int:
    index = start + 1
    escaped = False
    while index < len(data):
        byte = data[index]
        if not escaped and byte == 0x22:
            if index - start - 1 > MAX_JSON_STRING_BYTES:
                raise CapsuleIoError("capsule JSON string exceeds the size limit")
            return index + 1
        if not escaped and byte < 0x20:
            raise CapsuleIoError("capsule JSON string contains a control byte")
        if escaped:
            escaped = False
        elif byte == 0x5C:
            escaped = True
        index += 1
        if index - start - 1 > MAX_JSON_STRING_BYTES:
            raise CapsuleIoError("capsule JSON string exceeds the size limit")
    raise CapsuleIoError("capsule JSON string is unterminated")


def _validate_json_budget(data: bytes) -> None:
    """Reject excessive JSON width/depth before constructing a Python object graph."""
    # frame = [kind, state, direct child/member count]; root never increments count.
    frames: list[list[int]] = [[_ROOT, _VALUE, 0]]
    total_values = 0
    total_object_members = 0
    total_array_elements = 0
    total_containers = 0
    index = 0

    def finish_value() -> None:
        frame = frames[-1]
        if frame[1] != _PENDING:
            raise CapsuleIoError("capsule JSON value state is invalid")
        frame[1] = _END if frame[0] == _ROOT else _COMMA_OR_END

    def begin_value() -> None:
        nonlocal total_array_elements, total_values
        frame = frames[-1]
        if frame[0] == _ROOT:
            if frame[1] != _VALUE:
                raise CapsuleIoError("capsule JSON has multiple root values")
        elif frame[0] == _OBJECT:
            if frame[1] != _VALUE:
                raise CapsuleIoError("capsule JSON object value is misplaced")
        elif frame[0] == _ARRAY:
            if frame[1] not in {_VALUE, _VALUE_OR_END}:
                raise CapsuleIoError("capsule JSON array value is misplaced")
            frame[2] += 1
            total_array_elements += 1
            if frame[2] > MAX_JSON_CONTAINER_ITEMS:
                raise CapsuleIoError("capsule JSON array exceeds the item limit")
            if total_array_elements > MAX_JSON_ARRAY_ELEMENTS:
                raise CapsuleIoError("capsule JSON exceeds the total array element limit")
        frame[1] = _PENDING
        total_values += 1
        if total_values > MAX_JSON_VALUES:
            raise CapsuleIoError("capsule JSON exceeds the total value limit")

    def close_container(kind: int) -> None:
        if len(frames) == 1 or frames[-1][0] != kind:
            raise CapsuleIoError("capsule JSON container is unbalanced")
        frames.pop()
        finish_value()

    def start_value() -> None:
        nonlocal index, total_containers
        begin_value()
        byte = data[index]
        if byte == 0x7B:
            total_containers += 1
            if total_containers > MAX_JSON_CONTAINERS:
                raise CapsuleIoError("capsule JSON exceeds the container limit")
            if len(frames) > MAX_JSON_DEPTH:
                raise CapsuleIoError("capsule JSON nesting is too deep")
            frames.append([_OBJECT, _KEY_OR_END, 0])
            index += 1
            return
        if byte == 0x5B:
            total_containers += 1
            if total_containers > MAX_JSON_CONTAINERS:
                raise CapsuleIoError("capsule JSON exceeds the container limit")
            if len(frames) > MAX_JSON_DEPTH:
                raise CapsuleIoError("capsule JSON nesting is too deep")
            frames.append([_ARRAY, _VALUE_OR_END, 0])
            index += 1
            return
        if byte == 0x22:
            index = _skip_json_string(data, index)
            finish_value()
            return
        scalar_start = index
        while index < len(data) and data[index] not in b" \t\r\n,]}":
            index += 1
            if index - scalar_start > MAX_JSON_SCALAR_BYTES:
                if data[scalar_start] == 0x2D or 0x30 <= data[scalar_start] <= 0x39:
                    raise CapsuleIoError("capsule JSON integer is outside the safe range")
                raise CapsuleIoError("capsule JSON scalar exceeds the size limit")
        if index == scalar_start:
            raise CapsuleIoError("capsule JSON value is invalid")
        finish_value()

    while index < len(data):
        byte = data[index]
        if byte in b" \t\r\n":
            index += 1
            continue
        frame = frames[-1]
        kind, state = frame[0], frame[1]
        if kind == _ROOT:
            if state == _VALUE:
                start_value()
                continue
            if state == _END:
                raise CapsuleIoError("capsule JSON has trailing data")
            raise CapsuleIoError("capsule JSON root state is invalid")
        if kind == _OBJECT:
            if state in {_KEY_OR_END, _KEY}:
                if byte == 0x7D and state == _KEY_OR_END:
                    index += 1
                    close_container(_OBJECT)
                    continue
                if byte != 0x22:
                    raise CapsuleIoError("capsule JSON object key must be a string")
                index = _skip_json_string(data, index)
                frame[2] += 1
                total_object_members += 1
                if frame[2] > MAX_JSON_CONTAINER_ITEMS:
                    raise CapsuleIoError("capsule JSON object exceeds the member limit")
                if total_object_members > MAX_JSON_OBJECT_MEMBERS:
                    raise CapsuleIoError("capsule JSON exceeds the total object member limit")
                frame[1] = _COLON
                continue
            if state == _COLON:
                if byte != 0x3A:
                    raise CapsuleIoError("capsule JSON object member lacks a colon")
                frame[1] = _VALUE
                index += 1
                continue
            if state == _VALUE:
                start_value()
                continue
            if state == _COMMA_OR_END:
                if byte == 0x2C:
                    frame[1] = _KEY
                    index += 1
                    continue
                if byte == 0x7D:
                    index += 1
                    close_container(_OBJECT)
                    continue
                raise CapsuleIoError("capsule JSON object lacks a comma or closing brace")
            raise CapsuleIoError("capsule JSON object state is invalid")
        if state in {_VALUE_OR_END, _VALUE}:
            if byte == 0x5D and state == _VALUE_OR_END:
                index += 1
                close_container(_ARRAY)
                continue
            start_value()
            continue
        if state == _COMMA_OR_END:
            if byte == 0x2C:
                frame[1] = _VALUE
                index += 1
                continue
            if byte == 0x5D:
                index += 1
                close_container(_ARRAY)
                continue
            raise CapsuleIoError("capsule JSON array lacks a comma or closing bracket")
        raise CapsuleIoError("capsule JSON array state is invalid")
    if len(frames) != 1 or frames[0][1] != _END:
        raise CapsuleIoError("capsule JSON is incomplete")


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


def managed_document_filenames_from_root(
    root: Mapping[str, JsonValue],
) -> tuple[str, ...]:
    """Return the exact top-level Markdown set named by a parsed Capsule."""
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
    prefixes: set[str] = set()
    families: set[str] = set()
    for filename in filenames:
        matched = next(
            (family for family in _DOCUMENT_SUFFIXES if filename.endswith(f"-{family}.md")),
            None,
        )
        if matched is None:
            raise CapsuleIoError("capsule document filename is outside the managed family set")
        prefixes.add(filename[: -len(f"-{matched}.md")])
        families.add(matched)
    if (
        len(prefixes) != 1
        or "" in prefixes
        or not _REQUIRED_DOCUMENT_SUFFIXES <= families
        or not families <= _DOCUMENT_SUFFIXES
        or len(families) != len(filenames)
    ):
        raise CapsuleIoError("capsule document filenames do not form one closed project set")
    return tuple(filenames)


def managed_document_filenames(capsule_data: bytes) -> tuple[str, ...]:
    """Return the exact top-level Markdown set named by a bounded canonical Capsule."""
    return managed_document_filenames_from_root(parse_capsule(capsule_data))


def existing_managed_filenames(export_dir: Path) -> tuple[str, ...]:
    """Read the previous Capsule's bounded managed namespace, if one exists."""
    if export_dir.is_symlink():
        raise CapsuleIoError(f"export directory must not be a symlink: {export_dir}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(export_dir, flags)
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise CapsuleIoError(f"cannot open export directory safely: {export_dir}") from exc
    try:
        try:
            metadata = os.stat(CAPSULE_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return ()
        if not stat.S_ISREG(metadata.st_mode):
            raise CapsuleIoError("existing export Capsule must be a regular file")
        capsule = _read_regular_at(directory_fd, CAPSULE_FILENAME, MAX_CAPSULE_BYTES)
        return (CAPSULE_FILENAME, *managed_document_filenames(capsule))
    finally:
        os.close(directory_fd)


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


def read_export_files_with_capsule(
    export_dir: Path,
) -> tuple[dict[str, bytes], Mapping[str, JsonValue]]:
    """Read Capsule-bound files and return the already validated parsed Capsule."""
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
        root = parse_capsule(capsule)
        filenames = managed_document_filenames_from_root(root)
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
        return files, root
    except OSError as exc:
        raise CapsuleIoError(f"cannot inspect export directory safely: {export_dir}") from exc
    finally:
        os.close(directory_fd)


def read_export_files(export_dir: Path) -> dict[str, bytes]:
    """Read only Capsule-bound files; ignore real, top-level operator files/directories."""
    files, _ = read_export_files_with_capsule(export_dir)
    return files


def parse_capsule(data: bytes) -> Mapping[str, JsonValue]:
    """Load exact canonical JSON while rejecting duplicate keys and non-UTF-8 bytes."""
    if len(data) > MAX_CAPSULE_BYTES:
        raise CapsuleIoError("capsule exceeds the supported size limit")
    _validate_json_budget(data)
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
    except MemoryError as exc:
        raise CapsuleIoError("capsule JSON exceeds the available parser memory") from exc
    except RecursionError as exc:
        raise CapsuleIoError("capsule JSON nesting is too deep") from exc
    except ValueError as exc:
        raise CapsuleIoError("capsule JSON contains an invalid value") from exc
    return parsed
