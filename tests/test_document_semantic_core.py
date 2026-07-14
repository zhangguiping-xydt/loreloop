from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.knowledge.authoritative_git import capture_source_snapshot
from loreloop.knowledge.authoritative_semantic import build_semantic_core
from loreloop.knowledge.authoritative_source import detect_source_snapshot, read_snapshot_blobs


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def test_semantic_core_binds_requirements_and_source_to_stable_records(tmp_path: Path) -> None:
    # Given: committed source and committed requirement material.
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    app = repo / "app.py"
    _ = app.write_text('@app.get("/health")\ndef health(): return True\n', encoding="utf-8")
    requirements = repo / "requirements.md"
    _ = requirements.write_text(
        "| ID | 需求描述 | 验收标准 |\n|---|---|---|\n"
        + "| REQ-1 | 服务提供健康检查 | 返回可用状态 |\n",
        encoding="utf-8",
    )
    _commit(repo, "initial")

    # When: the single SemanticCore boundary is built twice.
    snapshot = capture_source_snapshot(repo)
    blobs = read_snapshot_blobs(snapshot, repo, requirements=("requirements.md",))
    report = detect_source_snapshot(snapshot, repo, requirements=("requirements.md",))
    first = build_semantic_core(snapshot, blobs, report, project_name="demo")
    second = build_semantic_core(snapshot, blobs, report, project_name="demo")
    renamed = build_semantic_core(snapshot, blobs, report, project_name="renamed")

    # Then: package/core IDs are deterministic and every record has exact evidence bindings.
    assert first == second
    assert first.project_name == "demo"
    assert renamed.project_name == "renamed"
    assert renamed.semantic_core_sha256 != first.semantic_core_sha256
    assert renamed.package_id != first.package_id
    assert {record.row_kind.value for record in first.records} >= {
        "InterfaceRow",
        "RequirementRow",
        "AcceptanceRow",
    }
    assert all(record.evidence_id.startswith("EVD-") for record in first.records)
    assert all(record.atom_id.startswith("ATM-") for record in first.records)
    assert all(record.bindings for record in first.records)

    # When: only source line placement changes in a later clean commit.
    interface_id = next(
        record.record_id for record in first.records if record.row_kind.value == "InterfaceRow"
    )
    _ = app.write_text('\n@app.get("/health")\ndef health(): return True\n', encoding="utf-8")
    _commit(repo, "move source line")
    moved_snapshot = capture_source_snapshot(repo)
    moved_blobs = read_snapshot_blobs(
        moved_snapshot, repo, requirements=("requirements.md",)
    )
    moved_report = detect_source_snapshot(moved_snapshot, repo, requirements=("requirements.md",))
    moved = build_semantic_core(
        moved_snapshot, moved_blobs, moved_report, project_name="demo"
    )
    moved_interface = next(
        record for record in moved.records if record.row_kind.value == "InterfaceRow"
    )

    # Then: semantic identity stays stable while the evidence atom and core digest change.
    assert moved_interface.record_id == interface_id
    assert moved_interface.atom_id != next(
        record.atom_id for record in first.records if record.record_id == interface_id
    )
    assert moved.semantic_core_sha256 != first.semantic_core_sha256
