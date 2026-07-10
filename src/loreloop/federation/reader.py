"""Read foreign trust domains without mutating their project or key state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..evidence.chain import (
    ChainVerificationError,
    EvidenceChain,
    EvidenceRecord,
    FederatedTrustUnavailable,
)
from ..knowledge.code_reverse import drifted_code_entry_ids
from ..knowledge.endorsement import chain_rejected_ids, chain_superseded_ids, entry_digest
from ..knowledge.model import Channel, Curation, Entry
from ..knowledge.repos import RepoConfigError, load_repos
from ..knowledge.store import KnowledgeStore
from ..paths import state_path


@dataclass(frozen=True)
class ForeignEntry:
    project_id: str
    entry: Entry
    strong_there: bool
    drifted_there: bool
    trust_note: str
    trust_ts: str | None = None


@dataclass(frozen=True)
class FederationWarning:
    project_id: str
    message: str


def read_project(
    project_id: str, path: Path
) -> tuple[list[ForeignEntry], list[FederationWarning]]:
    workdir = path.resolve()
    db = state_path(workdir, "knowledge.db")
    if not workdir.is_dir():
        return [], [FederationWarning(project_id, f"project path is unavailable: {workdir}")]
    if not db.is_file():
        return [], [FederationWarning(project_id, f"knowledge store is unavailable: {db}")]
    try:
        with KnowledgeStore.open_readonly(db) as store:
            entries = store.list()
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        return [], [FederationWarning(project_id, f"knowledge store is invalid: {exc}")]

    warnings: list[FederationWarning] = []
    try:
        records = EvidenceChain.verify_readonly(workdir)
    except (FederatedTrustUnavailable, ChainVerificationError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        records = None
        warnings.append(FederationWarning(project_id, f"trust unavailable: {exc}"))

    try:
        declared = load_repos(workdir)
        drifted = (
            drifted_code_entry_ids(workdir, entries)
            if (workdir / ".git").exists() or declared
            else set()
        )
    except RepoConfigError as exc:
        drifted = {entry.id for entry in entries if entry.source.channel is Channel.CODE}
        warnings.append(FederationWarning(project_id, f"repository configuration is invalid: {exc}"))

    if records is None:
        return [
            ForeignEntry(
                project_id=project_id,
                entry=entry,
                strong_there=False,
                drifted_there=entry.id in drifted,
                trust_note="trust unavailable (chain not verifiable)",
            )
            for entry in entries
        ], warnings
    return _grade_entries(project_id, entries, records, drifted), warnings


def grade_local_entries(
    project_id: str,
    entries: list[Entry],
    records: list[EvidenceRecord],
    drifted: set[str],
) -> list[ForeignEntry]:
    return _grade_entries(project_id, entries, records, drifted, location="here")


def _grade_entries(
    project_id: str,
    entries: list[Entry],
    records: list[EvidenceRecord],
    drifted: set[str],
    location: str = "there",
) -> list[ForeignEntry]:
    retired = chain_rejected_ids(records) | chain_superseded_ids(records)
    trust = _trust_digests(records)
    graded: list[ForeignEntry] = []
    for entry in entries:
        if entry.id in retired:
            continue
        digest = entry_digest(entry)
        status, ts = "draft", None
        verified = trust["verified"].get(entry.id)
        approved = trust["approved"].get(entry.id)
        if verified and verified[0] == digest:
            status, ts = f"verified {location}", verified[1]
        elif approved and approved[0] == digest:
            status, ts = f"approved {location}", approved[1]
        is_strong = status != "draft"
        is_drifted = entry.id in drifted
        if is_strong and is_drifted:
            status += " (anchor drifted since)"
        graded.append(
            ForeignEntry(project_id, entry, is_strong, is_drifted, status, ts)
        )
    return graded


def _trust_digests(records: list[EvidenceRecord]) -> dict[str, dict[str, tuple[str, str]]]:
    approved: dict[str, tuple[str, str]] = {}
    verified: dict[str, tuple[str, str]] = {}
    for record in records:
        payload = record.payload
        if record.event == "curation_changed":
            entry_id = payload.get("entry_id")
            digest = payload.get("entry_digest")
            if payload.get("curation") == Curation.APPROVED.value and entry_id and digest:
                approved[entry_id] = (digest, record.ts)
            elif entry_id:
                approved.pop(entry_id, None)
        elif record.event == "entry_verified":
            entry_id = payload.get("entry_id")
            digest = payload.get("entry_digest")
            if entry_id and digest:
                verified[entry_id] = (digest, record.ts)
        elif record.event == "entry_contradicted":
            verified.pop(payload.get("entry_id"), None)
        elif record.event == "knowledge_harvested":
            minted = payload.get("minted")
            if isinstance(minted, dict):
                for entry_id, digest in minted.items():
                    if isinstance(entry_id, str) and isinstance(digest, str) and digest:
                        verified[entry_id] = (digest, record.ts)
    return {"approved": approved, "verified": verified}
