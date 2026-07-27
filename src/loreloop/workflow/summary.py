"""Agent-authored task narrative kept separate from machine evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..evidence.chain import EvidenceChain, EvidenceRecord

TASK_SUMMARY_EVENT = "task_summary_recorded"
BUG_FINDING_EVENT = "task_bug_recorded"


@dataclass(frozen=True, slots=True)
class TaskNarrative:
    run_id: str
    analysis: str
    implementation: str
    acceptance: tuple[str, ...]
    risks: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "analysis": self.analysis,
            "implementation": self.implementation,
            "acceptance": list(self.acceptance),
            "risks": list(self.risks),
            "authorship": "host-agent",
        }


@dataclass(frozen=True, slots=True)
class BugRecord:
    run_id: str
    bug_id: str
    test_steps: tuple[str, ...]
    description: str
    root_cause: str
    recommendation: str
    fix_solution: str
    modified_files: tuple[str, ...]
    test_proof: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bug_id": self.bug_id,
            "test_steps": list(self.test_steps),
            "description": self.description,
            "root_cause": self.root_cause,
            "recommendation": self.recommendation,
            "fix_solution": self.fix_solution,
            "modified_files": list(self.modified_files),
            "test_proof": list(self.test_proof),
            "authorship": "host-agent",
        }


def record_task_narrative(
    chain: EvidenceChain,
    run_id: str,
    analysis: str,
    implementation: str,
    acceptance: tuple[str, ...],
    risks: tuple[str, ...],
) -> tuple[TaskNarrative, EvidenceRecord]:
    narrative = TaskNarrative(
        _text(run_id, "run id", 256),
        _text(analysis, "analysis", 8_000),
        _text(implementation, "implementation", 8_000),
        _items(acceptance, "acceptance"),
        _items(risks, "risk"),
    )
    return narrative, chain.append(TASK_SUMMARY_EVENT, narrative.to_json())


def latest_task_narrative(run_id: str, records: list[EvidenceRecord]) -> TaskNarrative | None:
    record = next(
        (
            item
            for item in reversed(records)
            if item.event == TASK_SUMMARY_EVENT and item.payload.get("run_id") == run_id
        ),
        None,
    )
    if record is None:
        return None
    payload = record.payload
    return TaskNarrative(
        run_id,
        str(payload.get("analysis", "")),
        str(payload.get("implementation", "")),
        tuple(item for item in payload.get("acceptance", []) if isinstance(item, str)),
        tuple(item for item in payload.get("risks", []) if isinstance(item, str)),
    )


def record_bug(
    chain: EvidenceChain,
    run_id: str,
    bug_id: str,
    test_steps: tuple[str, ...],
    description: str,
    root_cause: str,
    recommendation: str,
    fix_solution: str,
    modified_files: tuple[str, ...],
    test_proof: tuple[str, ...],
) -> tuple[BugRecord, EvidenceRecord]:
    bug = BugRecord(
        _text(run_id, "run id", 256),
        _text(bug_id, "bug id", 128),
        _required_items(test_steps, "test step"),
        _text(description, "description", 8_000),
        _text(root_cause, "root cause", 8_000),
        _text(recommendation, "recommendation", 8_000),
        _text(fix_solution, "fix solution", 8_000),
        _required_items(modified_files, "modified file"),
        _required_items(test_proof, "test proof"),
    )
    return bug, chain.append(BUG_FINDING_EVENT, bug.to_json())


def task_bugs(run_id: str, records: list[EvidenceRecord]) -> tuple[BugRecord, ...]:
    latest: dict[str, EvidenceRecord] = {}
    for record in records:
        if record.event == BUG_FINDING_EVENT and record.payload.get("run_id") == run_id:
            bug_id = record.payload.get("bug_id")
            if isinstance(bug_id, str):
                latest[bug_id] = record
    bugs: list[BugRecord] = []
    for bug_id, record in sorted(latest.items()):
        payload = record.payload
        bugs.append(
            BugRecord(
                run_id,
                bug_id,
                _string_items(payload.get("test_steps")),
                str(payload.get("description", "")),
                str(payload.get("root_cause", "")),
                str(payload.get("recommendation", "")),
                str(payload.get("fix_solution", "")),
                _string_items(payload.get("modified_files")),
                _string_items(payload.get("test_proof")),
            )
        )
    return tuple(bugs)


def _text(value: str, label: str, maximum: int) -> str:
    clean = value.strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{label} must be 1..{maximum} characters")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in clean):
        raise ValueError(f"{label} contains control characters")
    return clean


def _items(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(values) > 32:
        raise ValueError(f"{label} list must contain at most 32 items")
    return tuple(_text(value, label, 2_000) for value in values)


def _required_items(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    items = _items(values, label)
    if not items:
        raise ValueError(f"{label} list must not be empty")
    return items


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
