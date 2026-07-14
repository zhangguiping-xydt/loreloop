from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.cli import main
from loreloop.knowledge.authoritative_archive import read_export_archive
from loreloop.knowledge.authoritative_git import capture_source_snapshot


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_portable_package_contains_no_raw_secret_or_git_object_identity(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    raw_secret = "secret-value-that-must-not-leak"
    (repo / ".env.example").write_text(
        f"APP_ENV=production\nAPI_TOKEN={raw_secret}\n", encoding="utf-8"
    )
    (repo / "app.py").write_text(
        '@app.get("/health")\ndef health(): return True\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    snapshot = capture_source_snapshot(repo)
    raw_git_ids = {
        repository.commit_id.hex
        for repository in snapshot.repositories
    } | {
        repository.tree_id.hex
        for repository in snapshot.repositories
    } | {
        entry.object_id.hex
        for repository in snapshot.repositories
        for entry in repository.entries
    }
    output = tmp_path / "knowledge.zip"
    monkeypatch.chdir(repo)

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "package",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    portable = b"\n".join(read_export_archive(output).values())
    assert raw_secret.encode() not in portable
    assert all(identifier.encode() not in portable for identifier in raw_git_ids)
