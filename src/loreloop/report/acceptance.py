"""Acceptance report: a Markdown projection of the evidence chain.

The report stores nothing of its own. The verdict rests entirely on the
signed evidence chain: checks, artifact audits, and the chain-endorsed
``delegation_completed`` record that pins the run's task and base commit.
The run trace under ``.loreloop/runs/`` sits in the agent-writable tree, so
it is display material only — a forged ``delegation_finished`` line or an
edited ``base_commit`` there must never sway acceptance or harvest.
Checks count only when they postdate the completion record on the chain,
and a run id with more than one completion record is never accepted:
evidence must be attributable to exactly one finished delegation.
When an ArtifactStore is supplied, every artifact referenced on the chain is
re-hashed and cross-checked: a missing, tampered, swapped or unpinned
observation downgrades the verdict — a report must not claim acceptance
while its evidence material is gone or unaccounted for.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord
from ..evidence.repository_state import capture_repository_states
from ..security import redact_sensitive

CHECK_EVENTS = {"check_passed", "check_failed"}
DELEGATION_EVENT = "delegation_completed"


class RunTraceError(Exception):
    pass


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    task: str
    context_entries: list[str]
    finished: bool
    base_commits: dict[str, str] | None = None

    @property
    def base_commit(self) -> str | None:
        return (self.base_commits or {}).get(".")


@dataclass(frozen=True)
class RunEvaluation:
    checks: list[EvidenceRecord]
    passed: list[EvidenceRecord]
    failed: list[EvidenceRecord]
    broken_artifacts: list[tuple[str, str]]
    completions: list[EvidenceRecord]

    @property
    def completed(self) -> EvidenceRecord | None:
        """The FIRST completion record for the run. ``run`` or confirmed
        current-session ``complete`` appends exactly one; a later record with
        the same run_id cannot rewrite the task or base commit the first one
        pinned."""
        return self.completions[0] if self.completions else None

    @property
    def finished(self) -> bool:
        """Chain-endorsed completion. The trace's ``delegation_finished`` line
        is one file-append away from any process in the tree; only the signed
        ``delegation_completed`` record counts."""
        return self.completed is not None

    @property
    def base_commits(self) -> dict[str, str]:
        return _read_base_commits(
            self.completed.payload if self.completed else {}, "delegation_completed"
        )

    @property
    def base_commit(self) -> str | None:
        return self.base_commits.get(".")

    @property
    def accepted(self) -> bool:
        return (
            self.finished
            and len(self.completions) == 1
            and bool(self.checks)
            and not self.failed
            and not self.broken_artifacts
        )


def load_run(trace_path: Path) -> RunSummary:
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RunTraceError(f"cannot read run trace {trace_path}: {exc}") from exc

    events = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunTraceError(
                f"invalid run trace {trace_path}: line {line_no} is not JSON"
            ) from exc
        if not isinstance(event, dict):
            raise RunTraceError(f"invalid run trace {trace_path}: line {line_no} is not an object")
        events.append(event)

    started = next((e for e in events if e.get("event") == "delegation_started"), None)
    if started is None:
        raise RunTraceError(f"invalid run trace {trace_path}: missing delegation_started")
    task = started.get("task")
    if not isinstance(task, str):
        raise RunTraceError(f"invalid run trace {trace_path}: delegation_started.task missing")
    context_entries = started.get("context_entries", [])
    if not isinstance(context_entries, list):
        raise RunTraceError(
            f"invalid run trace {trace_path}: delegation_started.context_entries is not a list"
        )
    finished = any(e.get("event") == "delegation_finished" for e in events)
    return RunSummary(
        run_id=trace_path.stem,
        task=task,
        context_entries=context_entries,
        finished=finished,
        base_commits=_read_base_commits(started, "delegation_started"),
    )


def _read_base_commits(payload: dict, context: str) -> dict[str, str]:
    if "base_commits" in payload:
        value = payload["base_commits"]
        if not isinstance(value, dict) or any(
            not isinstance(name, str)
            or not _valid_repo_key(name)
            or not isinstance(commit, str)
            or not commit
            or commit != commit.strip()
            or any(char.isspace() for char in commit)
            for name, commit in value.items()
        ):
            raise RunTraceError(f"invalid {context}.base_commits")
        return dict(value)
    value = payload.get("base_commit")
    if value is None:
        return {}
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(char.isspace() for char in value)
    ):
        raise RunTraceError(f"invalid {context}.base_commit")
    return {".": value}


def _valid_repo_key(name: str) -> bool:
    if name == ".":
        return True
    from ..knowledge.repos import RepoConfigError, validate_repo_name

    try:
        validate_repo_name(name)
    except RepoConfigError:
        return False
    return True


def evaluate_run(
    run: RunSummary, chain: EvidenceChain, artifacts: ArtifactStore | None = None
) -> RunEvaluation:
    records = chain.verify()
    completions = [
        r for r in records if r.event == DELEGATION_EVENT and r.payload.get("run_id") == run.run_id
    ]
    # Acceptance checks must postdate the completion record on the chain.
    # Run ids appear in the trace while the agent is still working, so a
    # check recorded before completion could only be checking work that did
    # not exist yet — pre-planted "evidence" for a run still in flight.
    after = completions[0].index if completions else -1
    raw_checks = [
        r
        for r in records
        if r.event in CHECK_EVENTS and r.payload.get("run_id") == run.run_id and r.index > after
    ]
    latest: dict[str, EvidenceRecord] = {}
    for record in raw_checks:
        latest[_check_identity(record)] = record
    checks = sorted(latest.values(), key=lambda record: record.index)
    passed = [r for r in checks if r.event == "check_passed"]
    failed = [r for r in checks if r.event == "check_failed"]
    return RunEvaluation(
        checks=checks,
        passed=passed,
        failed=failed,
        broken_artifacts=_audit_artifacts(checks, artifacts),
        completions=completions,
    )


def render_report(
    run: RunSummary,
    chain: EvidenceChain,
    artifacts: ArtifactStore | None = None,
    workdir: Path | None = None,
) -> str:
    records = chain.verify()
    evaluation = evaluate_run(run, chain, artifacts)
    checks = evaluation.checks
    passed = evaluation.passed
    failed = evaluation.failed
    broken_artifacts = evaluation.broken_artifacts
    verdict = "ACCEPTED" if evaluation.accepted else "NOT ACCEPTED"

    # Run metadata comes from the chain record when it exists; the trace is a
    # fallback for display only and cannot make the verdict look better.
    completed = evaluation.completed
    task = completed.payload.get("task", run.task) if completed else run.task
    context = (
        completed.payload.get("context_entries", run.context_entries)
        if completed
        else run.context_entries
    )
    lines = [
        f"# Acceptance report — {run.run_id}",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Task: {task}",
        f"- Delegation completed (chain-endorsed): {'yes' if evaluation.finished else 'no'}",
        f"- Knowledge entries injected: {len(context)}",
        f"- Evidence chain: verified, {len(records)} records intact",
        "",
        f"## Verdict: {verdict}",
        "",
    ]
    if not evaluation.finished:
        lines += [
            "No chain-endorsed `delegation_completed` record exists for this run;",
            "the trace file alone is not acceptance evidence.",
            "",
        ]
    elif len(evaluation.completions) > 1:
        lines += [
            f"{len(evaluation.completions)} `delegation_completed` records exist "
            "for this run id; the evidence cannot be attributed to a single "
            "delegation, so the run is not acceptable as recorded.",
            "",
        ]
    if artifacts is not None and workdir is not None:
        lines.extend(_task_workflow_sections(run.run_id, records, artifacts, workdir))
    if not checks:
        lines += ["No acceptance checks were recorded for this run.", ""]
    else:
        lines += [
            f"## Checks ({len(passed)} passed / {len(failed)} failed)",
            "",
            "| Check | Result | Judge | Chain hash | Artifact |",
            "|---|---|---|---|---|",
        ]
        for rec in checks:
            result = "PASS" if rec.event == "check_passed" else "FAIL"
            judge = rec.payload.get("judge", "-")
            sha = rec.payload.get("artifact")
            artifact = f"`{sha[:16]}`" if sha else "none (operator vouched)"
            lines.append(
                f"| {_md(rec.payload.get('check', '?'))} | {result} | {_md(str(judge))} "
                f"| `{rec.chain_hash[:16]}` | {artifact} |"
            )
        lines.append("")
        vouched = [r for r in passed if not r.payload.get("artifact")]
        if vouched:
            lines += [
                f"Note: {len(vouched)} passed check(s) carry no evidence artifact — "
                "they rest on the operator's word alone and cannot be re-audited.",
                "",
            ]
        if broken_artifacts:
            lines += ["### Evidence integrity failures", ""]
            for sha, problem in broken_artifacts:
                lines.append(f"- artifact `{sha[:16]}`: {_md(problem)}")
            lines.append("")
        if failed:
            lines += ["### Failure details", ""]
            for rec in failed:
                detail = rec.payload.get("detail", "no detail recorded")
                lines.append(f"- **{_md(rec.payload.get('check', '?'))}**: {_md(detail)}")
            lines.append("")
    return "\n".join(lines)


def _task_workflow_sections(
    run_id: str,
    records: list[EvidenceRecord],
    artifacts: ArtifactStore,
    workdir: Path,
) -> list[str]:
    from ..webexplore.coverage import build_web_coverage
    from ..webexplore.scenarios import WEB_TEST_TRIALED_EVENT
    from ..workflow.execution import TASK_TEST_EXECUTED_EVENT
    from ..workflow.impact import latest_task_test_plan
    from ..workflow.summary import latest_task_narrative

    lines: list[str] = []
    narrative = latest_task_narrative(run_id, records)
    if narrative is not None:
        lines.extend(
            [
                "## Root cause or requirement analysis",
                "",
                narrative.analysis,
                "",
                "## Implementation summary",
                "",
                narrative.implementation,
                "",
            ]
        )
        if narrative.acceptance:
            lines.extend(["## Acceptance criteria", ""])
            lines.extend(f"- {item}" for item in narrative.acceptance)
            lines.append("")
        if narrative.risks:
            lines.extend(["## Known risks and limitations", ""])
            lines.extend(f"- {item}" for item in narrative.risks)
            lines.append("")
        lines.extend(
            [
                "The sections above are host-agent-authored narrative; test, browser, "
                "Git, and chain evidence below determine verification.",
                "",
            ]
        )
    plan = latest_task_test_plan(run_id, records, artifacts)
    if plan is not None:
        lines.extend(
            [
                "## Task understanding and change impact",
                "",
                f"- Task type: {plan.intent.kind}",
                f"- Source changes since task start: {len(plan.changes)}",
            ]
        )
        for change in plan.changes:
            lines.append(f"  - {change.kind}: `{change.repository}:{change.path}`")
        lines.extend(["", "## Selected tests and rationale", ""])
        for tier, title in (
            ("must", "Must run"),
            ("recommended", "Recommended"),
            ("missing", "Coverage gap"),
        ):
            selected = [item for item in plan.selections if item.tier == tier]
            lines.append(f"### {title} ({len(selected)})")
            lines.append("")
            if not selected:
                lines.append("- None")
            for item in selected:
                location = f"{item.repository}:{item.path}" if item.path else item.repository
                lines.append(f"- **{_md(item.name)}** (`{location}`): {_md(item.reason)}")
            lines.append("")
        if plan.commands:
            import shlex

            lines.extend(["### Suggested deterministic commands", ""])
            for command in plan.commands:
                lines.append(f"- `{_md(shlex.join(command.argv))}`")
            lines.append("")

    executions = [
        record
        for record in records
        if record.event == TASK_TEST_EXECUTED_EVENT and record.payload.get("run_id") == run_id
    ]
    if executions:
        latest_executions: dict[tuple[str, ...], EvidenceRecord] = {}
        for record in executions:
            command = record.payload.get("command")
            if isinstance(command, list) and all(isinstance(item, str) for item in command):
                latest_executions[tuple(command)] = record
        lines.extend(["## Provisional automated test execution", ""])
        for command, record in latest_executions.items():
            import shlex

            lines.append(
                f"- {record.payload.get('status', 'unknown')}: "
                f"`{_md(shlex.join(command))}` — provisional evidence; "
                "rerun after completion for acceptance authority"
            )
        lines.append("")

    trials = [
        record
        for record in records
        if record.event == WEB_TEST_TRIALED_EVENT and record.payload.get("run_id") == run_id
    ]
    if trials:
        latest_trials: dict[str, EvidenceRecord] = {}
        for record in trials:
            scenario_id = record.payload.get("scenario_id")
            if isinstance(scenario_id, str):
                latest_trials[scenario_id] = record
        lines.extend(["## Provisional Web trials", ""])
        for scenario_id, record in sorted(latest_trials.items()):
            lines.append(
                f"- `{scenario_id}`: {record.payload.get('status', 'unknown')} — "
                "non-authoritative; approval and governed replay are still required"
            )
        lines.append("")

    try:
        coverage = build_web_coverage(workdir, records, artifacts)
    except (OSError, ValueError) as exc:
        lines.extend(["## Web coverage", "", f"Coverage unavailable: {_md(str(exc))}", ""])
    else:
        summary = coverage.summary
        if summary["pages_observed"] or summary["journeys_candidate"] or summary["journeys_approved"]:
            lines.extend(
                [
                    "## Web coverage",
                    "",
                    f"- Pages tested: {summary['pages_tested']} / {summary['pages_observed']}",
                    f"- Pages with trial-only evidence: {summary['pages_trialed']}",
                    f"- Page states observed: {summary['states_observed']}",
                    f"- Controls exercised: {summary['controls_exercised']} / "
                    f"{summary['controls_observed']}",
                    f"- Controls exercised only in trial: "
                    f"{summary['controls_trial_exercised']}",
                    f"- Write-gated controls: {summary['controls_write_gated']}",
                    "",
                ]
            )
    return lines


def _md(text: str) -> str:
    """Neutralize characters that would break out of a Markdown table cell.
    Check text and details are operator input, but they can also echo page
    content — a `|` or newline must not let them forge extra columns/rows."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _audit_artifacts(
    checks: list[EvidenceRecord], artifacts: ArtifactStore | None
) -> list[tuple[str, str]]:
    """Load every referenced artifact AND cross-check it against the chain
    payload. Hash integrity alone would accept a swap: replacing the artifact
    reference with a different (valid) observation of a different page. The
    url and page snapshot recorded on the signed chain pin which observation
    the verdict was actually about — a check that carries an artifact but no
    such pin cannot prove the artifact is about ITS page, so the missing pin
    is itself an integrity failure (operator checks carry no artifact and are
    untouched by this rule)."""
    if artifacts is None:
        return []
    broken = []
    for rec in checks:
        payload = rec.payload
        sha = payload.get("artifact")
        if not sha:
            data = None
        else:
            data = _load_artifact(artifacts, sha, broken, "observation artifact")
        artifact_type = data.get("type") if data is not None else None
        if artifact_type == "page_observation":
            chain_url = payload.get("url")
            chain_snap = payload.get("page_snapshot")
            if chain_url is None or chain_snap is None:
                broken.append(
                    (
                        sha,
                        "chain record carries an artifact but no url/page_snapshot "
                        "pin — the artifact cannot be tied to the verdict",
                    )
                )
            else:
                if data.get("url") != chain_url:
                    broken.append(
                        (
                            sha,
                            f"artifact url {data.get('url')!r} does not match "
                            f"the chain record ({chain_url!r})",
                        )
                    )
                if data.get("snapshot_hash") != chain_snap:
                    broken.append((sha, "artifact snapshot hash does not match the chain record"))
        elif artifact_type == "command_evidence":
            if payload.get("verified_via") != "command":
                broken.append((sha, "command artifact is not pinned as command verification"))
            if data.get("argv") != payload.get("command"):
                broken.append((sha, "command artifact argv does not match the chain record"))
            if data.get("exit_code") != payload.get("exit_code"):
                broken.append((sha, "command artifact exit code does not match the chain record"))
            if payload.get("exit_code") == 0 and data.get("timed_out"):
                broken.append((sha, "successful command check is marked timed out"))
        elif data is not None:
            broken.append((sha, f"unsupported evidence artifact type {artifact_type!r}"))

        script_digest = payload.get("script_digest")
        if script_digest:
            if not sha:
                broken.append(
                    (
                        rec.chain_hash,
                        "script check carries no final observation artifact",
                    )
                )
            _audit_script_artifact(rec, artifacts, broken)
            _audit_trace_artifact(rec, artifacts, broken)
        elif payload.get("script_artifact") or payload.get("trace_artifact"):
            broken.append(
                (
                    rec.chain_hash,
                    "chain record carries script/trace artifacts but no script_digest pin",
                )
            )
    return broken


