"""Acceptance report: a Markdown projection of run trace + evidence chain.

The report stores nothing of its own. It reads the delegation trace and the
verified evidence chain, and renders a matrix of checks with their chain
hashes so every claim in the report can be traced back to a signed record.
When an ArtifactStore is supplied, every artifact referenced on the chain is
re-hashed: a missing or tampered observation downgrades the verdict — a
report must not claim acceptance while its evidence material is gone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..evidence.artifacts import ArtifactStore
from ..evidence.chain import EvidenceChain, EvidenceRecord

CHECK_EVENTS = {"check_passed", "check_failed"}


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
    finished: bool

    @property
    def accepted(self) -> bool:
        return (
            self.finished
            and bool(self.checks)
            and not self.failed
            and not self.broken_artifacts
        )


def load_run(trace_path: Path) -> RunSummary:
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    started = next(e for e in events if e["event"] == "delegation_started")
    finished = any(e["event"] == "delegation_finished" for e in events)
    return RunSummary(
        run_id=trace_path.stem,
        task=started["task"],
        context_entries=started.get("context_entries", []),
        finished=finished,
        base_commit=started.get("base_commit"),
    )


def evaluate_run(
    run: RunSummary, chain: EvidenceChain, artifacts: ArtifactStore | None = None
) -> RunEvaluation:
    records = chain.verify()
    checks = [r for r in records if r.event in CHECK_EVENTS and r.payload.get("run_id") == run.run_id]
    passed = [r for r in checks if r.event == "check_passed"]
    failed = [r for r in checks if r.event == "check_failed"]
    return RunEvaluation(
        checks=checks,
        passed=passed,
        failed=failed,
        broken_artifacts=_audit_artifacts(checks, artifacts),
        finished=run.finished,
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

    lines = [
        f"# Acceptance report — {run.run_id}",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Task: {run.task}",
        f"- Delegation finished: {'yes' if run.finished else 'no'}",
        f"- Knowledge entries injected: {len(run.context_entries)}",
        f"- Evidence chain: verified, {len(records)} records intact",
        "",
        f"## Verdict: {verdict}",
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
    the verdict was actually about."""
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
        if chain_url is not None and data.get("url") != chain_url:
            broken.append((sha, f"artifact url {data.get('url')!r} does not match "
                                f"the chain record ({chain_url!r})"))
        chain_snap = rec.payload.get("page_snapshot")
        if chain_snap is not None and data.get("snapshot_hash") != chain_snap:
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
