"""Route one SemanticCore into the closed six-plus-two document AST set."""

from __future__ import annotations

import hashlib

from .authoritative_ast import (
    ApplicabilityStatus,
    AstViolation,
    AstRow,
    AuthorityHeader,
    Coverage,
    DocumentAst,
    DocumentRowKind,
    DocumentSection,
    DocumentSet,
    OptionalDocumentApplicability,
    OptionalDocumentFamily,
    ProjectedValue,
    RequiredDocumentFamily,
)
from .authoritative_document_routes import (
    CANONICAL_DOCUMENT_OWNER,
    DOCUMENT_ROUTES,
    ROUTED_ROW_KINDS,
    SECTION_ROUTES,
)
from .authoritative_documents import source_document_filenames
from .authoritative_semantic_model import SemanticCore, SemanticRecord


def _ast_row(record: SemanticRecord) -> AstRow:
    return AstRow(
        record.row_kind,
        record.record_id,
        record.values,
        (),
        (record.evidence_id,),
        True,
        f"ll-{record.record_id.lower()}",
        None,
        record.bindings,
    )


def _sections(records: tuple[SemanticRecord, ...]) -> tuple[DocumentSection, ...]:
    sections = tuple(
        DocumentSection(
            section_id,
            title,
            tuple(_ast_row(record) for record in records if record.row_kind is kind),
            (),
        )
        for kind, (section_id, title) in SECTION_ROUTES.items()
        if any(record.row_kind is kind for record in records)
    )
    return sections or (DocumentSection("no-source-records", "源码记录", (), ()),)


def _evidence_rows(core: SemanticCore, identifiers: frozenset[str]) -> tuple[AstRow, ...]:
    return tuple(
        AstRow(
            DocumentRowKind.EVIDENCE,
            evidence.evidence_id,
            (
                ProjectedValue("/repository_alias", evidence.source.repository_alias),
                ProjectedValue("/path", evidence.source.path),
                ProjectedValue("/line", evidence.source.line),
            ),
            (),
            (evidence.evidence_id,),
            True,
            f"ll-{evidence.evidence_id.lower()}",
            None,
            (),
        )
        for evidence in core.evidence
        if evidence.evidence_id in identifiers
    )


def _applicability(
    core: SemanticCore,
) -> tuple[tuple[OptionalDocumentApplicability, ...], bool, bool]:
    interface_ids = tuple(
        record.record_id
        for record in core.records
        if record.row_kind
        in {
            DocumentRowKind.INTERFACE,
            DocumentRowKind.CONTRACT_FIELD,
            DocumentRowKind.COMMAND,
            DocumentRowKind.WEB_INTERFACE,
        }
    )
    database_ids = tuple(
        record.record_id
        for record in core.records
        if record.row_kind
        in {
            DocumentRowKind.CURRENT_DATA,
            DocumentRowKind.HISTORICAL_DATA,
            DocumentRowKind.MIGRATION_OPERATION,
            DocumentRowKind.RELATION,
        }
    )
    interface_present = bool(interface_ids)
    database_present = bool(database_ids)
    items = (
        OptionalDocumentApplicability(
            OptionalDocumentFamily.INTERFACE_CONTRACT,
            ApplicabilityStatus.PRESENT
            if interface_present
            else ApplicabilityStatus.NO_EXPLICIT_MARKER,
            interface_ids,
        ),
        OptionalDocumentApplicability(
            OptionalDocumentFamily.DATABASE_DESIGN,
            ApplicabilityStatus.PRESENT
            if database_present
            else ApplicabilityStatus.NO_EXPLICIT_MARKER,
            database_ids,
        ),
    )
    return items, interface_present, database_present


def build_document_ast_set(core: SemanticCore) -> DocumentSet:
    """Create the exact typed document set before any Markdown rendering."""
    project_name = core.project_name
    unrouted = tuple(
        record.record_id for record in core.records if record.row_kind not in ROUTED_ROW_KINDS
    )
    if unrouted:
        raise AstViolation(
            "SemanticCore contains records outside the closed document routing matrix: "
            + ", ".join(unrouted)
        )
    applicability, interface_present, database_present = _applicability(core)
    active_routes = tuple(
        route
        for route in DOCUMENT_ROUTES
        if not isinstance(route.family, OptionalDocumentFamily)
        or (route.family is OptionalDocumentFamily.INTERFACE_CONTRACT and interface_present)
        or (route.family is OptionalDocumentFamily.DATABASE_DESIGN and database_present)
    )
    active_families = {route.family for route in active_routes}
    ownerless = tuple(
        record.record_id
        for record in core.records
        if CANONICAL_DOCUMENT_OWNER[record.row_kind] not in active_families
    )
    if ownerless:
        raise AstViolation(
            "SemanticCore contains searchable records without a human document owner: "
            + ", ".join(ownerless)
        )
    routed = tuple(
        tuple(record for record in core.records if record.row_kind in route.row_kinds)
        for route in active_routes
    )
    routed_ids = {record.record_id for records in routed for record in records}
    if routed_ids != {record.record_id for record in core.records}:
        raise AstViolation("SemanticCore document routing is not complete")
    routed_leaf_total = sum(len(record.values) for records in routed for record in records)
    coverage = Coverage(
        len(core.records),
        len(core.records),
        len(core.records),
        len(core.records),
        sum(len(record.values) for record in core.records),
        routed_leaf_total,
        routed_leaf_total,
        (),
    )
    filenames = source_document_filenames(project_name)
    has_web = any(record.row_kind.value.startswith("Web") for record in core.records)
    working_tree = core.source_snapshot_kind == "working_tree"
    documents: list[DocumentAst] = []
    for route, records in zip(active_routes, routed, strict=True):
        family_index = next(
            index
            for index, candidate in enumerate(DOCUMENT_ROUTES)
            if candidate.family is route.family
        )
        header = AuthorityHeader(
            core.trust_domain_id,
            core.repository_config_digest,
            core.package_id,
            coverage,
            (),
            authority_label=(
                "git_working_tree_snapshot_plus_governed_web_projection"
                if working_tree and has_web
                else "git_working_tree_snapshot_verified"
                if working_tree
                else "git_snapshot_plus_governed_web_projection"
                if has_web
                else "git_snapshot_verified"
            ),
            knowledge_db_status=("governed_web_loaded" if has_web else "not_loaded"),
        )
        documents.append(
            DocumentAst(
                "DOC-"
                + hashlib.sha256(
                    f"{core.semantic_core_sha256}\0{route.family.value}".encode()
                ).hexdigest(),
                filenames[family_index],
                f"{project_name} {route.title}",
                header,
                _sections(records),
                _evidence_rows(core, frozenset(record.evidence_id for record in records)),
                (),
                route.family if isinstance(route.family, RequiredDocumentFamily) else None,
                route.family if isinstance(route.family, OptionalDocumentFamily) else None,
            )
        )
    return DocumentSet(tuple(documents), applicability)