def _load_artifact(
    artifacts: ArtifactStore, sha: str, broken: list[tuple[str, str]], label: str
) -> dict | None:
    try:
        return artifacts.load(sha)
    except FileNotFoundError:
        broken.append((sha, f"{label} referenced on the chain but the file is missing"))
    except ValueError as exc:
        broken.append((sha, f"{label}: {exc}"))
    return None


def _audit_script_artifact(
    rec: EvidenceRecord, artifacts: ArtifactStore, broken: list[tuple[str, str]]
) -> None:
    from ..webexplore.actions import ActionScriptError, parse_action_script

    script_digest = rec.payload.get("script_digest")
    sha = rec.payload.get("script_artifact")
    if not sha:
        broken.append((rec.chain_hash, "chain record carries script_digest but no script_artifact"))
        return
    data = _load_artifact(artifacts, sha, broken, "script artifact")
    if data is None:
        return
    if data.get("type") != "interaction_script":
        broken.append((sha, "script artifact has the wrong type"))
    if data.get("script_digest") != script_digest:
        broken.append((sha, "script artifact digest does not match the chain record"))
        return
    try:
        script = parse_action_script(data.get("script"))
    except ActionScriptError as exc:
        broken.append((sha, f"script artifact contains an invalid script: {exc}"))
        return
    if script.digest != script_digest:
        broken.append((sha, "script artifact canonical digest does not match the chain record"))


