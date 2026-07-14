from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loreloop.cli import main
from loreloop.knowledge.repos import save_repos


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


def _agent_must_not_run(_name: str) -> None:
    raise AssertionError("agent must not run")


def test_cli_docs_export_builds_flat_six_plus_two_without_agent_or_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a clean repository with explicit interface, database, config, and dependency evidence.
    repo = _repository(
        tmp_path / "demo",
        {
            "app.py": """
import os
from fastapi import FastAPI
app = FastAPI()
TOKEN = os.getenv("API_TOKEN", "must-not-leak")

@app.post("/users/<script>")
def create_user(name: str) -> dict[str, str]:
    if current_user.role != "admin":
        raise PermissionError
    return {"name": name}
""",
            "schema.sql": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n",
            "pyproject.toml": '[project]\ndependencies=["fastapi>=0.115"]\n',
            "docs/requirements.md": """
| ID | 需求描述 | 验收标准 |
|---|---|---|
| REQ-BIZ-001 | 管理员可以创建用户 | 创建成功后返回用户标识 |
""",
        },
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr("loreloop.cli._inference_agent", _agent_must_not_run)
    target = tmp_path / "export"

    # When: the existing docs export surface is used without init, a key, or a knowledge DB.
    result = main(
        [
            "knowledge",
            "export",
            "--format",
            "docs",
            "--output",
            str(target),
            "--project-name",
            "demo",
            "--requirements",
            "docs/requirements.md",
        ]
    )

    # Then: six fixed and two evidence-supported Markdown documents are written flat.
    assert result == 0
    assert "exported 8 reverse-engineered documents" in capsys.readouterr().out
    assert {path.name for path in target.glob("*.md")} == {
        "demo-功能清单.md",
        "demo-需求规格.md",
        "demo-系统架构.md",
        "demo-详细设计.md",
        "demo-用户手册.md",
        "demo-验收规格.md",
        "demo-接口契约.md",
        "demo-数据库设计.md",
    }
    capsule = target / ".loreloop-export.json"
    assert capsule.is_file() and '"package_id"' in capsule.read_text(encoding="utf-8")
    interface = (target / "demo-接口契约.md").read_text(encoding="utf-8")
    database = (target / "demo-数据库设计.md").read_text(encoding="utf-8")
    capabilities = (target / "demo-功能清单.md").read_text(encoding="utf-8")
    requirements = (target / "demo-需求规格.md").read_text(encoding="utf-8")
    architecture = (target / "demo-系统架构.md").read_text(encoding="utf-8")
    detailed = (target / "demo-详细设计.md").read_text(encoding="utf-8")
    user_guide = (target / "demo-用户手册.md").read_text(encoding="utf-8")
    assert "POST" in interface and "/users" in interface and "name:str*" in interface
    assert "<script>" not in interface and "&lt;script&gt;" in interface
    assert "## 源码能力域" in capabilities and capabilities != interface
    assert "repository" in interface and ":app.py#L" in interface
    assert "users" in database and "id" in database and "name" in database
    assert "## ER 关系图" in database and "flowchart LR" in database
    assert "REQ-BIZ-001" in requirements and "管理员可以创建用户" in requirements
    assert "current_user.role != 'admin'" in requirements
    assert "## HTTP 接口" not in requirements
    assert "## HTTP 接口" not in architecture
    assert "## HTTP 接口" not in detailed
    assert "## HTTP 接口" not in user_guide
    assert requirements != user_guide
    acceptance = (target / "demo-验收规格.md").read_text(encoding="utf-8")
    assert "创建成功后返回用户标识" in acceptance
    assert "## 证据索引" not in "".join(
        path.read_text(encoding="utf-8") for path in target.glob("*.md")
    )
    assert "must-not-leak" not in "".join(
        path.read_text(encoding="utf-8") for path in target.glob("*.md")
    )
    assert not (repo / ".loreloop").exists()


def test_package_export_defaults_to_baseline_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "demo", {"app.py": "VALUE = 1\n"})
    monkeypatch.chdir(repo)

    assert main(["knowledge", "export", "--format", "package"]) == 0

    package = repo / "baseline.zip"
    assert package.is_file()
    assert main(["knowledge", "replay", str(package)]) == 0


