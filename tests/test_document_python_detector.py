from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_detector_python import detect_python_source
from loreloop.knowledge.authoritative_records import DetectionError


def test_python_detector_extracts_interfaces_config_permissions_symbols_and_sql() -> None:
    # Given: representative FastAPI, Typer, environment, permission, and embedded DDL source.
    source = '''
import os
from fastapi import APIRouter
import typer

router = APIRouter()
cli = typer.Typer()
API_TOKEN = os.getenv("API_TOKEN", "must-not-leak")

@router.post("/users")
async def create_user(name: str, enabled: bool = True) -> dict[str, str]:
    if current_user.role != "admin":
        raise PermissionError
    return {"name": name}

@cli.command(name="sync-users")
def sync_users(limit: int = 10) -> None:
    return None

SCHEMA = """CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);"""
'''

    # When: the Python detector parses its AST without executing the module.
    report = detect_python_source(source, "backend", "app.py")

    # Then: documents receive concrete source-backed facts and no secret default.
    assert tuple((item.kind, item.method, item.path) for item in report.interfaces) == (
        ("http", "POST", "/users"),
        ("cli", "COMMAND", "sync-users"),
    )
    assert report.interfaces[0].parameters[0].name == "name"
    assert report.interfaces[0].return_type == "dict[str, str]"
    assert {symbol.qualified_name for symbol in report.symbols} == {"create_user", "sync_users"}
    assert report.configurations[0].key == "API_TOKEN"
    assert report.configurations[0].default is None
    assert report.configurations[0].redacted is True
    assert report.permissions[0].subject == "current_user.role"
    assert report.permissions[0].expected == "'admin'"
    assert {dependency.name for dependency in report.dependencies} == {"os", "fastapi", "typer"}
    assert report.tables[0].name == "users"
    assert "must-not-leak" not in repr(report)


def test_python_detector_extracts_django_path_without_importing_django() -> None:
    # Given: a Django-style URL declaration.
    source = 'urlpatterns = [path("health/", views.health, name="health")]\n'

    # When: static detection runs.
    report = detect_python_source(source, ".", "urls.py")

    # Then: the caller-visible route is retained even without executing framework code.
    assert len(report.interfaces) == 1
    assert report.interfaces[0].path == "health/"
    assert report.interfaces[0].name == "views.health"


def test_python_detector_rejects_invalid_source() -> None:
    # Given / When / Then: syntax errors fail closed with a source location.
    with pytest.raises(DetectionError, match="broken.py:1"):
        _ = detect_python_source("def broken(:\n", ".", "broken.py")
