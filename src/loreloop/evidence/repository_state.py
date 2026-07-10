"""Content-bound Git state captured alongside executable evidence."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

from ..knowledge.repos import load_repos
from ..paths import state_root


def capture_repository_states(workdir: Path) -> dict[str, dict[str, str | bool]]:
    repositories = {".": workdir.resolve(), **load_repos(workdir)}
    states: dict[str, dict[str, str | bool]] = {}
    for name, repo in repositories.items():
        if not repo.is_dir() or not (repo / ".git").exists():
            continue
        excluded_state = _state_relative_to_repo(repo, workdir)
        pathspecs = _content_pathspecs(excluded_state)
        head = _git(repo, "rev-parse", "HEAD").decode().strip()
        unstaged = _git(repo, "diff", "--binary", "--no-ext-diff", *pathspecs)
        staged = _git(repo, "diff", "--cached", "--binary", "--no-ext-diff", *pathspecs)
        material = bytearray(head.encode())
        material.extend(b"\0unstaged\0")
        material.extend(unstaged)
        material.extend(b"\0staged\0")
        material.extend(staged)
        untracked = _git(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        included_untracked = False
        for relpath in _nul_paths(untracked):
            if _is_within(relpath, excluded_state):
                continue
            candidate = repo / relpath
            try:
                info = candidate.lstat()
                candidate.resolve(strict=True).relative_to(repo.resolve())
            except (OSError, ValueError):
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            included_untracked = True
            material.extend(b"\0untracked\0")
            material.extend(os.fsencode(relpath))
            material.extend(hashlib.sha256(candidate.read_bytes()).digest())
        states[name] = {
            "head": head,
            "dirty": bool(unstaged or staged or included_untracked),
            "workspace_digest": hashlib.sha256(material).hexdigest(),
        }
    return states


def _state_relative_to_repo(repo: Path, workdir: Path) -> str | None:
    try:
        return state_root(workdir.resolve()).relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def _content_pathspecs(excluded_state: str | None) -> tuple[str, ...]:
    """Hash repository content while excluding LoreLoop's own mutable state.

    Command evidence writes an artifact and a chain record after the command
    finishes. Including ``.loreloop`` would therefore make every evidence
    record stale by construction in projects that have not run ``init`` yet
    (and thus do not have the state directory in ``.gitignore``).
    """
    if excluded_state is None:
        return ("--", ".")
    return (
        "--",
        ".",
        f":(exclude){excluded_state}",
        f":(exclude){excluded_state}/**",
    )


def _is_within(relpath: str, directory: str | None) -> bool:
    return directory is not None and (relpath == directory or relpath.startswith(f"{directory}/"))


def repository_states_match(expected: object, workdir: Path) -> tuple[bool, str]:
    if not isinstance(expected, dict):
        return False, "command evidence has no repository-state binding"
    current = capture_repository_states(workdir)
    if expected == current:
        return True, ""
    return False, "repository state changed after the command evidence was recorded"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


def _nul_paths(raw: bytes) -> list[str]:
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]
