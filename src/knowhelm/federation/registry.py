"""User-level registry of knowhelm trust domains."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..knowledge.repos import RepoConfigError, load_repos, validate_repo_name


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Project:
    project_id: str
    path: Path
    name: str
    aliases: list[str]
    tags: list[str]
    added_at: str


def registry_path() -> Path:
    configured = os.environ.get("KNOWHELM_REGISTRY")
    return Path(configured).expanduser() if configured else Path.home() / ".knowhelm/projects.json"


def load_projects() -> dict[str, Project]:
    path = registry_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"version", "projects"}:
        raise RegistryError(f"invalid {path}: expected version and projects fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise RegistryError(f"invalid {path}: version must be 1")
    raw_projects = data["projects"]
    if not isinstance(raw_projects, dict):
        raise RegistryError(f"invalid {path}: projects must be an object")
    projects: dict[str, Project] = {}
    for raw_id, raw in raw_projects.items():
        if not isinstance(raw_id, str):
            raise RegistryError(f"invalid {path}: project ids must be strings")
        try:
            project_id = validate_repo_name(raw_id)
        except Exception as exc:
            raise RegistryError(str(exc)) from exc
        projects[project_id] = _parse_project(path, project_id, raw)
    return projects


def save_projects(projects: dict[str, Project]) -> None:
    path = registry_path()
    payload: dict[str, dict[str, object]] = {}
    for project_id, project in sorted(projects.items()):
        try:
            validate_repo_name(project_id)
        except RepoConfigError as exc:
            raise RegistryError(str(exc)) from exc
        if project.project_id != project_id:
            raise RegistryError(f"project key {project_id!r} does not match its record")
        _validate_text(project.name, f"project {project_id!r} name")
        payload[project_id] = {
            "path": str(project.path.resolve()),
            "name": project.name,
            "aliases": _validate_text_list(project.aliases, "aliases"),
            "tags": _validate_text_list(project.tags, "tags"),
            "added_at": _validate_timestamp(project.added_at),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": 1, "projects": payload}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def add_project(
    project_path: Path,
    project_id: str | None = None,
    name: str | None = None,
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
) -> Project:
    resolved = project_path.expanduser().resolve()
    if not (resolved / ".knowhelm/knowledge.db").is_file():
        raise RegistryError(f"not a knowhelm trust domain: {resolved}")
    raw_id = project_id or resolved.name
    try:
        normalized_id = validate_repo_name(raw_id)
    except Exception as exc:
        raise RegistryError(str(exc)) from exc
    projects = load_projects()
    if normalized_id in projects:
        raise RegistryError(f"project id {normalized_id!r} already exists; choose --id")
    project = Project(
        project_id=normalized_id,
        path=resolved,
        name=_validate_text(name or resolved.name, "project name"),
        aliases=_validate_text_list(aliases or [], "aliases"),
        tags=_validate_text_list(tags or [], "tags"),
        added_at=datetime.now(timezone.utc).isoformat(),
    )
    projects[normalized_id] = project
    save_projects(projects)
    return project


def remove_project(project_id: str) -> Project:
    projects = load_projects()
    try:
        removed = projects.pop(project_id)
    except KeyError as exc:
        raise RegistryError(f"project {project_id!r} is not registered") from exc
    save_projects(projects)
    return removed


def list_projects() -> list[Project]:
    return list(load_projects().values())


def related_projects(current_workdir: Path) -> list[tuple[str, int]]:
    current = _member_paths(current_workdir, ".")
    related = []
    for project in load_projects().values():
        members = _member_paths(project.path, project.project_id)
        related.append((project.project_id, len(current & members)))
    return sorted(related, key=lambda item: (-item[1], item[0]))


def _parse_project(path: Path, project_id: str, raw: object) -> Project:
    required = {"path", "name", "aliases", "tags", "added_at"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise RegistryError(f"invalid {path}: project {project_id!r} has invalid fields")
    raw_path = raw["path"]
    if not isinstance(raw_path, str) or not raw_path or not Path(raw_path).is_absolute():
        raise RegistryError(f"invalid {path}: project {project_id!r} path must be absolute")
    return Project(
        project_id=project_id,
        path=Path(raw_path).resolve(),
        name=_validate_text(raw["name"], f"project {project_id!r} name"),
        aliases=_validate_text_list(raw["aliases"], f"project {project_id!r} aliases"),
        tags=_validate_text_list(raw["tags"], f"project {project_id!r} tags"),
        added_at=_validate_timestamp(raw["added_at"]),
    )


def _member_paths(workdir: Path, project_id: str) -> set[Path]:
    root = workdir.resolve()
    try:
        declared = load_repos(root)
    except RepoConfigError as exc:
        raise RegistryError(
            f"project {project_id!r} has invalid repository configuration: {exc}"
        ) from exc
    return {root, *(path.resolve() for path in declared.values())}


def _validate_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RegistryError(f"invalid {label}")
    return value


def _validate_text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise RegistryError(f"invalid {label}: expected a list")
    items = [_validate_text(item, label) for item in value]
    if len(set(items)) != len(items):
        raise RegistryError(f"invalid {label}: duplicate values")
    return items


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise RegistryError("invalid added_at timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RegistryError("invalid added_at timestamp") from exc
    if parsed.tzinfo is None:
        raise RegistryError("invalid added_at timestamp: timezone required")
    return value
