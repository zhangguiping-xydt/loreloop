"""Optional chain-backed attestation for portable authoritative exports."""

from __future__ import annotations

import hashlib
import hmac
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..evidence.chain import EvidenceChain, EvidenceRecord
from .authoritative_capsule import CapsuleArtifact
from .authoritative_types import SourceSnapshot

ATTESTATION_EVENT = "authoritative_export_attested"


class ExportTrustError(RuntimeError):
    """A portable package lacks a matching local trust-chain attestation."""


def _location_digest(path: Path) -> str:
    return hashlib.sha256(
        b"loreloop-repository-location-v1\0" + str(path.resolve()).encode("utf-8")
    ).hexdigest()


def _git_common_dir_identity(path: Path) -> tuple[int, int]:
    completed = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=path,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ExportTrustError(f"cannot inspect trusted Git checkout: {path}")
    raw = completed.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        raise ExportTrustError(f"trusted Git checkout has no common directory: {path}")
    common = Path(raw)
    if not common.is_absolute():
        common = path / common
    try:
        metadata = common.resolve().stat()
    except OSError as exc:
        raise ExportTrustError(f"cannot inspect trusted Git common directory: {path}") from exc
    if metadata.st_dev < 0 or metadata.st_ino <= 0:
        raise ExportTrustError(f"trusted Git common directory has no stable local identity: {path}")
    return metadata.st_dev, metadata.st_ino


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
) -> dict[str, dict[str, str]]:
    """Bind each alias to both Git lineage and this reviewed checkout location."""
    paths = _repository_paths(snapshot, root, peers)
    bindings: dict[str, dict[str, str]] = {}
    for repository in snapshot.repositories:
        identity = repository.repository_identity_sha256
        if identity is None:
            raise ExportTrustError(f"repository {repository.alias!r} has no stable identity")
        common_device, common_inode = _git_common_dir_identity(paths[repository.alias])
        bindings[repository.alias] = {
            "repository_identity_sha256": identity,
            "location_sha256": _location_digest(paths[repository.alias]),
            "checkout_path": str(paths[repository.alias]),
            "git_common_dir_device": common_device,
            "git_common_dir_inode": common_inode,
        }
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
        common_device, common_inode = _git_common_dir_identity(path)
        if (
            type(raw_binding.get("git_common_dir_device")) is not int
            or type(raw_binding.get("git_common_dir_inode")) is not int
            or raw_binding["git_common_dir_device"] != common_device
            or raw_binding["git_common_dir_inode"] != common_inode
        ):
            raise ExportTrustError(
                "trusted repository checkout instance changed after export"
            )
        completed = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=path,
            check=False,
            capture_output=True,
        )
        roots = tuple(sorted(line for line in completed.stdout.splitlines() if line))
        identity = hashlib.sha256(
            b"loreloop-git-roots-v1\0" + b"\0".join(roots)
        ).hexdigest()
        if completed.returncode != 0 or not roots or not hmac.compare_digest(
            identity, str(raw_binding.get("repository_identity_sha256"))
        ):
            raise ExportTrustError("trusted repository lineage changed after export")
    if set(configured) != {alias for alias in stored if not alias.startswith("submodule:")}:
        raise ExportTrustError(
            "trusted repository identity or checkout location changed after export"
        )
    return record
