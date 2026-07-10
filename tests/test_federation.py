import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loreloop.cli import main
from loreloop.evidence.chain import EvidenceChain, key_path_for
from loreloop.federation.reader import read_project
from loreloop.federation.registry import RegistryError, add_project, load_projects
from loreloop.knowledge.endorsement import curate, entry_digest
from loreloop.knowledge.model import Channel, Curation, Entry, Kind, Source, Trust
from loreloop.knowledge.store import KnowledgeStore


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return path


def add_entry(project: Path, entry: Entry) -> None:
    db = project / ".loreloop/knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with KnowledgeStore(db) as store:
        store.add(entry)


def manual_entry(title="Fund contribution cap", content="公积金缴存比例上限是 12%。") -> Entry:
    return Entry(
        title=title,
        content=content,
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.MANUAL, locator="operator:policy"),
    )


def test_registry_adds_and_strictly_loads_projects(tmp_path):
    project = git_repo(tmp_path / "fund")
    add_entry(project, manual_entry())

    added = add_project(
        project,
        project_id="hr-fund",
        name="HR公积金测试系统",
        aliases=["公积金"],
        tags=["hr"],
    )

    assert load_projects() == {"hr-fund": added}

    registry = Path(__import__("os").environ["LORELOOP_REGISTRY"])
    registry.write_text(json.dumps({"version": 1, "projects": {"../bad": {}}}))
    with pytest.raises(RegistryError):
        load_projects()


def test_registry_rejects_paths_without_knowledge_store(tmp_path):
    project = git_repo(tmp_path / "plain")
    with pytest.raises(RegistryError, match="not a LoreLoop trust domain"):
        add_project(project)


def test_knowledge_store_readonly_connection_cannot_write(tmp_path):
    db = tmp_path / "knowledge.db"
    with KnowledgeStore(db) as store:
        store.add(manual_entry())

    with KnowledgeStore.open_readonly(db) as store:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store.add(manual_entry("Other", "Other content."))


def test_foreign_db_strong_bit_without_chain_endorsement_is_draft(tmp_path):
    project = git_repo(tmp_path / "foreign")
    planted = manual_entry()
    add_entry(
        project,
        Entry(
            id=planted.id,
            title=planted.title,
            content=planted.content,
            kind=planted.kind,
            source=planted.source,
            trust=Trust(curation=Curation.APPROVED),
        ),
    )
    EvidenceChain.for_workdir(project)

    entries, warnings = read_project("foreign", project)

    assert warnings == []
    assert len(entries) == 1
    assert not entries[0].strong_there
    assert entries[0].trust_note == "draft"


def test_missing_foreign_key_degrades_trust_without_creating_files(tmp_path):
    project = git_repo(tmp_path / "foreign")
    add_entry(project, manual_entry())
    key_dir = key_path_for(project).parent
    before_project = {path.relative_to(project) for path in project.rglob("*")}
    before_keys = {path.relative_to(key_dir) for path in key_dir.rglob("*")}

    entries, warnings = read_project("foreign", project)

    after_project = {path.relative_to(project) for path in project.rglob("*")}
    after_keys = {path.relative_to(key_dir) for path in key_dir.rglob("*")}
    assert len(entries) == 1
    assert entries[0].trust_note == "trust unavailable (chain not verifiable)"
    assert warnings and "trust unavailable" in warnings[0].message
    assert after_project == before_project
    assert after_keys == before_keys


def test_rejected_foreign_entries_are_not_returned(tmp_path):
    project = git_repo(tmp_path / "foreign")
    entry = manual_entry()
    add_entry(project, entry)
    chain = EvidenceChain.for_workdir(project)
    with KnowledgeStore(project / ".loreloop/knowledge.db") as store:
        curate(store, chain, entry.id, Curation.REJECTED, datetime.now(timezone.utc))

    entries, warnings = read_project("foreign", project)

    assert warnings == []
    assert entries == []


def test_search_selects_project_by_alias_and_reports_foreign_trust(tmp_path, monkeypatch, capsys):
    current = git_repo(tmp_path / "current")
    foreign = git_repo(tmp_path / "foreign")
    entry = manual_entry()
    add_entry(foreign, entry)
    chain = EvidenceChain.for_workdir(foreign)
    with KnowledgeStore(foreign / ".loreloop/knowledge.db") as store:
        curate(store, chain, entry.id, Curation.APPROVED, datetime.now(timezone.utc))
    add_project(
        foreign,
        project_id="hr-fund",
        name="HR公积金测试系统",
        aliases=["公积金"],
        tags=["hr"],
    )
    monkeypatch.chdir(current)

    assert main(["knowledge", "search", "缴存比例", "--project", "公积金"]) == 0
    output = capsys.readouterr().out
    assert "[hr-fund]" in output
    assert "[approved there]" in output
    assert "Fund contribution cap" in output


