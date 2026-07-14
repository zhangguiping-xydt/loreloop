"""Deterministic TypeScript/JavaScript contract detector without code execution."""

from __future__ import annotations

import re
from typing import Final

from .authoritative_detector_sql import detect_sql_source
from .authoritative_detector_typeorm import detect_typeorm_entities
from .authoritative_detector_ui import detect_typescript_ui_surfaces
from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionReport,
    InterfaceRecord,
    PermissionRecord,
    SourceRef,
    SymbolRecord,
    merge_reports,
)

_ROUTE: Final = re.compile(
    r"\b(?:app|router|server|fastify)\s*\.\s*(get|post|put|patch|delete|head|options)"
    + r"\s*\(\s*(['\"])(?P<path>[^'\"]+)\2\s*,\s*(?P<handler>[A-Za-z_$][\w$]*)",
    re.IGNORECASE,
)
_CONTROLLER: Final = re.compile(r"@Controller\s*\(\s*(['\"])(?P<path>[^'\"]*)\1\s*\)")
_NEST_ROUTE: Final = re.compile(
    r"@(?P<method>Get|Post|Put|Patch|Delete)\s*\(\s*(?:(['\"])(?P<path>[^'\"]*)\2)?\s*\)"
    + r"(?:\s*@[^\n]+)*\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?"
    + r"(?P<handler>[A-Za-z_$][\w$]*)\s*\(",
)
_FUNCTION: Final = re.compile(
    r"^(?P<export>export\s+)(?:default\s+)?(?:(?P<async>async)\s+)?"
    + r"function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_ARROW: Final = re.compile(
    r"^(?P<export>export\s+)(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*)"
    + r"\s*=\s*(?:async\s+)?\((?P<params>[^)]*)\)\s*=>",
    re.MULTILINE,
)
_CLASS: Final = re.compile(
    r"^(?P<export>export\s+)(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_IMPORT: Final = re.compile(
    r"(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)(['\"])(?P<name>[^'\"]+)\1"
)
_DEPENDENCY_SPECIFIER: Final = re.compile(r"(?:@[A-Za-z0-9_.-]+/)?[#A-Za-z0-9_][A-Za-z0-9._~:/#-]*")
_ENV: Final = re.compile(
    r"\bprocess\.env\.(?P<property>[A-Za-z_][A-Za-z0-9_]*)|"
    + r"\b(?:process\.env|Deno\.env)\s*(?:\.get)?\s*\[?\(?\s*(['\"])(?P<call>[^'\"]+)\2"
)
_PERMISSION_TOKEN: Final = re.compile(r"\.(?:role|permission|scope)\b", re.IGNORECASE)
_PERMISSION_TAIL: Final = re.compile(
    r"\s*(?P<operator>===|!==|==|!=)\s*(?P<quote>['\"])"
    + r"(?P<expected>[^'\"\r\n]{1,512})(?P=quote)"
)
_IDENTIFIER: Final = re.compile(r"[A-Za-z_$][\w$]*")
_COMMAND: Final = re.compile(r"\b(?:program|cli)\.command\s*\(\s*(['\"])(?P<name>[^'\"]+)\1")
_SQL_DDL: Final = re.compile(r"\bCREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\b", re.IGNORECASE)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _ref(alias: str, path: str, source: str, offset: int) -> SourceRef:
    return SourceRef(alias, path, _line(source, offset))


def _dependency_name(specifier: str) -> str:
    if specifier.startswith("@"):
        return "/".join(specifier.split("/")[:2])
    return specifier.split("/", 1)[0]


def _valid_dependency_specifier(specifier: str) -> bool:
    """Reject string-expression fragments captured from generated JavaScript."""
    return _DEPENDENCY_SPECIFIER.fullmatch(specifier) is not None


def _template_literals(source: str) -> tuple[tuple[int, str], ...]:
    """Scan template literals linearly; regex backtracking is unbounded on generated TS."""
    literals: list[tuple[int, str]] = []
    opening = source.find("`")
    while opening >= 0:
        index = opening + 1
        while index < len(source):
            character = source[index]
            if character == "\\":
                index += 2
                continue
            if character == "`":
                literals.append((opening + 1, source[opening + 1 : index]))
                opening = source.find("`", index + 1)
                break
            index += 1
        else:
            break
    return tuple(literals)


