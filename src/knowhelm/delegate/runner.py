"""Delegate a task to the coding agent with a context pack, recording a trace."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agents import AgentRunner
from ..knowledge.model import Entry
from .context_pack import ContextPack, render, select

RUNS_DIR = ".knowhelm/runs"


def _head_or_none(workdir: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass(frozen=True)
class DelegationResult:
    run_id: str
    output: str
    trace_path: Path
    pack: ContextPack


class DelegateRunner:
    def __init__(self, agent: AgentRunner, workdir: Path) -> None:
        self._agent = agent
        self._workdir = workdir
        self._runs_dir = workdir / RUNS_DIR
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    def run(self, task: str, entries: list[Entry]) -> DelegationResult:
        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        trace_path = self._runs_dir / f"{run_id}.jsonl"
        pack = select(task, entries)
        prefix = render(pack)
        prompt = f"{prefix}\n# Task\n\n{task}\n" if prefix else task

        self._trace(
            trace_path,
            "delegation_started",
            task=task,
            context_entries=pack.entry_ids,
            base_commit=_head_or_none(self._workdir),
        )
        try:
            output = self._agent.run(prompt)
        except Exception as exc:
            self._trace(trace_path, "delegation_failed", error=str(exc))
            raise
        self._trace(trace_path, "delegation_finished", output_chars=len(output))
        return DelegationResult(run_id=run_id, output=output, trace_path=trace_path, pack=pack)

    def _trace(self, path: Path, event: str, **fields) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
