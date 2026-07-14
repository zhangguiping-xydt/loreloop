from __future__ import annotations

import subprocess

from loreloop.knowledge.authoritative_git import capture_source_snapshot
from loreloop.knowledge.authoritative_records import DependencyRecord, DetectionReport, SourceRef
from loreloop.knowledge.authoritative_semantic import build_semantic_core
from loreloop.knowledge.authoritative_source import read_snapshot_blobs


def test_semantic_core_collapses_identical_record_identity(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("import os\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    source = SourceRef(".", "app.py", 1)
    item = DependencyRecord("os", None, "python_import", source)
    snapshot = capture_source_snapshot(repo)

    core = build_semantic_core(
        snapshot,
        read_snapshot_blobs(snapshot, repo),
        DetectionReport(dependencies=(item, item)),
        project_name="demo",
    )

    assert len(core.records) == 1
    assert len(core.evidence) == 1
