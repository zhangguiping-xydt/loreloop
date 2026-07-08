"""Thin adapter over local agent CLIs (claude -p / codex exec).

knowhelm never calls a model API directly: it reuses the coding-agent CLI the
user already has, so there is no API key configuration of its own.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class AgentError(Exception):
    pass


@dataclass(frozen=True)
class AgentRunner:
    command: tuple[str, ...] = ("claude", "-p")
    timeout: float = 600.0

    def run(self, prompt: str) -> str:
        try:
            proc = subprocess.run(
                list(self.command),
                input=prompt,
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


CODEX_RUNNER = AgentRunner(command=("codex", "exec", "-"))
