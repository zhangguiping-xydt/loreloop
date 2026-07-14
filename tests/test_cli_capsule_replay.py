from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from loreloop.cli import main
from loreloop.knowledge import authoritative_capsule_io
from loreloop.knowledge.authoritative_capsule_io import CapsuleIoError, parse_capsule


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


def test_cli_directory_replay_ignores_unmanaged_files_directories_and_large_sparse_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "export"
    monkeypatch.chdir(repo)
    assert main(["knowledge", "export", "--format", "docs", "--output", str(output)]) == 0
    capsys.readouterr()
    (output / "extra.md").write_text("operator Markdown\n", encoding="utf-8")
    nested = output / "operator"
    nested.mkdir()
    (nested / "keep.txt").write_text("operator file\n", encoding="utf-8")
    large = output / "large-unmanaged.bin"
    with large.open("wb") as stream:
        stream.seek(1024 * 1024 * 1024 - 1)
        stream.write(b"\0")

    assert main(["knowledge", "replay", str(output)]) == 0

    assert "Capsule replay: no_key" in capsys.readouterr().out
    assert large.stat().st_size == 1024 * 1024 * 1024


def test_cli_directory_replay_rejects_top_level_special_node(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO nodes are unavailable")
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "export"
    monkeypatch.chdir(repo)
    assert main(["knowledge", "export", "--format", "docs", "--output", str(output)]) == 0
    capsys.readouterr()
    os.mkfifo(output / "operator.fifo")

    assert main(["knowledge", "replay", str(output)]) == 2

    error = capsys.readouterr().err
    assert "regular file or real directory" in error
    assert "Traceback" not in error


def test_cli_directory_replay_enforces_managed_document_limit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "export"
    monkeypatch.chdir(repo)
    assert main(["knowledge", "export", "--format", "docs", "--output", str(output)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(authoritative_capsule_io, "MAX_DOCUMENT_BYTES", 1)

    assert main(["knowledge", "replay", str(output)]) == 2

    error = capsys.readouterr().err
    assert "exceeds its size limit" in error
    assert "Traceback" not in error


def test_capsule_parser_normalizes_deep_json_and_oversized_integer_errors() -> None:
    deep = b'{"value":' + b"[" * 2_000 + b"0" + b"]" * 2_000 + b"}\n"
    with pytest.raises(CapsuleIoError, match="nesting is too deep"):
        _ = parse_capsule(deep)

    oversized_integer = b'{"value":' + b"9" * 10_000 + b"}\n"
    with pytest.raises(CapsuleIoError, match="integer is outside the safe range"):
        _ = parse_capsule(oversized_integer)


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
