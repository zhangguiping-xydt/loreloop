"""Remove semantically duplicate detector facts while preserving first evidence."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

from .authoritative_records import DetectionReport

T = TypeVar("T")


def _unique(items: Iterable[T], key: Callable[[T], Hashable]) -> tuple[T, ...]:
    seen: set[Hashable] = set()
    result: list[T] = []
    for item in items:
        identity = key(item)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return tuple(result)


def normalize_detection_report(report: DetectionReport) -> DetectionReport:
    """Collapse repeated generated/cross-file facts, never truncate unique contracts."""
    return DetectionReport(
        interfaces=_unique(
            report.interfaces,
            lambda item: (
                item.source.repository_alias,
                item.kind,
                item.name,
                item.method,
                item.path,
                item.parameters,
                item.return_type,
            ),
        ),
        symbols=_unique(
            report.symbols,
            lambda item: (
                item.kind,
                item.qualified_name,
                item.signature,
                item.source.repository_alias,
                item.source.path,
            ),
        ),
        permissions=_unique(
            report.permissions,
            lambda item: (
                item.source.repository_alias,
                item.subject,
                item.operator,
                item.expected,
                item.expression,
            ),
        ),
        ui_surfaces=_unique(
            report.ui_surfaces,
            lambda item: (
                item.source.repository_alias,
                item.source.path,
                item.name,
                item.surface_type,
                item.entry,
                item.actions,
            ),
        ),
        tests=_unique(
            report.tests,
            lambda item: (
                item.source.repository_alias,
                item.source.path,
                item.name,
                item.framework,
                item.scope,
                item.cases,
            ),
        ),
        web_knowledge=_unique(
            report.web_knowledge,
            lambda item: (
                item.entry_id,
                item.kind,
                item.title,
                item.statement,
                item.locator,
                item.snapshot_ref,
            ),
        ),
        configurations=_unique(
            report.configurations,
            lambda item: (
                item.source.repository_alias,
                item.key,
                item.default,
                item.required,
                item.redacted,
            ),
        ),
        dependencies=_unique(
            report.dependencies,
            lambda item: (
                item.source.repository_alias,
                item.name,
                item.requirement,
                item.scope,
            ),
        ),
        requirements=_unique(
            report.requirements,
            lambda item: (
                item.source.repository_alias,
                item.external_id,
                item.title,
                item.statement,
                item.priority,
                item.role,
            ),
        ),
        acceptances=_unique(
            report.acceptances,
            lambda item: (
                item.source.repository_alias,
                item.requirement_external_id,
                item.statement,
            ),
        ),
        tables=_unique(
            report.tables,
            lambda item: (
                item.source.repository_alias,
                item.name,
                item.columns,
                item.primary_key,
                item.foreign_keys,
            ),
        ),
        indexes=_unique(
            report.indexes,
            lambda item: (
                item.source.repository_alias,
                item.name,
                item.table,
                item.columns,
                item.unique,
            ),
        ),
        source_issues=_unique(
            report.source_issues,
            lambda item: (
                item.source.repository_alias,
                item.path,
                item.issue,
                item.selected_encoding,
                item.replacement_count,
                item.dropped_fact_count,
            ),
        ),
    )
