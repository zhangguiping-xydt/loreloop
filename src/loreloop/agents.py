"""Thin adapters over supported local coding-agent CLIs.

loreloop never calls a model API directly: it reuses the coding-agent CLI the
user already has, so there is no API key configuration of its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
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
    prompt_as_argument: bool = False
    environment: tuple[tuple[str, str], ...] = ()

    def run(self, prompt: str) -> str:
        if self.isolated:
            with tempfile.TemporaryDirectory(prefix="loreloop-inference-") as temp:
                return self._run(prompt, Path(temp))
        return self._run(prompt, self.cwd)

    def _run(self, prompt: str, cwd: Path | None) -> str:
        command = list(self.command)
        stdin = prompt
        if self.prompt_as_argument:
            command.append(prompt)
            stdin = None
        env = agent_environment()
        env.update(self.environment)
        try:
            proc = subprocess.run(
                command,
                input=stdin,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise AgentError(
                f"agent CLI not found: {self.command[0]!r}. Install the selected supported "
                "coding agent and make sure it is on your PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"agent call timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "no diagnostic output"
            raise AgentError(f"agent exited with code {proc.returncode}: {detail[:500]}")
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
                *_codex_connection_args(),
                "-",
            ),
            timeout=timeout,
            isolated=True,
        )
    if name == "opencode":
        inline = json.dumps(
            {
                "plugin": [],
                "tools": {
                    "bash": False,
                    "edit": False,
                    "write": False,
                    "skill": False,
                },
                "permission": {"*": "deny"},
                "share": "disabled",
            },
            separators=(",", ":"),
        )
        return AgentRunner(
            command=("opencode", "run", "--format", "default"),
            timeout=timeout,
            isolated=True,
            prompt_as_argument=True,
            environment=(("OPENCODE_CONFIG_CONTENT", inline),),
        )
    executable = "co-mind" if name == "co-mind" else "claude"
    return AgentRunner(
        command=(
            executable,
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


def _codex_connection_args() -> tuple[str, ...]:
    """Preserve only model/provider connectivity while ignoring user capabilities.

    ``--ignore-user-config`` is important for inference isolation: user MCP
    servers, hooks, rules, and other capabilities must not reach an extraction
    subprocess. Custom Codex providers are connection metadata rather than a
    capability, though, and dropping them can silently route requests to an
    unreachable default endpoint. Read a deliberately small allowlist and
    replay it through explicit ``-c`` overrides; static headers and all other
    config remain excluded.
    """
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    try:
        config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        config = {}

    model = os.environ.get("LORELOOP_CODEX_MODEL") or config.get("model")
    effort = os.environ.get("LORELOOP_CODEX_REASONING_EFFORT") or config.get(
        "model_reasoning_effort"
    )
    provider = os.environ.get("LORELOOP_CODEX_PROVIDER") or config.get("model_provider")

    overrides: list[tuple[str, object]] = []
    for key, value in (
        ("model", model),
        ("model_reasoning_effort", effort),
        ("model_provider", provider),
    ):
        if isinstance(value, (str, bool, int, float)):
            overrides.append((key, value))

    providers = config.get("model_providers")
    provider_config = providers.get(provider) if isinstance(providers, dict) else None
    if isinstance(provider, str) and isinstance(provider_config, dict):
        for key in (
            "name",
            "base_url",
            "env_key",
            "wire_api",
            "requires_openai_auth",
            "supports_websockets",
        ):
            value = provider_config.get(key)
            if isinstance(value, (str, bool, int, float)):
                overrides.append((f"model_providers.{provider}.{key}", value))

    args: list[str] = []
    for key, value in overrides:
        rendered = json.dumps(value) if isinstance(value, str) else str(value).lower()
        args.extend(("-c", f"{key}={rendered}"))
    return tuple(args)


def delegation_runner(name: str, workdir: Path, *, timeout: float = 600.0) -> AgentRunner:
    """Return the coding runner with an explicit, non-bypass permission mode."""
    if name == "codex":
        command = ("codex", "exec", "--sandbox", "workspace-write", "--ephemeral", "-")
    elif name == "opencode":
        raise AgentError(
            "OpenCode headless delegation is not enabled because its CLI does not expose "
            "a workspace sandbox equivalent. Use `loreloop begin` inside the current "
            "OpenCode session instead."
        )
    else:
        executable = "co-mind" if name == "co-mind" else "claude"
        command = (
            executable,
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
        "LORELOOP_TRUST_REGISTRY",
    ):
        env.pop(name, None)
    env["LORELOOP_AGENT_PROCESS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
