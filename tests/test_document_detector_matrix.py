from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loreloop.knowledge.authoritative_git import GitSnapshotError, capture_source_snapshot
from loreloop.knowledge.authoritative_records import DetectionError
from loreloop.knowledge.authoritative_source import detect_source_snapshot


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(path: Path, files: dict[str, str]) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "LoreLoop Test")
    _git(path, "config", "user.email", "loreloop@example.invalid")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(content, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "initial")
    return path


def test_snapshot_detector_combines_root_and_peer_source_without_agent(tmp_path: Path) -> None:
    # Given: a committed backend root and a separately committed frontend peer.
    root = _repository(
        tmp_path / "backend",
        {
            "app.py": """
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health(verbose: bool = False) -> dict[str, bool]:
    return {"ok": True}
""",
            "schema.sql": "CREATE TABLE health_checks (id INTEGER PRIMARY KEY, ok BOOLEAN);\n",
            "pyproject.toml": '[project]\ndependencies=["fastapi>=0.115"]\n',
        },
    )
    peer = _repository(
        tmp_path / "frontend",
        {
            "package.json": '{"dependencies":{"react":"^19.0.0"}}',
            ".env.example": "PUBLIC_API_URL=/api\nAPI_TOKEN=must-not-leak\n",
        },
    )
    snapshot = capture_source_snapshot(root, {"frontend": peer})

    # When: exact committed blobs flow through the detector matrix.
    report = detect_source_snapshot(snapshot, root, {"frontend": peer})

    # Then: optional document applicability and cross-repository evidence are source-derived.
    assert report.interface_document_applicable is True
    assert report.database_document_applicable is True
    assert report.interfaces[0].path == "/health"
    assert report.tables[0].name == "health_checks"
    assert {(item.name, item.source.repository_alias) for item in report.dependencies} >= {
        ("fastapi", "."),
        ("react", "frontend"),
    }
    assert {item.key for item in report.configurations} == {"PUBLIC_API_URL", "API_TOKEN"}
    assert "must-not-leak" not in repr(report)


def test_detector_matrix_emits_no_optional_documents_without_evidence(tmp_path: Path) -> None:
    # Given: committed Python containing no external interface or database schema.
    root = _repository(
        tmp_path / "library", {"maths.py": "def add(a: int, b: int): return a + b\n"}
    )
    snapshot = capture_source_snapshot(root)

    # When: source detection runs.
    report = detect_source_snapshot(snapshot, root)

    # Then: unknown is not invented and both optional document decisions are false.
    assert report.interface_document_applicable is False
    assert report.database_document_applicable is False
    assert {item.qualified_name for item in report.symbols} == {"add"}


def test_detector_matrix_rejects_worktree_drift_after_snapshot(tmp_path: Path) -> None:
    # Given: source changes after the authoritative snapshot was captured.
    root = _repository(tmp_path / "backend", {"app.py": "VALUE = 1\n"})
    snapshot = capture_source_snapshot(root)
    _ = (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    # When / Then: detectors never mix current worktree bytes with the captured commit.
    with pytest.raises(GitSnapshotError, match="uncommitted|changed"):
        _ = detect_source_snapshot(snapshot, root)


def test_application_yaml_with_custom_swagger_settings_is_not_openapi(tmp_path: Path) -> None:
    # Given: valid application configuration using an indentationless YAML sequence and a
    # custom top-level "swagger" namespace, but no OpenAPI/Swagger version marker.
    root = _repository(
        tmp_path / "backend",
        {
            "src/main/resources/application.yml": """
spring:
  kafka:
    bootstrapServers:
    - broker-a:9092
swagger:
  enabled: true
  path: /swagger.json
""",
        },
    )
    snapshot = capture_source_snapshot(root)

    # When: the detector matrix inspects the file.
    report = detect_source_snapshot(snapshot, root)

    # Then: application YAML is not sent to the strict OpenAPI parser.
    assert report.interfaces == ()


def test_swagger_named_business_config_without_version_marker_is_not_openapi(
    tmp_path: Path,
) -> None:
    root = _repository(
        tmp_path / "backend",
        {
            "config/swagger-settings.yml": """
swagger:
  enabled: true
  path: /swagger.json
""",
        },
    )
    snapshot = capture_source_snapshot(root)

    report = detect_source_snapshot(snapshot, root)

    assert report.interfaces == ()


@pytest.mark.parametrize(
    ("path", "content"),
    (
        (
            "config/settings.json",
            '''{
  "service": {
    "swagger": "2.0",
    "enabled": true
  }
}
''',
        ),
        (
            "config/settings.yml",
            '''service:
  openapi: "3.0.3"
  enabled: true
''',
        ),
    ),
)
def test_nested_business_version_fields_are_not_openapi(
    tmp_path: Path, path: str, content: str
) -> None:
    root = _repository(tmp_path / "backend", {path: content})
    snapshot = capture_source_snapshot(root)

    report = detect_source_snapshot(snapshot, root)

    assert report.interfaces == ()


def test_root_openapi_marker_is_detected_when_it_is_not_the_first_json_field(
    tmp_path: Path,
) -> None:
    root = _repository(
        tmp_path / "backend",
        {
            "contracts/openapi.json": '''{
  "info": {"title": "demo", "version": "1"},
  "openapi": "3.0.3",
  "paths": {
    "/health": {
      "get": {"responses": {"200": {"description": "ok"}}}
    }
  }
}
'''
        },
    )
    snapshot = capture_source_snapshot(root)

    report = detect_source_snapshot(snapshot, root)

    assert tuple(item.path for item in report.interfaces) == ("/health",)


def test_snapshot_detection_error_identifies_repository_and_file(tmp_path: Path) -> None:
    root = _repository(
        tmp_path / "backend",
        {"contracts/openapi.yaml": "openapi: 3.0.0\npaths:\n  /pets: [unterminated\n"},
    )
    snapshot = capture_source_snapshot(root)

    with pytest.raises(
        DetectionError,
        match=r"\.:contracts/openapi\.yaml: unterminated YAML scalar",
    ):
        _ = detect_source_snapshot(snapshot, root)
