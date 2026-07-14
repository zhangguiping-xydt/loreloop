"""Shared normalization for framework-level database facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .authoritative_records import DatabaseColumn, DatabaseIndex, ForeignKeyRecord


class MutableTable(Protocol):
    columns: list[DatabaseColumn]
    foreign_keys: list[ForeignKeyRecord]
    indexes: list[DatabaseIndex]
    column_aliases: dict[str, str]


def normalize_model_tables(
    tables: Sequence[MutableTable], logical_names: Mapping[str, str]
) -> None:
    """Resolve in-file model names and logical Django field names."""
    for table in tables:
        table.foreign_keys = [
            ForeignKeyRecord(
                foreign_key.columns,
                logical_names.get(foreign_key.referenced_table, foreign_key.referenced_table),
                foreign_key.referenced_columns,
            )
            for foreign_key in table.foreign_keys
        ]
        column_names = {column.name for column in table.columns}
        table.indexes = [
            DatabaseIndex(
                index.name,
                index.table,
                tuple(
                    column if column in column_names else table.column_aliases.get(column, column)
                    for column in index.columns
                ),
                index.unique,
                index.source,
            )
            for index in table.indexes
        ]
