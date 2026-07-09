"""Trust bits in the SQLite store only count when the evidence chain endorses
them — the store lives in the agent-writable tree, the chain's key does not."""

from datetime import datetime, timezone

import pytest

from knowhelm.evidence.chain import EvidenceChain
from knowhelm.knowledge.endorsement import curate, endorsed_strong_ids
from knowhelm.knowledge.model import (
    Channel,
    Curation,
    Entry,
    Kind,
    Source,
    Trust,
    Verification,
)
from knowhelm.knowledge.store import InvalidTransition, KnowledgeStore

NOW = datetime(2026, 7, 9, tzinfo=timezone.utc)


def make_entry(title="Upload contract", content="POST /upload returns 201."):
    return Entry(
        title=title, content=content, kind=Kind.INTERFACE,
        source=Source(channel=Channel.CODE, locator="api.py@abc"),
    )


@pytest.fixture()
def env(tmp_path):
    (tmp_path / ".knowhelm").mkdir()
    store = KnowledgeStore(tmp_path / ".knowhelm/knowledge.db")
    chain = EvidenceChain.for_workdir(tmp_path)
    yield store, chain
    store.close()


def test_curate_endorses_approval_on_chain(env):
    store, chain = env
    entry = store.add(make_entry())

    updated = curate(store, chain, entry.id, Curation.APPROVED, NOW)

    assert updated.trust.curation is Curation.APPROVED
    records = chain.verify()
    assert records[-1].event == "curation_changed"
    assert records[-1].payload == {"entry_id": entry.id, "curation": "approved"}
    assert entry.id in endorsed_strong_ids(records)


def test_curate_rejection_revokes_endorsement(env):
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)
    curate(store, chain, entry.id, Curation.REJECTED, NOW)

    assert entry.id not in endorsed_strong_ids(chain.verify())


def test_curate_invalid_transition_writes_no_chain_record(env):
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)

    with pytest.raises(InvalidTransition):
        curate(store, chain, entry.id, Curation.APPROVED, NOW)

    assert len([r for r in chain.verify() if r.event == "curation_changed"]) == 1


def test_curate_missing_entry_writes_no_chain_record(env):
    store, chain = env
    with pytest.raises(KeyError):
        curate(store, chain, "nope", Curation.APPROVED, NOW)
    assert chain.verify() == []


def test_endorsed_ids_include_browser_verification_events(env):
    _, chain = env
    chain.append("entry_verified", {"entry_id": "e1", "run_id": "run-1"})
    chain.append("knowledge_harvested", {"run_id": "run-2", "minted": ["e2", "e3"]})

    assert endorsed_strong_ids(chain.verify()) == {"e1", "e2", "e3"}


def test_contradiction_revokes_verification_endorsement(env):
    _, chain = env
    chain.append("entry_verified", {"entry_id": "e1", "run_id": "run-1"})
    chain.append("entry_contradicted", {"entry_id": "e1", "run_id": "run-2"})

    assert endorsed_strong_ids(chain.verify()) == set()


def test_db_only_strong_bit_is_not_endorsed(env):
    store, chain = env
    # simulate the attack: agent UPDATEs the store directly, no chain event
    laundered = Entry(
        title="Laundered", content="Agent says this is a fact.", kind=Kind.BEHAVIOR,
        source=Source(channel=Channel.WEB, locator="http://x", snapshot_ref="s"),
        trust=Trust(
            verification=Verification.VERIFIED, verified_at=NOW, verified_by="forged",
        ),
    )
    store.add(laundered)

    assert laundered.is_strong_evidence()
    assert laundered.id not in endorsed_strong_ids(chain.verify())
