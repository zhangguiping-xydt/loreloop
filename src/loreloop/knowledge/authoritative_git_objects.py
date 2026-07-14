"""Compatibility wrappers for bounded authoritative Git object reads."""

from __future__ import annotations

from pathlib import Path

from .authoritative_git import (
    GitSnapshotError,
    read_blob_batch as _read_blob_batch,
    snapshot_entries as _snapshot_entries,
    tree_shape as _tree_shape,
)
from .authoritative_types import SnapshotEntry


class GitObjectError(RuntimeError):
    """Git returned malformed or unavailable immutable object data."""


def read_blob_batch(repo: Path, object_ids: tuple[str, ...]) -> dict[str, bytes]:
    """Read selected blobs through the bounded streaming implementation."""
    try:
        return _read_blob_batch(repo, object_ids)
    except GitSnapshotError as exc:
        raise GitObjectError(str(exc)) from exc


def tree_shape(repo: Path) -> tuple[tuple[str, str, str], ...]:
    """Return committed path, mode, and object identities."""
    try:
        return _tree_shape(repo)
    except GitSnapshotError as exc:
        raise GitObjectError(str(exc)) from exc


def snapshot_entries(repo: Path) -> tuple[SnapshotEntry, ...]:
    """Create content-bound entries without retaining whole-repository bytes."""
    try:
        return _snapshot_entries(repo)
    except GitSnapshotError as exc:
        raise GitObjectError(str(exc)) from exc
