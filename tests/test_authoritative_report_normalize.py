from __future__ import annotations

from loreloop.knowledge.authoritative_records import (
    ConfigurationRecord,
    DatabaseColumn,
    DatabaseTable,
    DependencyRecord,
    DetectionReport,
    ImplementationFactRecord,
    InterfaceRecord,
    SourceRef,
)
from loreloop.knowledge.authoritative_report_normalize import normalize_detection_report


def test_normalization_keeps_one_real_source_for_repeated_semantic_fact() -> None:
    first = SourceRef(".", "a.ts", 1)
    second = SourceRef(".", "b.ts", 2)
    report = DetectionReport(
        interfaces=(
            InterfaceRecord("http", "health", "GET", "/health", (), None, first),
            InterfaceRecord("http", "health", "GET", "/health", (), None, second),
        ),
        dependencies=(
            DependencyRecord("react", "^19", "typescript_import", first),
            DependencyRecord("react", "^19", "typescript_import", second),
        ),
        implementation_facts=(
            ImplementationFactRecord("sync", "writes", "USERS", None, first),
            ImplementationFactRecord("sync", "writes", "USERS", None, second),
        ),
        configurations=(
            ConfigurationRecord("API_URL", None, True, False, first),
            ConfigurationRecord("API_URL", None, True, False, second),
        ),
        tables=(
            DatabaseTable(
                "health",
                (DatabaseColumn("id", "INTEGER", False, True, None),),
                ("id",),
                (),
                first,
            ),
            DatabaseTable(
                "health",
                (DatabaseColumn("id", "INTEGER", False, True, None),),
                ("id",),
                (),
                second,
            ),
        ),
    )

    normalized = normalize_detection_report(report)

    assert normalized.dependencies == report.dependencies[:1]
    assert normalized.configurations == report.configurations[:1]
    assert normalized.interfaces == report.interfaces[:1]
    assert normalized.implementation_facts == report.implementation_facts
    assert normalized.tables == report.tables[:1]


def test_normalization_never_merges_identical_facts_across_repositories() -> None:
    first = SourceRef("service-a", "app.ts", 1)
    second = SourceRef("service-b", "app.ts", 1)
    report = DetectionReport(
        interfaces=(
            InterfaceRecord("http", "health", "GET", "/health", (), None, first),
            InterfaceRecord("http", "health", "GET", "/health", (), None, second),
        ),
        dependencies=(
            DependencyRecord("react", "^19", "typescript_import", first),
            DependencyRecord("react", "^19", "typescript_import", second),
        ),
        configurations=(
            ConfigurationRecord("API_URL", None, True, False, first),
            ConfigurationRecord("API_URL", None, True, False, second),
        ),
        tables=(
            DatabaseTable(
                "health",
                (DatabaseColumn("id", "INTEGER", False, True, None),),
                ("id",),
                (),
                first,
            ),
            DatabaseTable(
                "health",
                (DatabaseColumn("id", "INTEGER", False, True, None),),
                ("id",),
                (),
                second,
            ),
        ),
    )

    normalized = normalize_detection_report(report)

    assert normalized.dependencies == report.dependencies
    assert normalized.configurations == report.configurations
    assert normalized.interfaces == report.interfaces
    assert normalized.tables == report.tables
