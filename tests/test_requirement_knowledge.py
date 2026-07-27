from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.evidence.chain import EvidenceChain
from loreloop.knowledge.endorsement import entry_digest
from loreloop.knowledge.requirement_context import load_requirement_materials
from loreloop.knowledge.requirement_knowledge import (
    RequirementLifecycle,
    ingest_requirement_materials,
    reconcile_requirement_projection,
    requirement_lifecycle,
    verified_requirement_materials,
)
from loreloop.knowledge.store import KnowledgeStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(path: Path, statement: str) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "LoreLoop Test")
    _git(path, "config", "user.email", "loreloop@example.invalid")
    (path / "requirements.md").write_text(f"# 需求\n\n- {statement}\n", encoding="utf-8")
    _git(path, "add", "requirements.md")
    _git(path, "commit", "-m", "requirement v1")
    return path


def test_new_material_version_supersedes_removed_requirement_and_stays_proposed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo", "用户可以导出权威项目文档。")
    chain = EvidenceChain.for_workdir(repo)
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        first = ingest_requirement_materials(
            chain,
            store,
            "run-v1",
            load_requirement_materials(repo, ("requirements.md",)),
        )
        chain.append(
            "delegation_completed",
            {
                "run_id": "run-v1",
                "requirement_entry_ids": [entry.id for entry in first],
            },
        )
        chain.append(
            "knowledge_harvested",
            {
                "run_id": "run-v1",
                "minted": {entry.id: entry_digest(entry) for entry in first},
                "requirement_entries": [entry.id for entry in first],
            },
        )
        assert verified_requirement_materials(chain.verify())

        (repo / "requirements.md").write_text(
            "# 需求\n\n- 用户可以批量导出权威项目文档。\n", encoding="utf-8"
        )
        _git(repo, "add", "requirements.md")
        _git(repo, "commit", "-m", "requirement v2")
        second = ingest_requirement_materials(
            chain,
            store,
            "run-v2",
            load_requirement_materials(repo, ("requirements.md",)),
        )

        assert store.get(first[0].id) is not None
        assert store.get(second[0].id) is not None

    states = requirement_lifecycle(chain.verify())
    assert states[first[0].id] is RequirementLifecycle.SUPERSEDED
    assert states[second[0].id] is RequirementLifecycle.PROPOSED
    assert verified_requirement_materials(chain.verify()) == ()


def test_reusing_identical_verified_material_does_not_downgrade_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "用户可以导出权威项目文档。")
    chain = EvidenceChain.for_workdir(repo)
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        first = ingest_requirement_materials(
            chain,
            store,
            "run-v1",
            load_requirement_materials(repo, ("requirements.md",)),
        )
        chain.append(
            "delegation_completed",
            {"run_id": "run-v1", "requirement_entry_ids": [first[0].id]},
        )
        chain.append(
            "knowledge_harvested",
            {
                "run_id": "run-v1",
                "minted": {first[0].id: entry_digest(first[0])},
                "requirement_entries": [first[0].id],
            },
        )

        second = ingest_requirement_materials(
            chain,
            store,
            "run-v2",
            load_requirement_materials(repo, ("requirements.md",)),
        )

    assert second[0].id == first[0].id
    assert requirement_lifecycle(chain.verify())[first[0].id] is (RequirementLifecycle.VERIFIED)


def test_reopen_does_not_restore_pre_rejection_verified_requirement(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "用户可以导出权威项目文档。")
    chain = EvidenceChain.for_workdir(repo)
    with KnowledgeStore(repo / ".loreloop/knowledge.db") as store:
        entries = ingest_requirement_materials(
            chain,
            store,
            "run-v1",
            load_requirement_materials(repo, ("requirements.md",)),
        )
    entry = entries[0]
    chain.append(
        "delegation_completed",
        {"run_id": "run-v1", "requirement_entry_ids": [entry.id]},
    )
    chain.append(
        "knowledge_harvested",
        {
            "run_id": "run-v1",
            "minted": {entry.id: entry_digest(entry)},
            "requirement_entries": [entry.id],
        },
    )
    chain.append("curation_changed", {"entry_id": entry.id, "curation": "rejected"})
    chain.append("curation_changed", {"entry_id": entry.id, "curation": "draft"})

    assert requirement_lifecycle(chain.verify())[entry.id] is RequirementLifecycle.PROPOSED
    assert verified_requirement_materials(chain.verify()) == ()


def test_missing_sqlite_requirement_row_is_restored_from_chain(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo", "用户可以导出权威项目文档。")
    chain = EvidenceChain.for_workdir(repo)
    db_path = repo / ".loreloop/knowledge.db"
    with KnowledgeStore(db_path) as store:
        entries = ingest_requirement_materials(
            chain,
            store,
            "run-v1",
            load_requirement_materials(repo, ("requirements.md",)),
        )
        store._conn.execute("DELETE FROM entries WHERE id = ?", (entries[0].id,))
        store._conn.commit()
        assert store.get(entries[0].id) is None

        reconcile_requirement_projection(store, chain.verify())

        restored = store.get(entries[0].id)
        assert restored is not None
        assert entry_digest(restored) == entry_digest(entries[0])
