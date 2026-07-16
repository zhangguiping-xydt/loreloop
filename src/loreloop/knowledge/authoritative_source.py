"""Read and detect bytes from an already captured authoritative Git snapshot."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path

from .authoritative_detector_config import detect_config_source
from .authoritative_detector_extended import detect_extended_source, is_extended_source
from .authoritative_detector_graphql import detect_graphql_source
from .authoritative_detector_openapi import detect_openapi_source, has_supported_openapi_root
from .authoritative_detector_prisma import detect_prisma_schema
from .authoritative_detector_proto import detect_proto_source
from .authoritative_detector_python import detect_python_source
from .authoritative_detector_sql import detect_sql_source
from .authoritative_detector_tests import (
    detect_test_source,
    is_supported_test_evidence_path,
    is_web_scenario_path,
)
from .authoritative_detector_typescript import detect_typescript_source
from .authoritative_detector_ui import detect_vue_source
from .authoritative_git import (
    GitSnapshotError,
    read_blob_batch,
    verify_source_snapshot_metadata,
)
from .authoritative_records import (
    DetectionError,
    DetectionReport,
    SourceIssueRecord,
    SourceRef,
    merge_reports,
)
from .authoritative_report_normalize import normalize_detection_report
from .authoritative_types import SourceSnapshot

MAX_SEMANTIC_BLOB_BYTES = 16 * 1024 * 1024
MAX_SEMANTIC_TOTAL_BYTES = 256 * 1024 * 1024

_AUXILIARY_SEGMENTS = frozenset(
    {"test", "tests", "__tests__", "fixtures", "snapshots", "__snapshots__"}
)
_AUXILIARY_FILE = re.compile(
    r"(?:^test_.*|.*(?:[._-](?:test|tests|spec|fixture|snapshot))\.[^.]+$|"
    + r".*(?:[._-]generated)\.[^.]+$|.*_test\.go$|conftest\.py$)",
    re.IGNORECASE,
)
_LEGACY_SOURCE_ENCODINGS = ("gb18030",)
_REPAIRED_UTF8_ENCODING = "utf-8-repaired"
_MAX_REPAIRED_UTF8_REPLACEMENTS = 4_096


@dataclass(frozen=True, slots=True)
class SnapshotBlob:
    repository_alias: str
    path: str
    data: bytes | None
    blob_sha256: str
    byte_length: int | None = None


@dataclass(frozen=True, slots=True)
class DecodedSourceText:
    text: str
    encoding: str
    replacement_count: int = 0


class SourceDecodeError(DetectionError):
    """A supported source blob cannot be decoded without inventing content."""


def _looks_like_text(text: str) -> bool:
    if "\x00" in text:
        return False
    controls = sum(ord(character) < 32 and character not in "\t\n\r\f" for character in text)
    return controls <= max(2, len(text) // 2_000)


def _decode_source_text(blob: SnapshotBlob) -> DecodedSourceText | None:
    if blob.data is None:
        return None
    try:
        text = blob.data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return DecodedSourceText(text, "utf-8") if _looks_like_text(text) else None
    for encoding in _LEGACY_SOURCE_ENCODINGS:
        try:
            text = blob.data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_like_text(text):
            return DecodedSourceText(text, encoding)
    repaired = blob.data.decode("utf-8", errors="replace")
    replacement_count = repaired.count("\ufffd")
    replacement_limit = min(
        _MAX_REPAIRED_UTF8_REPLACEMENTS,
        max(4, len(repaired) // 10),
    )
    if 0 < replacement_count <= replacement_limit and _looks_like_text(repaired):
        return DecodedSourceText(
            repaired,
            _REPAIRED_UTF8_ENCODING,
            replacement_count,
        )
    return None


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
    requirements: tuple[str, ...] = (),
) -> tuple[SnapshotBlob, ...]:
    """Load only bounded semantic candidates while retaining full snapshot coverage."""
    verify_source_snapshot_metadata(snapshot, root, peers)
    paths = _repository_paths(snapshot, root, peers)
    requirement_keys: set[tuple[str, str]] = set()
    for locator in requirements:
        if locator.startswith("repo:"):
            alias, separator, path = locator[5:].partition("/")
            if separator and alias and path:
                requirement_keys.add((alias, path))
        elif locator and not locator.startswith("/"):
            requirement_keys.add((".", locator))
    blobs: list[SnapshotBlob] = []
    remaining = MAX_SEMANTIC_TOTAL_BYTES
    for repository in snapshot.repositories:
        repo = paths[repository.alias]
        entries = tuple(
            entry for entry in repository.entries if entry.mode not in {"120000", "160000"}
        )
        selected = tuple(
            entry
            for entry in entries
            if (repository.alias, entry.path) in requirement_keys
            or (
                (not excluded_semantic_source(entry.path) and _semantic_path_candidate(entry.path))
                or is_supported_test_evidence_path(entry.path)
            )
            if entry.byte_length is not None and entry.byte_length <= MAX_SEMANTIC_BLOB_BYTES
        )
        oversized_requirements = tuple(
            entry.path
            for entry in entries
            if (repository.alias, entry.path) in requirement_keys
            and entry.byte_length is not None
            and entry.byte_length > MAX_SEMANTIC_BLOB_BYTES
        )
        if oversized_requirements:
            raise GitSnapshotError(
                "requirement material exceeds the semantic blob size limit: "
                + ", ".join(oversized_requirements)
            )
        oversized_supported = tuple(
            entry
            for entry in entries
            if (
                (not excluded_semantic_source(entry.path) and _semantic_path_candidate(entry.path))
                or is_supported_test_evidence_path(entry.path)
            )
            and entry.byte_length is not None
            and entry.byte_length > MAX_SEMANTIC_BLOB_BYTES
        )
        if oversized_supported:
            details = ", ".join(
                f"{repository.alias}:{entry.path} ({entry.byte_length} bytes)"
                for entry in oversized_supported
            )
            raise GitSnapshotError(
                "supported source exceeds the semantic blob size limit "
                f"({MAX_SEMANTIC_BLOB_BYTES} bytes): {details}"
            )
        payloads = read_blob_batch(
            repo,
            tuple(entry.object_id.git_sha1_hex() for entry in selected),
            max_total_bytes=remaining,
        )
        remaining -= sum(len(data) for data in payloads.values())
        for entry in entries:
            data = payloads.get(entry.object_id.git_sha1_hex())
            digest = entry.blob_sha256
            if digest is None or entry.byte_length is None:
                raise GitSnapshotError("snapshot blob metadata is incomplete")
            if data is not None and (
                len(data) != entry.byte_length or hashlib.sha256(data).hexdigest() != digest
            ):
                raise GitSnapshotError(
                    f"blob {repository.alias}:{entry.path} differs from the captured snapshot"
                )
            blobs.append(
                SnapshotBlob(
                    repository.alias,
                    entry.path,
                    data,
                    digest,
                    entry.byte_length,
                )
            )
    verify_source_snapshot_metadata(snapshot, root, peers)
    return tuple(blobs)


def source_text_encoding(blob: SnapshotBlob) -> str | None:
    """Return the deterministic text codec selected for one loaded source blob."""
    decoded = _decode_source_text(blob)
    return decoded.encoding if decoded is not None else None


def _text(blob: SnapshotBlob) -> str:
    if blob.data is None:
        raise DetectionError(
            f"supported source exceeds semantic loading limits: {blob.repository_alias}:{blob.path}"
        )
    decoded = _decode_source_text(blob)
    if decoded is None:
        raise SourceDecodeError(
            "supported source is not safely decodable as UTF-8 or GB18030: "
            f"{blob.repository_alias}:{blob.path}"
        )
    return decoded.text


def _contains_replacement(value: object) -> bool:
    if isinstance(value, str):
        return "\ufffd" in value
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains_replacement(getattr(value, field.name)) for field in fields(value))
    if isinstance(value, (tuple, list)):
        return any(_contains_replacement(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_replacement(key) or _contains_replacement(item) for key, item in value.items()
        )
    return False


def _source_lines(value: object) -> set[int]:
    if isinstance(value, SourceRef):
        return {value.line}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            line for field in fields(value) for line in _source_lines(getattr(value, field.name))
        }
    if isinstance(value, (tuple, list)):
        return {line for item in value for line in _source_lines(item)}
    if isinstance(value, dict):
        return {
            line
            for key, item in value.items()
            for line in (*_source_lines(key), *_source_lines(item))
        }
    return set()


def _without_replacement_facts(
    report: DetectionReport,
    damaged_lines: frozenset[int],
) -> tuple[DetectionReport, int]:
    values: dict[str, tuple[object, ...]] = {}
    dropped = 0
    for field in fields(DetectionReport):
        items = tuple(getattr(report, field.name))
        retained = tuple(
            item
            for item in items
            if not _contains_replacement(item) and not (_source_lines(item) & damaged_lines)
        )
        values[field.name] = retained
        dropped += len(items) - len(retained)
    return DetectionReport(**values), dropped  # type: ignore[arg-type]


def _first_replacement_line(text: str) -> int:
    return next(
        (index for index, line in enumerate(text.splitlines(), 1) if "\ufffd" in line),
        1,
    )


def _replacement_lines(text: str) -> frozenset[int]:
    return frozenset(index for index, line in enumerate(text.splitlines(), 1) if "\ufffd" in line)


def _source_issue_report(
    blob: SnapshotBlob,
    *,
    issue: str,
    selected_encoding: str | None,
    replacement_count: int,
    dropped_fact_count: int,
    line: int = 1,
) -> DetectionReport:
    return DetectionReport(
        source_issues=(
            SourceIssueRecord(
                blob.path,
                issue,  # type: ignore[arg-type]
                selected_encoding,
                replacement_count,
                dropped_fact_count,
                SourceRef(blob.repository_alias, blob.path, line),
            ),
        )
    )


def _test_text(blob: SnapshotBlob) -> str:
    """Decode test syntax with its separately governed compatibility profile."""
    if blob.data is None:
        raise DetectionError(
            f"supported test source exceeds semantic loading limits: "
            f"{blob.repository_alias}:{blob.path}"
        )
    encodings = ("utf-8",) if is_web_scenario_path(blob.path) else ("utf-8", "gb18030", "latin-1")
    for encoding in encodings:
        try:
            return blob.data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DetectionError(
        f"supported test source is not valid UTF-8: {blob.repository_alias}:{blob.path}"
    )


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
    return path.lower().endswith((".json", ".yaml", ".yml")) and has_supported_openapi_root(text)


def excluded_semantic_source(path: str) -> bool:
    """Keep tests/generated artifacts in the snapshot but outside product semantics."""
    parts = path.lower().split("/")
    return any(part in _AUXILIARY_SEGMENTS for part in parts[:-1]) or bool(
        _AUXILIARY_FILE.fullmatch(parts[-1])
    )


def _semantic_path_candidate(path: str) -> bool:
    lower = path.lower()
    return (
        lower.endswith(
            (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".mjs",
                ".cjs",
                ".vue",
                ".sql",
                ".prisma",
                ".graphql",
                ".graphqls",
                ".gql",
                ".proto",
                ".json",
                ".yaml",
                ".yml",
            )
        )
        or _is_config(path)
        or is_extended_source(path)
    )


def detector_profile(blob: SnapshotBlob) -> str | None:
    """Name the deterministic detector that will inspect this committed blob."""
    lower = blob.path.lower()
    if blob.data is None:
        return None
    if is_supported_test_evidence_path(blob.path):
        return "test_evidence"
    if excluded_semantic_source(blob.path):
        return None
    if _semantic_path_candidate(blob.path) and source_text_encoding(blob) is None:
        return None
    if lower.endswith(".py"):
        return "python"
    if lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return "typescript_javascript"
    if lower.endswith(".vue"):
        return "vue_sfc"
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
        if blob.data is None:
            continue
        if is_supported_test_evidence_path(blob.path):
            reports.append(detect_test_source(_test_text(blob), blob.repository_alias, blob.path))
            continue
        if excluded_semantic_source(blob.path):
            continue
        if not _semantic_path_candidate(blob.path):
            continue
        decoded = _decode_source_text(blob)
        if decoded is None:
            reports.append(
                _source_issue_report(
                    blob,
                    issue="unreadable_text_encoding",
                    selected_encoding=None,
                    replacement_count=0,
                    dropped_fact_count=0,
                )
            )
            continue
        try:
            report = _detect_snapshot_blob(blob, decoded.text)
        except DetectionError as exc:
            raise DetectionError(f"{blob.repository_alias}:{blob.path}: {exc}") from exc
        if report is not None:
            if decoded.encoding == _REPAIRED_UTF8_ENCODING:
                report, dropped = _without_replacement_facts(
                    report,
                    _replacement_lines(decoded.text),
                )
                reports.append(
                    _source_issue_report(
                        blob,
                        issue="lossy_utf8_recovery",
                        selected_encoding=decoded.encoding,
                        replacement_count=decoded.replacement_count,
                        dropped_fact_count=dropped,
                        line=_first_replacement_line(decoded.text),
                    )
                )
            reports.append(report)
    if requirements:
        from .authoritative_requirements_input import detect_requirement_materials

        reports.append(detect_requirement_materials(blobs, requirements))
    return normalize_detection_report(merge_reports(*reports))


def _detect_snapshot_blob(blob: SnapshotBlob, text: str | None = None) -> DetectionReport | None:
    lower = blob.path.lower()
    source = text if text is not None else _text(blob)
    if lower.endswith(".py"):
        return detect_python_source(source, blob.repository_alias, blob.path)
    if lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return detect_typescript_source(source, blob.repository_alias, blob.path)
    if lower.endswith(".vue"):
        return detect_vue_source(source, blob.repository_alias, blob.path)
    if lower.endswith(".sql"):
        return detect_sql_source(source, blob.repository_alias, blob.path)
    if _is_config(blob.path):
        return detect_config_source(source, blob.repository_alias, blob.path)
    if lower.endswith(".prisma"):
        return detect_prisma_schema(source, blob.repository_alias, blob.path)
    if lower.endswith((".graphql", ".graphqls", ".gql")):
        return detect_graphql_source(source, blob.repository_alias, blob.path)
    if lower.endswith(".proto"):
        return detect_proto_source(source, blob.repository_alias, blob.path)
    if lower.endswith((".json", ".yaml", ".yml")):
        if _is_openapi_contract(blob.path, source):
            return detect_openapi_source(source, blob.repository_alias, blob.path)
        if is_extended_source(blob.path):
            return detect_extended_source(source, blob.repository_alias, blob.path)
        return None
    if is_extended_source(blob.path):
        return detect_extended_source(source, blob.repository_alias, blob.path)
    return None


def detect_source_snapshot(
    snapshot: SourceSnapshot,
    root: Path,
    peers: Mapping[str, Path] | None = None,
    requirements: tuple[str, ...] = (),
) -> DetectionReport:
    """Read and detect one exact committed source snapshot."""
    return detect_snapshot_blobs(
        read_snapshot_blobs(snapshot, root, peers, requirements=requirements),
        requirements,
    )
