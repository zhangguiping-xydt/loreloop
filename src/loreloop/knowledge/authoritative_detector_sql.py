"""Small deterministic SQL schema detector for explicit DDL evidence."""

from __future__ import annotations

import re
from typing import Final

from .authoritative_records import (
    DatabaseColumn,
    DatabaseIndex,
    DatabaseTable,
    DependencyRecord,
    DetectionError,
    DetectionReport,
    ForeignKeyRecord,
    SourceRef,
)
from .authoritative_redaction import redact_default

_IDENT: Final = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[^\W\d][\w$.]*)'
_CREATE_TABLE: Final = re.compile(
    rf"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_IDENT})\s*\(",
    re.IGNORECASE,
)
_CREATE_INDEX: Final = re.compile(
    r"\bCREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    + rf"(?P<name>{_IDENT})\s+ON\s+(?P<table>{_IDENT})\s*\((?P<columns>[^)]+)\)",
    re.IGNORECASE,
)
_INLINE_INDEX: Final = re.compile(
    r"^(?P<unique>UNIQUE\s+)?(?:KEY|INDEX)\s+" + rf"(?P<name>{_IDENT})\s*\((?P<columns>[^)]+)\)",
    re.IGNORECASE,
)
_FOREIGN_KEY: Final = re.compile(
    r"FOREIGN\s+KEY\s*\((?P<columns>[^)]+)\)\s+REFERENCES\s+"
    + rf"(?P<table>{_IDENT})\s*\((?P<refs>[^)]+)\)",
    re.IGNORECASE,
)
_REFERENCES: Final = re.compile(
    rf"\bREFERENCES\s+(?P<table>{_IDENT})\s*\((?P<column>[^)]+)\)",
    re.IGNORECASE,
)
_DEFAULT: Final = re.compile(
    r"\bDEFAULT\s+(?P<value>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[^\s,]+)",
    re.IGNORECASE,
)
_CONSTRAINT: Final = re.compile(
    r"\s+(?:PRIMARY\s+KEY|NOT\s+NULL|NULL|UNIQUE|DEFAULT|REFERENCES|CHECK|COLLATE)\b",
    re.IGNORECASE,
)
_DATABASE_LINK: Final = re.compile(
    rf"^\s*CREATE\s+(?:SHARED\s+|PUBLIC\s+)?DATABASE\s+LINK\s+(?P<name>{_IDENT})",
    re.IGNORECASE | re.MULTILINE,
)


def _name(raw: str) -> str:
    if raw[0] == "[" and raw[-1] == "]":
        return raw[1:-1]
    if raw[0] in {'"', "`"} and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _names(raw: str) -> tuple[str, ...]:
    return tuple(_name(item.strip()) for item in raw.split(",") if item.strip())


def _mask_sql_comments(sql: str) -> str:
    """Mask comments without changing offsets or treating comment markers in strings as syntax."""
    characters = list(sql)
    quote: str | None = None
    index = 0
    while index < len(characters):
        character = characters[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(characters) and characters[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            index += 1
            continue
        if character == "-" and index + 1 < len(characters) and characters[index + 1] == "-":
            while index < len(characters) and characters[index] not in {"\r", "\n"}:
                characters[index] = " "
                index += 1
            continue
        if character == "/" and index + 1 < len(characters) and characters[index + 1] == "*":
            characters[index] = " "
            characters[index + 1] = " "
            index += 2
            while index < len(characters):
                if (
                    characters[index] == "*"
                    and index + 1 < len(characters)
                    and characters[index + 1] == "/"
                ):
                    characters[index] = " "
                    characters[index + 1] = " "
                    index += 2
                    break
                if characters[index] not in {"\r", "\n"}:
                    characters[index] = " "
                index += 1
            continue
        index += 1
    return "".join(characters)


def _closing_parenthesis(sql: str, opening: int) -> int:
    depth = 0
    quote: str | None = None
    for index in range(opening, len(sql)):
        character = sql[index]
        if quote is not None:
            if character == quote and (index + 1 == len(sql) or sql[index + 1] != quote):
                quote = None
            elif character == quote and sql[index + 1] == quote:
                continue
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    raise DetectionError("CREATE TABLE has no closing parenthesis")


def _split_clauses(body: str) -> tuple[str, ...]:
    clauses: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, character in enumerate(body):
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            clauses.append(body[start:index].strip())
            start = index + 1
    clauses.append(body[start:].strip())
    return tuple(clause for clause in clauses if clause)


def _column(clause: str) -> DatabaseColumn | None:
    if re.match(
        r"^(?:CONSTRAINT\b|PRIMARY\s+KEY\b|FOREIGN\s+KEY\b|"
        r"(?:UNIQUE\s+)?(?:KEY|INDEX)\b|UNIQUE\b|CHECK\b)",
        clause,
        re.IGNORECASE,
    ):
        return None
    match = re.match(rf"(?P<name>{_IDENT})\s+(?P<rest>.+)$", clause, re.DOTALL)
    if match is None:
        raise DetectionError(f"unsupported column definition: {clause}")
    rest = match.group("rest").strip()
    constraint = _CONSTRAINT.search(rest)
    data_type = rest if constraint is None else rest[: constraint.start()]
    default_match = _DEFAULT.search(rest)
    default = None if default_match is None else default_match.group("value")
    column_name = _name(match.group("name"))
    portable_default, _ = redact_default(column_name, default)
    return DatabaseColumn(
        name=column_name,
        data_type=" ".join(data_type.split()),
        nullable=re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE) is None,
        primary_key=re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE) is not None,
        default=portable_default,
    )


