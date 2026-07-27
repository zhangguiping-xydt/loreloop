from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.cli import main
from loreloop.evidence.chain import EvidenceChain
from loreloop.knowledge.model import Kind
from loreloop.knowledge.requirement_knowledge import (
    REQUIREMENT_INGESTED_EVENT,
    RequirementLifecycle,
    requirement_lifecycle,
)
from loreloop.knowledge.store import KnowledgeStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "LoreLoop Test")
    _git(path, "config", "user.email", "loreloop@example.invalid")
    (path / "requirements.md").write_text(
        "# 需求\n\n- 用户可以导出权威项目文档。\n", encoding="utf-8"
    )
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "requirements")
    return path


def test_begin_injects_committed_requirement_and_signs_its_digest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    monkeypatch.chdir(repo)

    assert main(["begin", "实现导出需求", "--requirements", "requirements.md"]) == 0

    output = capsys.readouterr().out
    assert "Requirement materials (committed Git blobs)" in output
    assert "用户可以导出权威项目文档" in output
    prepared = next(
        record
        for record in EvidenceChain.for_workdir(repo).verify()
        if record.event == "delegation_prepared"
    )
    material = prepared.payload["requirement_materials"][0]
    assert material["locator"] == "requirements.md"
    assert len(material["sha256"]) == 64
    requirement_ids = prepared.payload["requirement_entry_ids"]
    assert len(requirement_ids) == 1
    ingested = next(
        record
        for record in EvidenceChain.for_workdir(repo).verify()
        if record.event == REQUIREMENT_INGESTED_EVENT
    )
    assert ingested.payload["run_id"] == prepared.payload["run_id"]
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        entry = store.get(requirement_ids[0])
    assert entry is not None
    assert entry.kind is Kind.REQUIREMENT
    assert entry.content == "用户可以导出权威项目文档。"
    assert requirement_lifecycle(EvidenceChain.for_workdir(repo).verify())[entry.id] is (
        RequirementLifecycle.PROPOSED
    )
    assert main(["knowledge", "list", "--kind", "requirement"]) == 0
    assert "[requirement:proposed]" in capsys.readouterr().out


def test_begin_reads_head_blob_not_uncommitted_requirement_copy(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    (repo / "requirements.md").write_text("forged worktree text\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    assert main(["begin", "实现需求", "--requirements", "requirements.md"]) == 0

    output = capsys.readouterr().out
    assert "用户可以导出权威项目文档" in output
    assert "forged worktree text" not in output


def test_complete_marks_ingested_requirement_implemented(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    monkeypatch.chdir(repo)

    assert main(["begin", "实现导出需求", "--requirements", "requirements.md"]) == 0
    run_id = next(
        line.removeprefix("Run ID: ")
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("Run ID: ")
    )
    assert main(["complete", run_id, "--confirm"]) == 0

    records = EvidenceChain.for_workdir(repo).verify()
    entry_id = next(
        record.payload["requirement_entry_ids"][0]
        for record in records
        if record.event == "delegation_completed"
    )
    assert requirement_lifecycle(records)[entry_id] is RequirementLifecycle.IMPLEMENTED
    assert "requirement knowledge marked implemented: 1" in capsys.readouterr().out
    assert main(["knowledge", "list", "--kind", "requirement"]) == 0
    assert "[requirement:implemented]" in capsys.readouterr().out
    assert main(["report", run_id]) == 0
    report = capsys.readouterr().out
    assert "Requirement knowledge lifecycle" in report
    assert "state=implemented" in report
