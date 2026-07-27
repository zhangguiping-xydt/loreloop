"""Task-scoped knowledge checkpoints and source-coverage gates.

The project baseline is immutable during a task.  This module records a
short-lived, signed context checkpoint whenever the change-impact workflow is
run.  A checkpoint binds the knowledge ids that were available to the exact
source snapshot and records conservative file/domain coverage.  It is not a
claim that a model understood a fact internally; it is an auditable proof of
what LoreLoop injected and whether the resulting diff was covered.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..knowledge.model import Entry, Kind
from ..knowledge.repos import RepoConfigError, parse_code_locator
from .model import SourceChange
from .snapshot import capture_task_source_snapshot, compare_task_source_snapshots

TASK_CONTEXT_EVENT = "task_context_refreshed"
_HIGH_RISK = re.compile(
    r"(?:^|/)(?:api|apis|interface|interfaces|auth|authentication|permission|permissions|"
    r"role|roles|middleware|schema|schemas|migration|migrations|database|db|config|"
    r"configuration|external|integration)(?:[._/-]|$)|"
    r"(?:^|/)(?:openapi|swagger|graphql|routes?|models?|settings?)(?:[._/-]|$)|"
    r"(?:^|/)(?:pyproject|package\.json|uv\.lock|package-lock|pnpm-lock|yarn\.lock)$",
    re.IGNORECASE,
)


def is_high_risk_path(path: str) -> bool:
    """Return whether a changed path can alter a public/high-impact contract."""
    return bool(_HIGH_RISK.search(path.replace("\\", "/")))


def latest_task_context(run_id: str, records: Iterable[EvidenceRecord]) -> EvidenceRecord | None:
    ordered = list(records)
    refreshed = next(
        (
            record
            for record in reversed(ordered)
            if record.event == TASK_CONTEXT_EVENT
            and record.payload.get("run_id") == run_id
            and isinstance(record.payload.get("context_source_snapshot"), str)
        ),
        None,
    )
    if refreshed is not None:
        return refreshed
    return next(
        (
            record
            for record in reversed(ordered)
            if record.event in {"delegation_prepared", "delegation_completed"}
            and record.payload.get("run_id") == run_id
            and isinstance(record.payload.get("context_source_snapshot"), str)
        ),
        None,
    )


def refresh_task_context(
    workdir: Path,
    run_id: str,
    task: str,
    changes: tuple[SourceChange, ...],
    entries: list[Entry],
    initial_entry_ids: Iterable[str],
    records: list[EvidenceRecord],
    chain: EvidenceChain,
    artifacts: ArtifactStore,
    *,
    endorsed_ids: set[str] | frozenset[str] = frozenset(),
    unendorsed_ids: set[str] | frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], EvidenceRecord]:
    """Select an incremental pack and bind it to the current source snapshot."""
    from ..delegate.context_pack import select

    after = capture_task_source_snapshot(workdir)
    snapshot_artifact = artifacts.save_json(after)[0]
    prior = latest_task_context(run_id, records)
    previous_generation = 0
    if prior is not None:
        previous_generation = int(prior.payload.get("context_generation", 0) or 0)
    changed_paths = [change.path for change in changes]
    query = " ".join([task, *changed_paths])
    pack = select(
        query,
        entries,
        drifted_ids=unendorsed_ids,
        unendorsed_ids=unendorsed_ids,
        endorsed_ids=endorsed_ids,
        limit=24,
    )
    # A source anchor is stronger than lexical coincidence.  An API contract
    # whose title does not repeat the filename must still be injected when the
    # file changes; otherwise a successful BM25 score could hide precisely the
    # knowledge needed for a high-risk edit.
    anchored = [
        entry
        for entry in entries
        if any(_entry_covers_path(entry, change.repository, change.path) for change in changes)
    ]
    initial = [value for value in initial_entry_ids if isinstance(value, str)]
    selected_ids = list(
        dict.fromkeys([*initial, *pack.entry_ids, *(entry.id for entry in anchored)])
    )
    by_id = {entry.id: entry for entry in entries}
    strong_ids = [
        entry_id
        for entry_id in selected_ids
        if (entry := by_id.get(entry_id)) is not None
        and entry_id not in unendorsed_ids
        and (entry.is_strong_evidence() or entry_id in endorsed_ids)
    ]
    added = [value for value in selected_ids if value not in initial]
    coverage = _coverage(changes, entries, selected_ids, endorsed_ids, unendorsed_ids)
    state = {
        "version": 1,
        "type": "task_context",
        "run_id": run_id,
        "context_generation": previous_generation + 1,
        "context_source_snapshot": snapshot_artifact,
        "context_entry_ids": selected_ids,
        "strong_context_entry_ids": strong_ids,
        "reference_context_entry_ids": [
            entry_id for entry_id in selected_ids if entry_id not in strong_ids
        ],
        "initial_context_entry_ids": initial,
        "added_context_entry_ids": added,
        "changed_files": [f"{c.repository}:{c.path}" for c in changes],
        "coverage": coverage,
    }
    state_artifact = artifacts.save_json(state)[0]
    record = chain.append(
        TASK_CONTEXT_EVENT,
        {
            **state,
            "context_artifact": state_artifact,
        },
    )
    return state, record


def knowledge_gate_problems(
    workdir: Path,
    run_id: str,
    records: list[EvidenceRecord],
    artifacts: ArtifactStore,
) -> list[str]:
    """Return blocking problems for a new, source-bound session.

    Legacy hand-written evidence records without a context checkpoint remain
    readable for backwards compatibility; sessions created by ``begin`` carry
    ``context_generation`` and are fully gated.
    """
    source_bound_run = next(
        (
            record
            for record in records
            if record.event in {"delegation_prepared", "delegation_completed"}
            and record.payload.get("run_id") == run_id
            and "context_generation" in record.payload
        ),
        None,
    )
    if source_bound_run is None:
        return []
    checkpoint = latest_task_context(run_id, records)
    if checkpoint is None:
        return ["no signed knowledge checkpoint exists for this task"]
    snapshot_ref = checkpoint.payload.get("context_source_snapshot")
    if not isinstance(snapshot_ref, str):
        return ["latest knowledge checkpoint has no source snapshot"]
    try:
        before = artifacts.load(snapshot_ref)
        after = capture_task_source_snapshot(workdir)
        changes = compare_task_source_snapshots(before, after)
    except (OSError, ValueError, KeyError) as exc:
        return [f"cannot validate latest knowledge checkpoint: {exc}"]
    problems: list[str] = []
    if changes:
        problems.append(
            f"source changed after knowledge checkpoint (run `loreloop test select {run_id}` again)"
        )
    coverage = checkpoint.payload.get("coverage")
    if not isinstance(coverage, dict):
        return problems + ["latest knowledge checkpoint has no coverage record"]
    missing = coverage.get("high_risk_missing")
    if isinstance(missing, list) and missing:
        problems.append(
            "high-risk changes lack project-knowledge coverage: " + ", ".join(map(str, missing))
        )
    untrusted = coverage.get("high_risk_untrusted")
    if isinstance(untrusted, list) and untrusted:
        problems.append(
            "high-risk changes are covered only by unverified/reference knowledge: "
            + ", ".join(map(str, untrusted))
        )
    return problems


def _coverage(
    changes: tuple[SourceChange, ...],
    entries: list[Entry],
    selected_ids: list[str],
    endorsed_ids: set[str] | frozenset[str],
    unendorsed_ids: set[str] | frozenset[str],
) -> dict[str, Any]:
    by_id = {entry.id: entry for entry in entries}
    covered_files: dict[str, list[str]] = {}
    high_risk_missing: list[str] = []
    high_risk_untrusted: list[str] = []
    for change in changes:
        key = f"{change.repository}:{change.path}"
        matches = [
            entry.id
            for entry_id in selected_ids
            if (entry := by_id.get(entry_id)) is not None
            and _entry_covers_path(entry, change.repository, change.path)
        ]
        if matches:
            covered_files[key] = matches
            trusted_matches = [
                entry_id
                for entry_id in matches
                if (entry := by_id.get(entry_id)) is not None
                and entry_id not in unendorsed_ids
                and (entry.is_strong_evidence() or entry_id in endorsed_ids)
            ]
            if is_high_risk_path(change.path) and not trusted_matches:
                high_risk_untrusted.append(key)
        elif is_high_risk_path(change.path):
            high_risk_missing.append(key)
    domains = sorted({_domain(change.path) for change in changes if is_high_risk_path(change.path)})
    return {
        "files": covered_files,
        "changed_files": [f"{c.repository}:{c.path}" for c in changes],
        "domains": domains,
        "high_risk_missing": high_risk_missing,
        "high_risk_untrusted": high_risk_untrusted,
    }


def _entry_covers_path(entry: Entry, repository: str, path: str) -> bool:
    if entry.source.channel.value != "code":
        return False
    try:
        repo, entry_path, _ = parse_code_locator(entry.source.locator)
    except RepoConfigError:
        return False
    if repo != repository or entry_path != path:
        return False
    return entry.kind in {
        Kind.REQUIREMENT,
        Kind.INTERFACE,
        Kind.ARCHITECTURE,
        Kind.CONSTRAINT,
        Kind.BEHAVIOR,
        Kind.ACCEPTANCE,
    }


def _domain(path: str) -> str:
    lower = path.casefold()
    for domain in ("api", "auth", "permission", "database", "config", "external"):
        if domain in lower:
            return domain
    return "high-risk"
