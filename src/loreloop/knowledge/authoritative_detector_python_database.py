"""Deterministic SQLAlchemy and Django model schema detection."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .authoritative_detector_database_normalize import normalize_model_tables
from .authoritative_detector_python_routes import call_name, constant_text, keyword
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


@dataclass(slots=True)
class _Table:
    name: str
    source: SourceRef
    columns: list[DatabaseColumn] = field(default_factory=list)
    foreign_keys: list[ForeignKeyRecord] = field(default_factory=list)
    indexes: list[DatabaseIndex] = field(default_factory=list)
    column_aliases: dict[str, str] = field(default_factory=dict)


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant):
        return None if node.value is None else str(node.value)
    return None if node is None else ast.unparse(node)


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _bool(call: ast.Call, name: str, default: bool) -> bool:
    value = keyword(call, name)
    return (
        value.value
        if isinstance(value, ast.Constant) and isinstance(value.value, bool)
        else default
    )


def _type(call: ast.Call, fallback: str | None = None) -> str:
    for argument in call.args:
        name = call_name(argument.func) if isinstance(argument, ast.Call) else call_name(argument)
        if name.rsplit(".", 1)[-1] not in {"ForeignKey", "Sequence"}:
            return ast.unparse(argument)
    return fallback or "unspecified"


def _default(column_name: str, call: ast.Call) -> str | None:
    node = keyword(call, "server_default") or keyword(call, "default")
    portable, _ = redact_default(column_name, _literal(node))
    return portable


def _foreign_key(call: ast.Call, column_name: str) -> ForeignKeyRecord | None:
    for argument in call.args:
        if (
            not isinstance(argument, ast.Call)
            or call_name(argument.func).rsplit(".", 1)[-1] != "ForeignKey"
        ):
            continue
        target = _string(argument.args[0]) if argument.args else None
        if target is None or "." not in target:
            continue
        table, referenced_column = target.rsplit(".", 1)
        return ForeignKeyRecord((column_name,), table, (referenced_column,))
    return None


def _sqlalchemy_column(name: str, call: ast.Call, annotation: str | None = None) -> DatabaseColumn:
    explicit = _string(call.args[0]) if call.args else None
    column_name = explicit if explicit and len(call.args) > 1 else name
    type_call = ast.Call(call.func, call.args[1:], call.keywords) if explicit else call
    return DatabaseColumn(
        column_name,
        _type(type_call, annotation),
        _bool(call, "nullable", not _bool(call, "primary_key", False)),
        _bool(call, "primary_key", False),
        _default(column_name, call),
    )


def _mapped_annotation(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Subscript) and call_name(node.value).rsplit(".", 1)[-1] == "Mapped":
        return ast.unparse(node.slice)
    return None


def _sqlalchemy_class(node: ast.ClassDef, alias: str, path: str) -> _Table | None:
    table_name = next(
        (
            _string(item.value)
            for item in node.body
            if isinstance(item, (ast.Assign, ast.AnnAssign))
            and (
                (
                    isinstance(item, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "__tablename__"
                        for target in item.targets
                    )
                )
                or (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id == "__tablename__"
                )
            )
        ),
        None,
    )
    if table_name is None:
        return None
    table = _Table(table_name, SourceRef(alias, path, node.lineno))
    for item in node.body:
        target: str | None = None
        value: ast.AST | None = None
        annotation: str | None = None
        if (
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
        ):
            target, value = item.targets[0].id, item.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            target, value = item.target.id, item.value
            annotation = _mapped_annotation(item.annotation)
        if (
            target
            and isinstance(value, ast.Call)
            and call_name(value.func).rsplit(".", 1)[-1] in {"Column", "mapped_column"}
        ):
            column = _sqlalchemy_column(target, value, annotation)
            table.columns.append(column)
            if foreign_key := _foreign_key(value, column.name):
                table.foreign_keys.append(foreign_key)
        if target == "__table_args__" and value is not None:
            _sqlalchemy_table_args(table, value)
    return table


def _sqlalchemy_table_args(table: _Table, node: ast.AST) -> None:
    values = node.elts if isinstance(node, (ast.Tuple, ast.List)) else (node,)
    for value in values:
        if not isinstance(value, ast.Call) or call_name(value.func).rsplit(".", 1)[-1] != "Index":
            continue
        name = _string(value.args[0]) if value.args else None
        columns = tuple(filter(None, (_string(item) for item in value.args[1:])))
        if name and columns:
            table.indexes.append(
                DatabaseIndex(
                    name, table.name, columns, _bool(value, "unique", False), table.source
                )
            )


def _django_field(name: str, call: ast.Call) -> tuple[DatabaseColumn, ForeignKeyRecord | None]:
    field_type = call_name(call.func).rsplit(".", 1)[-1]
    column_name = _string(keyword(call, "db_column")) or (
        f"{name}_id" if field_type in {"ForeignKey", "OneToOneField"} else name
    )
    maximum = constant_text(keyword(call, "max_length"))
    data_type = field_type if maximum is None else f"{field_type}(max_length={maximum})"
    column = DatabaseColumn(
        column_name,
        data_type,
        _bool(call, "null", False),
        _bool(call, "primary_key", False),
        _default(column_name, call),
    )
    if field_type not in {"ForeignKey", "OneToOneField"} or not call.args:
        return column, None
    target = _string(call.args[0]) or ast.unparse(call.args[0])
    referenced = _string(keyword(call, "to_field")) or "id"
    return column, ForeignKeyRecord((column_name,), target, (referenced,))


def _django_class(node: ast.ClassDef, alias: str, path: str) -> _Table | None:
    if not any(call_name(base).rsplit(".", 1)[-1] == "Model" for base in node.bases):
        return None
    meta = next(
        (item for item in node.body if isinstance(item, ast.ClassDef) and item.name == "Meta"), None
    )
    table_name = node.name
    if meta is not None:
        table_name = (
            next(
                (
                    _string(item.value)
                    for item in meta.body
                    if isinstance(item, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "db_table"
                        for target in item.targets
                    )
                ),
                None,
            )
            or table_name
        )
    table = _Table(table_name, SourceRef(alias, path, node.lineno))
    for item in node.body:
        if not isinstance(item, (ast.Assign, ast.AnnAssign)):
            continue
        target = (
            item.targets[0]
            if isinstance(item, ast.Assign) and len(item.targets) == 1
            else item.target
            if isinstance(item, ast.AnnAssign)
            else None
        )
        value = item.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        if (
            not call_name(value.func).rsplit(".", 1)[-1].endswith("Field")
            and call_name(value.func).rsplit(".", 1)[-1] != "ForeignKey"
        ):
            continue
        column, foreign_key = _django_field(target.id, value)
        table.columns.append(column)
        table.column_aliases[target.id] = column.name
        if foreign_key:
            table.foreign_keys.append(foreign_key)
    if meta is not None:
        for call in ast.walk(meta):
            if isinstance(call, ast.Call) and call_name(call.func).rsplit(".", 1)[-1] == "Index":
                name = _string(keyword(call, "name"))
                fields = keyword(call, "fields")
                columns = (
                    tuple(_string(item) or "" for item in fields.elts)
                    if isinstance(fields, (ast.List, ast.Tuple))
                    else ()
                )
                if name and all(columns):
                    table.indexes.append(
                        DatabaseIndex(name, table.name, columns, False, table.source)
                    )
    return table


def _freeze(table: _Table) -> DatabaseTable:
    primary = tuple(column.name for column in table.columns if column.primary_key)
    return DatabaseTable(
        table.name,
        tuple(table.columns),
        primary,
        tuple(dict.fromkeys(table.foreign_keys)),
        table.source,
    )


def detect_python_database_models(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract SQLAlchemy declarative and Django model tables without imports."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise DetectionError(f"invalid Python source at {path}:{exc.lineno or 1}") from exc
    detected = [
        (node.name, table)
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for table in (
            _sqlalchemy_class(node, repository_alias, path)
            or _django_class(node, repository_alias, path),
        )
        if table is not None
    ]
    names = {class_name: table.name for class_name, table in detected}
    tables = [table for _, table in detected]
    normalize_model_tables(tables, names)
    return DetectionReport(
        tables=tuple(_freeze(table) for table in tables),
        indexes=tuple(index for table in tables for index in table.indexes),
    )
