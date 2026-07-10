"""Companion skill installation for host coding agents.

The skill makes the host agent a better citizen in a knowhelm-governed
project. It teaches — it never verifies, never judges, never mints. The
four invariants (human-written assertions, on-chain verdicts, human-driven
harvest, read-only knowledge access for agents) are restated inside the
skill text itself so the agent carries them into every session.
"""

from __future__ import annotations

from pathlib import Path

CLAUDE_SKILL_RELPATH = ".claude/skills/knowhelm/SKILL.md"
CODEX_SKILL_RELPATH = ".agents/skills/knowhelm/SKILL.md"

CLAUDE_SKILL_MD = """\
---
name: knowhelm
description: Collaborate with knowhelm, the knowledge-governance and evidence-backed acceptance tool for this project. Use when a prompt contains a "Project knowledge (provided by knowhelm)" section, or when working in a repository with a .knowhelm directory.
---

# Working in a knowhelm-governed project

knowhelm injects curated project knowledge into your prompt before a task
and verifies the result against a real browser afterwards. Evidence, not
your own account, decides acceptance. Your part:

## Reading the injected context pack

- **Established facts** are constraints. Do not contradict them. If your
  task seems to require contradicting one, stop and tell the operator —
  the knowledge may be wrong, but that call is theirs.
- **Unverified references** are plausible hints. Verify against the actual
  source before relying on one.
- An entry marked `[source changed since this was captured]` has a drifted
  anchor: the file it was extracted from has changed since. Treat it as a
  question, not an answer.

## Looking things up

You may run read-only knowledge commands at any time:

    knowhelm knowledge list
    knowhelm knowledge list --stale

## Acceptance: draft, never certify

- When you finish a change, propose acceptance assertions for the operator
  to approve. Prefer deterministic ones — they are checked against the
  page without any model involved:

      contains:<text that must appear>
      absent:<text that must not appear>
      title-contains:<text>

- You may re-run checks the operator already approved for fast feedback
  while iterating (`knowhelm verify <run-id> <url> "<approved assertion>"`).
- The verdict comes from the operator running `knowhelm report`, which
  audits the tamper-evident evidence chain. Never present your own summary
  or your own verify runs as the acceptance verdict.
- End your work by reminding the operator to run their verification.

## Never

- Never run `knowhelm harvest` — minting knowledge is a human act.
- Never run `knowhelm knowledge approve`, `reject`, or `supersede` —
  curation is a human act.
- Never create, edit, or delete anything under `.knowhelm/`.
- Never invent, weaken, or reword an operator's acceptance assertion.
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