def test_cli_docs_export_emits_only_six_documents_without_optional_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a source library with no HTTP/CLI route and no explicit DDL.
    repo = _repository(tmp_path / "library", {"maths.py": "def add(a, b): return a + b\n"})
    monkeypatch.chdir(repo)
    target = tmp_path / "export"

    # When: documents are exported.
    assert main(["knowledge", "export", "--format", "docs", "--output", str(target)]) == 0

    # Then: optional interface/database documents are absent rather than unknown placeholders.
    assert len(tuple(target.glob("*.md"))) == 6
    assert not tuple(target.glob("*接口契约.md"))
    assert not tuple(target.glob("*数据库设计.md"))
    requirements = (target / "library-需求规格.md").read_text(encoding="utf-8")
    user_guide = (target / "library-用户手册.md").read_text(encoding="utf-8")
    acceptance = (target / "library-验收规格.md").read_text(encoding="utf-8")
    assert "不能作为完整业务需求规格" in requirements
    assert "无法形成可执行用户操作手册" in user_guide
    assert "不能用于正式项目验收" in acceptance
    assert len({requirements, user_guide, acceptance}) == 3


def test_cli_package_export_supports_a_non_git_aggregate_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a non-Git project workspace with two declared Git member repositories.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = _repository(
        workspace / "backend",
        {"app.py": '@app.get("/health")\ndef health(): return True\n'},
    )
    frontend = _repository(
        workspace / "frontend",
        {"app.ts": "export function ready(): boolean { return true }\n"},
    )
    save_repos(workspace, {"backend": backend, "frontend": frontend})
    monkeypatch.chdir(workspace)
    target = tmp_path / "workspace-knowledge.zip"

    # When: the normal deliverable package command runs at the aggregate root.
    result = main(
        [
            "knowledge",
            "export",
            "--format",
            "package",
            "--output",
            str(target),
            "--project-name",
            "workspace",
        ]
    )

    # Then: no synthetic root Git repository is required and the package replays.
    assert result == 0
    error = capsys.readouterr().err
    assert "across 2 repositories" in error
    assert main(["knowledge", "replay", str(target)]) == 0


def test_cli_docs_export_refuses_nonempty_output_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an operator-owned output directory.
    repo = _repository(tmp_path / "demo", {"app.py": "VALUE = 1\n"})
    monkeypatch.chdir(repo)
    target = tmp_path / "export"
    target.mkdir()
    keep = target / "keep.txt"
    _ = keep.write_text("operator file", encoding="utf-8")

    # When: export runs without explicit overwrite authorization.
    result = main(["knowledge", "export", "--format", "docs", "--output", str(target)])

    # Then: no document is partially written and the operator file is preserved.
    assert result == 2
    assert "output directory is not empty" in capsys.readouterr().err
    assert keep.read_text(encoding="utf-8") == "operator file"
    assert not tuple(target.glob("*.md"))


def test_cli_docs_export_reports_dirty_source_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: source changed after the last commit.
    repo = _repository(tmp_path / "demo", {"app.py": "VALUE = 1\n"})
    _ = (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    # When: export is requested.
    result = main(["knowledge", "export", "--format", "docs", "--output", str(tmp_path / "export")])

    # Then: the user receives a recoverable source-state error and no traceback.
    assert result == 2
    error = capsys.readouterr().err
    assert error.startswith(
        "capturing clean Git source snapshot...\nerror: source document export failed"
    )
    assert "uncommitted source changes" in error
    assert "Traceback" not in error


def test_force_export_removes_stale_optional_docs_and_preserves_operator_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an earlier eight-document export plus an unrelated operator file.
    repo = _repository(
        tmp_path / "demo",
        {
            "app.py": '@app.get("/health")\ndef health(): return True\n',
            "schema.sql": "CREATE TABLE health (id INTEGER PRIMARY KEY);\n",
        },
    )
    monkeypatch.chdir(repo)
    target = tmp_path / "export"
    command = ["knowledge", "export", "--format", "docs", "--output", str(target)]
    assert main(command) == 0
    keep = target / "keep.txt"
    _ = keep.write_text("operator file", encoding="utf-8")
    _ = (repo / "app.py").write_text("def health(): return True\n", encoding="utf-8")
    _ = (repo / "schema.sql").write_text("SELECT 1;\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "remove optional contracts")

    # When: the same managed document set is regenerated with explicit force.
    assert main([*command, "--force"]) == 0

    # Then: managed stale files disappear while unrelated output remains untouched.
    assert len(tuple(target.glob("*.md"))) == 6
    assert not tuple(target.glob("*接口契约.md"))
    assert not tuple(target.glob("*数据库设计.md"))
    assert keep.read_text(encoding="utf-8") == "operator file"


def test_cli_docs_export_rejects_symlinked_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an output path redirected to an operator-owned directory.
    repo = _repository(tmp_path / "demo", {"app.py": "VALUE = 1\n"})
    monkeypatch.chdir(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / "export"
    try:
        target.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    # When: source documents are requested with force.
    result = main(["knowledge", "export", "--format", "docs", "--output", str(target), "--force"])

    # Then: the redirect is rejected before any output is written.
    assert result == 2
    assert "must not be a symlink" in capsys.readouterr().err
    assert not tuple(outside.iterdir())
