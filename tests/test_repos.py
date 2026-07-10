import json
import os
import subprocess
from pathlib import Path

import pytest

from loreloop.cli import main
from loreloop.knowledge.repos import (
    RepoConfigError,
    format_code_locator,
    load_repos,
    parse_code_locator,
    resolve_repo,
    save_repos,
)
from loreloop.paths import StatePathError


def init_repo(path: Path) -> Path:
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


@pytest.mark.parametrize(
    "locator, expected",
    [
        ("src/api.py@abc", (".", "src/api.py", "abc")),
        ("src/api.py", (".", "src/api.py", None)),
        ("repo:backend/src/api.py@abc", ("backend", "src/api.py", "abc")),
    ],
)
def test_code_locator_parsing(locator, expected):
    assert parse_code_locator(locator) == expected


def test_code_locator_formats_both_repository_shapes():
    assert format_code_locator(".", "src/api.py", "abc") == "src/api.py@abc"
    assert format_code_locator("backend", "src/api.py", "abc") == "repo:backend/src/api.py@abc"


@pytest.mark.parametrize(
    "locator",
    ["repo:../api.py@abc", "repo:backend/../api.py@abc", "/api.py@abc", "api.py@"],
)
def test_code_locator_rejects_invalid_input(locator):
    with pytest.raises(RepoConfigError):
        parse_code_locator(locator)


def test_load_repos_is_strict_and_resolves_relative_paths(tmp_path):
    workdir = init_repo(tmp_path / "workdir")
    backend = init_repo(tmp_path / "backend")
    config = workdir / ".loreloop/repos.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"version": 1, "repos": {"backend": "../backend"}}))

    assert load_repos(workdir) == {"backend": backend.resolve()}
    assert resolve_repo(workdir, ".") == workdir.resolve()
    assert resolve_repo(workdir, "backend") == backend.resolve()

    config.write_text(json.dumps({"version": 1, "repos": {"../bad": "../backend"}}))
    with pytest.raises(RepoConfigError, match="invalid repository name"):
        load_repos(workdir)


def test_load_repos_rejects_non_git_paths(tmp_path):
    workdir = init_repo(tmp_path / "workdir")
    plain = tmp_path / "plain"
    plain.mkdir()
    config = workdir / ".loreloop/repos.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"version": 1, "repos": {"plain": str(plain)}}))

    with pytest.raises(RepoConfigError, match="not a git root"):
        load_repos(workdir)


def test_repository_config_is_private_and_rejects_symlink_substitution(tmp_path):
    workdir = init_repo(tmp_path / "workdir")
    backend = init_repo(tmp_path / "backend")
    save_repos(workdir, {"backend": backend})
    config = workdir / ".loreloop/repos.json"

    assert config.is_file()
    if os.name != "nt":
        assert config.stat().st_mode & 0o777 == 0o600
    outside = tmp_path / "outside-repos.json"
    outside.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    config.unlink()
    try:
        config.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(StatePathError, match="symlinked repository configuration"):
        load_repos(workdir)


def test_repo_cli_add_list_remove(tmp_path, monkeypatch, capsys):
    workdir = init_repo(tmp_path / "workdir")
    backend = init_repo(tmp_path / "backend")
    monkeypatch.chdir(workdir)

    assert main(["repo", "add", str(backend), "--name", "backend"]) == 0
    assert load_repos(workdir) == {"backend": backend.resolve()}

    assert main(["repo", "list"]) == 0
    output = capsys.readouterr().out
    assert f".\t{workdir.resolve()}" in output
    assert f"backend\t{backend.resolve()}" in output

    assert main(["repo", "remove", "backend"]) == 0
    assert load_repos(workdir) == {}


def test_repo_cli_reports_bad_input_without_traceback(tmp_path, monkeypatch, capsys):
    workdir = init_repo(tmp_path / "workdir")
    monkeypatch.chdir(workdir)

    assert main(["repo", "add", str(tmp_path)]) == 2
    error = capsys.readouterr().err
    assert error.startswith("error: ")
    assert "Traceback" not in error