def _audit_trace_artifact(
    rec: EvidenceRecord, artifacts: ArtifactStore, broken: list[tuple[str, str]]
) -> None:
    script_digest = rec.payload.get("script_digest")
    sha = rec.payload.get("trace_artifact")
    if not sha:
        broken.append((rec.chain_hash, "chain record carries script_digest but no trace_artifact"))
        return
    data = _load_artifact(artifacts, sha, broken, "trace artifact")
    if data is None:
        return
    if data.get("type") != "interaction_trace":
        broken.append((sha, "trace artifact has the wrong type"))
    if data.get("script_digest") != script_digest:
        broken.append((sha, "trace artifact digest does not match the chain record"))
    final_snapshot = data.get("final_snapshot")
    chain_snapshot = rec.payload.get("page_snapshot")
    if final_snapshot and chain_snapshot and final_snapshot != chain_snapshot:
        broken.append((sha, "trace final snapshot does not match the chain record"))


def record_check(
    chain: EvidenceChain, run_id: str, check: str, passed: bool, detail: str | None = None
) -> EvidenceRecord:
    """A manual check is the operator vouching personally — legitimate (a
    human eyeballing the app is real acceptance) but carrying no machine
    evidence. It is labeled ``judge: operator`` so reports and harvest can
    tell it apart from browser-verified checks; harvest never mints from it."""
    check = validate_check_text(check)
    payload: dict = {"run_id": run_id, "check": check, "judge": "operator"}
    if detail:
        payload["detail"] = detail
    return chain.append("check_passed" if passed else "check_failed", payload)


