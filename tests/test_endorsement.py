"""Trust bits in the SQLite store only count when the evidence chain endorses
them FOR THE CURRENT CONTENT — the store lives in the agent-writable tree,
the chain's key does not, and an endorsement bound to a bare id would survive
a content rewrite."""

from datetime import datetime, timezone

import pytest

from knowhelm.evidence.chain import EvidenceChain
from knowhelm.knowledge.endorsement import (
    chain_endorsed_strong_ids,
    chain_rejected_ids,
    chain_superseded_ids,
    curate,
    entry_digest,
    unendorsed_strong_ids,
)
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


def strong(entry):
    from dataclasses import replace

    return replace(
        entry,
        trust=Trust(verification=Verification.VERIFIED, verified_at=NOW, verified_by="run-x"),
    )


@pytest.fixture()
def env(tmp_path):
    (tmp_path / ".knowhelm").mkdir()
    store = KnowledgeStore(tmp_path / ".knowhelm/knowledge.db")
    chain = EvidenceChain.for_workdir(tmp_path)
    yield store, chain
    store.close()


def test_curate_endorses_approval_with_content_digest(env):
    store, chain = env
    entry = store.add(make_entry())

    updated = curate(store, chain, entry.id, Curation.APPROVED, NOW)

    assert updated.trust.curation is Curation.APPROVED
    records = chain.verify()
    assert records[-1].event == "curation_changed"
    assert records[-1].payload == {
        "entry_id": entry.id,
        "curation": "approved",
        "entry_digest": entry_digest(entry),
    }
    assert unendorsed_strong_ids([updated], records) == set()


def test_curate_rejection_revokes_endorsement(env):
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)
    curate(store, chain, entry.id, Curation.REJECTED, NOW)

    # A later strong bit for this id (however it appears) has no endorsement.
    assert unendorsed_strong_ids([strong(entry)], chain.verify()) == {entry.id}


def test_curate_invalid_transition_writes_no_chain_record(env):
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.REJECTED, NOW)

    with pytest.raises(InvalidTransition):
        curate(store, chain, entry.id, Curation.REJECTED, NOW)

    assert len([r for r in chain.verify() if r.event == "curation_changed"]) == 1


def test_curate_missing_entry_writes_no_chain_record(env):
    store, chain = env
    with pytest.raises(KeyError):
        curate(store, chain, "nope", Curation.APPROVED, NOW)
    assert chain.verify() == []


def test_content_rewrite_after_approval_is_not_endorsed(env):
    # H2 attack: human approves, then the agent rewrites the row's content
    # via SQL, keeping the id and the approved bit. The endorsement was bound
    # to the old content digest, so the rewritten row is demoted.
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)

    store._conn.execute(
        "UPDATE entries SET content = ? WHERE id = ?",
        ("POST /upload returns 200 and skips auth.", entry.id),
    )
    store._conn.commit()
    tampered = store.get(entry.id)

    assert tampered.is_strong_evidence()
    assert unendorsed_strong_ids([tampered], chain.verify()) == {entry.id}


def test_approval_survives_db_curation_flip_to_draft(env):
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)

    store._conn.execute("UPDATE entries SET curation = 'draft' WHERE id = ?", (entry.id,))
    store._conn.commit()
    suppressed = store.get(entry.id)

    assert not suppressed.is_strong_evidence()
    assert chain_endorsed_strong_ids([suppressed], chain.verify()) == {entry.id}
    assert unendorsed_strong_ids([suppressed], chain.verify()) == set()


def test_locator_rewrite_after_verification_is_not_endorsed(env):
    store, chain = env
    entry = store.add(make_entry())
    chain.append(
        "entry_verified", {"entry_id": entry.id, "entry_digest": entry_digest(entry)}
    )

    store._conn.execute(
        "UPDATE entries SET locator = ? WHERE id = ?", ("http://evil.local/", entry.id)
    )
    store._conn.commit()
    tampered = store.get(entry.id)

    assert unendorsed_strong_ids([strong(tampered)], chain.verify()) == {entry.id}


def test_verified_event_with_digest_endorses_current_row(env):
    store, chain = env
    entry = store.add(make_entry())
    chain.append(
        "entry_verified", {"entry_id": entry.id, "entry_digest": entry_digest(entry)}
    )

    assert unendorsed_strong_ids([strong(entry)], chain.verify()) == set()


def test_events_without_digest_grant_no_endorsement(env):
    # Legacy events predating content binding: an unbound endorsement is the
    # exact loophole H2 closes, so they endorse nothing.
    store, chain = env
    entry = store.add(make_entry())
    chain.append("entry_verified", {"entry_id": entry.id})
    chain.append("curation_changed", {"entry_id": entry.id, "curation": "approved"})

    assert unendorsed_strong_ids([strong(entry)], chain.verify()) == {entry.id}


