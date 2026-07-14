"""Static schema extraction for common Alembic and Django migrations."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .authoritative_detector_python_routes import call_name, keyword
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
    columns: dict[str, DatabaseColumn] = field(default_factory=dict)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyRecord] = field(default_factory=list)


def _text(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return None if node.value is None else str(node.value)
    return ast.unparse(node)


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _strings(node: ast.AST | None) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ()
    return tuple(value for item in node.elts if (value := _string(item)) is not None)


def _boolean(call: ast.Call, name: str, default: bool) -> bool:
    value = keyword(call, name)
    return (
        value.value
        if isinstance(value, ast.Constant) and isinstance(value.value, bool)
        else default
    )


def _column(
    call: ast.Call, fallback_name: str | None = None
) -> tuple[DatabaseColumn, ForeignKeyRecord | None] | None:
    kind = call_name(call.func).rsplit(".", 1)[-1]
    if kind == "Column":
        name = _string(call.args[0]) if call.args else fallback_name
        if name is None:
            return None
        type_node = call.args[1] if len(call.args) > 1 else None
        data_type = ast.unparse(type_node) if type_node is not None else "unspecified"
        primary = _boolean(call, "primary_key", False)
        default, _ = redact_default(
            name, _text(keyword(call, "server_default") or keyword(call, "default"))
        )
        column = DatabaseColumn(
            name,
            data_type,
            _boolean(call, "nullable", not primary),
            primary,
            default,
        )
        foreign_key = _inline_foreign_key(call, name)
        return column, foreign_key
    if not kind.endswith("Field") and kind not in {"ForeignKey", "OneToOneField"}:
        return None
    if fallback_name is None:
        return None
    name = _string(keyword(call, "db_column")) or (
        f"{fallback_name}_id" if kind in {"ForeignKey", "OneToOneField"} else fallback_name
    )
    primary = _boolean(call, "primary_key", False)
    default, _ = redact_default(name, _text(keyword(call, "default")))
    column = DatabaseColumn(
        name,
        ast.unparse(call.func),
        _boolean(call, "null", False),
        primary,
        default,
    )
    target_node = keyword(call, "to") or (call.args[0] if call.args else None)
    target = _string(target_node) or (_text(target_node) if target_node is not None else None)
    if kind not in {"ForeignKey", "OneToOneField"} or target is None:
        return column, None
    return column, ForeignKeyRecord((name,), target, (_string(keyword(call, "to_field")) or "id",))


def _inline_foreign_key(call: ast.Call, name: str) -> ForeignKeyRecord | None:
    for argument in call.args[1:]:
        if not isinstance(argument, ast.Call):
            continue
        if call_name(argument.func).rsplit(".", 1)[-1] != "ForeignKey" or not argument.args:
            continue
        target = _string(argument.args[0])
        if target and "." in target:
            table, column = target.rsplit(".", 1)
            return ForeignKeyRecord((name,), table, (column,))
    return None


def _table(tables: dict[str, _Table], name: str, alias: str, path: str, node: ast.AST) -> _Table:
    return tables.setdefault(name, _Table(name, SourceRef(alias, path, getattr(node, "lineno", 1))))


def _put(table: _Table, value: tuple[DatabaseColumn, ForeignKeyRecord | None] | None) -> None:
    if value is None:
        return
    column, foreign_key = value
    table.columns[column.name] = column
    if column.primary_key and column.name not in table.primary_key:
        table.primary_key.append(column.name)
    if foreign_key is not None and foreign_key not in table.foreign_keys:
        table.foreign_keys.append(foreign_key)


def _alembic(
    call: ast.Call,
    tables: dict[str, _Table],
    indexes: list[DatabaseIndex],
    alias: str,
    path: str,
) -> None:
    operation = call_name(call.func)
    if not operation.startswith("op."):
        return
    name = operation.rsplit(".", 1)[-1]
    table_name = _string(call.args[0]) if call.args else None
    if name == "create_table" and table_name:
        table = _table(tables, table_name, alias, path, call)
        for argument in call.args[1:]:
            if not isinstance(argument, ast.Call):
                continue
            constraint = call_name(argument.func).rsplit(".", 1)[-1]
            if constraint == "PrimaryKeyConstraint":
                table.primary_key = [value for item in argument.args if (value := _string(item))]
            elif constraint == "ForeignKeyConstraint" and len(argument.args) >= 2:
                local, remote = _strings(argument.args[0]), _strings(argument.args[1])
                if local and remote and all("." in value for value in remote):
                    parents = tuple(value.rsplit(".", 1)[0] for value in remote)
                    if len(set(parents)) == 1:
                        table.foreign_keys.append(
                            ForeignKeyRecord(
                                local,
                                parents[0],
                                tuple(value.rsplit(".", 1)[1] for value in remote),
                            )
                        )
            else:
                _put(table, _column(argument))
    elif (
        name == "add_column"
        and table_name
        and len(call.args) > 1
        and isinstance(call.args[1], ast.Call)
    ):
        _put(_table(tables, table_name, alias, path, call), _column(call.args[1]))
    elif name == "create_foreign_key" and len(call.args) >= 5:
        source_table = _string(call.args[1])
        target_table = _string(call.args[2])
        local, remote = _strings(call.args[3]), _strings(call.args[4])
        if source_table and target_table and local and remote:
            _table(tables, source_table, alias, path, call).foreign_keys.append(
                ForeignKeyRecord(local, target_table, remote)
            )
    elif name == "create_index" and len(call.args) >= 3:
        index_name, indexed_table = _string(call.args[0]), _string(call.args[1])
        columns = _strings(call.args[2])
        if index_name and indexed_table and columns:
            indexes.append(
                DatabaseIndex(
                    index_name,
                    indexed_table,
                    columns,
                    _boolean(call, "unique", False),
                    SourceRef(alias, path, call.lineno),
                )
            )


def _dict_value(node: ast.AST | None, key: str) -> ast.AST | None:
    if not isinstance(node, ast.Dict):
        return None
    return next(
        (value for item, value in zip(node.keys, node.values, strict=True) if _string(item) == key),
        None,
    )


def _django(
    call: ast.Call, tables: dict[str, _Table], indexes: list[DatabaseIndex], alias: str, path: str
) -> None:
    operation = call_name(call.func).rsplit(".", 1)[-1]
    if operation == "CreateModel":
        model = _string(keyword(call, "name"))
        fields = keyword(call, "fields")
        options = keyword(call, "options")
        table_name = _string(_dict_value(options, "db_table")) or model
        if table_name is None or not isinstance(fields, (ast.List, ast.Tuple)):
            return
        table = _table(tables, table_name, alias, path, call)
        for item in fields.elts:
            if (
                isinstance(item, ast.Tuple)
                and len(item.elts) == 2
                and isinstance(item.elts[1], ast.Call)
            ):
                _put(table, _column(item.elts[1], _string(item.elts[0])))
    elif operation == "AddField":
        model = _string(keyword(call, "model_name"))
        name = _string(keyword(call, "name"))
        field_call = keyword(call, "field")
        if model and name and isinstance(field_call, ast.Call):
            _put(_table(tables, model, alias, path, call), _column(field_call, name))
    elif operation == "AddIndex":
        model = _string(keyword(call, "model_name"))
        index_call = keyword(call, "index")
        if model and isinstance(index_call, ast.Call):
            name = _string(keyword(index_call, "name"))
            columns = _strings(keyword(index_call, "fields"))
            if name and columns:
                indexes.append(
                    DatabaseIndex(name, model, columns, False, SourceRef(alias, path, call.lineno))
                )


def detect_python_migrations(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract deterministic schema facts from Alembic and Django operation calls."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise DetectionError(f"invalid Python source at {path}:{exc.lineno or 1}") from exc
    tables: dict[str, _Table] = {}
    indexes: list[DatabaseIndex] = []
    calls = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
        key=lambda node: node.lineno,
    )
    for call in calls:
        _alembic(call, tables, indexes, repository_alias, path)
        _django(call, tables, indexes, repository_alias, path)
    frozen = tuple(
        DatabaseTable(
            table.name,
            tuple(table.columns.values()),
            tuple(table.primary_key),
            tuple(dict.fromkeys(table.foreign_keys)),
            table.source,
        )
        for table in tables.values()
    )
    return DetectionReport(tables=frozen, indexes=tuple(indexes))
