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
from .model import CURATION_TRANSITIONS, Channel, Curation, Entry, Kind, Source
from .store import InvalidTransition, KnowledgeStore

CURATION_EVENT = "curation_changed"
SUPERSEDE_EVENT = "entry_superseded"
UNSUPERSEDE_EVENT = "entry_supersession_reverted"
REINGEST_EVENT = "entry_reingested"


class TrustProjectionError(RuntimeError):
    """The agent-writable SQLite projection no longer matches chain authority."""


def entry_digest(entry: Entry) -> str:
    """Canonical fingerprint of what an entry claims and where it points.
    Trust state is deliberately excluded — the chain event sequence itself
    carries trust; the digest pins the substance that trust was granted to."""
    substance = {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "kind": entry.kind.value,
        "channel": entry.source.channel.value,
        "locator": entry.source.locator,
        "snapshot_ref": entry.source.snapshot_ref,
    }
    evidence = {
        "symbol": entry.source.symbol,
        "line_start": entry.source.line_start,
        "line_end": entry.source.line_end,
        "excerpt": entry.source.excerpt,
    }
    if any(value is not None for value in evidence.values()):
        substance["evidence"] = evidence
    material = json.dumps(
        substance,
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def endorsed_strong_digests(records: list[EvidenceRecord]) -> dict[str, set[str]]:
    """entry_id -> digests under which the chain currently endorses strong
    status. An entry is chain-backed strong only if its CURRENT digest is in
    this set. Events without a digest grant no endorsement: an unbound
    endorsement is exactly the loophole this module exists to close."""
    approved: dict[str, tuple[str, int]] = {}
    verified: dict[str, tuple[str, int]] = {}
    contradicted: dict[str, int] = {}
    for rec in records:
        payload = rec.payload
        if rec.event == CURATION_EVENT:
            entry_id = payload.get("entry_id")
            digest = payload.get("entry_digest")
            if payload.get("curation") == Curation.APPROVED.value and digest:
                approved[entry_id] = (digest, rec.index)
            else:
                approved.pop(entry_id, None)
                if payload.get("curation") == Curation.REJECTED.value:
                    # Rejection retires the whole trust decision, including a
                    # verification that predates it. A later reopen returns
                    # the entry to draft; it must not unmask the old machine
                    # endorsement and immediately make the row strong again.
                    verified.pop(entry_id, None)
        elif rec.event == "entry_verified":
            entry_id = payload.get("entry_id")
            digest = payload.get("entry_digest")
            if digest:
                verified[entry_id] = (digest, rec.index)
            else:
                verified.pop(entry_id, None)
        elif rec.event == "entry_contradicted":
            entry_id = payload.get("entry_id")
            verified.pop(entry_id, None)
            if entry_id:
                contradicted[entry_id] = rec.index
        elif rec.event == "knowledge_harvested":
            minted = payload.get("minted")
            if isinstance(minted, dict):
                for entry_id, digest in minted.items():
                    if digest:
                        verified[entry_id] = (digest, rec.index)
            # The payload's "reversed" digests are deliberately NOT applied:
            # re-reversal is LLM extraction, and letting it move an existing
            # endorsement to the re-anchored row would launder trust — an
            # agent that steers the extractor (comment pollution) into
            # re-stating a claim whose facts changed would refresh the strong
            # bit without any human act. A re-anchored strong entry stays
            # demoted until a human re-approves or re-verifies it.
    out: dict[str, set[str]] = {}
    for entry_id, (digest, index) in approved.items():
        if index > contradicted.get(entry_id, -1):
            out.setdefault(entry_id, set()).add(digest)
    for entry_id, (digest, index) in verified.items():
        if index > contradicted.get(entry_id, -1):
            out.setdefault(entry_id, set()).add(digest)
    return out


def chain_endorsed_strong_ids(entries: list[Entry], records: list[EvidenceRecord]) -> set[str]:
    """Entries whose current content digest is chain-endorsed strong."""
    endorsed = endorsed_strong_digests(records)
    return {e.id for e in entries if entry_digest(e) in endorsed.get(e.id, set())}


def chain_verified_ids(entries: list[Entry], records: list[EvidenceRecord]) -> set[str]:
    """Entries whose current digest has a live machine-verification endorsement."""
    verified: dict[str, str] = {}
    for record in records:
        payload = record.payload
        entry_id = payload.get("entry_id")
        if record.event == "entry_verified" and entry_id and payload.get("entry_digest"):
            verified[entry_id] = payload["entry_digest"]
        elif record.event == "entry_contradicted" and entry_id:
            verified.pop(entry_id, None)
        elif record.event == CURATION_EVENT and entry_id:
            if payload.get("curation") == Curation.REJECTED.value:
                verified.pop(entry_id, None)
    return {entry.id for entry in entries if verified.get(entry.id) == entry_digest(entry)}


def unendorsed_strong_ids(entries: list[Entry], records: list[EvidenceRecord]) -> set[str]:
    """Entries that claim strong trust in the DB but whose current content
    carries no matching chain endorsement — demote these before injection."""
    endorsed_ids = chain_endorsed_strong_ids(entries, records)
    return {e.id for e in entries if e.is_strong_evidence() and e.id not in endorsed_ids}


def chain_contradicted_ids(records: list[EvidenceRecord]) -> set[str]:
    """Entries whose latest trust-relevant event is a contradiction.

    A deliberate human re-approval after the contradiction is an explicit
    override. An older approval never silently wins over newer machine evidence.
    """
    positive: dict[str, int] = {}
    contradicted: dict[str, int] = {}
    for rec in records:
        payload = rec.payload
        entry_id = payload.get("entry_id")
        if rec.event == CURATION_EVENT and entry_id:
            if payload.get("curation") == Curation.APPROVED.value and payload.get("entry_digest"):
                positive[entry_id] = rec.index
            else:
                positive.pop(entry_id, None)
        elif rec.event == "entry_verified" and entry_id and payload.get("entry_digest"):
            positive[entry_id] = rec.index
        elif rec.event == "entry_contradicted" and entry_id:
            contradicted[entry_id] = rec.index
        elif rec.event == "knowledge_harvested":
            minted = payload.get("minted")
            if isinstance(minted, dict):
                for minted_id, digest in minted.items():
                    if digest:
                        positive[minted_id] = rec.index
    return {
        entry_id for entry_id, index in contradicted.items() if index > positive.get(entry_id, -1)
    }


def known_projection_digests(records: list[EvidenceRecord]) -> dict[str, set[str]]:
    """Digests produced by explicit re-projection events without raising trust."""
    known: dict[str, set[str]] = {}
    for rec in records:
        if rec.event == REINGEST_EVENT:
            entry_id = rec.payload.get("entry_id")
            digest = rec.payload.get("entry_digest")
            if entry_id and digest:
                known.setdefault(entry_id, set()).add(digest)
        elif rec.event == "knowledge_harvested":
            reversed_rows = rec.payload.get("reversed")
            if isinstance(reversed_rows, dict):
                for entry_id, digest in reversed_rows.items():
                    if digest:
                        known.setdefault(entry_id, set()).add(digest)
    return known


def assert_trust_projection(
    entries: list[Entry],
    records: list[EvidenceRecord],
    *,
    retired_ids: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Fail closed when chain-backed strong facts disappear from SQLite.

    A digest mismatch is allowed only when a chain-recorded re-ingest/harvest
    explains the new projection; such rows remain demoted until re-endorsed.
    """
    by_id = {entry.id: entry for entry in entries}
    endorsed = endorsed_strong_digests(records)
    known = known_projection_digests(records)
    problems = []
    for entry_id, digests in endorsed.items():
        if entry_id in retired_ids:
            continue
        entry = by_id.get(entry_id)
        if entry is None:
            problems.append(f"chain-backed entry {entry_id[:8]} is missing from knowledge.db")
            continue
        current = entry_digest(entry)
        if current not in digests and current not in known.get(entry_id, set()):
            problems.append(
                f"chain-backed entry {entry_id[:8]} has an unexplained content/source digest"
            )
    if problems:
        raise TrustProjectionError(
            "; ".join(problems)
            + ". Restore the SQLite projection from backup or re-ingest under operator review."
        )


def chain_supersession_links(records: list[EvidenceRecord]) -> set[tuple[str, str]]:
    """Active ``(new_id, old_id)`` supersession edges after chain replay."""
    active: set[tuple[str, str]] = set()
    for rec in records:
        if rec.event not in (SUPERSEDE_EVENT, UNSUPERSEDE_EVENT):
            continue
        new_id = rec.payload.get("new_id")
        old_id = rec.payload.get("old_id")
        if not new_id or not old_id:
            continue
        edge = (new_id, old_id)
        if rec.event == SUPERSEDE_EVENT:
            active.add(edge)
        else:
            active.discard(edge)
    return active


def chain_superseded_ids(records: list[EvidenceRecord]) -> set[str]:
    """Superseded set as the chain recorded it. The DB links table is in the
    agent-writable tree: deleting a supersedes row there would resurrect a
    retired entry, so activity decisions use the chain, DB links are cache."""
    return {old_id for _, old_id in chain_supersession_links(records)}


def chain_rejected_ids(records: list[EvidenceRecord]) -> set[str]:
    """Entries whose LATEST chain-recorded curation is rejected. Rejection is
    a human act appended by ``curate``; the DB curation column is a cache in
    the agent-writable tree. Replaying the chain closes the resurrection
    direction: flipping a rejected row back to draft in SQLite must not put
    the entry back into injection. (A rejected entry can return legitimately:
    the operator's reject -> draft transition appends its own curation event,
    which stops being 'rejected' here.)"""
    latest: dict[str, str] = {}
    for rec in records:
        if rec.event == CURATION_EVENT and rec.payload.get("entry_id"):
            latest[rec.payload["entry_id"]] = rec.payload.get("curation")
    return {eid for eid, cur in latest.items() if cur == Curation.REJECTED.value}


def chain_effective_curation(records: list[EvidenceRecord]) -> dict[str, Curation]:
    """Latest signed human curation per entry; absent entries remain draft."""
    latest: dict[str, Curation] = {}
    for rec in records:
        if rec.event != CURATION_EVENT or not rec.payload.get("entry_id"):
            continue
        try:
            latest[rec.payload["entry_id"]] = Curation(rec.payload.get("curation"))
        except ValueError:
            continue
    return latest


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
    effective = chain_effective_curation(chain.verify()).get(entry_id, Curation.DRAFT)
    if new not in CURATION_TRANSITIONS[effective]:
        if effective is new and entry.trust.curation is not new:
            return store.project_curation(entry_id, new, now)
        raise InvalidTransition(f"{effective.value} -> {new.value}")
    chain.append(
        CURATION_EVENT,
        {
            "entry_id": entry_id,
            "curation": new.value,
            "entry_digest": entry_digest(entry),
            "entry": entry_payload(entry),
        },
    )
    return store.project_curation(entry_id, new, now)


def record_reingested(chain: EvidenceChain, entry: Entry) -> EvidenceRecord:
    return chain.append(
        REINGEST_EVENT,
        {
            "entry_id": entry.id,
            "entry_digest": entry_digest(entry),
            "entry": entry_payload(entry),
        },
    )


def entry_payload(entry: Entry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "kind": entry.kind.value,
        "source": {
            "channel": entry.source.channel.value,
            "locator": entry.source.locator,
            "snapshot_ref": entry.source.snapshot_ref,
            "symbol": entry.source.symbol,
            "line_start": entry.source.line_start,
            "line_end": entry.source.line_end,
            "excerpt": entry.source.excerpt,
        },
    }


def entry_from_payload(data: object) -> Entry:
    """Rebuild an untrusted SQLite projection from a signed chain snapshot.

    Trust is deliberately reset to draft/unverified. The caller verifies the
    reconstructed substance against the digest carried by the same record
    before persisting it.
    """
    if not isinstance(data, dict):
        raise TypeError("entry snapshot must be an object")
    source = data["source"]
    if not isinstance(source, dict):
        raise TypeError("entry source snapshot must be an object")
    return Entry(
        id=data["id"],
        title=data["title"],
        content=data["content"],
        kind=Kind(data["kind"]),
        source=Source(
            channel=Channel(source["channel"]),
            locator=source["locator"],
            snapshot_ref=source.get("snapshot_ref"),
            symbol=source.get("symbol"),
            line_start=source.get("line_start"),
            line_end=source.get("line_end"),
            excerpt=source.get("excerpt"),
        ),
    )
