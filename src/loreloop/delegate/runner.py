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
from ..knowledge.code_reverse import IngestionPolicy, drifted_code_entry_ids
from ..knowledge.model import Entry
from ..knowledge.repos import load_repos
from ..paths import ensure_private_directory, ensure_state_root, secure_append_text, state_path
from .context_pack import ContextPack, render, select


def _repository_snapshot(workdir: Path) -> tuple[dict[str, str], dict[str, str]]:
    heads: dict[str, str] = {}
    roots: dict[str, str] = {}
    for name, repo in {".": workdir.resolve(), **load_repos(workdir)}.items():
        resolved = repo.resolve()
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=resolved, capture_output=True, text=True
        )
        if result.returncode == 0:
            heads[name] = result.stdout.strip()
            roots[name] = str(resolved)
    return heads, roots


@dataclass(frozen=True)
class RunPreparation:
    run_id: str
    prompt: str
    trace_path: Path
    pack: ContextPack
    base_commits: dict[str, str]
    repository_roots: dict[str, str]

    @property
    def base_commit(self) -> str | None:
        return self.base_commits.get(".")


@dataclass(frozen=True)
class DelegationResult:
    run_id: str
    output: str
    trace_path: Path
    pack: ContextPack
    base_commits: dict[str, str]
    repository_roots: dict[str, str]

    @property
    def base_commit(self) -> str | None:
        return self.base_commits.get(".")


class DelegateRunner:
    def __init__(self, agent: AgentRunner | None, workdir: Path) -> None:
        self._agent = agent
        self._workdir = workdir
        ensure_state_root(workdir)
        self._runs_dir = ensure_private_directory(state_path(workdir, "runs"))

    def prepare(
        self,
        task: str,
        entries: list[Entry],
        unendorsed_ids: set[str] | frozenset[str] = frozenset(),
        endorsed_ids: set[str] | frozenset[str] = frozenset(),
        expansion: str = "",
        related: list[ForeignEntry] | None = None,
        ingestion_policies: dict[str, IngestionPolicy] | None = None,
        mode: str = "delegated",
        requirement_context: str = "",
        requirement_materials: list[dict[str, str]] | None = None,
    ) -> RunPreparation:
        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        trace_path = self._runs_dir / f"{run_id}.jsonl"
        base_commits, repository_roots = _repository_snapshot(self._workdir)
        drifted = (
            drifted_code_entry_ids(self._workdir, entries, policies=ingestion_policies)
            if base_commits
            else set()
        )
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
        task_prompt = f"# Task\n\n{task}\n"
        prompt = "\n".join(
            part.rstrip() for part in (prefix, requirement_context, task_prompt) if part
        ) + "\n"

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
            repository_roots=repository_roots,
            ingestion_policies={
                name: (ingestion_policies or {}).get(name, IngestionPolicy()).payload()
                for name in sorted(base_commits)
            },
            related_entries=pack.related_ids,
            mode=mode,
            requirement_materials=requirement_materials or [],
        )
        return RunPreparation(
            run_id=run_id,
            prompt=prompt,
            trace_path=trace_path,
            pack=pack,
            base_commits=base_commits,
            repository_roots=repository_roots,
        )

    def run(
        self,
        task: str,
        entries: list[Entry],
        unendorsed_ids: set[str] | frozenset[str] = frozenset(),
        endorsed_ids: set[str] | frozenset[str] = frozenset(),
        expansion: str = "",
        related: list[ForeignEntry] | None = None,
        ingestion_policies: dict[str, IngestionPolicy] | None = None,
    ) -> DelegationResult:
        if self._agent is None:
            raise RuntimeError("delegated run requires an agent runner")
        prepared = self.prepare(
            task,
            entries,
            unendorsed_ids=unendorsed_ids,
            endorsed_ids=endorsed_ids,
            expansion=expansion,
            related=related,
            ingestion_policies=ingestion_policies,
        )
        try:
            output = self._agent.run(prepared.prompt)
        except KeyboardInterrupt:
            self._trace(prepared.trace_path, "delegation_interrupted", reason="operator cancelled")
            raise
        except Exception as exc:
            self._trace(prepared.trace_path, "delegation_failed", error=str(exc))
            raise
        self.finish(prepared.trace_path, output_chars=len(output), mode="delegated")
        return DelegationResult(
            run_id=prepared.run_id,
            output=output,
            trace_path=prepared.trace_path,
            pack=prepared.pack,
            base_commits=prepared.base_commits,
            repository_roots=prepared.repository_roots,
        )

    def finish(self, trace_path: Path, *, output_chars: int = 0, mode: str) -> None:
        self._trace(
            trace_path,
            "delegation_finished",
            output_chars=output_chars,
            mode=mode,
        )

    def _trace(self, path: Path, event: str, **fields) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        secure_append_text(path, json.dumps(record, ensure_ascii=False) + "\n")
