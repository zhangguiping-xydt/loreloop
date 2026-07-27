from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from loreloop.cli import main
from loreloop.evidence.chain import EvidenceChain
from loreloop.knowledge.endorsement import curate
from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source, Trust
from loreloop.knowledge.store import KnowledgeStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    (repo / "src").mkdir()
    (repo / "src/api.py").write_text("def upload():\n    return 200\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _run_id(output: str) -> str:
    return next(
        line.removeprefix("Run ID: ") for line in output.splitlines() if line.startswith("Run ID: ")
    )


def test_high_risk_change_without_knowledge_is_blocked(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(["init", "--no-skill"]) == 0
    capsys.readouterr()
    assert main(["begin", "修复上传接口返回错误"]) == 0
    run_id = _run_id(capsys.readouterr().out)
    (repo / "src/api.py").write_text("def upload():\n    return 201\n", encoding="utf-8")
    assert main(["test", "select", run_id]) == 0
    capsys.readouterr()
    assert main(["complete", run_id, "--confirm"]) == 2
    error = capsys.readouterr().err
    assert "high-risk changes lack project-knowledge coverage" in error
    assert "test select" in error


def test_matching_code_knowledge_allows_high_risk_change(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(["init", "--no-skill"]) == 0
    capsys.readouterr()
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        entry = store.add(
            Entry(
                title="Upload API contract",
                content="The upload API returns an HTTP status code.",
                kind=Kind.INTERFACE,
                source=Source(channel=Channel.CODE, locator="src/api.py@fixture"),
            )
        )
        curate(
            store,
            EvidenceChain.for_workdir(repo),
            entry.id,
            Curation.APPROVED,
            datetime.now(timezone.utc),
        )
    assert main(["begin", "修复上传接口返回错误"]) == 0
    run_id = _run_id(capsys.readouterr().out)
    (repo / "src/api.py").write_text("def upload():\n    return 201\n", encoding="utf-8")
    assert main(["test", "select", run_id]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "task",
                "bug",
                run_id,
                "--id",
                "BUG-001",
                "--test-step",
                "调用上传接口",
                "--test-step",
                "检查 HTTP 状态码",
                "--description",
                "上传成功仍返回 200。",
                "--root-cause",
                "接口实现使用了错误的成功状态码。",
                "--recommendation",
                "按接口契约返回 201。",
                "--fix-solution",
                "把成功分支状态码改为 201。",
                "--modified-file",
                "src/api.py",
                "--test-proof",
                "任务测试计划覆盖 src/api.py。",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["complete", run_id, "--confirm"]) == 0
    capsys.readouterr()
    assert main(["report", run_id]) == 0
    report = capsys.readouterr().out
    assert "## Knowledge context and source coverage" in report
    assert "src/api.py" in report
    assert "strong" in report
    assert "## Root cause or requirement analysis" in report
    report_dir = repo / "workspace/change" / run_id
    test_report = (report_dir / "test-report.md").read_text(encoding="utf-8")
    bug_report = (report_dir / "bug-findings.md").read_text(encoding="utf-8")
    fix_report = (report_dir / "fix-report.md").read_text(encoding="utf-8")
    assert "# 测试报告" in test_report
    assert "## BUG-001" in bug_report
    assert "### 测试步骤" in bug_report
    assert "### 修改建议" in bug_report
    assert "### 根因确认" in fix_report
    assert "### 修复方案" in fix_report
    assert "src/api.py" in fix_report
    assert "### 测试证明" in fix_report


def test_store_only_strong_bit_does_not_satisfy_high_risk_gate(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(["init", "--no-skill"]) == 0
    capsys.readouterr()
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        store.add(
            Entry(
                title="Upload API contract",
                content="The upload API returns an HTTP status code.",
                kind=Kind.INTERFACE,
                source=Source(channel=Channel.CODE, locator="src/api.py@fixture"),
                trust=Trust(curation=Curation.APPROVED),
            )
        )
    assert main(["begin", "修复上传接口返回错误"]) == 0
    run_id = _run_id(capsys.readouterr().out)
    (repo / "src/api.py").write_text("def upload():\n    return 201\n", encoding="utf-8")
    assert main(["test", "select", run_id]) == 0
    capsys.readouterr()
    assert main(["complete", run_id, "--confirm"]) == 2
    assert "covered only by unverified/reference knowledge" in capsys.readouterr().err


def test_complete_rejects_source_change_after_context_checkpoint(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert main(["init", "--no-skill"]) == 0
    capsys.readouterr()
    assert main(["begin", "修复上传接口返回错误"]) == 0
    run_id = _run_id(capsys.readouterr().out)
    (repo / "README.md").write_text("changed after begin\n", encoding="utf-8")
    assert main(["test", "select", run_id]) == 0
    capsys.readouterr()
    (repo / "README.md").write_text("changed again after checkpoint\n", encoding="utf-8")
    assert main(["complete", run_id, "--confirm"]) == 2
    assert "source changed after knowledge checkpoint" in capsys.readouterr().err
