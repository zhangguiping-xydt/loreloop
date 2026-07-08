import json
import subprocess

import pytest

from knowhelm.evidence.artifacts import ArtifactStore
from knowhelm.evidence.chain import EvidenceChain
from knowhelm.knowledge.harvest import HarvestError, harvest_run
from knowhelm.knowledge.model import Channel, Curation, Entry, Kind, Source, Verification
from knowhelm.knowledge.store import KnowledgeStore
from knowhelm.report.acceptance import load_run
from knowhelm.webexplore.browser import Observation


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


def record_browser_check(
    chain, run_id, check="upload rejects files over 50MB", artifacts=None
):
    payload = {
        "run_id": run_id, "check": check, "url": "http://app.local/upload",
        "page_snapshot": "snap123", "verified_via": "browser", "judge": "deterministic",
    }
    if artifacts is not None:
        obs = Observation(url="http://app.local/upload", title="Upload", text="Max 50MB.")
        payload["artifact"] = artifacts.save_observation(obs)[0]
        payload["page_snapshot"] = obs.snapshot_hash
    return chain.append("check_passed", payload)


@pytest.fixture()
def env(tmp_path):
    repo = make_repo(tmp_path)
    (tmp_path / ".knowhelm").mkdir()
    store = KnowledgeStore(tmp_path / ".knowhelm/knowledge.db")
    chain = EvidenceChain.for_workdir(tmp_path)
    artifacts = ArtifactStore.for_workdir(tmp_path)
    yield repo, store, chain, artifacts
    store.close()


def test_harvest_refuses_non_accepted_run(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo), finished=False)
    with pytest.raises(HarvestError, match="not ACCEPTED"):
        harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)


def test_harvest_refuses_dirty_working_tree(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x", artifacts=artifacts)
    (repo / "api.py").write_text("def upload(): return 500  # uncommitted\n")

    with pytest.raises(HarvestError, match="uncommitted source changes"):
        harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)


def test_harvest_mints_browser_checks_as_born_verified(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x", artifacts=artifacts)

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)

    assert len(result.minted) == 1
    stored = store.get(result.minted[0].id)
    assert stored.kind is Kind.ACCEPTANCE
    assert stored.source.channel is Channel.WEB
    assert stored.source.locator == "http://app.local/upload"
    assert stored.source.snapshot_ref
    assert stored.trust.verification is Verification.VERIFIED
    assert stored.trust.verified_by == "run-x"
    assert stored.trust.curation is Curation.DRAFT
    assert stored.is_strong_evidence()
    assert not result.reversed_entries


def test_harvest_skips_non_browser_checks(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo))
    chain.append("check_passed", {"run_id": "run-x", "check": "tests pass"})

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)

    assert result.minted == []
    assert result.unauditable_checks == []


def test_harvest_never_mints_browser_check_without_artifact(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x")  # no artifact recorded

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)

    assert result.minted == []
    assert result.unauditable_checks == ["upload rejects files over 50MB"]
    rec = next(r for r in chain.verify() if r.event == "knowledge_harvested")
    assert rec.payload["unauditable_checks"] == ["upload rejects files over 50MB"]


def test_harvest_reverses_changed_files_as_draft(env):
    repo, store, chain, artifacts = env
    base = head_of(repo)
    trace = write_trace(repo, "run-x", base)
    record_browser_check(chain, "run-x", artifacts=artifacts)

    (repo / "api.py").write_text("def upload(): return 201\ndef delete(): return 204\n")
    git(repo, "add", "api.py")
    git(repo, "commit", "-m", "add delete")

    extract = json.dumps([{
        "claim": "DELETE returns 204.", "title": "Delete contract", "file": "api.py",
    }])
    classify = json.dumps([{"id": 0, "kind": "interface"}])
    result = harvest_run(
        load_run(trace), chain, store, FakeRunner([extract, classify]), repo,
        artifacts=artifacts,
    )

    assert len(result.reversed_entries) == 1
    stored = store.get(result.reversed_entries[0].id)
    assert stored.trust.curation is Curation.DRAFT
    assert stored.trust.verification is Verification.UNVERIFIED
    assert not stored.is_strong_evidence()
    assert stored.source.snapshot_ref == head_of(repo)


def test_harvest_reports_stale_entries_without_touching_them(env):
    repo, store, chain, artifacts = env
    base = head_of(repo)
    old = Entry(
        title="Old upload contract", content="POST /upload returns 200.",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator=f"api.py@{base}", snapshot_ref=base),
    )
    store.add(old)
    trace = write_trace(repo, "run-x", base)
    record_browser_check(chain, "run-x", artifacts=artifacts)

    (repo / "api.py").write_text("def upload(): return 201  # changed\n")
    git(repo, "add", "api.py")
    git(repo, "commit", "-m", "change upload")

    extract = json.dumps([{
        "claim": "POST /upload returns 201.", "title": "Upload contract", "file": "api.py",
    }])
    classify = json.dumps([{"id": 0, "kind": "interface"}])
    result = harvest_run(
        load_run(trace), chain, store, FakeRunner([extract, classify]), repo,
        artifacts=artifacts,
    )

    assert [e.id for e in result.stale] == [old.id]
    untouched = store.get(old.id)
    assert untouched.trust.curation is Curation.DRAFT
    assert untouched.source.snapshot_ref == base


def test_harvest_flags_entries_in_deleted_files_as_stale(env):
    repo, store, chain, artifacts = env
    base = head_of(repo)
    old = Entry(
        title="Doomed contract", content="api.py exposes upload().",
        kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator=f"api.py@{base}", snapshot_ref=base),
    )
    store.add(old)
    trace = write_trace(repo, "run-x", base)
    record_browser_check(chain, "run-x", artifacts=artifacts)

    git(repo, "rm", "api.py")
    (repo / "handlers.py").write_text("def upload(): return 201\n")
    git(repo, "add", "handlers.py")
    git(repo, "commit", "-m", "rename api to handlers")

    extract = json.dumps([{
        "claim": "handlers.py exposes upload().", "title": "Upload handler",
        "file": "handlers.py",
    }])
    classify = json.dumps([{"id": 0, "kind": "interface"}])
    result = harvest_run(
        load_run(trace), chain, store, FakeRunner([extract, classify]), repo,
        artifacts=artifacts,
    )

    assert [e.id for e in result.stale] == [old.id]
    assert len(result.reversed_entries) == 1


def test_harvest_is_idempotent_via_chain(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x", artifacts=artifacts)

    harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)
    with pytest.raises(HarvestError, match="already harvested"):
        harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)

    events = [r.event for r in chain.verify()]
    assert events.count("knowledge_harvested") == 1


def test_harvest_dedupes_repeated_checks(env):
    repo, store, chain, artifacts = env
    trace = write_trace(repo, "run-x", head_of(repo))
    record_browser_check(chain, "run-x", artifacts=artifacts)
    record_browser_check(chain, "run-x", artifacts=artifacts)

    result = harvest_run(load_run(trace), chain, store, FakeRunner([]), repo, artifacts=artifacts)

    assert len(result.minted) == 1
