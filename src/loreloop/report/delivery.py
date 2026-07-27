"""Human-readable per-change Markdown delivery reports."""

from __future__ import annotations

import shlex
import os
from datetime import datetime, timezone
from pathlib import Path

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceRecord
from ..workflow.execution import TASK_TEST_EXECUTED_EVENT
from ..workflow.impact import latest_task_test_plan
from ..workflow.summary import task_bugs
from .acceptance import RunEvaluation, RunSummary


def render_change_reports(
    run: RunSummary,
    records: list[EvidenceRecord],
    artifacts: ArtifactStore,
    evaluation: RunEvaluation,
) -> dict[str, str]:
    """Render the auxiliary Markdown files beside acceptance-report.md."""
    completed = evaluation.completed
    task = str(completed.payload.get("task", run.task)) if completed else run.task
    generated = datetime.now(timezone.utc).isoformat()
    return {
        "test-report.md": _test_report(run.run_id, task, generated, records, artifacts, evaluation),
        "bug-findings.md": _bug_findings(run.run_id, task, generated, records),
        "fix-report.md": _fix_report(run.run_id, task, generated, records),
    }


def write_change_reports(directory: Path, reports: dict[str, str]) -> tuple[Path, ...]:
    written: list[Path] = []
    directory.mkdir(parents=True, exist_ok=True)
    targets = {name: directory / name for name in reports}
    for path in targets.values():
        if path.is_symlink():
            raise ValueError(f"refusing symlinked report output: {path}")
    for name, content in reports.items():
        path = targets[name]
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        written.append(path)
    return tuple(written)


def _test_report(
    run_id: str,
    task: str,
    generated: str,
    records: list[EvidenceRecord],
    artifacts: ArtifactStore,
    evaluation: RunEvaluation,
) -> str:
    plan = latest_task_test_plan(run_id, records, artifacts)
    lines = _header("测试报告", run_id, task, generated)
    if plan is None:
        lines.extend(["## 测试范围", "", "尚未生成任务测试计划。", ""])
    else:
        lines.extend(
            [
                "## 测试范围",
                "",
                f"- 变更文件：{len(plan.changes)}",
                f"- 必须执行：{sum(item.tier == 'must' for item in plan.selections)}",
                f"- 建议执行：{sum(item.tier == 'recommended' for item in plan.selections)}",
                f"- 覆盖缺口：{sum(item.tier == 'missing' for item in plan.selections)}",
                "",
                "| 级别 | 测试项 | 位置 | 选择原因 |",
                "|---|---|---|---|",
            ]
        )
        for item in plan.selections:
            location = f"{item.repository}:{item.path}" if item.path else item.repository
            lines.append(
                f"| {item.tier.upper()} | {_md(item.name)} | `{_md(location)}` | {_md(item.reason)} |"
            )
        lines.append("")
        if plan.commands:
            lines.extend(["## 执行命令", ""])
            lines.extend(f"- `{_md(shlex.join(command.argv))}`" for command in plan.commands)
            lines.append("")
    executions = [
        record
        for record in records
        if record.event == TASK_TEST_EXECUTED_EVENT and record.payload.get("run_id") == run_id
    ]
    lines.extend(["## 执行结果", ""])
    if not executions and not evaluation.checks:
        lines.extend(["尚无自动化测试或验收检查证据。", ""])
    else:
        lines.extend(["| 类型 | 命令/检查 | 结果 | 证据 |", "|---|---|---|---|"])
        for record in executions:
            command = record.payload.get("command", [])
            rendered = shlex.join(command) if isinstance(command, list) else str(command)
            artifact = str(record.payload.get("artifact", ""))[:16] or "-"
            lines.append(
                f"| 开发期测试 | `{_md(rendered)}` | {record.payload.get('status', 'unknown')} | `{artifact}` |"
            )
        for record in evaluation.checks:
            status = "PASS" if record.event == "check_passed" else "FAIL"
            artifact = str(record.payload.get("artifact", ""))[:16] or "人工确认"
            lines.append(
                f"| 验收检查 | {_md(str(record.payload.get('check', '?')))} | {status} | `{_md(artifact)}` |"
            )
        lines.append("")
    lines.extend(
        [
            "## 结论",
            "",
            f"- 通过：{len(evaluation.passed)}",
            f"- 失败：{len(evaluation.failed)}",
            f"- 最终验收：{'通过' if evaluation.accepted else '未通过'}",
            "",
        ]
    )
    return "\n".join(lines)


def _bug_findings(run_id: str, task: str, generated: str, records: list[EvidenceRecord]) -> str:
    bugs = task_bugs(run_id, records)
    lines = _header("Bug 发现清单", run_id, task, generated)
    if not bugs:
        lines.extend(
            [
                "本次任务没有记录结构化 Bug 条目。若任务中发现问题，应由 Agent 使用",
                "`loreloop task bug` 写入测试步骤、问题描述、根因和修改建议。",
                "",
            ]
        )
        return "\n".join(lines)
    for bug in bugs:
        lines.extend(
            [
                f"## {bug.bug_id}",
                "",
                "### 测试步骤",
                "",
                *[f"{index}. {step}" for index, step in enumerate(bug.test_steps, start=1)],
                "",
                "### 问题描述",
                "",
                bug.description,
                "",
                "### 根因分析",
                "",
                bug.root_cause,
                "",
                "### 修改建议",
                "",
                bug.recommendation,
                "",
            ]
        )
    return "\n".join(lines)


def _fix_report(run_id: str, task: str, generated: str, records: list[EvidenceRecord]) -> str:
    bugs = task_bugs(run_id, records)
    lines = _header("修复报告", run_id, task, generated)
    if not bugs:
        lines.extend(["本次任务没有记录可对应的 Bug 修复条目。", ""])
        return "\n".join(lines)
    for bug in bugs:
        lines.extend(
            [
                f"## {bug.bug_id}",
                "",
                "### 根因确认",
                "",
                bug.root_cause,
                "",
                "### 修复方案",
                "",
                bug.fix_solution,
                "",
                "### 修改文件",
                "",
                *[f"- `{_md(path)}`" for path in bug.modified_files],
                "",
                "### 测试证明",
                "",
                *[f"- {proof}" for proof in bug.test_proof],
                "",
            ]
        )
    return "\n".join(lines)


def _header(title: str, run_id: str, task: str, generated: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"- 运行编号：`{_md(run_id)}`",
        f"- 任务：{_md(task)}",
        f"- 生成时间：{generated}",
        "",
    ]


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
