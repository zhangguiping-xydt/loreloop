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
