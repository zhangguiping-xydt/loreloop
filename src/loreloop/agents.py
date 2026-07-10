"""Thin adapter over local agent CLIs (claude -p / codex exec).

loreloop never calls a model API directly: it reuses the coding-agent CLI the
user already has, so there is no API key configuration of its own.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class AgentError(Exception):
    pass


@dataclass(frozen=True)
class AgentRunner:
    command: tuple[str, ...] = ("claude", "-p")
    timeout: float = 600.0
    cwd: Path | None = None
    isolated: bool = False

    def run(self, prompt: str) -> str:
        if self.isolated:
            with tempfile.TemporaryDirectory(prefix="loreloop-inference-") as temp:
                return self._run(prompt, Path(temp))
        return self._run(prompt, self.cwd)

    def _run(self, prompt: str, cwd: Path | None) -> str:
        try:
            proc = subprocess.run(
                list(self.command),
                input=prompt,
                cwd=cwd,
                env=agent_environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise AgentError(
                f"agent CLI not found: {self.command[0]!r}. Install Claude Code or Codex "
                "and make sure it is on your PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"agent call timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise AgentError(
                f"agent exited with code {proc.returncode}: {proc.stderr.strip()[:500]}"
            )
        return proc.stdout


def inference_runner(name: str, *, timeout: float = 600.0) -> AgentRunner:
    """Return a least-capability runner for extraction, expansion and judging.

    These calls receive untrusted source/page text but never need repository
    tools. Claude can disable tools entirely. Codex currently exposes no
    equivalent no-tools switch, so it is additionally placed in a blank
    temporary directory with read-only sandboxing and project/user rules
    disabled. The latter is defence in depth, not a general OS security
    boundary; the threat model documents that limitation explicitly.
    """
    if name == "codex":
        return AgentRunner(
            command=(
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "-",
            ),
            timeout=timeout,
            isolated=True,
        )
    return AgentRunner(
        command=(
            "claude",
            "-p",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
        ),
        timeout=timeout,
        isolated=True,
    )


def delegation_runner(name: str, workdir: Path, *, timeout: float = 600.0) -> AgentRunner:
    """Return the coding runner with an explicit, non-bypass permission mode."""
    if name == "codex":
        command = ("codex", "exec", "--sandbox", "workspace-write", "--ephemeral", "-")
    else:
        command = (
            "claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--no-session-persistence",
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
        )
    return AgentRunner(command=command, timeout=timeout, cwd=workdir.resolve())


def agent_environment() -> dict[str, str]:
    env = os.environ.copy()
    # A delegated model must not inherit operator-only LoreLoop locations or
    # passphrases. The marker also makes normal LoreLoop signing APIs refuse
    # calls originating from the agent process (see evidence.chain).
    for name in (
        "LORELOOP_KEY_DIR",
        "LORELOOP_KEY_PASSPHRASE",
        "LORELOOP_REGISTRY",
    ):
        env.pop(name, None)
    env["LORELOOP_AGENT_PROCESS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
