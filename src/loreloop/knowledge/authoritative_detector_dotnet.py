"""Deterministic metadata detector for legacy .NET and NAnt/MSBuild files."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionReport,
    ImplementationFactRecord,
    SourceRef,
)
from .authoritative_redaction import redact_default

_OUTPUT_TYPE: Final = re.compile(r"<OutputType>\s*(?P<value>[^<]+?)\s*</OutputType>", re.I)
_TARGET_FRAMEWORK: Final = re.compile(
    r"<TargetFrameworkVersion>\s*(?P<value>[^<]+?)\s*</TargetFrameworkVersion>", re.I
)
_ASSEMBLY_NAME: Final = re.compile(r"<AssemblyName>\s*(?P<value>[^<]+?)\s*</AssemblyName>", re.I)
_REFERENCE: Final = re.compile(
    r"<(?:ProjectReference|Reference)\b[^>]*\bInclude\s*=\s*['\"](?P<value>[^'\"]+)['\"]",
    re.I,
)
_APPSETTING: Final = re.compile(
    r"<add\b[^>]*\bkey\s*=\s*['\"](?P<key>[^'\"]+)['\"][^>]*"
    r"\bvalue\s*=\s*['\"](?P<value>[^'\"]*)['\"][^>]*/?>",
    re.I,
)
_PROPERTY: Final = re.compile(
    r"<property\b[^>]*\bname\s*=\s*['\"](?P<key>[^'\"]+)['\"][^>]*"
    r"\bvalue\s*=\s*['\"](?P<value>[^'\"]*)['\"][^>]*/?>",
    re.I,
)
_TARGET: Final = re.compile(r"<target\b[^>]*\bname\s*=\s*['\"](?P<name>[^'\"]+)['\"]", re.I)
_SLN_PROJECT: Final = re.compile(
    r'^Project\("[^\"]+"\)\s*=\s*"(?P<name>[^\"]+)",\s*"(?P<path>[^\"]+)"',
    re.M,
)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _ref(alias: str, path: str, source: str, offset: int) -> SourceRef:
    return SourceRef(alias, path, _line(source, offset))


def _configuration(
    key: str, value: str | None, alias: str, path: str, source: str, offset: int
) -> ConfigurationRecord:
    portable, redacted = redact_default(key, value)
    return ConfigurationRecord(key, portable, value is None, redacted, _ref(alias, path, source, offset))


def detect_dotnet_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract project output, framework, references, AppSettings, and build targets."""
    name = PurePosixPath(path).name
    stem = PurePosixPath(path).stem
    lower = name.lower()
    configurations: list[ConfigurationRecord] = []
    dependencies: list[DependencyRecord] = []
    facts: list[ImplementationFactRecord] = []

    for pattern in (_APPSETTING, _PROPERTY):
        for match in pattern.finditer(source):
            configurations.append(
                _configuration(
                    match.group("key"),
                    match.group("value") or None,
                    repository_alias,
                    path,
                    source,
                    match.start(),
                )
            )
    framework = _TARGET_FRAMEWORK.search(source)
    if framework is not None:
        configurations.append(
            _configuration(
                "TargetFrameworkVersion",
                framework.group("value").strip(),
                repository_alias,
                path,
                source,
                framework.start(),
            )
        )
    assembly = _ASSEMBLY_NAME.search(source)
    subject = assembly.group("value").strip() if assembly is not None else stem
    output = _OUTPUT_TYPE.search(source)
    if output is not None:
        kind = output.group("value").strip().lower()
        label = {
            "winexe": "desktop executable",
            "exe": ".NET executable",
            "library": ".NET library",
        }.get(kind, f".NET output:{kind}")
        facts.append(
            ImplementationFactRecord(
                subject,
                "hosts",
                label,
                None,
                _ref(repository_alias, path, source, output.start()),
            )
        )
    for match in _REFERENCE.finditer(source):
        requirement = match.group("value").split(",", 1)[0].strip()
        dependencies.append(
            DependencyRecord(
                PurePosixPath(requirement.replace("\\", "/")).stem,
                requirement,
                "dotnet_reference",
                _ref(repository_alias, path, source, match.start()),
            )
        )
    for match in _SLN_PROJECT.finditer(source):
        dependencies.append(
            DependencyRecord(
                match.group("name"),
                match.group("path").replace("\\", "/"),
                "solution_project",
                _ref(repository_alias, path, source, match.start()),
            )
        )
    if lower.endswith(".xml"):
        for match in _TARGET.finditer(source):
            facts.append(
                ImplementationFactRecord(
                    stem,
                    "configures",
                    f"build target:{match.group('name')}",
                    None,
                    _ref(repository_alias, path, source, match.start()),
                )
            )
    return DetectionReport(
        configurations=tuple(configurations),
        dependencies=tuple(dependencies),
        implementation_facts=tuple(facts),
    )
