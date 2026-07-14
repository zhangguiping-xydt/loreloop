from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loreloop.knowledge.authoritative_git import (
    GitSnapshotError,
    capture_source_snapshot,
    verify_source_snapshot,
    verify_source_snapshot_metadata,
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(path: Path, files: dict[str, str]) -> Path:
    path.mkdir()
    _ = _git(path, "init")
    _ = _git(path, "config", "user.name", "LoreLoop Test")
    _ = _git(path, "config", "user.email", "loreloop@example.invalid")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(content, encoding="utf-8")
    _ = _git(path, "add", "-A")
    _ = _git(path, "commit", "-m", "initial")
    return path


def test_capture_source_snapshot_includes_root_peer_and_submodule(tmp_path: Path) -> None:
    # Given: one project root, a peer backend, and a checked-out submodule.
    dependency = _repository(tmp_path / "dependency", {"lib.py": "VALUE = 1\n"})
    root = _repository(tmp_path / "root", {"app.py": "print('root')\n"})
    peer = _repository(tmp_path / "backend", {"api.py": "def health(): return 'ok'\n"})
    _ = _git(
        root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(dependency),
        "vendor/dependency",
    )
    _ = _git(root, "commit", "-am", "add submodule")

    # When: the project snapshot is captured without an agent or trust key.
    snapshot = capture_source_snapshot(root, {"backend": peer})

    # Then: repository order and roles are deterministic and every source blob is bound.
    assert tuple((repo.alias, repo.role) for repo in snapshot.repositories) == (
        (".", "root"),
        ("backend", "peer"),
        ("submodule:vendor/dependency", "submodule"),
    )
    root_entries = {entry.path: entry for entry in snapshot.repositories[0].entries}
    assert root_entries["app.py"].blob_sha256 is not None
    assert root_entries["vendor/dependency"].mode == "160000"
    assert snapshot.repositories[2].entries[0].path == "lib.py"
    assert all(item.repository_identity_sha256 is not None for item in snapshot.repositories)
    verify_source_snapshot(snapshot, root, {"backend": peer})


def test_capture_source_snapshot_rejects_a_dirty_member_repository(tmp_path: Path) -> None:
    # Given: a clean root and a peer changed after its last commit.
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})
    peer = _repository(tmp_path / "backend", {"api.py": "VALUE = 1\n"})
    _ = (peer / "api.py").write_text("VALUE = 2\n", encoding="utf-8")

    # When / Then: the project cannot mix committed and uncommitted source states.
    with pytest.raises(GitSnapshotError, match="backend.*uncommitted"):
        _ = capture_source_snapshot(root, {"backend": peer})


def test_capture_ignores_loreloop_state_but_rejects_untracked_source(tmp_path: Path) -> None:
    # Given: LoreLoop's local state exists beside otherwise committed source.
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})
    state = root / ".loreloop"
    state.mkdir()
    _ = (state / "knowledge.db").write_bytes(b"local state")

    # When: the source snapshot is captured.
    snapshot = capture_source_snapshot(root)

    # Then: internal state is absent, while an untracked source file still blocks readiness.
    assert all(
        not entry.path.startswith(".loreloop")
        for repository in snapshot.repositories
        for entry in repository.entries
    )
    _ = (root / "untracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(GitSnapshotError, match="uncommitted"):
        _ = capture_source_snapshot(root)


def test_verify_source_snapshot_rejects_drift_after_capture(tmp_path: Path) -> None:
    # Given: a captured clean project snapshot.
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})
    snapshot = capture_source_snapshot(root)
    _ = (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    # When / Then: later source drift invalidates the captured baseline.
    with pytest.raises(GitSnapshotError, match="uncommitted|changed"):
        verify_source_snapshot(snapshot, root)


def test_capture_source_snapshot_rejects_duplicate_repository_roots(tmp_path: Path) -> None:
    # Given: one physical repository presented under two project aliases.
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})

    # When / Then: aliases cannot duplicate or substitute repository identity.
    with pytest.raises(GitSnapshotError, match="same repository"):
        _ = capture_source_snapshot(root, {"duplicate": root})


def test_capture_rejects_peer_that_is_also_a_discovered_submodule(tmp_path: Path) -> None:
    dependency = _repository(tmp_path / "dependency", {"lib.py": "VALUE = 1\n"})
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})
    _ = _git(
        root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(dependency),
        "vendor/dependency",
    )
    _ = _git(root, "commit", "-am", "add submodule")

    with pytest.raises(GitSnapshotError, match="same repository"):
        _ = capture_source_snapshot(
            root, {"dependency": root / "vendor" / "dependency"}
        )


def test_capture_rejects_linked_worktree_as_a_second_repository_alias(tmp_path: Path) -> None:
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})
    linked = tmp_path / "linked"
    _ = _git(root, "worktree", "add", str(linked))

    with pytest.raises(GitSnapshotError, match="same repository"):
        _ = capture_source_snapshot(root, {"linked": linked})


def test_capture_ignores_caller_git_repository_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path / "root", {"app.py": "VALUE = 1\n"})
    expected_head = _git(root, "rev-parse", "HEAD")
    invalid = str(tmp_path / "attacker-controlled")
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
    ):
        monkeypatch.setenv(name, invalid)

    snapshot = capture_source_snapshot(root)

    assert snapshot.repositories[0].commit_id.hex == expected_head


def test_capture_source_snapshot_supports_non_git_aggregate_with_declared_repositories(
    tmp_path: Path,
) -> None:
    # Given: one project workspace that groups repositories but is not itself a repository.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    frontend = _repository(workspace / "frontend", {"app.ts": "export const ready = true\n"})
    backend = _repository(workspace / "backend", {"api.py": "def health(): return 'ok'\n"})

    # When: only the declared project members are captured.
    snapshot = capture_source_snapshot(workspace, {"frontend": frontend, "backend": backend})

    # Then: aliases remain stable and no synthetic Git root is invented.
    assert tuple((repo.alias, repo.role) for repo in snapshot.repositories) == (
        ("backend", "peer"),
        ("frontend", "peer"),
    )
    verify_source_snapshot(snapshot, workspace, {"frontend": frontend, "backend": backend})


def test_capture_source_snapshot_rejects_non_git_root_without_declared_members(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(GitSnapshotError, match="no declared Git repositories"):
        _ = capture_source_snapshot(workspace)


def test_aggregate_snapshot_rejects_root_topology_change_during_use(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = _repository(workspace / "backend", {"api.py": "VALUE = 1\n"})
    snapshot = capture_source_snapshot(workspace, {"backend": backend})
    _ = _git(workspace, "init")

    with pytest.raises(GitSnapshotError, match="topology changed"):
        verify_source_snapshot_metadata(snapshot, workspace, {"backend": backend})