def test_search_tag_without_all_selects_tagged_projects(tmp_path, monkeypatch, capsys):
    current = git_repo(tmp_path / "current")
    foreign = git_repo(tmp_path / "foreign")
    entry = manual_entry()
    add_entry(foreign, entry)
    EvidenceChain.for_workdir(foreign)
    add_project(foreign, project_id="hr-fund", tags=["hr"])
    monkeypatch.chdir(current)

    assert main(["knowledge", "search", "缴存比例", "--tag", "hr"]) == 0

    assert "[hr-fund]" in capsys.readouterr().out


def test_import_is_always_born_draft_with_foreign_digest_provenance(tmp_path, monkeypatch, capsys):
    current = git_repo(tmp_path / "current")
    foreign = git_repo(tmp_path / "foreign")
    source = manual_entry()
    add_entry(foreign, source)
    chain = EvidenceChain.for_workdir(foreign)
    with KnowledgeStore(foreign / ".loreloop/knowledge.db") as store:
        curate(store, chain, source.id, Curation.APPROVED, datetime.now(timezone.utc))
    add_project(foreign, project_id="hr-fund")
    monkeypatch.chdir(current)

    assert main(["knowledge", "import", "hr-fund", source.id[:8]]) == 0

    with KnowledgeStore(current / ".loreloop/knowledge.db") as store:
        imported = store.list()[0]
    assert imported.id != source.id
    assert imported.source.channel is Channel.MANUAL
    assert imported.source.locator == f"project:hr-fund#{source.id}"
    assert imported.source.snapshot_ref == entry_digest(source)
    assert imported.trust.curation is Curation.DRAFT
    assert not imported.is_strong_evidence()
    assert "imported entry is draft" in capsys.readouterr().out


def test_cli_reports_corrupt_registry_without_traceback(tmp_path, monkeypatch, capsys):
    current = git_repo(tmp_path / "current")
    registry = Path(__import__("os").environ["LORELOOP_REGISTRY"])
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("not json")
    monkeypatch.chdir(current)

    assert main(["project", "list"]) == 2
    error = capsys.readouterr().err
    assert error.startswith("error: ")
    assert "Traceback" not in error


def test_related_projects_are_ranked_by_shared_repository_paths(tmp_path):
    from loreloop.federation.registry import related_projects
    from loreloop.knowledge.repos import save_repos

    current = git_repo(tmp_path / "current")
    shared = git_repo(tmp_path / "shared")
    related = git_repo(tmp_path / "related")
    unrelated = git_repo(tmp_path / "unrelated")
    add_entry(related, manual_entry())
    add_entry(unrelated, manual_entry("Other", "Other policy."))
    save_repos(current, {"shared": shared})
    save_repos(related, {"shared": shared})
    add_project(related, project_id="related")
    add_project(unrelated, project_id="unrelated")

    assert related_projects(current) == [("related", 1), ("unrelated", 0)]


def test_run_with_related_applies_overlap_order_budget_and_records_ids(
    tmp_path, monkeypatch, capsys
):
    import loreloop.cli as cli
    from loreloop.knowledge.repos import save_repos

    class FakeAgent:
        def __init__(self):
            self.prompts = []

        def run(self, prompt):
            self.prompts.append(prompt)
            return "done"

    current = git_repo(tmp_path / "current")
    shared = git_repo(tmp_path / "shared")
    related = git_repo(tmp_path / "related")
    unrelated = git_repo(tmp_path / "unrelated")
    close = manual_entry("Shared upload policy", "The upload endpoint accepts 50MB files.")
    far = manual_entry("Unrelated upload policy", "The upload endpoint accepts 100MB files.")
    add_entry(related, close)
    add_entry(unrelated, far)
    EvidenceChain.for_workdir(related)
    EvidenceChain.for_workdir(unrelated)
    save_repos(current, {"shared": shared})
    save_repos(related, {"shared": shared})
    add_project(related, project_id="related")
    add_project(unrelated, project_id="unrelated")
    monkeypatch.chdir(current)
    agent = FakeAgent()
    monkeypatch.setattr(cli, "_agent", lambda name: agent)

    assert (
        main(
            [
                "run",
                "fix upload endpoint",
                "--no-expand",
                "--with-related",
                "--related-limit",
                "1",
            ]
        )
        == 0
    )

    assert "Related project references" in agent.prompts[0]
    assert "Shared upload policy" in agent.prompts[0]
    assert "Unrelated upload policy" not in agent.prompts[0]
    trace = next((current / ".loreloop/runs").glob("*.jsonl"))
    started = json.loads(trace.read_text().splitlines()[0])
    expected = [f"related#{close.id}"]
    assert started["related_entries"] == expected
    completed = next(
        record
        for record in EvidenceChain.for_workdir(current).verify()
        if record.event == "delegation_completed"
    )
    assert completed.payload["related_entries"] == expected
    capsys.readouterr()
