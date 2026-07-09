"""Chain endorsement of trust state.

The SQLite store lives inside the agent-writable project tree, so its trust
columns cannot be taken at face value: one UPDATE would launder a draft into
an established fact without touching the HMAC key. Every trust-raising act
therefore also appends a chain record — curation by the operator, verification
by browser runs, minting by harvest — and injection recomputes strong status
from the chain.

Endorsement binds CONTENT, not just identity: each trust event records a
digest of the entry's substance (title, content, kind, source). An id alone
would let the agent keep the endorsed row's trust bits while rewriting what
the row actually says. At injection time the digest is recomputed from the
current DB row; a mismatch demotes the entry to reference. The only events
that refresh a bound digest are real trust acts on the current row —
re-approval, or a verify pass on the drifted page. Harvest's ``reversed``
digests are provenance only: LLM re-extraction is not a trust act, so they
neither grant an endorsement nor move an existing one.

The DB row itself is never rewritten on mismatch — the discrepancy is a
signal for the operator, not something to silently repair.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from ..evidence.chain import EvidenceChain, EvidenceRecord
from .model import CURATION_TRANSITIONS, Curation, Entry
from .store import InvalidTransition, KnowledgeStore

CURATION_EVENT = "curation_changed"
SUPERSEDE_EVENT = "entry_superseded"


def entry_digest(entry: Entry) -> str:
    """Canonical fingerprint of what an entry claims and where it points.
    Trust state is deliberately excluded — the chain event sequence itself
    carries trust; the digest pins the substance that trust was granted to."""
    material = json.dumps(
        {
            "id": entry.id,
            "title": entry.title,
            "content": entry.content,
            "kind": entry.kind.value,
            "channel": entry.source.channel.value,
            "locator": entry.source.locator,
            "snapshot_ref": entry.source.snapshot_ref,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def endorsed_strong_digests(records: list[EvidenceRecord]) -> dict[str, set[str]]:
    """entry_id -> digests under which the chain currently endorses strong
    status. An entry is chain-backed strong only if its CURRENT digest is in
    this set. Events without a digest grant no endorsement: an unbound
    endorsement is exactly the loophole this module exists to close."""
    approved: dict[str, str] = {}
    verified: dict[str, str] = {}
    for rec in records:
        payload = rec.payload
        if rec.event == CURATION_EVENT:
            entry_id = payload.get("entry_id")
            digest = payload.get("entry_digest")
            if payload.get("curation") == Curation.APPROVED.value and digest:
                approved[entry_id] = digest
            else:
                approved.pop(entry_id, None)
        elif rec.event == "entry_verified":
            entry_id = payload.get("entry_id")
            digest = payload.get("entry_digest")
            if digest:
                verified[entry_id] = digest
            else:
                verified.pop(entry_id, None)
        elif rec.event == "entry_contradicted":
            verified.pop(payload.get("entry_id"), None)
        elif rec.event == "knowledge_harvested":
            minted = payload.get("minted")
            if isinstance(minted, dict):
                for entry_id, digest in minted.items():
                    if digest:
                        verified[entry_id] = digest
            # The payload's "reversed" digests are deliberately NOT applied:
            # re-reversal is LLM extraction, and letting it move an existing
            # endorsement to the re-anchored row would launder trust — an
            # agent that steers the extractor (comment pollution) into
            # re-stating a claim whose facts changed would refresh the strong
            # bit without any human act. A re-anchored strong entry stays
            # demoted until a human re-approves or re-verifies it.
    out: dict[str, set[str]] = {}
    for entry_id, digest in approved.items():
        out.setdefault(entry_id, set()).add(digest)
    for entry_id, digest in verified.items():
        out.setdefault(entry_id, set()).add(digest)
    return out


def unendorsed_strong_ids(entries: list[Entry], records: list[EvidenceRecord]) -> set[str]:
    """Entries that claim strong trust in the DB but whose current content
    carries no matching chain endorsement — demote these before injection."""
    endorsed = endorsed_strong_digests(records)
    return {
        e.id
        for e in entries
        if e.is_strong_evidence() and entry_digest(e) not in endorsed.get(e.id, set())
    }


def chain_superseded_ids(records: list[EvidenceRecord]) -> set[str]:
    """Superseded set as the chain recorded it. The DB links table is in the
    agent-writable tree: deleting a supersedes row there would resurrect a
    retired entry, so activity decisions use the chain, DB links are cache."""
    return {
        rec.payload["old_id"]
        for rec in records
        if rec.event == SUPERSEDE_EVENT and rec.payload.get("old_id")
    }


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
    chain.append(
        CURATION_EVENT,
        {"entry_id": entry_id, "curation": new.value, "entry_digest": entry_digest(entry)},
    )
    return store.set_curation(entry_id, new, now)
