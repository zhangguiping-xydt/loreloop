"""knowhelm CLI: ingest / run / check / report / knowledge."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .agents import CODEX_RUNNER, AgentError, AgentRunner
from .delegate.runner import DelegateRunner
from .evidence.chain import ChainVerificationError, EvidenceChain
from .federation.reader import ForeignEntry
from .federation.registry import Project
from .knowledge.code_reverse import reverse_code
from .knowledge.endorsement import (
    SUPERSEDE_EVENT,
    chain_endorsed_strong_ids,
    chain_rejected_ids,
    chain_superseded_ids,
    curate,
    unendorsed_strong_ids,
)
from .knowledge.model import Curation, Entry
from .knowledge.store import KnowledgeStore
from .report.acceptance import RunTraceError, load_run, record_check, render_report

DB_PATH = ".knowhelm/knowledge.db"

# run ids are used to build filesystem paths; a strict shape rules out
# traversal like "../../etc/passwd" without any path canonicalization games.
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class InitializationError(Exception):
    pass


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
    workdir_ok, workdir_problem = _probe_writable_directory(workdir)
    if not workdir_ok:
        raise InitializationError(
            f"project directory is not writable: {workdir_problem}. Choose a writable checkout."
        )
    key_dir = key_path_for(workdir).parent
    key_ok, key_problem = _probe_writable_directory(key_dir, create=True)
    if not key_ok:
        raise InitializationError(
            f"cannot initialize evidence key in {key_dir}: {key_problem}. "
            "Set KNOWHELM_KEY_DIR to a writable directory outside the project tree."
        )
    _store(workdir).close()
    EvidenceChain.for_workdir(workdir)
    print(f"initialized .knowhelm/ (knowledge store, evidence chain) in {workdir}")
    print(f"evidence signing key: {key_path_for(workdir)} (outside the project tree)")
    print("register this trust domain for federation with `knowhelm project add .`")

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

    if args.skill is None:
        answer = input(
            f"install the knowhelm companion skill for {', '.join(hosts)}? [Y/n] "
        )
        wanted = answer.strip().lower() in ("", "y", "yes")
    else:
        wanted = args.skill
    if wanted:
        if "claude" in hosts:
            from .companion import install_claude_skill

            path = install_claude_skill(workdir)
            print(f"installed companion skill for Claude: {path.relative_to(workdir)}")
        if "codex" in hosts:
            from .companion import install_codex_skill

            path = install_codex_skill(workdir)
            print(f"installed companion skill for Codex: {path.relative_to(workdir)}")
    else:
        print("skipped skill installation (re-run `knowhelm init --skill` to install)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import platform
    import shutil

    from .evidence.chain import key_path_for, lock_backend

    workdir = _workdir()
    checks: list[tuple[str, str, str, bool]] = []
    python_ok = sys.version_info >= (3, 11)
    checks.append(("Python", "PASS" if python_ok else "FAIL", platform.python_version(), python_ok))
    git = shutil.which("git")
    checks.append(("Git", "PASS" if git else "FAIL", git or "not found on PATH", bool(git)))
    agents = [name for name in ("claude", "codex") if shutil.which(name)]
    checks.append((
        "coding agent",
        "PASS" if agents else "FAIL",
        ", ".join(agents) if agents else "install claude or codex",
        bool(agents),
    ))
    writable, detail = _probe_writable_directory(workdir)
    checks.append(("project directory", "PASS" if writable else "FAIL", detail, writable))
    key_dir = key_path_for(workdir).parent
    key_writable, key_detail = _probe_writable_directory(key_dir, create=True)
    checks.append((
        "evidence key directory",
        "PASS" if key_writable else "FAIL",
        f"{key_dir} ({key_detail})",
        key_writable,
    ))
    backend = lock_backend()
    lock_ok = backend != "unavailable"
    checks.append((
        "evidence lock", "PASS" if lock_ok else "FAIL", backend, lock_ok
    ))
    try:
        import playwright  # noqa: F401

        playwright_detail = "installed"
    except ImportError:
        playwright_detail = "optional; install knowhelm[web] for browser evidence"
    checks.append(("Playwright", "INFO", playwright_detail, True))

    for name, status, detail, _ in checks:
        print(f"{status:4}  {name:24} {detail}")
    ready = all(ok for _, status, _, ok in checks if status != "INFO")
    print("\nREADY: knowhelm preflight passed" if ready else "\nNOT READY: fix FAIL checks above")
    return 0 if ready else 1


def _probe_writable_directory(path: Path, *, create: bool = False) -> tuple[bool, str]:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return False, "path is not a directory"
        with tempfile.NamedTemporaryFile(prefix=".knowhelm-write-", dir=path):
            pass
    except OSError as exc:
        return False, str(exc)
    return True, "writable"


def cmd_ingest(args: argparse.Namespace) -> int:
    workdir = _workdir()
    if args.source == "code":
        repo_name, repo = _resolve_ingest_repo(workdir, args.target)
        entries = reverse_code(_agent(args.agent), repo, repo_name=repo_name)
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


def _resolve_ingest_repo(workdir: Path, target: str) -> tuple[str, Path]:
    from .knowledge.repos import RepoConfigError, load_repos

    repos = load_repos(workdir)
    if target in repos:
        return target, repos[target]
    resolved = Path(target).expanduser().resolve()
    matches = [name for name, path in repos.items() if path == resolved]
    if len(matches) == 1:
        return matches[0], resolved
    if len(matches) > 1:
        raise RepoConfigError(
            f"path {resolved} is declared under multiple repository names: {', '.join(matches)}"
        )
    if resolved == workdir.resolve():
        return ".", resolved
    raise RepoConfigError(
        f"code source {resolved} is not a declared repository; "
        "run `knowhelm repo add <path>` first"
    )


def cmd_repo(args: argparse.Namespace) -> int:
    from .knowledge.model import Channel
    from .knowledge.repos import (
        RepoConfigError,
        load_repos,
        parse_code_locator,
        save_repos,
        validate_repo_name,
    )

    workdir = _workdir()
    repos = load_repos(workdir)
    if args.action == "add":
        repo = Path(args.path).expanduser().resolve()
        if not repo.is_dir() or not (repo / ".git").exists():
            raise RepoConfigError(f"not a git repository root: {repo}")
        if repo == workdir.resolve():
            raise RepoConfigError("the current workdir is already the implicit '.' repository")
        name = validate_repo_name(args.name or repo.name)
        if name in repos:
            raise RepoConfigError(f"repository name {name!r} is already declared")
        duplicate = next((existing for existing, path in repos.items() if path == repo), None)
        if duplicate is not None:
            raise RepoConfigError(f"repository path is already declared as {duplicate!r}")
        repos[name] = repo
        save_repos(workdir, repos)
        print(f"added repository {name}: {repo}")
        return 0
    if args.action == "list":
        if args.path or args.name:
            raise RepoConfigError("repo list does not accept a path or --name")
        rows = [(".", workdir.resolve()), *repos.items()]
        for name, repo in rows:
            reachable = repo.is_dir() and (repo / ".git").exists()
            print(f"{name}\t{repo}\t{_git_head_short(repo) if reachable else '-'}\t"
                  f"{'reachable' if reachable else 'unreachable'}")
        return 0
    if args.name:
        raise RepoConfigError("--name is only valid with repo add")
    name = args.path
    if name == ".":
        raise RepoConfigError("the implicit '.' repository cannot be removed")
    if name not in repos:
        raise RepoConfigError(f"repository {name!r} is not declared")
    count = 0
    db = workdir / DB_PATH
    if db.exists():
        with KnowledgeStore.open_readonly(db) as store:
            for entry in store.list(channel=Channel.CODE):
                repo_name, _, _ = parse_code_locator(entry.source.locator)
                count += repo_name == name
    repos.pop(name)
    save_repos(workdir, repos)
    print(f"removed repository {name}; {count} anchored entr"
          f"{'y' if count == 1 else 'ies'} will display as stale until it is declared again")
    return 0


def _git_head_short(repo: Path) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "-"


def cmd_project(args: argparse.Namespace) -> int:
    from .federation.registry import add_project, list_projects, remove_project

    if args.action == "add":
        if not args.path:
            from .federation.registry import RegistryError

            raise RegistryError("project add requires a path")
        project = add_project(
            Path(args.path),
            project_id=args.project_id,
            name=args.name,
            aliases=args.alias,
            tags=args.tag,
        )
        print(f"registered project {project.project_id}: {project.path}")
        return 0
    if args.action == "list":
        if args.path or args.project_id or args.name or args.alias or args.tag:
            from .federation.registry import RegistryError

            raise RegistryError("project list does not accept add/remove arguments")
        for project in list_projects():
            available = (project.path / ".knowhelm/knowledge.db").is_file()
            print(
                f"{project.project_id}\t{project.name}\t{project.path}\t"
                f"{'available' if available else 'unavailable'}"
            )
        return 0
    if not args.path:
        from .federation.registry import RegistryError

        raise RegistryError("project remove requires an id")
    if args.project_id or args.name or args.alias or args.tag:
        from .federation.registry import RegistryError

        raise RegistryError("project remove only accepts a project id")
    removed = remove_project(args.path)
    print(f"removed project {removed.project_id}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .evidence.artifacts import ArtifactStore
    from .webexplore.actions import ActionScriptError, load_action_script, validate_script_origin
    from .webexplore.browser import PlaywrightBrowser
    from .webexplore.verify import (
        MalformedExpectation,
        parse_assertion,
        verify_expectation,
        verify_script_expectation,
    )

    try:
        if args.allow_writes and not args.script:
            raise ActionScriptError("--allow-writes requires --script")
        parse_assertion(args.expectation)
        script = load_action_script(Path(args.script)) if args.script else None
        if script is not None:
            validate_script_origin(script, args.url)
    except MalformedExpectation as exc:
        print(f"invalid expectation: {exc}", file=sys.stderr)
        return 2
    except ActionScriptError as exc:
        print(f"invalid action script: {exc}", file=sys.stderr)
        return 2

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    browser = PlaywrightBrowser(headed=args.headed)
    try:
        if script is None:
            result = verify_expectation(
                browser, _agent(args.agent), chain, args.run_id, args.url, args.expectation,
                artifacts=artifacts,
            )
        else:
            result = verify_script_expectation(
                browser,
                _agent(args.agent),
                chain,
                args.run_id,
                args.url,
                script,
                args.expectation,
                artifacts=artifacts,
                allow_writes=args.allow_writes,
            )
    finally:
        browser.close()
    status = "PASS" if result.passed else "FAIL"
    print(f"{status}: {result.reason}")
    snapshot = result.snapshot[:16] if result.snapshot else "none"
    print(f"evidence: chain hash {result.record.chain_hash[:16]}, "
          f"page snapshot {snapshot}")
    return 0 if result.passed else 1


def cmd_run(args: argparse.Namespace) -> int:
    from .knowledge.model import Channel

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    with _store(workdir) as store:
        entries = store.list()
    # Retirement by chain replay, not DB state: DB-only rejected flags or
    # supersedes links live in the agent-writable tree and cannot suppress a
    # chain-backed fact. Conversely, a chain-rejected or chain-superseded entry
    # stays retired even if SQLite is edited back to active.
    retired = chain_superseded_ids(records) | chain_rejected_ids(records)
    entries = [e for e in entries if e.id not in retired]
    # The DB sits in the agent-writable tree; its strong bits count only when
    # the chain endorses them FOR THE CURRENT CONTENT. Anything strong-in-DB
    # but unendorsed (no event, or content changed since endorsement) is
    # injected as reference and flagged for the operator.
    endorsed = chain_endorsed_strong_ids(entries, records)
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
    agent = _agent(args.agent)
    expansion = ""
    if (entries or args.with_related) and not args.no_expand:
        from .delegate.expand import ExpansionError, expand_query

        try:
            expansion = expand_query(
                agent,
                args.task,
                cache_path=workdir / ".knowhelm/cache/query-expansion.json",
            )
        except (ExpansionError, AgentError) as exc:
            print(f"[knowhelm] query expansion failed ({exc}); retrieving with the "
                  f"task text only", file=sys.stderr)
    runner = DelegateRunner(agent, workdir)
    related = (
        _select_related_entries(workdir, args.task, expansion, args.related_limit)
        if args.with_related
        else []
    )
    result = runner.run(
        args.task, entries,
        unendorsed_ids=unendorsed,
        endorsed_ids=endorsed,
        expansion=expansion,
        related=related,
    )
    chain_only = [e for e in result.pack.strong if e.id in endorsed and not e.is_strong_evidence()]
    if chain_only:
        print(f"[knowhelm] note: {len(chain_only)} entr"
              f"{'y is' if len(chain_only) == 1 else 'ies are'} chain-endorsed "
              f"although the store cache says reference — injected as established fact.",
              file=sys.stderr)
    # This chain record is the acceptance authority for the run: report and
    # harvest key off it, not off the agent-writable trace file.
    chain.append(
        "delegation_completed",
        {
            "run_id": result.run_id,
            "task": args.task,
            "context_entries": result.pack.entry_ids,
            "base_commits": result.base_commits,
            "related_entries": result.pack.related_ids,
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


def _select_related_entries(
    workdir: Path, task: str, expansion: str, limit: int
) -> list[ForeignEntry]:
    from .delegate.context_pack import rank_entries
    from .federation.reader import read_project
    from .federation.registry import RegistryError, load_projects, related_projects

    if limit < 1:
        raise RegistryError("related limit must be at least 1")
    projects = load_projects()
    overlap = dict(related_projects(workdir))
    candidates = []
    seen_paths: set[Path] = set()
    for project_id, _ in sorted(overlap.items(), key=lambda item: (-item[1], item[0])):
        project = projects[project_id]
        if project.path == workdir.resolve() or project.path in seen_paths:
            continue
        seen_paths.add(project.path)
        entries, warnings = read_project(project_id, project.path)
        for warning in warnings:
            print(f"warning [{warning.project_id}]: {warning.message}", file=sys.stderr)
        by_id = {item.entry.id: item for item in entries}
        ranked = rank_entries(
            task,
            [item.entry for item in entries],
            limit=max(limit * 2, limit),
            drifted_ids={item.entry.id for item in entries if item.drifted_there},
            endorsed_ids={item.entry.id for item in entries if item.strong_there},
            expansion=expansion,
        )
        for ranked_entry in ranked:
            candidates.append(
                (overlap[project_id], ranked_entry.adjusted_score, by_id[ranked_entry.entry.id])
            )
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2].project_id, item[2].entry.id))
    return [item for _, _, item in candidates[:limit]]


def cmd_check(args: argparse.Namespace) -> int:
    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    if args.command:
        from .evidence.artifacts import ArtifactStore
        from .report.acceptance import record_command_check

        argv = shlex.split(args.command)
        shell_tokens = {";", "|", "||", "&&", ">", ">>", "<", "2>", "2>>"}
        if any(part in shell_tokens for part in argv):
            print(
                "error: shell operators are not supported in --command; invoke an "
                "executable with explicit arguments",
                file=sys.stderr,
            )
            return 2
        rec = record_command_check(
            chain,
            ArtifactStore.for_workdir(workdir),
            args.run_id,
            args.check,
            argv,
            cwd=workdir,
            timeout=args.timeout,
        )
    else:
        rec = record_check(chain, args.run_id, args.check, args.passed, args.detail)
    print(f"recorded {rec.event} for {args.run_id} (chain hash {rec.chain_hash[:16]})")
    print("PASS" if rec.event == "check_passed" else "FAIL")
    return 0 if rec.event == "check_passed" else 1


def cmd_report(args: argparse.Namespace) -> int:
    workdir = _workdir()
    runs_dir = workdir / ".knowhelm/runs"
    if args.run_id:
        trace = _run_trace(workdir, args.run_id)
        if trace is None:
            return 2
        if not trace.exists():
            print(f"no trace found for {args.run_id}", file=sys.stderr)
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
    if result.demoted:
        print(f"  {len(result.demoted)} strong entr"
              f"{'y was' if len(result.demoted) == 1 else 'ies were'} re-anchored and "
              f"lost chain endorsement — they inject as reference until you "
              f"re-approve (`knowhelm knowledge approve`):", file=sys.stderr)
        for entry in result.demoted:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})",
                  file=sys.stderr)
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    workdir = _workdir()
    with _store(workdir) as store:
        if args.action == "list":
            return _list_entries(args, workdir, store)
        elif args.action == "search":
            return _search_entries(args, workdir, store)
        elif args.action == "import":
            return _import_entry(args, store)
        elif args.action == "export":
            return _export_entries(args, workdir, store)
        elif args.action in ("approve", "reject"):
            return _curate(args, workdir, store)
        elif args.action == "supersede":
            return _supersede(args, workdir, store)
        elif args.action == "verify":
            return _verify_entries(args, workdir, store)
        elif args.action == "usage":
            return _knowledge_usage(workdir, store)


def _knowledge_usage(workdir: Path, store: KnowledgeStore) -> int:
    """Show correlation between injection and later accepted harvests."""
    records = EvidenceChain.for_workdir(workdir).verify()
    accepted_runs = {
        record.payload.get("run_id")
        for record in records
        if record.event == "knowledge_harvested" and record.payload.get("run_id")
    }
    injected: dict[str, int] = {}
    accepted: dict[str, int] = {}
    for record in records:
        if record.event != "delegation_completed":
            continue
        entry_ids = record.payload.get("context_entries", [])
        if not isinstance(entry_ids, list):
            continue
        run_accepted = record.payload.get("run_id") in accepted_runs
        for entry_id in {value for value in entry_ids if isinstance(value, str)}:
            injected[entry_id] = injected.get(entry_id, 0) + 1
            if run_accepted:
                accepted[entry_id] = accepted.get(entry_id, 0) + 1
    rows = [entry for entry in store.list() if entry.id in injected]
    if not rows:
        print("no knowledge usage recorded yet")
        return 0
    print("ID        INJECTED  ACCEPTED  TITLE")
    for entry in sorted(rows, key=lambda item: (-injected[item.id], item.title, item.id)):
        print(
            f"{entry.id[:8]}  {injected[entry.id]:8d}  "
            f"{accepted.get(entry.id, 0):8d}  {entry.title}"
        )
    print("\nAccepted means the run was later harvested after evidence-backed acceptance; "
          "it is correlation, not proof that one entry caused success.")
    return 0


def _search_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .delegate.context_pack import rank_entries
    from .federation.reader import grade_local_entries, read_project
    from .federation.registry import load_projects

    if not args.entry_id:
        from .federation.registry import RegistryError

        raise RegistryError("knowledge search requires a query")
    if args.limit < 1:
        from .federation.registry import RegistryError

        raise RegistryError("search limit must be at least 1")

    query = args.entry_id
    projects = load_projects()
    selected = []
    include_local = not args.project
    if args.all:
        selected = list(projects.values())
    elif args.project:
        selected = [_resolve_project_selector(selector, projects) for selector in args.project]
    elif args.tag:
        selected = list(projects.values())
        include_local = False
    if args.tag:
        selected = [project for project in selected if all(tag in project.tags for tag in args.tag)]

    groups: list[list[ForeignEntry]] = []
    warnings = []
    if include_local:
        records = EvidenceChain.for_workdir(workdir).verify()
        local_entries = store.list()
        groups.append(
            grade_local_entries(".", local_entries, records, _drifted_entries(workdir, local_entries))
        )
    seen_paths: set[Path] = set()
    for project in selected:
        if project.path == workdir.resolve() and include_local:
            continue
        if project.path in seen_paths:
            continue
        seen_paths.add(project.path)
        entries, project_warnings = read_project(project.project_id, project.path)
        groups.append(entries)
        warnings.extend(project_warnings)
    for warning in warnings:
        print(f"warning [{warning.project_id}]: {warning.message}", file=sys.stderr)

    ranked: list[tuple[float, ForeignEntry]] = []
    for group in groups:
        candidates = [item.entry for item in group]
        by_id = {item.entry.id: item for item in group}
        group_ranked = rank_entries(
            query,
            candidates,
            limit=max(args.limit * 2, args.limit),
            drifted_ids={item.entry.id for item in group if item.drifted_there},
            endorsed_ids={item.entry.id for item in group if item.strong_there},
        )
        ranked.extend(
            (item.adjusted_score, by_id[item.entry.id]) for item in group_ranked
        )
    ranked.sort(key=lambda pair: (-pair[0], pair[1].project_id, pair[1].entry.id))
    for _, item in ranked[: args.limit]:
        print(
            f"[{item.project_id}] {item.entry.id[:8]}  [{item.entry.kind.value}] "
            f"[{item.trust_note}] {item.entry.title}"
        )
    if not ranked:
        print("no matching knowledge entries")
    return 0


def _resolve_project_selector(selector: str, projects: dict[str, Project]) -> Project:
    from .delegate.context_pack import Bm25Scorer, _terms
    from .federation.registry import RegistryError
    from .knowledge.model import Channel, Entry, Kind, Source

    if selector in projects:
        return projects[selector]
    entries = [
        Entry(
            id=project.project_id,
            title=project.name,
            content=" ".join([project.project_id, project.name, *project.aliases, *project.tags]),
            kind=Kind.ARCHITECTURE,
            source=Source(channel=Channel.MANUAL, locator=f"registry:{project.project_id}"),
        )
        for project in projects.values()
    ]
    scorer = Bm25Scorer(entries)
    scores = sorted(
        ((scorer.score(_terms(selector), entry), entry.id) for entry in entries),
        reverse=True,
    )
    if not scores or scores[0][0] <= 0:
        raise RegistryError(f"no registered project matches {selector!r}")
    top = scores[0][0]
    candidates = [project_id for score, project_id in scores if score == top]
    if len(candidates) != 1:
        raise RegistryError(
            f"project selector {selector!r} is ambiguous: {', '.join(sorted(candidates))}"
        )
    return projects[candidates[0]]


def _import_entry(args: argparse.Namespace, store: KnowledgeStore) -> int:
    from .federation.reader import read_project
    from .federation.registry import RegistryError, load_projects
    from .knowledge.endorsement import entry_digest
    from .knowledge.model import Channel, Entry, Source

    if not args.entry_id or not args.old_id:
        raise RegistryError("knowledge import requires a project id and entry id prefix")
    projects = load_projects()
    project = projects.get(args.entry_id)
    if project is None:
        raise RegistryError(f"project {args.entry_id!r} is not registered")
    entries, warnings = read_project(project.project_id, project.path)
    for warning in warnings:
        print(f"warning [{warning.project_id}]: {warning.message}", file=sys.stderr)
    matches = [item for item in entries if item.entry.id.startswith(args.old_id)]
    if len(matches) != 1:
        reason = "no entry matches" if not matches else f"{len(matches)} entries match"
        raise RegistryError(f"{reason} id prefix {args.old_id!r} in project {project.project_id}")
    foreign = matches[0]
    imported = store.add(
        Entry(
            title=foreign.entry.title,
            content=foreign.entry.content,
            kind=foreign.entry.kind,
            source=Source(
                channel=Channel.MANUAL,
                locator=f"project:{project.project_id}#{foreign.entry.id}",
                snapshot_ref=entry_digest(foreign.entry),
            ),
        )
    )
    print(f"imported {foreign.entry.id[:8]} from {project.project_id} as {imported.id[:8]}")
    when = f" at {foreign.trust_ts}" if foreign.trust_ts else ""
    print(f"source was {foreign.trust_note} in {project.project_id}{when}; imported entry is draft")
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
    entries = store.list()
    # Chain replay, not DB state: a deleted links row must not un-supersede
    # an entry, a strong bit UPDATEd straight into the DB must not show as
    # [strong], and a chain-rejected entry stays rejected even if its DB
    # curation was flipped back — the list view informs curation decisions,
    # so it applies the same rules as injection.
    records = EvidenceChain.for_workdir(workdir).verify()
    superseded = chain_superseded_ids(records)
    rejected = chain_rejected_ids(records)
    endorsed = chain_endorsed_strong_ids(entries, records)
    unendorsed = unendorsed_strong_ids(entries, records)
    drifted = _drifted_entries(workdir, entries)
    if args.stale:
        entries = [e for e in entries if e.id in drifted and e.id not in superseded]
        if not entries:
            print("no stale entries: every code anchor matches the current tree")
            return 0
    for e in entries:
        demoted = e.id in unendorsed or e.id in rejected
        chain_backed = e.id in endorsed
        strong = "strong" if (e.is_strong_evidence() or chain_backed) and not demoted else "ref"
        flags = ""
        if e.id in unendorsed:
            flags += "  [unendorsed: strong bit has no chain endorsement]"
        if chain_backed and not e.is_strong_evidence() and not demoted:
            flags += "  [chain-backed: store cache says reference]"
        if e.id in rejected:
            flags += "  [rejected]"
        if e.id in superseded:
            flags += "  [superseded]"
        if e.id in drifted:
            flags += "  [stale: source changed since capture]"
        print(f"{e.id[:8]}  [{e.kind.value:<12}] [{strong:<6}] {e.title}{flags}")
    return 0


def _export_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    entries = store.list()
    records = EvidenceChain.for_workdir(workdir).verify()
    superseded = chain_superseded_ids(records)
    rejected = chain_rejected_ids(records)
    endorsed = chain_endorsed_strong_ids(entries, records)
    unendorsed = unendorsed_strong_ids(entries, records)
    drifted = _drifted_entries(workdir, entries)
    if args.stale:
        entries = [e for e in entries if e.id in drifted and e.id not in superseded]

    lines = [
        "# knowhelm knowledge export",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Entries: {len(entries)}",
        "",
    ]
    if not entries:
        lines.append("_No entries._")
    for entry in entries:
        demoted = entry.id in unendorsed or entry.id in rejected
        chain_backed = entry.id in endorsed
        strength = "strong" if (entry.is_strong_evidence() or chain_backed) and not demoted else "reference"
        flags = []
        if entry.id in unendorsed:
            flags.append("unendorsed")
        if entry.id in rejected:
            flags.append("rejected")
        if entry.id in superseded:
            flags.append("superseded")
        if entry.id in drifted:
            flags.append("stale")
        lines += [
            f"## {_md_escape(entry.title)}",
            "",
            f"- ID: `{entry.id}`",
            f"- Kind: `{entry.kind.value}`",
            f"- Strength: `{strength}`",
            f"- Curation: `{entry.trust.curation.value}`",
            f"- Verification: `{entry.trust.verification.value}`",
            f"- Source: `{entry.source.channel.value}` `{_md_escape(entry.source.locator)}`",
            f"- Snapshot: `{entry.source.snapshot_ref or ''}`",
        ]
        if flags:
            lines.append(f"- Flags: {', '.join(f'`{flag}`' for flag in flags)}")
        lines += ["", _md_escape(entry.content), ""]

    text = "\n".join(lines).rstrip() + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"exported {len(entries)} entries to {path}")
    else:
        print(text, end="")
    return 0


def _md_escape(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ")


def _drifted_entries(workdir: Path, entries: list[Entry]) -> set[str]:
    from .knowledge.code_reverse import drifted_code_entry_ids
    from .knowledge.repos import load_repos

    if not (workdir / ".git").exists() and not load_repos(workdir):
        return set()
    return drifted_code_entry_ids(workdir, entries)


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
    from .webexplore.actions import ActionBlocked
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
            try:
                result = verify_entry(
                    browser, agent, chain, store, entry, run_id, artifacts=artifacts
                )
            except ActionBlocked as exc:
                print(f"BLOCKED: {entry.title}", file=sys.stderr)
                print(f"  {exc}", file=sys.stderr)
                return 2
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

    p_doctor = sub.add_parser("doctor", help="check prerequisites and writable trust state")
    p_doctor.set_defaults(func=cmd_doctor)

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

    p_repo = sub.add_parser("repo", help="manage repositories in this trust domain")
    p_repo.add_argument("action", choices=["add", "list", "remove"])
    p_repo.add_argument("path", nargs="?", help="for add: git repository root")
    p_repo.add_argument("--name", help="repository name; defaults to the directory name")
    p_repo.set_defaults(func=cmd_repo)

    p_project = sub.add_parser("project", help="manage the federation project registry")
    p_project.add_argument("action", choices=["add", "list", "remove"])
    p_project.add_argument("path", nargs="?", help="project path for add; project id for remove")
    p_project.add_argument("--id", dest="project_id")
    p_project.add_argument("--name")
    p_project.add_argument("--alias", action="append", default=[])
    p_project.add_argument("--tag", action="append", default=[])
    p_project.set_defaults(func=cmd_project)

    p_verify = sub.add_parser("verify", help="verify an expectation against a live page")
    p_verify.add_argument("run_id")
    p_verify.add_argument("url", help="page URL, or same-origin base URL for --script")
    p_verify.add_argument("expectation")
    p_verify.add_argument("--headed", action="store_true")
    p_verify.add_argument("--script", help="replay an interaction script before verification")
    p_verify.add_argument(
        "--allow-writes",
        action="store_true",
        help="allow fill/select/POST-submit actions outside search/filter controls",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", help="delegate a task with injected knowledge")
    p_run.add_argument("task")
    p_run.add_argument("--no-expand", action="store_true",
                       help="skip LLM query expansion; retrieve with the task text only")
    p_run.add_argument("--with-related", action="store_true")
    p_run.add_argument("--related-limit", type=int, default=5)
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check", help="record an acceptance check for a run")
    p_check.add_argument("run_id")
    p_check.add_argument("check")
    result = p_check.add_mutually_exclusive_group(required=True)
    result.add_argument("--pass", dest="passed", action="store_true")
    result.add_argument("--fail", dest="passed", action="store_false")
    result.add_argument(
        "--command",
        help="execute an argv-style command without a shell; exit code zero passes",
    )
    p_check.add_argument("--detail")
    p_check.add_argument("--timeout", type=float, default=300.0)
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
        "action",
        choices=[
            "list", "search", "import", "export", "approve", "reject", "supersede",
            "verify", "usage",
        ],
    )
    p_knowledge.add_argument("entry_id", nargs="?")
    p_knowledge.add_argument("old_id", nargs="?",
                             help="for supersede: the entry being replaced")
    p_knowledge.add_argument("--stale", action="store_true",
                             help="for list: only entries whose code anchor drifted")
    p_knowledge.add_argument("--headed", action="store_true")
    p_knowledge.add_argument("--output", help="for export: write markdown to this path")
    search_scope = p_knowledge.add_mutually_exclusive_group()
    search_scope.add_argument("--all", action="store_true", help="search all registered projects")
    search_scope.add_argument(
        "--project", action="append", help="search one registered project; may be repeated"
    )
    p_knowledge.add_argument("--tag", action="append", help="filter selected projects by tag")
    p_knowledge.add_argument("--limit", type=int, default=10)
    p_knowledge.set_defaults(func=cmd_knowledge)

    return parser


def main(argv: list[str] | None = None) -> int:
    from .evidence.chain import LegacyKeyError
    from .federation.registry import RegistryError
    from .knowledge.repos import RepoConfigError
    from .knowledge.code_reverse import ExtractionError
    from .webexplore.browser import BrowserUnavailable

    args = build_parser().parse_args(argv)
    try:
        if args.command == "repo" and args.action == "add" and not args.path:
            raise RepoConfigError("repo add requires a path")
        if args.command == "repo" and args.action == "remove":
            if not args.path:
                raise RepoConfigError("repo remove requires a name")
        return args.func(args)
    except (
        ChainVerificationError,
        LegacyKeyError,
        RegistryError,
        RepoConfigError,
        RunTraceError,
        InitializationError,
        AgentError,
        ExtractionError,
        BrowserUnavailable,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
