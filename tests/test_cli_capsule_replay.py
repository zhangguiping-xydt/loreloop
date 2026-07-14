from __future__ import annotations

import subprocess
from pathlib import Path

from loreloop.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "LoreLoop Test")
    _git(path, "config", "user.email", "loreloop@example.invalid")
    (path / "app.py").write_text('@app.get("/health")\ndef health(): return True\n')
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "initial")
    return path


def test_cli_no_key_replay_needs_no_project_trust(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "export"
    monkeypatch.chdir(repo)
    assert main(["knowledge", "export", "--format", "docs", "--output", str(output)]) == 0
    capsys.readouterr()

    assert main(["knowledge", "replay", str(output)]) == 0

    rendered = capsys.readouterr().out
    assert "Capsule replay: no_key" in rendered
    assert "documents: 7" in rendered


def test_cli_replay_ignores_preserved_non_markdown_operator_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "export"
    output.mkdir()
    (output / "keep.txt").write_text("operator file\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--output",
                str(output),
                "--force",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["knowledge", "replay", str(output)]) == 0
    assert (output / "keep.txt").read_text(encoding="utf-8") == "operator file\n"


def test_cli_trusted_replay_requires_exact_attested_package(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "export"
    monkeypatch.chdir(repo)
    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--output",
                str(output),
                "--attest",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["knowledge", "replay", str(output), "--trusted"]) == 0

    assert "Capsule replay: trusted" in capsys.readouterr().out


def test_cli_trusted_replay_accepts_attested_zip_transport(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
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
                "--attest",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["knowledge", "replay", str(output), "--trusted"]) == 0
    assert "Capsule replay: trusted" in capsys.readouterr().out
