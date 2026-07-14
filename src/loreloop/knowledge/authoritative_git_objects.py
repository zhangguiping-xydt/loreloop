"""Batch Git object reads used by authoritative snapshots."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from .authoritative_git import git_environment
from .authoritative_types import GitObjectId, SnapshotEntry

_TREE_RECORD = re.compile(rb"(100644|100755|120000|160000) (blob|commit) ([0-9a-f]{40})\t")


class GitObjectError(RuntimeError):
    """Git returned malformed or unavailable immutable object data."""


def _git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=git_environment(),
        input=input_data,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise GitObjectError("Git object read failed")
    return completed.stdout


def read_blob_batch(repo: Path, object_ids: tuple[str, ...]) -> dict[str, bytes]:
    """Read all requested blobs through one `git cat-file --batch` process."""
    unique = tuple(dict.fromkeys(object_ids))
    if not unique:
        return {}
    raw = _git(repo, "cat-file", "--batch", input_data=b"".join(f"{oid}\n".encode() for oid in unique))
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected in unique:
        newline = raw.find(b"\n", offset)
        if newline < 0:
            raise GitObjectError("Git batch output lacks an object header")
        fields = raw[offset:newline].split()
        if len(fields) != 3 or fields[0].decode() != expected or fields[1] != b"blob":
            raise GitObjectError("Git batch output contains an unexpected object")
        try:
            size = int(fields[2])
        except ValueError as exc:
            raise GitObjectError("Git batch output has an invalid object length") from exc
        start = newline + 1
        end = start + size
        if end >= len(raw) or raw[end : end + 1] != b"\n":
            raise GitObjectError("Git batch output contains a truncated object")
        blobs[expected] = raw[start:end]
        offset = end + 1
    if offset != len(raw):
        raise GitObjectError("Git batch output contains trailing bytes")
    return blobs


def tree_shape(repo: Path) -> tuple[tuple[str, str, str], ...]:
    """Return path, mode, and object ID without reading blob contents."""
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    shape: list[tuple[str, str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        match = _TREE_RECORD.match(record)
        if match is None:
            raise GitObjectError("Git returned an unsupported tree record")
        path = os.fsdecode(record[match.end() :])
        if path == ".loreloop" or path.startswith(".loreloop/"):
            continue
        shape.append((path, match.group(1).decode(), match.group(3).decode()))
    return tuple(shape)


def snapshot_entries(repo: Path) -> tuple[SnapshotEntry, ...]:
    """Create content-bound entries with one batch blob read per repository."""
    shape = tree_shape(repo)
    blob_ids = tuple(object_id for _, mode, object_id in shape if mode != "160000")
    blobs = read_blob_batch(repo, blob_ids)
    entries: list[SnapshotEntry] = []
    for path, mode, object_id in shape:
        oid = GitObjectId.parse(f"sha1:{object_id}")
        if mode == "160000":
            entries.append(SnapshotEntry(path, "160000", oid, None, None))
            continue
        data = blobs[object_id]
        typed_mode: Literal["100644", "100755", "120000"]
        if mode == "100644":
            typed_mode = "100644"
        elif mode == "100755":
            typed_mode = "100755"
        else:
            typed_mode = "120000"
        entries.append(
            SnapshotEntry(path, typed_mode, oid, len(data), hashlib.sha256(data).hexdigest())
        )
    return tuple(entries)
