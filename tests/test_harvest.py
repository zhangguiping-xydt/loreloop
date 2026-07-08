import json
import subprocess

import pytest

from knowhelm.evidence.chain import EvidenceChain
from knowhelm.knowledge.harvest import HarvestError, harvest_run
from knowhelm.knowledge.model import Channel, Curation, Entry, Kind, Source, Verification
from knowhelm.knowledge.store import KnowledgeStore
from knowhelm.report.acceptance import load_run


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path):
    repo = tmp_path
    git(repo, "init")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "api.py").write_text("def upload(): return 201\n")
    git(repo, "add", "api.py")
    git(repo, "commit", "-m", "base")
    return repo


def head_of(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def write_trace(workdir, run_id, base_commit, finished=True):
    runs = workdir / ".knowhelm/runs"
    runs.mkdir(parents=True, exist_ok=True)
    events = [{
        "ts": "t0", "event": "delegation_started", "task": "fix upload",
        "context_entries": [], "base_commit": base_commit,
    }]
    if finished:
        events.append({"ts": "t1", "event": "delegation_finished", "output_chars": 1})
    path = runs / f"{run_id}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def record_browser_check(chain, run_id, check="upload rejects files over 50MB"):
    return chain.append("check_passed", {
        "run_id": run_id, "check": check, "url": "http://app.local/upload",
        "page_snapshot": "snap123", "verified_via": "browser", "judge": "deterministic",
    })


@pytest.fixture()
def env(tmp_path):
    repo = make_repo(tmp_path)
    (tmp_path / ".knowhelm").mkdir()
    store = KnowledgeStore(tmp_path / ".knowhelm/knowledge.db")
    chain = EvidenceChain.for_workdir(tmp_path)
    yield repo, store, chain
    store.close()


def test_harvest_refuses_non_accepted_run(env):
    repo, store, chain = env
    trace = write_trace(repo, "run-x", head_of(repo), finished=False)
    with pytest.raises(HarvestError, match="not ACCEPTED"):
        harvest_run(load_run(trace), chain, store, FakeRunner([]), repo)


def test_harvest_mints_browser_checks_as_born_verified(env):
    repo, store, chain = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x")

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo)

    assert len(result.minted) == 1
    stored = store.get(result.minted[0].id)
    assert stored.kind is Kind.ACCEPTANCE
    assert stored.source.channel is Channel.WEB
    assert stored.source.locator == "http://app.local/upload"
    assert stored.source.snapshot_ref == "snap123"
    assert stored.trust.verification is Verification.VERIFIED
    assert stored.trust.verified_by == "run-x"
    assert stored.trust.curation is Curation.DRAFT
    assert stored.is_strong_evidence()
    assert not result.reversed_entries


def test_harvest_skips_non_browser_checks(env):
    repo, store, chain = env
    trace = write_trace(repo, "run-x", head_of(repo))
    chain.append("check_passed", {"run_id": "run-x", "check": "tests pass"})

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo)

    assert result.minted == []


def test_harvest_reverses_changed_files_as_draft(env):
    repo, store, chain = env
    base = head_of(repo)
    trace = write_trace(repo, "run-x", base)
    record_browser_check(chain, "run-x")

    (repo / "api.py").write_text("def upload(): return 201\ndef delete(): return 204\n")
    git(repo, "add", "api.py")
    git(repo, "commit", "-m", "add delete")

    extract = json.dumps([{
        "claim": "DELETE returns 204.", "title": "Delete contract", "file": "api.py",
    }])
    classify = json.dumps([{"id": 0, "kind": "interface"}])
    result = harvest_run(load_run(trace), chain, store, FakeRunner([extract, classify]), repo)

    assert len(result.reversed_entries) == 1
    stored = store.get(result.reversed_entries[0].id)
    assert stored.trust.curation is Curation.DRAFT
    assert stored.trust.verification is Verification.UNVERIFIED
    assert not stored.is_strong_evidence()
    assert stored.source.snapshot_ref == head_of(repo)


def test_harvest_reports_stale_entries_without_touching_them(env):
    repo, store, chain = env
    base = head_of(repo)
    old = Entry(
        title="Old upload contract", content="POST /upload returns 200.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator=f"api.py@{base}", snapshot_ref=base),
    )
    store.add(old)
    trace = write_trace(repo, "run-x", base)
    record_browser_check(chain, "run-x")

    (repo / "api.py").write_text("def upload(): return 201  # changed\n")
    git(repo, "add", "api.py")
    git(repo, "commit", "-m", "change upload")

    extract = json.dumps([{
        "claim": "POST /upload returns 201.", "title": "Upload contract", "file": "api.py",
    }])
    classify = json.dumps([{"id": 0, "kind": "interface"}])
    result = harvest_run(load_run(trace), chain, store, FakeRunner([extract, classify]), repo)

    assert [e.id for e in result.stale] == [old.id]
    untouched = store.get(old.id)
    assert untouched.trust.curation is Curation.DRAFT
    assert untouched.source.snapshot_ref == base


def test_harvest_is_idempotent_via_chain(env):
    repo, store, chain = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x")

    harvest_run(load_run(trace), chain, store, FakeRunner([]), repo)
    with pytest.raises(HarvestError, match="already harvested"):
        harvest_run(load_run(trace), chain, store, FakeRunner([]), repo)

    events = [r.event for r in chain.verify()]
    assert events.count("knowledge_harvested") == 1


def test_harvest_dedupes_repeated_checks(env):
    repo, store, chain = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x")
    record_browser_check(chain, "run-x")

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo)

    assert len(result.minted) == 1
