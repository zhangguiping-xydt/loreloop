"""Optional chain-backed attestation for portable authoritative exports."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..evidence.chain import EvidenceChain, EvidenceRecord
from .authoritative_capsule import CapsuleArtifact
from .authoritative_git import (
    GitSnapshotError,
    capture_source_snapshot,
    git_common_dir_identity,
    repository_snapshot_sha256,
    source_snapshot_sha256,
)
from .authoritative_types import SourceSnapshot

ATTESTATION_EVENT = "authoritative_export_attested"


class ExportTrustError(RuntimeError):
    """A portable package lacks a matching local trust-chain attestation."""


def _location_digest(path: Path) -> str:
    return hashlib.sha256(
        b"loreloop-repository-location-v1\0" + str(path.resolve()).encode("utf-8")
    ).hexdigest()


def _repository_paths(
    snapshot: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None,
) -> dict[str, Path]:
    paths = {".": root.resolve()}
    paths.update({name: path.resolve() for name, path in sorted((peers or {}).items())})
    for repository in snapshot.repositories:
        parent = paths.get(repository.alias)
        if parent is None:
            raise ExportTrustError(f"repository path is unavailable for {repository.alias!r}")
        prefix = "" if repository.alias == "." else f"{repository.alias}/"
        for entry in repository.entries:
            if entry.mode == "160000":
                paths[f"submodule:{prefix}{entry.path}"] = (parent / entry.path).resolve()
    return paths


def repository_bindings(
    snapshot: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None = None,
) -> dict[str, dict[str, object]]:
    """Bind each alias to both Git lineage and this reviewed checkout location."""
    paths = _repository_paths(snapshot, root, peers)
    bindings: dict[str, dict[str, object]] = {}
    for repository in snapshot.repositories:
        identity = repository.repository_identity_sha256
        if identity is None:
            raise ExportTrustError(f"repository {repository.alias!r} has no stable identity")
        try:
            common_device, common_inode = git_common_dir_identity(paths[repository.alias])
        except GitSnapshotError as exc:
            raise ExportTrustError(str(exc)) from exc
        bindings[repository.alias] = {
            "repository_identity_sha256": identity,
            "location_sha256": _location_digest(paths[repository.alias]),
            "checkout_path": str(paths[repository.alias]),
            "git_common_dir_device": common_device,
            "git_common_dir_inode": common_inode,
            "commit_id": repository.commit_id.hex,
            "tree_id": repository.tree_id.hex,
            "index_sha256": repository.index_sha256,
            "source_snapshot_sha256": repository_snapshot_sha256(repository),
        }
        if repository.snapshot_kind == "working_tree":
            bindings[repository.alias]["snapshot_kind"] = repository.snapshot_kind
            bindings[repository.alias]["worktree_state_sha256"] = str(
                repository.worktree_state_sha256
            )
        if repository.excluded_paths:
            bindings[repository.alias]["excluded_paths"] = list(repository.excluded_paths)
    return bindings


def attest_export(
    chain: EvidenceChain,
    workdir: Path,
    snapshot: SourceSnapshot,
    capsule: CapsuleArtifact,
    package_id: str,
    peers: Mapping[str, Path] | None = None,
) -> EvidenceRecord:
    """Append an operator-triggered local attestation without changing the capsule."""
    return chain.append(
        ATTESTATION_EVENT,
        {
            "package_id": package_id,
            "capsule_sha256": capsule.sha256,
            "source_snapshot_sha256": source_snapshot_sha256(snapshot),
            "repositories": repository_bindings(snapshot, workdir, peers),
        },
    )


def verify_trusted_export(
    records: Sequence[EvidenceRecord],
    workdir: Path,
    capsule: CapsuleArtifact,
    package_id: str,
    peers: Mapping[str, Path] | None = None,
) -> EvidenceRecord:
    """Require an exact attestation and reject alias substitution after export."""
    package_candidates = [
        record
        for record in records
        if record.event == ATTESTATION_EVENT and record.payload.get("package_id") == package_id
    ]
    if not package_candidates:
        raise ExportTrustError("no local trust attestation exists for this package")
    candidates = [
        record
        for record in package_candidates
        if isinstance(record.payload.get("capsule_sha256"), str)
        and hmac.compare_digest(str(record.payload["capsule_sha256"]), capsule.sha256)
    ]
    if not candidates:
        raise ExportTrustError("trusted capsule digest does not match the exported package")
    record = candidates[-1]
    stored = record.payload.get("repositories")
    if not isinstance(stored, dict):
        raise ExportTrustError("trusted repository bindings are invalid")
    stored_snapshot = record.payload.get("source_snapshot_sha256")
    if not isinstance(stored_snapshot, str):
        raise ExportTrustError("trusted source snapshot binding is invalid; attest a fresh export")
    snapshot_kinds = {
        str(binding.get("snapshot_kind", "commit"))
        for binding in stored.values()
        if isinstance(binding, dict)
    }
    if len(snapshot_kinds) != 1 or not snapshot_kinds <= {"commit", "working_tree"}:
        raise ExportTrustError("trusted repository snapshot modes are invalid")
    exclusions: dict[str, tuple[str, ...]] = {}
    for alias, binding in stored.items():
        if not isinstance(alias, str) or not isinstance(binding, dict):
            raise ExportTrustError("trusted repository bindings are invalid")
        raw_exclusions = binding.get("excluded_paths", [])
        if not isinstance(raw_exclusions, list) or not all(
            isinstance(path, str) for path in raw_exclusions
        ):
            raise ExportTrustError("trusted repository snapshot exclusions are invalid")
        if raw_exclusions:
            exclusions[alias] = tuple(raw_exclusions)
    try:
        current_snapshot = capture_source_snapshot(
            workdir,
            peers,
            require_clean=snapshot_kinds == {"working_tree"},
            working_tree=snapshot_kinds == {"working_tree"},
            excluded_paths=exclusions,
        )
    except GitSnapshotError as exc:
        raise ExportTrustError(f"cannot verify trusted source snapshot: {exc}") from exc
    current_snapshot_digest = source_snapshot_sha256(current_snapshot)
    if not hmac.compare_digest(stored_snapshot, current_snapshot_digest):
        raise ExportTrustError("trusted repository source snapshot changed after export")
    current_bindings = repository_bindings(current_snapshot, workdir, peers)
    if set(current_bindings) != set(stored):
        raise ExportTrustError(
            "trusted repository identity or checkout location changed after export"
        )
    configured = {name: path.resolve() for name, path in (peers or {}).items()}
    if "." in stored:
        configured["."] = workdir.resolve()
    for alias, raw_binding in stored.items():
        if not isinstance(alias, str) or not isinstance(raw_binding, dict):
            raise ExportTrustError("trusted repository bindings are invalid")
        raw_path = raw_binding.get("checkout_path")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise ExportTrustError("trusted repository checkout binding is invalid")
        path = configured.get(alias, Path(raw_path).resolve())
        if str(path) != raw_path or _location_digest(path) != raw_binding.get("location_sha256"):
            raise ExportTrustError(
                "trusted repository identity or checkout location changed after export"
            )
        current = current_bindings.get(alias)
        if current is None:
            raise ExportTrustError("trusted repository bindings are invalid")
        for field in (
            "repository_identity_sha256",
            "git_common_dir_device",
            "git_common_dir_inode",
            "commit_id",
            "tree_id",
            "index_sha256",
            "source_snapshot_sha256",
        ):
            if raw_binding.get(field) != current.get(field):
                if field in {"git_common_dir_device", "git_common_dir_inode"}:
                    raise ExportTrustError(
                        "trusted repository checkout instance changed after export"
                    )
                raise ExportTrustError("trusted repository source snapshot changed after export")
    if set(configured) != {alias for alias in stored if not alias.startswith("submodule:")}:
        raise ExportTrustError(
            "trusted repository identity or checkout location changed after export"
        )
    return record
