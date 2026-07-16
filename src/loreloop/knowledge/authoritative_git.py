"""Deterministic, key-free Git snapshots for authoritative source export."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
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
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _git(
    repo: Path,
    *args: str,
    input_data: bytes | None = None,
    extra_environment: Mapping[str, str] | None = None,
) -> bytes:
    environment = git_environment()
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=environment,
        input=input_data,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitSnapshotError(f"cannot inspect repository: {detail or 'git command failed'}")
    return completed.stdout


def _stream_blob_batch(
    repo: Path,
    object_ids: tuple[str, ...],
    *,
    retain_bytes: bool,
    max_total_bytes: int | None = None,
) -> dict[str, tuple[int, str, bytes | None]]:
    unique = tuple(dict.fromkeys(object_ids))
    if not unique:
        return {}
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        env=git_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise GitSnapshotError("cannot open Git batch object streams")
    results: dict[str, tuple[int, str, bytes | None]] = {}
    total = 0
    try:
        for expected in unique:
            process.stdin.write(f"{expected}\n".encode())
            process.stdin.flush()
            fields = process.stdout.readline().split()
            if len(fields) != 3 or fields[0].decode() != expected or fields[1] != b"blob":
                raise GitSnapshotError("Git batch output contains an unexpected object")
            try:
                size = int(fields[2])
            except ValueError as exc:
                raise GitSnapshotError("Git batch output has an invalid object length") from exc
            total += size
            if max_total_bytes is not None and total > max_total_bytes:
                raise GitSnapshotError("selected semantic blobs exceed the total byte limit")
            digest = hashlib.sha256()
            chunks: list[bytes] | None = [] if retain_bytes else None
            remaining = size
            while remaining:
                chunk = process.stdout.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise GitSnapshotError("Git batch output contains a truncated object")
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
                remaining -= len(chunk)
            if process.stdout.read(1) != b"\n":
                raise GitSnapshotError("Git batch output lacks an object terminator")
            data = b"".join(chunks) if chunks is not None else None
            results[expected] = (size, digest.hexdigest(), data)
        process.stdin.close()
        return_code = process.wait()
        detail = process.stderr.read().decode("utf-8", errors="replace").strip()
        if return_code != 0:
            raise GitSnapshotError(
                f"cannot inspect repository blobs: {detail or 'git cat-file failed'}"
            )
        return results
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
        if not process.stdin.closed:
            process.stdin.close()


def read_blob_batch(
    repo: Path,
    object_ids: tuple[str, ...],
    *,
    max_total_bytes: int | None = None,
) -> dict[str, bytes]:
    """Read selected immutable Git blobs with a bounded streaming batch process."""
    results = _stream_blob_batch(
        repo,
        object_ids,
        retain_bytes=True,
        max_total_bytes=max_total_bytes,
    )
    return {object_id: data for object_id, (_, _, data) in results.items() if data is not None}


def blob_metadata_batch(repo: Path, object_ids: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """Hash arbitrary-size committed blobs without retaining their bytes."""
    results = _stream_blob_batch(repo, object_ids, retain_bytes=False)
    return {object_id: (size, digest) for object_id, (size, digest, _) in results.items()}


def tree_shape(repo: Path, treeish: str = "HEAD") -> tuple[tuple[str, str, str], ...]:
    """Return one captured tree shape without honoring caller repository redirects."""
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", treeish)
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


def snapshot_entries(repo: Path, treeish: str = "HEAD") -> tuple[SnapshotEntry, ...]:
    shape = tree_shape(repo, treeish)
    blob_ids = tuple(object_id for _, mode, object_id in shape if mode != "160000")
    metadata = blob_metadata_batch(repo, blob_ids)
    entries: list[SnapshotEntry] = []
    for path, mode, object_id in shape:
        oid = GitObjectId.parse(f"sha1:{object_id}")
        if mode == "160000":
            entries.append(SnapshotEntry(path, "160000", oid, None, None))
            continue
        byte_length, blob_sha256 = metadata[object_id]
        typed_mode: Literal["100644", "100755", "120000"]
        if mode == "100644":
            typed_mode = "100644"
        elif mode == "100755":
            typed_mode = "100755"
        else:
            typed_mode = "120000"
        entries.append(SnapshotEntry(path, typed_mode, oid, byte_length, blob_sha256))
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


def _excluded(path: str, excluded_paths: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in excluded_paths)


def _safe_filter_overrides(repo: Path) -> tuple[str, ...]:
    """Disable repository-local content filters while inspecting mutable files."""
    keys = tuple(
        os.fsdecode(item)
        for item in _git(repo, "config", "--local", "--name-only", "--null", "--list").split(b"\0")
        if item
    )
    drivers = {
        match.group(1)
        for key in keys
        if (match := re.fullmatch(r"filter\.(.+)\.(?:clean|process|required)", key))
    }
    return tuple(
        argument
        for driver in sorted(drivers)
        for argument in (
            "-c",
            f"filter.{driver}.clean=",
            "-c",
            f"filter.{driver}.process=",
            "-c",
            f"filter.{driver}.required=false",
        )
    )


def _changes(repo: Path, excluded_paths: tuple[str, ...] = ()) -> tuple[tuple[str, str], ...]:
    safe_filters = _safe_filter_overrides(repo)
    commands = (
        ("unstaged", (*safe_filters, "diff", "--name-only", "-z")),
        ("staged", (*safe_filters, "diff", "--cached", "--name-only", "-z")),
        ("untracked", ("ls-files", "--others", "--exclude-standard", "-z")),
    )
    return tuple(
        (state, path)
        for state, command in commands
        for path in _source_paths(_git(repo, *command))
        if not _excluded(path, excluded_paths)
    )


def _worktree_state_sha256(changes: tuple[tuple[str, str], ...]) -> str:
    return hashlib.sha256(
        b"loreloop-worktree-state-v1\0"
        + canon_v4([{"state": state, "path": path} for state, path in changes])
    ).hexdigest()


def _render_changes(changes: tuple[tuple[str, str], ...], *, limit: int = 20) -> str:
    visible = changes[:limit]
    detail = ", ".join(f"{state}:{path}" for state, path in visible)
    if len(changes) > limit:
        detail += f", ... (+{len(changes) - limit} more)"
    return detail


def _clean(repo: Path, alias: str, excluded_paths: tuple[str, ...] = ()) -> None:
    changes = _changes(repo, excluded_paths)
    if changes:
        raise GitSnapshotError(
            f"repository {alias!r} has uncommitted source changes: {_render_changes(changes)}"
        )


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


def _working_tree_material(
    repo: Path,
    excluded_paths: tuple[str, ...],
) -> tuple[str, bytes, tuple[SnapshotEntry, ...], str]:
    changes_before = _changes(repo, excluded_paths)
    paths = tuple(
        dict.fromkeys(
            path
            for path in _source_paths(
                _git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
            )
            if not _excluded(path, excluded_paths)
        )
    )
    index_records: list[bytes] = []
    regular_paths: list[str] = []
    regular_modes: dict[str, str] = {}
    for path in paths:
        source = repo / path
        try:
            metadata = source.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(metadata.st_mode):
            regular_paths.append(path)
            regular_modes[path] = "100755" if metadata.st_mode & 0o111 else "100644"
            continue
        if stat.S_ISLNK(metadata.st_mode):
            object_id = (
                _git(
                    repo,
                    "hash-object",
                    "-w",
                    "--stdin",
                    input_data=os.fsencode(os.readlink(source)),
                )
                .decode()
                .strip()
            )
            index_records.append(
                b"120000 " + object_id.encode() + b"\t" + os.fsencode(path) + b"\0"
            )
            continue
        if stat.S_ISDIR(metadata.st_mode):
            object_id = _git(source, "rev-parse", "HEAD").decode().strip()
            index_records.append(
                b"160000 " + object_id.encode() + b"\t" + os.fsencode(path) + b"\0"
            )
            continue
        raise GitSnapshotError(f"unsupported working-tree file type: {path}")
    for offset in range(0, len(regular_paths), 200):
        batch = regular_paths[offset : offset + 200]
        object_ids = (
            _git(
                repo,
                "hash-object",
                "-w",
                "--no-filters",
                "--",
                *batch,
            )
            .decode()
            .splitlines()
        )
        if len(object_ids) != len(batch):
            raise GitSnapshotError("Git returned an incomplete working-tree blob set")
        index_records.extend(
            regular_modes[path].encode()
            + b" "
            + object_id.encode()
            + b"\t"
            + os.fsencode(path)
            + b"\0"
            for path, object_id in zip(batch, object_ids, strict=True)
        )
    with tempfile.TemporaryDirectory(prefix="loreloop-index-") as temporary:
        index_path = Path(temporary) / "index"
        environment = {"GIT_INDEX_FILE": str(index_path)}
        _git(repo, "read-tree", "--empty", extra_environment=environment)
        if index_records:
            _git(
                repo,
                "update-index",
                "-z",
                "--index-info",
                input_data=b"".join(index_records),
                extra_environment=environment,
            )
        tree = _git(repo, "write-tree", extra_environment=environment).decode().strip()
        index = _source_index(
            _git(repo, "ls-files", "--stage", "-z", extra_environment=environment)
        )
    changes_after = _changes(repo, excluded_paths)
    if changes_after != changes_before:
        raise GitSnapshotError("repository changed during working-tree snapshot capture")
    return (
        tree,
        index,
        snapshot_entries(repo, tree),
        _worktree_state_sha256(changes_before),
    )


def _snapshot(
    repository: _RepositoryInput,
    *,
    require_clean: bool,
    working_tree: bool,
    excluded_paths: tuple[str, ...],
) -> RepositorySnapshot:
    repo = _root(repository.path, repository.alias)
    if require_clean and not working_tree:
        _clean(repo, repository.alias, excluded_paths)
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    if repository.expected_commit is not None and head != repository.expected_commit:
        raise GitSnapshotError(f"submodule {repository.alias!r} does not match its gitlink commit")
    if working_tree:
        tree, index, entries, worktree_state = _working_tree_material(repo, excluded_paths)
    else:
        tree = _git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
        index = _source_index(_git(repo, "ls-files", "--stage", "-z"))
        entries = snapshot_entries(repo)
        worktree_state = None
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
    snapshot = RepositorySnapshot(
        alias=repository.alias,
        role=repository.role,
        commit_id=GitObjectId.parse(f"sha1:{head}"),
        tree_id=GitObjectId.parse(f"sha1:{tree}"),
        index_sha256=hashlib.sha256(index).hexdigest(),
        entries=entries,
        repository_identity_sha256=repository_identity,
        snapshot_kind="working_tree" if working_tree else "commit",
        worktree_state_sha256=worktree_state,
        excluded_paths=excluded_paths,
    )
    if require_clean and not working_tree:
        _clean(repo, repository.alias, excluded_paths)
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
    working_tree: bool = False,
    excluded_paths: Mapping[str, tuple[str, ...]] | None = None,
) -> SourceSnapshot:
    """Capture a Git root or a declared aggregate workspace, plus submodules."""
    if working_tree and not require_clean:
        raise GitSnapshotError("working-tree capture cannot disable stability checks")
    peer_items = sorted((peers or {}).items())
    exclusions = excluded_paths or {}
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
            working_tree=working_tree,
            excluded_paths=tuple(sorted(exclusions.get(item.alias, ()))),
        )
        snapshots.append(current)
        inputs.extend(_submodules(item, current))
        index += 1
    aliases = tuple(snapshot.alias for snapshot in snapshots)
    if len(aliases) != len(set(aliases)):
        raise GitSnapshotError("repository aliases are not unique")
    return SourceSnapshot(tuple(snapshots))


def _repository_snapshot_payload(repository: RepositorySnapshot) -> CanonicalInput:
    payload: dict[str, CanonicalInput] = {
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
    if repository.snapshot_kind == "working_tree":
        payload["snapshot_kind"] = repository.snapshot_kind
        payload["worktree_state"] = repository.worktree_state_sha256
    return payload


def _snapshot_payload(snapshot: SourceSnapshot) -> CanonicalInput:
    return [_repository_snapshot_payload(repository) for repository in snapshot.repositories]


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
    kinds = {repository.snapshot_kind for repository in expected.repositories}
    if len(kinds) != 1:
        raise GitSnapshotError("source snapshot mixes committed and working-tree repositories")
    exclusions = {
        repository.alias: repository.excluded_paths
        for repository in expected.repositories
        if repository.excluded_paths
    }
    current = capture_source_snapshot(
        root,
        peers,
        working_tree=kinds == {"working_tree"},
        excluded_paths=exclusions,
    )
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
    kinds = {repository.snapshot_kind for repository in expected.repositories}
    if kinds == {"working_tree"}:
        exclusions = {
            repository.alias: repository.excluded_paths
            for repository in expected.repositories
            if repository.excluded_paths
        }
        current = capture_source_snapshot(
            root,
            peers,
            working_tree=True,
            excluded_paths=exclusions,
        )
        if current != expected:
            raise GitSnapshotError("project working tree changed after snapshot capture")
        return
    if kinds != {"commit"}:
        raise GitSnapshotError("source snapshot mixes committed and working-tree repositories")
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
        _clean(repo, repository.alias, repository.excluded_paths)
        if _git(repo, "rev-parse", "HEAD").decode().strip() != repository.commit_id.git_sha1_hex():
            raise GitSnapshotError("project source changed after snapshot capture")
        if (
            _git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
            != repository.tree_id.git_sha1_hex()
        ):
            raise GitSnapshotError("project source changed after snapshot capture")
        index = _source_index(_git(repo, "ls-files", "--stage", "-z"))
        actual_shape = tree_shape(repo)
        if hashlib.sha256(
            index
        ).hexdigest() != repository.index_sha256 or actual_shape != _expected_shape(repository):
            raise GitSnapshotError("project source changed after snapshot capture")
        prefix = "" if repository.alias == "." else f"{repository.alias}/"
        for entry in repository.entries:
            if entry.mode == "160000":
                paths[f"submodule:{prefix}{entry.path}"] = (repo / entry.path).resolve()
