from __future__ import annotations

import os
import subprocess
import sys
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


def test_force_directory_export_removes_previous_project_document_namespace(
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
                "--project-name",
                "old",
            ]
        )
        == 0
    )
    capsys.readouterr()
    (output / "operator.md").write_text("operator\n", encoding="utf-8")

    assert (
        main(
            [
                "knowledge",
                "export",
                "--format",
                "docs",
                "--output",
                str(output),
                "--project-name",
                "new",
                "--force",
            ]
        )
        == 0
    )

    names = {path.name for path in output.iterdir()}
    assert not any(name.startswith("old-") for name in names)
    assert any(name.startswith("new-") for name in names)
    assert (output / "operator.md").read_text(encoding="utf-8") == "operator\n"
    assert main(["knowledge", "replay", str(output)]) == 0


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


def test_capsule_parser_rejects_wide_json_before_building_the_object_graph() -> None:
    wide = b'{"x":[' + b"0," * authoritative_capsule_io.MAX_JSON_CONTAINER_ITEMS + b"0]}\n"

    with pytest.raises(CapsuleIoError, match="array exceeds the item limit"):
        _ = parse_capsule(wide)


@pytest.mark.skipif(sys.platform != "linux", reason="address-space proof uses Linux resource limits")
def test_capsule_parser_rejects_12m_values_under_bounded_address_space() -> None:
    code = """
import resource
from loreloop.knowledge.authoritative_capsule_io import CapsuleIoError, parse_capsule
limit = 220_000_000
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
n = 12_000_000
data = b'{"x":[' + b'0,' * (n - 1) + b'0]}\\n'
try:
    parse_capsule(data)
except CapsuleIoError as exc:
    print(exc)
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "array exceeds the item limit" in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="address-space proof uses Linux resource limits")
def test_capsule_parser_rejects_many_small_containers_under_bounded_address_space() -> None:
    code = """
import resource
from loreloop.knowledge.authoritative_capsule_io import CapsuleIoError, parse_capsule
limit = 220_000_000
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
rows = 39
columns = 100_000
row = b'[' + b'{},' * (columns - 1) + b'{}]'
data = b'{"x":[' + b','.join([row] * rows) + b']}\\n'
try:
    parse_capsule(data)
except CapsuleIoError as exc:
    print(exc)
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "total array element limit" in result.stdout
        or "container limit" in result.stdout
    )


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
