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


def test_sql_detector_treats_mysql_inline_keys_as_indexes_not_columns() -> None:
    sql = """
    CREATE TABLE employee_ratio (
        id BIGINT NOT NULL,
        employee_id VARCHAR(32) NOT NULL,
        city_code VARCHAR(32) NOT NULL,
        PRIMARY KEY (id),
        KEY `idx_employee_city` (`employee_id`, `city_code`),
        UNIQUE KEY `uk_employee` (`employee_id`)
    );
    """

    report = detect_sql_source(sql, "backend", "schema.sql")

    assert tuple(column.name for column in report.tables[0].columns) == (
        "id",
        "employee_id",
        "city_code",
    )
    assert tuple((item.name, item.columns, item.unique) for item in report.indexes) == (
        ("idx_employee_city", ("employee_id", "city_code"), False),
        ("uk_employee", ("employee_id",), True),
    )


def test_sql_detector_accepts_legacy_unquoted_unicode_column_names() -> None:
    sql = """
    CREATE TABLE CRT_ATM_KQ_DAY_SALARY_STATIC (
        员工编号 VARCHAR2(32) NOT NULL,
        事假小时 NUMBER(10,2) DEFAULT 0,
        PRIMARY KEY (员工编号)
    );
    """

    report = detect_sql_source(sql, ".", "legacy.sql")

    assert report.tables[0].name == "CRT_ATM_KQ_DAY_SALARY_STATIC"
    assert tuple(column.name for column in report.tables[0].columns) == (
        "员工编号",
        "事假小时",
    )
    assert report.tables[0].primary_key == ("员工编号",)


def test_sql_detector_records_database_links_as_architecture_dependencies() -> None:
    sql = "CREATE PUBLIC DATABASE LINK hrdb.us.oracle.com CONNECT TO app USING 'HRDB';\n"

    report = detect_sql_source(sql, ".", "DB/DB_LINKs/hrdb.sql")

    assert [(item.name, item.scope) for item in report.dependencies] == [
        ("hrdb.us.oracle.com", "database_link")
    ]


def test_sql_detector_ignores_commented_and_granted_database_link_phrases() -> None:
    sql = """
    -- Create database link
    CREATE DATABASE LINK ATM.US.ORACLE.COM CONNECT TO app USING 'ATM';
    /* CREATE DATABASE LINK ignored.example CONNECT TO app USING 'IGNORED'; */
    GRANT CREATE DATABASE LINK TO ATM;
    """

    report = detect_sql_source(sql, ".", "DB/DB_LINKs/atm.sql")

    assert [(item.name, item.scope) for item in report.dependencies] == [
        ("ATM.US.ORACLE.COM", "database_link")
    ]


def test_sql_detector_rejects_unclosed_explicit_schema() -> None:
    # Given: source claims to create a table but the schema is incomplete.
    # When / Then: applicability fails closed instead of silently omitting the table.
    with pytest.raises(DetectionError, match="closing parenthesis"):
        _ = detect_sql_source("CREATE TABLE users (id INTEGER", ".", "schema.sql")
