"""Agent-authored task narrative kept separate from machine evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..evidence.chain import EvidenceChain, EvidenceRecord

TASK_SUMMARY_EVENT = "task_summary_recorded"


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


def latest_task_narrative(
    run_id: str, records: list[EvidenceRecord]
) -> TaskNarrative | None:
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
