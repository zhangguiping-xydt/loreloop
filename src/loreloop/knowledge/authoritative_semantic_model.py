"""SemanticCore records and exact source-binding construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .authoritative_ast import DocumentRowKind, ProjectedValue
from .authoritative_bindings import BindingEntry, BindingSet, SourceBinding, SourceTransform
from .authoritative_ids import (
    AtomIdentity,
    CanonicalScalar,
    EvidenceIdentity,
    RecordIdentity,
    atom_id,
    evidence_id,
    record_id,
)
from .authoritative_records import DetectionError, SourceRef
from .authoritative_source import SnapshotBlob

Payload: TypeAlias = dict[str, CanonicalScalar]


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    evidence_id: str
    source: SourceRef
    blob_sha256: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SemanticRecord:
    record_id: str
    atom_id: str
    atom_kind: str
    row_kind: DocumentRowKind
    values: tuple[ProjectedValue, ...]
    evidence_id: str
    bindings: BindingSet


@dataclass(frozen=True, slots=True)
class SemanticCore:
    trust_domain_id: str
    repository_config_digest: str
    source_snapshot_sha256: str
    records: tuple[SemanticRecord, ...]
    evidence: tuple[SemanticEvidence, ...]
    semantic_core_sha256: str
    package_id: str


@dataclass(frozen=True, slots=True)
class SemanticContext:
    trust_domain_id: str
    repository_config_digest: str
    blobs: dict[tuple[str, str], SnapshotBlob]
    line_spans: dict[tuple[str, str], tuple[tuple[int, int], ...]]


def _span(context: SemanticContext, blob: SnapshotBlob, line: int) -> tuple[int, int]:
    spans = context.line_spans[(blob.repository_alias, blob.path)]
    if line < 1 or line > len(spans):
        raise DetectionError(f"source line is outside snapshot blob: {blob.path}:{line}")
    return spans[line - 1]


def make_semantic_record(
    context: SemanticContext,
    prefix: str,
    row_kind: DocumentRowKind,
    atom_kind: str,
    source: SourceRef,
    payload: Payload,
) -> tuple[SemanticRecord, SemanticEvidence]:
    blob = context.blobs.get((source.repository_alias, source.path))
    if blob is None:
        raise DetectionError(f"record source is absent from snapshot: {source.path}")
    start, end = _span(context, blob, source.line)
    evidence = evidence_id(
        EvidenceIdentity(source.repository_alias, source.path, blob.blob_sha256, start, end)
    )
    atom = atom_id(
        AtomIdentity(
            atom_kind,
            source.repository_alias,
            source.path,
            blob.blob_sha256,
            start,
            end,
            payload,
        )
    )
    identifier = record_id(
        prefix,
        RecordIdentity(
            context.trust_domain_id,
            context.repository_config_digest,
            {
                "alias": source.repository_alias,
                "path": source.path,
                "kind": atom_kind,
                "payload": payload,
            },
        ),
    )
    values = tuple(ProjectedValue(f"/{key}", value) for key, value in payload.items())
    bindings = tuple(
        BindingEntry(
            f"/{key}",
            SourceBinding(evidence, atom, f"/payload/{key}", SourceTransform.IDENTITY),
        )
        for key in payload
    )
    return (
        SemanticRecord(identifier, atom, atom_kind, row_kind, values, evidence, bindings),
        SemanticEvidence(evidence, source, blob.blob_sha256, start, end),
    )
