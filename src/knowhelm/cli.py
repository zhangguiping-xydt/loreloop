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
    if args.source == "code":
        entries = reverse_code(_agent(args.agent), Path(args.target).resolve())
    else:
        from .webexplore.browser import PlaywrightBrowser
        from .webexplore.explorer import Explorer
        from .webexplore.web_reverse import reverse_web

        browser = PlaywrightBrowser(headed=args.headed)
        on_login_wall = "handover" if args.headed else "skip"
        try:
            explorer = Explorer(
                browser, workdir, max_pages=args.max_pages, on_login_wall=on_login_wall
            )
            result = explorer.explore(args.target)
            print(f"explored {len(result.pages)} pages "
                  f"({len(result.skipped)} skipped), trace at {result.trace_path}",
                  file=sys.stderr)
            if result.login_walls and not args.headed:
                print(f"skipped {len(result.login_walls)} login-walled page(s); "
                      f"re-run with --headed to sign in yourself", file=sys.stderr)
            entries = reverse_web(_agent(args.agent), result.pages)
        finally:
            browser.close()
    with _store(workdir) as store:
        for entry in entries:
            store.add(entry)
    print(f"ingested {len(entries)} knowledge entries from {args.target}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .evidence.artifacts import ArtifactStore
    from .webexplore.browser import PlaywrightBrowser
    from .webexplore.verify import MalformedExpectation, parse_assertion, verify_expectation

    try:
        parse_assertion(args.expectation)
    except MalformedExpectation as exc:
        print(f"invalid expectation: {exc}", file=sys.stderr)
        return 2

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    browser = PlaywrightBrowser(headed=args.headed)
    try:
        result = verify_expectation(
            browser, _agent(args.agent), chain, args.run_id, args.url, args.expectation,
            artifacts=artifacts,
        )
    finally:
        browser.close()
    status = "PASS" if result.passed else "FAIL"
    print(f"{status}: {result.reason}")
    print(f"evidence: chain hash {result.record.chain_hash[:16]}, "
          f"page snapshot {result.snapshot[:16]}")
    return 0 if result.passed else 1


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
    from .evidence.artifacts import ArtifactStore

    report = render_report(
        load_run(trace), EvidenceChain.for_workdir(workdir),
        artifacts=ArtifactStore.for_workdir(workdir),
    )
    print(report)
    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    from .evidence.artifacts import ArtifactStore
    from .knowledge.harvest import HarvestError, harvest_run

    workdir = _workdir()
    trace = workdir / f".knowhelm/runs/{args.run_id}.jsonl"
    if not trace.exists():
        print(f"no trace found for {args.run_id}", file=sys.stderr)
        return 2
    run = load_run(trace)
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    with _store(workdir) as store:
        try:
            result = harvest_run(
                run, chain, store, _agent(args.agent), workdir, artifacts=artifacts
            )
        except HarvestError as exc:
            print(f"harvest refused: {exc}", file=sys.stderr)
            return 1
    print(f"harvested run {args.run_id}:")
    print(f"  {len(result.minted)} verified acceptance assertions minted")
    print(f"  {len(result.reversed_entries)} draft entries reversed from changed code")
    if result.stale:
        print(f"  {len(result.stale)} existing entries anchored before this run "
              f"touch changed files — review with `knowhelm knowledge list`:")
        for entry in result.stale:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})")
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
        elif args.action == "verify":
            return _verify_entries(args, workdir, store)
    return 0


def _verify_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .evidence.artifacts import ArtifactStore
    from .knowledge.model import Channel
    from .webexplore.browser import PlaywrightBrowser
    from .webexplore.verify import verify_entry

    web_entries = store.list(channel=Channel.WEB)
    if args.entry_id:
        web_entries = [e for e in web_entries if e.id.startswith(args.entry_id)]
    if not web_entries:
        print("no matching web-channel entries to verify", file=sys.stderr)
        return 2

    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    run_id = f"verify-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    agent = _agent(args.agent)
    browser = PlaywrightBrowser(headed=args.headed)
    contradicted = 0
    try:
        for entry in web_entries:
            result = verify_entry(
                browser, agent, chain, store, entry, run_id, artifacts=artifacts
            )
            status = "VERIFIED" if result.passed else "CONTRADICTED"
            drift = "  [page drifted since ingest]" if result.drifted else ""
            print(f"{status}: {entry.title}{drift}")
            print(f"  {result.reason}")
            if not result.passed:
                contradicted += 1
    finally:
        browser.close()
    print(f"\n{len(web_entries) - contradicted} verified, {contradicted} contradicted "
          f"(evidence run {run_id})")
    return 0 if contradicted == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowhelm")
    parser.add_argument("--agent", choices=["claude", "codex"], default="claude")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="reverse-engineer knowledge from a source")
    p_ingest.add_argument("--from", dest="source", choices=["code", "web"], required=True)
    p_ingest.add_argument("target")
    p_ingest.add_argument("--max-pages", type=int, default=20)
    p_ingest.add_argument("--headed", action="store_true",
                          help="show the browser window (needed for login handover)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_verify = sub.add_parser("verify", help="verify an expectation against a live page")
    p_verify.add_argument("run_id")
    p_verify.add_argument("url")
    p_verify.add_argument("expectation")
    p_verify.add_argument("--headed", action="store_true")
    p_verify.set_defaults(func=cmd_verify)

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

    p_harvest = sub.add_parser(
        "harvest", help="flow knowledge back from an accepted run"
    )
    p_harvest.add_argument("run_id")
    p_harvest.set_defaults(func=cmd_harvest)

    p_knowledge = sub.add_parser("knowledge", help="inspect, curate and verify knowledge entries")
    p_knowledge.add_argument("action", choices=["list", "approve", "reject", "verify"])
    p_knowledge.add_argument("entry_id", nargs="?")
    p_knowledge.add_argument("--headed", action="store_true")
    p_knowledge.set_defaults(func=cmd_knowledge)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
