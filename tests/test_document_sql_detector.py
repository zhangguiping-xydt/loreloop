from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_detector_sql import detect_sql_source
from loreloop.knowledge.authoritative_records import DetectionError


def test_sql_detector_extracts_columns_keys_foreign_keys_and_indexes() -> None:
    # Given: explicit schema DDL with both inline and table-level constraints.
    sql = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        api_token TEXT DEFAULT 'must-not-leak'
    );
    CREATE TABLE orders (
        id INTEGER NOT NULL,
        user_id INTEGER REFERENCES users(id),
        amount DECIMAL(10, 2) DEFAULT 0,
        PRIMARY KEY (id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE UNIQUE INDEX idx_users_email ON users (email);
    """

    # When: deterministic SQL detection runs.
    report = detect_sql_source(sql, ".", "schema.sql")

    # Then: the database document has concrete field, key, relation, and index evidence.
    assert report.database_document_applicable is True
    assert tuple(table.name for table in report.tables) == ("users", "orders")
    assert report.tables[0].primary_key == ("id",)
    assert report.tables[0].columns[2].default is None
    assert report.tables[1].columns[2].data_type == "DECIMAL(10, 2)"
    assert report.tables[1].foreign_keys[0].referenced_table == "users"
    assert len(report.tables[1].foreign_keys) == 1
    assert report.indexes[0].columns == ("email",)
    assert report.indexes[0].unique is True


def test_sql_detector_rejects_unclosed_explicit_schema() -> None:
    # Given: source claims to create a table but the schema is incomplete.
    # When / Then: applicability fails closed instead of silently omitting the table.
    with pytest.raises(DetectionError, match="closing parenthesis"):
        _ = detect_sql_source("CREATE TABLE users (id INTEGER", ".", "schema.sql")
