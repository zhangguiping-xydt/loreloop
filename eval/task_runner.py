#!/usr/bin/env python3
# ruff: noqa: E402
"""Run real coding-agent tasks with and without knowhelm context.

The agent sees only the public fixture and task. Hidden evaluators stay outside
the copied repository and run after the agent exits. Every run stores stdout,
stderr, duration, git diff and evaluator output for independent inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.metrics import evaluate_task_runs
from knowhelm.delegate.context_pack import ContextPack, render
from knowhelm.knowledge.model import Channel, Curation, Entry, Kind, Source, Trust

TASK_ROOT = ROOT / "eval/tasks"
AGENT_COMMANDS = {
    "codex": ("codex", "exec", "--sandbox", "workspace-write", "--ephemeral", "-"),
    "claude": (
        "claude", "-p", "--permission-mode", "acceptEdits", "--no-session-persistence",
    ),
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)=([^\r\n]*)"
)
_SECRET_LABEL = re.compile(
    r"(?im)\b(password|token|secret|api[_ -]?key)\s*[:=]\s*([^\s,;]+)"
)
_MAX_TRANSCRIPT_CHARS = 20_000


def run_task(
    spec: dict[str, Any],
    *,
    agent: str,
    variant: str,
    timeout: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"knowhelm-task-{spec['id']}-") as temp:
        repo = Path(temp) / "repo"
        shutil.copytree(
            TASK_ROOT / spec["repo"],
            repo,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "eval@knowhelm.local")
        _git(repo, "config", "user.name", "knowhelm eval")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "task fixture")
        prompt = _task_prompt(spec, variant, repo)
        started = time.monotonic()
        try:
            agent_env = os.environ.copy()
            agent_env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.run(
                AGENT_COMMANDS[agent],
                input=prompt,
                cwd=repo,
                env=agent_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            timed_out = False
            agent_exit = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            agent_exit = None
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr)
        duration = time.monotonic() - started
        test_env = os.environ.copy()
        test_env["PYTHONDONTWRITEBYTECODE"] = "1"
        public = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-v"],
            cwd=repo,
            env=test_env,
            capture_output=True,
            text=True,
        )
        env = test_env.copy()
        env["PYTHONPATH"] = str(repo)
        hidden = subprocess.run(
            [sys.executable, str(TASK_ROOT / spec["evaluator"])],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        diff = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--",
                ".",
                ":(exclude)**/__pycache__/**",
                ":(exclude)**/*.pyc",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout
        passed = (
            not timed_out
            and agent_exit == 0
            and public.returncode == 0
            and hidden.returncode == 0
        )
        return {
            "task": spec["id"],
            "agent": agent,
            "variant": variant,
            "passed": passed,
            "duration_seconds": round(duration, 3),
            "timed_out": timed_out,
            "agent_exit_code": agent_exit,
            "agent_stdout": _redact_transcript(stdout),
            "agent_stderr": _redact_transcript(stderr),
            "public_test_exit_code": public.returncode,
            "public_test_output": _portable_output(public.stdout + public.stderr, repo),
            "hidden_test_exit_code": hidden.returncode,
            "hidden_test_output": _portable_output(hidden.stdout + hidden.stderr, repo),
            "diff": diff,
        }


def _task_prompt(spec: dict[str, Any], variant: str, repo: Path | None = None) -> str:
    context = ""
    if variant == "knowhelm":
        entries = [
            Entry(
                title=item["title"],
                content=item["content"],
                kind=Kind(item["kind"]),
                source=Source(channel=Channel.MANUAL, locator=f"eval:{spec['id']}"),
                trust=Trust(curation=Curation.APPROVED),
            )
            for item in spec["knowledge"]
        ]
        context = render(ContextPack(strong=entries, reference=[])) + "\n\n"
    elif variant == "session_memory":
        notes = "\n".join(
            f"- {item['title']}: {item['content']}" for item in spec["knowledge"]
        )
        context = (
            "# Prior session memory (unverified and ephemeral)\n\n"
            f"{notes}\n\n"
        )
    elif variant == "codebase_index":
        if repo is None:
            raise ValueError("codebase_index variant requires the copied repository")
        context = _render_codebase_index(repo) + "\n\n"
    return (
        context
        + "# Task\n\n"
        + spec["task"]
        + "\n\nWork directly in this repository. Complete the implementation, run the public "
          "tests, and leave the working tree with the solution applied. Do not inspect or print "
          "environment variables, credentials, tokens, or unrelated files outside the repository."
    )


def _render_codebase_index(repo: Path) -> str:
    """A bounded lexical-index baseline made only from public repository files."""
    blocks = []
    total = 0
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or ".git" in path.parts or path.suffix not in {".py", ".ts", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:8_000]
        block = f"## {path.relative_to(repo)}\n\n```\n{text}\n```"
        if total + len(block) > 24_000:
            break
        blocks.append(block)
        total += len(block)
    return (
        "# Codebase index results (source snippets, no external project memory)\n\n"
        + "\n\n".join(blocks)
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _redact_transcript(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", value)
    redacted = _SECRET_LABEL.sub(r"\1: <redacted>", redacted)
    for name, secret in os.environ.items():
        upper = name.upper()
        if secret and len(secret) >= 6 and any(
            marker in upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
        ):
            redacted = redacted.replace(secret, "<redacted>")
    if len(redacted) > _MAX_TRANSCRIPT_CHARS:
        redacted = "[transcript truncated]\n" + redacted[-_MAX_TRANSCRIPT_CHARS:]
    return redacted


def _portable_output(value: str, repo: Path) -> str:
    return value.replace(str(repo), "<task-repo>").replace(str(ROOT), "<repo-root>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=sorted(AGENT_COMMANDS), required=True)
    parser.add_argument("--task", action="append", help="task id; repeatable, defaults to all")
    parser.add_argument(
        "--variant",
        choices=[
            "all",
            "both",
            "no_memory",
            "no_knowledge",
            "session_memory",
            "codebase_index",
            "knowhelm",
        ],
        default="both",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.repetitions < 1 or args.timeout <= 0:
        parser.error("repetitions and timeout must be positive")
    specs = json.loads((TASK_ROOT / "tasks.json").read_text(encoding="utf-8"))
    selected = [spec for spec in specs if not args.task or spec["id"] in args.task]
    missing = set(args.task or []) - {spec["id"] for spec in selected}
    if missing:
        parser.error(f"unknown task(s): {', '.join(sorted(missing))}")
    if args.variant == "all":
        variants = ["no_memory", "session_memory", "codebase_index", "knowhelm"]
    elif args.variant == "both":
        variants = ["no_memory", "knowhelm"]
    else:
        variants = [args.variant]
    runs = []
    for _ in range(args.repetitions):
        for spec in selected:
            for variant in variants:
                print(f"running {spec['id']} [{variant}] with {args.agent}", file=sys.stderr)
                runs.append(run_task(spec, agent=args.agent, variant=variant, timeout=args.timeout))
    result = {
        "benchmark": "coding-task-success",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agent": args.agent,
        "metrics": evaluate_task_runs(runs),
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    return 0 if all(run["passed"] for run in runs if run["variant"] == "knowhelm") else 1


if __name__ == "__main__":
    raise SystemExit(main())
