from __future__ import annotations

import os
import subprocess
import warnings
import zipfile
from pathlib import Path

import pytest

from loreloop.cli import main
from loreloop.knowledge.authoritative_archive import (
    ExportArchiveError,
    read_export_archive,
    write_export_archive,
)
from loreloop.knowledge.authoritative_capsule_replay import (
    CapsuleReplayError,
    replay_capsule_archive,
)
from loreloop.knowledge.authoritative_documents import SourceDocument


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


def test_cli_docs_export_writes_and_replays_deliverable_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "knowledge.zip"
    monkeypatch.chdir(repo)

    assert main(["knowledge", "export", "--format", "package", "--output", str(output)]) == 0
    first_bytes = output.read_bytes()
    rendered = capsys.readouterr().out
    assert "ZIP package" in rendered
    with zipfile.ZipFile(output) as archive:
        assert ".loreloop-export.json" in archive.namelist()
        assert len(tuple(name for name in archive.namelist() if name.endswith(".md"))) == 7

    assert main(["knowledge", "replay", str(output)]) == 0
    assert "Capsule replay: no_key" in capsys.readouterr().out

    output.unlink()
    assert main(["knowledge", "export", "--format", "package", "--output", str(output)]) == 0
    assert output.read_bytes() == first_bytes


def test_cli_zip_export_requires_force_before_atomic_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "knowledge.zip"
    output.write_bytes(b"operator archive")
    monkeypatch.chdir(repo)

    assert main(["knowledge", "export", "--format", "docs", "--output", str(output)]) == 2
    assert output.read_bytes() == b"operator archive"
    assert "already exists" in capsys.readouterr().err

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
    assert replay_capsule_archive(output).documents


def test_archive_replay_rejects_paths_duplicates_and_unbound_extra_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "repo")
    output = tmp_path / "knowledge.zip"
    monkeypatch.chdir(repo)
    assert main(["knowledge", "export", "--format", "docs", "--output", str(output)]) == 0
    files = read_export_archive(output)

    escaped = tmp_path / "escaped.zip"
    with zipfile.ZipFile(escaped, "w") as archive:
        archive.writestr("../escape.md", b"bad")
    with pytest.raises(ExportArchiveError, match="invalid archive filename"):
        _ = read_export_archive(escaped)

    duplicate = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("same.md", b"one")
            archive.writestr("same.md", b"two")
    with pytest.raises(ExportArchiveError, match="duplicate filename"):
        _ = read_export_archive(duplicate)

    extra = tmp_path / "extra.zip"
    with zipfile.ZipFile(extra, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
        archive.writestr("extra.txt", b"not capsule-bound")
    with pytest.raises(CapsuleReplayError, match="file set mismatch"):
        _ = replay_capsule_archive(extra)


def test_archive_reader_rejects_symlinked_package(tmp_path: Path) -> None:
    target = tmp_path / "target.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("doc.md", b"content")
    link = tmp_path / "link.zip"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks are unavailable")

    with pytest.raises(ExportArchiveError, match="must not be a symlink"):
        _ = read_export_archive(link)


def test_archive_first_install_does_not_overwrite_a_racing_operator_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "knowledge.zip"
    real_link = os.link

    def collide(source: Path, destination: Path, *, follow_symlinks: bool) -> None:
        Path(destination).write_bytes(b"operator file")
        real_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("loreloop.knowledge.authoritative_archive.os.link", collide)

    with pytest.raises(ExportArchiveError, match="appeared while export was running"):
        write_export_archive(
            output,
            (SourceDocument("doc.md", "content\n"),),
            replace=False,
        )

    assert output.read_bytes() == b"operator file"
    assert not tuple(tmp_path.glob(".knowledge.zip.loreloop-stage-*"))
