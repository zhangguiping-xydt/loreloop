from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_detector_config import detect_config_source
from loreloop.knowledge.authoritative_records import DetectionError


def test_config_detector_extracts_python_and_node_dependencies() -> None:
    # Given: standard Python and Node project manifests.
    pyproject = """
[project]
dependencies = ["fastapi>=0.115", "sqlalchemy[asyncio]>=2"]

[project.optional-dependencies]
dev = ["pytest>=8"]
"""
    package = '{"dependencies":{"react":"^19.0.0"},"devDependencies":{"vite":"^7.0.0"}}'

    # When: manifest detection runs.
    python_report = detect_config_source(pyproject, ".", "pyproject.toml")
    node_report = detect_config_source(package, "frontend", "package.json")

    # Then: exact requirements and their scopes are retained.
    assert tuple((item.name, item.scope) for item in python_report.dependencies) == (
        ("fastapi", "runtime"),
        ("sqlalchemy", "runtime"),
        ("pytest", "optional:dev"),
    )
    assert tuple((item.name, item.scope) for item in node_report.dependencies) == (
        ("react", "runtime"),
        ("vite", "development"),
    )


def test_environment_detector_redacts_secrets_and_preserves_safe_defaults() -> None:
    # Given: a committed environment template with safe and sensitive defaults.
    source = "APP_ENV=production\nAPI_TOKEN=must-not-leak\nDATABASE_URL=\n"

    # When: configuration detection runs.
    report = detect_config_source(source, ".", ".env.example")

    # Then: keys and requiredness survive but credential bytes do not.
    assert tuple(item.key for item in report.configurations) == (
        "APP_ENV",
        "API_TOKEN",
        "DATABASE_URL",
    )
    assert report.configurations[0].default == "production"
    assert report.configurations[1].default is None
    assert report.configurations[1].redacted is True
    assert report.configurations[2].required is True
    assert "must-not-leak" not in repr(report)


def test_config_detector_rejects_malformed_supported_manifest() -> None:
    # Given / When / Then: a supported manifest cannot silently disappear on parse failure.
    with pytest.raises(DetectionError, match="invalid JSON"):
        _ = detect_config_source("{", ".", "package.json")
