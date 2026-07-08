"""knowhelm CLI: ingest / run / check / report / knowledge."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .agents import CODEX_RUNNER, AgentRunner
from .delegate.runner import DelegateRunner
from .evidence.chain import EvidenceChain
from .knowledge.code_reverse import reverse_code
from .knowledge.model import Curation
from .knowledge.store import KnowledgeStore
from .report.acceptance import load_run, record_check, render_report

DB_PATH = ".knowhelm/knowledge.db"


def _workdir() -> Path:
    return Path.cwd()


def _store(workdir: Path) -> KnowledgeStore:
    db = workdir / DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(db)


def _agent(name: str) -> AgentRunner:
    return CODEX_RUNNER if name == "codex" else AgentRunner()


def cmd_ingest(args: argparse.Namespace) -> int:
    workdir = _workdir()
    if args.source != "code":
        print(f"channel {args.source!r} is not implemented yet", file=sys.stderr)
        return 2
    entries = reverse_code(_agent(args.agent), Path(args.target).resolve())
    with _store(workdir) as store:
        for entry in entries:
            store.add(entry)
    print(f"ingested {len(entries)} knowledge entries from {args.target}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    workdir = _workdir()
    with _store(workdir) as store:
        entries = [e for e in store.list() if e.trust.curation is not Curation.REJECTED]
    runner = DelegateRunner(_agent(args.agent), workdir)
    result = runner.run(args.task, entries)
    chain = EvidenceChain.for_workdir(workdir)
    chain.append(
        "delegation_completed",
        {"run_id": result.run_id, "task": args.task, "context_entries": result.pack.entry_ids},
    )
    print(result.output)
    print(f"\n[knowhelm] run {result.run_id}: injected {len(result.pack.entry_ids)} entries, "
          f"trace at {result.trace_path}", file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    rec = record_check(chain, args.run_id, args.check, args.passed, args.detail)
    print(f"recorded {rec.event} for {args.run_id} (chain hash {rec.chain_hash[:16]})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    workdir = _workdir()
    runs_dir = workdir / ".knowhelm/runs"
    if args.run_id:
        trace = runs_dir / f"{args.run_id}.jsonl"
    else:
        traces = sorted(runs_dir.glob("run-*.jsonl")) if runs_dir.exists() else []
        if not traces:
            print("no runs found", file=sys.stderr)
            return 2
        trace = traces[-1]
    report = render_report(load_run(trace), EvidenceChain.for_workdir(workdir))
    print(report)
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    workdir = _workdir()
    with _store(workdir) as store:
        if args.action == "list":
            for e in store.list():
                strong = "strong" if e.is_strong_evidence() else "ref"
                print(f"{e.id[:8]}  [{e.kind.value:<12}] [{strong:<6}] {e.title}")
        elif args.action == "approve":
            entry = store.set_curation(args.entry_id, Curation.APPROVED, datetime.now(timezone.utc))
            print(f"approved: {entry.title}")
        elif args.action == "reject":
            entry = store.set_curation(args.entry_id, Curation.REJECTED, datetime.now(timezone.utc))
            print(f"rejected: {entry.title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowhelm")
    parser.add_argument("--agent", choices=["claude", "codex"], default="claude")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="reverse-engineer knowledge from a source")
    p_ingest.add_argument("--from", dest="source", choices=["code", "web"], required=True)
    p_ingest.add_argument("target")
    p_ingest.set_defaults(func=cmd_ingest)

    p_run = sub.add_parser("run", help="delegate a task with injected knowledge")
    p_run.add_argument("task")
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check", help="record an acceptance check for a run")
    p_check.add_argument("run_id")
    p_check.add_argument("check")
    result = p_check.add_mutually_exclusive_group(required=True)
    result.add_argument("--pass", dest="passed", action="store_true")
    result.add_argument("--fail", dest="passed", action="store_false")
    p_check.add_argument("--detail")
    p_check.set_defaults(func=cmd_check)

    p_report = sub.add_parser("report", help="render the acceptance report for a run")
    p_report.add_argument("run_id", nargs="?")
    p_report.set_defaults(func=cmd_report)

    p_knowledge = sub.add_parser("knowledge", help="inspect and curate knowledge entries")
    p_knowledge.add_argument("action", choices=["list", "approve", "reject"])
    p_knowledge.add_argument("entry_id", nargs="?")
    p_knowledge.set_defaults(func=cmd_knowledge)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
