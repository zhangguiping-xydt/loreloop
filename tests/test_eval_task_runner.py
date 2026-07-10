from __future__ import annotations

import json
import sys

from eval import task_runner


def test_task_runner_executes_hidden_evaluator_and_changes_success_with_context(
    tmp_path, monkeypatch
) -> None:
    agent = tmp_path / "fake_agent.py"
    agent.write_text(
        """
import sys
from pathlib import Path

prompt = sys.stdin.read()
if "37 MiB" in prompt:
    path = Path("upload_policy.py")
    path.write_text(path.read_text().replace("10 * 1024 * 1024", "37 * 1024 * 1024"))
print("done")
""",
        encoding="utf-8",
    )
    monkeypatch.setitem(task_runner.AGENT_COMMANDS, "codex", (sys.executable, str(agent)))
    specs = json.loads((task_runner.TASK_ROOT / "tasks.json").read_text(encoding="utf-8"))
    spec = next(item for item in specs if item["id"] == "upload-limit-policy")

    plain = task_runner.run_task(spec, agent="codex", variant="no_knowledge", timeout=30)
    governed = task_runner.run_task(spec, agent="codex", variant="knowhelm", timeout=30)

    assert not plain["passed"]
    assert plain["public_test_exit_code"] == 0
    assert plain["hidden_test_exit_code"] != 0
    assert governed["passed"]
    assert "37 * 1024 * 1024" in governed["diff"]


def test_task_prompt_keeps_knowledge_out_of_no_context_variant() -> None:
    spec = json.loads((task_runner.TASK_ROOT / "tasks.json").read_text(encoding="utf-8"))[0]

    plain = task_runner._task_prompt(spec, "no_knowledge")
    governed = task_runner._task_prompt(spec, "knowhelm")

    assert "37 MiB" not in plain
    assert "37 MiB" in governed
    assert "Established facts" in governed


def test_task_prompt_exposes_distinct_memory_and_index_baselines(tmp_path) -> None:
    spec = json.loads((task_runner.TASK_ROOT / "tasks.json").read_text(encoding="utf-8"))[0]
    (tmp_path / "policy.py").write_text("MAX = 10\n", encoding="utf-8")

    session = task_runner._task_prompt(spec, "session_memory")
    indexed = task_runner._task_prompt(spec, "codebase_index", tmp_path)

    assert "37 MiB" in session
    assert "unverified and ephemeral" in session
    assert "37 MiB" not in indexed
    assert "MAX = 10" in indexed
    assert "source snippets, no external project memory" in indexed


def test_task_result_redaction_removes_environment_secrets() -> None:
    text = "OPENAI_API_KEY=super-secret\nnormal output\npassword: hunter2"

    redacted = task_runner._redact_transcript(text)

    assert "super-secret" not in redacted
    assert "hunter2" not in redacted
    assert "OPENAI_API_KEY=<redacted>" in redacted
    assert "normal output" in redacted
