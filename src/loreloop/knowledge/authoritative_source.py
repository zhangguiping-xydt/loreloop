"""Read and detect bytes from an already captured authoritative Git snapshot."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .authoritative_detector_config import detect_config_source
from .authoritative_detector_extended import detect_extended_source, is_extended_source
from .authoritative_detector_graphql import detect_graphql_source
from .authoritative_detector_openapi import detect_openapi_source
from .authoritative_detector_prisma import detect_prisma_schema
from .authoritative_detector_proto import detect_proto_source
from .authoritative_detector_python import detect_python_source
from .authoritative_detector_sql import detect_sql_source
from .authoritative_detector_typescript import detect_typescript_source
from .authoritative_git import GitSnapshotError, verify_source_snapshot_metadata
from .authoritative_git_objects import GitObjectError, read_blob_batch
from .authoritative_records import DetectionError, DetectionReport, merge_reports
from .authoritative_report_normalize import normalize_detection_report
from .authoritative_types import SourceSnapshot

_OPENAPI_NAME = re.compile(r"^(?:openapi|swagger)(?:[._-].*)?\.(?:json|ya?ml)$", re.I)
_OPENAPI_MARKER = re.compile(
    r'''(?mx)
    ^(?:
        openapi\s*:\s*["']?3(?:\.\d+){1,2}["']?\s*(?:\#.*)?
      | swagger\s*:\s*["']?2\.0["']?\s*(?:\#.*)?
      | [ \t]*\{?[ \t]*["']openapi["']\s*:\s*["']3(?:\.\d+){1,2}["']
      | [ \t]*\{?[ \t]*["']swagger["']\s*:\s*["']2\.0["']
    )
    ''',
    re.I,
)
_AUXILIARY_SEGMENTS = frozenset(
    {"test", "tests", "__tests__", "fixtures", "snapshots", "__snapshots__"}
)
_AUXILIARY_FILE = re.compile(
    r"(?:^test_.*|.*(?:[._-](?:test|tests|spec|fixture|snapshot))\.[^.]+$|"
    + r".*(?:[._-]generated)\.[^.]+$|.*_test\.go$|conftest\.py$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SnapshotBlob:
    repository_alias: str
    path: str
    data: bytes
    blob_sha256: str


def _repository_paths(
    snapshot: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None,
) -> dict[str, Path]:
    paths = {".": root.resolve()}
    paths.update({alias: path.resolve() for alias, path in sorted((peers or {}).items())})
    for repository in snapshot.repositories:
        parent = paths.get(repository.alias)
        if parent is None:
            raise GitSnapshotError(f"snapshot repository {repository.alias!r} has no source path")
        for entry in repository.entries:
            if entry.mode != "160000":
                continue
            prefix = "" if repository.alias == "." else f"{repository.alias}/"
            paths[f"submodule:{prefix}{entry.path}"] = (parent / entry.path).resolve()
    return paths


def read_snapshot_blobs(
    snapshot: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None = None,
) -> tuple[SnapshotBlob, ...]:
    """Read exact committed bytes and prove they still match the captured snapshot."""
    verify_source_snapshot_metadata(snapshot, root, peers)
    paths = _repository_paths(snapshot, root, peers)
    blobs: list[SnapshotBlob] = []
    for repository in snapshot.repositories:
        repo = paths[repository.alias]
        entries = tuple(entry for entry in repository.entries if entry.mode not in {"120000", "160000"})
        try:
            payloads = read_blob_batch(
                repo, tuple(entry.object_id.git_sha1_hex() for entry in entries)
            )
        except GitObjectError as exc:
            raise GitSnapshotError(str(exc)) from exc
        for entry in entries:
            data = payloads[entry.object_id.git_sha1_hex()]
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != entry.byte_length or digest != entry.blob_sha256:
                raise GitSnapshotError(
                    f"blob {repository.alias}:{entry.path} differs from the captured snapshot"
                )
            blobs.append(SnapshotBlob(repository.alias, entry.path, data, digest))
    verify_source_snapshot_metadata(snapshot, root, peers)
    return tuple(blobs)


def _text(blob: SnapshotBlob) -> str:
    try:
        return blob.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DetectionError(
            f"supported source is not UTF-8: {blob.repository_alias}:{blob.path}"
        ) from exc


def _is_config(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return name in {
        "pyproject.toml",
        "package.json",
        ".env",
        ".env.example",
        ".env.sample",
        "requirements.txt",
        "requirements-dev.txt",
    } or name.endswith(".env.example")


def _is_openapi_contract(path: str, text: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return bool(
        _OPENAPI_NAME.fullmatch(name)
        or (
            name.lower().endswith((".json", ".yaml", ".yml"))
            and _OPENAPI_MARKER.search(text)
        )
    )


def excluded_semantic_source(path: str) -> bool:
    """Keep tests/generated artifacts in the snapshot but outside product semantics."""
    parts = path.lower().split("/")
    return any(part in _AUXILIARY_SEGMENTS for part in parts[:-1]) or bool(
        _AUXILIARY_FILE.fullmatch(parts[-1])
    )


def detector_profile(blob: SnapshotBlob) -> str | None:
    """Name the deterministic detector that will inspect this committed blob."""
    lower = blob.path.lower()
    if excluded_semantic_source(blob.path):
        return None
    if lower.endswith(".py"):
        return "python"
    if lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return "typescript_javascript"
    if lower.endswith(".sql"):
        return "sql"
    if _is_config(blob.path):
        return "configuration"
    if lower.endswith(".prisma"):
        return "prisma"
    if lower.endswith((".graphql", ".graphqls", ".gql")):
        return "graphql"
    if lower.endswith(".proto"):
        return "protobuf"
    if lower.endswith((".json", ".yaml", ".yml")):
        text = _text(blob)
        if _is_openapi_contract(blob.path, text):
            return "openapi_swagger"
        if is_extended_source(blob.path):
            return "container_platform"
        return None
    if is_extended_source(blob.path):
        return "extended_language_or_platform"
    return None


def detect_snapshot_blobs(
    blobs: tuple[SnapshotBlob, ...],
    requirements: tuple[str, ...] = (),
) -> DetectionReport:
    """Run deterministic detectors over one already verified blob set."""
    reports: list[DetectionReport] = []
    for blob in blobs:
        if excluded_semantic_source(blob.path):
            continue
        try:
            report = _detect_snapshot_blob(blob)
        except DetectionError as exc:
            raise DetectionError(
                f"{blob.repository_alias}:{blob.path}: {exc}"
            ) from exc
        if report is not None:
            reports.append(report)
    if requirements:
        from .authoritative_requirements_input import detect_requirement_materials

        reports.append(detect_requirement_materials(blobs, requirements))
    return normalize_detection_report(merge_reports(*reports))


def _detect_snapshot_blob(blob: SnapshotBlob) -> DetectionReport | None:
    lower = blob.path.lower()
    if lower.endswith(".py"):
        return detect_python_source(_text(blob), blob.repository_alias, blob.path)
    if lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return detect_typescript_source(_text(blob), blob.repository_alias, blob.path)
    if lower.endswith(".sql"):
        return detect_sql_source(_text(blob), blob.repository_alias, blob.path)
    if _is_config(blob.path):
        return detect_config_source(_text(blob), blob.repository_alias, blob.path)
    if lower.endswith(".prisma"):
        return detect_prisma_schema(_text(blob), blob.repository_alias, blob.path)
    if lower.endswith((".graphql", ".graphqls", ".gql")):
        return detect_graphql_source(_text(blob), blob.repository_alias, blob.path)
    if lower.endswith(".proto"):
        return detect_proto_source(_text(blob), blob.repository_alias, blob.path)
    if lower.endswith((".json", ".yaml", ".yml")):
        text = _text(blob)
        if _is_openapi_contract(blob.path, text):
            return detect_openapi_source(text, blob.repository_alias, blob.path)
        if is_extended_source(blob.path):
            return detect_extended_source(text, blob.repository_alias, blob.path)
        return None
    if is_extended_source(blob.path):
        return detect_extended_source(_text(blob), blob.repository_alias, blob.path)
    return None


def detect_source_snapshot(
    snapshot: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None = None,
    requirements: tuple[str, ...] = (),
) -> DetectionReport:
    """Read and detect one exact committed source snapshot."""
    return detect_snapshot_blobs(read_snapshot_blobs(snapshot, root, peers), requirements)
