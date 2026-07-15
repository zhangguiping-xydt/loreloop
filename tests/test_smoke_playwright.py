"""End-to-end smoke tests against a real Chromium via Playwright.

Skipped when playwright is not installed; CI runs it in a dedicated job.
Serves a static page locally and exercises both browser primitives and the
complete Web-to-baseline CLI loop. Model-shaped extraction and judging use a
deterministic local subprocess adapter, so no network model is involved.
"""

import json
import os
import re
import subprocess
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

import pytest

if os.environ.get("LORELOOP_REQUIRE_PLAYWRIGHT_E2E") == "1":
    __import__("playwright")
else:
    pytest.importorskip("playwright")

from loreloop.evidence.artifacts import ArtifactStore  # noqa: E402
from loreloop.evidence.chain import EvidenceChain  # noqa: E402
from loreloop.webexplore.actions import execute_action_script, parse_action_script  # noqa: E402
from loreloop.webexplore.browser import PlaywrightBrowser  # noqa: E402
from loreloop.webexplore.verify import verify_expectation, verify_script_expectation  # noqa: E402

PAGE = """<!doctype html>
<html><head><title>Upload Console</title></head>
<body>
  <h1>Upload</h1>
  <p>Maximum file size is 50MB.</p>
  <button onclick="document.getElementById('status').innerText = 'Filtered results: 1 item'">Filter</button>
  <p id="status"></p>
  <form><input type="file" name="doc"><input type="submit"></form>
  <a href="/other.html">other</a>
</body></html>
"""

ROOT = Path(__file__).parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _install_hermetic_codex(root: Path, site_url: str) -> dict[str, str]:
    """Install a prompt-aware local agent executable; browser/state remain real."""
    binary_dir = root / "agent-bin"
    binary_dir.mkdir()
    executable = binary_dir / "codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys

SITE_URL = {site_url}
prompt = sys.stdin.read()
if "prompt-version: web-extract-v2" in prompt:
    result = [{{
        "claim": "Maximum file size is 50MB.",
        "title": "Upload limit",
        "url": SITE_URL,
    }}]
elif "prompt-version: claim-classify-v2" in prompt:
    result = [{{"id": 0, "kind": "constraint"}}]
elif "You are verifying an acceptance expectation" in prompt:
    result = {{"passed": True, "reason": "The page states Maximum file size is 50MB."}}
else:
    sys.stderr.write("unexpected LoreLoop inference prompt")
    raise SystemExit(2)
print(json.dumps(result, ensure_ascii=False))
""".format(site_url=json.dumps(site_url)),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{binary_dir}{os.pathsep}{environment.get('PATH', '')}"
    source_path = str(ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{source_path}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else source_path
    )
    return environment


def _cli(
    cwd: Path,
    environment: dict[str, str],
    *arguments: str,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "loreloop.cli", *arguments],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert completed.returncode == 0, (
        f"command failed: loreloop {' '.join(arguments)}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed


class NoLLM:
    def run(self, prompt):
        raise AssertionError("deterministic path must never call the model")


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "index.html").write_text(PAGE)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


@pytest.fixture()
def browser():
    b = PlaywrightBrowser(headed=False)
    yield b
    b.close()


@pytest.fixture()
def write_site():
    posted = threading.Event()
    page = b"""<!doctype html><html><body>
    <button onclick="fetch('/write', {method: 'POST', body: 'x'})">Save</button>
    </body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):  # noqa: N802
            posted.set()
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", posted
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_observe_captures_title_text_forms_links(site, browser):
    obs = browser.observe(site)
    assert obs.title == "Upload Console"
    assert "Maximum file size is 50MB." in obs.text
    assert any("file" in f for f in obs.forms)
    assert any(link.endswith("/other.html") for link in obs.links)
    assert "Upload" in obs.headings
    assert "Filter" in obs.buttons
    assert obs.snapshot_hash == browser.observe(site).snapshot_hash


def test_observe_rejects_http_error_pages(site, browser):
    parsed = urlsplit(site)
    missing = f"{parsed.scheme}://{parsed.netloc}/missing.html"

    with pytest.raises(RuntimeError, match="HTTP 404"):
        browser.observe(missing)


def test_deterministic_verify_against_real_page(site, browser, tmp_path):
    workdir = tmp_path / "wd"
    (workdir / ".loreloop").mkdir(parents=True)
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)

    ok = verify_expectation(
        browser,
        NoLLM(),
        chain,
        "run-smoke",
        site,
        "contains:Maximum file size is 50MB",
        artifacts=artifacts,
    )
    assert ok.passed
    assert ok.record.payload["judge"] == "deterministic"
    loaded = artifacts.load(ok.record.payload["artifact"])
    assert "Maximum file size" in loaded["text"]

    bad = verify_expectation(
        browser,
        NoLLM(),
        chain,
        "run-smoke",
        site,
        "contains:no such text on page",
        artifacts=artifacts,
    )
    assert not bad.passed
    assert [r.event for r in chain.verify()] == ["check_passed", "check_failed"]


