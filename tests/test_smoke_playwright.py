"""End-to-end smoke test against a real chromium via playwright.

Skipped when playwright is not installed; CI runs it in a dedicated job.
Serves a static page locally, observes it with the real browser, and walks
the deterministic verify path — no LLM anywhere, so the test is hermetic.
"""

import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

pytest.importorskip("playwright")

from knowhelm.evidence.artifacts import ArtifactStore  # noqa: E402
from knowhelm.evidence.chain import EvidenceChain  # noqa: E402
from knowhelm.webexplore.browser import PlaywrightBrowser  # noqa: E402
from knowhelm.webexplore.verify import verify_expectation  # noqa: E402

PAGE = """<!doctype html>
<html><head><title>Upload Console</title></head>
<body>
  <h1>Upload</h1>
  <p>Maximum file size is 50MB.</p>
  <form><input type="file" name="doc"><input type="submit"></form>
  <a href="/other.html">other</a>
</body></html>
"""


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


def test_observe_captures_title_text_forms_links(site, browser):
    obs = browser.observe(site)
    assert obs.title == "Upload Console"
    assert "Maximum file size is 50MB." in obs.text
    assert any("file" in f for f in obs.forms)
    assert any(link.endswith("/other.html") for link in obs.links)
    assert obs.snapshot_hash == browser.observe(site).snapshot_hash


def test_deterministic_verify_against_real_page(site, browser, tmp_path):
    workdir = tmp_path / "wd"
    (workdir / ".knowhelm").mkdir(parents=True)
    chain = EvidenceChain.for_workdir(workdir)
    artifacts = ArtifactStore.for_workdir(workdir)

    ok = verify_expectation(
        browser, NoLLM(), chain, "run-smoke", site, "contains:Maximum file size is 50MB",
        artifacts=artifacts,
    )
    assert ok.passed
    assert ok.record.payload["judge"] == "deterministic"
    loaded = artifacts.load(ok.record.payload["artifact"])
    assert "Maximum file size" in loaded["text"]

    bad = verify_expectation(
        browser, NoLLM(), chain, "run-smoke", site, "contains:no such text on page",
        artifacts=artifacts,
    )
    assert not bad.passed
    assert [r.event for r in chain.verify()] == ["check_passed", "check_failed"]
