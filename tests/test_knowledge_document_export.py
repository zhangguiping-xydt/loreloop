from __future__ import annotations

import hashlib
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
            "src/views/users.vue": """
<template><button @click="createUser">Create</button></template>
<script>export default { name: "UsersPage" }</script>
""",
            "tests/test_users.py": "def test_create_user(): assert True\n",
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
    assert "## 系统能力概览" in capabilities and capabilities != interface
    assert "## 已实现功能清单" in capabilities
    assert "仓库" in interface and ":app.py#L" in interface
    assert "users" in database and "id" in database and "name" in database
    assert "## ER 关系图" in database and "flowchart LR" in database
    assert "REQ-BIZ-001" in requirements and "管理员可以创建用户" in requirements
    assert "current_user.role != 'admin'" in requirements
    assert "## HTTP 接口" not in requirements
    assert "## HTTP 接口" not in architecture
    assert "## HTTP 接口" not in detailed
    assert "## HTTP 接口" not in user_guide
    assert requirements != user_guide
    assert "UsersPage" in user_guide and "click:createUser" in user_guide
    assert interface.index("## HTTP 接口") < interface.index("## 契约可信边界")
    assert "## 证据附录：完整可召回事实" not in interface
    assert "证据化人类视图" in interface
    assert "Capsule Agent 视图" in interface
    assert "未发现结构化错误码或异常响应契约" in interface
    acceptance = (target / "demo-验收规格.md").read_text(encoding="utf-8")
    assert "创建成功后返回用户标识" in acceptance
    assert "test_create_user" in acceptance and "pytest" in acceptance
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


def test_docs_export_defaults_to_readable_baseline_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "demo", {"app.py": "VALUE = 1\n"})
    monkeypatch.chdir(repo)

    assert main(["knowledge", "export", "--format", "docs"]) == 0

    baseline = repo / "baseline"
    assert baseline.is_dir()
    assert len(tuple(baseline.glob("*.md"))) == 6
    assert (baseline / ".loreloop-export.json").is_file()
    assert main(["knowledge", "replay", str(baseline)]) == 0


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
    assert "不能替代未来需求决策" in requirements
    assert "当前没有可确认的 UI/CLI 入口" in user_guide
    assert "当前没有提交态正式验收条款" in acceptance
    assert len({requirements, user_guide, acceptance}) == 3


def test_docs_export_preserves_and_detects_gb18030_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repository(tmp_path / "legacy", {"README.md": "legacy project\n"})
    raw_sql = (
        "-- 历史用户表\n"
        "CREATE TABLE legacy_users (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL);\n"
    ).encode("gb18030")
    (repo / "schema.sql").write_bytes(raw_sql)
    _git(repo, "add", "schema.sql")
    _git(repo, "commit", "-m", "add legacy schema")
    monkeypatch.chdir(repo)
    target = tmp_path / "baseline"

    assert main(["knowledge", "export", "--format", "docs", "--output", str(target)]) == 0

    database = (target / "legacy-数据库设计.md").read_text(encoding="utf-8")
    capsule = (target / ".loreloop-export.json").read_text(encoding="utf-8")
    assert "legacy_users" in database
    assert hashlib.sha256(raw_sql).hexdigest() in capsule
    assert "gb18030=1" in capsys.readouterr().err
    assert (repo / "schema.sql").read_bytes() == raw_sql
    assert main(["knowledge", "replay", str(target)]) == 0


def test_docs_export_records_legacy_and_damaged_source_coverage_without_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repository(tmp_path / "legacy", {"README.md": "legacy project\n"})
    legacy = "// 中文注释\npublic class LegacyEmployee {}\n".encode("gb18030")
    repaired = b"public class RepairedEmployee {}\r\n// copyright: \x80 2006\r\n"
    unreadable = b"\x81\x30\x81\x00"
    (repo / "LegacyEmployee.cs").write_bytes(legacy)
    (repo / "RepairedEmployee.cs").write_bytes(repaired)
    (repo / "Unreadable.cs").write_bytes(unreadable)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add legacy encoded sources")
    monkeypatch.chdir(repo)
    target = tmp_path / "baseline"

    assert main(["knowledge", "export", "--format", "docs", "--output", str(target)]) == 0

    detailed = (target / "legacy-详细设计.md").read_text(encoding="utf-8")
    capsule = (target / ".loreloop-export.json").read_text(encoding="utf-8")
    coverage = capsys.readouterr().err
    assert "LegacyEmployee" in detailed
    assert "RepairedEmployee" in detailed
    assert "## 源码解析覆盖缺口" in detailed
    assert "RepairedEmployee.cs" in detailed and "轻微 UTF-8 损坏" in detailed
    assert "Unreadable.cs" in detailed and "无法安全解码" in detailed
    assert "gb18030=1" in coverage
    assert "utf-8-repaired=1" in coverage
    assert "source decode gaps: 1" in coverage
    assert "source_issues=2" in coverage
    for raw in (legacy, repaired, unreadable):
        assert hashlib.sha256(raw).hexdigest() in capsule
    assert (repo / "LegacyEmployee.cs").read_bytes() == legacy
    assert (repo / "RepairedEmployee.cs").read_bytes() == repaired
    assert (repo / "Unreadable.cs").read_bytes() == unreadable
    assert main(["knowledge", "replay", str(target)]) == 0


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


