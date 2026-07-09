"""knowhelm CLI: ingest / run / check / report / knowledge."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .agents import CODEX_RUNNER, AgentRunner
from .delegate.runner import DelegateRunner
from .evidence.chain import EvidenceChain
from .knowledge.code_reverse import reverse_code
from .knowledge.endorsement import (
    SUPERSEDE_EVENT,
    chain_superseded_ids,
    curate,
    unendorsed_strong_ids,
)
from .knowledge.model import Curation
from .knowledge.store import KnowledgeStore
from .report.acceptance import load_run, record_check, render_report

DB_PATH = ".knowhelm/knowledge.db"

# run ids are used to build filesystem paths; a strict shape rules out
# traversal like "../../etc/passwd" without any path canonicalization games.
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def _run_trace(workdir: Path, run_id: str) -> Path | None:
    if not _RUN_ID.match(run_id):
        print(f"invalid run id: {run_id!r}", file=sys.stderr)
        return None
    return workdir / f".knowhelm/runs/{run_id}.jsonl"


def _workdir() -> Path:
    return Path.cwd()


def _store(workdir: Path) -> KnowledgeStore:
    db = workdir / DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(db)


def _agent(name: str) -> AgentRunner:
    return CODEX_RUNNER if name == "codex" else AgentRunner()


def cmd_init(args: argparse.Namespace) -> int:
    import shutil

    from .evidence.chain import key_path_for

    workdir = _workdir()
    _store(workdir).close()
    EvidenceChain.for_workdir(workdir)
    print(f"initialized .knowhelm/ (knowledge store, evidence chain) in {workdir}")
    print(f"evidence signing key: {key_path_for(workdir)} (outside the project tree)")

    gitignore = workdir / ".gitignore"
    if (workdir / ".git").exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
        if ".knowhelm/" not in lines:
            with gitignore.open("a", encoding="utf-8") as fh:
                if lines and lines[-1].strip():
                    fh.write("\n")
                fh.write(".knowhelm/\n")
            print("added .knowhelm/ to .gitignore (evidence may embed page content)")

    hosts = [name for name in ("claude", "codex") if shutil.which(name)]
    if not hosts:
        print("no coding agent (claude/codex) found on PATH; skill installation skipped")
        return 0
    print(f"detected coding agent(s): {', '.join(hosts)}")

    if "claude" in hosts:
        if args.skill is None:
            answer = input("install the knowhelm companion skill for Claude Code? [Y/n] ")
            wanted = answer.strip().lower() in ("", "y", "yes")
        else:
            wanted = args.skill
        if wanted:
            from .companion import install_claude_skill

            path = install_claude_skill(workdir)
            print(f"installed companion skill: {path.relative_to(workdir)}")
        else:
            print("skipped skill installation (re-run `knowhelm init --skill` to install)")
    if "codex" in hosts:
        print("codex companion skill is not available yet; the CLI works with codex today")
    return 0


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
    from .knowledge.model import Channel

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    with _store(workdir) as store:
        entries = store.list_active()
    # Supersession by chain replay, not DB links: deleting a row in the links
    # table (inside the agent-writable tree) must not resurrect a retired
    # entry. list_active() already applies DB links as a cache; the chain
    # filter here is the authoritative pass.
    superseded = chain_superseded_ids(records)
    entries = [e for e in entries if e.id not in superseded]
    # The DB sits in the agent-writable tree; its strong bits count only when
    # the chain endorses them FOR THE CURRENT CONTENT. Anything strong-in-DB
    # but unendorsed (no event, or content changed since endorsement) is
    # injected as reference and flagged for the operator.
    unendorsed = unendorsed_strong_ids(entries, records)
    if unendorsed:
        print(f"[knowhelm] WARNING: {len(unendorsed)} entr{'y' if len(unendorsed) == 1 else 'ies'} "
              f"claim strong trust in the store without evidence-chain endorsement "
              f"of their current content — injected as reference only. "
              f"Inspect with `knowhelm knowledge list`:",
              file=sys.stderr)
        for e in entries:
            if e.id in unendorsed:
                print(f"    {e.id[:8]}  {e.title}", file=sys.stderr)
    runner = DelegateRunner(_agent(args.agent), workdir)
    result = runner.run(args.task, entries, unendorsed_ids=unendorsed)
    # This chain record is the acceptance authority for the run: report and
    # harvest key off it, not off the agent-writable trace file.
    chain.append(
        "delegation_completed",
        {
            "run_id": result.run_id,
            "task": args.task,
            "context_entries": result.pack.entry_ids,
            "base_commit": result.base_commit,
        },
    )
    print(result.output)
    print(f"\n[knowhelm] run {result.run_id}: injected {len(result.pack.entry_ids)} entries, "
          f"trace at {result.trace_path}", file=sys.stderr)
    strong_web = [e for e in result.pack.strong if e.source.channel is Channel.WEB]
    if strong_web:
        # Known limitation, documented in SECURITY.md: injection trusts the
        # last verification; it does not re-open a browser per run.
        print(f"[knowhelm] note: {len(strong_web)} strong web entr"
              f"{'y was' if len(strong_web) == 1 else 'ies were'} injected as-is; "
              f"live pages may have changed since verification — "
              f"re-check with `knowhelm knowledge verify`", file=sys.stderr)
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
        trace = _run_trace(workdir, args.run_id)
        if trace is None:
            return 2
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
    trace = _run_trace(workdir, args.run_id)
    if trace is None:
        return 2
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
    if result.unauditable_checks:
        print(f"  {len(result.unauditable_checks)} browser check(s) had no evidence "
              f"artifact and were NOT minted:", file=sys.stderr)
        for check in result.unauditable_checks:
            print(f"    {check}", file=sys.stderr)
    if result.stale:
        print(f"  {len(result.stale)} existing entries anchored before this run "
              f"touch changed files — review with `knowhelm knowledge list --stale`:")
        for entry in result.stale:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})")
    if result.review:
        print(f"  {len(result.review)} existing strong entries cover pages verified in "
              f"this run — check they still hold, supersede if not:")
        for entry in result.review:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})")
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    workdir = _workdir()
    with _store(workdir) as store:
        if args.action == "list":
            return _list_entries(args, workdir, store)
        elif args.action in ("approve", "reject"):
            return _curate(args, workdir, store)
        elif args.action == "supersede":
            return _supersede(args, workdir, store)
        elif args.action == "verify":
            return _verify_entries(args, workdir, store)
    return 0


def _curate(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    if not args.entry_id:
        print(f"usage: knowhelm knowledge {args.action} <entry_id>", file=sys.stderr)
        return 2
    target = _resolve_entry(store, args.entry_id)
    if target is None:
        return 2
    from .knowledge.store import InvalidTransition

    new = Curation.APPROVED if args.action == "approve" else Curation.REJECTED
    chain = EvidenceChain.for_workdir(workdir)
    try:
        entry = curate(store, chain, target.id, new, datetime.now(timezone.utc))
    except InvalidTransition as exc:
        print(f"invalid curation transition: {exc}", file=sys.stderr)
        return 2
    print(f"{args.action}{'d' if args.action.endswith('e') else 'ed'}: {entry.title}")
    return 0


def _list_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .knowledge.code_reverse import drifted_code_entry_ids

    entries = store.list()
    # Chain replay, not DB links: a deleted links row must not un-supersede
    # an entry in any view that informs decisions.
    superseded = chain_superseded_ids(EvidenceChain.for_workdir(workdir).verify())
    drifted = drifted_code_entry_ids(workdir, entries) if (workdir / ".git").exists() else set()
    if args.stale:
        entries = [e for e in entries if e.id in drifted and e.id not in superseded]
        if not entries:
            print("no stale entries: every code anchor matches the current tree")
            return 0
    for e in entries:
        strong = "strong" if e.is_strong_evidence() else "ref"
        flags = ""
        if e.id in superseded:
            flags += "  [superseded]"
        if e.id in drifted:
            flags += "  [stale: source changed since capture]"
        print(f"{e.id[:8]}  [{e.kind.value:<12}] [{strong:<6}] {e.title}{flags}")
    return 0


def _supersede(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .knowledge.model import Link, LinkType

    if not args.entry_id or not args.old_id:
        print("usage: knowhelm knowledge supersede <new_id> <old_id>", file=sys.stderr)
        return 2
    new = _resolve_entry(store, args.entry_id)
    old = _resolve_entry(store, args.old_id)
    if new is None or old is None:
        return 2
    # Supersession silences an entry at injection time — a trust-affecting
    # act, so it is endorsed on the chain like curation. Chain first.
    EvidenceChain.for_workdir(workdir).append(
        SUPERSEDE_EVENT, {"new_id": new.id, "old_id": old.id}
    )
    store.add_link(Link(from_id=new.id, to_id=old.id, link_type=LinkType.SUPERSEDES))
    print(f"superseded: {old.title}  ({old.id[:8]})")
    print(f"        by: {new.title}  ({new.id[:8]})")
    return 0


def _resolve_entry(store: KnowledgeStore, prefix: str):
    matches = [e for e in store.list() if e.id.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    reason = "no entry matches" if not matches else f"{len(matches)} entries match"
    print(f"{reason} id prefix {prefix!r}", file=sys.stderr)
    return None


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

    p_init = sub.add_parser("init", help="set up knowhelm in this project")
    skill_group = p_init.add_mutually_exclusive_group()
    skill_group.add_argument("--skill", dest="skill", action="store_true", default=None,
                             help="install the companion skill without asking")
    skill_group.add_argument("--no-skill", dest="skill", action="store_false",
                             help="skip companion skill installation")
    p_init.set_defaults(func=cmd_init)

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
    p_knowledge.add_argument(
        "action", choices=["list", "approve", "reject", "supersede", "verify"]
    )
    p_knowledge.add_argument("entry_id", nargs="?")
    p_knowledge.add_argument("old_id", nargs="?",
                             help="for supersede: the entry being replaced")
    p_knowledge.add_argument("--stale", action="store_true",
                             help="for list: only entries whose code anchor drifted")
    p_knowledge.add_argument("--headed", action="store_true")
    p_knowledge.set_defaults(func=cmd_knowledge)

    return parser


def main(argv: list[str] | None = None) -> int:
    from .evidence.chain import LegacyKeyError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LegacyKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
