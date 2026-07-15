from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from loreloop.knowledge.authoritative_ast_render import render_document_set
from loreloop.knowledge.authoritative_capsule import CapsuleError, build_capsule, verify_capsule
from loreloop.knowledge.authoritative_document_ast import build_document_ast_set
from loreloop.knowledge.authoritative_git import capture_source_snapshot
from loreloop.knowledge.authoritative_semantic import build_semantic_core
from loreloop.knowledge.authoritative_source import detect_source_snapshot, read_snapshot_blobs


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_capsule_binds_core_full_ast_and_markdown_without_raw_git_or_secrets(
    tmp_path: Path,
) -> None:
    # Given: one clean source package containing a redacted configuration default.
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    _ = (repo / "app.py").write_text(
        'import os\nTOKEN=os.getenv("API_TOKEN", "must-not-leak")\n'
        + '@app.get("/health")\ndef health(): return True\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    snapshot = capture_source_snapshot(repo)
    blobs = read_snapshot_blobs(snapshot, repo)
    core = build_semantic_core(
        snapshot,
        blobs,
        detect_source_snapshot(snapshot, repo),
        project_name="demo",
    )
    document_set = build_document_ast_set(core)
    documents = render_document_set(document_set)

    # When: Capsule creation and independent recomputation run.
    capsule = build_capsule(core, document_set, documents)
    verify_capsule(capsule, core, document_set, documents)
    legacy_capsule = build_capsule(core, document_set, documents, schema_version=2)
    verify_capsule(legacy_capsule, core, document_set, documents)

    # Then: portable closure has package/products but no raw source identity or secret bytes.
    assert core.package_id in capsule.content
    assert '"schema_version":3' in capsule.content
    assert '"ast":' not in capsule.content
    assert len(capsule.content) < len(legacy_capsule.content)
    assert "must-not-leak" not in capsule.content
    raw_identities = {repository.commit_id.hex for repository in snapshot.repositories} | {
        repository.tree_id.hex for repository in snapshot.repositories
    }
    raw_identities.update(
        entry.object_id.hex for repository in snapshot.repositories for entry in repository.entries
    )
    assert all(identity not in capsule.content for identity in raw_identities)

    # When / Then: changed Markdown or changed Capsule bytes are rejected.
    changed_documents = (
        replace(documents[0], content=documents[0].content + "tampered\n"),
        *documents[1:],
    )
    with pytest.raises(CapsuleError, match="closure"):
        verify_capsule(capsule, core, document_set, changed_documents)
    changed_capsule = replace(capsule, content=capsule.content.replace(core.package_id, "0" * 64))
    with pytest.raises(CapsuleError, match="artifact digest"):
        verify_capsule(changed_capsule, core, document_set, documents)
