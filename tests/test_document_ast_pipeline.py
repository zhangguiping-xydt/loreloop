from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.knowledge.authoritative_ast import OptionalDocumentFamily
from loreloop.knowledge.authoritative_document_ast import build_document_ast_set
from loreloop.knowledge.authoritative_git import capture_source_snapshot
from loreloop.knowledge.authoritative_semantic import build_semantic_core
from loreloop.knowledge.authoritative_source import detect_source_snapshot, read_snapshot_blobs


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_semantic_core_routes_into_exact_typed_document_ast_set(tmp_path: Path) -> None:
    # Given: source evidence supporting both optional document families.
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    _ = (repo / "app.py").write_text(
        '@app.get("/health")\ndef health(): return True\n', encoding="utf-8"
    )
    _ = (repo / "schema.sql").write_text(
        "CREATE TABLE health (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    snapshot = capture_source_snapshot(repo)
    blobs = read_snapshot_blobs(snapshot, repo)
    report = detect_source_snapshot(snapshot, repo)
    core = build_semantic_core(snapshot, blobs, report)

    # When: the package-neutral document AST routing closes.
    document_set = build_document_ast_set("demo", core)

    # Then: 6+2 families, package identity, evidence, and bindings are explicit.
    assert len(document_set.documents) == 8
    assert {item.optional_family for item in document_set.documents if item.optional_family} == set(
        OptionalDocumentFamily
    )
    assert all(item.header.package_id == core.package_id for item in document_set.documents)
    routed_ids = {
        row.record_id
        for document in document_set.documents
        for section in document.sections
        for row in section.rows
    }
    assert routed_ids == {record.record_id for record in core.records}
    assert all(
        row.bindings and row.evidence_ids
        for document in document_set.documents
        for section in document.sections
        for row in section.rows
    )
    assert all(document.evidence_rows for document in document_set.documents)
