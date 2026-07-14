"""Build the single source-and-requirements SemanticCore proof boundary."""

from __future__ import annotations

import hashlib

from .authoritative_ids import (
    CanonicalInput,
    canon_v4,
    package_id,
    require_unique_ids,
    semantic_core_sha256,
)
from .authoritative_documents import normalize_project_name
from .authoritative_records import DetectionError, DetectionReport
from .authoritative_semantic_model import SemanticContext, SemanticCore
from .authoritative_semantic_records import build_semantic_records
from .authoritative_source import SnapshotBlob
from .authoritative_types import SourceSnapshot


def _line_spans(data: bytes) -> tuple[tuple[int, int], ...]:
    lines = data.splitlines(keepends=True)
    if not lines:
        return ((0, 0),)
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in lines:
        end = offset + len(line)
        spans.append((offset, end))
        offset = end
    return tuple(spans)


def _snapshot_payload(snapshot: SourceSnapshot) -> CanonicalInput:
    return [
        {
            "alias": repository.alias,
            "role": repository.role,
            "identity": repository.repository_identity_sha256,
            "commit": repository.commit_id.hex,
            "tree": repository.tree_id.hex,
            "index": repository.index_sha256,
            "entries": [
                {
                    "path": entry.path,
                    "mode": entry.mode,
                    "oid": entry.object_id.hex,
                    "length": entry.byte_length,
                    "digest": entry.blob_sha256,
                }
                for entry in repository.entries
            ],
        }
        for repository in snapshot.repositories
    ]


def _identities(snapshot: SourceSnapshot) -> tuple[str, str, str]:
    identities: list[CanonicalInput] = []
    topology: list[CanonicalInput] = []
    for repository in snapshot.repositories:
        identity = repository.repository_identity_sha256
        if identity is None:
            raise DetectionError(f"repository {repository.alias!r} lacks a stable Git identity")
        identities.append({"alias": repository.alias, "identity": identity})
        topology.append({"alias": repository.alias, "role": repository.role, "identity": identity})
    trust = hashlib.sha256(b"loreloop-keyless-project-v1\0" + canon_v4(identities)).hexdigest()
    config = hashlib.sha256(b"loreloop-repository-config-v1\0" + canon_v4(topology)).hexdigest()
    snapshot_digest = hashlib.sha256(
        b"loreloop-source-snapshot-v1\0" + canon_v4(_snapshot_payload(snapshot))
    ).hexdigest()
    return trust, config, snapshot_digest


def build_semantic_core(
    snapshot: SourceSnapshot,
    blobs: tuple[SnapshotBlob, ...],
    report: DetectionReport,
    *,
    project_name: str,
) -> SemanticCore:
    """Bind every detected fact to exact committed bytes and stable semantic IDs."""
    trust, config, snapshot_digest = _identities(snapshot)
    context = SemanticContext(
        trust,
        config,
        {(blob.repository_alias, blob.path): blob for blob in blobs},
        {
            (blob.repository_alias, blob.path): _line_spans(blob.data)
            for blob in blobs
        },
    )
    records, evidence = build_semantic_records(context, report)
    require_unique_ids(tuple(record.record_id for record in records))
    project = normalize_project_name(project_name)
    provisional = SemanticCore(
        project, trust, config, snapshot_digest, records, evidence, "0" * 64, "0" * 64
    )
    payload = semantic_core_payload(provisional)
    core_digest = semantic_core_sha256(payload)
    return SemanticCore(
        project,
        trust,
        config,
        snapshot_digest,
        records,
        evidence,
        core_digest,
        package_id(core_digest),
    )


def semantic_core_payload(core: SemanticCore) -> CanonicalInput:
    """Return the exact portable payload covered by the SemanticCore digest."""
    canonical_records: CanonicalInput = [
        {
            "record_id": record.record_id,
            "atom_id": record.atom_id,
            "atom_kind": record.atom_kind,
            "row_kind": record.row_kind.value,
            "values": {value.pointer: value.value for value in record.values},
            "value_order": [value.pointer for value in record.values],
            "evidence_id": record.evidence_id,
        }
        for record in core.records
    ]
    canonical_evidence: CanonicalInput = [
        {
            "evidence_id": evidence.evidence_id,
            "repository_alias": evidence.source.repository_alias,
            "path": evidence.source.path,
            "line": evidence.source.line,
            "blob_sha256": evidence.blob_sha256,
            "start": evidence.start,
            "end": evidence.end,
        }
        for evidence in core.evidence
    ]
    return {
        "project_name": core.project_name,
        "trust_domain_id": core.trust_domain_id,
        "repository_config_digest": core.repository_config_digest,
        "source_snapshot_sha256": core.source_snapshot_sha256,
        "records": canonical_records,
        "evidence": canonical_evidence,
    }