def test_package_export_replays_when_test_cases_require_multiple_suite_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(
        tmp_path / "suite-split",
        {
            "app.py": "VALUE = 1\n",
            "tests/many.test.js": "\n".join(
                f'test("case-{index:02d}-xxxxxxxx", () => true);' for index in range(20)
            ),
        },
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "loreloop.knowledge.authoritative_detector_tests.MAX_TEST_CASES_FIELD_BYTES",
        64,
    )
    package = tmp_path / "baseline.zip"

    assert main(["knowledge", "export", "--format", "package", "--output", str(package)]) == 0
    assert main(["knowledge", "replay", str(package)]) == 0


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
    assert "unstaged:app.py" in error
    assert "--working-tree" in error
    assert "Traceback" not in error


def test_cli_docs_export_can_capture_exact_working_tree_without_changing_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repository(
        tmp_path / "demo",
        {"app.py": "def original(): return 'committed'\n"},
    )
    _ = (repo / "app.py").write_text("def staged(): return 'staged'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _ = (repo / "app.py").write_text("def working(): return 'working'\n", encoding="utf-8")
    _ = (repo / "extra.py").write_text("def extra(): return 'untracked'\n", encoding="utf-8")
    status_before = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    index_before = subprocess.run(
        ["git", "ls-files", "--stage"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    monkeypatch.chdir(repo)
    target = tmp_path / "working-baseline"

    result = main(
        [
            "knowledge",
            "export",
            "--format",
            "docs",
            "--output",
            str(target),
            "--working-tree",
        ]
    )

    assert result == 0
    assert "capturing verifiable Git working-tree source snapshot" in capsys.readouterr().err
    detailed = (target / "demo-详细设计.md").read_text(encoding="utf-8")
    assert "可验证工作树快照" in detailed
    assert "工作树" in detailed
    assert "| working | working() |" not in detailed
    assert "| extra | extra() |" not in detailed
    assert "| original | original() |" not in detailed
    assert "| staged | staged() |" not in detailed
    assert main(["knowledge", "search", "working", "--package", str(target)]) == 0
    searched = capsys.readouterr().out
    assert "working" in searched
    assert "demo-详细设计.md#Agent视图" in searched
    assert main(["knowledge", "search", "extra", "--package", str(target)]) == 0
    assert "extra" in capsys.readouterr().out
    assert main(["knowledge", "replay", str(target)]) == 0
    status_after = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    index_after = subprocess.run(
        ["git", "ls-files", "--stage"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status_after == status_before
    assert index_after == index_before


def test_working_tree_export_excludes_its_default_baseline_on_regeneration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repository(tmp_path / "demo", {"app.py": "def committed(): return 1\n"})
    _ = (repo / "app.py").write_text("def first(): return 1\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    assert main(["knowledge", "export", "--format", "docs", "--working-tree"]) == 0
    assert (repo / "baseline").is_dir()
    first_capsule = (repo / "baseline/.loreloop-export.json").read_text(encoding="utf-8")
    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--working-tree",
                "--force",
            ]
        )
        == 0
    )
    assert (repo / "baseline/.loreloop-export.json").read_text(encoding="utf-8") == first_capsule
    _ = (repo / "app.py").write_text("def second(): return 2\n", encoding="utf-8")

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--working-tree",
                "--force",
            ]
        )
        == 0
    )

    detailed = (repo / "baseline/demo-详细设计.md").read_text(encoding="utf-8")
    assert "| second | second() |" not in detailed
    assert "| first | first() |" not in detailed
    assert main(["knowledge", "search", "second", "--package", "baseline"]) == 0
    searched = capsys.readouterr().out
    assert "second" in searched
    assert "demo-详细设计.md#Agent视图" in searched
    assert main(["knowledge", "replay", "baseline"]) == 0


def test_working_tree_package_replays_as_a_portable_zip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path / "demo", {"app.py": "def committed(): return 1\n"})
    _ = (repo / "app.py").write_text("def current(): return 2\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    package = tmp_path / "baseline.zip"

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "package",
                "--output",
                str(package),
                "--working-tree",
            ]
        )
        == 0
    )

    assert package.is_file()
    assert main(["knowledge", "replay", str(package)]) == 0


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
