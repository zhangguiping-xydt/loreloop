"""Acceptance report: a Markdown projection of the evidence chain.

The report stores nothing of its own. The verdict rests entirely on the
signed evidence chain: checks, artifact audits, and the chain-endorsed
``delegation_completed`` record that pins the run's task and base commit.
The run trace under ``.knowhelm/runs/`` sits in the agent-writable tree, so
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord

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
    base_commit: str | None = None


@dataclass(frozen=True)
class RunEvaluation:
    checks: list[EvidenceRecord]
    passed: list[EvidenceRecord]
    failed: list[EvidenceRecord]
    broken_artifacts: list[tuple[str, str]]
    completions: list[EvidenceRecord]

    @property
    def completed(self) -> EvidenceRecord | None:
        """The FIRST completion record for the run. cmd_run appends exactly
        one; a later record with the same run_id cannot rewrite the task or
        base commit the first one pinned."""
        return self.completions[0] if self.completions else None

    @property
    def finished(self) -> bool:
        """Chain-endorsed completion. The trace's ``delegation_finished`` line
        is one file-append away from any process in the tree; only the signed
        ``delegation_completed`` record counts."""
        return self.completed is not None

    @property
    def base_commit(self) -> str | None:
        return self.completed.payload.get("base_commit") if self.completed else None

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
            raise RunTraceError(
                f"invalid run trace {trace_path}: line {line_no} is not an object"
            )
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
        base_commit=started.get("base_commit"),
    )


def evaluate_run(
    run: RunSummary, chain: EvidenceChain, artifacts: ArtifactStore | None = None
) -> RunEvaluation:
    records = chain.verify()
    completions = [
        r
        for r in records
        if r.event == DELEGATION_EVENT and r.payload.get("run_id") == run.run_id
    ]
    # Acceptance checks must postdate the completion record on the chain.
    # Run ids appear in the trace while the agent is still working, so a
    # check recorded before completion could only be checking work that did
    # not exist yet — pre-planted "evidence" for a run still in flight.
    after = completions[0].index if completions else -1
    checks = [
        r
        for r in records
        if r.event in CHECK_EVENTS
        and r.payload.get("run_id") == run.run_id
        and r.index > after
    ]
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
    run: RunSummary, chain: EvidenceChain, artifacts: ArtifactStore | None = None
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
        sha = rec.payload.get("artifact")
        if not sha:
            continue
        try:
            data = artifacts.load(sha)
        except FileNotFoundError:
            broken.append((sha, "referenced on the chain but the file is missing"))
            continue
        except ValueError as exc:
            broken.append((sha, str(exc)))
            continue
        chain_url = rec.payload.get("url")
        chain_snap = rec.payload.get("page_snapshot")
        if chain_url is None or chain_snap is None:
            broken.append((sha, "chain record carries an artifact but no url/page_snapshot "
                                "pin — the artifact cannot be tied to the verdict"))
            continue
        if data.get("url") != chain_url:
            broken.append((sha, f"artifact url {data.get('url')!r} does not match "
                                f"the chain record ({chain_url!r})"))
        if data.get("snapshot_hash") != chain_snap:
            broken.append((sha, "artifact snapshot hash does not match the chain record"))
    return broken


def record_check(
    chain: EvidenceChain, run_id: str, check: str, passed: bool, detail: str | None = None
) -> EvidenceRecord:
    """A manual check is the operator vouching personally — legitimate (a
    human eyeballing the app is real acceptance) but carrying no machine
    evidence. It is labeled ``judge: operator`` so reports and harvest can
    tell it apart from browser-verified checks; harvest never mints from it."""
    payload: dict = {"run_id": run_id, "check": check, "judge": "operator"}
    if detail:
        payload["detail"] = detail
    return chain.append("check_passed" if passed else "check_failed", payload)
