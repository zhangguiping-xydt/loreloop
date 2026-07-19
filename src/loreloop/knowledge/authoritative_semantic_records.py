"""Convert all detector result variants into closed SemanticCore records."""

from __future__ import annotations

from .authoritative_ast import DocumentRowKind
from .authoritative_records import DetectionReport, SourceRef
from .authoritative_semantic_model import (
    Payload,
    SemanticContext,
    SemanticEvidence,
    SemanticRecord,
    make_blob_semantic_record,
    make_semantic_record,
)


def _add(
    context: SemanticContext,
    records: list[SemanticRecord],
    evidence: dict[str, SemanticEvidence],
    prefix: str,
    row_kind: DocumentRowKind,
    atom_kind: str,
    source: SourceRef,
    payload: Payload,
) -> None:
    record, source_evidence = make_semantic_record(
        context, prefix, row_kind, atom_kind, source, payload
    )
    records.append(record)
    evidence[source_evidence.evidence_id] = source_evidence


def build_semantic_records(
    context: SemanticContext,
    report: DetectionReport,
) -> tuple[tuple[SemanticRecord, ...], tuple[SemanticEvidence, ...]]:
    records: list[SemanticRecord] = []
    evidence: dict[str, SemanticEvidence] = {}
    for item in report.interfaces:
        prefix = "CLI" if item.kind == "cli" else "API"
        kind = DocumentRowKind.COMMAND if item.kind == "cli" else DocumentRowKind.INTERFACE
        _add(
            context,
            records,
            evidence,
            prefix,
            kind,
            item.kind,
            item.source,
            {
                "name": item.name,
                "method": item.method,
                "path": item.path,
                "parameters": ", ".join(
                    f"{value.name}:{value.annotation or '-'}{'*' if value.required else ''}"
                    for value in item.parameters
                ),
                "return_type": item.return_type,
            },
        )
    for item in report.contract_fields:
        _add(
            context,
            records,
            evidence,
            "FIELD",
            DocumentRowKind.CONTRACT_FIELD,
            "contract_field",
            item.source,
            {
                "owner_type": item.owner_type,
                "name": item.name,
                "data_type": item.data_type,
                "required": item.required,
                "nullable": item.nullable,
            },
        )
    for item in report.symbols:
        _add(
            context,
            records,
            evidence,
            "MOD",
            DocumentRowKind.MODULE,
            item.kind,
            item.source,
            {"qualified_name": item.qualified_name, "signature": item.signature},
        )
    for item in report.permissions:
        _add(
            context,
            records,
            evidence,
            "PERM",
            DocumentRowKind.PERMISSION,
            "permission",
            item.source,
            {
                "subject": item.subject,
                "operator": item.operator,
                "expected": item.expected,
                "expression": item.expression,
            },
        )
    for item in report.ui_surfaces:
        _add(
            context,
            records,
            evidence,
            "UI",
            DocumentRowKind.UI_SURFACE,
            item.surface_type,
            item.source,
            {
                "name": item.name,
                "surface_type": item.surface_type,
                "entry": item.entry,
                "actions": "".join(f"{action}\n" for action in item.actions),
            },
        )
    for item in report.tests:
        _add(
            context,
            records,
            evidence,
            "TEST",
            DocumentRowKind.TEST,
            "test",
            item.source,
            {
                "name": item.name,
                "framework": item.framework,
                "scope": item.scope,
                "case_count": len(item.cases),
                "cases": ", ".join(item.cases),
            },
        )
    web_row_kinds = {
        "requirement": DocumentRowKind.WEB_REQUIREMENT,
        "interface": DocumentRowKind.WEB_INTERFACE,
        "architecture": DocumentRowKind.WEB_ARCHITECTURE,
        "behavior": DocumentRowKind.WEB_BEHAVIOR,
        "constraint": DocumentRowKind.WEB_CONSTRAINT,
        "acceptance": DocumentRowKind.WEB_ACCEPTANCE,
    }
    for item in report.web_knowledge:
        _add(
            context,
            records,
            evidence,
            "WEB",
            web_row_kinds[item.kind],
            f"web_{item.kind}",
            item.source,
            {
                "entry_id": item.entry_id,
                "title": item.title,
                "statement": item.statement,
                "locator": item.locator,
                "snapshot_ref": item.snapshot_ref,
            },
        )
    for item in report.configurations:
        _add(
            context,
            records,
            evidence,
            "CFG",
            DocumentRowKind.CONFIGURATION,
            "configuration",
            item.source,
            {
                "key": item.key,
                "default": item.default,
                "required": item.required,
                "redacted": item.redacted,
            },
        )
    for item in report.dependencies:
        _add(
            context,
            records,
            evidence,
            "DEP",
            DocumentRowKind.DEPENDENCY,
            "dependency",
            item.source,
            {"name": item.name, "requirement": item.requirement, "scope": item.scope},
        )
    for item in report.implementation_facts:
        _add(
            context,
            records,
            evidence,
            "FACT",
            DocumentRowKind.IMPLEMENTATION_FACT,
            item.predicate,
            item.source,
            {
                "subject": item.subject,
                "predicate": item.predicate,
                "object": item.object,
                "detail": item.detail,
            },
        )
    for item in report.requirements:
        _add(
            context,
            records,
            evidence,
            "REQ",
            DocumentRowKind.REQUIREMENT,
            "requirement",
            item.source,
            {
                "external_id": item.external_id,
                "title": item.title,
                "statement": item.statement,
                "priority": item.priority,
                "role": item.role,
            },
        )
    for item in report.acceptances:
        _add(
            context,
            records,
            evidence,
            "ACC",
            DocumentRowKind.ACCEPTANCE,
            "acceptance",
            item.source,
            {"requirement_external_id": item.requirement_external_id, "statement": item.statement},
        )
    for table in report.tables:
        _add(
            context,
            records,
            evidence,
            "DATA",
            DocumentRowKind.CURRENT_DATA,
            "table",
            table.source,
            {
                "record_type": "table",
                "table": table.name,
                "primary_key": ", ".join(table.primary_key),
            },
        )
        for column in table.columns:
            _add(
                context,
                records,
                evidence,
                "DATA",
                DocumentRowKind.CURRENT_DATA,
                "column",
                table.source,
                {
                    "record_type": "column",
                    "table": table.name,
                    "name": column.name,
                    "data_type": column.data_type,
                    "nullable": column.nullable,
                    "primary_key": column.primary_key or column.name in table.primary_key,
                    "default": column.default,
                },
            )
        for foreign_key in table.foreign_keys:
            _add(
                context,
                records,
                evidence,
                "EDGE",
                DocumentRowKind.RELATION,
                "foreign_key",
                table.source,
                {
                    "record_type": "foreign_key",
                    "table": table.name,
                    "columns": ", ".join(foreign_key.columns),
                    "referenced_table": foreign_key.referenced_table,
                    "referenced_columns": ", ".join(foreign_key.referenced_columns),
                },
            )
    for item in report.indexes:
        _add(
            context,
            records,
            evidence,
            "DATA",
            DocumentRowKind.CURRENT_DATA,
            "index",
            item.source,
            {
                "record_type": "index",
                "name": item.name,
                "table": item.table,
                "columns": ", ".join(item.columns),
                "unique": item.unique,
            },
        )
    for item in report.source_issues:
        _add(
            context,
            records,
            evidence,
            "DOCSRC",
            DocumentRowKind.ANNOTATION,
            "source_decode_gap",
            item.source,
            {
                "path": item.path,
                "issue": item.issue,
                "selected_encoding": item.selected_encoding,
                "replacement_count": item.replacement_count,
                "dropped_fact_count": item.dropped_fact_count,
            },
        )
    for item in report.source_coverage:
        record, source_evidence = make_blob_semantic_record(
            context,
            "COV",
            DocumentRowKind.SOURCE_COVERAGE,
            "source_coverage",
            item.source,
            {
                "path": item.path,
                "suffix": item.suffix,
                "detector": item.detector,
                "status": item.status,
                "byte_length": item.byte_length,
            },
        )
        records.append(record)
        evidence[source_evidence.evidence_id] = source_evidence
    unique_records: list[SemanticRecord] = []
    seen_record_ids: set[str] = set()
    for record in records:
        if record.record_id in seen_record_ids:
            continue
        seen_record_ids.add(record.record_id)
        unique_records.append(record)
    used_evidence = {record.evidence_id for record in unique_records}
    return (
        tuple(unique_records),
        tuple(evidence[key] for key in sorted(used_evidence)),
    )
