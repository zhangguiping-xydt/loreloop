"""Companion skill installation for host coding agents.

The skill keeps Codex or Claude Code as the user's entry point while LoreLoop
provides knowledge and evidence underneath. It may execute operator-authorized
CLI actions in the current session, but it never treats the agent's own
judgment as authorization to complete, harvest, or curate.
"""

from __future__ import annotations

from pathlib import Path

CLAUDE_SKILL_RELPATH = ".claude/skills/loreloop/SKILL.md"
CODEX_SKILL_RELPATH = ".agents/skills/loreloop/SKILL.md"

CLAUDE_SKILL_MD = """\
---
name: loreloop
description: Collaborate with LoreLoop, the knowledge-governance and evidence-backed acceptance tool for this project. Use when a prompt contains a "Project knowledge (provided by LoreLoop)" section, or when working in a repository with a .loreloop directory.
---

# Working in a LoreLoop-governed project

Keep the user in this Claude Code or Codex session. LoreLoop is the local
knowledge and evidence engine behind the host agent, not a replacement chat
entry point. Evidence, not your own account, decides acceptance.

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
  acceptance assertions. Ask the operator to confirm completion before
  running `loreloop complete <run-id> --confirm`. Never infer that approval
  from silence or from your own confidence.
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
- Never work around an operator-boundary refusal or signing-key restriction.
"""


def install_claude_skill(workdir: Path) -> Path:
    path = workdir / CLAUDE_SKILL_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CLAUDE_SKILL_MD, encoding="utf-8")
    return path


def install_codex_skill(workdir: Path) -> Path:
    """Install the same governance contract in Codex's project skill tree."""
    path = workdir / CODEX_SKILL_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CLAUDE_SKILL_MD, encoding="utf-8")
    return path