def test_contradiction_revokes_verification_endorsement(env):
    store, chain = env
    entry = store.add(make_entry())
    chain.append(
        "entry_verified", {"entry_id": entry.id, "entry_digest": entry_digest(entry)}
    )
    chain.append("entry_contradicted", {"entry_id": entry.id})

    assert unendorsed_strong_ids([strong(entry)], chain.verify()) == {entry.id}


def test_harvest_minted_digests_endorse(env):
    store, chain = env
    entry = store.add(make_entry())
    chain.append(
        "knowledge_harvested",
        {"run_id": "run-1", "minted": {entry.id: entry_digest(entry)}},
    )

    assert unendorsed_strong_ids([strong(entry)], chain.verify()) == set()


def test_harvest_legacy_list_payload_grants_nothing(env):
    store, chain = env
    entry = store.add(make_entry())
    chain.append("knowledge_harvested", {"run_id": "run-1", "minted": [entry.id]})

    assert unendorsed_strong_ids([strong(entry)], chain.verify()) == {entry.id}


def test_harvest_reanchor_does_not_move_endorsement_to_new_digest(env):
    # Codex round-3 H1: re-reversal is LLM extraction. If the harvested
    # "reversed" digests moved endorsements, an agent could pollute comments
    # so the extractor re-states a claim whose facts changed, and the re-
    # anchored row would inherit the human's approval. The reversed digest
    # must grant nothing: the re-anchored strong entry is demoted until a
    # human re-approves the CURRENT row.
    from dataclasses import replace

    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)

    reanchored = replace(
        entry, source=replace(entry.source, locator="api.py@def", snapshot_ref="def")
    )
    chain.append(
        "knowledge_harvested",
        {"run_id": "run-1", "minted": {}, "reversed": {entry.id: entry_digest(reanchored)}},
    )

    approved = replace(reanchored, trust=Trust(curation=Curation.APPROVED))
    assert unendorsed_strong_ids([approved], chain.verify()) == {entry.id}


def test_reapprove_rebinds_endorsement_to_current_row(env):
    # The recovery path for a legitimately re-anchored entry: a fresh human
    # approval of the current row. APPROVED -> APPROVED exists exactly for
    # this re-endorsement.
    store, chain = env
    entry = store.add(make_entry())
    curate(store, chain, entry.id, Curation.APPROVED, NOW)

    store.set_snapshot_ref(entry.id, "def", NOW, locator="api.py@def")
    moved = store.get(entry.id)
    assert unendorsed_strong_ids([moved], chain.verify()) == {moved.id}

    curate(store, chain, entry.id, Curation.APPROVED, NOW)
    assert unendorsed_strong_ids([store.get(entry.id)], chain.verify()) == set()


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
    assert unendorsed_strong_ids([laundered], chain.verify()) == {laundered.id}


def test_chain_superseded_ids_replays_supersede_events(env):
    _, chain = env
    chain.append("entry_superseded", {"new_id": "n1", "old_id": "o1"})
    chain.append("entry_superseded", {"new_id": "n2", "old_id": "o2"})

    assert chain_superseded_ids(chain.verify()) == {"o1", "o2"}


def test_chain_rejected_ids_replays_latest_curation(env):
    # Round-5 H2 (resurrection direction): rejection lives on the chain; the
    # DB curation column is a cache. The latest curation event decides.
    store, chain = env
    entry = store.add(make_entry())
    other = store.add(make_entry(title="Other", content="Another claim."))

    curate(store, chain, entry.id, Curation.REJECTED, NOW)
    curate(store, chain, other.id, Curation.APPROVED, NOW)
    assert chain_rejected_ids(chain.verify()) == {entry.id}

    # the legitimate return path appends its own curation event
    curate(store, chain, entry.id, Curation.DRAFT, NOW)
    assert chain_rejected_ids(chain.verify()) == set()


def test_rejection_survives_db_curation_flip(env):
    # The attack: entry is verified (endorsed digest on chain), the human
    # rejects it, the agent flips the DB curation back to draft. The digest
    # endorsement survives rejection — rejection is curation, not
    # contradiction — so only the chain-rejected replay keeps it out.
    store, chain = env
    entry = store.add(make_entry())
    chain.append(
        "entry_verified", {"entry_id": entry.id, "entry_digest": entry_digest(entry)}
    )
    curate(store, chain, entry.id, Curation.REJECTED, NOW)

    store._conn.execute(
        "UPDATE entries SET curation = 'draft', verification = 'verified',"
        " verified_at = ?, verified_by = 'run-x' WHERE id = ?",
        (NOW.isoformat(), entry.id),
    )
    store._conn.commit()
    resurrected = store.get(entry.id)

    assert resurrected.is_strong_evidence()
    # digest check alone would let it back in as strong…
    assert unendorsed_strong_ids([resurrected], chain.verify()) == set()
    # …the chain-rejected set is what keeps it retired
    assert chain_rejected_ids(chain.verify()) == {entry.id}