def test_script_verify_against_real_page(site, browser, tmp_path):
    workdir = tmp_path / "wd"
    (workdir / ".loreloop").mkdir(parents=True)
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)
    parsed = urlsplit(site)
    base = f"{parsed.scheme}://{parsed.netloc}"
    script = parse_action_script(
        {
            "version": 1,
            "base": base,
            "steps": [
                {"goto": "/index.html"},
                {"click": {"text": "Filter", "role": "button"}},
                {"wait": {"text": "Filtered results: 1 item"}},
            ],
        }
    )

    result = verify_script_expectation(
        browser,
        NoLLM(),
        chain,
        "run-smoke",
        base,
        script,
        "contains:Filtered results: 1 item",
        artifacts=artifacts,
    )

    assert result.passed
    rec = chain.verify()[0]
    assert rec.payload["script_digest"] == script.digest
    assert artifacts.load(rec.payload["trace_artifact"])["status"] == "completed"


def test_real_browser_blocks_javascript_post_without_allow_writes(write_site, browser):
    base, posted = write_site
    script = parse_action_script(
        {
            "version": 1,
            "base": base,
            "steps": [
                {"goto": "/"},
                {"click": {"text": "Save", "role": "button"}},
            ],
        }
    )

    blocked = execute_action_script(browser, script, allow_writes=False)

    assert blocked.status == "blocked"
    assert "blocked POST request (write-method)" in blocked.reason
    assert blocked.allow_writes is False
    assert not posted.is_set()

    allowed = execute_action_script(browser, script, allow_writes=True)
    assert allowed.succeeded
    assert allowed.allow_writes is True
    assert posted.wait(2)


def test_real_web_cli_loop_updates_and_searches_baseline(site, tmp_path):
    """Real Chromium -> governed update -> replay -> expanded no-import ZIP search."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LoreLoop E2E")
    _git(repo, "config", "user.email", "loreloop-e2e@example.invalid")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    environment = _install_hermetic_codex(tmp_path, site)

    initialized = _cli(repo, environment, "init", "--no-skill")
    assert "local trust: ready" in initialized.stdout
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore LoreLoop state")

    package = tmp_path / "baseline.zip"
    source_export = _cli(
        repo,
        environment,
        "knowledge",
        "export",
        "--format",
        "package",
        "--output",
        str(package),
    )
    assert "ZIP package" in source_export.stdout
    source_baseline = package.read_bytes()
    source_replay = _cli(repo, environment, "knowledge", "replay", str(package))
    assert "Capsule replay: no_key" in source_replay.stdout
    with zipfile.ZipFile(package) as archive:
        source_capsule = json.loads(archive.read(".loreloop-export.json"))
        source_rendered = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".md")
        )
    assert "Maximum file size is 50MB." not in source_rendered

    ingested = _cli(
        repo,
        environment,
        "--agent",
        "codex",
        "ingest",
        "--from",
        "web",
        site,
        "--max-pages",
        "1",
    )
    assert "ingested 1 knowledge entries" in ingested.stdout
    assert "explored 1 pages" in ingested.stderr

    listed = _cli(repo, environment, "knowledge", "list", "--channel", "web")
    match = re.search(r"(?m)^([0-9a-f]{8})\s+", listed.stdout)
    assert match is not None
    entry_prefix = match.group(1)

    approved = _cli(repo, environment, "knowledge", "approve", entry_prefix)
    assert "approved:" in approved.stdout
    verified = _cli(
        repo,
        environment,
        "--agent",
        "codex",
        "knowledge",
        "verify",
        entry_prefix,
    )
    assert "VERIFIED: Upload limit" in verified.stdout

    exported = _cli(
        repo,
        environment,
        "knowledge",
        "export",
        "--format",
        "package",
        "--output",
        str(package),
        "--include-web",
        "--force",
    )
    assert "included 1 approved and verified Web knowledge entries" in exported.stderr
    assert package.read_bytes() != source_baseline
    replayed = _cli(repo, environment, "knowledge", "replay", str(package))
    assert "Capsule replay: no_key" in replayed.stdout
    with zipfile.ZipFile(package) as archive:
        updated_capsule = json.loads(archive.read(".loreloop-export.json"))
        rendered = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".md")
        )
    assert "Maximum file size is 50MB." in rendered
    assert (
        updated_capsule["semantic_core"]["source_snapshot_sha256"]
        == source_capsule["semantic_core"]["source_snapshot_sha256"]
    )
    assert (
        updated_capsule["semantic_core"]["repository_config_digest"]
        == source_capsule["semantic_core"]["repository_config_digest"]
    )

    isolated = tmp_path / "search-only"
    isolated.mkdir()
    plain = _cli(
        isolated,
        environment,
        "knowledge",
        "search",
        "附件容量阈值",
        "--package",
        str(package),
    )
    assert "no matching baseline records" in plain.stdout
    searched = _cli(
        isolated,
        environment,
        "knowledge",
        "search",
        "附件容量阈值",
        "--package",
        str(package),
        "--expand",
        "Maximum file size upload limit 50MB",
    )
    assert "Maximum file size is 50MB." in searched.stdout
    assert "verifying baseline and building a transient search index" in searched.stderr
    assert not (isolated / ".loreloop").exists()
