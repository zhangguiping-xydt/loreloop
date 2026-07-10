"""Delegate a task to the coding agent with a context pack, recording a trace."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agents import AgentRunner
from ..federation.reader import ForeignEntry
from ..knowledge.code_reverse import drifted_code_entry_ids
from ..knowledge.model import Entry
from ..knowledge.repos import load_repos
from ..paths import state_path
from .context_pack import ContextPack, render, select


def _heads(workdir: Path) -> dict[str, str]:
    heads: dict[str, str] = {}
    for name, repo in {".": workdir.resolve(), **load_repos(workdir)}.items():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        )
        if result.returncode == 0:
            heads[name] = result.stdout.strip()
    return heads


@dataclass(frozen=True)
class DelegationResult:
    run_id: str
    output: str
    trace_path: Path
    pack: ContextPack
    base_commits: dict[str, str]

    @property
    def base_commit(self) -> str | None:
        return self.base_commits.get(".")


class DelegateRunner:
    def __init__(self, agent: AgentRunner, workdir: Path) -> None:
        self._agent = agent
        self._workdir = workdir
        self._runs_dir = state_path(workdir, "runs")
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        task: str,
        entries: list[Entry],
        unendorsed_ids: set[str] | frozenset[str] = frozenset(),
        endorsed_ids: set[str] | frozenset[str] = frozenset(),
        expansion: str = "",
        related: list[ForeignEntry] | None = None,
    ) -> DelegationResult:
        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        trace_path = self._runs_dir / f"{run_id}.jsonl"
        base_commits = _heads(self._workdir)
        drifted = drifted_code_entry_ids(self._workdir, entries) if base_commits else set()
        pack = select(
            task,
            entries,
            drifted_ids=drifted,
            unendorsed_ids=unendorsed_ids,
            endorsed_ids=endorsed_ids,
            expansion=expansion,
            related=related,
        )
        prefix = render(pack)
        prompt = f"{prefix}\n# Task\n\n{task}\n" if prefix else task

        self._trace(
            trace_path,
            "delegation_started",
            task=task,
            context_entries=pack.entry_ids,
            drifted_entries=sorted(pack.drifted_ids & set(pack.entry_ids)),
            unendorsed_entries=sorted(pack.unendorsed_ids & set(pack.entry_ids)),
            chain_endorsed_entries=sorted(pack.endorsed_ids & set(pack.entry_ids)),
            query_expansion=expansion,
            base_commits=base_commits,
            related_entries=pack.related_ids,
        )
        try:
            output = self._agent.run(prompt)
        except KeyboardInterrupt:
            self._trace(trace_path, "delegation_interrupted", reason="operator cancelled")
            raise
        except Exception as exc:
            self._trace(trace_path, "delegation_failed", error=str(exc))
            raise
        self._trace(trace_path, "delegation_finished", output_chars=len(output))
        return DelegationResult(
            run_id=run_id,
            output=output,
            trace_path=trace_path,
            pack=pack,
            base_commits=base_commits,
        )

    def _trace(self, path: Path, event: str, **fields) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
