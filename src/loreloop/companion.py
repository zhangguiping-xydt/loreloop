"""Companion skill installation for host coding agents.

The skill keeps Codex or Claude Code as the user's entry point while LoreLoop
provides knowledge and evidence underneath. It may execute operator-authorized
CLI actions in the current session, but it never treats the agent's own
judgment as authorization to complete, harvest, or curate.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .paths import StatePathError, reject_symlink

CLAUDE_SKILL_RELPATH = ".claude/skills/loreloop/SKILL.md"
AGENT_SKILL_RELPATH = ".agents/skills/loreloop/SKILL.md"
CODEX_SKILL_RELPATH = AGENT_SKILL_RELPATH
OPENCODE_COMMAND_RELPATH = ".opencode/commands/loreloop.md"
OPENCODE_GLOBAL_SKILL_RELPATH = "skills/loreloop/SKILL.md"
OPENCODE_GLOBAL_COMMAND_RELPATH = "commands/loreloop.md"

CLAUDE_SKILL_MD = """\
---
name: loreloop
description: Collaborate with LoreLoop, the knowledge-governance and evidence-backed acceptance tool for this project. Use when a prompt contains a "Project knowledge (provided by LoreLoop)" section, when the operator invokes LoreLoop, or when working in a repository with a .loreloop directory.
---

# Working in a LoreLoop-governed project

Keep the user in this host coding-agent session. LoreLoop is the local
knowledge and evidence engine behind the host agent, not a replacement chat
entry point. Evidence, not your own account, decides acceptance.

## Keep the project integration current

At the first LoreLoop action in each new host session, run
`loreloop init --skill` once from the project workspace before any other
LoreLoop command. Initialization is idempotent for an existing trust domain:
it verifies the current state and refreshes the managed project-level Skill
from the installed LoreLoop runtime. It must not reset trust, remove project
files, or require another confirmation. Then continue with `loreloop doctor`
or the operator's requested action.

## Start work in the current session

When the operator asks to use LoreLoop for a development task:

1. Run `loreloop begin "<task>"`. This prepares and signs the task boundary,
   retrieves relevant knowledge, and prints a context pack without launching
   a nested coding agent. If `loreloop` is not on PATH, use the project's
   `.venv/bin/loreloop` or `uv run loreloop` installation.
2. Keep the printed run id for later evidence commands.
3. Read the printed context pack using the rules below, then perform the task
   in this current host session.

Do not use `loreloop run` for normal interactive work: it launches a separate
coding-agent process. Use it only when the operator explicitly requests an
automated or headless delegation.

## Default autonomous development loop

The operator should only need to describe a bug or requirement in natural
language. Keep LoreLoop subcommands behind this host-agent workflow:

1. Classify the request as bug, feature, or general task. For a bug, reproduce
   it and add or identify a regression test. For a feature, derive concrete
   acceptance criteria before implementation.
2. Implement in this current host session.
3. Before claiming success, run `loreloop test select <run-id> --format json`.
   Read MUST, RECOMMENDED, MISSING, and the suggested commands. Never hide a
   MISSING coverage gap.
4. Run `loreloop test run <run-id>` to execute deterministic non-Web commands
   as provisional evidence. Fix failures and regenerate the plan after source
   changes that materially alter impact.
5. For a generated read-only Web candidate, use
   `loreloop web test trial <run-id> <scenario-id> [--headed]`. Trial evidence
   is provisional and cannot replace operator approval. Never trial a
   write-risk candidate.
6. Record the host-agent narrative with `loreloop task summarize <run-id>
   --analysis "..." --implementation "..."`, adding each acceptance criterion
   and remaining risk with repeated `--acceptance` and `--risk` options.
7. `loreloop report <run-id>` also writes the evidence-backed development
   record to `workspace/change/<run-id>/acceptance-report.md`. Present one
   concise completion summary containing root cause or requirement
   interpretation, changed files, selected-test rationale, results, Web states,
   coverage gaps, and risks. Then ask for completion confirmation.

The detailed CLI remains an advanced/debugging surface. Do not make the
operator manually orchestrate select, run, coverage, or report in normal work.

## Export the authoritative project package

When the operator asks to export project knowledge, a knowledge baseline, or
reverse-engineered project documents, export a directly readable directory
rather than the legacy entry audit:

    loreloop knowledge export --format docs --output workspace/baseline

The directory contains six fixed Markdown documents, evidence-backed optional
interface/database documents, and the portable Capsule. It can be opened,
searched, and replayed without manual extraction. Use the compressed command
`loreloop knowledge export --format package --output workspace/baseline.zip` only when
the operator explicitly asks for a handoff artifact. Use `--format audit` only
when the operator explicitly asks for the entry-by-entry knowledge audit.

Run the command from the initialized project workspace. The workspace may be
a Git repository or a non-Git aggregate root with declared member repositories.
When invoking a host shell/Bash tool, always pass the complete non-empty command
string shown above; never issue a shell tool call with its command omitted. Do
not add `--force` unless the operator explicitly authorizes replacing an
existing output. Verify a produced package with:

    loreloop knowledge replay workspace/baseline

