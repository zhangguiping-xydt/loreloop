"""Run the complete LoreLoop cycle against the bundled legacy application.

Default mode uses the selected local coding-agent CLI and Playwright. ``--offline``
uses deterministic adapters so CI can prove the same first-run plumbing on every
supported OS without credentials or a browser download.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class DemoError(RuntimeError):
    pass


class OfflineDemoAgent:
    def __init__(self, project: Path) -> None:
        self.project = project

    def run(self, prompt: str) -> str:
        if "Classify each knowledge assertion" in prompt:
            return json.dumps([{"id": 0, "kind": "constraint"}])
        if "You are extracting project knowledge" in prompt:
            source = (self.project / "app.py").read_text(encoding="utf-8")
            limit = int(re.search(r"MAX_UPLOAD_MIB = (\d+)", source).group(1))
            line = next(
                index
                for index, text in enumerate(source.splitlines(), start=1)
                if text.startswith("MAX_UPLOAD_MIB =")
            )
            return json.dumps(
                [
                    {
                        "claim": f"Uploads are limited to {limit} MiB.",
                        "title": "Upload size ceiling",
                        "file": "app.py",
                        "evidence": {
                            "line_start": line,
                            "line_end": line,
                            "symbol": "MAX_UPLOAD_MIB",
                            "excerpt": f"MAX_UPLOAD_MIB = {limit}",
                        },
                    }
                ]
            )
        if "# Task" in prompt or "Raise the legacy upload ceiling" in prompt:
            for name in ("app.py", "test_contract.py"):
                path = self.project / name
                text = path.read_text(encoding="utf-8")
                path.write_text(
                    text.replace("MAX_UPLOAD_MIB = 5", "MAX_UPLOAD_MIB = 8")
                    .replace("MAX_UPLOAD_MIB == 5", "MAX_UPLOAD_MIB == 8")
                    .replace("upload_allowed(5)", "upload_allowed(8)")
                    .replace("upload_allowed(6)", "upload_allowed(9)"),
                    encoding="utf-8",
                )
            return "Updated the upload ceiling and its contract test from 5 MiB to 8 MiB."
        raise RuntimeError("offline demo received an unexpected agent prompt")


class OfflineBrowser:
    def __init__(self, project: Path, headed: bool = False) -> None:
        self.project = project
        self.headed = headed

    def observe(self, url: str):
        from loreloop.webexplore.browser import Observation

        source = (self.project / "app.py").read_text(encoding="utf-8")
        limit = re.search(r"MAX_UPLOAD_MIB = (\d+)", source).group(1)
        return Observation(
            url=url,
            title="Legacy Upload",
            text=f"Legacy Upload Upload limit: {limit} MiB",
        )

    def close(self) -> None:
        pass


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True, text=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise DemoError(f"demo readiness URL must be local HTTP: {url}")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            # B310 is safe here because the scheme and loopback host are checked above.
            with urllib.request.urlopen(url, timeout=1):  # nosec B310
                return
        except OSError:
            time.sleep(0.1)
    raise DemoError(f"demo server did not become ready: {url}")


def _step(cli_main, argv: list[str]) -> None:
    print(f"\n$ loreloop {' '.join(argv)}", flush=True)
    status = cli_main(argv)
    if status != 0:
        raise DemoError(f"step failed with exit code {status}: {' '.join(argv)}")


def run_demo(workspace: Path, *, agent: str, offline: bool) -> Path:
    from loreloop import cli

    template = Path(__file__).with_name("example") / "legacy_upload"
    project = workspace / "legacy-upload"
    shutil.copytree(template, project)
    _git(project, "init")
    _git(project, "config", "user.email", "demo@loreloop.local")
    _git(project, "config", "user.name", "LoreLoop demo")
    _git(project, "add", "-A")
    _git(project, "commit", "-m", "baseline legacy upload service")

    previous_cwd = Path.cwd()
    previous_key_dir = os.environ.get("LORELOOP_KEY_DIR")
    previous_agent_factory = cli._agent
    previous_inference_factory = cli._inference_agent
    browser_module = None
    previous_browser = None
    os.environ["LORELOOP_KEY_DIR"] = str(workspace / "keys")
    if offline:
        demo_agent = OfflineDemoAgent(project)
        cli._agent = lambda _name: demo_agent
        cli._inference_agent = lambda _name: demo_agent
        import loreloop.webexplore.browser as browser_module

        previous_browser = browser_module.PlaywrightBrowser
        browser_module.PlaywrightBrowser = lambda headed=False: OfflineBrowser(project, headed)
    server = None
    try:
        os.chdir(project)
        _step(cli.main, ["init", "--no-skill"])
        _step(cli.main, ["--agent", agent, "ingest", "--from", "code", "."])
        from loreloop.knowledge.store import KnowledgeStore

        with KnowledgeStore(project / ".loreloop/knowledge.db") as store:
            original = store.list()[0]
        _step(cli.main, ["knowledge", "show", original.id[:8]])
        _step(cli.main, ["knowledge", "approve", original.id[:8]])
        _step(
            cli.main,
            [
                "--agent",
                agent,
                "run",
                "--no-expand",
                "Raise the legacy upload ceiling from 5 MiB to 8 MiB and update its contract test.",
            ],
        )
        run_id = max((project / ".loreloop/runs").glob("run-*.jsonl")).stem
        _git(project, "add", "-A")
        _git(project, "commit", "-m", "raise upload ceiling to 8 MiB")

        port = 8765 if offline else _free_port()
        url = f"http://127.0.0.1:{port}/"
        if not offline:
            server = subprocess.Popen(
                [sys.executable, "app.py", "--port", str(port)],
                cwd=project,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait_for(url)
        _step(cli.main, ["--agent", agent, "verify", run_id, url, "contains:Upload limit: 8 MiB"])
        _step(cli.main, ["report", run_id])
        _step(cli.main, ["--agent", agent, "harvest", run_id])
        _step(cli.main, ["knowledge", "review", "--stale"])
        with KnowledgeStore(project / ".loreloop/knowledge.db") as store:
            updated = next(
                entry
                for entry in store.list()
                if entry.source.channel.value == "code" and "8 MiB" in entry.content
            )
            acceptance = next(entry for entry in store.list() if entry.kind.value == "acceptance")
        _step(cli.main, ["knowledge", "show", updated.id[:8]])
        _step(cli.main, ["knowledge", "approve", updated.id[:8]])
        _step(cli.main, ["knowledge", "show", acceptance.id[:8]])
        _step(cli.main, ["knowledge", "approve", acceptance.id[:8]])
        _step(
            cli.main,
            ["knowledge", "supersede", updated.id[:8], original.id[:8], "--yes"],
        )
        _step(cli.main, ["knowledge", "list", "--active"])
        _step(cli.main, ["knowledge", "review", "--status", "draft"])
        print(f"\nComplete: the reproducible demo workspace is {project}")
        print("Continue with:")
        print(f"  cd {project}")
        print("  loreloop trust status")
        print("  loreloop knowledge review --status draft")
        return project
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=5)
        os.chdir(previous_cwd)
        if previous_key_dir is None:
            os.environ.pop("LORELOOP_KEY_DIR", None)
        else:
            os.environ["LORELOOP_KEY_DIR"] = previous_key_dir
        cli._agent = previous_agent_factory
        cli._inference_agent = previous_inference_factory
        if browser_module is not None and previous_browser is not None:
            browser_module.PlaywrightBrowser = previous_browser


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["claude", "codex", "co-mind"], default="claude")
    parser.add_argument("--offline", action="store_true", help="use deterministic CI adapters")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="loreloop-demo-"))
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        run_demo(workspace.resolve(), agent=args.agent, offline=args.offline)
    except Exception as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
