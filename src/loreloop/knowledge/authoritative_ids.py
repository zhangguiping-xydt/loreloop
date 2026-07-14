"""Canonical bytes and domain-separated identities for export v4."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias, assert_never

CanonicalScalar: TypeAlias = None | bool | int | str
CanonicalInput: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | Sequence["CanonicalInput"]
    | Mapping[str, "CanonicalInput"]
    | Mapping[int, "CanonicalInput"]
    | Set["CanonicalInput"]
)
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
ID_RE: Final = re.compile(r"([A-Z]+)-[0-9a-f]{64}")
PREFIX_ORDINALS: Final = {
    "EVD": 10,
    "ATM": 20,
    "CAND": 30,
    "FACT": 40,
    "API": 40,
    "CLI": 40,
    "UI": 40,
    "DATAOP": 40,
    "PERM": 40,
    "CFG": 40,
    "DEPLOY": 40,
    "STATE": 40,
    "ERR": 40,
    "TEST": 40,
    "DEP": 40,
    "DOCSRC": 40,
    "MOD": 50,
    "DATA": 70,
    "REQ": 80,
    "REF": 90,
    "EDGE": 100,
    "ACC": 110,
    "APPL": 120,
    "MREP": 130,
}


class IdentityContractError(ValueError):
    """An identity input violates the frozen formula contract."""

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CanonicalValueError(IdentityContractError):
    """A value is outside the closed canon-v4 domain."""


def _canonical_string(value: str) -> bytes:
    if unicodedata.normalize("NFC", value) != value:
        raise CanonicalValueError("string is not NFC")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CanonicalValueError("string contains a surrogate")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def canon_v4(value: CanonicalInput) -> bytes:
    """Serialize the exact closed canon-v4 value domain."""
    match value:
        case None:
            return b"null"
        case bool() as boolean:
            return b"true" if boolean else b"false"
        case int() as integer:
            if not -MAX_SAFE_INTEGER <= integer <= MAX_SAFE_INTEGER:
                raise CanonicalValueError("integer is outside the safe range")
            return str(integer).encode("ascii")
        case str() as string:
            return _canonical_string(string)
        case list() | tuple() as array:
            return b"[" + b",".join(canon_v4(item) for item in array) + b"]"
        case Mapping() as mapping:
            encoded: list[tuple[bytes, bytes]] = []
            normalized_keys: set[str] = set()
            for key, item in mapping.items():
                if not isinstance(key, str):
                    raise CanonicalValueError("map key is not a string")
                normalized = unicodedata.normalize("NFC", key)
                if normalized in normalized_keys:
                    raise CanonicalValueError("duplicate normalized map key")
                normalized_keys.add(normalized)
                key_bytes = _canonical_string(key)
                encoded.append((key.encode(), key_bytes + b":" + canon_v4(item)))
            encoded.sort(key=lambda entry: entry[0])
            return b"{" + b",".join(entry[1] for entry in encoded) + b"}"
        case float() | bytes() | Set() | Sequence():
            raise CanonicalValueError("value is outside canon-v4")
        case _:
            assert_never(value)


def _sha256(domain: bytes, payload: bytes) -> str:
    return hashlib.sha256(domain + payload).hexdigest()


def _require_sha256(value: str, label: str) -> None:
    if SHA256_RE.fullmatch(value) is None:
        raise IdentityContractError(f"invalid {label}")


def _walk_ids(value: CanonicalInput) -> tuple[str, ...]:
    match value:
        case str() as string:
            return (string,) if ID_RE.fullmatch(string) is not None else ()
        case list() | tuple() as array:
            return tuple(found for item in array for found in _walk_ids(item))
        case Mapping() as mapping:
            return tuple(found for item in mapping.values() for found in _walk_ids(item))
        case None | bool() | int() | float() | bytes() | Set() | Sequence():
            return ()
        case _:
            assert_never(value)


@dataclass(frozen=True, slots=True)
class RecordIdentity:
    trust_domain_id: str
    repository_config_digest: str
    semantic_key: Mapping[str, CanonicalInput]


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    alias: str
    path: str
    redacted_blob_sha256: str
    redacted_start: int
    redacted_end: int


@dataclass(frozen=True, slots=True)
class AtomIdentity:
    kind: str
    alias: str
    path: str
    redacted_blob_sha256: str
    redacted_start: int
    redacted_end: int
    payload_signature: CanonicalInput


@dataclass(frozen=True, slots=True)
class RefIdentity:
    kind: str
    relation_or_access_or_null: str | None
    source_record_id: str
    target_signature: str
    evidence_id: str
    branch_ordinal_or_null: int | None


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    atom_id: str
    disposition: Literal["source_record", "implementation_detail", "non_contract_atom"]


def record_id(prefix: str, identity: RecordIdentity) -> str:
    """Compute one full prefixed v4 record identifier."""
    consumer = PREFIX_ORDINALS.get(prefix)
    if consumer is None or consumer < 40 or prefix == "REF":
        raise IdentityContractError("unknown record prefix")
    _require_sha256(identity.trust_domain_id, "trust domain id")
    _require_sha256(identity.repository_config_digest, "repository config digest")
    for nested_id in _walk_ids(identity.semantic_key):
        match = ID_RE.fullmatch(nested_id)
        producer = None if match is None else PREFIX_ORDINALS.get(match.group(1))
        if producer is None or producer >= consumer:
            raise IdentityContractError("semantic key contains same-or-later identity")
    payload = canon_v4(
        {
            "trust_domain_id": identity.trust_domain_id,
            "repository_config_digest": identity.repository_config_digest,
            "semantic_key": identity.semantic_key,
        }
    )
    domain = b"loreloop-record-v4\0" + prefix.encode() + b"\0"
    return f"{prefix}-{_sha256(domain, payload)}"


def evidence_id(identity: EvidenceIdentity) -> str:
    _require_sha256(identity.redacted_blob_sha256, "redacted blob digest")
    payload = canon_v4(
        {
            "alias": identity.alias,
            "path": identity.path,
            "redacted_blob_sha256": identity.redacted_blob_sha256,
            "redacted_start": identity.redacted_start,
            "redacted_end": identity.redacted_end,
        }
    )
    return "EVD-" + _sha256(b"loreloop-evidence-v4\0", payload)


def atom_id(identity: AtomIdentity) -> str:
    _require_sha256(identity.redacted_blob_sha256, "redacted blob digest")
    payload = canon_v4(
        {
            "kind": identity.kind,
            "alias": identity.alias,
            "path": identity.path,
            "redacted_blob_sha256": identity.redacted_blob_sha256,
            "redacted_start": identity.redacted_start,
            "redacted_end": identity.redacted_end,
            "payload_signature": identity.payload_signature,
        }
    )
    return "ATM-" + _sha256(b"loreloop-atom-v4\0", payload)


def ref_id(identity: RefIdentity) -> str:
    payload = canon_v4(
        {
            "kind": identity.kind,
            "relation_or_access_or_null": identity.relation_or_access_or_null,
            "source_record_id": identity.source_record_id,
            "target_signature": identity.target_signature,
            "evidence_id": identity.evidence_id,
            "branch_ordinal_or_null": identity.branch_ordinal_or_null,
        }
    )
    return "REF-" + _sha256(b"loreloop-ref-v4\0", payload)


def candidate_id(identity: CandidateIdentity) -> str:
    payload = canon_v4({"atom_id": identity.atom_id, "disposition": identity.disposition})
    return "CAND-" + _sha256(b"loreloop-candidate-v4\0", payload)


def semantic_core_sha256(core: CanonicalInput) -> str:
    return _sha256(b"loreloop-semantic-core-v4\0", canon_v4(core))


def package_id(core_sha256: str) -> str:
    _require_sha256(core_sha256, "semantic core digest")
    return _sha256(b"loreloop-package-v4\0", core_sha256.encode())


def keyed_digest(key: bytes, domain: bytes, payload: CanonicalInput) -> str:
    return "hmac-sha256:" + hmac.new(key, domain + canon_v4(payload), hashlib.sha256).hexdigest()


def require_unique_ids(identities: Sequence[str]) -> None:
    if len(set(identities)) != len(identities):
        raise IdentityContractError("duplicate semantic identifier")
