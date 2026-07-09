
import pytest

from knowhelm.evidence.chain import EvidenceChain
from knowhelm.knowledge.model import Channel, Entry, Kind, Source, Verification
from knowhelm.knowledge.store import KnowledgeStore
from knowhelm.webexplore.browser import Observation
from knowhelm.webexplore.verify import verify_entry

PAGE = Observation(
    url="http://app.local/upload",
    title="Upload",
    text="Select a file. Max 50MB.",
    forms=["input:file:document"],
)


class FakeBrowser:
    def observe(self, url):
        assert url == PAGE.url
        return PAGE

    def close(self):
        pass


class FakeRunner:
    def __init__(self, output):
        self.output = output

    def run(self, prompt):
        return self.output


def make_web_entry(snapshot_ref):
    return Entry(
        title="Upload limit",
        content="Uploads are limited to 50MB.",
        kind=Kind.CONSTRAINT,
        source=Source(channel=Channel.WEB, locator=PAGE.url, snapshot_ref=snapshot_ref),
    )


@pytest.fixture()
def env(tmp_path):
    store = KnowledgeStore(tmp_path / "kh.db")
    chain = EvidenceChain.for_workdir(tmp_path)
    yield store, chain
    store.close()


def test_verify_entry_pass_writes_back_verified(env):
    store, chain = env
    entry = make_web_entry(PAGE.snapshot_hash)
    store.add(entry)
    runner = FakeRunner('{"passed": true, "reason": "Page says Max 50MB."}')

    result = verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    assert result.passed and not result.drifted
    stored = store.get(entry.id)
    assert stored.trust.verification is Verification.VERIFIED
    assert stored.trust.verified_by == "run-9"
    assert stored.is_strong_evidence()
    rec = chain.verify()[0]
    assert rec.event == "entry_verified"
    assert rec.payload["entry_id"] == entry.id
    assert rec.payload["anchor_drifted"] is False


def test_verify_entry_fail_writes_back_contradicted(env):
    store, chain = env
    entry = make_web_entry(PAGE.snapshot_hash)
    store.add(entry)
    runner = FakeRunner('{"passed": false, "reason": "Page shows a 100MB limit."}')

    result = verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    assert not result.passed
    assert store.get(entry.id).trust.verification is Verification.CONTRADICTED
    assert chain.verify()[0].event == "entry_contradicted"


def test_verify_entry_drift_pass_reanchors_snapshot(env):
    store, chain = env
    entry = make_web_entry("stale-hash-from-old-ingest")
    store.add(entry)
    runner = FakeRunner('{"passed": true, "reason": "Still true."}')

    result = verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    assert result.drifted
    stored = store.get(entry.id)
    assert stored.source.snapshot_ref == PAGE.snapshot_hash
    assert stored.trust.verification is Verification.VERIFIED
    rec = chain.verify()[0]
    assert rec.payload["anchor_drifted"] is True
    assert rec.payload["reanchored"] is True


def test_verify_entry_without_anchor_counts_as_drifted_and_gets_anchored(env):
    # H4: "no anchor" must never read as "still fresh". A pass anchors the
    # entry to the observed page hash so it never stays anchor-less verified.
    store, chain = env
    entry = make_web_entry(None)
    store.add(entry)
    runner = FakeRunner('{"passed": true, "reason": "Page says Max 50MB."}')

    result = verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    assert result.drifted
    stored = store.get(entry.id)
    assert stored.source.snapshot_ref == PAGE.snapshot_hash
    assert stored.trust.verification is Verification.VERIFIED
    rec = chain.verify()[0]
    assert rec.payload["anchor_drifted"] is True
    assert rec.payload["reanchored"] is True


def test_verify_entry_endorses_digest_of_reanchored_row(env):
    # The chain endorsement must match the row the run leaves behind
    # (anchored to the verified page), or injection would demote it.
    from knowhelm.knowledge.endorsement import entry_digest, unendorsed_strong_ids

    store, chain = env
    entry = make_web_entry("stale-hash-from-old-ingest")
    store.add(entry)
    runner = FakeRunner('{"passed": true, "reason": "Still true."}')

    verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    stored = store.get(entry.id)
    records = chain.verify()
    assert records[0].payload["entry_digest"] == entry_digest(stored)
    assert unendorsed_strong_ids([stored], records) == set()


def test_verify_entry_drift_fail_keeps_old_anchor(env):
    store, chain = env
    entry = make_web_entry("stale-hash-from-old-ingest")
    store.add(entry)
    runner = FakeRunner('{"passed": false, "reason": "Page now shows 100MB."}')

    result = verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    assert result.drifted and not result.passed
    stored = store.get(entry.id)
    assert stored.source.snapshot_ref == "stale-hash-from-old-ingest"
    assert stored.trust.verification is Verification.CONTRADICTED
    assert chain.verify()[0].payload["reanchored"] is False


def test_verify_entry_saves_artifact(env, tmp_path):
    from knowhelm.evidence.artifacts import ArtifactStore

    store, chain = env
    entry = make_web_entry(PAGE.snapshot_hash)
    store.add(entry)
    artifacts = ArtifactStore.for_workdir(tmp_path)
    runner = FakeRunner('{"passed": true, "reason": "Page says Max 50MB."}')

    verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9", artifacts=artifacts)

    sha = chain.verify()[0].payload["artifact"]
    assert artifacts.load(sha)["snapshot_hash"] == PAGE.snapshot_hash


def test_verify_entry_chain_failure_leaves_store_untouched(env):
    # chain first, store second: trust state must never exist without its
    # chain-backed justification
    store, chain = env
    entry = make_web_entry(PAGE.snapshot_hash)
    store.add(entry)
    runner = FakeRunner('{"passed": true, "reason": "ok"}')

    def broken_append(event, payload):
        raise OSError("disk full")

    chain.append = broken_append
    with pytest.raises(OSError):
        verify_entry(FakeBrowser(), runner, chain, store, entry, "run-9")

    assert store.get(entry.id).trust.verification is Verification.UNVERIFIED


def test_verify_entry_rejects_non_web_channel(env):
    store, chain = env
    entry = Entry(
        title="t", content="c", kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="a.py@abc"),
    )
    store.add(entry)
    with pytest.raises(ValueError, match="not web-channel"):
        verify_entry(FakeBrowser(), FakeRunner("{}"), chain, store, entry, "run-9")
