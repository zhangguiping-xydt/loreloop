"""Acceptance report: a Markdown projection of run trace + evidence chain.

The report stores nothing of its own. It reads the delegation trace and the
verified evidence chain, and renders a matrix of checks with their chain
hashes so every claim in the report can be traced back to a signed record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..evidence.chain import EvidenceChain, EvidenceRecord

CHECK_EVENTS = {"check_passed", "check_failed"}


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    task: str
    context_entries: list[str]
    finished: bool


def load_run(trace_path: Path) -> RunSummary:
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    started = next(e for e in events if e["event"] == "delegation_started")
    finished = any(e["event"] == "delegation_finished" for e in events)
    return RunSummary(
        run_id=trace_path.stem,
        task=started["task"],
        context_entries=started.get("context_entries", []),
        finished=finished,
    )


def render_report(run: RunSummary, chain: EvidenceChain) -> str:
    records = chain.verify()
    checks = [r for r in records if r.event in CHECK_EVENTS and r.payload.get("run_id") == run.run_id]
    passed = [r for r in checks if r.event == "check_passed"]
    failed = [r for r in checks if r.event == "check_failed"]
    verdict = "ACCEPTED" if run.finished and checks and not failed else "NOT ACCEPTED"

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
            "| Check | Result | Evidence (chain hash) |",
            "|---|---|---|",
        ]
        for rec in checks:
            result = "PASS" if rec.event == "check_passed" else "FAIL"
            lines.append(f"| {rec.payload.get('check', '?')} | {result} | `{rec.chain_hash[:16]}` |")
        lines.append("")
        if failed:
            lines += ["### Failure details", ""]
            for rec in failed:
                detail = rec.payload.get("detail", "no detail recorded")
                lines.append(f"- **{rec.payload.get('check', '?')}**: {detail}")
            lines.append("")
    return "\n".join(lines)


def record_check(
    chain: EvidenceChain, run_id: str, check: str, passed: bool, detail: str | None = None
) -> EvidenceRecord:
    payload: dict = {"run_id": run_id, "check": check}
    if detail:
        payload["detail"] = detail
    return chain.append("check_passed" if passed else "check_failed", payload)
