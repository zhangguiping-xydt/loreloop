"""LoreLoop CLI: ingest / run / check / report / knowledge."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .agents import AgentError, AgentRunner, delegation_runner, inference_runner
from .delegate.runner import DelegateRunner
from .evidence.chain import ChainVerificationError, EvidenceChain, EvidenceRecord
from .federation.reader import ForeignEntry
from .federation.registry import Project
from .knowledge.code_reverse import (
    CodeIngestionProgress,
    IngestionPolicy,
    chain_ingestion_policies,
    dirty_source_files,
    ingestion_policies_payload,
    record_ingestion_policy,
    reverse_code,
    scan_repo_manifest,
)
from .knowledge.endorsement import (
    SUPERSEDE_EVENT,
    UNSUPERSEDE_EVENT,
    assert_trust_projection,
    chain_approved_ids,
    chain_contradicted_ids,
    chain_endorsed_strong_ids,
    chain_effective_curation,
    chain_rejected_ids,
    chain_supersession_links,
    chain_superseded_ids,
    chain_verified_ids,
    curate,
    entry_digest,
    entry_from_payload,
    entry_payload,
    record_reingested,
    unendorsed_strong_ids,
)
from .knowledge.model import Channel, Curation, Entry
from .knowledge.store import KnowledgeStore
from .paths import (
    StatePathError,
    ensure_state_root,
    key_directory,
    load_trust_locations,
    register_key_directory,
    state_path,
    state_root,
    unregister_key_directory,
)
from .report.acceptance import RunTraceError, evaluate_run, load_run, record_check, render_report

# run ids are used to build filesystem paths; a strict shape rules out
# traversal like "../../etc/passwd" without any path canonicalization games.
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_CODEX_MARKETPLACE = "loreloop"
_CODEX_PLUGIN_ID = "loreloop@loreloop"
_CODEX_MARKETPLACE_SOURCE = "zhangguiping-xydt/loreloop"
_CODEX_DEFAULT_REF = f"v{__version__}"
_CLAUDE_MARKETPLACE = "loreloop"
_CLAUDE_PLUGIN_ID = "loreloop@loreloop"
_CLAUDE_MARKETPLACE_SOURCE = "zhangguiping-xydt/loreloop"
_COMIND_MARKETPLACE = "loreloop"
_COMIND_PLUGIN_ID = "loreloop@loreloop"
_COMIND_MARKETPLACE_SOURCE = "zhangguiping-xydt/loreloop"
_AGENT_CHOICES = ["claude", "codex", "opencode", "co-mind"]


class InitializationError(Exception):
    pass


class CLIError(Exception):
    """A failure the operator can recover from without a traceback."""

    def __init__(
        self,
        summary: str,
        reason: str,
        next_action: str,
        *,
        exit_code: int = 2,
    ) -> None:
        super().__init__(reason)
        self.summary = summary
        self.reason = reason
        self.next_action = next_action
        self.exit_code = exit_code


class _HelpRequested(Exception):
    pass


class CLIArgumentParser(argparse.ArgumentParser):
    """Keep argparse diagnostics inside the same recoverable CLI contract."""

    def error(self, message: str) -> None:
        raise CLIError(
            "invalid command",
            message,
            f"run `{self.prog} --help` and retry with the documented arguments",
        )

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, sys.stderr)
        if status == 0:
            raise _HelpRequested
        raise CLIError(
            "command parsing failed",
            message.strip() if message else f"argument parser exited with status {status}",
            f"run `{self.prog} --help` and correct the command",
        )


def _print_cli_error(error: CLIError) -> int:
    print(f"error: {error.summary}", file=sys.stderr)
    print(f"reason: {error.reason}", file=sys.stderr)
    print(f"next: {error.next_action}", file=sys.stderr)
    return error.exit_code


def _add_agent_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=argparse.SUPPRESS,
        help="override the coding-agent CLI for this action",
    )


def _add_knowledge_filters(parser: argparse.ArgumentParser) -> None:
    from .knowledge.model import Channel, Kind

    parser.add_argument(
        "--status",
        choices=[value.value for value in Curation],
        help="filter by curation status",
    )
    parser.add_argument(
        "--channel", choices=[value.value for value in Channel], help="filter by source channel"
    )
    parser.add_argument(
        "--kind", choices=[value.value for value in Kind], help="filter by knowledge kind"
    )
    parser.add_argument("--active", action="store_true", help="exclude all chain-retired entries")
    parser.add_argument("--limit", type=int, default=50, help="maximum entries to display")
    parser.add_argument("--offset", type=int, default=0, help="entries to skip before display")


def _parse_search_expansion(value: str) -> str:
    from .knowledge.authoritative_search import MAX_SEARCH_EXPANSION_CHARS

    if len(value) > MAX_SEARCH_EXPANSION_CHARS:
        raise argparse.ArgumentTypeError(
            f"search expansion must be at most {MAX_SEARCH_EXPANSION_CHARS} characters"
        )
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise argparse.ArgumentTypeError("search expansion contains a control character")
    return value.strip()


def _run_trace(workdir: Path, run_id: str) -> Path | None:
    if not _RUN_ID.match(run_id):
        raise CLIError(
            "invalid run id",
            f"{run_id!r} contains unsupported characters or is too long",
            "use the exact run id printed by `loreloop begin`, `loreloop run`, or `loreloop report`",
        )
    return state_path(workdir, "runs", f"{run_id}.jsonl")


def _workdir() -> Path:
    return Path.cwd()


def _store(workdir: Path) -> KnowledgeStore:
    ensure_state_root(workdir)
    db = state_path(workdir, "knowledge.db")
    return KnowledgeStore(db)


def _agent(name: str) -> AgentRunner:
    return delegation_runner(name, _workdir())


def _inference_agent(name: str) -> AgentRunner:
    return inference_runner(name)


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
            f"cannot initialize local trust in {key_dir}: {key_problem}. "
            "Choose a writable operator-owned directory outside the project tree."
        )
    _store(workdir).close()
    chain = EvidenceChain.for_workdir(workdir)
    chain.verify()
    register_key_directory(workdir, key_dir)
    state_dir = state_root(workdir)
    print(f"initialized {state_dir.name}/ (knowledge store, evidence chain) in {workdir}")
    print("local trust: ready (managed automatically)")
    print("register this trust domain for federation with `loreloop project add .`")

    gitignore = workdir / ".gitignore"
    if (workdir / ".git").exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
        ignore_entry = f"{state_dir.name}/"
        if ignore_entry not in lines:
            with gitignore.open("a", encoding="utf-8") as fh:
                if lines and lines[-1].strip():
                    fh.write("\n")
                fh.write(f"{ignore_entry}\n")
            print(f"added {ignore_entry} to .gitignore (evidence may embed page content)")
        print(
            "Git note: project-local integration files remain ordinary project files; "
            "LoreLoop never commits them automatically. Use "
            "`loreloop knowledge export --format docs --output baseline --working-tree` "
            "to export the current state without cleaning Git."
        )

    hosts = [name for name in _AGENT_CHOICES if shutil.which(name)]
    if not hosts and args.skill is not True:
        print(
            "no supported coding agent (claude/codex/opencode/co-mind) found on PATH; "
            "skill installation skipped"
        )
        return 0
    if hosts:
        print(f"detected coding agent(s): {', '.join(hosts)}")
    else:
        print(
            "no supported coding-agent CLI found on PATH; "
            "installing shared project skills because --skill was explicit"
        )

    if args.skill is None:
        answer = input(f"install the loreloop companion skill for {', '.join(hosts)}? [Y/n] ")
        wanted = answer.strip().lower() in ("", "y", "yes")
    else:
        wanted = args.skill
    if wanted:
        from .companion import AGENT_SKILL_RELPATH, CLAUDE_SKILL_RELPATH

        refresh_existing = args.skill is True
        claude_hosts = [name for name in ("claude", "co-mind") if name in hosts]
        install_claude = bool(claude_hosts) or (
            refresh_existing and ((workdir / CLAUDE_SKILL_RELPATH).exists() or not hosts)
        )
        if install_claude:
            from .companion import install_claude_skill

            existed = (workdir / CLAUDE_SKILL_RELPATH).exists()
            path = install_claude_skill(workdir)
            labels = {"claude": "Claude", "co-mind": "co-mind"}
            detected = "/".join(labels[name] for name in claude_hosts) or "Claude-compatible hosts"
            action = "refreshed" if existed else "installed"
            print(f"{action} companion skill for {detected}: {path.relative_to(workdir)}")
        agent_hosts = [name for name in ("codex", "opencode") if name in hosts]
        install_agents = bool(agent_hosts) or (
            refresh_existing and ((workdir / AGENT_SKILL_RELPATH).exists() or not hosts)
        )
        if install_agents:
            from .companion import install_codex_skill

            existed = (workdir / AGENT_SKILL_RELPATH).exists()
            path = install_codex_skill(workdir)
            labels = {"codex": "Codex", "opencode": "OpenCode"}
            detected = "/".join(labels[name] for name in agent_hosts) or "Codex-compatible hosts"
            action = "refreshed" if existed else "installed"
            print(f"{action} companion skill for {detected}: {path.relative_to(workdir)}")
        if "opencode" in hosts:
            from .companion import install_opencode_command

            path = install_opencode_command(workdir)
            print(f"installed OpenCode command: {path.relative_to(workdir)}")
    else:
        print("skipped skill installation (re-run `loreloop init --skill` to install)")
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
    agents = [name for name in _AGENT_CHOICES if shutil.which(name)]
    checks.append(
        (
            "coding agent",
            "PASS" if agents else "FAIL",
            ", ".join(agents) if agents else "install claude, codex, opencode, or co-mind",
            bool(agents),
        )
    )
    writable, detail = _probe_writable_directory(workdir)
    checks.append(("project directory", "PASS" if writable else "FAIL", detail, writable))
    try:
        key_path = key_path_for(workdir)
    except StatePathError as exc:
        key_path = None
        key_dir = key_directory(workdir)
        key_writable = False
        key_detail = str(exc)
    else:
        key_dir = key_path.parent
        key_writable, key_detail = _probe_writable_directory(key_dir, create=True)
    checks.append(
        (
            "local trust directory",
            "PASS" if key_writable else "FAIL",
            "managed automatically (writable)" if key_writable else f"{key_dir} ({key_detail})",
            key_writable,
        )
    )
    history_path = state_path(workdir, "evidence.jsonl")
    has_history = history_path.is_file() and bool(history_path.read_text(encoding="utf-8").strip())
    if key_path is None:
        key_ok = False
        key_detail = "local trust storage is unavailable until the directory boundary is fixed"
    elif key_path.exists():
        try:
            key_size = len(key_path.read_bytes())
            if key_size != 32:
                key_ok = False
                key_detail = "local trust material is invalid; restore its backup"
            else:
                EvidenceChain.for_workdir(workdir, create_key=False).verify()
                key_ok = True
                key_detail = "ready"
        except ChainVerificationError as exc:
            key_ok = False
            if exc.index == 0 and exc.reason == "signature invalid":
                key_detail = (
                    "local trust does not match this project's history; run `loreloop trust status`"
                )
            else:
                key_detail = f"project history integrity check failed: {exc.reason}"
        except OSError as exc:
            key_ok = False
            key_detail = f"cannot read local trust material: {exc}"
    else:
        key_ok = key_writable and not has_history
        key_detail = (
            "existing project history needs its original local trust; run `loreloop trust status`"
            if has_history
            else "will be created automatically during initialization"
        )
    checks.append(("local trust", "PASS" if key_ok else "FAIL", key_detail, key_ok))
    backend = lock_backend()
    lock_ok = backend != "unavailable"
    checks.append(("evidence lock", "PASS" if lock_ok else "FAIL", backend, lock_ok))
    try:
        import playwright  # noqa: F401

        playwright_detail = "installed"
    except ImportError:
        playwright_detail = "optional; install loreloop[web] for browser evidence"
    checks.append(("Playwright", "INFO", playwright_detail, True))

    for name, status, detail, _ in checks:
        print(f"{status:4}  {name:24} {detail}")
    ready = all(ok for _, status, _, ok in checks if status != "INFO")
    print("\nREADY: loreloop preflight passed" if ready else "\nNOT READY: fix FAIL checks above")
    return 0 if ready else 1


def _codex_json(*argv: str) -> dict[str, object]:
    import json
    import shutil
    import subprocess

    executable = shutil.which("codex")
    if executable is None:
        raise CLIError(
            "Codex integration is unavailable",
            "the Codex CLI is not installed or is not on PATH",
            "install and authenticate Codex, then rerun `loreloop codex install`",
        )
    try:
        result = subprocess.run(
            [executable, *argv],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise CLIError(
            "Codex integration command failed",
            detail,
            "run `codex plugin list`, resolve the reported marketplace/plugin issue, then retry",
        ) from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CLIError(
            "Codex integration returned invalid output",
            "the Codex CLI did not return the expected JSON response",
            "upgrade Codex, then rerun `loreloop codex status`",
        ) from exc
    if not isinstance(payload, dict):
        raise CLIError(
            "Codex integration returned invalid output",
            "the Codex CLI JSON response is not an object",
            "upgrade Codex, then rerun `loreloop codex status`",
        )
    return payload


def _codex_marketplace() -> dict[str, object] | None:
    payload = _codex_json("plugin", "marketplace", "list", "--json")
    raw = payload.get("marketplaces")
    if not isinstance(raw, list):
        return None
    return next(
        (item for item in raw if isinstance(item, dict) and item.get("name") == _CODEX_MARKETPLACE),
        None,
    )


def _codex_plugin() -> dict[str, object] | None:
    payload = _codex_json(
        "plugin",
        "list",
        "--json",
        "--available",
        "--marketplace",
        _CODEX_MARKETPLACE,
    )
    for group in ("installed", "available"):
        raw = payload.get(group)
        if not isinstance(raw, list):
            continue
        match = next(
            (
                item
                for item in raw
                if isinstance(item, dict) and item.get("pluginId") == _CODEX_PLUGIN_ID
            ),
            None,
        )
        if match is not None:
            return match
    return None


def cmd_codex(args: argparse.Namespace) -> int:
    if args.action == "status":
        marketplace = _codex_marketplace()
        if marketplace is None:
            print("Codex integration: not installed")
            print("Next: loreloop codex install")
            return 1
        plugin = _codex_plugin()
        if plugin is None or not plugin.get("installed") or not plugin.get("enabled"):
            print("Codex integration: plugin not enabled")
            print(f"Next: codex plugin add {_CODEX_PLUGIN_ID}")
            return 1
        print("Codex integration: ready")
        print(f"Plugin: {_CODEX_PLUGIN_ID} {plugin.get('version', '')}".rstrip())
        print("Entry point: invoke `$loreloop` in a new Codex thread")
        return 0

    if args.action == "install":
        marketplace = _codex_marketplace()
        if marketplace is None:
            command = [
                "plugin",
                "marketplace",
                "add",
                args.source,
                "--json",
            ]
            if args.ref and not Path(args.source).expanduser().exists():
                command[4:4] = ["--ref", args.ref]
            _codex_json(*command)
            print(f"Added Codex marketplace: {_CODEX_MARKETPLACE}")
        else:
            print(f"Using existing Codex marketplace: {_CODEX_MARKETPLACE} (source preserved)")
        _codex_json("plugin", "add", _CODEX_PLUGIN_ID, "--json")
        print(f"Installed and enabled Codex plugin: {_CODEX_PLUGIN_ID}")
        print("Next: start a new Codex thread and invoke `$loreloop` in your project.")
        return 0

    if args.action == "uninstall":
        marketplace = _codex_marketplace()
        if marketplace is None:
            print("Codex integration: already removed")
            return 0
        plugin = _codex_plugin()
        if plugin is not None and plugin.get("installed"):
            _codex_json("plugin", "remove", _CODEX_PLUGIN_ID, "--json")
            print(f"Removed Codex plugin: {_CODEX_PLUGIN_ID}")
        if args.remove_marketplace:
            _codex_json("plugin", "marketplace", "remove", _CODEX_MARKETPLACE, "--json")
            print(f"Removed Codex marketplace: {_CODEX_MARKETPLACE}")
        else:
            print("Marketplace preserved; pass --remove-marketplace to remove it too.")
        return 0

    raise CLIError(
        "unsupported Codex integration action",
        f"unknown Codex action: {args.action}",
        "run `loreloop codex --help`",
    )


def cmd_opencode(args: argparse.Namespace) -> int:
    from .companion import (
        install_opencode_global,
        opencode_global_status,
        uninstall_opencode_global,
    )

    if args.action == "status":
        status = opencode_global_status()
        if all(state == "ready" for _, state in status):
            print("OpenCode integration: ready")
            for path, _ in status:
                print(f"Installed: {path}")
            print("Entry point: run `/loreloop <request>` in a new OpenCode session")
            return 0
        print("OpenCode integration: not ready")
        for path, state in status:
            print(f"{state}: {path}")
        print("Next: loreloop opencode install")
        return 1

    if args.action == "install":
        try:
            skill, command = install_opencode_global()
        except RuntimeError as exc:
            raise CLIError(
                "OpenCode integration was not installed",
                str(exc),
                "preserve or rename the conflicting file, then rerun `loreloop opencode install`",
            ) from exc
        print(f"Installed OpenCode skill: {skill}")
        print(f"Installed OpenCode command: {command}")
        print("Next: start a new OpenCode session and run `/loreloop <request>`.")
        return 0

    if args.action == "uninstall":
        try:
            removed = uninstall_opencode_global()
        except RuntimeError as exc:
            raise CLIError(
                "OpenCode integration was not removed",
                str(exc),
                "keep the modified file or restore LoreLoop's managed content, then retry",
            ) from exc
        if not removed:
            print("OpenCode integration: already removed")
            return 0
        for path in removed:
            print(f"Removed OpenCode integration file: {path}")
        return 0

    raise CLIError(
        "unsupported OpenCode integration action",
        f"unknown OpenCode action: {args.action}",
        "run `loreloop opencode --help`",
    )


def _claude_command(*argv: str) -> str:
    import shutil
    import subprocess

    executable = shutil.which("claude")
    if executable is None:
        raise CLIError(
            "Claude Code integration is unavailable",
            "the Claude Code CLI is not installed or is not on PATH",
            "install Claude Code, then rerun `loreloop claude install`",
        )
    try:
        result = subprocess.run(
            [executable, *argv],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise CLIError(
            "Claude Code integration command failed",
            detail,
            "run `claude plugin list`, resolve the reported marketplace/plugin issue, then retry",
        ) from exc
    return result.stdout


def _claude_json(*argv: str) -> object:
    import json

    output = _claude_command(*argv)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CLIError(
            "Claude Code integration returned invalid output",
            "the Claude Code CLI did not return the expected JSON response",
            "upgrade Claude Code, then rerun `loreloop claude status`",
        ) from exc


def _claude_marketplace() -> dict[str, object] | None:
    payload = _claude_json("plugin", "marketplace", "list", "--json")
    raw = payload if isinstance(payload, list) else None
    if raw is None and isinstance(payload, dict):
        value = payload.get("marketplaces")
        raw = value if isinstance(value, list) else None
    if raw is None:
        return None
    return next(
        (
            item
            for item in raw
            if isinstance(item, dict) and item.get("name") == _CLAUDE_MARKETPLACE
        ),
        None,
    )


def _claude_plugin() -> dict[str, object] | None:
    payload = _claude_json("plugin", "list", "--json")
    raw = payload if isinstance(payload, list) else None
    if raw is None and isinstance(payload, dict):
        value = payload.get("installed")
        raw = value if isinstance(value, list) else None
    if raw is None:
        return None
    return next(
        (
            item
            for item in raw
            if isinstance(item, dict)
            and (item.get("pluginId") == _CLAUDE_PLUGIN_ID or item.get("id") == _CLAUDE_PLUGIN_ID)
        ),
        None,
    )


def cmd_claude(args: argparse.Namespace) -> int:
    if args.action == "status":
        marketplace = _claude_marketplace()
        if marketplace is None:
            print("Claude Code integration: not installed")
            print("Next: loreloop claude install")
            return 1
        plugin = _claude_plugin()
        if plugin is None or plugin.get("enabled") is False:
            print("Claude Code integration: plugin not installed")
            print(f"Next: claude plugin install {_CLAUDE_PLUGIN_ID} --scope user")
            return 1
        print("Claude Code integration: ready")
        print(f"Plugin: {_CLAUDE_PLUGIN_ID} {plugin.get('version', '')}".rstrip())
        print("Entry point: ask Claude Code to use LoreLoop in a new session")
        return 0

    if args.action == "install":
        marketplace = _claude_marketplace()
        if marketplace is None:
            _claude_command("plugin", "marketplace", "add", args.source, "--scope", "user")
            print(f"Added Claude Code marketplace: {_CLAUDE_MARKETPLACE}")
        else:
            print(
                f"Using existing Claude Code marketplace: {_CLAUDE_MARKETPLACE} (source preserved)"
            )
        _claude_command("plugin", "install", _CLAUDE_PLUGIN_ID, "--scope", "user")
        print(f"Installed Claude Code plugin: {_CLAUDE_PLUGIN_ID}")
        print("Next: start a new Claude Code session and ask it to use LoreLoop.")
        return 0

    if args.action == "uninstall":
        marketplace = _claude_marketplace()
        if marketplace is None:
            print("Claude Code integration: already removed")
            return 0
        plugin = _claude_plugin()
        if plugin is not None:
            _claude_command("plugin", "uninstall", _CLAUDE_PLUGIN_ID, "--scope", "user")
            print(f"Removed Claude Code plugin: {_CLAUDE_PLUGIN_ID}")
        if args.remove_marketplace:
            _claude_command("plugin", "marketplace", "remove", _CLAUDE_MARKETPLACE)
            print(f"Removed Claude Code marketplace: {_CLAUDE_MARKETPLACE}")
        else:
            print("Marketplace preserved; pass --remove-marketplace to remove it too.")
        return 0

    raise CLIError(
        "unsupported Claude Code integration action",
        f"unknown Claude Code action: {args.action}",
        "run `loreloop claude --help`",
    )


def _comind_command(*argv: str) -> str:
    import shutil
    import subprocess

    executable = shutil.which("co-mind")
    if executable is None:
        raise CLIError(
            "co-mind integration is unavailable",
            "the co-mind CLI is not installed or is not on PATH",
            "install co-mind, then rerun `loreloop comind install`",
        )
    try:
        result = subprocess.run(
            [executable, *argv],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise CLIError(
            "co-mind integration command failed",
            detail,
            "run `co-mind plugin list`, resolve the reported marketplace/plugin issue, then retry",
        ) from exc
    return result.stdout


def _comind_json(*argv: str) -> object:
    import json

    output = _comind_command(*argv)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CLIError(
            "co-mind integration returned invalid output",
            "the co-mind CLI did not return the expected JSON response",
            "upgrade co-mind, then rerun `loreloop comind status`",
        ) from exc


def _comind_marketplace() -> dict[str, object] | None:
    payload = _comind_json("plugin", "marketplace", "list", "--json")
    raw = payload if isinstance(payload, list) else None
    if raw is None and isinstance(payload, dict):
        value = payload.get("marketplaces")
        raw = value if isinstance(value, list) else None
    if raw is None:
        return None
    return next(
        (
            item
            for item in raw
            if isinstance(item, dict) and item.get("name") == _COMIND_MARKETPLACE
        ),
        None,
    )


def _comind_plugin() -> dict[str, object] | None:
    payload = _comind_json("plugin", "list", "--json")
    raw = payload if isinstance(payload, list) else None
    if raw is None and isinstance(payload, dict):
        value = payload.get("installed")
        raw = value if isinstance(value, list) else None
    if not isinstance(raw, list):
        return None
    return next(
        (
            item
            for item in raw
            if isinstance(item, dict)
            and (item.get("pluginId") == _COMIND_PLUGIN_ID or item.get("id") == _COMIND_PLUGIN_ID)
        ),
        None,
    )


def cmd_comind(args: argparse.Namespace) -> int:
    if args.action == "status":
        marketplace = _comind_marketplace()
        if marketplace is None:
            print("co-mind integration: not installed")
            print("Next: loreloop comind install")
            return 1
        plugin = _comind_plugin()
        if plugin is None or plugin.get("enabled") is False:
            print("co-mind integration: plugin not installed")
            print(f"Next: co-mind plugin install {_COMIND_PLUGIN_ID} --scope user")
            return 1
        print("co-mind integration: ready")
        print(f"Plugin: {_COMIND_PLUGIN_ID} {plugin.get('version', '')}".rstrip())
        print("Entry point: ask co-mind to use LoreLoop in a new session")
        return 0

    if args.action == "install":
        marketplace = _comind_marketplace()
        if marketplace is None:
            _comind_command("plugin", "marketplace", "add", args.source, "--scope", "user")
            print(f"Added co-mind marketplace: {_COMIND_MARKETPLACE}")
        else:
            print(f"Using existing co-mind marketplace: {_COMIND_MARKETPLACE} (source preserved)")
        _comind_command("plugin", "install", _COMIND_PLUGIN_ID, "--scope", "user")
        print(f"Installed co-mind plugin: {_COMIND_PLUGIN_ID}")
        print("Next: start a new co-mind session and ask it to use LoreLoop.")
        return 0

    if args.action == "uninstall":
        marketplace = _comind_marketplace()
        if marketplace is None:
            print("co-mind integration: already removed")
            return 0
        plugin = _comind_plugin()
        if plugin is not None:
            _comind_command("plugin", "uninstall", _COMIND_PLUGIN_ID, "--scope", "user")
            print(f"Removed co-mind plugin: {_COMIND_PLUGIN_ID}")
        if args.remove_marketplace:
            _comind_command("plugin", "marketplace", "remove", _COMIND_MARKETPLACE)
            print(f"Removed co-mind marketplace: {_COMIND_MARKETPLACE}")
        else:
            print("Marketplace preserved; pass --remove-marketplace to remove it too.")
        return 0

    raise CLIError(
        "unsupported co-mind integration action",
        f"unknown co-mind action: {args.action}",
        "run `loreloop comind --help`",
    )


def cmd_trust(args: argparse.Namespace) -> int:
    from .evidence.chain import TrustCredentialUnavailable, key_path_for

    workdir = _workdir()
    history_path = state_path(workdir, "evidence.jsonl")
    has_history = history_path.is_file() and bool(history_path.read_text(encoding="utf-8").strip())

    if args.action == "status":
        registered = load_trust_locations().get(str(workdir.resolve()))
        key_path = key_path_for(workdir)
        if not state_root(workdir).exists():
            print("Project trust: not initialized")
            print("Next: loreloop init --skill")
            return 0
        if has_history and not key_path.is_file():
            print("Project trust: unavailable on this machine")
            print(
                "This project has existing LoreLoop history, but its original local trust "
                "is not connected."
            )
            print("Next: loreloop trust recover --from <original-trust-directory>")
            return 1
        if not key_path.is_file():
            print("Project trust: ready to initialize")
            print("LoreLoop will prepare local trust automatically during `loreloop init`.")
            return 0
        try:
            EvidenceChain.for_workdir(workdir, create_key=False).verify()
        except ChainVerificationError as exc:
            if exc.index == 0 and exc.reason == "signature invalid":
                print("Project trust: connected to the wrong local trust")
                print("The selected local trust does not belong to this project's history.")
                print("Next: loreloop trust recover --from <original-trust-directory>")
                return 1
            raise
        source = (
            "environment override"
            if os.environ.get("LORELOOP_KEY_DIR")
            else "saved project registration"
            if registered is not None
            else "default local storage"
        )
        print("Project trust: ready")
        print(f"Connection: {source}")
        return 0

    if args.action == "recover":
        if not has_history:
            raise CLIError(
                "no project history to recover",
                "this project has no existing LoreLoop evidence history",
                "run `loreloop init --skill` to initialize it normally",
            )
        candidate_dir = args.source_dir.expanduser().resolve()
        try:
            EvidenceChain.for_workdir(
                workdir,
                create_key=False,
                key_dir=candidate_dir,
            ).verify()
        except TrustCredentialUnavailable as exc:
            raise CLIError(
                "trust recovery failed",
                f"no matching LoreLoop trust was found in {candidate_dir}",
                "select the original LoreLoop trust directory or restore its backup",
            ) from exc
        except ChainVerificationError as exc:
            if exc.index == 0 and exc.reason == "signature invalid":
                raise CLIError(
                    "trust recovery failed",
                    f"the local trust in {candidate_dir} does not match this project's history",
                    "select the original LoreLoop trust directory or restore its backup",
                ) from exc
            raise
        register_key_directory(workdir, candidate_dir)
        print("Project trust: recovered")
        print("The connection is saved for future LoreLoop and Codex sessions.")
        print("Next: loreloop doctor")
        return 0

    if args.action == "reset":
        if not args.confirm:
            raise CLIError(
                "explicit confirmation required",
                "reset archives this project's current LoreLoop state",
                "review recovery options first, then run `loreloop trust reset --confirm`",
            )
        root = state_root(workdir)
        if not root.exists():
            raise CLIError(
                "project trust is not initialized",
                "there is no .loreloop state to archive",
                "run `loreloop init --skill`",
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive = workdir / f".loreloop.archived-{stamp}"
        suffix = 1
        while archive.exists():
            archive = workdir / f".loreloop.archived-{stamp}-{suffix}"
            suffix += 1
        root.rename(archive)
        unregister_key_directory(workdir)
        print(f"Project trust archived: {archive.name}")
        print("No operator-owned local trust material was deleted.")
        print("Next: loreloop init --skill")
        return 0

    raise CLIError(
        "unsupported trust action",
        f"unknown trust action: {args.action}",
        "run `loreloop trust --help`",
    )


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import DemoError, run_demo

    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="loreloop-demo-"))
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        run_demo(workspace.resolve(), agent=args.agent, offline=args.offline)
    except DemoError as exc:
        raise CLIError(
            "demo did not complete",
            str(exc),
            "follow the failed step's recovery message, then rerun with a new --workspace",
        ) from exc
    return 0


def _probe_writable_directory(path: Path, *, create: bool = False) -> tuple[bool, str]:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return False, "path is not a directory"
        with tempfile.NamedTemporaryFile(prefix=".loreloop-write-", dir=path):
            pass
    except OSError as exc:
        return False, str(exc)
    return True, "writable"


def cmd_ingest(args: argparse.Namespace) -> int:
    workdir = _workdir()
    code_policy = None
    web_capture = None
    if args.source == "code":
        repo_name, repo = _resolve_ingest_repo(workdir, args.target)
        try:
            code_policy = IngestionPolicy(
                include=tuple(args.include),
                exclude=tuple(args.exclude),
                max_file_bytes=args.max_file_bytes,
            )
        except ValueError as exc:
            raise CLIError(
                "invalid ingestion policy",
                str(exc),
                "fix --include/--exclude patterns or set --max-file-bytes to a positive integer",
            ) from exc
        dirty = dirty_source_files(repo, policy=code_policy)
        if dirty:
            raise CLIError(
                "code source is not clean",
                "uncommitted source files cannot be anchored to Git HEAD: " + ", ".join(dirty[:10]),
                "commit or discard those source changes, then rerun `loreloop ingest`",
            )
        try:
            manifest = scan_repo_manifest(repo, policy=code_policy)
        except ValueError as exc:
            raise CLIError(
                "invalid ingestion limits",
                str(exc),
                "set `--max-file-bytes` to a positive integer",
            ) from exc
        strict_skips = {
            reason: paths for reason, paths in manifest.skipped.items() if reason != "excluded"
        }
        if args.strict and strict_skips:
            detail = ", ".join(
                f"{reason}={len(paths)}" for reason, paths in sorted(strict_skips.items())
            )
            raise CLIError(
                "code ingestion coverage is incomplete",
                detail,
                "add explicit --include/--exclude rules or adjust --max-file-bytes, then retry",
            )
        entries = reverse_code(
            _inference_agent(args.agent),
            repo,
            files=manifest.files,
            repo_name=repo_name,
            on_progress=_report_code_ingestion_progress,
        )
        skipped = ", ".join(
            f"{reason}={len(paths)}" for reason, paths in sorted(manifest.skipped.items())
        )
        print(
            f"ingestion manifest: tracked={manifest.tracked}, scanned={len(manifest.files)}, "
            f"skipped={manifest.skipped_count}" + (f" ({skipped})" if skipped else ""),
            file=sys.stderr,
        )
    else:
        from .evidence.artifacts import ArtifactStore
        from .webexplore.browser import PlaywrightBrowser
        from .webexplore.explorer import Explorer
        from .webexplore.scenarios import WEB_EXPLORATION_EVENT
        from .webexplore.web_reverse import reverse_web

        browser = PlaywrightBrowser(headed=args.headed)
        on_login_wall = "handover" if args.headed else "skip"
        try:
            explorer = Explorer(
                browser, workdir, max_pages=args.max_pages, on_login_wall=on_login_wall
            )
            result = explorer.explore(args.target)
            print(
                f"explored {len(result.pages)} pages "
                f"({len(result.skipped)} skipped), trace at {result.trace_path}",
                file=sys.stderr,
            )
            if result.login_walls and not args.headed:
                print(
                    f"skipped {len(result.login_walls)} login-walled page(s); "
                    f"re-run with --headed to sign in yourself",
                    file=sys.stderr,
                )
            if result.login_resumed:
                print(
                    f"resumed {len(result.login_resumed)} login handover(s) and continued "
                    "from the authenticated page",
                    file=sys.stderr,
                )
            abandoned = len(result.login_walls) - len(result.login_resumed)
            if args.headed and abandoned:
                print(
                    f"could not resume {abandoned} login handover(s); inspect {result.trace_path}",
                    file=sys.stderr,
                )
            entries = reverse_web(_inference_agent(args.agent), result.pages)
            artifacts = ArtifactStore.for_workdir(workdir)
            pages = []
            for observation in result.pages:
                artifact = artifacts.save_observation(observation)[0]
                pages.append(
                    {
                        "url": observation.url,
                        "title": observation.title,
                        "snapshot": observation.snapshot_hash,
                        "artifact": artifact,
                    }
                )
            web_capture = (
                WEB_EXPLORATION_EVENT,
                {
                    "start_url": args.target,
                    "trace": str(result.trace_path.relative_to(state_root(workdir))),
                    "pages": pages,
                },
            )
        finally:
            browser.close()
    refreshed = 0
    chain = EvidenceChain.for_workdir(workdir)
    if code_policy is not None:
        record_ingestion_policy(chain, repo_name, code_policy)
    if web_capture is not None:
        event, payload = web_capture
        chain.append(event, payload)
    with _store(workdir) as store:
        for entry in entries:
            stored, was_refreshed = store.plan_add_or_refresh(entry)
            if was_refreshed:
                record_reingested(chain, stored)
                store.apply_refresh(stored)
                refreshed += 1
            else:
                store.add(stored)
    suffix = f" ({refreshed} source anchor(s) refreshed)" if refreshed else ""
    print(f"ingested {len(entries)} knowledge entries from {args.target}{suffix}")
    print("Next: loreloop knowledge review --status draft")
    return 0


def _report_code_ingestion_progress(progress: CodeIngestionProgress) -> None:
    if progress.stage == "extract":
        count = progress.file_count
        unit = "file" if count == 1 else "files"
        detail = f"extracting {count} {unit}"
    else:
        count = progress.assertion_count
        if count is None:
            raise ValueError("classification progress requires an assertion count")
        unit = "assertion" if count == 1 else "assertions"
        detail = f"classifying {count} {unit}"
    print(
        f"code ingestion: batch {progress.batch_index}/{progress.batch_total}, {detail}",
        file=sys.stderr,
        flush=True,
    )


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
        f"code source {resolved} is not a declared repository; run `loreloop repo add <path>` first"
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
        repo = Path(args.repo_path).expanduser().resolve()
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
        rows = [(".", workdir.resolve()), *repos.items()]
        for name, repo in rows:
            reachable = repo.is_dir() and (repo / ".git").exists()
            print(
                f"{name}\t{repo}\t{_git_head_short(repo) if reachable else '-'}\t"
                f"{'reachable' if reachable else 'unreachable'}"
            )
        return 0
    name = args.repo_name
    if name == ".":
        raise RepoConfigError("the implicit '.' repository cannot be removed")
    if name not in repos:
        raise RepoConfigError(f"repository {name!r} is not declared")
    count = 0
    db = state_path(workdir, "knowledge.db")
    if db.exists():
        with KnowledgeStore.open_readonly(db) as store:
            for entry in store.list(channel=Channel.CODE):
                repo_name, _, _ = parse_code_locator(entry.source.locator)
                count += repo_name == name
    repos.pop(name)
    save_repos(workdir, repos)
    print(
        f"removed repository {name}; {count} anchored entr"
        f"{'y' if count == 1 else 'ies'} will display as stale until it is declared again"
    )
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
        project = add_project(
            Path(args.project_path),
            project_id=args.project_id,
            name=args.name,
            aliases=args.alias,
            tags=args.tag,
        )
        print(f"registered project {project.project_id}: {project.path}")
        return 0
    if args.action == "list":
        for project in list_projects():
            available = state_path(project.path, "knowledge.db").is_file()
            print(
                f"{project.project_id}\t{project.name}\t{project.path}\t"
                f"{'available' if available else 'unavailable'}"
            )
        return 0
    removed = remove_project(args.registry_project_id)
    print(f"removed project {removed.project_id}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .evidence.artifacts import ArtifactStore
    from .webexplore.scenarios import (
        WebScenarioError,
        approve_candidate,
        approved_scenario,
        export_playwright,
        generate_latest_candidates,
        list_approved_scenarios,
        list_candidate_scenarios,
        run_scenario,
        scenario_locator,
        write_candidate,
    )

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    if args.web_test_action == "generate":
        paths = generate_latest_candidates(workdir, chain, artifacts)
        for path in paths:
            print(f"candidate: {path.relative_to(workdir)}")
        print(f"generated {len(paths)} candidate Web scenario(s)")
        print("Next: loreloop web test review")
        return 0
    if args.web_test_action == "review":
        candidates = list_candidate_scenarios(workdir)
        approved = list_approved_scenarios(workdir)
        for _, scenario in candidates:
            print(
                f"candidate  {scenario.scenario_id}  [{scenario.risk}]  "
                f"{scenario.title}  ({len(scenario.script.steps)} steps, "
                f"{len(scenario.assertions)} assertions)"
            )
        for _, scenario in approved:
            print(f"approved   {scenario.scenario_id}  [{scenario.risk}]  {scenario.title}")
        if not candidates and not approved:
            print("no Web scenarios found")
        return 0
    if args.web_test_action == "approve":
        path, scenario, _ = approve_candidate(
            workdir,
            args.scenario_id,
            chain,
            repository_alias=args.repo,
        )
        locator = scenario_locator(workdir, path)
        print(f"approved: {scenario.title}  ({scenario.scenario_id})")
        print(f"published: {locator}")
        repository = path.parents[3]
        relative = path.relative_to(repository)
        print(f"Next: git -C {repository} add {relative} && git -C {repository} commit")
        return 0
    if args.web_test_action == "record":
        from .webexplore.browser import PlaywrightBrowser
        from .webexplore.recorder import record_scenario

        browser = PlaywrightBrowser(headed=True)
        try:
            scenario = record_scenario(
                browser,
                artifacts,
                args.url,
                title=args.title,
                risk=args.risk,
                allow_writes=args.allow_writes,
            )
        finally:
            browser.close()
        path = write_candidate(workdir, scenario)
        print(f"recorded candidate: {path.relative_to(workdir)}")
        print("Next: loreloop web test review")
        return 0
    if args.web_test_action == "run":
        from .webexplore.browser import PlaywrightBrowser

        records = chain.verify()
        if args.all:
            selected = [
                approved_scenario(workdir, scenario.scenario_id, records)
                for _, scenario in list_approved_scenarios(workdir)
            ]
        elif args.scenario_id:
            selected = [approved_scenario(workdir, args.scenario_id, records)]
        else:
            raise WebScenarioError("choose a scenario id or --all")
        if not selected:
            raise WebScenarioError("no approved Web scenarios are available")
        browser = PlaywrightBrowser(headed=args.headed)
        failures = 0
        try:
            for _, scenario in selected:
                result = run_scenario(
                    scenario,
                    browser,
                    chain,
                    artifacts,
                    allow_writes=args.allow_writes,
                )
                label = "PASS" if result.passed else "FAIL"
                print(f"{label}: {scenario.scenario_id}  {scenario.title}")
                for assertion in result.assertions:
                    mark = "PASS" if assertion["passed"] else "FAIL"
                    print(f"  {mark} {assertion['kind']}: {assertion['value']}")
                failures += not result.passed
        finally:
            browser.close()
        print(f"{len(selected) - failures} passed, {failures} failed")
        return 0 if failures == 0 else 1
    if args.web_test_action == "export":
        records = chain.verify()
        selected = tuple(
            approved_scenario(workdir, scenario.scenario_id, records)
            for _, scenario in list_approved_scenarios(workdir)
        )
        if not selected:
            raise WebScenarioError("no approved Web scenarios are available")
        paths = export_playwright(selected, Path(args.output), force=args.force)
        for path in paths:
            print(f"exported: {path}")
        return 0
    raise WebScenarioError(f"unsupported Web test action: {args.web_test_action}")


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
        raise CLIError(
            "invalid expectation",
            str(exc),
            "use `contains:<text>`, `absent:<text>`, `title-contains:<text>`, "
            "or a non-empty free-form claim",
        ) from exc
    except ActionScriptError as exc:
        raise CLIError(
            "invalid action script",
            str(exc),
            "fix the JSON script, then rerun `loreloop verify --script ...`",
        ) from exc

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    browser = PlaywrightBrowser(headed=args.headed)
    try:
        if script is None:
            result = verify_expectation(
                browser,
                _inference_agent(args.agent),
                chain,
                args.run_id,
                args.url,
                args.expectation,
                artifacts=artifacts,
            )
        else:
            result = verify_script_expectation(
                browser,
                _inference_agent(args.agent),
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
    print(f"evidence: chain hash {result.record.chain_hash[:16]}, page snapshot {snapshot}")
    if not result.passed:
        print("Next: fix the observed behavior or expectation, then rerun this verification")
    return 0 if result.passed else 1


def cmd_run(args: argparse.Namespace) -> int:
    from .knowledge.model import Channel

    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    entries, endorsed, unendorsed = _active_delegation_entries(workdir, records)
    agent = _agent(args.agent)
    expansion = ""
    if (entries or args.with_related) and not args.no_expand:
        from .delegate.expand import ExpansionError, expand_query

        try:
            expansion = expand_query(
                _inference_agent(args.agent),
                args.task,
                cache_path=state_path(workdir, "cache", "query-expansion.json"),
            )
        except (ExpansionError, AgentError) as exc:
            print(
                f"[LoreLoop] query expansion failed ({exc}); retrieving with the task text only",
                file=sys.stderr,
            )
    runner = DelegateRunner(agent, workdir)
    related = (
        _select_related_entries(workdir, args.task, expansion, args.related_limit)
        if args.with_related
        else []
    )
    current_policies = chain_ingestion_policies(records)
    result = runner.run(
        args.task,
        entries,
        unendorsed_ids=unendorsed,
        endorsed_ids=endorsed,
        expansion=expansion,
        related=related,
        ingestion_policies=current_policies,
    )
    _print_context_pack_notes(result.pack, endorsed)
    # This chain record is the acceptance authority for the run: report and
    # harvest key off it, not off the agent-writable trace file.
    chain.append(
        "delegation_completed",
        _completion_payload(
            result.run_id,
            args.task,
            result.pack.entry_ids,
            result.base_commits,
            result.repository_roots,
            result.pack.related_ids,
            current_policies,
        ),
    )
    print(result.output)
    print(
        f"\n[LoreLoop] run {result.run_id}: injected {len(result.pack.entry_ids)} entries, "
        f"trace at {result.trace_path}",
        file=sys.stderr,
    )
    print(
        f"[LoreLoop] next: record live proof with `loreloop check {result.run_id} ...` "
        f"or `loreloop verify {result.run_id} ...`, then run "
        f"`loreloop report {result.run_id}`",
        file=sys.stderr,
    )
    strong_web = [e for e in result.pack.strong if e.source.channel is Channel.WEB]
    if strong_web:
        # Known limitation, documented in SECURITY.md: injection trusts the
        # last verification; it does not re-open a browser per run.
        print(
            f"[LoreLoop] note: {len(strong_web)} strong web entr"
            f"{'y was' if len(strong_web) == 1 else 'ies were'} injected as-is; "
            f"live pages may have changed since verification — "
            f"re-check with `loreloop knowledge verify`",
            file=sys.stderr,
        )
    return 0


def _active_delegation_entries(workdir: Path, records) -> tuple[list[Entry], set[str], set[str]]:
    with _store(workdir) as store:
        entries = store.list()
    # Retirement by chain replay, not DB state: DB-only rejected flags or
    # supersedes links live in the agent-writable tree and cannot suppress a
    # chain-backed fact. Conversely, a chain-rejected or chain-superseded entry
    # stays retired even if SQLite is edited back to active.
    retired = chain_superseded_ids(records) | chain_rejected_ids(records)
    assert_trust_projection(entries, records, retired_ids=retired)
    entries = [e for e in entries if e.id not in retired]
    # The DB sits in the agent-writable tree; its strong bits count only when
    # the chain endorses them FOR THE CURRENT CONTENT. Anything strong-in-DB
    # but unendorsed (no event, or content changed since endorsement) is
    # injected as reference and flagged for the operator.
    endorsed = chain_endorsed_strong_ids(entries, records)
    unendorsed = unendorsed_strong_ids(entries, records)
    if unendorsed:
        print(
            f"[LoreLoop] WARNING: {len(unendorsed)} entr{'y' if len(unendorsed) == 1 else 'ies'} "
            f"claim strong trust in the store without evidence-chain endorsement "
            f"of their current content — injected as reference only. "
            f"Inspect with `loreloop knowledge list`:",
            file=sys.stderr,
        )
        for e in entries:
            if e.id in unendorsed:
                print(f"    {e.id[:8]}  {e.title}", file=sys.stderr)
    return entries, endorsed, unendorsed


def _print_context_pack_notes(pack, endorsed: set[str]) -> None:
    chain_only = [e for e in pack.strong if e.id in endorsed and not e.is_strong_evidence()]
    if chain_only:
        print(
            f"[LoreLoop] note: {len(chain_only)} entr"
            f"{'y is' if len(chain_only) == 1 else 'ies are'} chain-endorsed "
            f"although the store cache says reference — injected as established fact.",
            file=sys.stderr,
        )


def _completion_payload(
    run_id: str,
    task: str,
    context_entries: list[str],
    base_commits: dict[str, str],
    repository_roots: dict[str, str],
    related_entries: list[str],
    ingestion_policies: dict[str, IngestionPolicy],
) -> dict:
    return {
        "run_id": run_id,
        "task": task,
        "context_entries": context_entries,
        "base_commits": base_commits,
        "repository_roots": repository_roots,
        "related_entries": related_entries,
        "ingestion_policies": ingestion_policies_payload(ingestion_policies, set(base_commits)),
    }


def cmd_begin(args: argparse.Namespace) -> int:
    """Prepare a signed run for the coding-agent session already in use."""
    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    entries, endorsed, unendorsed = _active_delegation_entries(workdir, records)
    related = (
        _select_related_entries(workdir, args.task, args.expand, args.related_limit)
        if args.with_related
        else []
    )
    current_policies = chain_ingestion_policies(records)
    from .knowledge.requirement_context import (
        load_requirement_materials,
        render_requirement_context,
    )

    requirement_materials = load_requirement_materials(workdir, tuple(args.requirements))
    prepared = DelegateRunner(None, workdir).prepare(
        args.task,
        entries,
        unendorsed_ids=unendorsed,
        endorsed_ids=endorsed,
        expansion=args.expand,
        related=related,
        ingestion_policies=current_policies,
        mode="session",
        requirement_context=render_requirement_context(requirement_materials),
        requirement_materials=[item.evidence_payload() for item in requirement_materials],
    )
    payload = _completion_payload(
        prepared.run_id,
        args.task,
        prepared.pack.entry_ids,
        prepared.base_commits,
        prepared.repository_roots,
        prepared.pack.related_ids,
        current_policies,
    )
    payload["mode"] = "session"
    payload["requirement_materials"] = [item.evidence_payload() for item in requirement_materials]
    chain.append("delegation_prepared", payload)
    _print_context_pack_notes(prepared.pack, endorsed)
    print("# LoreLoop current-session run")
    print()
    print(f"Run ID: {prepared.run_id}")
    print()
    print(prepared.prompt)
    print(
        f"[LoreLoop] current session prepared with {len(prepared.pack.entry_ids)} entries. "
        f"After implementation, ask the operator before running "
        f"`loreloop complete {prepared.run_id} --confirm`.",
        file=sys.stderr,
    )
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """End a prepared current-session run without trusting its writable trace."""
    if not args.confirm:
        raise CLIError(
            "operator confirmation required",
            "current-session completion signs the task boundary into the evidence chain",
            f"ask the operator to confirm completion, then run "
            f"`loreloop complete {args.run_id} --confirm`",
        )
    workdir = _workdir()
    trace = _run_trace(workdir, args.run_id)
    if not trace.exists():
        raise CLIError(
            "run trace not found",
            f"no trace found for {args.run_id}",
            "copy the exact id printed by `loreloop begin`, then retry completion",
        )
    # Validate the display trace, but never use its task/context/base values as
    # completion authority: the coding agent can write everything in-tree.
    load_run(trace)
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    preparations = [
        record
        for record in records
        if record.event == "delegation_prepared" and record.payload.get("run_id") == args.run_id
    ]
    if len(preparations) != 1:
        raise CLIError(
            "prepared session not found" if not preparations else "ambiguous prepared session",
            f"expected exactly one signed preparation for {args.run_id}; found {len(preparations)}",
            "start a fresh current-session run with `loreloop begin <task>`",
        )
    completions = [
        record
        for record in records
        if record.event == "delegation_completed" and record.payload.get("run_id") == args.run_id
    ]
    if completions:
        raise CLIError(
            "run already complete",
            f"{args.run_id} already has a signed completion record",
            f"record acceptance evidence, then run `loreloop report {args.run_id}`",
            exit_code=1,
        )
    prepared = preparations[0]
    payload = dict(prepared.payload)
    _validate_session_preparation(payload, args.run_id)
    payload["prepared_chain_hash"] = prepared.chain_hash
    DelegateRunner(None, workdir).finish(trace, mode="session")
    chain.append("delegation_completed", payload)
    print(f"completed current-session run {args.run_id}")
    print(f"  injected knowledge entries: {len(payload.get('context_entries', []))}")
    print(
        f"Next: record evidence with `loreloop check {args.run_id} ...` or "
        f"`loreloop verify {args.run_id} ...`, then run `loreloop report {args.run_id}`"
    )
    return 0


def _validate_session_preparation(payload: dict, run_id: str) -> None:
    context = payload.get("context_entries")
    base_commits = payload.get("base_commits")
    roots = payload.get("repository_roots")
    related = payload.get("related_entries")
    policies = payload.get("ingestion_policies")
    requirements = payload.get("requirement_materials", [])
    valid = (
        payload.get("run_id") == run_id
        and payload.get("mode") == "session"
        and isinstance(payload.get("task"), str)
        and bool(payload["task"].strip())
        and isinstance(context, list)
        and all(isinstance(value, str) for value in context)
        and isinstance(base_commits, dict)
        and all(
            isinstance(name, str) and isinstance(commit, str) and bool(commit)
            for name, commit in base_commits.items()
        )
        and isinstance(roots, dict)
        and set(roots) == set(base_commits)
        and all(isinstance(value, str) and bool(value) for value in roots.values())
        and isinstance(related, list)
        and all(isinstance(value, str) for value in related)
        and isinstance(policies, dict)
        and set(policies) == set(base_commits)
        and isinstance(requirements, list)
        and all(
            isinstance(item, dict)
            and set(item) == {"locator", "commit", "sha256"}
            and all(isinstance(value, str) and bool(value) for value in item.values())
            for item in requirements
        )
    )
    if not valid:
        raise CLIError(
            "invalid prepared session",
            f"the signed preparation metadata for {run_id} is incomplete or malformed",
            "start a fresh current-session run with `loreloop begin <task>`",
        )


def _select_related_entries(
    workdir: Path, task: str, expansion: str, limit: int
) -> list[ForeignEntry]:
    from .federation.reader import read_project
    from .federation.registry import RegistryError, load_projects, related_projects

    if limit < 1:
        raise RegistryError("related limit must be at least 1")
    projects = load_projects()
    overlap = dict(related_projects(workdir))
    foreign: list[ForeignEntry] = []
    seen_paths: set[Path] = set()
    for project_id, _ in sorted(overlap.items(), key=lambda item: (-item[1], item[0])):
        project = projects[project_id]
        if project.path == workdir.resolve() or project.path in seen_paths:
            continue
        seen_paths.add(project.path)
        entries, warnings = read_project(project_id, project.path)
        for warning in warnings:
            print(f"warning [{warning.project_id}]: {warning.message}", file=sys.stderr)
        foreign.extend(entries)
    ranked = _rank_foreign_entries(
        task,
        foreign,
        limit=max(len(foreign), limit),
        expansion=expansion,
    )
    candidates = [(overlap[item.project_id], score, item) for score, item in ranked]
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2].project_id, item[2].entry.id))
    return [item for _, _, item in candidates[:limit]]


def cmd_check(args: argparse.Namespace) -> int:
    from .report.acceptance import validate_check_text

    try:
        args.check = validate_check_text(args.check)
    except ValueError as exc:
        raise CLIError(
            "invalid acceptance check",
            str(exc),
            "provide a concise, non-empty assertion and retry",
        ) from exc
    workdir = _workdir()
    chain = EvidenceChain.for_workdir(workdir)
    if args.command:
        from .evidence.artifacts import ArtifactStore
        from .report.acceptance import record_command_check

        try:
            argv = shlex.split(args.command)
        except ValueError as exc:
            raise CLIError(
                "invalid check command",
                str(exc),
                "quote each argument correctly and rerun `loreloop check --command ...`",
            ) from exc
        shell_tokens = {";", "|", "||", "&&", ">", ">>", "<", "2>", "2>>"}
        if any(part in shell_tokens for part in argv):
            raise CLIError(
                "unsafe check command",
                "shell operators are not supported in --command",
                "invoke one executable with explicit arguments and no shell operators",
            )
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
    if rec.event == "check_failed":
        print("Next: inspect the pinned command artifact, fix the failure, and record a new check")
    return 0 if rec.event == "check_passed" else 1


def cmd_report(args: argparse.Namespace) -> int:
    workdir = _workdir()
    runs_dir = state_path(workdir, "runs")
    if args.run_id:
        trace = _run_trace(workdir, args.run_id)
        if not trace.exists():
            raise CLIError(
                "run trace not found",
                f"no trace found for {args.run_id}",
                "copy the exact id printed by `loreloop begin` or `loreloop run`, "
                "or omit RUN_ID to use the latest run",
            )
    else:
        traces = sorted(runs_dir.glob("run-*.jsonl")) if runs_dir.exists() else []
        if not traces:
            raise CLIError(
                "no runs found",
                "this project has no delegation trace to report",
                "run `loreloop begin <task>` in the current agent session, or "
                "`loreloop run <task>` for headless delegation",
            )
        trace = traces[-1]
    from .evidence.artifacts import ArtifactStore

    run = load_run(trace)
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    evaluation = evaluate_run(run, chain, artifacts)
    report = render_report(run, chain, artifacts=artifacts)
    print(report)
    if evaluation.accepted:
        print(f"Next: loreloop harvest {trace.stem}")
    else:
        print(f"Next: satisfy the missing checks, then rerun `loreloop report {trace.stem}`")
    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    from .evidence.artifacts import ArtifactStore
    from .knowledge.harvest import HarvestAlreadyCompleted, HarvestError, harvest_run

    workdir = _workdir()
    trace = _run_trace(workdir, args.run_id)
    if not trace.exists():
        raise CLIError(
            "run trace not found",
            f"no trace found for {args.run_id}",
            "copy the exact id printed by `loreloop begin` or `loreloop run`, then retry harvest",
        )
    run = load_run(trace)
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    with _store(workdir) as store:
        try:
            result = harvest_run(
                run, chain, store, _inference_agent(args.agent), workdir, artifacts=artifacts
            )
        except HarvestAlreadyCompleted as exc:
            raise CLIError(
                "harvest already complete",
                str(exc),
                "no acceptance work is required; review the current knowledge entries instead",
                exit_code=1,
            ) from exc
        except HarvestError as exc:
            raise CLIError(
                "harvest refused",
                str(exc),
                "run `loreloop report <run-id>`, satisfy every acceptance check, then retry",
                exit_code=1,
            ) from exc
    print(f"{'resumed' if result.resumed else 'harvested'} run {args.run_id}:")
    print(f"  {len(result.minted)} verified acceptance assertions minted")
    print(f"  {len(result.reversed_entries)} draft entries reversed from changed code")
    if result.unauditable_checks:
        print(
            f"  {len(result.unauditable_checks)} browser check(s) had no evidence "
            f"artifact and were NOT minted:",
            file=sys.stderr,
        )
        for check in result.unauditable_checks:
            print(f"    {check}", file=sys.stderr)
    if result.stale:
        print(
            f"  {len(result.stale)} existing entries anchored before this run "
            f"touch changed files — review with `loreloop knowledge review --stale`:"
        )
        for entry in result.stale:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})")
    if result.review:
        print(
            f"  {len(result.review)} existing strong entries cover pages verified in "
            f"this run — check they still hold, supersede if not:"
        )
        for entry in result.review:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})")
    if result.demoted:
        print(
            f"  {len(result.demoted)} strong entr"
            f"{'y was' if len(result.demoted) == 1 else 'ies were'} re-anchored and "
            f"lost chain endorsement — they inject as reference until you "
            f"re-approve (`loreloop knowledge approve`):",
            file=sys.stderr,
        )
        for entry in result.demoted:
            print(f"    {entry.id[:8]}  {entry.title}  ({entry.source.locator})", file=sys.stderr)
    print("Next: loreloop knowledge review --status draft")
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    workdir = _workdir()
    if args.action == "export" and args.format == "audit" and args.working_tree:
        raise CLIError(
            "--working-tree requires project documents",
            "the audit export reads governed knowledge entries rather than a source snapshot",
            "use `--format docs` or `--format package`, or remove `--working-tree`",
        )
    if args.action == "export" and args.format == "audit" and args.include_web:
        raise CLIError(
            "--include-web requires a project package",
            "the audit export already lists knowledge entries directly",
            "use `--format package` or remove `--include-web`",
        )
    if args.action == "export" and args.format in {"package", "docs"}:
        return _export_document_set(args, workdir)
    if args.action == "replay":
        return _replay_document_set(args, workdir)
    if args.action == "search" and args.package:
        return _search_baseline_package(args)
    with _store(workdir) as store:
        if args.action == "list":
            return _list_entries(args, workdir, store)
        elif args.action == "show":
            return _show_entry(args, workdir, store)
        elif args.action == "review":
            return _review_entries(args, workdir, store)
        elif args.action == "search":
            return _search_entries(args, workdir, store)
        elif args.action == "import":
            return _import_entry(args, store)
        elif args.action == "export":
            return _export_entries(args, workdir, store)
        elif args.action in ("approve", "reject", "reopen"):
            return _curate(args, workdir, store)
        elif args.action == "supersede":
            return _supersede(args, workdir, store)
        elif args.action == "unsupersede":
            return _unsupersede(args, workdir, store)
        elif args.action == "verify":
            return _verify_entries(args, workdir, store)
        elif args.action == "usage":
            return _knowledge_usage(workdir, store)


def _search_baseline_package(args: argparse.Namespace) -> int:
    from .knowledge.authoritative_search import BaselineSearchError, search_baseline

    if args.tag:
        raise CLIError(
            "invalid baseline search filters",
            "--tag applies to registered projects and cannot be combined with --package",
            "remove --tag and retry the package search",
        )
    print("verifying baseline and building a transient search index...", file=sys.stderr)
    try:
        hits = search_baseline(
            Path(args.package),
            args.query,
            limit=args.limit,
            expansion=args.expand,
        )
    except BaselineSearchError as exc:
        raise CLIError(
            "baseline search failed",
            str(exc),
            "restore or regenerate the baseline package, then retry the search",
        ) from exc
    for hit in hits:
        print(f"{hit.score:.3f}  {hit.filename}#{hit.heading}")
        print(f"       {hit.snippet}")
    if not hits:
        print("no matching baseline records")
    elif all(hit.expanded_only for hit in hits):
        print(
            "[LoreLoop] note: these low-confidence matches depend on --expand; "
            "the expansion changed candidate selection but is not project knowledge",
            file=sys.stderr,
        )
    return 0


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
    print(
        "\nAccepted means the run was later harvested after evidence-backed acceptance; "
        "it is correlation, not proof that one entry caused success."
    )
    return 0


def _search_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .federation.reader import grade_local_entries, read_project
    from .federation.registry import load_projects

    if args.limit < 1:
        from .federation.registry import RegistryError

        raise RegistryError("search limit must be at least 1")

    query = args.query
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
            grade_local_entries(
                ".", local_entries, records, _drifted_entries(workdir, local_entries, records)
            )
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

    ranked = _rank_foreign_entries(
        query,
        [item for group in groups for item in group],
        limit=max(args.limit * 2, args.limit),
        expansion=args.expand,
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


def _rank_foreign_entries(
    query: str,
    items: list[ForeignEntry],
    *,
    limit: int,
    expansion: str = "",
) -> list[tuple[float, ForeignEntry]]:
    """Rank every project in one corpus so BM25 scores are comparable."""
    from dataclasses import replace

    from .delegate.context_pack import rank_entries

    prepared = []
    by_synthetic_id: dict[str, ForeignEntry] = {}
    drifted: set[str] = set()
    endorsed: set[str] = set()
    for index, item in enumerate(items):
        synthetic_id = f"federated-{index}-{item.entry.id}"
        prepared.append(replace(item.entry, id=synthetic_id))
        by_synthetic_id[synthetic_id] = item
        if item.drifted_there:
            drifted.add(synthetic_id)
        if item.strong_there:
            endorsed.add(synthetic_id)
    ranked = rank_entries(
        query,
        prepared,
        limit=limit,
        drifted_ids=drifted,
        endorsed_ids=endorsed,
        expansion=expansion,
    )
    return [(row.adjusted_score, by_synthetic_id[row.entry.id]) for row in ranked]


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

    projects = load_projects()
    project = projects.get(args.project_id)
    if project is None:
        raise RegistryError(f"project {args.project_id!r} is not registered")
    entries, warnings = read_project(project.project_id, project.path)
    for warning in warnings:
        print(f"warning [{warning.project_id}]: {warning.message}", file=sys.stderr)
    matches = [item for item in entries if item.entry.id.startswith(args.entry_id)]
    if len(matches) != 1:
        reason = "no entry matches" if not matches else f"{len(matches)} entries match"
        raise RegistryError(f"{reason} id prefix {args.entry_id!r} in project {project.project_id}")
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
    from .knowledge.store import InvalidTransition

    new = {
        "approve": Curation.APPROVED,
        "reject": Curation.REJECTED,
        "reopen": Curation.DRAFT,
    }[args.action]
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    target = (
        _resolve_or_restore_rejected_entry(store, records, args.entry_id)
        if args.action == "reopen"
        else _resolve_entry(store, args.entry_id)
    )
    try:
        entry = curate(store, chain, target.id, new, datetime.now(timezone.utc))
    except InvalidTransition as exc:
        raise CLIError(
            "invalid curation transition",
            str(exc),
            "inspect the entry with `loreloop knowledge list` and choose a valid next state",
        ) from exc
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
    effective_curation = chain_effective_curation(records)
    contradicted = chain_contradicted_ids(records)
    assert_trust_projection(entries, records, retired_ids=superseded | rejected)
    endorsed = chain_endorsed_strong_ids(entries, records)
    unendorsed = unendorsed_strong_ids(entries, records)
    drifted = _drifted_entries(workdir, entries, records)
    entries = _filter_knowledge_entries(
        entries,
        args,
        drifted,
        superseded | rejected,
        effective_curation,
    )
    if args.stale:
        entries = [e for e in entries if e.id in drifted and e.id not in superseded]
        if not entries:
            print("no stale entries: every code anchor matches the current tree")
            return 0
    entries = entries[args.offset : args.offset + args.limit]
    for e in entries:
        demoted = (
            e.id in unendorsed
            or e.id in rejected
            or e.id in contradicted
            or e.id in drifted
            or e.id in superseded
        )
        chain_backed = e.id in endorsed
        strong = "strong" if (e.is_strong_evidence() or chain_backed) and not demoted else "ref"
        flags = ""
        if e.id in unendorsed:
            flags += "  [unendorsed: strong bit has no chain endorsement]"
        if chain_backed and not e.is_strong_evidence() and not demoted:
            flags += "  [chain-backed: store cache says reference]"
        if e.id in rejected:
            flags += "  [rejected]"
        if e.id in contradicted:
            flags += "  [contradicted]"
        if e.id in superseded:
            flags += "  [superseded]"
        if e.id in drifted:
            flags += "  [stale: source changed since capture]"
        effective = effective_curation.get(e.id, Curation.DRAFT)
        if e.trust.curation is not effective:
            flags += (
                f"  [curation cache mismatch: stored={e.trust.curation.value}, "
                f"effective={effective.value}]"
            )
        print(f"{e.id[:8]}  [{e.kind.value:<12}] [{strong:<6}] {e.title}{flags}")
    return 0


def _filter_knowledge_entries(
    entries: list[Entry],
    args: argparse.Namespace,
    drifted: set[str],
    retired: set[str],
    effective_curation: dict[str, Curation],
) -> list[Entry]:
    if args.status:
        entries = [
            entry
            for entry in entries
            if effective_curation.get(entry.id, Curation.DRAFT).value == args.status
        ]
    if args.channel:
        entries = [entry for entry in entries if entry.source.channel.value == args.channel]
    if args.kind:
        entries = [entry for entry in entries if entry.kind.value == args.kind]
    if getattr(args, "stale", False):
        entries = [entry for entry in entries if entry.id in drifted]
    if getattr(args, "active", False):
        entries = [entry for entry in entries if entry.id not in retired]
    if args.limit < 1:
        raise CLIError("invalid pagination", "--limit must be at least 1", "use a positive limit")
    if args.offset < 0:
        raise CLIError("invalid pagination", "--offset cannot be negative", "use zero or greater")
    return entries


def _knowledge_state(workdir: Path, store: KnowledgeStore):
    entries = store.list()
    records = EvidenceChain.for_workdir(workdir).verify()
    superseded = chain_superseded_ids(records)
    rejected = chain_rejected_ids(records)
    contradicted = chain_contradicted_ids(records)
    assert_trust_projection(entries, records, retired_ids=superseded | rejected)
    return {
        "entries": entries,
        "records": records,
        "superseded": superseded,
        "rejected": rejected,
        "contradicted": contradicted,
        "curation": chain_effective_curation(records),
        "endorsed": chain_endorsed_strong_ids(entries, records),
        "unendorsed": unendorsed_strong_ids(entries, records),
        "drifted": _drifted_entries(workdir, entries, records),
        "links": chain_supersession_links(records),
    }


def _show_entry(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    entry = _resolve_entry(store, args.entry_id)
    state = _knowledge_state(workdir, store)
    _print_entry_details(entry, state)
    return 0


def _review_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    state = _knowledge_state(workdir, store)
    entries = state["entries"]
    if not args.status and not args.stale:
        args.status = Curation.DRAFT.value
    entries = _filter_knowledge_entries(
        entries,
        args,
        state["drifted"],
        state["superseded"] | state["rejected"],
        state["curation"],
    )
    entries = entries[args.offset : args.offset + args.limit]
    if not entries:
        print("no knowledge entries require review for the selected filters")
        return 0
    for index, entry in enumerate(entries):
        if index:
            print("\n" + "-" * 72)
        _print_entry_details(entry, state)
        if entry.id in state["drifted"]:
            print(
                "Next: inspect new drafts with `loreloop knowledge review --status draft`, "
                f"then run `loreloop knowledge supersede <new-id> {entry.id[:8]} --yes` "
                "or reject it with "
                f"`loreloop knowledge reject {entry.id[:8]}`"
            )
        elif entry.id not in state["superseded"] and entry.id not in state["rejected"]:
            print(
                "Next: "
                f"loreloop knowledge approve {entry.id[:8]}  |  "
                f"loreloop knowledge reject {entry.id[:8]}"
            )
    return 0


def _print_entry_details(entry: Entry, state: dict) -> None:
    demoted = (
        entry.id in state["unendorsed"]
        or entry.id in state["rejected"]
        or entry.id in state["contradicted"]
        or entry.id in state["drifted"]
        or entry.id in state["superseded"]
    )
    chain_backed = entry.id in state["endorsed"]
    strength = (
        "strong" if (entry.is_strong_evidence() or chain_backed) and not demoted else "reference"
    )
    by_id = {item.id: item for item in state["entries"]}
    flags = [
        name
        for name, present in (
            ("unendorsed", entry.id in state["unendorsed"]),
            ("rejected", entry.id in state["rejected"]),
            ("contradicted", entry.id in state["contradicted"]),
            ("superseded", entry.id in state["superseded"]),
            ("stale", entry.id in state["drifted"]),
        )
        if present
    ]
    print(f"ID: {entry.id}")
    print(f"Title: {entry.title}")
    print(f"Kind: {entry.kind.value}")
    print(f"Content: {entry.content}")
    print(f"Effective trust: {strength}" + (f" ({', '.join(flags)})" if flags else ""))
    effective_curation = state["curation"].get(entry.id, Curation.DRAFT)
    print(f"Effective curation: {effective_curation.value}")
    stored_curation = entry.trust.curation.value
    mismatch = " (cache mismatch)" if entry.trust.curation is not effective_curation else ""
    print(f"Stored curation: {stored_curation}{mismatch}")
    print(f"Verification: {entry.trust.verification.value}")
    print(f"Verified at: {entry.trust.verified_at.isoformat() if entry.trust.verified_at else '-'}")
    print(f"Verified by: {entry.trust.verified_by or '-'}")
    print(f"Source channel: {entry.source.channel.value}")
    print(f"Source locator: {entry.source.locator}")
    print(f"Source snapshot: {entry.source.snapshot_ref or '-'}")
    print(f"Source symbol: {entry.source.symbol or '-'}")
    line_range = (
        f"{entry.source.line_start}-{entry.source.line_end}"
        if entry.source.line_start is not None
        else "-"
    )
    print(f"Source lines: {line_range}")
    print(f"Source excerpt: {entry.source.excerpt or '-'}")
    print(f"Created at: {entry.created_at.isoformat()}")
    print(f"Updated at: {entry.updated_at.isoformat()}")
    relations = []
    for new_id, old_id in sorted(state["links"]):
        if new_id == entry.id:
            target = by_id.get(old_id)
            relations.append(f"supersedes {old_id[:8]} ({target.title if target else 'missing'})")
        elif old_id == entry.id:
            source = by_id.get(new_id)
            relations.append(
                f"superseded by {new_id[:8]} ({source.title if source else 'missing'})"
            )
    print(f"Relations: {', '.join(relations) if relations else '-'}")


def _export_entries(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    entries = store.list()
    records = EvidenceChain.for_workdir(workdir).verify()
    superseded = chain_superseded_ids(records)
    rejected = chain_rejected_ids(records)
    contradicted = chain_contradicted_ids(records)
    assert_trust_projection(entries, records, retired_ids=superseded | rejected)
    endorsed = chain_endorsed_strong_ids(entries, records)
    unendorsed = unendorsed_strong_ids(entries, records)
    drifted = _drifted_entries(workdir, entries, records)
    effective_curation = chain_effective_curation(records)
    if args.stale:
        entries = [e for e in entries if e.id in drifted and e.id not in superseded]

    lines = [
        "# loreloop knowledge export",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Entries: {len(entries)}",
        "",
    ]
    if not entries:
        lines.append("_No entries._")
    for entry in entries:
        demoted = (
            entry.id in unendorsed
            or entry.id in rejected
            or entry.id in contradicted
            or entry.id in drifted
            or entry.id in superseded
        )
        chain_backed = entry.id in endorsed
        strength = (
            "strong"
            if (entry.is_strong_evidence() or chain_backed) and not demoted
            else "reference"
        )
        flags = []
        if entry.id in unendorsed:
            flags.append("unendorsed")
        if entry.id in rejected:
            flags.append("rejected")
        if entry.id in contradicted:
            flags.append("contradicted")
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
            f"- Effective curation: `{effective_curation.get(entry.id, Curation.DRAFT).value}`",
            f"- Stored curation: `{entry.trust.curation.value}`",
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


def _select_governed_web_entries(
    entries: list[Entry], records: list[EvidenceRecord]
) -> tuple[Entry, ...]:
    """Filter current Web assertions through replayed human and machine trust."""
    retired = chain_superseded_ids(records) | chain_rejected_ids(records)
    assert_trust_projection(entries, records, retired_ids=retired)
    effective = chain_effective_curation(records)
    approved = chain_approved_ids(entries, records)
    verified = chain_verified_ids(entries, records)
    contradicted = chain_contradicted_ids(records)
    selected = tuple(
        entry
        for entry in entries
        if entry.source.channel is Channel.WEB
        and effective.get(entry.id, Curation.DRAFT) is Curation.APPROVED
        and entry.id in approved
        and entry.id in verified
        and entry.id not in retired
        and entry.id not in contradicted
    )
    return selected


def _governed_web_entries(workdir: Path) -> tuple[Entry, ...]:
    """Load Web assertions backed by both human and machine trust events."""
    records = EvidenceChain.for_workdir(workdir).verify()
    with _store(workdir) as store:
        entries = store.list()
    selected = _select_governed_web_entries(entries, records)
    if not selected:
        raise CLIError(
            "no governed Web knowledge is ready for the baseline",
            "--include-web requires at least one current Web entry that is both approved and verified",
            "run `loreloop knowledge review --status draft`, approve the entry, then run "
            "`loreloop knowledge verify <entry-id>` before retrying",
        )
    return selected


def _export_output_exclusions(
    output: Path,
    workdir: Path,
    peers: dict[str, Path],
) -> dict[str, tuple[str, ...]]:
    """Keep the managed export target outside its own source proof boundary."""
    if output.exists():
        if output.is_dir():
            managed_output = (output / ".loreloop-export.json").is_file()
        else:
            from .knowledge.authoritative_archive import (
                ExportArchiveError,
                read_export_archive_with_capsule,
            )

            try:
                _ = read_export_archive_with_capsule(output)
                managed_output = True
            except ExportArchiveError:
                managed_output = False
        if not managed_output:
            return {}
    target = output.resolve()
    repositories = {
        ".": workdir.resolve(),
        **{name: path.resolve() for name, path in peers.items()},
    }
    excluded: dict[str, tuple[str, ...]] = {}
    for alias, repository in repositories.items():
        try:
            relative = target.relative_to(repository)
        except ValueError:
            continue
        if relative.parts:
            excluded[alias] = (relative.as_posix(),)
    return excluded


def _export_document_set(args: argparse.Namespace, workdir: Path) -> int:
    from .knowledge.authoritative_archive import (
        ExportArchiveError,
        ensure_archive_output_ready,
        is_archive_output,
        write_export_archive,
    )
    from .knowledge.authoritative_documents import (
        SourceDocument,
        SourceDocumentError,
        ensure_source_output_ready,
        source_document_filenames,
        write_source_documents,
    )
    from .knowledge.authoritative_ast_render import render_document_set
    from .knowledge.authoritative_capsule import (
        CAPSULE_FILENAME,
        build_capsule,
        verify_capsule,
    )
    from .knowledge.authoritative_document_ast import build_document_ast_set
    from .knowledge.authoritative_coverage import render_coverage_summary
    from .knowledge.authoritative_git import GitSnapshotError, capture_source_snapshot
    from .knowledge.authoritative_ast import AstViolation
    from .knowledge.authoritative_records import DetectionError, merge_reports
    from .knowledge.authoritative_report_normalize import normalize_detection_report
    from .knowledge.authoritative_ids import IdentityContractError
    from .knowledge.authoritative_semantic import build_semantic_core
    from .knowledge.authoritative_source import detect_snapshot_blobs, read_snapshot_blobs
    from .knowledge.authoritative_web_input import build_governed_web_input
    from .knowledge.authoritative_web_test_input import build_governed_web_test_results
    from .knowledge.repos import RepoConfigError, load_repos

    if args.stale:
        raise CLIError(
            "source document export does not accept --stale",
            "the source snapshot is rebuilt from Git commits or an explicit working tree",
            "remove --stale and retry, or use --format audit for knowledge-entry drift",
        )
    default_output = "baseline.zip" if args.format == "package" else "baseline"
    output = Path(args.output or default_output)
    archive_output = is_archive_output(output)
    output_existed: bool | None = None
    try:
        if archive_output:
            ensure_archive_output_ready(output, force=args.force)
        else:
            output_existed = ensure_source_output_ready(output, force=args.force)
        peers = load_repos(workdir)
        excluded_paths = _export_output_exclusions(output, workdir, peers)
        mode = "verifiable Git working-tree" if args.working_tree else "clean Git"
        print(f"capturing {mode} source snapshot...", file=sys.stderr)
        snapshot = capture_source_snapshot(
            workdir,
            peers,
            working_tree=args.working_tree,
            excluded_paths=excluded_paths,
        )
        print(
            f"detecting source contracts across {len(snapshot.repositories)} repositories...",
            file=sys.stderr,
        )
        requirements = tuple(args.requirements)
        blobs = read_snapshot_blobs(snapshot, workdir, peers, requirements=requirements)
        report = detect_snapshot_blobs(blobs, requirements=requirements)
        semantic_blobs = blobs
        if args.include_web:
            web_entries = _governed_web_entries(workdir)
            web_report, web_blobs = build_governed_web_input(web_entries)
            web_test_report, web_test_blobs = build_governed_web_test_results(
                EvidenceChain.for_workdir(workdir).verify()
            )
            report = normalize_detection_report(merge_reports(report, web_report, web_test_report))
            semantic_blobs = (*blobs, *web_blobs, *web_test_blobs)
            print(
                f"included {len(web_entries)} approved and verified Web knowledge entries",
                file=sys.stderr,
            )
            if web_test_report.web_knowledge:
                print(
                    f"included {len(web_test_report.web_knowledge)} governed Web-test result(s)",
                    file=sys.stderr,
                )
        project_name = args.project_name or workdir.name
        core = build_semantic_core(snapshot, semantic_blobs, report, project_name=project_name)
        document_set = build_document_ast_set(core)
        documents = render_document_set(document_set)
        print(
            render_coverage_summary(snapshot, blobs, report, len(document_set.documents)),
            file=sys.stderr,
        )
        capsule = build_capsule(core, document_set, documents)
        verify_capsule(capsule, core, document_set, documents)
        export_files = (*documents, SourceDocument(capsule.filename, capsule.content))
        if archive_output:
            write_export_archive(output, export_files, replace=args.force)
        else:
            write_source_documents(
                output,
                export_files,
                managed_filenames=(*source_document_filenames(project_name), CAPSULE_FILENAME),
                expected_output_exists=output_existed,
            )
        if args.attest:
            from .knowledge.authoritative_trust import attest_export

            record = attest_export(
                EvidenceChain.for_workdir(workdir),
                workdir,
                snapshot,
                capsule,
                core.package_id,
                peers,
            )
            print(f"attested package in local trust chain at record {record.index}")
    except (
        SourceDocumentError,
        ExportArchiveError,
        GitSnapshotError,
        AstViolation,
        DetectionError,
        IdentityContractError,
        RepoConfigError,
    ) as exc:
        if "uncommitted source changes" in str(exc) and not args.working_tree:
            next_action = (
                "commit, stash, or restore the listed files; or retry with `--working-tree` "
                "to export their exact current bytes without changing commits or the real index"
            )
        else:
            next_action = "fix the reported source state, then retry"
        raise CLIError(
            "source document export failed",
            str(exc),
            next_action,
        ) from exc
    destination = "ZIP package" if archive_output else "directory"
    print(f"exported {len(documents)} reverse-engineered documents to {destination} {output}")
    return 0


def _replay_document_set(args: argparse.Namespace, workdir: Path) -> int:
    from .knowledge.authoritative_capsule import CAPSULE_FILENAME, CapsuleArtifact
    from .knowledge.authoritative_capsule_replay import (
        CapsuleReplayError,
        replay_capsule_export,
    )
    from .knowledge.authoritative_trust import ExportTrustError, verify_trusted_export
    from .knowledge.repos import load_repos

    export_dir = Path(args.export_directory)
    try:
        result = replay_capsule_export(export_dir)
        mode = result.verification_mode
        if args.trusted:
            verify_trusted_export(
                EvidenceChain.for_workdir(workdir).verify(),
                workdir,
                CapsuleArtifact(CAPSULE_FILENAME, "", result.capsule_sha256),
                result.package_id,
                load_repos(workdir),
            )
            mode = "trusted"
    except (CapsuleReplayError, ExportTrustError) as exc:
        raise CLIError(
            "Capsule replay failed",
            str(exc),
            "restore the exact exported files or generate and attest a fresh export",
        ) from exc
    print(f"Capsule replay: {mode}")
    print(f"  package_id: {result.package_id}")
    print(f"  semantic_core_sha256: {result.semantic_core_sha256}")
    print(f"  capsule_sha256: {result.capsule_sha256}")
    print(f"  documents: {len(result.documents)}")
    return 0


def _md_escape(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ")


def _drifted_entries(workdir: Path, entries: list[Entry], records=None) -> set[str]:
    from .knowledge.code_reverse import drifted_code_entry_ids
    from .knowledge.repos import load_repos

    if not (workdir / ".git").exists() and not load_repos(workdir):
        return set()
    policies = chain_ingestion_policies(
        records if records is not None else EvidenceChain.for_workdir(workdir).verify()
    )
    return drifted_code_entry_ids(workdir, entries, policies=policies)


def _supersede(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .knowledge.model import Link, LinkType

    new = _resolve_entry(store, args.new_entry_id)
    old = _resolve_entry(store, args.old_entry_id)
    if not args.yes:
        raise CLIError(
            "supersession requires confirmation",
            f"this retires {old.id[:8]} from knowledge injection until explicitly restored",
            "review both entries, then repeat the command with `--yes`",
        )
    if new.id == old.id:
        raise CLIError(
            "invalid supersession",
            "an entry cannot supersede itself",
            "choose two different knowledge entries",
        )
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    active = chain_supersession_links(records)
    retired = chain_superseded_ids(records) | chain_rejected_ids(records)
    if new.id in retired or old.id in retired:
        target = new if new.id in retired else old
        raise CLIError(
            "invalid supersession",
            f"entry {target.id[:8]} is already retired",
            "restore it with `loreloop knowledge unsupersede` or choose an active entry",
        )
    edge = (new.id, old.id)
    if edge in active:
        raise CLIError(
            "invalid supersession",
            "that supersession relationship is already active",
            "inspect the entries with `loreloop knowledge show`",
        )
    graph: dict[str, set[str]] = {}
    for source, target in active:
        graph.setdefault(source, set()).add(target)
    pending = [old.id]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == new.id:
            raise CLIError(
                "invalid supersession",
                "the relationship would create a supersession cycle",
                "restore or revise the existing relationship before adding this one",
            )
        if current not in visited:
            visited.add(current)
            pending.extend(graph.get(current, ()))
    # Supersession silences an entry at injection time — a trust-affecting
    # act, so it is endorsed on the chain like curation. Chain first.
    chain.append(
        SUPERSEDE_EVENT,
        {
            "new_id": new.id,
            "old_id": old.id,
            "new_entry_digest": entry_digest(new),
            "new_entry": entry_payload(new),
            "old_entry_digest": entry_digest(old),
            "old_entry": entry_payload(old),
        },
    )
    store.add_link(Link(from_id=new.id, to_id=old.id, link_type=LinkType.SUPERSEDES))
    print(f"superseded: {old.title}  ({old.id[:8]})")
    print(f"        by: {new.title}  ({new.id[:8]})")
    return 0


def _unsupersede(args: argparse.Namespace, workdir: Path, store: KnowledgeStore) -> int:
    from .knowledge.model import LinkType

    new = _resolve_entry(store, args.new_entry_id)
    chain = EvidenceChain.for_workdir(workdir)
    records = chain.verify()
    active = chain_supersession_links(records)
    stored_old = [entry for entry in store.list() if entry.id.startswith(args.old_entry_id)]
    if stored_old:
        old = _resolve_entry(store, args.old_entry_id)
        old_id = old.id
    else:
        old_ids = {
            old_id
            for new_id, old_id in active
            if new_id == new.id and old_id.startswith(args.old_entry_id)
        }
        if len(old_ids) != 1:
            _raise_entry_resolution_error(args.old_entry_id, len(old_ids))
        old_id = next(iter(old_ids))
        old = None
    if not args.yes:
        raise CLIError(
            "supersession recovery requires confirmation",
            f"this restores {old_id[:8]} to active knowledge",
            "review both entries, then repeat the command with `--yes`",
        )
    if (new.id, old_id) not in active:
        raise CLIError(
            "supersession relationship not found",
            "the specified active relationship does not exist",
            "inspect the entry relationships with `loreloop knowledge show`",
        )
    if old is None:
        old = _restore_chain_entry(store, records, old_id)
    chain.append(UNSUPERSEDE_EVENT, {"new_id": new.id, "old_id": old.id})
    store.remove_link(new.id, old.id, LinkType.SUPERSEDES)
    print(f"restored: {old.title}  ({old.id[:8]})")
    print(f" removed: superseded by {new.title}  ({new.id[:8]})")
    return 0


def _resolve_entry(store: KnowledgeStore, prefix: str):
    matches = [e for e in store.list() if e.id.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    _raise_entry_resolution_error(prefix, len(matches))


def _raise_entry_resolution_error(prefix: str, matches: int) -> None:
    reason = "no entry matches" if not matches else f"{matches} entries match"
    raise CLIError(
        "knowledge entry not found",
        f"{reason} id prefix {prefix!r}",
        "run `loreloop knowledge list` and use a unique displayed id prefix",
    )


def _resolve_or_restore_rejected_entry(store: KnowledgeStore, records, prefix: str) -> Entry:
    stored = [entry for entry in store.list() if entry.id.startswith(prefix)]
    if stored:
        return _resolve_entry(store, prefix)
    rejected = {entry_id for entry_id in chain_rejected_ids(records) if entry_id.startswith(prefix)}
    if len(rejected) != 1:
        _raise_entry_resolution_error(prefix, len(rejected))
    return _restore_chain_entry(store, records, next(iter(rejected)))


def _restore_chain_entry(store: KnowledgeStore, records, entry_id: str) -> Entry:
    snapshot_fields = (
        ("entry", "entry_digest"),
        ("old_entry", "old_entry_digest"),
        ("new_entry", "new_entry_digest"),
    )
    for record in reversed(records):
        for snapshot_field, digest_field in snapshot_fields:
            snapshot = record.payload.get(snapshot_field)
            if not isinstance(snapshot, dict) or snapshot.get("id") != entry_id:
                continue
            expected = record.payload.get(digest_field)
            try:
                restored = entry_from_payload(snapshot)
            except (KeyError, TypeError, ValueError) as exc:
                raise CLIError(
                    "knowledge entry recovery refused",
                    f"signed snapshot for {entry_id[:8]} is invalid: {exc}",
                    "restore knowledge.db from backup or re-ingest the source under review",
                ) from exc
            if not isinstance(expected, str) or entry_digest(restored) != expected:
                raise CLIError(
                    "knowledge entry recovery refused",
                    f"signed snapshot for {entry_id[:8]} does not match its digest",
                    "restore knowledge.db from backup or re-ingest the source under review",
                )
            return store.restore(restored)
    raise CLIError(
        "knowledge entry recovery unavailable",
        f"the active chain record for {entry_id[:8]} has no recoverable entry snapshot",
        "restore knowledge.db from backup or re-ingest the source under review",
    )


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
        raise CLIError(
            "no web knowledge to verify",
            "no web-channel entry matches the requested id prefix",
            "run `loreloop knowledge list`, then choose a web entry or ingest a web source",
        )

    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    run_id = f"verify-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    agent = _inference_agent(args.agent)
    browser = PlaywrightBrowser(headed=args.headed)
    contradicted = 0
    try:
        for entry in web_entries:
            try:
                result = verify_entry(
                    browser, agent, chain, store, entry, run_id, artifacts=artifacts
                )
            except ActionBlocked as exc:
                raise CLIError(
                    "verification action blocked",
                    f"{entry.title}: {exc}",
                    "use a read-only interaction or explicitly review a script before allowing writes",
                ) from exc
            status = "VERIFIED" if result.passed else "CONTRADICTED"
            drift = "  [page drifted since ingest]" if result.drifted else ""
            print(f"{status}: {entry.title}{drift}")
            print(f"  {result.reason}")
            if not result.passed:
                contradicted += 1
    finally:
        browser.close()
    print(
        f"\n{len(web_entries) - contradicted} verified, {contradicted} contradicted "
        f"(evidence run {run_id})"
    )
    return 0 if contradicted == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = CLIArgumentParser(
        prog="loreloop",
        description=(
            "Reverse-engineer project knowledge, apply it to coding-agent work, "
            "and return accepted outcomes to a tamper-evident knowledge loop."
        ),
    )
    parser.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default="claude",
        help="coding-agent CLI used for extraction and delegated work (default: claude)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="check prerequisites and writable trust state")
    p_doctor.set_defaults(func=cmd_doctor)

    p_codex = sub.add_parser("codex", help="install or inspect the native Codex integration")
    codex_sub = p_codex.add_subparsers(dest="action", required=True)
    p_codex_status = codex_sub.add_parser("status", help="show Codex plugin readiness")
    p_codex_status.set_defaults(func=cmd_codex)
    p_codex_install = codex_sub.add_parser(
        "install", help="install and enable LoreLoop through the Codex plugin system"
    )
    p_codex_install.add_argument(
        "--source",
        default=_CODEX_MARKETPLACE_SOURCE,
        help="Git repository or local marketplace root",
    )
    p_codex_install.add_argument(
        "--ref",
        default=_CODEX_DEFAULT_REF,
        help=f"Git ref used when adding a Git marketplace (default: {_CODEX_DEFAULT_REF})",
    )
    p_codex_install.set_defaults(func=cmd_codex)
    p_codex_uninstall = codex_sub.add_parser(
        "uninstall", help="remove the LoreLoop plugin from Codex"
    )
    p_codex_uninstall.add_argument(
        "--remove-marketplace",
        action="store_true",
        help="also remove the LoreLoop marketplace registration",
    )
    p_codex_uninstall.set_defaults(func=cmd_codex)

    p_opencode = sub.add_parser(
        "opencode", help="install or inspect the native OpenCode integration"
    )
    opencode_sub = p_opencode.add_subparsers(dest="action", required=True)
    p_opencode_status = opencode_sub.add_parser(
        "status", help="show OpenCode integration readiness"
    )
    p_opencode_status.set_defaults(func=cmd_opencode)
    p_opencode_install = opencode_sub.add_parser(
        "install", help="install LoreLoop's global OpenCode Skill and command"
    )
    p_opencode_install.set_defaults(func=cmd_opencode)
    p_opencode_uninstall = opencode_sub.add_parser(
        "uninstall", help="remove unmodified LoreLoop OpenCode integration files"
    )
    p_opencode_uninstall.set_defaults(func=cmd_opencode)

    p_claude = sub.add_parser(
        "claude", help="install or inspect the native Claude Code integration"
    )
    claude_sub = p_claude.add_subparsers(dest="action", required=True)
    p_claude_status = claude_sub.add_parser("status", help="show Claude Code plugin readiness")
    p_claude_status.set_defaults(func=cmd_claude)
    p_claude_install = claude_sub.add_parser(
        "install", help="install and enable LoreLoop through Claude Code's plugin system"
    )
    p_claude_install.add_argument(
        "--source",
        default=_CLAUDE_MARKETPLACE_SOURCE,
        help="GitHub repository or local marketplace root",
    )
    p_claude_install.set_defaults(func=cmd_claude)
    p_claude_uninstall = claude_sub.add_parser(
        "uninstall", help="remove the LoreLoop plugin from Claude Code"
    )
    p_claude_uninstall.add_argument(
        "--remove-marketplace",
        action="store_true",
        help="also remove the LoreLoop marketplace registration",
    )
    p_claude_uninstall.set_defaults(func=cmd_claude)

    p_comind = sub.add_parser("comind", help="install or inspect the native co-mind integration")
    comind_sub = p_comind.add_subparsers(dest="action", required=True)
    p_comind_status = comind_sub.add_parser("status", help="show co-mind plugin readiness")
    p_comind_status.set_defaults(func=cmd_comind)
    p_comind_install = comind_sub.add_parser(
        "install", help="install and enable LoreLoop through co-mind's plugin system"
    )
    p_comind_install.add_argument(
        "--source",
        default=_COMIND_MARKETPLACE_SOURCE,
        help="GitHub repository or local marketplace root",
    )
    p_comind_install.set_defaults(func=cmd_comind)
    p_comind_uninstall = comind_sub.add_parser(
        "uninstall", help="remove the LoreLoop plugin from co-mind"
    )
    p_comind_uninstall.add_argument(
        "--remove-marketplace",
        action="store_true",
        help="also remove the LoreLoop marketplace registration",
    )
    p_comind_uninstall.set_defaults(func=cmd_comind)

    p_trust = sub.add_parser("trust", help="inspect or recover this project's local trust")
    trust_sub = p_trust.add_subparsers(dest="action", required=True)
    p_trust_status = trust_sub.add_parser("status", help="show local trust readiness")
    p_trust_status.set_defaults(func=cmd_trust)
    p_trust_recover = trust_sub.add_parser(
        "recover", help="reconnect existing history to its original local trust"
    )
    p_trust_recover.add_argument(
        "--from",
        dest="source_dir",
        type=Path,
        required=True,
        metavar="TRUST_DIR",
        help="operator-owned directory containing this project's original local trust",
    )
    p_trust_recover.set_defaults(func=cmd_trust)
    p_trust_reset = trust_sub.add_parser(
        "reset", help="archive existing LoreLoop state and start a new local trust domain"
    )
    p_trust_reset.add_argument(
        "--confirm", action="store_true", help="confirm archival of the current .loreloop state"
    )
    p_trust_reset.set_defaults(func=cmd_trust)

    p_init = sub.add_parser("init", help="set up LoreLoop in this project")
    skill_group = p_init.add_mutually_exclusive_group()
    skill_group.add_argument(
        "--skill",
        dest="skill",
        action="store_true",
        default=None,
        help="install or refresh the companion skill without asking",
    )
    skill_group.add_argument(
        "--no-skill", dest="skill", action="store_false", help="skip companion skill installation"
    )
    p_init.set_defaults(func=cmd_init)

    p_demo = sub.add_parser("demo", help="run the bundled five-minute knowledge loop")
    p_demo.add_argument("--offline", action="store_true", help="use deterministic CI adapters")
    p_demo.add_argument("--workspace", type=Path, help="parent directory for the demo repository")
    _add_agent_option(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_ingest = sub.add_parser("ingest", help="reverse-engineer knowledge from a source")
    p_ingest.add_argument("--from", dest="source", choices=["code", "web"], required=True)
    p_ingest.add_argument("target", help="Git repository path/name or starting web URL")
    p_ingest.add_argument("--max-pages", type=int, default=20)
    p_ingest.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="include an additional tracked-file glob; may be repeated",
    )
    p_ingest.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude a tracked-file glob; may be repeated",
    )
    p_ingest.add_argument(
        "--max-file-bytes",
        type=int,
        default=256_000,
        help="maximum bytes read from one code file (default: 256000)",
    )
    p_ingest.add_argument(
        "--strict",
        action="store_true",
        help="fail when tracked files are skipped without an explicit --exclude",
    )
    p_ingest.add_argument(
        "--headed", action="store_true", help="show the browser window (needed for login handover)"
    )
    _add_agent_option(p_ingest)
    p_ingest.set_defaults(func=cmd_ingest)

    p_repo = sub.add_parser("repo", help="manage repositories in this trust domain")
    repo_sub = p_repo.add_subparsers(dest="action", required=True)
    p_repo_add = repo_sub.add_parser("add", help="add a Git repository to this trust domain")
    p_repo_add.add_argument("repo_path", metavar="REPO_PATH", help="Git repository root")
    p_repo_add.add_argument("--name", help="repository name; defaults to the directory name")
    p_repo_add.set_defaults(func=cmd_repo)
    p_repo_list = repo_sub.add_parser("list", help="list declared repositories and reachability")
    p_repo_list.set_defaults(func=cmd_repo)
    p_repo_remove = repo_sub.add_parser("remove", help="remove a repository declaration")
    p_repo_remove.add_argument("repo_name", metavar="REPO_NAME", help="declared repository name")
    p_repo_remove.set_defaults(func=cmd_repo)

    p_project = sub.add_parser("project", help="manage the federation project registry")
    project_sub = p_project.add_subparsers(dest="action", required=True)
    p_project_add = project_sub.add_parser("add", help="register a project for federation")
    p_project_add.add_argument("project_path", metavar="PROJECT_PATH")
    p_project_add.add_argument("--id", dest="project_id")
    p_project_add.add_argument("--name")
    p_project_add.add_argument("--alias", action="append", default=[])
    p_project_add.add_argument("--tag", action="append", default=[])
    p_project_add.set_defaults(func=cmd_project)
    p_project_list = project_sub.add_parser("list", help="list registered projects")
    p_project_list.set_defaults(func=cmd_project)
    p_project_remove = project_sub.add_parser("remove", help="remove a federation registration")
    p_project_remove.add_argument(
        "registry_project_id", metavar="PROJECT_ID", help="registered project id"
    )
    p_project_remove.set_defaults(func=cmd_project)

    p_web = sub.add_parser("web", help="govern replayable Web test scenarios")
    web_sub = p_web.add_subparsers(dest="web_command", required=True)
    p_web_test = web_sub.add_parser("test", help="generate, approve, run or export Web tests")
    web_test_sub = p_web_test.add_subparsers(dest="web_test_action", required=True)
    p_web_test_generate = web_test_sub.add_parser(
        "generate", help="generate candidate scenarios from the latest captured exploration"
    )
    p_web_test_generate.set_defaults(func=cmd_web)
    p_web_test_review = web_test_sub.add_parser(
        "review", help="list candidate and approved Web scenarios"
    )
    p_web_test_review.set_defaults(func=cmd_web)
    p_web_test_approve = web_test_sub.add_parser(
        "approve", help="publish one reviewed candidate into the committed test suite"
    )
    p_web_test_approve.add_argument("scenario_id", metavar="SCENARIO_ID")
    p_web_test_approve.add_argument(
        "--repo", metavar="REPO_NAME", help="repository that will own the approved test"
    )
    p_web_test_approve.set_defaults(func=cmd_web)
    p_web_test_record = web_test_sub.add_parser(
        "record", help="record a headed user journey as a private candidate"
    )
    p_web_test_record.add_argument("url", metavar="URL")
    p_web_test_record.add_argument("--title")
    p_web_test_record.add_argument("--risk", choices=("read-only", "writes"), default="read-only")
    p_web_test_record.add_argument("--allow-writes", action="store_true")
    p_web_test_record.set_defaults(func=cmd_web)
    p_web_test_run = web_test_sub.add_parser(
        "run", help="replay one or all chain-approved Web scenarios"
    )
    p_web_test_run.add_argument("scenario_id", metavar="SCENARIO_ID", nargs="?")
    p_web_test_run.add_argument("--all", action="store_true")
    p_web_test_run.add_argument("--headed", action="store_true")
    p_web_test_run.add_argument("--allow-writes", action="store_true")
    p_web_test_run.set_defaults(func=cmd_web)
    p_web_test_export = web_test_sub.add_parser(
        "export", help="export approved scenarios as deterministic Playwright tests"
    )
    p_web_test_export.add_argument("--format", choices=("playwright",), default="playwright")
    p_web_test_export.add_argument("--output", required=True, metavar="DIRECTORY")
    p_web_test_export.add_argument("--force", action="store_true")
    p_web_test_export.set_defaults(func=cmd_web)

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
    _add_agent_option(p_verify)
    p_verify.set_defaults(func=cmd_verify)

    p_begin = sub.add_parser(
        "begin", help="prepare knowledge for the coding-agent session already in use"
    )
    p_begin.add_argument("task", help="development task for the current agent session")
    p_begin.add_argument(
        "--expand",
        default="",
        metavar="TERMS",
        help="caller-supplied retrieval terms; never included in the context pack",
    )
    p_begin.add_argument(
        "--with-related",
        action="store_true",
        help="include relevant references from registered related projects",
    )
    p_begin.add_argument(
        "--related-limit", type=int, default=5, help="maximum related-project references"
    )
    p_begin.add_argument(
        "--requirements",
        action="append",
        default=[],
        metavar="PATH",
        help="committed requirement Markdown; use repo:NAME/path for a peer repository",
    )
    p_begin.set_defaults(func=cmd_begin)

    p_complete = sub.add_parser(
        "complete", help="mark a prepared current-session implementation complete"
    )
    p_complete.add_argument("run_id")
    p_complete.add_argument(
        "--confirm",
        action="store_true",
        help="confirm that the operator authorized signing session completion",
    )
    p_complete.set_defaults(func=cmd_complete)

    p_run = sub.add_parser("run", help="delegate a task in a new coding-agent process")
    p_run.add_argument("task", help="development task to delegate")
    p_run.add_argument(
        "--no-expand",
        action="store_true",
        help="skip LLM query expansion; retrieve with the task text only",
    )
    p_run.add_argument(
        "--with-related",
        action="store_true",
        help="include relevant references from registered related projects",
    )
    p_run.add_argument(
        "--related-limit", type=int, default=5, help="maximum related-project references"
    )
    _add_agent_option(p_run)
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

    p_harvest = sub.add_parser("harvest", help="flow knowledge back from an accepted run")
    p_harvest.add_argument("run_id")
    _add_agent_option(p_harvest)
    p_harvest.set_defaults(func=cmd_harvest)

    p_knowledge = sub.add_parser("knowledge", help="inspect, curate and verify knowledge entries")
    knowledge_sub = p_knowledge.add_subparsers(dest="action", required=True)

    p_knowledge_list = knowledge_sub.add_parser("list", help="list knowledge and trust state")
    p_knowledge_list.add_argument(
        "--stale", action="store_true", help="only entries whose code anchor drifted"
    )
    _add_knowledge_filters(p_knowledge_list)
    p_knowledge_list.set_defaults(func=cmd_knowledge)

    p_knowledge_show = knowledge_sub.add_parser(
        "show", help="show one entry's full assertion, trust, evidence and relationships"
    )
    p_knowledge_show.add_argument("entry_id", metavar="ENTRY_ID")
    p_knowledge_show.set_defaults(func=cmd_knowledge)

    p_knowledge_review = knowledge_sub.add_parser(
        "review", help="review filtered entries with complete source evidence"
    )
    p_knowledge_review.add_argument(
        "--stale", action="store_true", help="only entries whose code anchor drifted"
    )
    _add_knowledge_filters(p_knowledge_review)
    p_knowledge_review.set_defaults(func=cmd_knowledge)

    p_knowledge_search = knowledge_sub.add_parser(
        "search", help="search local or federated knowledge"
    )
    p_knowledge_search.add_argument("query", metavar="QUERY")
    search_scope = p_knowledge_search.add_mutually_exclusive_group()
    search_scope.add_argument("--all", action="store_true", help="search all registered projects")
    search_scope.add_argument(
        "--project", action="append", help="search one registered project; may be repeated"
    )
    search_scope.add_argument(
        "--package",
        metavar="PATH",
        help="search a replay-verified baseline ZIP or directory without importing it",
    )
    p_knowledge_search.add_argument(
        "--tag", action="append", help="filter selected projects by tag"
    )
    p_knowledge_search.add_argument(
        "--expand",
        default="",
        metavar="TERMS",
        type=_parse_search_expansion,
        help="retrieval-only synonyms, translations and identifiers; never treated as knowledge",
    )
    p_knowledge_search.add_argument("--limit", type=int, default=10)
    p_knowledge_search.set_defaults(func=cmd_knowledge)

    p_knowledge_import = knowledge_sub.add_parser(
        "import", help="copy a foreign entry into the local draft store"
    )
    p_knowledge_import.add_argument("project_id", metavar="PROJECT_ID")
    p_knowledge_import.add_argument("entry_id", metavar="ENTRY_ID")
    p_knowledge_import.set_defaults(func=cmd_knowledge)

    p_knowledge_export = knowledge_sub.add_parser(
        "export", help="export knowledge audit Markdown or source-derived project documents"
    )
    p_knowledge_export.add_argument(
        "--stale", action="store_true", help="audit only: export entries with drifted code anchors"
    )
    p_knowledge_export.add_argument(
        "--format",
        choices=("audit", "package", "docs"),
        default="audit",
        help="audit entries, readable project docs, or a compressed project package",
    )
    p_knowledge_export.add_argument(
        "--output",
        help=(
            "audit Markdown file, source-docs directory, or deliverable .zip package; "
            "docs defaults to baseline/ and package defaults to baseline.zip"
        ),
    )
    p_knowledge_export.add_argument("--project-name", help="project name used in source documents")
    p_knowledge_export.add_argument(
        "--requirements",
        action="append",
        default=[],
        metavar="PATH",
        help="committed Markdown requirements; use repo:NAME/path for a peer repository",
    )
    p_knowledge_export.add_argument(
        "--force",
        action="store_true",
        help="replace an existing ZIP or update source docs in a non-empty directory",
    )
    p_knowledge_export.add_argument(
        "--working-tree",
        action="store_true",
        help=(
            "snapshot staged, unstaged, and untracked non-ignored files without committing; "
            "the documents are marked as a working-tree baseline"
        ),
    )
    p_knowledge_export.add_argument(
        "--attest",
        action="store_true",
        help="append an optional local trust-chain attestation for the exported package",
    )
    p_knowledge_export.add_argument(
        "--include-web",
        action="store_true",
        help="include current Web entries that are both operator-approved and browser-verified",
    )
    p_knowledge_export.set_defaults(func=cmd_knowledge)

    p_knowledge_replay = knowledge_sub.add_parser(
        "replay", help="verify an exported source-document directory or ZIP without source access"
    )
    p_knowledge_replay.add_argument("export_directory", metavar="EXPORT_PATH")
    p_knowledge_replay.add_argument(
        "--trusted",
        action="store_true",
        help="also require a matching local chain attestation and repository binding",
    )
    p_knowledge_replay.set_defaults(func=cmd_knowledge)

    for action, help_text in (
        ("approve", "approve one draft entry and endorse its content"),
        ("reject", "reject one entry and retire it from injection"),
        ("reopen", "return a rejected entry to draft for renewed review"),
    ):
        p_curate = knowledge_sub.add_parser(action, help=help_text)
        p_curate.add_argument("entry_id", metavar="ENTRY_ID")
        p_curate.set_defaults(func=cmd_knowledge)

    p_knowledge_supersede = knowledge_sub.add_parser(
        "supersede", help="retire an old entry in favor of a new entry"
    )
    p_knowledge_supersede.add_argument("new_entry_id", metavar="NEW_ENTRY_ID")
    p_knowledge_supersede.add_argument("old_entry_id", metavar="OLD_ENTRY_ID")
    p_knowledge_supersede.add_argument(
        "--yes", action="store_true", help="confirm retirement of the old entry"
    )
    p_knowledge_supersede.set_defaults(func=cmd_knowledge)

    p_knowledge_unsupersede = knowledge_sub.add_parser(
        "unsupersede", help="restore an entry retired by a supersession relationship"
    )
    p_knowledge_unsupersede.add_argument("new_entry_id", metavar="NEW_ENTRY_ID")
    p_knowledge_unsupersede.add_argument("old_entry_id", metavar="OLD_ENTRY_ID")
    p_knowledge_unsupersede.add_argument(
        "--yes", action="store_true", help="confirm restoration of the old entry"
    )
    p_knowledge_unsupersede.set_defaults(func=cmd_knowledge)

    p_knowledge_verify = knowledge_sub.add_parser(
        "verify", help="recheck web knowledge against live pages"
    )
    p_knowledge_verify.add_argument("entry_id", metavar="ENTRY_ID", nargs="?")
    p_knowledge_verify.add_argument("--headed", action="store_true")
    _add_agent_option(p_knowledge_verify)
    p_knowledge_verify.set_defaults(func=cmd_knowledge)

    p_knowledge_usage = knowledge_sub.add_parser(
        "usage", help="show injection and accepted-run correlation"
    )
    p_knowledge_usage.set_defaults(func=cmd_knowledge)

    return parser


def main(argv: list[str] | None = None) -> int:
    import sqlite3
    import subprocess

    from .evidence.chain import (
        KeyMaterialError,
        LegacyKeyError,
        OperatorBoundaryError,
        TrustCredentialUnavailable,
    )
    from .federation.registry import RegistryError
    from .knowledge.repos import RepoConfigError
    from .knowledge.requirement_context import RequirementContextError
    from .knowledge.code_reverse import ExtractionError
    from .knowledge.endorsement import TrustProjectionError
    from .knowledge.store import InvalidKnowledgeProjection, SchemaVersionError
    from .webexplore.scenarios import WebScenarioError
    from .paths import StatePathError
    from .webexplore.browser import BrowserError, BrowserUnavailable

    try:
        args = build_parser().parse_args(argv)
        return args.func(args)
    except _HelpRequested:
        return 0
    except CLIError as exc:
        return _print_cli_error(exc)
    except TrustCredentialUnavailable as exc:
        return _print_cli_error(
            CLIError(
                "local project trust is unavailable",
                str(exc),
                "run `loreloop trust status`, then reconnect the original local trust "
                "or explicitly reset the local trust domain",
            )
        )
    except ChainVerificationError as exc:
        if exc.index == 0 and exc.reason == "signature invalid":
            return _print_cli_error(
                CLIError(
                    "local project trust does not match",
                    "this project has existing LoreLoop history, but the selected local "
                    "trust belongs to a different trust domain",
                    "run `loreloop trust status`, then `loreloop trust recover --from "
                    "<original-trust-directory>`",
                )
            )
        return _print_cli_error(
            CLIError(
                "evidence chain broken",
                str(exc),
                "restore the intact project state and matching local trust credential",
            )
        )
    except KeyboardInterrupt:
        return _print_cli_error(
            CLIError(
                "command interrupted",
                "the operator cancelled the command",
                "inspect the last printed run id; interrupted delegations are marked and are never accepted",
                exit_code=130,
            )
        )
    except (
        LegacyKeyError,
        KeyMaterialError,
        OperatorBoundaryError,
        RegistryError,
        RepoConfigError,
        RequirementContextError,
        InvalidKnowledgeProjection,
        SchemaVersionError,
        StatePathError,
        RunTraceError,
        InitializationError,
        AgentError,
        ExtractionError,
        TrustProjectionError,
        BrowserError,
        BrowserUnavailable,
        WebScenarioError,
        sqlite3.Error,
        subprocess.CalledProcessError,
        EOFError,
        OSError,
    ) as exc:
        command = getattr(locals().get("args"), "command", None)
        hints = {
            "init": "run `loreloop doctor`, fix each FAIL check, then retry initialization",
            "codex": "run `loreloop codex status`, resolve the reported Codex issue, then retry",
            "opencode": "run `loreloop opencode status`, resolve the reported OpenCode issue, then retry",
            "claude": "run `loreloop claude status`, resolve the reported Claude Code issue, then retry",
            "comind": "run `loreloop comind status`, resolve the reported co-mind issue, then retry",
            "trust": "run `loreloop trust --help` and choose status, recover, or reset",
            "demo": "fix the reported prerequisite, then rerun `loreloop demo --help`",
            "ingest": "check the source path/URL and agent setup, then retry ingestion",
            "begin": "fix the reported trust-state issue, then retry `loreloop begin <task>`",
            "complete": "use the run id printed by `loreloop begin` and confirm explicitly",
            "run": "run `loreloop doctor`, resolve the reported agent or trust-state issue, then retry",
            "verify": "check Playwright, the URL, and the expectation, then retry verification",
            "report": "use a run id printed by `loreloop begin` or `loreloop run`, then retry",
            "harvest": "inspect `loreloop report <run-id>` and resolve the reported blocker",
            "repo": "run `loreloop repo --help` and correct the repository declaration",
            "project": "run `loreloop project --help` and correct the registry operation",
            "web": "run `loreloop web test --help`, review the scenario, then retry",
            "knowledge": "run `loreloop knowledge --help` and retry the relevant knowledge action",
        }
        return _print_cli_error(
            CLIError(
                f"{command or 'loreloop'} failed",
                str(exc) or exc.__class__.__name__,
                hints.get(command, "run `loreloop doctor`, fix the reported reason, then retry"),
            )
        )


if __name__ == "__main__":
    sys.exit(main())
