"""Private content snapshot captured at task start for dirty-worktree-safe diffs."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from ..knowledge.repos import load_repos
from ..paths import state_root
from .model import SourceChange


def capture_task_source_snapshot(workdir: Path) -> dict[str, Any]:
    repositories = {".": workdir.resolve(), **load_repos(workdir)}
    captured: dict[str, Any] = {}
    for alias, repository in repositories.items():
        repository = repository.resolve()
        if not repository.is_dir() or not (repository / ".git").exists():
            continue
        files: dict[str, str] = {}
        excluded = _state_relative_to_repo(repository, workdir)
        listed = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=repository,
            capture_output=True,
            check=True,
        ).stdout
        for raw in listed.split(b"\0"):
            if not raw:
                continue
            relative = os.fsdecode(raw)
            if _is_within(relative, excluded):
                continue
            candidate = repository / relative
            try:
                info = candidate.lstat()
                candidate.resolve(strict=True).relative_to(repository)
            except (OSError, ValueError):
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            files[relative] = _sha256_file(candidate)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        captured[alias] = {"root": str(repository), "head": head, "files": files}
    return {"version": 1, "type": "task_source_snapshot", "repositories": captured}


def compare_task_source_snapshots(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[SourceChange, ...]:
    before_repositories = _repositories(before)
    after_repositories = _repositories(after)
    changes: list[SourceChange] = []
    for alias in sorted(set(before_repositories) | set(after_repositories)):
        old_files = _files(before_repositories.get(alias, {}))
        new_files = _files(after_repositories.get(alias, {}))
        for path in sorted(set(old_files) | set(new_files)):
            if path not in old_files:
                kind = "added"
            elif path not in new_files:
                kind = "deleted"
            elif old_files[path] != new_files[path]:
                kind = "modified"
            else:
                continue
            changes.append(SourceChange(alias, path, kind))
    return tuple(changes)


def _repositories(snapshot: dict[str, Any]) -> dict[str, Any]:
    repositories = snapshot.get("repositories")
    return repositories if isinstance(repositories, dict) else {}


def _files(repository: dict[str, Any]) -> dict[str, str]:
    files = repository.get("files") if isinstance(repository, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        path: digest
        for path, digest in files.items()
        if isinstance(path, str) and isinstance(digest, str)
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_relative_to_repo(repository: Path, workdir: Path) -> str | None:
    try:
        return state_root(workdir.resolve()).relative_to(repository).as_posix()
    except ValueError:
        return None


def _is_within(path: str, directory: str | None) -> bool:
    return directory is not None and (path == directory or path.startswith(f"{directory}/"))
