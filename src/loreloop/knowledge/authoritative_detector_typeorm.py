"""Deterministic TypeORM entity detector for common decorator forms."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .authoritative_records import (
    DatabaseColumn,
    DatabaseIndex,
    DatabaseTable,
    DetectionReport,
    ForeignKeyRecord,
    SourceRef,
)
from .authoritative_redaction import redact_default


@dataclass(slots=True)
class _Entity:
    class_name: str
    table_name: str
    source: SourceRef
    columns: list[DatabaseColumn] = field(default_factory=list)
    relations: list[tuple[str, str, str]] = field(default_factory=list)
    indexes: list[tuple[str, tuple[str, ...], bool]] = field(default_factory=list)


_ENTITY = re.compile(
    r"@Entity\s*(?:\((?P<entity>[^\n]*)\))?"
    + r"(?P<prefix>(?:\s*@[^\n]+)*)\s*(?:export\s+)?class\s+(?P<class>\w+)\s*{"
)
_MEMBER = re.compile(
    r"(?P<decorators>(?:\s*@[A-Za-z_]\w*(?:\([^\n;]*\))?\s*)+)"
    + r"(?:public\s+|private\s+|protected\s+|readonly\s+)*"
    + r"(?P<name>[A-Za-z_$][\w$]*)\s*[!?]?\s*:\s*(?P<type>[^;=\n]+)[^;]*;"
)


def _closing_brace(source: str, opening: int) -> int:
    depth = 0
    quote: str | None = None
    for index in range(opening, len(source)):
        character = source[index]
        if quote is not None:
            if character == quote and source[index - 1] != "\\":
                quote = None
        elif character in {'"', "'", "`"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(source)


def _quoted(text: str | None) -> str | None:
    if text is None:
        return None
    match = re.search(r'["\'](?P<value>[^"\']+)["\']', text)
    return None if match is None else match.group("value")


def _option(text: str | None, name: str) -> str | None:
    if text is None:
        return None
    match = re.search(
        rf"\b{re.escape(name)}\s*:\s*(?P<value>[\"'][^\"']*[\"']|true|false|-?\d+(?:\.\d+)?)",
        text,
    )
    if match is None:
        return None
    return match.group("value").strip("\"'")


def _decorator(text: str, name: str) -> str | None:
    match = re.search(rf"@{re.escape(name)}(?:\((?P<args>[^\n;]*)\))?", text)
    return None if match is None else (match.group("args") or "")


def _decorators(text: str, name: str) -> tuple[str, ...]:
    return tuple(
        match.group("args") or ""
        for match in re.finditer(rf"@{re.escape(name)}(?:\((?P<args>[^\n;]*)\))?", text)
    )


def _entity_name(arguments: str | None, class_name: str) -> str:
    return _option(arguments, "name") or _quoted(arguments) or class_name


def _class_indexes(prefix: str) -> list[tuple[str, tuple[str, ...], bool]]:
    indexes: list[tuple[str, tuple[str, ...], bool]] = []
    for arguments in _decorators(prefix, "Index"):
        name = _quoted(arguments)
        columns_match = re.search(r"\[(?P<columns>[^]]+)]", arguments)
        columns = (
            tuple(re.findall(r'["\']([^"\']+)["\']', columns_match.group("columns")))
            if columns_match is not None
            else ()
        )
        if name and columns:
            indexes.append((name, columns, _option(arguments, "unique") == "true"))
    return indexes


def _column(
    member_name: str, annotation: str, decorators: str
) -> tuple[
    DatabaseColumn | None, tuple[str, str, str] | None, tuple[str, tuple[str, ...], bool] | None
]:
    primary_args = _decorator(decorators, "PrimaryColumn")
    generated_args = _decorator(decorators, "PrimaryGeneratedColumn")
    column_args = _decorator(decorators, "Column")
    join_args = _decorator(decorators, "JoinColumn")
    relation_args = _decorator(decorators, "ManyToOne")
    if relation_args is None:
        relation_args = _decorator(decorators, "OneToOne")
    index_args = _decorator(decorators, "Index")
    index: tuple[str, tuple[str, ...], bool] | None = None
    if index_args is not None and (index_name := _quoted(index_args)):
        index = (index_name, (member_name,), _option(index_args, "unique") == "true")
    if (
        column_args is None
        and primary_args is None
        and generated_args is None
        and join_args is None
    ):
        return None, None, index
    arguments = column_args if column_args is not None else primary_args or generated_args or ""
    name = _option(arguments, "name") or member_name
    if join_args is not None:
        name = _option(join_args, "name") or f"{member_name}Id"
    data_type = _option(arguments, "type") or annotation.strip()
    if join_args is not None:
        data_type = "foreign_key"
    default, _ = redact_default(name, _option(arguments, "default"))
    primary = primary_args is not None or generated_args is not None
    nullable = _option(arguments, "nullable") == "true"
    column = DatabaseColumn(name, data_type, nullable, primary, default)
    if join_args is None or relation_args is None:
        return column, None, index
    target_match = re.search(r"=>\s*(?P<target>\w+)", relation_args)
    if target_match is None:
        return column, None, index
    referenced = _option(join_args, "referencedColumnName") or "id"
    return column, (name, target_match.group("target"), referenced), index


def _entity(source: str, match: re.Match[str], alias: str, path: str) -> _Entity:
    class_name = match.group("class")
    opening = match.end() - 1
    closing = _closing_brace(source, opening)
    prefix = match.group("prefix") or ""
    entity = _Entity(
        class_name,
        _entity_name(match.group("entity"), class_name),
        SourceRef(alias, path, source.count("\n", 0, match.start()) + 1),
        indexes=_class_indexes(prefix),
    )
    body = source[opening + 1 : closing]
    for member in _MEMBER.finditer(body):
        column, relation, index = _column(
            member.group("name"), member.group("type"), member.group("decorators")
        )
        if column is not None:
            entity.columns.append(column)
        if relation is not None:
            entity.relations.append(relation)
        if index is not None:
            name, _, unique = index
            actual = column.name if column is not None else member.group("name")
            entity.indexes.append((name, (actual,), unique))
    return entity


def detect_typeorm_entities(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract TypeORM entity columns, relations, primary keys, and named indexes."""
    entities = tuple(
        _entity(source, match, repository_alias, path) for match in _ENTITY.finditer(source)
    )
    table_names = {entity.class_name: entity.table_name for entity in entities}
    tables = tuple(
        DatabaseTable(
            entity.table_name,
            tuple(entity.columns),
            tuple(column.name for column in entity.columns if column.primary_key),
            tuple(
                ForeignKeyRecord((column,), table_names.get(target, target), (referenced,))
                for column, target, referenced in entity.relations
            ),
            entity.source,
        )
        for entity in entities
    )
    indexes = tuple(
        DatabaseIndex(name, entity.table_name, columns, unique, entity.source)
        for entity in entities
        for name, columns, unique in entity.indexes
    )
    return DetectionReport(tables=tables, indexes=indexes)