def _symbols(source: str, alias: str, path: str) -> tuple[SymbolRecord, ...]:
    records: list[SymbolRecord] = []
    records.extend(
        SymbolRecord(
            "async_function" if match.group("async") else "function",
            match.group("name"),
            f"{match.group('name')}({match.group('params').strip()})",
            _ref(alias, path, source, match.start()),
        )
        for match in _FUNCTION.finditer(source)
    )
    records.extend(
        SymbolRecord(
            "function",
            match.group("name"),
            f"{match.group('name')}({match.group('params').strip()})",
            _ref(alias, path, source, match.start()),
        )
        for match in _ARROW.finditer(source)
    )
    records.extend(
        SymbolRecord(
            "class",
            match.group("name"),
            match.group("name"),
            _ref(alias, path, source, match.start()),
        )
        for match in _CLASS.finditer(source)
    )
    return tuple(records)


def _permissions(source: str, alias: str, path: str) -> tuple[PermissionRecord, ...]:
    records: list[PermissionRecord] = []
    for token in _PERMISSION_TOKEN.finditer(source):
        start = token.start()
        while start > 0 and (source[start - 1].isalnum() or source[start - 1] in "_.$"):
            start -= 1
        subject = source[start : token.end()]
        parts = subject.split(".")
        if not 2 <= len(parts) <= 10 or any(_IDENTIFIER.fullmatch(part) is None for part in parts):
            continue
        tail = _PERMISSION_TAIL.match(source, token.end())
        if tail is None:
            continue
        expression = source[start : tail.end()]
        records.append(
            PermissionRecord(
                subject,
                tail.group("operator"),
                repr(tail.group("expected")),
                expression,
                _ref(alias, path, source, start),
            )
        )
    return tuple(records)


def _interfaces(source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    records = [
        InterfaceRecord(
            "http",
            match.group("handler"),
            match.group(1).upper(),
            match.group("path"),
            (),
            None,
            _ref(alias, path, source, match.start()),
        )
        for match in _ROUTE.finditer(source)
    ]
    controller = next(_CONTROLLER.finditer(source), None)
    prefix = "" if controller is None else controller.group("path").rstrip("/")
    records.extend(
        InterfaceRecord(
            "http",
            match.group("handler"),
            match.group("method").upper(),
            f"{prefix}/{(match.group('path') or '').lstrip('/')}" or "/",
            (),
            None,
            _ref(alias, path, source, match.start()),
        )
        for match in _NEST_ROUTE.finditer(source)
    )
    records.extend(
        InterfaceRecord(
            "cli",
            match.group("name").split()[0],
            "COMMAND",
            match.group("name"),
            (),
            None,
            _ref(alias, path, source, match.start()),
        )
        for match in _COMMAND.finditer(source)
    )
    return tuple(records)


def detect_typescript_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract TypeScript/JavaScript routes, symbols, dependencies, config, permissions, and DDL."""
    dependencies = tuple(
        DependencyRecord(
            _dependency_name(match.group("name")),
            None,
            "typescript_import",
            _ref(repository_alias, path, source, match.start()),
        )
        for match in _IMPORT.finditer(source)
        if not match.group("name").startswith((".", "/"))
        and _valid_dependency_specifier(match.group("name"))
    )
    configurations = tuple(
        ConfigurationRecord(
            match.group("property") or match.group("call"),
            None,
            True,
            False,
            _ref(repository_alias, path, source, match.start()),
        )
        for match in _ENV.finditer(source)
    )
    permissions = _permissions(source, repository_alias, path)
    base = DetectionReport(
        interfaces=_interfaces(source, repository_alias, path),
        symbols=_symbols(source, repository_alias, path),
        permissions=permissions,
        ui_surfaces=detect_typescript_ui_surfaces(source, repository_alias, path),
        configurations=configurations,
        dependencies=dependencies,
    )
    sql_reports = tuple(
        detect_sql_source(
            body,
            repository_alias,
            path,
            _line(source, offset),
        )
        for offset, body in _template_literals(source)
        if "${" not in body and _SQL_DDL.search(body)
    )
    return merge_reports(
        base,
        detect_typeorm_entities(source, repository_alias, path),
        *sql_reports,
    )
