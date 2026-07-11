from pathlib import Path
import subprocess
import sys


def test_bundled_offline_demo_completes_full_first_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loreloop.cli",
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
    assert "export LORELOOP_KEY_DIR=" in result.stdout
    assert "superseded:" in result.stdout
    assert (tmp_path / "legacy-upload/.loreloop/knowledge.db").is_file()


def test_offline_demo_engine_runs_in_process(tmp_path):
    from loreloop.demo import run_demo
    from loreloop.knowledge.model import Curation
    from loreloop.knowledge.store import KnowledgeStore

    project = run_demo(tmp_path, agent="claude", offline=True)

    assert project == tmp_path / "legacy-upload"
    assert (project / ".loreloop/knowledge.db").is_file()
    assert (project / ".loreloop/evidence.jsonl").is_file()
    with KnowledgeStore(project / ".loreloop/knowledge.db") as store:
        assert all(entry.trust.curation is not Curation.DRAFT for entry in store.list())
