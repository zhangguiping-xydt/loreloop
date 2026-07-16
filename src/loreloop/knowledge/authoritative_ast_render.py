"""Render Markdown exclusively from the closed document AST."""

from __future__ import annotations

from .authoritative_ast import DocumentAst, DocumentSet
from .authoritative_documents import SourceDocument
from .authoritative_markdown_render import (
    EvidenceLocation,
    MarkdownDocument,
    MarkdownRow,
    MarkdownSection,
    render_markdown,
)


def _row(row) -> MarkdownRow:
    return MarkdownRow(
        row.row_kind.value,
        row.record_id,
        tuple((value.pointer.removeprefix("/"), value.value) for value in row.values),
        row.evidence_ids,
    )


def _document(document: DocumentAst) -> MarkdownDocument:
    evidence: list[tuple[str, EvidenceLocation]] = []
    for row in document.evidence_rows:
        values = {value.pointer.removeprefix("/"): value.value for value in row.values}
        repository = values.get("repository_alias")
        path = values.get("path")
        line = values.get("line")
        if isinstance(repository, str) and isinstance(path, str) and isinstance(line, int):
            evidence.append((row.record_id, EvidenceLocation(repository, path, line)))
    family = document.required_family or document.optional_family
    if family is None:
        raise ValueError("document family is missing")
    return MarkdownDocument(
        document.title,
        family.value,
        document.header.authority_label,
        document.header.package_id,
        document.header.repository_config_digest,
        document.header.coverage.record_total,
        tuple(
            MarkdownSection(section.title, tuple(_row(row) for row in section.rows))
            for section in document.sections
        ),
        tuple(evidence),
    )


def render_document_ast(
    document: DocumentAst,
    paths: tuple[str, ...],
    *,
    include_agent_appendix: bool = False,
    human_view_version: int | None = None,
) -> SourceDocument:
    """Render one validated AST without reading source or detector objects."""
    return SourceDocument(
        document.path,
        render_markdown(
            _document(document),
            paths,
            include_agent_appendix=include_agent_appendix,
            human_view_version=(
                1
                if human_view_version is None and include_agent_appendix
                else human_view_version or 2
            ),
        ),
    )


def render_document_set(
    document_set: DocumentSet,
    *,
    include_agent_appendix: bool = False,
    human_view_version: int | None = None,
) -> tuple[SourceDocument, ...]:
    paths = tuple(document.path for document in document_set.documents)
    return tuple(
        render_document_ast(
            document,
            paths,
            include_agent_appendix=include_agent_appendix,
            human_view_version=human_view_version,
        )
        for document in document_set.documents
    )
