"""Deterministic, key-free Git snapshots for authoritative source export."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .authoritative_ids import CanonicalInput, canon_v4
from .authoritative_types import (
    GitObjectId,
    RepositorySnapshot,
    SnapshotEntry,
    SourceSnapshot,
)

_TREE_RECORD = re.compile(rb"(100644|100755|120000|160000) (blob|commit) ([0-9a-f]{40})\t")


class GitSnapshotError(RuntimeError):
    """A repository cannot produce one clean, stable source snapshot."""


@dataclass(frozen=True, slots=True)
class _RepositoryInput:
    alias: str
    role: Literal["root", "peer", "submodule"]
    path: Path
    expected_commit: str | None = None


def git_environment() -> dict[str, str]:
    """Return an environment where caller-controlled Git repository redirects are inert."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


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
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitSnapshotError(f"cannot inspect repository: {detail or 'git command failed'}")
    return completed.stdout


def read_blob_batch(repo: Path, object_ids: tuple[str, ...]) -> dict[str, bytes]:
    """Read immutable Git blobs without honoring caller repository redirects."""
    unique = tuple(dict.fromkeys(object_ids))
    if not unique:
        return {}
    raw = _git(
        repo,
        "cat-file",
        "--batch",
        input_data=b"".join(f"{object_id}\n".encode() for object_id in unique),
    )
    offset = 0
    blobs: dict[str, bytes] = {}
    for expected in unique:
        newline = raw.find(b"\n", offset)
        if newline < 0:
            raise GitSnapshotError("Git batch output lacks an object header")
        fields = raw[offset:newline].split()
        if len(fields) != 3 or fields[0].decode() != expected or fields[1] != b"blob":
            raise GitSnapshotError("Git batch output contains an unexpected object")
        try:
            size = int(fields[2])
        except ValueError as exc:
            raise GitSnapshotError("Git batch output has an invalid object length") from exc
        start = newline + 1
        end = start + size
        if end >= len(raw) or raw[end : end + 1] != b"\n":
            raise GitSnapshotError("Git batch output contains a truncated object")
        blobs[expected] = raw[start:end]
        offset = end + 1
    if offset != len(raw):
        raise GitSnapshotError("Git batch output contains trailing bytes")
    return blobs


def tree_shape(repo: Path) -> tuple[tuple[str, str, str], ...]:
    """Return the committed tree shape without honoring caller repository redirects."""
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    shape: list[tuple[str, str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        match = _TREE_RECORD.match(record)
        if match is None:
            raise GitSnapshotError("Git returned an unsupported tree record")
        path = os.fsdecode(record[match.end() :])
        if path == ".loreloop" or path.startswith(".loreloop/"):
            continue
        shape.append((path, match.group(1).decode(), match.group(3).decode()))
    return tuple(shape)


def _snapshot_entries(repo: Path) -> tuple[SnapshotEntry, ...]:
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


def git_common_dir_identity(path: Path) -> tuple[int, int]:
    """Return the physical Git common-directory identity for one checkout."""
    raw = _git(path, "rev-parse", "--git-common-dir").decode().strip()
    if not raw:
        raise GitSnapshotError(f"repository has no Git common directory: {path}")
    common = Path(raw)
    if not common.is_absolute():
        common = path / common
    try:
        metadata = common.resolve().stat()
    except OSError as exc:
        raise GitSnapshotError(f"cannot inspect Git common directory: {path}") from exc
    if metadata.st_dev < 0 or metadata.st_ino <= 0:
        raise GitSnapshotError(f"Git common directory has no stable local identity: {path}")
    return metadata.st_dev, metadata.st_ino


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
        env=git_environment(),
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


def _snapshot(repository: _RepositoryInput, *, require_clean: bool) -> RepositorySnapshot:
    repo = _root(repository.path, repository.alias)
    if require_clean:
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
    entries = _snapshot_entries(repo)
    snapshot = RepositorySnapshot(
        alias=repository.alias,
        role=repository.role,
        commit_id=GitObjectId.parse(f"sha1:{head}"),
        tree_id=GitObjectId.parse(f"sha1:{tree}"),
        index_sha256=hashlib.sha256(index).hexdigest(),
        entries=entries,
        repository_identity_sha256=repository_identity,
    )
    if require_clean:
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
    *,
    require_clean: bool = True,
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
    snapshots: list[RepositorySnapshot] = []
    repository_roots: set[Path] = set()
    common_directories: set[tuple[int, int]] = set()
    index = 0
    while index < len(inputs):
        item = inputs[index]
        resolved = _root(item.path, item.alias)
        common_identity = git_common_dir_identity(resolved)
        if resolved in repository_roots or common_identity in common_directories:
            raise GitSnapshotError("multiple aliases refer to the same repository")
        repository_roots.add(resolved)
        common_directories.add(common_identity)
        current = _snapshot(
            _RepositoryInput(item.alias, item.role, resolved, item.expected_commit),
            require_clean=require_clean,
        )
        snapshots.append(current)
        inputs.extend(_submodules(item, current))
        index += 1
    aliases = tuple(snapshot.alias for snapshot in snapshots)
    if len(aliases) != len(set(aliases)):
        raise GitSnapshotError("repository aliases are not unique")
    return SourceSnapshot(tuple(snapshots))


def _repository_snapshot_payload(repository: RepositorySnapshot) -> CanonicalInput:
    return {
        "alias": repository.alias,
        "role": repository.role,
        "identity": repository.repository_identity_sha256,
        "commit": repository.commit_id.hex,
        "tree": repository.tree_id.hex,
        "index": repository.index_sha256,
        "entries": [
            {
                "path": entry.path,
                "mode": entry.mode,
                "oid": entry.object_id.hex,
                "length": entry.byte_length,
                "digest": entry.blob_sha256,
            }
            for entry in repository.entries
        ],
    }


def _snapshot_payload(snapshot: SourceSnapshot) -> CanonicalInput:
    return [
        _repository_snapshot_payload(repository) for repository in snapshot.repositories
    ]


def repository_snapshot_sha256(repository: RepositorySnapshot) -> str:
    """Return an exact digest for one repository's committed snapshot."""
    return hashlib.sha256(
        b"loreloop-repository-source-snapshot-v1\0"
        + canon_v4(_repository_snapshot_payload(repository))
    ).hexdigest()


def source_snapshot_sha256(snapshot: SourceSnapshot) -> str:
    """Return the exact committed-source digest used by trusted attestations."""
    return hashlib.sha256(
        b"loreloop-source-snapshot-v1\0" + canon_v4(_snapshot_payload(snapshot))
    ).hexdigest()


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
        actual_shape = tree_shape(repo)
        if hashlib.sha256(index).hexdigest() != repository.index_sha256 or actual_shape != _expected_shape(repository):
            raise GitSnapshotError("project source changed after snapshot capture")
        prefix = "" if repository.alias == "." else f"{repository.alias}/"
        for entry in repository.entries:
            if entry.mode == "160000":
                paths[f"submodule:{prefix}{entry.path}"] = (repo / entry.path).resolve()
