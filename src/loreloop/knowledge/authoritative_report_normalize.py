"""Remove semantically duplicate detector facts while preserving first evidence."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

import re

from .authoritative_records import ContractFieldRecord, DetectionReport, InterfaceRecord

T = TypeVar("T")

_SCALAR_CONTRACT_TYPES = frozenset(
    {
        "any",
        "bool",
        "boolean",
        "byte",
        "char",
        "datetime",
        "dateonly",
        "decimal",
        "dict",
        "dictionary",
        "double",
        "dynamic",
        "float",
        "guid",
        "int",
        "int16",
        "int32",
        "int64",
        "integer",
        "list",
        "long",
        "map",
        "nullable",
        "object",
        "short",
        "single",
        "stream",
        "string",
        "timespan",
        "uint",
        "uint16",
        "uint32",
        "uint64",
        "void",
    }
)


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


def _contract_type_names(annotation: str | None) -> tuple[str, ...]:
    if not annotation:
        return ()
    names = tuple(
        name
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", annotation)
        if name.rsplit(".", 1)[-1].casefold() not in _SCALAR_CONTRACT_TYPES
    )
    return tuple(dict.fromkeys(names))


def _reachable_contract_fields(
    interfaces: tuple[InterfaceRecord, ...],
    fields: tuple[ContractFieldRecord, ...],
) -> tuple[ContractFieldRecord, ...]:
    """Keep only model fields reachable from an explicit interface signature."""
    by_repository: dict[str, dict[str, list[str]]] = {}
    rows_by_owner: dict[tuple[str, str], list[ContractFieldRecord]] = {}
    for field in fields:
        repository = field.source.repository_alias
        owner_key = (repository, field.owner_type)
        rows_by_owner.setdefault(owner_key, []).append(field)
        simple = field.owner_type.rsplit(".", 1)[-1].casefold()
        by_repository.setdefault(repository, {}).setdefault(simple, [])
        if field.owner_type not in by_repository[repository][simple]:
            by_repository[repository][simple].append(field.owner_type)

    reachable: set[tuple[str, str]] = set()
    pending: list[tuple[str, str]] = []

    def resolve(repository: str, annotation: str | None) -> None:
        owners = by_repository.get(repository, {})
        for name in _contract_type_names(annotation):
            exact = (repository, name)
            if exact in rows_by_owner:
                candidates = [name]
            else:
                candidates = owners.get(name.rsplit(".", 1)[-1].casefold(), [])
            if len(candidates) != 1:
                continue
            key = (repository, candidates[0])
            if key not in reachable:
                reachable.add(key)
                pending.append(key)

    for interface in interfaces:
        repository = interface.source.repository_alias
        for parameter in interface.parameters:
            resolve(repository, parameter.annotation)
        resolve(repository, interface.return_type)
    while pending:
        repository, owner = pending.pop()
        for field in rows_by_owner.get((repository, owner), ()):
            resolve(repository, field.data_type)
    return tuple(
        field for field in fields if (field.source.repository_alias, field.owner_type) in reachable
    )


def normalize_detection_report(report: DetectionReport) -> DetectionReport:
    """Collapse repeated generated/cross-file facts, never truncate unique contracts."""
    contract_fields = _reachable_contract_fields(report.interfaces, report.contract_fields)
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
        contract_fields=_unique(
            contract_fields,
            lambda item: (
                item.source.repository_alias,
                item.owner_type,
                item.name,
                item.data_type,
                item.required,
                item.nullable,
                item.source.path,
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
        implementation_facts=_unique(
            report.implementation_facts,
            lambda item: (
                item.source.repository_alias,
                item.source.path,
                item.subject,
                item.predicate,
                item.object,
                item.detail,
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
        source_coverage=_unique(
            report.source_coverage,
            lambda item: (
                item.source.repository_alias,
                item.path,
                item.suffix,
                item.detector,
                item.status,
                item.byte_length,
            ),
        ),
    )