Committed snapshots remain the default. If export reports uncommitted source
changes, show the listed files to the operator. Do not commit, stash, delete,
or ignore them automatically. If the request is to export the current project
baseline (rather than specifically a committed-release baseline), retry
directly without asking for a Git commit:

    loreloop knowledge export --format docs --output workspace/baseline --working-tree

This mode includes staged, unstaged, and untracked non-ignored files, binds
them to the current HEAD and a separate source tree, and labels every document
as a working-tree baseline rather than a committed release state.

Legacy text source does not require repository-wide transcoding. LoreLoop reads
supported source as strict UTF-8 first and falls back to GB18030 (including
GBK-compatible files) while keeping the original blob bytes and digest as
evidence. Bounded UTF-8 damage is reported in the generated detailed design;
facts anchored to damaged lines are excluded. Never rewrite source encodings
merely to make an export pass.

Search the replay-verified SemanticCore Agent view without extracting the
package:

    loreloop knowledge search "<query>" --package workspace/baseline

Every hit is an exact Agent-view fact mapped to its owning human document
domain and source evidence. Do not claim that an atomic Agent fact appears
verbatim in the human Markdown; use the human view for narrative review and
the Agent view for precise code, interface, field, and symbol lookup.

If the question uses different wording, derive 5-15 concise synonyms,
translations, abbreviations, and likely code identifiers in this current host
session, then retry without launching a nested agent:

    loreloop knowledge search "<query>" --package workspace/baseline --expand "<terms>"

Expansion is retrieval-only. Never present expansion terms as project facts or
evidence, and never let them change trust. Use only replay-verified package
content returned by the search in the answer.

When the operator explicitly requests a Web-enriched replacement and has
approved and verified the relevant Web entries:

    loreloop knowledge export --format docs --output workspace/baseline --include-web --force
    loreloop knowledge replay workspace/baseline

When the operator asks for repeatable Web tests:

    loreloop ingest --from web <url> [--headed]
    loreloop web test generate
    loreloop web test review
    loreloop web test approve <scenario-id>
    git add tests/loreloop/web/<scenario-id>.json && git commit
    loreloop web test run <scenario-id>
    loreloop web test coverage --format markdown --output web-coverage.md
    loreloop web test export --format playwright --output <directory>

In a non-Git aggregate with multiple declared repositories, pass
`--repo <repo-name>` to `web test approve` so the committed authority lives in
one member repository.

Candidates under `.loreloop/web-tests/candidates/` are untrusted review
material. Never approve one on the operator's behalf. The chain-approved JSON
under `tests/loreloop/web/` is authoritative; Playwright is only a derivative
export. Keep tests read-only unless the operator explicitly authorizes
`--allow-writes`, never store credentials, and treat replay results as chain
evidence that can enter the package acceptance specification with
`--include-web`.

## Local trust recovery

LoreLoop manages local trust automatically during normal initialization. If a
command reports that project trust is unavailable or does not match:

1. Run `loreloop trust status` and summarize its user-facing result.
2. Do not expose signature internals, key identifiers, evidence record indexes,
   or recommend moving/deleting `.loreloop` manually.
3. If the operator has the original LoreLoop trust directory or its backup, ask
   for that directory and run `loreloop trust recover --from <directory>`.
4. Run `loreloop doctor` after recovery, then retry the original command.

`loreloop trust reset --confirm` archives the existing LoreLoop state and starts
a new trust domain. Run it only after the operator explicitly authorizes losing
the old domain's continuity. Never infer that authorization from a failed
recovery or from the absence of a backup.

## Reading the injected context pack

- **Established facts** are constraints. Do not contradict them. If your
  task seems to require contradicting one, stop and tell the operator —
  the knowledge may be wrong, but that call is theirs.
- **Unverified references** are plausible hints. Verify against the actual
  source before relying on one.
- An entry marked `[source changed since this was captured]` has a drifted
  anchor: the file it was extracted from has changed since. Treat it as a
  question, not an answer.
- A strong web entry reflects its last verification, not a live browser check
  for this run. If the task materially relies on it, propose re-verification
  to the operator.

## Looking things up

You may run read-only knowledge commands at any time:

    loreloop knowledge list
    loreloop knowledge list --stale

## Finish and prove — explicit operator authorization

- When implementation is ready, summarize the concrete changes and propose
  acceptance assertions. Include the provisional task-test and Web-trial
  results, plus every remaining coverage gap. Ask the operator to confirm completion before
  running `loreloop complete <run-id> --confirm`. Never infer that approval
  from silence or from your own confidence.
- After confirmed completion, run `loreloop test prove <run-id>`. It reruns
  deterministic selected commands as post-completion, chain-backed command
  checks. These checks, not provisional self-test events, decide acceptance.
  Then run `loreloop report <run-id>` and give the operator the complete report
  outcome.
- Propose acceptance assertions for the operator to approve. Prefer
  deterministic ones — they are checked without trusting your self-report:

      contains:<text that must appear>
      absent:<text that must not appear>
      title-contains:<text>

- Run `loreloop check` or `loreloop verify` only for assertions the operator
  has approved. You may re-run an already approved check while iterating.
