"""Typed source facts emitted by deterministic authoritative detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class DetectionError(ValueError):
    """Source bytes cannot be converted into a closed detection result."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    repository_alias: str
    path: str
    line: int

    def __post_init__(self) -> None:
        if not self.repository_alias or not self.path or self.line < 1:
            raise DetectionError("invalid source reference")


@dataclass(frozen=True, slots=True)
class ParameterRecord:
    name: str
    annotation: str | None
    required: bool


@dataclass(frozen=True, slots=True)
class InterfaceRecord:
    kind: Literal["http", "cli"]
    name: str
    method: str
    path: str
    parameters: tuple[ParameterRecord, ...]
    return_type: str | None
    source: SourceRef


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    kind: Literal["class", "function", "async_function"]
    qualified_name: str
    signature: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class PermissionRecord:
    subject: str
    operator: str
    expected: str
    expression: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class UiSurfaceRecord:
    name: str
    surface_type: Literal["route", "page", "component"]
    entry: str | None
    actions: tuple[str, ...]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class TestRecord:
    name: str
    framework: str
    scope: Literal["unit", "integration", "unknown"]
    cases: tuple[str, ...]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class ConfigurationRecord:
    key: str
    default: str | None
    required: bool
    redacted: bool
    source: SourceRef


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    name: str
    requirement: str | None
    scope: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class RequirementRecord:
    external_id: str | None
    title: str | None
    statement: str
    priority: str | None
    role: str | None
    source: SourceRef


@dataclass(frozen=True, slots=True)
class AcceptanceRecord:
    requirement_external_id: str | None
    statement: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class ForeignKeyRecord:
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatabaseColumn:
    name: str
    data_type: str
    nullable: bool
    primary_key: bool
    default: str | None


@dataclass(frozen=True, slots=True)
class DatabaseTable:
    name: str
    columns: tuple[DatabaseColumn, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyRecord, ...]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class DatabaseIndex:
    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool
    source: SourceRef


@dataclass(frozen=True, slots=True)
class DetectionReport:
    interfaces: tuple[InterfaceRecord, ...] = ()
    symbols: tuple[SymbolRecord, ...] = ()
    permissions: tuple[PermissionRecord, ...] = ()
    ui_surfaces: tuple[UiSurfaceRecord, ...] = ()
    tests: tuple[TestRecord, ...] = ()
    configurations: tuple[ConfigurationRecord, ...] = ()
    dependencies: tuple[DependencyRecord, ...] = ()
    requirements: tuple[RequirementRecord, ...] = ()
    acceptances: tuple[AcceptanceRecord, ...] = ()
    tables: tuple[DatabaseTable, ...] = ()
    indexes: tuple[DatabaseIndex, ...] = ()

    @property
    def interface_document_applicable(self) -> bool:
        return bool(self.interfaces)

    @property
    def database_document_applicable(self) -> bool:
        return bool(self.tables)


def merge_reports(*reports: DetectionReport) -> DetectionReport:
    """Merge detector outputs without changing their source traversal order."""
    return DetectionReport(
        interfaces=tuple(item for report in reports for item in report.interfaces),
        symbols=tuple(item for report in reports for item in report.symbols),
        permissions=tuple(item for report in reports for item in report.permissions),
        ui_surfaces=tuple(item for report in reports for item in report.ui_surfaces),
        tests=tuple(item for report in reports for item in report.tests),
        configurations=tuple(item for report in reports for item in report.configurations),
        dependencies=tuple(item for report in reports for item in report.dependencies),
        requirements=tuple(item for report in reports for item in report.requirements),
        acceptances=tuple(item for report in reports for item in report.acceptances),
        tables=tuple(item for report in reports for item in report.tables),
        indexes=tuple(item for report in reports for item in report.indexes),
    )
