"""Deterministic Python AST detector for source-level project contracts."""

from __future__ import annotations

import ast
import re
from typing import Final

from .authoritative_detector_python_routes import (
    call_name,
    constant_text,
    definitions,
    django_interfaces,
    keyword,
    source_ref,
)
from .authoritative_detector_python_database import detect_python_database_models
from .authoritative_detector_python_migrations import detect_python_migrations
from .authoritative_detector_sql import detect_sql_source
from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionError,
    DetectionReport,
    PermissionRecord,
    merge_reports,
)
from .authoritative_redaction import redact_default

_PERMISSION_NAMES: Final = frozenset(
    {"role", "roles", "permission", "permissions", "scope", "scopes"}
)
_SQL_DDL: Final = re.compile(r"\bCREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\b", re.IGNORECASE)


def _dependencies(tree: ast.AST, alias: str, path: str) -> tuple[DependencyRecord, ...]:
    records: list[DependencyRecord] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            records.extend(
                DependencyRecord(
                    name.name.split(".", 1)[0], None, "python_import", source_ref(alias, path, node)
                )
                for name in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            records.append(
                DependencyRecord(
                    node.module.split(".", 1)[0],
                    None,
                    "python_import",
                    source_ref(alias, path, node),
                )
            )
    return tuple(records)


def _configurations(tree: ast.AST, alias: str, path: str) -> tuple[ConfigurationRecord, ...]:
    records: list[ConfigurationRecord] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        key: str | None = None
        default: str | None = None
        if isinstance(node, ast.Call) and call_name(node.func) in {
            "getenv",
            "os.getenv",
            "environ.get",
            "os.environ.get",
        }:
            key = constant_text(node.args[0]) if node.args else None
            default_node = node.args[1] if len(node.args) > 1 else keyword(node, "default")
            default = constant_text(default_node)
        elif isinstance(node, ast.Subscript) and call_name(node.value) in {
            "environ",
            "os.environ",
        }:
            key = constant_text(node.slice)
        if key is None or (key, getattr(node, "lineno", 1)) in seen:
            continue
        seen.add((key, getattr(node, "lineno", 1)))
        portable, redacted = redact_default(key, default)
        records.append(
            ConfigurationRecord(
                key, portable, default is None, redacted, source_ref(alias, path, node)
            )
        )
    return tuple(records)


def _permissions(tree: ast.AST, alias: str, path: str) -> tuple[PermissionRecord, ...]:
    records: list[PermissionRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
            continue
        subject = ast.unparse(node.left)
        if subject.rsplit(".", 1)[-1].lower() not in _PERMISSION_NAMES:
            continue
        match node.ops[0]:
            case ast.Eq():
                operator = "=="
            case ast.NotEq():
                operator = "!="
            case ast.In():
                operator = "in"
            case ast.NotIn():
                operator = "not in"
            case ast.Is():
                operator = "is"
            case ast.IsNot():
                operator = "is not"
            case _:
                continue
        records.append(
            PermissionRecord(
                subject,
                operator,
                ast.unparse(node.comparators[0]),
                ast.unparse(node),
                source_ref(alias, path, node),
            )
        )
    return tuple(records)


def detect_python_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract Python symbols, interfaces, config, permissions, dependencies, and DDL."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise DetectionError(f"invalid Python source at {path}:{exc.lineno or 1}") from exc
    symbols, decorated = definitions(tree.body, repository_alias, path)
    base = DetectionReport(
        interfaces=(*decorated, *django_interfaces(tree, repository_alias, path)),
        symbols=symbols,
        permissions=_permissions(tree, repository_alias, path),
        configurations=_configurations(tree, repository_alias, path),
        dependencies=_dependencies(tree, repository_alias, path),
    )
    sql_reports = tuple(
        detect_sql_source(node.value, repository_alias, path, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _SQL_DDL.search(node.value) is not None
    )
    return merge_reports(
        base,
        detect_python_database_models(source, repository_alias, path),
        detect_python_migrations(source, repository_alias, path),
        *sql_reports,
    )