def _table(
    sql: str, match: re.Match[str], source: SourceRef
) -> tuple[DatabaseTable, tuple[DatabaseIndex, ...], int]:
    opening = match.end() - 1
    closing = _closing_parenthesis(sql, opening)
    clauses = _split_clauses(sql[opening + 1 : closing])
    columns = tuple(column for clause in clauses if (column := _column(clause)) is not None)
    primary = tuple(column.name for column in columns if column.primary_key)
    foreign_keys: list[ForeignKeyRecord] = []
    indexes: list[DatabaseIndex] = []
    for clause in clauses:
        inline_index = _INLINE_INDEX.match(clause)
        if inline_index is not None:
            indexes.append(
                DatabaseIndex(
                    name=_name(inline_index.group("name")),
                    table=_name(match.group("name")),
                    columns=_names(inline_index.group("columns")),
                    unique=inline_index.group("unique") is not None,
                    source=source,
                )
            )
            continue
        table_match = _FOREIGN_KEY.search(clause)
        if table_match is not None:
            foreign_keys.append(
                ForeignKeyRecord(
                    columns=_names(table_match.group("columns")),
                    referenced_table=_name(table_match.group("table")),
                    referenced_columns=_names(table_match.group("refs")),
                )
            )
            continue
        column = _column(clause)
        reference = _REFERENCES.search(clause)
        if column is not None and reference is not None:
            foreign_keys.append(
                ForeignKeyRecord(
                    columns=(column.name,),
                    referenced_table=_name(reference.group("table")),
                    referenced_columns=_names(reference.group("column")),
                )
            )
        table_primary = re.search(r"\bPRIMARY\s+KEY\s*\(([^)]+)\)", clause, re.IGNORECASE)
        if table_primary is not None:
            primary = _names(table_primary.group(1))
    return (
        DatabaseTable(
            name=_name(match.group("name")),
            columns=columns,
            primary_key=primary,
            foreign_keys=tuple(dict.fromkeys(foreign_keys)),
            source=source,
        ),
        tuple(indexes),
        closing,
    )


def detect_sql_source(
    sql: str,
    repository_alias: str,
    path: str,
    base_line: int = 1,
) -> DetectionReport:
    """Extract explicit tables, columns, keys, constraints, and indexes."""
    sql = _mask_sql_comments(sql)
    tables: list[DatabaseTable] = []
    inline_indexes: list[DatabaseIndex] = []
    position = 0
    while match := _CREATE_TABLE.search(sql, position):
        line = base_line + sql[: match.start()].count("\n")
        table, table_indexes, position = _table(sql, match, SourceRef(repository_alias, path, line))
        tables.append(table)
        inline_indexes.extend(table_indexes)
    indexes = (
        *inline_indexes,
        *(
            DatabaseIndex(
                name=_name(match.group("name")),
                table=_name(match.group("table")),
                columns=_names(match.group("columns")),
                unique=match.group("unique") is not None,
                source=SourceRef(
                    repository_alias,
                    path,
                    base_line + sql[: match.start()].count("\n"),
                ),
            )
            for match in _CREATE_INDEX.finditer(sql)
        ),
    )
    dependencies = tuple(
        DependencyRecord(
            _name(match.group("name")),
            None,
            "database_link",
            SourceRef(
                repository_alias,
                path,
                base_line + sql[: match.start()].count("\n"),
            ),
        )
        for match in _DATABASE_LINK.finditer(sql)
    )
    return DetectionReport(tables=tuple(tables), indexes=indexes, dependencies=dependencies)
