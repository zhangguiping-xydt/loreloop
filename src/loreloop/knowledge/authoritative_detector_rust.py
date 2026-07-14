"""Deterministic shallow detector for Rust source files."""

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
    r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+"
    + r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_FUNCTION: Final = re.compile(
    r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?P<async>async\s+)?"
    + r"(?:unsafe\s+)?(?:extern\s+\"[^\"]+\"\s+)?fn\s+"
    + r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]+>)?\s*\((?P<params>[^)]*)\)"
)
_ATTRIBUTE_ROUTE: Final = re.compile(
    r"#\[\s*(?P<method>get|post|put|patch|delete|head|options)\s*"
    + r"\(\s*\"(?P<path>[^\"]+)\"[^)]*\)\s*\]"
    + r"(?:(?!\bfn\b).){0,300}\b(?:pub\s+)?(?:async\s+)?fn\s+"
    + r"(?P<handler>[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)
_AXUM_ROUTE: Final = re.compile(
    r"\.route\s*\(\s*\"(?P<path>[^\"]+)\"\s*,\s*"
    + r"(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*"
    + r"(?P<handler>[A-Za-z_][A-Za-z0-9_:]*)"
)
_ENV: Final = re.compile(
    r"\b(?:(?:std::)?env)::var(?:_os)?\s*\(\s*\"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\""
)
_USE: Final = re.compile(
    r"(?m)^\s*(?:pub\s+)?use\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)::"
)
_EXTERN: Final = re.compile(r"(?m)^\s*extern\s+crate\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_LOCAL_ROOTS: Final = frozenset({"crate", "self", "super", "std", "core", "alloc"})


def _interfaces(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    matches = [*_ATTRIBUTE_ROUTE.finditer(source), *_AXUM_ROUTE.finditer(source)]
    matches.sort(key=lambda match: match.start())
    return tuple(
        InterfaceRecord(
            "http",
            match.group("handler"),
            match.group("method").upper(),
            match.group("path"),
            (),
            None,
            source_ref(alias, path, source, match.start()),
        )
        for match in matches
    )


def _dependencies(source: str, alias: str, path: str) -> tuple[DependencyRecord, ...]:
    matches = [*_USE.finditer(source), *_EXTERN.finditer(source)]
    matches.sort(key=lambda match: match.start())
    return tuple(
        DependencyRecord(
            match.group("name"),
            None,
            "rust_use",
            source_ref(alias, path, source, match.start()),
        )
        for match in matches
        if match.group("name") not in _LOCAL_ROOTS
    )


def detect_rust_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract explicit Rust symbols, HTTP registrations, env reads, and crate uses."""
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
            "async_function" if match.group("async") else "function",
            match.group("name"),
            f"{match.group('name')}({match.group('params').strip()})",
            source_ref(repository_alias, path, source, match.start()),
        )
        for match in _FUNCTION.finditer(masked)
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
    return DetectionReport(
        interfaces=_interfaces(masked, repository_alias, path),
        symbols=tuple(symbols),
        configurations=configurations,
        dependencies=_dependencies(masked, repository_alias, path),
    )
