"""Deterministic dependency and environment configuration detectors."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Mapping
from typing import TypeAlias

from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionError,
    DetectionReport,
    SourceRef,
)
from .authoritative_redaction import redact_default

ConfigScalar: TypeAlias = None | bool | int | float | str
ConfigValue: TypeAlias = ConfigScalar | list["ConfigValue"] | dict[str, "ConfigValue"]
JSON_LOADS: Callable[[str], ConfigValue] = json.loads
TOML_LOADS: Callable[[str], ConfigValue] = tomllib.loads
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _mapping(value: ConfigValue | None, label: str) -> Mapping[str, ConfigValue]:
    if not isinstance(value, dict):
        raise DetectionError(f"{label} must be an object")
    return value


def _text(value: ConfigValue | None, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DetectionError(f"{label} must be text")
    return value


def _requirement(requirement: str, scope: str, source: SourceRef) -> DependencyRecord:
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise DetectionError(f"invalid dependency requirement: {requirement}")
    return DependencyRecord(match.group(1), requirement, scope, source)


def _pyproject(text: str, alias: str, path: str) -> DetectionReport:
    try:
        root = _mapping(TOML_LOADS(text), "pyproject")
    except tomllib.TOMLDecodeError as exc:
        raise DetectionError(f"invalid TOML source: {path}") from exc
    project_value = root.get("project")
    if project_value is None:
        return DetectionReport()
    project = _mapping(project_value, "project")
    dependencies: list[DependencyRecord] = []
    source = SourceRef(alias, path, 1)
    raw_dependencies = project.get("dependencies", [])
    if not isinstance(raw_dependencies, list):
        raise DetectionError("project dependencies must be an array")
    dependencies.extend(
        _requirement(_text(item, "dependency"), "runtime", source) for item in raw_dependencies
    )
    optional_value = project.get("optional-dependencies", {})
    optional = _mapping(optional_value, "optional dependencies")
    for group, raw_group in sorted(optional.items()):
        if not isinstance(raw_group, list):
            raise DetectionError(f"optional dependency group {group} must be an array")
        dependencies.extend(
            _requirement(_text(item, "dependency"), f"optional:{group}", source)
            for item in raw_group
        )
    return DetectionReport(dependencies=tuple(dependencies))


def _package_json(text: str, alias: str, path: str) -> DetectionReport:
    try:
        root = _mapping(JSON_LOADS(text), "package.json")
    except json.JSONDecodeError as exc:
        raise DetectionError(f"invalid JSON source: {path}") from exc
    dependencies: list[DependencyRecord] = []
    source = SourceRef(alias, path, 1)
    for field, scope in (("dependencies", "runtime"), ("devDependencies", "development")):
        raw = root.get(field)
        if raw is None:
            continue
        values = _mapping(raw, field)
        dependencies.extend(
            DependencyRecord(name, _text(requirement, name), scope, source)
            for name, requirement in sorted(values.items())
        )
    return DetectionReport(dependencies=tuple(dependencies))


def _environment(text: str, alias: str, path: str) -> DetectionReport:
    records: list[ConfigurationRecord] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator != "=" or _ENV_KEY.fullmatch(key) is None:
            raise DetectionError(f"invalid environment declaration at {path}:{line_number}")
        default = value.strip() or None
        portable, redacted = redact_default(key, default)
        records.append(
            ConfigurationRecord(
                key,
                portable,
                default is None,
                redacted,
                SourceRef(alias, path, line_number),
            )
        )
    return DetectionReport(configurations=tuple(records))


def _requirements(text: str, alias: str, path: str) -> DetectionReport:
    dependencies = tuple(
        _requirement(line, "runtime", SourceRef(alias, path, line_number))
        for line_number, raw in enumerate(text.splitlines(), 1)
        if (line := raw.strip()) and not line.startswith("#") and not line.startswith("-")
    )
    return DetectionReport(dependencies=dependencies)


def detect_config_source(text: str, repository_alias: str, path: str) -> DetectionReport:
    """Detect supported project manifests and environment templates by path."""
    name = path.rsplit("/", 1)[-1].lower()
    if name == "pyproject.toml":
        return _pyproject(text, repository_alias, path)
    if name == "package.json":
        return _package_json(text, repository_alias, path)
    if name in {".env", ".env.example", ".env.sample"} or name.endswith(".env.example"):
        return _environment(text, repository_alias, path)
    if name in {"requirements.txt", "requirements-dev.txt"}:
        return _requirements(text, repository_alias, path)
    return DetectionReport()
