from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.cli import main
from loreloop.evidence.chain import EvidenceChain


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
