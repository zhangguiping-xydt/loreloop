"""Independently render Capsule AST payloads for portable replay."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from .authoritative_capsule import JsonValue

JsonScalar: TypeAlias = None | bool | int | str


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


def _scalar(value: JsonValue | None, label: str) -> JsonScalar:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise CapsuleRenderError(f"{label} must be a scalar")


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


def _value(value: JsonScalar) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _row_values(row: Mapping[str, JsonValue]) -> dict[str, JsonScalar]:
    result: dict[str, JsonScalar] = {}
    for index, item in enumerate(_array(row.get("values"), "AST row values")):
        projected = _mapping(item, f"AST row value {index}")
        pointer = _text(projected.get("pointer"), "AST value pointer")
        result[pointer.removeprefix("/")] = _scalar(projected.get("value"), "AST value")
    return result


def _rows(value: JsonValue | None, label: str) -> tuple[Mapping[str, JsonValue], ...]:
    return tuple(_mapping(item, label) for item in _array(value, label))


def _table(rows: tuple[Mapping[str, JsonValue], ...]) -> list[str]:
    columns = tuple(
        dict.fromkeys(
            _text(item.get("pointer"), "AST value pointer").removeprefix("/")
            for row in rows
            for item in (
                _mapping(value, "AST row value")
                for value in _array(row.get("values"), "AST row values")
            )
        )
    )
    headers = ("record_id", *columns, "evidence")
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        values = _row_values(row)
        cells = [f"`{_text(row.get('record_id'), 'AST record id')}`"]
        cells.extend(_cell(_value(values.get(column))) for column in columns)
        evidence = (
            _text(item, "AST evidence id")
            for item in _array(row.get("evidence_ids"), "AST evidence ids")
        )
        cells.append("<br>".join(f"`{item}`" for item in evidence) or "-")
        lines.append("| " + " | ".join(cells) + " |")
    return [*lines, ""]


def _relationship_graph(sections: list[JsonValue]) -> list[str]:
    rows = tuple(
        row
        for section_value in sections
        for row in _rows(_mapping(section_value, "AST section").get("rows"), "AST rows")
        if row.get("row_kind") in {"CurrentDataRow", "RelationRow"}
    )
    table_names: list[str] = []
    relations: list[dict[str, JsonScalar]] = []
    for row in rows:
        values = _row_values(row)
        for key in ("table", "referenced_table"):
            name = values.get(key)
            if isinstance(name, str) and name not in table_names:
                table_names.append(name)
        if row.get("row_kind") == "RelationRow":
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
        *(f'    {identifiers[name]}["{_cell(name)}"]' for name in table_names),
    ]
    for values in relations:
        table, referenced = values.get("table"), values.get("referenced_table")
        if isinstance(table, str) and isinstance(referenced, str):
            label = f"{_value(values.get('columns'))} → {_value(values.get('referenced_columns'))}"
            lines.append(
                f'    {identifiers[table]} -->|"{_cell(label)}"| {identifiers[referenced]}'
            )
    return [*lines, "```", ""]


def _evidence(ast: Mapping[str, JsonValue]) -> list[str]:
    rows = _rows(ast.get("evidence_rows"), "AST evidence rows")
    lines = ["## 证据索引", ""]
    if not rows:
        return [*lines, "本文件没有源记录。", ""]
    lines.extend(["| evidence_id | repository | path | line |", "|---|---|---|---:|"])
    for row in rows:
        values = _row_values(row)
        lines.append(
            f"| `{_text(row.get('record_id'), 'evidence record id')}` | "
            + f"`{_cell(_value(values.get('repository_alias')))}` | "
            + f"`{_cell(_value(values.get('path')))}` | {_value(values.get('line'))} |"
        )
    return [*lines, ""]


def render_capsule_ast(ast_value: JsonValue, paths: tuple[str, ...]) -> str:
    """Render one stored AST without importing source or detector objects."""
    ast = _mapping(ast_value, "document AST")
    header = _mapping(ast.get("header"), "AST authority header")
    coverage = _mapping(header.get("coverage"), "AST coverage")
    sections = _array(ast.get("sections"), "AST sections")
    package = header.get("package_id")
    if package is not None and not isinstance(package, str):
        raise CapsuleRenderError("AST package id must be text or null")
    lines = [f"# {_cell(_text(ast.get('title'), 'AST title'))}", "", "## 文档导航", ""]
    lines.extend(f"- [{path[:-3]}]({path})" for path in paths)
    lines.extend(
        [
            "",
            "## 权威边界",
            "",
            f"- Authority: `{_text(header.get('authority_label'), 'AST authority')}`",
            f"- Package ID: `{package or '-'}`",
            f"- Repository configuration: `{_text(header.get('repository_config_digest'), 'AST repository digest')}`",
            f"- Records: {_integer(coverage.get('record_total'), 'AST record total')}",
            "",
        ]
    )
    if ast.get("optional_family") == "database_design":
        lines.extend(_relationship_graph(sections))
    for section_value in sections:
        section = _mapping(section_value, "AST section")
        lines.extend([f"## {_cell(_text(section.get('title'), 'AST section title'))}", ""])
        rows = _rows(section.get("rows"), "AST rows")
        lines.extend(_table(rows) if rows else ["没有源记录。", ""])
    lines.extend(_evidence(ast))
    lines.extend(
        ["## 证据限制", "", "未在提交态需求材料或源码中明确表达的业务结论不会被补写。", ""]
    )
    return "\n".join(lines).rstrip() + "\n"
