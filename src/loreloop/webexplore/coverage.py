"""Evidence-backed Web journey, page-state and control coverage reporting."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceRecord
from .scenarios import (
    WEB_EXPLORATION_EVENT,
    WEB_TEST_EXECUTED_EVENT,
    WEB_TEST_TRIALED_EVENT,
    WebScenario,
    list_approved_scenarios,
    list_candidate_scenarios,
)

_WRITE_CONTROL = re.compile(
    r"\b(add|create|edit|save|submit|delete|remove|activate|deactivate|approve|reject)\b|"
    r"新增|新建|编辑|保存|提交|删除|移除|生效|失效|审批|驳回",
    re.IGNORECASE,
)


@dataclass
class PageCoverage:
    url: str
    titles: set[str] = field(default_factory=set)
    snapshots: set[str] = field(default_factory=set)
    artifacts: set[str] = field(default_factory=set)
    controls: dict[tuple[str, str], str] = field(default_factory=dict)
    tested: bool = False
    trialed: bool = False

    def to_json(self) -> dict[str, Any]:
        controls = [
            {"kind": kind, "label": label, "status": status}
            for (kind, label), status in sorted(self.controls.items())
        ]
        return {
            "url": self.url,
            "titles": sorted(self.titles),
            "state_count": len(self.snapshots),
            "tested": self.tested,
            "trialed": self.trialed,
            "controls": controls,
        }


@dataclass(frozen=True)
class JourneyCoverage:
    scenario_id: str
    title: str
    risk: str
    authority: str
    status: str
    steps: int
    assertions: int
    state_count: int
    target_url: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.scenario_id,
            "title": self.title,
            "risk": self.risk,
            "authority": self.authority,
            "status": self.status,
            "steps": self.steps,
            "assertions": self.assertions,
            "state_count": self.state_count,
            "target_url": self.target_url,
        }


@dataclass(frozen=True)
class WebCoverageReport:
    pages: tuple[PageCoverage, ...]
    journeys: tuple[JourneyCoverage, ...]

    @property
    def summary(self) -> dict[str, int]:
        controls = [status for page in self.pages for status in page.controls.values()]
        return {
            "pages_observed": len(self.pages),
            "pages_tested": sum(page.tested for page in self.pages),
            "pages_trialed": sum(page.trialed and not page.tested for page in self.pages),
            "states_observed": sum(len(page.snapshots) for page in self.pages),
            "controls_observed": len(controls),
            "controls_exercised": controls.count("exercised"),
            "controls_trial_exercised": controls.count("trial-exercised"),
            "controls_write_gated": controls.count("write-gated"),
            "journeys_candidate": sum(j.authority == "candidate" for j in self.journeys),
            "journeys_approved": sum(j.authority == "approved" for j in self.journeys),
            "journeys_passed": sum(j.status == "passed" for j in self.journeys),
            "journeys_failed": sum(j.status in {"failed", "blocked"} for j in self.journeys),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "summary": self.summary,
            "pages": [page.to_json() for page in self.pages],
            "journeys": [journey.to_json() for journey in self.journeys],
        }


def build_web_coverage(
    workdir: Path,
    records: list[EvidenceRecord],
    artifacts: ArtifactStore,
) -> WebCoverageReport:
    pages: dict[str, PageCoverage] = {}
    executions: dict[str, EvidenceRecord] = {}
    trials: dict[str, EvidenceRecord] = {}
    exercised: dict[str, set[tuple[str, str]]] = {}
    trial_exercised: dict[str, set[tuple[str, str]]] = {}

    for record in records:
        if record.event == WEB_EXPLORATION_EVENT:
            for page in _list(record.payload.get("pages")):
                _add_artifact_page(pages, page, artifacts, tested=False, trialed=False)
        elif record.event == WEB_TEST_EXECUTED_EVENT:
            scenario_id = record.payload.get("scenario_id")
            if isinstance(scenario_id, str):
                executions[scenario_id] = record
        elif record.event == WEB_TEST_TRIALED_EVENT:
            scenario_id = record.payload.get("scenario_id")
            if isinstance(scenario_id, str):
                trials[scenario_id] = record

    for record in executions.values():
        states = _list(record.payload.get("states"))
        for state in states:
            page = _add_artifact_page(
                pages, state, artifacts, tested=True, trialed=False
            )
            if page is not None:
                page.tested = True
        if not states and isinstance(record.payload.get("observation_artifact"), str):
            _add_artifact_page(
                pages,
                {"artifact": record.payload["observation_artifact"]},
                artifacts,
                tested=True,
                trialed=False,
            )
        trace_sha = record.payload.get("trace_artifact")
        if isinstance(trace_sha, str):
            trace = artifacts.load(trace_sha)
            state_urls = {
                state.get("step_index"): state.get("url")
                for state in _list(trace.get("states"))
                if isinstance(state.get("step_index"), int)
                and isinstance(state.get("url"), str)
            }
            for step in _list(trace.get("steps")):
                if step.get("status") != "completed":
                    continue
                action = step.get("action")
                if not isinstance(action, dict) or len(action) != 1:
                    continue
                operation, argument = next(iter(action.items()))
                if operation not in {"click", "fill", "select"} or not isinstance(
                    argument, dict
                ):
                    continue
                label = _locator_label(argument)
                step_index = step.get("index")
                url = state_urls.get(step_index) or step.get("url")
                if label and isinstance(url, str):
                    exercised.setdefault(url, set()).add((operation, label.casefold()))

    for record in trials.values():
        for state in _list(record.payload.get("states")):
            _add_artifact_page(pages, state, artifacts, tested=False, trialed=True)
        trace_sha = record.payload.get("trace_artifact")
        if not isinstance(trace_sha, str):
            continue
        trace = artifacts.load(trace_sha)
        state_urls = {
            state.get("step_index"): state.get("url")
            for state in _list(trace.get("states"))
            if isinstance(state.get("step_index"), int)
            and isinstance(state.get("url"), str)
        }
        for step in _list(trace.get("steps")):
            action = step.get("action")
            if step.get("status") != "completed" or not isinstance(action, dict) or len(action) != 1:
                continue
            operation, argument = next(iter(action.items()))
            if operation not in {"click", "fill", "select"} or not isinstance(argument, dict):
                continue
            label = _locator_label(argument)
            url = state_urls.get(step.get("index")) or step.get("url")
            if label and isinstance(url, str):
                trial_exercised.setdefault(url, set()).add((operation, label.casefold()))

    for url, labels in exercised.items():
        page = pages.get(url)
        if page is None:
            continue
        for key in tuple(page.controls):
            kind, label = key
            if _control_exercised(kind, label, labels):
                page.controls[key] = "exercised"
    for url, labels in trial_exercised.items():
        page = pages.get(url)
        if page is None:
            continue
        for key, status in tuple(page.controls.items()):
            kind, label = key
            if status != "exercised" and _control_exercised(kind, label, labels):
                page.controls[key] = "trial-exercised"

    scenarios: dict[str, tuple[str, WebScenario]] = {}
    for _, scenario in list_candidate_scenarios(workdir):
        scenarios[scenario.scenario_id] = ("candidate", scenario)
    for _, scenario in list_approved_scenarios(workdir):
        scenarios[scenario.scenario_id] = ("approved", scenario)

    journeys: list[JourneyCoverage] = []
    for scenario_id, (authority, scenario) in sorted(scenarios.items()):
        execution = executions.get(scenario_id)
        trial = trials.get(scenario_id)
        status = str(execution.payload.get("status", "not-run")) if execution else (
            f"trial-{trial.payload.get('status', 'failed')}" if trial else "not-run"
        )
        evidence = execution or trial
        states = _list(evidence.payload.get("states")) if evidence else []
        journeys.append(
            JourneyCoverage(
                scenario_id,
                scenario.title,
                scenario.risk,
                authority,
                status,
                len(scenario.script.steps),
                len(scenario.assertions),
                len(states),
                _scenario_target(scenario),
            )
        )
    return WebCoverageReport(
        tuple(pages[url] for url in sorted(pages)),
        tuple(journeys),
    )


def render_web_coverage(report: WebCoverageReport, format_name: str) -> str:
    if format_name == "json":
        return json.dumps(report.to_json(), ensure_ascii=False, indent=2) + "\n"
    if format_name == "markdown":
        return _render_markdown(report)
    if format_name != "summary":
        raise ValueError(f"unsupported Web coverage format: {format_name}")
    summary = report.summary
    return "\n".join(
        [
            "Web coverage",
            f"pages: {summary['pages_tested']}/{summary['pages_observed']} tested",
            f"trial pages: {summary['pages_trialed']}",
            f"states: {summary['states_observed']} observed",
            f"controls: {summary['controls_exercised']}/{summary['controls_observed']} exercised "
            f"({summary['controls_trial_exercised']} trial, "
            f"{summary['controls_write_gated']} write-gated)",
            f"journeys: {summary['journeys_passed']} passed, "
            f"{summary['journeys_failed']} failed, "
            f"{summary['journeys_approved']} approved, "
            f"{summary['journeys_candidate']} candidate",
        ]
    ) + "\n"


def _add_artifact_page(
    pages: dict[str, PageCoverage],
    reference: dict[str, Any],
    artifacts: ArtifactStore,
    *,
    tested: bool,
    trialed: bool,
) -> PageCoverage | None:
    artifact = reference.get("artifact")
    if not isinstance(artifact, str):
        return None
    observation = artifacts.load(artifact)
    if observation.get("type") != "page_observation":
        return None
    url = observation.get("url")
    snapshot = observation.get("snapshot_hash")
    if not isinstance(url, str) or not isinstance(snapshot, str):
        return None
    page = pages.setdefault(url, PageCoverage(url))
    title = observation.get("title")
    if isinstance(title, str) and title.strip():
        page.titles.add(title.strip())
    page.snapshots.add(snapshot)
    page.artifacts.add(artifact)
    page.tested = page.tested or tested
    page.trialed = page.trialed or trialed
    for kind, label in _controls(observation):
        status = "write-gated" if kind == "button" and _WRITE_CONTROL.search(label) else "observed-only"
        page.controls.setdefault((kind, label), status)
    return page


def _controls(observation: dict[str, Any]):
    for label in _values(observation.get("buttons")):
        if isinstance(label, str) and label.strip():
            yield "button", label.strip()
    for form in _values(observation.get("forms")):
        if not isinstance(form, str):
            continue
        for raw in form.split(","):
            parts = raw.split(":", 3)
            label = next((part.strip() for part in reversed(parts[1:]) if part.strip()), "")
            if label:
                yield "field", label


def _control_exercised(
    kind: str, label: str, exercised: set[tuple[str, str]]
) -> bool:
    folded = label.casefold()
    for operation, locator in exercised:
        if (kind == "button" and operation == "click") or (
            kind == "field" and operation in {"fill", "select"}
        ):
            if locator == folded or locator in folded or folded in locator:
                return True
    return False


def _locator_label(argument: dict[str, Any]) -> str | None:
    for key in ("label", "text", "role"):
        value = argument.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _scenario_target(scenario: WebScenario) -> str:
    for step in scenario.script.steps:
        if step.op == "goto":
            return urljoin(scenario.script.base, step.arg)
    return scenario.script.base


def _render_markdown(report: WebCoverageReport) -> str:
    summary = report.summary
    lines = [
        "# Web Coverage Checklist",
        "",
        "## Summary",
        "",
        f"- Pages tested: {summary['pages_tested']} / {summary['pages_observed']}",
        f"- Pages trialed only: {summary['pages_trialed']}",
        f"- Page states observed: {summary['states_observed']}",
        f"- Controls exercised: {summary['controls_exercised']} / {summary['controls_observed']}",
        f"- Controls exercised in trial only: {summary['controls_trial_exercised']}",
        f"- Write-gated controls: {summary['controls_write_gated']}",
        f"- Journeys passed: {summary['journeys_passed']}",
        "",
        "## Pages and states",
        "",
    ]
    if not report.pages:
        lines.append("- [ ] No page evidence captured")
    for page in report.pages:
        mark = "x" if page.tested else " "
        title = sorted(page.titles)[0] if page.titles else "Untitled"
        page_suffix = " — trial evidence only" if page.trialed and not page.tested else ""
        lines.append(
            f"- [{mark}] {title} — `{page.url}` ({len(page.snapshots)} state(s)){page_suffix}"
        )
        for (kind, label), status in sorted(page.controls.items()):
            control_mark = "x" if status == "exercised" else " "
            suffix = (
                " — write authorization required"
                if status == "write-gated"
                else " — trial evidence only"
                if status == "trial-exercised"
                else ""
            )
            lines.append(f"  - [{control_mark}] {kind}: {label}{suffix}")
    lines.extend(["", "## User journeys", ""])
    if not report.journeys:
        lines.append("- [ ] No candidate or approved user journeys")
    for journey in report.journeys:
        mark = "x" if journey.status == "passed" else " "
        lines.append(
            f"- [{mark}] {journey.title} (`{journey.scenario_id}`) — "
            f"{journey.authority}, {journey.status}, {journey.state_count} state(s)"
        )
    return "\n".join(lines) + "\n"


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
