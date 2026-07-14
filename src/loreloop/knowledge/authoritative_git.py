"""Deterministic, key-free Git snapshots for authoritative source export."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .authoritative_git_objects import GitObjectError, snapshot_entries, tree_shape
from .authoritative_types import GitObjectId, RepositorySnapshot, SourceSnapshot


class GitSnapshotError(RuntimeError):
    """A repository cannot produce one clean, stable source snapshot."""


@dataclass(frozen=True, slots=True)
class _RepositoryInput:
    alias: str
    role: Literal["root", "peer", "submodule"]
    path: Path
    expected_commit: str | None = None


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitSnapshotError(f"cannot inspect repository: {detail or 'git command failed'}")
    return completed.stdout


def _root(path: Path, alias: str) -> Path:
    resolved = path.expanduser().resolve()
    output = _git(resolved, "rev-parse", "--show-toplevel").decode().strip()
    actual = Path(output).resolve()
    if actual != resolved:
        raise GitSnapshotError(f"repository {alias!r} is not a Git root: {resolved}")
    return resolved


def _root_if_repository(path: Path, alias: str) -> Path | None:
    """Return a Git root, or None when an aggregate workspace is not a repository."""
    resolved = path.expanduser().resolve()
    if (resolved / ".git").exists() or (resolved / ".git").is_symlink():
        return _root(resolved, alias)
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=resolved,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    output = completed.stdout.decode("utf-8", errors="replace").strip()
    actual = Path(output).resolve()
    if actual != resolved:
        raise GitSnapshotError(f"repository {alias!r} is not a Git root: {resolved}")
    return resolved


def _clean(repo: Path, alias: str) -> None:
    changed = (
        _git(repo, "diff", "--name-only", "-z"),
        _git(repo, "diff", "--cached", "--name-only", "-z"),
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z"),
    )
    if any(_source_paths(output) for output in changed):
        raise GitSnapshotError(f"repository {alias!r} has uncommitted source changes")


def _is_source_path(path: str) -> bool:
    return path != ".loreloop" and not path.startswith(".loreloop/")


def _source_paths(raw: bytes) -> tuple[str, ...]:
    return tuple(
        path for item in raw.split(b"\0") if item if _is_source_path(path := os.fsdecode(item))
    )


def _source_index(raw: bytes) -> bytes:
    records: list[bytes] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        separator = record.find(b"\t")
        if separator < 0:
            raise GitSnapshotError("Git returned an unsupported index record")
        if _is_source_path(os.fsdecode(record[separator + 1 :])):
            records.append(record)
    return b"\0".join(records) + (b"\0" if records else b"")


def _snapshot(repository: _RepositoryInput) -> RepositorySnapshot:
    repo = _root(repository.path, repository.alias)
    _clean(repo, repository.alias)
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    if repository.expected_commit is not None and head != repository.expected_commit:
        raise GitSnapshotError(f"submodule {repository.alias!r} does not match its gitlink commit")
    tree = _git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    roots = tuple(
        sorted(
            root
            for line in _git(repo, "rev-list", "--max-parents=0", "HEAD").decode().splitlines()
            if (root := line.strip())
        )
    )
    if not roots:
        raise GitSnapshotError(f"repository {repository.alias!r} has no root commit")
    repository_identity = hashlib.sha256(
        b"loreloop-git-roots-v1\0" + b"\0".join(root.encode("ascii") for root in roots)
    ).hexdigest()
    index = _source_index(_git(repo, "ls-files", "--stage", "-z"))
    try:
        entries = snapshot_entries(repo)
    except GitObjectError as exc:
        raise GitSnapshotError(str(exc)) from exc
    snapshot = RepositorySnapshot(
        alias=repository.alias,
        role=repository.role,
        commit_id=GitObjectId.parse(f"sha1:{head}"),
        tree_id=GitObjectId.parse(f"sha1:{tree}"),
        index_sha256=hashlib.sha256(index).hexdigest(),
        entries=entries,
        repository_identity_sha256=repository_identity,
    )
    _clean(repo, repository.alias)
    if _git(repo, "rev-parse", "HEAD").decode().strip() != head:
        raise GitSnapshotError(f"repository {repository.alias!r} changed during snapshot capture")
    return snapshot


def _submodules(
    parent: _RepositoryInput, snapshot: RepositorySnapshot
) -> tuple[_RepositoryInput, ...]:
    children: list[_RepositoryInput] = []
    for entry in snapshot.entries:
        if entry.mode != "160000":
            continue
        prefix = "" if parent.alias == "." else f"{parent.alias}/"
        alias = f"submodule:{prefix}{entry.path}"
        children.append(
            _RepositoryInput(
                alias=alias,
                role="submodule",
                path=parent.path / entry.path,
                expected_commit=entry.object_id.git_sha1_hex(),
            )
        )
    return tuple(children)


def capture_source_snapshot(
    root: Path,
    peers: Mapping[str, Path] | None = None,
) -> SourceSnapshot:
    """Capture a Git root or a declared aggregate workspace, plus submodules."""
    peer_items = sorted((peers or {}).items())
    root_repository = _root_if_repository(root, ".")
    inputs: list[_RepositoryInput] = []
    if root_repository is not None:
        inputs.append(_RepositoryInput(".", "root", root_repository))
    elif not peer_items:
        raise GitSnapshotError(
            "project root is not a Git repository and no declared Git repositories are available"
        )
    inputs.extend(_RepositoryInput(alias, "peer", path) for alias, path in peer_items)
    resolved = tuple(_root(item.path, item.alias) for item in inputs)
    if len(resolved) != len(set(resolved)):
        raise GitSnapshotError("multiple aliases refer to the same repository")
    snapshots: list[RepositorySnapshot] = []
    index = 0
    while index < len(inputs):
        item = inputs[index]
        current = _snapshot(item)
        snapshots.append(current)
        inputs.extend(_submodules(item, current))
        index += 1
    aliases = tuple(snapshot.alias for snapshot in snapshots)
    if len(aliases) != len(set(aliases)):
        raise GitSnapshotError("repository aliases are not unique")
    return SourceSnapshot(tuple(snapshots))


def verify_source_snapshot(
    expected: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None = None,
) -> None:
    """Fail when any repository differs from a previously captured snapshot."""
    current = capture_source_snapshot(root, peers)
    if current != expected:
        raise GitSnapshotError("project source changed after snapshot capture")


def _expected_shape(repository: RepositorySnapshot) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (entry.path, entry.mode, entry.object_id.git_sha1_hex()) for entry in repository.entries
    )


def verify_source_snapshot_metadata(
    expected: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None = None,
) -> None:
    """Verify mutable Git state without re-reading every immutable blob."""
    has_root = any(repository.alias == "." for repository in expected.repositories)
    current_root = _root_if_repository(root, ".")
    if has_root != (current_root is not None):
        raise GitSnapshotError("project repository topology changed after snapshot capture")
    inputs = [_RepositoryInput(".", "root", root)] if has_root else []
    inputs.extend(
        _RepositoryInput(alias, "peer", path) for alias, path in sorted((peers or {}).items())
    )
    paths = {item.alias: item.path.resolve() for item in inputs}
    for repository in expected.repositories:
        repo = paths.get(repository.alias)
        if repo is None:
            raise GitSnapshotError(f"snapshot repository {repository.alias!r} has no source path")
        _clean(repo, repository.alias)
        if _git(repo, "rev-parse", "HEAD").decode().strip() != repository.commit_id.git_sha1_hex():
            raise GitSnapshotError("project source changed after snapshot capture")
        if _git(repo, "rev-parse", "HEAD^{tree}").decode().strip() != repository.tree_id.git_sha1_hex():
            raise GitSnapshotError("project source changed after snapshot capture")
        index = _source_index(_git(repo, "ls-files", "--stage", "-z"))
        try:
            actual_shape = tree_shape(repo)
        except GitObjectError as exc:
            raise GitSnapshotError(str(exc)) from exc
        if hashlib.sha256(index).hexdigest() != repository.index_sha256 or actual_shape != _expected_shape(repository):
            raise GitSnapshotError("project source changed after snapshot capture")
        prefix = "" if repository.alias == "." else f"{repository.alias}/"
        for entry in repository.entries:
            if entry.mode == "160000":
                paths[f"submodule:{prefix}{entry.path}"] = (repo / entry.path).resolve()
