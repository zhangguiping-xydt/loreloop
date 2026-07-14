"""Deterministic Prisma schema detector."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .authoritative_records import (
    DatabaseColumn,
    DatabaseIndex,
    DatabaseTable,
    DetectionError,
    DetectionReport,
    ForeignKeyRecord,
    SourceRef,
)
from .authoritative_redaction import redact_default


@dataclass(frozen=True, slots=True)
class _Field:
    logical_name: str
    column_name: str
    data_type: str
    optional: bool
    primary: bool
    default: str | None


@dataclass(slots=True)
class _Model:
    logical_name: str
    table_name: str
    source: SourceRef
    fields: list[_Field] = field(default_factory=list)
    relations: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = field(default_factory=list)
    primary_key: tuple[str, ...] = ()
    indexes: list[tuple[str, tuple[str, ...], bool]] = field(default_factory=list)


def _attribute(text: str, name: str, prefix: str = "@") -> str | None:
    marker = f"{prefix}{name}"
    start = text.find(marker)
    if start < 0:
        return None
    opening = text.find("(", start + len(marker))
    if opening < 0:
        return ""
    depth = 0
    quote: str | None = None
    for index in range(opening, len(text)):
        character = text[index]
        if quote is not None:
            if character == quote and text[index - 1] != "\\":
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index].strip()
    return None


def _quoted(text: str | None) -> str | None:
    if text is None:
        return None
    match = re.search(r'["\'](?P<value>[^"\']+)["\']', text)
    return None if match is None else match.group("value")


def _named_quoted(text: str | None, name: str) -> str | None:
    if text is None:
        return None
    match = re.search(rf"\b{re.escape(name)}\s*:\s*([\"'])(?P<value>[^\"']+)\1", text)
    return None if match is None else match.group("value")


def _named_list(text: str | None, name: str) -> tuple[str, ...]:
    if text is None:
        return ()
    match = re.search(rf"\b{re.escape(name)}\s*:\s*\[(?P<values>[^]]*)]", text)
    return () if match is None else _list(match.group("values"))


def _list(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for value in text.split(","):
        clean = value.strip().split("(", 1)[0].strip()
        if clean:
            values.append(clean)
    return tuple(values)


def _mapped_columns(model: _Model, logical: tuple[str, ...]) -> tuple[str, ...]:
    mapping = {field.logical_name: field.column_name for field in model.fields}
    return tuple(mapping.get(name, name) for name in logical)


def _field(line: str) -> _Field | None:
    parts = line.split(None, 2)
    if len(parts) < 2 or parts[0].startswith("@@"):
        return None
    logical_name, raw_type = parts[:2]
    attributes = "" if len(parts) == 2 else parts[2]
    if "@relation" in attributes:
        return None
    column_name = _quoted(_attribute(attributes, "map")) or logical_name
    default_value = _attribute(attributes, "default")
    default, _ = redact_default(column_name, default_value)
    return _Field(
        logical_name,
        column_name,
        raw_type.rstrip("?"),
        raw_type.endswith("?"),
        re.search(r"(?:^|\s)@id(?:\s|$|\()", attributes) is not None,
        default,
    )


def _model(lines: list[tuple[int, str]], alias: str, path: str) -> _Model:
    first_line, header = lines[0]
    header_match = re.match(r"\s*model\s+(\w+)", header)
    if header_match is None:
        raise DetectionError(f"invalid Prisma model header at {path}:{first_line}")
    logical_name = header_match.group(1)
    table_name = logical_name
    for _, line in lines[1:]:
        mapped = _quoted(_attribute(line, "map", "@@"))
        if mapped is not None:
            table_name = mapped
    model = _Model(logical_name, table_name, SourceRef(alias, path, first_line))
    for _, raw_line in lines[1:]:
        line = raw_line.split("//", 1)[0].strip()
        if not line or line == "}":
            continue
        if record := _field(line):
            model.fields.append(record)
        relation = _attribute(line, "relation")
        parts = line.split(None, 2)
        if relation is not None and len(parts) >= 2:
            local = _named_list(relation, "fields")
            remote = _named_list(relation, "references")
            if local and remote:
                model.relations.append((parts[1].rstrip("?[]"), local, remote))
        for kind, unique in (("index", False), ("unique", True)):
            value = _attribute(line, kind, "@@")
            if value is None:
                continue
            columns_match = re.match(r"\s*\[(?P<columns>[^]]+)]", value)
            columns = () if columns_match is None else _list(columns_match.group("columns"))
            index_name = _named_quoted(value, "map")
            if columns and index_name:
                model.indexes.append((index_name, columns, unique))
        model_primary = _attribute(line, "id", "@@")
        if model_primary is not None:
            match = re.match(r"\s*\[(?P<columns>[^]]+)]", model_primary)
            if match is not None:
                model.primary_key = _list(match.group("columns"))
    return model


def _blocks(source: str) -> tuple[list[tuple[int, str]], ...]:
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] | None = None
    for number, line in enumerate(source.splitlines(), 1):
        if current is None and re.match(r"\s*model\s+\w+\s*{", line):
            current = [(number, line)]
        elif current is not None:
            current.append((number, line))
            if line.strip() == "}":
                blocks.append(current)
                current = None
    if current is not None:
        raise DetectionError("Prisma model has no closing brace")
    return tuple(blocks)


def detect_prisma_schema(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract model columns, keys, relations, and explicitly named indexes."""
    models = tuple(_model(block, repository_alias, path) for block in _blocks(source))
    table_names = {model.logical_name: model.table_name for model in models}
    tables: list[DatabaseTable] = []
    indexes: list[DatabaseIndex] = []
    for model in models:
        fields = tuple(
            DatabaseColumn(
                item.column_name,
                item.data_type,
                item.optional,
                item.primary,
                item.default,
            )
            for item in model.fields
            if item.data_type.rstrip("[]") not in table_names
        )
        primary = model.primary_key or tuple(
            item.logical_name for item in model.fields if item.primary
        )
        foreign_keys = tuple(
            ForeignKeyRecord(
                _mapped_columns(model, local),
                table_names.get(target, target),
                remote,
            )
            for target, local, remote in model.relations
        )
        tables.append(
            DatabaseTable(
                model.table_name,
                fields,
                _mapped_columns(model, primary),
                foreign_keys,
                model.source,
            )
        )
        indexes.extend(
            DatabaseIndex(
                name, model.table_name, _mapped_columns(model, columns), unique, model.source
            )
            for name, columns, unique in model.indexes
        )
    return DetectionReport(tables=tuple(tables), indexes=tuple(indexes))
