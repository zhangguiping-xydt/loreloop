"""Portable Capsule binding SemanticCore, document ASTs, and Markdown bytes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import TypeAlias

from .authoritative_ast import DocumentAst, DocumentSet
from .authoritative_documents import SourceDocument
from .authoritative_ids import CanonicalInput, canon_v4, package_id, semantic_core_sha256
from .authoritative_semantic import semantic_core_payload
from .authoritative_semantic_model import SemanticCore

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JSON_LOADS: Callable[[str], JsonValue] = json.loads
AST_ASDICT: Callable[[DocumentAst], CanonicalInput] = asdict
CAPSULE_FILENAME = ".loreloop-export.json"


class CapsuleError(ValueError):
    """Capsule bytes do not prove the supplied export products."""


@dataclass(frozen=True, slots=True)
class CapsuleArtifact:
    filename: str
    content: str
    sha256: str


def _ast_payload(document: DocumentAst) -> CanonicalInput:
    return AST_ASDICT(document)


def _document_payloads(
    document_set: DocumentSet,
    documents: tuple[SourceDocument, ...],
) -> list[CanonicalInput]:
    markdown = {document.filename: document for document in documents}
    if len(markdown) != len(documents):
        raise CapsuleError("duplicate Markdown filename")
    payloads: list[CanonicalInput] = []
    for document in document_set.documents:
        rendered = markdown.get(document.path)
        if rendered is None:
            raise CapsuleError(f"Markdown is missing for AST: {document.path}")
        ast = _ast_payload(document)
        payloads.append(
            {
                "document_id": document.document_id,
                "filename": document.path,
                "ast": ast,
                "ast_sha256": hashlib.sha256(canon_v4(ast)).hexdigest(),
                "markdown_sha256": hashlib.sha256(rendered.content.encode()).hexdigest(),
            }
        )
    if len(payloads) != len(documents):
        raise CapsuleError("Markdown set contains a file without a document AST")
    return payloads


def build_capsule(
    core: SemanticCore,
    document_set: DocumentSet,
    documents: tuple[SourceDocument, ...],
) -> CapsuleArtifact:
    """Build canonical JSON that exposes no raw Git OID, blob bytes, or key material."""
    payload = _capsule_payload(core, document_set, documents)
    content = canon_v4(payload).decode() + "\n"
    return CapsuleArtifact(CAPSULE_FILENAME, content, hashlib.sha256(content.encode()).hexdigest())


def _capsule_payload(
    core: SemanticCore,
    document_set: DocumentSet,
    documents: tuple[SourceDocument, ...],
) -> CanonicalInput:
    return {
        "schema_version": 2,
        "package_id": core.package_id,
        "semantic_core_sha256": core.semantic_core_sha256,
        "semantic_core": semantic_core_payload(core),
        "applicability": [
            {
                "family": item.family.value,
                "status": item.status.value,
                "reason_ids": list(item.reason_ids),
            }
            for item in document_set.applicability
        ],
        "documents": _document_payloads(document_set, documents),
    }


def _mapping(value: JsonValue | None, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise CapsuleError(f"{label} must be an object")
    return value


def _text(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str):
        raise CapsuleError(f"{label} must be text")
    return value


def verify_capsule(
    capsule: CapsuleArtifact,
    core: SemanticCore,
    document_set: DocumentSet,
    documents: tuple[SourceDocument, ...],
) -> None:
    """Recompute every portable identity and reject any changed product."""
    if capsule.filename != CAPSULE_FILENAME:
        raise CapsuleError("capsule filename mismatch")
    if hashlib.sha256(capsule.content.encode()).hexdigest() != capsule.sha256:
        raise CapsuleError("capsule artifact digest mismatch")
    try:
        root = _mapping(JSON_LOADS(capsule.content), "capsule")
    except json.JSONDecodeError as exc:
        raise CapsuleError("capsule is not valid JSON") from exc
    semantic_payload = root.get("semantic_core")
    computed_core = semantic_core_sha256(semantic_payload)
    if computed_core != _text(root.get("semantic_core_sha256"), "semantic digest"):
        raise CapsuleError("semantic core digest mismatch")
    if computed_core != core.semantic_core_sha256:
        raise CapsuleError("capsule belongs to a different SemanticCore")
    if package_id(computed_core) != _text(root.get("package_id"), "package id"):
        raise CapsuleError("package id mismatch")
    if canon_v4(root) != canon_v4(_capsule_payload(core, document_set, documents)):
        raise CapsuleError("capsule product closure mismatch")
