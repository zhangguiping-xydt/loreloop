"""Deterministic shallow detector for Go source files."""

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
    r"(?m)^\s*type\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)\s*\{"
)
_FUNCTION: Final = re.compile(
    r"(?m)^\s*func\s*(?:\(\s*\w+\s+\*?(?P<receiver>[A-Za-z_][\w.]*)\s*\)\s*)?"
    + r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)"
)
_ROUTE: Final = re.compile(
    r"\b[A-Za-z_][\w.]*\.(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|"
    + r"Get|Post|Put|Patch|Delete|Head|Options)\s*\(\s*"
    + r"(?P<quote>[\"`])(?P<path>[^\"`]+)(?P=quote)\s*,\s*"
    + r"(?P<handler>[A-Za-z_][\w.]*)"
)
_HANDLE: Final = re.compile(
    r"\bhttp\.HandleFunc\s*\(\s*(?P<quote>[\"`])(?P<path>[^\"`]+)(?P=quote)"
    + r"\s*,\s*(?P<handler>[A-Za-z_][\w.]*)"
)
_GORILLA: Final = re.compile(
    r"\b[A-Za-z_][\w.]*\.HandleFunc\s*\(\s*"
    + r"(?P<quote>[\"`])(?P<path>[^\"`]+)(?P=quote)\s*,\s*"
    + r"(?P<handler>[A-Za-z_][\w.]*)\s*\)\s*\.Methods\s*\(\s*"
    + r"(?P<method_quote>[\"`])(?P<method>[A-Za-z]+)(?P=method_quote)"
)
_ENV: Final = re.compile(
    r"\bos\.(?:Getenv|LookupEnv)\s*\(\s*(?P<quote>[\"`])"
    + r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
)
_IMPORT_SINGLE: Final = re.compile(
    r"(?m)^\s*import\s+(?:[A-Za-z_.][\w.]*\s+)?(?P<quote>[\"`])"
    + r"(?P<name>[^\"`]+)(?P=quote)"
)
_IMPORT_BLOCK: Final = re.compile(r"(?ms)^\s*import\s*\((?P<body>.*?)^\s*\)")
_IMPORT_ITEM: Final = re.compile(
    r"(?m)^\s*(?:[A-Za-z_.][\w.]*\s+)?(?P<quote>[\"`])(?P<name>[^\"`]+)(?P=quote)"
)


def _dependencies(source: str, alias: str, path: str) -> tuple[DependencyRecord, ...]:
    matches: list[tuple[str, int]] = [
        (match.group("name"), match.start()) for match in _IMPORT_SINGLE.finditer(source)
    ]
    for block in _IMPORT_BLOCK.finditer(source):
        matches.extend(
            (match.group("name"), block.start("body") + match.start())
            for match in _IMPORT_ITEM.finditer(block.group("body"))
        )
    return tuple(
        DependencyRecord(name, None, "go_import", source_ref(alias, path, source, offset))
        for name, offset in sorted(matches, key=lambda item: item[1])
    )


def _interfaces(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    records: list[InterfaceRecord] = []
    for pattern in (_ROUTE, _GORILLA):
        records.extend(
            InterfaceRecord(
                "http",
                match.group("handler"),
                match.group("method").upper(),
                match.group("path"),
                (),
                None,
                source_ref(alias, path, source, match.start()),
            )
            for match in pattern.finditer(source)
        )
    records.extend(
        InterfaceRecord(
            "http",
            match.group("handler"),
            "ANY",
            match.group("path"),
            (),
            None,
            source_ref(alias, path, source, match.start()),
        )
        for match in _HANDLE.finditer(source)
    )
    return tuple(records)


def detect_go_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract explicit Go symbols, HTTP registrations, env reads, and imports."""
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
    for match in _FUNCTION.finditer(masked):
        receiver = match.group("receiver")
        name = match.group("name")
        qualified_name = name if receiver is None else f"{receiver}.{name}"
        symbols.append(
            SymbolRecord(
                "function",
                qualified_name,
                f"{qualified_name}({match.group('params').strip()})",
                source_ref(repository_alias, path, source, match.start()),
            )
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
