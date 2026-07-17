"""Typed task, impact and test-plan records used by the host-agent workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

TaskKind = Literal["bug", "feature", "task"]
ChangeKind = Literal["added", "modified", "deleted"]
TestTier = Literal["must", "recommended", "missing"]
TestStatus = Literal["passed", "failed", "timed-out", "skipped"]

_BUG = re.compile(
    r"\b(bug|fix|error|failure|failed|crash|incorrect|regression)\b|"
    r"修复|报错|错误|异常|失败|问题|缺陷|不正确",
    re.IGNORECASE,
)
_FEATURE = re.compile(
    r"\b(add|feature|implement|support|introduce|requirement)\b|"
    r"新增|增加|实现|支持|需求|开发",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TaskIntent:
    text: str
    kind: TaskKind

    @classmethod
    def from_text(cls, text: str) -> "TaskIntent":
        clean = text.strip()
        if not clean:
            raise ValueError("task text must be non-empty")
        kind: TaskKind = "bug" if _BUG.search(clean) else "feature" if _FEATURE.search(clean) else "task"
        return cls(clean, kind)

    def to_json(self) -> dict[str, str]:
        return {"text": self.text, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class SourceChange:
    repository: str
    path: str
    kind: ChangeKind

    def to_json(self) -> dict[str, str]:
        return {"repository": self.repository, "path": self.path, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class TestSelection:
    tier: TestTier
    repository: str
    path: str | None
    name: str
    framework: str | None
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "repository": self.repository,
            "path": self.path,
            "name": self.name,
            "framework": self.framework,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TestCommand:
    repository: str
    argv: tuple[str, ...]
    covers: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "argv": list(self.argv),
            "covers": list(self.covers),
        }


@dataclass(frozen=True, slots=True)
class TaskTestPlan:
    run_id: str
    intent: TaskIntent
    changes: tuple[SourceChange, ...]
    selections: tuple[TestSelection, ...]
    commands: tuple[TestCommand, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "type": "task_test_plan",
            "run_id": self.run_id,
            "intent": self.intent.to_json(),
            "changes": [change.to_json() for change in self.changes],
            "selections": [selection.to_json() for selection in self.selections],
            "commands": [command.to_json() for command in self.commands],
        }


@dataclass(frozen=True, slots=True)
class TestExecutionResult:
    repository: str
    argv: tuple[str, ...]
    status: TestStatus
    exit_code: int | None
    artifact: str | None
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "argv": list(self.argv),
            "status": self.status,
            "exit_code": self.exit_code,
            "artifact": self.artifact,
            "reason": self.reason,
        }
