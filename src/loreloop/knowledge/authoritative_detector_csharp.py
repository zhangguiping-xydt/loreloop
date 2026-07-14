"""Deterministic shallow detector for C# source files."""

from __future__ import annotations

import re
from typing import Final

from .authoritative_detector_common import mask_c_like_comments, source_ref
from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionReport,
    InterfaceRecord,
    SymbolRecord,
)

_TYPE: Final = re.compile(
    r"(?m)^\s*(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|"
    + r"sealed\s+|static\s+|partial\s+)*(?:class|interface|record|struct)\s+"
    + r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_METHOD: Final = re.compile(
    r"(?m)^\s*(?:\[[^\n]+\]\s*)*(?:public|private|protected|internal)\s+"
    + r"(?:static\s+|virtual\s+|override\s+|abstract\s+|sealed\s+|async\s+)*"
    + r"(?P<return>[A-Za-z_][\w.<>,?\[\] ]*)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    + r"\s*\((?P<params>[^)]*)\)"
)
_MINIMAL_ROUTE: Final = re.compile(
    r"\b[A-Za-z_][\w.]*\.Map(?P<method>Get|Post|Put|Patch|Delete)\s*"
    + r"\(\s*\"(?P<path>[^\"]+)\"\s*,\s*(?P<handler>[A-Za-z_][\w.]*)"
)
_HTTP_ATTRIBUTE: Final = re.compile(
    r"\[\s*Http(?P<method>Get|Post|Put|Patch|Delete|Head|Options)"
    + r"(?:\s*\(\s*\"(?P<path>[^\"]*)\"[^)]*\))?\s*\]"
)
_NEXT_METHOD: Final = re.compile(
    r"(?:(?:public|private|protected|internal|static|virtual|override|async|sealed)\s+)+"
    + r"[A-Za-z_][\w.<>,?\[\] ]*\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_CONTROLLER_PREFIX: Final = re.compile(
    r"\[\s*Route\s*\(\s*\"(?P<path>[^\"]*)\"\s*\)\s*\]"
    + r"(?:(?!\bclass\b).){0,400}\bclass\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)
_ENV: Final = re.compile(
    r"\bEnvironment\.GetEnvironmentVariable\s*\(\s*"
    + r"\"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\""
)
_USING: Final = re.compile(
    r"(?m)^\s*(?:global\s+)?using\s+(?:static\s+)?(?P<name>[A-Za-z_][\w.]*)\s*;"
)


def _join(prefix: str, suffix: str) -> str:
    combined = f"/{prefix.strip('/')}/{suffix.strip('/')}".replace("//", "/")
    return combined or "/"


def _interfaces(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    prefixes = tuple(_CONTROLLER_PREFIX.finditer(source))
    records = [
        InterfaceRecord(
            "http",
            match.group("handler"),
            match.group("method").upper(),
            match.group("path"),
            (),
            None,
            source_ref(alias, path, source, match.start()),
        )
        for match in _MINIMAL_ROUTE.finditer(source)
    ]
    for match in _HTTP_ATTRIBUTE.finditer(source):
        tail = source[match.end() : match.end() + 400]
        method = _NEXT_METHOD.search(tail)
        if method is None:
            continue
        preceding = [candidate for candidate in prefixes if candidate.end() <= match.start()]
        prefix = "" if not preceding else preceding[-1].group("path")
        if preceding:
            controller = preceding[-1].group("class")
            prefix = prefix.replace("[controller]", re.sub(r"Controller$", "", controller))
        records.append(
            InterfaceRecord(
                "http",
                method.group("name"),
                match.group("method").upper(),
                _join(prefix, match.group("path") or ""),
                (),
                None,
                source_ref(alias, path, source, match.start()),
            )
        )
    return tuple(records)


def detect_csharp_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract explicit C# symbols, ASP.NET routes, env reads, and namespace uses."""
    masked = mask_c_like_comments(source)
    symbols = [
        SymbolRecord(
            "class",
            match.group("name"),
            match.group("name"),
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _TYPE.finditer(masked)
    ]
    symbols.extend(
        SymbolRecord(
            "function",
            match.group("name"),
            f"{match.group('name')}({match.group('params').strip()})",
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _METHOD.finditer(masked)
    )
    configurations = tuple(
        ConfigurationRecord(
            match.group("key"),
            None,
            True,
            False,
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _ENV.finditer(masked)
    )
    dependencies = tuple(
        DependencyRecord(
            match.group("name"),
            None,
            "csharp_using",
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _USING.finditer(masked)
    )
    return DetectionReport(
        interfaces=_interfaces(masked, repository_alias, path),
        symbols=tuple(symbols),
        configurations=configurations,
        dependencies=dependencies,
    )
