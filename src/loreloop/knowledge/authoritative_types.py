"""Snapshot, package, and journal contracts for authoritative export v4."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal

SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
HMAC_RE: Final = re.compile(r"hmac-sha256:[0-9a-f]{64}")


class ContractViolation(ValueError):
    """A foundation value violates its closed v4 contract."""

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ContractViolation(reason)


def _valid_relative_path(path: str) -> bool:
    parts = path.split("/")
    return (
        bool(path)
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in parts)
    )


class RefKind(StrEnum):
    IMPORT_TARGET = "import_target"
    CALL_TARGET = "call_target"
    DATA_TARGET = "data_target"
    REQUIREMENT_SUBJECT = "requirement_subject"


class JournalState(StrEnum):
    PREPARE_INTENT = "PREPARE_INTENT"
    STAGING = "STAGING"
    STAGED = "STAGED"
    INSTALL_INTENT = "INSTALL_INTENT"
    INSTALLED = "INSTALLED"
    CLEANUP_INTENT = "CLEANUP_INTENT"
    ABORTED = "ABORTED"


@dataclass(frozen=True, slots=True)
class GitObjectId:
    algorithm: Literal["sha1", "sha256"]
    hex: str

    def __post_init__(self) -> None:
        width = 40 if self.algorithm == "sha1" else 64
        _require(re.fullmatch(rf"[0-9a-f]{{{width}}}", self.hex) is not None, "invalid Git OID")

    @classmethod
    def parse(cls, tagged: str) -> GitObjectId:
        algorithm, separator, value = tagged.partition(":")
        _require(separator == ":" and algorithm in {"sha1", "sha256"}, "invalid tagged Git OID")
        if algorithm == "sha1":
            return cls("sha1", value)
        return cls("sha256", value)

    def git_sha1_hex(self) -> str:
        _require(self.algorithm == "sha1", "Git profile accepts only SHA-1")
        return self.hex


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    path: str
    mode: Literal["100644", "100755", "120000", "160000"]
    object_id: GitObjectId
    byte_length: int | None
    blob_sha256: str | None

    def __post_init__(self) -> None:
        _require(_valid_relative_path(self.path), "invalid snapshot path")
        gitlink = self.mode == "160000"
        if gitlink:
            _require(
                self.byte_length is None and self.blob_sha256 is None, "gitlink has blob bytes"
            )
        else:
            _require(self.byte_length is not None and self.byte_length >= 0, "invalid blob length")
            _require(
                self.blob_sha256 is not None and SHA256_RE.fullmatch(self.blob_sha256) is not None,
                "invalid blob digest",
            )


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    alias: str
    role: Literal["root", "peer", "submodule"]
    commit_id: GitObjectId
    tree_id: GitObjectId
    index_sha256: str
    entries: tuple[SnapshotEntry, ...]
    repository_identity_sha256: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.alias), "empty repository alias")
        _require(SHA256_RE.fullmatch(self.index_sha256) is not None, "invalid index digest")
        _require(
            self.repository_identity_sha256 is None
            or SHA256_RE.fullmatch(self.repository_identity_sha256) is not None,
            "invalid repository identity",
        )
        paths = tuple(entry.path for entry in self.entries)
        _require(len(paths) == len(set(paths)), "duplicate snapshot path")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    repositories: tuple[RepositorySnapshot, ...]

    def __post_init__(self) -> None:
        aliases = tuple(repository.alias for repository in self.repositories)
        roots = tuple(repository for repository in self.repositories if repository.role == "root")
        _require(
            bool(aliases) and len(aliases) == len(set(aliases)), "repository aliases are invalid"
        )
        _require(len(roots) == 1 and roots[0].alias == ".", "root snapshot is invalid")


@dataclass(frozen=True, slots=True)
class SealedSnapshot:
    trust_domain_id: str
    repository_config_digest: str
    repository_aliases: tuple[str, ...]
    source_snapshot_hmac: str
    authority_label: Literal["local_hmac_verified_as_is"] = field(
        default="local_hmac_verified_as_is", init=False
    )
    knowledge_db_status: Literal["not_loaded"] = field(default="not_loaded", init=False)

    def __post_init__(self) -> None:
        _require(SHA256_RE.fullmatch(self.trust_domain_id) is not None, "invalid trust domain id")
        _require(
            SHA256_RE.fullmatch(self.repository_config_digest) is not None,
            "invalid repository digest",
        )
        _require(
            bool(self.repository_aliases)
            and self.repository_aliases[0] == "."
            and len(self.repository_aliases) == len(set(self.repository_aliases)),
            "sealed repository aliases are invalid",
        )
        _require(HMAC_RE.fullmatch(self.source_snapshot_hmac) is not None, "invalid snapshot HMAC")


@dataclass(frozen=True, slots=True)
class PackageDigests:
    semantic_core_sha256: str
    package_id: str
    post_ast_sha256: str
    markdown_map_sha256: str
    payload_tree_digest: str
    source_snapshot_hmac: str

    def __post_init__(self) -> None:
        digests = (
            self.semantic_core_sha256,
            self.package_id,
            self.post_ast_sha256,
            self.markdown_map_sha256,
            self.payload_tree_digest,
        )
        _require(
            all(SHA256_RE.fullmatch(value) is not None for value in digests),
            "invalid package digest",
        )
        _require(HMAC_RE.fullmatch(self.source_snapshot_hmac) is not None, "invalid snapshot HMAC")


@dataclass(frozen=True, slots=True)
class PackageEnvelope:
    sealed_snapshot: SealedSnapshot
    digests: PackageDigests
    local_attestation: str
    schema_version: Literal[4] = field(default=4, init=False)
    authority_label: Literal["local_hmac_verified_as_is"] = field(
        default="local_hmac_verified_as_is", init=False
    )
    manifest_mode: Literal["0600"] = field(default="0600", init=False)

    def __post_init__(self) -> None:
        _require(
            self.sealed_snapshot.source_snapshot_hmac == self.digests.source_snapshot_hmac,
            "package snapshot seal mismatch",
        )
        _require(HMAC_RE.fullmatch(self.local_attestation) is not None, "invalid local attestation")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    version: Literal[4]
    txid: str
    target_name: str
    target_sha256: str
    parent_device: int
    parent_inode: int
    state: JournalState
    sequence: int
    package_id: str | None
    semantic_core_sha256: str | None
    post_ast_sha256: str | None
    markdown_map_sha256: str | None
    payload_tree_digest: str | None
    created_at: str
    updated_at: str
    abort_reason: str | None
    hmac: str

    def __post_init__(self) -> None:
        _require(
            self.version == 4 and re.fullmatch(r"[0-9a-f]{32}", self.txid) is not None,
            "invalid journal identity",
        )
        _require(bool(self.target_name) and "/" not in self.target_name, "invalid target name")
        _require(SHA256_RE.fullmatch(self.target_sha256) is not None, "invalid target digest")
        _require(self.parent_device >= 0 and self.parent_inode > 0, "invalid parent identity")
        expected = {
            JournalState.PREPARE_INTENT: 1,
            JournalState.STAGING: 2,
            JournalState.STAGED: 3,
            JournalState.INSTALL_INTENT: 4,
            JournalState.INSTALLED: 5,
            JournalState.CLEANUP_INTENT: 6,
        }
        if self.state is not JournalState.ABORTED:
            _require(self.sequence == expected[self.state], "invalid journal sequence")
        payload = (
            self.package_id,
            self.semantic_core_sha256,
            self.post_ast_sha256,
            self.markdown_map_sha256,
            self.payload_tree_digest,
        )
        early = self.state in {JournalState.PREPARE_INTENT, JournalState.STAGING}
        if early:
            _require(
                all(value is None for value in payload), "early journal contains package payload"
            )
        elif self.state is not JournalState.ABORTED:
            valid = all(
                value is not None and SHA256_RE.fullmatch(value) is not None for value in payload
            )
            _require(valid, "staged journal lacks package payload")
        _require(
            (self.state is JournalState.ABORTED) == bool(self.abort_reason), "invalid abort reason"
        )
        _require(HMAC_RE.fullmatch(self.hmac) is not None, "invalid journal HMAC")


FOUNDATION_MODEL_TYPES: Final = (
    GitObjectId,
    SnapshotEntry,
    RepositorySnapshot,
    SourceSnapshot,
    SealedSnapshot,
    PackageDigests,
    PackageEnvelope,
    JournalEntry,
)
