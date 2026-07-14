"""Portable no-key and caller-attested replay for authoritative exports."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from .authoritative_capsule import CAPSULE_FILENAME, JsonValue
from .authoritative_capsule_io import CapsuleIoError, parse_capsule, read_export_files
from .authoritative_capsule_render import CapsuleRenderError, render_capsule_ast
from .authoritative_ids import IdentityContractError, canon_v4, package_id, semantic_core_sha256

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ROOT_KEYS = {
    "applicability",
    "documents",
    "package_id",
    "schema_version",
    "semantic_core",
    "semantic_core_sha256",
}
_DOCUMENT_KEYS = {"ast", "ast_sha256", "document_id", "filename", "markdown_sha256"}
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
    "records",
    "repository_config_digest",
    "source_snapshot_sha256",
    "trust_domain_id",
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


def _validate_core(root: Mapping[str, JsonValue]) -> tuple[str, str, str, str]:
    _keys(root, _ROOT_KEYS, "capsule")
    if root.get("schema_version") != 2:
        raise CapsuleReplayError("unsupported capsule schema version; expected version 2")
    semantic = _mapping(root.get("semantic_core"), "semantic core")
    _keys(semantic, _SEMANTIC_KEYS, "semantic core")
    core_digest = _sha256(root.get("semantic_core_sha256"), "semantic core digest")
    if semantic_core_sha256(semantic) != core_digest:
        raise CapsuleReplayError("semantic core digest mismatch")
    package = _sha256(root.get("package_id"), "package id")
    if package_id(core_digest) != package:
        raise CapsuleReplayError("package id mismatch")
    trust = _sha256(semantic.get("trust_domain_id"), "trust domain id")
    repository = _sha256(
        semantic.get("repository_config_digest"), "repository configuration digest"
    )
    _ = _sha256(semantic.get("source_snapshot_sha256"), "source snapshot digest")
    _ = _array(semantic.get("records"), "semantic records")
    return package, core_digest, trust, repository


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


def _validate_documents(
    root: Mapping[str, JsonValue],
    files: Mapping[str, bytes],
    package: str,
    trust: str,
    repository: str,
) -> tuple[str, ...]:
    entries = _array(root.get("documents"), "capsule documents")
    if not 6 <= len(entries) <= 8:
        raise CapsuleReplayError("capsule must contain between six and eight documents")
    documents = tuple(_mapping(value, "capsule document") for value in entries)
    for document in documents:
        _keys(document, _DOCUMENT_KEYS, "capsule document")
    filenames = tuple(_safe_markdown_filename(document.get("filename")) for document in documents)
    if len(filenames) != len(set(filenames)):
        raise CapsuleReplayError("capsule contains duplicate document filenames")
    expected_files = {CAPSULE_FILENAME, *filenames}
    missing = expected_files - set(files)
    extra_markdown = {
        filename for filename in set(files) - expected_files if filename.lower().endswith(".md")
    }
    if missing or extra_markdown:
        raise CapsuleReplayError(
            f"export file set mismatch; missing={sorted(missing)}, "
            f"extra_markdown={sorted(extra_markdown)}"
        )
    required: set[str] = set()
    optional: set[str] = set()
    document_ids: set[str] = set()
    for document, filename in zip(documents, filenames, strict=True):
        ast = _mapping(document.get("ast"), f"document AST for {filename}")
        _keys(ast, _AST_KEYS, f"document AST for {filename}")
        ast_digest = hashlib.sha256(canon_v4(ast)).hexdigest()
        if ast_digest != _sha256(document.get("ast_sha256"), f"AST digest for {filename}"):
            raise CapsuleReplayError(f"document AST digest mismatch: {filename}")
        if ast.get("schema_version") != 4:
            raise CapsuleReplayError(f"unsupported document AST schema: {filename}")
        if ast.get("path") != filename or ast.get("document_id") != document.get("document_id"):
            raise CapsuleReplayError(f"document identity disagrees with AST: {filename}")
        document_id = _text(document.get("document_id"), f"document id for {filename}")
        if document_id in document_ids:
            raise CapsuleReplayError("capsule contains duplicate document ids")
        document_ids.add(document_id)
        required_family, optional_family = ast.get("required_family"), ast.get("optional_family")
        if isinstance(required_family, str):
            required.add(required_family)
        if isinstance(optional_family, str):
            optional.add(optional_family)
        header = _mapping(ast.get("header"), f"AST header for {filename}")
        if header.get("package_id") != package:
            raise CapsuleReplayError(f"document AST belongs to another package: {filename}")
        if (
            header.get("trust_domain_id") != trust
            or header.get("repository_config_digest") != repository
        ):
            raise CapsuleReplayError(
                f"document AST authority disagrees with SemanticCore: {filename}"
            )
        if header.get("authority_label") != "git_snapshot_verified":
            raise CapsuleReplayError(f"document AST has an invalid authority label: {filename}")
        expected_markdown = render_capsule_ast(cast(JsonValue, ast), filenames).encode()
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
    return filenames


def replay_capsule_directory(
    export_dir: Path,
    *,
    trust_verifier: CapsuleTrustVerifier | None = None,
) -> CapsuleReplayResult:
    """Verify the complete portable package without reading source, keys, or LoreLoop state."""
    try:
        files = read_export_files(export_dir)
    except CapsuleIoError as exc:
        raise CapsuleReplayError(str(exc)) from exc
    capsule_bytes = files.get(CAPSULE_FILENAME)
    if capsule_bytes is None:
        raise CapsuleReplayError(f"export is missing {CAPSULE_FILENAME}")
    try:
        root = parse_capsule(capsule_bytes)
    except CapsuleIoError as exc:
        raise CapsuleReplayError(str(exc)) from exc
    package, core_digest, trust, repository = _validate_core(root)
    try:
        filenames = _validate_documents(root, files, package, trust, repository)
    except (CapsuleRenderError, IdentityContractError) as exc:
        raise CapsuleReplayError(f"document AST cannot be replayed: {exc}") from exc
    claim = CapsuleTrustClaim(
        hashlib.sha256(capsule_bytes).hexdigest(), package, core_digest, trust
    )
    mode: Literal["no_key", "trusted"] = "no_key"
    if trust_verifier is not None:
        try:
            trust_verifier.verify(claim)
        except Exception as exc:
            raise CapsuleReplayError("trusted verifier rejected the exact Capsule digest") from exc
        mode = "trusted"
    return CapsuleReplayResult(
        package,
        core_digest,
        trust,
        claim.capsule_sha256,
        filenames,
        mode,
    )
