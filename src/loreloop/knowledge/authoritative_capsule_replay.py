"""Portable no-key and caller-attested replay for authoritative exports."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from .authoritative_archive import (
    ExportArchiveError,
    read_export_archive_with_capsule,
)
from .authoritative_ast import AstViolation, DocumentRowKind, DocumentSet, ProjectedValue
from .authoritative_ast_render import render_document_ast
from .authoritative_bindings import BindingEntry, SourceBinding, SourceTransform
from .authoritative_capsule import CAPSULE_FILENAME, JsonValue, document_ast_sha256
from .authoritative_capsule_io import (
    CapsuleIoError,
    parse_capsule,
    read_export_files_with_capsule,
)
from .authoritative_capsule_render import CapsuleRenderError, render_capsule_ast
from .authoritative_document_ast import build_document_ast_set
from .authoritative_documents import normalize_project_name
from .authoritative_ids import (
    AtomIdentity,
    EvidenceIdentity,
    IdentityContractError,
    RecordIdentity,
    atom_id,
    canon_v4,
    evidence_id,
    package_id,
    record_id,
    semantic_core_sha256,
)
from .authoritative_records import DetectionError, SourceRef
from .authoritative_semantic import semantic_core_payload
from .authoritative_semantic_model import SemanticCore, SemanticEvidence, SemanticRecord

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ROOT_KEYS = {
    "applicability",
    "documents",
    "package_id",
    "schema_version",
    "semantic_core",
    "semantic_core_sha256",
}
_DOCUMENT_KEYS_V2 = {"ast", "ast_sha256", "document_id", "filename", "markdown_sha256"}
_DOCUMENT_KEYS_V3 = {"ast_sha256", "document_id", "filename", "markdown_sha256"}
_APPLICABILITY_KEYS = {"family", "reason_ids", "status"}
_AST_KEYS = {
    "bindings",
    "document_id",
    "evidence_rows",
    "header",
    "optional_family",
    "path",
    "required_family",
    "schema_version",
    "sections",
    "title",
}
_SEMANTIC_KEYS = {
    "evidence",
    "project_name",
    "records",
    "repository_config_digest",
    "source_snapshot_sha256",
    "trust_domain_id",
}
_WORKTREE_SEMANTIC_KEYS = _SEMANTIC_KEYS | {"source_snapshot_kind"}
_LEGACY_SEMANTIC_KEY_SETS = {
    frozenset(_SEMANTIC_KEYS - {"evidence"}),
    frozenset(_SEMANTIC_KEYS - {"project_name"}),
    frozenset(_SEMANTIC_KEYS - {"project_name", "evidence"}),
}
_SEMANTIC_RECORD_KEYS = {
    "atom_id",
    "atom_kind",
    "evidence_id",
    "record_id",
    "row_kind",
    "value_order",
    "values",
}
_SEMANTIC_EVIDENCE_KEYS = {
    "blob_sha256",
    "end",
    "evidence_id",
    "line",
    "path",
    "repository_alias",
    "start",
}
_REQUIRED_FAMILIES = {
    "acceptance",
    "architecture",
    "capability_catalog",
    "detailed_design",
    "requirements",
    "user_guide",
}
_OPTIONAL_FAMILIES = {"database_design", "interface_contract"}


class CapsuleReplayError(ValueError):
    """An exported package cannot be replayed safely."""


@dataclass(frozen=True, slots=True)
class CapsuleTrustClaim:
    """Exact portable identity offered to an external trust-domain verifier."""

    capsule_sha256: str
    package_id: str
    semantic_core_sha256: str
    trust_domain_id: str


class CapsuleTrustVerifier(Protocol):
    """Caller-supplied verifier; implementations must not mutate LoreLoop state."""

    def verify(self, claim: CapsuleTrustClaim) -> None:
        """Raise an exception unless the exact Capsule digest is trusted."""


@dataclass(frozen=True, slots=True)
class CapsuleReplayResult:
    package_id: str
    semantic_core_sha256: str
    trust_domain_id: str
    capsule_sha256: str
    documents: tuple[str, ...]
    verification_mode: Literal["no_key", "trusted"]


@dataclass(frozen=True, slots=True)
class ReplayedCapsuleExport:
    result: CapsuleReplayResult
    files: Mapping[str, bytes]


def _mapping(value: JsonValue | None, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise CapsuleReplayError(f"{label} must be an object")
    return value


def _array(value: JsonValue | None, label: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise CapsuleReplayError(f"{label} must be an array")
    return value


def _text(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str):
        raise CapsuleReplayError(f"{label} must be text")
    return value


def _sha256(value: JsonValue | None, label: str) -> str:
    digest = _text(value, label)
    if _SHA256_RE.fullmatch(digest) is None:
        raise CapsuleReplayError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _integer(value: JsonValue | None, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapsuleReplayError(f"{label} must be an integer")
    return value


def _scalar(value: JsonValue | None, label: str) -> None | bool | int | str:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise CapsuleReplayError(f"{label} must be a canonical scalar")


def _keys(value: Mapping[str, JsonValue], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise CapsuleReplayError(f"{label} has unexpected or missing fields")


def _safe_markdown_filename(value: JsonValue | None) -> str:
    filename = _text(value, "document filename")
    if (
        not filename.endswith(".md")
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
    ):
        raise CapsuleReplayError(f"unsafe document filename in Capsule: {filename!r}")
    return filename


def _semantic_evidence(value: JsonValue | None) -> tuple[SemanticEvidence, ...]:
    evidence: list[SemanticEvidence] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(_array(value, "semantic evidence")):
        item = _mapping(raw, f"semantic evidence {index}")
        _keys(item, _SEMANTIC_EVIDENCE_KEYS, f"semantic evidence {index}")
        identifier = _text(item.get("evidence_id"), f"semantic evidence id {index}")
        if identifier in identifiers:
            raise CapsuleReplayError("semantic core contains duplicate evidence ids")
        alias = _text(item.get("repository_alias"), f"semantic evidence repository {index}")
        path = _text(item.get("path"), f"semantic evidence path {index}")
        line = _integer(item.get("line"), f"semantic evidence line {index}")
        blob_digest = _sha256(item.get("blob_sha256"), f"semantic evidence blob digest {index}")
        start = _integer(item.get("start"), f"semantic evidence start {index}")
        end = _integer(item.get("end"), f"semantic evidence end {index}")
        if start < 0 or end < start:
            raise CapsuleReplayError(f"semantic evidence span {index} is invalid")
        try:
            source = SourceRef(alias, path, line)
            computed = evidence_id(EvidenceIdentity(alias, path, blob_digest, start, end))
        except (DetectionError, IdentityContractError) as exc:
            raise CapsuleReplayError(f"semantic evidence {index} is invalid: {exc}") from exc
        if computed != identifier:
            raise CapsuleReplayError(f"semantic evidence identity mismatch: {identifier}")
        identifiers.add(identifier)
        evidence.append(SemanticEvidence(identifier, source, blob_digest, start, end))
    if tuple(item.evidence_id for item in evidence) != tuple(sorted(identifiers)):
        raise CapsuleReplayError("semantic evidence is not in deterministic order")
    return tuple(evidence)


def _semantic_values(
    record: Mapping[str, JsonValue], index: int
) -> tuple[tuple[ProjectedValue, ...], dict[str, None | bool | int | str]]:
    raw_values = _mapping(record.get("values"), f"semantic record values {index}")
    order = tuple(
        _text(item, f"semantic record value pointer {index}")
        for item in _array(record.get("value_order"), f"semantic record value order {index}")
    )
    if len(order) != len(set(order)) or set(order) != set(raw_values):
        raise CapsuleReplayError(f"semantic record value order mismatch: {index}")
    values: list[ProjectedValue] = []
    payload: dict[str, None | bool | int | str] = {}
    for pointer in order:
        key = pointer.removeprefix("/")
        if not pointer.startswith("/") or not key or "/" in key:
            raise CapsuleReplayError(f"semantic record pointer is not a payload field: {pointer}")
        scalar = _scalar(raw_values.get(pointer), f"semantic record value {index}:{pointer}")
        try:
            values.append(ProjectedValue(pointer, scalar))
        except AstViolation as exc:
            raise CapsuleReplayError(f"semantic record pointer is invalid: {pointer}") from exc
        payload[key] = scalar
    return tuple(values), payload


def _semantic_records(
    value: JsonValue | None,
    evidence: tuple[SemanticEvidence, ...],
    trust: str,
    repository: str,
) -> tuple[SemanticRecord, ...]:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    records: list[SemanticRecord] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(_array(value, "semantic records")):
        item = _mapping(raw, f"semantic record {index}")
        _keys(item, _SEMANTIC_RECORD_KEYS, f"semantic record {index}")
        identifier = _text(item.get("record_id"), f"semantic record id {index}")
        if identifier in identifiers:
            raise CapsuleReplayError("semantic core contains duplicate record ids")
        source_id = _text(item.get("evidence_id"), f"semantic record evidence id {index}")
        source_evidence = evidence_by_id.get(source_id)
        if source_evidence is None:
            raise CapsuleReplayError(f"semantic record has unknown evidence: {identifier}")
        atom_kind = _text(item.get("atom_kind"), f"semantic record atom kind {index}")
        if not atom_kind:
            raise CapsuleReplayError(f"semantic record atom kind is empty: {identifier}")
        values, payload = _semantic_values(item, index)
        raw_kind = _text(item.get("row_kind"), f"semantic record row kind {index}")
        try:
            row_kind = DocumentRowKind(raw_kind)
        except ValueError as exc:
            raise CapsuleReplayError(f"semantic record row kind is invalid: {raw_kind}") from exc
        source = source_evidence.source
        computed_atom = atom_id(
            AtomIdentity(
                atom_kind,
                source.repository_alias,
                source.path,
                source_evidence.blob_sha256,
                source_evidence.start,
                source_evidence.end,
                payload,
            )
        )
        stored_atom = _text(item.get("atom_id"), f"semantic record atom id {index}")
        if computed_atom != stored_atom:
            raise CapsuleReplayError(f"semantic atom identity mismatch: {identifier}")
        prefix, separator, _ = identifier.partition("-")
        if not separator:
            raise CapsuleReplayError(f"semantic record id is invalid: {identifier}")
        try:
            computed_record = record_id(
                prefix,
                RecordIdentity(
                    trust,
                    repository,
                    {
                        "alias": source.repository_alias,
                        "path": source.path,
                        "kind": atom_kind,
                        "payload": payload,
                    },
                ),
            )
        except IdentityContractError as exc:
            raise CapsuleReplayError(f"semantic record identity is invalid: {identifier}") from exc
        if computed_record != identifier:
            raise CapsuleReplayError(f"semantic record identity mismatch: {identifier}")
        bindings = tuple(
            BindingEntry(
                projected.pointer,
                SourceBinding(
                    source_id,
                    stored_atom,
                    f"/payload{projected.pointer}",
                    SourceTransform.IDENTITY,
                ),
            )
            for projected in values
        )
        identifiers.add(identifier)
        records.append(
            SemanticRecord(
                identifier,
                stored_atom,
                atom_kind,
                row_kind,
                values,
                source_id,
                bindings,
            )
        )
    if {item.evidence_id for item in records} != set(evidence_by_id):
        raise CapsuleReplayError("semantic evidence set does not match semantic records")
    return tuple(records)


def _validate_core(root: Mapping[str, JsonValue]) -> SemanticCore:
    _keys(root, _ROOT_KEYS, "capsule")
    if root.get("schema_version") not in {2, 3}:
        raise CapsuleReplayError("unsupported capsule schema version; expected version 2 or 3")
    semantic = _mapping(root.get("semantic_core"), "semantic core")
    if frozenset(semantic) in _LEGACY_SEMANTIC_KEY_SETS:
        raise CapsuleReplayError(
            "legacy capsule lacks deterministic SemanticCore generation inputs; regenerate it"
        )
    semantic_keys = set(semantic)
    if semantic_keys != _SEMANTIC_KEYS and semantic_keys != _WORKTREE_SEMANTIC_KEYS:
        raise CapsuleReplayError("semantic core has unexpected fields")
    core_digest = _sha256(root.get("semantic_core_sha256"), "semantic core digest")
    if semantic_core_sha256(semantic) != core_digest:
        raise CapsuleReplayError("semantic core digest mismatch")
    package = _sha256(root.get("package_id"), "package id")
    if package_id(core_digest) != package:
        raise CapsuleReplayError("package id mismatch")
    project = _text(semantic.get("project_name"), "project name")
    if normalize_project_name(project) != project:
        raise CapsuleReplayError("semantic core project name is not canonical")
    trust = _sha256(semantic.get("trust_domain_id"), "trust domain id")
    repository = _sha256(
        semantic.get("repository_config_digest"), "repository configuration digest"
    )
    snapshot = _sha256(semantic.get("source_snapshot_sha256"), "source snapshot digest")
    raw_snapshot_kind = semantic.get("source_snapshot_kind", "commit")
    if raw_snapshot_kind == "commit":
        snapshot_kind = "commit"
    elif raw_snapshot_kind == "working_tree":
        snapshot_kind = "working_tree"
    else:
        raise CapsuleReplayError("semantic core source snapshot kind is invalid")
    evidence = _semantic_evidence(semantic.get("evidence"))
    records = _semantic_records(semantic.get("records"), evidence, trust, repository)
    core = SemanticCore(
        project,
        trust,
        repository,
        snapshot,
        records,
        evidence,
        core_digest,
        package,
        snapshot_kind,
    )
    if semantic_core_payload(core) != semantic:
        raise CapsuleReplayError("semantic core is not in deterministic portable form")
    return core


def _validate_applicability(value: JsonValue | None, optional: set[str]) -> None:
    items = tuple(
        _mapping(item, "document applicability") for item in _array(value, "document applicability")
    )
    if len(items) != 2:
        raise CapsuleReplayError("capsule must contain both optional document decisions")
    families: set[str] = set()
    for item in items:
        _keys(item, _APPLICABILITY_KEYS, "document applicability")
        family = _text(item.get("family"), "optional document family")
        families.add(family)
        expected = "present" if family in optional else "no_explicit_marker_within_detector_profile"
        if item.get("status") != expected:
            raise CapsuleReplayError(f"applicability disagrees with document set: {family}")
        for reason in _array(item.get("reason_ids"), "applicability reason ids"):
            _ = _text(reason, "applicability reason id")
    if families != _OPTIONAL_FAMILIES:
        raise CapsuleReplayError("optional document applicability families are invalid")


def _expected_applicability(document_set: DocumentSet) -> JsonValue:
    return [
        {
            "family": item.family.value,
            "status": item.status.value,
            "reason_ids": list(item.reason_ids),
        }
        for item in document_set.applicability
    ]


def _validate_documents(
    root: Mapping[str, JsonValue],
    files: Mapping[str, bytes],
    core: SemanticCore,
    *,
    allow_extra_non_markdown: bool,
) -> tuple[str, ...]:
    entries = _array(root.get("documents"), "capsule documents")
    if not 6 <= len(entries) <= 8:
        raise CapsuleReplayError("capsule must contain between six and eight documents")
    documents = tuple(_mapping(value, "capsule document") for value in entries)
    schema_version = root.get("schema_version")
    document_keys = _DOCUMENT_KEYS_V2 if schema_version == 2 else _DOCUMENT_KEYS_V3
    for document in documents:
        _keys(document, document_keys, "capsule document")
    filenames = tuple(_safe_markdown_filename(document.get("filename")) for document in documents)
    if len(filenames) != len(set(filenames)):
        raise CapsuleReplayError("capsule contains duplicate document filenames")
    expected_files = {CAPSULE_FILENAME, *filenames}
    missing = expected_files - set(files)
    extras = set(files) - expected_files
    rejected_extras = (
        {filename for filename in extras if filename.lower().endswith(".md")}
        if allow_extra_non_markdown
        else extras
    )
    if missing or rejected_extras:
        raise CapsuleReplayError(
            f"export file set mismatch; missing={sorted(missing)}, extra={sorted(rejected_extras)}"
        )
    expected_set = build_document_ast_set(core)
    if len(expected_set.documents) != len(documents):
        raise CapsuleReplayError("document set is not the deterministic SemanticCore projection")
    required: set[str] = set()
    optional: set[str] = set()
    document_ids: set[str] = set()
    for document, filename, expected_document in zip(
        documents, filenames, expected_set.documents, strict=True
    ):
        expected_ast_digest = document_ast_sha256(expected_document)
        stored_ast_digest = _sha256(document.get("ast_sha256"), f"AST digest for {filename}")
        document_id = _text(document.get("document_id"), f"document id for {filename}")
        if document_id in document_ids:
            raise CapsuleReplayError("capsule contains duplicate document ids")
        document_ids.add(document_id)
        if document_id != expected_document.document_id or filename != expected_document.path:
            raise CapsuleReplayError(
                f"document AST is not the deterministic SemanticCore projection: {filename}"
            )
        if expected_document.required_family is not None:
            required.add(expected_document.required_family.value)
        if expected_document.optional_family is not None:
            optional.add(expected_document.optional_family.value)
        if schema_version == 2:
            expected_ast = asdict(expected_document)
            ast = _mapping(document.get("ast"), f"document AST for {filename}")
            _keys(ast, _AST_KEYS, f"document AST for {filename}")
            if hashlib.sha256(canon_v4(ast)).hexdigest() != stored_ast_digest:
                raise CapsuleReplayError(f"document AST digest mismatch: {filename}")
            if ast.get("schema_version") != 4:
                raise CapsuleReplayError(f"unsupported document AST schema: {filename}")
            if ast.get("path") != filename or ast.get("document_id") != document_id:
                raise CapsuleReplayError(f"document identity disagrees with AST: {filename}")
            header = _mapping(ast.get("header"), f"AST header for {filename}")
            if header.get("package_id") != core.package_id:
                raise CapsuleReplayError(f"document AST belongs to another package: {filename}")
            if (
                header.get("trust_domain_id") != core.trust_domain_id
                or header.get("repository_config_digest") != core.repository_config_digest
            ):
                raise CapsuleReplayError(
                    f"document AST authority disagrees with SemanticCore: {filename}"
                )
            if header.get("authority_label") != expected_document.header.authority_label:
                raise CapsuleReplayError(f"document AST has an invalid authority label: {filename}")
            if canon_v4(ast) != canon_v4(expected_ast):
                raise CapsuleReplayError(
                    f"document AST is not the deterministic SemanticCore projection: {filename}"
                )
            expected_markdown = render_capsule_ast(cast(JsonValue, ast), filenames).encode()
        else:
            if expected_ast_digest != stored_ast_digest:
                raise CapsuleReplayError(f"document AST digest mismatch: {filename}")
            expected_markdown = render_document_ast(
                expected_document,
                filenames,
            ).content.encode()
        markdown = files[filename]
        if hashlib.sha256(markdown).hexdigest() != _sha256(
            document.get("markdown_sha256"), f"Markdown digest for {filename}"
        ):
            raise CapsuleReplayError(f"Markdown digest mismatch: {filename}")
        if markdown != expected_markdown:
            raise CapsuleReplayError(f"Markdown is not the rendering of its AST: {filename}")
    if required != _REQUIRED_FAMILIES or not optional <= _OPTIONAL_FAMILIES:
        raise CapsuleReplayError("document families are incomplete or invalid")
    _validate_applicability(root.get("applicability"), optional)
    if root.get("applicability") != _expected_applicability(expected_set):
        raise CapsuleReplayError(
            "document applicability is not the deterministic SemanticCore projection"
        )
    return filenames


def _replay_capsule_files(
    files: Mapping[str, bytes],
    *,
    allow_extra_non_markdown: bool,
    trust_verifier: CapsuleTrustVerifier | None = None,
) -> CapsuleReplayResult:
    return _load_replayed_capsule_files(
        files,
        allow_extra_non_markdown=allow_extra_non_markdown,
        trust_verifier=trust_verifier,
    ).result


def _load_replayed_capsule_files(
    files: Mapping[str, bytes],
    *,
    allow_extra_non_markdown: bool,
    trust_verifier: CapsuleTrustVerifier | None = None,
    root: Mapping[str, JsonValue] | None = None,
) -> ReplayedCapsuleExport:
    capsule_bytes = files.get(CAPSULE_FILENAME)
    if capsule_bytes is None:
        raise CapsuleReplayError(f"export is missing {CAPSULE_FILENAME}")
    if root is None:
        try:
            root = parse_capsule(capsule_bytes)
        except CapsuleIoError as exc:
            raise CapsuleReplayError(str(exc)) from exc
    core = _validate_core(root)
    try:
        filenames = _validate_documents(
            root,
            files,
            core,
            allow_extra_non_markdown=allow_extra_non_markdown,
        )
    except (AstViolation, CapsuleRenderError, IdentityContractError) as exc:
        raise CapsuleReplayError(f"document AST cannot be replayed: {exc}") from exc
    claim = CapsuleTrustClaim(
        hashlib.sha256(capsule_bytes).hexdigest(),
        core.package_id,
        core.semantic_core_sha256,
        core.trust_domain_id,
    )
    mode: Literal["no_key", "trusted"] = "no_key"
    if trust_verifier is not None:
        try:
            trust_verifier.verify(claim)
        except Exception as exc:
            raise CapsuleReplayError("trusted verifier rejected the exact Capsule digest") from exc
        mode = "trusted"
    return ReplayedCapsuleExport(
        CapsuleReplayResult(
            core.package_id,
            core.semantic_core_sha256,
            core.trust_domain_id,
            claim.capsule_sha256,
            filenames,
            mode,
        ),
        files,
    )


def replay_capsule_directory(
    export_dir: Path,
    *,
    trust_verifier: CapsuleTrustVerifier | None = None,
) -> CapsuleReplayResult:
    """Verify one directory package without reading source, keys, or LoreLoop state."""
    try:
        files, root = read_export_files_with_capsule(export_dir)
    except CapsuleIoError as exc:
        raise CapsuleReplayError(str(exc)) from exc
    return _load_replayed_capsule_files(
        files,
        allow_extra_non_markdown=True,
        trust_verifier=trust_verifier,
        root=root,
    ).result


def replay_capsule_archive(
    export_archive: Path,
    *,
    trust_verifier: CapsuleTrustVerifier | None = None,
) -> CapsuleReplayResult:
    """Verify one complete ZIP transport; every archive entry belongs to the package."""
    try:
        files, root = read_export_archive_with_capsule(export_archive)
    except ExportArchiveError as exc:
        raise CapsuleReplayError(str(exc)) from exc
    return _load_replayed_capsule_files(
        files,
        allow_extra_non_markdown=False,
        trust_verifier=trust_verifier,
        root=root,
    ).result


def replay_capsule_export(
    export_path: Path,
    *,
    trust_verifier: CapsuleTrustVerifier | None = None,
) -> CapsuleReplayResult:
    """Replay either the compatible directory form or the deliverable ZIP form."""
    return load_replayed_capsule_export(export_path, trust_verifier=trust_verifier).result


def load_replayed_capsule_export(
    export_path: Path,
    *,
    trust_verifier: CapsuleTrustVerifier | None = None,
) -> ReplayedCapsuleExport:
    """Read and verify one immutable package snapshot for replay-aware consumers."""
    if export_path.is_dir() and not export_path.is_symlink():
        try:
            files, root = read_export_files_with_capsule(export_path)
        except CapsuleIoError as exc:
            raise CapsuleReplayError(str(exc)) from exc
        return _load_replayed_capsule_files(
            files,
            allow_extra_non_markdown=True,
            trust_verifier=trust_verifier,
            root=root,
        )
    else:
        try:
            files, root = read_export_archive_with_capsule(export_path)
        except ExportArchiveError as exc:
            raise CapsuleReplayError(str(exc)) from exc
        return _load_replayed_capsule_files(
            files,
            allow_extra_non_markdown=False,
            trust_verifier=trust_verifier,
            root=root,
        )
