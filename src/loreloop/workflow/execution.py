"""Execute deterministic task-test commands and preserve provisional evidence."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..evidence.repository_state import capture_repository_states
from ..knowledge.repos import load_repos
from ..security import redact_sensitive
from ..report.acceptance import record_command_check
from .impact import latest_task_test_plan
from .model import TestExecutionResult

TASK_TEST_EXECUTED_EVENT = "task_test_executed"


def execute_task_test_plan(
    workdir: Path,
    run_id: str,
    records: list[EvidenceRecord],
    chain: EvidenceChain,
    artifacts: ArtifactStore,
    *,
    timeout: float = 300.0,
) -> tuple[TestExecutionResult, ...]:
    if timeout <= 0:
        raise ValueError("test timeout must be positive")
    plan = latest_task_test_plan(run_id, records, artifacts)
    if plan is None:
        raise ValueError("no task test plan exists; run `loreloop test select` first")
    repositories = {".": workdir.resolve(), **load_repos(workdir)}
    results: list[TestExecutionResult] = []
    for command in plan.commands:
        repository = repositories.get(command.repository)
        if repository is None:
            results.append(
                _record_skipped(
                    chain,
                    run_id,
                    command.repository,
                    command.argv,
                    "repository is no longer declared",
                )
            )
            continue
        if command.argv[:4] == ("loreloop", "web", "test", "run"):
            results.append(
                _record_skipped(
                    chain,
                    run_id,
                    command.repository,
                    command.argv,
                    "approved Web tests require their governed run flow; use candidate trial "
                    "before approval or `web test run` after approval",
                )
            )
            continue
        argv = _resolve_python(command.argv, repository)
        started = datetime.now(timezone.utc)
        timed_out = False
        try:
            process = subprocess.run(
                argv,
                cwd=repository,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            exit_code = process.returncode
            stdout = process.stdout
            stderr = process.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = _timeout_text(exc.stdout)
            stderr = _timeout_text(exc.stderr)
        finished = datetime.now(timezone.utc)
        stdout = redact_sensitive(stdout)
        stderr = redact_sensitive(stderr)
        repository_states = capture_repository_states(workdir)
        artifact = artifacts.save_json(
            {
                "type": "command_evidence",
                "argv": list(argv),
                "cwd": str(repository.resolve()),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "stdout": stdout[-100_000:],
                "stderr": stderr[-100_000:],
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "repository_states": repository_states,
            }
        )[0]
        status = "timed-out" if timed_out else "passed" if exit_code == 0 else "failed"
        record = chain.append(
            TASK_TEST_EXECUTED_EVENT,
            {
                "run_id": run_id,
                "repository": command.repository,
                "command": list(argv),
                "status": status,
                "exit_code": exit_code,
                "artifact": artifact,
                "covers": list(command.covers),
                "repository_states": repository_states,
                "authoritative": False,
            },
        )
        results.append(
            TestExecutionResult(
                command.repository,
                tuple(argv),
                status,  # type: ignore[arg-type]
                exit_code,
                artifact,
                stderr[-500:] or None,
            )
        )
        if record.payload["status"] != status:
            raise RuntimeError("recorded task test status changed unexpectedly")
    return tuple(results)


def prove_task_test_plan(
    workdir: Path,
    run_id: str,
    records: list[EvidenceRecord],
    chain: EvidenceChain,
    artifacts: ArtifactStore,
    *,
    timeout: float = 300.0,
) -> tuple[tuple[EvidenceRecord, ...], tuple[TestExecutionResult, ...]]:
    if not any(
        record.event == "delegation_completed" and record.payload.get("run_id") == run_id
        for record in records
    ):
        raise ValueError("task must be confirmed complete before acceptance proof")
    plan = latest_task_test_plan(run_id, records, artifacts)
    if plan is None:
        raise ValueError("no task test plan exists; run `loreloop test select` first")
    repositories = {".": workdir.resolve(), **load_repos(workdir)}
    checks: list[EvidenceRecord] = []
    skipped: list[TestExecutionResult] = []
    for command in plan.commands:
        repository = repositories.get(command.repository)
        if repository is None:
            skipped.append(
                TestExecutionResult(
                    command.repository,
                    command.argv,
                    "skipped",
                    None,
                    None,
                    "repository is no longer declared",
                )
            )
            continue
        if command.argv[:4] == ("loreloop", "web", "test", "run"):
            skipped.append(
                TestExecutionResult(
                    command.repository,
                    command.argv,
                    "skipped",
                    None,
                    None,
                    "Web acceptance requires approved scenario replay",
                )
            )
            continue
        argv = _resolve_python(command.argv, repository)
        label = "selected tests: " + ", ".join(command.covers[:8])
        checks.append(
            record_command_check(
                chain,
                artifacts,
                run_id,
                label,
                list(argv),
                cwd=repository,
                timeout=timeout,
                state_workdir=workdir,
            )
        )
    return tuple(checks), tuple(skipped)


def _record_skipped(
    chain: EvidenceChain,
    run_id: str,
    repository: str,
    argv: tuple[str, ...],
    reason: str,
) -> TestExecutionResult:
    chain.append(
        TASK_TEST_EXECUTED_EVENT,
        {
            "run_id": run_id,
            "repository": repository,
            "command": list(argv),
            "status": "skipped",
            "exit_code": None,
            "artifact": None,
            "reason": reason,
            "authoritative": False,
        },
    )
    return TestExecutionResult(repository, argv, "skipped", None, None, reason)


def _resolve_python(argv: tuple[str, ...], repository: Path) -> tuple[str, ...]:
    if not argv or argv[0] != "python":
        return argv
    candidates = (
        repository / ".venv/bin/python",
        repository / "venv/bin/python",
    )
    executable = next((path for path in candidates if path.is_file()), Path(sys.executable))
    return (str(executable), *argv[1:])


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