def record_command_check(
    chain: EvidenceChain,
    artifacts: ArtifactStore,
    run_id: str,
    check: str,
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 300.0,
    state_workdir: Path | None = None,
) -> EvidenceRecord:
    """Execute an operator-specified command without a shell and pin its output.

    This gives tests, linters, CLI probes and API test clients the same
    re-auditable evidence path as browser checks. Only exit code zero passes.
    """
    if not argv or any(not isinstance(part, str) or not part for part in argv):
        raise ValueError("command check requires a non-empty argv")
    check = validate_check_text(check)
    if timeout <= 0:
        raise ValueError("command check timeout must be positive")
    started = datetime.now(timezone.utc)
    timed_out = False
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
    finished = datetime.now(timezone.utc)
    stdout = redact_sensitive(stdout)
    stderr = redact_sensitive(stderr)
    repository_states = capture_repository_states(state_workdir or cwd)
    artifact, _ = artifacts.save_json(
        {
            "type": "command_evidence",
            "argv": argv,
            "cwd": str(cwd.resolve()),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": stdout[-100_000:],
            "stderr": stderr[-100_000:],
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "repository_states": repository_states,
        }
    )
    payload = {
        "run_id": run_id,
        "check": check,
        "judge": "command",
        "verified_via": "command",
        "artifact": artifact,
        "command": argv,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "repository_states": repository_states,
    }
    if stderr:
        payload["detail"] = stderr[-500:]
    passed = exit_code == 0 and not timed_out
    return chain.append("check_passed" if passed else "check_failed", payload)


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def validate_check_text(check: str) -> str:
    if not isinstance(check, str) or not check.strip():
        raise ValueError("acceptance check must be non-empty")
    if len(check) > 4_000:
        raise ValueError("acceptance check must be at most 4000 characters")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in check):
        raise ValueError("acceptance check contains control characters")
    return check.strip()


def _check_identity(record: EvidenceRecord) -> str:
    payload = record.payload
    explicit = payload.get("check_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    material = {
        "judge": payload.get("judge"),
        "check": payload.get("check"),
        "url": payload.get("url"),
        "script_digest": payload.get("script_digest"),
        "command": payload.get("command"),
    }
    return json.dumps(material, ensure_ascii=False, sort_keys=True)
