from pathlib import Path
import subprocess
import sys


def test_bundled_offline_demo_completes_full_first_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "knowhelm.cli",
            "demo",
            "--offline",
            "--workspace",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verdict: ACCEPTED" in result.stdout
    assert "harvested run" in result.stdout
    assert (tmp_path / "legacy-upload/.knowhelm/knowledge.db").is_file()