- Run `loreloop report <run-id>` when the operator asks for the verdict. The
  report audits the tamper-evident evidence chain; never present your own
  summary or a raw test run as LoreLoop's acceptance verdict.
- `loreloop harvest <run-id>` and knowledge curation remain operator acts.
  You may execute them inside this host session only after the operator gives
  a specific, explicit instruction for that run or entry. Never decide to
  harvest, approve, reject, reopen, supersede, or unsupersede on your own.

## Never

- Never call `loreloop complete --confirm`, harvest, or curation based only on
  your own judgment; the confirmation must come from the operator.
- Never create, edit, or delete anything under `.loreloop/`.
- Never invent, weaken, or reword an operator's acceptance assertion.
- Never work around an operator-boundary refusal or local-trust restriction.
"""


def install_claude_skill(workdir: Path) -> Path:
    path = workdir / CLAUDE_SKILL_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CLAUDE_SKILL_MD, encoding="utf-8")
    return path


def install_codex_skill(workdir: Path) -> Path:
    """Install the shared governance contract in the agent-compatible tree."""
    path = workdir / AGENT_SKILL_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CLAUDE_SKILL_MD, encoding="utf-8")
    return path


OPENCODE_COMMAND_MD = """\
---
description: Use LoreLoop's governed project knowledge in this OpenCode session
---

Load the `loreloop` skill and use it for this request in the current OpenCode
session. Keep acceptance, harvest, and curation behind explicit operator
authorization.

Request: $ARGUMENTS
"""


def install_opencode_command(workdir: Path) -> Path:
    path = workdir / OPENCODE_COMMAND_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OPENCODE_COMMAND_MD, encoding="utf-8")
    return path


def opencode_config_dir() -> Path:
    import os

    configured = os.environ.get("OPENCODE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return (Path(xdg).expanduser() / "opencode").absolute()
    return (Path.home() / ".config/opencode").absolute()


def install_opencode_global() -> tuple[Path, Path]:
    root = opencode_config_dir()
    skill = root / OPENCODE_GLOBAL_SKILL_RELPATH
    command = root / OPENCODE_GLOBAL_COMMAND_RELPATH
    managed = ((skill, CLAUDE_SKILL_MD), (command, OPENCODE_COMMAND_MD))
    for path, content in managed:
        _validate_managed_target(path, content)
    for path, content in managed:
        _write_managed_file(path, content)
    return skill, command


def uninstall_opencode_global() -> tuple[Path, ...]:
    root = opencode_config_dir()
    managed = (
        (root / OPENCODE_GLOBAL_SKILL_RELPATH, CLAUDE_SKILL_MD),
        (root / OPENCODE_GLOBAL_COMMAND_RELPATH, OPENCODE_COMMAND_MD),
    )
    removable: list[Path] = []
    for path, expected in managed:
        reject_symlink(path, label="OpenCode integration file")
        if not path.exists():
            continue
        if path.read_text(encoding="utf-8") != expected:
            raise RuntimeError(f"refusing to remove modified OpenCode integration file: {path}")
        removable.append(path)
    removed: list[Path] = []
    for path in removable:
        path.unlink()
        removed.append(path)
    return tuple(removed)


def opencode_global_status() -> tuple[tuple[Path, str], ...]:
    root = opencode_config_dir()
    managed = (
        (root / OPENCODE_GLOBAL_SKILL_RELPATH, CLAUDE_SKILL_MD),
        (root / OPENCODE_GLOBAL_COMMAND_RELPATH, OPENCODE_COMMAND_MD),
    )
    status: list[tuple[Path, str]] = []
    for path, expected in managed:
        try:
            reject_symlink(path, label="OpenCode integration file")
        except StatePathError:
            status.append((path, "symlink"))
            continue
        if not path.exists():
            status.append((path, "missing"))
        elif not path.is_file():
            status.append((path, "not-file"))
        elif path.read_text(encoding="utf-8") == expected:
            status.append((path, "ready"))
        else:
            status.append((path, "modified"))
    return tuple(status)


def _validate_managed_target(path: Path, content: str) -> None:
    _ensure_plain_directory(path.parent)
    reject_symlink(path, label="OpenCode integration file")
    if path.exists():
        if not path.is_file():
            raise RuntimeError(f"refusing to replace non-file OpenCode integration path: {path}")
        if path.read_text(encoding="utf-8") != content:
            raise RuntimeError(f"refusing to overwrite modified OpenCode integration file: {path}")


def _write_managed_file(path: Path, content: str) -> None:
    """Atomically install one preflighted integration file."""
    if path.exists():
        return
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def _ensure_plain_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while True:
        if current.is_symlink():
            raise StatePathError(f"refusing symlinked OpenCode integration directory: {current}")
        if current.exists():
            break
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    if not current.is_dir():
        raise StatePathError(f"OpenCode integration parent is not a directory: {current}")
    for directory in reversed(missing):
        directory.mkdir()
        os.chmod(directory, 0o700)
    cursor = path
    while True:
        if cursor.is_symlink():
            raise StatePathError(f"refusing symlinked OpenCode integration directory: {cursor}")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
