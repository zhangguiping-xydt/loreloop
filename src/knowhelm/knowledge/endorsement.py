"""Chain endorsement of trust state.

The SQLite store lives inside the agent-writable project tree, so its trust
columns cannot be taken at face value: one UPDATE would launder a draft into
an established fact without touching the HMAC key. Every trust-raising act
therefore also appends a chain record — curation by the operator, verification
by browser runs, minting by harvest — and injection recomputes strong status
from the chain. An entry that is strong in the DB but has no chain endorsement
is demoted to reference for that injection; the DB row itself is not rewritten
(the mismatch is a signal for the operator, not something to silently repair).
"""

from __future__ import annotations

from datetime import datetime

from ..evidence.chain import EvidenceChain, EvidenceRecord
from .model import CURATION_TRANSITIONS, Curation, Entry
from .store import InvalidTransition, KnowledgeStore

CURATION_EVENT = "curation_changed"
SUPERSEDE_EVENT = "entry_superseded"


def endorsed_strong_ids(records: list[EvidenceRecord]) -> set[str]:
    """Entry ids whose strong status is backed by the evidence chain.

    Curation follows the latest chain event per entry; verification follows
    the latest browser outcome, plus harvest minting (which is itself a
    chain-backed browser verification).
    """
    approved: set[str] = set()
    verified: set[str] = set()
    for rec in records:
        payload = rec.payload
        if rec.event == CURATION_EVENT:
            if payload.get("curation") == Curation.APPROVED.value:
                approved.add(payload["entry_id"])
            else:
                approved.discard(payload["entry_id"])
        elif rec.event == "entry_verified":
            verified.add(payload["entry_id"])
        elif rec.event == "entry_contradicted":
            verified.discard(payload["entry_id"])
        elif rec.event == "knowledge_harvested":
            verified.update(payload.get("minted", []))
    return approved | verified


def curate(
    store: KnowledgeStore,
    chain: EvidenceChain,
    entry_id: str,
    new: Curation,
    now: datetime,
) -> Entry:
    """Curation with chain endorsement: validate the transition, append the
    chain record, then update the store. The chain record documents the
    operator's decision, so it must never be written for an invalid one."""
    entry = store.get(entry_id)
    if entry is None:
        raise KeyError(entry_id)
    if new not in CURATION_TRANSITIONS[entry.trust.curation]:
        raise InvalidTransition(f"{entry.trust.curation.value} -> {new.value}")
    chain.append(CURATION_EVENT, {"entry_id": entry_id, "curation": new.value})
    return store.set_curation(entry_id, new, now)
