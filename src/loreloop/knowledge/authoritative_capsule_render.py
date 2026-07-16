"""Render a validated Capsule AST through the canonical Markdown projection."""

from __future__ import annotations

from collections.abc import Mapping

from .authoritative_capsule import JsonValue
from .authoritative_markdown_render import (
    EvidenceLocation,
    MarkdownDocument,
    MarkdownRow,
    MarkdownSection,
    Scalar,
    render_markdown,
)


class CapsuleRenderError(ValueError):
    """A stored document AST cannot be rendered safely."""


def _mapping(value: JsonValue | None, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise CapsuleRenderError(f"{label} must be an object")
    return value


def _array(value: JsonValue | None, label: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise CapsuleRenderError(f"{label} must be an array")
    return value


def _text(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str):
        raise CapsuleRenderError(f"{label} must be text")
    return value


def _integer(value: JsonValue | None, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapsuleRenderError(f"{label} must be an integer")
    return value


def _scalar(value: JsonValue | None, label: str) -> Scalar:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise CapsuleRenderError(f"{label} must be a scalar")


def _row(value: JsonValue, label: str) -> MarkdownRow:
    row = _mapping(value, label)
    values: list[tuple[str, Scalar]] = []
    for index, raw in enumerate(_array(row.get("values"), f"{label} values")):
        projected = _mapping(raw, f"{label} value {index}")
        pointer = _text(projected.get("pointer"), f"{label} value pointer")
        values.append(
            (
                pointer.removeprefix("/"),
                _scalar(projected.get("value"), f"{label} value"),
            )
        )
    evidence = tuple(
        _text(item, f"{label} evidence id")
        for item in _array(row.get("evidence_ids"), f"{label} evidence ids")
    )
    return MarkdownRow(
        _text(row.get("row_kind"), f"{label} kind"),
        _text(row.get("record_id"), f"{label} id"),
        tuple(values),
        evidence,
    )


def _document(ast: Mapping[str, JsonValue]) -> MarkdownDocument:
    header = _mapping(ast.get("header"), "AST authority header")
    coverage = _mapping(header.get("coverage"), "AST coverage")
    raw_family = ast.get("required_family") or ast.get("optional_family")
    family = _text(raw_family, "AST document family")
    package = header.get("package_id")
    if package is not None and not isinstance(package, str):
        raise CapsuleRenderError("AST package id must be text or null")
    sections: list[MarkdownSection] = []
    for index, raw in enumerate(_array(ast.get("sections"), "AST sections")):
        section = _mapping(raw, f"AST section {index}")
        rows = tuple(
            _row(item, f"AST section {index} row")
            for item in _array(section.get("rows"), f"AST section {index} rows")
        )
        sections.append(MarkdownSection(_text(section.get("title"), "AST section title"), rows))
    evidence: list[tuple[str, EvidenceLocation]] = []
    for index, raw in enumerate(_array(ast.get("evidence_rows"), "AST evidence rows")):
        row = _row(raw, f"AST evidence row {index}")
        values = dict(row.values)
        repository = values.get("repository_alias")
        path = values.get("path")
        line = values.get("line")
        if (
            not isinstance(repository, str)
            or not isinstance(path, str)
            or not isinstance(line, int)
        ):
            raise CapsuleRenderError(f"AST evidence row {index} has invalid source values")
        evidence.append((row.record_id, EvidenceLocation(repository, path, line)))
    return MarkdownDocument(
        _text(ast.get("title"), "AST title"),
        family,
        _text(header.get("authority_label"), "AST authority"),
        package,
        _text(header.get("repository_config_digest"), "AST repository digest"),
        _integer(coverage.get("record_total"), "AST record total"),
        tuple(sections),
        tuple(evidence),
    )


def render_capsule_ast(
    ast_value: JsonValue,
    paths: tuple[str, ...],
    *,
    include_agent_appendix: bool = False,
    human_view_version: int = 1,
) -> str:
    """Render one stored AST after the replay layer validates its exact projection."""
    return render_markdown(
        _document(_mapping(ast_value, "document AST")),
        paths,
        include_agent_appendix=include_agent_appendix,
        human_view_version=human_view_version,
    )
