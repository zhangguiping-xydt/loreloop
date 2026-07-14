"""Render Markdown exclusively from the closed document AST."""

from __future__ import annotations

from .authoritative_ast import AstRow, DocumentAst, DocumentRowKind, DocumentSet
from .authoritative_documents import SourceDocument

AstScalar = None | bool | int | str


def _cell(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("`", "&#96;")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _value(value: AstScalar) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _row_values(row: AstRow) -> dict[str, AstScalar]:
    return {value.pointer.removeprefix("/"): value.value for value in row.values}


def _table(rows: tuple[AstRow, ...]) -> list[str]:
    columns = tuple(
        dict.fromkeys(value.pointer.removeprefix("/") for row in rows for value in row.values)
    )
    headers = ("record_id", *columns, "evidence")
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        values = _row_values(row)
        cells = [f"`{row.record_id}`"]
        cells.extend(_cell(_value(values.get(column))) for column in columns)
        cells.append("<br>".join(f"`{item}`" for item in row.evidence_ids) or "-")
        lines.append("| " + " | ".join(cells) + " |")
    return [*lines, ""]


def _relationship_graph(document: DocumentAst) -> list[str]:
    rows = tuple(
        row
        for section in document.sections
        for row in section.rows
        if row.row_kind in {DocumentRowKind.CURRENT_DATA, DocumentRowKind.RELATION}
    )
    table_names: list[str] = []
    relations: list[dict[str, AstScalar]] = []
    for row in rows:
        values = _row_values(row)
        table = values.get("table")
        if isinstance(table, str) and table not in table_names:
            table_names.append(table)
        referenced = values.get("referenced_table")
        if isinstance(referenced, str) and referenced not in table_names:
            table_names.append(referenced)
        if row.row_kind is DocumentRowKind.RELATION:
            relations.append(values)
    if not table_names:
        return []
    identifiers = {name: f"T{index:03d}" for index, name in enumerate(table_names, 1)}
    lines = [
        "## ER 关系图",
        "",
        "该图仅表达源码中的外键方向，不推断业务基数。",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    lines.extend(f'    {identifiers[name]}["{_cell(name)}"]' for name in table_names)
    for values in relations:
        table = values.get("table")
        referenced = values.get("referenced_table")
        if not isinstance(table, str) or not isinstance(referenced, str):
            continue
        label = f"{_value(values.get('columns'))} → {_value(values.get('referenced_columns'))}"
        lines.append(f'    {identifiers[table]} -->|"{_cell(label)}"| {identifiers[referenced]}')
    return [*lines, "```", ""]


def _evidence(document: DocumentAst) -> list[str]:
    lines = ["## 证据索引", ""]
    if not document.evidence_rows:
        return [*lines, "本文件没有源记录。", ""]
    lines.extend(["| evidence_id | repository | path | line |", "|---|---|---|---:|"])
    for row in document.evidence_rows:
        values = _row_values(row)
        lines.append(
            f"| `{row.record_id}` | `{_cell(_value(values.get('repository_alias')))}` | "
            + f"`{_cell(_value(values.get('path')))}` | {_value(values.get('line'))} |"
        )
    return [*lines, ""]


def render_document_ast(document: DocumentAst, paths: tuple[str, ...]) -> SourceDocument:
    """Render one validated AST without reading source or detector objects."""
    lines = [f"# {_cell(document.title)}", "", "## 文档导航", ""]
    lines.extend(f"- [{path[:-3]}]({path})" for path in paths)
    lines.extend(
        [
            "",
            "## 权威边界",
            "",
            f"- Authority: `{document.header.authority_label}`",
            f"- Package ID: `{document.header.package_id or '-'}`",
            f"- Repository configuration: `{document.header.repository_config_digest}`",
            f"- Records: {document.header.coverage.record_total}",
            "",
        ]
    )
    if document.optional_family is not None and document.optional_family.value == "database_design":
        lines.extend(_relationship_graph(document))
    for section in document.sections:
        lines.extend([f"## {_cell(section.title)}", ""])
        lines.extend(_table(section.rows) if section.rows else ["没有源记录。", ""])
    lines.extend(_evidence(document))
    lines.extend(
        [
            "## 证据限制",
            "",
            "未在提交态需求材料或源码中明确表达的业务结论不会被补写。",
            "",
        ]
    )
    return SourceDocument(document.path, "\n".join(lines).rstrip() + "\n")


def render_document_set(document_set: DocumentSet) -> tuple[SourceDocument, ...]:
    paths = tuple(document.path for document in document_set.documents)
    return tuple(render_document_ast(document, paths) for document in document_set.documents)
