"""Declared git repositories that belong to one LoreLoop trust domain."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

from ..paths import state_path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RepoConfigError(Exception):
    pass


def validate_repo_name(name: str) -> str:
    if name == "." or not _NAME.fullmatch(name):
        raise RepoConfigError(f"invalid repository name: {name!r}")
    return name


def load_repos(workdir: Path) -> dict[str, Path]:
    path = state_path(workdir, "repos.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"version", "repos"}:
        raise RepoConfigError(f"invalid {path}: expected version and repos fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise RepoConfigError(f"invalid {path}: version must be 1")
    raw_repos = data["repos"]
    if not isinstance(raw_repos, dict):
        raise RepoConfigError(f"invalid {path}: repos must be an object")
    repos: dict[str, Path] = {}
    for raw_name, raw_path in raw_repos.items():
        if not isinstance(raw_name, str):
            raise RepoConfigError(f"invalid {path}: repository names must be strings")
        name = validate_repo_name(raw_name)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RepoConfigError(f"invalid {path}: path for {name!r} must be a non-empty string")
        candidate = Path(raw_path).expanduser()
        resolved = (candidate if candidate.is_absolute() else workdir / candidate).resolve()
        if not resolved.is_dir() or not (resolved / ".git").exists():
            raise RepoConfigError(f"invalid {path}: repository {name!r} is not a git root: {resolved}")
        repos[name] = resolved
    return repos


def save_repos(workdir: Path, repos: dict[str, Path]) -> None:
    normalized: dict[str, str] = {}
    for name, repo in sorted(repos.items()):
        validate_repo_name(name)
        resolved = repo.resolve()
        if not resolved.is_dir() or not (resolved / ".git").exists():
            raise RepoConfigError(f"repository {name!r} is not a git root: {resolved}")
        normalized[name] = str(resolved)
    path = state_path(workdir, "repos.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": 1, "repos": normalized}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def resolve_repo(workdir: Path, name: str) -> Path:
    if name == ".":
        return workdir.resolve()
    repos = load_repos(workdir)
    try:
        return repos[name]
    except KeyError as exc:
        raise RepoConfigError(f"repository {name!r} is not declared") from exc


def parse_code_locator(locator: str) -> tuple[str, str, str | None]:
    if not isinstance(locator, str) or not locator or locator != locator.strip():
        raise RepoConfigError(f"invalid code locator: {locator!r}")
    repo_name = "."
    rest = locator
    if locator.startswith("repo:"):
        prefix, separator, rest = locator[5:].partition("/")
        if not separator:
            raise RepoConfigError(f"invalid code locator: {locator!r}")
        repo_name = validate_repo_name(prefix)
    relpath, separator, commit = rest.rpartition("@")
    if not separator:
        relpath, commit = rest, None
    elif not commit:
        raise RepoConfigError(f"invalid code locator: {locator!r}")
    _validate_relpath(relpath, locator)
    if commit is not None and (commit != commit.strip() or any(c.isspace() for c in commit)):
        raise RepoConfigError(f"invalid code locator: {locator!r}")
    return repo_name, relpath, commit


def format_code_locator(repo_name: str, relpath: str, commit: str) -> str:
    if repo_name != ".":
        validate_repo_name(repo_name)
    _validate_relpath(relpath, relpath)
    if not commit or commit != commit.strip() or any(c.isspace() for c in commit):
        raise RepoConfigError(f"invalid commit in code locator: {commit!r}")
    prefix = "" if repo_name == "." else f"repo:{repo_name}/"
    return f"{prefix}{relpath}@{commit}"


def _validate_relpath(relpath: str, locator: str) -> None:
    path = PurePosixPath(relpath)
    if (
        not relpath
        or relpath.startswith("/")
        or "\\" in relpath
        or str(path) != relpath
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RepoConfigError(f"invalid code locator path: {locator!r}")
