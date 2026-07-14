from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "verification/authoritative_export/run.py"


def test_proof_runner_refuses_to_delete_source_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(ROOT), "--force"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must not be the source repository" in result.stderr


def test_proof_runner_force_requires_a_recognized_prior_manifest(tmp_path: Path) -> None:
    output = tmp_path / "operator-directory"
    output.mkdir()
    (output / "keep.txt").write_text("operator data\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(output), "--force"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "without a proof manifest" in result.stderr
    assert (output / "keep.txt").read_text(encoding="utf-8") == "operator data\n"


def test_proof_runner_requires_large_project_dogfood(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(tmp_path / "proof")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "requires --dogfood-repo" in result.stderr


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _tiny_dogfood(tmp_path: Path, remote: str) -> Path:
    repo = tmp_path / "dogfood"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop Test")
    _git(repo, "config", "user.email", "loreloop@example.invalid")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "remote", "add", "origin", remote)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def test_proof_runner_rejects_non_public_dogfood_remote(tmp_path: Path) -> None:
    dogfood = _tiny_dogfood(tmp_path, str(tmp_path / "private.git"))
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--output",
            str(tmp_path / "proof"),
            "--dogfood-repo",
            str(dogfood),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "public GitHub HTTPS" in result.stderr


def test_proof_runner_rejects_tiny_public_remote_backed_dogfood(tmp_path: Path) -> None:
    dogfood = _tiny_dogfood(tmp_path, "https://github.com/example/example.git")
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--output",
            str(tmp_path / "proof"),
            "--dogfood-repo",
            str(dogfood),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "at least 5000 tracked files" in result.stderr
